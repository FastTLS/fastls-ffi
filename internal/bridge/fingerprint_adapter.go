package bridge

import (
	"bytes"
	"encoding/json"
	fastls "github.com/FastTLS/fastls"
)

// AdaptProfile validates both the envelope and complete base before converting.
// No session, transport, resolver or network operation is created.
func AdaptProfile(raw []byte) (any, error) {
	var input struct {
		SchemaVersion int             `json:"schema_version"`
		Base          json.RawMessage `json:"base"`
		JA3           string          `json:"ja3"`
		H2            string          `json:"h2"`
	}
	if err := strictJSON(raw, &input, MaxInput, 16384); err != nil {
		return nil, err
	}
	if input.SchemaVersion != 1 {
		return nil, invalidConfig()
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	for _, name := range []string{"ja3", "h2"} {
		if value, ok := fields[name]; ok && bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return nil, invalidConfig()
		}
	}
	base, _, err := parseSpec(input.Base)
	if err != nil {
		return nil, err
	}
	spec, err := fastls.AdaptProfile(*base, fastls.FingerprintParameters{JA3: input.JA3, H2: input.H2})
	if err != nil {
		return nil, invalidConfig()
	}
	return spec, nil
}
