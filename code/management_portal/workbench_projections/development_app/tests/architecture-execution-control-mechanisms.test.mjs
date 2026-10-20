import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createExecutionControlSimulation as create,EXECUTION_CONTROL_FIELDS,supportsExecutionControlMechanism} from '../src/architecture/execution-control-mechanisms.model.js';
const fixture=JSON.parse(readFileSync(new URL('./architecture-execution-control-mechanisms.fixture.json',import.meta.url)));
const batchKey=fixture.capture.pair.sha256;
const content=id=>({mechanism:fixture.mechanisms.find(m=>m.id===`control.${id}`),objects:fixture.objects,evidence:fixture.evidence,batchKey});
function run(id,caseId,stop){const sim=create(content(id));sim.selectCase(caseId);while(sim.getScene().next){sim.advance(sim.getScene().next.id);if(sim.getScene().current.id===stop)break;}return sim;}
test('exact eight IDs; another control adapter user is rejected',()=>{
  assert.equal(Object.keys(EXECUTION_CONTROL_FIELDS).length,8);
  const c=structuredClone(content('submission'));c.mechanism.id='control.admission';
  assert.equal(supportsExecutionControlMechanism(c.mechanism),false);assert.throws(()=>create(c),/unsupported_mechanism/);
});
test('formal 32 paths terminate, back recomputes, original facts remain immutable',()=>{
  const original=JSON.stringify(fixture);let count=0;
  for(const m of fixture.mechanisms)for(const c of m.cases){const sim=run(m.id.slice(8),c.id),end=sim.getScene();assert.equal(end.next,null);assert.equal(end.outcome,c.outcome);
    const saved=sim.getViewState();sim.back();sim.advance(c.path.at(-1));assert.deepEqual(sim.getScene(),end);sim.restore(saved);assert.deepEqual(sim.getScene(),end);count++;}
  assert.equal(count,32);assert.equal(JSON.stringify(fixture),original);
});
test('lost reply reuses one queue row and preparation; conflicting digest changes neither',()=>{
  const a=run('submission','回执丢失重试').getScene();assert.equal(a.state.queue_rows,1);assert.equal(a.state.prepare_count,1);assert.equal(a.state.result,'existing');
  const b=run('submission','ID冲突').getScene();assert.equal(b.state.queue_rows,0);assert.equal(b.state.prepare_count,0);assert.equal(b.state.result,'digest_conflict');
});
test('RPC denial stops before dispatch; preparation failure and bypass have distinct paths',()=>{
  for(const id of ['actor不符','未认证','参数类型错'])assert.equal(run('rpc-dispatch',id).getScene().state.dispatched,false);
  assert.equal(run('preparation','准备失败').getScene().state.queued,false);
  const bypass=run('preparation','无状态服务').getScene();assert.equal(bypass.state.queued,true);assert.ok(!bypass.prefix.includes('session'));
  assert.equal(run('preparation','继续已有会话').getScene().state.route,'bound_session_route');
});
test('local singleflight waits for predecessor and releases current slot, cancellation never acquires',()=>{
  const wait=run('runtime-slot','等待前任','wait');assert.equal(wait.getScene().state.predecessor_active,false);assert.equal(wait.getScene().state.slot_acquired,false);
  wait.advance('acquire');assert.equal(wait.getScene().state.slot_acquired,true);wait.advance('finally');assert.equal(wait.getScene().state.slot_acquired,false);
  assert.ok(!run('runtime-slot','等待时取消').getScene().prefix.includes('acquire'));
  assert.equal(run('runtime-slot','等待时换代').getScene().state.outcome,'ExecutionRecoveryDeferred');
});
test('completed checkpoint recovery sends no new native input; cancellation preserves written content and unknown binding',()=>{
  const s=run('runtime-finalization','完成后恢复').getScene().state;assert.equal(s.provider_call_count,0);assert.equal(s.service_called,false);assert.equal(s.result_recovered,true);
  const c=run('runtime-finalization','内容后取消').getScene().state;assert.equal(c.content_phase,'finalized');assert.equal(c.binding_phase,'unknown');assert.equal(c.outcome,'ExecutionCancelled');
  const d=run('runtime-finalization','完成后交接').getScene().state;assert.equal(d.checkpoint_phase,'provider_completed');assert.equal(d.content_phase,'not_finalized');
});
test('direct Provider path still prepares Harness; observation timeout neither cancels nor resubmits',()=>{
  const a=run('agent-service','直接路径').getScene().state;assert.equal(a.native_options_prepared,true);assert.equal(a.harness_prepared,true);assert.equal(a.path,'direct_callback');
  const b=run('request-protocol','观察超时').getScene().state;assert.equal(b.cancel_sent,false);assert.equal(b.resubmitted,false);assert.equal(b.acknowledged,false);
  const c=run('request-protocol','正常确认').getScene().state;assert.equal(c.acknowledged,true);assert.equal(c.result_retained,true);
});
test('chat constructs replace or append and rejects invalid requests before network',()=>{
  assert.equal(run('chat-service','末条替换').getScene().state.messages,'replace_last_user');
  assert.equal(run('chat-service','追加user').getScene().state.messages,'append_user');
  for(const c of ['参数错误','缺模型','调用前取消'])assert.equal(run('chat-service',c).getScene().state.network_called,false);
});
test('cross-case, cross-batch and arbitrary state cannot bypass official prefix/guards',()=>{
  const sim=run('submission','首次成功');const saved=sim.getViewState();assert.throws(()=>sim.restore({...saved,batchKey:'other'}),/restore_scope/);
  assert.throws(()=>sim.restore({...saved,caseId:'unknown'}),/restore_scope/);assert.throws(()=>sim.advance('same'),/step_not_next/);
  sim.restore({...saved,state:{queue_rows:99}});assert.equal(sim.getScene().state.queue_rows,1);
  const bad=structuredClone(content('submission'));bad.mechanism.cases[0].inputs.existing=true;assert.throws(()=>create(bad),/guard_blocked/);
  const missing=structuredClone(content('submission'));missing.evidence=[];assert.throws(()=>create(missing),/evidence_unavailable/);
  const authority=structuredClone(content('submission'));authority.objects=authority.objects.filter(o=>o.id!=='execution.scheduling');assert.throws(()=>create(authority),/unavailable/);
  const malformed=structuredClone(content('submission'));malformed.mechanism.steps[0].when=false;assert.throws(()=>create(malformed),/guard/);
});
