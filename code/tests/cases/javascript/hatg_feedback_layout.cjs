'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { babelParse, traverse } = require('./babel_parse.cjs');
const portal = path.resolve(__dirname, '../../../management_portal');
const source = fs.readFileSync(path.join(portal, 'static/hatg_panel.js'), 'utf8');
const functions = [];
traverse(babelParse(source), { FunctionDeclaration(p) {
  if (['applyProgramMutation', 'showProgramMutationStatus'].includes(p.node.id?.name))
    functions.push(source.slice(p.node.start, p.node.end));
} });
const observerStart = source.indexOf('    const graphControlSizes = new Map(');
const observerEnd = source.indexOf('graphControlSizes.forEach((_, element) => graphControlObserver.observe(element));', observerStart);
assert.ok(observerStart > 0 && observerEnd > observerStart);
const observation = source.slice(observerStart, source.indexOf('\n', observerEnd));

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(portal, name)), contentType: 'text/css'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html><body><div class="portalHatgPanelComponent">'
        + fs.readFileSync(path.join(portal, 'hatg-panel.html'), 'utf8') + '</div></body></html>'});
    });
    await page.goto('http://hatg-feedback.fixture.invalid');
    for (const name of ['portal_design_system', 'portal_themes', 'hatg_panel'])
      await page.addStyleTag({url: '/static/' + name + '.css'});
    await page.addStyleTag({content: 'html,body {margin:0;height:100%} .portalHatgPanelComponent {height:100vh} #agentLineageShell {height:100%;display:block} #hatgPane {height:100%;display:flex;flex-direction:column}'});
    await page.evaluate(({functions, observation}) => {
      window.$id = id => document.getElementById(id);
      window.state = {lineage: {lineage_id: 'fixture'}};
      window.toastCalls = []; window.requestCount = 0;
      window.toast = (...args) => toastCalls.push(args);
      window.lineageEditQueue = {capture: id => id, isCurrent: () => true, enqueue: (_intent, fn) => fn()};
      window.programFeature = {beginMutation() {}, endMutation() {}};
      window.renderProgramInspector = window.renderLineageSurface = () => {};
      window.commitLineage = lineage => {state.lineage = lineage;};
      window.programMutationStatusTimer = 0; window.graphControlObserver = null;
      (0, eval)(functions.join('\n') + '\n' + observation);
    }, {functions, observation});
    await page.evaluate(async () => {
      for (let i = 1; i <= 5; i++) await applyProgramMutation(async () => {
        requestCount++; return {lineage: {lineage_id: 'fixture'}};
      }, `Wire ${i} saved`);
    });
    assert.equal(await page.locator('#programMutationStatus').innerText(), 'Wire 5 saved');
    assert.deepEqual(await page.evaluate(() => toastCalls), [], 'successful edits must not stack floating overlays');
    assert.equal(await page.evaluate(() => requestCount), 5, 'feedback never resubmits an edit');
    for (const width of [1440, 600, 420]) {
      await page.setViewportSize({width, height: 900});
      for (const inspectorOpen of [false, true]) {
        await page.evaluate(open => {
          $id('programInspectorPanel').hidden = !open;
          $id('programInspectorPanel').textContent = 'Effect inspector';
        }, inspectorOpen);
        await page.waitForFunction(() => {
          const bar = document.getElementById('graphNavigationBar');
          const measured = parseFloat(document.getElementById('hatgViewport').style.getPropertyValue('--hatg-navigation-height'));
          return Math.abs(measured - bar.getBoundingClientRect().height) < 1;
        });
        const geometry = await page.evaluate(() => Object.fromEntries(['hatgCanvas', 'graphNavigationBar', 'programMutationStatus', 'hatgViewport']
          .map(id => [id, document.getElementById(id).getBoundingClientRect().toJSON()])));
        assert.ok(geometry.hatgCanvas.top >= geometry.graphNavigationBar.bottom,
          `toolbar cannot cover titles while panning or focusing (${width}, inspector ${inspectorOpen}): ${JSON.stringify(geometry)}`);
        assert.ok(geometry.programMutationStatus.bottom <= geometry.hatgViewport.top,
          'success feedback occupies layout space above the canvas and palette');
        assert.ok(geometry.hatgCanvas.height > 100, 'wrapped navigation leaves a usable canvas');
      }
    }
    await page.evaluate(async () => {
      try { await applyProgramMutation(async () => {throw Error('fixture write rejected');}, 'Should not appear'); }
      catch (_) {}
    });
    assert.equal(await page.locator('#programMutationStatus').innerText(), 'Wire 5 saved', 'failure cannot replace last success');
    const notices = await page.evaluate(() => toastCalls);
    assert.equal(notices.length, 1); assert.equal(notices[0][1], 'error', 'write errors remain prominent');
    assert.deepEqual(errors, []);
    console.log('HATG feedback and wrapped navigation reserve space at desktop and narrow widths');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
