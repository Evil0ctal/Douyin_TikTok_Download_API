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

// maxResumes is how many times one transfer asks a mirror for the rest of a
// file it cut off. A few, not unbounded: a mirror that cuts every connection
// is better given up on for the next one than retried forever.
const maxResumes = 3

// transferResult is what one successful mirror produced.
type transferResult struct {
	Bytes       int64
	SHA256      string
	ContentType string
	Host        string
	// How many times the mirror cut the transfer off and was asked for the
	// rest. Logged, so an edge that does it routinely is visible.
	Resumes int
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
	// Optional. Told when a mirror starts and after every write.
	Progress progress
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

	request, err := newRequest(ctx, spec, target)
	if err != nil {
		return transferResult{}, err
	}
	response, err := f.client(spec.Domains).Do(request)
	if err != nil {
		return transferResult{}, fmt.Errorf("%s: %w", host, err)
	}
	defer closeBody(response)

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
	if spec.Progress != nil {
		spec.Progress.begin(response.ContentLength)
	}

	rest := func(offset int64) (*http.Response, error) {
		return f.resume(ctx, spec, target, offset, response)
	}
	sum, written, resumes, err := f.spool(response.Body, spec, response.ContentLength, rest)
	if err != nil {
		return transferResult{}, fmt.Errorf("%s: %w", host, err)
	}
	return transferResult{Bytes: written, SHA256: sum, ContentType: contentType, Host: host, Resumes: resumes}, nil
}

func newRequest(ctx context.Context, spec transferSpec, target *url.URL) (*http.Request, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Accept-Encoding", "identity")
	if spec.UserAgent != "" {
		request.Header.Set("User-Agent", spec.UserAgent)
	}
	if spec.Referer != "" {
		request.Header.Set("Referer", spec.Referer)
	}
	return request, nil
}

func closeBody(response *http.Response) {
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	_ = response.Body.Close()
}

// resume asks the same mirror for the rest of a file it cut off.
//
// Bilibili's Akamai edge does this: measured 2026-10-19, a video it had not
// cached yet came back as a 200 that ended with "unexpected EOF" after exactly
// 2 MiB, every time, and the same request a minute later - once the edge had
// it - came back whole. Three downloads in five failed that way while every
// other mirror landed. Starting over from byte zero would only reach the same
// cut, so the transfer continues from where it stopped.
//
// Only ever the same object: If-Range carries the first answer's validator, so
// a file replaced in between comes back as a 200 and is refused rather than
// spliced onto the first one's head. The answer has to be a 206 whose range
// starts exactly where the bytes on disk end and runs to the length the first
// answer declared; anything else is refused.
func (f *fetcher) resume(ctx context.Context, spec transferSpec, target *url.URL, offset int64, first *http.Response) (*http.Response, error) {
	request, err := newRequest(ctx, spec, target)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Range", fmt.Sprintf("bytes=%d-", offset))
	if tag := first.Header.Get("ETag"); tag != "" && !strings.HasPrefix(tag, "W/") {
		request.Header.Set("If-Range", tag)
	} else if modified := first.Header.Get("Last-Modified"); modified != "" {
		request.Header.Set("If-Range", modified)
	}
	response, err := f.client(spec.Domains).Do(request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusPartialContent || !continuesAt(response.Header.Get("Content-Range"), offset, first.ContentLength) {
		closeBody(response)
		return nil, fmt.Errorf("asked for the rest from byte %d, answered %d %q", offset, response.StatusCode, response.Header.Get("Content-Range"))
	}
	return response, nil
}

// continuesAt says whether a Content-Range is exactly the rest of the file.
func continuesAt(header string, offset, total int64) bool {
	var start, end, size int64
	if _, err := fmt.Sscanf(header, "bytes %d-%d/%d", &start, &end, &size); err != nil {
		return false
	}
	return start == offset && end == total-1 && size == total
}

// cutOff marks an error that came from reading the body rather than from
// writing the file. Only the first is worth asking a mirror about again: a
// full disk is not something the far end can fix.
type cutOff struct{ err error }

func (c *cutOff) Error() string { return c.err.Error() }
func (c *cutOff) Unwrap() error { return c.err }

type sourceReader struct{ from io.Reader }

func (r sourceReader) Read(chunk []byte) (int, error) {
	n, err := r.from.Read(chunk)
	if err != nil && !errors.Is(err, io.EOF) {
		err = &cutOff{err: err}
	}
	return n, err
}

// spool writes the body to "<dest>.part", then renames it into place.
//
// The rename is the commit point and it is atomic within a filesystem, so a
// process killed mid-transfer leaves a .part that the next run overwrites -
// never a truncated file wearing the name of a complete one.
//
// A body the far end cuts off short of the length it declared is continued
// through rest, up to maxResumes times; the digest, the ceiling and the
// progress all carry on across the join, so the file is hashed and counted as
// the one file it is.
func (f *fetcher) spool(body io.Reader, spec transferSpec, declared int64, rest func(int64) (*http.Response, error)) (string, int64, int, error) {
	if err := os.MkdirAll(filepath.Dir(spec.Dest), 0o750); err != nil {
		return "", 0, 0, err
	}
	partial := spec.Dest + ".part"
	file, err := os.OpenFile(partial, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o640)
	if err != nil {
		return "", 0, 0, err
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
	sinks := []io.Writer{file, digest}
	if spec.Progress != nil {
		sinks = append(sinks, &progressWriter{to: spec.Progress})
	}
	sink := io.MultiWriter(sinks...)

	var written int64
	resumed := 0
	source := body
	for ; ; resumed++ {
		reader := io.Reader(sourceReader{from: source})
		if spec.MaxBytes > 0 {
			// One byte past the ceiling, so "exactly at the limit" and "over
			// it" are distinguishable rather than both looking like a full read.
			reader = io.LimitReader(reader, spec.MaxBytes+1-written)
		}
		n, err := io.Copy(sink, reader)
		written += n
		if err == nil {
			break
		}
		var cut *cutOff
		if !errors.As(err, &cut) || rest == nil || declared <= 0 || written >= declared || resumed >= maxResumes {
			return "", 0, 0, err
		}
		next, resumeErr := rest(written)
		if resumeErr != nil {
			return "", 0, 0, fmt.Errorf("%w (then %v)", cut.err, resumeErr)
		}
		defer closeBody(next)
		source = next.Body
	}
	if spec.MaxBytes > 0 && written > spec.MaxBytes {
		return "", 0, 0, fmt.Errorf("exceeded the %d byte ceiling", spec.MaxBytes)
	}
	if written == 0 {
		return "", 0, 0, errors.New("the mirror returned an empty body")
	}
	if err := file.Sync(); err != nil {
		return "", 0, 0, err
	}
	if err := file.Close(); err != nil {
		return "", 0, 0, err
	}
	if err := os.Rename(partial, spec.Dest); err != nil {
		return "", 0, 0, err
	}
	committed = true
	return hex.EncodeToString(digest.Sum(nil)), written, resumed, nil
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
