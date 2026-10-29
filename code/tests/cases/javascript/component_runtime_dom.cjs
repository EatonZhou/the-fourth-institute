'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const { controller, serverFixture, raw } = require('./hatg_runtime_state.cjs');
const { LINEAGE_GRAPH_SCRIPTS } = require('./lineage_graph_scripts.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, label) {
  const deadline = Date.now() + 3000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, `timed out: ${label}`);
    await pause(5);
  }
}

// Adds the actual HTML parser, DOM controls, event dispatch, D3 presentation,
// PortalUI toast/busy state and fresh-document hydration to the existing AST
// controller contract. No control, selector, HTML sink or D3 renderer is faked.
// Native reload/URL initialization, physical input and layout remain in the
// retained event_source browser case in component_runtime_switch.spec.js.
async function documentFixture(kind, server, sourceTransform = (_name, source) => source) {
  const errors = [], observers = new Set();
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(String(error.stack || error)));
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM('<!doctype html><html data-portal-motion="reduced"><body>'
    + '<section id="programInspectorPanel"></section><main id="graph"><div id="world">'
    + '<svg><g id="edges"></g><g id="effects"></g></svg><div id="labels"></div><div id="nodes"></div>'
    + '</div></main></body></html>', {
    url: `https://component.fixture.invalid/lineage.html?lineage=${server.lineage_id}&view=hatg&program_item=${server.program.components[0].id}`,
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
  window.CSS = { escape(value) {
    assert.match(String(value), /^[a-zA-Z_][a-zA-Z0-9_-]*$/);
    return String(value);
  } };
  let graph;
  const close = async () => {
    graph?.destroy();
    await pause(25);
    for (const observer of observers) observer.disconnect();
    observers.clear();
    window.close();
    assert.equal(window.document, undefined, 'old document and controls disposed');
    assert.deepEqual(errors, [], 'DOM and production modules have no uncaught errors');
  };
  try {
    for (const name of ['portal_text.js', 'portal_state.js', 'portal_ui.js', 'portal_ui_primitives.js',
      'vendor/d3/d3.v7.9.0.min.js', 'projection_libraries/portal-graph/1.0.0/geometry.js',
      ...LINEAGE_GRAPH_SCRIPTS.map(name => `${name}.js`)]) {
      window.eval(sourceTransform(name, fs.readFileSync(path.join(staticRoot, name), 'utf8')) + `\n//# sourceURL=${name}`);
    }
    const h = controller(kind, sourceTransform('hatg_panel.js', raw), server, {
      context: dom.getInternalVMContext(), initialLineage: serverFixture(kind),
      render(api) {
        api.renderProgramInspector();
        if (!graph) graph = window.PortalHatgGraph.createGraph({
          root: '#graph', world: '#world', edgeLayer: '#edges', effectLayer: '#effects',
          labelLayer: '#labels', nodeLayer: '#nodes',
          nodeContent: api.graphNodeContent, nodeControl: api.hatgNodeControl,
        });
        graph.render({ nodes: api.state.lineage.program.components, edges: [] });
      },
    });
    // Start with a stale enabled definition in the new document. Only the real
    // client GetLineage result may hydrate the persisted runtime projection.
    h.api.state.lineage = await h.api.load(server.lineage_id);
    h.refresh();
    assert.ok(h.calls.some(call => call.instruction === 'GetLineage'
      && call.arguments.lineage_id === server.lineage_id), 'fresh document reads its lineage through the real client');
    return { ...h, window, close, errors };
  } catch (error) { await close(); throw error; }
}

async function domContract(kind, sourceTransform) {
  assert.ok(['event_source', 'effect'].includes(kind));
  const server = serverFixture(kind), id = server.program.components[0].id;
  let h = await documentFixture(kind, server, sourceTransform);
  const requests = [];
  const query = selector => h.window.document.querySelector(selector);
  const switchButton = () => query('[role="switch"]');
  const title = () => query('[name="title"]');
  const form = () => query('[data-program-component-form]');
  const badge = () => query(`[data-node-id="${id}"] [data-execution-state="paused"]`);
  const edit = value => {
    title().value = value;
    title().dispatchEvent(new h.window.Event('input', { bubbles: true }));
  };
  const assertTriggers = disabled => {
    if (kind !== 'event_source') return;
    for (const selector of ['[data-program-emit]', `[data-event-source-id="${id}"][data-manual-event-trigger]`]) {
      const button = query(selector);
      assert.ok(button instanceof h.window.HTMLButtonElement, 'trigger is an actual HTML button');
      assert.equal(button.disabled, disabled, `${selector} disabled property follows runtime`);
      assert.equal(button.matches(':disabled'), disabled, `${selector} native disabled semantics`);
    }
  };
  try {
    assert.ok(switchButton() instanceof h.window.HTMLButtonElement);
    assert.equal(switchButton().getAttribute('aria-checked'), 'true');
    edit('未保存的名称');
    assert.equal(form().dataset.programDraftDirty, 'true', 'bubbling input marks the actual form dirty');
    const draft = title(), draftForm = form(), initialDocument = h.window.document;
    let release;
    h.control.hold = new Promise(resolve => { release = resolve; });
    switchButton().click();
    await until(() => h.calls.some(call => call.instruction === 'PauseComponent'), 'pause reaches server');
    assert.equal(switchButton().matches(':disabled'), true, 'native switch disabled in flight');
    assert.equal(title().matches(':disabled'), true, 'native draft disabled in flight');
    const callsAtBusy = h.calls.length;
    switchButton().click();
    await pause(10);
    assert.equal(h.calls.length, callsAtBusy, 'native disabled switch blocks another click');
    release();
    await until(() => switchButton().getAttribute('aria-checked') === 'false' && !switchButton().disabled, 'pause projected and unlocked');
    assert.equal(title(), draft, 'actual title node survives pause');
    assert.equal(form(), draftForm, 'actual dirty form survives pause');
    assert.equal(title().value, '未保存的名称');
    assert.equal(form().dataset.programDraftDirty, 'true');
    assert.equal(badge()?.textContent, '已关闭', 'D3 renders real paused node badge');
    assertTriggers(true);
    if (kind === 'event_source') assert.equal(query('[data-event-listening-status]').textContent, '已关闭');
    requests.push(...h.calls.filter(call => call.instruction === 'PauseComponent'));
    const previousWindow = h.window;
    await h.close();
    h = await documentFixture(kind, server, sourceTransform);
    assert.notEqual(h.window, previousWindow);
    assert.notEqual(h.window.document, initialDocument);
    assert.equal(switchButton().getAttribute('aria-checked'), 'false', 'fresh document hydrates paused server projection');
    assertTriggers(true);
    edit('打开失败也保留');
    h.control.fail = true;
    switchButton().click();
    await until(() => /打开失败：.*运行开关暂时不可用/.test(h.window.document.body.textContent), 'actual error toast rendered');
    assert.equal(switchButton().getAttribute('aria-checked'), 'false');
    assert.equal(switchButton().matches(':disabled'), false, 'failed reopen permits a real click retry');
    assert.equal(title().value, '打开失败也保留');
    assertTriggers(true);
    h.control.fail = false;
    switchButton().click();
    await until(() => switchButton().getAttribute('aria-checked') === 'true' && !switchButton().disabled, 'retry projects resume');
    assert.equal(title().value, '打开失败也保留');
    assert.equal(badge(), null, 'D3 removes paused node badge');
    assertTriggers(false);
    requests.push(...h.calls.filter(call => call.instruction === 'PauseComponent'));
    assert.deepEqual(requests.map(call => call.arguments.paused), [true, false, false], 'pause, failed resume, retried resume');
    return { kind, requests, initial_document_disposed: true, fresh_document_hydrated: true };
  } finally { await h.close(); }
}

module.exports = { domContract };
if (require.main === module) domContract(process.argv[2]).then(evidence => {
  console.log(JSON.stringify({ result: 'PASS', ...evidence }));
}).catch(error => { console.error(error); process.exitCode = 1; });
