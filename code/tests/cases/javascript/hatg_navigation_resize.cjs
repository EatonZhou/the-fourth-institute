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
    await page.setContent('<!doctype html><html data-portal-theme="auto"><body><div class="portalHatgPanelComponent" id="fixture"></div></body></html>');
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
    for (const name of ['portal_design_system', 'portal_themes', 'portal_components', ...HATG_PANEL_STYLES])
      await page.addStyleTag({ path: path.join(root, 'static', name + '.css') });
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_form_controls', 'projection_libraries/portal-graph/1.0.0/geometry', 'vendor/d3/d3.v7.9.0.min', ...LINEAGE_GRAPH_SCRIPTS, 'hatg_toolbar'])
      await page.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    await page.evaluate(() => {
      window.graph = PortalHatgGraph.createGraph({ root: '#hatgCanvas', world: '#world', edgeLayer: '#edges', effectLayer: '#effects', labelLayer: '#labels', nodeLayer: '#nodes' });
      graph.render({ nodes: [], edges: [] });
      window.stopNavigation = PortalHatgToolbar.observeNavigationHeight(
        document.getElementById('hatgViewport'), document.getElementById('graphNavigationBar'));
    });
    const samples = [];
    for (const width of [1000, 850, 340, 700]) {
     for (const expanded of [false, true]) {
      await page.evaluate(width => { document.getElementById('hatgViewport').style.width = width + 'px'; }, width);
      await page.evaluate(expanded => { document.getElementById('graphNodeSearchCluster').dataset.expanded = String(expanded); }, expanded);
      await page.evaluate(() => new Promise(resolve => {
        let frames = 0; const sample = () => ++frames === 5 ? resolve() : requestAnimationFrame(sample); requestAnimationFrame(sample);
      }));
      samples.push(await page.evaluate(() => {
        const viewport = document.getElementById('hatgViewport'), nav = document.getElementById('graphNavigationBar'), canvas = document.getElementById('hatgCanvas');
        const v = viewport.getBoundingClientRect(), n = nav.getBoundingClientRect(), c = canvas.getBoundingClientRect();
        const groups = [...nav.querySelectorAll(':scope > .graphViewportCluster, :scope > .graphNavigator')]
          .map(group => ({ rect: group.getBoundingClientRect().toJSON(), input: getComputedStyle(group).pointerEvents,
            border: getComputedStyle(group).borderTopWidth, background: getComputedStyle(group).backgroundColor,
            shadow: getComputedStyle(group).boxShadow }));
        return { width: v.width, navHeight: n.height, reserved: parseFloat(viewport.style.getPropertyValue('--hatg-navigation-height')),
          canvasTop: c.top, viewportTop: v.top, viewportLeft: v.left, viewportRight: v.right,
          barInput: getComputedStyle(nav).pointerEvents, barBorder: getComputedStyle(nav).borderTopWidth,
          barBackground: getComputedStyle(nav).backgroundColor, barRect: n.toJSON(),
          searchVisible: document.getElementById('graphNodeSearchInput').getClientRects().length > 0,
          canvasHit: !!document.elementFromPoint(v.left + v.width / 2, n.bottom + 12)?.closest('#hatgCanvas'),
          controls: [...nav.querySelectorAll('button, input, select')].filter(e => e.getClientRects().length)
            .map(e => ({ id: e.id, rect: e.getBoundingClientRect().toJSON() })), groups };
      }));
      assert.equal(samples.at(-1).searchVisible, expanded, 'search opens inside the shared toolbar');
      if (process.env.HATG_NAVIGATION_ARTIFACT_DIR) {
        fs.mkdirSync(process.env.HATG_NAVIGATION_ARTIFACT_DIR, { recursive: true });
        await page.locator('#graphNavigationBar').screenshot({ path: path.join(process.env.HATG_NAVIGATION_ARTIFACT_DIR, `toolbar-${width}-${expanded}.png`) });
      }
     }
    }
    for (const s of samples) {
      assert.ok(Math.abs(s.reserved - s.navHeight) < 1, 'keep the measured toolbar height for overlays below it');
      assert.ok(Math.abs(s.canvasTop - s.viewportTop) < 1, 'the canvas fills the viewport under the floating toolbar');
      assert.equal(s.barInput, 'auto', 'the shared toolbar owns its entire input surface');
      assert.equal(s.barBorder, '1px', 'one outer border encloses all canvas tools');
      assert.notEqual(s.barBackground, 'rgba(0, 0, 0, 0)', 'one visible background joins the controls');
      assert.ok(s.canvasHit, 'the canvas below the toolbar still receives input');
      for (const group of s.groups) {
        assert.notEqual(group.input, 'none', 'controls stay operable');
        assert.equal(group.border, '0px', 'internal groups do not form separate floating boxes');
        assert.equal(group.background, 'rgba(0, 0, 0, 0)');
        assert.equal(group.shadow, 'none');
        assert.ok(group.rect.left >= s.viewportLeft - 1 && group.rect.right <= s.viewportRight + 1, `groups stay inside the canvas (${s.width})`);
      }
      for (const control of s.controls) assert.ok(control.rect.left >= s.barRect.left && control.rect.right <= s.barRect.right + 1
        && control.rect.top >= s.barRect.top && control.rect.bottom <= s.barRect.bottom + 1, `${control.id} stays inside the shared toolbar (${s.width})`);
    }
    const errors = await page.evaluate(() => window.resizeErrors);
    console.log(JSON.stringify({ samples, errors }));
    if (process.env.HATG_NAVIGATION_ARTIFACT_DIR) fs.writeFileSync(path.join(process.env.HATG_NAVIGATION_ARTIFACT_DIR, 'geometry.json'), JSON.stringify({ samples, errors }, null, 2));
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
