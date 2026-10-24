package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Parse multipart independently, hashing each file without buffering its body.
func requestOptionsRoute(w http.ResponseWriter, r *http.Request) bool {
	switch r.URL.Path {
	case "/charset":
		w.Header().Set("Content-Type", `text/plain; charset="iso-8859-1"`)
		w.Write([]byte{'c', 'a', 'f', 233})
	case "/multipart", "/multipart-slow":
		reader, err := r.MultipartReader()
		if err != nil {
			http.Error(w, "invalid multipart", 400)
			return true
		}
		parts := []map[string]any{}
		for {
			part, err := reader.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				return true
			}
			hash := sha256.New()
			var size int64
			var throttled int64
			buffer := make([]byte, 65536)
			for {
				n, readErr := part.Read(buffer)
				size += int64(n)
				hash.Write(buffer[:n])
				if r.URL.Path == "/multipart-slow" && size-throttled >= 65536 {
					time.Sleep(time.Millisecond)
					throttled = size
				}
				if readErr == io.EOF {
					break
				}
				if readErr != nil {
					return true
				}
			}
			parts = append(parts, map[string]any{"name": part.FormName(), "filename": part.FileName(),
				"type": part.Header.Get("Content-Type"), "bytes": size, "sha256": fmt.Sprintf("%x", hash.Sum(nil))})
		}
		json.NewEncoder(w).Encode(map[string]any{"parts": parts, "protocol": r.Proto, "length": r.ContentLength})
	default:
		return false
	}
	return true
}
