import { Session, ServerSentEvent, type SSEOptions, type SSEStream, type RequestOptions, type Response, type StreamResponse } from '@fastls/fastls';
import { fetch, type FetchResponse } from '@fastls/fastls';
import { ProfileSpec, type AddressPolicy } from '@fastls/fastls';
import { RetryPolicy, ProfileBundle, ProfileStore } from '@fastls/fastls';

async function reliability() {
  const retry = new RetryPolicy({ maxRetries: 1 });
  const session = new Session({ retry });
  await session.put('https://example.com', { bodyFactory: () => [Buffer.from('body')] });
  await session.fetch('https://example.com', { retry });
  const bundle = ProfileBundle.create(new ProfileSpec({ schema_version: 1 }), { source: 'local' });
  new ProfileStore('profiles').install('bundle.json', bundle.sha256);
  // @ts-expect-error Retry policies are explicit immutable objects.
  await session.get('https://example.com', { retry: { maxRetries: 3 } });
}
void reliability;

async function capabilities() {
  const profileSpec = new ProfileSpec({ schema_version: 1 });
  const addressPolicy: AddressPolicy = { allow_hosts: ['example.com'], deny_cidrs: ['127.0.0.0/8'] };
  const session = new Session({ profileSpec, addressPolicy });
  await session.get('https://example.com', { profileSpec });
  await session.closeAsync();
  const h3 = new Session({ httpVersion: 'h3' });
  await h3.fetch('https://example.com', { httpVersion: 'h3' });
  await h3.closeAsync();
  // @ts-expect-error Address policy belongs to Session creation.
  session.get('https://example.com', { addressPolicy });
  // @ts-expect-error Raw objects must use the explicit ProfileSpec wrapper.
  session.get('https://example.com', { profileSpec: { schema_version: 1 } });
}
void capabilities;

async function fetchConsumer(session: Session) {
  const response: FetchResponse = await fetch(new URL('https://example.com'), { profile: 'chrome/156/windows', headers: new Headers({ X: 'yes' }) });
  const native: globalThis.Response = response;
  const body: string = await response.text();
  const stream: ReadableStream<Uint8Array> | null = response.body;
  await session.fetch(new Request('https://example.com'), { redirect: 'manual', signal: AbortSignal.timeout(1000) });
  // @ts-expect-error Fetch body must use platform BodyInit, not a JSON object.
  fetch('https://example.com', { body: { hello: 'world' } });
  void native; void body; void stream;
}
void fetchConsumer;

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
  await session.post('https://example.com', { body: 'payload', trailers: { 'X-Checksum': ['one', 'two'] } });
  const finalURL: string = response.url, protocol: string = response.httpVersion;
  void finalURL; void protocol; response.trailers.getAll('X-Checksum');
  // @ts-expect-error Trailer values must be strings or arrays of strings.
  session.post('https://example.com', { trailers: { 'X-Checksum': 1 } });
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

const compactSpec = ProfileSpec.fromFingerprints(new ProfileSpec({ schema_version: 1 }), { h2: '2:0|0|0|m,a,s,p' });
void compactSpec;
// @ts-expect-error Compact parameters require strings.
ProfileSpec.fromFingerprints(new ProfileSpec({}), { ja3: 771 });
