package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Benchmark observations are aggregated by run, not by individual request. The
// normal acceptance routes retain their original observation/lifetime semantics.
var benchmarkRuns = struct {
	sync.Mutex
	items map[string]*benchmarkStats
}{items: make(map[string]*benchmarkStats)}

type benchmarkStats struct {
	Requests    int64                          `json:"requests"`
	Active      int64                          `json:"active"`
	Written     int64                          `json:"written"`
	Received    int64                          `json:"received"`
	Connections map[uint64]benchmarkConnection `json:"connections"`
}
type benchmarkConnection struct {
	Protocol string `json:"protocol"`
	TLS      uint16 `json:"tls"`
	Cipher   uint16 `json:"cipher"`
	Resumed  bool   `json:"resumed"`
}

func benchmarkRoute(w http.ResponseWriter, r *http.Request) bool {
	if !strings.HasPrefix(r.URL.Path, "/bench/") {
		return false
	}
	id := r.URL.Query().Get("run")
	benchmarkRuns.Lock()
	s := benchmarkRuns.items[id]
	if s == nil {
		s = &benchmarkStats{Connections: make(map[uint64]benchmarkConnection)}
		benchmarkRuns.items[id] = s
	}
	if r.URL.Path == "/bench/stats" {
		defer benchmarkRuns.Unlock()
		json.NewEncoder(w).Encode(s)
		if r.URL.Query().Get("delete") == "1" && s.Active == 0 {
			delete(benchmarkRuns.items, id)
		}
		return true
	}
	s.Requests++
	s.Active++
	connID, _ := r.Context().Value(connectionKey{}).(uint64)
	c := benchmarkConnection{Protocol: r.Proto}
	if r.TLS != nil {
		c.TLS, c.Cipher, c.Resumed = r.TLS.Version, r.TLS.CipherSuite, r.TLS.DidResume
	}
	s.Connections[connID] = c
	benchmarkRuns.Unlock()
	var written, received int64
	defer func() {
		benchmarkRuns.Lock()
		s.Active--
		if r.URL.Path != "/bench/slow" {
			s.Written += written
		}
		s.Received += received
		benchmarkRuns.Unlock()
	}()
	w.Header().Set("X-Test-Protocol", r.Proto)
	w.Header().Set("X-Test-Connection", strconv.FormatUint(connID, 10))
	size, _ := strconv.ParseInt(r.URL.Query().Get("n"), 10, 64)
	if size < 1 || size > 128*1024*1024 {
		http.Error(w, "invalid benchmark size", 400)
		return true
	}
	switch r.URL.Path {
	case "/bench/upload":
		h := sha256.New()
		received, _ = io.Copy(h, io.LimitReader(r.Body, size+1))
		if received != size {
			http.Error(w, "unexpected upload length", 400)
			return true
		}
		json.NewEncoder(w).Encode(map[string]any{"bytes": received, "sha256": fmt.Sprintf("%x", h.Sum(nil))})
	case "/bench/idle":
		w.Header().Set("Content-Type", "text/event-stream")
		event := "data: " + strings.Repeat("x", 56) + "\n\n"
		n, _ := io.WriteString(w, event)
		written += int64(n)
		w.(http.Flusher).Flush()
		ms, _ := strconv.Atoi(r.URL.Query().Get("hold_ms"))
		if ms < 1 || ms > 30000 {
			ms = 1000
		}
		timer := time.NewTimer(time.Duration(ms) * time.Millisecond)
		defer timer.Stop()
		select {
		case <-r.Context().Done():
			return true
		case <-timer.C:
		}
		n, _ = io.WriteString(w, event)
		written += int64(n)
	default:
		var out io.Writer = w
		var compressed *gzip.Writer
		if r.URL.Query().Get("gzip") == "1" {
			w.Header().Set("Content-Encoding", "gzip")
			compressed = gzip.NewWriter(w)
			out = compressed
		} else {
			w.Header().Set("Content-Length", strconv.FormatInt(size, 10))
		}
		slab := bytes.Repeat([]byte{0, 1, 128, 255}, 16384)
		for written < size {
			part := slab
			if remaining := size - written; remaining < int64(len(part)) {
				part = part[:remaining]
			}
			n, err := out.Write(part)
			written += int64(n)
			if r.URL.Path == "/bench/slow" {
				benchmarkRuns.Lock()
				s.Written += int64(n)
				benchmarkRuns.Unlock()
			}
			if err != nil {
				break
			}
		}
		if compressed != nil {
			compressed.Close()
		}
	}
	return true
}
