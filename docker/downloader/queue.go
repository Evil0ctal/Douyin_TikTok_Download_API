package main

// The job queue: bounded, in memory, and deliberately forgetful.
//
// Durability lives in Postgres on the main service's side, which is the only
// place that knows what a download means. This process keeps just enough state
// to answer "how is job X going" while it is going, plus a short history so a
// poller that blinked can still collect the result. A restart loses the queue,
// and that is the correct trade: the main service re-submits, and the files
// already on disk are the part that had to survive.
//
// Bounded on purpose. An unbounded queue converts a burst of submissions into
// memory growth in the one container that also holds large buffers, and the
// whole reason this service is separate is that its overload must not become
// everyone else's.

import (
	"context"
	"errors"
	"log/slog"
	"sort"
	"sync"
	"time"
)

type jobState string

const (
	stateQueued    jobState = "queued"
	stateRunning   jobState = "running"
	stateDone      jobState = "done"
	statePartial   jobState = "partial"
	stateFailed    jobState = "failed"
	stateCancelled jobState = "cancelled"
)

func (s jobState) terminal() bool {
	return s == stateDone || s == statePartial || s == stateFailed || s == stateCancelled
}

// item is one file inside a job.
type item struct {
	Name        string   `json:"name"`
	Kind        string   `json:"kind"`
	Mirrors     []string `json:"mirrors"`
	Accept      []string `json:"accept"`
	MaxBytes    int64    `json:"max_bytes"`
	State       string   `json:"state"`
	Bytes       int64    `json:"bytes"`
	SHA256      string   `json:"sha256,omitempty"`
	ContentType string   `json:"content_type,omitempty"`
	Error       string   `json:"error,omitempty"`
}

// job is one content key's media, as submitted by the main service.
type job struct {
	ID         string     `json:"id"`
	Platform   string     `json:"platform"`
	AuthorUID  string     `json:"author_uid"`
	ContentID  string     `json:"content_id"`
	Directory  string     `json:"directory"`
	State      jobState   `json:"state"`
	Items      []*item    `json:"items"`
	Domains    []string   `json:"-"`
	UserAgent  string     `json:"-"`
	Referer    string     `json:"-"`
	BytesTotal int64      `json:"bytes_total"`
	Error      string     `json:"error,omitempty"`
	CreatedAt  time.Time  `json:"created_at"`
	StartedAt  *time.Time `json:"started_at,omitempty"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`

	cancel context.CancelFunc
}

// snapshot copies a job for serialization while the manager lock is held.
//
// Handing the live pointer to a JSON encoder would race a worker writing item
// results into it, which is the kind of bug that shows up as a corrupt
// response once a week and never in a test.
func (j *job) snapshot() *job {
	clone := *j
	clone.cancel = nil
	clone.Items = make([]*item, 0, len(j.Items))
	for _, source := range j.Items {
		copied := *source
		clone.Items = append(clone.Items, &copied)
	}
	return &clone
}

var (
	errQueueFull = errors.New("the download queue is full")
	errNotFound  = errors.New("no such job")
)

type manager struct {
	mu      sync.Mutex
	jobs    map[string]*job
	order   []string
	queue   chan *job
	history int

	fetcher     *fetcher
	root        string
	itemWorkers int
	log         *slog.Logger
	wg          sync.WaitGroup
}

func newManager(root string, f *fetcher, queueSize, itemWorkers, history int, logger *slog.Logger) *manager {
	return &manager{
		jobs:        make(map[string]*job),
		queue:       make(chan *job, queueSize),
		history:     history,
		fetcher:     f,
		root:        root,
		itemWorkers: itemWorkers,
		log:         logger,
	}
}

func (m *manager) start(ctx context.Context, workers int) {
	for i := 0; i < workers; i++ {
		m.wg.Add(1)
		go func() {
			defer m.wg.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case queued, ok := <-m.queue:
					if !ok {
						return
					}
					m.run(ctx, queued)
				}
			}
		}()
	}
}

func (m *manager) wait() {
	m.wg.Wait()
}

// submit accepts a job, or refuses it because the queue is full.
//
// Refusing is the point. Accepting work this service cannot get to is how a
// caller learns nothing is wrong until the disk is full and every job is an
// hour old; a 429 lets the main service back off while it still can.
func (m *manager) submit(candidate *job) error {
	m.mu.Lock()
	if _, exists := m.jobs[candidate.ID]; exists {
		m.mu.Unlock()
		// Idempotent by id: the main service retries a submission it never saw
		// an answer to, and a retry must not download the same file twice.
		return nil
	}
	m.jobs[candidate.ID] = candidate
	m.order = append(m.order, candidate.ID)
	m.evictLocked()
	m.mu.Unlock()

	select {
	case m.queue <- candidate:
		return nil
	default:
		m.mu.Lock()
		delete(m.jobs, candidate.ID)
		m.dropOrderLocked(candidate.ID)
		m.mu.Unlock()
		return errQueueFull
	}
}

func (m *manager) get(id string) (*job, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	found, ok := m.jobs[id]
	if !ok {
		return nil, errNotFound
	}
	return found.snapshot(), nil
}

