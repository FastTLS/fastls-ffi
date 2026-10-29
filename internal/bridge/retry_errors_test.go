package bridge

import (
	"context"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"net"
	"syscall"
	"testing"

	fastls "github.com/FastTLS/fastls"
)

func TestRetryErrorClassification(t *testing.T) {
	cases := []struct {
		name string
		err  error
		code string
	}{
		{"EOF", io.EOF, "network_error"},
		{"truncated", io.ErrUnexpectedEOF, "network_error"},
		{"socket", &net.OpError{Op: "dial", Net: "tcp", Err: syscall.ECONNREFUSED}, "network_error"},
		{"permission", &net.OpError{Op: "dial", Net: "tcp", Err: syscall.EACCES}, "request_error"},
		{"temporary DNS", &net.DNSError{IsTemporary: true}, "network_error"},
		{"missing DNS", &net.DNSError{IsNotFound: true}, "request_error"},
		{"cancelled", context.Canceled, "cancelled"},
		{"deadline", context.DeadlineExceeded, "timeout"},
		{"address policy", fastls.ErrAddressDenied, "address_denied"},
		{"unknown CA", x509.UnknownAuthorityError{}, "certificate_error"},
		{"hostname", x509.HostnameError{}, "certificate_error"},
		{"certificate", x509.CertificateInvalidError{}, "certificate_error"},
		{"wrapped CA", &net.OpError{Op: "read", Err: x509.UnknownAuthorityError{}}, "certificate_error"},
		{"TLS alert", &net.OpError{Op: "read", Err: errors.New("TLS handshake failure")}, "request_error"},
		{"unknown", errors.New("unexpected failure"), "request_error"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := &request{changed: make(chan struct{})}
			r.setError(fmt.Errorf("wrapped: %w", tc.err))
			if r.info.ErrorCode != tc.code {
				t.Fatalf("code = %q, want %q", r.info.ErrorCode, tc.code)
			}
		})
	}
}
