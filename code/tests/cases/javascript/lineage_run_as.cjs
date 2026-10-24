// Real native forms/client/renderers with the ISA and catalog transport isolated.
// This does not prove live authorization, scheduling or browser hit testing.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { JSDOM } = require('jsdom');
const { babelParse } = require('./babel_parse.cjs');
const { hatgModuleFiles, overrideRuntimeFunctions } = require('./hatg_modules.cjs');
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
  nodes: [], edges: [], seeds: [], function_blocks: [], owner: 'owner-must-not-be-used', leader_resident_id: 'resident_other',
  run_as_principal: null,
  program: { components: [{ id: 'event_source_123456789abc', kind: 'event_source', title: 'Time', config: { event_type: 'time.reached' } }], wires: [] },
  program_event_source_runtime: { event_source_123456789abc: { state: 'listening', paused: false, exhausted: false } },
};
const calls = [];
let rejection = null;
let catalogRejection = false;
w.PortalAPI = { get: async url => {
  if (url === '/static/lineage_primitive_definitions.json') return JSON.parse(read('lineage_primitive_definitions.json'));
  throw new Error(`Unexpected read ${url}`);
} };
w.PortalLineageCatalogs = {
  // /api/users is the public id/name/type catalog, not management's username directory.
  users: async () => { if (catalogRejection) throw new Error('User catalog denied'); return [{ id: 'selected-user', name: 'Chosen User', type: 'human' }, { id: 'other-user', name: 'other-user', type: 'human' }]; },
  residents: async () => ({ residents: [] }), projectCatalog: async () => ({ projects: [{ id: 'project_test', name: 'Test project' }] }),
  agentBackends: async () => ({}), populateBackends() {},
};
w.PortalInstructionClient = { execute: async (name, args, controls) => {
  calls.push({ name, args: plain(args), controls: controls && plain(controls) });
  if (rejection && name === 'UpdateLineage') throw rejection;
  if (name === 'GetLineage') {
    const lineage = plain(server);
    if (lineage.run_as_principal === null) delete lineage.run_as_principal;
    return { lineage };
  }
  if (name === 'GetLineageDiagnostics') return { diagnostics: [] };
  if (name === 'CreateLineage') {
    server = { ...server, title: args.title, run_as_principal: args.definition.run_as_principal };
    return { lineage_id: server.lineage_id };
  }
  if (name === 'UpdateLineage') {
    assert.equal(controls.expectedVersion, 4, 'the form carries its opening revision');
    server = { ...server, ...args.changes, revision: 5, program_event_source_runtime: { event_source_123456789abc: {
      state: 'listening',
    } } };
    return {};
  }
  throw new Error(`Unexpected ISA ${name}`);
} };
for (const name of ['hatg_panel_features.js', 'hatg_panel_state.js', 'portal_society_service.js', 'lineage_program.js', 'lineage_element_fields.js', 'lineage_primitive_inspector.js', ...hatgModuleFiles()]) vm.runInContext(read(name), context, { filename: name });
const source = overrideRuntimeFunctions(read('hatg_panel.js'), {
  renderLineageSurface: '() => { if (state.selectedProgramObjectId) renderProgramInspector(); }',
  renderNodeProviderFields: '() => {}',
  renderControls: '() => {}',
  updateLineageSummary: '() => {}',
  openNodeModal: '() => {}',
  openModal: 'id => { $id(id).hidden = false; }',
  closeModal: 'id => { $id(id).hidden = true; }',
  selectLineage: 'async id => { state.lineage = await societyService.loadLineage(id); }',
});
const ast = babelParse(source, 'hatg_panel.js');
const scope = ast.program.body[0].expression.callee.body.body.find(n => n.type === 'FunctionDeclaration' && n.id.name === 'createHatgRuntime');
const ret = scope.body.body.find(n => n.type === 'ReturnStatement');
const hook = `
  return { state, openCreateLineageModal, openEditLineageModal, submitLineageForm, renderLineageRunAsOptions,
    readLineageRunAsInput, loadLineageAuxiliaryCatalogs, renderProgramInspector, syncProgramComponentSwitch,
    programEventSourceStatus, graphNodeContent, activateLineageEdits };
`;
vm.runInContext(source.slice(scope.start, ret.start) + hook + source.slice(ret.end, scope.end) + '\nwindow.api = createHatgRuntime();', context);
const api = w.api;
api.state.lineage = plain(server);
api.state.projects = [{ id: 'project_test', name: 'Test project' }];
api.state.lineages = [];
api.activateLineageEdits(server.lineage_id);
const submit = () => api.submitLineageForm({ preventDefault() {}, currentTarget: $('lineageForm') });
const choose = id => { $('lineageRunAsInput').value = id ? JSON.stringify({ kind: 'user', id }) : ''; };

