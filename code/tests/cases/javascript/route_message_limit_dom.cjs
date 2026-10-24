'use strict';

// test_c42f3a67d908: actual production inspector, form bindings, instruction
// client and SocietyService. Only the server boundary returns fixture data.
// Shared native URL/reload, input hit testing and toast CSS remain covered by
// component_runtime_switch.spec.js. This contract makes no layout claim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const { babelParse } = require('./babel_parse.cjs');
const { hatgModuleFiles } = require('./hatg_modules.cjs');
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const plain = value => JSON.parse(JSON.stringify(value));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const WIRE = 'wire_012345abcdef';
const LINEAGE = 'lineage_aaaaaaaaaaaa';

function fixture() {
  return {
    lineage_id: LINEAGE, title: 'Route limit contract', revision: 3,
    nodes: [
      { id: 'resident_111111111111', title: 'Research' },
      { id: 'resident_222222222222', title: 'Design' },
    ], edges: [], seeds: [], function_blocks: [],
    program: { components: [], wires: [{ id: WIRE, kind: 'route',
      source: 'resident_111111111111', source_port: 'output',
      target: 'resident_222222222222', target_port: 'invoke', config: {} }] },
    program_invoke_wire_runtime: { [WIRE]: {
      sent_count: 3, max_messages: null, remaining_messages: null,
      generation: 0, pending_count: 0,
    } },
  };
}

// API responses are explicit examples, not a second implementation of the
// budget algorithm. Requests are checked independently before each response.
function serverBoundary() {
  const server = fixture(), calls = [];
  const control = { failReset: false, resetHold: null, saves: 0 };
  return { server, calls, control, async requestResult(url, { body }) {
    assert.equal(url, '/api/instructions/execute');
    // Match the real JSON transport; do not compare objects across VM realms.
    body = plain(body);
    calls.push(body);
    let result;
    if (body.instruction === 'GetLineage') {
      assert.equal(body.arguments.lineage_id, LINEAGE);
      result = { lineage: plain(server), version: server.revision };
    } else if (body.instruction === 'GetLineageDiagnostics') result = { diagnostics: [] };
    else if (body.instruction === 'ListPendingDeliveries') result = { deliveries: [] };
    else if (body.instruction === 'UpdateWire') {
      assert.equal(body.arguments.lineage_id, LINEAGE);
      assert.equal(body.arguments.wire_id, WIRE);
      const expected = [0, 5, null][control.saves++];
      assert.equal(body.arguments.changes.config.max_messages, expected, 'save transmits zero, finite limit, then explicit null');
      assert.equal(body.expected_version, 3, 'definition save retains its revision');
      Object.assign(server.program.wires[0], plain(body.arguments.changes));
      const projections = [
        { sent_count: 3, max_messages: 0, remaining_messages: 0, message_limit_reached: true },
        { sent_count: 0, max_messages: 5, remaining_messages: 5, message_limit_reached: false },
        { sent_count: 0, max_messages: null, remaining_messages: null, message_limit_reached: false },
      ];
      Object.assign(server.program_invoke_wire_runtime[WIRE], projections[control.saves - 1]);
      result = { wire: plain(server.program.wires[0]), version: 3 };
    } else if (body.instruction === 'ResetComponentRuntime') {
      assert.deepEqual(body.arguments, { lineage_id: LINEAGE, wire_id: WIRE });
      assert.equal(Object.hasOwn(body, 'expected_version'), false, 'runtime reset omits definition CAS');
      if (control.resetHold) await control.resetHold;
      if (control.failReset) return { response: { ok: false, status: 409 },
        payload: { error: { code: 'instruction.conflict', message: '重置失败，请重试' } } };
      Object.assign(server.program_invoke_wire_runtime[WIRE], {
        sent_count: 0, remaining_messages: 0, generation: 1, message_limit_reached: true,
      });
      result = { runtime: plain(server.program_invoke_wire_runtime[WIRE]) };
    } else throw new Error(`Unexpected instruction: ${body.instruction}`);
    return { response: { ok: true, status: 200 }, payload: { status: 'committed', result } };
  } };
}

function exposeRuntime(source) {
  const outer = babelParse(source, 'hatg_panel.js').program.body[0].expression.callee.body;
  const factory = outer.body.find(node => node.type === 'FunctionDeclaration' && node.id.name === 'createHatgRuntime');
  assert.ok(factory, 'original factory lexical scope');
  const returned = factory.body.body.find(node => node.type === 'ReturnStatement');
  assert.ok(returned, 'original factory return');
  // Same scoped bridge as the existing HATG DOM harness. The broad page redraw
  // calls the real inspector; all save/reset/merge/normalization code is intact.
  const bridge = `renderLineageSurface = () => renderProgramInspector();
    return { state, activateLineageEdits, renderProgramInspector,
      load: id => societyService.loadLineage(id) };`;
  return source.slice(0, returned.start) + bridge + source.slice(returned.end, outer.end - 1)
    + '\nwindow.__routeRuntime = createHatgRuntime;\n' + source.slice(outer.end - 1);
}

