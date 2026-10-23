import asyncio
import base64
from contextlib import contextmanager, asynccontextmanager
import ctypes
import json
from pathlib import Path
import threading
import time
from .upload import is_stream, chunks, async_chunks

from ._native import Native, FastlsError, raise_response_error
from .headers import Headers
from .cookies import Cookies
from .timeout import Timeout

# Windows asyncio can dispatch timers up to one monotonic-clock tick early.
# A delay shorter than that tick can repeatedly complete without yielding time.
POLL_INTERVAL = max(0.01, 2 * time.get_clock_info("monotonic").resolution)
MAX_BUFFERED_BODY = 32 * 1024 * 1024


def _cancel_task(task):
    if task.done() or task.cancelling():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is task.get_loop():
        # Mark cancellation before closing the native handle. A queued producer
        # must not get another turn and use a handle that was just released.
        task.cancel()
    else:
        task.get_loop().call_soon_threadsafe(_cancel_task, task)


class Response:
    def __init__(self, owner, handle, info):
        self._owner, self._handle = owner, handle
        self.status_code = info["status"]
        self.headers = Headers(info.get("header_values"), fallback=info.get("headers", {}))
        self.wire_headers = Headers(info.get("wire_header_values"), fallback=dict(self.headers)) if info.get("wire_header_values") else self.headers
        self.decoded = info.get("decoded", False)
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
    def __init__(self, *, profile="chrome/156/windows", headers=None, timeout=30, library=None, ca_file=None, cookies=True,
                 decode_content=True, max_decoded_bytes=256 * 1024 * 1024):
        if not isinstance(cookies, bool):
            raise TypeError("cookies must be a boolean")
        self.profile, self.headers, self.timeout = profile, dict(headers or {}), timeout
        self.decode_content, self.max_decoded_bytes = decode_content, max_decoded_bytes
        self._ca_pem = Path(ca_file).read_text(encoding="utf-8") if ca_file is not None else None
        if self._ca_pem is not None and not self._ca_pem.strip():
            raise ValueError("ca_file is empty")
        self._native = Native(library)
        self._handles = set()
        self._producers = {}
        self._lock = threading.Lock()
        self._closed = False
        if not hasattr(self._native.lib, "fastls_features") or not {"stream_decode", "phase_timeouts"}.issubset(self._native.call("fastls_features")):
            raise FastlsError("stream decoding and phase timeouts require a newer Fastls native library")
        if not hasattr(self._native.lib, "fastls_session_create"):
            raise FastlsError("connection pools require a newer Fastls native library")
        if not hasattr(self._native.lib, "fastls_session_cookies"):
            raise FastlsError("cookie management requires a newer Fastls native library")
        self._session = self._native.call("fastls_session_create")
        self.cookies = Cookies(self)
        if not cookies:
            self.cookies._command(action="configure", enabled=False)

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

    def _begin(self, method, url, *, profile=None, headers=None, data=None, timeout=None, allow_redirects=True,
               decode_content=None, max_decoded_bytes=None, duplex=False):
        if not isinstance(duplex, bool):
            raise TypeError("duplex must be a boolean")
        if duplex and "duplex_h2" not in self._native.call("fastls_features"):
            raise FastlsError("HTTP/2 duplex requires a newer Fastls native library")
        # Merge headers case-insensitively so request overrides win.
        merged = {k.lower(): v for k, v in self.headers.items()}
        merged.update({k.lower(): v for k, v in (headers or {}).items()})
        if any(not isinstance(v, str) for v in merged.values()):
            raise TypeError("header values must be strings")
        limit = self.timeout if timeout is None else timeout
        limit = limit if isinstance(limit, Timeout) else Timeout(total=limit)
        if limit.write is not None and "write_timeout" not in self._native.call("fastls_features"):
            raise FastlsError("write timeout requires a newer Fastls native library")
        decode = self.decode_content if decode_content is None else decode_content
        maximum = self.max_decoded_bytes if max_decoded_bytes is None else max_decoded_bytes
        if not isinstance(decode, bool):
            raise TypeError("decode_content must be a boolean")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 1 << 40:
            raise ValueError("max_decoded_bytes must be 1..1099511627776")
        request = dict(url=url, method=method, profile=self.profile if profile is None else profile,
                       session=self._session, headers=merged, disable_redirect=duplex or not allow_redirects,
                       decode_content=decode, max_decoded_bytes=maximum, **limit.native_fields())
        if self._ca_pem is not None:
            request["ca_pem"] = self._ca_pem
        streaming = duplex or is_stream(data)
        if streaming:
            if "stream_upload" not in self._native.call("fastls_features"):
                raise FastlsError("stream upload requires a newer Fastls native library")
            request["stream_body"] = True
            if duplex:
                request["duplex"] = True
        elif isinstance(data, bytes):
            request["body_base64"] = base64.b64encode(data).decode("ascii")
        elif isinstance(data, str):
            request["body"] = data
        elif data is not None:
            if not is_stream(data):
                raise TypeError("data must be str, bytes, a binary file or an iterable of bytes")
        with self._lock:
            if self._closed:
                raise FastlsError("session is closed")
            handle = self._native.start(request)
            self._handles.add(handle)
        return handle

    def _close_handle(self, handle):
        with self._lock:
            producer = self._producers.pop(handle, None)
            if producer is not None:
                _cancel_task(producer)
            if handle in self._handles:
                self._native.call("fastls_close", handle)
                self._handles.remove(handle)

    def _info(self, handle):
        info = self._native.call("fastls_info", handle)
        if info["state"] == "error":
            raise_response_error(info)
        return info

    def _open(self, method, url, **kwargs):
        if kwargs.get("duplex"):
            raise TypeError("use Session.duplex() for synchronous manual send/receive")
        data = kwargs.get("data")
        if hasattr(data, "__aiter__"):
            raise TypeError("async upload iterators require AsyncSession")
        handle = self._begin(method, url, **kwargs)
        source = chunks(data) if is_stream(data) else None
        pending = None
        ended = source is None
        try:
            while True:
                info = self._info(handle)
                if info["state"] != "pending":
                    if not ended and info["status"] < 300 and not info.get("upload_discarded"):
                        raise FastlsError("server responded before upload finished; duplex streaming is not supported")
                    return Response(self, handle, info)
                if not ended:
                    if pending is None:
                        pending = next(source, None)
                    code = self._native.write(handle, pending or b"", end=pending is None)
                    if code == 2:
                        ended = True
                        continue
                    if code == 0:
                        ended = pending is None
                        pending = None
                        continue
                time.sleep(POLL_INTERVAL)
        except BaseException:
            self._close_handle(handle)
            raise
        finally:
            if source is not None:
                try:
                    source.close()
                except BaseException:
                    self._close_handle(handle)
                    raise

    @contextmanager
    def duplex(self, method, url, **kwargs):
        """Manual synchronous H2 exchange; the caller controls send/receive order."""
        if "data" in kwargs or "duplex" in kwargs:
            raise TypeError("Session.duplex uses send() and finish_upload(), not data")
        from .duplex import DuplexExchange
        handle = self._begin(method, url, duplex=True, **kwargs)
        exchange = DuplexExchange(self, handle)
        try:
            yield exchange
        finally:
            exchange.close()

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
            for producer in self._producers.values():
                _cancel_task(producer)
            self._producers.clear()
            for handle in list(self._handles):
                self._native.call("fastls_close", handle)
                self._handles.remove(handle)
            self._native.call("fastls_session_close", self._session)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AsyncResponse(Response):
    def __init__(self, owner, handle, info, producer=None):
        super().__init__(owner, handle, info)
        self._producer = producer

    def _check_upload(self):
        if self._producer is not None and self._producer.done():
            if not self._producer.cancelled():
                self._producer.result()

    async def wait_upload(self):
        """Wait for upload EOF acceptance; consume responses concurrently to avoid backpressure."""
        if self._producer is not None:
            if await asyncio.shield(self._producer) is not True:
                raise FastlsError("upload ended before EOF was accepted")

    async def aiter_bytes(self, chunk_size=65536):
        if not 1 <= chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be 1..1048576")
        buffer = ctypes.create_string_buffer(chunk_size)
        try:
            while True:
                self._check_upload()
                code, data = self._read(buffer)
                if code == 0:
                    yield data
                    await asyncio.sleep(0)  # Fairness even when a fast stream is ready.
                elif code == 2:
                    if self._producer is not None:
                        await self.aclose()
                    return
                else:
                    await asyncio.sleep(POLL_INTERVAL)
        except BaseException:
            await self.aclose()
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
        if self._producer is not None:
            results = await asyncio.gather(self._producer, return_exceptions=True)
            error = results[0]
            if isinstance(error, BaseException) and not isinstance(error, asyncio.CancelledError):
                raise error

    def cancel(self):
        super().cancel()
        if self._producer is not None:
            _cancel_task(self._producer)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()


