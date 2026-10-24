package main

import (
	"crypto/ecdsa"
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
)

// Independent roots ensure that trusting a target cannot authenticate a proxy.
func proxyCertificateFiles(dir string, names ...string) (map[string]string, error) {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	cert, ca, _, err := certificates(dir, names...)
	if err != nil {
		return nil, err
	}
	chain := []byte{}
	for _, der := range cert.Certificate {
		chain = append(chain, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})...)
	}
	key, err := x509.MarshalECPrivateKey(cert.PrivateKey.(*ecdsa.PrivateKey))
	if err != nil {
		return nil, err
	}
	certfile, keyfile := filepath.Join(dir, "proxy-cert.pem"), filepath.Join(dir, "proxy-key.pem")
	if err = os.WriteFile(certfile, chain, 0600); err != nil {
		return nil, err
	}
	if err = os.WriteFile(keyfile, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: key}), 0600); err != nil {
		return nil, err
	}
	return map[string]string{"cert": certfile, "key": keyfile, "ca": ca}, nil
}
