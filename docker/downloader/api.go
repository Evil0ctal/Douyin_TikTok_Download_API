package main

// The HTTP surface. Six routes, one shape, no surprises.
//
// This service answers only to the main API and worker on an internal compose
// network, so the surface is small by construction. What it must never grow is
// a route that streams stored bytes back to a caller: the downloader is a sink
// that fills the operator's own disk, not a relay that turns this instance into
// an open media proxy (docs/design/18, docs/design/07).

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const maxRequestBytes = 4 << 20

type submitRequest struct {
	ID        string          `json:"id"`
	Platform  string          `json:"platform"`
	AuthorUID string          `json:"author_uid"`
	ContentID string          `json:"content_id"`
	Domains   []string        `json:"domains"`
	UserAgent string          `json:"user_agent"`
	Referer   string          `json:"referer"`
	Meta      json.RawMessage `json:"meta,omitempty"`
	Items     []struct {
		Name     string   `json:"name"`
		Kind     string   `json:"kind"`
		Mirrors  []string `json:"mirrors"`
		Accept   []string `json:"accept"`
		MaxBytes int64    `json:"max_bytes"`
	} `json:"items"`
}

type deleteRequest struct {
	Paths []string `json:"paths"`
}

type server struct {
	manager *manager
	root    string
	token   string
	version string
	workers int
	log     *slog.Logger
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("POST /jobs", s.guard(s.submit))
	mux.HandleFunc("GET /jobs", s.guard(s.listJobs))
	mux.HandleFunc("GET /jobs/{id}", s.guard(s.getJob))
	mux.HandleFunc("DELETE /jobs/{id}", s.guard(s.cancelJob))
	mux.HandleFunc("GET /files", s.guard(s.listFiles))
	mux.HandleFunc("POST /files/delete", s.guard(s.deleteFiles))
	return mux
}

// guard checks the shared token when one is configured.
//
// Optional, because the service listens on an internal network no other
// container is on, and a token that every deployment must set is a token most
// deployments set to the same string. Available, because "internal network" is
// an assumption about someone else's docker-compose, and defence in depth costs
// one string comparison.
func (s *server) guard(next http.HandlerFunc) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		if s.token != "" && request.Header.Get("X-Downloader-Token") != s.token {
			fail(writer, http.StatusUnauthorized, "unauthorized")
			return
		}
		next(writer, request)
	}
}

func (s *server) health(writer http.ResponseWriter, _ *http.Request) {
	queued, running := s.manager.stats()
	measured, err := scan(s.root)
	detail := ""
	if err != nil {
		detail = err.Error()
	}
	respond(writer, http.StatusOK, map[string]any{
		"status":      "ok",
		"version":     s.version,
		"workers":     s.workers,
		"queued":      queued,
		"running":     running,
		"root":        s.root,
		"total_bytes": measured.TotalBytes,
		"detail":      detail,
	})
}

func (s *server) submit(writer http.ResponseWriter, request *http.Request) {
	var body submitRequest
	if err := decode(request, &body); err != nil {
		fail(writer, http.StatusBadRequest, err.Error())
		return
	}
	candidate, err := buildJob(body)
	if err != nil {
		fail(writer, http.StatusBadRequest, err.Error())
		return
	}
	if len(body.Meta) > 0 {
		if err := writeMeta(s.root, candidate, body.Meta); err != nil {
			fail(writer, http.StatusBadRequest, err.Error())
			return
		}
	}
	switch err := s.manager.submit(candidate); {
	case errors.Is(err, errQueueFull):
		// Retry-After rather than a bare 429: the main service should back off
		// by a known amount instead of guessing, and an operator reading the
		// log should see the queue is the constraint.
		writer.Header().Set("Retry-After", "30")
		fail(writer, http.StatusTooManyRequests, err.Error())
	case err != nil:
		fail(writer, http.StatusInternalServerError, err.Error())
	default:
		s.log.Info("job accepted",
			"job", candidate.ID, "platform", candidate.Platform,
			"items", len(candidate.Items))
		stored, _ := s.manager.get(candidate.ID)
		respond(writer, http.StatusAccepted, stored)
	}
}

func (s *server) getJob(writer http.ResponseWriter, request *http.Request) {
	found, err := s.manager.get(request.PathValue("id"))
	if err != nil {
		fail(writer, http.StatusNotFound, err.Error())
		return
	}
	respond(writer, http.StatusOK, found)
}

func (s *server) listJobs(writer http.ResponseWriter, request *http.Request) {
	limit := 50
	if raw := request.URL.Query().Get("limit"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			limit = min(parsed, 500)
		}
	}
	respond(writer, http.StatusOK, map[string]any{"jobs": s.manager.list(limit)})
}

