// Command downloader stores media this instance has already parsed onto the
// operator's own disk.
//
// It exists as a separate container in a separate language for two reasons the
// maintainer named: throughput, because pulling a dozen files at once is what
// goroutines and io.Copy are for; and isolation, because the component that
// dials arbitrary CDN hosts and fills a disk is exactly the one whose overload
// must not reach the API. The cgroup limits in docker/compose.yml are what make
// the second half true - a separate container alone does not.
//
// What it is not: a relay. It never streams bytes back to an API caller, never
// accepts a URL from one, and holds no cookies, no master key and no database
// address. It takes mirrors the main service resolved from its own archive,
// and it writes them to a volume. See docs/design/18-storage-media-and-collection.md.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

// version is stamped by the build; "dev" when built by hand.
var version = "dev"

type config struct {
	bind         string
	root         string
	token        string
	workers      int
	itemWorkers  int
	queueSize    int
	history      int
	maxRedirects int
	timeout      time.Duration
}

func load() config {
	return config{
		bind:         env("DTK_DOWNLOADER_BIND", "0.0.0.0:9100"),
		root:         env("DTK_DOWNLOADER_ROOT", "/var/lib/dtk/media"),
		token:        env("DTK_DOWNLOADER_TOKEN", ""),
		workers:      envInt("DTK_DOWNLOADER_WORKERS", 4),
		itemWorkers:  envInt("DTK_DOWNLOADER_ITEM_WORKERS", 4),
		queueSize:    envInt("DTK_DOWNLOADER_QUEUE", 64),
		history:      envInt("DTK_DOWNLOADER_HISTORY", 500),
		maxRedirects: envInt("DTK_DOWNLOADER_MAX_REDIRECTS", 5),
		timeout:      time.Duration(envInt("DTK_DOWNLOADER_TIMEOUT_SECONDS", 900)) * time.Second,
	}
}

func env(name, fallback string) string {
	if value, ok := os.LookupEnv(name); ok && value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value, ok := os.LookupEnv(name)
	if !ok || value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

// probe is the container healthcheck, run as `downloader healthcheck`.
//
// It lives in this binary because the image has no shell and no curl - the
// smaller the image, the fewer things inside the one container allowed to dial
// the open internet. A probe that needed a package installed would be a reason
// to stop using a scratch base.
func probe(settings config) int {
	address := settings.bind
	if host, port, err := net.SplitHostPort(address); err == nil {
		if host == "" || host == "0.0.0.0" || host == "::" {
			address = net.JoinHostPort("127.0.0.1", port)
		}
	}
	client := &http.Client{Timeout: 5 * time.Second}
	response, err := client.Get("http://" + address + "/health")
	if err != nil {
		return 1
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		return 1
	}
	var body struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil || body.Status != "ok" {
		return 1
	}
	return 0
}

func main() {
	settings := load()
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(probe(settings))
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

	if err := os.MkdirAll(settings.root, 0o750); err != nil {
		logger.Error("cannot use the media root", "root", settings.root, "error", err.Error())
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	transfers := newFetcher(settings.maxRedirects, settings.timeout)
	defer transfers.close()

	jobs := newManager(settings.root, transfers, settings.queueSize, settings.itemWorkers, settings.history, logger)
	jobs.start(ctx, settings.workers)

	api := &server{
		manager: jobs,
		root:    settings.root,
		token:   settings.token,
		version: version,
		workers: settings.workers,
		log:     logger,
	}
	httpServer := &http.Server{
		Addr:              settings.bind,
		Handler:           api.routes(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: /files on a full volume is a directory walk, and a
		// deadline that kills it would make the eviction sweep fail exactly
		// when the disk is fullest.
		IdleTimeout: 60 * time.Second,
	}

	go func() {
		<-ctx.Done()
		logger.Info("shutting down")
		// Long enough for an in-flight transfer to be cancelled cleanly and
		// its .part removed, short enough that a container stop is not a wait.
		shutdown, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdown)
	}()

	logger.Info("downloader listening",
		"bind", settings.bind, "root", settings.root,
		"workers", settings.workers, "item_workers", settings.itemWorkers,
		"queue", settings.queueSize, "authenticated", settings.token != "")

	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("server stopped", "error", err.Error())
		os.Exit(1)
	}
	jobs.wait()
	logger.Info("stopped")
}
