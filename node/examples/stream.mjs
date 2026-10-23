import { Session } from '../dist/index.mjs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createWriteStream } from 'node:fs';

const [url, output] = process.argv.slice(2);
if (!url || !output) throw new Error('Usage: node examples/stream.mjs <url> <output-file>');
const session = new Session();
try {
  const response = await session.stream('GET', url);
  try {
    await pipeline(Readable.from(response.body, { objectMode: false }), createWriteStream(output));
  } finally { response.close(); }
} finally { session.close(); }
