package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/hpack"
	"io"
	"net"
	"strconv"
	"sync"
	"time"
)

// Read actual decrypted HTTP/2 frames without using the client's transport.
func wireFixture(cert tls.Certificate) (string, func(), error) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return "", nil, err
	}
	var mu sync.Mutex
	connections := map[net.Conn]bool{}
	var workers sync.WaitGroup
	acceptDone := make(chan struct{})
	go func() {
		defer close(acceptDone)
		for {
			raw, err := listener.Accept()
			if err != nil {
				return
			}
			mu.Lock()
			connections[raw] = true
			workers.Add(1)
			mu.Unlock()
			go func() {
				defer workers.Done()
				defer raw.Close()
				defer func() { mu.Lock(); delete(connections, raw); mu.Unlock() }()
				_ = raw.SetDeadline(time.Now().Add(5 * time.Second))
				var ciphers []uint16
				var alpn []string
				var groups []tls.CurveID
				var signatures []tls.SignatureScheme
				config := &tls.Config{Certificates: []tls.Certificate{cert}, NextProtos: []string{"h2"}, MinVersion: tls.VersionTLS12}
				config.GetConfigForClient = func(hello *tls.ClientHelloInfo) (*tls.Config, error) {
					ciphers = append([]uint16{}, hello.CipherSuites...)
					alpn = append([]string{}, hello.SupportedProtos...)
					groups = append([]tls.CurveID{}, hello.SupportedCurves...)
					signatures = append([]tls.SignatureScheme{}, hello.SignatureSchemes...)
					return nil, nil
				}
				connection := tls.Server(raw, config)
				if connection.Handshake() != nil {
					return
				}
				preface := make([]byte, len(http2.ClientPreface))
				if _, err := io.ReadFull(connection, preface); err != nil || string(preface) != http2.ClientPreface {
					return
				}
				frames := http2.NewFramer(connection, connection)
				if frames.WriteSettings() != nil {
					return
				}
				settings := []map[string]uint32{}
				flow := uint32(0)
				priorities := []map[string]any{}
				names := []string{}
				var wantStreamWindow uint64
				decoder := hpack.NewDecoder(4096, func(field hpack.HeaderField) {
					names = append(names, field.Name)
					if field.Name == "x-fixture-stream-window" {
						wantStreamWindow, _ = strconv.ParseUint(field.Value, 10, 31)
					}
				})
				for {
					frame, err := frames.ReadFrame()
					if err != nil {
						return
					}
					switch f := frame.(type) {
					case *http2.WindowUpdateFrame:
						if f.StreamID == 0 {
							flow += f.Increment
						}
					case *http2.PriorityFrame:
						priorities = append(priorities, map[string]any{"stream_id": f.StreamID, "dependency": f.StreamDep, "exclusive": f.Exclusive, "weight": uint16(f.Weight) + 1})
					case *http2.SettingsFrame:
						if !f.IsAck() {
							_ = f.ForeachSetting(func(s http2.Setting) error {
								settings = append(settings, map[string]uint32{"id": uint32(s.ID), "value": s.Val})
								return nil
							})
							if frames.WriteSettingsAck() != nil {
								return
							}
						}
					case *http2.HeadersFrame:
						stream := f.StreamID
						if _, err := decoder.Write(f.HeaderBlockFragment()); err != nil {
							return
						}
						if !f.HeadersEnded() {
							for {
								next, err := frames.ReadFrame()
								if err != nil {
									return
								}
								continuation, ok := next.(*http2.ContinuationFrame)
								if !ok || continuation.StreamID != stream {
									return
								}
								if _, err := decoder.Write(continuation.HeaderBlockFragment()); err != nil {
									return
								}
								if continuation.HeadersEnded() {
									break
								}
							}
						}
						if decoder.Close() != nil {
							return
						}
						var streamWindow uint32
						if wantStreamWindow != 0 {
							for streamWindow == 0 {
								next, err := frames.ReadFrame()
								if err != nil {
									return
								}
								if update, ok := next.(*http2.WindowUpdateFrame); ok && update.StreamID == stream {
									streamWindow = update.Increment
								}
							}
							if uint64(streamWindow) != wantStreamWindow {
								return
							}
						}
						body, _ := json.Marshal(map[string]any{"ciphers": ciphers, "alpn": alpn, "groups": groups, "signatures": signatures, "settings": settings, "headers": names, "connection_flow": flow, "priorities": priorities, "stream_id": stream, "stream_window_increment": streamWindow})
						var block bytes.Buffer
						encoder := hpack.NewEncoder(&block)
						_ = encoder.WriteField(hpack.HeaderField{Name: ":status", Value: "200"})
						_ = encoder.WriteField(hpack.HeaderField{Name: "content-type", Value: "application/json"})
						if frames.WriteHeaders(http2.HeadersFrameParam{StreamID: stream, BlockFragment: block.Bytes(), EndHeaders: true}) != nil {
							return
						}
						if frames.WriteData(stream, true, body) != nil {
							return
						}
						names = []string{}
					}
				}
			}()
		}
	}()
	closeAll := func() {
		_ = listener.Close()
		<-acceptDone
		mu.Lock()
		for c := range connections {
			_ = c.Close()
		}
		mu.Unlock()
		workers.Wait()
	}
	return "https://" + listener.Addr().String(), closeAll, nil
}
