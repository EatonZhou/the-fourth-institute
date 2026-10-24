const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const html = fs.readFileSync(path.resolve(STATIC, '../hatg-panel.html'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const LINEAGE = 'lineage_one';

// A Writer and a Reviewer. The Writer has its own Workspace and a shared
// directory; only Workspace roots with an absolute path can be handed over.
const BINDINGS = {
  resident_a: {roots: [
    {root_kind: 'workspace', workspace_id: 'dir_a', path: '/work/a', label: 'Writer WS', is_default_cwd: true},
    {root_kind: 'workspace', workspace_id: 'dir_notes', path: '/work/notes', label: 'Notes'},
    {root_kind: 'shared', workspace_id: 'dir_shared', path: '/share', label: 'Shared'},
  ]},
  resident_b: {roots: [{root_kind: 'workspace', workspace_id: 'dir_b', path: '/work/b', label: 'Reviewer WS'}]},
};

function lineage({edges = [], nodes} = {}) {
  return {
    lineage_id: LINEAGE, revision: 7,
    nodes: nodes || [{id: 'resident_a', title: 'Writer'}, {id: 'resident_b', title: 'Reviewer'}],
    edges, program: {components: [], wires: []},
  };
}

function handoffEdge(status, extra = {}) {
  return {
    id: 'handoff_old', type: 'handoff', source: 'resident_a', target: 'resident_b', status,
    label: 'Draft review', explanation: 'Read the draft',
    items: [{
      item_id: 'workspace', label: 'Writer WS',
      source: {workspace_id: 'dir_a', path: '/work/a', scope: '.'},
      transfer: {mode: 'copy', method: 'filesystem_copy', parameters: {branch: '', base_commit: '', include_worktree_changes: false, exclude: []}},
      target: {workspace_id: '', path: '/copies/b', scope: '.', label: 'Reviewer copy'},
      attempts: status === 'draft' ? [] : [{status}], result: status === 'ready' ? {target_path: '/copies/b', file_count: 3, byte_size: 2048, content_hash: 'sha256:abc'} : {},
    }],
    ...extra,
  };
}

// The HATG ISA as the page sees it: GetLineage and the Workspace Handoff
// instructions with the result shapes their server handlers return.
function isaServer(initial) {
  const server = {lineage: plain(initial), calls: [], prepare: 'ready', fail: {}};
  const edge = id => server.lineage.edges.find(item => item.id === id);
  server.execute = async (name, args, controls = {}) => {
    server.calls.push({name, args: plain(args), expectedVersion: controls.expectedVersion, key: controls.idempotencyKey});
    if (server.fail[name]) throw server.fail[name];
    if (name === 'GetLineage') return {lineage: plain(server.lineage)};
    server.lineage.revision += 1;
    if (name === 'CreateWorkspaceHandoff') {
      server.lineage.edges.push({id: 'handoff_new', type: 'handoff', source: args.source_resident_id,
        target: args.target_resident_id, status: 'draft', label: args.label, explanation: args.explanation, items: plain(args.items)});
      return {handoff_id: 'handoff_new', created: true};
    }
    if (name === 'UpdateWorkspaceHandoff') {
      Object.assign(edge(args.handoff_id), plain(args.changes));
      return {handoff: plain(edge(args.handoff_id))};
    }
    if (name === 'PrepareWorkspaceHandoff') {
      const prepared = edge(args.handoff_id);
      prepared.status = server.prepare;
      prepared.error = server.prepare === 'failed' ? 'disk full' : '';
      return {handoff_id: prepared.id, status: server.prepare, items: plain(prepared.items), error: prepared.error};
    }
    if (name === 'DeleteWorkspaceHandoff') {
      server.lineage.edges = server.lineage.edges.filter(item => item.id !== args.handoff_id);
      return {handoff_id: args.handoff_id};
    }
    throw new Error(`unexpected instruction ${name}`);
  };
  server.writes = () => server.calls.filter(call => call.name !== 'GetLineage').map(call => call.name);
  return server;
}

// The production relations over the panel's own markup, with the production
// graph lookups, Workspace binding reads and Society client. The ISA, the
// Resident Workspace service, the dialogs, toasts and redraws are replaced.
function harness(initial = lineage()) {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, {runScripts: 'outside-only', pretendToBeVisual: true});
  const {window} = dom;
  for (const name of ['portal_text.js', 'portal_ui_primitives.js', 'portal_society_service.js', 'hatg_workspace_projection.js']) {
    window.eval(fs.readFileSync(path.join(STATIC, name), 'utf8'));
  }
  for (const name of ['hatg_graph_model.js', 'hatg_workspace_results.js', 'hatg_relations.js']) window.eval(readHatgModule(name));
  const server = isaServer(initial);
  const h = {window, server, toasts: [], draws: [], modals: new Set(), confirms: [], confirmAnswer: true,
    selected: [], seeds: [], modes: [], candidateForks: [], locations: [], commits: [], workspaceReads: [],
    selectionPatched: true, unreadable: new Set(), pendingReads: null};
  const state = h.state = {
    lineage: plain(initial), lineages: [{lineage_id: LINEAGE}], localVersion: 0,
    handoffMode: false, handoffSourceId: '', handoffTargetId: '', handoffPreset: 'direct',
    editingEdgeId: '', selectedEdgeId: '', selectedNodeId: '', selectedSeedId: '', selectedProgramObjectId: '',
    graphRelationFilter: 'context', graphViewMode: 'context', programMutationInFlight: false,
    workspaceGraphBindings: new Map(), workspaceGraphLoadSeq: 0, runtimeStatus: new Map(),
  };
  window.PortalResidentService = {workspaces: async id => {
    h.workspaceReads.push(id);
    if (h.pendingReads) await h.pendingReads;
    if (h.unreadable.has(id)) throw new Error('denied');
    return plain(BINDINGS[id] || {roots: []});
  }};
  window.PortalUI = {confirm: async options => { h.confirms.push(options.title); return h.confirmAnswer; }};
  const draw = name => () => { h.draws.push(name); };
  const esc = window.PortalUIPrimitives.escapeHtml;
  const model = window.PortalHatgGraphModel.createGraphModel({esc, state});
  const results = createHatgModule(window.PortalHatgWorkspaceResults, 'createWorkspaceResults', {
    state, renderGraph: draw('graph'),
  });
  let ids = 0;
  h.relations = createHatgModule(window.PortalHatgRelations, 'createRelations', {
    state, esc, $id: id => window.document.getElementById(id),
    clone: plain, compactText: value => String(value ?? ''), formatBytes: value => `${value} B`, uid: prefix => `${prefix}_${++ids}`,
    nodeById: model.nodeById, displayNodeById: model.displayNodeById, edgeById: model.edgeById,
    displayEdgeById: model.displayEdgeById, nodeResidentId: model.nodeResidentId, seedById: model.seedById,
    fillWorkspaceSelect: model.fillWorkspaceSelect, selectedBoundWorkspace: model.selectedBoundWorkspace,
    RELATION_LABEL: model.RELATION_LABEL, relationFamily: model.relationFamily,
    loadWorkspaceGraphBindings: results.loadWorkspaceGraphBindings,
    societyService: window.PortalSocietyService.createClient({execute: server.execute, getRevision: () => state.lineage?.revision}),
    commitLineage: (next, options = {}) => { h.commits.push(plain(options)); state.lineage = next; state.localVersion += 1; },
    openModal: id => h.modals.add(id), closeModal: id => h.modals.delete(id),
    setBusy: (button, busy) => { if (button) button.dataset.busy = String(busy); },
    toast: (message, tone) => h.toasts.push([tone, message]),
    programFeature: {clearSelection: draw('program-selection-cleared')},
    publishLocation: id => h.locations.push(id),
    renderControls: draw('controls'), renderGraph: draw('graph'), renderGraphModeHint: draw('mode-hint'),
    renderLineageSurface: draw('surface'), renderProgramInspector: draw('program-inspector'),
    renderSeedInspector: draw('seed-inspector'),
    renderGraphSelection: () => { h.draws.push('selection'); return h.selectionPatched; },
    seedTypeLabel: seed => seed.seed_type || 'Seed',
    selectNode: async (id, options) => h.selected.push([id, plain(options)]),
    selectSeed: async id => h.seeds.push(id),
    setGraphViewMode: async mode => { state.graphViewMode = mode; h.modes.push(mode); },
    setSeedCandidateFork: async (seed, source) => h.candidateForks.push([seed, source]),
  });
  h.$ = id => window.document.getElementById(id);
  h.cards = () => [...h.$('handoffItemsEditor').querySelectorAll('[data-handoff-item]')];
  h.field = (card, name) => card.querySelector(`[data-handoff-field="${name}"]`);
  h.submit = () => h.relations.submitHandoff({preventDefault() {}});
  h.until = async (condition, message) => {
    for (let index = 0; index < 50 && !condition(); index += 1) await new Promise(resolve => setTimeout(resolve, 0));
    assert(condition(), message);
  };
  return h;
}

async function openNewHandoff(h) {
  h.relations.activateHandoffMode();
  await h.relations.handleHandoffNodeClick('resident_a');
  await h.relations.handleHandoffNodeClick('resident_b');
  assert(h.modals.has('handoffModal'), 'the Handoff dialog opened');
}

test('handoff mode needs two Residents and starts without a source, target or selected relation', () => {
  const single = harness(lineage({nodes: [{id: 'resident_a', title: 'Writer'}]}));
  single.relations.activateHandoffMode();
  assert.equal(single.state.handoffMode, false);
  const h = harness();
  Object.assign(h.state, {selectedEdgeId: 'edge_x', handoffSourceId: 'resident_b'});
  h.relations.activateHandoffMode();
  assert.deepEqual([h.state.handoffMode, h.state.handoffSourceId, h.state.selectedEdgeId, h.state.graphRelationFilter],
    [true, '', '', 'all']);
  assert.deepEqual(h.draws, ['mode-hint', 'surface']);
  h.state.handoffSourceId = 'resident_a';
  h.relations.cancelHandoffMode();
  assert.deepEqual([h.state.handoffMode, h.state.handoffSourceId, h.state.handoffTargetId], [false, '', '']);
});

test("picking a source and a target opens a new Handoff over the source's Workspaces", async () => {
  const h = harness();
  h.relations.activateHandoffMode();
  await h.relations.handleHandoffNodeClick('resident_a');
  assert.deepEqual([h.state.handoffSourceId, h.state.selectedNodeId], ['resident_a', 'resident_a']);
  await h.relations.handleHandoffNodeClick('resident_a');
  assert.deepEqual(h.toasts.at(-1), ['warning', '交接目标不能与来源节点相同。']);
  await h.relations.handleHandoffNodeClick('resident_b');
  assert.deepEqual(h.workspaceReads.sort(), ['resident_a', 'resident_b'], 'both bindings are read once');
  assert(h.modals.has('handoffModal'));
  assert.equal(h.$('handoffModalTitle').textContent, '建立 Workspace Handoff');
  assert.match(h.$('handoffRoute').textContent, /Writer.*→.*Reviewer/s);
  const [card] = h.cards();
  assert.deepEqual([...h.field(card, 'source-workspace').options].map(option => option.value), ['dir_a', 'dir_notes'],
    'only the Workspace roots of the source can be handed over');
  assert.equal(h.field(card, 'source-workspace').value, 'dir_a', 'the default cwd is preselected');
  assert.deepEqual([h.$('handoffItemsCount').textContent, h.$('handoffSendNowInput').checked, h.$('removeEdgeBtn').hidden,
    h.$('handoffSubmitBtn').textContent], ['1 / 24', true, true, '保存并准备 Workspace']);
});

test('an unreadable Workspace binding or a cancelled pick does not open the dialog', async () => {
  const h = harness();
  h.unreadable.add('resident_b');
  await openNewHandoff(h).catch(() => {});
  assert.equal(h.modals.size, 0);
  assert.equal(h.state.handoffTargetId, '');
  assert.deepEqual(h.toasts.at(-1), ['error', '无法配置 Handoff：部分 Agent 的 Workspace Binding 尚未读取完成']);
  // The pick would open the existing Handoff between the two; leaving handoff
  // mode while its bindings are read drops it.
  const late = harness(lineage({edges: [handoffEdge('ready')]}));
  let release;
  late.pendingReads = new Promise(resolve => { release = resolve; });
  late.relations.activateHandoffMode();
  await late.relations.handleHandoffNodeClick('resident_a');
  const pick = late.relations.handleHandoffNodeClick('resident_b');
  late.relations.cancelHandoffMode();
  release();
  await pick;
  assert.deepEqual([late.modals.size, late.state.editingEdgeId], [0, ''], 'a pick that finishes after leaving handoff mode is dropped');
});

test('the editor shows the fields of each transfer method and keeps edits as items are added and removed', async () => {
  const h = harness();
  await openNewHandoff(h);
  let [first] = h.cards();
  assert.deepEqual([h.field(first, 'copy-fields').hidden, h.field(first, 'git-fields').hidden], [true, true]);
  h.field(first, 'materializer').value = 'git_worktree';
  h.relations.syncHandoffMaterializerFields();
  assert.deepEqual([h.field(first, 'copy-fields').hidden, h.field(first, 'git-fields').hidden], [false, false]);
  assert.match(h.$('directHandoffWorkspaceText').textContent, /worktree/);
  h.field(first, 'git-branch').value = 'review/1';
  h.relations.addHandoffItem();
  [first] = h.cards();
  assert.equal(h.cards().length, 2);
  assert.deepEqual([h.field(first, 'materializer').value, h.field(first, 'git-branch').value], ['git_worktree', 'review/1'],
    'adding an item keeps what was typed');
  assert.match(h.$('directHandoffWorkspaceText').textContent, /各交接项会按自己的方式准备/);
  assert.equal(h.$('handoffItemsCount').textContent, '2 / 24');
  h.relations.removeHandoffItem(first.querySelector('[data-remove-handoff-item]'));
  assert.equal(h.cards().length, 1);
  assert.equal(h.field(h.cards()[0], 'materializer').value, 'direct', 'the removed item was the first one');
  h.field(h.cards()[0], 'source-scope').value = 'kept';
  h.relations.removeHandoffItem(h.cards()[0].querySelector('[data-remove-handoff-item]'));
  assert.deepEqual([h.cards().length, h.field(h.cards()[0], 'source-scope').value], [1, 'kept'], 'the last item stays as edited');
  while (h.cards().length < 24) h.relations.addHandoffItem();
  assert.deepEqual([h.$('handoffAddItemBtn').disabled, h.$('handoffAddItemBtn').title], [true, '每个 Handoff 最多 24 个交接项']);
  h.relations.addHandoffItem();
  assert.equal(h.cards().length, 24);
  assert.deepEqual(h.toasts.at(-1), ['warning', '每个 Handoff 最多可以包含 24 个交接项。']);
});

test('invalid items and a missing permission are reported before any instruction is sent', async () => {
  const cases = [
    [card => { card.querySelector('[data-handoff-field="source-workspace"]').value = ''; }, '交接项 1：请选择来源节点真实绑定的 Workspace。'],
    [(card, h) => { h.field(card, 'materializer').value = 'git_worktree'; h.field(card, 'source-scope').value = 'src'; h.field(card, 'git-branch').value = 'b'; },
      '交接项 1：Git worktree 只能交接整个 Workspace，请将范围设为 .。'],
    [(card, h) => { h.field(card, 'materializer').value = 'git_worktree'; }, '交接项 1：Git worktree 需要填写目标分支。'],
    [(card, h) => { h.field(card, 'materializer').value = 'filesystem_copy'; h.field(card, 'target-path').value = 'copies/b'; },
      '交接项 1：目标 Workspace 必须是绝对路径。'],
  ];
  for (const [edit, message] of cases) {
    const h = harness();
    await openNewHandoff(h);
    edit(h.cards()[0], h);
    await h.submit();
    assert.deepEqual(h.toasts.at(-1), ['warning', message]);
    assert.deepEqual(h.server.writes(), []);
    assert(h.modals.has('handoffModal'), 'the dialog stays open for correction');
  }
  const denied = harness();
  await openNewHandoff(denied);
  denied.state.lineages[0].allows = {CreateWorkspaceHandoff: false};
  await denied.submit();
  assert.deepEqual(denied.toasts.at(-1), ['warning', '你没有在这个 Lineage 中创建 Handoff 的权限。']);
  assert.deepEqual(denied.server.calls, []);
});

test('saving and preparing a new Handoff sends the edited items and reports the prepared Workspace', async () => {
  const h = harness();
  await openNewHandoff(h);
  h.field(h.cards()[0], 'source-scope').value = 'docs';
  h.$('handoffExplanationInput').value = '  Review the docs  ';
  await h.submit();
  assert.deepEqual(h.server.calls.map(call => call.name),
    ['CreateWorkspaceHandoff', 'GetLineage', 'PrepareWorkspaceHandoff', 'GetLineage']);
  const [create, , prepare] = h.server.calls;
  assert.deepEqual(create.args, {
    source_resident_id: 'resident_a', target_resident_id: 'resident_b', items: [{
      item_id: 'workspace', label: 'Writer WS',
      source: {workspace_id: 'dir_a', path: '/work/a', scope: 'docs'},
      transfer: {mode: 'direct', method: 'direct', parameters: {branch: '', base_commit: '', include_worktree_changes: true, exclude: []}},
      target: {workspace_id: '', path: '/work/a', scope: '.', label: 'Reviewer · Workspace'},
      attempts: [], result: {},
    }],
    explanation: 'Review the docs', label: '共享 Workspace Handoff', lineage_id: LINEAGE,
  });
  assert.equal(create.expectedVersion, 7, 'the definition change carries the shown revision');
  assert.match(create.key, /^handoff-create:/);
  assert.deepEqual([prepare.args, prepare.expectedVersion], [{handoff_id: 'handoff_new', lineage_id: LINEAGE}, undefined]);
  assert.equal(h.state.lineage.edges.find(edge => edge.id === 'handoff_new').status, 'ready');
  assert.deepEqual([h.modals.size, h.state.handoffMode], [0, false]);
  assert.deepEqual(h.toasts.at(-1), ['success', 'Handoff 已就绪，目标 Workspace Binding 已验证。']);
});

test('a failed preparation is reported with its reason', async () => {
  const h = harness();
  h.server.prepare = 'failed';
  await openNewHandoff(h);
  await h.submit();
  assert.equal(h.state.lineage.edges.find(edge => edge.id === 'handoff_new').status, 'failed');
  assert.deepEqual(h.toasts.at(-1), ['error', 'Handoff 已保存，Workspace 准备失败：disk full']);
});

test('saving without preparing keeps a mixed Handoff as a draft', async () => {
  const h = harness();
  await openNewHandoff(h);
  h.relations.addHandoffItem();
  const second = h.cards()[1];
  h.field(second, 'source-workspace').value = 'dir_notes';
  h.field(second, 'materializer').value = 'filesystem_copy';
  h.field(second, 'target-path').value = '/copies/notes';
  h.$('handoffSendNowInput').checked = false;
  h.relations.syncHandoffSubmitLabel();
  assert.equal(h.$('handoffSubmitBtn').textContent, '仅保存 Handoff');
  await h.submit();
  assert.deepEqual(h.server.writes(), ['CreateWorkspaceHandoff']);
  const [create] = h.server.calls;
  assert.equal(create.args.label, '混合 Workspace Handoff');
  assert.deepEqual([create.args.items[1].source.workspace_id, create.args.items[1].transfer.mode, create.args.items[1].target.path],
    ['dir_notes', 'copy', '/copies/notes']);
  assert.deepEqual(h.toasts.at(-1), ['success', 'Handoff 已保存为草稿。']);
});

test('a prepared Handoff only changes its explanation, a failed one is retried with its edited items', async () => {
  const ready = harness(lineage({edges: [handoffEdge('ready')]}));
  await openNewHandoff(ready);
  assert.equal(ready.$('handoffModalTitle').textContent, '编辑 Workspace Handoff');
  const [card] = ready.cards();
  assert.deepEqual([h => h.field(card, 'materializer').disabled, h => h.$('handoffAddItemBtn').title,
    h => h.$('handoffSendNowInput').checked, h => h.$('removeEdgeBtn').hidden].map(read => read(ready)),
  [true, 'Workspace 准备完成后不能再修改交接范围', false, false]);
  ready.$('handoffExplanationInput').value = 'Read chapter 2';
  await ready.submit();
  assert.deepEqual(ready.server.writes(), ['UpdateWorkspaceHandoff']);
  assert.deepEqual(ready.server.calls[0].args, {handoff_id: 'handoff_old', changes: {explanation: 'Read chapter 2'}, lineage_id: LINEAGE});

  const failed = harness(lineage({edges: [handoffEdge('failed', {error: 'disk full'})]}));
  await openNewHandoff(failed);
  assert.deepEqual([failed.$('handoffSendNowInput').checked, failed.$('handoffSubmitBtn').textContent], [true, '保存并重试']);
  failed.field(failed.cards()[0], 'target-path').value = '/copies/b2';
  await failed.submit();
  assert.deepEqual(failed.server.writes(), ['UpdateWorkspaceHandoff', 'PrepareWorkspaceHandoff']);
  const [update] = failed.server.calls;
  assert.equal(update.args.changes.items[0].target.path, '/copies/b2');
  assert.deepEqual(update.args.changes.items[0].attempts, [{status: 'failed'}], 'earlier attempts stay with the item');
  assert.deepEqual(failed.toasts.at(-1), ['success', 'Handoff 已就绪，目标 Workspace Binding 已验证。']);
});

test('a failed save rereads the Lineage and keeps the dialog open', async () => {
  const h = harness();
  h.server.fail.CreateWorkspaceHandoff = Object.assign(new Error('denied'), {code: 'instruction.forbidden'});
  await openNewHandoff(h);
  await h.submit();
  assert.deepEqual(h.server.calls.map(call => call.name), ['CreateWorkspaceHandoff', 'GetLineage']);
  assert.equal(h.commits.length, 1, 'the reread Lineage is committed');
  assert.deepEqual(h.toasts.at(-1), ['error', 'Handoff 未完成：denied']);
  assert(h.modals.has('handoffModal'));
  assert.equal(h.$('handoffSubmitBtn').dataset.busy, 'false');
});

test('removing a Handoff asks first and keeps both Residents', async () => {
  const h = harness(lineage({edges: [handoffEdge('ready')]}));
  await openNewHandoff(h);
  h.confirmAnswer = false;
  await h.relations.removeEditingEdge();
  assert.deepEqual([h.server.writes(), h.modals.has('handoffModal')], [[], true]);
  h.confirmAnswer = true;
  await h.relations.removeEditingEdge();
  assert.deepEqual(h.confirms, ['移除关系？', '移除关系？']);
  assert.deepEqual(h.server.calls.find(call => call.name === 'DeleteWorkspaceHandoff').args, {handoff_id: 'handoff_old', lineage_id: LINEAGE});
  assert.deepEqual([h.state.lineage.edges.length, h.state.lineage.nodes.length, h.modals.size], [0, 2, 0]);
  assert.deepEqual(h.toasts.at(-1), ['warning', '交接已从 Lineage 中移除。']);
});

test('selecting a relation shows its inspector once per change and clears other selections', () => {
  const h = harness(lineage({edges: [handoffEdge('ready')]}));
  Object.assign(h.state, {selectedSeedId: 'seed_x', selectedProgramObjectId: 'component_x'});
  h.relations.selectRelationEdge('missing');
  assert.equal(h.state.selectedEdgeId, '');
  h.relations.selectRelationEdge('handoff_old');
  assert.deepEqual([h.state.selectedEdgeId, h.state.selectedSeedId, h.state.selectedProgramObjectId], ['handoff_old', '', '']);
  assert.deepEqual(h.locations, [LINEAGE]);
  assert.deepEqual(h.draws, ['program-selection-cleared', 'selection', 'program-inspector', 'seed-inspector']);
  h.selectionPatched = false;
  h.relations.selectRelationEdge('handoff_old');
  assert(h.draws.includes('graph'), 'the graph is redrawn when the selection cannot be patched');
  h.relations.renderRelationInspector();
  const panel = h.$('relationInspectorPanel');
  assert.deepEqual([panel.hidden, panel.getAttribute('aria-hidden'), panel.dataset.edgeType,
    h.$('hatgViewport').classList.contains('edgeInspectorOpen')], [false, 'false', 'handoff', true]);
  assert.equal(panel.querySelector('.handoffInspectorState').textContent, '已就绪');
  assert.equal(panel.querySelector('[data-retry-handoff]'), null, 'a ready Handoff has nothing to retry');
  assert.match(panel.querySelector('.handoffCargoList').textContent, /Writer WS.*目录复制.*3.*2048 B/s);
  const shown = panel.firstElementChild;
  h.relations.renderRelationInspector();
  assert.equal(panel.firstElementChild, shown, 'an unchanged relation is not rebuilt');
  h.state.localVersion += 1;
  h.relations.renderRelationInspector();
  assert.notEqual(panel.firstElementChild, shown);
  h.state.selectedEdgeId = 'gone';
  h.relations.renderRelationInspector();
  assert.deepEqual([panel.hidden, h.state.selectedEdgeId, panel.dataset.edgeType], [true, '', undefined]);
});

test('retrying a failed Handoff from the inspector reports the real outcome', async () => {
  const h = harness(lineage({edges: [handoffEdge('failed', {error: 'disk full'})]}));
  h.state.selectedEdgeId = 'handoff_old';
  h.relations.renderRelationInspector();
  const panel = h.$('relationInspectorPanel');
  assert.match(panel.querySelector('.handoffInspectorFailure').textContent, /disk full/);
  h.server.prepare = 'failed';
  panel.querySelector('[data-retry-handoff]').click();
  await h.until(() => h.toasts.length === 1, 'the retry finishes');
  assert.deepEqual(h.toasts.at(-1), ['error', 'Handoff 重试失败：disk full']);
  assert.deepEqual(h.commits, [{render: false}]);
  assert(h.draws.includes('surface'));
  h.server.prepare = 'ready';
  h.state.localVersion += 1;
  h.relations.renderRelationInspector();
  panel.querySelector('[data-retry-handoff]').click();
  await h.until(() => h.toasts.length === 2, 'the second retry finishes');
  assert.deepEqual(h.toasts.at(-1), ['success', 'Handoff Workspace 已就绪。']);
  h.state.lineage.edges[0].status = 'failed';
  h.state.localVersion += 1;
  h.relations.renderRelationInspector();
  h.server.fail.PrepareWorkspaceHandoff = new Error('runtime unavailable');
  panel.querySelector('[data-retry-handoff]').click();
  await h.until(() => h.toasts.length === 3, 'the failing retry finishes');
  assert.deepEqual(h.toasts.at(-1), ['error', 'Handoff 重试失败：runtime unavailable']);
  assert.equal(h.server.calls.at(-1).name, 'GetLineage', 'the Lineage is reread after the failure');
});

test('the inspector edits and deletes only Workspace Handoffs', async () => {
  const h = harness(lineage({edges: [handoffEdge('ready'),
    {id: 'fork_1', type: 'inheritance', source: 'resident_a', target: 'resident_b', label: '完整轨迹', truth: {verified: true}},
    {id: 'absorb_1', type: 'absorption', source: 'resident_b', target: 'resident_a',
      absorption: {status: 'failed', error: 'queue full', run_count: 2}}]}));
  const panel = h.$('relationInspectorPanel');
  h.state.selectedEdgeId = 'handoff_old';
  h.relations.renderRelationInspector();
  const form = panel.querySelector('[data-relation-inspector-form]');
  form.elements.label.value = '  ';
  form.elements.explanation.value = 'New notes';
  form.dispatchEvent(new h.window.Event('submit', {cancelable: true}));
  await h.until(() => h.toasts.length === 1, 'the save finishes');
  assert.deepEqual(h.server.calls[0].args, {handoff_id: 'handoff_old', changes: {explanation: 'New notes'}, lineage_id: LINEAGE},
    'an empty label is left unchanged');
  assert.deepEqual([h.toasts.at(-1), h.state.selectedEdgeId], [['success', '交接关系已保存。'], 'handoff_old']);
  h.relations.renderRelationInspector();
  h.confirmAnswer = false;
  panel.querySelector('[data-delete-relation]').click();
  await h.until(() => h.confirms.length === 1, 'the deletion is confirmed first');
  assert.deepEqual(h.server.writes(), ['UpdateWorkspaceHandoff']);
  h.confirmAnswer = true;
  panel.querySelector('[data-delete-relation]').click();
  await h.until(() => h.toasts.length === 2, 'the deletion finishes');
  assert.deepEqual([h.server.writes().at(-1), h.state.selectedEdgeId, h.toasts.at(-1)],
    ['DeleteWorkspaceHandoff', '', ['warning', '交接已删除。']]);

  h.state.selectedEdgeId = 'fork_1';
  h.relations.renderRelationInspector();
  assert.equal(panel.querySelector('[data-relation-inspector-form]'), null);
  assert.match(panel.textContent, /已核验：目标 Resident 由 Fork 操作创建/);
  assert.match(panel.textContent, /不允许只改线/);
  h.state.selectedEdgeId = 'absorb_1';
  h.relations.renderRelationInspector();
  assert.match(panel.textContent, /失败：queue full/);
  assert.match(panel.textContent, /2 次/);
  assert.match(panel.textContent, /吸收关系由真实投递结果更新，不能手工伪造。/);
});

test('inspector links select the Residents and Seed, and a candidate Fork is removed through its Seed', async () => {
  const initial = lineage({edges: [{id: 'cf_1', type: 'candidate_fork', source: 'resident_a', target: 'seed_one'}]});
  initial.program.components.push({id: 'seed_one', kind: 'seed', config: {title: 'Idea', seed_type: 'idea', artifact_path: '/work/a/idea.md'}});
  const h = harness(initial);
  const panel = h.$('relationInspectorPanel');
  h.state.selectedEdgeId = 'cf_1';
  h.relations.renderRelationInspector();
  assert.match(panel.textContent, /Idea.*idea/s);
  assert.match(panel.textContent, /\/work\/a\/idea\.md/);
  panel.querySelector('[data-relation-node]').click();
  panel.querySelector('[data-relation-seed]').click();
  assert.deepEqual([h.selected, h.seeds], [[['resident_a', {force: true}]], ['seed_one']]);
  panel.querySelector('[data-delete-candidate-fork]').click();
  await h.until(() => h.candidateForks.length === 1, 'the candidate Fork is removed');
  assert.deepEqual([h.candidateForks, h.state.selectedEdgeId, h.confirms], [[['seed_one', '']], '', ['删除“Idea”的候选 Fork？']]);
});

test('closing the inspector returns to the canvas, and its Context link reselects the relation', async () => {
  const h = harness(lineage({edges: [handoffEdge('ready')]}));
  assert.equal(h.relations.closeRelationInspector(), false, 'nothing to close');
  h.state.selectedEdgeId = 'handoff_old';
  h.relations.renderRelationInspector();
  h.state.graphViewMode = 'workspace';
  h.$('relationInspectorPanel').querySelector('[data-inspector-context]').click();
  await h.until(() => h.modes.length === 1, 'the view changes');
  assert.deepEqual([h.modes, h.state.selectedEdgeId], [['context'], 'handoff_old']);
  assert.equal(h.relations.closeRelationInspector(), true);
  assert.equal(h.state.selectedEdgeId, '');
  await new Promise(resolve => h.window.requestAnimationFrame(resolve));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.window.document.activeElement, h.$('hatgCanvas'));
});
