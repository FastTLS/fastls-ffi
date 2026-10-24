package bridge

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"runtime"
	"strconv"
	"syscall"
	"testing"
	"time"
)

func waitFixture() (*Engine, *request) {
	e := New()
	ctx, cancel := context.WithCancel(context.Background())
	r := &request{id: 1, ctx: ctx, cancel: cancel, chunks: make(chan chunk, 1), info: Info{State: "pending"}}
	e.requests[1] = r
	return e, r
}

func TestWaitGenerationAndTerminalReadiness(t *testing.T) {
	e, r := waitFixture()
	defer r.cancel()
	for i := 0; i < 1000; i++ {
		if e.Wait(1, WaitRead, 0) != Pending {
			t.Fatal("empty stream ready")
		}
		done := make(chan int, 1)
		go func() { done <- e.Wait(1, WaitRead, time.Second) }()
		r.buffered.Add(1)
		r.chunks <- chunk{data: []byte{42}}
		r.signal()
		select {
		case code := <-done:
			if code != Data {
				t.Fatal("lost wake", code)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("wait stuck")
		}
		data := make([]byte, 1)
		if n, code := e.Read(1, data); n != 1 || code != Data || data[0] != 42 {
			t.Fatal("read", n, code, data)
		}
	}
	r.chunks <- chunk{err: io.EOF}
	r.signal()
	if e.Wait(1, WaitRead, time.Second) != Data {
		t.Fatal("EOF not ready")
	}
	if _, code := e.Read(1, make([]byte, 1)); code != EOF {
		t.Fatal("EOF")
	}
	if e.Wait(1, WaitRead, 0) != Data {
		t.Fatal("consumed EOF not ready")
	}
}

func TestWaitHeadersWriteCancellationAndValidation(t *testing.T) {
	e, r := waitFixture()
	defer r.cancel()
	for _, args := range []struct {
		id      uint64
		mask    uint32
		timeout time.Duration
	}{{2, WaitRead, 0}, {1, 0, 0}, {1, 8, 0}, {1, WaitRead, -1}, {1, WaitRead, 2 * time.Minute}} {
		if e.Wait(args.id, args.mask, args.timeout) != Invalid {
			t.Fatal("invalid wait accepted")
		}
	}
	if e.Wait(1, WaitHeaders, 5*time.Millisecond) != Pending {
		t.Fatal("timeout")
	}
	r.mu.Lock()
	r.info.State = "headers"
	r.mu.Unlock()
	r.signal()
	if e.Wait(1, WaitHeaders, 0) != Data {
		t.Fatal("headers")
	}
	r.upload = &upload{ctx: r.ctx, wake: make(chan struct{}, 1), changed: r.signal}
	if e.Write(1, []byte("body"), false) != Data {
		t.Fatal("write")
	}
	if e.Wait(1, WaitWrite, 0) != Pending {
		t.Fatal("full upload ready")
	}
	done := make(chan int, 1)
	go func() { done <- e.Wait(1, WaitWrite, time.Second) }()
	if _, err := r.upload.Read(make([]byte, 4)); err != nil {
		t.Fatal(err)
	}
	if <-done != Data {
		t.Fatal("write capacity not signalled")
	}
	go func() { done <- e.Wait(1, WaitRead, time.Second) }()
	r.cancel()
	if <-done != Data {
		t.Fatal("close did not unblock wait")
	}
	e.Close(1)
	if e.Wait(1, WaitRead, 0) != Invalid {
		t.Fatal("closed handle accepted")
	}
}

func TestWatchBoundedCoalescingAndOverflow(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w := &watcher{dirty: make(map[uint64]struct{}), wake: make(chan struct{}, 1), ctx: ctx, cancel: cancel}
	for id := uint64(1); id < 10000; id++ {
		w.signal(id)
	}
	if len(w.wake) != 1 || len(w.dirty) != MaxRequests {
		t.Fatal("unbounded notifications")
	}
	batch := w.drain()
	if !batch.Overflow || len(batch.Handles) != MaxRequests {
		t.Fatal("overflow not reported")
	}
	w.signal(12345)
	if batch = w.drain(); batch.Overflow || len(batch.Handles) != 1 || batch.Handles[0] != 12345 {
		t.Fatal("lost signal after drain")
	}
	w.close()
	w.signal(99999)
	if len(w.drain().Handles) != 0 {
		t.Fatal("closed notifier retained events")
	}
}

func TestWatchSessionHandshakeOwnershipAndClose(t *testing.T) {
	e := New()
	id, _ := e.CreateSession()
	defer e.CloseSession(id)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, portText, _ := net.SplitHostPort(listener.Addr().String())
	port, _ := strconv.Atoi(portText)
	token := bytes.Repeat([]byte{42}, 32)
	if e.WatchSession(id, 0, token) == nil || e.WatchSession(id, uint16(port), token[:31]) == nil || e.WatchSession(999999, uint16(port), token) == nil {
		t.Fatal("invalid endpoint")
	}
	if err = e.WatchSession(id, uint16(port), token); err != nil {
		t.Fatal(err)
	}
	if err = e.WatchSession(id, uint16(port), token); err == nil {
		t.Fatal("duplicate registration")
	}
	conn, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	hello := make([]byte, 32)
	if _, err = io.ReadFull(conn, hello); err != nil || !bytes.Equal(hello, token) {
		t.Fatal("handshake", err)
	}
	e.sessions[id].watch.signal(42)
	if _, err = io.ReadFull(conn, make([]byte, 1)); err != nil {
		t.Fatal("wake", err)
	}
	batch, err := e.SessionEvents(id)
	if err != nil || len(batch.Handles) != 1 || batch.Handles[0] != 42 {
		t.Fatal("events", batch, err)
	}
	other, _ := e.CreateSession()
	defer e.CloseSession(other)
	if _, err = e.SessionEvents(other); err == nil {
		t.Fatal("foreign session saw events")
	}
	e.CloseSession(id)
	if _, err = conn.Read(make([]byte, 1)); !errors.Is(err, syscall.ECONNRESET) && !(runtime.GOOS == "windows" && errors.Is(err, syscall.Errno(10054))) {
		t.Fatal("connection not closed", err)
	}
	if _, err = e.SessionEvents(id); err == nil {
		t.Fatal("closed session saw events")
	}
}

func TestWatchDialFailureRemainsObservable(t *testing.T) {
	e := New()
	id, _ := e.CreateSession()
	defer e.CloseSession(id)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	if err := e.WatchSession(id, uint16(port), bytes.Repeat([]byte{1}, 32)); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := e.SessionEvents(id); err != nil {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("notification dial failure was silently lost")
}