class AsyncSession(Session):
    def _upload_step(self, handle, chunk, *, end=False, duplex=False):
        with self._lock:
            if handle not in self._handles:
                raise asyncio.CancelledError
            info = self._info(handle)
            if info["state"] != "pending" and not (duplex and info["state"] == "headers" and info["status"] < 300):
                return 2
            return self._native.write(handle, chunk, end=end)

    async def _upload(self, handle, data, duplex=False):
        source = async_chunks(data)
        try:
            async for chunk in source:
                while True:
                    code = self._upload_step(handle, chunk, duplex=duplex)
                    if code == 2:
                        return
                    if code == 0:
                        break
                    await asyncio.sleep(POLL_INTERVAL)
                await asyncio.sleep(0)
            while True:
                code = self._upload_step(handle, b"", end=True, duplex=duplex)
                if code == 2:
                    return
                if code == 0:
                    return True
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await source.aclose()

    async def _aopen(self, method, url, **kwargs):
        duplex = kwargs.get("duplex", False)
        handle = self._begin(method, url, **kwargs)
        data = kwargs.get("data")
        producer = asyncio.create_task(self._upload(handle, b"" if data is None else data, duplex=duplex)) if duplex or is_stream(data) else None
        if duplex:
            self._producers[handle] = producer
            def completed(task):
                if not task.cancelled() and task.exception() is not None:
                    # Wake a blocked native reader; retain the Python exception.
                    with self._lock:
                        if handle in self._handles:
                            self._native.call("fastls_cancel", handle)
            producer.add_done_callback(completed)
        transferred = False
        try:
            while True:
                if producer is not None and producer.done():
                    producer.result()
                info = self._info(handle)
                if info["state"] != "pending":
                    if not duplex and producer is not None and info["status"] < 300 and not info.get("upload_discarded") and (not producer.done() or producer.result() is not True):
                        raise FastlsError("server responded before upload finished; duplex streaming is not supported")
                    if duplex:
                        if info["status"] >= 300:
                            producer.cancel()
                        transferred = True
                        return AsyncResponse(self, handle, info, producer=producer)
                    return AsyncResponse(self, handle, info)
                await asyncio.sleep(POLL_INTERVAL)
        except BaseException:
            self._close_handle(handle)
            raise
        finally:
            if producer is not None and not transferred:
                producer.cancel()
                try:
                    outcome = (await asyncio.gather(producer, return_exceptions=True))[0]
                    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                        raise outcome
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
        producers = list(self._producers.values())
        self.close()
        if producers:
            results = await asyncio.gather(*producers, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()
