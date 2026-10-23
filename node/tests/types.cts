import fastls = require('@fasttls/fastls');

const options: fastls.SessionOptions = { timeoutMs: 1000, cookies: false };
function consume(session: fastls.Session): Promise<fastls.Response> {
  return session.get('https://example.com', { headers: { X: 'value' } });
}
void options;
void consume;
