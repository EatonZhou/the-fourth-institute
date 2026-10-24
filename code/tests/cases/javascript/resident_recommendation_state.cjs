'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the page's actual request/recovery functions with I/O replaced;
// no browser or live recommendation request is needed for this state contract.
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/lineage_workbench.js'), 'utf8');
function pageFunction(name, nextName) {
  const start = source.indexOf(`      async function ${name}(`);
  const end = source.indexOf(`      ${nextName}`, start);
  assert.ok(start >= 0 && end > start);
  return source.slice(start, end);
}

function requestDetailsRecovery() {
  function slice(start, end) {
    const at = source.indexOf(start);
    const to = source.indexOf(end, at);
    assert.ok(at >= 0 && to > at);
    return source.slice(at, to);
  }
  let saved;
  const functions = slice('      function whichResidentDetailsKey(', '      async function openWhichResidentFork(')
    + slice('      function persistWhichResidentState(', '      const lineageLayoutStore =');
  function session() {
    const context = vm.createContext({
      state: { whichResident: {} }, boundRecommendationCards: new WeakSet(),
      compactWhichResidentResult: result => result || null,
      writeLineageUiState: patch => { saved = JSON.parse(JSON.stringify(patch.whichResident)); },
      writeLineageUiStateSoon() { throw new Error('toggle persistence must finish before refresh'); },
      window: { setTimeout() {} },
    });
    vm.runInContext(functions, context);
    return context;
  }
  function details(context) {
    const listeners = {};
    const element = { open: false, isConnected: true, addEventListener: (name, fn) => { listeners[name] = fn; } };
    const card = { querySelector: selector => selector === '.whichResidentRequestDetails' ? element : null };
    const root = { querySelector: selector => selector === '.whichResidentPanel' ? card : null, querySelectorAll: () => [] };
    context.bindWhichResidentControls(root);
    return { element, toggle(open) { element.open = open; listeners.toggle(); } };
  }
  const first = session();
  first.state.whichResident = { requestId: 'request-original', residentId: 'resident-original', messageId: 'input-original', inputStatus: 'running', busy: true };
  const original = details(first);
  original.toggle(true);
  assert.equal(saved.details_open, true);
  const refreshed = session();
  refreshed.restoreWhichResidentState(saved);
  const restored = details(refreshed);
  assert.equal(restored.element.open, true, 'reload restores expansion for the same accepted request');
  restored.toggle(false);
  const closedRefresh = session();
  closedRefresh.restoreWhichResidentState(saved);
  assert.equal(details(closedRefresh).element.open, false, 'explicit collapse also survives refresh');
  refreshed.state.whichResident.requestId = 'request-new';
  assert.equal(details(refreshed).element.open, false, 'a new request does not inherit the old expansion');
  restored.toggle(true);
  assert.equal(refreshed.state.whichResident.detailsOpen, false, 'late toggle from old request cannot change new request');
  const legacy = session();
  legacy.restoreWhichResidentState({ version: 8, request_id: 'request-old' });
  assert.equal(details(legacy).element.open, false, 'old saved state remains compatible');
  console.log('PASS request-scoped detail expansion, collapse, reload and stale toggle');
}

async function main() {
  const failure = {
    status: 'failed', request_id: 'recommendation-too-large',
    query: '查询中文🧩职责', error: '推荐结构化参数过长：64001 字节，预算 56000 字节；请缩短查询后重新提交。',
    retryable: false,
  };
  let persisted, watches = 0, stopped = 0;
  const context = vm.createContext({
    state: { whichResident: { requestSeq: 0, busy: false } },
    window: { clearTimeout() {} },
    whichResidentRetryTimer: 0,
    whichResidentInputWatch: null,
    residentDiscovery: {
      recommend: async () => failure,
      latestRecommendation: async () => failure,
    },
    bindWhichResidentInputWatch() { watches++; },
    persistWhichResidentState() { persisted = { ...context.state.whichResident }; },
    renderAgentList() {},
  });
  vm.runInContext(pageFunction('runWhichResident', 'function stopUnavailableWhichResident(')
    + pageFunction('restoreLatestWhichResident', 'async function searchRoutingEvalResidents('), context);
  assert.equal(await context.runWhichResident(failure.query), false, 'submission stays failed so the composer retains the query');
  assert.equal(persisted.error, failure.error, 'show the actual size failure, not missing message identity');
  assert.equal(persisted.requestId, failure.request_id);
  assert.equal(persisted.busy, false);
  assert.equal(watches, 0, 'a request rejected before dispatch has no input to watch');

  for (const previous of [{}, {
    requestSeq: 4, requestId: 'old-request', busy: true,
    residentId: 'old-resident', messageId: 'old-message', result: { recommendations: [] },
  }]) {
    context.state.whichResident = previous;
    context.whichResidentInputWatch = { destroy() { stopped++; } };
    await context.restoreLatestWhichResident();
    assert.equal(persisted.error, failure.error);
    assert.equal(persisted.inputStatus, 'failed');
    assert.equal(persisted.requestId, failure.request_id);
    assert.equal(persisted.submittedQuery, failure.query);
    assert.equal(persisted.query, failure.query);
    assert.equal(persisted.busy, false);
    assert.equal(persisted.result, null, 'old results must not hide the latest failure');
    assert.equal(persisted.messageId, '');
    assert.equal(persisted.resultLoading, false);
    assert.equal(watches, 0, 'restoring a terminal failure never starts polling');
  }
  assert.equal(stopped, 2, 'restoring a failure stops any old observation');
  requestDetailsRecovery();
  console.log('PASS recommendation terminal failure and refresh recovery');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
