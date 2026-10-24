package bridge

import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	fastls "github.com/FastTLS/fastls"
	use "github.com/FastTLS/fastls/imitate"
	"github.com/gorilla/websocket"
)

const MaxWSMessage = 16 * 1024 * 1024

type WebSocketInput struct {
	URL            string            `json:"url"`
	Profile        string            `json:"profile"`
	Session        uint64            `json:"session"`
	Headers        map[string]string `json:"headers"`
	Proxy          string            `json:"proxy"`
	CAPEM          string            `json:"ca_pem"`
	ProxyCAPEM     string            `json:"proxy_ca_pem"`
	Protocols      []string          `json:"subprotocols"`
	TimeoutMS      int64             `json:"timeout_ms"`
	ReadTimeoutMS  int64             `json:"read_timeout_ms"`
	WriteTimeoutMS int64             `json:"write_timeout_ms"`
	MaxMessage     int64             `json:"max_message_bytes"`
}

type WebSocketInfo struct {
	Subprotocol   string `json:"subprotocol"`
	WritePending  bool   `json:"write_pending"`
	CloseCode     int    `json:"close_code"`
	CloseReason   string `json:"close_reason"`
	CloseReceived bool   `json:"close_received"`
	Pings         uint64 `json:"pings"`
	Pongs         uint64 `json:"pongs"`
	LastPong      string `json:"last_pong_base64"`
	MaxMessage    int64  `json:"max_message_bytes"`
	ALPNPolicy    string `json:"alpn_policy"`
}

type wsMessage struct {
	kind int
	data []byte
}
type webSocket struct {
	mu            sync.Mutex
	conn          *websocket.Conn
	info          WebSocketInfo
	incoming      chan wsMessage
	outgoing      chan wsMessage
	closing       chan struct{}
	closeOnce     sync.Once
	rest          *wsMessage // protected by request.readMu
	bufferedWrite atomic.Int64
}

func (w *webSocket) snapshot() *WebSocketInfo {
	w.mu.Lock()
	defer w.mu.Unlock()
	i := w.info
	return &i
}

func (w *webSocket) ready(mask uint32, state string, buffered int64) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return state == "eof" || (mask&WaitRead != 0 && (buffered > 0 || len(w.incoming) > 0)) || (mask&WaitWrite != 0 && !w.info.WritePending)
}

func (e *Engine) StartWebSocket(raw []byte) (uint64, error) {
	if len(raw) == 0 || len(raw) > MaxInput {
		return 0, errors.New("invalid WebSocket input size")
	}
	var in WebSocketInput
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&in); err != nil {
		return 0, err
	}
	if dec.Decode(new(any)) != io.EOF {
		return 0, errors.New("expected one WebSocket JSON object")
	}
	u, err := url.Parse(in.URL)
	if err != nil || u.Hostname() == "" || u.User != nil || u.Fragment != "" || (u.Scheme != "ws" && u.Scheme != "wss") {
		return 0, errors.New("url must be an absolute ws/wss URL without credentials or fragment")
	}
	if in.Profile == "" {
		in.Profile = "chrome/156/windows"
	}
	if in.MaxMessage == 0 {
		in.MaxMessage = 1024 * 1024
	}
	if in.TimeoutMS == 0 {
		in.TimeoutMS = 30000
	}
	if in.WriteTimeoutMS == 0 {
		in.WriteTimeoutMS = 30000
	}
	if in.MaxMessage < 1 || in.MaxMessage > MaxWSMessage {
		return 0, errors.New("max_message_bytes must be 1..16777216")
	}
	for _, t := range []int64{in.TimeoutMS, in.WriteTimeoutMS, in.ReadTimeoutMS} {
		if t < 0 || t > 86400000 {
			return 0, errors.New("WebSocket timeout must be 0..86400000 ms")
		}
	}
	if len(in.Protocols) > 16 {
		return 0, errors.New("too many WebSocket subprotocols")
	}
	seen := map[string]bool{}
	for _, p := range in.Protocols {
		if len(p) < 1 || len(p) > 128 || seen[p] {
			return 0, errors.New("invalid WebSocket subprotocol")
		}
		for _, c := range p {
			if c <= 32 || c >= 127 || strings.ContainsRune("()<>@,;:\\\"/[]?={}", c) {
				return 0, errors.New("invalid WebSocket subprotocol token")
			}
		}
		seen[p] = true
	}
	if err := fastls.ValidateProxy(in.Proxy); err != nil {
		return 0, err
	}
	for k, v := range in.Headers {
		lower := strings.ToLower(k)
		if strings.ContainsAny(k+v, "\r\n\x00") || lower == "connection" || lower == "upgrade" || lower == "content-length" || lower == "transfer-encoding" || strings.HasPrefix(lower, "sec-websocket-") || lower == "proxy-authorization" || lower == "proxy-connection" {
			return 0, errors.New("invalid or reserved WebSocket handshake header")
		}
	}
	opts := fastls.Options{Headers: in.Headers, Proxy: in.Proxy, VerifyTLS: true}
	for _, item := range []struct {
		pem   string
		proxy bool
	}{{in.CAPEM, false}, {in.ProxyCAPEM, true}} {
		if item.pem == "" {
			continue
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM([]byte(item.pem)) {
			return 0, errors.New("WebSocket CA contains no valid certificates")
		}
		if item.proxy {
			p, _ := url.Parse(in.Proxy)
			if p == nil || p.Scheme != "https" {
				return 0, errors.New("proxy CA requires HTTPS proxy")
			}
			opts.ProxyTLSRootCAs = roots
		} else {
			opts.TLSRootCAs = roots
		}
	}
	match, err := use.Use(&opts, in.Profile, use.WithProvider(e.provider))
	if err != nil {
		return 0, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	w := &webSocket{info: WebSocketInfo{MaxMessage: in.MaxMessage, CloseCode: 1006, ALPNPolicy: "http/1.1"}, incoming: make(chan wsMessage, 1), outgoing: make(chan wsMessage, 1), closing: make(chan struct{})}
	r := &request{ctx: ctx, cancel: cancel, ws: w, info: Info{State: "pending", Match: match}}
	e.mu.Lock()
	if len(e.requests) >= MaxRequests {
		e.mu.Unlock()
		cancel()
		return 0, errors.New("too many open requests")
	}
	if in.Session != 0 {
		s := e.sessions[in.Session]
		if s == nil {
			e.mu.Unlock()
			cancel()
			return 0, errors.New("invalid or closed session handle")
		}
		r.owner, r.watch = in.Session, s.watch
	}
	r.id = e.next.Add(1)
	e.requests[r.id] = r
	e.workers.Add(1)
	e.mu.Unlock()
	context.AfterFunc(ctx, func() {
		w.mu.Lock()
		c := w.conn
		w.mu.Unlock()
		if c != nil {
			c.Close()
		}
		r.signal()
	})
	go func() { defer e.workers.Add(-1); defer cancel(); r.runWebSocket(e, in, opts) }()
	return r.id, nil
}

func (r *request) wsError(err error, phase string) {
	if r.ctx.Err() != nil {
		err = r.ctx.Err()
	} else {
		var timeout net.Error
		if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
			err = &fastls.TimeoutError{Phase: phase}
		}
	}
	r.setError(err)
}

