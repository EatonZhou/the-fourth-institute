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
  console.log('PASS recommendation terminal failure and refresh recovery');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
