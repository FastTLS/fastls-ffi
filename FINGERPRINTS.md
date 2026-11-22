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
features = list(value["capabilities"].get("required_features") or [])
if "http2.stream.v1" not in features:
    features.append("http2.stream.v1")
value["capabilities"]["required_features"] = features
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

## Daily maintenance and released data (2026-11-22)

`imitate v0.1.3` includes Windows profiles based on Chrome **158.0.8127.37**, Edge **158.0.4370.33** and Firefox **160.0.1**, at data revision `browser-8ee4c22c6b779cbb`. Select `chrome/158/windows`, `edge/158/windows` or `firefox/160/windows` explicitly. Existing default selectors remain compatible and do not necessarily select these measured versions. The three services accept explicit `chrome158`, `edge158` and `firefox160` names.

The [daily maintenance workflow](https://github.com/FastTLS/imitate/blob/v2/.github/workflows/browser-maintenance.yml) checks official stable versions at **02:17 UTC / 10:17 China time**. Each product collects five fresh cold processes in each of three TLS/H1/H2 scenarios. An independent adoption job checks raw observations, complete Specs and six online/offline replays before a normal fast-forward data commit to the default branch. Collection is read-only; adoption has limited contents-write permission. Failures retain evidence; tags are not moved and branch protections are respected. Adoption can recover from a completed trusted run. Unchanged versions and inputs reuse verified evidence while still exporting a snapshot.

Evidence covers only Windows x64 cold navigation: Chrome en-US headed/minimized, Edge en-US with its window offscreen and software graphics, and Firefox zh-CN headless. Chromium extension membership/parameters do not establish randomized-order distribution equivalence. Resumption, ECH, other systems, H3 and whole-browser behavior remain unverified; ML-DSA remains advertised-only. Capture verifies browser signatures, full versions and applicable official checksums. Chrome's local SHA256 is not a vendor-published checksum.

**Source adoption and consumer upgrades are separate.** Daily maintenance does not publish Go modules, Python/npm packages or service binaries, and import/session/request operations never download updates. Python **0.7.3**, Node **0.16.3**, Fetch **v0.3.2**, RPC **v0.3.3**, MITM **v0.1.4** and fcurl **v0.2.3** pin this module. Older installations need an upgrade or an explicit, digest-reviewed Bundle import/pin/rollback through the existing `ProfileStore`. Raw collection reports and offline snapshots are available in [Actions](https://github.com/FastTLS/imitate/actions/workflows/browser-maintenance.yml); the fixed module source is [v0.1.3](https://github.com/FastTLS/imitate/releases/tag/v0.1.3). A Bundle is a fixed snapshot, not an automatic future-data subscription.

```sh
go get github.com/FastTLS/fastls@v0.2.1
go get github.com/FastTLS/imitate@v0.1.3
go run github.com/FastTLS/imitate/cmd/imitate-export@v0.1.3 -output fingerprints.json
```

The complete Go catalog export is loaded by a Go offline Provider. Python/Node ProfileStore accepts the per-product ProfileBundle v1 candidate from Actions, rather than the complete catalog snapshot.

Python/Node distributions include their native library and fixed data; normal installation does not require Go. Manual capture remains available for [Firefox](https://github.com/FastTLS/imitate/blob/v2/tools/firefox/README.md), [Chrome/Edge](https://github.com/FastTLS/imitate/blob/v2/tools/chromium/README.md), with [maintenance and recovery instructions](https://github.com/FastTLS/imitate/blob/v2/tools/maintenance/README.md).
