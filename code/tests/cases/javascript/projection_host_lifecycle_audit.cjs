'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const { createHarness } = require('./projection_watch_contracts.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const pause = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(predicate) {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await pause(); }
  assert.fail('condition not reached');
}

for (const failedAsset of ['projection_sdk.js', 'portal_state.js', 'portal_workbench.css']) {
  test(`bootstrap retry only rereads failed ${failedAsset}`, async () => {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'https://fixture.invalid/' });
    const w = dom.window, counts = new Map(), panels = [];
    let fail = true, id = 0;
    w.PortalProjections = { request() { throw Error('no business calls'); } };
    w.PortalWorkbench = { registerPanelPackage() {} };
    w.PortalI18n = { t: value => value };
    w.PortalUIPrimitives = { randomUUID: () => String(++id) };
    w.matchMedia = () => ({ addEventListener() {} });
    w.fetch = async url => {
      counts.set(url, (counts.get(url) || 0) + 1);
      if (fail && url.endsWith(failedAsset)) return { ok: false };
      return { ok: true, text: async () => '' };
    };
    w.eval(fs.readFileSync(path.join(staticRoot, 'projection_host.js'), 'utf8'));
    const mount = () => {
      const root = w.document.createElement('section'); w.document.body.append(root);
      const panel = w.PortalProjectionHost.createPanel({ root, app: { title: 'fixture', html: '<main>fixture</main>' } });
      panels.push(panel); return panel;
    };
    try {
      const first = mount(), concurrent = mount();
      await assert.rejects(first.ready); await assert.rejects(concurrent.ready);
      assert.equal(counts.size, 14);
      assert.ok([...counts.values()].every(value => value === 1), 'concurrent panels share every read');
      first.destroy(); concurrent.destroy(); fail = false;
      const retry = mount(), peer = mount();
      await Promise.all([retry.ready, peer.ready]);
      assert.equal(w.document.querySelectorAll('iframe').length, 2);
      for (const [url, count] of counts) assert.equal(count, url.endsWith(failedAsset) ? 2 : 1, url);
      console.log(JSON.stringify({ scenario: 'bootstrap-retry', failedAsset, totalReads: [...counts.values()].reduce((a, b) => a + b, 0) }));
    } finally { panels.forEach(panel => panel.destroy()); dom.window.close(); }
  });
}

for (const ending of ['hide', 'destroy', 'late-hide-show', 'accept']) {
  test(`confirmation lifetime ${ending}`, async () => {
    let release, calls = 0, confirmed = 0;
    const h = await createHarness({ apiResponse: (url, payload) => {
      if (url !== '/api/workbenches/isa') return;
      calls++;
      if (payload.confirmed) { confirmed++; return Response.json({ accepted: true }); }
      const response = () => Response.json({ code: 'projection.confirmation_required', error: 'confirm' }, { status: 409 });
      return ending === 'late-hide-show' ? new Promise(resolve => { release = () => resolve(response()); }) : response();
    } });
    h.window.PortalI18n = { t: x => x, markup: x => x };
    h.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    try {
      const result = h.sdk.hatg('FixtureMutation').then(value => value, error => error.code);
      if (ending === 'late-hide-show') {
        await until(() => release); h.host.setVisible(false); h.host.setVisible(true); release();
        await until(() => h.messages.some(m => m.type === 'portal-projection-result' && m.code === 'projection.cancelled') || h.window.document.querySelector('dialog'));
        assert.equal(h.window.document.querySelector('dialog'), null, 'late permission response must not reopen confirmation');
      } else {
        await until(() => h.window.document.querySelector('dialog'));
        const dialog = h.window.document.querySelector('dialog');
        if (ending === 'hide') h.host.setVisible(false);
        if (ending === 'destroy') h.host.destroy();
        if (ending !== 'accept') assert.equal(h.window.document.querySelector('dialog'), null, 'owner ending removes pending dialog');
        dialog.returnValue = 'confirm'; dialog.dispatchEvent(new h.window.Event('close'));
      }
      if (ending !== 'destroy') {
        const reply = await result;
        assert.equal(ending === 'accept' ? reply.accepted : reply, ending === 'accept' ? true : 'projection.cancelled');
      }
      await pause();
      assert.equal(confirmed, ending === 'accept' ? 1 : 0);
      assert.equal(calls, ending === 'accept' ? 2 : 1);
    } finally { release?.(); await h.close(); }
  });
}

