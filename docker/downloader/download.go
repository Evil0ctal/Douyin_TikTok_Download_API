package main

// Moving one file from a CDN onto the operator's disk, safely.
//
// Four rules hold for every transfer here, and each one exists because of a
// specific way this component could hurt the person running it:
//
//  1. The ceiling is counted on bytes actually written. Content-Length is a
//     claim by the server, and size_bytes from the platform is documented as
//     "int | None" - both of them lie, and a ceiling that trusts either is a
//     ceiling that fills the disk.
//  2. A file appears under its real name only after it is complete and its
//     digest is known. Everything in flight is a ".part", so a half file can
//     never be mistaken for an archive.
//  3. The name is ours. CDN-supplied filenames arrive from the same place the
//     bytes do and have no business deciding where those bytes land.
//  4. No credentials, ever. This process holds no cookies and no master key; a
//     mirror that needs a login is a mirror this job fails on.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// transferResult is what one successful mirror produced.
type transferResult struct {
	Bytes       int64
	SHA256      string
	ContentType string
	Host        string
}

// transferSpec is one file to fetch, already decided by the main service.
type transferSpec struct {
	Mirrors   []string
	Domains   []string
	Accept    []string
	MaxBytes  int64
	Dest      string
	UserAgent string
	Referer   string
}

// fetcher owns the shared transport and the redirect policy.
type fetcher struct {
	transport    *http.Transport
	maxRedirects int
	timeout      time.Duration
}

func newFetcher(maxRedirects int, timeout time.Duration) *fetcher {
	return newFetcherWith(maxRedirects, timeout, dialGuard)
}

// newFetcherWith takes the dial guard as an argument so the tests can point a
// fetcher at a local httptest server. Production always passes dialGuard; a
// loopback address is exactly what it refuses, which is why the tests cannot
// use the production constructor and why that is the right behaviour.
func newFetcherWith(maxRedirects int, timeout time.Duration, control func(string, string, syscall.RawConn) error) *fetcher {
	dialer := &net.Dialer{
		Timeout:   15 * time.Second,
		KeepAlive: 30 * time.Second,
		Control:   control,
	}
	return &fetcher{
		transport: &http.Transport{
			DialContext:           dialer.DialContext,
			MaxIdleConns:          32,
			IdleConnTimeout:       60 * time.Second,
			TLSHandshakeTimeout:   15 * time.Second,
			ResponseHeaderTimeout: 30 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			// The CDNs answer video/mp4 and image/jpeg, both already
			// compressed. Asking for gzip would only make the byte ceiling
			// count compressed bytes while the disk fills with decompressed
			// ones.
			DisableCompression: true,
		},
		maxRedirects: maxRedirects,
		timeout:      timeout,
	}
}

func (f *fetcher) close() {
	f.transport.CloseIdleConnections()
}

// client builds an HTTP client whose redirect policy is bound to one job's
// allowlist. The transport - and so the connection pool and the dial guard -
// is shared; only the policy varies.
func (f *fetcher) client(domains []string) *http.Client {
	return &http.Client{
		Transport: f.transport,
		Timeout:   f.timeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= f.maxRedirects {
				return fmt.Errorf("stopped after %d redirects", f.maxRedirects)
			}
			// Every hop is re-validated, not just the first. A redirect is an
			// instruction from the far end, and an allowlist checked once is
			// an allowlist a 302 walks straight out of.
			return checkTarget(req.URL, domains)
		},
	}
}

func checkTarget(target *url.URL, domains []string) error {
	if target.Scheme != "https" && target.Scheme != "http" {
		return fmt.Errorf("scheme %q is not fetchable", target.Scheme)
	}
	if target.User != nil {
		return errors.New("a URL carrying credentials is refused")
	}
	host := target.Hostname()
	if !allowedHost(host, domains) {
		return fmt.Errorf("host %s is not an allowed media domain", host)
	}
	return nil
}

// fetch tries each mirror in turn and returns the first that lands.
//
// Mirrors are alternates for the same bytes, so a failure on one is not a
// failure of the item: a signed link that expired, a regional edge that 403s,
// and a genuinely dead file all look the same from here, and only the last of
// them should fail the item.
func (f *fetcher) fetch(ctx context.Context, spec transferSpec) (transferResult, error) {
	if len(spec.Mirrors) == 0 {
		return transferResult{}, errors.New("no mirror survived the allowlist")
	}
	var lastErr error
	for _, mirror := range spec.Mirrors {
		result, err := f.fetchOne(ctx, spec, mirror)
		if err == nil {
			return result, nil
		}
		if ctx.Err() != nil {
			return transferResult{}, ctx.Err()
		}
		lastErr = err
	}
	return transferResult{}, lastErr
}

