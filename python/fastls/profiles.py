"""Explicit immutable snapshots for the native profile.Spec schema."""
import json
from collections.abc import Mapping


class ProfileSpec:
    def __init__(self, value: Mapping):
        if not isinstance(value, Mapping):
            raise TypeError("ProfileSpec requires a mapping")
        self._json = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        if len(self._json.encode("utf-8")) > 256 * 1024:
            raise ValueError("profile_spec exceeds 256KiB")

    @classmethod
    def from_fingerprints(cls, base: "ProfileSpec", *, ja3: str="", h2: str="", library=None) -> "ProfileSpec":
        """Overlay JA3/H2 onto a complete base using the native compiler, offline.

        JA3 omits signature algorithms, ALPN and key shares; those stay explicit
        in base. The result does not inherit browser verification evidence.
        """
        if not isinstance(base, cls):
            raise TypeError("base must be ProfileSpec")
        if not isinstance(ja3, str) or not isinstance(h2, str):
            raise TypeError("ja3 and h2 must be strings")
        from ._native import Native, FastlsError
        native = Native(library)
        if (not hasattr(native.lib, "fastls_profile_adapt") or
                not hasattr(native.lib, "fastls_features") or
                not ({"fingerprint_adapter_v1"} | base._required_capabilities()).issubset(native.call("fastls_features"))):
            raise FastlsError("native fingerprint adapter is unavailable", code="unsupported_capability")
        raw = json.dumps(dict(schema_version=1, base=base.to_dict(), ja3=ja3, h2=h2), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return cls(native.call("fastls_profile_adapt", raw, len(raw)))

    def to_dict(self):
        return json.loads(self._json)

    def _required_capabilities(self):
        value = self.to_dict()
        h2 = value.get("http2", {})
        needs_stream = isinstance(h2, dict) and any(h2.get(key) for key in ("first_stream_id", "stream_window_increment"))
        capabilities = value.get("capabilities", {})
        features = capabilities.get("required_features", []) if isinstance(capabilities, dict) else []
        return {"profile_spec_v1", "http2_stream_v1"} if needs_stream or isinstance(features, list) and "http2.stream.v1" in features else {"profile_spec_v1"}
