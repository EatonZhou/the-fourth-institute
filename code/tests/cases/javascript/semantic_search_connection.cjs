const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const flush = () => new Promise(setImmediate);

function fixture() {
  const dom = new JSDOM('<input id="query"><button id="start"></button><div id="jobs"></div>', {
    runScripts: 'outside-only',
  });
  const {window} = dom;
  const timers = new Map(), notices = [], requests = [];
  let sequence = 0, searches = [], getError = null, postError = null;
  Object.defineProperty(window.document, 'hidden', {value: false});
  window.setTimeout = (callback, delay) => {
    const id = ++sequence;
    timers.set(id, {callback, delay});
    return id;
  };
  window.clearTimeout = id => timers.delete(id);
  window.EventSource = function () {};
  window.PortalEvents = {isConnected: () => true};
  const template = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
  window.PortalI18n = {t: value => value, text: template, html: template};
  window.PortalUIPrimitives = {escapeHtml: value => String(value ?? '')};
  window.PortalUI = {
    toast: (message, level) => notices.push({message, level}),
    errorMessage: (label, error) => `${label}失败：${error.message}`,
  };
  const client = {
    get: async url => {
      requests.push(['GET', url]);
      if (getError) throw getError;
      return {searches};
    },
    post: async (url, body) => {
      requests.push(['POST', url, body]);
      if (postError) throw postError;
      return {search_id: 'search_test', query: body.query, status: 'queued'};
    },
  };
  for (const file of ['portal_refresh_coordinator.js', 'portal_semantic_search.js']) {
    window.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8'));
  }
  return {
    window, timers, notices, requests,
    searches: value => { searches = value; },
    getError: value => { getError = value; },
    postError: value => { postError = value; },
    create: (options = {}) => window.PortalSemanticSearch.create({
      input: 'query', button: 'start', panel: 'jobs', client, projectionScope: 'repo',
      urls: {list: '/searches', start: '/start', refresh: '/refresh', delete: '/delete'},
      ...options,
    }),
    event: name => window.dispatchEvent(new window.Event(name)),
    async tick(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `expected timer at ${delay}ms`);
      timers.delete(entry[0]); entry[1].callback();
      await flush();
    },
    close: () => dom.window.close(),
  };
}

test('destroy barrier prevents deletion after pending confirmation', async t => {
  const f = fixture();let confirm;
  const search = f.create({confirmRemove: () => new Promise(resolve => { confirm = resolve; })});
  t.after(() => { search.destroy(); f.close(); });
  f.searches([{search_id: 'search_test', query: 'fixture', status: 'done'}]);
  await search.load();
  f.window.document.querySelector('[data-semantic-delete]').click();
  assert.equal(typeof confirm, 'function');
  search.destroy();confirm(true);await flush();await flush();
  assert.equal(f.requests.filter(item => item[0] === 'POST').length, 0, 'destroyed component cannot dispatch a confirmed delete');
});

test('destroy barrier retains live approval, rejection and already-dispatched semantics', async t => {
  const f = fixture();let approved = false, confirm;
  const search = f.create({confirmRemove: () => approved ? true : new Promise(resolve => { confirm = resolve; })});
  t.after(() => { search.destroy(); f.close(); });
  f.searches([{search_id: 'search_test', query: 'fixture', status: 'done'}]);
  await search.load();
  f.window.document.querySelector('[data-semantic-delete]').click();
  confirm(false);await flush();
  assert.equal(f.requests.filter(item => item[0] === 'POST').length, 0);
  approved = true;
  f.window.document.querySelector('[data-semantic-delete]').click();await flush();
  assert.equal(f.requests.filter(item => item[0] === 'POST' && item[1] === '/delete').length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(f.requests.find(item => item[0] === 'POST')[2])), {search_id: 'search_test'});
  search.destroy();
  assert.equal(f.requests.filter(item => item[0] === 'POST').length, 1, 'destroy does not issue rollback or replay');
});

