// ffi-fixture is a loopback-only, independently implemented acceptance server.
// It generates an ephemeral CA; no system trust store or private key file changes.
package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

type observation struct {
	Started   bool   `json:"started"`
	Done      bool   `json:"done"`
	Cancelled bool   `json:"cancelled"`
	Bytes     int64  `json:"bytes"`
	Protocol  string `json:"protocol"`
	release   chan struct{}
	once      sync.Once
}

type fixture struct {
	mu      sync.Mutex
	entries map[string]*observation
}

type connectionKey struct{}

var connectionIDs atomic.Uint64

func connectionContext(ctx context.Context, conn net.Conn) context.Context {
	return context.WithValue(ctx, connectionKey{}, connectionIDs.Add(1))
}

func (f *fixture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	f.mu.Lock()
	o := f.entries[id]
	if o == nil {
		o = &observation{release: make(chan struct{})}
		f.entries[id] = o
	}
	if r.URL.Path == "/state" {
		snapshot := map[string]any{"started": o.Started, "done": o.Done, "cancelled": o.Cancelled, "bytes": o.Bytes, "protocol": o.Protocol}
		f.mu.Unlock()
		json.NewEncoder(w).Encode(snapshot)
		return
	}
	if r.URL.Path == "/release" {
		o.once.Do(func() { close(o.release) })
		f.mu.Unlock()
		io.WriteString(w, "ok")
		return
	}
	o.Started, o.Protocol = true, r.Proto
	f.mu.Unlock()
	defer func() { f.mu.Lock(); o.Done = true; o.Cancelled = r.Context().Err() != nil; f.mu.Unlock() }()
	w.Header().Set("X-Test-Protocol", r.Proto)
	w.Header().Set("X-Test-Connection", fmt.Sprint(r.Context().Value(connectionKey{})))
	switch r.URL.Path {
	case "/sse-vectors", "/sse-bad":
		w.Header().Set("Content-Type", "Text/Event-Stream; charset=iso-8859-1")
		w.Header().Set("X-Test-Accept", r.Header.Get("Accept"))
		w.Header().Set("X-Test-Last-Event-ID", r.Header.Get("Last-Event-ID"))
		if r.URL.Path == "/sse-bad" {
			w.Header().Set("Content-Type", r.URL.Query().Get("type"))
			if r.URL.Query().Get("duplicate") == "1" {
				w.Header().Add("Content-Type", "text/event-stream")
			}
		}
		data := []byte("\ufeff: heartbeat\r\nid: 41\r\nretry: 25\r\n\r\nevent: update\rretry: 30\rdata: 第一条\rdata: second\r\r: ping\nData: ignored\nid: bad\x00id\nretry: -1\nretry: ٢\n\ndata\n\nid:\nevent:\ndata:  final\n\ndata: {\"ok\":true}\n\ndata: [DONE]\n\nid: lost\ndata: unfinished")
		if r.URL.Query().Get("corrupt") == "1" {
			var encoded bytes.Buffer
			z := gzip.NewWriter(&encoded)
			z.Write(data)
			z.Close()
			payload := encoded.Bytes()
			payload[len(payload)-5] ^= 0x80
			w.Header().Set("Content-Encoding", "gzip")
			w.Write(payload)
			return
		}
		var output io.Writer = w
		if r.URL.Query().Get("gzip") == "1" {
			w.Header().Set("Content-Encoding", "gzip")
			z := gzip.NewWriter(w)
			defer z.Close()
			output = z
		}
		step := len(data)
		if r.URL.Query().Get("fragment") == "1" {
			step = 1
		}
		for offset := 0; offset < len(data); offset += step {
			end := min(offset+step, len(data))
			if _, err := output.Write(data[offset:end]); err != nil {
				return
			}
			w.(http.Flusher).Flush()
		}
	case "/sse-hold":
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "id: first\ndata: first\n\n")
		w.(http.Flusher).Flush()
		var ticks <-chan time.Time
		if r.URL.Query().Get("heartbeat") == "1" {
			ticker := time.NewTicker(40 * time.Millisecond)
			defer ticker.Stop()
			ticks = ticker.C
		}
		for {
			select {
			case <-o.release:
				io.WriteString(w, "data: last\n\n")
				return
			case <-r.Context().Done():
				return
			case <-ticks:
				if _, err := io.WriteString(w, ": heartbeat\n\n"); err != nil {
					return
				}
				w.(http.Flusher).Flush()
			}
		}
	case "/sse-many":
		w.Header().Set("Content-Type", "text/event-stream")
		count, _ := strconv.Atoi(r.URL.Query().Get("n"))
		size, _ := strconv.Atoi(r.URL.Query().Get("size"))
		if count < 1 || count > 1000000 || size < 1 || size > 2*1024*1024 {
			http.Error(w, "bad bounds", 400)
			return
		}
		data := append(append([]byte("data: "), bytes.Repeat([]byte("x"), size)...), '\n', '\n')
		for i := 0; i < count; i++ {
			if _, err := w.Write(data); err != nil {
				return
			}
			f.mu.Lock()
			o.Bytes += int64(len(data))
			f.mu.Unlock()
			if i%16 == 0 {
				w.(http.Flusher).Flush()
			}
		}
	case "/sse-post", "/sse-duplex":
		w.Header().Set("Content-Type", "text/event-stream")
		if r.URL.Path == "/sse-post" {
			body, err := io.ReadAll(r.Body)
			if err != nil {
				return
			}
			encoded, _ := json.Marshal(map[string]string{"body": string(body), "accept": r.Header.Get("Accept"), "last_id": r.Header.Get("Last-Event-ID")})
			fmt.Fprintf(w, "data: %s\n\n", encoded)
			return
		}
		if r.ProtoMajor != 2 {
			http.Error(w, "HTTP2 required", 400)
			return
		}
		io.WriteString(w, "data: ready\n\n")
		w.(http.Flusher).Flush()
		buffer := make([]byte, 16384)
		for {
			n, err := r.Body.Read(buffer)
			if n > 0 {
				if _, writeErr := fmt.Fprintf(w, "data: %s\n\n", buffer[:n]); writeErr != nil {
					return
				}
				w.(http.Flusher).Flush()
			}
			if err != nil {
				return
			}
		}
	case "/duplex", "/duplex-gzip", "/duplex-silent":
		if r.ProtoMajor != 2 {
			http.Error(w, "HTTP2 required", 400)
			return
		}
		var output io.Writer = w
		var encoded *gzip.Writer
		if r.URL.Path == "/duplex-gzip" {
			w.Header().Set("Content-Encoding", "gzip")
			encoded = gzip.NewWriter(w)
			output = encoded
			defer encoded.Close()
		}
		flush := func() {
			if encoded != nil {
				encoded.Flush()
			}
			w.(http.Flusher).Flush()
		}
		io.WriteString(output, "ready\n")
		flush()
		buffer := make([]byte, 16384)
		for {
			n, err := r.Body.Read(buffer)
			f.mu.Lock()
			o.Bytes += int64(n)
			f.mu.Unlock()
			if n > 0 && r.URL.Path != "/duplex-silent" {
				if _, writeErr := output.Write(buffer[:n]); writeErr != nil {
					return
				}
				flush()
			}
			if err != nil {
				return
			}
		}
	case "/upload", "/upload-slow":
		hash := sha256.New()
		buffer := make([]byte, 16384)
		var total int64
		for {
			n, err := r.Body.Read(buffer)
			hash.Write(buffer[:n])
			total += int64(n)
			f.mu.Lock()
			o.Bytes = total
			f.mu.Unlock()
			if err == io.EOF {
				break
			}
			if err != nil {
				return
			}
			if r.URL.Path == "/upload-slow" {
				time.Sleep(2 * time.Millisecond)
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"bytes": total, "sha256": fmt.Sprintf("%x", hash.Sum(nil)), "protocol": r.Proto, "length": r.ContentLength})
	case "/upload-reject", "/upload-early-ok":
		if r.URL.Path == "/upload-reject" {
			w.WriteHeader(413)
		} else {
			w.WriteHeader(200)
		}
		w.(http.Flusher).Flush()
	case "/delay-redirect":
		count, _ := strconv.Atoi(r.URL.Query().Get("n"))
		if count < 0 || count > 4 {
			http.Error(w, "count", 400)
			return
		}
		select {
		case <-time.After(120 * time.Millisecond):
		case <-r.Context().Done():
			return
		}
		if count > 0 {
			http.Redirect(w, r, "/delay-redirect?n="+strconv.Itoa(count-1), 302)
		} else {
			io.WriteString(w, "done")
		}
	case "/encoded-echo":
		data, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "body", 400)
			return
		}
		w.Header().Set("Content-Encoding", r.URL.Query().Get("encoding"))
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		w.Write(data)
	case "/encoded-wait":
		w.Header().Set("Content-Encoding", "gzip")
		w.WriteHeader(200)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	case "/gzip-hold":
		w.Header().Set("Content-Encoding", "gzip")
		z := gzip.NewWriter(w)
		defer z.Close()
		io.WriteString(z, "first")
		z.Flush()
		w.(http.Flusher).Flush()
		select {
		case <-o.release:
			io.WriteString(z, "last!")
		case <-r.Context().Done():
		}
	case "/gzip-stream":
		size, _ := strconv.ParseInt(r.URL.Query().Get("n"), 10, 64)
		if size <= 0 || size > 128<<20 {
			http.Error(w, "invalid size", 400)
			return
		}
		w.Header().Set("Content-Encoding", "gzip")
		z := gzip.NewWriter(w)
		defer z.Close()
		slab := bytes.Repeat([]byte{0, 1, 128, 255}, 16384)
		for sent := int64(0); sent < size; {
			part := slab
			if int64(len(part)) > size-sent {
				part = part[:size-sent]
			}
			n, err := z.Write(part)
			sent += int64(n)
			f.mu.Lock()
			o.Bytes = sent
			f.mu.Unlock()
			if err != nil {
				return
			}
			if sent == int64(len(part)) {
				z.Flush()
				w.(http.Flusher).Flush()
			}
		}
	case "/drip":
		w.Header().Set("Content-Length", "30")
		for i := 0; i < 6; i++ {
			if i > 0 {
				select {
				case <-time.After(100 * time.Millisecond):
				case <-r.Context().Done():
					return
				}
			}
			if _, err := io.WriteString(w, "abcde"); err != nil {
				return
			}
			w.(http.Flusher).Flush()
		}
	case "/cookie-set":
		name, value := r.URL.Query().Get("name"), r.URL.Query().Get("value")
		if name == "" {
			name, value = "saved", "yes"
		}
		age, _ := strconv.Atoi(r.URL.Query().Get("max_age"))
		http.SetCookie(w, &http.Cookie{Name: name, Value: value, Path: "/", MaxAge: age})
		io.WriteString(w, "set")
	case "/cookie-fields":
		w.Header().Add("Set-Cookie", "root=one; Path=/; HttpOnly")
		w.Header().Add("Set-Cookie", "future=two; Expires=Fri, 01 Jan 2100 00:00:00 GMT; Path=/")
		w.Header().Add("Set-Cookie", "private=three; Path=/private")
		w.Header().Add("Set-Cookie", "secure=four; Secure; Path=/")
		w.Header().Add("Set-Cookie", "foreign=bad; Domain=evil.invalid; Path=/")
		w.Header().Add("X-List", "a, b")
		w.Header().Add("X-List", "literal/,/value")
		io.WriteString(w, "fields")
	case "/cookie-redirect":
		http.SetCookie(w, &http.Cookie{Name: "first", Value: "one", Path: "/"})
		http.Redirect(w, r, "/cookie-middle", http.StatusFound)
	case "/cookie-middle":
		http.SetCookie(w, &http.Cookie{Name: "second", Value: "two", Path: "/"})
		http.Redirect(w, r, "/inspect", http.StatusFound)
	case "/abort":
		w.Header().Set("Content-Length", "100")
		io.WriteString(w, "first")
		w.(http.Flusher).Flush()
		// net/http closes H1 or emits H2 RST_STREAM after these bytes.
		panic(http.ErrAbortHandler)
	case "/status":
		status, _ := strconv.Atoi(r.URL.Query().Get("code"))
		if status != 204 && status != 304 && status != 503 {
			status = 400
		}
		w.WriteHeader(status)
	case "/redirect-code":
		status, _ := strconv.Atoi(r.URL.Query().Get("code"))
		if status < 301 || status > 308 || status == 304 || status == 305 || status == 306 {
			status = 302
		}
		http.Redirect(w, r, "/inspect", status)
	case "/redirect-loop":
		http.Redirect(w, r, "/redirect-loop", http.StatusFound)
	case "/redirect-host":
		_, port, err := net.SplitHostPort(r.Host)
		if err != nil {
			http.Error(w, "invalid fixture host", 400)
			return
		}
		scheme := "http"
		if r.TLS != nil {
			scheme = "https"
		}
		http.Redirect(w, r, scheme+"://localhost:"+port+"/inspect", http.StatusFound)
	case "/inspect":
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"method": r.Method, "body": body, "headers": r.Header, "protocol": r.Proto, "host": r.Host})
	case "/wait":
		select {
		case <-o.release:
			io.WriteString(w, "released")
		case <-r.Context().Done():
		}
	case "/hold", "/cookie-hold":
		if r.URL.Path == "/cookie-hold" {
			http.SetCookie(w, &http.Cookie{Name: "streamed", Value: "ready", Path: "/"})
		}
		w.Header().Set("Content-Length", "10")
		io.WriteString(w, "first")
		w.(http.Flusher).Flush()
		select {
		case <-o.release:
			io.WriteString(w, "last!")
		case <-r.Context().Done():
		}
	case "/truncated":
		w.Header().Set("Content-Length", "100")
		io.WriteString(w, "short")
	case "/redirect":
		http.Redirect(w, r, "/headers", http.StatusFound)
	case "/headers":
		json.NewEncoder(w).Encode(map[string]any{"protocol": r.Proto, "headers": r.Header})
	case "/echo":
		io.Copy(w, r.Body)
	case "/sse":
		w.Header().Set("Content-Type", "text/event-stream")
		data := []byte("data: 第一条\n\ndata: second\n\n")
		for _, b := range data {
			if _, err := w.Write([]byte{b}); err != nil {
				return
			}
			w.(http.Flusher).Flush()
		}
	default:
		size, _ := strconv.ParseInt(r.URL.Query().Get("n"), 10, 64)
		if size <= 0 {
			size = 4096
		}
		if size > 512*1024*1024 {
			http.Error(w, "size too large", 400)
			return
		}
		w.Header().Set("Content-Length", strconv.FormatInt(size, 10))
		slab := bytes.Repeat([]byte{0, 1, 128, 255}, 16384)
		for sent := int64(0); sent < size; {
			part := slab
			if int64(len(part)) > size-sent {
				part = part[:size-sent]
			}
			n, err := w.Write(part)
			sent += int64(n)
			f.mu.Lock()
			o.Bytes = sent
			f.mu.Unlock()
			if err != nil {
				return
			}
		}
	}
}

