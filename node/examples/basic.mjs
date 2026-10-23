import { Session } from '../dist/index.mjs';

const session = new Session({ profile: 'chrome/156/windows' });
try {
  const response = await session.get(process.argv[2] ?? 'https://example.com');
  console.log(response.status, response.text());
  console.log(response.match);
} finally {
  session.close();
}
