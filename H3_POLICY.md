# Explicit H3 with a trusted address policy

Python 0.6.0 / Node 0.15.0 add the native `http3_address_policy_v1` capability.
Supporting H3 and address policies individually does not imply support for their
combination. Both SDKs check the combined capability before creating an owner
or starting a per-request H3 selection on a policy owner. C ABI, transport JSON,
Spec and Bundle formats remain v1.

```python
from fastls import Session

policy = {"allow_schemes": ["https"], "allow_hosts": ["api.example.com"],
          "allow_ports": [443], "deny_cidrs": ["127.0.0.0/8", "::1/128"]}
with Session(http_version="h3", address_policy=policy) as session:
    response = session.get("https://api.example.com/")
    response.raise_for_status()
```

```js
import { Session } from '@fastls/fastls';
const session = new Session({ httpVersion: 'h3', addressPolicy: {
  allow_schemes: ['https'], allow_hosts: ['api.example.com'], allow_ports: [443],
  deny_cidrs: ['127.0.0.0/8', '::1/128'],
} });
try { (await session.get('https://api.example.com/')).raiseForStatus(); }
finally { await session.closeAsync(); }
```

The example host must be replaced with an approved H3 endpoint. Empty allow
lists impose no restriction; this example deny list is not complete SSRF
protection. Rules are copied into an immutable trusted Session owner, never
accepted from ordinary request JSON. Python async sessions and standalone Node
fetch use the same contract. A policy owner may select H3 per request; it cannot
replace its policy. A forced-H3 owner cannot downgrade to auto.

Every new physical QUIC connection resolves afresh, validates every answer and
connects only to numeric IP/port values from that snapshot. Any forbidden answer
rejects the attempt before opening a socket. DNS and all connection attempts
share a 30-second ceiling, subject to shorter request/phase deadlines. Origin,
HTTP authority, SNI and certificate verification retain the original host.
Verification remains enabled by default in both SDKs.

A connected UDP socket fixes the remote peer in the kernel; the PacketConn also
rejects alternate destinations with `peer_change_unsupported`. The policy path
does not expose raw/OOB/batch socket access or enable ECN/GSO, migration,
preferred-address switching, or 0-RTT. An existing approved connection can be
reused; DNS changes do not revoke it retroactively. New connections check a new
snapshot. Each redirect and cached dispatch still checks the logical target.
Handshake failure, cancellation, connection end and owner closure release the
owned sockets. Existing active-body idle-close rules remain in force.

Policy rejections surface as `FastlsError.code == "address_denied"`; missing
combined capability as `unsupported_capability`. Invalid schemas and forbidden
request policy injection are rejected. Proxies, H1/H2 profiles, q fingerprint
selectors, request trailers, WebSockets and H2 duplex remain incompatible with
this H3 policy path. No TCP fallback, automatic Alt-Svc, browser H3 fingerprint,
new platform or service-protocol support is added. Unrestricted H3 is unchanged.

See the [Go policy contract](https://github.com/FastTLS/fastls/blob/main/ADDRESS_POLICY.md)
and [中文说明](H3_POLICY.zh-CN.md). Release assets and their source manifests
determine publication and platform validation status.

Unspecified destination addresses (`0.0.0.0`, `::` and mapped zero) are rejected before dialing, even under broad allow rules: Go can interpret them as the local system. This shared policy check also protects H1/H2.
