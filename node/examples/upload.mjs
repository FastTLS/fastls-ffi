import { Session } from '../dist/index.mjs';
import { createReadStream } from 'node:fs';

const [url, path] = process.argv.slice(2);
if (!url || !path) throw Error('Usage: node examples/upload.mjs <url> <file>');
const session = new Session({ timeoutMs: { total: 120000, connect: 5000, write: 10000, headers: 15000, read: 10000 } });
try {
  // A factory receives a signal for cancellation, timeouts and early server responses.
  const response = await session.post(url, { body: signal => createReadStream(path, { signal }) });
  console.log(response.status, response.text());
} finally { session.close(); }
