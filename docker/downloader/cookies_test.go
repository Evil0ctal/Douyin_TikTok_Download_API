package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// A made-up token in the shape TikTok issues; no real session is behind it.
const testChainToken = "AAAbbbCCC111-_tokenvalue"

func TestFetchSendsABoundCookieOnlyInsideItsDomain(t *testing.T) {
	var mu sync.Mutex
	seen := map[string]string{}
	record := func(label string, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		seen[label] = r.Header.Get("Cookie")
	}
	// The hop off the cookie's domain: still on the allowlist, so the file is
	// fetched, but a host the cookie does not belong to.
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		record("elsewhere", r)
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("mp4"))
	}))
	defer elsewhere.Close()
	elsewhereByName := strings.Replace(elsewhere.URL, "127.0.0.1", "localhost", 1)
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		record("origin", r)
		http.Redirect(w, r, elsewhereByName, http.StatusFound)
	}))
	defer origin.Close()

	_, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{"127.0.0.1", "localhost"},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
		Cookies:  []cookieSpec{{Name: "tt_chain_token", Value: testChainToken, Domain: "127.0.0.1"}},
	})
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if want := "tt_chain_token=" + testChainToken; seen["origin"] != want {
		t.Errorf("origin got Cookie %q, want %q", seen["origin"], want)
	}
	if seen["elsewhere"] != "" {
		t.Errorf("the hop outside the cookie's domain got Cookie %q", seen["elsewhere"])
	}
}

func TestFetchSendsNoCookieWhenTheJobHasNone(t *testing.T) {
	var got string
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("Cookie")
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("mp4"))
	}))
	defer origin.Close()

	if _, err := localFetcher(t).fetch(context.Background(), transferSpec{
		Mirrors:  []string{origin.URL},
		Domains:  []string{hostOf(t, origin.URL)},
		Accept:   []string{"video/"},
		MaxBytes: 1 << 20,
		Dest:     filepath.Join(t.TempDir(), "video.mp4"),
	}); err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if got != "" {
		t.Errorf("a job with no cookies sent Cookie %q", got)
	}
}

func TestSubmitRefusesACookieOutsideTheExemption(t *testing.T) {
	api, _ := testServer(t, t.TempDir())
	cases := map[string]struct {
		platform string
		cookie   map[string]any
	}{
		"a name the platform does not bind links to": {
			"tiktok", map[string]any{"name": "sessionid", "value": "abc", "domain": "tiktok.com"},
		},
		"a platform with no exemption at all": {
			"douyin", map[string]any{"name": "tt_chain_token", "value": "abc", "domain": "tiktok.com"},
		},
		"a domain outside the job's allowlist": {
			"tiktok", map[string]any{"name": "tt_chain_token", "value": "abc", "domain": "example.com"},
		},
		"a value that would start a second cookie": {
			"tiktok", map[string]any{"name": "tt_chain_token", "value": "abc; sessionid=x", "domain": "tiktok.com"},
		},
		"a value that would start a second header": {
			"tiktok", map[string]any{"name": "tt_chain_token", "value": "abc\r\nX-Evil: 1", "domain": "tiktok.com"},
		},
		"an empty value": {
			"tiktok", map[string]any{"name": "tt_chain_token", "value": "", "domain": "tiktok.com"},
		},
	}
	for label, tc := range cases {
		recorder := post(t, api.routes(), "/jobs", map[string]any{
			"id": "cookie-refused", "platform": tc.platform, "author_uid": "a", "content_id": "b",
			"domains": []string{"tiktok.com"},
			"cookies": []map[string]any{tc.cookie},
			"items": []map[string]any{
				{"name": "video.mp4", "mirrors": []string{"https://v16-webapp-prime.tiktok.com/v"},
					"accept": []string{"video/"}, "max_bytes": 10},
			},
		})
		if recorder.Code != http.StatusBadRequest {
			t.Errorf("%s: expected 400, got %d", label, recorder.Code)
		}
		if strings.Contains(recorder.Body.String(), "abc") {
			t.Errorf("%s: the refusal quoted the cookie's value: %s", label, recorder.Body.String())
		}
	}
}

func TestAJobNeverShowsItsCookie(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer origin.Close()

	api, jobs := testServer(t, t.TempDir())
	handler := api.routes()
	recorder := post(t, handler, "/jobs", map[string]any{
		"id": "cookie-hidden", "platform": "tiktok", "author_uid": "a", "content_id": "b",
		"domains": []string{hostOf(t, origin.URL)},
		"cookies": []map[string]any{
			{"name": "tt_chain_token", "value": testChainToken, "domain": hostOf(t, origin.URL)},
		},
		"items": []map[string]any{
			{"name": "video.mp4", "kind": "video", "mirrors": []string{origin.URL},
				"accept": []string{"video/"}, "max_bytes": 1 << 20},
		},
	})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("submit = %d: %s", recorder.Code, recorder.Body.String())
	}
	waitFor(t, jobs, "cookie-hidden")

	for _, path := range []string{"/jobs/cookie-hidden", "/jobs"} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		body, _ := io.ReadAll(response.Body)
		if strings.Contains(string(body), testChainToken) {
			t.Errorf("GET %s showed the cookie: %s", path, body)
		}
	}
}
