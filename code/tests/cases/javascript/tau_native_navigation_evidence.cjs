'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { observeBrowser } = require(path.join(codeRoot, 'management_portal/test_framework/tau_browser_observer'));
const { readNativeNavigationTarget } = require(path.join(codeRoot, 'management_portal/test_framework/tau_native_navigation_evidence'));
const { PORTAL_COMPONENTS_STYLES } = require(path.join(codeRoot, 'tests/cases/javascript/portal_components_styles.cjs'));

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-editor-nav-'));
  let browser, observer;
  try {
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const requests = [], errors = [];
    await context.route('**/*', route => {
      const url = new URL(route.request().url()); requests.push(url.pathname);
      if (url.origin !== 'http://navigation.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(codeRoot, 'management_portal', url.pathname);
        return route.fulfill({ body: fs.readFileSync(file), contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript' });
      }
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body><nav data-portal-nav></nav><main>Fixture destination</main></body></html>' });
    });
    observer = await observeBrowser(context, { evidenceDir: dir });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://navigation.fixture.invalid/workbenches?panel=settings&id=fixture');
    for (const name of ['portal_design_system', 'portal_themes', ...PORTAL_COMPONENTS_STYLES]) await page.addStyleTag({ url: `/static/${name}.css` });
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_theme']) await page.addScriptTag({ url: `/static/${name}.js` });
    await page.evaluate(() => {
      window.PortalAPI = { get: () => new Promise(() => {}) };
      window.PortalInstructionClient = { execute: () => new Promise(() => {}) };
      window.PortalIdentity = { peek: () => ({}), load: async () => ({}) };
      window.PortalCompatibility = { identifier: () => 'application/x-nav-evidence-fixture' };
      window.PortalConversationStore = {};
      window.PortalAgentInbox = { mount() {} };
      window.PortalUIPrimitives.loadAsset = async () => {};
    });
    await page.addScriptTag({ url: '/static/portal_nav.js' });
    const link = page.locator('[data-portal-nav] > [data-module-link="lineage"]');
    await link.waitFor();
    let sample = await readNativeNavigationTarget(page, { moduleId: 'lineage' });
    assert.equal(sample.ready_for_native_click, true);
    assert.equal(sample.target.pathname, '/lineage');
    const initial = JSON.stringify(sample), count = requests.length;
    for (let i = 0; i < 3; i++) await readNativeNavigationTarget(page, { moduleId: 'lineage' });
    assert.equal(requests.length, count, 'passive samples cause no request');
    assert.equal(JSON.stringify(sample), initial, 'earlier samples remain unchanged');
    for (const [attribute, value] of [['hidden', ''], ['aria-disabled', 'true'], ['target', '_blank'], ['href', 'https://foreign.invalid/lineage']]) {
      const before = await link.getAttribute(attribute);
      await link.evaluate((e, { attribute, value }) => e.setAttribute(attribute, value), { attribute, value });
      sample = await readNativeNavigationTarget(page, { moduleId: 'lineage' });
      assert.equal(sample.ready_for_native_click, false, attribute);
      if (attribute === 'href') assert.equal(sample.target.href, null);
      await link.evaluate((e, { attribute, before }) => before === null ? e.removeAttribute(attribute) : e.setAttribute(attribute, before), { attribute, before });
    }
    await link.evaluate(e => e.parentElement.appendChild(e.cloneNode(true)));
    sample = await readNativeNavigationTarget(page, { moduleId: 'lineage' });
    assert.equal(sample.visible_count, 2); assert.equal(sample.ready_for_native_click, false);
    await page.locator('[data-portal-nav] > [data-module-link="lineage"]').last().evaluate(e => e.remove());
    await page.evaluate(() => { const cover = document.createElement('div'); cover.id = 'fixture-cover'; cover.style.cssText = 'position:fixed;inset:0;z-index:99999'; document.body.appendChild(cover); });
    sample = await readNativeNavigationTarget(page, { moduleId: 'lineage' });
    assert.equal(sample.target.hit, false); assert.equal(sample.ready_for_native_click, false);
    await page.locator('#fixture-cover').evaluate(e => e.remove());
    // A native anchor may have query/hash state. Preserve that exact href,
    // instead of requiring a suffix pattern which rejects the legal URL.
    await link.evaluate(e => e.href = '/lineage?lineage=fixture-owned#canvas');
    await observer.action(page, 'original-native-navigation', async actualPage => {
      const target = await readNativeNavigationTarget(actualPage, { moduleId: 'lineage' });
      assert.equal(target.ready_for_native_click, true);
      const originalURL = actualPage.waitForURL(url => url.href === target.target.href, { timeout: 3000 });
      await actualPage.locator('[data-portal-nav] > [data-module-link="lineage"]').click();
      await originalURL;
      assert.equal(await actualPage.locator('main').textContent(), 'Fixture destination');
    });
    await observer.flush(); observer.stop();
    const rows = fs.readFileSync(path.join(dir, 'tau-browser-observations.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    const start = rows.find(r => r.kind === 'action_started' && r.name === 'original-native-navigation');
    const end = rows.find(r => r.kind === 'action_finished' && r.action_id === start.action_id);
    assert.equal(end.status, 'completed'); assert.equal(end.page_id, start.page_id);
    assert.notEqual(end.document_id, start.document_id);
    const navigation = rows.filter(r => r.kind === 'request_started' && end.request_ids.includes(r.request_id) && r.document_association === 'navigation_target');
    assert.equal(navigation.length, 1); assert.equal(navigation[0].source_document_id, start.document_id);
    assert.equal(navigation[0].document_id, end.document_id);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'passed', scope: 'isolated Chromium native PortalNav and original browser observer', checks: ['unique visible native target', 'no sample requests', 'hidden/disabled/foreign/new-tab/duplicate/covered rejection', 'exact href including query/hash', 'one native click and original page/frame/document navigation identity'], production_calls: 0, business_acceptance: false, outer_tool_rtt_measured: false }));
  } finally {
    observer?.stop();
    if (browser) await browser.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
