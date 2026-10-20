import test from 'node:test';
import assert from 'node:assert/strict';
import { MAIN_BRANCH, validateScenario, branchOptions, createPlayback, movePlayback,
  selectPlaybackBranch, timelineFor, sceneFor, sourcesFor } from '../src/architecture/playback.js';

const participants = [
  { id: 'panel', module_id: 'interface.workbenches', role: '发送方' },
  { id: 'host', module_id: 'interface.workbenches', role: '宿主通道' },
  { id: 'subscriber', module_id: 'interface.workbenches', role: '订阅方' },
  { id: 'reader', module_id: 'resources', role: '读取结果' },
];
const frozen = 'workspace_123456789abc/file/objects/design-frozen.md';
const version = { ref: 'decision_123456789abc', body_version: '3', response_version: '7',
  body_sha256: 'a'.repeat(64), body: { address: frozen } };
const source = { ref: version.ref, section: '动作 / 修订核对', line_start: 166, line_end: 169,
  excerpt: '读取结果只在修订相等时应用。' };
const step = (id, from = 'host', to = 'subscriber') => ({ id, from, to,
  condition: `condition:${id}`, request: `request:${id}`, result: `result:${id}`, source_refs: [source] });
const branch = (id, kind, at_step, steps) => ({ id, kind, at_step, condition: `branch:${id}`, steps: steps.map(id => step(id)) });
const focus = {
  status: 'simulated', scenario_id: 'focus-revision-order', protocol_id: 'projection-family',
  participants, source_versions: [version], steps: [step('select-a'), step('start-read-a')],
  branches: [
    branch('normal-arrival', 'ordering', 'start-read-a', ['apply-a', 'select-b', 'start-read-b', 'apply-b']),
    branch('a-arrives-after-b', 'concurrency', 'start-read-a', ['select-b', 'start-read-b', 'apply-b', 'discard-late-a']),
  ],
};
const budget = {
  status: 'simulated', scenario_id: 'feedback-confirmation-budget', protocol_id: 'projection-family',
  participants, source_versions: [version],
  budget_example: { initial_ms: 90000, active_before_pending_ms: 12000,
    confirmation_wait_ms: 180000, remaining_after_resolved_ms: 78000 },
  steps: ['draft', 'request-confirmation', 'pending', 'waiting'].map(id => step(id)),
  branches: [
    branch('confirmed', 'confirmation', 'waiting', ['resume-confirmed', 'submit-once', 'receipt', 'reply']),
    branch('rejected', 'confirmation', 'waiting', ['reject', 'retain-draft']),
    branch('activity-timeout', 'exception', 'waiting', ['resume-before-timeout', 'exhaust-budget', 'no-retry']),
  ],
};
function at(scenario, branchId, stepId) {
  const start = createPlayback(scenario, branchId);
  return movePlayback(scenario, start, timelineFor(scenario, branchId).findIndex(item => item.id === stepId));
}

test('late A is discarded after B, backtracking reconstructs the pending read', () => {
  const state = at(focus, 'a-arrives-after-b', 'discard-late-a');
  const end = sceneFor(focus, state);
  assert.equal(end.status, 'simulated');
  assert.deepEqual(end.explanation, { type: 'revision', status: 'simulated', current_revision: 'rB',
    pending_reads: [], visible_detail: 'B', discarded: ['rA'], received_focus_republished: false });
  const previous = sceneFor(focus, movePlayback(focus, state, -1));
  assert.equal(previous.explanation.visible_detail, 'B');
  assert.deepEqual(previous.explanation.pending_reads, ['rA']);
  assert.deepEqual(previous.explanation.discarded, []);
  assert.equal(sceneFor(focus, at(focus, 'normal-arrival', 'apply-a')).explanation.visible_detail, 'A');
  assert.deepEqual(sceneFor(focus, at(focus, 'normal-arrival', 'start-read-b')).explanation.pending_reads, ['rB']);
});

test('branch identity isolates same-named steps and restarts the local path', () => {
  const normal = timelineFor(focus, 'normal-arrival').find(item => item.id === 'apply-b');
  const late = timelineFor(focus, 'a-arrives-after-b').find(item => item.id === 'apply-b');
  assert.notEqual(normal.key, late.key);
  const changedScenario = { ...focus, scenario_id: 'other-frozen-scene' };
  assert.notEqual(timelineFor(changedScenario, 'normal-arrival')[0].key, timelineFor(focus, 'normal-arrival')[0].key);
  const switched = selectPlaybackBranch(focus, 'normal-arrival');
  assert.equal(switched.index, 0);
  assert.equal(sceneFor(focus, switched).explanation.visible_detail, null);
  assert.deepEqual(sceneFor(focus, switched).explanation.pending_reads, []);
  assert.equal(movePlayback(focus, switched, -999).index, 0);
  assert.equal(movePlayback(focus, switched, 999).index, 5);
});

