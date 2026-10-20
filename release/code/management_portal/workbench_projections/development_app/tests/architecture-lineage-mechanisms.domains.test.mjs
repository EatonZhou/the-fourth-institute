import test from 'node:test';
import assert from 'node:assert/strict';
import {createLineageSimulation,lineageSupportedMechanisms} from '../src/architecture/lineage-mechanisms.model.js';
import {lineageReadyObjectEntries,LINEAGE_OBJECT_ENTRIES,resolveLineageObjectEntry} from '../src/architecture/lineage-mechanisms.entries.js';
import {lineageDomainViews} from '../src/architecture/lineage-mechanisms.views.js';
import {readLineageFixture} from './architecture-lineage-mechanisms.fixture.mjs';
const {model,content}=readLineageFixture();
const payload=m=>structuredClone({...content,mechanism:m,mechanisms:model.mechanisms});
test('every authored new mechanism has a distinct workface and exact defaults preserve composition',()=>{
  assert.equal(new Set(Object.values(lineageDomainViews)).size,Object.keys(lineageDomainViews).length);
  const ids=model.mechanisms.filter(m=>m.id.startsWith('mechanism.lineage.')).map(m=>m.id).sort();
  assert.deepEqual([...lineageSupportedMechanisms].sort(),ids);
  for(const object of model.objects.filter(o=>Object.hasOwn(LINEAGE_OBJECT_ENTRIES,o.id))){
    const result=resolveLineageObjectEntry(object.id,payload(content.mechanism));
    assert(result,object.id);
    if(object.child_ids.length)assert.equal(result.route.kind,'composition');
    else {assert.equal(result.route.mechanismId,lineageReadyObjectEntries[object.id]);assert(object.mechanism_ids.includes(result.route.mechanismId));}
  }
});
test('new bodies require their fixed fields, steps and same-batch source endpoints',()=>{
  for(const id of Object.keys(lineageDomainViews)){
    const m=model.mechanisms.find(m=>m.id===id);assert(m,id);createLineageSimulation(payload(m));
    const f=payload(m);f.mechanism.state_fields=f.mechanism.state_fields.filter(x=>x.id!=='phase');assert.throws(()=>createLineageSimulation(f),/workface_field_missing/,id);
    const s=payload(m);s.mechanism.steps=s.mechanism.steps.filter(x=>x.id!==m.steps.at(-1).id);
    // Removing a step either violates the workface contract or leaves a next edge dangling.
    assert.throws(()=>createLineageSimulation(s),{message:/^lineage:(?:workface_step_missing|next_unavailable)$/},id);
    const e=payload(m);e.evidence=e.evidence.filter(x=>x.id!==m.evidence_ids[0]);assert.throws(()=>createLineageSimulation(e),/evidence_unavailable/,id);
  }
});
test('new mechanism scenes survive parent restoration without trusting caller state',()=>{
  for(const id of Object.keys(lineageDomainViews)){
    const m=model.mechanisms.find(m=>m.id===id), c=m.cases[0], s=createLineageSimulation(payload(m));
    for(const step of c.path.slice(1))s.perform(step);
    const saved=s.getViewState(),next=createLineageSimulation(payload(m));next.restore({...saved,state:{phase:'invented'}});
    assert.deepEqual(next.getScene(),s.getScene(),id);assert.throws(()=>next.restore({...saved,batchKey:'other'}),/restore_batch/);
  }
});
