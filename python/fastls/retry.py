"""Bounded business retry decisions; no body consumption or native I/O."""
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import math
import random
import time

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    methods: tuple[str, ...] = ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")
    statuses: tuple[int, ...] = (429, 502, 503, 504)
    backoff: float = 0.1
    max_delay: float = 10.0
    jitter: bool = True
    retry_after: bool = True
    retry_network: bool = True
    allow_non_idempotent: bool = False

    def __post_init__(self):
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 100:
            raise ValueError("max_retries must be 0..100")
        for name in ("jitter", "retry_after", "retry_network", "allow_non_idempotent"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(name + " must be a boolean")
        for name in ("backoff", "max_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400:
                raise ValueError(name + " must be finite and within 0..86400 seconds")
        if isinstance(self.methods, str) or not isinstance(self.methods, (tuple, list)) or any(not isinstance(m, str) or not m.isascii() or not m.isalpha() for m in self.methods):
            raise ValueError("methods must contain HTTP method names")
        methods = tuple(m.upper() for m in self.methods)
        if not self.allow_non_idempotent and set(methods) - SAFE_METHODS:
            raise ValueError("non-idempotent methods require explicit authorization")
        if not isinstance(self.statuses, (tuple, list)) or any(isinstance(s, bool) or not isinstance(s, int) or not 400 <= s <= 599 or s in (401, 403, 407) for s in self.statuses):
            raise ValueError("statuses must be 400..599 excluding authentication/permission failures")
        object.__setattr__(self, "methods", methods)
        object.__setattr__(self, "statuses", tuple(self.statuses))

    def eligible(self, method, used, replayable, error=None, response=None):
        if used >= self.max_retries or method.upper() not in self.methods or not replayable:
            return False
        if response is not None:
            return response.status_code in self.statuses
        code = getattr(error, "code", None)
        return self.retry_network and (code == "network_error" or (code == "timeout" and getattr(error, "phase", None) in ("connect", "headers")))

    def delay(self, used, header=None):
        delay = min(self.max_delay, self.backoff * 2 ** used)
        if self.jitter:
            delay *= random.random()
        if self.retry_after and header:
            try:
                if header.isascii() and header.isdigit():
                    digits = header.lstrip("0") or "0"
                    after = float(digits) if len(digits) < 12 else self.max_delay
                else:
                    parsed = parsedate_to_datetime(header)
                    if parsed.tzinfo is None:
                        raise ValueError()
                    after = max(0.0, parsed.timestamp() - time.time())
                delay = max(delay, min(self.max_delay, after))
            except (ValueError, TypeError, OverflowError):
                pass
        return delay


def replayable(body, factory=None):
    return factory is not None or body is None or isinstance(body, (str, bytes, bytearray, memoryview))
