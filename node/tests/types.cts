import fastls = require('@fasttls/fastls');

const options: fastls.SessionOptions = { timeoutMs: 1000, cookies: false, notifications: 'required', proxy: 'https://127.0.0.1:8080', proxyCaFile: '/path/to/proxy-ca.pem' };
function consume(session: fastls.Session): Promise<fastls.Response> {
  return session.get('https://example.com', { headers: { X: 'value' }, proxy: null });
}
void options;
void consume;
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
