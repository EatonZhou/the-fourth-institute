import test from 'node:test';
import assert from 'node:assert/strict';
import {createLineageSimulation} from '../src/architecture/lineage-mechanisms.model.js';
import {readLineageFixture} from './architecture-lineage-mechanisms.fixture.mjs';
const {model,content}=readLineageFixture();
const payload=id=>({...content,mechanism:model.mechanisms.find(m=>m.id==='mechanism.lineage.'+id),mechanisms:model.mechanisms});
test('intake observation separates job head, internal queue and public waiting count',()=>{
  const expectations={normal:[0,0,'job_head','demo.new'],closed:[0,1,'job_head',''],busy:[1,1,'internal_queue','demo.active']};
  for(const [id,expected] of Object.entries(expectations)){
    const p=payload('intake'),s=createLineageSimulation(p);s.choose(id);s.perform('accept');
    assert.equal(s.getScene().state.queue_observation,'not_observed');
    for(const step of p.mechanism.cases.find(c=>c.id===id).path.slice(2))s.perform(step);
    const st=s.getScene().state;assert.equal(st.queue_observation,'observed');assert.equal(st.input_state,'queued');
    assert.deepEqual([st.internal_queue_size,st.public_queued_count,st.new_input_location,st.active_message_id],expected);
  }
});
test('declared barrier entry, per-slot completion and restore use the fixed teaching graph',()=>{
  const p=payload('packet-operators');
  for(const c of p.mechanism.cases.filter(c=>c.id.startsWith('barrier_'))){
    const s=createLineageSimulation(p);s.choose(c.id);assert.deepEqual(s.getScene().history,['barrier_load']);
    for(const step of c.path.slice(1)){
      s.perform(step);const snapshot=s.getViewState(),restored=createLineageSimulation(p);restored.restore(snapshot);assert.deepEqual(restored.getScene(),s.getScene());
      const st=s.getScene().state;
      if(step.includes('.arrive_'))assert.equal(st.barrier_ready,st.expected_wire_ids.every(id=>st.slots[id]?.received));
      if(step.includes('.arrive_')&&Object.values(st.slots).some(slot=>slot.received&&!slot.packet_ids.length)&&!st.barrier_ready)assert.equal(st.output_packet_ids.length,0);
    }
    const st=s.getScene().state;
    if(c.id==='barrier_all_empty')assert.deepEqual([st.barrier_consumed,st.output_packet_ids,st.parents],[true,[],[]]);
    if(c.id==='barrier_missing_branch')assert.deepEqual([st.barrier_ready,st.output_packet_ids],[false,[]]);
    if(c.id==='barrier_late_legacy')assert.deepEqual([st.phase,st.expected_wire_ids.length,st.barrier_consumed,st.output_packet_ids.length],['legacy_recreated',1,false,1]);
    assert.throws(()=>s.restore({...s.getViewState(),history:['load']}),/起始状态/);
  }
});
test('teaching topology cannot become a global semantic identity or invent barrier wires',()=>{
  for(const mutate of [p=>delete p.mechanism.teaching_graph,p=>p.mechanism.teaching_graph.nodes[0].owner_object_id='other',p=>p.mechanism.teaching_graph.barriers[0].expected_packet_wire_ids=['absent']]){
    const p=structuredClone(payload('packet-operators'));mutate(p);assert.throws(()=>createLineageSimulation(p),/teaching_/);
  }
});
