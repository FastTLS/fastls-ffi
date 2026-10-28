import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
const root=resolve(import.meta.dirname,'../..');
const sources=['test_python.py','test_stability.py','test_network.py','test_pooling.py','test_cookies.py','test_decode_timeouts.py','test_upload.py','test_duplex.py','test_sse.py','test_notifications.py','test_proxy.py','test_request_options.py','test_websocket.py','test_websocket_cookies.py','test_response_metadata.py','test_sdk_sessions.py'];
const deferred=[];
// Module-level one-shot calls are Python-specific, not Node parity scenarios.
const pythonSpecific=['test_api.py'];
// These test the benchmark controller and Windows measurement API, not SDK
// request behavior. SDK suites still require explicit Node scenario mappings.
// Continuous workloads and sampler fault injection test the benchmark host,
// rather than adding SDK capabilities requiring new Node parity scenarios.
const infrastructure=['test_ci_plan.py','test_ci.py','test_ci_artifact.py','test_pypi.py','test_benchmarks.py','test_regression.py','test_windows_cpu_probe.py','test_c_wait.py','test_c_websocket.py','test_continuous_regression.py','test_observation.py','test_windows_snapshot.py'];
const unreviewed=readdirSync(resolve(root,'tests')).filter(file=>/^test_.*\.py$/.test(file)&&![...sources,...deferred,...infrastructure,...pythonSpecific].includes(file));
if(unreviewed.length)throw Error(`Review new Python suites before claiming parity: ${unreviewed.join(', ')}`);
const python=new Set();
const baseline=JSON.parse(readFileSync(resolve(root,'node/tests/python-baseline.json'),'utf8'));
for(const file of sources){const text=readFileSync(resolve(root,'tests',file),'utf8').replaceAll('\r\n','\n');if(createHash('sha256').update(text).digest('hex')!==baseline.files[file])throw Error(`Python suite changed: ${file}; review the Node assertions and refresh the baseline`);for(const match of text.matchAll(/^    (?:async )?def (test_\w+)\(/gm))python.add(`${file}::${match[1]}`);}
const node=new Set();
for(const file of readdirSync(resolve(root,'node/tests')).filter(file=>/^parity-.+\.mjs$/.test(file))){
  const text=readFileSync(resolve(root,'node/tests',file),'utf8'),source=text.match(/parity\('([^']+)'/)[1];
  for(const match of text.matchAll(/\bp\('([^']+)'/g)){const id=`${source}::${match[1]}`;if(node.has(id))throw Error(`Duplicate mapping ${id}`);node.add(id);}
}
const missing=[...python].filter(id=>!node.has(id)),obsolete=[...node].filter(id=>!python.has(id));
if(missing.length||obsolete.length)throw Error(JSON.stringify({missing,obsolete},null,2));
console.log(`Python parity inventory: ${python.size} scenarios have explicit Node counterparts (language adaptations documented in test records)`);
if(deferred.length)console.log(`Deferred SDK suites: ${deferred.join(', ')}; these capabilities are not yet implemented in Node`);
export const scenarioCount = python.size;

console.log(`Python-specific API suites (no Node convenience export claimed): ${pythonSpecific.join(", ")}`);
