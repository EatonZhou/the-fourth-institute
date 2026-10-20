import test from 'node:test';
import assert from 'node:assert/strict';
import {createLineageSimulation,supportsLineageMechanism} from '../src/architecture/lineage-mechanisms.model.js';
import {readLineageFixture} from './architecture-lineage-mechanisms.fixture.mjs';
const fixture=readLineageFixture();
const content=()=>structuredClone(fixture.content);

test('implemented identity is narrower than the shared adapter',()=>{
 const c=content();assert(supportsLineageMechanism(c.mechanism));
 c.mechanism.id='mechanism.lineage.packet';assert(!supportsLineageMechanism(c.mechanism));
 assert.throws(()=>createLineageSimulation(c),/mechanism_not_implemented/);
});
test('restoration is pinned and replayed, never a supplied committed result',()=>{
 const s=createLineageSimulation(content());s.choose('unknown');s.perform('begin');s.perform('call');s.perform('unknown');
 const saved=s.getViewState();s.choose('normal');
 s.restore({...saved,state:{call_state:'committed'},inputs:{call_outcome:'committed'}});
 assert.equal(s.getScene().state.call_state,'unresolved');assert.deepEqual(s.available(),[]);
 const before=s.getScene();
 assert.throws(()=>s.restore({...saved,batchKey:'changed-source'}),/restore_batch/);
 assert.throws(()=>s.restore({...saved,history:[...saved.history,'commit']}),/不允许/);
 assert.deepEqual(s.getScene(),before);
});
test('incomplete authorization payload is refused before labels or sources are used',()=>{
 const c=content();c.evidence=c.evidence.filter(e=>e.id!==c.mechanism.evidence_ids[0]);
 assert.throws(()=>createLineageSimulation(c),/evidence_unavailable/);
 const d=content();d.objects=d.objects.filter(o=>o.id!==d.mechanism.owner_id);
 assert.throws(()=>createLineageSimulation(d),/owner_unavailable/);
 const e=content();e.mechanism.steps[1].to='unavailable';
 assert.throws(()=>createLineageSimulation(e),/endpoint_unavailable/);
});
test('caller mutations cannot alter the pinned simulation or its returned snapshots',()=>{
 const c=content(),s=createLineageSimulation(c),title=s.getContent().mechanism.title;
 c.mechanism.title='changed';s.getContent().mechanism.title='changed again';
 s.getSession().state.call_state='committed';
 assert.equal(s.getContent().mechanism.title,title);assert.notEqual(s.getScene().state.call_state,'committed');
});
