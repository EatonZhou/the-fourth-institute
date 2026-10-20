import test from 'node:test';
import assert from 'node:assert/strict';
import {createProjectionSimulation} from '../src/architecture/projection-mechanisms.model.js';
import {readProjectionFixture} from './architecture-projection-mechanisms.fixture.mjs';
const f = readProjectionFixture();
const simulation = id => createProjectionSimulation(f.content('web.m.' + id));
const finish = (model, caseId) => { model.choose(caseId); while (!model.getScene().complete) model.next(); return model.getScene(); };
test('the fixed package and every projected source are byte/line verified; all declared cases replay', () => {
  for (const mechanism of f.mechanisms) {
    const model = createProjectionSimulation(f.content(mechanism.id));
    for (const c of mechanism.cases) {
      const scene = finish(model, c.id);
      assert.equal(scene.outcome, c.outcome);
      assert.equal(scene.status, 'simulated');
      assert.equal(scene.frames.length, c.path.filter(x => x !== 'load_case_inputs').length);
    }
  }
});
test('unknown package and factory failure release the attempted instance; visible set follows creation', () => {
  const model = simulation('panel-lifecycle');
  const missing = finish(model, 'missing'); assert.equal(missing.state.instance, false); assert.equal(missing.state.cleanup, true);
  assert.equal(finish(model, 'create-failure').state.visible, false);
  model.choose('show'); model.next(); assert.equal(model.getScene().state.instance, false);
  model.next(); assert.equal(model.getScene().state.instance, true); assert.equal(model.getScene().state.visible, false);
  model.next(); assert.equal(model.getScene().state.visible, true);
});
test('binding scope rejects foreign Resident, unconfirmed mutation and streaming before owner dispatch', () => {
  const model = simulation('projection-scope');
  for (const id of ['foreign', 'unconfirmed', 'stream']) assert.equal(finish(model,id).state.dispatched,false);
  const ok = finish(model,'ok'); assert.equal(ok.state.dispatched,true); assert.equal(ok.state.lineage_source,'app.binding');
});
test('old focus, wrong source and wrong channel preserve revision and do not notify', () => {
  const model = simulation('focus-revision');
  for (const id of ['late','channel','source']) {
    const scene = finish(model,id); assert.equal(scene.state.focus_revision,2); assert.equal(scene.state.accepted,false);
  }
  assert.equal(finish(model,'newer').state.focus_revision,3);
});
test('confirmation pauses only during the original pending operation; changed context never reaches send', () => {
  const model = simulation('feedback-confirmation');
  model.choose('confirmed'); model.next(); model.next(); assert.equal(model.getScene().state.timer_paused,true);
  model.next(); assert.equal(model.getScene().state.would_send,true); model.next(); assert.equal(model.getScene().state.timer_paused,false);
  for (const id of ['focus','cancel','binding','simulated']) {
    const scene = finish(model,id); assert.equal(scene.state.would_send,false); assert.equal(scene.state.busy,false);
  }
});
test('restore is batch-bound, atomic and clone-isolated; unsupported rules fail closed', () => {
  const content=f.content('web.m.focus-revision'), model=createProjectionSimulation(content);
  model.choose('late');model.next();const saved=model.getViewState();model.next();model.restore(saved);assert.deepEqual(model.getViewState(),saved);
  assert.throws(()=>model.restore({...saved,batchKey:'other'}),/restore_mismatch/);assert.deepEqual(model.getViewState(),saved);
  const scene=model.getScene();scene.state.focus_revision=99;assert.equal(model.getScene().state.focus_revision,2);
  const bad=structuredClone(content);bad.mechanism.steps[0].state_changes[0].op='execute';assert.throws(()=>createProjectionSimulation(bad),/unsupported_change/);
  const missing=structuredClone(content);missing.evidence=[];assert.throws(()=>createProjectionSimulation(missing),/evidence_missing/);
});
test('a pending confirmation can compare only declared branches with the same completed prefix', () => {
  const model=simulation('feedback-confirmation');
  model.choose('confirmed');model.next();model.next();const waiting=model.getViewState();
  for(const id of ['confirmed','cancel','focus','binding']) {
    model.restore(waiting);model.branch(id);assert.equal(model.getScene().position,2);model.next();
    assert.equal(model.getScene().state.would_send,id==='confirmed');
  }
  model.restore(waiting);assert.throws(()=>model.branch('simulated'),/branch_mismatch/);
  assert.deepEqual(model.getViewState(),waiting);assert.equal(model.getScene().state.timer_paused,true);
});
test('semantic inputs need both evidence and current readability before entering displayed objects',()=>{
  const model=simulation('semantic-validation');
  for(const id of ['unproved','forbidden']){
    const scene=finish(model,id);assert.equal(scene.state.accepted,false);assert.equal(scene.state.revealed,false);
    assert.equal(scene.frames.some(f=>f.step.id==='resolve'),id==='forbidden');
  }
  model.choose('current');model.next();model.next();assert.equal(model.getScene().state.revealed,false);
  model.next();assert.equal(model.getScene().state.accepted,true);assert.equal(model.getScene().state.revealed,true);
});
test('generation save recovery preserves the one applied update and refuses changed content or revision',()=>{
  const model=simulation('generation-save');model.choose('resume');model.next();model.next();model.next();
  assert.deepEqual([model.getScene().state.revision,model.getScene().state.writes,model.getScene().state.pending,model.getScene().state.receipt],[8,1,true,false]);
  model.next();assert.equal(model.getScene().state.writes,1);model.next();assert.equal(model.getScene().state.pending,false);assert.equal(model.getScene().state.receipt,true);
  for(const id of ['payload','revision']){const scene=finish(model,id);assert.equal(scene.state.writes,0);assert.equal(scene.state.receipt,false);assert.notEqual(scene.state.error,'');}
});
test('interface associations replace a single value, preserve the old value on rejection and grant no access',()=>{
  const model=simulation('ui-association'),saved=finish(model,'save');
  assert.deepEqual(saved.state.value,{resident_id:'selected'});assert.equal(saved.state.written,true);assert.equal(saved.state.permissions_granted,false);
  const rejected=finish(model,'limit');assert.deepEqual(rejected.state.value,{resident_id:'previous'});assert.equal(rejected.state.written,false);assert.equal(rejected.state.permissions_granted,false);
});
