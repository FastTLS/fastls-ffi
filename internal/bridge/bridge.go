// Package bridge owns request lifetimes without exposing Go objects to callers.
package bridge

import (
	"bytes"
	"context"
	"crypto/sha256"
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
	Proxy            string            `json:"proxy"`
	ProxyCAPEM       string            `json:"proxy_ca_pem"`
	Duplex           bool              `json:"duplex"`
	WriteTimeoutMS   int64             `json:"write_timeout_ms"`
	StreamBody       bool              `json:"stream_body"`
	ConnectTimeoutMS int64             `json:"connect_timeout_ms"`
	HeadersTimeoutMS int64             `json:"headers_timeout_ms"`
	ReadTimeoutMS    int64             `json:"read_timeout_ms"`
	DecodeContent    bool              `json:"decode_content"`
	MaxDecodedBytes  int64             `json:"max_decoded_bytes"`
	Session          uint64            `json:"session,omitempty"`
	URL              string            `json:"url"`
	Method           string            `json:"method"`
	Profile          string            `json:"profile"`
	Headers          map[string]string `json:"headers"`
	Body             string            `json:"body"`
	BodyBase64       *string           `json:"body_base64"`
	TimeoutMS        int64             `json:"timeout_ms"`
	DisableRedirect  bool              `json:"disable_redirect"`
	CAPEM            string            `json:"ca_pem"`
}

type Info struct {
	UploadDiscarded  bool                `json:"upload_discarded,omitempty"`
	WireHeaderValues map[string][]string `json:"wire_header_values,omitempty"`
	Decoded          bool                `json:"decoded"`
	ErrorCode        string              `json:"error_code,omitempty"`
	TimeoutPhase     string              `json:"timeout_phase,omitempty"`
	State            string              `json:"state"`
	Status           int                 `json:"status,omitempty"`
	Headers          map[string]string   `json:"headers,omitempty"`
	HeaderValues     map[string][]string `json:"header_values,omitempty"`
	Match            profile.Match       `json:"match"`
	Error            string              `json:"error,omitempty"`
}

type chunk struct {
	data []byte
	err  error
}
type request struct {
	id       uint64
	eventMu  sync.Mutex
	changed  chan struct{}
	watch    *watcher
	upload   *upload
	owner    uint64
	pool     *fastls.Session
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
	sessions map[uint64]*session
	mu       sync.Mutex
	requests map[uint64]*request
	next     atomic.Uint64
	provider *generators.Provider
	workers  atomic.Int64
}

func New() *Engine {
	return &Engine{requests: make(map[uint64]*request), sessions: make(map[uint64]*session), provider: generators.New()}
}

type session struct {
	watch   *watcher
	pools   map[string]*fastls.Session
	cookies *fastls.CookieStore
}

func (e *Engine) CreateSession() (uint64, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if len(e.sessions) >= 64 {
		return 0, errors.New("too many open sessions (64)")
	}
	id := e.next.Add(1)
	e.sessions[id] = &session{pools: make(map[string]*fastls.Session), cookies: fastls.NewCookieStore()}
	return id, nil
}

func (e *Engine) CloseSession(id uint64) {
	e.mu.Lock()
	s := e.sessions[id]
	delete(e.sessions, id)
	for handle, r := range e.requests {
		if r.owner == id && id != 0 {
			r.cancel()
			delete(e.requests, handle)
		}
	}
	e.mu.Unlock()
	if s != nil {
		if s.watch != nil {
			s.watch.close()
		}
		s.cookies.SetEnabled(false)
		for _, pool := range s.pools {
			pool.Close()
		}
	}
}

func (e *Engine) CloseSessionIdle(id uint64) error {
	e.mu.Lock()
	s := e.sessions[id]
	if s == nil {
		e.mu.Unlock()
		return errors.New("invalid or closed session handle")
	}
	pools := make([]*fastls.Session, 0, len(s.pools))
	for _, pool := range s.pools {
		pools = append(pools, pool)
	}
	e.mu.Unlock()
	for _, pool := range pools {
		pool.CloseIdleConnections()
	}
	return nil
}

func (e *Engine) Profiles() ([]string, error) { return e.provider.List() }

