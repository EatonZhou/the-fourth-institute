const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  const calls = [], errors = [], timers = new Map(), handlers = new Map(), hooks = {};
  let sequence = 0;
  const state = { selectedId: 'A', detailRequestSeq: 0, paperPollEpoch: 0, mode: 'reader',
    detail: { paper: { id: 'A' }, analysis: 'old' }, detailSections: new Set(['analysis', 'summary']),
    detailSectionPromises: {}, leaving: false };
  const document = { hidden: false };
  const window = {
    setTimeout(callback, delay) { timers.set(++sequence, { callback, delay }); return sequence; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(name, callback) { handlers.set(name, callback); },
  };
  const context = { window, document, clearTimeout: window.clearTimeout, setTimeout: window.setTimeout };
  for (const file of ['paper_detail', 'paper_reviews', 'paper_projection']) {
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file + '.js'), 'utf8'), context);
  }
  const deps = Object.fromEntries([...window.PortalPaperDetail.dependencies,
    ...window.PortalPaperReviews.dependencies, ...window.PortalPaperProjection.dependencies].map(key => [key, () => {}]));
  const api = { request: async (url, options) => {
    assert.ok(!options?.method || options.method === 'GET', 'read reconciliation must not submit work');
    calls.push(['GET', url]);
    return url.includes('sections=') ? { paper: { id: state.selectedId }, analysis: 'fresh', loaded_sections: ['analysis', 'summary'] } : { status: 'done' };
  } };
  Object.assign(deps, { state, paperApi: api, cancelPdfRendering: async () => true, isCollectionMode: () => true,
    alertError: error => errors.push(error), renderWorkspace: () => calls.push(['render']),
    loadLibrary: async () => calls.push(['library']) });
  const detail = window.PortalPaperDetail.createDetail(deps);
  Object.assign(deps, detail);
  const reviews = window.PortalPaperReviews.createReviews(deps);
  const realRefresh = reviews.refreshDeepReadProjection;
  for (const name of ['loadCollectorJobs', 'refreshAssetsProjection', 'refreshDeepReadProjection',
    'refreshExpProjection', 'refreshFigureProjection', 'ensureCcfPaperEnrichment']) {
    deps[name] = async (...args) => hooks[name] ? hooks[name](...args) : calls.push([name]);
  }
  const projection = window.PortalPaperProjection.createProjection(deps);
  projection.bindPaperProjectionEvents();
  return { state, document, timers, calls, errors, hooks, api, detail, reviews, realRefresh, projection,
    event(changes) { handlers.get('portal:change')({ detail: { projection_changes: { paper: changes } } }); },
    snapshot() { handlers.get('portal:events-ready')({ detail: { snapshot_required: true } }); },
    async fire(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, 'expected timer at ' + delay + ', got ' + JSON.stringify([...timers.values()].map(t => t.delay)));
      const [id, timer] = entry; timers.delete(id); timer.callback(); await tick();
    },
  };
}

test('artifacts: events and snapshots reload DeepRead without a local job transition', async () => {
  for (const status of ['done', undefined]) {
    const f = fixture(); f.state.deepReadStatus = { status };
    f.api.request = async url => {
      f.calls.push(['GET', url]);
      return url.includes('sections=') ? { paper: { id: 'A' }, analysis: 'external artifact', loaded_sections: ['analysis', 'summary'] } : { status };
    };
    await f.realRefresh();
    assert.equal(f.state.detail.analysis, 'external artifact', String(status));
    assert.equal(f.calls.filter(c => c[0] === 'library').length, 1);
    assert.equal(f.calls.filter(c => c[0] === 'GET').length, 2);
  }
  const f = fixture(), reply = deferred();
  f.api.request = () => reply.promise;
  const pending = f.realRefresh();
  f.state.selectedId = 'B'; f.state.detailRequestSeq++;
  f.state.selectedId = 'A'; f.state.detailRequestSeq++;
  reply.resolve({ status: 'done' }); await pending;
  assert.equal(f.state.deepReadStatus, undefined);
  assert.equal(f.calls.length, 0, 'obsolete status must not trigger artifact or library reads');
});

