package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
	"github.com/quic-go/quic-go/qlog"
	"github.com/quic-go/quic-go/qlogwriter"
	"net"
	"net/http"
	"sync"
	"time"
)

type h3PeerRecord struct {
	mu     sync.Mutex
	values map[string]any
}

func (p *h3PeerRecord) AddProducer() qlogwriter.Recorder { return p }
func (*h3PeerRecord) SupportsSchemas(string) bool        { return true }
func (*h3PeerRecord) Close() error                       { return nil }
func (p *h3PeerRecord) RecordEvent(event qlogwriter.Event) {
	if e, ok := event.(qlog.ParametersSet); ok && e.Initiator == qlog.InitiatorRemote {
		p.mu.Lock()
		p.values = map[string]any{"initial_stream_receive_window": int64(e.InitialMaxStreamDataBidiLocal), "initial_connection_receive_window": int64(e.InitialMaxData), "max_idle_timeout_ms": int64(e.MaxIdleTimeout / time.Millisecond)}
		p.mu.Unlock()
	}
}

type h3PeerKey struct{}

// This same-backend peer observes received QUIC parameters independently of the
// client JSON/snapshot; fixed-core flow-control proofs remain separate evidence.
func h3Fixture(handler http.Handler, cert tls.Certificate) (string, func(), error) {
	socket, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		return "", nil, err
	}
	var mu sync.Mutex
	records := map[any]*h3PeerRecord{}
	server := &http3.Server{TLSConfig: &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS13},
		QUICConfig: &quic.Config{Tracer: func(ctx context.Context, _ bool, _ quic.ConnectionID) qlogwriter.Trace {
			record := &h3PeerRecord{}
			mu.Lock()
			records[ctx.Value(quic.ConnectionTracingKey)] = record
			mu.Unlock()
			return record
		}},
		ConnContext: func(ctx context.Context, conn *quic.Conn) context.Context {
			key := conn.Context().Value(quic.ConnectionTracingKey)
			mu.Lock()
			record := records[key]
			mu.Unlock()
			context.AfterFunc(conn.Context(), func() { mu.Lock(); delete(records, key); mu.Unlock() })
			return context.WithValue(ctx, h3PeerKey{}, record)
		},
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/h3-config-state" {
				record, _ := r.Context().Value(h3PeerKey{}).(*h3PeerRecord)
				if record == nil {
					http.Error(w, "missing QUIC record", 503)
					return
				}
				record.mu.Lock()
				defer record.mu.Unlock()
				_ = json.NewEncoder(w).Encode(record.values)
				return
			}
			handler.ServeHTTP(w, r)
		}),
	}
	go func() { _ = server.Serve(socket) }()
	return "https://" + socket.LocalAddr().String(), func() { _ = server.Close(); _ = socket.Close() }, nil
}
