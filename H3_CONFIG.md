# Controlled H3 configuration — Python 0.7.0 / Node 0.16.0

The SDKs use Go core v0.2.0, retaining the reviewed transport, profile and generator
pins. New optional capability `http3_config_v1` adds two ABI-v1 symbols and seven
owner creation fields. Transport JSON schema_version stays 1. Existing calls
remain compatible; older libraries reject new configuration before allocating
an owner or request. Both capability and the two symbols must be available.

```python
from fastls import Session, AsyncSession, HTTP3Config, http3_capabilities
config = HTTP3Config(initial_stream_receive_window=64 << 10,
    max_stream_receive_window=1 << 20,
    initial_connection_receive_window=128 << 10,
    max_connection_receive_window=2 << 20,
    max_idle_timeout_ms=30000, keep_alive_period_ms=0,
    max_response_header_bytes=64 << 10)
with Session(http_version="h3", http3_config=config) as session:
    print(dict(session.effective_http3_config()))
    response = session.get("https://your-h3-origin.example/")
    response.raise_for_status()
print(http3_capabilities()["fields"])
```

Use an actual approved H3 origin. AsyncSession/ClientSession accept the same
creation option. Module-level get/request accept http3_config together with
http_version="h3" and own their temporary session. Session requests cannot
replace the configuration. Request and response closure rules are unchanged.

```js
import { Session, HTTP3Config, http3Capabilities, fetch } from '@fastls/fastls';
const config = new HTTP3Config({ initialStreamReceiveWindow: 64 << 10,
  maxStreamReceiveWindow: 1 << 20, initialConnectionReceiveWindow: 128 << 10,
  maxConnectionReceiveWindow: 2 << 20, maxIdleTimeoutMs: 30000,
  keepAlivePeriodMs: 0, maxResponseHeaderBytes: 64 << 10 });
const session = new Session({ httpVersion: 'h3', http3Config: config });
try { console.log(session.effectiveHTTP3Config()); }
finally { await session.closeAsync(); }
console.log(http3Capabilities().fields);
// Standalone fetch creates and closes its own configured owner.
const response = await fetch('https://your-h3-origin.example/', {
  httpVersion: 'h3', http3Config: config,
});
await response.arrayBuffer();
```

HTTP3Config is an immutable typed value; plain dictionaries/objects are not
accepted in its place. Construction copies values, to_dict/toJSON returns a
copy, and inspection snapshots cannot mutate the owner. Python inspection is
an immutable mapping; Node snapshots are frozen. An auto owner returns None /
undefined. Querying a closed owner fails; neither discovery nor inspection
dials. Capabilities return fresh field metadata, not a negotiated peer result.
Node Session.fetch cannot replace http3Config; standalone fetch can create it.

## Fields, units, defaults and limits

Python and ABI names use snake_case; Node uses camelCase. All input numbers
are integers (Node safe integers); booleans, strings, NaN/infinity, fractions,
negative and oversized values fail. Input durations are **milliseconds**, not
Go nanoseconds or Python seconds. Native JSON rejects unknown/duplicate/case
aliases and null field values. Python keep_alive_period_ms=None and Node
undefined mean omission, so the wrappers never encode them as JSON null.

- initial_stream_receive_window / initialStreamReceiveWindow: zero/omitted
  selects 512 KiB; max_stream_receive_window / maxStreamReceiveWindow: 6 MiB.
- initial_connection_receive_window / initialConnectionReceiveWindow: zero/
  omitted selects 512 KiB; max_connection_receive_window /
  maxConnectionReceiveWindow: 15 MiB.
- All four windows accept 4096..67108864 bytes and each normalized initial
  value must be <= its maximum. These limit outstanding receive credit, not
  total bytes transferred or process-wide memory.
- max_idle_timeout_ms / maxIdleTimeoutMs: zero/omitted selects 30000; explicit
  100..86400000. Local post-handshake idle, subject to backend recovery and peer
  limits; a fixed-backend peer's parsed trace floors received idle to 5000ms.
- keep_alive_period_ms / keepAlivePeriodMs: omitted selects min(10000, local
  idle/2); explicit zero disables periodic keepalive; otherwise 10..idle/2.
  Normalized output may contain a half-millisecond default for odd idle input
  (101ms -> 50.5ms), preserving Go behavior instead of truncating it. Disabled
  periodic keepalive still permits QUIC recovery/PTO PINGs.
- max_response_header_bytes / maxResponseHeaderBytes: zero/omitted selects
  10485760; explicit 1024..16777216 bytes. Backend encoded HEADERS frame and
  decoded initial field-section limits apply, with per-field overhead.
  Response trailers have an encoded-frame check, without an equivalent total
  decoded trailer cap. This is not a body, decompression or process memory cap.

Only explicit h3 creation accepts non-default configuration. H1/H2 profile,
proxy, header order, request Trailer, duplex and WebSocket conflicts retain
existing errors. Configuration cannot change CA/SNI trust, immutable address
policy, approved numeric UDP peers or migration restrictions. SDK certificate
verification remains enabled. Keepalive never extends total/stage deadlines;
use bounded body reads and max_decoded_bytes independently.

## C ABI and validation

Create an owner with fastls_transport_create_with_options and
{"schema_version":1,"http_version":"h3","http3_config":{...}}. The new
fastls_http3_capabilities(void) returns discovery; fastls_transport_http3_config
(uint64_t handle) returns a fixed H3 owner's normalized values or null for an
auto owner. Zero/closed handles reject. Both return the normal owned JSON
envelope and must be released with fastls_free. Ordinary fastls_start and
WebSocket input cannot inject this creation option. No Go pointer or arbitrary
QUIC/TLS/dial callback crosses the ABI.

Five-platform source and installed Python/Node suites cover strict validation,
old capability/symbol guards before allocation, fresh snapshots, actual received
QUIC windows/idle, 2 MiB delivery, encoded header/trailer rejection, trusted
policy combinations, cancellation/siblings, async and standalone calls. The
fixed Go v0.2.0 report provides backend window-growth/idle/keepalive proofs;
SDK tests establish the new mapping and lifecycle, not a second independent
QUIC implementation. Linux race and the existing five-minute resource budgets
include configured H3, header rejection and recovery alongside defaults/H1/H2.

SDK adoption includes Go's graceful cleanup of connections evicted after a
header error, preserving active sibling bodies. It does not add browser H3
fingerprints, arbitrary SETTINGS/QPACK/order, 0-RTT, UDP proxies, new platforms,
or these fields to Fetch/RPC/MITM. Services retain their published core pins.
