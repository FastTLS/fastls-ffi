"""Pinned, offline profile bundles and atomic local activation."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .profiles import ProfileSpec

LIMIT = 512 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate profile bundle field")
        result[key] = value
    return result


def _strict(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > LIMIT:
        raise ValueError("profile bundle exceeds 512KiB")
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
        def visit(item, depth=0):
            if depth > 64:
                raise ValueError("profile bundle exceeds depth 64")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if not isinstance(item, int) or abs(item) > 2 ** 53 - 1:
                    raise ValueError("profile bundle requires portable integer fields")
            if isinstance(item, dict):
                for key in item:
                    key.encode("utf-8", errors="strict")
                for entry in item.values():
                    visit(entry, depth + 1)
            elif isinstance(item, list):
                for entry in item:
                    visit(entry, depth + 1)
            elif isinstance(item, str):
                item.encode("utf-8", errors="strict")
        visit(value)
        return value
    except (RecursionError, UnicodeError, json.JSONDecodeError):
        raise ValueError("invalid profile bundle JSON") from None


def _read(path):
    with Path(path).open("rb") as file:
        raw = file.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("profile bundle exceeds 512KiB")
    return raw.decode("utf-8", errors="strict")


def _atomic(path, text):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".fastls-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(text.encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProfileBundle:
    """The pinned digest covers exact spec_json bytes, not producer identity."""
    def __init__(self, text, *, sha256, library=None):
        if not isinstance(sha256, str) or not _HASH.fullmatch(sha256):
            raise ValueError("an explicit lowercase SHA-256 pin is required")
        envelope = _strict(text)
        keys = {"schema_version", "profile_id", "spec_json", "sha256", "source"}
        if not isinstance(envelope, dict) or set(envelope) != keys or type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise ValueError("unsupported profile bundle schema")
        raw, source = envelope["spec_json"], envelope["source"]
        if not isinstance(raw, str) or not isinstance(source, str) or not 1 <= len(source.encode("utf-8")) <= 512 or any(ord(c) < 32 or ord(c) == 127 for c in source):
            raise ValueError("invalid profile bundle fields")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if digest != sha256 or digest != envelope["sha256"]:
            raise ValueError("profile bundle digest mismatch")
        value = _strict(raw)
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or value["id"] != envelope["profile_id"]:
            raise ValueError("profile bundle ID mismatch")
        spec = ProfileSpec(value)
        from .client import Session
        with Session(library=library, profile_spec=spec):
            pass  # The native compiler validates without DNS or dialing.
        self._text = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._spec = spec
        self.sha256, self.profile_id, self.source = digest, value["id"], source

    @classmethod
    def create(cls, spec, *, source, library=None):
        if not isinstance(spec, ProfileSpec):
            raise TypeError("spec must be ProfileSpec")
        raw = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        envelope = dict(schema_version=1, profile_id=spec.to_dict().get("id"), spec_json=raw, sha256=digest, source=source)
        return cls(json.dumps(envelope, ensure_ascii=False), sha256=digest, library=library)

    @property
    def spec(self):
        return ProfileSpec(self._spec.to_dict())

    def dumps(self):
        return self._text + "\n"

    def export(self, path):
        _atomic(path, self.dumps())


class ProfileStore:
    """Trusted local directory. Callers serialize writers to the same store."""
    def __init__(self, directory, *, library=None):
        self.directory = Path(directory)
        self.library = library
        self._objects = self.directory / "objects"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._state_path = self.directory / "state.json"

    def _state(self):
        if not self._state_path.exists():
            return {"current": None, "previous": None}
        value = _strict(_read(self._state_path))
        if not isinstance(value, dict) or set(value) != {"current", "previous"} or any(v is not None and (not isinstance(v, str) or not _HASH.fullmatch(v)) for v in value.values()):
            raise ValueError("invalid profile store state")
        return value

    def load(self, sha256=None):
        pin = self._state()["current"] if sha256 is None else sha256
        if not isinstance(pin, str) or not _HASH.fullmatch(pin):
            raise ValueError("no valid profile pin")
        return ProfileBundle(_read(self._objects / (pin + ".json")), sha256=pin, library=self.library)

    def install(self, path, *, sha256):
        bundle = ProfileBundle(_read(path), sha256=sha256, library=self.library)
        target = self._objects / (bundle.sha256 + ".json")
        if target.exists():
            self.load(bundle.sha256)  # Corrupt existing objects are never replaced.
        else:
            _atomic(target, bundle.dumps())
        return self.pin(bundle.sha256)

    def pin(self, sha256):
        bundle = self.load(sha256)
        state = self._state()
        if state["current"] != sha256:
            _atomic(self._state_path, json.dumps({"current": sha256, "previous": state["current"]}) + "\n")
        return bundle

    def rollback(self):
        previous = self._state()["previous"]
        if previous is None:
            raise ValueError("no previous profile pin")
        return self.pin(previous)
