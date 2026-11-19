'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const zlib = require('node:zlib');
const { pathToFileURL } = require('node:url');
const { createHarness } = require('./projection_watch_contracts.cjs');

const development = path.resolve(__dirname, '../../../management_portal/workbench_projections/development_app');
const bundle = require(path.join(development, 'node_modules/esbuild')).buildSync({
  entryPoints: [path.join(development, 'src/architecture/exploration-app.js')],
  bundle: true, write: false, format: 'iife', globalName: 'Exploration', platform: 'browser',
  loader: { '.css': 'empty' },
}).outputFiles[0].text;
const model = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(development, 'tests/fixtures/frozen-exploration/model.json.gz'))));
const instructionBundle = require(path.join(development, 'node_modules/esbuild')).buildSync({
  entryPoints: [path.join(development, 'src/architecture/instruction-workbench.js')],
  bundle: true, write: false, format: 'iife', globalName: 'Instructions', platform: 'browser',
  loader: { '.css': 'empty' },
}).outputFiles[0].text;
let mapRef;
const reviewPromise = import(pathToFileURL(path.join(development, 'src/architecture/exploration-source-review.js')))
  .then(({ default: review }) => { mapRef = review.map; return review; });
const mapText = 'modules:\n  - id: interface\n    title: 浏览器前端\n  - id: router\n    title: 用户路由入口\n';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, label) {
  const deadline = Date.now() + 4000;
  while (!predicate()) { assert.ok(Date.now() < deadline, label); await pause(5); }
}

async function fixture() {
  const sourceReview = await reviewPromise;
  // An explicitly reviewed candidate is supplied to this controlled fixture.
  // Frozen historical input bytes remain unchanged.
  const currentModel = structuredClone(model);
  for (const object of currentModel.objects) {
    const record = sourceReview.objects[object.id];
    if (record?.status === 'current') object.identity.resource_ref = record.address;
  }
  const state = { error: null, gate: null, reads: 0, objects: [], facadeCalls: 0, mapText };
  const h = await createHarness({
    context: [{ channel: 'focus', publishers: ['architecture'], subscribers: ['architecture'] }],
    // postMessage creates objects in the receiving realm. Preserve undefined
    // and cycles as well; a JSON stringify bridge would conceal invalid scenes.
    transportClone(target, packet) {
      return target.eval(`(function clone(value, seen = new Map()) {
        if (value === null || typeof value !== 'object') return value;
        if (seen.has(value)) return seen.get(value);
        const result = Array.isArray(value) ? [] : {};
        seen.set(value, result);
        for (const key of Object.keys(value)) result[key] = clone(value[key], seen);
        return result;
      })`)(packet);
    },
    objectResponse(payload) { state.objects.push(payload.object); return Response.json(payload.object); },
    apiResponse(url, payload) {
      if (url === '/api/workbenches/object' || url === '/api/workbenches/observe') return undefined;
      if (url === '/api/workbenches/facade') {
        state.facadeCalls++;
        return Response.json({ resident_id: 'resident_111111111111', lineage_id: 'lineage_111111111111' });
      }
      assert.equal(url, '/api/workbenches/resource', 'no business send or other endpoint is allowed');
      if (payload.operation === 'resolve') return Response.json({ readable: true, title: '验收门面' });
      assert.equal(payload.operation, 'read');
      assert.equal(payload.arguments.address, mapRef);
      return (async () => {
        state.reads++;
        if (state.gate) await state.gate;
        return state.error ? Response.json(state.error.body, { status: state.error.status }) : Response.json({ text: state.mapText });
      })();
    },
  });
  const child = h.child;
  child.structuredClone = structuredClone;
  child.TextDecoder = TextDecoder;
  // Only omitted DOM capabilities are supplied. Production graph, session,
  // resource transport, SDK, host guards and confirmation all execute unchanged.
  child.ResizeObserver = class { observe() {} disconnect() {} };
  h.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  h.window.HTMLDialogElement.prototype.close = function (value) {
    this.returnValue = value; this.open = false; this.dispatchEvent(new h.window.Event('close'));
  };
  let app;
  try {
    child.eval(bundle);
    const root = child.document.createElement('main'); child.document.body.append(root);
    const exploration = { status: 'available', mode: 'current', batchKey: 'isolated-feedback',
      model: currentModel, sourceReview, requiredRefs: [],
      pinned: { exploration: { address: 'workspace_111111111111/file/manifest.json' }, model: { address: 'workspace_111111111111/file/model.json' } },
      binding: { module_map: { address: mapRef } },
    };
    app = await child.Exploration.mountExploration(root, { sdk: h.sdk, onFocus: fn => h.sdk.onFocus(fn) }, { candidate: { exploration } });
    assert.equal(app.session.snapshot().status, 'ready', root.textContent);
    const button = text => [...root.querySelectorAll('button')].find(node => node.textContent === text);
    const textarea = () => root.querySelector('textarea[aria-label="反馈内容"]');
    const open = () => root.querySelector('[aria-label="反馈所选对象"]').click();
    const submit = () => button('交宿主确认').click();
    const result = () => root.querySelector('.ade-source').textContent;
    const settled = () => !button('交宿主确认').disabled;
    return { ...h, app, root, state, button, textarea, open, submit, result, settled,
      async close() { app.destroy(); await h.close(); },
      async draft() {
        await app.session.select({ objectId: 'interface' }).catch(error => {
          if (!state.error) throw error;
          assert.equal(error.code, state.error?.body.code, 'only the controlled source failure is allowed');
        });
        assert.ok(state.reads > 0, root.querySelector('.ade-notice').textContent);
        open();
        textarea().value = '保留原草稿，不发送业务消息';
        textarea().dispatchEvent(new child.Event('input', { bubbles: true }));
      },
    };
  } catch (error) { app?.destroy(); await h.close(); throw error; }
}

