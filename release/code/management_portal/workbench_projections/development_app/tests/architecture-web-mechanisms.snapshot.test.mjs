import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createWebSimulation} from '../src/architecture/web-mechanisms.model.js';
import {WEB_VIEWS} from '../src/architecture/web-mechanisms.views.js';
import {cursorEndLabel} from '../src/architecture/web-mechanisms.cursors.js';

const fixture = JSON.parse(fs.readFileSync(new URL('./architecture-web-mechanisms.snapshot.fixture.json',import.meta.url)));
function start(caseId) { const s=createWebSimulation(fixture);s.choose(caseId);return s; }
function until(sim, step) { while(sim.getScene().stepId!==step && !sim.getScene().complete)sim.advance();assert.equal(sim.getScene().stepId,step);return sim.getScene(); }
function end(sim) { while(!sim.getScene().complete)sim.advance();return sim.getScene(); }
function view(scene) { return WEB_VIEWS['resident-cursors'](scene,fixture.mechanism); }

for (const caseId of ['gap','omitted']) test(`${caseId}: arrival is distinct from installation, both cursors retain authority`,()=>{
  const sim=start(caseId), request=until(sim,'request');
  assert.equal(request.state.conversation_cursor,10);assert.equal(request.state.state_cursor,12);
  assert.deepEqual(request.state.message_ids,['demo.m10']);assert.equal(request.state.snapshot_reads,1);
  const arrived=until(sim,'return');
  assert.equal(arrived.state.returned_cursor,12);assert.equal(arrived.state.snapshot_accepted,false);
  assert.deepEqual(arrived.state.returned_message_ids,['demo.m10','demo.m11','demo.m12']);
  assert.deepEqual(arrived.state.message_ids,['demo.m10']);assert.match(view(arrived),/已到达，等待核验/);
  const accepted=until(sim,'accept');
  assert.equal(accepted.state.conversation_cursor,12);assert.equal(accepted.state.state_cursor,12);
  assert.equal(accepted.state.snapshot_accepted,true);assert.deepEqual(accepted.state.message_ids,arrived.state.returned_message_ids);
  assert.match(view(accepted),/本地模拟/);assert.match(view(accepted),/不代表全部历史/);
  sim.previous();assert.equal(sim.getScene().state.snapshot_accepted,false);assert.deepEqual(sim.getScene().state.message_ids,['demo.m10']);
});
for (const caseId of ['actor_changed','wrong_resident','released']) test(`${caseId}: returned window never overwrites the shared window`,()=>{
  const sim=start(caseId), scene=end(sim);
  assert.equal(scene.state.conversation_cursor,10);assert.equal(scene.state.state_cursor,12);
  assert.equal(scene.state.installed_snapshot_label,'demo.snapshot@10');assert.equal(scene.state.snapshot_accepted,false);
  assert.deepEqual(scene.state.message_ids,['demo.m10']);assert.notEqual(scene.state.returned_snapshot_label,'');
  if(caseId==='actor_changed'){assert.equal(scene.state.actor_revision,2);assert.equal(scene.state.request_actor_revision,1);assert.match(view(scene),/旧返回不能借新身份安装/);}
  if(caseId==='wrong_resident'){assert.equal(scene.state.returned_resident_id,'demo.other');assert.match(view(scene),/返回目标不匹配/);}
  if(caseId==='released'){assert.equal(scene.state.ref_count,0);assert.match(cursorEndLabel(scene),/未安装/);assert.match(view(scene),/仅返回给调用者/);}
});
test('pending contiguous event extends the installed window without moving the independent state cursor',()=>{
  const sim=start('pending_contiguous');until(sim,'accept');const scene=end(sim);
  assert.equal(scene.state.returned_cursor,12);assert.equal(scene.state.conversation_cursor,13);assert.equal(scene.state.state_cursor,12);
  assert.deepEqual(scene.state.message_ids,['demo.m10','demo.m11','demo.m12','demo.m13']);
  assert.match(view(scene),/连续事件 13 已应用/);assert.equal(scene.state.snapshot_reads,1);
});
test('a second gap retains accepted window 12 and schedules recovery without claiming event 14 or another snapshot',()=>{
  const sim=start('pending_gap'),scene=end(sim);
  assert.equal(scene.state.snapshot_accepted,true);assert.equal(scene.state.recovery_requested,true);
  assert.equal(scene.state.conversation_cursor,12);assert.equal(scene.state.discarded_event_cursor,14);assert.equal(scene.state.snapshot_reads,1);
  assert.deepEqual(scene.state.message_ids,['demo.m10','demo.m11','demo.m12']);
  assert.match(view(scene),/缺少 13/);assert.match(cursorEndLabel(scene),/尚无下一批/);
  const saved=sim.getViewState();sim.reset();sim.restore(saved);assert.deepEqual(sim.getScene(),scene);
  assert.throws(()=>sim.restore({...saved,batchKey:'other'}),/invalid_restore/);
});
test('new snapshot sources are required and incoming labels are escaped',()=>{
  const missing=structuredClone(fixture);missing.evidence=missing.evidence.filter(e=>e.id!=='projection.e.snapshot-accept');
  assert.throws(()=>createWebSimulation(missing),/evidence_unavailable/);
  const scene=end(start('wrong_resident'));scene.state.returned_resident_id='<img onerror=bad>';
  assert.doesNotMatch(view(scene),/<img/);assert.match(view(scene),/&lt;img/);
});
