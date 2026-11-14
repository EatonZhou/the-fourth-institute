'use strict';

// Real shared Society styles and viewport owner; no business data or API calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, webkit } = require(path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
const assets = path.resolve(__dirname, '../../../management_portal/static');
const evidence = process.env.WORKBENCH_VIEWPORT_EVIDENCE;
const engines = process.env.WORKBENCH_VIEWPORT_ENGINE === 'webkit' ? [webkit] : [chromium, webkit];
const pause = page => page.waitForTimeout(120);
const samples = [];
const html = `<!doctype html><html lang="zh-CN" data-portal-theme="light"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body class="portalBoardShell"><header class="portalSiteHeader" style="height:50px;padding:12px">The Fourth Institute　 /　 社群</header>
<main id="shell" class="consoleShell portalPageFrame portalWorkbenchPageLayout portalWorkbenchViewportShell">
<aside class="portalActivityRail">Ag</aside>
<div id="root" class="lineageWorkbenchRoot portalWorkbenchViewportRoot portalWorkbench">
  <div class="fixtureColumns">
    <section><h2>对话</h2><div class="fixtureMessages">窗口高度改变后，工作区应继续铺满导航栏下方的空间。</div><textarea aria-label="草稿"></textarea><button>发送</button></section>
    <section><h2>文件预览</h2><div class="fixtureDocument">文档与对话区域的底边应保持对齐。</div><footer>文档状态</footer></section>
  </div>
</div></main></body></html>`;
const fixtureStyle = `
.fixtureColumns { display:grid;grid-template-columns:1fr 1fr;height:100%;gap:6px; }
.fixtureColumns section { display:flex;flex-direction:column;min-height:0;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden; }
.fixtureColumns h2 { font-size:16px;margin:0;padding:14px;border-bottom:1px solid var(--border); }
.fixtureMessages,.fixtureDocument { flex:1;min-height:0;overflow:auto;padding:16px; }
.fixtureColumns textarea { resize:none;height:150px;margin:8px;border:1px solid var(--border);border-radius:6px; }
.fixtureColumns button { align-self:flex-end;margin:8px; }
.fixtureColumns footer { padding:8px;border-top:1px solid var(--border); }
`;

