package main

import (
	"crypto/tls"
	"github.com/quic-go/quic-go/http3"
	"net"
	"net/http"
)

// This peer uses the same QUIC backend as the client. Independent-stack H3
// evidence is recorded separately; this fixture tests SDK lifecycle contracts.
func h3Fixture(handler http.Handler, cert tls.Certificate) (string, func(), error) {
	socket, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		return "", nil, err
	}
	server := &http3.Server{Handler: handler, TLSConfig: &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS13}}
	go func() { _ = server.Serve(socket) }()
	return "https://" + socket.LocalAddr().String(), func() { _ = server.Close(); _ = socket.Close() }, nil
}
