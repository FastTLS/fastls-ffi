package bridge

import (
	"context"
	"io"
	"testing"
	"time"
)

func TestUploadBoundedCopyEOFAndCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	u := &upload{ctx: ctx, wake: make(chan struct{}, 1)}
	e := New()
	e.requests[1] = &request{upload: u}
	data := make([]byte, ChunkSize)
	data[0] = 42
	if e.Write(1, data, false) != Data {
		t.Fatal("write")
	}
	data[0] = 99
	if e.Write(1, data, false) != Pending {
		t.Fatal("unbounded queue")
	}
	b := make([]byte, 1)
	if n, err := u.Read(b); n != 1 || err != nil || b[0] != 42 {
		t.Fatal("caller buffer retained")
	}
	if e.Write(1, data, false) != Pending {
		t.Fatal("partial chunk bypassed bound")
	}
	if _, err := io.ReadFull(u, make([]byte, ChunkSize-1)); err != nil {
		t.Fatal(err)
	}
	if e.Write(1, nil, true) != Data {
		t.Fatal("EOF write")
	}
	if n, err := u.Read(b); n != 0 || err != io.EOF {
		t.Fatal("EOF read", n, err)
	}
	if e.Write(1, data, false) != Failed {
		t.Fatal("write after EOF")
	}
	u = &upload{ctx: ctx, wake: make(chan struct{}, 1)}
	done := make(chan error, 1)
	go func() { _, err := u.Read(b); done <- err }()
	cancel()
	select {
	case err := <-done:
		if err != context.Canceled {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("blocked upload reader")
	}
}
