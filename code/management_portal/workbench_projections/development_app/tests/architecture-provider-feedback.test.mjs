import test from 'node:test';
import assert from 'node:assert/strict';
import {createProviderSimulation} from '../src/architecture/provider-mechanisms.model.js';
import {createNativeSimulation} from '../src/architecture/native-workbench.model.js';
import {readProviderFeedbackFixture} from './architecture-provider-feedback.fixture.mjs';
const enabled=!!process.env.PROVIDER_FEEDBACK_FACTS;
const fixture=enabled?readProviderFeedbackFixture():null;
function replay(sim, stop) {while(sim.getScene().next){const step=sim.getScene().next;assert.ok(sim.perform(step.id));if(step.id===stop)break;}return sim.getScene();}
test('feedback: consumed model evidence closes over the fixed manifest; old-batch snapshots are rejected', {skip:!enabled},()=>{
  assert.ok(fixture.provenance.verified_source_descriptors>0);
  const sim=createProviderSimulation(fixture.content('ps.mechanism.relay-connection','model-api'));
  const before=sim.getScene();assert.equal(sim.restoreViewState({...sim.getViewState(),batchKey:'0c67fa9f8d391ce1b2bf5a42f21fa5b4e000d8a3bcddcea9d13250cc3cd5f2fd'}),false);assert.deepEqual(sim.getScene(),before);
});
test('feedback: rejected business frame preserves connection, then a new valid frame submits once', {skip:!enabled},()=>{
  for(const status of [400,403]){
    const sim=createProviderSimulation(fixture.content('ps.mechanism.relay-connection','model-api'));
    const id=`business${status}_then_valid`;assert.ok(sim.chooseCase(id));
    const first=replay(sim,id+'.first').state;
    assert.equal(first.connection_open,true);assert.equal(first.slot_held,true);assert.equal(first.execution_submit_count,0);
    assert.deepEqual(first.completed_frames,[{frame_index:1,status,execution_submitted:false}]);
    const second=replay(sim).state;
    assert.equal(second.connection_open,true);assert.equal(second.slot_held,true);assert.equal(second.frame_index,2);assert.equal(second.execution_submit_count,1);
    assert.deepEqual(second.completed_frames,[{frame_index:1,status,execution_submitted:false},{frame_index:2,status:200,execution_submitted:true}]);
    assert.equal(second.response_status,200);assert.equal(second.can_read_next_frame,true);
  }
});
test('feedback: preaccept summary agrees with zero start and absent accepted/checkpoint identities', {skip:!enabled},()=>{
  const sim=createNativeSimulation(fixture.content('codex.native-turn','provider-native'));
  sim.chooseCase('connect_failure_before_accept');const scene=replay(sim);
  assert.equal(scene.state.turn_start_count,0);assert.equal(scene.state.accepted_turn_id,'');assert.equal(scene.state.checkpoint_turn_id,'');
  assert.match(scene.outcome,/建连阶段失败/);assert.match(scene.outcome,/turn_start_count=0/);
  assert.match(scene.outcome,/不表示其他远端轮次已停止/);
});
