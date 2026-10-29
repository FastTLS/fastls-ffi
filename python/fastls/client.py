from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager, asynccontextmanager
from collections.abc import Mapping
import ctypes
import json
from pathlib import Path
import threading
import time
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from typing_extensions import Unpack
    from ._typing import RequestOptions, SyncRequestOptions
from .upload import is_stream, chunks, async_chunks

from ._native import Native, FastlsError, raise_response_error
from .headers import Headers
from .cookies import Cookies
from .timeout import Timeout
from .redirects import Redirects
from .request_options import Multipart, prepare, response_encoding
from .profiles import ProfileSpec


class FastlsHTTPError(FastlsError):
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code
        super().__init__(f"HTTP status {self.status_code}")

# Windows asyncio can dispatch timers up to one monotonic-clock tick early.
# A delay shorter than that tick can repeatedly complete without yielding time.
POLL_INTERVAL = max(0.01, 2 * time.get_clock_info("monotonic").resolution)
MAX_BUFFERED_BODY = 32 * 1024 * 1024
_INHERIT_PROXY = object()


def _cancel_task(task):
    if task.done() or getattr(task, "_fastls_cancel_requested", False):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is task.get_loop():
        # Mark cancellation before closing the native handle. A queued producer
        # must not get another turn and use a handle that was just released.
        # Track our request explicitly for Python 3.10, before Task.cancelling().
        task._fastls_cancel_requested = True
        task.cancel()
    else:
        task.get_loop().call_soon_threadsafe(_cancel_task, task)


