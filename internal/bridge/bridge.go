// Package bridge owns request lifetimes without exposing Go objects to callers.
package bridge

import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	fastls "github.com/FastTLS/fastls"
	use "github.com/FastTLS/fastls/imitate"
	"github.com/FastTLS/fastls/profile"
	generators "github.com/FastTLS/imitate"
)

const (
	ChunkSize   = 64 * 1024
	MaxInput    = 1024 * 1024
	MaxRequests = 256
	Data        = 0
	Pending     = 1
	EOF         = 2
	Failed      = -1
	Invalid     = -2
)

type Input struct {
	URL             string            `json:"url"`
	Method          string            `json:"method"`
	Profile         string            `json:"profile"`
	Headers         map[string]string `json:"headers"`
	Body            string            `json:"body"`
	BodyBase64      *string           `json:"body_base64"`
	TimeoutMS       int64             `json:"timeout_ms"`
	DisableRedirect bool              `json:"disable_redirect"`
	CAPEM           string            `json:"ca_pem"`
}

type Info struct {
	State   string            `json:"state"`
	Status  int               `json:"status,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
	Match   profile.Match     `json:"match"`
	Error   string            `json:"error,omitempty"`
}

type chunk struct {
	data []byte
	err  error
}
type request struct {
	ctx      context.Context
	cancel   context.CancelFunc
	mu       sync.Mutex
	info     Info
	readMu   sync.Mutex
	rest     []byte
	terminal int
	chunks   chan chunk
	buffered atomic.Int64
}

type Engine struct {
	mu       sync.Mutex
	requests map[uint64]*request
	next     atomic.Uint64
	provider *generators.Provider
	workers  atomic.Int64
}

func New() *Engine {
	return &Engine{requests: make(map[uint64]*request), provider: generators.New()}
}

func (e *Engine) Profiles() ([]string, error) { return e.provider.List() }

// Stats is a read-only snapshot. Heap/goroutine counts cover this Go runtime;
// protocol and OS buffers are not included in BufferedBodyBytes.
func (e *Engine) Stats() map[string]any {
	e.mu.Lock()
	open := len(e.requests)
	var buffered int64
	for _, r := range e.requests {
		buffered += r.buffered.Load()
	}
	e.mu.Unlock()
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	return map[string]any{"open_requests": open, "workers": e.workers.Load(),
		"buffered_body_bytes": buffered, "goroutines": runtime.NumGoroutine(),
		"heap_alloc_bytes": mem.HeapAlloc, "heap_inuse_bytes": mem.HeapInuse}
}

func (e *Engine) Start(raw []byte) (uint64, error) {
	if len(raw) == 0 || len(raw) > MaxInput {
		return 0, errors.New("request JSON size must be 1..1048576 bytes")
	}
	var in Input
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&in); err != nil {
		return 0, err
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return 0, errors.New("expected exactly one JSON object")
	}
	u, err := url.Parse(in.URL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return 0, errors.New("url must be an absolute HTTP(S) URL without credentials")
	}
	if in.Profile == "" {
		in.Profile = "chrome/156/windows"
	}
	if in.Method == "" {
		in.Method = "GET"
	}
	if in.TimeoutMS == 0 {
		in.TimeoutMS = 30000
	}
	if in.TimeoutMS < 1 || in.TimeoutMS > 86400000 {
		return 0, errors.New("timeout_ms must be 1..86400000")
	}
	if in.BodyBase64 != nil {
		if in.Body != "" {
			return 0, errors.New("body and body_base64 are mutually exclusive")
		}
		b, err := base64.StdEncoding.Strict().DecodeString(*in.BodyBase64)
		if err != nil {
			return 0, fmt.Errorf("body_base64: %w", err)
		}
		in.Body = string(b)
	}
	if len(in.Body) > 512*1024 {
		return 0, errors.New("upload exceeds 512KiB; streaming upload is not implemented")
	}
	opts := fastls.Options{Headers: in.Headers, Body: in.Body, VerifyTLS: true,
		Timeout: int((in.TimeoutMS + 999) / 1000), DisableRedirect: in.DisableRedirect}
	if in.CAPEM != "" {
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM([]byte(in.CAPEM)) {
			return 0, errors.New("ca_pem contains no valid CA certificates")
		}
		opts.TLSRootCAs = roots
	}
	match, err := use.Use(&opts, in.Profile, use.WithProvider(e.provider))
	if err != nil {
		return 0, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(in.TimeoutMS)*time.Millisecond)
	r := &request{ctx: ctx, cancel: cancel, info: Info{State: "pending", Match: match}, chunks: make(chan chunk, 1)}
	e.mu.Lock()
	if len(e.requests) >= MaxRequests {
		e.mu.Unlock()
		cancel()
		return 0, errors.New("too many open requests; close responses")
	}
	id := e.next.Add(1)
	e.requests[id] = r
	e.workers.Add(1)
	e.mu.Unlock()
	go func() { defer e.workers.Add(-1); r.run(in, opts) }()
	return id, nil
}

func (r *request) run(in Input, opts fastls.Options) {
	defer func() {
		if p := recover(); p != nil {
			r.setError(fmt.Errorf("internal request panic: %v", p))
		}
	}()
	resp, err := fastls.NewClient().DoContext(r.ctx, in.URL, opts, strings.ToUpper(in.Method))
	// Core may return a diagnostic response together with an error.
	defer resp.Client.CloseIdleConnections()
	if resp.Body != nil {
		defer resp.Body.Close()
	}
	if err != nil {
		r.setError(err)
		return
	}
	r.mu.Lock()
	r.info.State, r.info.Status, r.info.Headers = "headers", resp.Status, resp.Headers
	r.mu.Unlock()
	for {
		b := make([]byte, ChunkSize)
		n, err := resp.Body.Read(b)
		r.buffered.Add(int64(n))
		if n == 0 && err == nil {
			continue
		}
		select {
		case r.chunks <- chunk{data: b[:n], err: err}:
		case <-r.ctx.Done():
			r.setError(r.ctx.Err())
			return
		}
		if err != nil {
			return
		}
	}
}

func (r *request) setError(err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.info.State, r.info.Error = "error", err.Error()
}

func (e *Engine) get(id uint64) (*request, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	r := e.requests[id]
	if r == nil {
		return nil, errors.New("invalid or closed request handle")
	}
	return r, nil
}

func (e *Engine) Info(id uint64) (Info, error) {
	r, err := e.get(id)
	if err != nil {
		return Info{}, err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	// Preserve already-consumed EOF; otherwise cancellation/timeout is terminal.
	if r.info.State != "eof" && r.ctx.Err() != nil {
		r.info.State, r.info.Error = "error", r.ctx.Err().Error()
	}
	return r.info, nil
}

// Read never waits on network I/O and copies into caller-owned storage.
func (e *Engine) Read(id uint64, dst []byte) (int, int) {
	if len(dst) < 1 || len(dst) > MaxInput {
		return 0, Invalid
	}
	r, err := e.get(id)
	if err != nil {
		return 0, Invalid
	}
	r.readMu.Lock()
	defer r.readMu.Unlock()
	r.mu.Lock()
	done := r.info.State == "eof"
	r.mu.Unlock()
	if done {
		return 0, EOF
	}
	if r.ctx.Err() != nil {
		r.setError(r.ctx.Err())
		return 0, Failed
	}
	if len(r.rest) > 0 {
		n := copy(dst, r.rest)
		r.buffered.Add(-int64(n))
		r.rest = r.rest[n:]
		return n, Data
	}
	if r.terminal != 0 {
		if r.terminal == EOF {
			r.mu.Lock()
			r.info.State = "eof"
			r.mu.Unlock()
		}
		return 0, r.terminal
	}
	r.mu.Lock()
	failed := r.info.State == "error"
	r.mu.Unlock()
	if failed {
		return 0, Failed
	}
	select {
	case c := <-r.chunks:
		if c.err != nil {
			if errors.Is(c.err, io.EOF) {
				r.terminal = EOF
			} else {
				r.terminal = Failed
				r.setError(c.err)
			}
		}
		n := copy(dst, c.data)
		r.buffered.Add(-int64(n))
		r.rest = c.data[n:]
		if n > 0 {
			return n, Data
		}
		if r.terminal == EOF {
			r.mu.Lock()
			r.info.State = "eof"
			r.mu.Unlock()
		}
		return 0, r.terminal
	default:
		return 0, Pending
	}
}

func (e *Engine) Cancel(id uint64) error {
	r, err := e.get(id)
	if err != nil {
		return err
	}
	r.cancel()
	return nil
}

func (e *Engine) Close(id uint64) {
	e.mu.Lock()
	r := e.requests[id]
	delete(e.requests, id)
	e.mu.Unlock()
	if r != nil {
		r.cancel()
	}
}
