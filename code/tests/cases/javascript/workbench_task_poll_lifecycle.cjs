const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../../management_portal/static/workbench_studio.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const tag = (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '');
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
const app = () => ({ workbench_id: 'workbench_owned', title: 'Owned', revision: 1, bindings: {}, allows: { UpdateResource: true } });
const job = status => ({ id: 'job_owned', workbench_id: 'workbench_owned', status });

async function fixture({ saved = true, existing = true, request, mount } = {}) {
  const dom = new JSDOM('<div class="projectionHeading"><h1 id="pageTitle"></h1><p id="pageDescription"></p></div><div id="projectionContent"></div><div id="projectionError" hidden></div>', {
    url: 'https://portal.test/workbenches?panel=settings' + (existing ? '&id=workbench_owned' : ''), runScripts: 'outside-only',
  });
  const w = dom.window, calls = [], timers = new Map(); let timerId = 0;
  if (saved) w.sessionStorage.setItem('portal.projection.generation', JSON.stringify({ id: 'job_owned', workbench_id: 'workbench_owned' }));
  Object.assign(w, {
    PortalProjections: { request: async (url, body) => {
      calls.push({ url, body });
      if (request) return request(url, body);
      if (url.startsWith('detail?')) return app();
      if (url.startsWith('generation?')) return job('running');
      throw new Error('unexpected request: ' + url);
    } },
    PortalProjectionHost: { mount: mount || (async () => ({ destroy() {} })) },
    PortalSocietyService: { createClient: () => ({ listLineages: async () => ({ lineages: [] }) }) },
    PortalUIPrimitives: { escapeHtml: String, randomUUID: () => 'request_owned' },
    PortalI18n: { markup: String, t: String, text: tag, html: tag }, PortalSite: { setTitle() {} },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  w.eval(source); await tick();
  return { w, timers, calls, get: id => w.document.getElementById(id),
    leave() { w.dispatchEvent(new w.Event('pagehide')); },
    close() { w.close(); },
    async fire() { assert.equal(timers.size, 1); const [id, item] = timers.entries().next().value;
      assert.equal(item.delay, 3000); timers.delete(id); await item.callback(); await tick(); },
  };
}

test('Workbench initial detail cannot restore polling after page disposal', async () => {
  const response = deferred();
  const f = await fixture({ request: url => url.startsWith('detail?') ? response.promise : job('running') });
  try {
    assert.equal(f.calls.length, 1); f.leave(); response.resolve(app()); await tick();
    assert.equal(f.calls.length, 1); assert.equal(f.timers.size, 0);
  } finally { f.close(); }
});

test('Workbench late preview mount is destroyed and cannot restart task observation', async () => {
  const response = deferred(); let destroyed = 0;
  const f = await fixture({ request: async url => url.startsWith('detail?') ? { ...app(), html: '<p>preview</p>' } : job('running'), mount: () => response.promise });
  try {
    f.leave(); response.resolve({ destroy() { destroyed++; } }); await tick();
    assert.equal(destroyed, 1); assert.equal(f.calls.length, 1); assert.equal(f.timers.size, 0);
  } finally { f.close(); }
});

test('Workbench late task submission acknowledgement issues no follow-up read after disposal', async () => {
  const response = deferred();
  const f = await fixture({ saved: false, existing: false, request: url => url === 'generate' ? response.promise : app() });
  try {
    f.get('targetLineage').value = 'lineage_owned'; f.get('requirements').value = 'Synthetic request';
    const pending = f.get('generate').onclick({ currentTarget: f.get('generate') });
    assert.equal(f.calls.length, 1); assert.equal(f.calls[0].url, 'generate');
    f.leave(); response.resolve(job('running')); await pending; await tick();
    assert.equal(f.calls.length, 1); assert.equal(f.timers.size, 0);
  } finally { f.close(); }
});

test('Workbench queued and in-flight status callbacks do not read again after disposal', async () => {
  for (const phase of ['queued', 'inflight']) {
    const response = deferred(); let reads = 0;
    const f = await fixture({ request: async url => {
      if (url.startsWith('detail?')) return app();
      return ++reads === 1 ? job('running') : response.promise;
    } });
    try {
      assert.equal(f.get('projectionError').textContent, ''); assert.equal(f.timers.size, 1);
      const callback = f.timers.values().next().value.callback;
      let pending;
      if (phase === 'inflight') pending = f.fire();
      f.leave(); const before = f.calls.length;
      if (phase === 'queued') { const queued = callback(); response.resolve(job('running')); await queued; }
      else { response.resolve(job('completed')); await pending; }
      assert.equal(f.calls.length, before, phase); assert.equal(f.timers.size, 0, phase);
    } finally { f.close(); }
  }
});

test('Workbench active states poll once; terminal states and permanent read errors stop', async () => {
  for (const outcome of ['completed', 'failed', 'cancelled', 'conflict', 401, 403, 404]) {
    let reads = 0;
    const f = await fixture({ request: async url => {
      if (url.startsWith('detail?')) return app();
      if (++reads === 1) return job(outcome === 'completed' ? 'submitting' : 'running');
      if (typeof outcome === 'number') throw Object.assign(new Error('read denied'), { status: outcome });
      return job(outcome);
    } });
    try {
      assert.equal(f.get('projectionError').textContent, ''); await f.fire();
      assert.equal(reads, 2); assert.equal(f.timers.size, 0, String(outcome));
      if (typeof outcome === 'number') {
        assert.match(f.get('projectionError').textContent, /read denied/);
        assert.ok(f.w.sessionStorage.getItem('portal.projection.generation'));
      } else assert.equal(f.w.sessionStorage.getItem('portal.projection.generation'), null);
    } finally { f.close(); }
  }
});
