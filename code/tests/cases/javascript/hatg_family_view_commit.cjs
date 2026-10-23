'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const {babelParse, traverse} = require('./babel_parse.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js'), 'utf8');
const names = new Set(['normalizeHatgViewPreference', 'scheduleHatgViewPreferenceSave',
  'flushHatgViewPreferenceSave', 'applyProgramMutation']);
const functions = [];
traverse(babelParse(source), {FunctionDeclaration(p) {
  if (names.has(p.node.id?.name)) functions.push(source.slice(p.node.start, p.node.end));
}});
assert.equal(functions.length, names.size);
function harness() {
  let release, fail, puts = 0, reveals = 0, requests = 0;
  const timers = new Map(); let timer = 0;
  const state = {lineage: {lineage_id: 'lineage_a'}};
  const context = vm.createContext({state, HATG_VIEW_UI_NAMESPACE: 'lineage.hatg-view',
    hatgViewPreferences: new Map(), hatgViewSaveTimers: new Map(), hatgViewSaveRequests: new Map(),
    console: {warn() {}}, toast() {}, renderProgramInspector() {}, renderLineageSurface() {},
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
  state.graphController = {ensureNodeVisible(id, options) {
    reveals++; assert.equal(id, 'resident_outer'); assert.equal(options.persist, true);
    context.scheduleHatgViewPreferenceSave('lineage_a', {viewport: {centerX: 5, centerY: 6, k: 1.45}});
  }};
  return {context, timers, release: () => release({}), fail: () => fail(Error('save unavailable')),
    counts: () => ({puts, reveals, requests}),
    apply: () => context.applyProgramMutation(async () => {
      requests++; return {lineage: {...state.lineage}, facade_resident_id: 'resident_outer'};
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
