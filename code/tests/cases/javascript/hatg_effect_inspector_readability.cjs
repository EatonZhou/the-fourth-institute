'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const portal = path.resolve(__dirname, '../../../management_portal');
const {HATG_PROGRAM_INSPECTOR_SCRIPTS} = require('./hatg_program_inspector_scripts.cjs');
const {HATG_PANEL_STYLES} = require('./hatg_panel_styles.cjs');
const {readEffectDiagnosticsReady} = require('../../../management_portal/test_framework/tau_effect_diagnostics_evidence');
// The production program inspector draws a saved Effect and reads its
// diagnostics; only the inspector's listed dependencies are provided.
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
    for (const name of ['portal_design_system', 'portal_themes', ...HATG_PANEL_STYLES]) await page.addStyleTag({url: '/static/' + name + '.css'});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'lineage_element_fields', ...HATG_PROGRAM_INSPECTOR_SCRIPTS]) await page.addScriptTag({url: '/static/' + name + '.js'});
    await page.addStyleTag({content: 'html,body{margin:0;height:100%}.portalHatgPanelComponent{height:100vh}#agentLineageShell{height:100%;display:block}#hatgPane{height:100%;display:flex;flex-direction:column}'});
    await page.evaluate(() => {
      // The server boundary: the script editor's instruction and user catalogs are empty.
      window.PortalInstructionClient = {execute: async name => (
        name === 'ListInstructions' ? {instructions: []} : name === 'ListUsers' ? {users: []} : {})};
      const panel = document.getElementById('programInspectorPanel');
      const component = {id: 'effect', kind: 'effect', title: 'Saved Effect',
        config: {script: 'DeliverMessage resident_id="${endpoint.target}" text="' + 'long-script-value'.repeat(45) + '"'}};
      const state = {lineage: {lineage_id: 'fixture', program: {components: [component], wires: []}},
        selectedProgramObjectId: component.id, programMutationInFlight: false};
      let diagnosticsRead = Promise.resolve();
      const provided = {
        state, esc: PortalUIPrimitives.escapeHtml, toast: () => {}, definitionRevisions: new WeakMap(),
        displayProgramComponentById: id => (id === component.id ? component : null),
        displayProgramWireById: () => null, refreshFunctionBlockMenu: () => {},
        programComponentSwitchHtml: () => '', programResourceAddressHtml: () => '',
        programExecutionExplanationHtml: () => '', programEffectRuntime: () => ({}),
        executeHatgInstruction: () => (diagnosticsRead = window.deferDiagnostics
          ? new Promise((resolve,reject)=>{window.finishDiagnostics=()=>resolve({diagnostics:window.diagnosticItems||[]});window.failDiagnostics=reject;})
          : Promise.resolve({diagnostics: window.diagnosticItems || []})),
      };
      const module = window.PortalHatgProgramInspector;
      const inspector = module.createProgramInspector(Object.fromEntries(module.dependencies.map(name => [
        name, Object.hasOwn(provided, name) ? provided[name] : () => { throw new Error(`Unexpected use of ${name}`); }])));
      window.inspectSavedScript = async () => {
        inspector.renderProgramInspector({panel, objectId: component.id});
        await diagnosticsRead.catch(() => {}); // The native renderer presents transport failure as unavailable.
        await new Promise(resolve => setTimeout(resolve));
      };
    });
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
      assert.equal((await readEffectDiagnosticsReady(page.locator('#programInspectorPanel'),{componentId:'effect'})).ready,true);
      assert.ok(!result.scriptValue.includes('\n'), 'soft wrapping never changes the submitted script');
    }
    await page.evaluate(() => {window.diagnosticItems = [{item_id: 'effect', level: 'error', code: 'invalid-script', message: 'Saved script rejected'}]; return inspectSavedScript();});
    assert.equal(await page.locator('[data-effect-diagnostics]').getAttribute('data-effect-diagnostics-state'), 'error');
    assert.ok((await page.locator('[data-effect-diagnostics]').innerText()).includes('Saved script rejected'));
    const panel=page.locator('#programInspectorPanel');
    assert.equal((await readEffectDiagnosticsReady(panel,{componentId:'effect'})).ready,false);
    assert.equal((await readEffectDiagnosticsReady(panel,{componentId:'other'})).ready,false);
    await page.evaluate(()=>{window.deferDiagnostics=true;window.pendingInspection=inspectSavedScript();});
    const loading=await readEffectDiagnosticsReady(panel,{componentId:'effect'});
    assert.equal(loading.candidates[0].state,'loading');assert.equal(loading.ready,false);
    // Exercise the absent attribute during replacement, which the old negative
    // predicate accepted, then let the real renderer finish its original request.
    await page.locator('[data-effect-diagnostics]').evaluate(node=>node.removeAttribute('data-effect-diagnostics-state'));
    assert.equal((await readEffectDiagnosticsReady(panel,{componentId:'effect'})).ready,false);
    await page.evaluate(async()=>{window.diagnosticItems=[{item_id:'effect',level:'warning',message:'Review'}];finishDiagnostics();await pendingInspection;});
    assert.equal((await readEffectDiagnosticsReady(panel,{componentId:'effect'})).ready,true);
    await page.evaluate(()=>{window.pendingInspection=inspectSavedScript();});
    await page.evaluate(async()=>{failDiagnostics(new Error('Fixture service unavailable'));await pendingInspection;});
    const unavailable=await readEffectDiagnosticsReady(panel,{componentId:'effect'});
    assert.equal(unavailable.candidates[0].state,'unavailable');assert.equal(unavailable.ready,false);
    assert.deepEqual(errors, []);
    console.log('Effect diagnostics stay visible while scrolling at wide/narrow widths; long script wraps without changing its value');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
