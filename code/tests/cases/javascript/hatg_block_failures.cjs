const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {babelParse, traverse} = require('./babel_parse.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js'), 'utf8');
const names = new Set(['functionBlockInstanceFailures', 'functionBlockInstanceUnacknowledgedFailures', 'acknowledgeFunctionBlockFailures']);
const functions = [];
traverse(babelParse(source, 'hatg_panel.js'), {FunctionDeclaration(p) {
  if (names.has(p.node.id?.name)) functions.push(source.slice(p.node.start, p.node.end));
}});
assert.equal(functions.length, names.size);
function harness() {
  let saved = {};
  const member = {id: 'resident_one', title: 'Member', last_execution: {execution_id: 'run_one', message_id: 'input_one', status: 'failed', error: 'The read operation timed out', finished_at: '2026-10-22T12:00:00Z'}};
  const context = vm.createContext({
    state: {lineage: {lineage_id: 'lineage_one'}},
    instanceInnerObjects: (_, kind) => kind === 'nodes' ? [member] : [],
    pageStateController: {read: () => saved, write: value => {saved = value;}},
    renderLineageSurface: () => {},
  });
  vm.runInContext(functions.join('\n'), context);
  return {member, issues: () => context.functionBlockInstanceFailures({}),
    unread: () => context.functionBlockInstanceUnacknowledgedFailures({}),
    acknowledge: () => context.acknowledgeFunctionBlockFailures(context.functionBlockInstanceFailures({}))};
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