test('opening a catalog without searching survives event disconnects and keeps polling', async t => {
  const f = fixture();
  const search = f.create(); t.after(() => { search.destroy(); f.close(); });
  await search.load();
  for (let attempt = 0; attempt < 3; attempt++) {
    f.event('portal:events-error');
    await f.tick(0);
    await f.tick(5000);
  }
  assert.equal(f.notices.length, 0);
  assert.ok(f.requests.length >= 4, 'disconnected subscription still reads snapshots');
  assert.ok(f.requests.every(([method]) => method === 'GET'), 'opening a page never starts a search');
  search.destroy();
  assert.equal(f.timers.size, 0);
});

test('event recovery preserves saved results and resumes projection updates', async t => {
  const f = fixture();
  const job = {search_id: 'saved', query: 'test', status: 'done', result_count: 1,
    results: [{resource_id: 'repo_test'}]};
  f.searches([job]);
  const errors = [];
  const search = f.create({initialActiveId: 'saved', onError: error => errors.push(error)});
  t.after(() => { search.destroy(); f.close(); });
  await search.load();
  f.event('portal:events-error');
  await f.tick(0);
  assert.equal(search.activeId(), 'saved');
  assert.equal(search.resultFor('repo_test').rank, 1);
  assert.equal(errors.length, 0);
  await f.tick(5000);
  f.event('portal:events-ready');
  await f.tick(100);
  f.searches([{...job, query: 'updated'}]);
  f.window.dispatchEvent(new f.window.CustomEvent('portal:change', {
    detail: {projection_changes: {'semantic-search': {saved: ['scope:repo']}}},
  }));
  await f.tick(100);
  assert.equal(search.activeJob().query, 'updated');
  assert.equal(errors.length, 0);
});

test('real list and explicit search action failures remain visible', async t => {
  const f = fixture();
  f.searches([{search_id: 'saved', query: 'saved', status: 'done', result_count: 1}]);
  const search = f.create(); t.after(() => { search.destroy(); f.close(); });
  await search.load();
  // Even an identical error string from an HTTP request is a real read failure.
  f.getError(new Error('Portal event stream unavailable'));
  await assert.rejects(search.load({fresh: true}), /Portal event stream unavailable/);
  assert.equal(f.notices.length, 1);
  assert.equal(search.jobs().length, 1, 'failed read retains existing results');
  f.postError(new Error('operation rejected'));
  f.window.document.getElementById('query').value = 'test query';
  for (const selector of ['#start', '[data-semantic-refresh]', '[data-semantic-delete]']) {
    f.window.document.querySelector(selector).click();
    await flush();
  }
  assert.equal(f.notices.length, 4);
  assert.ok(f.notices.slice(1).every(item => item.message.includes('operation rejected')));
});

test('coordinator identifies subscription errors independently of refresh/apply errors', async t => {
  const f = fixture();
  const errors = [];
  let callbacks, failFetch = true, failSubscribe = false;
  const owner = f.window.PortalRefreshCoordinator.create({
    subscribe(value) {
      if (failSubscribe) throw new Error('subscribe failed');
      callbacks = value;
      value.onOpen();
      return () => {};
    },
    fetch: () => { if (failFetch) throw new Error('read failed'); return {}; },
    apply: () => { throw new Error('apply failed'); },
    onError: (error, context) => errors.push([error.message, context.source]),
  });
  t.after(() => { owner.destroy(); f.close(); });
  owner.start();
  await assert.rejects(owner.refresh(), /read failed/);
  failFetch = false;
  await assert.rejects(owner.refresh(), /apply failed/);
  callbacks.onUpdate({}); await flush();
  callbacks.onError(new Error('stream failed'));
  failSubscribe = true; owner.reconnect();
  assert.deepEqual(errors, [
    ['read failed', 'refresh'], ['apply failed', 'refresh'], ['apply failed', 'apply'],
    ['stream failed', 'subscription'], ['subscribe failed', 'subscription'],
  ]);
  owner.destroy();
  callbacks.onError(new Error('late stream error'));
  assert.equal(errors.length, 5, 'stale callback cannot report an error');
});
