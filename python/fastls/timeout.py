from dataclasses import dataclass


@dataclass(frozen=True)
class Timeout:
    """Total deadline and per-hop connect/write/headers/read limits, in seconds."""
    total: float = 30
    connect: float | None = None
    headers: float | None = None
    read: float | None = None
    write: float | None = None

    def __post_init__(self):
        for name in ("total", "connect", "headers", "read", "write"):
            value = getattr(self, name)
            if value is None and name != "total":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 86400:
                raise ValueError(f"{name} timeout must be 0..86400 seconds, excluding zero")

    def native_fields(self):
        result = {"timeout_ms": max(1, int(self.total * 1000))}
        for name in ("connect", "headers", "read", "write"):
            value = getattr(self, name)
            if value is not None:
                result[name + "_timeout_ms"] = max(1, int(value * 1000))
        return result
