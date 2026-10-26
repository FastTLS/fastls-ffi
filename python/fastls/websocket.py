"""Bounded RFC6455 messages using the native session's readiness notifications."""
import asyncio
import ctypes as C
import json
import math
from pathlib import Path
import struct
import threading
import time

from ._native import FastlsError, raise_response_error
from .client import _INHERIT_PROXY, POLL_INTERVAL
from .headers import Headers
from .request_options import prepare


class WebSocketClosed(FastlsError):
    def __init__(self, code=1006, reason=""):
        self.code, self.reason = code, reason
        super().__init__(f"WebSocket closed ({code})")


def _milliseconds(value, name, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not (0 if zero else 0.001) <= value <= 86400:
        raise ValueError(f"{name} must be a finite number of seconds in {'0' if zero else '0.001'}..86400")
    return math.ceil(value * 1000)


def _start(owner, url, *, params=None, headers=None, profile=None, subprotocols=(),
           proxy=_INHERIT_PROXY, proxy_ca_file=_INHERIT_PROXY, handshake_timeout=30,
           read_timeout=None, write_timeout=30, max_message_bytes=1024 * 1024, close_timeout=1, cookie_mode="session"):
    features = owner._native.call("fastls_features")
    if cookie_mode not in ("session", "manual"):
        raise ValueError("cookie_mode must be session or manual")
    session_cookies = "websocket_cookie_control_v1" in features or "websocket_cookies" in features
    if cookie_mode == "manual" and not session_cookies and "websocket_cookies_v1" in features:
        raise FastlsError("This native library cannot disable automatic WebSocket cookies; a newer library with websocket_cookie_control_v1 is required")
    if cookie_mode == "session" and not session_cookies:
        raise FastlsError("WebSocket session cookies require a newer Fastls native library; use cookie_mode=manual for legacy behavior")
    if "websocket_v1" not in features:
        raise FastlsError("WebSocket requires a newer Fastls native library")
    if isinstance(max_message_bytes, bool) or not isinstance(max_message_bytes, int) or not 1 <= max_message_bytes <= 16 * 1024 * 1024:
        raise ValueError("max_message_bytes must be 1..16777216")
    _milliseconds(close_timeout, "close_timeout")
    if close_timeout > 60:
        raise ValueError("close_timeout must not exceed 60 seconds")
    if isinstance(subprotocols, str) or not isinstance(subprotocols, (list, tuple)) or any(not isinstance(p, str) for p in subprotocols):
        raise TypeError("subprotocols must be a list or tuple of strings")
    url, options = prepare(url, dict(params=params, headers=headers), owner.headers)
    if any(not isinstance(v, str) for v in options["headers"].values()):
        raise TypeError("header values must be strings")
    request = dict(url=url, headers=options["headers"], profile=owner.profile if profile is None else profile,
                   session=owner._session, subprotocols=list(subprotocols), max_message_bytes=max_message_bytes,
                   timeout_ms=_milliseconds(handshake_timeout, "handshake_timeout"),
                   read_timeout_ms=0 if read_timeout is None else _milliseconds(read_timeout, "read_timeout"),
                   write_timeout_ms=_milliseconds(write_timeout, "write_timeout"))
    request["disable_cookies"] = True
    if cookie_mode == "session" and "cookie" not in {name.lower() for name in request["headers"]}:
        cookie_header = owner.cookies.header(url)
        if cookie_header:
            request["headers"]["Cookie"] = cookie_header
    selected = owner.proxy if proxy is _INHERIT_PROXY else proxy
    if selected is not None and not isinstance(selected, str):
        raise TypeError("proxy must be a URL string or None")
    if selected:
        request["proxy"] = selected
    if owner._ca_pem is not None:
        request["ca_pem"] = owner._ca_pem
    if selected and selected.lower().startswith("https://"):
        pem = owner._proxy_ca_pem
        if proxy_ca_file is not _INHERIT_PROXY:
            pem = Path(proxy_ca_file).read_text(encoding="utf-8") if proxy_ca_file is not None else None
            if pem is not None and not pem.strip():
                raise ValueError("proxy_ca_file is empty")
        if pem is not None:
            request["proxy_ca_pem"] = pem
    raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
    with owner._lock:
        if owner._closed:
            raise FastlsError("session is closed")
        handle = owner._native.call("fastls_ws_start", raw, len(raw))
        owner._handles.add(handle)
        if cookie_mode == "session":
            owner._ws_cookie_requests[handle] = url
    return handle, close_timeout


class WebSocket:
    def __init__(self, owner, handle, close_timeout):
        self._owner, self._handle, self._close_timeout = owner, handle, close_timeout
        self._reader, self._writer = threading.Lock(), threading.Lock()
        self._last = {}
        self.headers = Headers()
        self.subprotocol = ""

    def _id(self):
        handle = self._handle
        if handle is None or self._owner._closed:
            raise FastlsError("WebSocket or session is closed")
        return handle

    def _info(self):
        self._last = self._owner._native.call("fastls_info", self._id())
        if self._last.get("status"):
            url = self._owner._ws_cookie_requests.pop(self._handle, None)
            if url and (self._last["status"] != 101 or self._last["state"] in ("headers", "eof")):
                self._owner.cookies.extract(url, Headers(self._last.get("header_values")).get_list("set-cookie"))
        return self._last

    @property
    def info(self):
        return self._info() if self._handle is not None and not self._owner._closed else self._last

    @property
    def close_code(self):
        return self.info.get("websocket", {}).get("close_code", 1006)

    @property
    def close_reason(self):
        return self.info.get("websocket", {}).get("close_reason", "")

    def _check(self, info):
        if info["state"] == "error":
            raise_response_error(info)
        if info["state"] == "eof":
            raise WebSocketClosed(info["websocket"]["close_code"], info["websocket"]["close_reason"])

    def _opened(self, info):
        self.headers = Headers(info.get("header_values"))
        self.subprotocol = info["websocket"]["subprotocol"]
        self.max_message_bytes = info["websocket"]["max_message_bytes"]
        return self

    def _payload(self, value):
        if isinstance(value, str):
            if len(value) > self.max_message_bytes:
                raise ValueError("WebSocket message exceeds max_message_bytes")
            data, kind = value.encode("utf-8"), 1
        elif isinstance(value, (bytes, bytearray, memoryview)):
            if memoryview(value).nbytes > self.max_message_bytes:
                raise ValueError("WebSocket message exceeds max_message_bytes")
            data, kind = bytes(value), 2
        else:
            raise TypeError("WebSocket messages must be str or bytes-like")
        if len(data) > self.max_message_bytes:
            raise ValueError("WebSocket message exceeds max_message_bytes")
        return kind, data

    def _wait(self, events, deadline=None):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("WebSocket close timed out")
        if self._owner._native_notifications:
            milliseconds = 1000 if deadline is None else max(1, min(1000, math.ceil((deadline - time.monotonic()) * 1000)))
            self._owner._native.wait(self._id(), events, milliseconds)
        else:
            time.sleep(POLL_INTERVAL)

    def _send(self, kind, data, deadline=None):
        if not self._writer.acquire(False):
            raise FastlsError("another WebSocket send is active")
        try:
            accepted = False
            while True:
                info = self._info()
                self._check(info)
                if accepted:
                    if not info["websocket"]["write_pending"]:
                        return
                else:
                    code = self._owner._native.lib.fastls_ws_send(self._id(), kind, data, len(data))
                    if code < 0:
                        self._check(self._info())
                        raise FastlsError("invalid WebSocket send or connection is closing")
                    if code == 0:
                        accepted = True
                        continue
                self._wait(4, deadline)
        except BaseException:
            self.abort()
            raise
        finally:
            self._writer.release()

    def send(self, message):
        self._send(*self._payload(message))

    def ping(self, data=b""):
        self._send(9, self._control(data))

    def pong(self, data=b""):
        self._send(10, self._control(data))

    @staticmethod
    def _control(data):
        if not isinstance(data, bytes):
            raise TypeError("control payload must be bytes")
        if len(data) > 125:
            raise ValueError("control payload must not exceed 125 bytes")
        return data

    def _read(self, buffer):
        size, kind, end = C.c_size_t(), C.c_int(), C.c_int()
        code = self._owner._native.lib.fastls_ws_read(self._id(), buffer, len(buffer), C.byref(size), C.byref(kind), C.byref(end))
        if code == 2:
            self._check(self._info())
            raise WebSocketClosed()
        if code < 0:
            self._check(self._info())
            raise FastlsError("WebSocket read failed")
        return code, buffer.raw[:size.value], kind.value, bool(end.value)

    def recv(self):
        if not self._reader.acquire(False):
            raise FastlsError("another WebSocket recv is active")
        try:
            buffer, body = C.create_string_buffer(65536), bytearray()
            while True:
                self._info()  # Check ownership; preserve already queued complete messages.
                code, data, kind, end = self._read(buffer)
                if code == 0:
                    body.extend(data)
                    if end:
                        return body.decode("utf-8") if kind == 1 else bytes(body)
                else:
                    self._wait(2)
        except BaseException:
            self.abort()
            raise
        finally:
            self._reader.release()

    @staticmethod
    def _close_payload(code, reason):
        if isinstance(code, bool) or not isinstance(code, int) or not (code in (1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014) or 3000 <= code <= 4999):
            raise ValueError("invalid WebSocket close code")
        if not isinstance(reason, str):
            raise TypeError("close reason must be str")
        encoded = reason.encode("utf-8")
        if len(encoded) > 123:
            raise ValueError("close reason must not exceed 123 UTF-8 bytes")
        return struct.pack("!H", code) + encoded

    def close(self, code=1000, reason=""):
        data = self._close_payload(code, reason)
        if self._handle is None:
            return
        deadline = time.monotonic() + self._close_timeout
        try:
            info = self._info()
            if info["state"] == "headers":
                self._send(8, data, deadline)
                while self._info()["state"] == "headers":
                    self._wait(2, deadline)
        except (FastlsError, TimeoutError):
            pass
        finally:
            self.abort()

    def abort(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            self._owner._close_handle(handle)

    cancel = abort

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AsyncWebSocket(WebSocket):
    async def _await(self, generation, deadline=None):
        waiting = self._owner._await_change(self._handle, generation)
        if deadline is None:
            await waiting
        else:
            await asyncio.wait_for(waiting, max(0, deadline - time.monotonic()))

    async def _send(self, kind, data, deadline=None):
        if not self._writer.acquire(False):
            raise FastlsError("another WebSocket send is active")
        try:
            accepted = False
            while True:
                generation = self._owner._generation(self._handle)
                info = self._info()
                self._check(info)
                if accepted:
                    if not info["websocket"]["write_pending"]:
                        return
                else:
                    code = self._owner._native.lib.fastls_ws_send(self._id(), kind, data, len(data))
                    if code < 0:
                        self._check(self._info())
                        raise FastlsError("invalid WebSocket send or connection is closing")
                    if code == 0:
                        accepted = True
                        continue
                await self._await(generation, deadline)
        except BaseException:
            self.abort()
            raise
        finally:
            self._writer.release()

    async def send(self, message):
        await self._send(*self._payload(message))

    async def ping(self, data=b""):
        await self._send(9, self._control(data))

    async def pong(self, data=b""):
        await self._send(10, self._control(data))

    async def recv(self):
        if not self._reader.acquire(False):
            raise FastlsError("another WebSocket recv is active")
        try:
            buffer, body = C.create_string_buffer(65536), bytearray()
            while True:
                generation = self._owner._generation(self._handle)
                self._info()
                code, data, kind, end = self._read(buffer)
                if code == 0:
                    body.extend(data)
                    if end:
                        return body.decode("utf-8") if kind == 1 else bytes(body)
                    await asyncio.sleep(0)
                else:
                    await self._await(generation)
        except BaseException:
            self.abort()
            raise
        finally:
            self._reader.release()

    async def aclose(self, code=1000, reason=""):
        data = self._close_payload(code, reason)
        if self._handle is None:
            return
        deadline = time.monotonic() + self._close_timeout
        try:
            if self._info()["state"] == "headers":
                await self._send(8, data, deadline)
                while True:
                    generation = self._owner._generation(self._handle)
                    if self._info()["state"] != "headers":
                        break
                    await self._await(generation, deadline)
        except (FastlsError, TimeoutError, asyncio.TimeoutError):
            pass
        finally:
            self.abort()

    def close(self, *args, **kwargs):
        raise FastlsError("use await websocket.aclose() or websocket.abort()")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()


def connect(owner, url, **kwargs):
    handle, timeout = _start(owner, url, **kwargs)
    ws = WebSocket(owner, handle, timeout)
    try:
        while True:
            info = ws._info()
            if info.get("status") == 101 and info["state"] in ("headers", "eof"):
                return ws._opened(info)
            ws._check(info)
            if info["state"] != "pending":
                return ws._opened(info)
            ws._wait(1)
    except BaseException:
        ws.abort()
        raise


async def aconnect(owner, url, **kwargs):
    await owner._ensure_notifications()
    handle, timeout = _start(owner, url, **kwargs)
    ws = AsyncWebSocket(owner, handle, timeout)
    try:
        while True:
            generation = owner._generation(handle)
            info = ws._info()
            if info.get("status") == 101 and info["state"] in ("headers", "eof"):
                return ws._opened(info)
            ws._check(info)
            if info["state"] != "pending":
                return ws._opened(info)
            await ws._await(generation)
    except BaseException:
        ws.abort()
        raise
