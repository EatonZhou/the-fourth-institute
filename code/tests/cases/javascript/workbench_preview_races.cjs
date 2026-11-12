'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const tick = () => new Promise(resolve => setImmediate(resolve));
const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

async function studioFixture(empty = false) {
  const dom = new JSDOM('<header class="projectionHeading"><h1 id="pageTitle"></h1><p id="pageDescription"></p></header><div id="projectionContent"></div><div id="projectionError" hidden></div>', {
    url: 'https://fixture.invalid/workbenches?panel=settings&id=workbench_fixture', runScripts: 'outside-only',
  });
  const w = dom.window, mounts = [], reads = [];
  const app = revision => ({ workbench_id: 'workbench_fixture', title: 'Fixture', revision, html: empty && revision === 2 ? '' : `<p>version ${revision}</p>`, bindings: {}, navigation: 'none', allows: { UpdateResource: true } });
  Object.assign(w, {
    PortalProjections: { request: async (url, payload) => {
      reads.push({ url, payload });
      if (url.startsWith('detail?')) return app(1);
      if (url === 'update') return app(2);
      throw new Error('unexpected fixture request: ' + url);
    } },
    PortalProjectionHost: { mount: async (target, value, options) => {
      const pending = deferred(), record = { ...pending, options, destroyed: 0, revision: value.revision };
      mounts.push(record);
      await pending.promise;
      // The host contract is exercised independently below, with real host source.
      if (options.signal?.aborted) throw Object.assign(new Error('cancelled'), { code: 'projection.cancelled' });
      const frame = w.document.createElement('section'); frame.textContent = 'version ' + value.revision;
      target.replaceChildren(frame);
      return { destroy() { record.destroyed++; frame.remove(); } };
    } },
    PortalSocietyService: { createClient: () => ({ listLineages: async () => ({ lineages: [] }) }) },
    PortalUIPrimitives: { escapeHtml: String },
    PortalI18n: { markup: String, t: String, text: tag, html: tag }, PortalSite: { setTitle() {} },
  });
  w.eval(fs.readFileSync(path.join(root, 'workbench_studio.js'), 'utf8'));
  await tick();
  const get = id => w.document.getElementById(id);
  assert.equal(mounts.length, 1);
  return { w, mounts, reads, get,
    save: () => get('save').onclick({ currentTarget: get('save') }),
    close() { w.dispatchEvent(new w.PageTransitionEvent('pagehide', { persisted: false })); dom.window.close(); },
  };
}

for (const outcome of ['late-success', 'late-error', 'empty-successor']) {
  test(`studio ignores superseded initial preview ${outcome}`, async () => {
    const f = await studioFixture(outcome === 'empty-successor');
    try {
      const saving = f.save(); await tick();
      if (outcome !== 'empty-successor') { assert.equal(f.mounts.length, 2); f.mounts[1].resolve(); }
      await saving;
      const content = f.get('previewRoot').innerHTML;
      if (outcome === 'late-error') f.mounts[0].reject(new Error('obsolete network failure'));
      else f.mounts[0].resolve();
      await tick(); await tick();
      assert.equal(f.get('previewRoot').innerHTML, content, 'older mount cannot replace current preview');
      assert.equal(f.get('projectionError').textContent, '', 'obsolete failure cannot replace current status');
      assert.equal(f.mounts[0].options.signal?.aborted, true, 'superseded mount is cancelled');
      assert.equal(f.reads.length, 2, 'one detail and one simulated update, no extra retry');
    } finally { f.close(); }
  });
}

test('studio reports a failure from the current preview', async () => {
  const f = await studioFixture();
  try {
    f.mounts[0].reject(new Error('current network failure'));
    await tick();
    assert.equal(f.get('projectionError').textContent, 'current network failure');
  } finally { f.close(); }
});

