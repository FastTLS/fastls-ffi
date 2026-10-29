package bridge

import (
	"bytes"
	"encoding/json"
	"fmt"
	fastls "github.com/FastTLS/fastls"
	use "github.com/FastTLS/fastls/imitate"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func completeSpec(t *testing.T, e *Engine) []byte {
	t.Helper()
	var o fastls.Options
	if _, err := use.Use(&o, "chrome/156/windows", use.WithProvider(e.provider)); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(o.Profile)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
func TestTransportConfigurationRejectsMalformedWithoutHandles(t *testing.T) {
	e := New()
	for _, raw := range []string{`null`, `{}`, `{"schema_version":2}`, `{"schema_version":1,"schema_version":1}`,
		`{"schema_version":1,"http_version":null}`, `{"schema_version":1,"profile":null}`,
		`{"schema_version":1,"HTTP_VERSION":"h3"}`, `{"schema_version":1,"address_policy":{"Allow_Hosts":[]}}`,
		`{"schema_version":1,"unknown":true}`, `{"schema_version":1,"address_policy":null}`,
		`{"schema_version":1,"address_policy":{"deny_cidrs":null}}`,
		`{"schema_version":1,"address_policy":{"allow_hosts":[],"allow_hosts":[]}}`,
		`{"schema_version":1,"profile_spec":null}`, `{"schema_version":1,"http_version":"h2"}`,
		`{"schema_version":1,"profile_spec":{}}`,
		`{"schema_version":1,"http_version":"h3","profile":"chrome/156/windows"}`,
		`{"schema_version":1,"http_version":"h3","address_policy":{}}`,
		`{"schema_version":1}` + strings.Repeat(" ", MaxInput),
		`{"schema_version":1,"profile_spec":` + strings.Repeat("[", 33) + `0` + strings.Repeat("]", 33) + `}`} {
		if id, err := e.CreateTransportWithOptions([]byte(raw)); err == nil || id != 0 {
			t.Fatalf("accepted invalid config: %d %v", id, err)
		}
	}
	if e.Stats()["open_sessions"] != 0 {
		t.Fatal("configuration failure leaked owners")
	}
}
func TestCompleteSpecSnapshotAndPresence(t *testing.T) {
	e := New()
	raw := completeSpec(t, e)
	var value map[string]any
	_ = json.Unmarshal(raw, &value)
	h := value["http2"].(map[string]any)
	h["settings"] = []any{map[string]any{"id": float64(2), "value": float64(0)}}
	raw, _ = json.Marshal(value)
	c, err := e.CreateTransportWithOptions(append(append([]byte(`{"schema_version":1,"profile_spec":`), raw...), '}'))
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(c)
	raw[0] = '!'
	owner, _ := e.transportConfiguration(c)
	if len(owner.spec.HTTP2.Settings) != 1 || owner.spec.HTTP2.Settings[0].Value != 0 {
		t.Fatal("explicit zero lost")
	}
	for _, mode := range []string{"empty", "missing", "null", "unknown", "duplicate"} {
		h["settings"] = []any{}
		if mode == "missing" {
			delete(h, "settings")
		}
		if mode == "null" {
			h["settings"] = nil
		}
		if mode == "unknown" {
			h["surprise"] = true
		}
		b, _ := json.Marshal(value)
		if mode == "duplicate" {
			b = bytes.Replace(b, []byte(`"settings":[]`), []byte(`"settings":[],"settings":[]`), 1)
		}
		s, _, err := parseSpec(b)
		if mode == "empty" {
			if err != nil || s.HTTP2.Settings == nil || len(s.HTTP2.Settings) != 0 {
				t.Fatal("explicit empty settings lost", err)
			}
		} else if err == nil {
			t.Fatal("accepted", mode)
		}
		delete(h, "surprise")
	}
}
func waitBridge(t *testing.T, e *Engine, id uint64) Info {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		i, err := e.Info(id)
		if err != nil {
			t.Fatal(err)
		}
		if i.State != "pending" {
			return i
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("request stalled")
	return Info{}
}
func TestPolicyOwnerBindingAndNoDeniedDial(t *testing.T) {
	var hits atomic.Int32
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hits.Add(1); fmt.Fprint(w, "ok") }))
	defer origin.Close()
	e := New()
	denied, err := e.CreateTransportWithOptions([]byte(`{"schema_version":1,"address_policy":{"deny_cidrs":["127.0.0.0/8"]}}`))
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(denied)
	allowed, err := e.CreateTransportWithOptions([]byte(`{"schema_version":1,"address_policy":{"allow_cidrs":["127.0.0.0/8"]}}`))
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(allowed)
	for _, owner := range []uint64{denied, allowed, denied} {
		raw, _ := json.Marshal(Input{Session: owner, URL: origin.URL, DisableRedirect: true})
		id, err := e.Start(raw)
		if err != nil {
			t.Fatal(err)
		}
		i := waitBridge(t, e, id)
		if owner == denied && (i.State != "error" || i.ErrorCode != "address_denied") {
			t.Fatal(i)
		}
		if owner == allowed && i.Status != 200 {
			t.Fatal(i)
		}
		e.Close(id)
	}
	if hits.Load() != 1 {
		t.Fatal("denied owner reached origin", hits.Load())
	}
	for _, extra := range []string{`,"proxy":"http://127.0.0.1:1"`, `,"http_version":"h3"`, `,"address_policy":{}`} {
		raw := []byte(fmt.Sprintf(`{"session":%d,"url":%q%s}`, denied, origin.URL, extra))
		if _, err := e.Start(raw); err == nil {
			t.Fatal("policy bypass", extra)
		}
	}
	ws := []byte(fmt.Sprintf(`{"session":%d,"url":"ws://127.0.0.1:1"}`, denied))
	if _, err := e.StartWebSocket(ws); ErrorCode(err) != "unsupported_capability" {
		t.Fatal("WebSocket bypass", err)
	}
}
func TestH3ConflictsRejectedBeforeRequestRegistration(t *testing.T) {
	e := New()
	owner, err := e.CreateTransportWithOptions([]byte(`{"schema_version":1,"http_version":"h3"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(owner)
	for _, extra := range []string{`,"proxy":"http://127.0.0.1:1"`, `,"profile":"chrome/156/windows"`, `,"duplex":true,"stream_body":true,"disable_redirect":true`, `,"http_version":"auto"`, `,"trailers":{"X-End":["v"]}`} {
		raw := []byte(fmt.Sprintf(`{"session":%d,"url":"https://127.0.0.1:1"%s}`, owner, extra))
		if _, err := e.Start(raw); err == nil {
			t.Fatal("accepted H3 conflict", extra)
		}
	}
	if e.Stats()["open_requests"] != 0 {
		t.Fatal("failed preflight allocated requests")
	}
}
