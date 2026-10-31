package bridge

import (
	"encoding/json"
	"time"

	fastls "github.com/FastTLS/fastls"
)

// Wire units are bytes and whole milliseconds, never native duration nanoseconds.
type http3ConfigInput struct {
	InitialStreamReceiveWindow     uint64 `json:"initial_stream_receive_window"`
	MaxStreamReceiveWindow         uint64 `json:"max_stream_receive_window"`
	InitialConnectionReceiveWindow uint64 `json:"initial_connection_receive_window"`
	MaxConnectionReceiveWindow     uint64 `json:"max_connection_receive_window"`
	MaxIdleTimeoutMS               int64  `json:"max_idle_timeout_ms"`
	KeepAlivePeriodMS              *int64 `json:"keep_alive_period_ms,omitempty"`
	MaxResponseHeaderBytes         int64  `json:"max_response_header_bytes"`
}

func parseHTTP3Config(raw []byte) (*fastls.HTTP3Config, string, error) {
	var in http3ConfigInput
	if err := strictJSON(raw, &in, 16384, 4096); err != nil {
		return nil, "", err
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	for _, value := range fields {
		if string(value) == "null" {
			return nil, "", invalidConfig()
		}
	}
	// Bound before multiplication; do not permit signed overflow or backend clipping.
	if in.MaxIdleTimeoutMS < 0 || in.MaxIdleTimeoutMS > 86400000 {
		return nil, "", invalidConfig()
	}
	c := &fastls.HTTP3Config{InitialStreamReceiveWindow: in.InitialStreamReceiveWindow, MaxStreamReceiveWindow: in.MaxStreamReceiveWindow,
		InitialConnectionReceiveWindow: in.InitialConnectionReceiveWindow, MaxConnectionReceiveWindow: in.MaxConnectionReceiveWindow,
		MaxIdleTimeout: time.Duration(in.MaxIdleTimeoutMS) * time.Millisecond, MaxResponseHeaderBytes: in.MaxResponseHeaderBytes}
	if in.KeepAlivePeriodMS != nil {
		if *in.KeepAlivePeriodMS < 0 || *in.KeepAlivePeriodMS > 86400000 {
			return nil, "", invalidConfig()
		}
		d := time.Duration(*in.KeepAlivePeriodMS) * time.Millisecond
		c.KeepAlivePeriod = &d
	}
	effective, err := (fastls.Options{HTTPVersion: fastls.HTTPVersion3, HTTP3: c}).EffectiveHTTP3Config()
	if err != nil {
		return nil, "", invalidConfig()
	}
	normalized := http3Values(effective)
	identity, _ := json.Marshal(normalized)
	return c, string(identity), nil
}

func http3Values(c fastls.HTTP3EffectiveConfig) map[string]any {
	return map[string]any{
		"initial_stream_receive_window": c.InitialStreamReceiveWindow, "max_stream_receive_window": c.MaxStreamReceiveWindow,
		"initial_connection_receive_window": c.InitialConnectionReceiveWindow, "max_connection_receive_window": c.MaxConnectionReceiveWindow,
		"max_idle_timeout_ms": int64(c.MaxIdleTimeout / time.Millisecond), "keep_alive_period_ms": float64(c.KeepAlivePeriod) / float64(time.Millisecond),
		"max_response_header_bytes": c.MaxResponseHeaderBytes,
	}
}

func HTTP3ConfigCapabilities() any {
	defaults, _ := (fastls.Options{HTTPVersion: fastls.HTTPVersion3}).EffectiveHTTP3Config()
	return map[string]any{"schema_version": 1, "scope": "transport", "http_version": "h3", "defaults": http3Values(defaults), "fields": []map[string]any{
		{"name": "initial_stream_receive_window", "unit": "bytes", "default": 512 << 10, "minimum": 4096, "maximum": 64 << 20, "zero": "default"},
		{"name": "max_stream_receive_window", "unit": "bytes", "default": 6 << 20, "minimum": 4096, "maximum": 64 << 20, "zero": "default"},
		{"name": "initial_connection_receive_window", "unit": "bytes", "default": 512 << 10, "minimum": 4096, "maximum": 64 << 20, "zero": "default"},
		{"name": "max_connection_receive_window", "unit": "bytes", "default": 15 << 20, "minimum": 4096, "maximum": 64 << 20, "zero": "default"},
		{"name": "max_idle_timeout_ms", "unit": "milliseconds", "default": 30000, "minimum": 100, "maximum": 86400000, "zero": "default"},
		{"name": "keep_alive_period_ms", "unit": "milliseconds", "default": 10000, "minimum": 10, "maximum": "effective_idle/2", "zero": "disabled", "omitted": "min(10000,effective_idle/2)"},
		{"name": "max_response_header_bytes", "unit": "bytes", "default": 10 << 20, "minimum": 1024, "maximum": 16 << 20, "zero": "default"},
	}}
}

// TransportHTTP3Config inspects a live owner's immutable local configuration.
// It performs no network I/O and returns nil for owners without fixed H3.
func (e *Engine) TransportHTTP3Config(id uint64) (any, error) {
	if id == 0 {
		return nil, invalidConfig()
	}
	c, err := e.transportConfiguration(id)
	if err != nil {
		return nil, err
	}
	if c.version != fastls.HTTPVersion3 {
		return nil, nil
	}
	effective, err := (fastls.Options{HTTPVersion: c.version, HTTP3: c.http3}).EffectiveHTTP3Config()
	if err != nil {
		return nil, invalidConfig()
	}
	return http3Values(effective), nil
}
