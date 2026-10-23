import { Session } from '../dist/index.mjs';

const [url] = process.argv.slice(2);
if (!url) throw Error('Usage: node examples/duplex.mjs <HTTP/2 duplex URL>');
const session = new Session({ timeoutMs: 30000 });
const exchange = session.duplex('POST', url);
try {
  // Starting the reader before sending keeps both HTTP/2 flow-control windows moving.
  const reading = (async () => {
    const response = await exchange.response;
    for await (const chunk of response.body) process.stdout.write(chunk);
  })();
  // Handle an early read failure while send() is still pending.
  void reading.catch(() => {});
  await exchange.send(Buffer.from('first\n'));
  await exchange.send(Buffer.from('last\n'));
  await exchange.finishUpload();
  await reading;
} finally {
  exchange.close();
  await session.closeAsync();
}