function sdkFixture() {
  const events = new Map(), timers = new Map(), sent = [], errors = [];
  let timerId = 0, terminated = 0;
  const host = { postMessage: packet => sent.push(packet) };
  const scope = {
    document: { currentScript: { dataset: { channel: 'fixture' } } }, parent: host,
    addEventListener: (type, fn) => { const listeners = events.get(type) || []; listeners.push(fn); events.set(type, listeners); },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    performance: { now: () => 0 }, URL: { createObjectURL: () => 'blob:fixture', revokeObjectURL() {} }, Blob,
    Worker: class { terminate() { terminated++; } }, console: { error: (...args) => errors.push(args) },
  };
  scope.window = scope;
  vm.runInNewContext(fs.readFileSync(path.join(staticRoot, 'projection_sdk.js'), 'utf8'), scope);
  const emit = (type, event) => (events.get(type) || []).forEach(fn => fn(event));
  const reply = (packet, result) => emit('message', { source: host, data: { type: 'portal-projection-result', channel: 'fixture', requestId: packet.requestId, result } });
  const confirmation = packet => emit('message', { source: host, data: { type: 'portal-projection-confirmation', channel: 'fixture', requestId: packet.requestId, phase: 'pending' } });
  return { sdk: scope.Projection, emit, reply, confirmation, sent, timers, errors, terminated: () => terminated };
}

test('sdk preserves cached page and workers across persisted pagehide', async () => {
  const h = sdkFixture();
  const worker = h.sdk.createWorker('fixture.js'); h.reply(h.sent.at(-1), ''); await worker;
  const pending = h.sdk.context();
  h.emit('pagehide', { persisted: true });
  assert.equal(h.terminated(), 0, 'browser-cache suspension must preserve workers');
  h.reply(h.sent.at(-1), { visible: true }); assert.equal((await pending).visible, true);
  const restored = h.sdk.loadLibrary('fixture');
  // The source read must reach the host; the test does not execute any library.
  assert.equal(h.sent.at(-1).method, 'library-source');
  h.emit('pagehide', { persisted: false }); await assert.rejects(restored, { code: 'projection.disposed' });
});

test('sdk final disposal settles pending reads and confirmation budgets without new calls', async () => {
  const h = sdkFixture();
  const work = h.sdk.state({ draft: 'fixture' }).catch(error => error.code);
  const packet = h.sent.at(-1);
  h.confirmation(packet);
  assert.equal(h.timers.size, 0, 'confirmation pauses the request budget');
  h.sdk.onFocus(() => assert.fail('disposed focus callback'));
  h.sdk.onVisibility(() => assert.fail('disposed visibility callback'));
  h.emit('pagehide', { persisted: false });
  assert.equal(h.timers.size, 0, 'final disposal clears all request timers');
  assert.equal(await work, 'projection.disposed');
  const count = h.sent.length;
  await assert.rejects(h.sdk.context(), { code: 'projection.disposed' });
  assert.equal(h.sent.length, count, 'no new host request after disposal');
  h.reply(packet, { saved: true });
  await pause(); assert.equal(h.errors.length, 0, 'expected disposal is not an unhandled subscription failure');
});

test('sdk does not install a watch listener when final disposal follows the host reply', async () => {
  const h = sdkFixture();
  const watching = h.sdk.watch(['article_fixture'], () => assert.fail('disposed watch callback'));
  h.reply(h.sent.at(-1), {});
  h.emit('pagehide', { persisted: false });
  await assert.rejects(watching, { code: 'projection.disposed' });
});

