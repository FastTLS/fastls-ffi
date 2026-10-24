package bridge

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestWebSocketEngineConcurrentLifetime(t *testing.T) {
	u := websocket.Upgrader{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := u.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		for {
			kind, data, err := c.ReadMessage()
			if err != nil {
				return
			}
			if c.WriteMessage(kind, data) != nil {
				return
			}
		}
	}))
	defer srv.Close()
	e := New()
	session, err := e.CreateSession()
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(session)
	var group sync.WaitGroup
	for i := 0; i < 16; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			raw, _ := json.Marshal(WebSocketInput{URL: strings.Replace(srv.URL, "http", "ws", 1), Session: session})
			id, err := e.StartWebSocket(raw)
			if err != nil {
				t.Error(err)
				return
			}
			defer e.Close(id)
			if e.Wait(id, WaitHeaders, 3*time.Second) != Data {
				t.Error("handshake wait")
				return
			}
			for e.WebSocketSend(id, 2, []byte{0, 128, 255}) == Pending {
				e.Wait(id, WaitWrite, time.Second)
			}
			buffer := make([]byte, 8)
			for {
				n, kind, end, code := e.WebSocketRead(id, buffer)
				if code == Pending {
					e.Wait(id, WaitRead, time.Second)
					continue
				}
				if code != Data || n != 3 || kind != 2 || !end {
					t.Error("message", n, kind, end, code)
				}
				break
			}
		}()
	}
	group.Wait()
	e.CloseSession(session)
	deadline := time.Now().Add(3 * time.Second)
	for e.workers.Load() != 0 {
		if time.Now().After(deadline) {
			t.Fatal("WebSocket workers retained")
		}
		time.Sleep(time.Millisecond)
	}
	if e.Stats()["open_requests"] != 0 {
		t.Fatal("WebSocket handles retained")
	}
}

func TestWebSocketEngineInvalidContracts(t *testing.T) {
	e := New()
	for _, raw := range []string{`{}`, `{"url":"https://localhost/"}`, `{"url":"ws://u:p@localhost/"}`, `{"url":"ws://localhost/#x"}`, `{"url":"ws://localhost/","max_message_bytes":-1}`, `{"url":"ws://localhost/","subprotocols":["bad protocol"]}`, `{"url":"ws://localhost/","unknown":true}`} {
		if _, err := e.StartWebSocket([]byte(raw)); err == nil {
			t.Error("accepted invalid input", raw)
		}
	}
	if e.WebSocketSend(0, 1, nil) != Invalid {
		t.Fatal("accepted invalid handle")
	}
	if _, _, _, code := e.WebSocketRead(0, make([]byte, 1)); code != Invalid {
		t.Fatal("accepted invalid reader")
	}
}

func TestWebSocketSessionCookieExchange(t *testing.T) {
	for _, name := range []string{"inherited", "explicit", "disabled", "rejected"} {
		t.Run(name, func(t *testing.T) {
			seen := make(chan string, 1)
			upgrader := websocket.Upgrader{}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				seen <- r.Header.Get("Cookie")
				header := http.Header{"Set-Cookie": {"reply=one; Path=/", "second=two; Path=/"}}
				if name == "rejected" {
					w.Header()["Set-Cookie"] = header["Set-Cookie"]
					http.Error(w, "denied", 401)
					return
				}
				c, err := upgrader.Upgrade(w, r, header)
				if err == nil {
					defer c.Close()
					c.ReadMessage() // Wait for the client to close.
				}
			}))
			defer server.Close()
			e := New()
			session, _ := e.CreateSession()
			defer e.CloseSession(session)
			command, _ := json.Marshal(map[string]any{"action": "set", "url": server.URL, "cookie": map[string]string{"name": "login", "value": "yes", "path": "/"}})
			if _, err := e.SessionCookies(session, command); err != nil {
				t.Fatal(err)
			}
			input := WebSocketInput{URL: strings.Replace(server.URL, "http", "ws", 1), Session: session}
			want := "login=yes"
			if name == "explicit" {
				input.Headers = map[string]string{"cOoKiE": "manual=only"}
				want = "manual=only"
			}
			if name == "disabled" {
				if _, err := e.SessionCookies(session, []byte(`{"action":"configure","enabled":false}`)); err != nil {
					t.Fatal(err)
				}
				want = ""
			}
			raw, _ := json.Marshal(input)
			id, err := e.StartWebSocket(raw)
			if err != nil {
				t.Fatal(err)
			}
			defer e.Close(id)
			e.Wait(id, WaitHeaders, 3*time.Second)
			select {
			case got := <-seen:
				if got != want {
					t.Fatalf("Cookie = %q, want %q", got, want)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("no handshake")
			}
			get, _ := json.Marshal(map[string]string{"action": "get", "url": server.URL})
			stored, err := e.SessionCookies(session, get)
			if err != nil {
				t.Fatal(err)
			}
			encoded, _ := json.Marshal(stored)
			if name == "disabled" {
				if string(encoded) != "[]" {
					t.Fatal(string(encoded))
				}
			} else if !strings.Contains(string(encoded), `"name":"reply"`) || !strings.Contains(string(encoded), `"name":"second"`) {
				t.Fatal("handshake cookies were not stored:", string(encoded))
			}
		})
	}
}

func TestWebSocketCancelRejectsQueuedMessages(t *testing.T) {
	u := websocket.Upgrader{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := u.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		c.WriteMessage(websocket.TextMessage, []byte("queued"))
		c.ReadMessage()
	}))
	defer srv.Close()
	e := New()
	raw, _ := json.Marshal(WebSocketInput{URL: strings.Replace(srv.URL, "http", "ws", 1)})
	id, err := e.StartWebSocket(raw)
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close(id)
	if e.Wait(id, WaitHeaders, 3*time.Second) != Data || e.Wait(id, WaitRead, 3*time.Second) != Data {
		t.Fatal("message never became ready")
	}
	if err = e.Cancel(id); err != nil {
		t.Fatal(err)
	}
	if _, _, _, status := e.WebSocketRead(id, make([]byte, 32)); status != Failed {
		t.Fatal("cancelled connection delivered queued data", status)
	}
}
