import test from 'node:test';
import assert from 'node:assert/strict';
import {readProjectionFixture} from './architecture-projection-mechanisms.fixture.mjs';
import {createProjectionSimulation} from '../src/architecture/projection-mechanisms.model.js';
const f=readProjectionFixture();
const model=id=>createProjectionSimulation(f.content('projection.mechanism.'+id));
const advance=(m,id)=>{while(m.getScene().step?.id!==id){assert(!m.getScene().complete,'step not reachable: '+id);m.next();}return m.getScene().state;};
const end=(m,id)=>{m.choose(id);while(!m.getScene().complete)m.next();return m.getScene().state;};
test('assembly starts independent reads, then owns and releases each subscription separately',()=>{
  const m=model('assembly');let a=advance(m,'start');assert.equal(a.definition,'pending');assert.equal(a.facade,'pending');
  a=advance(m,'authorized');assert.equal(a.definition,'pending');assert(a.preload_started);assert(a.temporary_subscription);assert(!a.chat_panel);
  a=advance(m,'mount_chat');assert(a.chat_subscription);assert(a.temporary_subscription);
  a=advance(m,'release_open');assert(a.chat_subscription);assert(!a.temporary_subscription);
  a=end(m,'binding_mismatch');assert(a.regions_mounted);assert(!a.chat_panel);assert(!a.chat_subscription);
  for(const id of ['abort_after_preload','exit_after_mount']){a=end(m,id);assert(!a.regions_mounted);assert(!a.chat_panel);assert(!a.temporary_subscription);assert(!a.chat_subscription);}
});
test('opaque channel acceptance is distinct from resource authority and disappears on destruction',()=>{
  const m=model('opaque-bridge');const valid=end(m,'authorized_read');assert.equal(valid.proxy_dispatch_count,1);assert(valid.requires_actor_auth);assert(valid.opaque_origin);assert(valid.outer_navigation_guard);assert(!valid.connect_allowed);
  for(const id of ['wrong_source','wrong_channel','destroyed']) assert.equal(end(m,id).proxy_dispatch_count,0);
});
test('fixed host accepts late A as revision 2; SDK duplicate filter cannot undo it',()=>{
  const m=model('host-focus-order');advance(m,'b_first');assert.deepEqual([m.getScene().state.host_focus,m.getScene().state.sdk_revision],['B',1]);
  advance(m,'a_late');assert.deepEqual([m.getScene().state.latest_requested,m.getScene().state.host_focus,m.getScene().state.sdk_focus,m.getScene().state.sdk_revision],['B','A','A',2]);
  advance(m,'old_sdk');assert.equal(m.getScene().state.sdk_focus,'A');
  assert.equal(end(m,'destroyed').host_revision,0);
});
test('region source installation checks generation, captured data identity and disposal',()=>{
  const m=model('region-detail-order');assert.equal(end(m,'a_late').visible_body,'B');
  for(const id of ['batch_changed','destroyed']){const s=end(m,id);assert.equal(s.visible_body,'');assert.equal(s.discarded_response,'A');}
});
test('confirmation consumes active budget only and never recharges it on cleanup',()=>{
  const m=model('feedback-budget');advance(m,'wait_dialog');let s=m.getScene().state;
  assert.deepEqual([s.remaining_ms,s.stored_remaining_ms,s.active_elapsed_ms,s.confirmation_elapsed_ms],[89000,89000,1000,120000]);assert(s.timer_paused);
  advance(m,'native_send');assert(m.getScene().state.timer_paused);assert.equal(m.getScene().state.native_send_entries,1);
  advance(m,'resolved');s=m.getScene().state;assert(!s.timer_paused);assert.equal(s.remaining_ms,89000);
  advance(m,'result');assert.equal(m.getScene().state.business_outcome,'receipt_observed');
});
test('hidden, revoked and changed-revision requests stop before send; timeout after send stays unknown',()=>{
  const m=model('feedback-budget');for(const id of ['hidden_cancel','permission_denied','focus_changed_after_validation']){const s=end(m,id);assert.equal(s.native_send_entries,0);assert.equal(s.business_outcome,'not_sent');assert(!s.request_pending);}
  const s=end(m,'timeout_after_send');assert.equal(s.native_send_entries,1);assert.equal(s.sdk_outcome,'timeout_unknown');assert.equal(s.business_outcome,'unknown');assert(s.late_result_ignored);assert(!s.remote_interrupt);
});