test('sections: ABA selection and replacement protect current payload and promise ownership', async () => {
  const f = fixture(), old = deferred(), base = deferred(), fresh = deferred();
  const replies = [old, base, fresh];
  f.api.request = () => replies.shift().promise;
  const first = f.detail.loadDetailSections('A', ['analysis'], { force: true });
  f.state.selectedId = 'B';
  const reopening = f.detail.loadDetail('A', { render: false });
  await tick();
  old.resolve({ paper: { id: 'A' }, analysis: 'obsolete before base' }); await first;
  assert.equal(f.state.detail.analysis, 'old');
  base.resolve({ paper: { id: 'A' }, loaded_sections: ['base'] }); await reopening; await tick();
  fresh.resolve({ paper: { id: 'A' }, analysis: 'fresh' }); await tick();
  assert.equal(f.state.detail.analysis, 'fresh');

  const g = fixture(), stale = deferred(), latest = deferred();
  let reads = 0; g.api.request = () => (++reads === 1 ? stale : latest).promise;
  const staleRequest = g.detail.loadDetailSections('A', ['analysis'], { force: true });
  g.detail.mergeDetailPayload({ paper: { id: 'A' } }, { replace: true });
  const latestRequest = g.detail.loadDetailSections('A', ['analysis'], { force: true });
  const owned = g.state.detailSectionPromises['A:analysis'];
  stale.resolve({ paper: { id: 'A' }, analysis: 'obsolete after base' }); await staleRequest;
  assert.equal(g.state.detail.analysis, undefined);
  assert.equal(g.state.detailSectionPromises['A:analysis'], owned);
  const duplicate = g.detail.loadDetailSections('A', ['analysis'], { force: true });
  assert.equal(reads, 2, 'same-generation requests still deduplicate');
  latest.resolve({ paper: { id: 'A' }, analysis: 'latest' }); await latestRequest; await duplicate;
  assert.equal(g.state.detail.analysis, 'latest');
  assert.equal(Object.keys(g.state.detailSectionPromises).length, 0);
});

test('retry: failed event survives and merges new changes without bypassing backoff', async () => {
  const f = fixture(); let reads = 0;
  f.hooks.refreshDeepReadProjection = async () => { if (++reads === 1) throw new Error('offline'); };
  f.event({ A: ['deep_read'] }); await f.fire(100);
  f.event({ A: ['figures'] });
  assert.deepEqual([...f.timers.values()].map(t => t.delay), [1000]);
  await f.fire(1000);
  assert.equal(reads, 2);
  assert.equal(f.calls.filter(c => c[0] === 'refreshFigureProjection').length, 1);
  assert.equal(f.timers.size, 0);
});

function failingFixture(failure) {
  const f = fixture(); let reads = 0;
  f.hooks.loadCollectorJobs = async () => { reads++; return failure(reads); };
  f.reads = () => reads;
  return f;
}

test('snapshot: failed reconnect snapshot is retained through successful replay', async () => {
  const f = failingFixture(n => { if (n === 1) throw new Error('offline'); });
  f.snapshot(); await f.fire(100); await f.fire(1000);
  assert.equal(f.reads(), 2);
  assert.equal(f.calls.filter(c => c[0] === 'refreshAssetsProjection').length, 1);
  assert.equal(f.calls.filter(c => c[0] === 'refreshDeepReadProjection').length, 1);
  assert.equal(f.timers.size, 0);
});

test('bounded: repeated transient failure stops after three delayed retries', async () => {
  const f = failingFixture(() => { throw new Error('offline'); });
  f.event({ A: ['collector'] }); await f.fire(100);
  for (const delay of [1000, 2000, 4000]) await f.fire(delay);
  assert.equal(f.reads(), 4); assert.equal(f.timers.size, 0);
  f.event({ A: ['collector'] }); assert.equal(f.timers.size, 0, 'events cannot reset exhausted retry budget');
});

test('denial: 401 403 404 stop automatic retries and ordinary events do not bypass the stop', async () => {
  for (const status of [401, 403, 404]) {
    const f = failingFixture(() => { throw Object.assign(new Error('denied'), { status }); });
    f.event({ A: ['collector'] }); await f.fire(100);
    assert.equal(f.timers.size, 0);
    f.event({ A: ['collector'] });
    assert.equal(f.timers.size, 0); assert.equal(f.reads(), 1);
  }
});

