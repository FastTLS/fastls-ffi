"""Deterministic TLS control-record and partial-write relay regressions."""
import ssl
import unittest
from unittest.mock import patch

from proxy_lab import ProxyLab


class ProxyRelayTests(unittest.TestCase):
    def test_tls_readability_without_application_data_keeps_both_directions_live(self):
        class Endpoint:
            def __init__(self, reads, writes):
                self.reads, self.writes = list(reads), list(writes)
                self.blocking, self.received = [], bytearray()
            def setblocking(self, value): self.blocking.append(value)
            def pending(self): return 0
            def recv(self, size):
                result = self.reads.pop(0) if self.reads else BlockingIOError()
                if isinstance(result, Exception): raise result
                return result
            def send(self, data):
                result = self.writes.pop(0) if self.writes else 2
                if isinstance(result, Exception): raise result
                result = min(result, len(data))
                self.received.extend(data[:result])
                return result
        client = Endpoint([ssl.SSLWantReadError(), b'upload', b''], [ssl.SSLWantWriteError(), 2])
        upstream = Endpoint([b'download'], [ssl.SSLWantReadError(), 1])
        calls = []
        def ready(readers, writers, errors, timeout):
            calls.append((readers, writers))
            if len(calls) > 30: raise AssertionError('relay did not drain after EOF')
            return list(readers), list(writers), []
        with patch('proxy_lab.select.select', side_effect=ready):
            ProxyLab.relay_tls(client, upstream)
        self.assertEqual(client.blocking, [False])
        self.assertEqual(upstream.blocking, [False])
        self.assertEqual(client.received, b'download')
        self.assertEqual(upstream.received, b'upload')

    def test_zero_length_write_is_a_relay_failure(self):
        class Endpoint:
            def setblocking(self, value): pass
            def pending(self): return 0
            def recv(self, size): return b'payload'
            def send(self, data): return 0
        client, upstream = Endpoint(), Endpoint()
        with patch('proxy_lab.select.select', side_effect=lambda r, w, e, t: (list(r), list(w), [])):
            with self.assertRaises(EOFError): ProxyLab.relay_tls(client, upstream)

    def test_ssl_write_retry_preserves_bytes_while_more_data_arrives(self):
        class Endpoint:
            def __init__(self, reads): self.reads, self.writes = list(reads), []
            def setblocking(self, value): pass
            def pending(self): return 0
            def recv(self, size):
                value = self.reads.pop(0) if self.reads else ssl.SSLWantReadError()
                if isinstance(value, Exception): raise value
                return value
            def send(self, data):
                self.writes.append(data)
                if len(self.writes) == 1: raise ssl.SSLWantWriteError()
                return len(data)
        client, upstream = Endpoint([]), Endpoint([b'a', b'b', b'c', b''])
        with patch('proxy_lab.select.select', side_effect=lambda r, w, e, t: (list(r), list(w), [])):
            ProxyLab.relay_tls(client, upstream)
        self.assertEqual(client.writes, [b'ab', b'ab', b'c'])
        self.assertIs(client.writes[0], client.writes[1])


if __name__ == '__main__': unittest.main()
