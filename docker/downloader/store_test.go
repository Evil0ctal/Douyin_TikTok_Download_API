package main

import (
	"os"
	"path/filepath"
	"testing"
)

func seed(t *testing.T, root, platform, author, content, name string, size int) {
	t.Helper()
	directory := filepath.Join(root, platform, author, content)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, name), make([]byte, size), 0o640); err != nil {
		t.Fatal(err)
	}
}

func TestScanTotalsByContentDirectory(t *testing.T) {
	root := t.TempDir()
	seed(t, root, "douyin", "MS4wA", "7408", "video.mp4", 100)
	seed(t, root, "douyin", "MS4wA", "7408", "cover.jpg", 50)
	seed(t, root, "tiktok", "12345", "7667", "video.mp4", 30)
	// A stray file at the root is not media; totalling it would offer an
	// operator space this service cannot free.
	if err := os.WriteFile(filepath.Join(root, "stray.txt"), make([]byte, 999), 0o640); err != nil {
		t.Fatal(err)
	}

	measured, err := scan(root)
	if err != nil {
		t.Fatal(err)
	}
	if measured.TotalBytes != 180 {
		t.Errorf("total = %d, want 180", measured.TotalBytes)
	}
	if len(measured.Entries) != 2 {
		t.Fatalf("entries = %d, want 2", len(measured.Entries))
	}
	for _, found := range measured.Entries {
		if found.Path == "douyin/MS4wA/7408" && (found.Bytes != 150 || found.Files != 2) {
			t.Errorf("douyin entry = %d bytes in %d files, want 150 in 2", found.Bytes, found.Files)
		}
	}
}

func TestScanOnAMissingRootIsEmptyNotAnError(t *testing.T) {
	measured, err := scan(filepath.Join(t.TempDir(), "never-created"))
	if err != nil {
		t.Fatalf("expected an absent root to be empty, got %v", err)
	}
	if measured.TotalBytes != 0 || len(measured.Entries) != 0 {
		t.Error("an absent root reported contents")
	}
}

func TestRemoveAllFreesAndPrunes(t *testing.T) {
	root := t.TempDir()
	seed(t, root, "douyin", "MS4wA", "7408", "video.mp4", 100)
	freed, removed, err := removeAll(root, []string{"douyin/MS4wA/7408"})
	if err != nil {
		t.Fatal(err)
	}
	if freed != 100 || len(removed) != 1 {
		t.Fatalf("freed = %d in %d directories, want 100 in 1", freed, len(removed))
	}
	// The empty author and platform directories go too, so the volume does not
	// accumulate a skeleton of everything ever collected.
	if _, err := os.Stat(filepath.Join(root, "douyin")); !os.IsNotExist(err) {
		t.Error("empty parents were not pruned")
	}
}

func TestRemoveAllRefusesAnythingButAContentDirectory(t *testing.T) {
	root := t.TempDir()
	seed(t, root, "douyin", "MS4wA", "7408", "video.mp4", 10)
	for _, candidate := range []string{"douyin", "douyin/MS4wA", "../../etc", "douyin/../../etc/passwd", "/"} {
		if _, _, err := removeAll(root, []string{candidate}); err == nil {
			t.Errorf("expected %q to be refused", candidate)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "douyin", "MS4wA", "7408", "video.mp4")); err != nil {
		t.Error("a refused delete removed something anyway")
	}
}

func TestRemoveAllIsIdempotent(t *testing.T) {
	root := t.TempDir()
	seed(t, root, "douyin", "MS4wA", "7408", "video.mp4", 10)
	if _, _, err := removeAll(root, []string{"douyin/MS4wA/7408"}); err != nil {
		t.Fatal(err)
	}
	// A retried sweep must not fail on what it already removed.
	freed, removed, err := removeAll(root, []string{"douyin/MS4wA/7408"})
	if err != nil || freed != 0 || len(removed) != 1 {
		t.Fatalf("second removal: freed %d, removed %d, err %v", freed, len(removed), err)
	}
}
