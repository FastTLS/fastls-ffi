export const platforms = [
  ['win32', 'x64'], ['linux', 'x64'], ['linux', 'arm64'], ['darwin', 'x64'], ['darwin', 'arm64'],
].map(([os, cpu]) => ({
  name: `@fastls/fastls-${os}-${cpu}${os === 'linux' ? '-glibc' : ''}`,
  os: [os], cpu: [cpu], ...(os === 'linux' ? { libc: ['glibc'] } : {}),
}));
