package main

// Live progress for a file in flight.
//
// The durable result of a job is written once, when it settles. Everything here
// is for the minutes before that: an item's size used to be recorded only on
// completion, so a 100 MB Bilibili video showed its cover's 366 KB for the
// whole minute and a half it took, and nothing on the page moved.

import "time"

// meterWindow is how far back a transfer rate looks. Long enough not to jitter
// with every 32 KB write, short enough to follow a CDN that slows down.
const meterWindow = 2 * time.Second

// meterSettle is how long a transfer runs before it has a rate at all. The
// first write lands within a millisecond of the response headers, and 32 KB
// over one millisecond is a speed no link here has.
const meterSettle = 500 * time.Millisecond

// meter turns a running byte count into a recent rate.
//
// It keeps two samples and rolls them once the newer is meterWindow old, so a
// rate always covers the last two to four seconds. The rate is taken against
// the clock at read time rather than at the last write: a stalled connection
// writes nothing, and a rate frozen at its last value would report a stall as
// full speed.
type meter struct {
	at    [2]time.Time
	bytes [2]int64
}

func (m *meter) reset(now time.Time) {
	m.at = [2]time.Time{now, now}
	m.bytes = [2]int64{}
}

func (m *meter) observe(now time.Time, total int64) {
	if now.Sub(m.at[1]) < meterWindow {
		return
	}
	m.at[0], m.bytes[0] = m.at[1], m.bytes[1]
	m.at[1], m.bytes[1] = now, total
}

func (m *meter) rate(now time.Time, total int64) int64 {
	elapsed := now.Sub(m.at[0])
	if m.at[0].IsZero() || elapsed < meterSettle {
		return 0
	}
	return int64(float64(total-m.bytes[0]) / elapsed.Seconds())
}

// progress hears about one item's transfer while it happens.
//
// Defined here, where it is used, so the fetcher knows nothing about jobs and a
// test can hand it a recorder.
type progress interface {
	// begin is called once per mirror attempt, with the size the server
	// declared or -1. A mirror that fails halfway starts the count over,
	// because the next one truncates the .part and writes from zero.
	begin(declared int64)
	// advance is called after every write with the bytes written so far.
	advance(written int64)
}

// progressWriter forwards a running byte count to a progress.
type progressWriter struct {
	written int64
	to      progress
}

func (w *progressWriter) Write(chunk []byte) (int, error) {
	w.written += int64(len(chunk))
	w.to.advance(w.written)
	return len(chunk), nil
}

// itemProgress records one item's transfer on the job the manager holds.
type itemProgress struct {
	manager *manager
	target  *item
}

func (p itemProgress) begin(declared int64) {
	now := time.Now()
	p.manager.mu.Lock()
	defer p.manager.mu.Unlock()
	p.target.State = "running"
	p.target.Bytes = 0
	// Display only. The ceiling is enforced on bytes written, and a server
	// that declares less than it sends just walks the bar past its end.
	p.target.BytesExpected = max(declared, 0)
	p.target.meter.reset(now)
}

func (p itemProgress) advance(written int64) {
	now := time.Now()
	p.manager.mu.Lock()
	defer p.manager.mu.Unlock()
	p.target.Bytes = written
	p.target.meter.observe(now, written)
}
