import asyncio
import base64
from contextlib import contextmanager, asynccontextmanager
import ctypes
import json
from pathlib import Path
import threading
import time

from ._native import Native, FastlsError

# Windows asyncio can dispatch timers up to one monotonic-clock tick early.
# A delay shorter than that tick can repeatedly complete without yielding time.
POLL_INTERVAL = max(0.01, 2 * time.get_clock_info("monotonic").resolution)
MAX_BUFFERED_BODY = 32 * 1024 * 1024


class Response:
    def __init__(self, owner, handle, info):
        self._owner, self._handle = owner, handle
        self.status_code = info["status"]
        self.headers = info.get("headers", {})
        self.match = info["match"]
        self._content = None

    def _read(self, buffer):
        if self._handle is None:
            raise FastlsError("response is closed")
        return self._owner._native.read(self._handle, buffer)

    def iter_bytes(self, chunk_size=65536):
        if not 1 <= chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be 1..1048576")
        buffer = ctypes.create_string_buffer(chunk_size)
        while True:
            code, data = self._read(buffer)
            if code == 0:
                yield data
            elif code == 2:
                return
            else:
                time.sleep(POLL_INTERVAL)

    def read(self):
        if self._content is None:
            self._content = _collect(self.iter_bytes())
        return self._content

    @property
    def content(self):
        return self.read()

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def cancel(self):
        if self._handle is not None:
            self._owner._native.call("fastls_cancel", self._handle)

    def close(self):
        if self._handle is not None:
            self._owner._close_handle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _collect(chunks):
    body = bytearray()
    for chunk in chunks:
        body.extend(chunk)
        if len(body) > MAX_BUFFERED_BODY:
            raise FastlsError("buffered response exceeds 32MiB; use streaming")
    return bytes(body)


class Session:
    """Configuration, request ownership and isolated reusable connection pools."""
    def __init__(self, *, profile="chrome/156/windows", headers=None, timeout=30, library=None, ca_file=None):
        self.profile, self.headers, self.timeout = profile, dict(headers or {}), timeout
        self._ca_pem = Path(ca_file).read_text(encoding="utf-8") if ca_file is not None else None
        if self._ca_pem is not None and not self._ca_pem.strip():
            raise ValueError("ca_file is empty")
        self._native = Native(library)
        self._handles = set()
        self._lock = threading.Lock()
        self._closed = False
        if not hasattr(self._native.lib, "fastls_session_create"):
            raise FastlsError("connection pools require a newer Fastls native library")
        self._session = self._native.call("fastls_session_create")

    def close_idle_connections(self):
        with self._lock:
            if self._closed:
                raise FastlsError("session is closed")
            self._native.call("fastls_session_close_idle", self._session)

    def list_profiles(self):
        return self._native.call("fastls_profiles")

    def stats(self):
        if not hasattr(self._native.lib, "fastls_stats"):
            raise FastlsError("runtime statistics require a newer Fastls library")
        return self._native.call("fastls_stats")

    def _begin(self, method, url, *, profile=None, headers=None, data=None, timeout=None, allow_redirects=True):
        # Merge headers case-insensitively so request overrides win.
        merged = {k.lower(): v for k, v in self.headers.items()}
        merged.update({k.lower(): v for k, v in (headers or {}).items()})
        if any(not isinstance(v, str) for v in merged.values()):
            raise TypeError("header values must be strings")
        seconds = self.timeout if timeout is None else timeout
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400:
            raise ValueError("timeout must be 0..86400 seconds, excluding zero")
        request = dict(url=url, method=method, profile=self.profile if profile is None else profile,
                       session=self._session, headers=merged, timeout_ms=max(1, int(seconds * 1000)), disable_redirect=not allow_redirects)
        if self._ca_pem is not None:
            request["ca_pem"] = self._ca_pem
        if isinstance(data, bytes):
            request["body_base64"] = base64.b64encode(data).decode("ascii")
        elif isinstance(data, str):
            request["body"] = data
        elif data is not None:
            raise TypeError("data must be str or bytes; streaming upload is not implemented")
        with self._lock:
            if self._closed:
                raise FastlsError("session is closed")
            handle = self._native.start(request)
            self._handles.add(handle)
        return handle

    def _close_handle(self, handle):
        with self._lock:
            if handle in self._handles:
                self._native.call("fastls_close", handle)
                self._handles.remove(handle)

    def _info(self, handle):
        info = self._native.call("fastls_info", handle)
        if info["state"] == "error":
            raise FastlsError(info["error"])
        return info

    def _open(self, method, url, **kwargs):
        handle = self._begin(method, url, **kwargs)
        try:
            while True:
                info = self._info(handle)
                if info["state"] != "pending":
                    return Response(self, handle, info)
                time.sleep(POLL_INTERVAL)
        except BaseException:
            self._close_handle(handle)
            raise

    @contextmanager
    def stream(self, method, url, **kwargs):
        response = self._open(method, url, **kwargs)
        try:
            yield response
        finally:
            response.close()

    def request(self, method, url, **kwargs):
        with self.stream(method, url, **kwargs) as response:
            response.read()
            return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for handle in list(self._handles):
                self._native.call("fastls_close", handle)
                self._handles.remove(handle)
            self._native.call("fastls_session_close", self._session)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AsyncResponse(Response):
    async def aiter_bytes(self, chunk_size=65536):
        if not 1 <= chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be 1..1048576")
        buffer = ctypes.create_string_buffer(chunk_size)
        try:
            while True:
                code, data = self._read(buffer)
                if code == 0:
                    yield data
                    await asyncio.sleep(0)  # Fairness even when a fast stream is ready.
                elif code == 2:
                    return
                else:
                    await asyncio.sleep(POLL_INTERVAL)
        except BaseException:
            self.close()
            raise

    async def aread(self):
        if self._content is None:
            body = bytearray()
            async for chunk in self.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BUFFERED_BODY:
                    self.close()
                    raise FastlsError("buffered response exceeds 32MiB; use streaming")
            self._content = bytes(body)
        return self._content

    def read(self):
        if self._content is None:
            raise FastlsError("use await response.aread() for an async response")
        return self._content

    def iter_bytes(self, chunk_size=65536):
        raise FastlsError("use response.aiter_bytes() for an async response")

    async def aclose(self):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()


class AsyncSession(Session):
    async def _aopen(self, method, url, **kwargs):
        handle = self._begin(method, url, **kwargs)
        try:
            while True:
                info = self._info(handle)
                if info["state"] != "pending":
                    return AsyncResponse(self, handle, info)
                await asyncio.sleep(POLL_INTERVAL)
        except BaseException:
            self._close_handle(handle)
            raise

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        response = await self._aopen(method, url, **kwargs)
        try:
            yield response
        finally:
            await response.aclose()

    async def request(self, method, url, **kwargs):
        async with self.stream(method, url, **kwargs) as response:
            await response.aread()
            return response

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)

    async def aclose(self):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()
