"""Keyword completion for one-shot requests, compatible with Python 3.10+."""
from collections.abc import Iterable, Mapping, Sequence
from os import PathLike
from typing import Any, BinaryIO, Literal, TypeAlias

from .client import Response
from .timeout import Timeout

_Path: TypeAlias = str | PathLike[str]
_Bytes: TypeAlias = bytes | bytearray | memoryview
_Key: TypeAlias = str | bytes
_Scalar: TypeAlias = str | bytes | int | float | None
_Value: TypeAlias = _Scalar | Sequence[_Scalar]
_Fields: TypeAlias = Mapping[str, _Value] | Mapping[bytes, _Value] | Mapping[_Key, _Value] | Sequence[tuple[_Key, _Value]]
_FileBody: TypeAlias = _Bytes | BinaryIO
_File: TypeAlias = _FileBody | tuple[str | None, _FileBody] | tuple[str | None, _FileBody, str | None]
_Files: TypeAlias = Mapping[str, _File] | Sequence[tuple[str, _File]]
_Data: TypeAlias = str | _Bytes | BinaryIO | Iterable[_Bytes] | _Fields
_Trailers: TypeAlias = Mapping[str, str | list[str] | tuple[str, ...]]

def request(
    method: str,
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def get(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def post(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def put(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def patch(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def delete(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def head(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...

def options(
    url: str,
    *,
    params: _Fields | str | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any = ...,
    data: _Data | None = None,
    files: _Files | None = None,
    trailers: _Trailers | None = None,
    profile: str | None = None,
    timeout: float | Timeout | None = None,
    allow_redirects: bool = True,
    decode_content: bool | None = None,
    max_decoded_bytes: int | None = None,
    duplex: bool = False,
    proxy: str | None = None,
    proxy_ca_file: _Path | None = None,
    library: _Path | None = None,
    ca_file: _Path | None = None,
    cookies: bool = True,
    notifications: Literal["auto", "required", "poll"] = "auto",
) -> Response: ...
