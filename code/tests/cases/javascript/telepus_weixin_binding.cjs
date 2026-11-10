'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_TEST_CHROMIUM ? {executablePath: process.env.PORTAL_TEST_CHROMIUM} : {})});
  try {
    const page = await browser.newPage();
    await page.setContent('<main id="board"></main><section id="modal"></section>');
    await page.evaluate(() => {
      window.PortalWatchboardApplications = {register: app => { window.application = app; }};
      window.PortalI18n = {t: text => text, html: (strings, ...values) => strings.reduce((s, text, i) => s + text + (values[i] ?? ''), '')};
      window.PortalUIPrimitives = {iconHtml: () => '+'};
    });
    await page.addScriptTag({path: path.resolve(__dirname, '../../../management_portal/static/watchboard_applications/telepus.js')});
    await page.evaluate(() => {
      let dashboard = {telegram_users: {enabled: true, users: [], permissions: {can_manage: true}}, weixin_binding: {status: 'unbound'}};
      window.calls = [];
      window.bindingStep = 0;
      window.controller = application.create({
        escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
        ui: {toast: text => { throw Error(text); }, errorMessage: (label, error) => label + error.message},
        api: {watchboardApplication: async request => {
          calls.push(request);
          let binding = {status: 'unbound'};
          if (request.operation === 'weixin-start') binding = {status: 'wait', session_id: 'session', qr_image: 'data:image/svg+xml;base64,PHN2Zy8+'};
          if (request.operation === 'weixin-get') {
            binding = request.args.pin === '1234' ? {status: 'active'} : {status: 'need_verifycode', session_id: 'session'};
          }
          return {binding, watchboard: {...dashboard, weixin_binding: {status: binding.status === 'active' ? 'active' : 'unbound'}}};
        }},
        view: {dashboard: () => dashboard, residentId: () => 'resident_a3dc51f3859d',
          accept: payload => { dashboard = payload.watchboard; return true; },
          render: () => { document.querySelector('#board').innerHTML = controller.render(dashboard); controller.bind(document.querySelector('#board'), dashboard); }},
        modal: {open: (title, body, options) => { const modal = document.querySelector('#modal'); modal.innerHTML = body; options.onBind(modal); },
          close: () => { document.querySelector('#modal').replaceChildren(); }},
        components: {generic: (_, content) => content},
      });
      document.querySelector('#board').innerHTML = controller.render(dashboard);
      controller.bind(document.querySelector('#board'), dashboard);
    });
    await page.locator('[data-weixin-bind]').click();
    await page.locator('[data-weixin-pin-form]').waitFor({state: 'visible'});
    await page.locator('[data-weixin-pin]').fill('1234');
    await page.locator('[data-weixin-pin-form] button').click();
    await page.waitForFunction(() => document.querySelector('[data-weixin-status]').textContent === '微信已绑定');
    await page.locator('[data-weixin-unbind]').click();
    await page.waitForFunction(() => document.querySelector('#board').textContent.includes('微信未绑定'));
    const calls = await page.evaluate(() => calls);
    assert(calls.every(call => call.resident_id === 'resident_a3dc51f3859d'));
    assert(calls.some(call => call.args.pin === '1234'));
    assert.equal(calls.at(-1).operation, 'weixin-unbind');
    console.log('Weixin Watchboard QR, PIN, confirmation and unbind passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
