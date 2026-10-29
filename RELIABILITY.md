# SDK reliability and offline profile contract

This batch preserves ABI v1. New business retries are disabled by default. The
SDK coordinates status/error retries and redirects under one total deadline;
the native transports retain their existing protocol-level recovery. A retry
count limits additional SDK attempts, not transport packets or requests the
remote application can prove were processed. There is no exactly-once claim.

Python accepts `BasicAuth`, `BearerAuth`, and `(username, password)` through
session/request `auth`. Explicit Authorization headers win. Request `auth=None`
disables inherited auth. Credentials are prepared once and stripped on origin
changes; they cannot reappear on later redirect hops. Basic encodes UTF-8.
`request_cookies` is a request-local mapping scoped to the initial origin and
root path, including retries and same-origin redirects. It overrides matching
session cookie names without modifying the jar. Explicit Cookie headers win.
Server Set-Cookie still updates the session normally. `Cookies.update(mapping,
url=...)` validates the entire merge before changing the jar. The existing
session and one-shot `cookies: bool` switch is unchanged.
These auth defaults apply to the ordinary HTTP/SSE request entry points;
WebSocket and manual duplex retain their existing explicit-header interfaces.

Python `RetryPolicy` uses seconds; Node `RetryPolicy` uses milliseconds, matching
their existing timeout APIs. Defaults: 2 additional attempts, GET/HEAD/OPTIONS/
PUT/DELETE, statuses 429/502/503/504, connection failures and pre-header
connect/headers timeouts, base delay 0.1s, maximum delay 10s, full jitter and
Retry-After enabled. Non-idempotent methods require `allow_non_idempotent` /
`allowNonIdempotent`. TLS/certificate, policy, cancellation, decode,
configuration and unclassified errors never qualify. Error retries use the
structured `network_error` native code; older libraries remain conservative.
Retry-After is an integer number of seconds or an HTTP date, capped by the
maximum delay. No response-body read failure is retried. A response returned to
the caller cannot be retried. Status attempts close the discarded response.

Bytes, strings and empty bodies are replayable regardless of streaming size.
Python `body_factory` and Node `bodyFactory` explicitly construct a new body per
attempt/hop; they cannot be combined with data/body/json/form/files. Node's
existing upload function remains an upload source, not a replay declaration.
One-shot files/iterators/multipart are not automatically replayed. 307/308 use
the same rule. A redirect converting to GET clears the factory and body.
Callers keep mutable byte sources unchanged until completion; an explicit body
factory is responsible for producing an independent source for every attempt.
Retry counts cover the whole redirect chain. Duplex requests reject retry and
body factory options. Session close interrupts pending backoff; Python async
task cancellation and Node AbortSignal also interrupt it. Python synchronous
code uses session close for external cancellation.

Offline profile bundles carry a versioned envelope, exact UTF-8 Spec text,
SHA-256, profile ID and caller-supplied provenance. A checksum detects corruption;
it does not authenticate a producer. Loading requires a caller-pinned SHA-256.
The native compiler validates the complete Spec before a store activation.
Local stores use content-addressed immutable bundles and one atomically replaced
state file for current/previous pins. A failed import cannot replace an active
configuration. No operation downloads configuration or browser binaries.
Only trusted local directories are supported; concurrent writers must be
serialized by the caller. Browser discovery/capture and signed distribution
remain separate work.

## Examples

```python
from fastls import BasicAuth, RetryPolicy, Session

with Session(auth=BasicAuth("user", "password"),
             retry=RetryPolicy(max_retries=2), timeout=10) as session:
    response = session.get("https://example.com", request_cookies={"language": "en"})
    session.put("https://example.com/upload", body_factory=lambda: iter([b"fresh"]))
```

```typescript
import { Session, RetryPolicy, ProfileStore } from '@fastls/fastls';

const retry = new RetryPolicy({ maxRetries: 2, maxDelayMs: 2000 });
const session = new Session({ retry, timeoutMs: 10000 });
try { await session.fetch('https://example.com', { retry }); }
finally { await session.closeAsync(); }
// A profile store is optional and performs no downloads.
const bundle = new ProfileStore('profiles').load();
```

`python -m fastls profile export spec.json bundle.json --source local-capture`
prints the Spec SHA-256. Use that exact pin with `profile validate bundle.json
--sha256 PIN` or `profile import bundle.json --sha256 PIN --store profiles`.
`profile pin PIN --store profiles` activates a previously imported pin;
`profile rollback --store profiles` switches to the previous pin.
The shared store can be loaded using Python `ProfileStore("profiles").load()`
or Node `new ProfileStore("profiles").load()`, then passed as `profile_spec` /
`profileSpec` using its `spec` property.
