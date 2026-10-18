package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestMeterRate(t *testing.T) {
	start := time.Date(2026, 10, 18, 0, 0, 0, 0, time.UTC)
	at := func(seconds float64) time.Time {
		return start.Add(time.Duration(seconds * float64(time.Second)))
	}
	cases := []struct {
		name    string
		writes  map[float64]int64
		readAt  float64
		total   int64
		want    int64
		wantMax int64
	}{
		{
			// One millisecond in, the first 32 KB would read as 32 MB/s.
			name: "no rate before it settles", writes: map[float64]int64{0.001: 32 << 10},
			readAt: 0.1, total: 32 << 10, want: 0,
		},
		{
			name:   "a steady transfer",
			writes: map[float64]int64{1: 1000, 2: 2000, 3: 3000, 4: 4000, 5: 5000},
			readAt: 5, total: 5000, want: 1000,
		},
		{
			// Nothing written for ten seconds. The rate falls rather than
			// staying at the last speed the connection managed.
			name:   "a stall decays",
			writes: map[float64]int64{1: 1000, 2: 2000, 3: 3000, 4: 4000},
			readAt: 14, total: 4000, wantMax: 250,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var m meter
			m.reset(start)
			for _, second := range []float64{0.001, 1, 2, 3, 4, 5} {
				if written, ok := tc.writes[second]; ok {
					m.observe(at(second), written)
				}
			}
			got := m.rate(at(tc.readAt), tc.total)
			if tc.wantMax > 0 {
				if got <= 0 || got > tc.wantMax {
					t.Fatalf("rate = %d, want in (0, %d]", got, tc.wantMax)
				}
				return
			}
			if got != tc.want {
				t.Fatalf("rate = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestAMeterNeverStartedHasNoRate(t *testing.T) {
	var m meter
	if got := m.rate(time.Now(), 100); got != 0 {
		t.Fatalf("rate = %d, want 0", got)
	}
}

// recorder is a progress that remembers what it was told.
type recorder struct {
	mu       sync.Mutex
	declared []int64
	written  [][]int64
}

func (r *recorder) begin(declared int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.declared = append(r.declared, declared)
	r.written = append(r.written, nil)
}

func (r *recorder) advance(written int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	last := len(r.written) - 1
	r.written[last] = append(r.written[last], written)
}

func TestFetchReportsProgressWhileWriting(t *testing.T) {
	chunk := strings.Repeat("v", 64<<10)
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", strconv.Itoa(4*len(chunk)))
		for range 4 {
			_, _ = w.Write([]byte(chunk))
			w.(http.Flusher).Flush()
		}
	}))
	defer origin.Close()

	seen := &recorder{}
	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
		Progress: seen,
	})
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if len(seen.declared) != 1 || seen.declared[0] != int64(4*len(chunk)) {
		t.Fatalf("declared = %v, want one Content-Length of %d", seen.declared, 4*len(chunk))
	}
	counts := seen.written[0]
	if len(counts) < 2 {
		t.Fatalf("advanced %d times, want one per write and more than one", len(counts))
	}
	for index := 1; index < len(counts); index++ {
		if counts[index] <= counts[index-1] {
			t.Fatalf("the count went backwards: %v", counts)
		}
	}
	if counts[len(counts)-1] != int64(4*len(chunk)) {
		t.Fatalf("last count = %d, want %d", counts[len(counts)-1], 4*len(chunk))
	}
}

func TestFetchStartsTheCountOverOnTheNextMirror(t *testing.T) {
	broken := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", "1000")
		_, _ = w.Write([]byte(strings.Repeat("x", 400)))
		w.(http.Flusher).Flush()
		// Hang up with 600 of the declared 1000 bytes missing.
		panic(http.ErrAbortHandler)
	}))
	defer broken.Close()
	alive := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("whole"))
	}))
	defer alive.Close()

	seen := &recorder{}
	result, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{broken.URL, alive.URL},
		Domains:  []string{hostOf(t, broken.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
		Progress: seen,
	})
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if result.Bytes != 5 {
		t.Fatalf("bytes = %d, want 5", result.Bytes)
	}
	// Two attempts, and the second counts from zero against its own size:
	// the 400 bytes of the first were truncated away with its .part.
	if len(seen.declared) != 2 || seen.declared[0] != 1000 {
		t.Fatalf("declared = %v, want [1000 <unknown>]", seen.declared)
	}
	second := seen.written[1]
	if len(second) == 0 || second[len(second)-1] != 5 {
		t.Fatalf("second attempt counted %v, want it to end at 5", second)
	}
}

func TestARunningJobReportsItsBytes(t *testing.T) {
	half := strings.Repeat("v", 3000)
	release := make(chan struct{})
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", strconv.Itoa(2*len(half)))
		_, _ = w.Write([]byte(half))
		w.(http.Flusher).Flush()
		<-release
		_, _ = w.Write([]byte(half))
	}))
	defer origin.Close()
	released := false
	defer func() {
		if !released {
			close(release)
		}
	}()

	api, jobs := testServer(t, t.TempDir())
	handler := api.routes()
	recorder := post(t, handler, "/jobs", map[string]any{
		"id": "live1", "platform": "bilibili", "author_uid": "1", "content_id": "BV1",
		"domains": []string{hostOf(t, origin.URL)},
		"items": []map[string]any{
			{"name": "video.mp4", "kind": "video", "mirrors": []string{origin.URL},
				"accept": []string{"video/"}, "max_bytes": 1 << 20},
		},
	})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("submit = %d: %s", recorder.Code, recorder.Body.String())
	}

	// Read the way the main service reads it: over HTTP, as JSON. What matters
	// is the wire names, not the Go fields behind them.
	type wireItem struct {
		State          string `json:"state"`
		Bytes          int64  `json:"bytes"`
		BytesExpected  int64  `json:"bytes_expected"`
		BytesPerSecond int64  `json:"bytes_per_second"`
	}
	read := func() wireItem {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/jobs/live1", nil))
		var body struct {
			Items []wireItem `json:"items"`
		}
		if err := json.NewDecoder(response.Body).Decode(&body); err != nil || len(body.Items) != 1 {
			t.Fatalf("job body: %v (%s)", err, response.Body.String())
		}
		return body.Items[0]
	}

	deadline := time.Now().Add(5 * time.Second)
	var live wireItem
	for time.Now().Before(deadline) {
		live = read()
		if live.State == "running" && live.Bytes == int64(len(half)) && live.BytesPerSecond > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if live.State != "running" || live.Bytes != int64(len(half)) {
		t.Fatalf("mid-transfer item = %+v, want running with %d bytes", live, len(half))
	}
	if live.BytesExpected != int64(2*len(half)) {
		t.Fatalf("bytes_expected = %d, want %d", live.BytesExpected, 2*len(half))
	}
	if live.BytesPerSecond <= 0 {
		t.Fatalf("bytes_per_second = %d, want a rate once the transfer has settled", live.BytesPerSecond)
	}

	close(release)
	released = true
	final := waitFor(t, jobs, "live1")
	if final.State != stateDone || final.BytesTotal != int64(2*len(half)) {
		t.Fatalf("final = %s with %d bytes", final.State, final.BytesTotal)
	}
	// A settled item has no rate: there is nothing left to be fast at.
	if settled := read(); settled.BytesPerSecond != 0 || settled.State != "done" {
		t.Fatalf("settled item = %+v", settled)
	}
}
