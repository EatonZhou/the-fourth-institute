'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 900, height: 800}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'account.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, url.pathname.slice(8))), contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><main></main>'});
    });
    await page.goto('http://account.fixture.invalid');
    await page.evaluate(() => {
      window.PortalI18n = {t: text => text};
      window.PortalInstructionClient = {execute: async (name, args) => {
        switch (name) {
          case 'ListProviders': return {providers: [{provider: 'codex', title: 'Codex', kinds: ['subscription_login'], account_directory_prefix: '/host/accounts/codex/'}]};
          case 'ListProviderAccounts': return {accounts: []};
          case 'ListBackendRoles': return {roles: []};
          case 'ListModelApis': return {apis: []};
          case 'DiscoverProviderAccountDirectories': return {candidates: [{path: '/host/login', evidence: '存在 auth.json', login_state: 'unverified'}]};
          case 'BrowseProviderAccountDirectories': return {path: args.path || '/host', parent: '/host', directories: [{name: 'login', path: '/host/login'}], roots: [{name: '/', path: '/'}]};
          default: throw Error(name);
        }
      }};
      window.PortalAPI = {requestResult: async url => {
        const query = new URL(url, location.href).searchParams, action = query.get('action');
        return {response: {ok: true}, payload: await window.PortalInstructionClient.execute(action === 'browse' ? 'BrowseProviderAccountDirectories' : 'DiscoverProviderAccountDirectories', action === 'browse' ? {path: query.get('path') || undefined} : {provider: query.get('provider')})};
      }};
    });
    for (const name of ['portal_state', 'portal_ui_primitives', 'portal_overlay', 'portal_ui']) await page.addScriptTag({url: `/static/${name}.js`});
    await page.evaluate(async () => {const module = await import('/static/portal_management_backend.js'); window.fixturePanel = await module.mount(document.querySelector('main'), {me: {access: {permission_system: 'open'}}});});
    await page.getByRole('button', {name: '接入订阅帐号', exact: true}).click();
    assert.deepEqual(errors, []);
    assert.equal(await page.locator('[name=account_dir]').count(), 1, JSON.stringify(await page.locator('dialog').evaluateAll(nodes => nodes.map(n => ({title: n.getAttribute('aria-label'), open: n.open, text: n.textContent.slice(0, 200)})))));
    const registration = page.getByRole('dialog', {name: '新增执行账号'});
    assert.equal(await registration.locator('[name=account_dir]').inputValue(), '/host/accounts/codex/new-account');
    await registration.getByRole('checkbox', {name: '使用本机已有登录'}).check();
    await registration.getByRole('button', {name: '/host/login', exact: true}).click();
    await registration.getByRole('button', {name: '浏览', exact: true}).click();
    const picker = page.getByRole('dialog', {name: '选择 Portal 主机目录'});
    await picker.getByRole('button', {name: '主目录', exact: true}).click();
    await picker.getByRole('button', {name: 'login', exact: true}).click();
    await picker.getByRole('button', {name: '选择此目录', exact: true}).click();
    assert.equal(await registration.locator('[name=account_dir]').inputValue(), '/host/login');
    await registration.getByRole('button', {name: '浏览', exact: true}).click();
    await page.getByRole('dialog', {name: '选择 Portal 主机目录'}).getByRole('button', {name: '关闭', exact: true}).click();
    assert.equal(await registration.locator('[name=account_dir]').inputValue(), '/host/login');
    assert.deepEqual(errors, []);
    await page.evaluate(() => window.fixturePanel.destroy());
    console.log('Chromium account directory modes and nested host picker passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
