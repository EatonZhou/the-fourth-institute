const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const permanent of [false, true]) {
      const page = await browser.newPage();
      let recovered = false;
      await page.route('**/*', async route => {
        const name = new URL(route.request().url()).pathname;
        if (name === '/static/hatg_seeds.js' && !recovered) {
          const attempts = await page.evaluate(() => window.attempts['/static/hatg_seeds.js'] || 0);
          if (permanent || attempts < 2) return route.abort('failed');
        }
        if (name.startsWith('/static/')) return route.fulfill({
          body: fs.readFileSync(path.join(root, name.slice(8))),
          contentType: name.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8',
        });
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>' });
      });
      await page.goto('http://hatg-load.fixture.invalid');
      await page.evaluate(() => {
        window.attempts = {};
        window.loaded = [];
        window.failures = [];
        new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
          if (node.tagName !== 'SCRIPT' || !node.src) return;
          const name = new URL(node.src).pathname;
          attempts[name] = (attempts[name] || 0) + 1;
        }))).observe(document.head, { childList: true });
        document.addEventListener('load', event => {
          if (event.target.tagName === 'SCRIPT' && event.target.src) loaded.push(new URL(event.target.src).pathname);
        }, true);
        document.addEventListener('error', event => {
          if (event.target.tagName === 'SCRIPT' && event.target.src) failures.push(new URL(event.target.src).pathname);
        }, true);
      });
      for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_hatg_loader']) {
        await page.addScriptTag({ url: `/static/${name}.js` });
      }
      const first = await page.evaluate(async () => {
        const a = PortalHatgLoader.load(), b = PortalHatgLoader.load();
        const shared = a === b;
        try { const panel = await a; return { shared, ready: typeof panel.mountHatgPanel === 'function' }; }
        catch (error) { return { shared, error: error.message }; }
      });
      assert.equal(first.shared, true, 'concurrent opens share one ordered load');
      assert.equal(await page.evaluate(() => attempts['/static/hatg_seeds.js']), 2, 'one bounded retry');
      if (permanent) {
        assert.match(first.error, /无法加载 \/static\/hatg_seeds\.js/);
        recovered = true;
        assert.equal(await page.evaluate(async () => typeof (await PortalHatgLoader.load()).mountHatgPanel), 'function', 'a later open recovers');
      } else assert.equal(first.ready, true, 'a transient failure must not abort opening');
      const state = await page.evaluate(() => ({ loaded, attempts, failures, failedElements: [...document.scripts].filter(s => s.src.endsWith('/hatg_seeds.js')).length }));
      assert.equal(state.failedElements, 1, 'failed script elements are removed');
      assert.equal(state.failures.length, permanent ? 2 : 1);
      assert.equal(new Set(state.loaded).size, state.loaded.length, 'successful modules execute once');
      assert.ok(state.loaded.indexOf('/static/hatg_seeds.js') < state.loaded.indexOf('/static/hatg_panel.js'), 'dependent modules stay ordered');
      console.log(JSON.stringify({ permanent, shared: first.shared, seedAttempts: state.attempts['/static/hatg_seeds.js'], recovered: true }));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