class Response:
    status_code: int
    http_version: str
    url: str
    headers: Headers
    wire_headers: Headers
    trailers: Headers
    decoded: bool
    match: dict[str, Any]
    encoding: str | None
    _content: bytes | None

    def __init__(self, owner, handle, info):
        self._owner, self._handle = owner, handle
        self.status_code = info["status"]
        self.http_version = info.get("protocol", "")
        self.url = info.get("final_url", "")
        self.headers = Headers(info.get("header_values"), fallback=info.get("headers", {}))
        self.history: list[Response] = []
        self.wire_headers = Headers(info.get("wire_header_values"), fallback=dict(self.headers)) if info.get("wire_header_values") else self.headers
        self.trailers = Headers(info.get("trailer_values"))
        self.decoded = info.get("decoded", False)
        self.match = info["match"]
        self._content = None
        self.encoding = response_encoding(self.headers.get("Content-Type"))

    def _read(self, buffer):
        if self._handle is None:
            raise FastlsError("response is closed")
        result = self._owner._native.read(self._handle, buffer)
        if result[0] == 2:
            info = self._owner._info(self._handle)
            self.trailers = Headers(info.get("trailer_values"))
            self.http_version = info.get("protocol", self.http_version)
            self.url = info.get("final_url", self.url)
        return result

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
                self._owner._wait(self._handle, 2)

    def iter_content(self, chunk_size: int = 65536):
        if self._content is not None:
            if chunk_size < 1:
                raise ValueError("chunk_size must be positive")
            for offset in range(0, len(self._content), chunk_size):
                yield self._content[offset:offset + chunk_size]
        else:
            yield from self.iter_bytes(chunk_size)

    def read(self) -> bytes:
        if self._content is None:
            self._content = _collect(self.iter_bytes())
        return self._content

    @property
    def content(self) -> bytes:
        return self.read()

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise FastlsHTTPError(self)

    def json(self) -> Any:
        return json.loads(self.content)

    def cancel(self) -> None:
        if self._handle is not None:
            self._owner._native.call("fastls_cancel", self._handle)

    def close(self) -> None:
        if self._handle is not None:
            self._owner._close_handle(self._handle)
            self._handle = None
        if getattr(self, "_close_session", False):
            self._close_session = False
            self._owner.close()

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
    def __init__(self, *, profile=None, headers=None, timeout=30, library=None, ca_file=None, cookies=True,
                 decode_content=True, max_decoded_bytes=256 * 1024 * 1024, notifications="auto", proxy=None, proxy_ca_file=None,
                 address_policy=None, profile_spec: ProfileSpec | None=None, http_version="auto"):
        if profile is not None and not isinstance(profile, str):
            raise TypeError("profile must be a selector string or None")
        if profile_spec is not None and not isinstance(profile_spec, ProfileSpec):
            raise TypeError("profile_spec must be a ProfileSpec")
        if profile_spec is not None and profile is not None:
            raise ValueError("profile and profile_spec are mutually exclusive")
        if http_version not in ("auto", "h3"):
            raise ValueError("http_version must be auto or h3")
        self._profile_spec = profile_spec
        self._http_version = http_version
        if proxy is not None and not isinstance(proxy, str):
            raise TypeError("proxy must be a URL string or None")
        self.proxy = proxy
        self._proxy_ca_pem = Path(proxy_ca_file).read_text(encoding="utf-8") if proxy_ca_file is not None else None
        if self._proxy_ca_pem is not None and not self._proxy_ca_pem.strip():
            raise ValueError("proxy_ca_file is empty")
        if notifications not in ("auto", "required", "poll"):
            raise ValueError("notifications must be auto, required or poll")
        if not isinstance(cookies, bool):
            raise TypeError("cookies must be a boolean")
        self.profile = profile if profile is not None else "" if profile_spec is not None or http_version == "h3" else "chrome/156/windows"
        self.headers, self.timeout = dict(headers or {}), timeout
        self.decode_content, self.max_decoded_bytes = decode_content, max_decoded_bytes
        self._ca_pem = Path(ca_file).read_text(encoding="utf-8") if ca_file is not None else None
        if self._ca_pem is not None and not self._ca_pem.strip():
            raise ValueError("ca_file is empty")
        self._native = Native(library)
        self._notifier = None
        self._notify_lock = None
        self._notify_loop = None
        features = self._native.call("fastls_features") if hasattr(self._native.lib, "fastls_features") else []
        self._native_notifications = "session_notifications" in features and all(hasattr(self._native.lib, name) for name in ("fastls_wait", "fastls_session_watch", "fastls_session_events", "fastls_session_unwatch"))
        if notifications == "required" and not self._native_notifications:
            raise FastlsError("notifications require a newer Fastls native library")
        self._native_notifications = self._native_notifications and notifications != "poll"
        self._handles = set()
        self._cookie_handles = set()
        self._producers = {}
        self._lock = threading.Lock()
        self._closed = False
        if not hasattr(self._native.lib, "fastls_features") or not {"stream_decode", "phase_timeouts"}.issubset(self._native.call("fastls_features")):
            raise FastlsError("stream decoding and phase timeouts require a newer Fastls native library")
        if not hasattr(self._native.lib, "fastls_session_create"):
            raise FastlsError("connection pools require a newer Fastls native library")
        if not hasattr(self._native.lib, "fastls_transport_create"):
            raise FastlsError("SDK-owned sessions require the Fastls 0.1.4 native library or newer")
        configuration = {"schema_version": 1}
        required = set()
        if address_policy is not None:
            if not isinstance(address_policy, Mapping):
                raise TypeError("address_policy must be a mapping")
            configuration["address_policy"] = dict(address_policy)
            required.add("transport_address_policy_v1")
        if profile_spec is not None:
            configuration["profile_spec"] = profile_spec.to_dict()
            required.add("profile_spec_v1")
        if http_version == "h3":
            configuration["http_version"] = "h3"
            required.add("http3_v1")
        if required:
            self._require_capabilities(required)
            if profile is not None:
                configuration["profile"] = profile
            if proxy and (address_policy is not None or http_version == "h3"):
                raise FastlsError("unsupported transport combination", code="unsupported_capability")
            raw = json.dumps(configuration, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._session = self._native.call("fastls_transport_create_with_options", raw, len(raw))
        else:
            self._session = self._native.call("fastls_transport_create")
        self.cookies = Cookies(self, enabled=cookies)
        self._ws_cookie_requests = {}

    def _require_capabilities(self, required):
        available = set(self._native.call("fastls_features"))
        if not set(required).issubset(available) or not hasattr(self._native.lib, "fastls_transport_create_with_options"):
            raise FastlsError("native transport capability is unavailable", code="unsupported_capability")

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

    @property
    def notification_mode(self):
        return "native-wait" if self._native_notifications else "poll"

    def _wait(self, handle, events):
        if self._native_notifications:
            self._native.wait(handle, events)
        else:
            time.sleep(POLL_INTERVAL)

    def _begin(self, method, url, *, profile=None, headers=None, trailers=None, data=None, timeout=None, allow_redirects=True,
               decode_content=None, max_decoded_bytes=None, duplex=False, proxy=_INHERIT_PROXY, proxy_ca_file=_INHERIT_PROXY,
               profile_spec: ProfileSpec | None=None, http_version=None):
        if profile_spec is not None and not isinstance(profile_spec, ProfileSpec):
            raise TypeError("profile_spec must be a ProfileSpec")
        if profile_spec is not None and profile is not None:
            raise ValueError("profile and profile_spec are mutually exclusive")
        required = set()
        if profile_spec is not None:
            required.add("profile_spec_v1")
        if http_version is not None:
            if http_version not in ("auto", "h3"):
                raise ValueError("http_version must be auto or h3")
            required.add("http3_v1")
        if required:
            self._require_capabilities(required)
        selected_proxy = self.proxy if proxy is _INHERIT_PROXY else proxy
        if selected_proxy is not None and not isinstance(selected_proxy, str):
            raise TypeError("proxy must be a URL string or None")
        if selected_proxy and "proxy_tunnels" not in self._native.call("fastls_features"):
            raise FastlsError("proxy support requires a newer Fastls native library")
        https_proxy = bool(selected_proxy and selected_proxy.lower().startswith("https://"))
        if https_proxy and "https_proxy_tls" not in self._native.call("fastls_features"):
            raise FastlsError("HTTPS proxy support requires a newer Fastls native library")
        proxy_pem = self._proxy_ca_pem
        if https_proxy and proxy_ca_file is not _INHERIT_PROXY:
            proxy_pem = Path(proxy_ca_file).read_text(encoding="utf-8") if proxy_ca_file is not None else None
            if proxy_pem is not None and not proxy_pem.strip():
                raise ValueError("proxy_ca_file is empty")
        if not isinstance(duplex, bool):
            raise TypeError("duplex must be a boolean")
        if duplex and "duplex_h2" not in self._native.call("fastls_features"):
            raise FastlsError("HTTP/2 duplex requires a newer Fastls native library")
        # Merge headers case-insensitively so request overrides win.
        merged = {k.lower(): v for k, v in (headers or {}).items()}
        if "cookie" not in merged:
            cookie_header = self.cookies.header(url)
            if cookie_header:
                merged["cookie"] = cookie_header
        if any(not isinstance(v, str) for v in merged.values()):
            raise TypeError("header values must be strings")
        normalized_trailers = {}
        if trailers is not None:
            if "request_trailers" not in self._native.call("fastls_features"):
                raise FastlsError("request trailers require a newer Fastls native library")
            if not isinstance(trailers, dict):
                raise TypeError("trailers must be a mapping")
            for name, value in trailers.items():
                values = [value] if isinstance(value, str) else value
                if not isinstance(name, str) or not isinstance(values, (list, tuple)) or any(not isinstance(item, str) for item in values):
                    raise TypeError("trailer names and values must be strings")
                normalized_trailers[name] = list(values)
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
        selected_profile = profile if profile is not None else "" if profile_spec is not None or http_version == "h3" else self.profile
        request = dict(url=url, method=method, profile=selected_profile,
                       session=self._session, headers=merged, disable_redirect=True,
                       decode_content=decode, max_decoded_bytes=maximum, **limit.native_fields())
        if profile_spec is not None:
            request["profile_spec"] = profile_spec.to_dict()
        if http_version is not None:
            request["http_version"] = http_version
        if normalized_trailers:
            request["trailers"] = normalized_trailers
        if self._ca_pem is not None:
            request["ca_pem"] = self._ca_pem
        if selected_proxy:
            request["proxy"] = selected_proxy
        if https_proxy and proxy_pem is not None:
            request["proxy_ca_pem"] = proxy_pem
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
            self._ws_cookie_requests.pop(handle, None)
            self._cookie_handles.discard(handle)
            producer = self._producers.pop(handle, None)
            if producer is not None:
                _cancel_task(producer)
            if handle in self._handles:
                self._native.call("fastls_close", handle)
                self._handles.remove(handle)
            if self._notifier is not None:
                self._notifier.forget(handle)

    def _info(self, handle):
        info = self._native.call("fastls_info", handle)
        if info["state"] == "error":
            raise_response_error(info)
        if info.get("status") and handle not in self._cookie_handles:
            self._accept_cookies(info)
            self._cookie_handles.add(handle)
        return info

    def _accept_cookies(self, info):
        self.cookies.extract(info.get("final_url", ""), Headers(info.get("header_values")).get_list("set-cookie"))

    def _open_hop(self, method, url, **kwargs):
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
                    end = pending is None or (isinstance(data, Multipart) and data.final_chunk)
                    code = self._native.write(handle, pending or b"", end=end)
                    if code == 2:
                        ended = True
                        continue
                    if code == 0:
                        ended = end
                        pending = None
                        continue
                self._wait(handle, 1 if ended else 5)
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

    def _open(self, method, url, **kwargs):
        url, kwargs = prepare(url, kwargs, self.headers)
        redirects = Redirects(method, url, kwargs, self.timeout)
        while True:
            method, url, options = redirects.request()
            response = self._open_hop(method, url, **options)
            try:
                if not redirects.follow(response):
                    return response
                response.read()
            except BaseException:
                response.close()
                raise
            response.close()

    @contextmanager
    def duplex(self, method, url, **kwargs):
        """Manual synchronous H2 exchange; the caller controls send/receive order."""
        if any(key in kwargs for key in ("data", "json", "files", "duplex")):
            raise TypeError("Session.duplex uses send() and finish_upload(), not data")
        from .duplex import DuplexExchange
        url, kwargs = prepare(url, kwargs, self.headers)
        handle = self._begin(method, url, duplex=True, **kwargs)
        exchange = DuplexExchange(self, handle)
        try:
            yield exchange
        finally:
            exchange.close()

    @contextmanager
    def sse(self, method, url, *, max_event_bytes=1024 * 1024, last_event_id=None, **kwargs):
        from .sse import SSEStream, _request_options
        options = _request_options(kwargs, max_event_bytes, last_event_id)
        with self.stream(method, url, **options) as response:
            events = SSEStream(response, max_event_bytes=max_event_bytes, last_event_id=last_event_id)
            try:
                yield events
            finally:
                events.close()

    @contextmanager
    def stream(self, method, url, **kwargs):
        response = self._open(method, url, **kwargs)
        try:
            yield response
        finally:
            response.close()

    def request(self, method: str, url: str, *, stream: bool = False, **kwargs: Unpack[RequestOptions]) -> Response:
        if stream:
            return self._open(method, url, **kwargs)
        with self.stream(method, url, **kwargs) as response:
            response.read()
            return response

    def get(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Unpack[SyncRequestOptions]) -> Response:
        return self.request("OPTIONS", url, **kwargs)

    def ws_connect(self, url, **kwargs):
        from .websocket import connect
        return connect(self, url, **kwargs)

    @contextmanager
    def websocket(self, url, **kwargs):
        ws = self.ws_connect(url, **kwargs)
        try:
            yield ws
        finally:
            ws.close()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self.cookies.clear()
            self._cookie_handles.clear()
            self._ws_cookie_requests.clear()
            self._closed = True
            if self._notifier is not None:
                self._notifier.stop()
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
                generation = self._owner._generation(self._handle)
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
                    await self._owner._await_change(self._handle, generation)
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
    @property
    def notification_mode(self):
        return "async-events" if self._native_notifications else "poll"

    async def _ensure_notifications(self):
        if not self._native_notifications:
            return
        from .notifications import AsyncNotifier
        loop = asyncio.get_running_loop()
        if self._notify_loop is not None and self._notify_loop is not loop:
            raise FastlsError("AsyncSession notifications cannot be shared across event loops")
        if self._notify_lock is None:
            self._notify_loop = loop
            self._notify_lock = asyncio.Lock()
        async with self._notify_lock:
            if self._notifier is None:
                notifier = self._notifier = AsyncNotifier(self)
                try:
                    await notifier.start()
                except BaseException:
                    self._notifier = None
                    raise
            self._notifier.check()

    def _generation(self, handle):
        return self._notifier.generation(handle) if self._notifier is not None and handle is not None else 0

    async def _await_change(self, handle, generation):
        if self._notifier is not None:
            await self._notifier.wait(handle, generation)
        else:
            await asyncio.sleep(POLL_INTERVAL)

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
                end = isinstance(data, Multipart) and data.final_chunk
                while True:
                    generation = self._generation(handle)
                    code = self._upload_step(handle, chunk, end=end, duplex=duplex)
                    if code == 2:
                        return
                    if code == 0:
                        if end:
                            return True
                        break
                    await self._await_change(handle, generation)
                await asyncio.sleep(0)
            while True:
                generation = self._generation(handle)
                code = self._upload_step(handle, b"", end=True, duplex=duplex)
                if code == 2:
                    return
                if code == 0:
                    return True
                await self._await_change(handle, generation)
        finally:
            await source.aclose()

    async def _aopen_hop(self, method, url, **kwargs):
        await self._ensure_notifications()
        duplex = kwargs.get("duplex", False)
        handle = self._begin(method, url, **kwargs)
        data = kwargs.get("data")
        producer = asyncio.create_task(self._upload(handle, b"" if data is None else data, duplex=duplex)) if duplex or is_stream(data) else None
        if duplex:
            self._producers[handle] = producer
        if producer is not None:
            def completed(task):
                if self._notifier is not None and handle in self._handles:
                    self._notifier.pulse(handle)
                if not task.cancelled() and task.exception() is not None:
                    # Wake a blocked native reader; retain the Python exception.
                    with self._lock:
                        if handle in self._handles:
                            self._native.call("fastls_cancel", handle)
            producer.add_done_callback(completed)
        transferred = False
        try:
            while True:
                generation = self._generation(handle)
                if producer is not None and producer.done():
                    producer.result()
                info = self._info(handle)
                if info["state"] != "pending":
                    if not duplex and producer is not None and info["status"] < 300 and not info.get("upload_discarded") and (not producer.done() or producer.result() is not True):
                        raise FastlsError("server responded before upload finished; duplex streaming is not supported")
                    if duplex:
                        if info["status"] >= 300:
                            _cancel_task(producer)
                        transferred = True
                        return AsyncResponse(self, handle, info, producer=producer)
                    return AsyncResponse(self, handle, info)
                await self._await_change(handle, generation)
        except BaseException:
            self._close_handle(handle)
            raise
        finally:
            if producer is not None and not transferred:
                _cancel_task(producer)
                try:
                    outcome = (await asyncio.gather(producer, return_exceptions=True))[0]
                    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                        raise outcome
                except BaseException:
                    self._close_handle(handle)
                    raise

    async def _aopen(self, method, url, **kwargs):
        url, kwargs = prepare(url, kwargs, self.headers)
        redirects = Redirects(method, url, kwargs, self.timeout)
        while True:
            method, url, options = redirects.request()
            response = await self._aopen_hop(method, url, **options)
            try:
                if not redirects.follow(response):
                    return response
                await response.aread()
            except BaseException:
                await response.aclose()
                raise
            await response.aclose()

    @asynccontextmanager
    async def sse(self, method, url, *, max_event_bytes=1024 * 1024, last_event_id=None, **kwargs):
        from .sse import AsyncSSEStream, _request_options
        options = _request_options(kwargs, max_event_bytes, last_event_id)
        async with self.stream(method, url, **options) as response:
            events = AsyncSSEStream(response, max_event_bytes=max_event_bytes, last_event_id=last_event_id)
            try:
                yield events
            finally:
                await events.aclose()

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        response = await self._aopen(method, url, **kwargs)
        try:
            yield response
        finally:
            await response.aclose()

    async def request(self, method: str, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        async with self.stream(method, url, **kwargs) as response:
            await response.aread()
            return response

    async def get(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        kwargs.setdefault("allow_redirects", False)
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Unpack[RequestOptions]) -> AsyncResponse:
        return await self.request("OPTIONS", url, **kwargs)

    async def ws_connect(self, url, **kwargs):
        from .websocket import aconnect
        return await aconnect(self, url, **kwargs)

    @asynccontextmanager
    async def websocket(self, url, **kwargs):
        ws = await self.ws_connect(url, **kwargs)
        try:
            yield ws
        finally:
            await ws.aclose()

    async def aclose(self):
        producers = list(self._producers.values())
        self.close()
        if self._notifier is not None:
            await self._notifier.aclose()
        if producers:
            results = await asyncio.gather(*producers, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result

    async def __aenter__(self):
        try:
            await self._ensure_notifications()
        except BaseException:
            self.close()
            raise
        return self

    async def __aexit__(self, *_):
        await self.aclose()
