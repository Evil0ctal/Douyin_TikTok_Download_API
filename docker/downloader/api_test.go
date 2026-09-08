package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testServer(t *testing.T, root string) (*server, *manager) {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	transfers := newFetcherWith(5, 10*time.Second, nil)
	t.Cleanup(transfers.close)
	jobs := newManager(root, transfers, 8, 2, 100, logger)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	jobs.start(ctx, 2)
	return &server{manager: jobs, root: root, version: "test", workers: 2, log: logger}, jobs
}

func post(t *testing.T, handler http.Handler, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, path, bytes.NewReader(encoded)))
	return recorder
}

func TestSubmitRunsAJobEndToEnd(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("mp4bytes"))
	}))
	defer origin.Close()

	root := t.TempDir()
	api, jobs := testServer(t, root)
	handler := api.routes()

	recorder := post(t, handler, "/jobs", map[string]any{
		"id":         "job1",
		"platform":   "douyin",
		"author_uid": "MS4wA",
		"content_id": "7408",
		"domains":    []string{hostOf(t, origin.URL)},
		"meta":       json.RawMessage(`{"title":"a post"}`),
		"items": []map[string]any{
			{"name": "video.mp4", "kind": "video", "mirrors": []string{origin.URL},
				"accept": []string{"video/"}, "max_bytes": 1 << 20},
		},
	})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("submit = %d: %s", recorder.Code, recorder.Body.String())
	}

	final := waitFor(t, jobs, "job1")
	if final.State != stateDone {
		t.Fatalf("state = %s, error %q", final.State, final.Error)
	}
	if final.BytesTotal != 8 {
		t.Errorf("bytes = %d, want 8", final.BytesTotal)
	}
	stored, err := os.ReadFile(filepath.Join(root, "douyin", "MS4wA", "7408", "video.mp4"))
	if err != nil || string(stored) != "mp4bytes" {
		t.Fatalf("stored file: %v", err)
	}
	// The sidecar is written at submission, so an interrupted job still leaves
	// a directory that says what it holds.
	sidecar, err := os.ReadFile(filepath.Join(root, "douyin", "MS4wA", "7408", "meta.json"))
	if err != nil || string(sidecar) != `{"title":"a post"}` {
		t.Fatalf("meta.json: %v (%s)", err, sidecar)
	}
}

func TestSubmitReportsPartialWhenOneItemFails(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/gone" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "image/jpeg")
		_, _ = w.Write([]byte("jpeg"))
	}))
	defer origin.Close()

	api, jobs := testServer(t, t.TempDir())
	recorder := post(t, api.routes(), "/jobs", map[string]any{
		"id": "job2", "platform": "tiktok", "author_uid": "1", "content_id": "2",
		"domains": []string{hostOf(t, origin.URL)},
		"items": []map[string]any{
			{"name": "cover.jpg", "kind": "cover", "mirrors": []string{origin.URL + "/ok"},
				"accept": []string{"image/"}, "max_bytes": 1 << 20},
			{"name": "image-01.jpg", "kind": "image", "mirrors": []string{origin.URL + "/gone"},
				"accept": []string{"image/"}, "max_bytes": 1 << 20},
		},
	})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("submit = %d", recorder.Code)
	}
	final := waitFor(t, jobs, "job2")
	// Neither done nor failed: one file landed and one did not, and calling
	// that either would hide half of what happened.
	if final.State != statePartial {
		t.Fatalf("state = %s, want partial", final.State)
	}
}

func TestSubmitRefusesAJobWithNoAllowlist(t *testing.T) {
	api, _ := testServer(t, t.TempDir())
	recorder := post(t, api.routes(), "/jobs", map[string]any{
		"id": "job3", "platform": "douyin", "author_uid": "a", "content_id": "b",
		"items": []map[string]any{
			{"name": "video.mp4", "mirrors": []string{"https://cdn.example/v"},
				"accept": []string{"video/"}, "max_bytes": 10},
		},
	})
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected a job with no domains to be refused, got %d", recorder.Code)
	}
}

