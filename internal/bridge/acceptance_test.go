package bridge

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestCancelWithUnreadFinalChunk(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "final-body") }))
	defer s.Close()
	e := New()
	id := start(t, e, Input{URL: s.URL})
	waitInfo(t, e, id)
	b := make([]byte, 1)
	deadline := time.Now().Add(3 * time.Second)
	for {
		n, code := e.Read(id, b)
		if n > 0 {
			break
		}
		if code < 0 || time.Now().After(deadline) {
			t.Fatal("no first byte")
		}
		time.Sleep(time.Millisecond)
	}
	e.Cancel(id)
	if _, code := e.Read(id, b); code != Failed {
		t.Fatalf("cancel leaked unread final bytes: status %d", code)
	}
}

func TestConcurrentControlAndRegistryRecovery(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "first")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer s.Close()
	e := New()
	for round := 0; round < 8; round++ {
		var ids []uint64
		for i := 0; i < 32; i++ {
			ids = append(ids, start(t, e, Input{URL: s.URL}))
		}
		var wg sync.WaitGroup
		for _, id := range ids {
			wg.Add(3)
			go func(id uint64) {
				defer wg.Done()
				for i := 0; i < 8; i++ {
					e.Info(id)
					e.Stats()
				}
			}(id)
			go func(id uint64) { defer wg.Done(); e.Read(id, make([]byte, 1)); e.Cancel(id) }(id)
			go func(id uint64) { defer wg.Done(); e.Close(id); e.Close(id) }(id)
		}
		wg.Wait()
	}
	deadline := time.Now().Add(5 * time.Second)
	for e.Stats()["workers"].(int64) != 0 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if stats := e.Stats(); stats["open_requests"] != 0 || stats["workers"] != int64(0) {
		t.Fatalf("workers/registry leaked: %+v", stats)
	}
	// A full registry is an ownership limit, even if a network worker has finished.
	raw, _ := json.Marshal(Input{URL: s.URL, TimeoutMS: 1000})
	var handles []uint64
	for i := 0; i < MaxRequests; i++ {
		id, err := e.Start(raw)
		if err != nil {
			t.Fatal(err)
		}
		handles = append(handles, id)
	}
	if _, err := e.Start(raw); err == nil {
		t.Fatal("registry limit was bypassed")
	}
	e.Close(handles[0])
	id, err := e.Start(raw)
	if err != nil {
		t.Fatalf("released slot not reusable: %v", err)
	}
	e.Close(id)
	for _, id := range handles {
		e.Close(id)
	}
}