(async () => {
  await w.PortalLineageProgram.loadPrimitiveDefinitions();
  api.openCreateLineageModal('project_test');
  await tick();
  assert.equal($('lineageRunAsInput').value, '', 'neither owner nor leader supplies a default');
  choose('selected-user');
  $('lineageFamilizeInput').checked = false;
  await submit();
  assert.deepEqual(calls.find(c => c.name === 'CreateLineage').args.definition, { run_as_principal: { kind: 'user', id: 'selected-user' } });
  assert.equal(api.state.lineage.run_as_principal.id, 'selected-user', 'creation re-reads the saved identity');

  api.openEditLineageModal();
  await tick();
  assert.equal(api.readLineageRunAsInput().id, 'selected-user');
  choose(null);
  await submit();
  assert.equal(calls.find(c => c.name === 'UpdateLineage').args.changes.run_as_principal, null, 'clear is explicit, not omitted');
  assert.equal(api.state.lineage.run_as_principal, null);
  assert.equal(api.programEventSourceStatus('event_source_123456789abc').state, 'listening', 'clearing optional User keeps the timer runnable');
  assert.match($('lineageRunAsInput').querySelector('option[value=""]').textContent, /Resident 使用自身权限/);

  // Permission rejection retains user draft and the server error in the open form.
  api.openEditLineageModal();
  await tick();
  choose('other-user');
  rejection = Object.assign(new Error('Cannot act as selected User'), { code: 'instruction.forbidden' });
  await submit();
  assert.equal($('lineageModal').hidden, false);
  assert.equal(api.readLineageRunAsInput().id, 'other-user');
  assert.equal($('lineageFormError').hidden, false);
  assert.match($('lineageFormError').textContent, /Cannot act as/);
  assert.equal(api.state.lineage.run_as_principal, null);

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

  // A missing catalog entry cannot silently clear an existing explicit identity.
  api.state.lineage.run_as_principal = { kind: 'user', id: 'no-longer-listed' };
  catalogRejection = true;
  api.openEditLineageModal();
  await tick();
  assert.equal(api.readLineageRunAsInput().id, 'no-longer-listed');
  assert.match($('lineageRunAsCatalogStatus').textContent, /User catalog denied/);
  catalogRejection = false;

  api.state.lineage = plain({ ...server, revision: 4, run_as_principal: null });
  api.state.selectedProgramObjectId = 'event_source_123456789abc';
  api.renderProgramInspector();
  const panel = $('programInspectorPanel');
  assert.match(panel.querySelector('[data-event-listening-status]').textContent, /监听中/);
  assert.match(panel.querySelector('[data-event-run-as-note]').textContent, /Resident 使用自身身份和权限/);
  assert.equal(panel.querySelector('[data-event-run-as]').hidden, false);
  panel.querySelector('[data-event-run-as-settings]').click();
  await tick();
  assert.equal($('lineageModal').hidden, false, 'Time guidance opens actual settings');
  assert.equal(w.document.activeElement.id, 'lineageRunAsInput');
  choose('selected-user');
  await submit();
  assert.match(panel.querySelector('[data-event-listening-status]').textContent, /监听中/);
  assert.match(panel.querySelector('[data-event-run-as-note]').textContent, /selected-user/);

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
  console.log('PASS: explicit identity create/read/clear, permission and CAS errors, catalog preservation, Time settings and blocked/paused/exhausted/Home plan rendering');
  dom.window.close();
})().catch(error => { dom.window.close(); console.error(error); process.exitCode = 1; });
