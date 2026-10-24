"""Resolve and validate the package version used by development builds."""
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "python" / "VERSION"
VERSION_PATTERN = re.compile(
    r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+|\.post[0-9]+|\.dev[0-9]+)?"
    r"(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?"
)


def package_version(environ=None):
    environ = os.environ if environ is None else environ
    value = environ.get("FASTLS_PACKAGE_VERSION") or VERSION_FILE.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise RuntimeError(f"invalid FASTLS package version: {value!r}")
    return value


def require_development_version(environ=None):
    value = package_version(environ)
    if not re.search(r"\.dev[0-9]+(?:\+|$)", value):
        raise RuntimeError(f"development snapshots require a .devN version, got {value!r}")
    return value
