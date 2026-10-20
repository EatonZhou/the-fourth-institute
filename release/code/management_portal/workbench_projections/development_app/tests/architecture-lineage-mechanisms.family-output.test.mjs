import test from 'node:test';
import assert from 'node:assert/strict';
import {createLineageSimulation} from '../src/architecture/lineage-mechanisms.model.js';
import {lineageReadyObjectEntries, resolveLineageObjectEntry} from '../src/architecture/lineage-mechanisms.entries.js';
import {readLineageFixture} from './architecture-lineage-mechanisms.fixture.mjs';
const {model,content} = readLineageFixture();
const payload = id => structuredClone({...content,mechanisms:model.mechanisms,mechanism:model.mechanisms.find(m=>m.id===id)});
const entries={
  'lineage.conversation.outputs':'mechanism.lineage.output-convergence',
  'lineage.family.admission':'mechanism.lineage.family-admission',
  'lineage.family.projection':'mechanism.lineage.family-projection',
};
test('implemented leaf entries open their own body and never change nonleaf composition',()=>{
  for(const [objectId,id] of Object.entries(entries))assert.equal(lineageReadyObjectEntries[objectId],id);
  for(const [objectId,id] of Object.entries(entries)){
    const p=payload(id),entry=resolveLineageObjectEntry(objectId,p);
    assert.deepEqual(entry.route,{kind:'mechanism',objectId,mechanismId:id});
    assert.equal(createLineageSimulation(p).getScene().mechanismId,id);
  }
  assert.equal(resolveLineageObjectEntry('lineage.family',payload(entries['lineage.family.projection'])).route.kind,'composition');
});
test('each new workface refuses missing relationship, wrong endpoints or missing rendering fields',()=>{
  for(const id of Object.values(entries)){
    const p=payload(id),rid=p.mechanism.steps.find(s=>s.relation_id).relation_id;
    p.relations=p.relations.filter(r=>r.id!==rid);
    assert.throws(()=>createLineageSimulation(p),/relation_unavailable/);
    const q=payload(id);q.relations.find(r=>r.id===rid).from='lineage';
    assert.throws(()=>createLineageSimulation(q),/relation_unavailable/);
    const f=payload(id);f.mechanism.state_fields=f.mechanism.state_fields.filter(v=>v.id!=='phase');
    assert.throws(()=>createLineageSimulation(f),/workface_field_missing/);
  }
});
test('new workfaces preserve their own local state through parent remount and reject another batch',()=>{
  for(const [id,scenario,steps] of [
    ['mechanism.lineage.family-admission','approved',['admission']],
    ['mechanism.lineage.family-projection','normal',['resolve','read','project']],
    ['mechanism.lineage.output-convergence','ack-failed',['authorize','append','confirm','ack-pending']],
  ]){
    const p=payload(id),a=createLineageSimulation(p);a.choose(scenario);for(const step of steps)a.perform(step);
    const saved=a.getViewState(),b=createLineageSimulation(p);b.restore(saved);
    assert.deepEqual(b.getScene(),a.getScene());
    assert.throws(()=>b.restore({...saved,batchKey:'obsolete'}),/restore_batch/);
  }
});
