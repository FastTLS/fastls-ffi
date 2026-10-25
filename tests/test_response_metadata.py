import unittest

from fastls.client import Response


class _Native:
    def __init__(self):
        self.reads = [(0, b"body"), (2, b"")]

    def read(self, _handle, _buffer):
        return self.reads.pop(0)


class _Owner:
    def __init__(self):
        self._native = _Native()

    def _info(self, _handle):
        return {
            "state": "eof",
            "status": 200,
            "protocol": "HTTP/2.0",
            "final_url": "https://example.test/final",
            "trailer_values": {"X-Checksum": ["one", "two"]},
        }


class ResponseMetadataTests(unittest.TestCase):
    def test_metadata_and_trailers_refresh_at_eof(self):
        response = Response(_Owner(), 1, {
            "status": 200,
            "protocol": "HTTP/1.1",
            "final_url": "https://example.test/start",
            "match": {},
        })
        chunks = response.iter_bytes()
        self.assertEqual(next(chunks), b"body")
        with self.assertRaises(StopIteration):
            next(chunks)
        self.assertEqual(response.http_version, "HTTP/2.0")
        self.assertEqual(response.url, "https://example.test/final")
        self.assertEqual(response.trailers.get_list("x-checksum"), ["one", "two"])


if __name__ == "__main__":
    unittest.main()
