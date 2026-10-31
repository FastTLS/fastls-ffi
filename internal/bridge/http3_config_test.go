package bridge

import (
	"encoding/json"
	fastls "github.com/FastTLS/fastls"
	"testing"
	"time"
)

func TestHTTP3ConfigStrictOwnerBoundary(t *testing.T) {
	e := New()
	baseline := e.Stats()["open_sessions"]
	invalid := []string{`null`, `[]`, `{"unknown":1}`, `{"MaxIdleTimeoutMS":100}`, `{"max_idle_timeout_ms":null}`, `{"keep_alive_period_ms":null}`, `{"max_idle_timeout_ms":true}`, `{"max_idle_timeout_ms":1.5}`, `{"max_idle_timeout_ms":9223372036854775807}`, `{"max_stream_receive_window":18446744073709551616}`, `{"initial_stream_receive_window":1}`, `{"max_response_header_bytes":1023}`, `{"keep_alive_period_ms":1}`, `{"keep_alive_period_ms":15001}`, `{"initial_stream_receive_window":8388608}`, `{"max_idle_timeout_ms":100,"max_idle_timeout_ms":200}`}
	for _, raw := range invalid {
		if id, err := e.CreateTransportWithOptions([]byte(`{"schema_version":1,"http_version":"h3","http3_config":` + raw + `}`)); err == nil {
			e.CloseSession(id)
			t.Fatal("accepted", raw)
		} else if ErrorCode(err) != "invalid_configuration" {
			t.Fatal(raw, err)
		}
	}
	for _, raw := range []string{`{"schema_version":1,"http3_config":{}}`, `{"schema_version":1,"http_version":"auto","http3_config":{}}`, `{"schema_version":1,"http_version":"h3","http3_config":{},"profile":"chrome/156/windows"}`} {
		if id, err := e.CreateTransportWithOptions([]byte(raw)); err == nil {
			e.CloseSession(id)
			t.Fatal("accepted combination", raw)
		}
	}
	if e.Stats()["open_sessions"] != baseline {
		t.Fatal("configuration rejection allocated owner")
	}
	if _, err := e.TransportHTTP3Config(0); err == nil {
		t.Fatal("zero handle accepted")
	}
	if _, err := e.Start([]byte(`{"url":"https://127.0.0.1:1","http3_config":{}}`)); ErrorCode(err) != "invalid_configuration" {
		t.Fatal("request injection accepted", err)
	}
}

func TestHTTP3ConfigAppliedIdentityAndSnapshot(t *testing.T) {
	e := New()
	raw := []byte(`{"schema_version":1,"http_version":"h3","http3_config":{"initial_stream_receive_window":16384,"max_stream_receive_window":65536,"initial_connection_receive_window":32768,"max_connection_receive_window":131072,"max_idle_timeout_ms":12345,"keep_alive_period_ms":0,"max_response_header_bytes":1024}}`)
	id, err := e.CreateTransportWithOptions(raw)
	if err != nil {
		t.Fatal(err)
	}
	defer e.CloseSession(id)
	c, err := e.transportConfiguration(id)
	if err != nil {
		t.Fatal(err)
	}
	var opts fastls.Options
	_, identity, err := applyTransport(&Input{URL: "https://example.com"}, &opts, c, e)
	if err != nil {
		t.Fatal(err)
	}
	v, err := opts.EffectiveHTTP3Config()
	if err != nil {
		t.Fatal(err)
	}
	if v.InitialStreamReceiveWindow != 16384 || v.MaxStreamReceiveWindow != 65536 || v.InitialConnectionReceiveWindow != 32768 || v.MaxConnectionReceiveWindow != 131072 || v.MaxIdleTimeout != 12345*time.Millisecond || v.KeepAlivePeriod != 0 || v.MaxResponseHeaderBytes != 1024 {
		t.Fatal(v)
	}
	snapshot, err := e.TransportHTTP3Config(id)
	if err != nil {
		t.Fatal(err)
	}
	values := snapshot.(map[string]any)
	values["max_response_header_bytes"] = 8192
	again, _ := e.TransportHTTP3Config(id)
	if again.(map[string]any)["max_response_header_bytes"] != int64(1024) {
		t.Fatal("snapshot aliases owner")
	}
	other, otherID, err := parseHTTP3Config([]byte(`{"max_response_header_bytes":2048}`))
	if err != nil {
		t.Fatal(err)
	}
	_, different, _ := applyTransport(&Input{URL: "https://example.com"}, &opts, &transportConfig{version: fastls.HTTPVersion3, http3: other, http3JSON: otherID}, e)
	if identity == different {
		t.Fatal("configuration missing from transport identity")
	}
	odd, _, err := parseHTTP3Config([]byte(`{"max_idle_timeout_ms":101}`))
	if err != nil {
		t.Fatal(err)
	}
	effective, _ := (fastls.Options{HTTPVersion: fastls.HTTPVersion3, HTTP3: odd}).EffectiveHTTP3Config()
	if http3Values(effective)["keep_alive_period_ms"] != 50.5 {
		t.Fatal("normalization lost half-millisecond default")
	}
	data, _ := json.Marshal(HTTP3ConfigCapabilities())
	if len(data) == 0 {
		t.Fatal("no field metadata")
	}
	e.CloseSession(id)
	if _, err := e.TransportHTTP3Config(id); err == nil {
		t.Fatal("closed owner inspection succeeded")
	}
}
