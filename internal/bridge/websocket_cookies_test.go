package bridge

import (
	"net/url"
	"testing"

	fastls "github.com/FastTLS/fastls"
	"net/http"
)

func TestWebSocketCookieScopeOverrideAndDisabledStore(t *testing.T) {
	secure, _ := url.Parse("https://example.com/private/ws")
	plain, _ := url.Parse("http://example.com/ws")
	store := fastls.NewCookieStore()
	store.SetCookies(secure, []*http.Cookie{
		{Name: "dup", Value: "root", Path: "/"},
		{Name: "dup", Value: "private", Path: "/private"},
		{Name: "secret", Value: "tls", Secure: true, Path: "/"},
		{Name: "foreign", Value: "no", Domain: "other.invalid", Path: "/"},
	})
	for _, test := range []struct {
		u    *url.URL
		want string
	}{
		{secure, "dup=private; dup=root; secret=tls"}, {plain, "dup=root"},
	} {
		opts := fastls.Options{CookieJar: store}
		addWebSocketCookies(&opts, test.u)
		if opts.Headers["Cookie"] != test.want {
			t.Fatal(opts.Headers)
		}
	}
	for _, value := range []string{"", "manual=only"} {
		opts := fastls.Options{CookieJar: store, Headers: map[string]string{"cOoKiE": value}}
		addWebSocketCookies(&opts, secure)
		if len(opts.Headers) != 1 || opts.Headers["cOoKiE"] != value {
			t.Fatal(opts.Headers)
		}
	}
	store.SetEnabled(false)
	store.SetCookies(secure, []*http.Cookie{{Name: "late", Value: "ignored", Path: "/"}})
	opts := fastls.Options{CookieJar: store}
	addWebSocketCookies(&opts, secure)
	if len(opts.Headers) != 0 {
		t.Fatal("disabled store retained cookies")
	}
}
