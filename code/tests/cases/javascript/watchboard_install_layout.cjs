'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {babelParse, traverse} = require('./babel_parse.cjs');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const parts = {};
for (const file of ['hatg_panel', 'portal_resident_service', 'lineage_workbench']) {
  const source = fs.readFileSync(path.join(root, file + '.js'), 'utf8');
  traverse(babelParse(source), {
    FunctionDeclaration(p) {
      if (['separateGraphObjects', 'updateCurrentResidentWatchboard'].includes(p.node.id?.name))
        parts[p.node.id.name] = source.slice(p.node.start, p.node.end);
    },
    ObjectMethod(p) {
      if (['applyLineageMutation', 'updateWatchboard'].includes(p.node.key?.name))
        parts[p.node.key.name] = source.slice(p.node.start, p.node.end);
    },
  });
}
assert.equal(Object.keys(parts).length, 4);
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, name.slice(8))), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1100px;height:700px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>'});
    });
    await page.goto('http://watchboard-install.fixture.invalid');
    for (const n of ['portal_design_system', 'portal_themes', 'hatg_panel', 'lineage_function_blocks']) await page.addStyleTag({url: '/static/' + n + '.css'});
    for (const n of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', 'lineage_graph']) await page.addScriptTag({url: '/static/' + n + '.js'});
    const result = await page.evaluate(async parts => {
      document.documentElement.style.zoom = '1.25';
      const title = '[TAU:testrun_0b64e9f35d85ff9d:resident.conversation] test_94b17916d4ba Watchboard';
      const existing = [{id: 'resident', kind: 'agent', title, x: 50, y: 50}, {id: 'neighbor', kind: 'agent', title: 'Existing shared Resident', x: 70, y: 50}];
      const added = Array.from({length: 5}, (_, i) => ({id: 'added_' + i, kind: i % 2 ? 'effect' : 'event_source', title: i ? 'Watchboard · status' : 'Watchboard · ' + title, x: 50 + i * 3, y: 52 + i * 3}));
      const original = JSON.parse(JSON.stringify(existing)), requests = [], saves = [], notices = [];
      const state = {lineage: {lineage_id: 'fixture', nodes: existing}, graphViewMode: 'context', graphFocusMode: false, watchboardHiddenResidents: new Set()};
      let nodes = existing;
      const graph = PortalHatgGraph.createGraph({root: '#canvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes', nodeContent: n => `<small>${n.kind}</small><strong>${PortalUIPrimitives.escapeHtml(n.title)}</strong><small>Listening · idle</small><small>0 messages</small>`});
      state.graphController = graph;
      const renderGraph = () => {graph.render({nodes, edges: []}); graph.refresh({measureNodes: true});};
      const graphItemById = id => nodes.find(n => n.id === id), functionBlockInstanceById = () => null;
      let failPlacement = false;
      const saveLineageLayout = async items => {
        if (failPlacement) throw Error('layout storage unavailable');
        saves.push(items.map(({id, x, y}) => ({id, x, y})));
      };
      const loadPromise = Promise.resolve(), commitLineage = lineage => {state.lineage = lineage; nodes = lineage.nodes; renderGraph(); return {changed: true};};
      const required = value => value, residentPath = value => value, keyed = () => ({});
      const get = async () => ({watchboard: {installation: {lineage_id: 'fixture', revision: 1, files: [], install: [
        ...added.map(value => ({op: 'create', kind: 'component', value})), {op: 'create', kind: 'wire', value: {id: 'wire'}}], remove: []}}});
      const execute = async (instruction, args) => {requests.push({instruction, args}); return {lineage: {lineage_id: 'fixture', nodes: [...existing, ...added]}};};
      const client = {watchboard: async () => ({watchboard: {installed: true}})};
      const residentService = eval('({' + parts.updateWatchboard + '})');
      const separateGraphObjects = eval('(' + parts.separateGraphObjects + ')');
      const hatgPanel = eval('({' + parts.applyLineageMutation + '})');
      const lineageWorkbench = {getPanel: () => hatgPanel}, selectedResidentId = () => 'resident';
      const agentChatPanel = {applyDetail(detail) {state.detail = detail;}};
      window.PortalResidentProjection = {invalidate() {}, load: async () => ({installed: true})};
      window.PortalUI = {toast: (...args) => notices.push(args), confirm: async () => true};
      const renderChatExtensionMenu = () => {}, writeLineageUiState = () => {}, closeChatExtensionMenu = () => {};
      const watchboardController = {render() {}}, loadFolder = async () => {}, refreshResidentSidebarIfDue = async () => {};
      eval(parts.updateCurrentResidentWatchboard + '\nwindow.install = updateCurrentResidentWatchboard;');
      renderGraph();
      await window.install('add', 'resident');
      graph.fit({duration: 0, maxScale: 1});
      const rectangles = [...document.querySelectorAll('.hatgNode')].map(el => ({id: el.dataset.nodeId, r: el.getBoundingClientRect().toJSON()}));
      const overlaps = [];
      for (let i = 0; i < rectangles.length; i++) for (let j = i + 1; j < rectangles.length; j++) {
        const a = rectangles[i], b = rectangles[j];
        if (Math.min(a.r.right, b.r.right) > Math.max(a.r.left, b.r.left) + .1 && Math.min(a.r.bottom, b.r.bottom) > Math.max(a.r.top, b.r.top) + .1) overlaps.push([a.id, b.id]);
      }
      window.checkPlacementFailure = async () => {
        for (const node of added) {node.x = 50; node.y = 50;}
        failPlacement = true; state.detail = null;
        const before = requests.length;
        await window.install('add', 'resident');
        return {requests: requests.length - before, installed: state.detail?.installed, notices};
      };
      return {overlaps, original, existing, requests, saves, notices, installed: state.detail?.installed};
    }, parts);
    assert.deepEqual(result.overlaps, [], 'installed long-title cards use their actual rendered rectangles');
    assert.deepEqual(result.existing, result.original, 'installation cannot rearrange existing shared objects');
    assert.equal(result.requests.length, 1, 'one committed definition, no mutation replay');
    assert.equal(result.saves.length, 1, 'all newly installed positions are persisted together');
    assert.ok(result.saves[0].length > 0 && result.saves[0].every(item => item.id.startsWith('added_')));
    assert.equal(result.installed, true); assert.deepEqual(result.notices, []); assert.deepEqual(errors, []);
    const failure = await page.evaluate(() => checkPlacementFailure());
    assert.equal(failure.requests, 1, 'a failed layout save cannot replay the installation');
    assert.equal(failure.installed, true, 'committed installation still reaches the extension menu');
    assert.equal(failure.notices.length, 1); assert.equal(failure.notices[0][1], 'warning');
    console.log('Watchboard committed installation separates only new measured cards and persists one layout batch');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
