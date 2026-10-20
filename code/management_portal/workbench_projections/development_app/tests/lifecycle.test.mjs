import { test } from 'node:test';
import assert from 'node:assert/strict';
import { start } from '../src/shared/lifecycle.js';
import { schemaMatches } from '../src/shared/format-tags.js';

test('cleanup retains async results, reports discarded failures and retries without double disposal', async t => {
  const reports = [], listeners = new Map();
  t.mock.method(console, 'error', (...args) => reports.push(args));
  const savedWindow = globalThis.window, savedDocument = globalThis.document, savedListener = globalThis.addEventListener;
  globalThis.addEventListener = (name, fn) => listeners.set(name, fn);
  globalThis.window = { Projection: { resource: async () => ({format_namespaces: []}), onVisibility: fn => { queueMicrotask(() => fn(true)); return () => {}; } } };
  globalThis.document = { getElementById: () => ({}) };
  t.after(() => { globalThis.window = savedWindow; globalThis.document = savedDocument; globalThis.addEventListener = savedListener; });
  let context;
  const dispose = await start((_, value) => { context = value; return {}; }, 'test');
  let calls = 0, reject;
  const stop = context.onDispose(() => {
    calls++;
    return calls === 1 ? new Promise((_, fail) => { reject = fail; }) : Promise.resolve('stopped');
  });
  const pending = stop();
  assert.equal(stop(), pending);
  assert.equal(calls, 1);
  const failure = Object.assign(new Error('revoked'), { code: 'resource.forbidden' });
  reject(failure);
  await assert.rejects(pending, error => error === failure);
  assert.equal(reports.length, 1);
  assert.equal(reports[0][1], failure);
  assert.equal(await stop(), 'stopped');
  listeners.get('pagehide')();
  dispose();
  await Promise.resolve();
  assert.equal(calls, 2);
  let lateCalls = 0;
  context.onDispose(() => { lateCalls++; return Promise.reject(new Error('late cleanup failed')); });
  await Promise.resolve();
  assert.equal(lateCalls, 1);
  assert.equal(reports.length, 2);
  assert.equal(context.signal.aborted, true);
});

function regionEnvironment(t) {
  const saved = { window: globalThis.window, document: globalThis.document, addEventListener: globalThis.addEventListener };
  let visibility, pagehide, removed = false;
  globalThis.window = { Projection: { resource: async () => ({format_namespaces: []}), onVisibility(fn) { visibility = fn; return () => { removed = true; }; } } };
  globalThis.document = { getElementById: () => ({}) };
  globalThis.addEventListener = (name, fn) => { if (name === 'pagehide') pagehide = fn; };
  t.after(() => Object.assign(globalThis, saved));
  return { visible: value => visibility(value), dispose: () => pagehide(), removed: () => removed };
}

test('hidden regions defer their reads until selected, then reuse the mounted view', async t => {
  const env = regionEnvironment(t), events = [];
  const starting = start(() => { events.push('read'); return {
    show: () => events.push('show'), hide: () => events.push('hide'), destroy: () => events.push('destroy'),
  }; }, 'progress');
  env.visible(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(events, []);
  env.visible(true);
  const dispose = await starting;
  env.visible(true);
  assert.deepEqual(events, ['read'], 'first visibility must not immediately read again through show');
  env.visible(false); env.visible(false); env.visible(true);
  assert.deepEqual(events, ['read', 'hide', 'show']);
  dispose();
  assert.equal(env.removed(), true);
  assert.deepEqual(events, ['read', 'hide', 'show', 'destroy']);
});

test('hide during startup is applied to the late runtime without starting a second mount', async t => {
  const env = regionEnvironment(t), events = [];
  let complete;
  const starting = start(() => new Promise(resolve => { complete = resolve; }), 'architecture');
  env.visible(true);
  await new Promise(resolve => setImmediate(resolve));
  env.visible(false);
  complete({ hide: () => events.push('hide'), show: () => events.push('show') });
  await starting;
  assert.deepEqual(events, ['hide']);
  env.visible(true);
  assert.deepEqual(events, ['hide', 'show']);
});

test('closing a never selected region releases visibility without starting work', async t => {
  const env = regionEnvironment(t);
  let mounts = 0;
  const starting = start(() => { mounts++; return {}; }, 'evolution');
  env.visible(false); env.dispose(); env.visible(true);
  await starting;
  assert.equal(mounts, 0);
  assert.equal(env.removed(), true);
});

test('closing during mounting destroys the late runtime without showing it', async t => {
  const env = regionEnvironment(t), events = [];
  let complete;
  const starting = start(() => new Promise(resolve => { complete = resolve; }), 'architecture');
  env.visible(true);
  await new Promise(resolve => setImmediate(resolve));
  env.dispose();
  complete({ show: () => events.push('show'), destroy: () => events.push('destroy') });
  await starting;
  assert.deepEqual(events, ['destroy']);
  assert.equal(env.removed(), true);
});

test('visible startup mounts without reading configuration or enabling historical formats', async t => {
  const env = regionEnvironment(t), calls = [];
  window.Projection.resource = async () => { throw new Error('configuration must not be read'); };
  const starting = start(() => {
    assert.equal(schemaMatches('archive.portal-architecture.v1', 'tfi.portal-architecture.v1'), false);
    calls.push('mount');
    return {};
  }, 'architecture');
  env.visible(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, []);
  env.visible(true);
  await starting;
  env.visible(false); env.visible(true);
  assert.deepEqual(calls, ['mount']);
});

test('closing before the visible mount microtask does not mount the region', async t => {
  const env = regionEnvironment(t);
  let mounted = false;
  const starting = start(() => { mounted = true; return {}; }, 'architecture');
  env.visible(true);
  env.dispose();
  await starting;
  assert.equal(mounted, false);
  assert.equal(env.removed(), true);
});
