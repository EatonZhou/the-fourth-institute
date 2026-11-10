'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_TEST_CHROMIUM ? {executablePath: process.env.PORTAL_TEST_CHROMIUM} : {})});
  try {
    const page = await browser.newPage();
    await page.setContent('<main id="board"></main><section id="modal"></section>');
    await page.addStyleTag({path: path.resolve(__dirname, '../../../management_portal/static/lineage_workbench.css')});
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
            if (bindingStep++ === 0) throw Object.assign(Error('HTTP 504'), {status: 504});
            if (request.args.pin === '1234' && bindingStep === 3) return {binding: {status: 'need_verifycode', session_id: 'session', retryable: true}, watchboard: dashboard};
            binding = request.args.pin === '1234' ? {status: 'active'} : {status: 'need_verifycode', session_id: 'session'};
          }
          return {binding, watchboard: {...dashboard, weixin_binding: {status: binding.status === 'active' ? 'active' : 'unbound'}}};
        }},
        view: {dashboard: () => dashboard, residentId: () => 'resident_a3dc51f3859d',
          accept: payload => { dashboard = payload.watchboard; return true; },
          render: () => { document.querySelector('#board').innerHTML = controller.render(dashboard); controller.bind(document.querySelector('#board'), dashboard); }},
        modal: {open: (title, body, options) => { const modal = document.querySelector('#modal'); modal.innerHTML = '<div class="watchboardModalCard"><button data-watchboard-modal-close>×</button>' + body + '</div>'; options.onBind(modal); },
          close: () => { document.querySelector('#modal').replaceChildren(); }},
        components: {generic: (_, content) => content},
      });
      document.querySelector('#board').innerHTML = controller.render(dashboard);
      controller.bind(document.querySelector('#board'), dashboard);
    });
    await page.locator('[data-weixin-bind]').click();
    assert.equal(await page.locator('.watchboardModalCard').evaluate(el => getComputedStyle(el).getPropertyValue('--portal-detail-width').trim()), '380px');
    await page.waitForFunction(() => document.querySelector('[data-weixin-status]').textContent.includes('重新确认'));
    await page.locator('[data-weixin-pin-form]').waitFor({state: 'visible'});
    await page.locator('[data-weixin-pin]').fill('1234');
    await page.locator('[data-weixin-pin-form] button').click();
    await page.waitForFunction(() => document.querySelector('[data-weixin-status]').textContent.includes('可以在微信发送 /help'));
    assert(await page.locator('[data-weixin-qr]').isHidden());
    await page.locator('[data-weixin-close]').click();
    assert.equal(await page.locator('[data-weixin-close]').count(), 0);
    await page.locator('[data-weixin-unbind]').click();
    await page.waitForFunction(() => document.querySelector('#board').textContent.includes('微信未绑定'));
    const calls = await page.evaluate(() => calls);
    assert(calls.every(call => call.resident_id === 'resident_a3dc51f3859d'));
    assert.equal(calls.filter(call => call.args.pin === '1234').length, 2);
    assert.equal(calls.at(-1).operation, 'weixin-unbind');
    console.log('Weixin Watchboard timeout recovery, PIN retry, compact dialog, confirmation and close passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
