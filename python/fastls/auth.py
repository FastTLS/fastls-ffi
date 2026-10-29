"""Explicit request credentials; representations never contain secrets."""
import base64
from dataclasses import dataclass, field

INHERIT = object()


@dataclass(frozen=True)
class BasicAuth:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.username, str) or not isinstance(self.password, str):
            raise TypeError("Basic credentials must be strings")
        if ":" in self.username or any(ord(c) < 32 or ord(c) == 127 for c in self.username + self.password):
            raise ValueError("invalid Basic credentials")

    def header(self):
        return "Basic " + base64.b64encode((self.username + ":" + self.password).encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class BearerAuth:
    token: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token or any(ord(c) <= 32 or ord(c) >= 127 for c in self.token):
            raise ValueError("invalid Bearer token")

    def header(self):
        return "Bearer " + self.token


def authorization(value):
    if isinstance(value, tuple) and len(value) == 2:
        value = BasicAuth(*value)
    if value is None:
        return None
    if not isinstance(value, (BasicAuth, BearerAuth)):
        raise TypeError("auth must be BasicAuth, BearerAuth, a credential tuple or None")
    return value.header()
