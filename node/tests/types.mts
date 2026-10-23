import { Session, ServerSentEvent, type SSEOptions, type SSEStream, type RequestOptions, type Response, type StreamResponse } from '@fasttls/fastls';

async function consume(session: Session): Promise<void> {
  const initialized: Session = await Session.open({ notifications: 'required' });
  await initialized.ready(AbortSignal.timeout(3000));
  const mode: 'async-events' | 'poll' = initialized.notificationMode;
  void mode;
  await initialized.closeAsync();
  // @ts-expect-error Notification modes are explicit strings.
  new Session({ notifications: true });
  const options: RequestOptions = { body: Buffer.from([0, 255]), signal: AbortSignal.timeout(1000) };
  const response: Response = await session.post('https://example.com', options);
  const value: string = response.json<{ value: string }>().value;
  const stream: StreamResponse = await session.stream('GET', 'https://example.com');
  for await (const chunk of stream.body) { const bytes: Buffer = chunk; void bytes; }
  session.cookies.set('value', value, { url: 'https://example.com', httpOnly: true });
  const decoded: boolean = response.decoded;
  const wire: string | undefined = response.wireHeaders.get('content-encoding');
  session.post('https://example.com', {
    timeoutMs: { total: 30000, connect: 1000, write: 1000, headers: 2000, read: 5000 },
    decodeContent: false, maxDecodedBytes: 1024,
    body: async function* (signal) { signal.throwIfAborted(); yield Buffer.from('bytes'); },
  });
  void decoded; void wire;
  const duplex = session.duplex('POST', 'https://example.com');
  const duplexResponse: StreamResponse = await duplex.response;
  await duplex.send(Buffer.from('chunk'));
  await duplex.finishUpload();
  await duplexResponse.waitUpload();
  await duplex.closeAsync();
  const automatic = await session.stream('POST', 'https://example.com', { duplex: true, body: [Buffer.from('chunk')] });
  await automatic.closeAsync();
  const sseOptions: SSEOptions = { maxEventBytes: 1024, lastEventId: '41', timeoutMs: { total: 60000, read: 5000 } };
  const events: SSEStream = await session.sse('GET', 'https://example.com/events', sseOptions);
  for await (const event of events) {
    const value: string = event.data, retry: number | undefined = event.retry;
    const payload: { ok: boolean } = event.json<{ ok: boolean }>();
    void value; void retry; void payload;
  }
  const saved: string = events.lastEventId;
  const event = new ServerSentEvent(saved);
  // @ts-expect-error Server-sent events are immutable.
  event.data = 'changed';
  // @ts-expect-error IDs must be strings.
  session.sse('GET', 'https://example.com/events', { lastEventId: 1 });
  await events.closeAsync();
  await session.closeAsync();
  // @ts-expect-error Manual duplex accepts send() instead of body.
  session.duplex('POST', 'https://example.com', { body: Buffer.from('invalid') });
  // @ts-expect-error Native handles and per-request ownership are internal.
  session.handle;
  // @ts-expect-error Header values must be strings.
  session.get('https://example.com', { headers: { X: 1 } });
}
void consume;