test('lifecycle: hidden and leaving cancel backoff and ignore obsolete in-flight failures', async () => {
  for (const lifecycle of ['hidden', 'leaving']) {
    const reply = deferred(), f = failingFixture(() => reply.promise);
    f.event({ A: ['collector'] }); await f.fire(100);
    if (lifecycle === 'hidden') f.document.hidden = true; else f.state.leaving = true;
    await f.projection.suspendPaperUpdates({ includePdf: false });
    reply.reject(new Error('late offline')); await tick();
    assert.equal(f.timers.size, 0); assert.equal(f.reads(), 1);
    f.event({ A: ['collector'] }); assert.equal(f.timers.size, 0);
  }
  const f = failingFixture(n => { if (n === 1) throw new Error('offline'); });
  f.event({ A: ['collector'] }); await f.fire(100);
  const queued = [...f.timers.values()][0]?.callback;
  assert.ok(queued, 'backoff must be pending before hide');
  f.document.hidden = true; await f.projection.suspendPaperUpdates({ includePdf: false });
  f.document.hidden = false;
  queued(); await tick();
  assert.equal(f.reads(), 1, 'obsolete queued callback cannot restart after visibility changed');
  assert.equal(f.timers.size, 0);
  f.projection.resumePaperUpdates(); await f.fire(100);
  assert.equal(f.reads(), 2); assert.equal(f.timers.size, 0);
});

test('progress: active DeepRead progress updates controls while snapshots and explicit artifacts refresh', async () => {
  for (const status of ['queued', 'running']) {
    for (const options of [{}, { snapshot: true }, { artifactsChanged: true }]) {
      const f = fixture(); f.state.deepReadStatus = { status };
      const calls = [];
      // Build a real review controller with observed reads and controls.
      const window = {};
      vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/paper_reviews.js'), 'utf8'), { window, document: f.document });
      const deps = Object.fromEntries(window.PortalPaperReviews.dependencies.map(k => [k, () => {}]));
      Object.assign(deps, { state: f.state, paperApi: { request: async () => { calls.push('status'); return { status }; } },
        loadDetailSections: async () => calls.push('sections'), loadLibrary: async () => calls.push('library'),
        renderWorkspace: () => calls.push('render'), updateDeepReadControls: () => calls.push('controls') });
      await window.PortalPaperReviews.createReviews(deps).refreshDeepReadProjection(options);
      assert.deepEqual(calls, options.snapshot || options.artifactsChanged
        ? ['status', 'sections', 'library', 'render'] : ['status', 'controls']);
    }
  }
  const f = fixture(); let options;
  f.hooks.refreshDeepReadProjection = async opts => { options = opts; };
  f.snapshot(); await f.fire(100); assert.equal(options.snapshot, true);
  f.event({ A: ['analysis'] }); await f.fire(100); assert.equal(options.artifactsChanged, true);
});

test('resume: visible or explicit refresh restores budget and old failures cannot restart retries', async () => {
  for (const status of [403, 503]) {
    const f = failingFixture(() => { throw Object.assign(new Error('offline'), { status }); });
    f.event({ A: ['collector'] }); await f.fire(100);
    if (status === 503) for (const delay of [1000, 2000, 4000]) await f.fire(delay);
    assert.equal(f.timers.size, 0);
    const previousReads = f.reads();
    f.hooks.loadCollectorJobs = async () => {};
    f.projection.resumePaperUpdates(); await f.fire(100);
    assert.equal(f.timers.size, 0); assert.equal(f.reads(), previousReads);
    assert.ok(f.calls.some(c => c[0] === 'refreshAssetsProjection'));
  }
  const f = failingFixture(() => {}), old = deferred(); let reads = 0;
  f.hooks.loadCollectorJobs = () => ++reads === 1 ? old.promise : Promise.resolve();
  f.event({ A: ['collector'] }); await f.fire(100);
  f.projection.resumePaperUpdates();
  old.reject(new Error('old request')); await tick();
  await f.fire(100);
  assert.equal(reads, 2); assert.equal(f.timers.size, 0);
});

test('readonly: snapshots and failed batch replays do not invoke CCF business POST', async () => {
  const f = fixture(); f.state.ccfSelectedPaper = { id: 'fixture' };
  f.snapshot(); await f.fire(100);
  assert.equal(f.calls.filter(c => c[0] === 'ensureCcfPaperEnrichment').length, 0);
  const g = failingFixture(n => { if (n === 1) throw new Error('offline'); });
  g.state.ccfSelectedPaper = { id: 'fixture' };
  g.event({ A: ['collector', 'ccf_enrichment'] }); await g.fire(100); await g.fire(1000);
  assert.equal(g.calls.filter(c => c[0] === 'ensureCcfPaperEnrichment').length, 0);
});
