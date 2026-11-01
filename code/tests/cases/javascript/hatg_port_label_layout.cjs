'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {LINEAGE_GRAPH_SCRIPTS} = require('./lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES} = require('./hatg_panel_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<!doctype html><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1100px;height:800px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>');
    for (const name of ['portal_design_system', 'portal_themes', ...HATG_PANEL_STYLES])
      await page.addStyleTag({content: fs.readFileSync(path.join(root, name + '.css'), 'utf8')});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS])
      await page.addScriptTag({content: fs.readFileSync(path.join(root, name + '.js'), 'utf8')});
    await page.evaluate(() => {
      window.clicked = [];
      window.fixtureNodes = [
        {id: 'event', kind: 'event_source', title: 'A finished', x: 30, y: 40,
          ports: [{id: 'watch', type: 'watch', direction: 'out', label: 'Observed object / 被观察对象'}]},
        {id: 'effect', kind: 'effect', title: 'Receive completion', x: 75, y: 40,
          ports: [{id: 'invoke', type: 'invoke', direction: 'in', label: 'Receive invocation / 接收调用'}]},
      ];
      window.graph = PortalHatgGraph.createGraph({root: '#canvas', world: '#world', edgeLayer: '#edges',
        effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes',
        nodeContent: node => `<strong>${node.title}</strong><p>Visible completion event description</p>`,
        isPortCompatible: () => true, onPortClick: port => clicked.push(port.node_id), scaleExtent: [.05, 3.2]});
      graph.render({nodes: fixtureNodes, edges: [], wireDraft: {node_id: 'event', ...fixtureNodes[0].ports[0]}});
      graph.fit({duration: 0});
    });
    const verify = async stage => {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      for (const id of ['event', 'effect']) {
        const port = page.locator(`.hatgPort[data-port-node-id="${id}"]`);
        await port.hover();
        await port.evaluate(async element => { await Promise.all(element.getAnimations().map(a => a.finished)); });
        const box = await port.boundingBox();
        const card = await page.locator(`.hatgNode[data-node-id="${id}"]`).boundingBox();
        assert.ok(id === 'event' ? box.x >= card.x + card.width - .5 : box.x + box.width <= card.x + .5,
          `${stage}: ${id} port overlaps its card: ${JSON.stringify({box, card})}`);
        await port.click();
      }
    };
    await verify('fit');
    await page.evaluate(() => graph.refresh());
    await verify('refreshed');
    await page.evaluate(() => graph.setTransform({...graph.getTransform(), k: .65}));
    await verify('zoomed');
    assert.deepEqual(await page.evaluate(() => clicked), ['event', 'effect', 'event', 'effect', 'event', 'effect']);
    assert.deepEqual(errors, []);
    console.log('Both port directions remain outside their cards and clickable during geometry changes.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
