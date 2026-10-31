"""Bounded H3 owner configuration. Durations are whole milliseconds."""
from dataclasses import dataclass, asdict
from ._native import Native, FastlsError


@dataclass(frozen=True, slots=True)
class HTTP3Config:
    initial_stream_receive_window: int = 0
    max_stream_receive_window: int = 0
    initial_connection_receive_window: int = 0
    max_connection_receive_window: int = 0
    max_idle_timeout_ms: int = 0
    keep_alive_period_ms: int | None = None
    max_response_header_bytes: int = 0

    def __post_init__(self):
        values = self.to_dict()
        for name, value in values.items():
            if type(value) is not int:
                raise TypeError(name + " must be an integer")
            if value < 0:
                raise ValueError(name + " cannot be negative")
        defaults = (524288, 6291456, 524288, 15728640)
        names = ("initial_stream_receive_window", "max_stream_receive_window", "initial_connection_receive_window", "max_connection_receive_window")
        windows = [values[name] or default for name, default in zip(names, defaults)]
        if any(not 4096 <= value <= 67108864 for value in windows) or windows[0] > windows[1] or windows[2] > windows[3]:
            raise ValueError("H3 windows must be zero/default or 4096..67108864; initial <= maximum")
        idle = self.max_idle_timeout_ms or 30000
        if not 100 <= idle <= 86400000:
            raise ValueError("max_idle_timeout_ms must be zero or 100..86400000")
        if self.keep_alive_period_ms not in (None, 0) and not 10 <= self.keep_alive_period_ms <= idle / 2:
            raise ValueError("keep_alive_period_ms must be zero or 10..effective idle/2")
        if self.max_response_header_bytes and not 1024 <= self.max_response_header_bytes <= 16777216:
            raise ValueError("max_response_header_bytes must be zero or 1024..16777216")

    def to_dict(self) -> dict[str, int]:
        return {key: value for key, value in asdict(self).items() if key != "keep_alive_period_ms" or value is not None}


def require_http3(native):
    available = native.call("fastls_features")
    if "http3_config_v1" not in available or not all(hasattr(native.lib, name) for name in ("fastls_http3_capabilities", "fastls_transport_http3_config")):
        raise FastlsError("native H3 configuration capability is unavailable", code="unsupported_capability")


def http3_capabilities(*, library=None) -> dict:
    """Return fresh local field metadata; no owner allocation or network I/O."""
    native = Native(library)
    require_http3(native)
    return native.call("fastls_http3_capabilities")
