package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// localFetcher points at an httptest server, which the production dial guard
// would refuse. The guard itself is covered in safety_test.go.
func localFetcher(t *testing.T) *fetcher {
	t.Helper()
	f := newFetcherWith(5, 10*time.Second, nil)
	t.Cleanup(f.close)
	return f
}

func hostOf(t *testing.T, raw string) string {
	t.Helper()
	parsed, err := url.Parse(raw)
	if err != nil {
		t.Fatalf("unparseable test URL: %v", err)
	}
	return parsed.Hostname()
}

func TestFetchStoresAndDigests(t *testing.T) {
	payload := strings.Repeat("dtk", 1000)
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte(payload))
	}))
	defer origin.Close()

	root := t.TempDir()
	destination := filepath.Join(root, "video.mp4")
	result, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL + "/v.mp4"},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	sum := sha256.Sum256([]byte(payload))
	if result.SHA256 != hex.EncodeToString(sum[:]) {
		t.Errorf("digest = %s, want %s", result.SHA256, hex.EncodeToString(sum[:]))
	}
	if result.Bytes != int64(len(payload)) {
		t.Errorf("bytes = %d, want %d", result.Bytes, len(payload))
	}
	stored, err := os.ReadFile(destination)
	if err != nil || string(stored) != payload {
		t.Fatalf("stored file does not match: %v", err)
	}
	// The .part must be gone: its whole purpose is that a name only appears
	// once the bytes behind it are complete.
	if _, err := os.Stat(destination + ".part"); !os.IsNotExist(err) {
		t.Error("a .part file survived a successful transfer")
	}
}

func TestFetchRefusesOversizeOnBytesWritten(t *testing.T) {
	// Content-Length lies: it claims a small file and sends a large one. Only
	// counting what is written catches this.
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", "10")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		for i := 0; i < 40; i++ {
			_, _ = w.Write([]byte(strings.Repeat("x", 100)))
			if flusher != nil {
				flusher.Flush()
			}
		}
	}))
	defer origin.Close()

	root := t.TempDir()
	destination := filepath.Join(root, "video.mp4")
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 500,
		Dest:     destination,
	})
	if err == nil {
		t.Fatal("expected the byte ceiling to refuse the transfer")
	}
	if _, err := os.Stat(destination); !os.IsNotExist(err) {
		t.Error("a refused transfer left a file behind")
	}
	if _, err := os.Stat(destination + ".part"); !os.IsNotExist(err) {
		t.Error("a refused transfer left a .part behind")
	}
}

func TestFetchRefusesWrongContentType(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte("<html>please log in</html>"))
	}))
	defer origin.Close()

	destination := filepath.Join(t.TempDir(), "video.mp4")
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	if err == nil || !strings.Contains(err.Error(), "content-type") {
		t.Fatalf("expected a content-type refusal, got %v", err)
	}
}

func TestFetchFailsOverToTheNextMirror(t *testing.T) {
	dead := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer dead.Close()
	alive := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/jpeg")
		_, _ = w.Write([]byte("jpegbytes"))
	}))
	defer alive.Close()

	destination := filepath.Join(t.TempDir(), "cover.jpg")
	result, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{dead.URL, alive.URL},
		Domains:  []string{hostOf(t, dead.URL), hostOf(t, alive.URL)},
		Accept:   []string{"image/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	if err != nil {
		t.Fatalf("expected the second mirror to be used: %v", err)
	}
	if result.Bytes != 9 {
		t.Errorf("bytes = %d, want 9", result.Bytes)
	}
}

func TestFetchRefusesRedirectOffTheAllowlist(t *testing.T) {
	// The 302 out of the allowlist is the SSRF that a host check performed once
	// would miss entirely.
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("secret"))
	}))
	defer elsewhere.Close()
	// Same address, different name: httptest binds everything to 127.0.0.1, so
	// the hop is expressed as "localhost" while the allowlist names the
	// literal. What is being tested is the allowlist check on the hop, not the
	// address behind it.
	elsewhereByName := strings.Replace(elsewhere.URL, "127.0.0.1", "localhost", 1)
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, elsewhereByName, http.StatusFound)
	}))
	defer origin.Close()

	destination := filepath.Join(t.TempDir(), "video.mp4")
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	if err == nil {
		t.Fatal("expected the redirect to be refused")
	}
	if _, err := os.Stat(destination); !os.IsNotExist(err) {
		t.Error("a refused redirect still wrote a file")
	}
}

func TestFetchRefusesAMirrorOffTheAllowlist(t *testing.T) {
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{"https://evil.example/video.mp4"},
		Domains:  []string{"tiktok.com"},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
	})
	if err == nil || !strings.Contains(err.Error(), "not an allowed media domain") {
		t.Fatalf("expected an allowlist refusal, got %v", err)
	}
}

func TestFetchRefusesCredentialsInAUrl(t *testing.T) {
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{"https://user:pass@cdn.tiktok.com/v.mp4"},
		Domains:  []string{"tiktok.com"},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
	})
	if err == nil || !strings.Contains(err.Error(), "credentials") {
		t.Fatalf("expected a userinfo refusal, got %v", err)
	}
}

func TestFetchRefusesAnEmptyBody(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
	}))
	defer origin.Close()

	destination := filepath.Join(t.TempDir(), "video.mp4")
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	if err == nil {
		t.Fatal("expected an empty body to be refused")
	}
	if _, err := os.Stat(destination); !os.IsNotExist(err) {
		t.Error("an empty body was stored as a file")
	}
}
