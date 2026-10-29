"""Independent loopback-only HTTP CONNECT / RFC1928+1929 acceptance proxy."""
import base64
from contextlib import AbstractContextManager
import ipaddress
import select
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time
import traceback
from urllib.parse import quote


class ProxyLab(AbstractContextManager):
    def __init__(self, scheme="http", *, auth=False, mode="normal", certfile=None, keyfile=None):
        self.scheme, self.auth, self.mode = scheme, auth, mode
        self.lock = threading.Lock()
        self.sockets = set()
        self.targets, self.methods = [], []
        self.accepted = 0
        self.cancelled_handshakes = 0
        self.tls_protocols = []
        self.tls_context = None
        if scheme == "https":
            self.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls_context.load_cert_chain(certfile, keyfile)
            self.tls_context.set_alpn_protocols(["http/1.1"])
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                conn = self.request
                with owner.lock:
                    owner.sockets.add(self.request); owner.accepted += 1
                try:
                    if scheme == "https":
                        if owner.mode == "stall-tls": owner.stall(conn); return
                        secure = owner.tls_context.wrap_socket(conn,server_side=True,do_handshake_on_connect=False)
                        with owner.lock:
                            owner.sockets.discard(conn);owner.sockets.add(secure)
                        conn = secure
                        conn.settimeout(5);conn.do_handshake();conn.settimeout(None)
                        with owner.lock: owner.tls_protocols.append(conn.selected_alpn_protocol())
                    if scheme in ("http","https"): owner.http(conn)
                    else: owner.socks(conn)
                except (OSError, EOFError, ValueError):
                    pass
                finally:
                    with owner.lock: owner.sockets.discard(conn)
                    conn.close()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs=dict(poll_interval=.02), daemon=True)
        self.thread.start()
        credentials = "proxy-user:" + quote("s3cr@t:secret", safe="") + "@" if auth else ""
        self.url = f"{scheme}://{credentials}127.0.0.1:{self.server.server_address[1]}"

    @staticmethod
    def read(c, n):
        parts = bytearray()
        while len(parts) < n:
            part = c.recv(n-len(parts))
            if not part: raise EOFError()
            parts.extend(part)
        return bytes(parts)

    def stall(self, c):
        while c.recv(4096): pass
        with self.lock: self.cancelled_handshakes += 1

    def http(self, c):
        with c.makefile("rb") as f:
            line = f.readline(8192).decode("ascii").split()
            if len(line) != 3 or line[0] != "CONNECT": raise ValueError("expected CONNECT")
            headers = {}
            while True:
                header = f.readline(8192)
                if not header: raise EOFError()
                if header == b"\r\n": break
                key, value = header.decode("ascii").split(":", 1)
                headers[key.lower()] = value.strip()
            if self.mode.startswith("stall"):
                self.stall(c); return
            expected = "Basic " + base64.b64encode(b"proxy-user:s3cr@t:secret").decode()
            if self.mode == "deny" or self.auth and headers.get("proxy-authorization") != expected:
                c.sendall(b"HTTP/1.1 407 CredentialMustNotBeEchoed\r\nContent-Length: 0\r\n\r\n"); return
            host, port = line[1].rsplit(":", 1)
            self.tunnel(c, host.strip("[]"), int(port), "CONNECT", lambda: c.sendall(b"HTTP/1.1 200 Tunnel\r\n\r\n"))

    def socks(self, c):
        version, count = self.read(c, 2)
        if version != 5: raise ValueError("SOCKS version")
        offered = self.read(c, count)
        if self.mode == "stall": self.stall(c); return
        method = 2 if self.auth else 0
        if method not in offered: c.sendall(b"\x05\xff"); return
        c.sendall(bytes((5, method)))
        if method == 2:
            version, size = self.read(c, 2); user = self.read(c, size)
            password = self.read(c, self.read(c, 1)[0])
            if self.mode == "stall-auth": self.stall(c); return
            if version != 1 or user != b"proxy-user" or password != b"s3cr@t:secret": c.sendall(b"\x01\x01"); return
            c.sendall(b"\x01\x00")
        version, cmd, reserved, atyp = self.read(c, 4)
        if (version, cmd, reserved) != (5, 1, 0): raise ValueError("SOCKS command")
        if atyp == 1: host = socket.inet_ntop(socket.AF_INET, self.read(c, 4))
        elif atyp == 4: host = socket.inet_ntop(socket.AF_INET6, self.read(c, 16))
        elif atyp == 3: host = self.read(c, self.read(c, 1)[0]).decode("ascii")
        else: raise ValueError("SOCKS address")
        port = struct.unpack("!H", self.read(c, 2))[0]
        if self.mode == "stall-connect": self.stall(c); return
        if self.mode == "deny": c.sendall(b"\x05\x02\x00\x01"+b"\0"*6); return
        self.tunnel(c, host, port, atyp, lambda: c.sendall(b"\x05\x00\x00\x01"+b"\0"*6))

    def tunnel(self, c, host, port, method, ready):
        with self.lock:
            self.targets.append((host, port)); self.methods.append(method)
        resolved = "127.0.0.1" if host in ("localhost", "proxy-only.invalid") else host
        if not ipaddress.ip_address(resolved).is_loopback: raise ValueError("proxy lab is loopback-only")
        with socket.create_connection((resolved, port), timeout=3) as upstream:
            upstream.settimeout(None)
            with self.lock: self.sockets.add(upstream)
            try:
                ready()
                if isinstance(c, ssl.SSLSocket):
                    self.relay_tls(c, upstream)
                    return
                readers = [c, upstream]
                while readers:
                    active = [source for source in readers if isinstance(source,ssl.SSLSocket) and source.pending()]
                    if not active: active, _, _ = select.select(readers, [], [], 1)
                    for source in active:
                        target = upstream if source is c else c
                        data = source.recv(65536)
                        if data: target.sendall(data)
                        else:
                            readers.remove(source)
                            target.shutdown(socket.SHUT_WR)
            finally:
                with self.lock: self.sockets.discard(upstream)

    @staticmethod
    def relay_tls(client, upstream):
        # OS readability can represent only TLS control records. Blocking SSL
        # recv can then stall application bytes and the other direction's EOF.
        # Retain partial writes and drain accepted bytes before closing.
        endpoints = (client, upstream)
        for endpoint in endpoints:
            endpoint.setblocking(False)
        pending = {endpoint: bytearray() for endpoint in endpoints}
        write_frames = {endpoint: None for endpoint in endpoints}
        receive_wants_write, send_wants_read = set(), set()
        eof = False
        while not eof or any(pending.values()):
            readers = [] if eof else [source for source in endpoints
                if len(pending[upstream if source is client else client]) < 262144]
            writers = [target for target in endpoints if pending[target] and target not in send_wants_read]
            readable, writable, _ = select.select(
                list(set(readers) | send_wants_read), list(set(writers) | receive_wants_write), [],
                0 if client in readers and client.pending() else .1)
            for source in readers:
                ready = source in readable or source in writable and source in receive_wants_write
                if source is client:
                    ready = ready or bool(client.pending())
                if not ready:
                    continue
                target = upstream if source is client else client
                try:
                    data = source.recv(min(65536, 262144 - len(pending[target])))
                except ssl.SSLWantWriteError:
                    receive_wants_write.add(source)
                    continue
                except (ssl.SSLWantReadError, BlockingIOError):
                    receive_wants_write.discard(source)
                    continue
                receive_wants_write.discard(source)
                if not data:
                    eof = True
                    receive_wants_write.clear()
                    break
                pending[target].extend(data)
            for target in endpoints:
                if not pending[target] or not (target in writable or target in readable and target in send_wants_read):
                    continue
                try:
                    # SSL wants-read/write retries require the original write
                    # bytes and length, even if the peer queues more data.
                    if write_frames[target] is None:
                        write_frames[target] = bytes(pending[target][:65536])
                    sent = target.send(write_frames[target])
                except ssl.SSLWantReadError:
                    send_wants_read.add(target)
                    continue
                except (ssl.SSLWantWriteError, BlockingIOError):
                    send_wants_read.discard(target)
                    continue
                if sent == 0:
                    raise EOFError('proxy relay write closed')
                send_wants_read.discard(target)
                del pending[target][:sent]
                write_frames[target] = None

    def wait_idle(self, timeout=4):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            with self.lock:
                if not self.sockets: return
            time.sleep(.01)
        stacks = []
        for frame in sys._current_frames().values():
            stack = traceback.extract_stack(frame)
            if any(item.filename == __file__ for item in stack):
                stacks.append(''.join(traceback.format_list(stack)))
        raise AssertionError("proxy retained active sockets\n" + '\n'.join(stacks))

    def __exit__(self, *args):
        self.server.shutdown()
        with self.lock: connections = list(self.sockets)
        for c in connections:
            try: c.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            c.close()
        self.server.server_close(); self.thread.join(3)
