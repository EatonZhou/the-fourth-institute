import test from 'node:test';
import assert from 'node:assert/strict';
import { createEvidenceLoader } from '../src/architecture/data.js';
import { MAP_REF, EVOLUTION_REF, REPOSITORY } from '../src/architecture/model.js';

function fixture() {
  const map={schema:'tfi.portal-architecture.v1',modules:[{id:'root',title:'Root',designs:['decision_111111111111'],code_paths:[]}],protocols:[]};
  const evolution={schema:'tfi.portal-evolution.v1',plans:[{id:'plan_111111111111',modules:['root']}]};
  const entries={[MAP_REF]:{data:map,text:'modules:\n  - id: root\n'},[EVOLUTION_REF]:{data:evolution,text:'plans:\n  - id: plan_111111111111\n'}};
  const reads=[],resolves=[],denied=new Set();
  let onRead=()=>{};
  const resources={resolve:async ref=>{resolves.push(ref);return {readable:!denied.has(ref),title:ref};},read:async(ref,format)=>{
    reads.push({ref,format});onRead(ref);const entry=entries[ref];return {readable:!denied.has(ref),data:denied.has(ref)?null:format==='yaml'?structuredClone(entry.data):entry.text,metadata:{version:entry.text}};
  }};
  const loader=createEvidenceLoader({resources,sdk:{}}),evidence={snapshots:[],audits:[],scenario_catalog:[]};
  return {loader,evidence,entries,reads,resolves,denied,setRead:fn=>onRead=fn};
}

test('an explicitly preauthorized same-batch current snapshot reuses associations only after fresh source-byte checks',async()=>{
  const f=fixture(),initial=await f.loader.current(f.evidence);f.reads.length=0;f.resolves.length=0;
  // Preauthorization belongs to the controller; this loader never caches grants.
  const next=await f.loader.current(f.evidence,{reuse:initial});
  assert.notEqual(next,initial);assert.equal(next.designMeta,initial.designMeta);
  assert.deepEqual(f.reads,[{ref:MAP_REF,format:'text'},{ref:EVOLUTION_REF,format:'text'}]);
  assert(f.resolves.includes(REPOSITORY));assert(!f.resolves.some(ref=>ref.startsWith('decision_')));
});
test('changed bytes or evidence batch cannot reuse old topology or knowledge associations',async()=>{
  const f=fixture(),initial=await f.loader.current(f.evidence);
  f.entries[MAP_REF].text+='  - id: new\n';f.entries[MAP_REF].data.modules.push({id:'new',title:'New',designs:[]});
  const next=await f.loader.current(f.evidence,{reuse:initial});assert(next.modules.some(m=>m.id==='new'));assert.notEqual(next.designMeta,initial.designMeta);
  f.reads.length=0;
  const other=await f.loader.current({...f.evidence},{reuse:next});assert.notEqual(other.designMeta,next.designMeta);assert(f.reads.some(r=>r.format==='yaml'));
});
test('watch invalidation during matching reads requires fresh normalization and permission filtering',async()=>{
  const f=fixture(),initial=await f.loader.current(f.evidence);let valid=true;
  f.setRead(ref=>{if(ref===MAP_REF){valid=false;f.denied.add('decision_111111111111');}});
  const next=await f.loader.current(f.evidence,{reuse:initial,canReuse:()=>valid});
  assert.deepEqual(next.modules[0].designs,[]);assert.deepEqual(Object.keys(next.designMeta),[]);
  assert.deepEqual(initial.modules[0].designs,['decision_111111111111']);
});
test('revoked source or read failure never falls back to an old authorized snapshot',async()=>{
  const f=fixture(),initial=await f.loader.current(f.evidence);f.denied.add(MAP_REF);
  await assert.rejects(f.loader.current(f.evidence,{reuse:initial}),/不可读/);
  f.denied.delete(MAP_REF);f.setRead(()=>{throw new Error('transport failed');});
  await assert.rejects(f.loader.current(f.evidence,{reuse:initial}),/transport failed/);
});
