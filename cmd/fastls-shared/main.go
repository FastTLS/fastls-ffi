// Build with CGO_ENABLED=1 go build -buildmode=c-shared ./cmd/fastls-shared.
package main

/*
#include <stdlib.h>
#include <stdint.h>
*/
import "C"

import (
	"encoding/json"
	"fmt"
	"unsafe"

	"github.com/FastTLS/fastls-ffi/internal/bridge"
)

var engine = bridge.New()

//export fastls_features
func fastls_features() *C.char {
	return result(func() (any, error) {
		return []string{"stream_decode", "phase_timeouts", "session_cookies", "multivalue_headers"}, nil
	})
}

//export fastls_session_cookies
func fastls_session_cookies(handle C.uint64_t, data *C.char, length C.size_t) *C.char {
	return result(func() (any, error) {
		if data == nil || length < 1 || length > bridge.MaxCookieInput {
			return nil, fmt.Errorf("invalid cookie input buffer")
		}
		return engine.SessionCookies(uint64(handle), C.GoBytes(unsafe.Pointer(data), C.int(length)))
	})
}

//export fastls_session_create
func fastls_session_create() *C.char {
	return result(func() (any, error) { return engine.CreateSession() })
}

//export fastls_session_close
func fastls_session_close(handle C.uint64_t) *C.char {
	return result(func() (any, error) { engine.CloseSession(uint64(handle)); return nil, nil })
}

//export fastls_session_close_idle
func fastls_session_close_idle(handle C.uint64_t) *C.char {
	return result(func() (any, error) { return nil, engine.CloseSessionIdle(uint64(handle)) })
}

func result(fn func() (any, error)) (out *C.char) {
	defer func() {
		if p := recover(); p != nil {
			b, _ := json.Marshal(map[string]any{"ok": false, "error": fmt.Sprintf("internal panic: %v", p)})
			out = C.CString(string(b))
		}
	}()
	value, err := fn()
	envelope := map[string]any{"ok": true, "result": value}
	if err != nil {
		envelope = map[string]any{"ok": false, "error": err.Error()}
	}
	b, err := json.Marshal(envelope)
	if err != nil {
		return C.CString(`{"ok":false,"error":"cannot encode result"}`)
	}
	return C.CString(string(b))
}

//export fastls_abi_version
func fastls_abi_version() C.uint32_t { return 1 }

//export fastls_profiles
func fastls_profiles() *C.char {
	return result(func() (any, error) { return engine.Profiles() })
}

//export fastls_stats
func fastls_stats() *C.char {
	return result(func() (any, error) { return engine.Stats(), nil })
}

//export fastls_start
func fastls_start(data *C.char, length C.size_t) *C.char {
	return result(func() (any, error) {
		if data == nil || length < 1 || length > bridge.MaxInput {
			return nil, fmt.Errorf("invalid input buffer")
		}
		return engine.Start(C.GoBytes(unsafe.Pointer(data), C.int(length)))
	})
}

//export fastls_info
func fastls_info(handle C.uint64_t) *C.char {
	return result(func() (any, error) { return engine.Info(uint64(handle)) })
}

//export fastls_read
func fastls_read(handle C.uint64_t, buffer unsafe.Pointer, capacity C.size_t, length *C.size_t) (status C.int) {
	status = bridge.Failed
	defer func() {
		if recover() != nil {
			status = bridge.Failed
		}
	}()
	if length == nil {
		return bridge.Invalid
	}
	*length = 0
	if buffer == nil || capacity < 1 || capacity > bridge.MaxInput {
		return bridge.Invalid
	}
	n, code := engine.Read(uint64(handle), unsafe.Slice((*byte)(buffer), int(capacity)))
	*length = C.size_t(n)
	return C.int(code)
}

//export fastls_cancel
func fastls_cancel(handle C.uint64_t) *C.char {
	return result(func() (any, error) { return nil, engine.Cancel(uint64(handle)) })
}

//export fastls_close
func fastls_close(handle C.uint64_t) *C.char {
	return result(func() (any, error) { engine.Close(uint64(handle)); return nil, nil })
}

//export fastls_free
func fastls_free(ptr unsafe.Pointer) { C.free(ptr) }

func main() {}
