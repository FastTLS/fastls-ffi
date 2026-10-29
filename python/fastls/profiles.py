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

    def to_dict(self):
        return json.loads(self._json)