func (f *fetcher) fetchOne(ctx context.Context, spec transferSpec, mirror string) (transferResult, error) {
	target, err := url.Parse(mirror)
	if err != nil {
		return transferResult{}, fmt.Errorf("unparseable mirror: %w", err)
	}
	if err := checkTarget(target, spec.Domains); err != nil {
		return transferResult{}, err
	}
	host := target.Hostname()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return transferResult{}, err
	}
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Accept-Encoding", "identity")
	if spec.UserAgent != "" {
		request.Header.Set("User-Agent", spec.UserAgent)
	}
	if spec.Referer != "" {
		request.Header.Set("Referer", spec.Referer)
	}

	response, err := f.client(spec.Domains).Do(request)
	if err != nil {
		return transferResult{}, fmt.Errorf("%s: %w", host, err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		_ = response.Body.Close()
	}()

	if response.StatusCode != http.StatusOK {
		return transferResult{}, fmt.Errorf("%s answered %d", host, response.StatusCode)
	}
	contentType := response.Header.Get("Content-Type")
	if !acceptableType(contentType, spec.Accept) {
		// A login page, a JSON error or an HTML interstitial all arrive as a
		// 200 with the wrong type. Refusing here is what keeps them off the
		// disk under a name that says "video".
		return transferResult{}, fmt.Errorf("%s answered content-type %q, which is not a media type this job accepts", host, contentType)
	}
	// Advisory only: a truthful Content-Length saves a pointless transfer, and
	// a lying one changes nothing, because the real check is on bytes written.
	if spec.MaxBytes > 0 && response.ContentLength > spec.MaxBytes {
		return transferResult{}, fmt.Errorf("%s declares %d bytes, over the %d byte ceiling", host, response.ContentLength, spec.MaxBytes)
	}

	sum, written, err := f.spool(response.Body, spec)
	if err != nil {
		return transferResult{}, fmt.Errorf("%s: %w", host, err)
	}
	return transferResult{Bytes: written, SHA256: sum, ContentType: contentType, Host: host}, nil
}

// spool writes the body to "<dest>.part", then renames it into place.
//
// The rename is the commit point and it is atomic within a filesystem, so a
// process killed mid-transfer leaves a .part that the next run overwrites -
// never a truncated file wearing the name of a complete one.
func (f *fetcher) spool(body io.Reader, spec transferSpec) (string, int64, error) {
	if err := os.MkdirAll(filepath.Dir(spec.Dest), 0o750); err != nil {
		return "", 0, err
	}
	partial := spec.Dest + ".part"
	file, err := os.OpenFile(partial, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o640)
	if err != nil {
		return "", 0, err
	}
	// Removed unless the rename below succeeds, so a refusal never leaves
	// bytes behind that nothing accounts for.
	committed := false
	defer func() {
		_ = file.Close()
		if !committed {
			_ = os.Remove(partial)
		}
	}()

	digest := sha256.New()
	reader := io.Reader(body)
	if spec.MaxBytes > 0 {
		// One byte past the ceiling, so "exactly at the limit" and "over it"
		// are distinguishable rather than both looking like a full read.
		reader = io.LimitReader(body, spec.MaxBytes+1)
	}
	written, err := io.Copy(io.MultiWriter(file, digest), reader)
	if err != nil {
		return "", 0, err
	}
	if spec.MaxBytes > 0 && written > spec.MaxBytes {
		return "", 0, fmt.Errorf("exceeded the %d byte ceiling", spec.MaxBytes)
	}
	if written == 0 {
		return "", 0, errors.New("the mirror returned an empty body")
	}
	if err := file.Sync(); err != nil {
		return "", 0, err
	}
	if err := file.Close(); err != nil {
		return "", 0, err
	}
	if err := os.Rename(partial, spec.Dest); err != nil {
		return "", 0, err
	}
	committed = true
	return hex.EncodeToString(digest.Sum(nil)), written, nil
}

// hostsOf lists the distinct hosts a set of mirrors points at, for a log line
// that says where bytes came from without reprinting a signed URL.
func hostsOf(mirrors []string) string {
	seen := make(map[string]struct{}, len(mirrors))
	names := make([]string, 0, len(mirrors))
	for _, mirror := range mirrors {
		parsed, err := url.Parse(mirror)
		if err != nil {
			continue
		}
		host := parsed.Hostname()
		if _, done := seen[host]; done || host == "" {
			continue
		}
		seen[host] = struct{}{}
		names = append(names, host)
	}
	return strings.Join(names, ",")
}