func (r *request) runWebSocket(e *Engine, in WebSocketInput, opts fastls.Options) {
	defer func() {
		if recover() != nil {
			r.setError(errors.New("internal WebSocket failure"))
		}
	}()
	ctx, cancel := context.WithTimeout(r.ctx, time.Duration(in.TimeoutMS)*time.Millisecond)
	c, resp, err := fastls.DialWebSocketContext(ctx, in.URL, opts, in.Protocols)
	cancel()
	if resp != nil {
		r.mu.Lock()
		r.info.Status = resp.StatusCode
		r.info.HeaderValues = resp.Header
		r.mu.Unlock()
		if resp.Body != nil {
			resp.Body.Close()
		}
	}
	if err != nil {
		r.wsError(err, "handshake")
		return
	}
	defer c.Close()
	w := r.ws
	w.mu.Lock()
	w.conn = c
	w.info.Subprotocol = c.Subprotocol()
	w.mu.Unlock()
	if r.ctx.Err() != nil {
		r.wsError(r.ctx.Err(), "handshake")
		return
	}
	c.SetReadLimit(in.MaxMessage)
	controlDeadline := func() time.Time { return time.Now().Add(time.Second) }
	c.SetPingHandler(func(data string) error {
		w.mu.Lock()
		w.info.Pings++
		w.mu.Unlock()
		r.signal()
		return c.WriteControl(websocket.PongMessage, []byte(data), controlDeadline())
	})
	c.SetPongHandler(func(data string) error {
		w.mu.Lock()
		w.info.Pongs++
		w.info.LastPong = base64.StdEncoding.EncodeToString([]byte(data))
		w.mu.Unlock()
		r.signal()
		return nil
	})
	c.SetCloseHandler(func(code int, reason string) error {
		w.mu.Lock()
		w.info.CloseCode, w.info.CloseReason, w.info.CloseReceived = code, reason, true
		w.mu.Unlock()
		// A local close may already have been sent; the peer close is valid.
		c.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(code, ""), controlDeadline())
		return nil
	})
	r.mu.Lock()
	r.info.State = "headers"
	r.mu.Unlock()
	r.signal()
	e.workers.Add(1)
	go func() { defer e.workers.Add(-1); r.wsWriter(c, time.Duration(in.WriteTimeoutMS)*time.Millisecond) }()
	for {
		if in.ReadTimeoutMS > 0 {
			c.SetReadDeadline(time.Now().Add(time.Duration(in.ReadTimeoutMS) * time.Millisecond))
		}
		kind, data, err := c.ReadMessage()
		if err != nil {
			var closed *websocket.CloseError
			if errors.As(err, &closed) && closed.Code != websocket.CloseAbnormalClosure {
				w.mu.Lock()
				w.info.CloseCode, w.info.CloseReason, w.info.CloseReceived = closed.Code, closed.Text, true
				w.mu.Unlock()
				r.mu.Lock()
				if r.info.State != "error" {
					r.info.State = "eof"
				}
				r.mu.Unlock()
				r.signal()
			} else {
				r.wsError(err, "read")
			}
			return
		}
		if kind == websocket.TextMessage && !utf8.Valid(data) {
			c.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1007, "invalid UTF-8"), controlDeadline())
			r.wsError(errors.New("WebSocket text is not valid UTF-8"), "read")
			return
		}
		r.buffered.Add(int64(len(data)))
		select {
		case w.incoming <- wsMessage{kind, data}:
			r.signal()
		case <-w.closing:
			r.buffered.Add(-int64(len(data)))
		case <-r.ctx.Done():
			r.buffered.Add(-int64(len(data)))
			return
		}
	}
}

