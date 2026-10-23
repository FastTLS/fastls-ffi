import fastls = require('@fasttls/fastls');

const options: fastls.SessionOptions = { timeoutMs: 1000, cookies: false };
function consume(session: fastls.Session): Promise<fastls.Response> {
  return session.get('https://example.com', { headers: { X: 'value' } });
}
void options;
void consume;
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
