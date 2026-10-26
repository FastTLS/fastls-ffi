import { Session } from '@fasttls/fastls';

const url = process.argv[2];
if (!url) throw new Error('Usage: node examples/sse.mjs https://example.com/events');
const session = new Session();
try {
  const events = await session.sse('GET', url, {
    timeoutMs: { total: 3_600_000, read: 45_000 },
    signal: AbortSignal.timeout(3_600_000),
  });
  try {
    for await (const event of events) {
      console.log(event.event, event.id, event.data);
      if (event.data === '[DONE]') break;
    }
    // Save events.lastEventId for a later, explicitly initiated request.
  } finally { await events.closeAsync(); }
} finally { await session.closeAsync(); }
