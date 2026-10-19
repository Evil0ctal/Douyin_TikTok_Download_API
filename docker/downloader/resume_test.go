package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// cuttingMirror serves payload with its full length declared, and drops the
// connection after cut bytes on the first `cuts` requests - what Bilibili's
// Akamai edge did with an object it had not cached yet. Range requests are
// answered as a well-behaved CDN would, unless changed is set, in which case
// the object is "replaced" and the If-Range no longer matches.
type cuttingMirror struct {
	payload string
	cut     int
	cuts    int32
	changed bool
	// How many requests for the rest are answered 504 first, as an edge
	// still pulling the object from its origin does.
	busy     int32
	requests atomic.Int32
	ranged   atomic.Int32
	ifRange  atomic.Value
}

func (m *cuttingMirror) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	attempt := m.requests.Add(1)
	if value := r.Header.Get("If-Range"); value != "" {
		m.ifRange.Store(value)
	}
	if r.Header.Get("Range") != "" && m.ranged.Add(1) <= m.busy {
		w.WriteHeader(http.StatusGatewayTimeout)
		return
	}
	w.Header().Set("Content-Type", "video/mp4")
	w.Header().Set("Accept-Ranges", "bytes")
	w.Header().Set("ETag", `"v1"`)
	start := 0
	if spec := r.Header.Get("Range"); spec != "" && !m.changed {
		_, _ = fmt.Sscanf(spec, "bytes=%d-", &start)
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, len(m.payload)-1, len(m.payload)))
		w.Header().Set("Content-Length", strconv.Itoa(len(m.payload)-start))
		w.WriteHeader(http.StatusPartialContent)
	} else {
		w.Header().Set("Content-Length", strconv.Itoa(len(m.payload)))
		w.WriteHeader(http.StatusOK)
	}
	body := m.payload[start:]
	if attempt <= m.cuts && len(body) > m.cut {
		_, _ = w.Write([]byte(body[:m.cut]))
		w.(http.Flusher).Flush()
		conn, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			_ = conn.Close()
		}
		return
	}
	_, _ = w.Write([]byte(body))
}

func fetchFrom(t *testing.T, mirror *cuttingMirror) (transferResult, string, error) {
	t.Helper()
	origin := httptest.NewServer(mirror)
	t.Cleanup(origin.Close)
	destination := filepath.Join(t.TempDir(), "video.mp4")
	result, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL + "/v.mp4"},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     destination,
	})
	return result, destination, err
}

func TestFetchContinuesATransferTheFarEndCutOff(t *testing.T) {
	mirror := &cuttingMirror{payload: strings.Repeat("0123456789", 5000), cut: 12000, cuts: 1}

	result, destination, err := fetchFrom(t, mirror)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	stored, err := os.ReadFile(destination)
	if err != nil || string(stored) != mirror.payload {
		t.Fatalf("the stored file is not the whole object (%d of %d bytes): %v", len(stored), len(mirror.payload), err)
	}
	// Hashed as the one file it is, across the join.
	sum := sha256.Sum256([]byte(mirror.payload))
	if result.SHA256 != hex.EncodeToString(sum[:]) || result.Bytes != int64(len(mirror.payload)) {
		t.Errorf("result = %d bytes %s, want the whole object", result.Bytes, result.SHA256)
	}
	if got := mirror.requests.Load(); got != 2 || result.Resumes != 1 {
		t.Errorf("requests = %d, resumes = %d, want the first and one for the rest", got, result.Resumes)
	}
	if got, _ := mirror.ifRange.Load().(string); got != `"v1"` {
		t.Errorf("If-Range = %q, want the first answer's ETag", got)
	}
}

func TestFetchNeverSplicesAReplacedFile(t *testing.T) {
	// The object changed between the two requests, so the If-Range fails and
	// the mirror answers the new one whole. Appending it to the old head would
	// store a file that is neither.
	mirror := &cuttingMirror{payload: strings.Repeat("abcdefghij", 5000), cut: 12000, cuts: 1, changed: true}

	_, destination, err := fetchFrom(t, mirror)
	if err == nil {
		t.Fatal("a cut-off transfer was completed from a replaced file")
	}
	if !strings.Contains(err.Error(), "answered 200") {
		t.Errorf("error = %v, want it to say the rest came back whole", err)
	}
	if _, statErr := os.Stat(destination); !os.IsNotExist(statErr) {
		t.Error("a file was stored under the real name")
	}
	if _, statErr := os.Stat(destination + ".part"); !os.IsNotExist(statErr) {
		t.Error("the .part survived a refused transfer")
	}
}

func TestFetchGivesUpOnAMirrorThatCutsEveryTime(t *testing.T) {
	mirror := &cuttingMirror{payload: strings.Repeat("0123456789", 5000), cut: 4000, cuts: 100}

	_, _, err := fetchFrom(t, mirror)
	if err == nil {
		t.Fatal("a mirror that never finishes was reported as finished")
	}
	if got := mirror.requests.Load(); got != maxResumes+1 {
		t.Errorf("requests = %d, want the first and %d resumes", got, maxResumes)
	}
}

func TestContinuesAtWantsExactlyTheRest(t *testing.T) {
	for _, tc := range []struct {
		header string
		want   bool
	}{
		{"bytes 100-999/1000", true},
		{"bytes 0-999/1000", false},   // the whole file again
		{"bytes 100-499/1000", false}, // only part of the rest
		{"bytes 100-999/2000", false}, // a different file
		{"", false},
	} {
		if got := continuesAt(tc.header, 100, 1000); got != tc.want {
			t.Errorf("continuesAt(%q) = %v, want %v", tc.header, got, tc.want)
		}
	}
}

func TestFetchWaitsForAnEdgeStillFetchingTheRest(t *testing.T) {
	saved := resumeWaits
	resumeWaits = []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}
	t.Cleanup(func() { resumeWaits = saved })
	mirror := &cuttingMirror{payload: strings.Repeat("0123456789", 5000), cut: 12000, cuts: 1, busy: 2}

	result, _, err := fetchFrom(t, mirror)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if result.Bytes != int64(len(mirror.payload)) {
		t.Errorf("bytes = %d, want the whole object", result.Bytes)
	}
	if got := mirror.ranged.Load(); got != 3 {
		t.Errorf("requests for the rest = %d, want two 504s and the one that worked", got)
	}
}

func TestFetchDoesNotWaitOnARefusalTimeCannotMend(t *testing.T) {
	saved := resumeWaits
	resumeWaits = []time.Duration{time.Hour}
	t.Cleanup(func() { resumeWaits = saved })
	// A replaced file answers 200 to If-Range; waiting an hour would not help.
	mirror := &cuttingMirror{payload: strings.Repeat("abcdefghij", 5000), cut: 12000, cuts: 1, changed: true}

	done := make(chan error, 1)
	go func() { _, _, err := fetchFrom(t, mirror); done <- err }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("a replaced file was accepted")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("waited before giving up on a refusal no wait could mend")
	}
}
