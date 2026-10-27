"""Independent TLS/hyper-h2 download fixture with bounded flow-control traces.

Requires h2 and cryptography in the server interpreter only. Does not import
the SDK clients or the Go fixture. Stops on stdin EOF and only binds loopback.
"""
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
import argparse
import importlib.metadata
import ipaddress
import json
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import RequestReceived, WindowUpdated, RemoteSettingsChanged, StreamReset

SLAB=bytes([0,1,128,255])*16384


def certificates(directory):
    now=datetime.now(timezone.utc)
    key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"G0 independent H2 test CA")])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .sign(key,hashes.SHA256()))
    leafkey=ec.generate_private_key(ec.SECP256R1())
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"localhost")]))
          .issuer_name(name).public_key(leafkey.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),critical=False)
          .sign(key,hashes.SHA256()))
    ca_path=directory/"ca.pem";cert_path=directory/"server.pem";key_path=directory/"server.key"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(leafkey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return ca_path,cert_path,key_path


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        trace=dict(events=Counter(),first_events=[],tail=deque(maxlen=32),streams={},alpn=None)
        connection=H2Connection(config=H2Configuration(client_side=False,header_encoding="utf-8"))
        streams={}
        acknowledgements=0
        deferred=None
        def record(kind,**details):
            trace["events"][kind]+=1
            row=dict(event=kind,**details)
            if len(trace["first_events"])<32:trace["first_events"].append(row)
            trace["tail"].append(row)
        try:
            self.request.settimeout(2)
            with self.server.tls.wrap_socket(self.request,server_side=True) as sock:
                trace["alpn"]=sock.selected_alpn_protocol()
                if trace["alpn"]!="h2":raise ValueError("H2 ALPN required")
                def flush():
                    nonlocal acknowledgements,deferred
                    raw=connection.data_to_send();output=bytearray();offset=0
                    while offset<len(raw):
                        length=int.from_bytes(raw[offset:offset+3],"big")
                        frame=raw[offset:offset+9+length];offset+=len(frame)
                        if frame[3]==4 and frame[4]&1:
                            acknowledgements+=1
                            if acknowledgements==2 and self.server.ack_mode!="normal":
                                deferred=frame
                                record("withheld_settings_ack",ordinal=2)
                                continue
                            record("sent_settings_ack",ordinal=acknowledgements)
                        output.extend(frame)
                    if output:sock.sendall(output)
                connection.initiate_connection();flush()
                while not self.server.stopping.is_set():
                    try:data=sock.recv(65536)
                    except socket.timeout:
                        if deferred is not None and self.server.ack_mode=="delay-second":
                            sock.sendall(deferred);deferred=None
                            record("released_settings_ack",sent_bytes={str(k):v["sent"] for k,v in streams.items()})
                        continue
                    if not data:break
                    for event in connection.receive_data(data):
                        if isinstance(event,RemoteSettingsChanged):
                            record("settings",values={str(int(k)):v.new_value for k,v in event.changed_settings.items()})
                        elif isinstance(event,WindowUpdated):
                            record("window_update",stream=event.stream_id,increment=event.delta)
                        elif isinstance(event,StreamReset):
                            streams.pop(event.stream_id,None);record("reset",stream=event.stream_id,code=int(event.error_code))
                        elif isinstance(event,RequestReceived):
                            path=dict(event.headers)[":path"]
                            size=int(parse_qs(urlsplit(path).query).get("n",[1024])[0])
                            if not 1<=size<=64*1048576:raise ValueError("invalid size")
                            streams[event.stream_id]=dict(size=size,sent=0)
                            trace["streams"][str(event.stream_id)]=streams[event.stream_id]
                            connection.send_headers(event.stream_id,[(":status","200"),("content-length",str(size)),("x-test-protocol","HTTP/2.0")])
                            record("request",stream=event.stream_id,size=size)
                    for stream_id,state in list(streams.items()):
                        while state["sent"]<state["size"]:
                            window=connection.local_flow_control_window(stream_id)
                            if window<=0:
                                record("blocked",stream=stream_id,sent=state["sent"],connection_window=connection.outbound_flow_control_window)
                                break
                            count=min(window,connection.max_outbound_frame_size,state["size"]-state["sent"])
                            offset=state["sent"]%4
                            connection.send_data(stream_id,SLAB[offset:offset+count],end_stream=state["sent"]+count==state["size"])
                            state["sent"]+=count
                            flush()
                        if state["sent"]==state["size"]:streams.pop(stream_id)
                    flush()
        except Exception as error:
            trace["error"]=f"{type(error).__name__}: {error}"
        finally:
            trace["tail"]=list(trace["tail"])
            with self.server.trace_lock:
                with self.server.trace.open("a",encoding="utf-8") as output:
                    output.write(json.dumps(trace)+"\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=False


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--trace",required=True,type=Path)
    parser.add_argument("--ack-mode",choices=("normal","drop-second","delay-second"),default="normal",
                        help="fault injection; non-normal modes deliberately withhold the second SETTINGS ACK")
    args=parser.parse_args();args.trace.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastls-g0-h2-") as directory:
        ca,cert,key=certificates(Path(directory))
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key);tls.set_alpn_protocols(["h2"])
        with Server(("127.0.0.1",0),Handler) as server:
            server.tls=tls;server.trace=args.trace;server.trace_lock=threading.Lock();server.stopping=threading.Event()
            server.ack_mode=args.ack_mode
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            print(json.dumps(dict(https2=f"https://127.0.0.1:{server.server_address[1]}",ca_file=str(ca),
                versions={name:importlib.metadata.version(name) for name in ("h2","hyperframe","hpack","cryptography")},ssl=ssl.OPENSSL_VERSION)),flush=True)
            try:sys.stdin.buffer.read()
            finally:server.stopping.set();server.shutdown();thread.join(5)


if __name__=="__main__":main()
