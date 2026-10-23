package bridge

import (
	"encoding/json"
	"testing"
)

func TestSessionHandleLimitsAndClosedOwner(t *testing.T) {
	e := New()
	ids := make([]uint64, 64)
	for i := range ids {
		var err error
		ids[i], err = e.CreateSession()
		if err != nil {
			t.Fatal(err)
		}
	}
	if _, err := e.CreateSession(); err == nil {
		t.Fatal("session limit not enforced")
	}
	e.CloseSession(ids[0])
	e.CloseSession(ids[0])
	if err := e.CloseSessionIdle(ids[0]); err == nil {
		t.Fatal("closed session accepted idle close")
	}
	raw, _ := json.Marshal(Input{URL: "http://127.0.0.1:1", Session: ids[0]})
	if _, err := e.Start(raw); err == nil {
		t.Fatal("closed session accepted request")
	}
	if e.Stats()["open_requests"] != 0 {
		t.Fatal("invalid session registered request")
	}
	for _, id := range ids {
		e.CloseSession(id)
	}
	if e.Stats()["open_sessions"] != 0 {
		t.Fatal("sessions retained")
	}
	id, err := e.CreateSession()
	if err != nil {
		t.Fatal(err)
	}
	e.CloseSession(id)
}
