package main

// Independent RFC6455 laboratory: intentionally does not use the client's
// WebSocket library. It checks masking and can emit invalid wire frames.
import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

var wsPeers sync.Map

func writeWS(w io.Writer, flags byte, data []byte) error {
	header := []byte{flags, 0}
	switch {
	case len(data) < 126:
		header[1] = byte(len(data))
	case len(data) <= 65535:
		header[1] = 126
		header = append(header, byte(len(data)>>8), byte(len(data)))
	default:
		header[1] = 127
		header = binary.BigEndian.AppendUint64(header, uint64(len(data)))
	}
	if _, err := w.Write(header); err != nil {
		return err
	}
	_, err := w.Write(data)
	return err
}

func readWS(reader *bufio.Reader) (byte, []byte, error) {
	var h [2]byte
	if _, err := io.ReadFull(reader, h[:]); err != nil {
		return 0, nil, err
	}
	if h[1]&128 == 0 || h[0]&0x70 != 0 {
		return 0, nil, errors.New("client frame must be masked and uncompressed")
	}
	size := uint64(h[1] & 127)
	if size == 126 {
		var n [2]byte
		if _, err := io.ReadFull(reader, n[:]); err != nil {
			return 0, nil, err
		}
		size = uint64(binary.BigEndian.Uint16(n[:]))
	}
	if size == 127 {
		var n [8]byte
		if _, err := io.ReadFull(reader, n[:]); err != nil {
			return 0, nil, err
		}
		size = binary.BigEndian.Uint64(n[:])
	}
	if size > 16*1024*1024 {
		return 0, nil, errors.New("client frame exceeds fixture limit")
	}
	var mask [4]byte
	if _, err := io.ReadFull(reader, mask[:]); err != nil {
		return 0, nil, err
	}
	data := make([]byte, int(size))
	if _, err := io.ReadFull(reader, data); err != nil {
		return 0, nil, err
	}
	for i := range data {
		data[i] ^= mask[i%4]
	}
	return h[0], data, nil
}

