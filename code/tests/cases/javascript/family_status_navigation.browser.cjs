'use strict';
// Real pointer/keyboard activation while the live Family table receives updates.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const runtime = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const browsers = require(fs.existsSync(runtime) ? runtime : 'playwright');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const resident = 'resident_111111111111';
const lineage = 'lineage_222222222222';

async function fixture(browser) {
  const page = await browser.newPage({viewport: {width: 1200, height: 900}});
  page.on('pageerror', error => console.error('pageerror:', error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'family.fixture.invalid') return route.abort();
    if (url.pathname.startsWith('/static/')) return route.fulfill({
      body: fs.readFileSync(path.join(root, url.pathname.slice(8))),
      contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
    });
    return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><button id="anchor">Family 成员</button><div id="inline"></div></body></html>'});
  });
  await page.goto('http://family.fixture.invalid/lineage');
  for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_design_system', 'portal_themes']) {
    await page.addStyleTag({url: `/static/${name}.css`});
  }
  for (const name of ['portal_state', 'portal_text', 'portal_ui_primitives', 'portal_ui', 'portal_overlay', 'resident_protocol', 'chat_workspace_family_status']) {
    await page.addScriptTag({url: `/static/${name}.js`});
  }
  await page.evaluate(({resident, lineage}) => {
    window.listeners = new Map();
    window.policy = {muted_member_ids: [], solo_member_id: ''};
    window.PortalInstructionClient = {execute: async () => policy};
    window.PortalAPI = {get: async () => ({accounts: []})};
    window.family = {title: 'Family', lineage_id: lineage, nodes: [
      {resident_id: resident, title: 'ISCA · cmodel4 数值版', role: 'member'},
      {resident_id: 'resident_333333333333', title: '另一位工作中的成员', role: 'member'},
    ]};
    window.PortalResidentService = {projection: async () => ({family_facade: family})};
    window.PortalResidentRuntimeController = {bind: () => ({
      state: () => ({}),
      load: async () => ({detail: {resident: {backend: 'codex'}}, settings: {}}),
      destroy() {}, close() {},
    })};
    const snapshot = count => ({state: {work_state: 'running', queued_count: count, execution_gate: {state: 'open'}}, messages: []});
    window.PortalResidentProtocol = {...PortalResidentProtocol, acquire: (id, listener) => {
      listeners.set(id, listener);
      return {load: async () => snapshot(0), destroy() { listeners.delete(id); }};
    }};
    window.update = count => listeners.get('resident_333333333333')({snapshot: snapshot(count)});
    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    window.statusUI = PortalChatWorkspaceFamilyStatus.createFamilyStatus({escapeHtml, footerTime: String, sessionRestartActive: () => false});
  }, {resident, lineage});
  return page;
}

(async () => {
  for (const engine of ['chromium', 'webkit']) {
    const browser = await browsers[engine].launch({headless: true});
    try {
      for (const mode of ['pointer', 'keyboard', 'inline']) {
        const page = await fixture(browser);
        try {
          await page.evaluate(async mode => {
            window.view = mode === 'inline'
              ? statusUI.mountFamilyStatus('facade', document.getElementById('inline'))
              : await statusUI.openFamilyStatus('facade', {anchor: document.getElementById('anchor')});
          }, mode);
          const link = page.locator(`[data-family-status-row="${resident}"] .familyStatusName`);
          await page.waitForFunction(() => document.querySelector('.familyStatusName, .familyStatusPanel [role="alert"]'));
          assert.deepEqual(await page.locator('.familyStatusPanel [role="alert"]').allTextContents(), []);
          await link.waitFor({state: 'visible'});
          await page.waitForFunction(() => listeners.size === 2 && document.querySelector('.familyStatusPanel')?.getAttribute('aria-busy') === 'false');
          // Expanded details must keep their owning row when an adjacent
          // member's details are inserted or removed.
          if (mode === 'pointer') {
            await page.locator(`[data-family-status-detail="${resident}"]`).click();
            await page.locator('[data-family-status-detail="resident_333333333333"]').click();
            await page.locator(`[data-family-status-detail="${resident}"]`).click();
            assert.equal(await page.locator('.familyStatusDetails').count(), 1);
            assert.equal(await page.locator('.familyStatusDetails a').evaluate(node => new URL(node.href).searchParams.get('resident')), 'resident_333333333333');
          }
          if (mode === 'keyboard') {
            await link.focus();
            await page.evaluate(() => update(2));
            assert.equal(await link.evaluate(node => node === document.activeElement), true, `${engine}: live status must retain the focused Resident link`);
            await page.keyboard.press('Enter');
          } else {
            const box = await link.boundingBox();
            await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
            await page.mouse.down();
            await page.evaluate(() => update(2));
            await page.mouse.up();
          }
          await page.waitForURL(url => url.searchParams.get('resident') === resident, {timeout: 2000});
          const target = new URL(page.url());
          assert.equal(target.searchParams.get('lineage'), lineage);
          assert.equal(target.searchParams.get('open_chat'), '1');
          console.log(`${engine} ${mode}: exact Resident navigation survives live refresh`);
        } finally { await page.close(); }
      }
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exit(1); });
