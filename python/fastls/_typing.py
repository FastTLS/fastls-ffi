"""Shared SDK type declarations (no runtime dependency on typing_extensions)."""
from collections.abc import Iterable, AsyncIterable, Mapping, Sequence
from os import PathLike
from typing import Any, BinaryIO, Literal, TypedDict, Union
from .timeout import Timeout

Path = Union[str, PathLike[str]]
Scalar = Union[str, bytes, int, float, None]
Fields = Union[Mapping[str, Any], Sequence[tuple[str, Any]]]
FileBody = Union[bytes, bytearray, memoryview, BinaryIO]
File = Union[FileBody, tuple[Union[str, None], FileBody], tuple[Union[str, None], FileBody, Union[str, None]]]


class RequestOptions(TypedDict, total=False):
    params: Union[Fields, str, None]
    headers: Union[Mapping[str, str], None]
    json: Any
    data: Union[str, bytes, bytearray, memoryview, BinaryIO, Iterable[bytes], AsyncIterable[bytes], Fields, None]
    files: Union[Mapping[str, File], Sequence[tuple[str, File]], None]
    trailers: Union[Mapping[str, Union[str, list[str], tuple[str, ...]]], None]
    profile: Union[str, None]
    timeout: Union[float, Timeout, None]
    allow_redirects: bool
    max_redirects: int
    decode_content: Union[bool, None]
    max_decoded_bytes: Union[int, None]
    duplex: bool
    proxy: Union[str, None]
    proxy_ca_file: Union[Path, None]


class SyncRequestOptions(RequestOptions, total=False):
    stream: bool


class SessionOptions(TypedDict, total=False):
    profile: str
    headers: Union[Mapping[str, str], None]
    timeout: Union[float, Timeout]
    library: Union[Path, None]
    ca_file: Union[Path, None]
    cookies: bool
    decode_content: bool
    max_decoded_bytes: int
    notifications: Literal["auto", "required", "poll"]
    proxy: Union[str, None]
    proxy_ca_file: Union[Path, None]