(async () => {
  for (const engine of engines) {
    const browser = await engine.launch({ headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent(html);
      for (const name of ['portal_components.css', 'portal_workbench.css', 'lineage_workbench.css', 'portal_design_system.css', 'portal_themes.css']) {
        await page.addStyleTag({ path: path.join(assets, name) });
      }
      await page.addStyleTag({ content: fixtureStyle });
      await page.addScriptTag({ path: path.join(assets, 'portal_workbench_viewport.js') });
      await page.evaluate(() => {
        window.viewportState = { destroyed: false };
        window.owner = PortalWorkbenchViewport.createViewport({ root: document.querySelector('#root'), options: {}, shellState: viewportState });
        owner.followViewport();
      });
      async function fillsViewport(label) {
        await pause(page);
        const box = await page.evaluate(() => {
          const shell = document.querySelector('#shell');
          const rect = shell.getBoundingClientRect();
          const panels = [...document.querySelectorAll('.fixtureColumns section')].map(el => el.getBoundingClientRect());
          const root = document.querySelector('#root').getBoundingClientRect();
          return { viewport: innerHeight, bottom: rect.bottom + scrollY, rootBottom: root.bottom,
            panelBottoms: panels.map(r => r.bottom), scrollHeight: document.documentElement.scrollHeight };
        });
        samples.push({ engine: engine.name(), label, ...box });
        assert.ok(Math.abs(box.viewport - box.bottom) <= 1, `${engine.name()} ${label}: bottom gap ${box.viewport - box.bottom}px`);
        assert.ok(box.scrollHeight <= box.viewport + 1, `${label}: unexpected document overflow`);
        for (const bottom of box.panelBottoms) assert.ok(Math.abs(bottom - box.rootBottom) <= 1, `${label}: pane does not fill root`);
      }

      await fillsViewport('initial');
      await page.getByRole('textbox').fill('未发送草稿');
      // CSS zoom exercises the coordinate conversion independently of browser UI.
      // It is not a claim to drive Safari's native Cmd-minus control.
      // Exclude body's unrelated 100vh minimum from this local sizing test.
      await page.evaluate(() => document.body.style.minHeight = '0');
      for (const zoom of [0.85, 1.25, 1]) {
        await page.evaluate(value => {
          document.body.style.zoom = String(value);
          // Native browser zoom notifies resize. A CSS zoom change alone
          // leaves ResizeObserver's unzoomed content boxes unchanged.
          window.dispatchEvent(new Event('resize'));
        }, zoom);
        await fillsViewport(`zoom-${zoom}`);
      }
      await page.evaluate(() => document.body.style.minHeight = '');
      for (const viewport of [{ width: 945, height: 900 }, { width: 625, height: 740 }, { width: 390, height: 640 }, { width: 1600, height: 1120 }]) {
        await page.setViewportSize(viewport);
        await fillsViewport(`resize-${viewport.width}`);
        if (evidence && engine === webkit && [625, 1600].includes(viewport.width)) {
          await page.screenshot({ path: path.join(evidence, `workbench-viewport-${viewport.width}.png`) });
        }
      }
      await page.evaluate(() => document.querySelector('header').style.height = '140px');
      await fillsViewport('header-grown');
      await page.evaluate(() => document.querySelector('header').style.height = '50px');
      await fillsViewport('header-restored');

      // Hidden documents may miss a resize; resume must repair stale geometry.
      for (const event of ['pageshow', 'visibilitychange']) {
        await page.evaluate(name => {
          document.querySelector('#shell').style.setProperty('--portal-workbench-shell-height', '500px');
          if (name === 'pageshow') window.dispatchEvent(new PageTransitionEvent(name, { persisted: true }));
          else document.dispatchEvent(new Event(name));
        }, event);
        await fillsViewport(event);
      }
      await page.evaluate(() => document.querySelector('#shell').style.display = 'none');
      await pause(page);
      assert.equal(await page.locator('#shell').isVisible(), false);
      await page.setViewportSize({ width: 1200, height: 800 });
      await page.evaluate(() => document.querySelector('#shell').style.display = '');
      await fillsViewport('revealed');
      assert.equal(await page.getByRole('textbox').inputValue(), '未发送草稿');

      // The same owner also supports a bounded workbench embedded in a page.
      await page.evaluate(() => {
        owner.destroyViewport();
        const shell = document.querySelector('#shell');
        shell.classList.remove('portalWorkbenchViewportShell', 'portalWorkbenchPageLayout');
        shell.style.padding = '0';
        const root = document.querySelector('#root');
        root.classList.remove('portalWorkbenchViewportRoot');
        window.owner = PortalWorkbenchViewport.createViewport({ root, options: { fitViewport: { bottom: 18, minHeight: 240, maxHeight: 980 } }, shellState: viewportState });
        owner.followViewport();
      });
      for (const height of [1200, 700, 200]) {
        await page.setViewportSize({ width: 1200, height }); await pause(page);
        const box = await page.locator('#root').boundingBox();
        const expected = Math.max(240, Math.min(980, height - box.y - 18));
        assert.ok(Math.abs(box.height - expected) <= 1, `fitViewport at ${height}`);
      }
      await page.evaluate(() => owner.destroyViewport());
      await page.setViewportSize({ width: 1200, height: 800 }); await pause(page);
      assert.equal(await page.locator('#root').evaluate(el => el.style.getPropertyValue('--portal-workbench-viewport-height')), '');
      assert.deepEqual(errors, []);
      console.log(`PASS ${engine.name()}: zoom, wide/narrow resize, delayed header, page restore, hidden/revealed, draft continuity, bounds and cleanup`);
    } finally { await browser.close(); }
  }
  if (evidence) fs.writeFileSync(path.join(evidence, 'workbench-viewport-results.json'), JSON.stringify(samples, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
