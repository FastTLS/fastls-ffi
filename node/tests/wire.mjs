import net from 'node:net';
import { once } from 'node:events';
import { delay } from './harness.mjs';
/** Independent TCP peer; never uses the Fastls implementation to produce a response. */
export async function wire(t, reply) {
  const sockets=new Set(); const peer={accepted:false,disconnected:false};
  const server=net.createServer(socket=>{
    sockets.add(socket);peer.accepted=true;let bytes=Buffer.alloc(0),sent=false;
    socket.on('error',()=>{});socket.on('close',()=>{peer.disconnected=true;sockets.delete(socket);});
    socket.on('data',async data=>{
      bytes=Buffer.concat([bytes,data]);
      if(sent||reply===undefined||!bytes.includes('\r\n\r\n'))return;
      sent=true;
      for(const part of Array.isArray(reply)?reply:[reply]){
        if(socket.destroyed)return;socket.write(part);if(Array.isArray(reply))await delay(2);
      }
      socket.end();
    });
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  peer.address='127.0.0.1:'+server.address().port;
  t.after(async()=>{for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));});
  return peer;
}
