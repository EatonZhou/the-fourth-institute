'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {babelParse, traverse} = require('./babel_parse.cjs');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const portal = path.resolve(__dirname, '../../../management_portal');
const source = fs.readFileSync(path.join(portal, 'static/hatg_panel.js'), 'utf8');
let markup, diagnostics;
traverse(babelParse(source), {
  AssignmentExpression(p) {
    if (p.node.left.property?.name === 'innerHTML') {
      const rhs = source.slice(p.node.right.start, p.node.right.end);
      if (rhs.includes('data-effect-diagnostics') && rhs.includes('effectFieldsHtml')) markup = rhs;
    }
  },
  FunctionDeclaration(p) {
    if (p.node.id?.name === 'renderEffectDiagnostics') diagnostics = source.slice(p.node.start, p.node.end);
  },
});
assert.ok(markup && diagnostics);
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(portal, name)), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><div class="portalHatgPanelComponent">' + fs.readFileSync(path.join(portal, 'hatg-panel.html'), 'utf8') + '</div>'});
    });
    await page.goto('http://effect-inspector.fixture.invalid');
    for (const name of ['portal_design_system', 'portal_themes', 'hatg_panel']) await page.addStyleTag({url: '/static/' + name + '.css'});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'lineage_element_fields']) await page.addScriptTag({url: '/static/' + name + '.js'});
    await page.addStyleTag({content: 'html,body{margin:0;height:100%}.portalHatgPanelComponent{height:100vh}#agentLineageShell{height:100%;display:block}#hatgPane{height:100%;display:flex;flex-direction:column}'});
    await page.evaluate(({markup, diagnostics}) => {
      const panel = document.getElementById('programInspectorPanel');
      panel.hidden = false; panel.dataset.componentId = 'effect';
      const component = {id: 'effect', title: 'Saved Effect'};
      const config = {script: 'DeliverMessage resident_id="${endpoint.target}" text="' + 'long-script-value'.repeat(45) + '"'};
      const readonly = false, esc = PortalUIPrimitives.escapeHtml;
      const programComponentSwitchHtml = () => '', programResourceAddressHtml = () => '';
      const programExecutionExplanationHtml = () => '', programEffectRuntimeHtml = () => '';
      panel.innerHTML = eval(markup);
      const state = {lineage: {lineage_id: 'fixture'}};
      const executeHatgInstruction = async () => ({diagnostics: window.diagnosticItems || []});
      const renderDiagnostics = eval('(' + diagnostics + ')');
      window.inspectSavedScript = () => renderDiagnostics(panel, component.id);
    }, {markup, diagnostics});
    for (const width of [1440, 600, 420]) {
      await page.setViewportSize({width, height: 900});
      await page.evaluate(() => inspectSavedScript());
      const script = page.locator('[data-effect-script]');
      await script.scrollIntoViewIfNeeded();
      await page.evaluate(() => {
        const form = document.querySelector('#programInspectorPanel form');
        form.scrollTop = form.scrollHeight;
      });
      const result = await page.evaluate(() => {
        const p = document.getElementById('programInspectorPanel'), d = p.querySelector('[data-effect-diagnostics]'), s = p.querySelector('[data-effect-script]');
        const pr = p.getBoundingClientRect(), dr = d.getBoundingClientRect();
        return {state: d.dataset.effectDiagnosticsState, visible: dr.top >= pr.top && dr.bottom <= pr.bottom, outsideForm: !d.closest('form'), scriptFits: s.scrollWidth <= s.clientWidth + 1, scriptValue: s.value, formScrolled: p.querySelector('form').scrollTop > 0};
      });
      assert.equal(result.state, 'ok'); assert.ok(result.visible && result.outsideForm && result.scriptFits && result.formScrolled, JSON.stringify({width, ...result}));
      assert.ok(!result.scriptValue.includes('\n'), 'soft wrapping never changes the submitted script');
    }
    await page.evaluate(() => {window.diagnosticItems = [{item_id: 'effect', level: 'error', code: 'invalid-script', message: 'Saved script rejected'}]; return inspectSavedScript();});
    assert.equal(await page.locator('[data-effect-diagnostics]').getAttribute('data-effect-diagnostics-state'), 'error');
    assert.ok((await page.locator('[data-effect-diagnostics]').innerText()).includes('Saved script rejected'));
    assert.deepEqual(errors, []);
    console.log('Effect diagnostics stay visible while scrolling at wide/narrow widths; long script wraps without changing its value');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
