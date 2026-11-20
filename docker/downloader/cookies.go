package main

// The one exception to "no credentials, ever".
//
// TikTok's video links carry `tk=tt_chain_token`, and its CDN serves the file
// only to a request presenting the tt_chain_token cookie of the session the
// link was issued to. Anyone else gets a 403, so without that cookie no TikTok
// video could be stored at all (#831; v4 met the same wall as #517).
//
// The token is not a login. TikTok hands one to any anonymous visitor, it names
// no account, and the only door it opens here is the media links issued to it.
// The exception is held to exactly that:
//
//   - only a name boundCookies lists for the job's platform, so a job cannot
//     bring a sessionid in under some other label;
//   - only to hosts inside the cookie's own domain, checked again on every
//     redirect hop, the way a browser scopes a cookie;
//   - in memory for the life of the job: never on the volume, never in a job
//     listing, never in a log line, never quoted in an error.

import (
	"fmt"
	"slices"
	"strings"
)

// boundCookies is, per platform, every cookie its CDN binds media links to.
// A name missing here is refused at submission, whatever the main service
// sends.
var boundCookies = map[string][]string{
	"tiktok": {"tt_chain_token"},
}

// maxCookieValue is far above the ~24 characters a tt_chain_token measures,
// and far below anything that could carry a smuggled jar.
const maxCookieValue = 512

// cookieSpec is one cookie a job may present, and the domain it belongs to.
type cookieSpec struct {
	Name   string `json:"name"`
	Value  string `json:"value"`
	Domain string `json:"domain"`
}

// validateCookies refuses any cookie the exception above does not cover.
//
// The errors name the cookie and never its value: they go back to the main
// service and from there into a task result.
func validateCookies(platform string, domains []string, cookies []cookieSpec) error {
	allowed := boundCookies[platform]
	for _, cookie := range cookies {
		if !slices.Contains(allowed, cookie.Name) {
			return fmt.Errorf("cookie %q is not one this service carries for %s", cookie.Name, platform)
		}
		if !usableCookieValue(cookie.Value) {
			return fmt.Errorf("cookie %q has an unusable value", cookie.Name)
		}
		domain := strings.ToLower(strings.Trim(strings.TrimSpace(cookie.Domain), "."))
		if domain == "" || !allowedHost(domain, domains) {
			return fmt.Errorf("cookie %q is scoped to %q, outside this job's allowlist", cookie.Name, cookie.Domain)
		}
	}
	return nil
}

// usableCookieValue is RFC 6265's cookie-octet set: no space, quote, comma,
// semicolon, backslash or control character. A semicolon would start a second
// cookie inside the first, and a CR or LF a second header.
func usableCookieValue(value string) bool {
	if value == "" || len(value) > maxCookieValue {
		return false
	}
	for _, r := range value {
		if r < 0x21 || r > 0x7e || r == '"' || r == ',' || r == ';' || r == '\\' {
			return false
		}
	}
	return true
}

// cookieHeader is the Cookie header for one host: the cookies whose domain
// the host is inside, or "" when none are.
func cookieHeader(host string, cookies []cookieSpec) string {
	pairs := make([]string, 0, len(cookies))
	for _, cookie := range cookies {
		if allowedHost(host, []string{cookie.Domain}) {
			pairs = append(pairs, cookie.Name+"="+cookie.Value)
		}
	}
	return strings.Join(pairs, "; ")
}
