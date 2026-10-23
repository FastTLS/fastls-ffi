package bridge

import (
	"context"
	"errors"
	"fmt"
	"net"
	"sync"
	"time"
)

const (
	WaitHeaders = 1
	WaitRead    = 2
	WaitWrite   = 4
)

// Signals are hints. State and bytes stay in the existing ABI. Closing a
// generation channel wakes every waiter; clients then recheck actual state.
func (r *request) signal() {
	r.eventMu.Lock()
	if r.changed != nil {
		close(r.changed)
	}
	r.changed = make(chan struct{})
	r.eventMu.Unlock()
	if r.watch != nil {
		r.watch.signal(r.id)
	}
}

func (r *request) ready(mask uint32) bool {
	if r.ctx.Err() != nil {
		return true
	}
	r.mu.Lock()
	state := r.info.State
	r.mu.Unlock()
	if state == "error" || (mask&WaitHeaders != 0 && state != "pending") {
		return true
	}
	if mask&WaitRead != 0 {
		r.readMu.Lock()
		ready := len(r.rest) > 0 || r.terminal != 0 || len(r.chunks) > 0 || state == "eof"
		r.readMu.Unlock()
		if ready {
			return true
		}
	}
	if mask&WaitWrite != 0 {
		if r.upload == nil {
			return true
		}
		r.upload.mu.Lock()
		ready := len(r.upload.data) == 0 || r.upload.closed || r.upload.ended
		r.upload.mu.Unlock()
		if ready {
			return true
		}
	}
	return false
}

// Wait blocks the calling C thread. ctypes releases the GIL. Snapshot-before-
// check prevents a signal between checking state and waiting being lost.
func (e *Engine) Wait(id uint64, mask uint32, timeout time.Duration) int {
	if mask == 0 || mask & ^uint32(7) != 0 || timeout < 0 || timeout > time.Minute {
		return Invalid
	}
	r, err := e.get(id)
	if err != nil {
		return Invalid
	}
	var timer *time.Timer
	var deadline <-chan time.Time
	if timeout > 0 {
		timer = time.NewTimer(timeout)
		defer timer.Stop()
		deadline = timer.C
	}
	for {
		r.eventMu.Lock()
		if r.changed == nil {
			r.changed = make(chan struct{})
		}
		changed := r.changed
		r.eventMu.Unlock()
		if r.ready(mask) {
			return Data
		}
		if timeout == 0 {
			return Pending
		}
		select {
		case <-changed:
		case <-r.ctx.Done():
			return Data
		case <-deadline:
			return Pending
		}
	}
}

type eventBatch struct {
	Handles  []uint64 `json:"handles"`
	Overflow bool     `json:"overflow"`
}

// One bounded dirty set and one coalesced wake signal per session. The TCP
// connection is loopback-only and carries a random handshake and wake bytes.
type watcher struct {
	mu               sync.Mutex
	dirty            map[uint64]struct{}
	overflow, closed bool
	wake             chan struct{}
	ctx              context.Context
	cancel           context.CancelFunc
	conn             net.Conn
}

func (w *watcher) signal(id uint64) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return
	}
	first := len(w.dirty) == 0
	if len(w.dirty) < MaxRequests {
		w.dirty[id] = struct{}{}
	} else {
		w.overflow = true
	}
	if first {
		select {
		case w.wake <- struct{}{}:
		default:
		}
	}
}

func (w *watcher) drain() eventBatch {
	w.mu.Lock()
	defer w.mu.Unlock()
	batch := eventBatch{Handles: make([]uint64, 0, len(w.dirty)), Overflow: w.overflow}
	for id := range w.dirty {
		batch.Handles = append(batch.Handles, id)
	}
	clear(w.dirty)
	w.overflow = false
	return batch
}

func (w *watcher) close() {
	w.mu.Lock()
	w.closed = true
	w.cancel()
	if w.conn != nil {
		w.conn.Close()
	}
	clear(w.dirty)
	w.mu.Unlock()
}

func (w *watcher) run(port uint16, token []byte) {
	defer w.close()
	dialer := net.Dialer{Timeout: 2 * time.Second}
	conn, err := dialer.DialContext(w.ctx, "tcp4", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return
	}
	w.mu.Lock()
	if w.closed {
		w.mu.Unlock()
		conn.Close()
		return
	}
	w.conn = conn
	w.mu.Unlock()
	if _, err := conn.Write(token); err != nil {
		return
	}
	for {
		select {
		case <-w.ctx.Done():
			return
		case <-w.wake:
			if _, err := conn.Write([]byte{1}); err != nil {
				return
			}
		}
	}
}

func (e *Engine) WatchSession(id uint64, port uint16, token []byte) error {
	if port == 0 || len(token) != 32 {
		return errors.New("notification endpoint requires a port and 32-byte token")
	}
	e.mu.Lock()
	s := e.sessions[id]
	if s == nil {
		e.mu.Unlock()
		return errors.New("invalid or closed session handle")
	}
	if s.watch != nil {
		e.mu.Unlock()
		return errors.New("session notifications already registered")
	}
	for _, r := range e.requests {
		if r.owner == id {
			e.mu.Unlock()
			return errors.New("enable notifications before starting requests")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	w := &watcher{dirty: make(map[uint64]struct{}), wake: make(chan struct{}, 1), ctx: ctx, cancel: cancel}
	s.watch = w
	e.mu.Unlock()
	go w.run(port, append([]byte(nil), token...))
	return nil
}

func (e *Engine) UnwatchSession(id uint64) {
	e.mu.Lock()
	var w *watcher
	if s := e.sessions[id]; s != nil {
		w, s.watch = s.watch, nil
	}
	e.mu.Unlock()
	if w != nil {
		w.close()
	}
}

func (e *Engine) SessionEvents(id uint64) (eventBatch, error) {
	e.mu.Lock()
	s := e.sessions[id]
	if s == nil || s.watch == nil {
		e.mu.Unlock()
		return eventBatch{}, errors.New("session notifications are not registered")
	}
	w := s.watch
	e.mu.Unlock()
	return w.drain(), nil
}
