'use strict';
const assert = require('node:assert/strict');
const {capture} = require('./tau_observation_state.cjs');
const {collectResourceBaseline} = require('../../../management_portal/test_framework/tau_resource_baseline');
const {reviewRequests} = require('../../../management_portal/test_framework/tau_network_review');
const {safeError} = require('../../../management_portal/test_framework/tau_safe_error');
const fs = require('node:fs'), path = require('node:path');
const api = fs.readFileSync(path.join(__dirname, '../../../management_portal/static/portal_api.js'), 'utf8');

(async () => {
  if (!process.argv.includes('--abort-only')) {
  await capture('explicit_ready', async ({page, observer, rows}) => {
    let clicked = 0;
    await assert.rejects(observer.action(page, 'first-refresh', async () => { clicked++; }, {
      preflight: async () => { throw new Error('Ambiguous ID locator'); }, ready: async () => true,
    }));
    assert.equal(clicked, 0, 'invalid ready locators must be discovered before the first refresh');
    assert(!rows().some(row => row.kind === 'action_started'));
    let checks = 0;
    const checkedReady = async () => { checks++; return true; };
    await observer.action(page, 'first-refresh', async () => { clicked++; }, {
      preflight: checkedReady, ready: checkedReady,
    });
    assert.equal(clicked, 1);
    assert.equal(checks, 2);
    let mutations = 0, resolve;
    const barrier = new Promise(done => resolve = done);
    const running = observer.action(page, 'save', async () => { mutations++; return 'original'; }, {
      ready: async (target, receipt) => { assert.equal(target, page); assert.equal(receipt, 'original'); await barrier; return true; },
    });
    await new Promise(done => setImmediate(done));
    assert.ok(rows().some(row => row.kind === 'action_ready_started'));
    assert.ok(!rows().some(row => row.kind === 'action_finished' && row.name === 'save'));
    resolve();
    assert.equal(await running, 'original');
    assert.equal(mutations, 1);
    assert.ok(rows().some(row => row.kind === 'action_ready_confirmed'));
    await assert.rejects(observer.action(page, 'refresh', async () => { mutations++; }, {ready: async () => false}));
    assert.equal(mutations, 2);
    assert.equal(rows().filter(row => row.kind === 'action_finished').at(-1).status, 'failed');
    let parentMutations = 0, childMutations = 0;
    await assert.rejects(observer.action(page, 'parent', async () => { parentMutations++; }, {
      ready: async () => observer.action(page, 'child', async () => { childMutations++; }),
    }), error => {
      assert.equal(safeError(error).code, 'tau.action_nested');
      return true;
    });
    assert.equal(parentMutations, 1);
    assert.equal(childMutations, 0, 'diagnostic must not imply that the nested write ran');
    assert.equal(rows().filter(row => row.kind === 'action_finished').at(-1).error.code, 'tau.action_nested');
    assert.equal(safeError({code:'secret-token', message:'secret-token'}).code, undefined);
  });
  const ids = ['repo_aaaaaaaaaaaa', 'repo_bbbbbbbbbbbb'];
  const calls = [];
  const complete = await collectResourceBaseline(async (name, args) => {
    calls.push({name, args});
    return {items: [{resource_id: ids[args.cursor ? 1 : 0]}], total: 2, next_cursor: args.cursor ? null : 'next'};
  }, {resource_type: 'repo'});
  assert.equal(complete.complete, true);
  assert.deepEqual(complete.ids, ids);
  assert.equal(calls[1].args.cursor, 'next');
  const changed = await collectResourceBaseline(async (_name, args) => ({
    items: [{resource_id: ids[args.cursor ? 1 : 0]}], total: args.cursor ? 3 : 2, next_cursor: args.cursor ? null : 'next',
  }));
  assert.equal(changed.complete, false);
  assert.equal(changed.reason, 'membership_changed_or_incomplete');
  const cycle = await collectResourceBaseline(async () => ({items: [], total: 1, next_cursor: 'same'}));
  assert.equal(cycle.reason, 'cursor_cycle');
  }
  await capture('owned_abort', async ({context, main, request, rows, output}) => {
    const w = main.window;
    w.Headers = Headers;
    w.PortalI18n = {requestLanguage: () => 'en', message: String};
    let requestId;
    w.fetch = (url, init) => new Promise((resolve, reject) => {
      requestId = init.headers.get('X-Portal-Request-ID');
      const req = request(main.frame, {url, headers: {'x-portal-request-id': requestId}, failure: {errorText: 'net::ERR_ABORTED'}});
      context.emit('request', req);
      init.signal.addEventListener('abort', () => { context.emit('requestfailed', req); reject(init.signal.reason); }, {once:true});
    });
    w.eval(api);
    const controller = new w.AbortController();
    const pending = w.PortalAPI.request('/api/example?secret=DO-NOT-LOG', {signal: controller.signal});
    controller.abort();
    await assert.rejects(pending);
    await new Promise(done => setImmediate(done));
    let review = reviewRequests(rows());
    assert.equal(review.length, 1);
    assert.equal(review[0].proofs[0].reason, 'caller_aborted');
    assert.equal(review[0].proofs[0].transport_id, requestId);
    await assert.rejects(w.PortalAPI.request('/api/example', {timeoutMs: 2}));
    await new Promise(done => setImmediate(done));
    review = reviewRequests(rows());
    assert.equal(review[1].proofs[0].reason, 'request_timeout');
    const owned = new w.AbortController();
    const ownedPending = w.PortalAPI.request('/api/example', {signal: owned.signal});
    w.eval(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/repo_workspace_catalog.js'), 'utf8'));
    const deps = Object.fromEntries(w.PortalRepoWorkspaceCatalog.dependencies.map(name => [name, () => {}]));
    deps.state = {requestController:owned, requestSequence:0, descriptionPending:new Set(), descriptionAttempted:new Set()};
    w.PortalRepoWorkspaceCatalog.createCatalog(deps).cancelRepoQuery('repo_query_replaced');
    await assert.rejects(ownedPending, {name:'AbortError'});
    await new Promise(done => setImmediate(done));
    review = reviewRequests(rows());
    assert.equal(review[2].proofs[0].reason, 'repo_query_replaced');
    // Exercise the real input event wiring, not just a direct Catalog call.
    const typing = new w.AbortController();
    deps.state.requestController = typing;
    const typingPending = w.PortalAPI.request('/api/example', {signal: typing.signal});
    w.eval(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/repo_workspace_events.js'), 'utf8'));
    const eventDeps = Object.fromEntries(w.PortalRepoWorkspaceEvents.dependencies.map(name => [name, () => {}]));
    const elements = new Proxy({modeTabs:[], editorTabs:[]}, {get(target, name) {
      if (!target[name]) {
        target[name] = w.document.createElement('div');
        target[name].innerHTML = '<input name="placement">';
      }
      return target[name];
    }});
    Object.assign(eventDeps, {elements, state:deps.state, queryState:()=>({}),
      cancelRepoQuery:w.PortalRepoWorkspaceCatalog.createCatalog(deps).cancelRepoQuery});
    w.PortalRepoWorkspaceEvents.createEvents(eventDeps);
    elements.query.value = 'replacement';
    elements.query.dispatchEvent(new w.Event('input'));
    w.clearTimeout(deps.state.queryTimer);
    await assert.rejects(typingPending, {name:'AbortError'});
    await new Promise(done => setImmediate(done));
    review = reviewRequests(rows());
    assert.equal(review[3].proofs[0].reason, 'repo_query_replaced');
    assert(!output().includes('DO-NOT-LOG'));
    const wrong = {...review[0].proofs[0], document_id: 'other-document'};
    assert.equal(reviewRequests([...rows().filter(r => ['request_started', 'request_failed'].includes(r.kind)), wrong])[0].proofs.length, 0);
    let foreign;
    w.fetch = async (_url, init) => { foreign = init.headers.get('X-Portal-Request-ID'); return new Response('{}'); };
    await w.PortalAPI.request('https://foreign.invalid/api/example');
    assert.equal(foreign, null);
    await w.PortalAPI.request('/api/example', {headers: {'X-Portal-Request-ID': 'existing-request'}});
    assert.equal(foreign, 'existing-request');
  });
  console.log('ready, one mutation, full pagination, concurrent changes and original RTT contracts verified');
})().catch(error => { console.error(error); process.exitCode = 1; });
