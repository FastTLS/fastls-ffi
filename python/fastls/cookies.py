import json

from ._native import FastlsError


class Cookies:
    """URL-scoped access to the session's native in-memory cookie store."""
    def __init__(self, owner):
        self._owner = owner

    def _command(self, **command):
        owner = self._owner
        raw = json.dumps(command, ensure_ascii=False).encode("utf-8")
        with owner._lock:
            if owner._closed:
                raise FastlsError("session is closed")
            return owner._native.call("fastls_session_cookies", owner._session, raw, len(raw))

    def get_list(self, url):
        return self._command(action="get", url=url)

    def get_dict(self, url):
        result = {}
        for cookie in self.get_list(url):
            result.setdefault(cookie["name"], cookie["value"])
        return result

    def get(self, name, *, url, default=None):
        return self.get_dict(url).get(name, default)

    def set(self, name, value, *, url, path=None, domain=None, secure=False,
            http_only=False, max_age=None, expires=None):
        cookie = dict(name=name, value=value, secure=secure, http_only=http_only)
        for key, item in dict(path=path, domain=domain, max_age=max_age, expires=expires).items():
            if item is not None:
                cookie[key] = item
        self._command(action="set", url=url, cookie=cookie)

    def delete(self, name, *, url, path=None, domain=None):
        self.set(name, "", url=url, path=path, domain=domain, max_age=-1)

    def clear(self):
        self._command(action="clear")
