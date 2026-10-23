const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { babelParse, traverse } = require('./babel_parse.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const panelPath = path.join(staticRoot, 'hatg_panel.js');
const panelSource = fs.readFileSync(panelPath, 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

// Load the real Lineage entry points, the merge and the canvas projection with
// their lexical dependencies from the HATG runtime. Only the page effects a
// commit dispatches are recorded instead of drawn.
const BOUNDARIES = {
  renderLineageSurface: 'function renderLineageSurface() { window.__draws.push("surface"); }',
  renderGraphRuntimePresentation: 'function renderGraphRuntimePresentation() { window.__draws.push("runtime"); }',
  renderGraph: 'function renderGraph() { window.__draws.push("graph"); }',
  renderProgramInspector: 'function renderProgramInspector() {}',
  renderControls: 'function renderControls() {}',
  updateLineageSummary: 'function updateLineageSummary() {}',
  setSyncStatus: 'function setSyncStatus() {}',
  toast: 'function toast(message) { window.__toasts.push(message); }',
  bindResidentStateCollection: `function bindResidentStateCollection() {
    window.__bindings.push((state.lineage?.nodes || []).map(node => node.id));
  }`,
  reconcileGraphInteractionState: 'function reconcileGraphInteractionState() {}',
  templateController: 'const templateController = { render() {} };',
  setBusy: 'function setBusy() {}',
  syncManualProgramEventInspectorBusy: 'function syncManualProgramEventInspectorBusy() {}',
};

function runtimeSource(entries) {
  let runtime;
  traverse(babelParse(panelSource, panelPath), { FunctionDeclaration(p) {
    if (p.node.id.name === 'createHatgRuntime') runtime = p;
  } });
  assert(runtime, 'Production HATG runtime must exist');
  const bindings = runtime.scope.bindings;
  const selected = new Map();
  function include(name) {
    if (selected.has(name)) return;
    const binding = bindings[name];
    assert(binding, `Missing runtime dependency: ${name}`);
    selected.set(name, binding.path);
    if (Object.hasOwn(BOUNDARIES, name)) return;
    binding.path.traverse({ ReferencedIdentifier(reference) {
      const dependency = reference.scope.getBinding(reference.node.name);
      if (dependency?.scope === runtime.scope) include(reference.node.name);
    } });
  }
  entries.forEach(include);
  const source = [...selected].sort(([, a], [, b]) => a.node.start - b.node.start)
    .map(([name, p]) => {
      if (Object.hasOwn(BOUNDARIES, name)) return BOUNDARIES[name];
      const bytes = panelSource.slice(p.node.start, p.node.end);
      return p.isVariableDeclarator() ? `${p.parent.kind} ${bytes};` : bytes;
    }).join('\n');
  return `(() => { 'use strict'; ${source}
    window.__hatg = { state, ${entries.join(', ')} };
  })();`;
}

function resident(id, extra = {}) {
  return { id, kind: 'resident', title: id, status: 'idle', x: 20, y: 20, ...extra };
}

function fixture(entries = []) {
  const draws = [], toasts = [], bindings = [];
  const context = vm.createContext({
    document: { querySelector: () => null, getElementById: () => null },
    CSS: { escape: value => value }, localStorage: { getItem: () => null },
    window: {
      PortalI18n: {
        t: value => value, definition: value => value,
        text: (strings, ...values) => strings.reduce((result, part, i) => result + part + (values[i] ?? ''), ''),
      },
      PortalUI: { toast() {} },
      PortalAPI: { get: () => { throw new Error('Unexpected network read'); } },
      PortalInstructionClient: { execute: () => { throw new Error('Unexpected instruction'); } },
      PortalUIPrimitives: { randomUUID: () => 'commit-key' },
      requestAnimationFrame: callback => callback(),
      __draws: draws, __toasts: toasts, __bindings: bindings,
    },
  });
  Object.assign(context, context.window);
  context.window = context;
  for (const file of [
    'portal_state.js', 'hatg_panel_features.js', 'hatg_panel_state.js',
    'projection_libraries/portal-graph/1.0.0/geometry.js', 'lineage_graph.js',
    'lineage_program.js', 'portal_society_service.js',
  ]) {
    const source = fs.readFileSync(path.join(staticRoot, file), 'utf8');
    const program = file === 'portal_state.js' ? babelParse(source, file).program.body[0] : null;
    vm.runInContext(program ? source.slice(program.start, program.end) : source, context, { filename: file });
  }
  vm.runInContext(runtimeSource(['applyLineageEventProjection', 'graphVisibleModel', 'commitLineage', ...entries]), context,
    { filename: panelPath });
  const hatg = context.window.__hatg;
  hatg.state.lineage = {
    lineage_id: 'lineage_aaaaaaaaaaaa', revision: 4, layout_revision: 1, runtime_revision: 2,
    refresh_signature: 'sig-4',
    nodes: [
      resident('resident_aaaaaaaaaaaa', { last_execution: { execution_id: 'run_one', status: 'running' } }),
      resident('resident_bbbbbbbbbbbb', { x: 60 }),
    ],
    edges: [], function_blocks: [], program: { components: [], wires: [] },
  };
  return { ...hatg, draws, toasts, bindings, context };
}

const read = (f, change) => {
  const lineage = plain(f.state.lineage);
  change(lineage);
  return lineage;
};
const shown = (f, id) => f.graphVisibleModel().nodes.find(node => node.id === id);

test('手动 Event 成功提交只显示真实投递结果，不把成功误报为失败或重复提交', async () => {
  for (const delivery of [
    { matched_count: 1, results: [{ status: 'completed' }] },
    { matched_count: 1, results: [{ status: 'queued' }] },
    { matched_count: 1, results: [{ status: 'dropped' }] },
    { matched_count: 0, results: [] },
  ]) {
    const f = fixture(['emitManualProgramEvent']);
    f.state.lineage.program.components.push({
      id: 'event_source', kind: 'event_source', config: { event_type: 'manual.triggered' },
    });
    let calls = 0;
    f.context.PortalUI.prompt = async () => 'one input';
    f.context.PortalInstructionClient.execute = async name => {
      if (name === 'TriggerEvent') calls += 1;
      else assert.equal(name, 'GetLineage');
      return { lineage: read(f, lineage => {
        lineage.revision += 1;
        lineage.nodes.push(resident('resident_cccccccccccc'));
      }), delivery };
    };
    await f.emitManualProgramEvent('event_source');
    assert.equal(calls, 1);
    assert.ok(shown(f, 'resident_cccccccccccc'));
    assert.ok(f.toasts.at(-1).startsWith('Event 已'), f.toasts.at(-1));
    assert.ok(!f.toasts.some(value => value.includes('触发失败')), f.toasts.join('\n'));
  }
});

test('同一版本读到的新执行结果会更新画布节点，不沿用此前的投影缓存', () => {
  const f = fixture();
  assert.equal(shown(f, 'resident_aaaaaaaaaaaa').last_execution.status, 'running');
  f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', read(f, lineage => {
    lineage.nodes[0].last_execution = { execution_id: 'run_one', status: 'failed', error: 'timed out' };
  }));
  assert.equal(f.state.lineage.revision, 4, 'the authored graph is unchanged');
  assert.equal(shown(f, 'resident_aaaaaaaaaaaa').last_execution.status, 'failed');
});

