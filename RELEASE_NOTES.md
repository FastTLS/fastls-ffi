# Python 0.2.0 / Node 0.11.0

This minor release adds trusted Session address policies, immutable full profile
Spec snapshots, and explicit HTTP/3 through the C ABI, Python sync/async APIs,
and Node Session/fetch APIs. Each capability is discovered independently.

ABI v1 and existing default requests remain compatible. Older native libraries
reject unsupported new options before sending requests. Python preserves existing
WebSocket close codes alongside the new structured native error codes.

HTTP/3 currently requires direct HTTPS and never falls back to TCP. Address
policies, proxies, H1/H2 fingerprint settings, request trailers, WebSocket and H2
duplex cannot be combined with explicit H3. Browser QUIC fingerprints, UDP proxy
support, automatic Alt-Svc discovery and generic retries are future work.

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
