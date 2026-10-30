const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { babelParse, traverse } = require('./babel_parse.cjs');
const { STATIC, readHatgModule, createHatgModule } = require('./hatg_modules.cjs');
const source = fs.readFileSync(path.join(STATIC, 'hatg_panel.js'), 'utf8');
let callback;
traverse(babelParse(source, 'hatg_panel.js'), { ObjectProperty(p) {
  if (p.node.key.name === 'onConflict') callback = source.slice(p.node.value.start, p.node.value.end);
} });

for (const refreshed of [true, false]) test(`conflict preserves dirty form and ${refreshed ? 'rebases to refreshed revision' : 'requires a fresh read before retry'}`, () => {
  const form = { dataset: { programDraftDirty: 'true' }, title: 'corrected Filter', conditions: '[{"from":"packet.events.0.type"}]' };
  const revisions = new WeakMap([[form, 3]]);
  const panel = { querySelectorAll: () => [form] };
  const state = { lineage: { lineage_id: 'lineage_fixture', revision: 3 }, localVersion: 0 };
  let rendered = 0;
  const context = vm.createContext({ state, definitionRevisions: revisions, detachedRoots: new Set(),
    window: {PortalI18n: {t: text => text}},
    panelHost: { hatgComponentRoot: panel },
    $id: () => panel, $queryAll: () => [form],
    // The refreshed read enters the panel, and redraws it, through commitLineage.
    commitLineage(next) { state.lineage = { ...state.lineage, ...next }; rendered++; },
    programFeature: { setWireDraft() {} }, setSyncStatus() {},
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

// A port click first saves the open inspector draft; the click is the
// production program edit module's, with the inspector's save replaced.
for (const conflict of [true, false]) test(`port interaction ${conflict ? 'blocks a conflicted draft' : 'still saves an ordinary draft'}`, async () => {
  const form = {dataset: {programDraftDirty: 'true', programDraftConflict: String(conflict)}, isConnected: true};
  const filter = {id: 'filter_fixture', kind: 'filter', config: {}};
  const state = {lineage: {lineage_id: 'lineage_fixture', revision: 3}};
  let saves = 0, draws = 0;
  const tag = (strings, ...values) => strings.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  const context = vm.createContext({PortalI18n: {t: text => text, text: tag}});
  context.window = context;
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'lineage_program.js'), 'utf8'), context);
  vm.runInContext(readHatgModule('hatg_program_edits.js'), context);
  const {handleProgramPortClick} = createHatgModule(context.PortalHatgProgramEdits, 'createProgramEdits', {
    state, toast() {}, renderGraph: () => { draws += 1; },
    $id: id => (id === 'programInspectorPanel' ? {dataset: {componentId: filter.id}, querySelector: () => form} : null),
    programComponentById: id => (id === filter.id ? filter : null),
    displayGraphItemById: id => (id === filter.id ? filter : null),
    saveProgramComponentFromInspector: async () => {saves++; state.lineage.revision++;},
  });
  const output = context.PortalLineageProgram.portsForNode(filter).find(port => port.direction === 'out');
  await handleProgramPortClick(output);
  assert.equal(saves, conflict ? 0 : 1);
  assert.equal(state.programWireDraft?.port_id, conflict ? undefined : 'output',
    conflict ? 'a conflicted draft blocks the wire' : 'the wire starts from the saved component');
  assert.equal(draws, conflict ? 0 : 1);
});
