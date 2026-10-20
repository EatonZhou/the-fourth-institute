import fs from 'node:fs';
import {createHash} from 'node:crypto';
const path=(process.env.HARNESS_OFFLOADING_CAPTURE || (() => { throw new Error("Set HARNESS_OFFLOADING_CAPTURE to a local fixture"); })());
export const captureHash='fb5f52c59c251d204a750213104c670761636e5a82e1a67a47954811cb476b74';
const sha=v=>createHash('sha256').update(v).digest('hex'),bytes=fs.readFileSync(path);
if(sha(bytes)!==captureHash)throw Error('Offloading capture mismatch');
const capture=JSON.parse(bytes);
function read(d,json=true){const text=capture.facts[d.address]?.text;if(!text||sha(text)!==d.sha256)throw Error('Frozen body mismatch');return json?JSON.parse(text):text;}
const manifest=read(capture.manifest);export const model=read(manifest.model);
read(capture.entry);read(capture.pair);read(manifest.binding.base_manifest);read(manifest.contract,false);
if(!capture.manifest.sha256.startsWith('8d196')||!manifest.model.sha256.startsWith('8fc5b')||!manifest.binding.commit.startsWith('a0dac')||!manifest.binding.base_manifest.sha256.startsWith('43e2'))throw Error('Offloading batch mismatch');
const mechanism=model.mechanisms.find(m=>m.id==='harness.tool-output-offloading');
const relevant=model.evidence.filter(e=>mechanism.evidence_ids.includes(e.id));
for(const e of relevant)read(e.frozen,false);
export const sourceHashes={capturePath:path,captureHash,entry:capture.entry,pair:capture.pair,manifest:capture.manifest,model:manifest.model,
  contract:manifest.contract,binding:manifest.binding,evidence:relevant.map(e=>({id:e.id,address:e.address,frozen:e.frozen,startLine:e.start_line,endLine:e.end_line}))};
export const contentFor=()=>structuredClone({mechanism,objects:model.objects,relations:model.relations,evidence:model.evidence,
  mechanisms:model.mechanisms.filter(m=>['harness.plugin-lifecycle','harness.tool-output-offloading'].includes(m.id)),batchKey:captureHash,modelDescriptor:manifest.model});