test('readable instruction teaching needs no invented current protocol identity', () => {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<main></main>', { runScripts: 'outside-only' });
  const selections = [], content = JSON.parse(fs.readFileSync(path.join(development, 'tests/fixtures/instruction-catalog.json')));
  let view;
  try {
    dom.window.eval(instructionBundle);
    const root = dom.window.document.querySelector('main');
    view = dom.window.Instructions.createInstructionWorkbench(root, { content, onSelect: value => selections.push(value) });
    assert.ok(root.querySelectorAll('.iw-entry').length > 0);
    assert.doesNotMatch(root.textContent, /需要父视图提供当前协议身份|无法读取指令目录/);
    assert.ok(selections.length > 0);
    assert.deepEqual(Object.keys(selections[0]).sort(), ['category', 'instruction']);
  } finally { view?.destroy(); dom.window.close(); }
});

for (const [name, status, code, expected] of [
  ['missing resource', 404, 'resource_not_found', /来源资源不存在/],
  ['revoked permission', 403, 'permission_denied', /无权读取来源/],
  ['unclassified endpoint error', 404, 'http_error', /controlled source failure/],
]) {
  test(`feedback reports ${name} without replacing it with a stale-selection error`, async () => {
    const h = await fixture();
    try {
      if (status === 404) h.state.error = { status, body: { code, error: 'controlled source failure' } };
      await h.draft(); const objects = h.state.objects.length;
      h.state.error = { status, body: { code, error: 'controlled source failure' } };
      h.submit(); await until(h.settled, 'source failure settled');
      assert.match(h.result(), expected);
      if (code !== 'http_error') assert.ok(h.result().includes(mapRef));
      else assert.doesNotMatch(h.result(), /无权读取来源|来源资源不存在/);
      assert.doesNotMatch(h.result(), /对象或时点已改变/);
      assert.equal(h.textarea().value, '保留原草稿，不发送业务消息');
      assert.equal(h.state.objects.length, objects, 'failed read publishes no new focus');
      assert.equal(h.state.facadeCalls, 0, 'failed read never enters confirmation');
      assert.equal(h.window.document.querySelector('dialog'), null);
      if (code === 'resource_not_found') {
        h.state.error = null; h.submit();
        await until(() => h.window.document.querySelector('dialog[open]') || h.settled(), 'explicit retry');
        const dialog = h.window.document.querySelector('dialog');
        assert.ok(dialog, h.result()); dialog.close('cancel');
        await until(h.settled, 'retry cancellation');
        assert.equal(h.state.facadeCalls, 1, 'retry observes the source afresh but does not send');
        assert.equal(h.textarea().value, '保留原草稿，不发送业务消息');
      }
    } finally { await h.close(); }
  });
}

test('valid reselected object reaches native confirmation with draft intact; cancellation sends nothing', async () => {
  const h = await fixture();
  try {
    await h.draft();
    for (let attempt = 0; attempt < 2; attempt++) {
      h.submit(); await until(() => h.window.document.querySelector('dialog[open]') || h.settled(), 'native confirmation');
      const dialog = h.window.document.querySelector('dialog');
      assert.ok(dialog, h.result());
      assert.match(dialog.textContent, /保留原草稿，不发送业务消息/);
      assert.equal(h.app.session.snapshot().selectionId, 'interface', 'own focus echo keeps selection');
      dialog.close('cancel'); await until(h.settled, 'cancel settled');
      assert.equal(h.textarea().value, '保留原草稿，不发送业务消息');
      assert.equal(h.state.facadeCalls, attempt + 1, 'only presentation, never native send');
      await h.app.session.select({ objectId: 'router' });
      await h.app.session.select({ objectId: 'interface' }); h.open();
    }
  } finally { await h.close(); }
});

test('removed current map object retains its draft without inventing focus or entering confirmation', async () => {
  const h = await fixture();
  try {
    await h.draft(); const objects = h.state.objects.length;
    h.state.mapText = 'modules:\n  - id: router\n';
    h.submit(); await until(h.settled, 'removed object settled');
    assert.match(h.result(), /当前地图已无该对象/);
    assert.doesNotMatch(h.result(), /对象或时点已改变/);
    assert.equal(h.textarea().value, '保留原草稿，不发送业务消息');
    assert.equal(h.state.objects.length, objects);
    assert.equal(h.state.facadeCalls, 0);
    assert.equal(h.window.document.querySelector('dialog'), null);
  } finally { await h.close(); }
});

test('selection changing during source read remains stale and cannot confirm the old draft', async () => {
  const h = await fixture(); let release;
  try {
    await h.draft(); const reads = h.state.reads;
    h.state.gate = new Promise(resolve => { release = resolve; });
    h.submit(); await until(() => h.state.reads > reads, 'source read started');
    const selecting = h.app.session.select({ objectId: 'router' });
    release(); h.state.gate = null; await selecting;
    await h.app.session.select({ objectId: 'interface' }); h.open();
    await until(h.settled, 'superseded feedback settled');
    assert.match(h.result(), /对象或时点已改变/);
    assert.equal(h.textarea().value, '保留原草稿，不发送业务消息');
    assert.equal(h.state.facadeCalls, 0);
  } finally { release?.(); await h.close(); }
});
