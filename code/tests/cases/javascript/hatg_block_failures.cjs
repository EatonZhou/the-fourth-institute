const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {babelParse, traverse} = require('./babel_parse.cjs');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js'), 'utf8');
// A Function Block whose only member is the Resident under test.
const block = {id: 'block_one', elements: [{id: 'resident_one', kind: 'resident'}]};

// The production Function Block projection; the viewer's saved
// acknowledgements and the page redraw are the only replaced parts.
function familyBlocks(state, renderLineageSurface = () => {}) {
  let saved = {};
  const context = vm.createContext({});
  context.window = context;
  vm.runInContext(readHatgModule('hatg_family_blocks.js'), context);
  return createHatgModule(context.PortalHatgFamilyBlocks, 'createFamilyBlocks', {
    state, renderLineageSurface,
    currentPageStateController: () => ({read: () => saved, write: value => {saved = value;}}),
  });
}

function harness() {
  const member = {id: 'resident_one', title: 'Member', last_execution: {execution_id: 'run_one', message_id: 'input_one', status: 'failed', error: 'The read operation timed out', finished_at: '2026-10-22T12:00:00Z'}};
  const blocks = familyBlocks({lineage: {lineage_id: 'lineage_one', nodes: [member]}});
  return {member, issues: () => blocks.functionBlockInstanceFailures(block),
    unread: () => blocks.functionBlockInstanceUnacknowledgedFailures(block),
    acknowledge: () => blocks.acknowledgeFunctionBlockFailures(blocks.functionBlockInstanceFailures(block))};
}
test('timeout failure is visible and only an explicit acknowledgement clears its reminder', () => {
  const h = harness();
  assert.equal(h.issues()[0].error, 'The read operation timed out');
  assert.equal(h.unread().length, 1);
  h.issues();
  assert.equal(h.unread().length, 1);
  h.acknowledge();
  h.member.updated_at = 'later';
  h.member.last_execution.error = 'late error detail';
  assert.equal(h.issues().length, 1);
  assert.equal(h.unread().length, 0);
});
test('another execution of the same input renews the reminder even for the same error', () => {
  const h = harness();
  h.acknowledge();
  h.member.last_execution.execution_id = 'run_two';
  assert.equal(h.unread().length, 1);
});
test('cancelled, running and completed executions ignore stale legacy error fields', () => {
  const h = harness();
  h.member.error = 'stale';
  h.member.last_run = {error: 'stale'};
  for (const status of ['cancelled', 'running', 'queued', 'completed']) {
    h.member.last_execution.status = status;
    assert.equal(h.issues().length, 0, status);
  }
});
test('authoritative failed status remains visible before error details arrive', () => {
  const h = harness();
  delete h.member.last_execution.error;
  assert.equal(h.issues()[0].error, '执行失败');
});

function projectionHarness() {
  const owners = [];
  traverse(babelParse(source, 'hatg_panel.js'), {FunctionDeclaration(p) {
    if (p.node.id?.name === 'selectedLineageChangeMatches') owners.push(source.slice(p.node.start, p.node.end));
  }});
  assert.equal(owners.length, 1);
  const draws = {surface: 0, runtime: 0};
  const state = {localVersion: 0, runtimeStatus: new Map(), residentRuntimeById: new Map(),
    sessionUserMessageCounts: new Map(), sessionContextPressures: new Map(),
    lineage: {lineage_id: 'lineage_one', revision: 3, layout_revision: 2,
    runtime_revision: 1, nodes: [{id: 'resident_one', title: 'Member', x: 0.25,
      last_execution: {execution_id: 'run_one', message_id: 'input_one', status: 'running'}}], edges: []}};
  const surface = {
    renderLineageSurface() { draws.surface += 1; },
    renderGraphRuntimePresentation() { draws.runtime += 1; },
  };
  const nodeResidentId = node => node.id;
  const context = vm.createContext({state, ...surface, nodeResidentId});
  context.window = context;
  vm.runInContext(owners.join('\n'), context);
  const blocks = familyBlocks(state, surface.renderLineageSurface);
  // Every Lineage read reaches the page through the production commit.
  vm.runInContext(readHatgModule('hatg_lineage_state.js'), context);
  const lineageState = createHatgModule(context.PortalHatgLineageState, 'createLineageState', {
    state, ...surface, clone: value => JSON.parse(JSON.stringify(value)), nodeResidentId,
    reconcileGraphInteractionState() {}, bindResidentStateCollection() {}, setSyncStatus() {},
    updateLineageSummary() {}, renderProgramInspector() {}, renderControls() {},
    currentTemplateController: () => ({render() {}}),
  });
  return {context, blocks, state, draws, update(execution) {
    const incoming = JSON.parse(JSON.stringify(state.lineage));
    incoming.nodes[0].last_execution = execution;
    return lineageState.commitLineage(incoming, {source: 'background'}).changed;
  }};
}

test('same-revision server reads update Resident failure identity, reminder and cancellation', () => {
  const h = projectionHarness();
  const failure = {execution_id: 'run_one', message_id: 'input_one', status: 'failed',
    error: 'The read operation timed out', finished_at: '2026-10-22T12:00:00Z'};
  assert.equal(h.update(failure), true);
  assert.equal(h.blocks.functionBlockInstanceFailures(block)[0].error, failure.error);
  assert.equal(h.blocks.functionBlockInstanceUnacknowledgedFailures(block).length, 1);
  h.blocks.acknowledgeFunctionBlockFailures(h.blocks.functionBlockInstanceFailures(block));
  assert.equal(h.update({...failure}), false, 'equal reads do not redraw');
  assert.equal(h.blocks.functionBlockInstanceUnacknowledgedFailures(block).length, 0);
  assert.equal(h.update({...failure, execution_id: 'run_two'}), true);
  assert.equal(h.blocks.functionBlockInstanceUnacknowledgedFailures(block).length, 1, 'new read does not acknowledge new failure');
  assert.equal(h.update({execution_id: 'run_three', status: 'cancelled'}), true);
  assert.equal(h.blocks.functionBlockInstanceFailures(block).length, 0);
  assert.equal(h.update({}), true, 'an authoritative empty observation clears the old result');
  assert.equal(h.state.lineage.revision, 3);
  assert.equal(h.state.lineage.runtime_revision, 1);
  assert.equal(h.state.lineage.nodes[0].x, 0.25);
  // The one surface redraw is the acknowledgement's own; the four changed
  // observations redraw only the runtime presentation.
  assert.deepEqual(h.draws, {surface: 1, runtime: 4}, 'same-revision observations redraw only the runtime presentation');
});

test('only committed terminal inputs of displayed Residents invalidate the graph observation', () => {
  const {context} = projectionHarness();
  const event = {resident_id: 'resident_one', event_type: 'resident.input.state.changed',
    payload: {input_state: {state: 'failed'}}};
  const matches = value => context.selectedLineageChangeMatches({resident_events: [value]});
  for (const state of ['failed', 'completed', 'cancelled']) {
    assert.equal(matches({...event, payload: {input_state: {state}}}), true);
  }
  assert.equal(matches({...event, resident_id: 'other'}), false);
  assert.equal(matches({...event, live: true}), false);
  assert.equal(matches({...event, payload: {input_state: {state: 'running'}}}), false);
  assert.equal(matches({...event, event_type: 'conversation.message.updated'}), false);
});