async function documentFixture(boundary, transform = (_name, source) => source) {
  const errors = [], observers = new Set();
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(String(error.stack || error)));
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM('<!doctype html><html data-portal-motion="reduced"><body><section id="programInspectorPanel"></section></body></html>', {
    url: `https://route.fixture.invalid/lineage.html?lineage=${LINEAGE}&view=hatg&program_item=${WIRE}`,
    runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  window.matchMedia = query => {
    assert.match(query, /^\((?:max-width: \d+px|prefers-reduced-motion: reduce)\)$/);
    const media = new window.EventTarget();
    Object.defineProperties(media, { media: { value: query }, matches: { value: query.includes('reduced-motion') } });
    return media;
  };
  window.CSS = { escape(value) { assert.match(String(value), /^[a-zA-Z_][a-zA-Z0-9_-]*$/); return String(value); } };
  window.PortalAPI = { requestResult: boundary.requestResult };
  // These page facilities are outside this inspector contract and never called.
  window.PortalLineageWorkbenchController = {};
  const close = async () => {
    await pause(25);
    for (const observer of observers) observer.disconnect();
    observers.clear();
    window.close();
    assert.equal(window.document, undefined, 'document disposed');
    assert.deepEqual(errors, [], 'no uncaught DOM/module errors');
  };
  try {
    for (const name of ['portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js',
      'hatg_panel_features.js', 'hatg_panel_state.js', 'portal_instruction_client.js',
      'portal_society_service.js', 'portal_resident_discovery_service.js',
      'lineage_program.js', 'lineage_element_fields.js', 'lineage_primitive_inspector.js',
      'lineage_invocation_inspector.js', ...hatgModuleFiles(), 'hatg_panel.js']) {
      let source = transform(name, fs.readFileSync(path.join(STATIC, name), 'utf8'));
      if (name === 'hatg_panel.js') source = exposeRuntime(source);
      window.eval(source + `\n//# sourceURL=${name}`);
    }
    const api = window.__routeRuntime();
    // New document always reads the persisted fixture through production clients.
    api.state.lineage = await api.load(LINEAGE);
    api.state.selectedProgramObjectId = WIRE;
    api.activateLineageEdits(LINEAGE);
    api.renderProgramInspector();
    return { api, window, close };
  } catch (error) { await close(); throw error; }
}

async function contract(transform) {
  const boundary = serverBoundary();
  let h = await documentFixture(boundary, transform);
  const q = selector => { const node = h.window.document.querySelector(selector); assert.ok(node, selector); return node; };
  const input = () => q('[name="max_messages"]');
  const reset = () => q('[data-wire-delivery-reset]');
  const edit = value => { input().value = value; input().dispatchEvent(new h.window.Event('input', { bubbles: true })); };
  const until = async (predicate, label) => {
    const deadline = Date.now() + 1500;
    while (!predicate()) {
      assert.ok(Date.now() < deadline, `${label}; toasts: ${Array.from(h.window.document.querySelectorAll('.portalToastMessage')).map(node => node.textContent).join(' | ')}`);
      await pause(5);
    }
  };
  const save = async expected => {
    q('[data-program-save]').click();
    await until(() => !h.api.state.programMutationInFlight
      && boundary.server.program.wires[0].config.max_messages === expected, 'saved configured limit');
  };
  try {
    assert.ok(input() instanceof h.window.HTMLInputElement);
    assert.equal(input().value, '');
    assert.equal(input().placeholder, '无限次');
    assert.equal(q('[data-wire-sent-count]').textContent, '3 条消息已发送');
    edit('0'); await save(0);
    assert.equal(q('[data-wire-limit-state]').textContent, '已达到发送上限，新的消息将不再发送。');
    edit('5');
    const draft = input();
    boundary.control.failReset = true;
    let release;
    boundary.control.resetHold = new Promise(resolve => { release = resolve; });
    reset().click();
    await until(() => boundary.calls.some(call => call.instruction === 'ResetComponentRuntime'), 'reset dispatched');
    assert.equal(reset().matches(':disabled'), true, 'real button disabled while reset pending');
    release();
    await until(() => Array.from(h.window.document.querySelectorAll('.portalToastMessage'))
      .some(node => node.textContent === '重置失败，请重试'), 'failure reaches actual toast DOM');
    assert.equal(input(), draft, 'failed reset retains the actual input node');
    assert.equal(input().value, '5', 'failed reset preserves unsaved edit');
    assert.equal(q('[data-wire-sent-count]').textContent, '3 条消息已发送');
    assert.equal(reset().disabled, false);
    boundary.control.failReset = false;
    boundary.control.resetHold = null;
    reset().click();
    await until(() => q('[data-wire-sent-count]').textContent === '0 条消息已发送', 'successful reset projection');
    assert.equal(input(), draft, 'successful reset retains input node');
    assert.equal(input().value, '5');
    const lastReset = boundary.calls.filter(call => call.instruction === 'ResetComponentRuntime').at(-1);
    assert.deepEqual(lastReset.arguments, { lineage_id: LINEAGE, wire_id: WIRE });
    assert.equal(Object.hasOwn(lastReset, 'expected_version'), false);
    await save(5);
    const oldDocument = h.window.document;
    await h.close(); h = null;
    const callsBeforeReload = boundary.calls.length;
    h = await documentFixture(boundary, transform);
    assert.notEqual(h.window.document, oldDocument, 'fresh DOM and controller');
    assert.equal(boundary.calls[callsBeforeReload].instruction, 'GetLineage', 'fresh document loads persisted configuration');
    assert.equal(input().value, '5');
    assert.equal(q('[data-wire-remaining]').textContent, '剩余次数：5');
    edit(''); await save(null);
    assert.equal(boundary.control.saves, 3);
    console.log('Route message limit: actual DOM/controller contract passed');
  } finally { if (h) await h.close(); }
}

module.exports = { contract };
if (require.main === module) contract().catch(error => { console.error(error); process.exitCode = 1; });
