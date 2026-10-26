# fastls Python SDK

The source distribution contains the Python SDK but does not compile or download the native Fastls library. Build and install the sdist with Python 3.10 or newer, then set `FASTLS_LIBRARY` to a compatible ABI v1 library or pass its absolute path to `Session(library=...)`.

Platform wheels contain the matching native library and need no Go or C compiler at installation time. New Linux wheels are audited as manylinux_2_39 and require glibc 2.39 or newer; Alpine/musl is not supported. Legacy v0.1.2 GitHub wheels use linux_* tags.

The distribution name is now `fastls`; imports remain `from fastls import Session`. After the first successful PyPI publication, install a native bundle with `python -m pip install --only-binary=:all: fastls`. When migrating from the legacy `fastls-ffi` distribution, uninstall it first: both distributions provide the same `fastls` import directory and must not coexist. Existing v0.1.2 GitHub assets retain their original names.

License terms are in `LICENSE`. Native-library third-party license texts are collected in `THIRD_PARTY_LICENSES.txt`.
