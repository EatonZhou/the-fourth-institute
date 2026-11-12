'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { portalWorkbenchScripts, STATIC } = require('./portal_workbench_scripts.cjs');
const { babelParse } = require('./babel_parse.cjs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/*', route => route.request().url() === 'http://workbench.fixture/'
      ? route.fulfill({ contentType: 'text/html', body: '<!doctype html><body><main id="root" style="height:800px"><section id="nested" style="height:100%"></section></main></body>' })
      : route.abort());
    await page.goto('http://workbench.fixture/');
    await page.addStyleTag({ path: path.join(STATIC, 'portal_workbench.css') });
    // Hold tab strips revealed so this test isolates native drag, not hover animation timing.
    await page.addStyleTag({ content: ':root{--accent:#36c;--panel:#fff;--text:#123;--border:#ccc} .portalWorkbenchTabs{opacity:1!important;pointer-events:auto!important;transform:translate(-50%,0)!important;transition:none!important}' });
    const state = fs.readFileSync(path.join(STATIC, 'portal_state.js'), 'utf8');
    const mod = babelParse(state, 'portal_state.js').program.body[0];
    await page.addScriptTag({ content: state.slice(mod.start, mod.end) });
    for (const file of ['portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts()]) {
      await page.addScriptTag({ path: path.join(STATIC, file) });
    }
    await page.evaluate(() => {
      PortalWorkbench.registerPanelPackage({ type: 'fixture-drag', version: 1,
        presentation: { title: 'Fixture', icon: 'panel' }, sizing: { minWidth: 100, minHeight: 100 },
        instances: 'multiple', capabilities: [], context: { channels: ['*'] }, persistence: { schemaVersion: 1 },
        behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
        create: options => { const element = options.source ? document.querySelector(options.source) : document.createElement('section');
          if (!options.source) element.textContent = 'Synthetic content'; return element; },
      });
      const { stack, split } = PortalWorkbench.model;
      const store = () => ({ read: () => ({}), replace: value => value });
      window.outer = PortalWorkbench.create({ id: 'outer', root: '#root', store: store(),
        initialEditors: [{ id: 'nested', type: 'fixture-drag', options: { source: '#nested' } }], defaultLayout: stack(['nested']) });
      window.inner = PortalWorkbench.create({ id: 'inner', root: '#nested', store: store(),
        initialEditors: ['a', 'b'].map(id => ({ id, title: id, type: 'fixture-drag' })),
        defaultLayout: split('vertical', [stack(['a']), stack(['b'])]) });
      window.beforeOuter = JSON.stringify(outer.getLayout());
      window.dragEvents = [];
      for (const name of ['dragstart', 'dragend', 'dragover', 'drop', 'blur']) window.addEventListener(name, event => {
        dragEvents.push({ name, target: event.target?.tagName, tab: event.target?.dataset?.portalWorkbenchTab,
          workbench: event.target?.closest?.('[data-portal-workbench-id]')?.dataset.portalWorkbenchId,
          types: event.dataTransfer ? Array.from(event.dataTransfer.types) : [] });
      }, true);
      document.querySelectorAll('.portalWorkbenchStack,.portalWorkbenchTabs').forEach(element => {
        for (const name of ['dragover', 'dragleave']) element.addEventListener(name, event => dragEvents.push({
          name: 'after-' + name, current: element.className, prevented: event.defaultPrevented,
          zone: element.dataset.portalWorkbenchDropZone, related: event.relatedTarget?.tagName,
        }));
      });
    });
    const a = page.locator('[data-portal-workbench-tab="a"]');
    const b = page.locator('[data-portal-workbench-tab="b"]');
    const source = await a.boundingBox();
    let target = await b.boundingBox();
    assert.ok(source && target);
    await page.mouse.move(source.x + 12, source.y + source.height / 2);
    await page.mouse.down();
    await page.mouse.move(source.x + 12, source.y + source.height + 25, { steps: 5 });
    target = await b.boundingBox();
    await page.mouse.move(target.x + 12, target.y + target.height / 2, { steps: 12 });
    await page.mouse.move(target.x + 18, target.y + target.height / 2);
    // Chromium first enters the child control, then delivers dragover on the next move.
    await page.mouse.move(target.x + 18, target.y + target.height / 2);
    console.log(JSON.stringify({ source, target, observed: await page.evaluate(() => ({ events: dragEvents.slice(-16), dragging: document.body.classList.contains('draggingWorkbenchTab') })) }));
    if (await page.locator('[data-portal-workbench-drop-zone]').count() !== 1) await page.screenshot({ path: '/tmp/workbench-drag-debug.png' });
    assert.equal(await page.locator('[data-portal-workbench-drop-zone]').count(), 1, 'native drag has one preview');
    await page.mouse.up();
    assert.deepEqual(await page.evaluate(() => ({
      overlays: document.querySelectorAll('[data-portal-workbench-drop-zone]').length,
      dragging: document.body.classList.contains('draggingWorkbenchTab'),
      sameStack: document.querySelector('[data-portal-workbench-tab="a"]').closest('.portalWorkbenchStack') === document.querySelector('[data-portal-workbench-tab="b"]').closest('.portalWorkbenchStack'),
      outerUnchanged: JSON.stringify(outer.getLayout()) === beforeOuter,
    })), { overlays: 0, dragging: false, sameStack: true, outerUnchanged: true });
    await page.evaluate(() => { inner.destroy(); outer.destroy(); });
    assert.deepEqual(errors, []);
    console.log('Chromium native mouse drag: one preview; move committed; preview/drag state cleared; outer layout unchanged.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
