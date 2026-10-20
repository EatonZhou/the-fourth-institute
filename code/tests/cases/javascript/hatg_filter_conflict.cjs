const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { babelParse, traverse } = require('./babel_parse.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js'), 'utf8');
let callback, beforeWire;
traverse(babelParse(source, 'hatg_panel.js'), { ObjectProperty(p) {
  if (p.node.key.name === 'onConflict') callback = source.slice(p.node.value.start, p.node.value.end);
}, FunctionDeclaration(p) {
  if (p.node.id.name === 'saveOpenProgramComponentDraftBeforeWire') beforeWire = source.slice(p.node.start, p.node.end);
} });

for (const refreshed of [true, false]) test(`conflict preserves dirty form and ${refreshed ? 'rebases to refreshed revision' : 'requires a fresh read before retry'}`, () => {
  const form = { dataset: { programDraftDirty: 'true' }, title: 'corrected Filter', conditions: '[{"from":"packet.events.0.type"}]' };
  const revisions = new WeakMap([[form, 3]]);
  const panel = { querySelectorAll: () => [form] };
  const state = { lineage: { lineage_id: 'lineage_fixture', revision: 3 }, localVersion: 0 };
  let rendered = 0;
  const context = vm.createContext({ state, definitionRevisions: revisions, detachedRoots: new Set(),
    window: {PortalI18n: {t: text => text}},
    hatgComponentRoot: panel,
    $id: () => panel, $queryAll: () => [form],
    mergeLineageStateDomains: (old, next) => ({ ...old, ...next }),
    programFeature: { setWireDraft() {} }, updateLineageSummary() {}, setSyncStatus() {},
    renderLineageSurface() { rendered++; },
  });
  const onConflict = vm.runInContext(`(${callback})`, context);
  const error = new Error('version conflict');
  onConflict({ lineageId: 'lineage_fixture', lineage: refreshed ? { lineage_id: 'lineage_fixture', revision: 4 } : undefined,
    error });
  assert.equal(form.dataset.programDraftDirty, 'true');
  assert.equal(form.dataset.programDraftConflict, 'true');
  assert.equal(revisions.get(form), refreshed ? 4 : undefined);
  assert.equal(form.title, 'corrected Filter');
  assert.equal(form.conditions, '[{"from":"packet.events.0.type"}]');
  assert.equal(rendered, refreshed ? 1 : 0);
  assert.match(error.message, /草稿已保留/);
  assert.match(error.message, refreshed ? /核对后再次点击保存/ : /先重读，再尝试保存/);
});

for (const conflict of [true, false]) test(`port interaction ${conflict ? 'blocks a conflicted draft' : 'still saves an ordinary draft'}`, async () => {
  const form = {dataset: {programDraftDirty: 'true', programDraftConflict: String(conflict)}, isConnected: true};
  const state = {lineage: {revision: 3}};
  let saves = 0;
  const context = vm.createContext({state, toast() {}, window: {PortalI18n: {t: text => text}},
    $id: () => ({dataset: {componentId: 'filter_fixture'}, querySelector: () => form}),
    programComponentById: () => ({id: 'filter_fixture'}),
    saveProgramComponentFromInspector: async () => {saves++; state.lineage.revision++;},
  });
  const result = await vm.runInContext(`(${beforeWire})()`, context);
  assert.equal(result, conflict ? 'blocked' : 'saved');
  assert.equal(saves, conflict ? 0 : 1);
});
