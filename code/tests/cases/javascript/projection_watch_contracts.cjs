'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const repository = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repository, 'management_portal/static');
const plain = value => JSON.parse(JSON.stringify(value));
const largeRefs = Array.from({ length: 584 }, (_, index) => `workspace_${index.toString(16).padStart(12, '0')}/file/source.json#L1`);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, label) {
  const deadline = Date.now() + 3000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, `timed out: ${label}`);
    await pause(5);
  }
}

// Complete, unchanged production modules own subscriptions, SDK continuations,
// HTTP parsing, DOM trees, workbench visibility and teardown. jsdom lacks srcdoc
// execution and MessageEvent.source for postMessage; the fixture transports
// structured-cloned packets asynchronously between real Window instances.
// It makes no assertions about browser sandbox, layout, focus or fullscreen.
async function createHarness({ sourceTransform = (_name, source) => source, context = [], objectResponse } = {}) {
  const errors = [], cleanupErrors = [], messages = [], observers = new Set();
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(String(error.stack || error)));
  virtualConsole.on('error', (...args) => {
    const message = args.map(String).join(' ');
    (message.includes('Region cleanup failed') ? cleanupErrors : errors).push(message);
  });
  const dom = new JSDOM('<!doctype html><html><body><main id="projection"></main></body></html>', {
    url: 'https://projection.fixture.invalid/', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  window.matchMedia = query => {
    assert.match(query, /^\((?:max-width: \d+px|prefers-reduced-motion: reduce|prefers-color-scheme: dark)\)$/);
    const media = new window.EventTarget();
    Object.defineProperties(media, { media: { value: query }, matches: { value: false } });
    return media;
  };
  window.CSS = { escape(value) {
    assert.match(String(value), /^[a-zA-Z_][a-zA-Z0-9_-]*$/);
    return String(value);
  } };
  const state = { calls: [], responses: 0, holdAt: -1, denyRef: null, denyEmpty: false, emptyError: null };
  const releases = new Set();
  window.fetch = async (url, options) => {
    if (url.startsWith('/static/')) {
      const name = url.slice('/static/'.length);
      assert.ok(['projection_sdk.js', 'portal_state.js', 'portal_components.css', 'portal_design_system.css', 'portal_workbench.css'].includes(name));
      return new Response(sourceTransform(name, fs.readFileSync(path.join(staticRoot, name), 'utf8')));
    }
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.headers['Content-Type'], 'application/json');
    const payload = JSON.parse(options.body);
    if (objectResponse && url === '/api/workbenches/object') return objectResponse(payload);
    assert.equal(url, '/api/workbenches/observe');
    state.calls.push(payload);
    const sequence = state.calls.length;
    if (sequence === state.holdAt) await new Promise(resolve => {
      state.release = () => { releases.delete(state.release); resolve(); };
      releases.add(state.release);
    });
    let status = 200;
    let json = { references: payload.refs.map(ref => ({ ref, root_id: ref.split(/[\/#]/)[0] })), observed_at: `observation-${sequence}` };
    if (payload.refs.length > 128) { status = 400; json = { code: 'projection.references', error: '最多128个对象地址' }; }
    else if (payload.refs.includes(state.denyRef) || (state.denyEmpty && !payload.refs.length)) {
      status = 403; json = { code: 'resource.forbidden', error: '当前查看者权限已撤销' };
    } else if (!payload.refs.length && state.emptyError) {
      status = 409; json = { code: state.emptyError, error: '停止请求失败' };
    }
    state.responses++;
    return Response.json(json, { status });
  };
  const transportTimers = new Set(), frames = new Map();
  const transport = (target, source, packet) => {
    const data = structuredClone(packet);
    messages.push(data);
    const timer = setTimeout(() => {
      transportTimers.delete(timer);
      if (target.document) target.dispatchEvent(new target.MessageEvent('message', { data, source }));
    }, 0);
    transportTimers.add(timer);
  };
  window.postMessage = (packet, origin) => {
    assert.equal(origin, '*');
    const source = frames.get(packet.channel);
    assert.ok(source, 'SDK request originates from a mounted frame');
    transport(window, source, packet);
  };
  let host;
  const close = async () => {
    host?.destroy();
    for (const release of releases) release();
    await pause(20);
    for (const timer of transportTimers) clearTimeout(timer);
    transportTimers.clear();
    for (const observer of observers) observer.disconnect();
    observers.clear();
    assert.equal(window.document.querySelectorAll('iframe').length, 0, 'host destroys all frame elements');
    if (host) assert.equal(window.PortalWorkbench.get(host.workbench.id), null, 'workbench unregisters');
    window.close();
    assert.equal(window.document, undefined, 'fixture window closes');
    assert.deepEqual(errors, [], 'no uncaught production or DOM errors');
  };
  try {
    for (const name of ['portal_text.js', 'portal_state.js', 'portal_ui.js', 'portal_ui_primitives.js',
      'portal_command_registry.js', 'portal_editor_open_service.js', 'portal_workbench_context.js',
      'portal_workbench.js', 'portal_projections.js', 'projection_host.js']) {
      window.eval(sourceTransform(name, fs.readFileSync(path.join(staticRoot, name), 'utf8')) + `\n//# sourceURL=${name}`);
    }
    host = await window.PortalProjectionHost.mount(window.document.getElementById('projection'), {
      workbench_id: 'workbench_111111111111', title: '投影订阅契约', bindings: {},
      manifest: { schema: 'tfi.projection.v2', sidebar: [
        { id: 'architecture', title: '架构与协议', source: 'a.html' },
        { id: 'progress', title: '阶段进展', source: 'b.html' },
      ], context }, panels: { 'a.html': '<main id="app"></main>', 'b.html': '<main id="app"></main>' },
    });
    for (const frame of window.document.querySelectorAll('iframe')) {
      const parsed = new window.DOMParser().parseFromString(frame.srcdoc, 'text/html');
      const channel = parsed.querySelector('script[data-channel]').dataset.channel;
      const child = frame.contentWindow;
      frames.set(channel, child);
      child.postMessage = (packet, origin) => {
        assert.equal(origin, '*');
        transport(child, window, packet);
      };
      child.document.open();
      child.document.write(frame.srcdoc);
      child.document.close();
      assert.ok(child.Projection, 'complete production SDK executes in the frame document');
    }
    await pause(30);
    const child = [...frames.values()][0];
    assert.equal((await child.Projection.context()).visible, true, 'initial production visibility');
    return {
      window, child, sdk: child.Projection, host, state, errors, cleanupErrors, messages, close,
      refresh(root) {
        window.dispatchEvent(new window.CustomEvent('portal:change', {
          detail: root ? { changed: { resource: [root] } } : { snapshot_required: true },
        }));
      },
    };
  } catch (error) { await close(); throw error; }
}

async function completeBatchContract(options) {
  const h = await createHarness(options);
  const { sdk, state } = h;
  try {
    const changes = [];
    state.holdAt = 5;
    let registered = false;
    const registration = sdk.watch([...largeRefs, largeRefs[0]], event => changes.push(plain(event))).then(stop => {
      registered = true;
      return stop;
    });
    await until(() => state.calls.length === 5, 'fifth authorization batch');
    assert.deepEqual(state.calls.map(call => call.refs.length), [128, 128, 128, 128, 72]);
    assert.deepEqual(state.calls.flatMap(call => call.refs), largeRefs, 'deduplicated complete group');
    assert.ok(state.calls.every(call => call.id === 'workbench_111111111111' && call.preview === false));
    assert.equal(registered, false, 'no registration before the last batch completes');
    state.release();
    await registration;
    h.refresh(largeRefs.at(-1).split(/[\/#]/)[0]);
    await until(() => changes.length === 1, 'one complete refreshed group');
    assert.deepEqual(changes[0].refs, largeRefs);
    assert.equal(changes[0].observed_at, 'observation-10', 'last completed batch owns observation time');
    assert.deepEqual(state.calls.slice(5).map(call => call.refs.length), [128, 128, 128, 128, 72]);
    state.denyRef = largeRefs[300];
    h.refresh();
    await until(() => changes.length === 2, 'revocation notification');
    assert.deepEqual(changes[1].refs, [], 'no partial authorized group after revocation');
    assert.equal(changes[1].code, 'resource.forbidden');
    assert.equal(state.calls.length, 13, 'no fourth or fifth batch after revocation');
    state.denyRef = null;
    h.refresh();
    await until(() => changes.length === 3, 'complete group recovers after revocation');
    assert.deepEqual(changes[2].refs, largeRefs);
    assert.equal(state.calls.length, 18);
    return { calls: state.calls, changes };
  } finally { await h.close(); }
}

async function replacementContract(options) {
  const h = await createHarness(options);
  const { sdk, child, state } = h;
  try {
    const changes = [], newChanges = [];
    const stopOld = await sdk.watch(['decision_111111111111'], event => changes.push(plain(event)));
    state.denyRef = largeRefs[300];
    await assert.rejects(sdk.watch(largeRefs, event => newChanges.push(plain(event))), { code: 'resource.forbidden' });
    state.denyRef = null;
    h.refresh();
    await until(() => changes.length === 1, 'old group survives failed replacement');
    assert.deepEqual(changes[0].refs, ['decision_111111111111']);
    assert.equal(newChanges.length, 0);
    state.denyEmpty = true;
    await assert.rejects(stopOld(), { code: 'resource.forbidden' });
    state.denyEmpty = false;
    await stopOld();
    const stoppedAt = state.calls.length;
    h.refresh();
    await pause(220);
    assert.equal(state.calls.length, stoppedAt, 'successful retry stops old group');
    state.holdAt = state.calls.length + 2;
    const pendingWatch = sdk.watch(largeRefs, event => newChanges.push(plain(event))).catch(error => error.code);
    await until(() => state.calls.length === state.holdAt, 'replacement second batch held');
    await sdk.watch(['decision_222222222222'], event => changes.push(plain(event)));
    state.release();
    assert.equal(await pendingWatch, 'projection.cancelled');
    const replacedAt = state.calls.length;
    await stopOld();
    assert.equal(state.calls.length, replacedAt, 'stale disposer does not issue a request');
    h.refresh();
    await until(() => changes.length === 2, 'new group receives refresh');
    assert.deepEqual(changes[1].refs, ['decision_222222222222']);
    assert.equal(state.calls.length, replacedAt + 1, 'superseded group cannot issue further batches');
    let failedReplacement;
    const overlap = event => {
      if (event.data?.type !== 'portal-projection-result'
        || event.data.result?.references?.[0]?.ref !== 'decision_333333333333') return;
      child.removeEventListener('message', overlap, true);
      failedReplacement = sdk.watch(null, event => newChanges.push(plain(event))).catch(error => error.code);
    };
    child.addEventListener('message', overlap, true);
    const stopCommitted = await sdk.watch(['decision_333333333333'], event => changes.push(plain(event)));
    assert.equal(await failedReplacement, 'projection.references');
    h.refresh();
    await until(() => changes.length === 3, 'committed reply survives a newer failing attempt');
    assert.deepEqual(changes[2].refs, ['decision_333333333333']);
    assert.equal(newChanges.length, 0, 'neither failed attempt installs its listener');
    await stopCommitted();
    return { calls: state.calls, changes, newChanges };
  } finally { await h.close(); }
}

async function inflightContract(options) {
  const h = await createHarness(options);
  const { sdk, host, window, state } = h;
  try {
    const changes = [];
    const stopWatch = await sdk.watch(largeRefs, event => changes.push(plain(event)));
    state.holdAt = state.calls.length + 2;
    h.refresh();
    await until(() => state.calls.length === state.holdAt, 'refresh second batch held');
    assert.equal(host.workbench.focusPanel('progress'), true);
    state.release();
    await pause(220);
    assert.equal(state.calls.length, 7, 'hidden panel does not continue batches');
    assert.equal(changes.length, 0, 'hidden panel receives no in-flight result');
    assert.equal(host.workbench.focusPanel('architecture'), true);
    await until(() => changes.length === 1, 'reshown panel reauthorizes');
    assert.deepEqual(state.calls.slice(7).flatMap(call => call.refs), largeRefs);
    state.holdAt = state.calls.length + 1;
    h.refresh();
    await until(() => state.calls.length === state.holdAt, 'whole-page refresh held');
    host.setVisible(false);
    host.setVisible(true);
    await until(() => changes.length === 2, 'reshown page starts a new complete observation');
    state.denyRef = largeRefs[0];
    state.release();
    await pause(220);
    assert.equal(changes.length, 2, 'old error is discarded even after page reshown');
    state.denyRef = null;
    state.holdAt = state.calls.length + 1;
    h.refresh();
    await until(() => state.calls.length === state.holdAt, 'refresh held before stop');
    await stopWatch();
    const stoppedAt = state.calls.length;
    state.release();
    await pause(220);
    assert.equal(state.calls.length, stoppedAt, 'stopped refresh cannot continue batches');
    assert.equal(changes.length, 2, 'stopped refresh cannot notify');
    state.holdAt = state.calls.length + 1;
    // Teardown removes the SDK document, so the caller deliberately discards
    // its registration exactly as the former browser test did.
    void sdk.watch(largeRefs).catch(() => {});
    await until(() => state.calls.length === state.holdAt, 'registration held before destroy');
    host.destroy();
    state.release();
    await pause(220);
    assert.equal(state.calls.length, state.holdAt, 'destroyed registration cannot continue batches');
    assert.equal(window.document.querySelectorAll('iframe').length, 0);
    return { calls: state.calls, changes };
  } finally { await h.close(); }
}

async function disposalContract(options = {}) {
  const h = await createHarness(options);
  const { child, state, errors, cleanupErrors } = h;
  try {
    const development = path.join(repository, 'management_portal/workbench_projections/development_app');
    const lifecycle = require(path.join(development, 'node_modules/esbuild')).buildSync({
      entryPoints: [path.join(development, 'src/shared/lifecycle.js')], bundle: true, write: false,
      format: 'iife', globalName: 'TestLifecycle', platform: 'browser',
    }).outputFiles[0].text;
    child.eval(options.sourceTransform ? options.sourceTransform('development-lifecycle.bundle.js', lifecycle) : lifecycle);
    const changes = [];
    let context;
    const disposeRegion = await child.TestLifecycle.start((_, value) => { context = value; return {}; }, 'architecture');
    const stopOld = await context.watch(['decision_111111111111'], event => changes.push(plain(event)));
    state.holdAt = state.calls.length + 1;
    const oldStopResult = stopOld();
    assert.equal(stopOld(), oldStopResult, 'overlapping disposal shares its in-flight Promise');
    await until(() => state.calls.length === state.holdAt, 'old stop held');
    const newWatch = context.watch(largeRefs, event => changes.push(plain(event)));
    await until(() => state.calls.length > state.holdAt, 'new watch begins before old stop returns');
    state.release();
    const [, stopNew] = await Promise.all([oldStopResult, newWatch]);
    h.refresh();
    await until(() => changes.length === 1, 'new subscription survives overlapping stop');
    assert.deepEqual(changes[0].refs, largeRefs);
    assert.deepEqual(errors, []);
    assert.deepEqual(cleanupErrors, []);
    state.emptyError = 'projection.cancelled';
    await assert.rejects(stopNew(), { code: 'projection.cancelled' });
    assert.equal(cleanupErrors.length, 1, 'non-superseded cancellation remains a logged error');
    state.emptyError = null;
    state.denyEmpty = true;
    const failedStop = stopNew();
    await until(() => cleanupErrors.length === 2, 'discarded cleanup still reports permission failure');
    await assert.rejects(failedStop, { code: 'resource.forbidden' });
    assert.deepEqual(errors, [], 'cleanup error never becomes an unhandled rejection');
    state.denyEmpty = false;
    await stopNew();
    const stoppedAt = state.calls.length;
    h.refresh();
    await pause(220);
    assert.equal(state.calls.length, stoppedAt, 'failed cleanup is retryable');
    disposeRegion();
    await pause(20);
    assert.equal(state.calls.length, stoppedAt, 'successful disposal is not repeated on region teardown');
    assert.deepEqual(errors, []);
    return { calls: state.calls, changes, errors, cleanupErrors };
  } finally { await h.close(); }
}

const contracts = { batches: completeBatchContract, replacement: replacementContract,
  inflight: inflightContract, disposal: disposalContract };
module.exports = { createHarness, contracts };
if (require.main === module) {
  assert.ok(contracts[process.argv[2]], 'select one independent contract');
  contracts[process.argv[2]]().then(evidence => {
    console.log(JSON.stringify({ result: 'PASS', contract: process.argv[2], ...evidence }));
  }).catch(error => { console.error(error); process.exitCode = 1; });
}
