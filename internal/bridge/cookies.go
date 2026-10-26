package bridge

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"time"

	"net/http"
)

const MaxCookieInput = 16384

type cookieInput struct {
	Name     string `json:"name"`
	Value    string `json:"value"`
	Domain   string `json:"domain"`
	Path     string `json:"path"`
	Secure   bool   `json:"secure"`
	HTTPOnly bool   `json:"http_only"`
	MaxAge   *int   `json:"max_age"`
	Expires  string `json:"expires"`
}

type cookieCommand struct {
	Action  string       `json:"action"`
	URL     string       `json:"url"`
	Cookie  *cookieInput `json:"cookie"`
	Enabled *bool        `json:"enabled"`
}

func (e *Engine) SessionCookies(id uint64, raw []byte) (any, error) {
	if len(raw) == 0 || len(raw) > MaxCookieInput {
		return nil, errors.New("cookie command size must be 1..16384 bytes")
	}
	var command cookieCommand
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&command); err != nil {
		return nil, err
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return nil, errors.New("expected exactly one cookie command")
	}
	e.mu.Lock()
	s := e.sessions[id]
	e.mu.Unlock()
	if s == nil {
		return nil, errors.New("invalid or closed session handle")
	}
	if s.cookies == nil {
		return nil, errors.New("transport context has no cookie store; manage cookies in the SDK")
	}
	switch command.Action {
	case "clear":
		s.cookies.Clear()
		return nil, nil
	case "configure":
		if command.Enabled == nil {
			return nil, errors.New("enabled is required")
		}
		s.cookies.SetEnabled(*command.Enabled)
		return nil, nil
	case "get", "set":
	default:
		return nil, errors.New("unknown cookie action")
	}
	u, err := url.Parse(command.URL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return nil, errors.New("cookie URL must be absolute HTTP(S) without credentials")
	}
	if command.Action == "get" {
		out := make([]map[string]string, 0)
		for _, c := range s.cookies.Cookies(u) {
			out = append(out, map[string]string{"name": c.Name, "value": c.Value})
		}
		return out, nil
	}
	if command.Cookie == nil {
		return nil, errors.New("cookie is required")
	}
	in := command.Cookie
	c := &http.Cookie{Name: in.Name, Value: in.Value, Domain: in.Domain, Path: in.Path, Secure: in.Secure, HttpOnly: in.HTTPOnly}
	if in.MaxAge != nil {
		c.MaxAge = *in.MaxAge
		if c.MaxAge <= 0 {
			c.MaxAge = -1
		}
	}
	if in.Expires != "" {
		c.Expires, err = time.Parse(time.RFC3339, in.Expires)
		if err != nil {
			return nil, errors.New("expires must use RFC3339 format")
		}
	}
	return nil, s.cookies.SetCookie(u, c)
}
