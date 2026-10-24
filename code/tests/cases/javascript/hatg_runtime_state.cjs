// Production controller contracts. The default sink does not emulate browser
// layout, D3 DOM updates, hit testing or accessibility-tree construction.
// component_runtime_dom.cjs reuses the AST bridge with real jsdom/D3 controls;
// component_runtime_switch.spec.js retains native reload and physical input.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { randomUUID } = require('node:crypto');
const { babelParse } = require('./babel_parse.cjs');
const { hatgModuleFiles, readHatgModule, overrideHatgFunctions, startHatgRuntime } = require('./hatg_modules.cjs');
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const PANEL = path.join(STATIC, 'hatg_panel.js');
const raw = fs.readFileSync(PANEL, 'utf8');
// The runtime and the module scripts it takes its parts from.
const SOURCES = Object.freeze({
  'hatg_panel.js': raw,
  ...Object.fromEntries(hatgModuleFiles().map(file => [file, readHatgModule(file)])),
});
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function walk(node, predicate, found = []) {
  if (!node || typeof node !== 'object') return found;
  if (predicate(node)) found.push(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'tokens', 'comments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, predicate, found));
    else if (value && typeof value === 'object') walk(value, predicate, found);
  }
  return found;
}

function runtimeScope(source) {
  const ast = babelParse(source, PANEL);
  const matches = walk(ast, n => n.type === 'FunctionDeclaration' && n.id?.name === 'createHatgRuntime');
  assert.equal(matches.length, 1, 'unique production runtime scope');
  return matches[0];
}

// AST-located mutations are in-memory negative controls, never source edits.
// A function is looked up in the runtime first, then in its module scripts.
function mutate(sources, name, kind) {
  if (!name) return sources;
  let target = '';
  const fn = id => {
    const scope = runtimeScope(sources['hatg_panel.js']);
    let node = scope.body.body.find(n => n.type === 'FunctionDeclaration' && n.id?.name === id);
    target = 'hatg_panel.js';
    for (const [file, source] of Object.entries(sources)) {
      if (node) break;
      [node] = walk(babelParse(source, file).program, n => n.type === 'FunctionDeclaration' && n.id?.name === id);
      target = file;
    }
    assert.ok(node, `production function ${id}`);
    return node;
  };
  let nodes, replacement;
  if (name === 'wrong-pause-value') {
    nodes = walk(fn('toggleProgramComponent'), n => n.type === 'ObjectProperty' && n.key.name === 'paused').map(n => n.value);
    replacement = 'paused';
  } else if (name === 'definition-cas') {
    nodes = walk(fn('toggleProgramComponent'), n => n.type === 'ObjectProperty' && n.key.name === 'definition').map(n => n.value);
    replacement = 'true';
  } else if (name === 'lost-runtime-projection') {
    const key = kind === 'effect' ? 'program_effect_runtime' : 'program_event_source_runtime';
    nodes = walk(fn('mergeLineageStateDomains'), n => n.type === 'IfStatement'
      && n.test.type === 'CallExpression' && n.test.arguments.some(a => a.type === 'StringLiteral' && a.value === key));
    replacement = ';';
  } else if (name === 'draft-overwrite') {
    nodes = walk(fn('renderProgramInspector'), n => n.type === 'IfStatement'
      && walk(n.test, p => p.type === 'StringLiteral' && p.value === 'true').length > 0).slice(0, 1).map(n => n.test);
    replacement = 'false';
  } else if (name === 'missing-input-listener') {
    nodes = walk(fn('renderProgramInspector'), n => n.type === 'CallExpression'
      && n.callee.property?.name === 'addEventListener' && n.callee.object?.name === 'inspectorDraftForm'
      && n.arguments[0]?.value === 'input').map(n => n.arguments[0]);
    replacement = '"not-input"';
  } else if (name === 'missing-click-listener') {
    nodes = walk(fn('renderProgramInspector'), n => n.type === 'OptionalCallExpression'
      && n.callee.property?.name === 'addEventListener'
      && walk(n.callee, p => p.type === 'StringLiteral' && p.value === '[data-program-component-switch]').length > 0);
    replacement = 'undefined';
  } else if (name === 'stuck-busy') {
    nodes = walk(fn('applyProgramMutation'), n => n.type === 'CallExpression'
      && n.callee.object?.name === 'programFeature' && n.callee.property?.name === 'endMutation');
    replacement = 'undefined';
  } else if (name === 'missing-node-badge') {
    nodes = walk(fn('graphNodeContent'), n => n.type === 'CallExpression' && n.callee.name === 'programComponentPaused');
    replacement = 'false';
  } else if (name === 'drawer-trigger-enabled') {
    nodes = walk(fn('syncProgramComponentSwitch'), n => n.type === 'LogicalExpression' && n.operator === '||'
      && n.right.type === 'Identifier' && n.right.name === 'paused').map(n => n.right);
    replacement = 'false';
  } else if (name === 'canvas-trigger-enabled') {
    nodes = walk(fn('programEventSourceNodeControl'), n => n.type === 'CallExpression' && n.callee.name === 'Boolean'
      && n.arguments[0]?.property?.name === 'paused');
    replacement = 'false';
  } else throw new Error(`unknown negative control ${name}`);
  assert.equal(nodes.length, 1, `one AST mutation site: ${name}`);
  const source = sources[target];
  return { ...sources, [target]: source.slice(0, nodes[0].start) + replacement + source.slice(nodes[0].end) };
}

