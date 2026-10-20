import test from 'node:test';
import assert from 'node:assert/strict';
import {LINEAGE_OBJECT_ENTRIES, getLineageObjectModes, resolveLineageObjectEntry} from '../src/architecture/lineage-mechanisms.entries.js';
import {resolveDomainEntry} from '../src/architecture/exploration-entry-routes.js';
import {readLineageFixture} from './architecture-lineage-mechanisms.fixture.mjs';
const {model,content} = readLineageFixture();
const input = () => structuredClone({...content,mechanisms:model.mechanisms});

test('explicit declarations cover the frozen Lineage objects without inventing a multi-mechanism leaf', () => {
  const ids = new Set(model.mechanisms.filter(m => m.adapter === 'lineage.declarative-transitions.v1').map(m => m.id));
  const objects = model.objects.filter(o => o.mechanism_ids?.some(id => ids.has(id)));
  assert.deepEqual(Object.keys(LINEAGE_OBJECT_ENTRIES).sort(),objects.map(o => o.id).sort());
  for (const object of objects) {
    const entry = LINEAGE_OBJECT_ENTRIES[object.id];
    assert.deepEqual([...entry.mechanismIds].sort(),[...object.mechanism_ids].sort(),object.id);
    if (object.child_ids.length) assert.equal(entry.defaultMechanismId,null,object.id);
    else {
      assert.equal(object.mechanism_ids.length,1,object.id);
      assert(object.mechanism_ids.includes(entry.defaultMechanismId),object.id);
    }
  }
});
test('protocol defaults enter their specific body and never alias Effect', () => {
  const expected = {
    'operation-identity':'mechanism.lineage.operation-identity',
    'society-service':'mechanism.lineage.rpc-dispatch',
    'lineage-events-effects':'mechanism.lineage.event-effect-pipeline',
  };
  for (const [objectId,mechanismId] of Object.entries(expected)) {
    assert.equal(LINEAGE_OBJECT_ENTRIES[objectId].defaultMechanismId,mechanismId);
    assert.deepEqual(resolveDomainEntry(model,{kind:'protocol',objectId,mechanismId:null},expected),{kind:'protocol',objectId,mechanismId});
    const c = input();c.objects.find(o => o.id === objectId).mechanism_ids.unshift('mechanism.lineage.effect-journal');
    assert.deepEqual(getLineageObjectModes(objectId,c).map(m=>m.mechanismId),[mechanismId]);
    assert.deepEqual(resolveLineageObjectEntry(objectId,c).route,{kind:'protocol',objectId,mechanismId});
  }
});
test('nonleaf composition is stable across array reorder; Effect remains a named body', () => {
  const c = input(),objectId = 'lineage.automation.effects';
  c.objects.find(o => o.id === objectId).mechanism_ids.reverse();c.mechanisms.reverse();
  const entry = resolveLineageObjectEntry(objectId,c);
  assert.deepEqual(entry.route,{kind:'composition',objectId,mechanismId:null});
  assert.deepEqual(entry.modes.map(m => m.mechanismId),['mechanism.lineage.effect-journal','mechanism.lineage.script-runs']);
  assert.deepEqual(entry.modes[0].route,{kind:'mechanism',objectId,mechanismId:'mechanism.lineage.effect-journal'});
  assert.equal(entry.modes[0].title,content.mechanism.title);
  assert.equal(resolveDomainEntry(model,entry.route,{[objectId]:'mechanism.lineage.effect-journal'}).kind,'composition');
});
test('identity, membership and source availability gate modes before they are shown', () => {
  const c = input(),objectId = 'lineage.automation.effects';
  assert.deepEqual(getLineageObjectModes(objectId+'.alias',c),[]);
  assert.equal(resolveLineageObjectEntry('toString',c),null);
  assert.deepEqual(getLineageObjectModes(objectId,{...c,batchKey:''}),[]);
  c.objects.find(o => o.id === objectId).mechanism_ids = ['mechanism.lineage.script-runs'];
  assert.deepEqual(getLineageObjectModes(objectId,c).map(m=>m.mechanismId),['mechanism.lineage.script-runs']);
  c.objects.find(o => o.id === objectId).mechanism_ids = [];
  assert.deepEqual(getLineageObjectModes(objectId,c),[]);
  const denied = input();denied.evidence=[];
  assert.deepEqual(getLineageObjectModes(objectId,denied),[]);
  const duplicate = input();duplicate.objects.push(duplicate.objects.find(o => o.id === objectId));
  assert.equal(resolveLineageObjectEntry(objectId,duplicate),null);
  const topology = input();topology.objects.find(o => o.id === objectId).child_ids=[];
  assert.equal(resolveLineageObjectEntry(objectId,topology),null);
});
