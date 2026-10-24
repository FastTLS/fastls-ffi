import { Session, ServerSentEvent, type SSEOptions, type SSEStream, type RequestOptions, type Response, type StreamResponse } from '@fasttls/fastls';

async function consume(session: Session): Promise<void> {
  const proxied = new Session({ proxy: 'https://127.0.0.1:8080', proxyCaFile: '/path/to/proxy-ca.pem' });
  await proxied.get('https://example.com', { proxy: null });
  await proxied.get('https://example.com', { proxyCaFile: null });
  await proxied.get('https://example.com', { proxyCaFile: '/path/to/other-proxy-ca.pem' });
  await proxied.get('https://example.com', { proxy: 'socks5h://127.0.0.1:1080' });
  await proxied.sse('GET', 'https://example.com/events', { proxy: '' });
  await proxied.duplex('POST', 'https://example.com', { proxy: null }).closeAsync();
  await proxied.closeAsync();
  // @ts-expect-error Proxy values must be URL strings or null.
  new Session({ proxy: false });
  // @ts-expect-error Proxy values must be URL strings or null.
  session.get('https://example.com', { proxy: 42 });
  // @ts-expect-error Proxy CA paths must be strings or null.
  session.get('https://example.com', { proxyCaFile: false });
  const initialized: Session = await Session.open({ notifications: 'required' });
  await initialized.ready(AbortSignal.timeout(3000));
  const mode: 'async-events' | 'poll' = initialized.notificationMode;
  void mode;
  await initialized.closeAsync();
  // @ts-expect-error Notification modes are explicit strings.
  new Session({ notifications: true });
  const options: RequestOptions = { body: Buffer.from([0, 255]), signal: AbortSignal.timeout(1000) };
  const response: Response = await session.post('https://example.com', options);
  const ws = await session.websocket('wss://example.com', { subprotocols: ['chat'], readTimeoutMs: null, maxMessageBytes: 1048576 });
  await ws.send('hello');
  const message: string | Buffer = await ws.recv();
  void message;
  await ws.closeAsync(1000, 'done');
  await session.websocket('wss://example.com', { cookieMode: 'manual' });
  // @ts-expect-error Cookie modes are explicit strings.
  session.websocket('wss://example.com', { cookieMode: 'automatic' });
  // @ts-expect-error WebSocket timeouts are explicit millisecond fields.
  session.websocket('wss://example.com', { timeoutMs: 1000 });
  await session.post('https://example.com', { params: [['tag', ['中文', 'a b']]], json: null });
  await session.post('https://example.com', { form: { tag: ['a', 'b'], empty: '' } });
  await session.post('https://example.com', { files: { file: ['a.bin', { read: async (maximum: number) => Buffer.alloc(maximum) }, 'application/octet-stream'] } });
  response.encoding = 'iso-8859-1';
  response.raiseForStatus();
  session.duplex('POST', 'https://example.com', { params: { q: '中文' } });
  // @ts-expect-error Manual duplex only accepts send() for its body.
  session.duplex('POST', 'https://example.com', { json: null });
  // @ts-expect-error JSON excludes undefined members.
  session.post('https://example.com', { json: { invalid: undefined } });
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
