# fastls-ffi Python SDK

The source distribution contains the Python SDK but does not compile or download the native Fastls library. Build and install the sdist with Python 3.10 or newer, then set `FASTLS_LIBRARY` to a compatible ABI v1 library or pass its absolute path to `Session(library=...)`.

Platform wheels contain the matching native library and need no Go or C compiler at installation time. Linux wheels are currently certified only for glibc 2.39 or newer on Ubuntu 24.04-compatible systems; they are not declared manylinux or musllinux compatible.

License terms are in `LICENSE`. Native-library third-party license texts are collected in `THIRD_PARTY_LICENSES.txt`.