test('后台读取只在内容变化时写入，并只重画变化的范围', () => {
  const f = fixture();
  const version = f.state.localVersion;
  f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', read(f, () => {}));
  assert.deepEqual(f.draws, [], 'an equal read changes and draws nothing');
  assert.equal(f.state.localVersion, version);
  f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', read(f, lineage => {
    lineage.runtime_revision = 3;
    lineage.nodes[1].status = 'done';
  }));
  assert.deepEqual(f.draws, ['runtime'], 'a runtime change redraws the runtime presentation');
  assert.equal(shown(f, 'resident_bbbbbbbbbbbb').status, 'done');
  f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', read(f, lineage => {
    lineage.revision = 5;
    lineage.refresh_signature = 'sig-5';
    lineage.nodes[1].title = 'Renamed';
  }));
  assert.deepEqual(f.draws, ['runtime', 'surface'], 'a graph change redraws the surface');
  assert.equal(shown(f, 'resident_bbbbbbbbbbbb').title, 'Renamed');
});

test('后台读取让位于进行中的修改和布局保存，修改结果照常写入', () => {
  const f = fixture();
  const removed = read(f, lineage => {
    lineage.revision = 5;
    lineage.nodes.pop();
  });
  for (const flag of ['structureMutationInFlight', 'layoutSaveInFlight']) {
    f.state[flag] = flag === 'layoutSaveInFlight' ? Promise.resolve() : true;
    f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', removed);
    assert.equal(f.state.lineage.nodes.length, 2, `a background read waits for ${flag}`);
    f.state[flag] = flag === 'layoutSaveInFlight' ? null : false;
  }
  f.state.structureMutationInFlight = true;
  assert.equal(f.commitLineage(removed).changed, true, 'the edit result itself applies');
  assert.equal(f.state.lineage.nodes.length, 1);
  assert.deepEqual(f.draws, ['surface']);
});

test('任何来源的成员变化都会重新订阅 Resident 状态，并清掉已删除成员的页面记录', () => {
  const f = fixture();
  for (const map of ['residentRuntimeById', 'sessionUserMessageCounts', 'sessionContextPressures']) {
    f.state[map].set('resident_bbbbbbbbbbbb', {});
  }
  f.state.runtimeStatus.set('resident_bbbbbbbbbbbb', 'running');
  f.commitLineage(read(f, lineage => { lineage.nodes[0].title = 'Renamed only'; }));
  assert.deepEqual(f.bindings, [], 'the same members keep their subscription');
  f.commitLineage(read(f, lineage => {
    lineage.revision = 5;
    lineage.nodes.pop();
  }));
  assert.deepEqual(plain(f.bindings), [['resident_aaaaaaaaaaaa']], 'a deleted member leaves the subscription');
  for (const map of ['runtimeStatus', 'residentRuntimeById', 'sessionUserMessageCounts', 'sessionContextPressures']) {
    assert.equal(f.state[map].has('resident_bbbbbbbbbbbb'), false, `${map} forgets the deleted member`);
  }
  assert.equal(shown(f, 'resident_bbbbbbbbbbbb'), undefined);
  f.applyLineageEventProjection('lineage_aaaaaaaaaaaa', read(f, lineage => {
    lineage.revision = 6;
    lineage.nodes.push(resident('resident_cccccccccccc'));
  }));
  assert.deepEqual(plain(f.bindings.at(-1)), ['resident_aaaaaaaaaaaa', 'resident_cccccccccccc'],
    'a member created elsewhere joins the subscription');
  assert.ok(f.toasts.some(message => message.includes('新增 1 个节点')));
});

test('回滚本地修改时原样装回保存的 Lineage 并重画', () => {
  const f = fixture();
  const saved = plain(f.state.lineage);
  f.state.lineage.nodes[0].x = 90;
  f.state.localVersion += 1;
  assert.equal(shown(f, 'resident_aaaaaaaaaaaa').x, 90);
  f.commitLineage(saved, { replace: true });
  assert.equal(f.state.lineage, saved);
  assert.equal(shown(f, 'resident_aaaaaaaaaaaa').x, 20);
  assert.deepEqual(f.draws, ['surface']);
});
