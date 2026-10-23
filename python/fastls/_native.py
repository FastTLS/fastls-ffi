"""ctypes binding to C ABI v1. No native callbacks or Python worker threads."""
import ctypes as C
import json
import os
from pathlib import Path
import sys
import threading


class FastlsError(Exception):
    pass


_libraries = {}
_load_lock = threading.Lock()


class Native:
    def __init__(self, library=None):
        name = "fastls.dll" if sys.platform == "win32" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
        path = Path(library or os.environ.get("FASTLS_LIBRARY") or Path(__file__).resolve().parents[2] / "dist" / name).resolve()
        with _load_lock:
            if path not in _libraries:
                _libraries[path] = C.CDLL(str(path))
            self.lib = _libraries[path]  # Retain the library for the process lifetime.
        self.lib.fastls_abi_version.argtypes = []
        self.lib.fastls_abi_version.restype = C.c_uint32
        if self.lib.fastls_abi_version() != 1:
            raise FastlsError("unsupported Fastls ABI version")
        signatures = {
            "fastls_profiles": [],
            "fastls_start": [C.c_char_p, C.c_size_t],
            "fastls_info": [C.c_uint64],
            "fastls_cancel": [C.c_uint64],
            "fastls_close": [C.c_uint64],
        }
        if hasattr(self.lib, "fastls_stats"):
            signatures["fastls_stats"] = []
        for name, args in {"fastls_session_create": [], "fastls_session_close": [C.c_uint64], "fastls_session_close_idle": [C.c_uint64]}.items():
            if hasattr(self.lib, name):
                signatures[name] = args
        if hasattr(self.lib, "fastls_session_cookies"):
            signatures["fastls_session_cookies"] = [C.c_uint64, C.c_char_p, C.c_size_t]
        for name, args in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, C.c_void_p
        self.lib.fastls_free.argtypes = [C.c_void_p]
        self.lib.fastls_free.restype = None
        self.lib.fastls_read.argtypes = [C.c_uint64, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.lib.fastls_read.restype = C.c_int

    def call(self, name, *args):
        ptr = getattr(self.lib, name)(*args)
        if not ptr:
            raise FastlsError("native library returned a null result")
        try:
            envelope = json.loads(C.string_at(ptr).decode("utf-8"))
        finally:
            self.lib.fastls_free(ptr)
        if not envelope["ok"]:
            raise FastlsError(envelope["error"])
        return envelope["result"]

    def start(self, request):
        raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
        return self.call("fastls_start", raw, len(raw))

    def read(self, handle, buffer):
        length = C.c_size_t()
        code = self.lib.fastls_read(handle, buffer, len(buffer), C.byref(length))
        if code < 0:
            info = self.call("fastls_info", handle)
            raise FastlsError(info.get("error") or "native read failed")
        return code, buffer.raw[:length.value]