func certificates(dir string) (tls.Certificate, string, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, "", "", err
	}
	now := time.Now()
	root := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Fastls FFI ephemeral test CA"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, root, root, &key.PublicKey, key)
	if err != nil {
		return tls.Certificate{}, "", "", err
	}
	ca := filepath.Join(dir, "ca.pem")
	if err = os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0600); err != nil {
		return tls.Certificate{}, "", "", err
	}
	otherKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	otherDER, err := x509.CreateCertificate(rand.Reader, root, root, &otherKey.PublicKey, otherKey)
	if err != nil {
		return tls.Certificate{}, "", "", err
	}
	wrong := filepath.Join(dir, "wrong-ca.pem")
	if err = os.WriteFile(wrong, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: otherDER}), 0600); err != nil {
		return tls.Certificate{}, "", "", err
	}
	leafKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), DNSNames: []string{"localhost"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, root, &leafKey.PublicKey, key)
	return tls.Certificate{Certificate: [][]byte{leafDER, der}, PrivateKey: leafKey}, ca, wrong, err
}

func main() {
	dir, err := os.MkdirTemp("", "fastls-ffi-fixture-")
	if err != nil {
		log.Fatal(err)
	}
	defer os.RemoveAll(dir)
	cert, ca, wrong, err := certificates(dir)
	if err != nil {
		log.Fatal(err)
	}
	f := &fixture{entries: make(map[string]*observation)}
	plain := httptest.NewUnstartedServer(f)
	plain.Config.ConnContext = connectionContext
	plain.Start()
	servers := []*httptest.Server{plain}
	urls := []string{}
	for _, h2 := range []bool{false, true} {
		s := httptest.NewUnstartedServer(f)
		s.EnableHTTP2 = h2
		s.Config.ConnContext = connectionContext
		s.TLS = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
		s.Config.ErrorLog = log.New(io.Discard, "", 0)
		s.StartTLS()
		servers = append(servers, s)
		urls = append(urls, s.URL)
	}
	json.NewEncoder(os.Stdout).Encode(map[string]string{"http": plain.URL, "https1": urls[0], "https2": urls[1], "ca_file": ca, "wrong_ca_file": wrong})
	io.Copy(io.Discard, os.Stdin)
	for _, s := range servers {
		s.CloseClientConnections()
		s.Close()
	}
	fmt.Fprintln(os.Stderr, "fixture stopped")
}