test('confirmation wait preserves remaining activity budget, receipt is separate from reply', () => {
  const pending = sceneFor(budget, at(budget, 'confirmed', 'pending')).explanation;
  const waiting = sceneFor(budget, at(budget, 'confirmed', 'waiting')).explanation;
  const resumed = sceneFor(budget, at(budget, 'confirmed', 'resume-confirmed')).explanation;
  assert.equal(pending.remaining_ms, 78000);
  assert.equal(waiting.remaining_ms, 78000);
  assert.equal(waiting.confirmation_wait_ms, 180000);
  assert.equal(waiting.timer, 'paused');
  assert.equal(resumed.timer, 'active');
  assert.equal(resumed.remaining_ms, 78000);
  const receipt = sceneFor(budget, at(budget, 'confirmed', 'receipt')).explanation;
  assert.equal(receipt.delivery, 'receipt');
  assert.equal(receipt.submission_count, 1);
  assert.equal(receipt.reply, 'none');
  const reply = sceneFor(budget, at(budget, 'confirmed', 'reply')).explanation;
  assert.equal(reply.submission_count, 1);
  assert.equal(reply.reply, 'native_chat');
});

test('rejection and timeout remain distinct; replay never retries or invents submission success', () => {
  const rejected = sceneFor(budget, at(budget, 'rejected', 'retain-draft')).explanation;
  assert.equal(rejected.submission_count, 0);
  assert.equal(rejected.draft_retained, true);
  assert.equal(rejected.delivery, 'not_submitted');
  const expiredState = at(budget, 'activity-timeout', 'no-retry');
  const timeout = sceneFor(budget, expiredState).explanation;
  assert.equal(timeout.remaining_ms, 0);
  assert.equal(timeout.outcome, 'projection.request_timeout');
  assert.equal(timeout.submission_count, null);
  assert.equal(timeout.delivery, 'unknown');
  const before = sceneFor(budget, movePlayback(budget, expiredState, -2)).explanation;
  assert.equal(before.remaining_ms, 78000);
  assert.equal(before.outcome, null);
  assert.equal(before.delivery, 'unknown');
  assert.equal(before.submission_count, null);
  assert.equal(sceneFor(budget, selectPlaybackBranch(budget, 'rejected')).explanation.remaining_ms, 90000);
});

test('resource exceptions stop at their pivot and do not claim a successful body', () => {
  const resource = { ...focus, scenario_id: 'resident-read-small-resource',
    steps: ['request', 'resolve', 'read', 'return'].map(id => step(id)),
    branches: [branch('forbidden', 'exception', 'resolve', ['deny']),
      branch('partial_body', 'exception', 'return', ['incomplete'])] };
  assert.equal(createPlayback(resource).branch_id, MAIN_BRANCH);
  assert.deepEqual(timelineFor(resource, 'forbidden').map(item => item.id), ['request', 'resolve', 'deny']);
  assert.notEqual(timelineFor(resource, 'forbidden')[1].result, 'result:resolve');
  assert.notEqual(timelineFor(resource, 'partial_body')[3].result, 'result:return');
  assert.equal(timelineFor(resource, MAIN_BRANCH)[3].result, 'result:return');
});

test('source links use immutable body and exact supplied lines, versions and excerpts', () => {
  const [read] = sourcesFor(focus, focus.steps[0]);
  assert.equal(read.frozen_ref, `${frozen}#L166-L169`);
  assert.equal(read.body_version, '3');
  assert.equal(read.response_version, '7');
  assert.equal(read.body_sha256, 'a'.repeat(64));
  assert.equal(read.excerpt, source.excerpt);
  assert.equal(sourcesFor(focus, { source_refs: [version.ref] })[0].frozen_ref, frozen);
  assert.equal(sourcesFor(focus, { source_refs: [{ ref: 'decision_missing', line_start: 1 }] })[0].frozen_ref, null);
  const snapshot = sceneFor(focus, createPlayback(focus));
  snapshot.source_versions[0].body.address = 'mutated';
  snapshot.source_refs[0].excerpt = 'mutated';
  assert.equal(focus.source_versions[0].body.address, frozen);
  assert.equal(sourcesFor(focus, focus.steps[0])[0].excerpt, source.excerpt);
});

test('same-module roles remain independent and malformed endpoint/status is rejected', () => {
  assert.equal(validateScenario(focus).participants.length, 4);
  assert.equal(new Set(focus.participants.map(item => item.module_id)).size, 2);
  assert.throws(() => createPlayback({ ...focus, status: 'current' }), /simulated/);
  assert.throws(() => createPlayback({ ...focus, steps: [{ ...step('bad'), to: 'missing' }] }), /未知参与角色/);
  assert.throws(() => createPlayback(focus, 'missing'), /未知场景分支/);
  assert.ok(branchOptions(focus).some(item => item.id === 'a-arrives-after-b' && item.kind === 'concurrency'));
});
