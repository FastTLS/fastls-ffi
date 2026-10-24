"""SDK-only request encoding; multipart bodies are one-shot bounded streams."""
from collections.abc import Mapping
from email.message import Message
import json
import mimetypes
from pathlib import Path
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit

from .upload import chunks

_UNSET = object()


def _pairs(value, name):
    if isinstance(value, Mapping):
        return list(value.items())
    if isinstance(value, (list, tuple)) and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in value):
        return list(value)
    raise TypeError(f"{name} must be a mapping or a sequence of (name, value) pairs")


def _values(value, name):
    for key, item in _pairs(value, name):
        if not isinstance(key, (str, bytes)):
            raise TypeError(f"{name} names must be str or bytes")
        for part in item if isinstance(item, (list, tuple)) else (item,):
            if part is not None:
                if not isinstance(part, (str, bytes, int, float)):
                    raise TypeError(f"{name} values must be scalar values or lists of scalars")
                yield key, part


def _form(value):
    return isinstance(value, Mapping) or (isinstance(value, (list, tuple)) and
        all(isinstance(p, (list, tuple)) and len(p) == 2 for p in value))


def _quoted(value):
    if not isinstance(value, str):
        raise TypeError("multipart names, filenames and content types must be strings")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("multipart metadata must not contain control characters")
    return value.replace("\\", "\\\\").replace('"', "%22")


class Multipart:
    def __init__(self, data, files):
        self.boundary = "fastls-" + secrets.token_hex(24)
        self.parts = []
        self.used = False
        self.final_chunk = False
        for name, value in _values(data, "data") if data is not None else ():
            name = name.decode("utf-8") if isinstance(name, bytes) else name
            self.parts.append((f'Content-Disposition: form-data; name="{_quoted(name)}"\r\n',
                               value if isinstance(value, bytes) else str(value).encode("utf-8")))
        for name, spec in _pairs(files, "files"):
            if isinstance(spec, tuple):
                if len(spec) not in (2, 3):
                    raise TypeError("file tuples must be (filename, body[, content_type])")
                filename, body = spec[:2]
                kind = spec[2] if len(spec) == 3 else None
            else:
                body = spec
                filename = Path(str(getattr(body, "name", name))).name
                kind = None
            if not isinstance(body, (bytes, bytearray, memoryview)) and not callable(getattr(body, "read", None)):
                raise TypeError("multipart files must be bytes or binary files")
            disposition = f'Content-Disposition: form-data; name="{_quoted(name)}"'
            if filename is not None:
                disposition += f'; filename="{_quoted(filename)}"'
            kind = kind or (mimetypes.guess_type(filename)[0] if filename else None) or "application/octet-stream"
            _quoted(kind)
            self.parts.append((disposition + f"\r\nContent-Type: {kind}\r\n", body))

    def __iter__(self):
        if self.used:
            raise ValueError("multipart bodies cannot be replayed")
        self.used = True
        for headers, body in self.parts:
            yield ("--" + self.boundary + "\r\n" + headers + "\r\n").encode("utf-8")
            source = chunks(body)
            try:
                yield from source
            finally:
                source.close()
            yield b"\r\n"
        # Publish EOF with this known terminal block. A multipart parser may
        # respond as soon as it sees the boundary, before a separate EOF write.
        self.final_chunk = True
        yield ("--" + self.boundary + "--\r\n").encode("ascii")


def prepare(url, options, default_headers):
    """Validate before opening a native handle, without reading upload sources."""
    options = dict(options)
    params = options.pop("params", None)
    payload = options.pop("json", _UNSET)
    files = options.pop("files", None)
    data = options.get("data")
    if payload is not _UNSET and (data is not None or files is not None):
        raise ValueError("json cannot be combined with data or files")
    if params is not None:
        query = params if isinstance(params, str) else urlencode(list(_values(params, "params")))
        if isinstance(params, str) and (not query.isascii() or "#" in query):
            raise ValueError("raw params must be an ASCII query without a fragment")
        if query:
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                             parts.query + ("&" if parts.query else "") + query, parts.fragment))
    headers = {k.lower(): v for k, v in default_headers.items()}
    headers.update({k.lower(): v for k, v in (options.get("headers") or {}).items()})
    kind = None
    if files is not None:
        if options.get("duplex"):
            raise ValueError("multipart cannot be combined with duplex")
        if "content-type" in headers:
            raise ValueError("multipart manages Content-Type and its boundary")
        data = Multipart(data, files)
        kind = "multipart/form-data; boundary=" + data.boundary
    elif payload is not _UNSET:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        kind = "application/json"
    elif _form(data):
        data = urlencode(list(_values(data, "data"))).encode("ascii")
        kind = "application/x-www-form-urlencoded"
    if kind:
        if "content-length" in headers or "transfer-encoding" in headers:
            raise ValueError("encoded bodies manage Content-Length and Transfer-Encoding")
        headers.setdefault("content-type", kind)
    options["headers"] = headers
    options["data"] = data
    return url, options


def response_encoding(content_type):
    message = Message()
    message["content-type"] = content_type or ""
    return message.get_content_charset() or "utf-8"
