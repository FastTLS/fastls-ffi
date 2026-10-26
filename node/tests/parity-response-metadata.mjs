import {parity,assert,sdk} from './harness.mjs';
const p=(name,fn)=>parity('test_response_metadata.py',name,fn,'Node refreshes metadata when the Promise-based body iterator reaches EOF.');
p('test_metadata_and_trailers_refresh_at_eof',async()=>{
  const chunks=[Buffer.from('body'),null];
  const transfer={next:async()=>chunks.shift(),info:()=>({state:'eof',status:200,protocol:'HTTP/2.0',final_url:'https://example.test/final',trailer_values:{'X-Checksum':['one','two']}}),close(){}};
  const r=new sdk.StreamResponse(transfer,{status:200,protocol:'HTTP/1.1',final_url:'https://example.test/start',match:{}}),reader=r.body[Symbol.asyncIterator]();
  assert.equal((await reader.next()).value.toString(),'body');assert.equal((await reader.next()).done,true);
  assert.equal(r.httpVersion,'HTTP/2.0');assert.equal(r.url,'https://example.test/final');assert.deepEqual(r.trailers.getAll('x-checksum'),['one','two']);
});