func (m *manager) list(limit int) []*job {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]*job, 0, len(m.order))
	for i := len(m.order) - 1; i >= 0; i-- {
		found, ok := m.jobs[m.order[i]]
		if !ok {
			continue
		}
		out = append(out, found.snapshot())
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	sort.SliceStable(out, func(a, b int) bool { return out[a].CreatedAt.After(out[b].CreatedAt) })
	return out
}

// cancel stops a job that is queued or running.
//
// A queued job is marked cancelled and skipped when a worker reaches it,
// rather than being pulled out of the channel: removing an element from the
// middle of a Go channel is not a thing, and a flag read at the moment of
// execution is both simpler and correct.
func (m *manager) cancel(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	found, ok := m.jobs[id]
	if !ok {
		return errNotFound
	}
	if found.State.terminal() {
		return nil
	}
	found.State = stateCancelled
	now := time.Now().UTC()
	found.FinishedAt = &now
	if found.cancel != nil {
		found.cancel()
	}
	return nil
}

func (m *manager) stats() (queued, running int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, found := range m.jobs {
		switch found.State {
		case stateQueued:
			queued++
		case stateRunning:
			running++
		}
	}
	return queued, running
}

func (m *manager) evictLocked() {
	if m.history <= 0 {
		return
	}
	for len(m.order) > m.history {
		oldest := m.order[0]
		if found, ok := m.jobs[oldest]; ok && !found.State.terminal() {
			// Never forget work still in flight; the cap is on history, not on
			// the queue, and dropping a running job would strand its poller.
			break
		}
		delete(m.jobs, oldest)
		m.order = m.order[1:]
	}
}

func (m *manager) dropOrderLocked(id string) {
	for index, candidate := range m.order {
		if candidate == id {
			m.order = append(m.order[:index], m.order[index+1:]...)
			return
		}
	}
}

// run executes one job: every item, concurrently, bounded.
func (m *manager) run(parent context.Context, current *job) {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()

	m.mu.Lock()
	if current.State == stateCancelled {
		m.mu.Unlock()
		return
	}
	now := time.Now().UTC()
	current.State = stateRunning
	current.StartedAt = &now
	current.cancel = cancel
	items := current.Items
	domains := current.Domains
	userAgent := current.UserAgent
	referer := current.Referer
	m.mu.Unlock()

	directory, err := safeJoin(m.root, current.Platform, current.AuthorUID, current.ContentID)
	if err != nil {
		m.finish(current, stateFailed, err.Error())
		return
	}

	slots := make(chan struct{}, max(1, m.itemWorkers))
	var group sync.WaitGroup
	for _, target := range items {
		group.Add(1)
		go func(current *item) {
			defer group.Done()
			slots <- struct{}{}
			defer func() { <-slots }()
			m.fetchItem(ctx, current, directory, domains, userAgent, referer)
		}(target)
	}
	group.Wait()

	if ctx.Err() != nil {
		m.finish(current, stateCancelled, "")
		return
	}
	m.settle(current)
}

func (m *manager) fetchItem(ctx context.Context, target *item, directory string, domains []string, userAgent, referer string) {
	destination, err := safeJoin(directory, target.Name)
	if err != nil {
		m.recordItem(target, "failed", 0, "", "", err.Error())
		return
	}
	result, err := m.fetcher.fetch(ctx, transferSpec{
		Mirrors:   target.Mirrors,
		Domains:   domains,
		Accept:    target.Accept,
		MaxBytes:  target.MaxBytes,
		Dest:      destination,
		UserAgent: userAgent,
		Referer:   referer,
	})
	if err != nil {
		m.log.Warn("item failed",
			"name", target.Name, "kind", target.Kind,
			"hosts", hostsOf(target.Mirrors), "error", err.Error())
		m.recordItem(target, "failed", 0, "", "", err.Error())
		return
	}
	m.log.Info("item stored",
		"name", target.Name, "kind", target.Kind,
		"host", result.Host, "bytes", result.Bytes)
	m.recordItem(target, "done", result.Bytes, result.SHA256, result.ContentType, "")
}

func (m *manager) recordItem(target *item, state string, bytes int64, sum, contentType, failure string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	target.State = state
	target.Bytes = bytes
	target.SHA256 = sum
	target.ContentType = contentType
	target.Error = failure
}

// settle decides the job's outcome from its items.
//
// "partial" is its own state rather than a success or a failure. A post whose
// video landed and whose third image 404'd is neither: reporting it as done
// hides a gap, and reporting it as failed hides a video that is on the disk.
func (m *manager) settle(current *job) {
	m.mu.Lock()
	var done, failed int
	var total int64
	for _, target := range current.Items {
		switch target.State {
		case "done":
			done++
			total += target.Bytes
		default:
			failed++
		}
	}
	current.BytesTotal = total
	m.mu.Unlock()

	switch {
	case failed == 0:
		m.finish(current, stateDone, "")
	case done == 0:
		m.finish(current, stateFailed, "every item failed")
	default:
		m.finish(current, statePartial, "")
	}
}

func (m *manager) finish(current *job, state jobState, failure string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if current.State == stateCancelled && state != stateCancelled {
		return
	}
	now := time.Now().UTC()
	current.State = state
	current.Error = failure
	current.FinishedAt = &now
	current.cancel = nil
	m.evictLocked()
	m.log.Info("job finished",
		"job", current.ID, "state", string(state),
		"bytes", current.BytesTotal, "items", len(current.Items))
}
