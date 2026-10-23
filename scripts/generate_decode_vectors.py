"""Optional maintainer tool; CI consumes its checked-in vectors without these packages."""
import base64
import gzip
import json
from pathlib import Path
import zlib

import brotli
import zstandard

payload = b"fastls independent compression fixture\x00\x80\xff" + bytes(range(256)) * 32
gzip_data = gzip.compress(payload, mtime=0)
raw = zlib.compressobj(wbits=-15)
cases = {
    "gzip": gzip_data,
    "deflate": zlib.compress(payload),
    "raw-deflate": raw.compress(payload) + raw.flush(),
    "br": brotli.compress(payload),
    "zstd": zstandard.ZstdCompressor(write_checksum=True).compress(payload),
    "gzip, br": brotli.compress(gzip_data),
    "concatenated-gzip": gzip.compress(payload[:100], mtime=0) + gzip.compress(payload[100:], mtime=0),
}
result = {"producer": {"brotli": brotli.__version__, "zstandard": zstandard.__version__, "zlib": zlib.ZLIB_VERSION},
          "payload_base64": base64.b64encode(payload).decode(),
          "cases": {name: base64.b64encode(data).decode() for name, data in cases.items()}}
target = Path(__file__).resolve().parents[1] / "tests/fixtures/decode-vectors.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(target)
