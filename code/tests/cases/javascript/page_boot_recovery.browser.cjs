'use strict';
// Real browser loading/error events exercise the dependency-free boot owner.
// The document and its tiny application are isolated; no business requests run.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
const boot = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_page_boot.js'), 'utf8');
const evidence = process.env.BOOT_RECOVERY_EVIDENCE;
const origin = 'http://portal-boot.test';
const config = { assets: ['/static/refs.js', '/static/app.js'], checks: [{ id: 'workspace', label: 'Society' }], navigation: true };
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><script>(${boot})(${JSON.stringify(config)})</script></head>
<body style="margin:0;background:#f5f7f9;font:16px system-ui;color:#172033">
<header data-portal-site-header style="padding:20px;border-bottom:1px solid #ddd">The Fourth Institute　　资源　 /　 项目　 /　 社群</header>
<main id="workspace" style="padding:24px"><textarea aria-label="Draft"></textarea></main>
<script src="/static/refs.js"></script><script src="/static/app.js"></script></body></html>`;
const app = 'PortalPageBoot.ready(document.querySelector("header")); if (!window.refs) throw new Error("refs missing"); PortalPageBoot.ready(document.querySelector("main"));';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const browser = await chromium.launch({ headless: true });
  let context;
  try {
    async function scenario(name, options = {}) {
      if (context) await context.close();
      context = await browser.newContext({ viewport: { width: 1000, height: 720 } });
      const page = await context.newPage();
      let documents = 0;
      let refs = 0;
      let fail = options.fail || 0;
      await page.route(`${origin}/**`, async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/society') {
          documents += 1;
          return route.fulfill({ contentType: 'text/html', body: html });
        }
        if (url.pathname === '/static/refs.js') {
          refs += 1;
          if (options.delay) await pause(options.delay);
          if (options.offline && refs === 1) await context.setOffline(true);
          if (refs <= fail) return route.abort('connectionreset');
          return route.fulfill({ contentType: 'application/javascript', body: 'window.refs = true;' });
        }
        if (url.pathname === '/static/app.js') return route.fulfill({ contentType: 'application/javascript', body: app });
        return route.fulfill({ status: 204, body: '' });
      });
      const url = `${origin}/society?scenario=${name}#selected`;
      const navigate = () => page.goto(url, { waitUntil: 'domcontentloaded' });
      return { page, url, navigate, documents: () => documents, recover: () => { fail = 0; } };
    }
    const status = (page, expected) => page.waitForFunction(value => window.PortalPageBoot?.status === value, expected);
    const budget = page => page.evaluate(() => sessionStorage.getItem(`portal.page-boot.retry.v1:${location.href}`));
    const notice = page => page.locator('#portalPageBootStatus');

    // Fast navigation never flashes a notice; slow success removes it without reload.
    let s = await scenario('fast');
    await s.navigate(); await status(s.page, 'ready');
    assert.equal(await notice(s.page).count(), 0);
    s = await scenario('slow', { delay: 1400 });
    let navigating = s.navigate();
    await notice(s.page).waitFor();
    assert.match(await notice(s.page).innerText(), /正在加载页面/);
    assert.equal(await s.page.locator('[role="alert"]').count(), 0);
    assert.equal(await notice(s.page).locator('button').isVisible(), false);
    await navigating; await status(s.page, 'ready');
    assert.equal(s.documents(), 1);
    assert.equal(await notice(s.page).count(), 0);

    // One lost dependency causes a neutral loading notice, then a healthy document.
    s = await scenario('transient', { fail: 1 });
    await s.navigate(); await status(s.page, 'retrying');
    assert.equal(await notice(s.page).locator('details').isVisible(), false);
    assert.doesNotMatch(await notice(s.page).innerText(), /refs\.js|脚本|失败|未能|1\/2/);
    if (evidence) await s.page.screenshot({ path: path.join(evidence, 'boot-retry-wide.png') });
    await status(s.page, 'ready');
    assert.equal(s.documents(), 2);
    assert.equal(s.page.url(), s.url);
    assert.equal(await budget(s.page), null);
    assert.equal(await notice(s.page).count(), 0);

    // Persistent failure exhausts the same budget, exposes diagnostics only on demand.
    s = await scenario('persistent', { fail: Infinity });
    await s.navigate(); await status(s.page, 'error');
    assert.equal(s.documents(), 3);
    assert.equal(await budget(s.page), '2');
    assert.match(await notice(s.page).innerText(), /页面暂时未能打开/);
    assert.doesNotMatch(await notice(s.page).innerText(), /refs\.js/);
    await s.page.setViewportSize({ width: 390, height: 700 });
    const box = await notice(s.page).boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 390);
    if (evidence) await s.page.screenshot({ path: path.join(evidence, 'boot-retry-narrow.png') });
    await s.page.addStyleTag({ path: path.resolve(__dirname, '../../../management_portal/static/portal_themes.css') });
    await s.page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
    assert.notEqual(await notice(s.page).evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
    await notice(s.page).locator('summary').click();
    assert.match(await notice(s.page).innerText(), /refs\.js/);
    await pause(2200);
    assert.equal(s.documents(), 3, 'must not loop after exhaustion');
    s.recover();
    await notice(s.page).locator('button').focus();
    await s.page.keyboard.press('Enter');
    await status(s.page, 'ready');
    assert.equal(s.documents(), 4);

    // Offline time spends no retry; online resumes only while safe to reload.
    s = await scenario('offline', { fail: 1, offline: true });
    await s.navigate(); await status(s.page, 'waiting-online');
    assert.match(await notice(s.page).innerText(), /等待网络连接/);
    await pause(1200);
    assert.equal(s.documents(), 1);
    assert.equal(await budget(s.page), null);
    await context.setOffline(false);
    await status(s.page, 'ready');
    assert.equal(s.documents(), 2);

    s = await scenario('draft', { fail: 1, offline: true });
    await s.navigate(); await status(s.page, 'waiting-online');
    await s.page.getByRole('textbox').fill('Keep my unsaved draft');
    await status(s.page, 'error');
    await context.setOffline(false); await pause(1200);
    assert.equal(s.documents(), 1);
    assert.match(await notice(s.page).innerText(), /请先保留未保存/);
    assert.equal(await s.page.getByRole('textbox').inputValue(), 'Keep my unsaved draft');

    // Blocked sessionStorage cannot turn a failure into an unbounded reload.
    s = await scenario('storage', { fail: Infinity });
    await s.page.addInitScript(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('blocked'); } }));
    await s.navigate(); await status(s.page, 'error');
    assert.equal(s.documents(), 1);

    // A silent stall still has a deadline; BFCache-style departure cancels retries.
    s = await scenario('deadline');
    await s.page.route(`${origin}/static/app.js`, route => route.fulfill({ contentType: 'application/javascript', body: '' }));
    await s.page.clock.install();
    await s.navigate();
    await s.page.clock.runFor(90001); await status(s.page, 'retrying');
    await s.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
    await s.page.clock.runFor(5000);
    assert.equal(s.documents(), 1);
    await s.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await status(s.page, 'error');
    console.log('PASS: delayed/successful loads, neutral recovery, bounded retries, diagnostics, offline resume, draft safety, blocked storage, deadline and page lifecycle');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
