// Independent loopback-only HTTP CONNECT / RFC1928 / RFC1929 fixture.
// It runs in the test event loop, never in the SDK or in a Python process.
import { createServer, createConnection, isIP } from 'node:net';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';

async function read(socket, count) {
  while (true) {
    const bytes = socket.read(count);
    if (bytes !== null) {
      if (count !== undefined && bytes.length !== count) throw Error('proxy fixture EOF');
      return bytes;
    }
    if (socket.destroyed || socket.readableEnded) throw Error('proxy fixture EOF');
    await new Promise((resolve, reject) => {
      const clean = () => { for (const event of ['readable', 'end', 'close', 'error']) socket.removeListener(event, events[event]); };
      const events = {
        readable: () => { clean(); resolve(); },
        end: () => { clean(); reject(Error('proxy fixture EOF')); },
        close: () => { clean(); reject(Error('proxy fixture closed')); },
        error: error => { clean(); reject(error); },
      };
      for (const event of Object.keys(events)) socket.once(event, events[event]);
    });
  }
}

export class ProxyLab {
  sockets = new Set();
  targets = [];
  methods = [];
  accepted = 0;
  cancelledHandshakes = 0;
  failures = [];
  static async open(scheme = 'http', { auth = false, mode = 'normal' } = {}) {
    const lab = new ProxyLab(); Object.assign(lab, { scheme, auth, mode });
    lab.server = createServer({ allowHalfOpen: true }, socket => {
      lab.accepted++; lab.own(socket);
      void lab.handle(socket).catch(error => {
        // Remote cancellation is expected; fixture programming errors are not.
        if (!socket.destroyed && !socket.readableEnded && !/EOF|closed|ECONNRESET|EPIPE/.test(String(error))) lab.failures.push(error);
        socket.destroy();
      });
    });
    lab.server.listen({ host: '127.0.0.1', port: 0 }); await once(lab.server, 'listening');
    lab.url = `${scheme}://${auth ? 'proxy-user:s3cr%40t%3Asecret@' : ''}127.0.0.1:${lab.server.address().port}`;
    return lab;
  }
  own(socket) {
    this.sockets.add(socket); socket.on('error', () => {});
    socket.once('close', () => this.sockets.delete(socket));
  }
  async stall(socket) {
    try { for await (const chunk of socket) void chunk; } finally { this.cancelledHandshakes++; socket.destroy(); }
  }
  async handle(socket) {
    if (this.scheme === 'http') {
      let bytes = Buffer.alloc(0), boundary;
      while (true) {
        const part = await read(socket);
        bytes = bytes.length ? Buffer.concat([bytes,part]) : part;
        boundary = bytes.indexOf('\r\n\r\n');
        if (boundary >= 0) {
          if (boundary+4 > 65536) throw Error('oversize proxy fixture request');
          if (bytes.length > boundary+4) socket.unshift(bytes.subarray(boundary+4));
          break;
        }
        if (bytes.length >= 65536) throw Error('oversize proxy fixture request');
      }
      const header = bytes.subarray(0,boundary+4).toString('latin1');
      const lines = header.split('\r\n'), [method, target, protocol] = lines.shift().split(' ');
      assert.equal(method, 'CONNECT'); assert.equal(protocol, 'HTTP/1.1');
      const headers = Object.fromEntries(lines.filter(Boolean).map(line => { const n = line.indexOf(':'); return [line.slice(0,n).toLowerCase(),line.slice(n+1).trim()]; }));
      if (this.mode.startsWith('stall')) return this.stall(socket);
      if (this.mode === 'deny' || this.auth && headers['proxy-authorization'] !== 'Basic ' + Buffer.from('proxy-user:s3cr@t:secret').toString('base64')) {
        socket.end('HTTP/1.1 407 CredentialMustNotBeEchoed\r\nContent-Length: 0\r\n\r\n'); return;
      }
      const split = target.lastIndexOf(':');
      return this.tunnel(socket, target.slice(0,split).replace(/^\[|\]$/g,''), Number(target.slice(split+1)), 'CONNECT', () => socket.write('HTTP/1.1 200 Tunnel\r\n\r\n'));
    }
    const [version, count] = await read(socket, 2); assert.equal(version, 5);
    const offered = await read(socket, count);
    if (this.mode === 'stall') return this.stall(socket);
    const method = this.auth ? 2 : 0;
    if (!offered.includes(method)) { socket.end(Buffer.from([5,255])); return; }
    socket.write(Buffer.from([5,method]));
    if (method === 2) {
      const [version, size] = await read(socket, 2), user = await read(socket,size);
      const password = await read(socket,(await read(socket,1))[0]);
      if (this.mode === 'stall-auth') return this.stall(socket);
      if (version !== 1 || user.toString() !== 'proxy-user' || password.toString() !== 's3cr@t:secret') { socket.end(Buffer.from([1,1])); return; }
      socket.write(Buffer.from([1,0]));
    }
    const [v, cmd, reserved, atyp] = await read(socket,4);
    assert.deepEqual([v,cmd,reserved],[5,1,0]);
    let host;
    if (atyp === 1) host = [...await read(socket,4)].join('.');
    else if (atyp === 3) host = (await read(socket,(await read(socket,1))[0])).toString('ascii');
    else if (atyp === 4) { const bytes = await read(socket,16); host = Array.from({length:8},(_,i)=>bytes.readUInt16BE(2*i).toString(16)).join(':'); }
    else throw Error('invalid SOCKS fixture address');
    const port = (await read(socket,2)).readUInt16BE();
    if (this.mode === 'stall-connect') return this.stall(socket);
    if (this.mode === 'deny') { socket.end(Buffer.from([5,2,0,1,0,0,0,0,0,0])); return; }
    return this.tunnel(socket,host,port,atyp,()=>socket.write(Buffer.from([5,0,0,1,0,0,0,0,0,0])));
  }
  async tunnel(client, host, port, method, ready) {
    const resolved = ['localhost','proxy-only.invalid'].includes(host) ? '127.0.0.1' : host;
    if (!(isIP(resolved) === 4 && resolved.startsWith('127.')) && resolved !== '::1' && resolved !== '0:0:0:0:0:0:0:1') throw Error('proxy fixture is loopback-only');
    this.targets.push([host,port]); this.methods.push(method);
    const upstream = createConnection({ host: resolved, port, allowHalfOpen: true }); this.own(upstream);
    client.once('error', () => upstream.destroy()); upstream.once('error', () => client.destroy());
    client.once('close', () => upstream.destroy()); upstream.once('close', () => client.destroy());
    await once(upstream,'connect');
    if (client.destroyed) { upstream.destroy(); return; }
    ready(); client.pipe(upstream); upstream.pipe(client);
  }
  async waitIdle() {
    const until = performance.now()+5000;
    while (this.sockets.size && performance.now()<until) await delay(10);
    assert.equal(this.sockets.size,0,'proxy tunnel retained sockets'); assert.deepEqual(this.failures,[]);
  }
  async close() {
    for (const socket of this.sockets) socket.destroy();
    await new Promise((resolve,reject)=>this.server.close(error=>error?reject(error):resolve()));
    assert.deepEqual(this.failures,[]);
  }
}
