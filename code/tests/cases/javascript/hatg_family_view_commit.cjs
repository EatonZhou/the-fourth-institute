'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const test = require('node:test');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
function harness(resultField = 'facade_resident_id') {
  let release, fail, puts = 0, reveals = 0, requests = 0;
  const timers = new Map(); let timer = 0;
  const state = {lineage: {lineage_id: 'lineage_a'}};
  const toast = () => {};
  const context = vm.createContext({console: {warn() {}},
    window: {requestAnimationFrame: fn => fn(),
      setTimeout: fn => {timers.set(++timer, fn); return timer;}, clearTimeout: id => timers.delete(id),
      PortalI18n: {t: s => s}, PortalIdentity: {putUIAssociation: () => {
        puts++; return new Promise((resolve, reject) => {release = resolve; fail = reject;});
      }}},
  });
  // The personal view preference store and the edit that reveals a new Family
  // are the production layout and program edit modules'.
  vm.runInContext(readHatgModule('hatg_layout.js'), context);
  vm.runInContext(readHatgModule('hatg_program_edits.js'), context);
  const layout = createHatgModule(context.window.PortalHatgLayout, 'createLayout', {state, toast});
  context.scheduleHatgViewPreferenceSave = layout.scheduleHatgViewPreferenceSave;
  context.applyProgramMutation = createHatgModule(context.window.PortalHatgProgramEdits, 'createProgramEdits', {
    state, toast, separateGraphObject: () => Promise.resolve(null),
    renderProgramInspector() {}, renderLineageSurface() {},
    commitLineage(lineage) {state.lineage = lineage;},
    lineageEditQueue: {capture: id => id, isCurrent: id => id === state.lineage.lineage_id,
      enqueue: (_intent, operation) => operation()},
    programFeature: {beginMutation() {}, endMutation() {}},
    flushHatgViewPreferenceSave: layout.flushHatgViewPreferenceSave,
  }).applyProgramMutation;
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
