import fastls = require('@fastls/fastls');

const configured = { profileSpec: new fastls.ProfileSpec({ schema_version: 1 }), httpVersion: 'auto' } satisfies fastls.SessionOptions;
void configured;

const options: fastls.SessionOptions = { timeoutMs: 1000, cookies: false, notifications: 'required', proxy: 'https://127.0.0.1:8080', proxyCaFile: '/path/to/proxy-ca.pem' };
function consume(session: fastls.Session): Promise<fastls.Response> {
  return session.get('https://example.com', { headers: { X: 'value' }, proxy: null });
}
void options;
void consume;
async function socket(session: fastls.Session) { const ws: fastls.WebSocket = await session.websocket('wss://example.com'); await ws.ping(Buffer.alloc(0)); await ws.closeAsync(); }
void socket;
async function encoded(session: fastls.Session) {
  const files: fastls.MultipartFiles = [['file', ['a.bin', Buffer.from('content')]]];
  const response = await session.post('https://example.com', { params: new Map([['tag', ['a', 'b']]]), form: { value: '中文' }, files });
  response.raiseForStatus();
  try { response.raiseForStatus(); } catch (error) { if (error instanceof fastls.FastlsHTTPError) { const status: number = error.status; const owner: fastls.Response | fastls.StreamResponse = error.response; void status; void owner; } }
}
void encoded;
function manual(session: fastls.Session): fastls.DuplexExchange {
  return session.duplex('POST', 'https://example.com', { timeoutMs: { total: 1000, write: 200 } });
}
void manual;
async function events(session: fastls.Session): Promise<fastls.SSEStream> {
  const options: fastls.SSEOptions = { maxEventBytes: 1024, lastEventId: '41' };
  const stream = await session.sse('GET', 'https://example.com/events', options);
  for await (const event of stream) { const typed: fastls.ServerSentEvent = event; void typed; }
  return stream;
}
void events;

const compactSpec = fastls.ProfileSpec.fromFingerprints(new fastls.ProfileSpec({ schema_version: 1 }), { h2: '2:0|0|0|m,a,s,p' });
void compactSpec;
const streamConfig: fastls.HTTP2StreamConfig = { first_stream_id: 3, stream_window_increment: 12451840 };
void streamConfig;
// @ts-expect-error Compact parameters require strings.
fastls.ProfileSpec.fromFingerprints(new fastls.ProfileSpec({}), { ja3: 771 });
