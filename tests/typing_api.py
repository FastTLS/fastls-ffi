"""Static consumer check for module-level requests (target Python 3.10+)."""
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fastls


async def async_consumer() -> None:
    async with fastls.ClientSession(profile="chrome/156/windows") as session:
        async with session.get("https://example.com", params={"q": "hello"}) as response:
            status: int = response.status
            body: bytes = await response.read()
            text: str = await response.text()
            async for chunk in response.content.iter_chunked(1024):
                part: bytes = chunk
                del part
            del status, body, text


if TYPE_CHECKING:
    credentials = fastls.BasicAuth("user", "password")
    retry = fastls.RetryPolicy(max_retries=1)
    reliable = fastls.Session(auth=credentials, retry=retry)
    reliable.get("https://example.com", request_cookies={"language": "zh"}, auth=None)
    reliable.put("https://example.com", body_factory=lambda: iter([b"body"]))
    fastls.get("https://example.com", auth=fastls.BearerAuth("token"), retry=retry, request_cookies={"x": "y"})
    local_store = fastls.ProfileStore(Path("offline-profiles"))
    spec = fastls.ProfileSpec({"schema_version": 1})
    configured = fastls.Session(profile_spec=spec, address_policy={"allow_hosts": ["example.com"]})
    configured.get("https://example.com", profile_spec=spec, http_version="auto")
    forced_h3 = fastls.AsyncSession(http_version="h3")
    query: dict[str, str] = {"q": "hello"}
    repeated: dict[str, list[str]] = {"tag": ["a", "b"]}
    fastls.get("https://example.com/", params=query)
    fastls.get("https://example.com/", params=repeated)
    response: fastls.Response = fastls.get(
        "https://example.com/", params={"q": "hello", "page": 1},
        headers={"Accept": "application/json"}, timeout=fastls.Timeout(total=15),
        ca_file=Path("test-ca.pem"), notifications="poll",
    )
    status: int = response.status_code
    text: str = response.text
    content: bytes = response.content
    ok: bool = response.ok
    header: str | None = response.headers.get("Content-Type")
    cookies: list[str] = response.headers.get_list("Set-Cookie")
    payload: Any = response.json()
    response.raise_for_status()
    for send in (fastls.post, fastls.put, fastls.patch, fastls.delete, fastls.options):
        result: fastls.Response = send("https://example.com/", json={"ok": True})
    fastls.head("https://example.com/", allow_redirects=False)
    fastls.request("CUSTOM", "https://example.com/", data=b"hello")
    fastls.post("https://example.com/", files={"file": ("data.bin", b"payload", "application/octet-stream")})
