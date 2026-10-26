"""Static consumer check for module-level requests (target Python 3.10+)."""
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fastls


if TYPE_CHECKING:
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
