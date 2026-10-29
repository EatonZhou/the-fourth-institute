'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {LINEAGE_GRAPH_SCRIPTS} = require('./lineage_graph_scripts.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, name.slice(8))),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><div id="chat"></div>'
        + '<div class="portalHatgPanelComponent" style="position:absolute;left:780px;top:100px">'
        + '<div id="canvas" class="hatgCanvas" style="width:360px;height:530px">'
        + '<div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg>'
        + '<div id="labels"></div><div id="nodes"></div></div></div></div>'});
    });
    await page.goto('http://fork-visibility.fixture.invalid');
    for (const name of ['portal_design_system', 'portal_themes', 'hatg_panel', 'lineage_function_blocks'])
      await page.addStyleTag({url: '/static/' + name + '.css'});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives',
      'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS,
      'hatg_graph_view', 'hatg_canvas_selection', 'hatg_resident_dialogs'])
      await page.addScriptTag({url: '/static/' + name + '.js'});
    for (const scenario of [{mode: 'full'}, {mode: 'new-session'}, {mode: 'full', failChat: true}]) {
      const {mode, failChat = false} = scenario;
      const result = await page.evaluate(async ({mode, failChat}) => {
        document.documentElement.style.zoom = '1.25';
        document.getElementById('chat').textContent = 'source';
        const source = {id: 'resident_source', kind: 'agent', title: 'Watchboard source', x: 40, y: 30};
        const child = {id: 'resident_child', kind: 'agent', title: 'Watchboard source · long inherited branch', x: 40, y: 1100};
        const state = {lineage: {lineage_id: 'lineage_fixture', nodes: [source]}, selectedNodeId: source.id};
        const calls = [], notices = [];
        const graph = PortalHatgGraph.createGraph({root: '#canvas', world: '#world', edgeLayer: '#edges',
          effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes',
          viewportInsets: () => ({top: 80, bottom: 10})});
        state.graphController = graph;
        const renderGraph = () => {graph.render({nodes: state.lineage.nodes, edges: [], selectedId: state.selectedNodeId}); graph.refresh({measureNodes: true});};
        const nodeById = id => state.lineage.nodes.find(node => node.id === id);
        const create = (module, values) => module[Object.keys(module).find(key => key.startsWith('create'))](
          Object.fromEntries(module.dependencies.map(name => [name, Object.hasOwn(values, name) ? values[name]
            : () => {throw Error('Unexpected dependency ' + name);}])));
        const view = create(PortalHatgGraphView, {state, graphSelectedId: () => state.selectedNodeId});
        const selection = create(PortalHatgCanvasSelection, {
          state, displayNodeById: nodeById, nodeResidentId: node => node.id,
          currentHatgComponentOptions: () => ({emit: async (_event, detail) => {
            calls.push({kind: 'open', resident_id: detail.resource.resident_id});
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            if (failChat) throw Error('Chat navigation unavailable after commit');
            document.getElementById('chat').textContent = detail.resource.resident_id;
          }}),
          publishLocation() {}, renderGraphSelection: () => {renderGraph(); return true;}, renderGraph,
          renderControls() {}, renderSeedInspector() {}, renderLineageHeader() {},
          revealSelectedGraphNode: view.revealSelectedGraphNode,
        });
        const service = async payload => {calls.push({kind: 'create', payload}); return {resident_id: child.id,
          lineage: {lineage_id: 'lineage_fixture', nodes: [source, child], revision: 2}};};
        const assertNoOverlap = id => { if (graph.getObjectSeparation(id)) throw Error('Unexpected overlap in visibility fixture'); };
        const dialogs = create(PortalHatgResidentDialogs, {
          state, nodeById, familyForFacade: () => null, familyForNewResident: () => null,
          flushLineageLayoutSave: async () => {}, societyService: {branchNode: service, createNode: service},
          commitLineage: lineage => {state.lineage = lineage; renderGraph();}, renderGraph,
          separateGraphObject: async id => { assertNoOverlap(id); },
          selectNode: selection.selectNode, setSyncStatus: (...args) => notices.push(args),
          toast: (...args) => notices.push(args), bindResidentStateCollection() {},
        });
        try {
          renderGraph(); graph.focusNode(source.id, {duration: 0});
          const created = await dialogs.createQuickFork(source, mode);
          const rect = document.querySelector('[data-node-id="resident_child"]').getBoundingClientRect().toJSON();
          const clip = document.getElementById('canvas').getBoundingClientRect().toJSON();
          return {created: created?.id, rect, clip, calls, notices, chat: document.getElementById('chat').textContent,
            source, child, busy: state.structureMutationInFlight};
        } finally {graph.destroy();}
      }, scenario);
      assert.equal(result.created, 'resident_child');
      assert.equal(result.calls.filter(call => call.kind === 'create').length, 1, 'one native mutation');
      assert.equal(result.busy, false);
      assert.equal(result.source.y, 30, 'camera reveal must not move existing nodes');
      assert.equal(result.child.y, 1100, 'camera reveal must not rewrite server placement');
      assert.equal(result.chat, mode === 'new-session' || failChat ? 'source' : 'resident_child');
      if (failChat) {
        assert.ok(result.notices.some(([message, kind]) => kind === 'warning' && message.includes('Chat navigation unavailable after commit')),
          'a committed Fork with failed navigation must remain created and warn against replay');
        assert.equal(result.notices.some(([, kind]) => kind === 'saved'), false, 'no false ready status');
      }
      assert.ok(result.rect.left >= result.clip.left && result.rect.right <= result.clip.right
        && result.rect.top >= result.clip.top + 80 * 1.25 && result.rect.bottom <= result.clip.bottom,
      `${mode}: created node must fit the actual narrow canvas below its toolbar: ${JSON.stringify(result)}`);
    }
    assert.deepEqual(errors, []);
    console.log('Native canvas Fork and blank-session creation reveal measured nodes without moving them.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
