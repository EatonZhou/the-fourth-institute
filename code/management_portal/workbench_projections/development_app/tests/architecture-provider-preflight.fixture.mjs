import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sha} from './architecture-provider-feedback.fixture.mjs';
export function readPreflightModesFixture(path=process.env.PROVIDER_PREFLIGHT_MODES_FACTS){
  const bytes=readFileSync(path),fixtureSha='fb5f52c59c251d204a750213104c670761636e5a82e1a67a47954811cb476b74';
  assert.equal(sha(bytes),fixtureSha);const capture=JSON.parse(bytes);
  function text(d){const value=capture.facts[d.address]?.text;assert.equal(typeof value,'string',d.address);assert.equal(sha(value),d.sha256,d.address);assert.equal(Buffer.byteLength(value),d.size,d.address);return value;}
  const read=d=>JSON.parse(text(d)),root=read(capture.entry),pair=read(capture.pair);
  assert.deepEqual(root.exploration,pair.exploration);
  const batchKey='8d1968564339972de4de5bb323096d9d9762c0a79367586e0a1e6001bf6aa926';
  assert.equal(root.exploration.manifest.sha256,batchKey);const manifest=read(root.exploration.manifest);
  assert.equal(manifest.model.sha256,'8fc5b0a7252879de2e3e65bce69836750ea1560c1d60a397d3c175d9abe8f70e');
  const model=read(manifest.model);text(manifest.contract);assert.deepEqual(model.binding,manifest.binding);
  assert.equal(capture.commit,'a0dac83394748582158ee9c63a8b23c715872a1e');
  for(const d of Object.values(manifest.binding))if(d?.address)text(d);
  const declared=new Map(manifest.sources.map(s=>[s.address,s]));for(const d of manifest.sources)text(d);
  for(const e of model.evidence)if(e.frozen)assert.deepEqual(declared.get(e.frozen.address),e.frozen,e.id);
  function content(objectId){
    const subject=model.objects.find(o=>o.id===objectId);assert.ok(subject);
    const mechanisms=model.mechanisms.filter(m=>subject.mechanism_ids.includes(m.id));
    const mechanism=mechanisms.find(m=>m.id==='provider-capabilities.preflight');assert.ok(mechanism);
    const eids=new Set(mechanisms.flatMap(m=>[...m.evidence_ids,...m.steps.flatMap(s=>s.evidence_ids),...m.cases.flatMap(c=>c.evidence_ids)]));
    const evidence=model.evidence.filter(e=>eids.has(e.id));assert.equal(evidence.length,eids.size);for(const e of evidence)text(e.frozen);
    const oids=new Set([objectId,...mechanisms.flatMap(m=>[m.owner_id,...m.participants.map(p=>p.object_id),...m.state_fields.map(f=>f.authority_id),...m.steps.flatMap(s=>[s.from,s.to])])]);
    return {mechanism,mechanisms,objects:model.objects.filter(o=>oids.has(o.id)),evidence,batchKey};
  }
  return {model,content,provenance:{fixturePath:path,fixtureSha,entry:capture.entry,pair:capture.pair,manifest:root.exploration.manifest,model:manifest.model,contract:manifest.contract,commit:capture.commit,verified_source_descriptors:declared.size}};
}
