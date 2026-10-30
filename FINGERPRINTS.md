# Browser candidates and compact fingerprint parameters

## Optional H2 stream configuration

Python 0.5.0 / Node 0.14.0 add `http2.first_stream_id` and
`http2.stream_window_increment` inside a complete Spec. Zero or omission keeps
stream 1 and no initial per-stream update. Nonzero IDs must be odd and at most
2147483647; each physical connection starts at that ID and subsequent requests
advance by two. No dummy request is sent. The increment is receive credit sent
once after initial request HEADERS, including all CONTINUATION frames, never
again for trailers. Its sum with SETTINGS_INITIAL_WINDOW_SIZE must fit 31 bits.
More credit increases possible per-stream response buffering; choose it with
the concurrency budget in mind.

Nonzero fields require `http2.stream.v1` in `capabilities.required_features`,
alongside any other required features. SDKs negotiate native `http2_stream_v1`
before creating an owner or adapting a Spec. Older strict parsers/backends
reject unsupported configuration. Spec/Bundle schema and C ABI remain v1.
Compact JA3/H2 inputs do not encode these two fields and preserve the base
values. Bundle digest validation, pin, rollback, sync/async and fetch use the
same contract. Browser candidates remain explicit imports; installed selectors
are not silently replaced.

```python
value = base.to_dict()  # start with a complete, reviewed Spec
value["http2"].update(first_stream_id=3, stream_window_increment=12451840)
features = value["capabilities"]["required_features"]
if "http2.stream.v1" not in features:
    features.append("http2.stream.v1")
spec = ProfileSpec(value)
```

