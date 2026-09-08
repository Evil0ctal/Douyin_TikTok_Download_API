package main

import (
	"net"
	"path/filepath"
	"testing"
)

func TestAllowedHostMatchesOnLabelBoundary(t *testing.T) {
	domains := []string{"tiktok.com", "douyinvod.com"}
	allowed := []string{
		"tiktok.com",
		"v16-webapp-prime.us.tiktok.com",
		"V9-V2-MPS-CDN.DOUYINVOD.COM",
		"douyinvod.com.",
	}
	for _, host := range allowed {
		if !allowedHost(host, domains) {
			t.Errorf("expected %q to be allowed", host)
		}
	}
	// The two failures a naive suffix check makes: a lookalike registrable
	// domain, and a host that merely contains the name.
	refused := []string{
		"eviltiktok.com",
		"tiktok.com.attacker.example",
		"notdouyinvod.com",
		"",
		"localhost",
	}
	for _, host := range refused {
		if allowedHost(host, domains) {
			t.Errorf("expected %q to be refused", host)
		}
	}
}

func TestRoutableIPRefusesEverythingInside(t *testing.T) {
	refused := []string{
		"127.0.0.1", "0.0.0.0", "10.1.2.3", "172.16.0.1", "192.168.1.1",
		"169.254.169.254", // the cloud metadata address
		"100.64.0.1",      // carrier NAT
		"192.0.2.1", "198.18.0.1", "198.51.100.1", "203.0.113.1",
		"240.0.0.1", "255.255.255.255",
		"::1", "::", "fc00::1", "fe80::1", "ff02::1",
		"::ffff:127.0.0.1", // IPv4-mapped loopback
		"::ffff:10.0.0.1",
		"2001:db8::1",
	}
	for _, raw := range refused {
		if routableIP(net.ParseIP(raw)) {
			t.Errorf("expected %s to be refused", raw)
		}
	}
	for _, raw := range []string{"1.1.1.1", "104.16.0.1", "2606:4700::1111"} {
		if !routableIP(net.ParseIP(raw)) {
			t.Errorf("expected %s to be routable", raw)
		}
	}
	if routableIP(nil) {
		t.Error("a nil address must never be dialed")
	}
}

func TestDialGuardRefusesLoopback(t *testing.T) {
	if err := dialGuard("tcp", "127.0.0.1:80", nil); err == nil {
		t.Fatal("expected the guard to refuse loopback")
	}
	if err := dialGuard("tcp", "1.1.1.1:443", nil); err != nil {
		t.Fatalf("expected a public address to pass: %v", err)
	}
}

func TestSafeJoinRefusesTraversal(t *testing.T) {
	root := t.TempDir()
	if _, err := safeJoin(root, "douyin", "..", "etc"); err == nil {
		t.Fatal("expected traversal to be refused")
	}
	if _, err := safeJoin(root, "douyin", "a/b", "c"); err == nil {
		t.Fatal("expected a separator inside a segment to be refused")
	}
	if _, err := safeJoin(root, ".hidden"); err == nil {
		t.Fatal("expected a leading dot to be refused")
	}
	joined, err := safeJoin(root, "douyin", "MS4wLjABAAAA_x-y", "7408915107113127220")
	if err != nil {
		t.Fatalf("expected a normal path to be built: %v", err)
	}
	if want := filepath.Join(root, "douyin", "MS4wLjABAAAA_x-y", "7408915107113127220"); joined != want {
		t.Fatalf("joined = %q, want %q", joined, want)
	}
}

func TestAcceptableType(t *testing.T) {
	accept := []string{"video/", "application/octet-stream"}
	for _, header := range []string{"video/mp4", "VIDEO/MP4; charset=binary", "application/octet-stream"} {
		if !acceptableType(header, accept) {
			t.Errorf("expected %q to be accepted", header)
		}
	}
	// An HTML interstitial or a JSON error arrives as a 200 with a wrong type;
	// so does an empty header, which is not a licence to store anything.
	for _, header := range []string{"text/html", "application/json", ""} {
		if acceptableType(header, accept) {
			t.Errorf("expected %q to be refused", header)
		}
	}
}
