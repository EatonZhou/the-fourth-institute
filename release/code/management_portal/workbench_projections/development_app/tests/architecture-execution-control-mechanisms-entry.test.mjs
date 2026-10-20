import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executionControlObjectPlan,executionControlObjectDefaults,EXECUTION_CONTROL_OBJECT_STATE} from '../src/architecture/execution-control-mechanisms.entry-model.js';
import {createExecutionControlSimulation} from '../src/architecture/execution-control-mechanisms.model.js';
const fixture=JSON.parse(readFileSync(new URL('./architecture-execution-control-mechanisms.fixture.json',import.meta.url)));
const content={...fixture,batchKey:fixture.capture.pair.sha256};
function saved(plan,id){return createExecutionControlSimulation(plan.getContent(id)).getViewState();}
test('named reading intent selects default independently of mechanism array order',()=>{
  const client=executionControlObjectPlan('execution.client',{...content,mechanisms:[...content.mechanisms].reverse()});
  const protocol=executionControlObjectPlan('resident-execution',content);
  assert.equal(client.defaultMechanismId,'control.submission');assert.equal(protocol.defaultMechanismId,'control.request-protocol');
  assert.deepEqual(client.modes.map(m=>m.mechanismId),['control.submission','control.request-protocol']);
  assert.equal(executionControlObjectDefaults['worker.runtime'],undefined);
  assert.throws(()=>executionControlObjectPlan('worker.runtime',content),/object_entry/);
});
test('unauthorized default cannot fall through to a different available mode',()=>{
  assert.throws(()=>executionControlObjectPlan('execution.client',{...content,mechanisms:content.mechanisms.filter(m=>m.id!=='control.submission')}),/default_unavailable/);
  const plan=executionControlObjectPlan('execution.client',{...content,mechanisms:content.mechanisms.filter(m=>m.id!=='control.request-protocol')});
  assert.equal(plan.modes.length,1);assert.throws(()=>plan.getContent('control.request-protocol'),/mode_unavailable/);
});
test('object state validates every inactive mode and preserves separate prefixes',()=>{
  const plan=executionControlObjectPlan('execution.client',content),id=plan.defaultMechanismId;
  const snapshot={schema:EXECUTION_CONTROL_OBJECT_STATE,objectId:plan.objectId,batchKey:plan.batchKey,activeMechanismId:id,
    modeStates:{[id]:saved(plan,id),'control.request-protocol':saved(plan,'control.request-protocol')}};
  assert.deepEqual(plan.restore(snapshot),snapshot);
  const bad=structuredClone(snapshot);bad.modeStates['control.request-protocol'].batchKey='other';assert.throws(()=>plan.restore(bad),/restore_scope/);
  assert.throws(()=>plan.restore({...snapshot,objectId:'resident-execution'}),/restore_scope/);
  assert.throws(()=>plan.restore({...snapshot,modeStates:{}}),/active_state_missing/);
  bad.modeStates['control.request-protocol']=saved(plan,'control.request-protocol');bad.modeStates['control.worker-protocol']={};assert.throws(()=>plan.restore(bad),/mode_unavailable/);
});
