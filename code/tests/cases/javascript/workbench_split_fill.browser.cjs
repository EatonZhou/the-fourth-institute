'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engines = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { portalWorkbenchScripts, STATIC } = require('./portal_workbench_scripts.cjs');
const { babelParse } = require('./babel_parse.cjs');

(async () => {
  for (const engine of (process.env.WORKBENCH_FILL_ENGINE || 'chromium,webkit').split(',')) {
    const browser = await engines[engine].launch({ headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      await page.route('**/*', route => route.request().url() === 'http://workbench.fixture/'
        ? route.fulfill({ contentType: 'text/html', body: '<!doctype html><html data-portal-theme="liquid-glass"><body><main id="root" style="height:900px"></main></body></html>' })
        : route.abort());
      await page.goto('http://workbench.fixture/');
      for (const file of ['portal_components.css', 'portal_workbench.css', 'portal_themes.css']) {
        await page.addStyleTag({ path: path.join(STATIC, file) });
      }
      const state = fs.readFileSync(path.join(STATIC, 'portal_state.js'), 'utf8');
      const mod = babelParse(state, 'portal_state.js').program.body[0];
      await page.addScriptTag({ content: state.slice(mod.start, mod.end) });
      for (const file of ['portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js', 'portal_editor_open_service.js',
        'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts()]) {
        await page.addScriptTag({ path: path.join(STATIC, file) });
      }
      await page.evaluate(() => {
        PortalWorkbench.registerPanelPackage({ type: 'fill-fixture', version: 1,
          presentation: { title: 'Fixture', icon: 'panel' }, sizing: { minWidth: 100, minHeight: 100 },
          instances: 'multiple', capabilities: [], context: { channels: [] }, persistence: { schemaVersion: 1 },
          behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
          create: (_options, context) => {
            const element = document.createElement('section');
            element.textContent = context?.panelId || 'Panel content';
            return element;
          },
        });
        window.mount = (orientation, weights, restore = false) => {
          window.wb?.destroy();
          document.querySelector('#root').replaceChildren();
          if (!restore) window.saved = {};
          const { split, stack } = PortalWorkbench.model;
          const ids = weights.length === 2 ? ['navigation', 'graph'] : ['navigation', 'graph', 'preview'];
          window.wb = PortalWorkbench.create({ id: 'fill-fixture', root: '#root',
            store: { read: () => saved, replace: value => (window.saved = value) },
            initialEditors: [...ids, 'chat'].map(id => ({ id, title: id, type: 'fill-fixture', collapseToRail: id === 'navigation' })),
            defaultLayout: split(orientation, [split(orientation, ids.map(id => stack([id])), weights), stack(['chat'])], [1, 1]),
          });
        };
        window.gaps = () => [...document.querySelectorAll('.portalWorkbenchSplit')].map(split => {
          const axis = split.dataset.orientation === 'vertical' ? 'height' : 'width';
          const extent = element => element.getBoundingClientRect()[axis];
          return { axis, size: extent(split), unused: extent(split) - [...split.children].reduce((sum, child) => sum + extent(child), 0) };
        });
      });
      const check = async label => {
        const gaps = await page.evaluate(() => window.gaps());
        if (process.env.WORKBENCH_FILL_CAPTURE && (label === 'horizontal-0.23_0.77-collapsed' || label.startsWith('viewport-'))) {
          await page.screenshot({ path: path.join(process.env.WORKBENCH_FILL_CAPTURE, `${engine}-${label}.png`) });
          console.log(`${engine} ${label}: ${JSON.stringify(gaps)}`);
        }
        assert.ok(gaps.length === 2 && gaps.every(gap => Math.abs(gap.unused) < 2), `${engine} ${label}: ${JSON.stringify(gaps)}`);
      };
      for (const orientation of ['horizontal', 'vertical']) {
        for (const weights of [[0.23, 0.77], [0.1, 0.2, 0.3], [1, 2, 3]]) {
          const label = `${orientation}-${weights.join('_')}`;
          await page.evaluate(({ orientation, weights }) => mount(orientation, weights), { orientation, weights });
          await check(`${label}-initial`);
          await page.evaluate(() => wb.collapsePanel('navigation'));
          await check(`${label}-collapsed`);
          assert.deepEqual(await page.evaluate(() => wb.getLayout().children[0].weights), weights, 'collapse preserves saved proportions');
          // Reopen using persisted collapsed state: this exercises initial render as well as refresh.
          await page.evaluate(({ orientation, weights }) => mount(orientation, weights, true), { orientation, weights });
          assert.equal(await page.evaluate(() => wb.isPanelCollapsed('navigation')), true);
          await check(`${label}-restored`);
          if (weights.length === 3) {
            const inner = page.locator('.portalWorkbenchSplit .portalWorkbenchSplit');
            const handle = inner.locator(':scope > .portalWorkbenchSplitHandle:not([hidden])');
            const graph = inner.locator(':scope > .portalWorkbenchSplitChild').nth(1);
            const axis = orientation === 'horizontal' ? 'width' : 'height';
            const before = (await graph.boundingBox())[axis];
            await handle.focus();
            await page.keyboard.press(orientation === 'horizontal' ? 'ArrowRight' : 'ArrowDown');
            assert.ok(Math.abs((await graph.boundingBox())[axis] - before - 24) < 2, 'keyboard resize follows the requested distance');
            await check(`${label}-keyboard`);
            const box = await handle.boundingBox();
            await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
            await page.mouse.down();
            await page.mouse.move(box.x + box.width / 2 + (axis === 'width' ? -16 : 0), box.y + box.height / 2 + (axis === 'height' ? -16 : 0), { steps: 3 });
            await check(`${label}-dragging`);
            await page.mouse.up();
            assert.ok(Math.abs((await graph.boundingBox())[axis] - before - 8) < 2, 'pointer resize follows the requested distance');
            assert.equal(await page.evaluate(() => wb.getLayout().children[0].weights[0]), weights[0], 'resize preserves collapsed proportions');
            await page.evaluate(() => wb.collapsePanel('preview'));
            await check(`${label}-strip`);
            await page.evaluate(() => wb.expandPanel('preview'));
          }
          await page.evaluate(() => wb.expandPanel('navigation'));
          await check(`${label}-expanded`);
        }
      }
      await page.evaluate(() => mount('horizontal', [0.23, 0.77]));
      await page.evaluate(() => wb.collapsePanel('navigation'));
      for (const width of [945, 625, 1920]) {
        await page.setViewportSize({ width, height: 1000 });
        await check(`viewport-${width}`);
      }
      assert.deepEqual(errors, []);
      console.log(`${engine}: nested splits fill the viewport across fractional/integer weights, collapse, restore, resize and expand`);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
