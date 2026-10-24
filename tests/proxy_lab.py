"""Independent loopback-only HTTP CONNECT / RFC1928+1929 acceptance proxy."""
import base64
from contextlib import AbstractContextManager
import ipaddress
import select
import socket
import socketserver
import struct
import threading
import time
from urllib.parse import quote


class ProxyLab(AbstractContextManager):
    def __init__(self, scheme="http", *, auth=False, mode="normal"):
        self.scheme, self.auth, self.mode = scheme, auth, mode
        self.lock = threading.Lock()
        self.sockets = set()
        self.targets, self.methods = [], []
        self.accepted = 0
        self.cancelled_handshakes = 0
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with owner.lock:
                    owner.sockets.add(self.request); owner.accepted += 1
                try:
                    if scheme == "http": owner.http(self.request)
                    else: owner.socks(self.request)
                except (OSError, EOFError, ValueError):
                    pass
                finally:
                    with owner.lock: owner.sockets.discard(self.request)
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
                readers = [c, upstream]
                while readers:
                    active, _, _ = select.select(readers, [], [], 1)
                    for source in active:
                        target = upstream if source is c else c
                        data = source.recv(65536)
                        if data: target.sendall(data)
                        else:
                            readers.remove(source)
                            target.shutdown(socket.SHUT_WR)
            finally:
                with self.lock: self.sockets.discard(upstream)

    def wait_idle(self, timeout=4):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            with self.lock:
                if not self.sockets: return
            time.sleep(.01)
        raise AssertionError("proxy retained active sockets")

    def __exit__(self, *args):
        self.server.shutdown()
        with self.lock: connections = list(self.sockets)
        for c in connections:
            try: c.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            c.close()
        self.server.server_close(); self.thread.join(3)
