import { Session, type RequestOptions, type Response, type StreamResponse } from '@fasttls/fastls';

async function consume(session: Session): Promise<void> {
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
  // @ts-expect-error Native handles and per-request ownership are internal.
  session.handle;
  // @ts-expect-error Header values must be strings.
  session.get('https://example.com', { headers: { X: 1 } });
}
void consume;