func (s *server) cancelJob(writer http.ResponseWriter, request *http.Request) {
	if err := s.manager.cancel(request.PathValue("id")); err != nil {
		fail(writer, http.StatusNotFound, err.Error())
		return
	}
	respond(writer, http.StatusOK, map[string]any{"cancelled": true})
}

func (s *server) listFiles(writer http.ResponseWriter, _ *http.Request) {
	measured, err := scan(s.root)
	if err != nil {
		fail(writer, http.StatusInternalServerError, err.Error())
		return
	}
	respond(writer, http.StatusOK, measured)
}

func (s *server) deleteFiles(writer http.ResponseWriter, request *http.Request) {
	var body deleteRequest
	if err := decode(request, &body); err != nil {
		fail(writer, http.StatusBadRequest, err.Error())
		return
	}
	freed, removed, err := removeAll(s.root, body.Paths)
	if err != nil {
		// Partial progress is reported rather than hidden: whatever was freed
		// is really gone, and the main service has to know which rows to mark.
		respond(writer, http.StatusBadRequest, map[string]any{
			"error": err.Error(), "freed_bytes": freed, "removed": removed,
		})
		return
	}
	s.log.Info("media removed", "directories", len(removed), "freed_bytes", freed)
	respond(writer, http.StatusOK, map[string]any{"freed_bytes": freed, "removed": removed})
}

// buildJob validates a submission into the job the workers run.
//
// Everything a caller can influence is checked here, once, before anything is
// queued: identifiers have to be usable as path segments, an item needs a name
// and at least one mirror, and the allowlist may not be empty - an absent
// domains list is a job that could dial anywhere, so it is refused rather than
// defaulted.
func buildJob(body submitRequest) (*job, error) {
	if !safeSegment(body.ID) {
		return nil, fmt.Errorf("id %q is not usable", body.ID)
	}
	for label, value := range map[string]string{
		"platform": body.Platform, "author_uid": body.AuthorUID, "content_id": body.ContentID,
	} {
		if !safeSegment(value) {
			return nil, fmt.Errorf("%s %q is not usable as a path segment", label, value)
		}
	}
	if len(body.Domains) == 0 {
		return nil, errors.New("domains must list the media hosts this job may reach")
	}
	if len(body.Items) == 0 {
		return nil, errors.New("a job needs at least one item")
	}
	items := make([]*item, 0, len(body.Items))
	for _, source := range body.Items {
		if !safeSegment(source.Name) {
			return nil, fmt.Errorf("item name %q is not usable as a filename", source.Name)
		}
		if len(source.Mirrors) == 0 {
			return nil, fmt.Errorf("item %q has no mirrors", source.Name)
		}
		if len(source.Accept) == 0 {
			return nil, fmt.Errorf("item %q does not say which content types are acceptable", source.Name)
		}
		if source.MaxBytes <= 0 {
			return nil, fmt.Errorf("item %q has no byte ceiling", source.Name)
		}
		items = append(items, &item{
			Name: source.Name, Kind: source.Kind, Mirrors: source.Mirrors,
			Accept: source.Accept, MaxBytes: source.MaxBytes, State: "queued",
		})
	}
	return &job{
		ID:        body.ID,
		Platform:  body.Platform,
		AuthorUID: body.AuthorUID,
		ContentID: body.ContentID,
		Directory: strings.Join([]string{body.Platform, body.AuthorUID, body.ContentID}, "/"),
		Domains:   body.Domains,
		UserAgent: body.UserAgent,
		Referer:   body.Referer,
		State:     stateQueued,
		Items:     items,
		CreatedAt: time.Now().UTC(),
	}, nil
}

// writeMeta drops the sidecar next to the media.
//
// It is written at submission rather than at completion on purpose: a job
// interrupted halfway still leaves a directory that says what it was, so an
// operator looking at the volume months later is never holding an unlabelled
// video file. The content is composed by the main service; this side only
// checks that it is JSON and puts it where it belongs.
func writeMeta(root string, current *job, meta json.RawMessage) error {
	if !json.Valid(meta) {
		return errors.New("meta is not valid JSON")
	}
	directory, err := safeJoin(root, current.Platform, current.AuthorUID, current.ContentID)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(directory, "meta.json"), meta, 0o640)
}

func decode(request *http.Request, into any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(nil, request.Body, maxRequestBytes))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(into); err != nil {
		return fmt.Errorf("unreadable request body: %w", err)
	}
	return nil
}

func respond(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}

func fail(writer http.ResponseWriter, status int, message string) {
	respond(writer, status, map[string]any{"error": message})
}
