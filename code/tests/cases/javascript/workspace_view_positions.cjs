const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('management_portal/static/hatg_panel.js', 'utf8');
const section = (start, end) => source.slice(source.indexOf('  function ' + start), source.indexOf('  function ' + end));
const preference = source.slice(source.indexOf('  function normalizeHatgViewPreference'), source.indexOf('  async function loadHatgViewPreference'));
const saves = [], notices = [], timers = new Map();
let timerId = 0, releaseFirst;
const state = { lineage: { lineage_id: 'lineage_aaaaaaaaaaaa' } };
const context = vm.createContext({
  state, Number, Object, Array, Map, Set, Promise, Date,
  console: { warn() {} },
  toast: message => notices.push(message),
  window: {
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    PortalI18n: { t: s => s },
    PortalIdentity: {
      putUIAssociation(namespace, key, value) {
        saves.push({ namespace, key, value: JSON.parse(JSON.stringify(value)) });
        if (saves.length === 1) return new Promise(resolve => { releaseFirst = resolve; });
        if (key === 'lineage_failed') return Promise.reject(Error('isolated failure'));
        return Promise.resolve({});
      },
      listUIAssociations() {
        return Promise.resolve({items: saves.map(save => ({key: save.key, value: save.value}))});
      },
    },
  },
});
vm.runInContext(`const HATG_VIEW_UI_NAMESPACE = 'lineage.hatg-view';
  const hatgViewPreferences = new Map(), hatgViewSaveTimers = new Map(), hatgViewSaveRequests = new Map();
  ${preference}
  ${source.slice(source.indexOf('  async function loadHatgViewPreference'), source.indexOf('  function scheduleHatgViewPreferenceSave'))}
  ${section('scheduleHatgViewPreferenceSave', 'displayedProgram')}
  ${section('syncWorkspaceNodePosition', 'syncGraphNodePosition')}
  globalThis.api = {normalizeHatgViewPreference, syncWorkspaceNodePosition, scheduleHatgViewPreferenceSave, flushHatgViewPreferenceSave, loadHatgViewPreference, hatgViewPreferences, hatgViewSaveRequests};`, context);
const api = context.api;
const flushTimers = () => { const batch = [...timers.values()]; timers.clear(); batch.forEach(fn => fn()); };
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const normalized = api.normalizeHatgViewPreference({
    workspace_positions: {
      'workspace::workspace_abc': { x: -20, y: 220, extra: 'drop' },
      resident_abc: { x: 30, y: 40 },
      arbitrary: { x: 0, y: 0 },
      resident_invalid: { x: Infinity, y: 0 },
      resident_string: { x: '1', y: 0 },
    },
    viewport: { centerX: 1, centerY: 2, k: 1, updatedAt: 1 },
    expanded_family_facade_ids: ['resident_abc'],
  });
  assert.deepEqual(JSON.parse(JSON.stringify(normalized.workspace_positions)), {
    'workspace::workspace_abc': { x: -20, y: 220 }, resident_abc: { x: 30, y: 40 },
  });
  assert.equal(normalized.viewport.k, 1);
  assert.equal(normalized.expanded_family_facade_ids[0], 'resident_abc');
  api.hatgViewPreferences.set(state.lineage.lineage_id, normalized);
  assert.equal(api.syncWorkspaceNodePosition({id:'resident_abc',kind:'resident',x:99,y:99}), false);
  assert.equal(api.syncWorkspaceNodePosition({id:'workspace::workspace_abc',kind:'workspace',x:5,y:6}), true);
  api.scheduleHatgViewPreferenceSave(state.lineage.lineage_id); flushTimers(); await tick();
  assert.equal(saves.length, 1);
  api.syncWorkspaceNodePosition({id:'resident_abc',kind:'workspace_resident',x:7,y:8});
  api.scheduleHatgViewPreferenceSave(state.lineage.lineage_id, {viewport:{centerX:3,centerY:4,k:2}});
  flushTimers(); await tick(); assert.equal(saves.length, 1, 'Later save must wait for in-flight write');
  releaseFirst(); await tick(); await tick();
  assert.equal(saves.length, 2);
  assert.deepEqual(saves[1].value.workspace_positions.resident_abc, {x:7,y:8});
  assert.deepEqual(saves[1].value.workspace_positions['workspace::workspace_abc'], {x:5,y:6});
  assert.equal(saves[1].value.viewport.k,2);
  assert.equal(saves[1].value.expanded_family_facade_ids[0],'resident_abc');
  assert.equal(api.hatgViewSaveRequests.size,0);
  assert.deepEqual(JSON.parse(JSON.stringify(api.normalizeHatgViewPreference({}).workspace_positions)),{});
  api.scheduleHatgViewPreferenceSave('lineage_failed', {workspace_positions:{resident_abc:{x:9,y:10}}});
  flushTimers(); await tick(); await tick();
  assert.equal(notices.length,1);
  assert.equal(api.hatgViewPreferences.get('lineage_failed').workspace_positions.resident_abc.x,9);
  assert.equal(api.hatgViewPreferences.get(state.lineage.lineage_id).workspace_positions.resident_abc.x,7);
  api.scheduleHatgViewPreferenceSave('lineage_refresh', {viewport: {centerX: 7, centerY: 8, k: 1.45}});
  // Reload while the debounce is still pending must first commit this camera.
  const restored = await api.loadHatgViewPreference('lineage_refresh');
  assert.equal(restored.viewport.k, 1.45);
  assert.equal(restored.viewport.centerX, 7);
  assert.equal(timers.size, 0);
  console.log('Workspace preference normalization, Context exclusion, serialized writes, viewport preservation, failure retention and Lineage isolation passed.');
})().catch(error => { console.error(error); process.exitCode=1; });
