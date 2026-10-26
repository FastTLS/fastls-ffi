"""Application redirect policy shared by the synchronous and asynchronous SDK."""
from dataclasses import replace
import time
from urllib.parse import urljoin, urlsplit, urldefrag

from ._native import FastlsError, FastlsTimeoutError
from .timeout import Timeout
from .upload import is_stream


class Redirects:
    def __init__(self, method, url, options, timeout):
        self.method, self.url, self.options = method.upper(), url, dict(options)
        self.limit = self.options.get("timeout") or timeout
        self.limit = self.limit if isinstance(self.limit, Timeout) else Timeout(total=self.limit)
        self.deadline = time.monotonic() + self.limit.total
        self.maximum = self.options.pop("max_redirects", 30)
        if isinstance(self.maximum, bool) or not isinstance(self.maximum, int) or self.maximum < 0:
            raise ValueError("max_redirects must be a non-negative integer")
        self.history = []

    def request(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FastlsTimeoutError("total deadline exceeded", "total")
        self.options["timeout"] = replace(self.limit, total=remaining)
        return self.method, self.url, self.options

    def follow(self, response):
        response.history = list(self.history)
        status = response.status_code
        location = response.headers.get("location")
        if (self.options.get("duplex") or not self.options.get("allow_redirects", True)
                or status not in (301, 302, 303, 307, 308) or not location):
            return False
        if status in (307, 308) and is_stream(self.options.get("data")):
            return False  # One-shot uploads cannot be replayed.
        if len(self.history) >= self.maximum:
            raise FastlsError("maximum redirects exceeded")
        target = urldefrag(urljoin(self.url, location))[0]
        old, new = urlsplit(self.url), urlsplit(target)
        if new.scheme not in ("http", "https") or not new.hostname or new.username is not None or new.password is not None:
            raise FastlsError("invalid redirect URL")
        headers = dict(self.options.get("headers") or {})
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            for name in ("authorization", "cookie", "host"):
                headers.pop(name, None)
        if (status == 303 and self.method != "HEAD") or (status == 302 and self.method != "HEAD") or (status == 301 and self.method == "POST"):
            self.method = "GET"
            self.options.pop("data", None)
            self.options.pop("trailers", None)
            for name in ("content-type", "content-length", "transfer-encoding"):
                headers.pop(name, None)
        self.options["headers"] = headers
        self.history.append(response)
        self.url = target
        return True
