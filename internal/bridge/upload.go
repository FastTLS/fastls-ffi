package bridge

import (
	"context"
	"errors"
	"io"
	"sync"
)

// upload owns at most one copied 64 KiB chunk, including the unread remainder.
// Caller memory is never retained. A producer polls Write; a transport blocks in Read.
type upload struct {
	mu            sync.Mutex
	ctx           context.Context
	data          []byte
	ended, closed bool
	wake          chan struct{}
	changed       func()
}

func (u *upload) signal() {
	select {
	case u.wake <- struct{}{}:
	default:
	}
}
func (u *upload) Read(dst []byte) (int, error) {
	for {
		u.mu.Lock()
		if u.closed {
			u.mu.Unlock()
			return 0, io.ErrClosedPipe
		}
		if len(u.data) > 0 {
			n := copy(dst, u.data)
			u.data = u.data[n:]
			empty := len(u.data) == 0
			u.mu.Unlock()
			if empty && u.changed != nil {
				u.changed()
			}
			return n, nil
		}
		if u.ended {
			u.mu.Unlock()
			return 0, io.EOF
		}
		u.mu.Unlock()
		select {
		case <-u.wake:
		case <-u.ctx.Done():
			return 0, u.ctx.Err()
		}
	}
}
func (u *upload) Close() error {
	u.mu.Lock()
	u.closed = true
	u.data = nil
	u.mu.Unlock()
	u.signal()
	if u.changed != nil {
		u.changed()
	}
	return nil
}

// Write is nonblocking. Data consumes the entire supplied chunk; Pending consumes nothing.
// An empty chunk with end=true sends EOF; EOF and subsequent writes are rejected.
func (e *Engine) Write(id uint64, data []byte, end bool) int {
	if len(data) > ChunkSize || (len(data) == 0 && !end) {
		return Invalid
	}
	r, err := e.get(id)
	if err != nil || r.upload == nil {
		return Invalid
	}
	u := r.upload
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.ctx.Err() != nil || u.ended {
		return Failed
	}
	if u.closed {
		return EOF
	}
	if len(u.data) != 0 {
		return Pending
	}
	u.data = append([]byte(nil), data...)
	u.ended = end
	u.signal()
	return Data
}

var errStreamBody = errors.New("stream_body cannot be combined with body or body_base64")