// Stats is a read-only snapshot. Heap/goroutine counts cover this Go runtime;
// protocol and OS buffers are not included in BufferedBodyBytes.
func (e *Engine) Stats() map[string]any {
	e.mu.Lock()
	open := len(e.requests)
	openSessions := len(e.sessions)
	poolCount := 0
	notifiers := 0
	for _, s := range e.sessions {
		poolCount += len(s.pools)
		if s.watch != nil {
			notifiers++
		}
	}
	var buffered, uploads int64
	for _, r := range e.requests {
		buffered += r.buffered.Load()
		if r.upload != nil {
			r.upload.mu.Lock()
			uploads += int64(len(r.upload.data))
			r.upload.mu.Unlock()
		}
	}
	e.mu.Unlock()
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	return map[string]any{"open_requests": open, "workers": e.workers.Load(),
		"open_sessions": openSessions, "session_pools": poolCount,
		"notifiers":           notifiers,
		"buffered_body_bytes": buffered, "goroutines": runtime.NumGoroutine(),
		"buffered_upload_bytes": uploads,
		"heap_alloc_bytes":      mem.HeapAlloc, "heap_inuse_bytes": mem.HeapInuse}
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
	for _, value := range []int64{in.ConnectTimeoutMS, in.HeadersTimeoutMS, in.ReadTimeoutMS, in.WriteTimeoutMS} {
		if value < 0 || value > 86400000 {
			return 0, errors.New("phase timeout_ms must be 0..86400000")
		}
	}
	if in.MaxDecodedBytes < 0 || in.MaxDecodedBytes > 1<<40 {
		return 0, errors.New("max_decoded_bytes must be 1..1099511627776 or zero for default")
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
	if in.StreamBody && (in.Body != "" || in.BodyBase64 != nil) {
		return 0, errStreamBody
	}
	if in.Duplex && !in.StreamBody {
		return 0, errors.New("duplex requires stream_body")
	}
	if in.Duplex && !in.DisableRedirect {
		return 0, errors.New("duplex requires disable_redirect")
	}
	if in.StreamBody {
		for name := range in.Headers {
			if strings.EqualFold(name, "Content-Length") || strings.EqualFold(name, "Transfer-Encoding") {
				return 0, errors.New("stream upload manages Content-Length and Transfer-Encoding")
			}
		}
	}
	if len(in.Body) > 512*1024 {
		return 0, errors.New("buffered upload exceeds 512KiB; use stream_body")
	}
	if err := fastls.ValidateProxy(in.Proxy); err != nil {
		return 0, err
	}
	for name := range in.Headers {
		if strings.EqualFold(name, "Proxy-Authorization") || strings.EqualFold(name, "Proxy-Connection") {
			return 0, errors.New("proxy headers must not be sent as target headers; configure proxy URL credentials")
		}
	}
	opts := fastls.Options{Proxy: in.Proxy, Headers: in.Headers, Body: in.Body, VerifyTLS: true,
		Timeout: int((in.TimeoutMS + 999) / 1000), DisableRedirect: in.DisableRedirect}
	if in.ProxyCAPEM != "" {
		proxyURL, _ := url.Parse(in.Proxy)
		if proxyURL == nil || proxyURL.Scheme != "https" {
			return 0, errors.New("proxy_ca_pem requires an HTTPS proxy")
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM([]byte(in.ProxyCAPEM)) {
			return 0, errors.New("proxy_ca_pem contains no valid CA certificates")
		}
		opts.ProxyTLSRootCAs = roots
	}
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
	if in.StreamBody {
		r.upload = &upload{ctx: ctx, wake: make(chan struct{}, 1)}
	}
	e.mu.Lock()
	if len(e.requests) >= MaxRequests {
		e.mu.Unlock()
		cancel()
		return 0, errors.New("too many open requests; close responses")
	}
	if in.Session != 0 {
		s := e.sessions[in.Session]
		if s == nil {
			e.mu.Unlock()
			cancel()
			return 0, errors.New("invalid or closed session handle")
		}
		identity, _ := json.Marshal([]string{in.Profile, in.CAPEM, in.Proxy, in.ProxyCAPEM})
		key := fmt.Sprintf("%x", sha256.Sum256(identity))
		pool := s.pools[key]
		if pool == nil {
			if len(s.pools) >= 16 {
				e.mu.Unlock()
				cancel()
				return 0, errors.New("session configuration limit exceeded (16)")
			}
			// The first request must not become the pool's default headers or UA.
			poolOpts := opts
			poolOpts.Headers, poolOpts.HeaderValues, poolOpts.Body = nil, nil, ""
			poolOpts.UserAgent = poolOpts.FingerprintUserAgent
			poolOpts.DisableRedirect = false
			poolOpts.CookieJar = s.cookies
			pool, err = fastls.NewSession(poolOpts)
			if err != nil {
				e.mu.Unlock()
				cancel()
				return 0, err
			}
			s.pools[key] = pool
		}
		r.pool, r.owner = pool, in.Session
		r.watch = s.watch
	}
	id := e.next.Add(1)
	r.id = id
	if r.upload != nil {
		r.upload.changed = r.signal
	}
	context.AfterFunc(ctx, r.signal)
	e.requests[id] = r
	e.workers.Add(1)
	e.mu.Unlock()
	go func() { defer e.workers.Add(-1); r.run(in, opts) }()
	return id, nil
}

func (r *request) run(in Input, opts fastls.Options) {
	if r.upload != nil {
		defer r.upload.Close()
	}
	defer func() {
		if p := recover(); p != nil {
			r.setError(fmt.Errorf("internal request panic: %v", p))
		}
	}()
	var resp fastls.Response
	var err error
	pool := r.pool
	if pool == nil && (in.StreamBody || in.DecodeContent || in.ConnectTimeoutMS > 0 || in.HeadersTimeoutMS > 0 || in.ReadTimeoutMS > 0 || in.WriteTimeoutMS > 0) {
		pool, err = fastls.NewSession(opts)
		if err != nil {
			r.setError(err)
			return
		}
		defer pool.Close()
	}
	if pool != nil {
		var body io.ReadCloser
		if r.upload != nil {
			body = r.upload
		}
		resp, err = pool.DoContext(r.ctx, in.URL, fastls.RequestOptions{Duplex: in.Duplex, Headers: in.Headers, Body: in.Body, BodyReader: body, DisableRedirect: in.DisableRedirect,
			DecodeContent: in.DecodeContent, MaxDecodedBytes: in.MaxDecodedBytes,
			Timeouts: fastls.Timeouts{Write: time.Duration(in.WriteTimeoutMS) * time.Millisecond, Connect: time.Duration(in.ConnectTimeoutMS) * time.Millisecond, Headers: time.Duration(in.HeadersTimeoutMS) * time.Millisecond, Read: time.Duration(in.ReadTimeoutMS) * time.Millisecond}}, strings.ToUpper(in.Method))
	} else {
		resp, err = fastls.NewClient().DoContext(r.ctx, in.URL, opts, strings.ToUpper(in.Method))
	}
	// Core may return a diagnostic response together with an error.
	if r.pool == nil {
		defer resp.Client.CloseIdleConnections()
	}
	if resp.Body != nil {
		defer resp.Body.Close()
	}
	if err != nil {
		r.setError(err)
		return
	}
	r.mu.Lock()
	r.info.State, r.info.Status, r.info.Headers = "headers", resp.Status, resp.Headers
	r.info.HeaderValues = resp.HeaderValues
	r.info.WireHeaderValues, r.info.Decoded = resp.WireHeaderValues, resp.Decoded
	r.info.UploadDiscarded = in.StreamBody && !resp.RequestHasBody
	r.mu.Unlock()
	r.signal()
	for {
		b := make([]byte, ChunkSize)
		n, err := resp.Body.Read(b)
		r.buffered.Add(int64(n))
		if n == 0 && err == nil {
			continue
		}
		select {
		case r.chunks <- chunk{data: b[:n], err: err}:
			r.signal()
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
	r.setErrorLocked(err)
}

func (r *request) setErrorLocked(err error) {
	if r.info.State == "error" {
		return
	}
	r.info.State, r.info.Error = "error", err.Error()
	defer r.signal()
	r.info.ErrorCode, r.info.TimeoutPhase = "request_error", ""
	var phase *fastls.TimeoutError
	var decoding *fastls.DecodeError
	switch {
	case errors.As(err, &phase):
		r.info.ErrorCode, r.info.TimeoutPhase = "timeout", phase.Phase
	case errors.Is(err, context.DeadlineExceeded):
		r.info.ErrorCode, r.info.TimeoutPhase = "timeout", "total"
	case errors.Is(err, context.Canceled):
		r.info.ErrorCode = "cancelled"
	case errors.As(err, &decoding):
		r.info.ErrorCode = "decode_error"
	}
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
	if r.info.State != "eof" && r.info.State != "error" && r.ctx.Err() != nil {
		r.setErrorLocked(r.ctx.Err())
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
