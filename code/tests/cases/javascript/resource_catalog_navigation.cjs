const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '../../..');
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const A = 'skill_111111111111', B = 'skill_222222222222';
const record = id => ({resource_id: id, resource_type: 'skill', title: id});

async function check(name) {
  const withList = name.startsWith('list-');
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'management_portal/resource_catalog.html'), 'utf8'),
    {url: 'http://fixture/resource/skill' + (withList ? '?item=' + A : ''), runScripts: 'outside-only'});
  const w = dom.window, pending = new Map(), opened = [], toasts = [];
  let initialLoad;
  pending.set(A, deferred());
  if (withList) pending.set('list', deferred());
  const text = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  w.PortalI18n = {t: String, text, html: text};
  w.PortalUIPrimitives = {escapeHtml: String, objectOf: v => v || {}, asyncStateHtml: v => v.message};
  w.PortalUI = {createDialogController: () => ({open() {}, close() {}}),
    toast: (message, kind) => toasts.push({message, kind})};
  w.PortalSite = {setTitle() {}};
  w.PortalFormControls = {bindFilterDropdown: () => ({open() {}, close() {}})};
  w.PortalAPI = {get: url => {
    const id = url.startsWith('/api/resource-index/query?') ? 'list' : decodeURIComponent(url.split('/').at(-1));
    assert.ok(pending.has(id), 'unexpected request: ' + url);
    return pending.get(id).promise;
  }};
  w.PortalComponentRecovery = {create: options => ({
    guard: fn => fn(),
    run() {
      if (withList) {
        initialLoad = options.onReady();
        initialLoad.catch(() => {});
      }
    },
    isUnavailable: error => error.code === 'resource_service_unavailable',
  })};
  function host(options) {
    let current = {status: 'empty', resourceId: ''};
    return {
      snapshot: () => current,
      open(resource) {
        current = {status: 'loading', resourceId: resource.resourceId};
        opened.push(resource.resourceId);
        options.onStateChange?.(current, 'loading');
      },
      close() {
        if (current.status !== 'empty') {
          current = {status: 'empty', resourceId: ''};
          options.onClose?.();
        }
      },
    };
  }
  w.PortalResourceDetail = {
    createDetailHistory: () => ({acceptCurrent() {}, replace() {}, open() {}, close() {}}),
    createFrameHost: host, createLocalHost: host,
  };
  const navigate = id => {
    w.history.replaceState({}, '', '/resource/skill' + (id ? '?item=' + id : ''));
    w.dispatchEvent(new w.PopStateEvent('popstate'));
  };
  try {
    w.eval(fs.readFileSync(path.join(root, 'management_portal/static/resource_catalog.js'), 'utf8'));
    if (!withList) navigate(A);
    if (withList) {
      if (name === 'list-close') navigate('');
      pending.get('list').resolve({resources: [], total: 0});
      await tick();
      if (name === 'list-close') pending.get(A).resolve(record(A));
      else if (name === 'list-service-unavailable') {
        const error = Object.assign(new Error('ResourceService unavailable'), {code: 'resource_service_unavailable'});
        pending.get(A).reject(error);
        await assert.rejects(initialLoad, error);
        assert.deepEqual(toasts, [], 'service recovery owns service outages');
        assert.deepEqual(opened, []);
        return;
      }
      else pending.get(A).reject(Object.assign(new Error('Resource not found'), {status: 404}));
      await initialLoad;
      await tick();
      assert.deepEqual(opened, [], 'the collection response must not reopen a dismissed detail');
      assert.equal(w.document.getElementById('resourceCount').textContent, '0 项', 'detail failure must preserve the loaded collection');
      assert.equal(toasts.length, name === 'list-close' ? 0 : 1);
      if (name === 'list-detail-failure') assert.equal(toasts[0].message, 'Resource not found');
    } else if (name === 'close') {
      navigate('');
      pending.get(A).resolve(record(A));
      await tick();
      assert.deepEqual(opened, [], 'closing a pending detail must not reopen it');
    } else if (name === 'filter') {
      const query = w.document.getElementById('resourceQuery');
      query.value = 'new search';
      query.dispatchEvent(new w.Event('input'));
      pending.get(A).resolve(record(A));
      await tick();
      assert.deepEqual(opened, [], 'a new filter invalidates the pending detail before debounce');
    } else if (name === 'latest' || name === 'stale-error') {
      pending.set(B, deferred());
      navigate(B);
      pending.get(B).resolve(record(B));
      await tick();
      if (name === 'latest') pending.get(A).resolve(record(A));
      else pending.get(A).reject(new Error('old lookup denied'));
      await tick();
      assert.deepEqual(opened, [B], 'the latest detail alone owns the surface');
      assert.deepEqual(toasts, [], 'stale errors must not replace current navigation');
    } else if (name === 'failure-retry') {
      pending.get(A).reject(Object.assign(new Error('Resource not found'), {status: 404}));
      await tick();
      assert.equal(toasts.length, 1, 'current inaccessible detail must report its failure');
      assert.equal(toasts[0].message, 'Resource not found');
      assert.equal(toasts[0].kind, 'error');
      assert.deepEqual(opened, []);
      pending.set(A, deferred());
      navigate(A);
      pending.get(A).resolve(record(A));
      await tick();
      assert.deepEqual(opened, [A], 'an explicit retry reads the resource again');
    } else assert.fail('unknown case: ' + name);
  } finally { w.close(); }
}
check(process.argv[2]).then(() => console.log(process.argv[2] + ': passed')).catch(error => {
  console.error(error); process.exitCode = 1;
});
