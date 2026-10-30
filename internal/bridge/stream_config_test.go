package bridge

import (
	"encoding/json"
	"testing"
)

func TestH2StreamSpecStrictContract(t *testing.T) {
	e := New()
	var value map[string]any
	if err := json.Unmarshal(completeSpec(t, e), &value); err != nil {
		t.Fatal(err)
	}
	h := value["http2"].(map[string]any)
	h["first_stream_id"] = 3
	h["stream_window_increment"] = 12451840
	features := value["capabilities"].(map[string]any)
	features["required_features"] = []string{"http2.stream.v1"}
	raw, _ := json.Marshal(value)
	s, snapshot, err := parseSpec(raw)
	if err != nil || s.HTTP2.FirstStreamID != 3 || s.HTTP2.StreamWindowIncrement != 12451840 {
		t.Fatalf("parse: %v", err)
	}
	roundtrip, _, err := parseSpec([]byte(snapshot))
	if err != nil || roundtrip.HTTP2.FirstStreamID != 3 || roundtrip.HTTP2.StreamWindowIncrement != 12451840 {
		t.Fatalf("roundtrip: %v", err)
	}
	for _, key := range []string{"first_stream_id", "stream_window_increment"} {
		before := h[key]
		for _, invalid := range []any{nil, -1, 1.5, true, uint64(1) << 32} {
			h[key] = invalid
			raw, _ := json.Marshal(value)
			if _, _, err := parseSpec(raw); err == nil {
				t.Fatalf("accepted %s=%v", key, invalid)
			}
		}
		h[key] = before
	}
	for _, id := range []uint32{2, 0x80000001} {
		h["first_stream_id"] = id
		raw, _ := json.Marshal(value)
		if _, _, err := parseSpec(raw); err == nil {
			t.Fatal("invalid stream accepted", id)
		}
	}
	h["first_stream_id"] = 3
	features["required_features"] = []string{}
	raw, _ = json.Marshal(value)
	if _, _, err := parseSpec(raw); err == nil {
		t.Fatal("missing required feature accepted")
	}
	if e.Stats()["open_sessions"] != 0 {
		t.Fatal("validation allocated an owner")
	}
}
