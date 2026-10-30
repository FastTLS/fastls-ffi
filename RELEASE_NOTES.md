# Python 0.4.0 / Node 0.13.0

This minor release adds a shared Go-backed JA3/H2 converter that returns an
explicit complete Spec. Python uses `ProfileSpec.from_fingerprints`, Node uses
`ProfileSpec.fromFingerprints`, and Go uses `AdaptProfile`. Missing compact fields
inherit a caller-supplied base; unknown, conflicting and unsupported inputs fail
before networking. Custom results clear inherited browser verification evidence.
The Python CLI can export the converted Spec as a pinned ProfileBundle.

C ABI stays v1 with the optional `fingerprint_adapter_v1` capability. Older
libraries explicitly reject conversion; existing session, async, fetch, profile
store, authentication, retry, cancellation and protocol contracts are preserved.

The generator now has a manual, read-only Firefox stable Windows x64 zh-CN
discovery/download/capture/replay workflow. It verifies the official HTTPS
SHA512SUMS, records source identities and full observations, and writes a semantic
diff plus offline candidate without changing active configuration. Firefox 159.0
Windows is updated from 15 independent fresh cold browser samples. Six selected
TLS/header replay comparisons match; two H2 comparisons retain known stream ID
and stream-window differences. This is not full browser equivalence.

Review source identity, digest and limitations before manually importing and
pinning a candidate. Hashes and HTTPS checksums do not independently authenticate
the producer. No implicit network updates, schedules, PR creation, automatic
activation or publishing are enabled by the update workflow. Other browser
families/platforms, ESR, H3 fingerprints, UDP proxies, new platforms and WebSocket
compression remain separate work.

Runtime inputs use core `v0.1.3-0.20261030065220-4c07fe9e4059` and imitate
`v0.1.2-0.20261030065723-c75c3bf7679f`; ftransport v0.1.1 and profile v0.1.0 are
unchanged. No service is republished. Validation uses five source/installed
platforms, independent wire observations, Linux race and a short resource
campaign. No full performance certification or hour-long observation is repeated.
Official publication uses checked artifacts and OIDC Trusted Publishing.

[中文指南](https://fasttls.github.io/reference/browser-profiles) ·
[English contract](https://fasttls.github.io/en/reference/browser-profiles)


## Previous release: Python 0.3.0 / Node 0.12.0

This minor release adds Python Basic/Bearer authentication and request-local
Cookie mappings, opt-in business retries in Python sync/async and Node
Session/fetch, and pinned offline profile bundles with atomic activation and
rollback. New retries are disabled by default and share a bounded deadline with
redirects. Non-idempotent methods require explicit authorization, one-shot bodies
are not replayed, and response-body failures never trigger another request.

Structured native network/certificate errors remain within ABI v1. Unsupported
or unclassified errors are conservative, including older native libraries.
Existing H3, address policy and full Spec capabilities remain available with
their documented limits. No service or dependency module is republished.

The offline tools validate exact pinned Spec bytes and native backend support
without network access. Checksums do not authenticate producers. Browser online
capture/update, QUIC fingerprints, UDP proxies and new platforms remain future
work. See [English contract](RELIABILITY.md) and [中文指南](RELIABILITY.zh-CN.md).

The release candidate uses the minor validation tier: existing five platforms,
source/installed SDK interfaces, Linux race and one short resource campaign.
Full performance certification and hour-long observation are not repeated.
Publication uses original checked artifacts and OIDC Trusted Publishing.

## Previous release: Python 0.2.0 / Node 0.11.0

This minor release adds trusted Session address policies, immutable full profile
Spec snapshots, and explicit HTTP/3 through the C ABI, Python sync/async APIs,
and Node Session/fetch APIs. Each capability is discovered independently.

ABI v1 and existing default requests remain compatible. Older native libraries
reject unsupported new options before sending requests. Python preserves existing
WebSocket close codes alongside the new structured native error codes.

HTTP/3 currently requires direct HTTPS and never falls back to TCP. Address
policies, proxies, H1/H2 fingerprint settings, request trailers, WebSocket and H2
duplex cannot be combined with explicit H3. Browser QUIC fingerprints, UDP proxy
support and automatic Alt-Svc discovery remain future work. Generic retries
were added by the release described above.

The reviewed public core dependency is
`v0.1.3-0.20261029044750-32699153caa1`. No unchanged service is republished.

Release validation retains five-platform source and installed-package checks.
A minor tag can reuse Linux race/resource checks after verifying immutable source
inputs, the workflow identity and every required source job. A pinned ancestor
may supply individual successful resource jobs only when the entire source diff
is limited to the reviewed release-test infrastructure and release notes; an
unrelated failed job remains failed. Missing evidence keeps the full test plan.
Node packages consume the separately verified same-commit main artifacts instead
of being rebuilt by the Python tag. Tag-native bytes are separately built,
identified and installed; prior resource observations do not certify their exact
bytes. Major releases and explicit resource requests retain their checks.

See [capabilities and limitations](CAPABILITIES.md) for the complete input contract.

npm publication uses GitHub Actions OIDC in the `npm` environment. Before any
upload, all six packages must accept live package-scoped OIDC exchanges for
`npm-publish.yml` on main. Tokens stay in memory and are excluded from evidence.
Local npm login is unnecessary for this effective authorization check.