This is a configuration example, not sufficient to recreate a browser. The
Firefox Windows cold-request candidate additionally needs its observed TLS,
headers and SETTINGS. Replays compare raw initial frames, with stream ID checked
independently of HEADERS priority. They do not certify full browser semantics.
Protocol boundaries follow [stream identifiers](https://www.rfc-editor.org/rfc/rfc9113.html#section-5.1.1)
and [receive windows](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.9).

Python 0.4.0 / Node 0.13.0 add a shared offline converter. C ABI remains v1 with
the optional `fingerprint_adapter_v1` capability and owned-result function
`char *fastls_profile_adapt(char *json, size_t length)`. Release the returned
envelope with `fastls_free`; the input envelope is schema 1 with `base`, `ja3`
and `h2`. At least one compact parameter is required. No session or network I/O
is created. Old libraries reject use with `unsupported_capability`; ordinary
operations retain their existing behavior.

```python
import json
from fastls import ProfileSpec, Session, ProfileBundle

with open("base-spec.json", encoding="utf-8") as file:
    base = ProfileSpec(json.load(file))
spec = ProfileSpec.from_fingerprints(base,
    h2="2:0;4:131072;1:4096|100000|3:1:0:201|p,a,m,s")
with Session(profile_spec=spec) as session:
    response = session.get("https://example.com")
bundle = ProfileBundle.create(spec, source="reviewed local parameters")
bundle.export("candidate.json")
```

```js
const { ProfileSpec, Session } = require('@fastls/fastls');
async function main() {
const base = new ProfileSpec(require('./base-spec.json'));
const profileSpec = ProfileSpec.fromFingerprints(base, {
  h2: '2:0;4:131072;1:4096|100000|3:1:0:201|p,a,m,s'
});
const session = new Session({ profileSpec });
try { const response = await session.get('https://example.com'); }
finally { await session.closeAsync(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
```

Go uses `fastls.AdaptProfile(base, fastls.FingerprintParameters{JA3: text, H2: text})`.
The returned complete Spec goes through the same backend validation as ordinary
Spec input. Pass it through existing session/request `profile_spec` / `profileSpec`
options, including asynchronous and fetch entry points. Existing selector/Spec
conflicts and H3 restrictions still apply; no new request keyword is introduced.

JA3 accepts exactly five comma-separated fields with canonical decimal values:
`771,ciphers,extensions,groups,point-formats`, using hyphen-separated ordered lists.
Groups and points may be empty only when their extensions are absent; ciphers must be nonempty. Duplicate values,
explicit GREASE identifiers, unknown extensions, out-of-range fields and
incompatible key shares fail. Extensions must already exist in the complete base,
which supplies their parameters. JA3 does not encode signature algorithms, ALPN,
supported TLS versions, key shares, identities or HTTP defaults; those remain
explicit base values. The adapter fixes extension order and disables optional
extension omission, removes GREASE key-share slots when replacing the group list,
and does not fabricate missing extensions or new cryptographic implementations.
Cold PSK and conditional BoringSSL padding extensions cannot be guaranteed by this
compact input and are rejected explicitly.
It supports the TLS 1.2/1.3 backend's ClientHello legacy version 771.

H2 uses four pipe-separated fields:
`settings|connection-window-increment|priority-frames|pseudo-order`.
Settings are ordered `id:value` pairs separated by semicolons; an empty field
means an empty SETTINGS list. Window increment is 0..2147483647. Priority frames
use `stream:exclusive:dependency:weight`, separated by commas; `0` means none.
Exclusive is 0/1 and weight is the semantic range **1..256**, not the encoded
wire byte. Pseudo order is a permutation of `m,a,s,p`. Duplicate settings,
invalid protocol bounds, duplicate/self-dependent/zero priority streams and
unknown tokens fail. HEADERS-frame priority, which this compact form does not
encode, remains the base's explicit value. This is a defined supported compact
input format, not a guarantee of interoperability with every collector dialect.

Results use a `custom` identity variant and clear inherited capture verification
evidence. Omitted fields inherit the base; a matching JA3 or H2 string alone is
not proof of browser equivalence. Inputs are bounded (16 KiB per compact string,
256 KiB base Spec); failures leave the base and active ProfileStore unchanged.

The Python CLI can produce an ordinary pinned bundle:

```sh
python -m fastls profile adapt base-spec.json candidate.json --h2 '2:0;4:131072|100000|0|m,a,s,p' --source 'reviewed local parameters'
```

Browser collection is an explicit external maintenance operation, documented in
[imitate's manual Firefox updater](https://github.com/FastTLS/imitate/blob/v2/tools/firefox/README.md).
It discovers/downloads an official stable Windows x64 zh-CN browser, verifies the
official HTTPS archive checksum, collects 15 fresh cold samples with an independent
peer, compares full TLS/header/H2 observations, and replays supported Spec fields.
Its read-only manual workflow outputs a semantic diff, provenance, restrictions
and ProfileBundle v1 candidate. Review the producer/source and digest before
installing with ProfileStore; import, session creation and requests do not fetch
updates. Known unsupported H2 stream details stay explicit in the report. Other
platforms, ESR, browser H3 fingerprints, schedules and automatic PRs or
publishing remain outside this batch.

The subsequent [Chrome/Edge maintenance tools](https://github.com/FastTLS/imitate/blob/v2/tools/chromium/README.md)
share `python tools/browser_update.py --browser chrome` or `--browser edge`,
followed by explicit source paths and a new output directory. They cover Windows
x64 en-US minimized headed cold navigation, five independent samples in each of
three TLS/H1/H2 scenarios per product. Chrome uses the current pinned stable
official signed enterprise MSI; Edge additionally binds the official MSI hash.
Both verify installer/browser signatures and full versions, extract without
installing, and reject unsigned replacements. Chrome's recorded archive SHA256
is local evidence, not a vendor-published checksum. New maintenance tools do not
replace the data bundled in Python 0.4.0 / Node 0.13.0; candidates require explicit
review, import and pin. Randomized extension orders remain raw evidence alongside
membership/parameter comparison, not a distributional-equivalence claim.

Protocol references: [JA3 definition](https://github.com/salesforce/ja3#readme),
[HTTP/2 settings](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.5.2),
[priority parameters](https://www.rfc-editor.org/rfc/rfc7540.html#section-6.3).

Go consumers must pin the core version containing this API; the older stable
v0.1.2 does not export `AdaptProfile`. The Firefox data update is also pinned:

```sh
go get github.com/FastTLS/fastls@v0.1.3-0.20261030065220-4c07fe9e4059
go get github.com/FastTLS/imitate@v0.1.2-0.20261030065723-c75c3bf7679f
```

Python/Node distributions already include their native library and fixed data;
ordinary SDK installation does not require Go.
