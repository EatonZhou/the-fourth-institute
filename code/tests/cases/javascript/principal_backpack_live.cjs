'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<div id="package"></div>');
    await page.evaluate(() => {
      const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
      window.PortalI18n = { html: tag, text: tag, t: x => x, markup: x => x };
      window.PortalUIPrimitives = { escapeHtml: x => String(x), renderAsyncState: el => el.textContent = 'Loading' };
      window.PortalButtonTooltips = { hide() {}, show() {} };
      window.PortalResidentAvatar = { markup: () => '<span>Resident</span>' };
      window.PortalIdentity = { permissionSystemOff: async () => true };
      window.watches = []; window.releases = []; window.reads = [];
      window.PortalEvents = { watchResident: id => {
        watches.push(id); return () => releases.push(id);
      } };
      window.holdings = [{ resource_id: 'workspace_shared', resource_type: 'workspace', title: 'Shared directory' }];
      window.PortalAPI = { request: async url => {
        reads.push(url);
        const principal = new URL(url, 'http://fixture').searchParams.get('principal');
        return { principal: { id: principal, title: principal, kind: 'resident', level: 'resident' }, possessions: structuredClone(holdings) };
      } };
    });
    await page.addScriptTag({ path: path.resolve(__dirname, '../../../management_portal/static/principal_backpack.js') });
    await page.evaluate(() => {
      window.backpack = PortalPrincipalBackpack.mount({ container: document.getElementById('package'), principalId: 'resident_aaaaaaaaaaaa' });
    });
    await page.waitForFunction(() => document.querySelector('[data-package-title="Shared directory"]'));
    await page.evaluate(() => {
      holdings = [];
      window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_changes: { resident_aaaaaaaaaaaa: ['identity'] } } }));
    });
    await page.waitForFunction(() => !document.querySelector('[data-package-title="Shared directory"]') && document.body.textContent.includes('角色资料来自当前登记'));
    await page.evaluate(() => {
      holdings = [{ resource_id: 'workspace_shared', resource_type: 'workspace', title: 'Bound again' }];
      window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_changes: { resident_aaaaaaaaaaaa: ['identity'] } } }));
    });
    await page.waitForFunction(() => document.querySelector('[data-package-title="Bound again"]'));
    const before = await page.evaluate(() => reads.length);
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_changes: { resident_other: ['identity'], resident_aaaaaaaaaaaa: ['runtime'] } } }));
    });
    assert.equal(await page.evaluate(() => reads.length), before, 'unrelated and runtime changes do not reload the Package');
    await page.evaluate(async () => {
      holdings = [];
      await backpack.refresh();
    });
    assert.equal(await page.locator('[data-package-title="Bound again"]').count(), 0);
    await page.evaluate(async () => { await backpack.setPrincipalId('resident_bbbbbbbbbbbb'); backpack.destroy(); });
    assert.deepEqual(await page.evaluate(() => ({ watches, releases })), {
      watches: ['resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb'],
      releases: ['resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb'],
    });
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