test('studio preserves pending preview on cache suspension and cancels on final exit', async () => {
  const f = await studioFixture();
  try {
    f.w.dispatchEvent(new f.w.PageTransitionEvent('pagehide', { persisted: true }));
    assert.equal(f.mounts[0].options.signal?.aborted, false);
    f.w.dispatchEvent(new f.w.PageTransitionEvent('pageshow', { persisted: true }));
    assert.equal(f.mounts.length, 1, 'cache restore does not start another preview');
    f.w.dispatchEvent(new f.w.PageTransitionEvent('pagehide', { persisted: false }));
    assert.equal(f.mounts[0].options.signal?.aborted, true);
    const content = f.get('previewRoot').innerHTML;
    f.mounts[0].resolve(); await tick();
    assert.equal(f.get('previewRoot').innerHTML, content, 'late preview cannot mount after exit');
    assert.equal(f.get('projectionError').textContent, '');
  } finally { f.close(); }
});

for (const phase of ['before-read', 'during-read', 'failed-read']) {
  test(`host cancelled preview never replaces root ${phase}`, async () => {
    const dom = new JSDOM('<main id="target">current preview</main>', { runScripts: 'outside-only', url: 'https://fixture.invalid/' });
    const w = dom.window, response = deferred(); let reads = 0;
    w.PortalProjections = { request: () => { reads++; return response.promise; } };
    w.PortalWorkbench = { registerPanelPackage() {} };
    w.PortalI18n = { t: String };
    w.matchMedia = () => ({ addEventListener() {} });
    w.eval(fs.readFileSync(path.join(root, 'projection_host.js'), 'utf8'));
    const abort = new w.AbortController();
    try {
      if (phase === 'before-read') abort.abort();
      const pending = w.PortalProjectionHost.mount(w.document.getElementById('target'), {
        workbench_id: 'workbench_fixture', title: 'Old', bindings: {},
        manifest: { chat: { binding: 'main' }, sidebar: [{ id: 'main', source: 'main.html' }] }, panels: { 'main.html': '<p>old</p>' },
      }, { preview: true, signal: abort.signal }).catch(error => error);
      abort.abort();
      if (phase === 'failed-read') response.reject(new Error('late facade error')); else response.resolve({});
      const error = await pending;
      assert.equal(w.document.getElementById('target').textContent, 'current preview', 'no stale shell or registration');
      assert.equal(error.code, 'projection.cancelled');
      assert.equal(reads, phase === 'before-read' ? 0 : 1);
    } finally { dom.window.close(); }
  });
}

for (const outcome of ['success', 'failure']) {
  test(`region pending chat cannot register or report after preview replacement ${outcome}`, async () => {
    const dom = new JSDOM('<main id="target"></main>', { runScripts: 'outside-only', url: 'https://fixture.invalid/' });
    const w = dom.window, response = deferred(); let reads = 0, registrations = 0;
    w.PortalProjections = { request: () => { reads++; return response.promise; } };
    w.PortalWorkbench = { registerPanelPackage() {}, model: { stack: () => ({}), split: () => ({}) }, create() { registrations++; throw new Error('unexpected stale registration'); } };
    w.PortalI18n = { t: String };
    w.matchMedia = () => ({ addEventListener() {} });
    w.eval(fs.readFileSync(path.join(root, 'projection_host.js'), 'utf8'));
    const abort = new w.AbortController(), target = w.document.getElementById('target');
    try {
      const pending = w.PortalProjectionHost.mount(target, {
        workbench_id: 'workbench_fixture', title: 'Old',
        bindings: { main: { lineage_id: 'lineage_fixture', residents: { facade: 'resident_fixture' } } },
        manifest: { sidebar: [{ id: 'main', source: 'main.html', chat: { binding: 'main' } }] }, panels: { 'main.html': '<p>old</p>' },
      }, { preview: true, signal: abort.signal }).catch(error => error);
      assert.equal(reads, 1, 'reached the region facade request');
      const oldShell = target.firstElementChild;
      abort.abort(); target.textContent = 'new preview';
      if (outcome === 'failure') response.reject(new Error('obsolete region error'));
      else response.resolve({ resident_id: 'resident_fixture', lineage_id: 'lineage_fixture' });
      const error = await pending;
      assert.equal(target.textContent, 'new preview', 'old region errors cannot leak into a replacement root');
      assert.equal(registrations, 0, 'stable workbench ID must not be registered by the old preview');
      assert.equal(error.code, 'projection.cancelled');
      assert.equal(oldShell.isConnected, false);
    } finally { dom.window.close(); }
  });
}
