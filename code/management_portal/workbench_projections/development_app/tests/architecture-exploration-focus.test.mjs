import test from 'node:test';
import assert from 'node:assert/strict';
import { createExplorationFocus, explorationSemanticObject, readExecutionCapabilityFocus } from '../src/architecture/exploration-focus.js';
import { MAP_REF } from '../src/architecture/model.js';
const object = { ref: 'repo/file/map', kind: 'module:worker', status: 'current', source: 'repo/file/map', observed_at: '2026-10-15T12:00:00Z' };
const mappedObject = (id, kind = 'module') => ({ id, kind, identity: {
  focus_owner_id: id, focus_kind: `${kind}:${id}`, resource_ref: object.source, local_only: false,
} });
const worker = mappedObject('worker'), lease = mappedObject('worker-lease', 'protocol');
const bundle = { mode: 'current', model: { objects: [worker, lease] } };
const map = 'modules:\n  - id: worker\n    responsibility: worker\nprotocols:\n  - id: worker-lease\n';
test('current module and protocol focus use exact map rows and only the five public fields', () => {
  assert.deepEqual(explorationSemanticObject(bundle, worker, map, object.observed_at), { ...object, ref: object.ref + '#L2' });
  assert.deepEqual(explorationSemanticObject(bundle, lease, map, object.observed_at), { ...object, kind: 'protocol:worker-lease', ref: object.ref + '#L5' });
  assert.equal(explorationSemanticObject(bundle, worker, '# new map preamble\n' + map, object.observed_at).ref, object.ref + '#L3');
});
test('missing map rows, historical reads, local concepts and unknown owners never invent semantic identities', () => {
  assert.equal(explorationSemanticObject(bundle, worker, 'modules:\n', object.observed_at).ref, object.source);
  assert.equal(explorationSemanticObject({ ...bundle, mode: 'history' }, worker, map, object.observed_at), null);
  assert.equal(explorationSemanticObject(bundle, { ...worker, id: 'worker.runtime', kind: 'concept', identity: { ...worker.identity, local_only: true } }, map, object.observed_at), null);
  assert.equal(explorationSemanticObject(bundle, { ...worker, identity: { ...worker.identity, focus_owner_id: 'unknown' } }, map, object.observed_at), null);
  assert.equal(explorationSemanticObject(bundle, { ...worker, identity: { ...worker.identity, focus_kind: 'module:execution' } }, map, object.observed_at), null);
});

test('host broadcast before focus reply does not turn a selection into navigation', async () => {
  const received = []; let focus;
  focus = createExplorationFocus({ accept: value => received.push(value), publish: async value => { focus.receive(value, { revision: 1 }); return { object: value, revision: 1 }; } });
  await focus.send(object); assert.equal(received.length, 0);
  focus.receive(object, { revision: 2 }); assert.equal(received.length, 1);
});
test('late own echo cannot interrupt a newer mechanism; later external same-object focus remains accepted', async () => {
  const received = []; let reply;
  const focus = createExplorationFocus({ accept: (_, metadata) => received.push(metadata.revision), publish: () => new Promise(resolve => { reply = resolve; }) });
  const sent = focus.send(object);
  focus.receive({ ...object, kind: 'module:execution' }, { revision: 2 });
  focus.receive(object, { revision: 3 }); reply({ revision: 3 }); await sent;
  focus.receive(object, { revision: 4 }); focus.receive(object, { revision: 2 });
  assert.deepEqual(received, [2, 4]);
});
test('same fields from an independent revision are not swallowed while publication is pending', async () => {
  const received = []; let reply;
  const focus = createExplorationFocus({ accept: (_, metadata) => received.push(metadata.revision), publish: () => new Promise(resolve => { reply = resolve; }) });
  const sent = focus.send(object); focus.receive(object, { revision: 7 }); reply({ revision: 6 }); await sent;
  assert.deepEqual(received, [7]);
});
test('destruction discards deferred focus delivery', async () => {
  const received = []; let reply;
  const focus = createExplorationFocus({ accept: value => received.push(value), publish: () => new Promise(resolve => { reply = resolve; }) });
  const sent = focus.send(object); focus.receive(object, { revision: 3 }); focus.destroy(); reply({ revision: 2 }); await sent;
  assert.equal(received.length, 0);
});
test('a broadcast delivered after the acknowledgement is still the same self revision', async () => {
  const received = [];
  const focus = createExplorationFocus({ accept: value => received.push(value), publish: async () => ({ revision: 9 }) });
  await focus.send(object); focus.receive(object, { revision: 9 }); assert.equal(received.length, 0);
  focus.receive(object, { revision: 10 }); assert.equal(received.length, 1);
});

function capabilityRig(text = 'modules:\n  - id: execution\n  - id: worker\n  - id: providers\n') {
  const pin = { address:'workspace_fixed/file/modules.yaml', sha256:'obsolete', size:0 };
  const batch = { mode:'current', binding:{module_map:pin}, model:{objects:['execution','worker','providers'].map(id=>({...mappedObject(id),parent_id:null}))} };
  const calls=[], control={readable:true,text};
  const resources={read:async ref=>{calls.push(['read',ref]);assert.equal(ref,MAP_REF);return {readable:control.readable,data:control.text,error:control.readable?undefined:{code:'permission_denied'}};}};
  return {batch,pin,resources,calls,control};
}
test('capability focus reads the workspace map with no source revision gate',async()=>{
  const r=capabilityRig(), value=await readExecutionCapabilityFocus(r.batch,r.resources);
  assert.deepEqual(Object.keys(value).sort(),['kind','observed_at','ref','source','status']);
  assert.equal(value.ref,MAP_REF);assert.equal(value.source,MAP_REF);assert.equal(value.status,'current');
  assert.equal(value.kind,'architecture-view:model-execution[execution,worker,providers]');
  assert.deepEqual(r.calls,[['read',MAP_REF]]);
  r.control.readable=false;await assert.rejects(readExecutionCapabilityFocus(r.batch,r.resources),{code:'permission_denied'});
});
test('capability focus tolerates map drift while keeping the documented object identity',async()=>{
  const r=capabilityRig();r.control.text+='changed';assert(await readExecutionCapabilityFocus(r.batch,r.resources));
  const absent=capabilityRig('modules: []');assert(await readExecutionCapabilityFocus(absent.batch,absent.resources));
  const mixed=capabilityRig();mixed.batch.model.objects[1].identity.resource_ref='other-map';await assert.rejects(readExecutionCapabilityFocus(mixed.batch,mixed.resources),/invalid_capability_members/);assert.equal(mixed.calls.length,0);
});
test('superseded capability selections and historical mode do not produce focus',async()=>{
  const r=capabilityRig();assert.equal(await readExecutionCapabilityFocus(r.batch,r.resources,()=>false),null);
  r.calls.length=0;assert.equal(await readExecutionCapabilityFocus({...r.batch,mode:'history'},r.resources),null);assert.equal(r.calls.length,0);
});
