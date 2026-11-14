'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(process.env.PORTAL_UI_PLAYWRIGHT || path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const standalone of [false, true]) {
      const page = await browser.newPage();
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.route('http://fixture.test/**', route => {
        if (route.request().url().endsWith('/static/portal_user_enrollment.js')) return route.fulfill({contentType: 'application/javascript', body: 'export const mountEnrollment = () => ({destroy() {}, isBusy() {return false;}, hasUnsavedChanges() {return false;}});'});
        return route.fulfill({contentType: 'text/html', body: `<main id="host" ${standalone ? 'data-portal-settings-host' : ''}></main>`});
      });
      await page.goto('http://fixture.test/');
      for (const name of ['portal_state', 'portal_text', 'portal_i18n']) await page.addScriptTag({path: path.join(root, `management_portal/static/${name}.js`)});
      await page.evaluate(() => {
        window.loaded = []; window.gets = [];
        window.PortalUI = {confirm: async () => true};
        window.PortalUIPrimitives = {loadAsset: async path => {loaded.push(path);}, randomUUID: () => 'test'};
        window.PortalAvatarEditor = {mount: () => ({destroy() {}, close() {}, isBusy() {return false;}, hasUnsavedChanges() {return false;}})};
        window.PortalIdentity = {load: async () => ({username: 'fixture'}), loadPreferences: async () => ({}), passwordRequirements: 'Password'};
        window.PortalTheme = {mode: () => 'auto', normalizeAutoColors: () => ({accent: '', bubble_intensity: 18, quote_color: ''}), paintAutoColors: () => ({accent: '', bubble_intensity: 18, quote_color: ''})};
        window.PortalAPI = {get: async url => {gets.push(url); return url === '/api/account/security' ? {} : {applications: []};}};
        window.PortalInstructionClient = {execute: async () => ({residents: []})};
        window.PortalNav = {mount() {}, mountPrincipalDetails: async () => ({destroy() {}})};
      });
      await page.addScriptTag({path: path.join(root, 'management_portal/static/portal_tool_settings.js')});
      await page.addScriptTag({path: path.join(root, 'management_portal/static/settings.js')});
      if (!standalone) await page.evaluate(async () => {window.shell = await PortalSettingsPanel.mount(document.querySelector('#host'));});
      await page.getByRole('tab', {name: '工具', exact: true}).waitFor();
      assert.equal(await page.evaluate(() => gets.includes('/api/tool-settings')), false);
      await page.getByRole('tab', {name: '工具', exact: true}).click();
      await page.getByLabel('选择 Resident', {exact: true}).waitFor();
      assert.equal(await page.evaluate(() => loaded.includes('/static/portal_tool_settings.js')), true);
      assert.equal(await page.evaluate(() => gets.filter(url => url === '/api/tool-settings').length), 1);
      await page.getByRole('tab', {name: 'Appearance', exact: true}).click();
      assert.equal(await page.locator('[data-settings-tools]').isHidden(), true);
      await page.getByRole('tab', {name: 'Package', exact: true}).click();
      assert.equal(await page.locator('[data-settings-package]').isVisible(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: actual settings.js mount and standalone bootstrap, lazy tool reads/assets, Appearance and Package tab switching');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
