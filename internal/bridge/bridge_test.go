package bridge

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func start(t *testing.T, e *Engine, in Input) uint64 {
	t.Helper()
	b, _ := json.Marshal(in)
	id, err := e.Start(b)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { e.Close(id) })
	return id
}

func waitInfo(t *testing.T, e *Engine, id uint64) Info {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		info, err := e.Info(id)
		if err != nil {
			t.Fatal(err)
		}
		if info.State != "pending" {
			return info
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("headers timed out")
	return Info{}
}

func TestBinaryAndStableEOF(t *testing.T) {
	data := bytes.Repeat([]byte{0, 1, 128, 255}, 100000)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(data) }))
	defer s.Close()
	e := New()
	id := start(t, e, Input{URL: s.URL})
	if info := waitInfo(t, e, id); info.Status != 200 {
		t.Fatalf("%+v", info)
	}
	var got []byte
	b := make([]byte, 777)
	deadline := time.Now().Add(5 * time.Second)
	for {
		if time.Now().After(deadline) {
			t.Fatal("read timed out")
		}
		n, code := e.Read(id, b)
		if code == EOF {
			break
		}
		if code < 0 {
			t.Fatalf("read: %d", code)
		}
		got = append(got, b[:n]...)
		if code == Pending {
			time.Sleep(time.Millisecond)
		}
	}
	if !bytes.Equal(got, data) {
		t.Fatalf("binary body mismatch: %d", len(got))
	}
	if _, code := e.Read(id, b); code != EOF {
		t.Fatalf("EOF not stable: %d", code)
	}
	if info, _ := e.Info(id); info.State != "eof" {
		t.Fatalf("EOF info: %+v", info)
	}
	e.Close(id)
	e.Close(id)
	if _, err := e.Info(id); err == nil {
		t.Fatal("closed handle still accessible")
	}
	if _, code := e.Read(id, b); code != Invalid {
		t.Fatalf("closed read: %d", code)
	}
}

func TestBoundedStreamAndCancel(t *testing.T) {
	finished := make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(finished)
		b := make([]byte, ChunkSize)
		for i := 0; i < 1024; i++ {
			if _, err := w.Write(b); err != nil {
				return
			}
			w.(http.Flusher).Flush()
		}
	}))
	defer s.Close()
	e := New()
	id := start(t, e, Input{URL: s.URL})
	if info := waitInfo(t, e, id); info.State != "headers" {
		t.Fatalf("%+v", info)
	}
	// An undrained 64MiB stream must not finish by accumulating in our bridge.
	select {
	case <-finished:
		t.Fatal("producer finished without a consumer")
	case <-time.After(150 * time.Millisecond):
	}
	if err := e.Cancel(id); err != nil {
		t.Fatal(err)
	}
	if _, code := e.Read(id, make([]byte, 100)); code != Failed {
		t.Fatalf("cancel read: %d", code)
	}
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatal("cancel failed to stop producer")
	}
}

func TestUnknownMalformedAndTimeout(t *testing.T) {
	e := New()
	for _, input := range []string{
		`{"url":"http://example.com","profile":"does-not-exist"}`,
		`{"url":"file:///tmp/a"}`, `{"url":"http://example.com","oops":1}`,
		`{"url":"http://example.com"} {}`, `null`,
	} {
		if _, err := e.Start([]byte(input)); err == nil {
			t.Fatalf("accepted %s", input)
		}
	}
	stopped := make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done(); close(stopped) }))
	defer s.Close()
	id := start(t, e, Input{URL: s.URL, TimeoutMS: 100})
	info := waitInfo(t, e, id)
	if info.State != "error" || !strings.Contains(info.Error, "deadline") {
		t.Fatalf("timeout: %+v", info)
	}
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("timeout did not stop server request")
	}
}

func TestTLSVerificationAndTruncation(t *testing.T) {
	e := New()
	tls := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "not trusted") }))
	defer tls.Close()
	id := start(t, e, Input{URL: tls.URL})
	info := waitInfo(t, e, id)
	if info.State != "error" || !strings.Contains(info.Error, "certificate") {
		t.Fatalf("untrusted TLS: %+v", info)
	}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "100")
		fmt.Fprint(w, "short")
	}))
	defer s.Close()
	id = start(t, e, Input{URL: s.URL})
	waitInfo(t, e, id)
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		_, code := e.Read(id, make([]byte, 100))
		if code == Failed {
			return
		}
		if code == EOF {
			t.Fatal("truncation reported EOF")
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("truncation not reported")
}
