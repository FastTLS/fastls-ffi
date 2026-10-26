"""Familiar aiohttp-style entry points over Fastls's asynchronous transport."""
from __future__ import annotations

from typing import Any, AsyncIterator, Coroutine, Generator, TYPE_CHECKING
if TYPE_CHECKING:
    from typing_extensions import Unpack
    from ._typing import RequestOptions, SessionOptions
from .client import AsyncSession, AsyncResponse


class ClientResponse:
    """Streaming async response. Consume or close it to release the request."""
    def __init__(self, response: AsyncResponse):
        self._response = response
        self.status = response.status_code
        self.url = response.url
        self.headers = response.headers
        self.history = response.history
        self.match = response.match

    @property
    def content(self) -> ClientResponse:
        return self

    async def iter_chunked(self, n: int) -> AsyncIterator[bytes]:
        if not 1 <= n <= 1024 * 1024:
            raise ValueError("chunk size must be 1..1048576")
        if self._response._content is not None:
            for offset in range(0, len(self._response._content), n):
                yield self._response._content[offset:offset + n]
        else:
            async for chunk in self._response.aiter_bytes(n):
                yield chunk

    async def read(self) -> bytes:
        return await self._response.aread()

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        return (await self.read()).decode(encoding or self._response.encoding or "utf-8", errors=errors)

    async def json(self) -> Any:
        import json
        return json.loads(await self.read())

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    def release(self) -> None:
        self._response.close()

    close = release

    async def wait_for_close(self) -> None:
        await self._response.aclose()

    async def __aenter__(self) -> ClientResponse:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.wait_for_close()


class _RequestContext:
    def __init__(self, request: Coroutine[Any, Any, AsyncResponse]):
        self._request = request
        self._response: ClientResponse | None = None

    async def _open(self) -> ClientResponse:
        if self._response is None:
            self._response = ClientResponse(await self._request)
        return self._response

    def __await__(self) -> Generator[Any, None, ClientResponse]:
        return self._open().__await__()

    async def __aenter__(self) -> ClientResponse:
        return await self._open()

    async def __aexit__(self, *_: Any) -> None:
        if self._response is not None:
            await self._response.wait_for_close()


class ClientSession:
    """Use ``async with session.get(url) as response`` or await the request.

    This entry point follows aiohttp's response-reading convention. AsyncSession
    retains the existing Fastls buffered API and advanced streaming helpers.
    """
    def __init__(self, **kwargs: Unpack[SessionOptions]):
        self._session = AsyncSession(**kwargs)
        self.cookies = self._session.cookies
        self.cookie_jar = self.cookies
        self.headers = self._session.headers

    @property
    def closed(self) -> bool:
        return self._session._closed

    async def close(self) -> None:
        await self._session.aclose()

    async def __aenter__(self) -> ClientSession:
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def request(self, method: str, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return _RequestContext(self._session._aopen(method, url, **kwargs))

    def get(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Unpack[RequestOptions]) -> _RequestContext:
        return self.request("OPTIONS", url, **kwargs)