class Control {
  constructor() { this.dataset = {}; this.attrs = {}; this.disabled = false; this.hidden = false; this.listeners = new Map(); this.value = ''; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key] ?? null; }
  addEventListener(name, listener) { const list = this.listeners.get(name) || []; list.push(listener); this.listeners.set(name, list); }
  fire(name) { for (const listener of this.listeners.get(name) || []) listener({ currentTarget: this, preventDefault() {} }); }
  click() { if (!this.disabled) this.fire('click'); }
}

// A deliberately small markup sink, not a DOM implementation. A redraw destroys
// form controls, so the fixture cannot itself preserve dirty input. Elements are
// exposed only when the *production-generated* markup contains their attributes.
class Panel extends Control {
  constructor() { super(); this.isConnected = true; this.htmlWrites = 0; this.innerHTML = ''; }
  set innerHTML(value) {
    this.html = String(value); this.htmlWrites += 1; this.elements = new Map();
    for (const marker of ['data-program-component-form', 'data-program-component-switch', 'data-component-switch-state',
      'data-component-switch-label', 'data-component-switch-note', 'data-program-emit', 'data-event-listening-status']) {
      if (this.html.includes(marker)) this.elements.set(`[${marker}]`, new Control());
    }
    const openingTag = marker => {
      const offset = this.html.indexOf(marker);
      return this.html.slice(this.html.lastIndexOf('<', offset), this.html.indexOf('>', offset));
    };
    for (const [selector, element] of this.elements) element.disabled = openingTag(selector.slice(1, -1)).includes(' disabled');
    const titleStart = this.html.indexOf('name="title"');
    if (titleStart >= 0) {
      const title = new Control(); const valueStart = this.html.indexOf('value="', titleStart) + 7;
      title.value = this.html.slice(valueStart, this.html.indexOf('"', valueStart));
      title.disabled = openingTag('name="title"').includes(' disabled');
      this.elements.set('[name="title"]', title);
    }
    const switchControl = this.elements.get('[data-program-component-switch]');
    if (switchControl) {
      const start = this.html.indexOf('aria-checked="', this.html.indexOf('data-program-component-switch')) + 14;
      switchControl.setAttribute('aria-checked', this.html.slice(start, this.html.indexOf('"', start)));
    }
    const form = this.elements.get('[data-program-component-form]');
    if (form) form.querySelectorAll = () => [...this.elements.values()].filter(e => e !== form);
  }
  get innerHTML() { return this.html; }
  querySelector(selector) {
    if (selector === '[data-program-component-form], [data-program-operator-form]') selector = '[data-program-component-form]';
    return this.elements.get(selector) || null;
  }
  querySelectorAll(selector) { return selector === 'form' ? [this.querySelector('[data-program-component-form]')].filter(Boolean) : []; }
  removeAttribute(name) {
    delete this.attrs[name];
    if (name.startsWith('data-')) {
      const parts = name.slice(5).split('-');
      delete this.dataset[parts[0] + parts.slice(1).map(s => s[0].toUpperCase() + s.slice(1)).join('')];
    }
  }
  contains(element) { return [...this.elements.values()].includes(element); }
}

