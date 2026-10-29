package bridge

import (
	"bytes"
	"encoding/json"
	"errors"
	fastls "github.com/FastTLS/fastls"
	use "github.com/FastTLS/fastls/imitate"
	"github.com/FastTLS/fastls/profile"
	"io"
	"reflect"
	"strings"
	"unicode/utf8"
)

const MaxSpecInput = 256 * 1024

type ConfigurationError struct{ Code string }

func (e *ConfigurationError) Error() string { return e.Code }
func invalidConfig() error                  { return &ConfigurationError{Code: "invalid_configuration"} }
func unsupported() error                    { return &ConfigurationError{Code: "unsupported_capability"} }
func ErrorCode(err error) string {
	var c *ConfigurationError
	if errors.As(err, &c) {
		return c.Code
	}
	if errors.Is(err, fastls.ErrAddressDenied) {
		return "address_denied"
	}
	return "request_error"
}

// Reject duplicates before struct decoding can discard them; bound every level.
func strictJSON(raw []byte, out any, limit, stringLimit int) error {
	if len(raw) == 0 || len(raw) > limit || !utf8.Valid(raw) {
		return invalidConfig()
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var walk func(int) error
	walk = func(depth int) error {
		if depth > 32 {
			return invalidConfig()
		}
		t, err := d.Token()
		if err != nil {
			return invalidConfig()
		}
		switch v := t.(type) {
		case string:
			if len(v) > stringLimit {
				return invalidConfig()
			}
		case json.Delim:
			if v != '{' && v != '[' {
				return invalidConfig()
			}
			count := 0
			seen := map[string]bool{}
			for d.More() {
				count++
				if count > 4096 {
					return invalidConfig()
				}
				if v == '{' {
					key, err := d.Token()
					if err != nil {
						return invalidConfig()
					}
					s, ok := key.(string)
					if !ok || seen[s] || len(s) > stringLimit {
						return invalidConfig()
					}
					seen[s] = true
				}
				if err := walk(depth + 1); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || v == '{' && end != json.Delim('}') || v == '[' && end != json.Delim(']') {
				return invalidConfig()
			}
		}
		return nil
	}
	if err := walk(0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return invalidConfig()
	}
	if bytes.TrimSpace(raw)[0] != '{' {
		return invalidConfig()
	}
	if err := exactFields(raw, reflect.TypeOf(out).Elem()); err != nil {
		return err
	}
	d = json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil {
		return invalidConfig()
	}
	return nil
}

// Go's decoder matches struct fields case-insensitively. Require exact schema
// names so differently cased aliases cannot overwrite a previously seen field.
func exactFields(raw json.RawMessage, t reflect.Type) error {
	if t == reflect.TypeOf(json.RawMessage{}) {
		return nil
	}
	if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		if t.Kind() == reflect.Pointer || t.Kind() == reflect.Slice || t.Kind() == reflect.Map {
			return nil
		}
		return invalidConfig()
	}
	if t.Kind() == reflect.Pointer {
		return exactFields(raw, t.Elem())
	}
	switch t.Kind() {
	case reflect.Struct:
		var values map[string]json.RawMessage
		if json.Unmarshal(raw, &values) != nil {
			return invalidConfig()
		}
		fields := map[string]reflect.Type{}
		for i := 0; i < t.NumField(); i++ {
			f := t.Field(i)
			tag := strings.Split(f.Tag.Get("json"), ",")[0]
			if tag != "" && tag != "-" {
				fields[tag] = f.Type
			}
		}
		for key, value := range values {
			field, ok := fields[key]
			if !ok {
				return invalidConfig()
			}
			if err := exactFields(value, field); err != nil {
				return err
			}
		}
	case reflect.Slice:
		if t.Elem().Kind() == reflect.Uint8 {
			return nil
		}
		var values []json.RawMessage
		if json.Unmarshal(raw, &values) != nil {
			return invalidConfig()
		}
		for _, value := range values {
			if err := exactFields(value, t.Elem()); err != nil {
				return err
			}
		}
	case reflect.Map:
		var values map[string]json.RawMessage
		if json.Unmarshal(raw, &values) != nil {
			return invalidConfig()
		}
		for _, value := range values {
			if err := exactFields(value, t.Elem()); err != nil {
				return err
			}
		}
	}
	return nil
}

type policyInput struct {
	AllowSchemes []string `json:"allow_schemes"`
	AllowHosts   []string `json:"allow_hosts"`
	AllowPorts   []uint16 `json:"allow_ports"`
	AllowCIDRs   []string `json:"allow_cidrs"`
	DenyCIDRs    []string `json:"deny_cidrs"`
}
type transportInput struct {
	SchemaVersion int             `json:"schema_version"`
	AddressPolicy json.RawMessage `json:"address_policy"`
	ProfileSpec   json.RawMessage `json:"profile_spec"`
	Profile       string          `json:"profile"`
	HTTPVersion   string          `json:"http_version"`
}
type transportConfig struct {
	policy             *fastls.AddressPolicy
	spec               *profile.Spec
	specJSON, selector string
	version            fastls.HTTPVersion
}

func parseSpec(raw []byte) (*profile.Spec, string, error) {
	var s profile.Spec
	if err := strictJSON(raw, &s, MaxSpecInput, 16384); err != nil {
		return nil, "", err
	}
	var shape struct {
		HTTP2 map[string]json.RawMessage `json:"http2"`
	}
	if json.Unmarshal(raw, &shape) != nil || shape.HTTP2 == nil {
		return nil, "", invalidConfig()
	}
	settings, ok := shape.HTTP2["settings"]
	if !ok || bytes.Equal(bytes.TrimSpace(settings), []byte("null")) {
		return nil, "", invalidConfig()
	}
	if fastls.ValidateProfile(s) != nil {
		return nil, "", invalidConfig()
	}
	var compact bytes.Buffer
	_ = json.Compact(&compact, raw)
	return &s, compact.String(), nil
}
func (e *Engine) CreateTransportWithOptions(raw []byte) (uint64, error) {
	var in transportInput
	if err := strictJSON(raw, &in, MaxInput, MaxInput); err != nil {
		return 0, err
	}
	if in.SchemaVersion != 1 {
		return 0, invalidConfig()
	}
	c := &transportConfig{selector: in.Profile}
	if in.HTTPVersion != "" && in.HTTPVersion != "auto" && in.HTTPVersion != "h3" {
		return 0, invalidConfig()
	}
	if in.HTTPVersion == "h3" {
		c.version = fastls.HTTPVersion3
	}
	if in.AddressPolicy != nil {
		var p policyInput
		if err := strictJSON(in.AddressPolicy, &p, MaxInput, 16384); err != nil {
			return 0, err
		}
		var fields map[string]json.RawMessage
		_ = json.Unmarshal(in.AddressPolicy, &fields)
		for _, value := range fields {
			if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
				return 0, invalidConfig()
			}
		}
		var err error
		c.policy, err = fastls.NewAddressPolicy(fastls.AddressPolicyConfig{AllowSchemes: p.AllowSchemes, AllowHosts: p.AllowHosts, AllowPorts: p.AllowPorts, AllowCIDRs: p.AllowCIDRs, DenyCIDRs: p.DenyCIDRs})
		if err != nil {
			return 0, invalidConfig()
		}
	}
	if in.ProfileSpec != nil {
		var err error
		c.spec, c.specJSON, err = parseSpec(in.ProfileSpec)
		if err != nil {
			return 0, err
		}
	}
	if c.spec != nil && c.selector != "" {
		return 0, invalidConfig()
	}
	if c.version == fastls.HTTPVersion3 && (c.policy != nil || c.spec != nil || c.selector != "") {
		return 0, unsupported()
	}
	opts := fastls.Options{VerifyTLS: true, AddressPolicy: c.policy, HTTPVersion: c.version}
	if c.spec != nil {
		if _, err := use.Apply(&opts, *c.spec); err != nil {
			return 0, invalidConfig()
		}
	}
	if c.selector != "" {
		if _, err := use.Use(&opts, c.selector, use.WithProvider(e.provider)); err != nil {
			return 0, invalidConfig()
		}
	}
	probe, err := fastls.NewSession(opts)
	if err != nil {
		return 0, invalidConfig()
	}
	probe.Close()
	return e.createConfiguredSession(false, c)
}
func (e *Engine) transportConfiguration(id uint64) (*transportConfig, error) {
	if id == 0 {
		return &transportConfig{}, nil
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	s := e.sessions[id]
	if s == nil {
		return nil, errors.New("invalid or closed session handle")
	}
	if s.config == nil {
		return &transportConfig{}, nil
	}
	return s.config, nil
}
func applyTransport(in *Input, opts *fastls.Options, c *transportConfig, e *Engine) (profile.Match, string, error) {
	var match profile.Match
	spec, snapshot := c.spec, c.specJSON
	if in.ProfileSpec != nil {
		var err error
		spec, snapshot, err = parseSpec(in.ProfileSpec)
		if err != nil {
			return match, "", err
		}
	}
	selector := in.Profile
	if selector == "" && spec == nil {
		selector = c.selector
	}
	if spec != nil && selector != "" {
		return match, "", invalidConfig()
	}
	version := c.version
	if in.HTTPVersion != "" {
		if in.HTTPVersion != "auto" && in.HTTPVersion != "h3" {
			return match, "", invalidConfig()
		}
		if c.version == fastls.HTTPVersion3 && in.HTTPVersion != "h3" {
			return match, "", unsupported()
		}
		if in.HTTPVersion == "h3" {
			version = fastls.HTTPVersion3
		}
	}
	if version == fastls.HTTPVersion3 && (c.policy != nil || spec != nil || selector != "" || in.Proxy != "" || in.Duplex || len(in.Trailers) > 0) {
		return match, "", unsupported()
	}
	if version == fastls.HTTPVersion3 && !strings.HasPrefix(in.URL, "https://") {
		return match, "", invalidConfig()
	}
	opts.AddressPolicy, opts.HTTPVersion = c.policy, version
	if c.policy != nil && in.Proxy != "" {
		return match, "", unsupported()
	}
	var err error
	switch {
	case version == fastls.HTTPVersion3:
	case spec != nil:
		match, err = use.Apply(opts, *spec)
	default:
		if selector == "" {
			selector = "chrome/156/windows"
		}
		match, err = use.Use(opts, selector, use.WithProvider(e.provider))
	}
	if err != nil {
		return match, "", invalidConfig()
	}
	identity, _ := json.Marshal([]string{selector, snapshot, string(version), in.CAPEM, in.Proxy, in.ProxyCAPEM})
	return match, string(identity), nil
}
