'use strict';
const assert = require('node:assert/strict');
const { createWorkbenchModule, loadWorkbenchModule } = require('./lineage_workbench_modules.cjs');

// Run the page's actual Which Resident part with I/O replaced; no browser or
// live recommendation request is needed for this state contract.
const RECOMMENDER = 'resident_recommender';

function whichResident({ discovery = {}, window = {}, whichResident = {} } = {}) {
  const io = { saved: null, watches: 0, stopped: 0 };
  const page = {
    clearTimeout() {}, setTimeout() {},
    PortalResidentProtocol: { watchInputState() { io.watches += 1; return { destroy() { io.stopped += 1; } }; } },
    ...window,
  };
  const state = { whichResident, recommendationResident: { resident_id: RECOMMENDER } };
  const part = createWorkbenchModule(loadWorkbenchModule('lineage_workbench_which_resident.js', page),
    'createWhichResident', {
      state, residentDiscovery: discovery, renderAgentList() {},
      writeLineageUiState: patch => { io.saved = JSON.parse(JSON.stringify(patch.whichResident)); },
      writeLineageUiStateSoon() { throw new Error('request state is saved at once'); },
    });
  return { part, state, io };
}

// The recommendation conversation's composer sends through the part.
function send(part, text) {
  const view = part.recommendationConversationView({ resident_id: RECOMMENDER }, { instanceId: () => 'panel-1' });
  const panel = { workspace: { state: { composer: { can_send: true } }, submit: run => run() } };
  return view.send({ text }, {}, panel);
}

function requestDetailsRecovery() {
  let saved;
  function session() {
    const page = whichResident();
    return { ...page, get saved() { return page.io.saved; } };
  }
  function details({ part }) {
    const listeners = {};
    const element = { open: false, isConnected: true, addEventListener: (name, fn) => { listeners[name] = fn; } };
    const card = { querySelector: selector => selector === '.whichResidentRequestDetails' ? element : null };
    const root = { querySelector: selector => selector === '.whichResidentPanel' ? card : null, querySelectorAll: () => [] };
    part.bindWhichResidentControls(root);
    return { element, toggle(open) { element.open = open; listeners.toggle(); } };
  }
  const first = session();
  first.state.whichResident = { requestId: 'request-original', residentId: 'resident-original', messageId: 'input-original', inputStatus: 'running', busy: true };
  const original = details(first);
  original.toggle(true);
  saved = first.saved;
  assert.equal(saved.details_open, true);
  const refreshed = session();
  refreshed.part.restoreWhichResidentState(saved);
  const restored = details(refreshed);
  assert.equal(restored.element.open, true, 'reload restores expansion for the same accepted request');
  restored.toggle(false);
  saved = refreshed.saved;
  const closedRefresh = session();
  closedRefresh.part.restoreWhichResidentState(saved);
  assert.equal(details(closedRefresh).element.open, false, 'explicit collapse also survives refresh');
  restored.toggle(true);
  assert.equal(refreshed.state.whichResident.detailsOpen, true, 'old request is expanded before switching requests');
  const oldRequestSaved = refreshed.saved;
  refreshed.state.whichResident.requestId = 'request-new';
  assert.equal(details(refreshed).element.open, false, 'a new request does not inherit the old expansion');
  restored.toggle(false);
  assert.equal(refreshed.state.whichResident.detailsOpen, true, 'late toggle from old request cannot change saved expansion');
  assert.deepEqual(refreshed.saved, oldRequestSaved, 'late toggle from old request cannot persist new state');
  const legacy = session();
  legacy.part.restoreWhichResidentState({ version: 8, request_id: 'request-old' });
  assert.equal(details(legacy).element.open, false, 'old saved state remains compatible');
  console.log('PASS request-scoped detail expansion, collapse, reload and stale toggle');
}

async function main() {
  const failure = {
    status: 'failed', request_id: 'recommendation-too-large',
    query: '查询中文🧩职责', error: '推荐结构化参数过长：64001 字节，预算 56000 字节；请缩短查询后重新提交。',
    retryable: false,
  };
  let latest = failure;
  const discovery = { recommend: async () => failure, latestRecommendation: async () => latest };
  const { part, state, io } = whichResident({ discovery, whichResident: { requestSeq: 0, busy: false } });
  await assert.rejects(send(part, failure.query), { message: failure.error },
    'submission stays failed with the actual size failure, so the composer retains the query');
  assert.equal(io.saved.error, failure.error, 'show the actual size failure, not missing message identity');
  assert.equal(io.saved.request_id, failure.request_id);
  assert.equal(io.saved.busy, false);
  assert.equal(io.watches, 0, 'a request rejected before dispatch has no input to watch');

  // A request this page observes fails on the server; the page may still show an older result.
  const observed = { status: 'running', request_id: 'request-observed', resident_id: 'resident-observer', message_id: 'message-observed', query: 'old' };
  for (const previous of [{}, {
    requestSeq: 4, requestId: 'old-request', busy: true,
    residentId: 'old-resident', messageId: 'old-message', result: { recommendations: [] },
  }]) {
    latest = observed;
    state.whichResident = {};
    await part.restoreLatestWhichResident();
    const watches = io.watches, stopped = io.stopped;
    assert.equal(watches, stopped + 1, 'the observed request is watched');
    state.whichResident = { ...previous, requestSeq: state.whichResident.requestSeq };
    latest = failure;
    await part.restoreLatestWhichResident();
    assert.equal(io.saved.error, failure.error);
    assert.equal(io.saved.input_status, 'failed');
    assert.equal(io.saved.request_id, failure.request_id);
    assert.equal(io.saved.submitted_query, failure.query);
    assert.equal(io.saved.query, failure.query);
    assert.equal(io.saved.busy, false);
    assert.equal(io.saved.result, null, 'old results must not hide the latest failure');
    assert.equal(io.saved.message_id, '');
    assert.equal(state.whichResident.resultLoading, false);
    assert.equal(io.watches, watches, 'restoring a terminal failure never starts polling');
    assert.equal(io.stopped, stopped + 1, 'restoring a failure stops the old observation');
  }
  requestDetailsRecovery();
  console.log('PASS recommendation terminal failure and refresh recovery');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
