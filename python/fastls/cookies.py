"""Python-owned cookie storage. Cookie state never crosses the native ABI."""
from datetime import datetime
from email.message import Message
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from ipaddress import ip_address
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request

from publicsuffixlist import PublicSuffixList
from ._native import FastlsError

_PSL = PublicSuffixList()
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def cookie_url(url: str) -> str:
    if url.startswith("ws:"):
        url = "http:" + url[3:]
    elif url.startswith("wss:"):
        url = "https:" + url[4:]
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        _ = parts.port
    except (ValueError, AttributeError):
        raise FastlsError("cookie URL must be absolute HTTP(S) without credentials") from None
    return url


class _Policy(DefaultCookiePolicy):
    def set_ok_domain(self, cookie, request):
        domain = cookie.domain.lstrip(".").lower()
        if cookie.domain_specified and _PSL.publicsuffix(domain) == domain:
            return False
        return super().set_ok_domain(cookie, request)

    def return_ok_domain(self, cookie, request):
        host = urlsplit(request.full_url).hostname
        if not cookie.domain_specified and cookie.domain not in (host, host + ".local"):
            return False
        return super().return_ok_domain(cookie, request)

    def return_ok_secure(self, cookie, request):
        host = urlsplit(request.full_url).hostname
        loopback = host == "localhost" or host.endswith(".localhost")
        try:
            loopback = loopback or ip_address(host).is_loopback
        except ValueError:
            pass
        return loopback or super().return_ok_secure(cookie, request)


class Cookies:
    """Thread-safe, URL-scoped Python cookie jar owned by a Session."""
    def __init__(self, owner, enabled: bool = True):
        self._owner = owner
        self._enabled = enabled
        self._jar = CookieJar(policy=_Policy())
        self._lock = threading.RLock()

    def _check(self):
        if self._owner._closed:
            raise FastlsError("session is closed")

    def get_list(self, url: str) -> list[dict[str, str]]:
        self._check()
        request = Request(cookie_url(url))
        with self._lock:
            self._jar.clear_expired_cookies()
            self._jar.add_cookie_header(request)
            selected = self._jar._cookies_for_request(request)
            selected.sort(key=lambda item: -len(item.path))
            return [{"name": item.name, "value": item.value or ""} for item in selected] if self._enabled else []

    def get_dict(self, url: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for cookie in self.get_list(url):
            result.setdefault(cookie["name"], cookie["value"])
        return result

    def get(self, name: str, *, url: str, default=None):
        return self.get_dict(url).get(name, default)

    def header(self, url: str) -> str:
        return "; ".join(item["name"] + "=" + item["value"] for item in self.get_list(url))

    def extract(self, url: str, fields: list[str]) -> None:
        if not self._enabled or not fields:
            return
        self._check()
        request = Request(cookie_url(url))
        message = Message()
        for value in fields:
            message["Set-Cookie"] = value
        class Incoming:
            def info(self):
                return message
        with self._lock:
            self._jar.extract_cookies(Incoming(), request)

    def set(self, name: str, value: str, *, url: str, path: str | None = None,
            domain: str | None = None, secure: bool = False, http_only: bool = False,
            max_age: int | None = None, expires: str | None = None) -> None:
        self._check()
        if not self._enabled:
            raise FastlsError("cookie store is disabled")
        request = Request(cookie_url(url))
        host = urlsplit(request.full_url).hostname
        if (not isinstance(name, str) or not _TOKEN.fullmatch(name) or not isinstance(value, str)
                or any(ord(char) < 32 or ord(char) >= 127 or char in ';"\\' for char in value)
                or len((name + value).encode()) > 4096):
            raise FastlsError("invalid cookie name or value")
        if domain:
            target = domain.lstrip(".").lower()
            if (host != target and not host.endswith("." + target)) or _PSL.publicsuffix(target) == target:
                raise FastlsError("invalid cookie domain")
        if path is not None and (not path.startswith("/") or any(ord(char) < 32 or char == ";" for char in path)):
            raise FastlsError("invalid cookie path")
        expiry = None
        if expires is not None:
            try:
                parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError()
                expiry = int(parsed.timestamp())
            except (ValueError, TypeError, AttributeError):
                raise FastlsError("expires must use RFC3339 format") from None
        if max_age is not None:
            if isinstance(max_age, bool) or not isinstance(max_age, int) or not -2147483648 <= max_age <= 2147483647:
                raise FastlsError("invalid cookie max_age")
            expiry = int(time.time()) + max_age if max_age > 0 else 0
        default_path = urlsplit(request.full_url).path.rpartition("/")[0] or "/"
        stored_host = host + ".local" if "." not in host else host
        cookie = Cookie(0, name, value, None, False, domain or stored_host, bool(domain), bool(domain and domain.startswith(".")),
                        path or default_path, bool(path), secure, expiry, expiry is None, None, None,
                        {"HttpOnly": None} if http_only else {}, False)
        with self._lock:
            self._jar.set_cookie_if_ok(cookie, request)
            self._jar.clear_expired_cookies()

    def delete(self, name: str, *, url: str, path: str | None = None, domain: str | None = None) -> None:
        self.set(name, "", url=url, path=path, domain=domain, max_age=-1)

    def clear(self) -> None:
        self._check()
        with self._lock:
            self._jar.clear()
