'use strict';
// Actual HATG fit options and D3 renderer, without live graph mutations.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { babelParse, traverse } = require('./babel_parse.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(root, 'hatg_panel.js'), 'utf8');
let options;
traverse(babelParse(source, 'hatg_panel.js'), { VariableDeclarator(p) {
  if (p.node.id.name === 'AUTO_LAYOUT_VIEW') options = source.slice(p.node.init.start, p.node.init.end);
} });
assert.ok(options, 'production arrange camera policy');
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
    for (const name of ['portal_design_system', 'portal_themes', 'hatg_panel']) await page.addStyleTag({ url: '/static/' + name + '.css' });
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', 'lineage_graph']) await page.addScriptTag({ url: '/static/' + name + '.js' });
    assert.deepEqual(errors, [], 'production dependencies loaded');
    await page.evaluate(options => {
      window.arrangeOptions = eval(options);
      window.graph = PortalHatgGraph.createGraph({ root: '#canvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes', scaleExtent: [.05, 3.2],
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
    assert.deepEqual(errors, []);
    await page.evaluate(() => graph.destroy());
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
