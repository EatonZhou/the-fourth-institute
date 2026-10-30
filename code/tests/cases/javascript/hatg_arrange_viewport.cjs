'use strict';
// Actual HATG fit options and D3 renderer, without live graph mutations.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { babelParse, traverse } = require('./babel_parse.cjs');
const { readHatgModule } = require('./hatg_modules.cjs');
const { LINEAGE_GRAPH_SCRIPTS } = require('./lineage_graph_scripts.cjs');
const { HATG_PANEL_STYLES } = require('./hatg_panel_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const familySource = readHatgModule('hatg_family_blocks.js');
// The arrange camera policy is the layout module's.
const layoutContext = vm.createContext({});
layoutContext.window = layoutContext;
vm.runInContext(readHatgModule('hatg_layout.js'), layoutContext);
let options = `(${JSON.stringify(layoutContext.PortalHatgLayout.AUTO_LAYOUT_VIEW)})`, familyOptions;
traverse(babelParse(familySource, 'hatg_family_blocks.js'), { FunctionDeclaration(p) {
  if (p.node.id.name !== 'showFamilyAlone') return;
  p.traverse({ OptionalCallExpression(call) {
    if (call.node.callee.property?.name === 'fit') {
      const argument = call.node.arguments[0];
      familyOptions = familySource.slice(argument.start, argument.end);
    }
  } });
} });
assert.ok(options, 'production arrange camera policy');
assert.ok(familyOptions, 'production Family camera policy');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.join(root, name.slice(8))), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:720px;height:420px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div><div id="toolbar" style="position:absolute;top:0;left:0;right:0;height:64px;background:white;z-index:50">Arrange</div></div></div></body></html>' });
    });
    await page.goto('http://hatg-arrange.fixture.invalid');
    for (const name of ['portal_design_system', 'portal_themes', ...HATG_PANEL_STYLES, 'lineage_function_blocks']) await page.addStyleTag({ url: '/static/' + name + '.css' });
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS]) await page.addScriptTag({ url: '/static/' + name + '.js' });
    assert.deepEqual(errors, [], 'production dependencies loaded');
    await page.evaluate(options => {
      window.arrangeOptions = eval(options);
      window.graph = PortalHatgGraph.createGraph({ root: '#canvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes', scaleExtent: [.05, 3.2],
        readViewport: () => window.savedViewport,
        onViewportChange: (_id, view) => window.savedViewport = view,
        viewportInsets: () => ({ top: document.getElementById('toolbar').clientHeight + 10, bottom: 10 }) });
      window.nodes = Array.from({ length: 8 }, (_, i) => ({ id: 'node_' + i, kind: 'agent', title: 'Arrange ' + i, x: 15 + i * 10, y: 15 + i * 9 }));
      graph.render({ nodes, edges: [] });
    }, options);
    for (const [width, height] of [[720, 420], [460, 340]]) {
      await page.evaluate(({ width, height }) => {
        Object.assign(document.getElementById('canvas').style, { width: width + 'px', height: height + 'px' });
        graph.fit({ ...arrangeOptions, duration: 0 });
      }, { width, height });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const clipped = await page.evaluate(() => {
        const canvas = document.getElementById('canvas').getBoundingClientRect(), toolbar = document.getElementById('toolbar').getBoundingClientRect();
        return [...document.querySelectorAll('#nodes [data-node-id]')].filter(node => {
          const r = node.getBoundingClientRect();
          return r.left < canvas.left || r.right > canvas.right || r.top < toolbar.bottom || r.bottom > canvas.bottom;
        }).map(node => node.dataset.nodeId);
      });
      assert.deepEqual(clipped, [], `arranged cards must fit the usable ${width}x${height} canvas`);
      assert.equal(await page.locator('#nodes [data-node-id]').count(), 8);
    }
    // Family isolation uses its own production policy. A fixed lower zoom
    // bound previously clipped members on a narrow, docked HATG panel.
    await page.evaluate(policy => {
      window.familyOptions = eval('(' + policy + ')');
      document.getElementById('canvas').style.width = '418px';
      graph.fit({ ...familyOptions, duration: 0 });
    }, familyOptions);
    async function assertVisible(label) {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const clipped = await page.evaluate(() => {
        const canvas = document.getElementById('canvas').getBoundingClientRect(), toolbar = document.getElementById('toolbar').getBoundingClientRect();
        return [...document.querySelectorAll('#nodes [data-node-id], #nodes .functionBlockBoundary')].filter(node => {
          const r = node.getBoundingClientRect();
          return r.left < canvas.left || r.right > canvas.right || r.top < toolbar.bottom || r.bottom > canvas.bottom;
        }).map(node => ({ id: node.dataset.nodeId || 'block',
          bounds: node.getBoundingClientRect().toJSON(), canvas: canvas.toJSON(), toolbar: toolbar.toJSON() }));
      });
      assert.deepEqual(clipped, [], label);
    }
    await assertVisible('Family isolation fits every card');
    await page.evaluate(() => {
      document.documentElement.style.zoom = '1.25';
      document.getElementById('canvas').style.width = '350px';
    });
    await assertVisible('fitted Family stays visible after panel resize and zoom');
    await page.evaluate(() => {
      window.nodes = [{ id: 'left', kind: 'agent', title: 'Left member', x: 15, y: 30 },
        { id: 'right', kind: 'agent', title: 'Right member', x: 75, y: 55 }];
      graph.render({ nodes, edges: [], functionBlockBoundaries: [{ id: 'block', title: 'Expanded block', member_ids: ['left', 'right'] }] });
    });
    await page.waitForFunction(() => document.querySelectorAll('#nodes .hatgNode').length === 2);
    await page.evaluate(() => {
      graph.refresh({ measureNodes: true });
      graph.focusNode('left', { duration: 0 });
    });
    await assertVisible('focus includes the complete asymmetric block boundary');
    // A native scroll offset used to survive camera fit/focus, clipping the
    // facade even though D3's transform itself was within the viewport.
    const scroll = await page.evaluate(() => {
      const canvas = document.getElementById('canvas');
      canvas.scrollLeft = 102;
      canvas.scrollTop = 80;
      document.querySelector('#nodes .hatgNode').scrollIntoView();
      return { x: canvas.scrollLeft, y: canvas.scrollTop };
    });
    assert.deepEqual(scroll, { x: 0, y: 0 }, 'the canvas has exactly one camera');
    await page.evaluate(() => graph.focusNode('left', { duration: 0 }));
    await assertVisible('native scroll attempts cannot displace a focused block');
    const persisted = await page.evaluate(() => {
      graph.setContext('lineage_fixture');
      // Emulate structural repair fitting the survivors, with an older stored
      // camera. The retained card is already visible and needs no extra pan.
      graph.fit({ duration: 0 });
      const expected = graph.getTransform();
      graph.ensureNodeVisible('left', { persist: true });
      graph.setContext('lineage_fixture');
      return { expected, restored: graph.getTransform() };
    });
    for (const axis of ['x', 'y', 'k']) assert.ok(Math.abs(persisted.expected[axis] - persisted.restored[axis]) < .001,
      `structurally revealed view survives reload: ${axis}`);
    await assertVisible('restored survivor view remains inside canvas');
    const familyReveal = await page.evaluate(async () => {
      graph.render({ nodes: [{id: 'outer', kind: 'agent', title: 'New outer Family', x: 12, y: 76}], edges: [] });
      graph.fit({duration: 220, maxScale: 1.45, persist: true});
      await new Promise(resolve => setTimeout(resolve, 40));
      graph.ensureNodeVisible('outer', {persist: true});
      const committed = graph.getTransform();
      await new Promise(resolve => setTimeout(resolve, 260));
      const settled = graph.getTransform();
      graph.setContext('lineage_fixture');
      return {committed, settled, restored: graph.getTransform()};
    });
    for (const view of ['settled', 'restored']) for (const axis of ['x', 'y', 'k']) {
      assert.ok(Math.abs(familyReveal.committed[axis] - familyReveal[view][axis]) < .001,
        `Family reveal owns a stable saved camera: ${view}.${axis}`);
    }
    await assertVisible('new Family remains visible on immediate reload');
    // Compare server fallback placement with actual CSS card rectangles, not
    // an assertion that repeats the server's size estimate.
    const placementPath = path.resolve(root, '../society_service/lineage_placement.py');
    for (const title of ['[TAU:testrun_443b3903add10916:lineage.hatg] Source F0', 'W'.repeat(100), '宽名称'.repeat(30)]) {
      const positions = JSON.parse(execFileSync('python3', ['-c', `
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('placement',sys.argv[1]); p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
source={'id':'source','title':sys.argv[2],'x':50,'y':50}
child={'id':'child','title':sys.argv[2]+' Fork'}
child['x'],child['y']=p.node_position({'nodes':[source]},source,{},child=child)
print(json.dumps([source,child]))
`, placementPath, title], { encoding: 'utf8' }));
      await page.evaluate(nodes => {
        graph.destroy();
        for (const id of ['nodes', 'edges', 'effects', 'labels']) document.getElementById(id).replaceChildren();
        document.documentElement.style.zoom = '1';
        document.getElementById('canvas').style.width = '418px';
        window.graph = PortalHatgGraph.createGraph({ root: '#canvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes', scaleExtent: [.05, 3.2],
          viewportInsets: () => ({ top: 74, bottom: 10 }),
          nodeContent: node => `<span class="nodeTopline"><span class="nodeIdentity">resident_123456789012</span></span><strong class="nodeTitle">${node.title}</strong><span class="nodeFooter"><span class="nodeTypePill">resident</span><span class="nodeMessageCount">0</span><span class="nodeFooterState">idle</span></span>` });
        graph.render({ nodes: nodes.map(node => ({ ...node, kind: 'agent' })), edges: [] });
        graph.refresh({ measureNodes: true });
        graph.fit({ ...arrangeOptions, duration: 0 });
      }, positions);
      await assertVisible('long title Fork cards fit');
      const overlap = await page.evaluate(() => {
        const [a, b] = [...document.querySelectorAll('#nodes .hatgNode')].map(n => n.getBoundingClientRect());
        return Math.min(a.right, b.right) > Math.max(a.left, b.left) && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top);
      });
      assert.equal(overlap, false, 'native Fork placement leaves long title cards separate');
    }
    assert.deepEqual(errors, []);
    await page.evaluate(() => graph.destroy());
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
