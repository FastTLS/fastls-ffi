> Python 0.3.0 / Node 0.12.0 保留本页能力，并增加 [认证、重试与离线配置](RELIABILITY.zh-CN.md)。
> Full Spec / address policy / explicit H3 first shipped in Python 0.2.0 / Node 0.11.0.
> The older baseline mentioned below records the original capability introduction.

# Native transport capabilities (development candidate)

These APIs are implemented in this source candidate. They are not enabled in
the existing Python 0.1.5 / Node 0.10.2 release libraries. The ABI stays at 1;
existing symbols and default requests remain compatible. Check both the
specific feature and optional constructor symbol before sending new options.

- Constructor: `fastls_transport_create_with_options(const char *, size_t)`.
- Constructor feature: `transport_options_v1`.
- Independent features: `transport_address_policy_v1`, `profile_spec_v1`,
  `http3_v1`. One feature never implies the other two.
- JSON result memory and handles follow [ABI.md](ABI.md). Constructor input
  requires `schema_version: 1`, at most 1 MiB, maximum nesting depth 32,
  no duplicate/unknown fields or differently cased aliases. No handle is
  allocated until validation succeeds.

## Trusted owner configuration

Optional constructor fields are `address_policy`, `profile_spec`, `profile`,
and `http_version` (`auto` or `h3`). Policy keys are `allow_schemes`,
`allow_hosts`, `allow_ports`, `allow_cidrs`, `deny_cidrs`; these are exact native
schema names in both SDKs. Policy lists cannot be null. Empty allow lists
impose no restriction in that dimension; deny CIDRs win. An empty policy is
not a private-network deny preset. This API is not a sandbox for hostile code
that can create its own unrestricted owner.

The compiled policy is immutable and belongs to all pools of that owner.
Each SDK redirect retains the owner; native checks bind DNS results, actual
peer and origin. No request policy replacement exists. Unsupported proxies,
H3 and WebSocket combinations fail explicitly. Syntax/combination prechecks
perform no DNS/dial; a DNS-dependent address decision may resolve but never
connects to a denied peer. Default owners retain existing behavior.

## Complete profile.Spec

Python `ProfileSpec(mapping)` and Node `new ProfileSpec(object)` snapshot the
complete core schema-v1 JSON. `to_dict()` / `toJSON()` return fresh copies.
Use the offline Go profile export as input; browser updates are not performed
by import or requests. Native input is limited to 256 KiB, strings to 16 KiB,
and each object/array to 4096 entries, in addition to core semantic/backend
validation. Errors are redacted rather than echoing profile input.

The complete `http2.settings` list is required: omission and null are rejected,
`[]` is explicitly empty, and individual zero setting values remain zero.
Other optional slices/pointers follow the core schema; no default merger
turns their empty/zero values into selector defaults. Raw accepted presence
and ordered data remain in pool identity. TLS identifiers use `0x0000`
strings; byte-vector fields retain the core's JSON encoding. Unsupported
features fail before network I/O.

Named `profile` and `profile_spec` cannot both select the same request.
Session-level Spec provides the immutable default; request-level Spec may
select another pool, but cannot change policy or downgrade forced H3.
Per-owner pool identity includes Spec snapshot, protocol, CA and proxy.
Different owners never share pools. The existing 16-configuration owner limit
and 64-owner engine limit remain in force. This is TLS/H1/H2 configuration,
not a browser QUIC/H3 fingerprint. Spec-configured WebSockets are rejected in
this candidate instead of silently using a selector.

## Explicit HTTP/3

`http_version="h3"` / `httpVersion: "h3"` requires direct HTTPS over QUIC
on every hop, without TCP fallback. Implicit SDK browser defaults are omitted;
explicit conflicting selectors, Spec, proxies, policies, request trailers,
WebSocket and H2 duplex are rejected. A forced-H3 owner cannot be changed to
auto per request. Auto retains existing H1/H2 behavior without Alt-Svc discovery.
Cancellation, body closure and Session closure follow existing SDK ownership.

Python sync/async Session and request helpers, Node Session and fetch support
the new selection. Standalone Node fetch may create a policy owner through
`addressPolicy`; `session.fetch()` cannot replace its Session policy. Cookie
and redirect state remain in the SDK. Browser QUIC fingerprinting, UDP proxies,
automatic retries and new platforms are outside this batch.

## Errors and evidence

Native envelopes and request metadata carry `invalid_configuration`,
`unsupported_capability`, or `address_denied` as appropriate. Both SDKs expose
these as `FastlsError.code`; existing timeout/decode exception types remain.
Local language shape/type errors may use TypeError/ValueError. Missing native
features or symbols produce `unsupported_capability`, without fallback.

`tests/test_capabilities.py` and `node/tests/parity-capabilities.mjs` cover real
ABI calls, owner/redirect binding, input mutation, empty/zero settings, actual
ClientHello and H2 frame/header order, pool isolation and H3 lifecycle.
`wire_peer.go` uses Go TLS plus raw H2/HPACK frames, independently of the
client transport. The normal H3 fixture shares the QUIC backend; separate
aioquic evidence is required before claiming independent-stack interoperability.
Legacy-library checks require an actual explicitly selected old binary.
Build success alone does not certify packages or all platforms.
