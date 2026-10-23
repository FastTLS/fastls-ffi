package bridge

import (
	"encoding/json"
	"testing"
)

func TestDecodeTimeoutInputBoundsBeforeRegistering(t *testing.T) {
	e := New()
	for _, input := range []Input{
		{URL: "http://127.0.0.1:1", ConnectTimeoutMS: -1},
		{URL: "http://127.0.0.1:1", HeadersTimeoutMS: 86400001},
		{URL: "http://127.0.0.1:1", ReadTimeoutMS: -1},
		{URL: "http://127.0.0.1:1", MaxDecodedBytes: -1},
		{URL: "http://127.0.0.1:1", MaxDecodedBytes: 1 << 41},
	} {
		raw, _ := json.Marshal(input)
		if _, err := e.Start(raw); err == nil {
			t.Fatalf("accepted invalid limits: %+v", input)
		}
	}
	if e.Stats()["open_requests"] != 0 || e.Stats()["workers"] != int64(0) {
		t.Fatal("invalid input registered a native request")
	}
}
