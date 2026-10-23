package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http/httptest"
	"testing"
)

func TestBenchmarkBodyHashCompressionAndRouteIsolation(t *testing.T) {
	f := &fixture{entries: make(map[string]*observation)}
	want := bytes.Repeat([]byte{0, 1, 128, 255}, 20000)
	for _, compressed := range []bool{false, true} {
		r := httptest.NewRequest("GET", fmt.Sprintf("/bench/download?run=body-%v&n=%d&gzip=%d", compressed, len(want), map[bool]int{false: 0, true: 1}[compressed]), nil)
		w := httptest.NewRecorder()
		f.ServeHTTP(w, r)
		var reader io.Reader = w.Body
		if compressed {
			decoded, err := gzip.NewReader(w.Body)
			if err != nil {
				t.Fatal(err)
			}
			defer decoded.Close()
			reader = decoded
		}
		body, err := io.ReadAll(reader)
		if err != nil || !bytes.Equal(body, want) || w.Header().Get("X-Test-Protocol") != "HTTP/1.1" {
			t.Fatal("body/protocol mismatch", err)
		}
	}
	if len(f.entries) != 0 {
		t.Fatal("benchmark requests accumulated ordinary fixture observations")
	}
}

func TestBenchmarkUploadAndStats(t *testing.T) {
	data := []byte{0, 1, 128, 255}
	w := httptest.NewRecorder()
	if !benchmarkRoute(w, httptest.NewRequest("POST", "/bench/upload?run=upload-test&n=4", bytes.NewReader(data))) {
		t.Fatal("route")
	}
	var result struct {
		Bytes int    `json:"bytes"`
		SHA   string `json:"sha256"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Bytes != 4 || result.SHA != fmt.Sprintf("%x", sha256.Sum256(data)) {
		t.Fatal("upload hash")
	}
	w = httptest.NewRecorder()
	benchmarkRoute(w, httptest.NewRequest("GET", "/bench/stats?run=upload-test", nil))
	var stats benchmarkStats
	if err := json.Unmarshal(w.Body.Bytes(), &stats); err != nil {
		t.Fatal(err)
	}
	if stats.Active != 0 || stats.Received != 4 || stats.Requests != 1 {
		t.Fatal("incorrect stats", stats)
	}
}
