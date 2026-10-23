"""Bounded, pull-based SSE parsing. Network lifetime remains owned by Response."""
import asyncio
from dataclasses import dataclass
import json
import re
from typing import Optional

from ._native import FastlsError

DEFAULT_EVENT_LIMIT = 1024 * 1024
MAX_RETRY_MS = 2147483647
_LINE_END = re.compile(br"\r\n|[\r\n]")


@dataclass(frozen=True)
class ServerSentEvent:
    data: str
    event: str = "message"
    id: str = ""
    retry: Optional[int] = None

    def json(self):
        return json.loads(self.data)


def _validate_options(max_event_bytes, last_event_id):
    if isinstance(max_event_bytes, bool) or not isinstance(max_event_bytes, int) or not 1 <= max_event_bytes <= 64 * 1024 * 1024:
        raise ValueError("max_event_bytes must be 1..67108864")
    if last_event_id is not None:
        if not isinstance(last_event_id, str):
            raise TypeError("last_event_id must be a string or None")
        if any(c in last_event_id for c in "\0\r\n"):
            raise ValueError("last_event_id must not contain NUL, CR or LF")
        if len(last_event_id.encode("utf-8")) > max_event_bytes:
            raise ValueError("last_event_id exceeds max_event_bytes")


def _request_options(options, max_event_bytes, last_event_id):
    _validate_options(max_event_bytes, last_event_id)
    headers = {"accept": "text/event-stream", "cache-control": "no-cache"}
    headers.update({k.lower(): v for k, v in (options.pop("headers", None) or {}).items()})
    if last_event_id is not None:
        headers["last-event-id"] = last_event_id
    return dict(options, headers=headers)


class _Decoder:
    def __init__(self, maximum, initial_id=""):
        self.maximum = maximum
        self.last_event_id = self._id = initial_id
        self.retry = self._block_retry = None
        self._line = bytearray()
        self._data = []
        self._size = 0
        self._event = ""
        self._first = True
        self._skip_lf = False

    def _append(self, part):
        if len(self._line) + len(part) > self.maximum:
            raise FastlsError("SSE line exceeds max_event_bytes")
        self._line.extend(part)

    def _process_line(self):
        line = self._line.decode("utf-8", errors="replace")
        self._line.clear()
        if self._first:
            line = line.removeprefix("\ufeff")
            self._first = False
        if not line:
            self.last_event_id = self._id
            event = ServerSentEvent("\n".join(self._data), self._event or "message", self._id, self._block_retry) if self._data else None
            self._data.clear()
            self._size = 0
            self._event = ""
            self._block_retry = None
            return event
        if line.startswith(":"):
            return
        field, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if field == "data":
            size = self._size + len(value.encode("utf-8")) + (1 if self._data else 0)
            if size > self.maximum:
                raise FastlsError("SSE event exceeds max_event_bytes")
            self._size = size
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id" and "\0" not in value:
            self._id = value
        elif field == "retry" and re.fullmatch(r"[0-9]+", value):
            # Avoid Python's integer-string limit and unbounded big-int work.
            digits = value.lstrip("0") or "0"
            ceiling = str(MAX_RETRY_MS)
            retry = MAX_RETRY_MS if len(digits) > len(ceiling) or (len(digits) == len(ceiling) and digits > ceiling) else int(digits)
            self.retry = self._block_retry = retry

    def feed(self, chunk):
        if not chunk:
            return
        start = 0
        if self._skip_lf:
            start = int(chunk.startswith(b"\n"))
            self._skip_lf = False
        for match in _LINE_END.finditer(chunk, start):
            self._append(chunk[start:match.start()])
            event = self._process_line()
            start = match.end()
            self._skip_lf = match.group() == b"\r" and start == len(chunk)
            if event is not None:
                yield event
        self._append(chunk[start:])

    def finish(self):
        # EOF is not a blank line and must not dispatch an incomplete event.
        self._line.clear()
        self._data.clear()
        self._size = 0
        self._event = ""
        self._id = self.last_event_id


class SSEStream:
    def __init__(self, response, *, max_event_bytes=DEFAULT_EVENT_LIMIT, last_event_id=None):
        _validate_options(max_event_bytes, last_event_id)
        self.response = response
        self._decoder = _Decoder(max_event_bytes, last_event_id or "")
        self._started = self._closed = self._cancelled = False
        if response.status_code != 204:
            if response.status_code != 200:
                raise FastlsError(f"SSE requires HTTP 200, received {response.status_code}")
            types = response.headers.get_list("content-type")
            if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "text/event-stream":
                raise FastlsError("SSE requires Content-Type text/event-stream")
            encoding = response.wire_headers.get("content-encoding", "identity").strip().lower()
            if encoding != "identity" and not response.decoded:
                raise FastlsError("SSE requires decoded content; enable decode_content")

    @property
    def last_event_id(self):
        return self._decoder.last_event_id

    @property
    def retry(self):
        """Latest server retry hint, in milliseconds; None before a valid hint."""
        return self._decoder.retry

    def _check_open(self):
        if self._closed or self._cancelled or self.response._handle is None or self.response._owner._closed:
            raise FastlsError("SSE stream is closed or cancelled")

    def _claim(self):
        self._check_open()
        if self._started:
            raise FastlsError("SSE stream permits only one consumer")
        self._started = True

    def __iter__(self):
        self._claim()
        return self._events()

    def _events(self):
        try:
            if self.response.status_code == 204:
                return
            for chunk in self.response.iter_bytes():
                for event in self._decoder.feed(chunk):
                    self._check_open()
                    yield event
        finally:
            self.close()

    def cancel(self):
        self._cancelled = True
        self.response.cancel()

    def close(self):
        self._closed = True
        self._decoder.finish()
        self.response.close()


class AsyncSSEStream(SSEStream):
    def __iter__(self):
        raise FastlsError("use async for with an async SSE stream")

    def __aiter__(self):
        self._claim()
        return self._events()

    async def _events(self):
        try:
            if self.response.status_code == 204:
                return
            async for chunk in self.response.aiter_bytes():
                for event in self._decoder.feed(chunk):
                    self._check_open()
                    yield event
                    await asyncio.sleep(0)
        finally:
            await self.aclose()

    async def aclose(self):
        self._closed = True
        self._decoder.finish()
        await self.response.aclose()