function serverFixture(kind) {
  const id = `${kind}_123456789abc`;
  return {
    lineage_id: 'lineage_123456789abc', title: 'runtime fixture', revision: 17,
    layout_revision: 2, runtime_revision: 3, nodes: [], edges: [], seeds: [], function_blocks: [],
    program: { schema: 'tfi.hatg-program.v2', components: [{ id, kind, title: '已保存的名称', config: kind === 'event_source'
      ? { event_type: 'manual.triggered' } : { script: 'GetLineage' } }], wires: [] },
    program_event_source_runtime: kind === 'event_source' ? { [id]: { paused: false, occurrence_count: 0 } } : {},
    program_effect_runtime: kind === 'effect' ? { [id]: { paused: false, state: 'active' } } : {},
  };
}

function controller(kind, source, server, environment = null) {
  const panel = environment?.context.document.querySelector('#programInspectorPanel') || new Panel();
  const calls = [], messages = [], graph = {};
  const control = { fail: false, hold: null };
  const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  const globals = { console, setTimeout, clearTimeout, URLSearchParams, CSS: { escape: String },
    document: { querySelector: id => id === '#programInspectorPanel' ? panel : null, getElementById: () => null },
    PortalI18n: { t: String, html: tag, text: tag, markup: String, message: String, definition: x => x },
    PortalUIPrimitives: { randomUUID, escapeHtml: value => String(value ?? '') },
    PortalUI: { toast: (message, kind) => messages.push({ message, kind }), setButtonBusy: (button, busy) => { button.disabled = busy; } },
    PortalResidentDiscoveryService: { createClient: () => ({}) }, PortalLineageWorkbenchController: {},
    PortalAPI: {
      requestResult: async (url, { body }) => {
        assert.equal(url, '/api/instructions/execute'); calls.push(plain(body));
        let result;
        if (body.instruction === 'PauseComponent') {
          if (control.hold) await control.hold;
          if (control.fail) return { response: { ok: false, status: 409 }, payload: { error: { code: 'instruction.conflict', message: '运行开关暂时不可用' } } };
          const key = kind === 'effect' ? 'program_effect_runtime' : 'program_event_source_runtime';
          assert.equal(typeof body.arguments.paused, 'boolean', 'PauseComponent carries the required paused boolean');
          const paused = body.arguments.paused;
          server[key][body.arguments.component_id] = { paused, state: paused ? 'paused' : 'active' };
          result = {};
        } else if (body.instruction === 'GetLineage') result = { lineage: plain(server) };
        else if (body.instruction === 'GetLineageDiagnostics') result = { diagnostics: [] };
        else throw new Error(`unexpected instruction ${body.instruction}`);
        return { response: { ok: true, status: 200 }, payload: { status: 'committed', result } };
      },
    },
    __render: api => {
      api.renderProgramInspector();
      const component = api.state.lineage.program.components[0];
      graph.content = api.graphNodeContent(component); graph.control = api.hatgNodeControl(component);
    },
  };
  const context = environment?.context || vm.createContext(globals);
  if (environment) {
    // The DOM adapter retains actual Window/Document, PortalUI and primitives.
    // Share only the isolated server boundary and existing runtime-scope hook.
    for (const name of ['PortalAPI', 'PortalResidentDiscoveryService', 'PortalLineageWorkbenchController']) {
      context[name] = globals[name];
    }
    context.__render = environment.render;
  } else context.window = context;
  const sources = typeof source === 'string' ? { ...SOURCES, 'hatg_panel.js': source } : source;
  for (const file of ['hatg_panel_features.js', 'hatg_panel_state.js', 'portal_instruction_client.js', 'portal_society_service.js',
    'lineage_program.js', 'lineage_element_fields.js', 'lineage_primitive_inspector.js']) {
    vm.runInContext(fs.readFileSync(path.join(STATIC, file), 'utf8'), context, { filename: file });
  }
  // Keep the entire original runtime, its parts and their lexical dependencies.
  // Only the broad page redraw effect is replaced; it still calls the real
  // inspector and graph presentation/controller. No state is written by __render.
  const exposed = ['activateLineageEdits', 'renderProgramInspector', 'graphNodeContent', 'hatgNodeControl',
    'syncProgramComponentSwitch'];
  const runtime = startHatgRuntime(context, overrideHatgFunctions(sources, {
    renderLineageSurface: '() => window.__render(window.__runtimeApi)',
  }), exposed);
  const api = {
    state: runtime.state, ...Object.fromEntries(exposed.map(name => [name, runtime.get(name)])),
    load: id => runtime.get('societyService').loadLineage(id),
  };
  context.__runtimeApi = api;
  api.state.lineage = plain(environment?.initialLineage || server);
  api.state.selectedProgramObjectId = server.program.components[0].id;
  api.activateLineageEdits(server.lineage_id); context.__render(api);
  return { api, panel, graph, calls, messages, control, refresh: () => context.__render(api) };
}

