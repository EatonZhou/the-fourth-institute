'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, webkit } = require('playwright');
const { PORTAL_MANAGEMENT_SCRIPTS } = require('./portal_management_scripts.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const output = process.env.PORTAL_NETWORK_BROWSER_ARTIFACTS;
const markup = fs.readFileSync(path.resolve(staticRoot, '../portal_management.html'), 'utf8');
const css = [...markup.matchAll(/<link[^>]+href="(\/static\/[^"]+\.css)"/g)].map(match => match[1]);
const html = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
  + css.map(url => `<link rel="stylesheet" href="${url}">`).join('')
  + '</head><body><main id="host" style="padding:16px"></main></body></html>';

async function run(engine, name) {
  const browser = await engine.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 1000}});
    const errors = [], posts = [];
    page.on('pageerror', error => errors.push(error.message));
    let fail = false;
    let state = {
      access: {mode: 'local', port: 18084, urls: ['http://127.0.0.1:18084']},
      proxy: {http: 'http://proxy.example:3128', https: '', all: '', no_proxy: 'localhost,127.0.0.1,::1'},
      proxy_source: 'environment', revision: '1',
    };
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/admin/network') {
        if (route.request().method() === 'POST') {
          const payload = route.request().postDataJSON(); posts.push(payload);
          if (fail) return route.fulfill({status: 409, json: {error: '代理设置已被修改，请重新读取后再保存'}});
          assert.equal(payload.expected_revision, state.revision);
          state = {...state, proxy: payload.proxy || state.proxy,
            proxy_source: payload.proxy ? 'saved' : 'environment', revision: String(Number(state.revision) + 1)};
        }
        return route.fulfill({json: state});
      }
      if (url.pathname === '/static/portal_user_enrollment.js') return route.fulfill({contentType: 'text/javascript',
        body: 'export function mountEnrollment(){return {destroy(){},isBusy(){return false},hasUnsavedChanges(){return false}}}'});
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(staticRoot, path.basename(url.pathname));
        if (fs.existsSync(file)) return route.fulfill({path: file});
      }
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      return route.abort();
    });
    await page.goto('http://network-test.invalid/');
    await page.evaluate(() => {
      const tag = (strings, ...values) => strings.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
      window.PortalI18n = {t: text => text, html: tag, text: tag};
      window.PortalIdentity = {passwordRequirements: 'Password requirements'};
      window.PortalUI = {createDialogController: () => ({open(){}, close(){}, destroy(){}})};
      window.PortalAPI = {get: async () => ({users: [], audit: []})};
    });
    for (const filename of PORTAL_MANAGEMENT_SCRIPTS) await page.addScriptTag({url: `/static/${filename}`});
    await page.evaluate(async () => {
      window.panel = await window.PortalManagementPanel.mount(document.querySelector('#host'), {
        me: {username: 'admin', access: {user_category: 'portal_admin', permission_system: 'open', allows: {GetExecutionSettings: true}}},
        view: 'network',
      });
    });
    const mode = page.locator('[data-network-mode]'), save = page.locator('[data-network-save]');
    await page.waitForFunction(() => !document.querySelector('[data-network-mode]').disabled);
    await mode.selectOption('saved');
    await page.locator('[data-network-field="all"]').fill('socks5h://proxy.example:1080');
    await page.locator('[data-network-field="http"]').fill('');
    for (const width of [1200, 390, 945]) {
      await page.setViewportSize({width, height: 1000});
      await save.scrollIntoViewIfNeeded();
      assert.ok(await page.locator('[data-network-field="all"]').isEditable());
      const bounds = await page.locator('[data-management-view="network"]').evaluate(element => ({client: element.clientWidth, scroll: element.scrollWidth}));
      assert.ok(bounds.scroll <= bounds.client + 1, `${name} at ${width}px overflows: ${JSON.stringify(bounds)}`);
      if (output) { fs.mkdirSync(output, {recursive: true}); await page.screenshot({path: path.join(output, `${name}-${width}.png`)}); }
    }
    fail = true;
    await save.click();
    await page.waitForFunction(() => document.querySelector('[data-network-status]').textContent.includes('已被修改'));
    assert.equal(await page.locator('[data-network-field="all"]').inputValue(), 'socks5h://proxy.example:1080');
    assert.equal(await page.evaluate(() => window.panel.hasUnsavedChanges()), true);
    fail = false;
    await save.click();
    await page.waitForFunction(() => document.querySelector('[data-network-status]').textContent === '代理设置已保存。');
    assert.equal(posts.at(-1).proxy.all, 'socks5h://proxy.example:1080');
    assert.equal(await page.evaluate(() => window.panel.hasUnsavedChanges()), false);
    await mode.selectOption('environment');
    await save.click();
    await page.waitForFunction(() => document.querySelector('[data-network-save]').disabled && document.querySelector('[data-network-status]').textContent === '代理设置已保存。');
    assert.equal(posts.at(-1).proxy, null);
    assert.equal(posts.length, 3);
    assert.deepEqual(errors, []);
    await page.evaluate(() => window.panel.destroy());
    console.log(`${name}: responsive proxy editing, failed save draft, save and environment reset passed`);
  } finally { await browser.close(); }
}
(async () => { await run(chromium, 'chromium'); await run(webkit, 'webkit'); })().catch(error => { console.error(error); process.exitCode = 1; });
