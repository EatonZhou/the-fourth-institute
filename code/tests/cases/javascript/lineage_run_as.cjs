// Real native forms/client/renderers with the ISA and catalog transport isolated.
// This does not prove live authorization, scheduling or browser hit testing.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { JSDOM } = require('jsdom');
const { babelParse } = require('./babel_parse.cjs');
const { hatgSources, overrideHatgFunctions, startHatgRuntime } = require('./hatg_modules.cjs');
const ROOT = path.resolve(__dirname, '../../..');
const STATIC = path.join(ROOT, 'management_portal/static');
const read = name => fs.readFileSync(path.join(STATIC, name), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const tag = (strings, ...values) => strings.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
const html = fs.readFileSync(path.join(ROOT, 'management_portal/hatg-panel.html'), 'utf8');
const dom = new JSDOM(html, { url: 'https://portal.test/lineage', runScripts: 'outside-only' });
const context = dom.getInternalVMContext();
const w = dom.window;
const $ = id => w.document.getElementById(id);
Object.assign(w, {
  CSS: { escape: String }, console,
  PortalUIPrimitives: { randomUUID, escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;') },
  PortalI18n: { t: String, html: tag, text: tag, markup: String, message: String, definition: x => x },
  PortalResidentDiscoveryService: { createClient: () => ({}) },
  PortalLineageWorkbenchController: {},
  PortalUI: { toast() {}, setButtonBusy(button, busy) { button.disabled = busy; } },
});
vm.runInContext(read('portal_state.js'), context);
let server = {
  lineage_id: 'lineage_123456789abc', title: 'Schedule', project_id: 'project_test', revision: 4,
  nodes: [{ id: 'resident_other', title: 'Team Leader' }, { id: 'resident_next', title: 'Next Leader' }],
  edges: [], seeds: [], function_blocks: [], owner: 'owner-must-not-be-used', leader_resident_id: 'resident_other',
  run_as_principal: { kind: 'user', id: 'legacy-user-must-not-be-used' },
  program: { components: [{ id: 'event_source_123456789abc', kind: 'event_source', title: 'Time', config: { event_type: 'time.reached' } }], wires: [] },
  program_event_source_runtime: { event_source_123456789abc: { state: 'listening', paused: false, exhausted: false } },
};
const calls = [];
let rejection = null;
let userCatalogReads = 0;
w.PortalAPI = { get: async url => {
  if (url === '/static/lineage_primitive_definitions.json') return JSON.parse(read('lineage_primitive_definitions.json'));
  throw new Error(`Unexpected read ${url}`);
} };
w.PortalLineageCatalogs = {
  users: async () => { userCatalogReads++; throw new Error('User catalog must not be requested'); },
  residents: async () => ({ residents: [] }), projectCatalog: async () => ({ projects: [{ id: 'project_test', name: 'Test project' }] }),
  agentBackends: async () => ({}), populateBackends() {},
};
w.PortalInstructionClient = { execute: async (name, args, controls) => {
  calls.push({ name, args: plain(args), controls: controls && plain(controls) });
  if (rejection && name === 'UpdateLineage') throw rejection;
  if (name === 'GetLineage') {
    const lineage = plain(server);
    return { lineage };
  }
  if (name === 'GetLineageDiagnostics') return { diagnostics: [] };
  if (name === 'CreateLineage') {
    assert.equal(Object.hasOwn(args, 'run_as_principal'), false);
    assert.equal(Object.hasOwn(args.definition || {}, 'run_as_principal'), false);
    server = { ...server, title: args.title };
    return { lineage_id: server.lineage_id };
  }
  if (name === 'UpdateLineage') {
    assert.equal(Object.hasOwn(args.changes, 'run_as_principal'), false);
    server = { ...server, ...args.changes, revision: 5, program_event_source_runtime: { event_source_123456789abc: {
      state: 'listening',
    } } };
    return {};
  }
  throw new Error(`Unexpected ISA ${name}`);
} };
for (const name of ['hatg_panel_features.js', 'hatg_panel_state.js', 'portal_society_service.js', 'lineage_program.js', 'lineage_element_fields.js', 'lineage_primitive_inspector.js']) vm.runInContext(read(name), context, { filename: name });
const sources = overrideHatgFunctions(hatgSources(), {
  renderLineageSurface: '() => { if (state.selectedProgramObjectId) renderProgramInspector(); }',
  renderNodeProviderFields: '() => {}',
  renderControls: '() => {}',
  updateLineageSummary: '() => {}',
  openNodeModal: '() => {}',
  openModal: 'id => { $id(id).hidden = false; }',
  closeModal: 'id => { $id(id).hidden = true; }',
  selectLineage: 'async id => { state.lineage = await societyService.loadLineage(id); }',
});
const names = ['openCreateLineageModal', 'openEditLineageModal', 'submitLineageForm',
  'loadLineageAuxiliaryCatalogs', 'renderProgramInspector', 'syncProgramComponentSwitch',
  'programEventSourceStatus', 'graphNodeContent', 'activateLineageEdits'];
const runtime = startHatgRuntime(context, sources, names);
const api = { state: runtime.state, ...Object.fromEntries(names.map(name => [name, runtime.get(name)])) };
api.state.lineage = plain(server);
api.state.projects = [{ id: 'project_test', name: 'Test project' }];
api.state.lineages = [];
api.activateLineageEdits(server.lineage_id);
const submit = () => api.submitLineageForm({ preventDefault() {}, currentTarget: $('lineageForm') });

(async () => {
  await w.PortalLineageProgram.loadPrimitiveDefinitions();
  api.openCreateLineageModal('project_test');
  await tick();
  assert.equal($('lineageRunAsInput'), null, 'creation has no automatic identity control');
  assert.equal(userCatalogReads, 0);
  await api.loadLineageAuxiliaryCatalogs();
  assert.equal(userCatalogReads, 0, 'page startup also has no User catalog dependency');
  $('lineageNameInput').value = 'Created without identity';
  $('lineageFamilizeInput').checked = false;
  await submit();
  const created = calls.find(c => c.name === 'CreateLineage');
  assert.equal(created.args.title, 'Created without identity');
  assert.equal(created.args.familize, false);
  assert.equal(created.args.definition, undefined);
  assert.equal(api.state.lineage.title, created.args.title, 'creation re-reads the saved graph');

  api.openCreateLineageModal('project_test');
  assert.equal($('lineageFamilizeInput').checked, true);
  await submit();
  assert.equal(calls.filter(c => c.name === 'CreateLineage').at(-1).args.familize, true);

  api.openEditLineageModal();
  await tick();
  assert.equal($('lineageLeaderInput').value, 'resident_other');
  $('lineageLeaderInput').value = 'resident_next';
  await submit();
  const saved = calls.find(c => c.name === 'UpdateLineage');
  assert.equal(saved.args.changes.leader_resident_id, 'resident_next');
  assert.equal(saved.controls.expectedVersion, 4, 'the form carries its opening revision');
  assert.equal(api.state.lineage.leader_resident_id, 'resident_next');
  assert.equal(userCatalogReads, 0, 'editing a legacy definition does not fetch its User');
  await w.PortalSocietyService.createClient().updateMetadata({
    lineage_id: server.lineage_id, title: 'Metadata only', run_as_principal: { kind: 'user', id: 'stale-client-value' },
  });
  assert.equal(Object.hasOwn(calls.filter(c => c.name === 'UpdateLineage').at(-1).args.changes, 'run_as_principal'), false);

  // Permission rejection retains user draft and the server error in the open form.
  api.openEditLineageModal();
  await tick();
  $('lineageNameInput').value = 'Unsaved name';
  rejection = Object.assign(new Error('Cannot edit Lineage'), { code: 'instruction.forbidden' });
  await submit();
  assert.equal($('lineageModal').hidden, false);
  assert.equal($('lineageNameInput').value, 'Unsaved name');
  assert.equal($('lineageFormError').hidden, false);
  assert.match($('lineageFormError').textContent, /Cannot edit Lineage/);

  // Updating the live graph must not upgrade the version attached to an old draft.
  const openingRevision = $('lineageForm').dataset.revision;
  api.state.lineage.revision = 99;
  server.revision = 100;
  rejection = Object.assign(new Error('changed concurrently'), { code: 'instruction.version_conflict' });
  const writes = calls.filter(c => c.name === 'UpdateLineage').length;
  await submit();
  const lastWrite = calls.filter(c => c.name === 'UpdateLineage').at(-1);
  assert.equal(lastWrite.controls.expectedVersion, Number(openingRevision));
  assert.equal(calls.filter(c => c.name === 'UpdateLineage').length, writes + 1, 'conflict does not replay');
  assert.match($('lineageFormError').textContent, /changed concurrently/);
  assert.equal(api.state.lineage.revision, server.revision, 'formal version conflict refreshes the graph');
  rejection = null;

  api.state.lineage = plain({ ...server, revision: 4 });
  api.state.selectedProgramObjectId = 'event_source_123456789abc';
  api.renderProgramInspector();
  const panel = $('programInspectorPanel');
  assert.match(panel.querySelector('[data-event-listening-status]').textContent, /监听中/);
  assert.match(panel.querySelector('[data-event-leader-note]').textContent, /Leader.*resident_next/);
  assert.equal(panel.querySelector('[data-event-leader-note]').textContent.includes('legacy-user'), false);
  assert.equal(panel.querySelector('[data-event-leader]').hidden, false);
  panel.querySelector('[data-event-leader-settings]').click();
  await tick();
  assert.equal($('lineageModal').hidden, false, 'Time guidance opens actual settings');
  assert.equal(w.document.activeElement.id, 'lineageLeaderInput');
  $('lineageLeaderInput').value = 'resident_other';
  await submit();
  assert.match(panel.querySelector('[data-event-listening-status]').textContent, /监听中/);
  assert.match(panel.querySelector('[data-event-leader-note]').textContent, /resident_other/);
  api.state.lineage.leader_resident_id = '';
  api.syncProgramComponentSwitch(panel, api.state.lineage.program.components[0]);
  assert.match(panel.querySelector('[data-event-leader-note]').textContent, /设置 Leader/);
  assert.equal(panel.querySelector('[data-event-leader-note]').textContent.includes('owner-must-not-be-used'), false);
  assert.equal(userCatalogReads, 0);

  for (const [runtime, label] of [
    [{ state: 'blocked', error: 'Access revoked' }, '自动触发受阻'],
    [{ state: 'error', error: 'Schedule invalid' }, '运行错误'],
    [{ state: 'blocked', paused: true, error: 'old reason' }, '已关闭'],
    [{ state: 'blocked', exhausted: true, error: 'old reason' }, '次数耗尽'],
    [{}, '等待运行状态'],
  ]) {
    api.state.lineage.program_event_source_runtime.event_source_123456789abc = runtime;
    api.syncProgramComponentSwitch(panel, api.state.lineage.program.components[0]);
    assert.ok(panel.querySelector('[data-event-listening-status]').textContent.includes(label));
    const card = api.graphNodeContent(api.state.lineage.program.components[0]);
    assert.ok(card.includes(label));
    assert.ok(!card.includes('监听中'));
  }

  // Home consumes the same authoritative source state, never an obsolete due time.
  Object.assign(w, { esc: w.PortalUIPrimitives.escapeHtml, displayDateTime: String, scheduledTriggeredExpanded: new Set() });
  const home = read('home.js');
  const names = new Set(['scheduledTriggeredAllows', 'scheduledTriggeredScheduleText', 'scheduledTriggeredNextAt', 'scheduledTriggeredWaitingForPlan', 'scheduledTriggeredItemId', 'scheduledTriggeredScriptStatus', 'scheduledTriggeredSourceState', 'scheduledTriggeredSourceHtml', 'scheduledTriggeredLineageHtml']);
  const declared = n => (n.type === 'FunctionDeclaration' ? [n.id] : n.type === 'VariableDeclaration' ? n.declarations.map(d => d.id) : []);
  for (const n of babelParse(home, 'home.js').program.body) if (declared(n).some(id => names.has(id.name))) vm.runInContext(home.slice(n.start, n.end), context);
  const sourceRow = { component_id: 'event_source_123456789abc', event_type: 'time.reached', config: { schedule_mode: 'recurring', recurrence_kind: 'interval', interval_seconds: 60 }, runtime: { state: 'blocked', error: '<Access denied>', next_due_at: 'obsolete-due-time' } };
  const item = { lineage_id: server.lineage_id, resident_id: 'resident_test', health: 'error', sources: [sourceRow], problems: ['<Access denied>'], can_manage: true };
  const host = w.document.createElement('div');
  host.innerHTML = w.scheduledTriggeredLineageHtml({ loaded: true, items: [item] });
  assert.match(host.querySelector('.scheduled-triggered-summary-value').textContent, /受阻.*<Access denied>/);
  assert.equal(host.querySelector('[data-scheduled-triggered-row]').classList.contains('is-error'), true);
  assert.equal(host.textContent.includes('obsolete-due-time'), false);
  const link = host.querySelector('a[href*="program_item="]');
  assert.equal(new URL(link.href).searchParams.get('program_item'), sourceRow.component_id);
  for (const state of ['paused', 'exhausted', 'error']) assert.equal(w.scheduledTriggeredNextAt({ runtime: { state, next_due_at: 'old' } }), '');

  // Enabled Time sources have no established plan until an actual next time arrives.
  const pendingSource = { ...sourceRow, runtime: { state: 'listening' } };
  const renderSources = sources => {
    host.innerHTML = w.scheduledTriggeredLineageHtml({ loaded: true, items: [{ ...item, health: 'warn', problems: [], sources }] });
    return host.querySelector('.scheduled-triggered-primary .scheduled-triggered-summary-value').textContent;
  };
  assert.equal(renderSources([pendingSource]), '等待调度生成计划');
  assert.equal(host.querySelector('[data-scheduled-triggered-source-state]').textContent, '等待调度生成计划');
  assert.ok(host.querySelector('[data-scheduled-triggered-source]').textContent.includes('每 60 秒'), 'interval remains visible as configuration');
  assert.equal(host.textContent.includes('下一次 '), false);
  const plannedSource = { ...pendingSource, runtime: { state: 'listening', next_scheduled_at: '2026-10-16T03:00:00Z' } };
  assert.equal(renderSources([plannedSource]), '2026-10-16T03:00:00Z');
  assert.equal(host.textContent.includes('等待调度生成计划'), false);
  plannedSource.runtime.next_due_at = '2026-10-16T03:00:05Z';
  assert.equal(renderSources([plannedSource]), '2026-10-16T03:00:05Z');
  assert.match(renderSources([plannedSource, { ...pendingSource, component_id: 'event_source_pending' }]), /2026-10-16T03:00:05Z.*等待调度生成计划/);
  for (const overrides of [{ paused: true }, { runtime: { state: 'paused' } }, { runtime: { state: 'listening', exhausted: true } }]) {
    renderSources([{ ...pendingSource, ...overrides }]);
    assert.equal(host.textContent.includes('等待调度生成计划'), false);
  }
  renderSources([{ ...pendingSource, event_type: 'file.changed', config: { path: '/workspace/input' } }]);
  assert.equal(host.textContent.includes('等待调度生成计划'), false, 'file listeners do not wait for a Time plan');
  console.log('PASS: creation/settings omit run-as and User reads; legacy identity ignored; Leader guidance/navigation; permission/CAS drafts and runtime/Home states');
  dom.window.close();
})().catch(error => { dom.window.close(); console.error(error); process.exitCode = 1; });
