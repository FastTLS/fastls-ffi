"""Manual synchronous duplex exchange without SDK worker threads."""
import time

from ._native import FastlsError
from .upload import split


class DuplexExchange:
    def __init__(self, owner, handle):
        self._owner, self._handle = owner, handle
        self._finished = False
        self._response = None

    def _write(self, data, end=False):
        from .client import POLL_INTERVAL
        if self._handle is None or self._finished:
            raise FastlsError("upload is closed")
        while True:
            self._owner._info(self._handle)
            code = self._owner._native.write(self._handle, data, end=end)
            if code == 0:
                return
            if code == 2:
                raise FastlsError("server closed the upload")
            time.sleep(POLL_INTERVAL)

    def send(self, data):
        """Send binary data, waiting for bounded queue capacity."""
        for chunk in split(data):
            self._write(chunk)

    def finish_upload(self):
        """Send EOF; response reads can continue."""
        if not self._finished:
            self._write(b"", end=True)
            self._finished = True

    @property
    def response(self):
        from .client import Response, POLL_INTERVAL
        if self._handle is None:
            raise FastlsError("exchange is closed")
        if self._response is None:
            while True:
                info = self._owner._info(self._handle)
                if info["state"] != "pending":
                    self._response = Response(self._owner, self._handle, info)
                    break
                time.sleep(POLL_INTERVAL)
        return self._response

    def close(self):
        if self._handle is not None:
            self._owner._close_handle(self._handle)
            self._handle = None
            if self._response is not None:
                self._response._handle = None
