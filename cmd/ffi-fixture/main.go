// ffi-fixture is a loopback-only, independently implemented acceptance server.
// It generates an ephemeral CA; no system trust store or private key file changes.
package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
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
	switch r.URL.Path {
	case "/wait":
		select {
		case <-o.release:
			io.WriteString(w, "released")
		case <-r.Context().Done():
		}
	case "/hold":
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
	plain := httptest.NewServer(f)
	servers := []*httptest.Server{plain}
	urls := []string{}
	for _, h2 := range []bool{false, true} {
		s := httptest.NewUnstartedServer(f)
		s.EnableHTTP2 = h2
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