async function scenario(kind, source) {
  const server = serverFixture(kind), id = server.program.components[0].id;
  let h = controller(kind, source, server);
  const get = selector => h.panel.querySelector(selector);
  const switchButton = () => get('[data-program-component-switch]');
  const title = () => get('[name="title"]');
  const form = () => get('[data-program-component-form]');
  const edit = value => { title().value = value; form().fire('input'); };
  assert.equal(switchButton().getAttribute('aria-checked'), 'true', 'initial projection is enabled');
  edit('未保存的名称');
  assert.equal(form().dataset.programDraftDirty, 'true', 'input binding marks draft dirty');
  const draftTitle = title(), writes = h.panel.htmlWrites;
  let release; h.control.hold = new Promise(resolve => { release = resolve; });
  switchButton().click(); await tick();
  assert.equal(switchButton().disabled, true, 'in-flight toggle is disabled');
  assert.equal(title().disabled, true, 'in-flight draft temporarily disabled');
  release(); await tick();
  assert.equal(switchButton().getAttribute('aria-checked'), 'false', 'server pause projection reaches switch');
  assert.equal(switchButton().disabled, false, 'pause settles and unlocks switch');
  assert.equal(title(), draftTitle, 'same draft control survives mutation');
  assert.equal(h.panel.htmlWrites, writes, 'dirty inspector is not replaced');
  assert.equal(title().value, '未保存的名称', 'pause preserves title');
  assert.equal(title().disabled, false, 'draft controls restored');
  assert.equal(form().dataset.programDraftDirty, 'true', 'pause preserves dirty marker');
  assert.match(h.graph.content, /data-execution-state="paused"[^>]*>已关闭/, 'closed node badge');
  const requests = () => h.calls.filter(call => call.instruction === 'PauseComponent');
  assert.equal(requests().length, 1, 'one pause');
  assert.deepEqual(requests()[0].arguments, { lineage_id: server.lineage_id, component_id: id, paused: true }, 'exact runtime arguments');
  assert.equal(Object.hasOwn(requests()[0], 'expected_version'), false, 'runtime switch omits graph CAS');
  assert.ok(requests()[0].idempotency_key, 'runtime operation has idempotency key');
  assert.equal(Object.hasOwn(server.program.components[0].config, 'paused'), false, 'runtime is not written into config');
  assert.equal(h.api.state.lineage.revision, 17, 'runtime projection works at unchanged graph revision');
  if (kind === 'event_source') {
    assert.equal(get('[data-program-emit]').disabled, true, 'drawer trigger disabled while paused');
    assert.equal(h.graph.control.action, 'emit-manual-event');
    assert.equal(h.graph.control.disabled, true, 'canvas control declares paused disabled');
    assert.equal(get('[data-event-listening-status]').textContent, '已关闭', 'listening status follows runtime');
  }
  // A fresh controller reads the same fake server through the real client.
  // This is controller hydration, not a browser reload/cache assertion.
  const loaded = await h.api.load(server.lineage_id);
  h = controller(kind, source, loaded);
  assert.equal(switchButton().getAttribute('aria-checked'), 'false', 'fresh controller reads persisted pause');
  edit('打开失败也保留'); h.control.fail = true;
  switchButton().click(); await tick();
  assert.ok(h.messages.some(m => m.kind === 'error' && /打开失败：.*运行开关暂时不可用/.test(m.message)), 'reopen error is surfaced');
  assert.equal(switchButton().getAttribute('aria-checked'), 'false', 'failed reopen retains paused projection');
  assert.equal(switchButton().disabled, false, 'failure permits retry');
  assert.equal(title().value, '打开失败也保留', 'failed reopen preserves draft');
  assert.equal(form().dataset.programDraftDirty, 'true', 'failure preserves dirty marker');
  h.control.fail = false; switchButton().click(); await tick();
  assert.equal(switchButton().getAttribute('aria-checked'), 'true', 'retry applies server resume projection');
  assert.equal(title().value, '打开失败也保留', 'retry preserves draft');
  assert.equal(requests().length, 2, 'failure then one explicit retry');
  for (const request of requests()) {
    assert.deepEqual(request.arguments, { lineage_id: server.lineage_id, component_id: id, paused: false }, 'resume is PauseComponent paused=false');
    assert.equal(Object.hasOwn(request, 'expected_version'), false, 'resume omits graph CAS');
  }
  assert.equal(new Set(requests().map(r => r.idempotency_key)).size, 2, 'user retry is a separate intent');
  assert.doesNotMatch(h.graph.content, /data-execution-state="paused"/, 'resume removes paused badge');
  if (kind === 'event_source') {
    assert.equal(get('[data-program-emit]').disabled, false, 'drawer trigger restored');
    assert.equal(h.graph.control.disabled, false, 'canvas control restored');
  }
}

