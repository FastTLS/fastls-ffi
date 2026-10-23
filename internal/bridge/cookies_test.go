package bridge

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMultivalueInfoPreservesLegacyHeaders(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Add("Set-Cookie", "a=one; Path=/")
		w.Header().Add("Set-Cookie", "b=two; Path=/")
		w.Write([]byte("ok"))
	}))
	defer s.Close()
	e := New()
	id := start(t, e, Input{URL: s.URL})
	info := waitInfo(t, e, id)
	if len(info.HeaderValues["Set-Cookie"]) != 2 {
		t.Fatalf("multivalue fields: %v", info.HeaderValues)
	}
	if info.Headers["Set-Cookie"] != "a=one; Path=//,/b=two; Path=/" {
		t.Fatalf("legacy field changed: %q", info.Headers["Set-Cookie"])
	}
}

func TestCookieCommandsRejectInvalidInputAndClosedSession(t *testing.T) {
	e := New()
	id, err := e.CreateSession()
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(id)
	for _, raw := range []string{`null`, `{}`, `{"action":"get","url":"file:///tmp/a"}`, `{"action":"clear","unknown":true}`, `{"action":"clear"} {}`, `{"action":"set","url":"https://example.com"}`} {
		if _, err := e.SessionCookies(id, []byte(raw)); err == nil {
			t.Fatalf("accepted %q", raw)
		}
	}
	if _, err := e.SessionCookies(id, []byte(`{"action":"set","url":"https://example.com","cookie":{"name":"token","value":"yes"}}`)); err != nil {
		t.Fatal(err)
	}
	e.CloseSession(id)
	if _, err := e.SessionCookies(id, []byte(`{"action":"get","url":"https://example.com"}`)); err == nil {
		t.Fatal("closed session accepted cookie query")
	}
}
