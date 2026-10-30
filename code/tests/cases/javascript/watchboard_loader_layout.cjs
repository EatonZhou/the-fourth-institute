'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const code = process.env.TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const {babelParse, traverse} = require(code + '/tests/cases/javascript/babel_parse.cjs');
const {workbenchModuleFiles} = require(code + '/tests/cases/javascript/lineage_workbench_modules.cjs');
const {LINEAGE_GRAPH_SCRIPTS} = require(code + '/tests/cases/javascript/lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES} = require(code + '/tests/cases/javascript/hatg_panel_styles.cjs');
const {chromium} = require(code + '/management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.join(code, 'management_portal/static');
const parts = {};
for (const file of ['hatg_panel_mount.js', 'portal_resident_service.js', 'lineage_workbench.js', ...workbenchModuleFiles()]) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  traverse(babelParse(source), {
    FunctionDeclaration(p) {
      if (['updateCurrentResidentWatchboard', 'completeResidentFork'].includes(p.node.id?.name))
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
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(name.endsWith('/portal_hatg_loader.js') && process.env.LOADER_DRAFT ? path.join(process.env.LOADER_DRAFT, 'portal_hatg_loader.js') : path.join(root, name.slice(8))), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1100px;height:700px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>'});
    });
    await page.goto('http://watchboard-install.fixture.invalid');
    for (const n of ['portal_design_system', 'portal_themes', ...HATG_PANEL_STYLES, 'lineage_function_blocks']) await page.addStyleTag({url: '/static/' + n + '.css'});
    for (const n of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS, 'hatg_layout', 'portal_hatg_loader']) await page.addScriptTag({url: '/static/' + n + '.js'});
    const result = await page.evaluate(async parts => {
      document.documentElement.style.zoom = '1.25';
      const title = '[TAU:testrun_0b64e9f35d85ff9d:resident.conversation] test_94b17916d4ba Watchboard';
      const existing = [{id: 'resident', kind: 'agent', title, x: 50, y: 50}, {id: 'neighbor', kind: 'agent', title: 'Existing shared Resident', x: 70, y: 50}];
      const added = Array.from({length: 5}, (_, i) => ({id: 'added_' + i, kind: i % 2 ? 'effect' : 'event_source', title: i ? 'Watchboard · status' : 'Watchboard · ' + title, x: 50 + i * 3, y: 52 + i * 3}));
      const original = JSON.parse(JSON.stringify(existing)), requests = [], saves = [], notices = [];
      const state = {lineage: {lineage_id: 'fixture', nodes: existing}, localVersion: 0, graphViewMode: 'context', graphFocusMode: false, watchboardHiddenResidents: new Set(['resident'])};
      let nodes = existing;
      const graph = PortalHatgGraph.createGraph({root: '#canvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes', nodeContent: n => `<small>${n.kind}</small><strong>${PortalUIPrimitives.escapeHtml(n.title)}</strong><small>Listening · idle</small><small>0 messages</small>`});
      state.graphController = graph;
      const renderGraph = () => {
        const visible = state.watchboardHiddenResidents.has('resident') ? nodes.filter(n => !n.id.startsWith('added_')) : nodes;
        graph.render({nodes: visible, edges: []}); graph.refresh({measureNodes: true});
      };
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
      let failRead = false;
      const execute = async (instruction, args) => {
        requests.push({instruction, args});
        if (instruction === 'ApplyLineageChanges') return {created_ids: [...added.map(n => n.id), 'wire'], updated_ids: [], deleted_ids: []};
        assertInstruction(instruction);
        if (failRead) throw Error('graph read unavailable');
        return {lineage: {lineage_id: 'fixture', nodes: [...existing, ...added]}};
      };
      const assertInstruction = instruction => {if (instruction !== 'GetLineage') throw Error('unexpected instruction');};
      const client = {watchboard: async () => ({watchboard: {installed: true}})};
      const residentService = eval('({' + parts.updateWatchboard + '})');
      // Placement is the production layout module's; any dependency not provided here fails.
      const placement = {state, graphItemById, functionBlockInstanceById, renderGraph, saveLineageLayout};
      const {separateGraphObjects} = PortalHatgLayout.createLayout(Object.fromEntries(PortalHatgLayout.dependencies.map(name => [
        name, Object.hasOwn(placement, name) ? placement[name] : () => { throw new Error(`Unexpected use of ${name}`); }])));
      const runtimePanel = eval('({' + parts.applyLineageMutation + '})');
      // Exercise the real lazy boundary. Asset transport is isolated; the panel
      // method and measured graph/layout are production code, not a mock offset.
      window.PortalUIPrimitives = {...window.PortalUIPrimitives, loadAsset: async () => {}};
      let mounts = 0;
      window.PortalHatgPanel = {mountHatgPanel() {mounts++; return runtimePanel;}};
      const lazyRoot = document.body.appendChild(document.createElement('div'));
      const hatgPanel = PortalHatgLoader.panelPackage.create({root: lazyRoot});
      const inactive = await hatgPanel.applyLineageMutation(state.lineage, {separateObjectIds: ['resident']});
      if (inactive !== false || mounts !== 0) throw Error('Inactive editor must not mount or rearrange');
      hatgPanel.show();
      // The following first installation enters while assets are still loading.

      const lineageWorkbench = {getPanel: () => hatgPanel}, selectedResidentId = () => 'resident';
      const currentLineageWorkbench = () => lineageWorkbench;
      const agentChatPanel = {applyDetail(detail) {state.detail = detail;}};
      window.PortalResidentProjection = {invalidate() {}, load: async () => ({installed: true})};
      window.PortalUI = {toast: (...args) => notices.push(args), confirm: async () => true};
      const renderChatExtensionMenu = () => {}, writeLineageUiState = () => {}, closeChatExtensionMenu = () => {};
      const watchboardController = {render() {}}, loadFolder = async () => {}, refreshResidentSidebarIfDue = async () => {};
      const currentAgentChatPanel = () => agentChatPanel, currentWatchboardController = () => watchboardController;
      eval(parts.updateCurrentResidentWatchboard + '\nwindow.install = updateCurrentResidentWatchboard;');
      const rememberResidentShortcutSession = () => {};
      const opened = [];
      const selectResident = async (id, options) => { opened.push({id, options}); return true; };
      const appendChatWorkspaceDraft = () => {};
      eval(parts.completeResidentFork + '\nwindow.finishFork = completeResidentFork;');
      renderGraph();
      await window.install('add', 'resident');
      graph.fit({duration: 0, maxScale: 1});
      const rectangles = [...document.querySelectorAll('.hatgNode')].map(el => ({id: el.dataset.nodeId, r: el.getBoundingClientRect().toJSON()}));
      const overlaps = [];
      for (let i = 0; i < rectangles.length; i++) for (let j = i + 1; j < rectangles.length; j++) {
        const a = rectangles[i], b = rectangles[j];
        if (Math.min(a.r.right, b.r.right) > Math.max(a.r.left, b.r.left) + .1 && Math.min(a.r.bottom, b.r.bottom) > Math.max(a.r.top, b.r.top) + .1) overlaps.push([a.id, b.id]);
      }
      window.checkForkPlacement = async () => {
        const before = nodes.map(({id, x, y}) => ({id, x, y}));
        const anchor = added[0];
        const fork = {id: 'fork', kind: 'agent', title: title + ' · Branch', x: anchor.x, y: anchor.y};
        const beforeSaves = saves.length;
        const ready = await window.finishFork({resident_id: 'fork',
          lineage: {lineage_id: 'fixture', nodes: [...nodes, fork]}}, 'fixture');
        const target = document.querySelector('[data-node-id="fork"]').getBoundingClientRect();
        const overlapping = [...document.querySelectorAll('.hatgNode')].filter(el => {
          if (el.dataset.nodeId === 'fork') return false;
          const r = el.getBoundingClientRect();
          return Math.min(target.right, r.right) > Math.max(target.left, r.left) + .1
            && Math.min(target.bottom, r.bottom) > Math.max(target.top, r.top) + .1;
        }).map(el => el.dataset.nodeId);
        return {ready, overlapping, before, after: nodes.filter(n => n.id !== 'fork').map(({id, x, y}) => ({id, x, y})),
          saves: saves.slice(beforeSaves), opened};
      };
      window.checkPlacementFailure = async () => {
        for (const node of added) {node.x = 50; node.y = 50;}
        failPlacement = true; state.detail = null;
        const before = requests.length;
        await window.install('add', 'resident');
        return {requests: requests.length - before, installed: state.detail?.installed, notices};
      };
      window.checkReadFailure = async () => {
        failPlacement = false;
        failRead = true;
        state.detail = {installed: false};
        const beforeRequests = requests.length, beforeNotices = notices.length;
        await window.install('add', 'resident');
        failRead = false;
        return {requests: requests.slice(beforeRequests).map(r => r.instruction), installed: state.detail.installed,
          notices: notices.slice(beforeNotices)};
      };
      window.checkForkPlacementFailure = async () => {
        const before = requests.length, beforeOpened = opened.length, beforeNotices = notices.length;
        const anchor = added[0];
        const fork = {id: 'fork-failed-layout', kind: 'agent', title: title + ' · Branch', x: anchor.x, y: anchor.y};
        failPlacement = true;
        const ready = await window.finishFork({resident_id: fork.id,
          lineage: {lineage_id: 'fixture', nodes: [...nodes, fork]}}, 'fixture');
        return {ready, requests: requests.length - before, opened: opened.slice(beforeOpened), notices: notices.slice(beforeNotices)};
      };
      return {overlaps, original, existing, requests, saves, notices, mounts, installed: state.detail?.installed};
    }, parts);
    assert.equal(result.mounts, 1);
    console.log(JSON.stringify({stage:'cold-install',overlaps:result.overlaps,saved_batches:result.saves.length}));
    assert.deepEqual(result.overlaps, [], 'installed long-title cards use their actual rendered rectangles');
    assert.deepEqual(result.existing, result.original, 'installation cannot rearrange existing shared objects');
    assert.deepEqual(result.requests.map(r => r.instruction), ['ApplyLineageChanges', 'GetLineage'], 'one committed definition then an authoritative read, no mutation replay');
    assert.equal(result.saves.length, 1, 'all newly installed positions are persisted together');
    assert.ok(result.saves[0].length > 0 && result.saves[0].every(item => item.id.startsWith('added_')));
    assert.equal(result.installed, true); assert.deepEqual(result.notices, []); assert.deepEqual(errors, []);
    const fork = await page.evaluate(() => checkForkPlacement());
    assert.equal(fork.ready, true);
    assert.deepEqual(fork.overlapping, [], 'a menu-created Fork clears the measured long Watchboard card');
    assert.deepEqual(fork.before, fork.after, 'Fork placement leaves every existing card in place');
    assert.equal(fork.saves.length, 1);
    assert.deepEqual(fork.saves[0].map(item => item.id), ['fork']);
    assert.equal(fork.opened[0].id, 'fork');
    assert.equal(fork.opened[0].options.revealInGraph, true);
    const failure = await page.evaluate(() => checkPlacementFailure());
    assert.equal(failure.requests, 2, 'a failed layout save permits only the committed write and read');
    assert.equal(failure.installed, true, 'committed installation still reaches the extension menu');
    assert.equal(failure.notices.length, 1); assert.equal(failure.notices[0][1], 'warning');
    const forkFailure = await page.evaluate(() => checkForkPlacementFailure());
    assert.equal(forkFailure.ready, true, 'a layout save failure must still open the already created Fork');
    assert.equal(forkFailure.requests, 0, 'opening a committed Fork cannot replay its creation');
    assert.equal(forkFailure.opened[0].id, 'fork-failed-layout');
    assert.equal(forkFailure.notices.length, 1); assert.equal(forkFailure.notices[0][1], 'warning');
    const readFailure = await page.evaluate(() => checkReadFailure());
    assert.deepEqual(readFailure.requests, ['ApplyLineageChanges', 'GetLineage'], 'failed read never repeats the accepted write');
    assert.equal(readFailure.installed, true, 'committed installation remains visible when graph refresh fails');
    assert.equal(readFailure.notices.length, 1); assert.equal(readFailure.notices[0][1], 'warning');
    console.log(JSON.stringify({status:'passed',formal_acceptance:false,isolated_chromium:true,checks:['cold loader installation preserves separation targets','warm loader Fork preserves separation targets','only new objects move','inactive editor stays unmounted','committed save/read failures do not replay mutation']}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
