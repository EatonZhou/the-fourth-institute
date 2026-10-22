'use strict';
// Actual scope menu and Resident protocol in a browser; transport and ancillary
// avatar/permission services are fixtures. No live instance mutations.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const {chromium} = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless: true});
  const errors = [], checks = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('about:blank');
    await page.evaluate(() => {
      const tag = (parts, ...values) => String.raw({raw: parts}, ...values);
      window.PortalI18n = {t: x => x, text: tag, html: tag};
      window.f = {calls: [], destroyed: 0, subscriptions: 0, hold: false, fail: false, pending: [], cursor: 10};
      window.PortalUIPrimitives = {
        escapeHtml: x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
        loadAsset: async () => {}, mountIcons() {}, randomUUID: () => 'fixture',
      };
      window.PortalIdentity = {permissionSystemOff: async () => false, load: async () => {}, username: () => 'fixture'};
      window.PortalUI = {toast() {}, createDialogController: options => ({
        open: () => options.onOpen(), close: () => options.onClose(), destroy() {},
      })};
      window.PortalDetailSurface = {mount: () => ({destroy() {}})};
      window.PortalAvatarEditor = {loadAssets: async () => {}, read: async () => null, paint() {}};
      window.PortalInstructionClient = {execute: async () => ({items: [], rules: {trees: {A: {nodes: []}}}})};
      window.PortalEvents = {watchResident: () => { f.subscriptions++; return () => { f.destroyed++; }; }};
      window.PortalResidentService = {loadStates: async (ids, options) => {
        f.calls.push({ids, options});
        if (f.fail) throw Object.assign(new Error('fixture read denied'), {status: 403});
        if (f.hold) await new Promise(resolve => f.pending.push(resolve));
        return {states: ids.map(resident_id => ({resident_id, cursor: f.cursor, state: {work_state: 'idle'}}))};
      }};
      f.open = id => PortalScopeMenu.open({kind: 'lineage', id: 'lineage_fixture', tab: 'members',
        record: {title: 'Fixture', nodes: [{id, title: 'Child', backend: 'codex', status: 'running'}]}});
      f.event = state => window.dispatchEvent(new CustomEvent('portal:change', {detail: {resident_events: [{
        resident_id: 'resident_child', cursor: ++f.cursor, event_type: 'resident.state.changed', payload: {state},
      }]}}));
    });
    for (const name of ['resident_protocol', 'portal_scope_menu']) await page.addScriptTag({path: path.join(root, 'static', name + '.js')});
    await page.evaluate(() => f.open('resident_child'));
    const state = page.locator('[data-member-state="resident_child"]');
    await page.waitForFunction(() => document.querySelector('[data-member-state]')?.textContent === 'idle');
    assert.equal(await state.textContent(), 'idle', 'stale sidebar running must be replaced by formal idle');
    assert.equal((await page.evaluate(() => f.calls[0].options)).includeViewerState, false);
    await page.locator('[data-member]').check();
    await page.locator('input[name="target"]').fill('principal_draft');
    await page.evaluate(() => f.event({work_state: 'running'}));
    assert.equal(await state.textContent(), 'running');
    assert.equal(await page.locator('[data-member]').isChecked(), true);
    assert.equal(await page.locator('input[name="target"]').inputValue(), 'principal_draft');
    await page.evaluate(() => f.event({work_state: 'queued', family_activity: {work_state: 'running'}}));
    assert.equal(await state.textContent(), 'running');
    await page.evaluate(() => f.event({work_state: 'idle', family_activity: null}));
    // A facade-only event invalidates the Family aggregate; the protocol reads
    // its formal projection before clearing the visible aggregate state.
    await page.waitForFunction(() => document.querySelector('[data-member-state]')?.textContent === 'idle');
    assert.equal(await state.textContent(), 'idle');
    checks.push('formal idle overrides stale running; live work/Family transitions preserve selection and draft');
    await page.locator('[data-portal-detail-close]').click();
    assert.equal(await page.evaluate(() => f.destroyed), 1);
    await page.evaluate(() => { f.hold = true; return f.open('resident_pending'); });
    await page.waitForFunction(() => f.pending.length === 1);
    await page.locator('[data-portal-detail-close]').click();
    await page.evaluate(() => { f.hold = false; return f.open('resident_new'); });
    await page.waitForFunction(() => document.querySelector('[data-member-state="resident_new"]')?.textContent === 'idle');
    await page.evaluate(() => { f.pending.splice(0).forEach(resolve => resolve()); });
    assert.equal(await page.locator('[data-member-state="resident_new"]').textContent(), 'idle');
    assert.equal(await page.locator('[data-member-state="resident_pending"]').count(), 0);
    checks.push('closing releases subscription and a late read cannot change the next menu');
    await page.locator('[data-portal-detail-close]').click();
    await page.evaluate(() => { f.fail = true; return f.open('resident_error'); });
    await page.waitForFunction(() => document.querySelector('[data-member-state]')?.textContent === '状态读取失败');
    assert(!await page.locator('.scopeMenuMemberList').textContent().then(s => s.includes('running')));
    await page.locator('[data-portal-detail-close]').click();
    await page.evaluate(() => { f.fail = false; return f.open('resident_error'); });
    await page.waitForFunction(() => document.querySelector('[data-member-state]')?.textContent === 'idle');
    checks.push('read failure is visible without stale success and reopening performs a fresh read');
    await page.locator('[data-portal-detail-close]').click();
    assert.equal(await page.evaluate(() => f.destroyed === f.subscriptions), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ok: true, checks, errors}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
