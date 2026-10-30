'use strict';
// Real HATG markup, CSS, graph and navigation measurement, without business data.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { LINEAGE_GRAPH_SCRIPTS } = require('./lineage_graph_scripts.cjs');
const { HATG_PANEL_STYLES } = require('./hatg_panel_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.setContent('<!doctype html><div class="portalHatgPanelComponent" id="fixture"></div>');
    await page.evaluate(html => {
      const template = document.createElement('template'); template.innerHTML = html;
      const viewport = template.content.querySelector('#hatgViewport').cloneNode(false);
      viewport.style.cssText = 'width:1000px;height:600px;flex:none';
      viewport.append(template.content.querySelector('#graphNavigationBar').cloneNode(true));
      viewport.insertAdjacentHTML('beforeend', '<div id="hatgCanvas" class="hatgCanvas"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div>');
      document.getElementById('fixture').append(viewport);
      window.resizeErrors = [];
      window.addEventListener('error', event => { window.resizeErrors.push(event.message); });
    }, fs.readFileSync(path.join(root, 'hatg-panel.html'), 'utf8'));
    for (const name of ['portal_design_system', 'portal_themes', ...HATG_PANEL_STYLES])
      await page.addStyleTag({ path: path.join(root, 'static', name + '.css') });
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS, 'hatg_toolbar'])
      await page.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    await page.evaluate(() => {
      window.graph = PortalHatgGraph.createGraph({ root: '#hatgCanvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes' });
      graph.render({ nodes: [], edges: [] });
      window.stopNavigation = PortalHatgToolbar.observeNavigationHeight(
        document.getElementById('hatgViewport'), document.getElementById('graphNavigationBar'));
    });
    const samples = [];
    for (const width of [1000, 340, 700]) {
      await page.evaluate(width => { document.getElementById('hatgViewport').style.width = width + 'px'; }, width);
      await page.evaluate(() => new Promise(resolve => {
        let frames = 0; const sample = () => ++frames === 5 ? resolve() : requestAnimationFrame(sample); requestAnimationFrame(sample);
      }));
      samples.push(await page.evaluate(() => {
        const viewport = document.getElementById('hatgViewport'), nav = document.getElementById('graphNavigationBar'), canvas = document.getElementById('hatgCanvas');
        const v = viewport.getBoundingClientRect(), n = nav.getBoundingClientRect(), c = canvas.getBoundingClientRect();
        return { width: v.width, navHeight: n.height, reserved: parseFloat(viewport.style.getPropertyValue('--hatg-navigation-height')), canvasTop: c.top, navBottom: n.bottom };
      }));
    }
    for (const s of samples) {
      assert.ok(Math.abs(s.reserved - s.navHeight) < 1, 'reserve the measured toolbar height');
      assert.ok(s.canvasTop >= s.navBottom, 'toolbar cannot overlap the canvas');
    }
    const errors = await page.evaluate(() => window.resizeErrors);
    console.log(JSON.stringify({ samples, errors }));
    assert.deepEqual(errors, [], 'native ResizeObserver delivery must remain error-free');
    const beforeStop = samples.at(-1).reserved;
    await page.evaluate(() => new Promise(resolve => {
      const nav = document.getElementById('graphNavigationBar');
      const sentinel = new ResizeObserver(() => {
        stopNavigation(); sentinel.disconnect(); resolve();
      });
      sentinel.observe(nav); nav.style.minHeight = '120px';
    }));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const reserved = () => page.evaluate(() => parseFloat(document.getElementById('hatgViewport').style.getPropertyValue('--hatg-navigation-height')));
    assert.equal(await reserved(), beforeStop, 'destroy cancels a pending measured layout write');
    await page.evaluate(() => new Promise(resolve => {
      document.getElementById('graphNavigationBar').style.minHeight = '160px';
      let frames = 0; const sample = () => ++frames === 5 ? resolve() : requestAnimationFrame(sample); requestAnimationFrame(sample);
    }));
    assert.equal(await reserved(), beforeStop, 'destroy stops measuring the toolbar');
    await page.evaluate(() => graph.destroy());
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
