'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [], clicks = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.exposeFunction('recordClick', hit => clicks.push(hit));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, name.slice(8))),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html><body><nav data-portal-nav></nav><main>Destination</main></body></html>'});
    });
    const setup = async (username = "") => {
      await page.goto('http://navigation.fixture.invalid/development');
      for (const name of ['portal_design_system', 'portal_themes', 'portal_components']) await page.addStyleTag({url: `/static/${name}.css`});
      for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_theme']) await page.addScriptTag({url: `/static/${name}.js`});
      await page.evaluate(username => {
        window.catalogReplies = [];
        window.typeReplies = [];
        window.PortalAPI = {get: () => new Promise(resolve => catalogReplies.push(resolve))};
        window.PortalInstructionClient = {execute: () => new Promise(resolve => typeReplies.push(resolve))};
        window.PortalIdentity = {peek: () => ({username}), load: async () => ({username})};
        window.PortalCompatibility = {identifier: () => 'application/x-portal-nav-fixture'};
        window.PortalConversationStore = {};
        window.PortalAgentInbox = {mount() {}};
        window.PortalUIPrimitives.loadAsset = async () => {};
        document.addEventListener('click', event => recordClick({tag: event.target.tagName,
          module: event.target.closest('[data-module-link]')?.dataset.moduleLink || null}), true);
      }, username);
      await page.addScriptTag({url: '/static/portal_nav.js'});
      await page.locator('[data-module-link="workbenches"]').waitFor();
    };
    const completeCatalog = async items => page.evaluate(items => {
      catalogReplies.shift()({builtins: {}, workbenches: items});
    }, items);
    const app = {workbench_id: 'workbench_fixture', title: 'Published app', url: '/workbenches/view?id=workbench_fixture',
      published_revision: 1, navigation: 'top'};

    if (process.argv[2] === 'ordering') {
      await setup('fixture');
      await completeCatalog([app]);
      await page.locator('[data-module-link="workbench_fixture"]').waitFor();
      await page.evaluate(() => {
        const second = document.createElement('nav');
        second.dataset.portalNav = '';
        document.body.appendChild(second);
        PortalNav.render(second);
        document.querySelectorAll('[data-portal-nav]').forEach(root => {
          PortalNav.render(root); PortalNav.render(root);
        });
        window.savedOrders = [];
        PortalState.write = (_page, _user, value) => savedOrders.push(value.itemOrder);
      });
      const first = page.locator('[data-portal-nav]').first();
      await first.locator('[data-module-link="workbench_fixture"]').dragTo(first.locator('[data-module-link="resource"]'),
        {targetPosition: {x: 2, y: 5}});
      await page.waitForFunction(() => savedOrders.length > 0);
      const state = await page.evaluate(() => ({savedOrders, orders: [...document.querySelectorAll('[data-portal-nav]')]
        .map(root => [...root.querySelectorAll('[data-module-link]')].map(link => link.dataset.moduleLink))}));
      assert.equal(state.savedOrders.length, 1, 'refresh does not duplicate drag handlers or persistence');
      assert.equal(state.orders[0][0], app.workbench_id, 'newly hydrated links participate in drag ordering');
      assert.deepEqual(state.orders[0], state.orders[1], 'other mounted navigations follow the same order');
      assert.deepEqual(errors, []);
      console.log('navigation refresh preserves drag ordering without duplicate handlers');
      return;
    }

    await setup();
    const link = page.locator('[data-module-link="workbenches"]');
    const rect = await link.boundingBox();
    await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
    await page.mouse.down();
    // Real asynchronous catalog hydration completes while a native click is in flight.
    await completeCatalog([app]);
    await page.locator('[data-module-link="workbench_fixture"]').waitFor();
    await page.mouse.up();
    try { await page.waitForURL('**/workbenches', {timeout: 2000}); }
    catch { assert.fail(`catalog hydration discarded the pressed navigation link: ${JSON.stringify(clicks)}`); }
    assert.equal(clicks.at(-1).module, 'workbenches');

    await setup();
    await completeCatalog([app]);
    await page.locator('[data-module-link="workbench_fixture"]').waitFor();
    await page.locator('[data-module-link="workbenches"]').focus();
    await page.evaluate(() => {
      window.retainedLink = document.activeElement;
      typeReplies.shift()({types: [{state: 'enabled', catalog_section: 'article', catalog_order: 1}]});
    });
    await page.waitForFunction(() => PortalNav.secondaryItems('resource').some(item => item.id === 'article'));
    assert.equal(await page.evaluate(() => retainedLink === document.activeElement), true, 'resource hydration preserves focus');
    await page.evaluate(() => dispatchEvent(new Event('portal:workbenches-changed')));
    await completeCatalog([{...app, title: 'Renamed app'}]);
    await page.getByText('Renamed app', {exact: true}).first().waitFor();
    assert.equal(await page.evaluate(() => retainedLink === document.activeElement), true, 'catalog refresh preserves focus');
    await page.evaluate(() => dispatchEvent(new Event('portal:workbenches-changed')));
    await completeCatalog([]);
    await page.locator('[data-module-link="workbench_fixture"]').waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => retainedLink === document.activeElement), true, 'removing a different entry preserves focus');
    await page.keyboard.press('ArrowDown');
    await page.locator('#portalSecondaryNavPopover:not([hidden])').waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => retainedLink === document.activeElement), true, 'Escape still restores the mounted trigger');
    assert.deepEqual(errors, []);
    console.log('navigation refresh preserves native clicks and keyboard focus');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