func (r *request) wsWriter(c *websocket.Conn, timeout time.Duration) {
	w := r.ws
	defer func() {
		w.mu.Lock()
		select {
		case <-w.outgoing:
		default:
		}
		w.info.WritePending = false
		w.mu.Unlock()
		w.bufferedWrite.Store(0)
		if recover() != nil {
			r.setError(errors.New("internal WebSocket writer failure"))
			r.cancel()
		}
	}()
	for {
		select {
		case <-r.ctx.Done():
			return
		case msg := <-w.outgoing:
			c.SetWriteDeadline(time.Now().Add(timeout))
			err := c.WriteMessage(msg.kind, msg.data)
			w.mu.Lock()
			remoteClosed := w.info.CloseReceived
			w.mu.Unlock()
			if err != nil {
				if remoteClosed {
					r.mu.Lock()
					if r.info.State != "error" {
						r.info.State = "eof"
					}
					r.mu.Unlock()
					r.signal()
				} else {
					r.wsError(err, "write")
				}
				r.cancel()
				return
			}
			w.bufferedWrite.Store(0)
			w.mu.Lock()
			w.info.WritePending = false
			w.mu.Unlock()
			r.signal()
		}
	}
}

func validClose(code int) bool {
	return code == 1000 || code == 1001 || code == 1002 || code == 1003 || code == 1007 || code == 1008 || code == 1009 || code == 1010 || code == 1011 || code == 1012 || code == 1013 || code == 1014 || (code >= 3000 && code <= 4999)
}

// WebSocketSend copies at most one message. Pending consumes no caller bytes.
func (e *Engine) WebSocketSend(id uint64, kind int, data []byte) int {
	r, err := e.get(id)
	if err != nil || r.ws == nil {
		return Invalid
	}
	w := r.ws
	if kind != 1 && kind != 2 && kind != 8 && kind != 9 && kind != 10 {
		return Invalid
	}
	if (kind < 8 && len(data) > int(w.info.MaxMessage)) || (kind >= 8 && len(data) > 125) || (kind == 1 && !utf8.Valid(data)) {
		return Invalid
	}
	if kind == 8 {
		if len(data) == 1 || (len(data) > 1 && (!validClose(int(binary.BigEndian.Uint16(data))) || !utf8.Valid(data[2:]))) {
			return Invalid
		}
	}
	r.mu.Lock()
	state := r.info.State
	r.mu.Unlock()
	if state == "pending" {
		return Pending
	}
	if state != "headers" || r.ctx.Err() != nil {
		return Failed
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if r.ctx.Err() != nil {
		return Failed
	}
	if w.info.WritePending {
		return Pending
	}
	select {
	case <-w.closing:
		return Failed
	default:
	}
	w.info.WritePending = true
	if kind == 8 {
		w.closeOnce.Do(func() { close(w.closing) })
	}
	w.bufferedWrite.Store(int64(len(data)))
	w.outgoing <- wsMessage{kind, append([]byte(nil), data...)}
	return Data
}

// WebSocketRead returns bounded pieces; end=true marks a complete message,
// including an empty one. Control frames are handled by the native reader.
func (e *Engine) WebSocketRead(id uint64, dst []byte) (n, kind int, end bool, status int) {
	if len(dst) < 1 || len(dst) > ChunkSize {
		return 0, 0, false, Invalid
	}
	r, err := e.get(id)
	if err != nil || r.ws == nil {
		return 0, 0, false, Invalid
	}
	r.readMu.Lock()
	defer r.readMu.Unlock()
	w := r.ws
	r.mu.Lock()
	state := r.info.State
	r.mu.Unlock()
	if r.ctx.Err() != nil && state != "eof" {
		return 0, 0, false, Failed
	}
	if w.rest == nil {
		select {
		case msg := <-w.incoming:
			w.rest = &msg
		default:
		}
	}
	if w.rest != nil {
		n = copy(dst, w.rest.data)
		kind = w.rest.kind
		w.rest.data = w.rest.data[n:]
		r.buffered.Add(-int64(n))
		end = len(w.rest.data) == 0
		if end {
			w.rest = nil
		}
		return n, kind, end, Data
	}
	r.mu.Lock()
	state = r.info.State
	r.mu.Unlock()
	if state == "eof" {
		return 0, 0, false, EOF
	}
	if state == "error" || r.ctx.Err() != nil {
		return 0, 0, false, Failed
	}
	return 0, 0, false, Pending
}