func (f *fixture) websocketRoute(w http.ResponseWriter, r *http.Request) bool {
	if r.URL.Path != "/ws" && r.URL.Path != "/private/ws" && r.URL.Path != "/ws/deep" {
		return false
	}
	mode := r.URL.Query().Get("mode")
	setCookies := r.URL.Query()["set_cookie"]
	for _, value := range setCookies {
		if strings.ContainsAny(value, "\r\n") {
			http.Error(w, "bad fixture cookie", 400)
			return true
		}
		w.Header().Add("Set-Cookie", value)
	}
	if mode == "cookie-auth" {
		cookie, err := r.Cookie("login")
		if err != nil || cookie.Value != "yes" {
			http.Error(w, "missing login cookie", 403)
			return true
		}
	}
	if mode == "reject" || mode == "cookie-reject" {
		http.Error(w, "not upgraded", 403)
		return true
	}
	if mode == "redirect" || mode == "cookie-redirect" {
		http.Redirect(w, r, "/ws", 302)
		return true
	}
	if mode == "handshake-stall" {
		id := r.URL.Query().Get("id")
		f.mu.Lock()
		o := f.entries[id]
		if o == nil {
			o = &observation{release: make(chan struct{})}
			f.entries[id] = o
		}
		o.Started = true
		f.mu.Unlock()
		select {
		case <-r.Context().Done():
		case <-time.After(10 * time.Second):
		}
		return true
	}
	if r.Method != "GET" || !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") || r.Header.Get("Sec-Websocket-Version") != "13" {
		http.Error(w, "bad handshake", 400)
		return true
	}
	key, err := base64.StdEncoding.DecodeString(r.Header.Get("Sec-Websocket-Key"))
	if err != nil || len(key) != 16 {
		http.Error(w, "bad key", 400)
		return true
	}
	c, rw, err := w.(http.Hijacker).Hijack()
	if err != nil {
		return true
	}
	wsPeers.Store(c, true)
	defer wsPeers.Delete(c)
	defer c.Close()
	c.SetDeadline(time.Now().Add(60 * time.Second))
	digest := sha1.Sum([]byte(r.Header.Get("Sec-Websocket-Key") + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	accept := base64.StdEncoding.EncodeToString(digest[:])
	if mode == "bad-accept" || mode == "cookie-bad-accept" {
		accept = "invalid"
	}
	fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\nX-WS-Protocol: %s\r\n", accept, r.Proto)
	for _, value := range setCookies {
		fmt.Fprintf(rw, "Set-Cookie: %s\r\n", value)
	}
	if mode == "bad-subprotocol" {
		fmt.Fprint(rw, "Sec-WebSocket-Protocol: unoffered\r\n")
	}
	if mode == "huge-headers" {
		fmt.Fprintf(rw, "X-Large: %s\r\n", strings.Repeat("x", 70*1024))
	}
	if r.Header.Get("Sec-Websocket-Protocol") != "" {
		fmt.Fprintf(rw, "Sec-WebSocket-Protocol: %s\r\n", strings.TrimSpace(strings.Split(r.Header.Get("Sec-Websocket-Protocol"), ",")[0]))
	}
	if mode == "compression" || mode == "cookie-compression" {
		fmt.Fprint(rw, "Sec-WebSocket-Extensions: permessage-deflate\r\n")
	}
	if mode == "hidden-compression" {
		fmt.Fprint(rw, "Sec-WebSocket-Extensions: \r\nSec-WebSocket-Extensions: permessage-deflate; server_no_context_takeover; client_no_context_takeover\r\n")
	}
	fmt.Fprint(rw, "\r\n")
	rw.Flush()
	switch mode {
	case "abrupt":
		return true
	case "fragment":
		text := []byte("你好🌍")
		writeWS(c, 1, text[:2])
		writeWS(c, 137, []byte("server-ping"))
		writeWS(c, 128, text[2:])
	case "remote-close":
		writeWS(c, 136, append([]byte{3, 233}, []byte("bye")...))
	case "bad-utf8":
		writeWS(c, 129, []byte{255})
	case "bad-rsv":
		writeWS(c, 193, []byte("x"))
	case "bad-opcode":
		writeWS(c, 131, []byte("x"))
	case "bad-control":
		writeWS(c, 9, []byte("x"))
	case "bad-close-code":
		writeWS(c, 136, []byte{3, 237}) // 1005 is not legal on wire.
	case "bad-close-utf8":
		writeWS(c, 136, []byte{3, 232, 255})
	case "masked":
		c.Write([]byte{129, 129, 1, 2, 3, 4, 'x' ^ 1})
	case "oversize":
		writeWS(c, 130, make([]byte, 2048))
	case "flood":
		for i := 0; i < 1024; i++ {
			if writeWS(c, 130, make([]byte, 65536)) != nil {
				return true
			}
		}
	case "slow-read":
		// Wait on the existing control endpoint; closing the fixture also
		// closes hijacked sockets. This creates real network backpressure.
		id := r.URL.Query().Get("id")
		f.mu.Lock()
		o := f.entries[id]
		if o == nil {
			o = &observation{release: make(chan struct{})}
			f.entries[id] = o
		}
		o.Started = true
		f.mu.Unlock()
		select {
		case <-o.release:
		case <-time.After(5 * time.Second):
		}
	}
	var message []byte
	kind := byte(0)
	for {
		flags, data, err := readWS(rw.Reader)
		if err != nil {
			return true
		}
		op := flags & 15
		switch op {
		case 8:
			if mode == "close-stall" {
				io.Copy(io.Discard, c)
				return true
			}
			writeWS(c, 136, data)
			return true
		case 9:
			if writeWS(c, 138, data) != nil {
				return true
			}
			continue
		case 10:
			if mode == "fragment" {
				payload, _ := json.Marshal(map[string]any{"pong": string(data)})
				if writeWS(c, 129, payload) != nil {
					return true
				}
			}
			continue
		case 1, 2:
			kind = op
			message = message[:0]
		case 0:
		default:
			return true
		}
		message = append(message, data...)
		if len(message) > 16*1024*1024 {
			return true
		}
		if flags&128 != 0 {
			if mode == "inspect" || r.URL.Query().Get("inspect") == "1" {
				alpn := ""
				if r.TLS != nil {
					alpn = r.TLS.NegotiatedProtocol
				}
				message, _ = json.Marshal(map[string]any{"headers": r.Header, "query": r.URL.Query(), "protocol": r.Proto, "alpn": alpn, "bytes": len(message)})
				kind = 1
			}
			if err := writeWS(c, 128|kind, message); err != nil {
				return true
			}
			if delay, _ := strconv.Atoi(r.URL.Query().Get("delay_ms")); delay > 0 {
				time.Sleep(time.Duration(delay) * time.Millisecond)
			}
		}
	}
}

func closeWebSocketPeers() {
	wsPeers.Range(func(key, value any) bool { key.(net.Conn).Close(); return true })
}