func TestSubmitRefusesUnsafeIdentifiers(t *testing.T) {
	api, _ := testServer(t, t.TempDir())
	for _, body := range []map[string]any{
		{"id": "job4", "platform": "../etc", "author_uid": "a", "content_id": "b"},
		{"id": "job5", "platform": "douyin", "author_uid": "..", "content_id": "b"},
		{"id": "job6", "platform": "douyin", "author_uid": "a", "content_id": "b/c"},
	} {
		body["domains"] = []string{"tiktok.com"}
		body["items"] = []map[string]any{
			{"name": "video.mp4", "mirrors": []string{"https://cdn.tiktok.com/v"},
				"accept": []string{"video/"}, "max_bytes": 10},
		}
		if recorder := post(t, api.routes(), "/jobs", body); recorder.Code != http.StatusBadRequest {
			t.Errorf("expected %v to be refused, got %d", body, recorder.Code)
		}
	}
}

func TestSubmitIsIdempotentById(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("x"))
	}))
	defer origin.Close()
	api, jobs := testServer(t, t.TempDir())
	body := map[string]any{
		"id": "job7", "platform": "douyin", "author_uid": "a", "content_id": "b",
		"domains": []string{hostOf(t, origin.URL)},
		"items": []map[string]any{
			{"name": "video.mp4", "mirrors": []string{origin.URL},
				"accept": []string{"video/"}, "max_bytes": 1 << 20},
		},
	}
	post(t, api.routes(), "/jobs", body)
	// A retried submission - the main service never saw the first answer -
	// must not download the same file twice.
	if recorder := post(t, api.routes(), "/jobs", body); recorder.Code != http.StatusAccepted {
		t.Fatalf("resubmit = %d", recorder.Code)
	}
	waitFor(t, jobs, "job7")
	if listed := jobs.list(0); len(listed) != 1 {
		t.Fatalf("jobs = %d, want 1", len(listed))
	}
}

func TestTokenGuardsEverythingButHealth(t *testing.T) {
	api, _ := testServer(t, t.TempDir())
	api.token = "shared"
	handler := api.routes()

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/jobs", nil))
	if recorder.Code != http.StatusUnauthorized {
		t.Errorf("unauthenticated /jobs = %d, want 401", recorder.Code)
	}

	recorder = httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/jobs", nil)
	request.Header.Set("X-Downloader-Token", "shared")
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Errorf("authenticated /jobs = %d, want 200", recorder.Code)
	}

	// Health stays open: it is what the compose healthcheck reads, and a
	// probe that needs a secret is a probe that reports a broken container
	// when the secret is what is broken.
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))
	if recorder.Code != http.StatusOK {
		t.Errorf("/health = %d, want 200", recorder.Code)
	}
}

func TestDeleteFilesRemovesOnlyWhatWasNamed(t *testing.T) {
	root := t.TempDir()
	seed(t, root, "douyin", "MS4wA", "7408", "video.mp4", 100)
	seed(t, root, "douyin", "MS4wA", "7409", "video.mp4", 100)
	api, _ := testServer(t, root)

	recorder := post(t, api.routes(), "/files/delete", map[string]any{
		"paths": []string{"douyin/MS4wA/7408"},
	})
	if recorder.Code != http.StatusOK {
		t.Fatalf("delete = %d: %s", recorder.Code, recorder.Body.String())
	}
	var answer struct {
		Freed int64 `json:"freed_bytes"`
	}
	_ = json.Unmarshal(recorder.Body.Bytes(), &answer)
	if answer.Freed != 100 {
		t.Errorf("freed = %d, want 100", answer.Freed)
	}
	if _, err := os.Stat(filepath.Join(root, "douyin", "MS4wA", "7409", "video.mp4")); err != nil {
		t.Error("the neighbouring directory was removed too")
	}
}

func TestCancelStopsAJob(t *testing.T) {
	release := make(chan struct{})
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.WriteHeader(http.StatusOK)
		<-release
	}))
	defer origin.Close()
	defer close(release)

	api, jobs := testServer(t, t.TempDir())
	handler := api.routes()
	post(t, handler, "/jobs", map[string]any{
		"id": "job8", "platform": "douyin", "author_uid": "a", "content_id": "b",
		"domains": []string{hostOf(t, origin.URL)},
		"items": []map[string]any{
			{"name": "video.mp4", "mirrors": []string{origin.URL},
				"accept": []string{"video/"}, "max_bytes": 1 << 20},
		},
	})

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodDelete, "/jobs/job8", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("cancel = %d", recorder.Code)
	}
	final := waitFor(t, jobs, "job8")
	if final.State != stateCancelled {
		t.Fatalf("state = %s, want cancelled", final.State)
	}
}

func waitFor(t *testing.T, jobs *manager, id string) *job {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		found, err := jobs.get(id)
		if err == nil && found.State.terminal() {
			return found
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("job %s never reached a terminal state", id)
	return nil
}
