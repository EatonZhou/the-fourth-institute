'use strict';
const assert = require('node:assert/strict');
const { mailboxResidentEventMetadata } = require('../../../management_portal/test_framework/tau_mailbox_evidence');
const target = { residentId: 'resident_a123', entryKey: 'resident:resident_a123' };
// The production event nests input identity under payload.input_state. AC's
// helper allowed payload but omitted input_state; its flat preflight missed it.
const event = { resident_id: target.residentId, event_type: 'resident.input.state.changed', cursor: 10,
  payload: { input_state: { message_id: 'msg_a123', state: 'completed', updated_at: '2026-10-25T00:00:00Z',
    error: { message: 'private error' } }, body: 'private body', token: 'private credential' } };
const omitted = { resident_id: target.residentId, event_type: 'resident.state.changed', cursor: 11, payload_omitted: true };
const raw = [event, omitted, { ...event, resident_id: 'resident_beef' }];
const original = JSON.stringify(raw);
const captured = mailboxResidentEventMetadata(raw, target);
assert.equal(captured.target_events.length, 2);
const input = captured.target_events[0];
assert.equal(input.input_state.fields.message_id.value, 'msg_a123');
assert.equal(input.input_state.fields.state.value, 'completed');
assert.equal(input.fields.cursor.value, 10);
assert.equal(input.payload.fields.message_id.present, false, 'never flatten nested identity into an absent field');
assert.equal(captured.target_events[1].fields.payload_omitted.value, true);
assert.equal(captured.target_events[1].input_state.type, 'undefined', 'omitted state event is not input completion');
assert.equal(captured.target_events[1].input_state.fields, null);
assert.equal(JSON.stringify(raw), original, 'original payload is untouched');
assert.ok(!JSON.stringify(captured).includes('private'));
assert.ok(!JSON.stringify(captured).includes('resident_beef'));
for (const [value, expectedType] of [[null, 'null'], [undefined, 'undefined'], [[], 'array'], ['unexpected', 'string']]) {
  const result = mailboxResidentEventMetadata(value, target);
  assert.equal(result.type, expectedType);
  assert.deepEqual(result.target_events, Array.isArray(value) ? [] : null);
}
for (const [payload, expectedType] of [[null, 'null'], [{input_state:null}, 'null'], [{input_state:[]}, 'array']]) {
  const row = mailboxResidentEventMetadata([{...event,payload}], target).target_events[0];
  assert.equal(row.input_state.type, payload === null ? 'undefined' : expectedType);
  assert.equal(row.input_state.fields, null);
}
const nullInput = mailboxResidentEventMetadata([{...event,payload:{input_state:{message_id:null,state:'completed'}}}], target).target_events[0];
assert.equal(nullInput.input_state.fields.message_id.present, true);
assert.equal(nullInput.input_state.fields.message_id.type, 'null');
assert.equal(nullInput.input_state.fields.updated_at.present, false);
const malformed = mailboxResidentEventMetadata([{...event,payload:{input_state:{message_id:{body:'private'},state:[]}}}], target).target_events[0];
assert.equal(malformed.input_state.fields.message_id.type, 'object');
assert.equal(malformed.input_state.fields.message_id.value, null);
assert.equal(malformed.input_state.fields.state.type, 'array');
assert.throws(() => mailboxResidentEventMetadata(raw, {}), /Explicit target/);
console.log('Mailbox original nested input identity evidence contracts passed');
