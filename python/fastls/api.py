"""Synchronous one-shot requests with an automatically closed session."""

from __future__ import annotations

from os import PathLike
from typing import Any, Literal

from .client import Response, Session


def request(method: str, url: str, *, library: str | PathLike[str] | None = None,
            ca_file: str | PathLike[str] | None = None, cookies: bool = True,
            notifications: Literal["auto", "required", "poll"] = "auto",
            **kwargs: Any) -> Response:
    """Send a buffered request and release its temporary session before returning.

    Request options match Session.request. library, ca_file, cookies and
    notifications configure the temporary session. Calls do not share cookies
    or connections; use Session for reuse, streaming or asynchronous requests.
    """
    with Session(library=library, ca_file=ca_file, cookies=cookies,
                 notifications=notifications) as session:
        return session.request(method, url, **kwargs)


def get(url: str, **kwargs: Any) -> Response:
    """Send a one-shot GET request. See request for options and lifecycle."""
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> Response:
    """Send a one-shot POST request. See request for options and lifecycle."""
    return request("POST", url, **kwargs)


def put(url: str, **kwargs: Any) -> Response:
    """Send a one-shot PUT request. See request for options and lifecycle."""
    return request("PUT", url, **kwargs)


def patch(url: str, **kwargs: Any) -> Response:
    """Send a one-shot PATCH request. See request for options and lifecycle."""
    return request("PATCH", url, **kwargs)


def delete(url: str, **kwargs: Any) -> Response:
    """Send a one-shot DELETE request. See request for options and lifecycle."""
    return request("DELETE", url, **kwargs)


def head(url: str, **kwargs: Any) -> Response:
    """Send HEAD using the same redirect policy as Session.request."""
    return request("HEAD", url, **kwargs)


def options(url: str, **kwargs: Any) -> Response:
    """Send a one-shot OPTIONS request. See request for options and lifecycle."""
    return request("OPTIONS", url, **kwargs)