async function verify(kind) {
  await scenario(kind, SOURCES);
  const controls = ['wrong-pause-value', 'definition-cas', 'lost-runtime-projection', 'draft-overwrite',
    'missing-input-listener', 'missing-click-listener', 'stuck-busy', 'missing-node-badge'];
  if (kind === 'event_source') controls.push('drawer-trigger-enabled', 'canvas-trigger-enabled');
  const failures = {
    'wrong-pause-value': 'server pause projection reaches switch',
    'definition-cas': 'runtime switch omits graph CAS',
    'lost-runtime-projection': 'server pause projection reaches switch',
    'draft-overwrite': 'same draft control survives mutation',
    'missing-input-listener': 'input binding marks draft dirty',
    'missing-click-listener': 'in-flight toggle is disabled',
    'stuck-busy': 'pause settles and unlocks switch',
    'missing-node-badge': 'closed node badge',
    'drawer-trigger-enabled': 'drawer trigger disabled while paused',
    'canvas-trigger-enabled': 'canvas control declares paused disabled',
  };
  for (const name of controls) {
    await assert.rejects(scenario(kind, mutate(SOURCES, name, kind)), error => error.code === 'ERR_ASSERTION' && error.message.includes(failures[name]),
      `production regression must be detected: ${kind}/${name}`);
  }
}

module.exports = { controller, serverFixture, raw, SOURCES, mutate };
// Importing the shared AST bridge never executes either independent case.
if (require.main === module) {
  test('event_source runtime switch preserves draft and recovers from reopen failure', () => verify('event_source'));
  test('effect runtime switch preserves draft and recovers from reopen failure', () => verify('effect'));
}
