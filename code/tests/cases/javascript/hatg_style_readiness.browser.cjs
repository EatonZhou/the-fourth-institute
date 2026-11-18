const assert = require('node:assert/strict');
const path = require('node:path');
const {run, portal} = require('./hatg_reference_family.browser.cjs');
const nav = '/static/hatg_panel_navigation.css';
const library = '/static/hatg_panel_library.css';

async function layout(page) {
  const state = await page.evaluate(() => {
    const links = [...document.querySelectorAll('link[rel="stylesheet"]')];
    const names = links.map(link => new URL(link.href).pathname);
    const required = names.filter(name => name.startsWith('/static/hatg_panel'));
    return {required, order: names.indexOf('/static/hatg_panel_navigation.css') < names.indexOf('/static/hatg_panel_library.css'),
      ready: links.filter(link => required.includes(new URL(link.href).pathname)).every(link => !!link.sheet),
      toolbox: getComputedStyle(document.getElementById('hatgToolbox')).display,
      nav: getComputedStyle(document.getElementById('graphNavigationBar')).position};
  });
  assert.equal(state.required.length, 10);
  assert.equal(new Set(state.required).size, 10, 'failed links removed; cascade has one copy per file');
  assert.equal(state.order, true);
  assert.equal(state.ready, true);
  assert.equal(state.toolbox, 'none');
  assert.equal(state.nav, 'absolute');
  await page.locator('#hatgToolboxToggle').hover();
  assert.equal(await page.locator('#hatgToolbox').isVisible(), true);
  await page.mouse.move(1000, 100);
  await page.locator('#hatgToolbox').waitFor({state: 'hidden'});
}

(async () => {
  // The host declared both files, but one failed and one has not finished.
  // The panel must stay behind its loading state until the CSS is applied.
  let attempts = 0, releaseLibrary;
  const gate = new Promise(resolve => {releaseLibrary = resolve;});
  let loadingLibrary = false;
  await run({
    route: async (route, url) => {
      if (url.pathname === nav && ++attempts <= 2) {await route.abort(); return true;}
      if (url.pathname === library) {
        if (!loadingLibrary) {loadingLibrary = true; await route.abort(); return true;}
        await gate;
        await route.fulfill({path: path.join(portal, library)}); return true;
      }
      return false;
    },
    beforeReady: async page => {
      await page.waitForFunction(() => document.querySelectorAll('link[href$="hatg_panel_library.css"]').length === 2);
      assert.equal(await page.locator('#hatgPane').count(), 0, 'never mount a partially styled canvas');
      assert.equal(await page.locator('.portalHatgPanelComponent').getAttribute('aria-busy'), 'true');
      releaseLibrary();
    },
    afterReady: async page => {
      assert.equal(attempts, 3, 'failed host request, mount attempt, bounded retry');
      const before = attempts;
      await page.evaluate(() => Promise.all([PortalUIPrimitives.loadAsset('/static/hatg_panel_navigation.css'), PortalUIPrimitives.loadAsset('/static/hatg_panel_navigation.css')]));
      assert.equal(attempts, before, 'ready styles are reused');
      await layout(page);
    },
  });
  console.log('PASS delayed styles, bounded recovery, cascade order, toolbox and navigation');

  let fail = true;
  await run({
    route: async (route, url) => {
      if (fail && url.pathname === nav) {await route.abort(); return true;}
      return false;
    },
    afterReady: async page => {
      assert.match(await page.locator('.portalHatgPanelComponent').getAttribute('data-hatg-error'), /hatg_panel_navigation.css/);
      assert.equal(await page.locator('#hatgPane').count(), 0);
      fail = false;
      await page.locator('[data-hatg-template-retry]').click();
      await page.evaluate(() => panel.ready);
      await layout(page);
    },
  });
  console.log('PASS persistent style failure displays retry and recovers');

  let old = true;
  await run({
    route: async (route, url) => {
      if (url.pathname !== '/hatg-panel.html' || !old) return false;
      await route.fulfill({contentType: 'text/html', body: '<main><section id="hatgPane"><div id="hatgViewport"></div><div>PRIMITIVE 原语 TEMPLATE 模板</div></section></main>'});
      return true;
    },
    afterReady: async page => {
      assert.match(await page.locator('.portalHatgPanelComponent').getAttribute('data-hatg-error'), /模板版本不匹配/);
      assert.equal(await page.locator('#hatgPane').count(), 0, 'legacy bottom-shelf DOM is rejected before insertion');
      old = false;
      await page.locator('[data-hatg-template-retry]').click();
      await page.evaluate(() => panel.ready);
      await layout(page);
    },
  });
  console.log('PASS stale template rejected, current template recovers');
})().catch(error => {console.error(error); process.exitCode = 1;});