let applicationLifecycle;
for (const phase of ['mounted', 'waiting-visibility', 'mount-pending', 'manual-dispose']) {
  test(`application persisted pagehide and pageshow preserve ${phase} until final disposal`, async () => {
    const development = path.resolve(staticRoot, '../workbench_projections/development_app');
    applicationLifecycle ||= require(path.join(development, 'node_modules/esbuild')).buildSync({
      entryPoints: [path.join(development, 'src/shared/lifecycle.js')], bundle: true, write: false,
      format: 'iife', globalName: 'TestLifecycle', platform: 'browser',
    }).outputFiles[0].text;
    const dom = new JSDOM('<!doctype html><main id="app"></main>', { runScripts: 'outside-only' });
    const w = dom.window, callbacks = new Set(), pagehideListeners = new Set();
    const add = w.addEventListener.bind(w), remove = w.removeEventListener.bind(w);
    w.addEventListener = (type, listener, options) => {
      if (type === 'pagehide') pagehideListeners.add(listener);
      add(type, listener, options);
    };
    w.removeEventListener = (type, listener, options) => {
      if (type === 'pagehide') pagehideListeners.delete(listener);
      remove(type, listener, options);
    };
    w.Projection = {
      onVisibility(fn) {
        callbacks.add(fn);
        queueMicrotask(() => { if (callbacks.has(fn)) fn(phase !== 'waiting-visibility'); });
        return () => callbacks.delete(fn);
      },
    };
    w.eval(applicationLifecycle);
    let context, release, mounts = 0, cleaned = 0, destroyed = 0, shows = 0, hides = 0, draft;
    const runtime = { show() { shows++; }, hide() { hides++; }, destroy() { destroyed++; } };
    const starting = w.TestLifecycle.start((root, ctx) => {
      mounts++; context = ctx;
      draft = w.document.createElement('input'); draft.value = 'unsaved fixture'; root.append(draft);
      ctx.onDispose(() => { cleaned++; });
      return phase === 'mount-pending' ? new Promise(resolve => { release = () => resolve(runtime); }) : runtime;
    }, 'architecture');
    const visibility = value => [...callbacks].forEach(fn => fn(value));
    const page = (type, persisted) => w.dispatchEvent(new w.PageTransitionEvent(type, { persisted }));
    try {
      if (phase === 'waiting-visibility') await until(() => callbacks.size === 1);
      else await until(() => context);
      if (phase === 'mounted' || phase === 'manual-dispose') await starting;
      for (let cycle = 0; cycle < 2; cycle++) {
        page('pagehide', true); page('pageshow', true);
        assert.equal(context?.signal.aborted ?? false, false, 'cache suspension must not abort the region');
        assert.equal(callbacks.size, 1, 'restored visibility subscription remains live');
        assert.equal(cleaned, 0); assert.equal(destroyed, 0);
        if (draft) assert.equal(draft.value, 'unsaved fixture');
      }
      if (phase === 'waiting-visibility') { assert.equal(mounts, 0); visibility(true); }
      if (phase === 'mount-pending') release();
      const dispose = await starting;
      assert.equal(mounts, 1, 'restoration never remounts the region');
      visibility(false); visibility(true);
      assert.equal(hides, 1); assert.equal(shows, 1);
      assert.equal(w.document.querySelector('input'), draft, 'same draft DOM survives restoration');
      assert.equal(draft.value, 'unsaved fixture');
      if (phase === 'manual-dispose') dispose(); else page('pagehide', false);
      assert.equal(context.signal.aborted, true);
      assert.equal(cleaned, 1); assert.equal(destroyed, 1);
      assert.equal(callbacks.size, 0);
      assert.equal(pagehideListeners.size, 0, 'final disposal removes the lifecycle listener');
      dispose(); page('pagehide', false); page('pageshow', true); visibility(true);
      assert.equal(cleaned, 1); assert.equal(destroyed, 1); assert.equal(mounts, 1);
      console.log(JSON.stringify({ scenario: 'application-page-cache', phase, persistedCycles: 2, mounts, cleaned, destroyed }));
    } finally { release?.(); dom.window.close(); }
  });
}
