package main

// The media volume: what is on it, and removing what the main service says to
// remove.
//
// Nothing here decides what to delete. The eviction policy - a size ceiling,
// oldest first, pinned files exempt - lives in the main service, because it is
// the only side that knows which download an operator pinned and when each one
// finished. This file is the pair of hands: it measures, and it removes exactly
// the directories it is named, refusing anything that is not one.
//
// That split matters more than it looks. A policy implemented here would be a
// second policy, running against a different view of the same facts, deleting
// files while the console showed them as kept.

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// entry is one content directory: <platform>/<author_uid>/<content_id>.
type entry struct {
	Path       string    `json:"path"`
	Platform   string    `json:"platform"`
	AuthorUID  string    `json:"author_uid"`
	ContentID  string    `json:"content_id"`
	Files      int       `json:"files"`
	Bytes      int64     `json:"bytes"`
	ModifiedAt time.Time `json:"modified_at"`
}

type usage struct {
	Root       string  `json:"root"`
	TotalBytes int64   `json:"total_bytes"`
	Entries    []entry `json:"entries"`
}

// scan walks the media root three levels deep and totals each content
// directory.
//
// Three levels exactly, because that is the layout this service writes. A
// generic recursive walk would also happily total whatever else got mounted at
// the same path, and then report it as media the operator could free.
func scan(root string) (usage, error) {
	result := usage{Root: root, Entries: []entry{}}
	platforms, err := os.ReadDir(root)
	if errors.Is(err, fs.ErrNotExist) {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	for _, platform := range platforms {
		if !platform.IsDir() {
			continue
		}
		authors, err := os.ReadDir(filepath.Join(root, platform.Name()))
		if err != nil {
			continue
		}
		for _, author := range authors {
			if !author.IsDir() {
				continue
			}
			contents, err := os.ReadDir(filepath.Join(root, platform.Name(), author.Name()))
			if err != nil {
				continue
			}
			for _, content := range contents {
				if !content.IsDir() {
					continue
				}
				directory := filepath.Join(root, platform.Name(), author.Name(), content.Name())
				measured, err := measure(directory)
				if err != nil {
					continue
				}
				measured.Path = path.Join(platform.Name(), author.Name(), content.Name())
				measured.Platform = platform.Name()
				measured.AuthorUID = author.Name()
				measured.ContentID = content.Name()
				result.Entries = append(result.Entries, measured)
				result.TotalBytes += measured.Bytes
			}
		}
	}
	sort.Slice(result.Entries, func(a, b int) bool {
		return result.Entries[a].ModifiedAt.Before(result.Entries[b].ModifiedAt)
	})
	return result, nil
}

// measure totals one content directory.
//
// A ".part" counts toward the total even though it is not a finished file: it
// occupies the same disk, and a ceiling that ignored in-flight bytes would be
// a ceiling that a large enough transfer walks straight through.
func measure(directory string) (entry, error) {
	listing, err := os.ReadDir(directory)
	if err != nil {
		return entry{}, err
	}
	result := entry{}
	for _, file := range listing {
		if file.IsDir() {
			continue
		}
		info, err := file.Info()
		if err != nil {
			continue
		}
		result.Files++
		result.Bytes += info.Size()
		if info.ModTime().After(result.ModifiedAt) {
			result.ModifiedAt = info.ModTime().UTC()
		}
	}
	return result, nil
}

// removeAll deletes the named content directories and reports what that freed.
//
// Each path is validated segment by segment against the same rules that built
// it, so a caller cannot name "../../etc" or a bare platform directory - only
// something exactly three levels deep, which is what this service creates and
// all it may destroy. Empty parents are then pruned, so a deleted author does
// not leave a tree of empty folders behind.
func removeAll(root string, paths []string) (int64, []string, error) {
	var freed int64
	removed := make([]string, 0, len(paths))
	for _, candidate := range paths {
		parts := strings.Split(strings.Trim(strings.TrimSpace(candidate), "/"), "/")
		if len(parts) != 3 {
			return freed, removed, fmt.Errorf("%q is not a <platform>/<author>/<content> path", candidate)
		}
		directory, err := safeJoin(root, parts[0], parts[1], parts[2])
		if err != nil {
			return freed, removed, err
		}
		measured, err := measure(directory)
		if errors.Is(err, fs.ErrNotExist) {
			// Already gone is the desired end state, not an error: the main
			// service may be retrying a sweep it never saw the answer to.
			removed = append(removed, candidate)
			continue
		}
		if err != nil {
			return freed, removed, err
		}
		if err := os.RemoveAll(directory); err != nil {
			return freed, removed, err
		}
		freed += measured.Bytes
		removed = append(removed, candidate)
		pruneEmpty(root, filepath.Dir(directory))
	}
	return freed, removed, nil
}

// pruneEmpty removes now-empty parent directories, stopping at the root.
func pruneEmpty(root, directory string) {
	cleanRoot := filepath.Clean(root)
	for directory != cleanRoot && strings.HasPrefix(directory, cleanRoot+string(os.PathSeparator)) {
		listing, err := os.ReadDir(directory)
		if err != nil || len(listing) > 0 {
			return
		}
		if err := os.Remove(directory); err != nil {
			return
		}
		directory = filepath.Dir(directory)
	}
}
