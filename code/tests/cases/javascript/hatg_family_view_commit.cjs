'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const {babelParse, traverse} = require('./babel_parse.cjs');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js'), 'utf8');
const functions = [];
traverse(babelParse(source), {FunctionDeclaration(p) {
  if (p.node.id?.name === 'applyProgramMutation') functions.push(source.slice(p.node.start, p.node.end));
}});
assert.equal(functions.length, 1);
function harness(resultField = 'facade_resident_id') {
  let release, fail, puts = 0, reveals = 0, requests = 0;
  const timers = new Map(); let timer = 0;
  const state = {lineage: {lineage_id: 'lineage_a'}};
  const toast = () => {};
  const context = vm.createContext({state,
    separateGraphObject: () => Promise.resolve(null),
    console: {warn() {}}, toast, renderProgramInspector() {}, renderLineageSurface() {},
    commitLineage(lineage) {state.lineage = lineage;},
    lineageEditQueue: {capture: id => id, isCurrent: id => id === state.lineage.lineage_id,
      enqueue: (_intent, operation) => operation()},
    programFeature: {beginMutation() {}, endMutation() {}},
    window: {requestAnimationFrame: fn => fn(),
      setTimeout: fn => {timers.set(++timer, fn); return timer;}, clearTimeout: id => timers.delete(id),
      PortalI18n: {t: s => s}, PortalIdentity: {putUIAssociation: () => {
        puts++; return new Promise((resolve, reject) => {release = resolve; fail = reject;});
      }}},
  });
  vm.runInContext(functions.join('\n'), context);
  // The personal view preference store is the production layout module's.
  vm.runInContext(readHatgModule('hatg_layout.js'), context);
  const layout = createHatgModule(context.window.PortalHatgLayout, 'createLayout', {state, toast});
  context.scheduleHatgViewPreferenceSave = layout.scheduleHatgViewPreferenceSave;
  context.flushHatgViewPreferenceSave = layout.flushHatgViewPreferenceSave;
  state.graphController = {ensureNodeVisible(id, options) {
    reveals++; assert.equal(id, 'resident_outer'); assert.equal(options.persist, true);
    context.scheduleHatgViewPreferenceSave('lineage_a', {viewport: {centerX: 5, centerY: 6, k: 1.45}});
  }};
  return {context, timers, release: () => release({}), fail: () => fail(Error('save unavailable')),
    counts: () => ({puts, reveals, requests}),
    apply: () => context.applyProgramMutation(async () => {
      requests++; return {lineage: {...state.lineage}, [resultField]: 'resident_outer'};
    })};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
test('Family application resolves only after its revealed camera is saved', async () => {
  const h = harness(); let done = false;
  const pending = h.apply().then(result => {done = true; return result;});
  await tick();
  assert.deepEqual(h.counts(), {puts: 1, reveals: 1, requests: 1});
  assert.equal(h.timers.size, 0, 'structural completion flushes the debounce');
  assert.equal(done, false, 'the creation form stays busy through persistence');
  h.release(); assert.equal((await pending).facade_resident_id, 'resident_outer');
});
test('preference failure cannot turn a committed Family into a retryable creation', async () => {
  const h = harness(); const pending = h.apply(); await tick(); h.fail();
  assert.equal((await pending).facade_resident_id, 'resident_outer');
  assert.equal(h.counts().requests, 1);
});
test('Functionalize reveals the replacement block and persists its view before completion', async () => {
  const h = harness('function_block_id'); let done = false;
  const pending = h.apply().then(result => {done = true; return result;});
  await tick();
  assert.deepEqual(h.counts(), {puts: 1, reveals: 1, requests: 1});
  assert.equal(done, false);
  h.release();
  assert.equal((await pending).function_block_id, 'resident_outer');
});
test('a committed Function Block is not resubmitted when saving its view fails', async () => {
  const h = harness('function_block_id'); const pending = h.apply();
  await tick(); h.fail();
  assert.equal((await pending).function_block_id, 'resident_outer');
  assert.equal(h.counts().requests, 1);
});
