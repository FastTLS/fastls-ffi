# Address policy and complete profile.Spec: S3 design decision

Status: design only, based on FFI `3aaa273`, core `cb551d8` and transport
`d5afcd1`. Neither capability is implemented or advertised by this commit.
Existing Python/Node options, C ABI symbols and package versions are unchanged.
Service audit events are a separate feature and do not enable SDK address policy.

## Verified starting point

`internal/bridge/bridge.go` accepts a named `Input.Profile` string. Its
`CreateTransport()` creates an SDK-owned transport context without native
application Cookie state. Python and Node create this owner with
`fastls_transport_create`; they retain Cookie and redirect decisions in the SDK.
The owner holds pools of core Sessions. `cmd/fastls-shared/main.go` advertises
`transport_context_v1`, decoding, timeouts and other existing features, but no
address-policy or complete-Spec capability. Existing `fastls_session_create`
also supports a legacy native Cookie owner; the new design must not silently
replace the current SDK transport with that legacy path.

Core policy-capable builds provide `WithAddressPolicyJSON([]byte)` and origin
checks. Published default service dependencies can lack these methods. Core
`profile.Spec` already has schema version 1, ordered TLS/H2/header fields and
explicit hexadecimal TLS identifiers. The bridge does not currently expose
that full object. New ABI work must use the real schema and capabilities,
rather than treating an arbitrary JSON object as a named browser selector. Current raw request JSON does not implement these options; unknown fields must not be mistaken for enforced policy. Neither passing an extra raw ABI field nor an unrecognized JavaScript property enables a capability. Future adapters must check the feature and new symbol before sending any configuration.

## Decision A: trusted immutable transport policy, implemented first

Add a new optional transport constructor, proposed name
`fastls_transport_create_with_options(json, length)`. Keep all existing symbols
and ABI version 1 intact. Its proposed feature marker is
`transport_address_policy_v1`; the name is a proposal, not a feature currently
returned by `fastls_features`.

The constructor accepts a schema-versioned trusted configuration containing an
address-policy object. Decode at most 1 MiB, reject unknown/duplicate fields,
trailing JSON, excessive nesting (maximum 32) and unsupported schema versions.
Compile an immutable core policy once before exposing a handle or making any
network request. A requested policy on a core without the required methods
must fail construction. Do not advertise the feature merely because the FFI
wrapper recognizes the option.

The policy belongs to the transport owner and every pool created under it.
Every hop of an SDK redirect remains bound to that owner, including changed
origins; the SDK's URL checks do not replace native DNS/address/origin binding.
Never accept policy replacement or relaxation from a request DTO, request
header, profile.Spec, redirect, proxy setting or another owner handle. Handle 0
and ad hoc requests cannot bypass a requested session policy. A caller can
choose a different unrestricted session only if its application explicitly
grants that authority; this API is not an in-process sandbox for hostile code.

Retain core's unsupported-combination rules. In particular, policy plus an
unsupported proxy or explicit H3 fails before DNS/dial; never silently disable
policy, choose direct networking, or downgrade the requested protocol. A
default session without policy keeps current behavior. Policy compilation
errors must not expose partially allocated handles or leave watcher/pool state.

Python and Node should accept policy only as a trusted Session configuration.
They must check both the feature marker and optional native symbol before
serializing the option. Missing support raises the existing language error
type with the same stable capability error code; it must not ignore the option
or retry using an old constructor. Native error envelopes should distinguish
`unsupported_capability`, `invalid_configuration` and `address_denied` without
including userinfo, query strings, authorization or raw policy contents.

## Decision B: complete Spec is a separate transport-identity feature

Proposed marker: `profile_spec_v1`, independently discovered from policy.
Address policy can ship without this capability. Do not overload policy JSON
with fingerprint fields or attach host permissions to a browser profile.

Accept a named selector or one complete `profile.Spec` snapshot, never both.
Start with explicit typed Python/Node Spec wrappers; do not reinterpret every
mapping as a Spec. Keep the existing selector default and precedence when the
new option is absent. A proposed native `profile_spec` request field replaces
`profile` for that request; the SDK must omit its default selector in that case.
Validate the whole snapshot before choosing/creating a pool or starting I/O.

Limit the complete Spec to 256 KiB and nesting depth 32, reject duplicate and
unknown fields at every level, require schema version 1 and bounded list/string
sizes, and validate with the core compiler. A present empty SETTINGS list must
remain explicitly empty; absent, null, empty and zero cannot be collapsed by
SDK defaults or `omitempty`. Zero SETTINGS values and optional empty/zero
fields need presence-aware decoding before mapping to core types. Ordered
arrays retain their wire order. TLS identifiers use core's `0x0000` encoding;
out-of-range values and unsupported TLS/H2 features fail rather than truncate.

Clone accepted input so mutations to a caller object cannot change an active
pool. Include the compiled effective Spec, proxy, CA and relevant transport
options in pool identity. Preserve current per-owner isolation and include
policy identity in the owner boundary; never reuse another session's pool on a
hash match alone. Export effective normalized configuration and capability
limitations without secret fields. Advertised-only versus implemented TLS
features must remain distinguishable; a matching JA3 hash is not validation of
the complete configuration or a browser identity.

Core H3 support does not make TLS/H2 Spec fields H3 settings. Initially reject
unsupported H3 combinations explicitly. Browser catalog discovery and updates
stay in imitate, with fixed selectors and evidence; importing the SDK must not
download profiles or silently substitute a browser.

## Implementation and acceptance sequence

1. Freeze constructor and feature/error names against Python/Node/native
   adapters. Implement policy parsing and owner binding on a reviewed core;
   add missing-core and missing-symbol tests before enabling the feature flag.
2. Verify allowed/denied DNS results, DNS rebinding, redirects, origin binding,
   unsupported proxies/H3, concurrent close/cancel, handle exhaustion and two
   owners with different policies. Every denied/unsupported case proves no
   network side effect; every successful case checks the actual peer.
3. Add Python and Node parity tests for trusted session-only input, legacy
   native libraries, errors and unchanged default Cookie/redirect semantics.
   Run packaged libraries in isolated installs, not only source adapters.
4. Implement full Spec parsing/compiler/pool identity independently. Cover exact
   size/depth/list boundaries, duplicate/unknown fields, omitted/null/empty/zero,
   input mutation and cross-pool identity. Capture ClientHello and H2 bytes with
   an independent peer and compare supported declarations with actual behavior.
5. Run the native platform matrix and common ABI/Python/Node gates for each
   capability separately before advertising or releasing it. Update docs and
   dependency locks only for the implementation actually certified.

S3 deliberately ships these decisions without adding unverified SDK options.
CPU performance certification, the known cleartext CONNECT/MITM compatibility
gap, and the MITM raw dialer's profile-to-TLS adaptation remain separate work.
