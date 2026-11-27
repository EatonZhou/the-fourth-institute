'use strict';
// Exercise the actual navigation loader on a page with no preloaded menu parts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const groups = JSON.parse(fs.readFileSync(path.join(root, '../script_groups.json'), 'utf8'));

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const errors = [], loaded = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'menu.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        loaded.push(url.pathname);
        const source = url.pathname === '/static/portal_resource_routes.js'
          ? process.env.PORTAL_TEST_RESOURCE_ROUTES : path.join(root, url.pathname.slice(8));
        return route.fulfill({body: fs.readFileSync(source),
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      }
      if (url.pathname.startsWith('/api/')) return route.fulfill({json: {}});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html><body></body></html>'});
    });
    await page.goto('http://menu.fixture.invalid');
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n',
      'portal_ui_primitives', 'portal_overlay', 'portal_theme', 'portal_resource_routes', 'portal_resource_chips']) {
      await page.addScriptTag({url: `/static/${name}.js`});
    }
    // Lineage preloads Chat through this declared group. Resident Menu and all
    // its parts must still be discovered by navigation, not by a test-only list.
    for (const url of groups['chat-workspace']) await page.addScriptTag({url});
    await page.evaluate(() => {
      // Only isolate unrelated identity/catalog/inbox services, not the loader
      // or any Resident Menu factory. The group adapter avoids business writes.
      window.PortalAPI = {get: async () => ({})};
      window.PortalIdentity = {peek: () => ({username: ''}), load: async () => ({username: ''})};
      window.PortalConversationStore = {};
      window.PortalAgentInbox = {mount() {}};
      window.PortalMailboxAlerts = {mount() {}};
    });
    await page.addScriptTag({url: '/static/portal_nav.js'});
    const result = await page.evaluate(async () => {
      const unloaded = !window.PortalResidentMenu && !window.PortalResidentMenuMemory;
      const menu = await PortalNav.openResidentMenu({
        lineage: {lineage_id: 'fixture', nodes: []},
        group: {active: 'family', tabs: [{key: 'family', label: 'Family'}], render(_key, container) {
          container.textContent = 'Family settings fixture';
        }},
      });
      return {unloaded, opened: Boolean(menu), memoryLoaded: Boolean(window.PortalResidentMenuMemory)};
    });
    assert.deepEqual(result, {unloaded: true, opened: true, memoryLoaded: true});
    await page.locator('#nodeMenuGroupPanel').waitFor({state: 'visible'});
    assert.equal(await page.locator('#nodeMenuGroupPanel').textContent(), 'Family settings fixture');
    assert.equal(loaded.filter(url => url === '/static/portal_resident_menu_memory.js').length, 1);
    assert.equal(loaded.some(url => url.startsWith('/static/principal_backpack.')), false);
    assert.deepEqual(errors, []);
    console.log('Resident Menu opens through the cold navigation loader with all factories ready');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
