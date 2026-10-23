import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const root=resolve(import.meta.dirname,'../..');
const sources=['test_python.py','test_stability.py','test_network.py','test_pooling.py','test_cookies.py','test_decode_timeouts.py','test_upload.py'];
const python=new Set();
for(const file of sources){const text=readFileSync(resolve(root,'tests',file),'utf8');for(const match of text.matchAll(/^    (?:async )?def (test_\w+)\(/gm))python.add(`${file}::${match[1]}`);}
const node=new Set();
for(const file of readdirSync(resolve(root,'node/tests')).filter(file=>/^parity-.+\.mjs$/.test(file))){
  const text=readFileSync(resolve(root,'node/tests',file),'utf8'),source=text.match(/parity\('([^']+)'/)[1];
  for(const match of text.matchAll(/\bp\('([^']+)'/g)){const id=`${source}::${match[1]}`;if(node.has(id))throw Error(`Duplicate mapping ${id}`);node.add(id);}
}
const missing=[...python].filter(id=>!node.has(id)),obsolete=[...node].filter(id=>!python.has(id));
if(missing.length||obsolete.length)throw Error(JSON.stringify({missing,obsolete},null,2));
console.log(`Python parity inventory: ${python.size} scenarios have explicit Node counterparts (language adaptations documented in test records)`);
export const scenarioCount = python.size;
