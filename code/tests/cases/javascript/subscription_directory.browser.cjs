'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
const css = [...fs.readFileSync(path.join(root, 'portal_management.html'), 'utf8').matchAll(/href="(\/static\/[^"?]+\.css)"/g)].map(match => match[1]);
const out = process.env.DIRECTORY_PICKER_EVIDENCE || '/tmp/directory-picker';
fs.mkdirSync(out, {recursive: true});
const evidence = {boundary: 'Real management mount, PortalUI dialog and production CSS; isolated synthetic read-only browse API. No user session or account registration.', checks: [], geometry: [], errors: []};
function check(name, actual, expected) { assert.deepEqual(actual, expected, name); evidence.checks.push(name); }
(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, hasTouch: true});
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => evidence.errors.push(error.message));
  try {
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'account.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, url.pathname.slice(1))), contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN" data-portal-theme="auto"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + css.map(href => `<link rel="stylesheet" href="${href}">`).join('') + '</head><body class="portalBoardShell settingsPage portalManagementPage"><main class="settingsShell portalManagementShell portalPageFrame"></main></body></html>'});
    });
    await page.goto('http://account.fixture.invalid');
    await page.evaluate(() => {
      window.PortalI18n = {t: text => text};
      window.directoryCalls = []; window.lateResponses = {}; window.denyChild = false;
      window.PortalInstructionClient = {execute: async name => {
        switch (name) {
          case 'ListProviders': return {providers: [{provider: 'codex', title: 'Codex', kinds: ['subscription_login'], account_directory_prefix: '/host/accounts/codex/'}]};
          case 'ListProviderAccounts': return {accounts: []};
          case 'ListBackendRoles': return {roles: []};
          case 'ListModelApis': return {apis: []};
          default: throw Error(name);
        }
      }};
      window.PortalAPI = {requestResult: async (url, options) => {
        const query = new URL(url, location.href).searchParams;
        if (query.get('action') !== 'browse') throw Error('Unexpected directory action');
        const p = query.get('path') || '/host';
        window.directoryCalls.push({path: p, method: options.method});
        // Deliberately ignore AbortSignal: stale results must still be discarded.
        if (p === '/slow' || p === '/closed-late') await new Promise(resolve => { window.lateResponses[p] = resolve; });
        if (p === '/missing' || p === '/denied' || (window.denyChild && p === '/host/alpha')) return {response: {ok: false}, payload: {error: p === '/missing' ? '主机目录不存在' : '主机目录不可读', code: 'forbidden'}};
        const names = p === '/host' ? ['alpha', 'beta', 'gamma', ...Array.from({length: 25}, (_, i) => `project-${String(i).padStart(2, '0')}`)] : p === '/host/alpha' ? ['nested'] : p === '/' ? ['host'] : p === '/truncated' ? ['visible'] : [];
        return {response: {ok: true}, payload: {path: p, parent: p === '/' ? '/' : p.slice(0, p.lastIndexOf('/')) || '/', directories: names.map(name => ({name, path: (p === '/' ? '' : p) + '/' + name})), roots: [{name: '/', path: '/'}], truncated: p === '/truncated'}};
      }};
    });
    for (const name of ['portal_state', 'portal_ui_primitives', 'portal_overlay', 'portal_ui']) await page.addScriptTag({url: `/static/${name}.js`});
    await page.evaluate(async () => {const module = await import('/static/portal_management_backend.js'); window.fixturePanel = await module.mount(document.querySelector('main'), {me: {access: {permission_system: 'open'}}});});
    await page.getByRole('button', {name: '接入订阅帐号', exact: true}).click();
    const registration = page.getByRole('dialog', {name: '新增执行账号'}), field = registration.locator('[name=account_dir]');
    await field.fill('/host');
    const browse = registration.getByRole('button', {name: '浏览', exact: true});
    const picker = page.getByRole('dialog', {name: '选择文件夹', exact: true});
    const button = name => picker.getByRole('button', {name, exact: true});
    const option = name => picker.getByRole('option', {name, exact: true});
    const list = picker.getByRole('listbox'), pending = picker.getByLabel('待选路径'), feedback = picker.locator('.backendDirectoryFeedback [role=status]');
    const loaded = p => page.waitForFunction(expected => {
      const el = document.querySelector('.backendDirectoryPicker');
      return el?.querySelector('output')?.textContent === expected && !el.querySelector('.backendDirectoryCommit button:last-child').disabled;
    }, p);
    const navigate = async p => { await picker.press('Control+l'); await picker.getByRole('textbox', {name: '目录路径', exact: true}).fill(p); await picker.getByRole('textbox', {name: '目录路径', exact: true}).press('Enter'); };
    async function geometry(label) {
      const result = await picker.evaluate(el => {
        const rect = n => {const r = n.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom, right: r.right};};
        return {dialog: rect(el), list: rect(el.querySelector('[role=listbox]')), choose: rect(el.querySelector('.backendDirectoryCommit button:last-child')), viewport: {width: innerWidth, height: innerHeight}, rootFont: getComputedStyle(document.documentElement).fontSize, background: getComputedStyle(el).backgroundColor, horizontalOverflow: el.scrollWidth > el.clientWidth};
      });
      evidence.geometry.push({label, ...result});
      check(`${label}: dialog and choose fit viewport`, result.dialog.bottom <= result.viewport.height && result.dialog.right <= result.viewport.width && result.dialog.y >= 0 && result.choose.bottom <= result.viewport.height, true);
      check(`${label}: no horizontal overflow`, result.horizontalOverflow, false);
      check(`${label}: theme background applied`, result.background !== 'rgba(0, 0, 0, 0)', true);
      check(`${label}: choose touch height`, result.choose.height >= (result.viewport.width < 540 ? 44 : 36), true);
      await page.screenshot({path: path.join(out, label + '.png')});
    }
    await browse.click(); await loaded('/host'); await geometry('01-desktop');
    await option('alpha').click(); check('single click selects child', await pending.textContent(), '/host/alpha'); check('browsing does not submit', await field.inputValue(), '/host');
    check('selection highlighted', await option('alpha').getAttribute('aria-selected'), 'true'); await geometry('01a-desktop-selected');
    await option('alpha').dblclick(); await loaded('/host/alpha'); check('double click enters', await option('nested').count(), 1);
    await button('后退').click(); await loaded('/host'); await button('前进').click(); await loaded('/host/alpha'); await button('上一级').click(); await loaded('/host');
    evidence.checks.push('mouse back/forward/up');
    await list.focus(); await list.press('End'); check('End selects last', await pending.textContent(), '/host/project-24');
    await list.press('Home'); await list.press('ArrowDown'); check('ArrowDown selects next', await pending.textContent(), '/host/beta');
    await list.press('ArrowUp'); await list.press('Enter'); await loaded('/host/alpha'); evidence.checks.push('Home/ArrowUp/Enter navigation');
    await picker.getByRole('navigation', {name: '目录路径'}).getByRole('button', {name: 'host', exact: true}).click(); await loaded('/host');
    await picker.getByRole('searchbox').fill('beta'); check('filter matches', await picker.getByRole('option').count(), 1);
    await option('beta').click(); await button('选择当前目录').click(); check('current path selection', await pending.textContent(), '/host');
    await picker.getByRole('searchbox').fill('not-present'); check('empty filter feedback', await feedback.textContent(), '没有匹配的文件夹。');
    await navigate('/missing'); await feedback.filter({hasText: '主机目录不存在'}).waitFor();
    check('invalid path cannot submit', await button('选择文件夹').isDisabled(), true); check('no fallback directory', await picker.getByRole('option').count(), 0);
    await button('后退').click(); await loaded('/host');
    await navigate('/denied'); await feedback.filter({hasText: '主机目录不可读'}).waitFor(); check('unreadable path cannot submit', await button('选择文件夹').isDisabled(), true);
    await button('主目录').click(); await loaded('/host');
    await navigate('/slow'); await page.waitForFunction(() => !!window.lateResponses['/slow']);
    await button('主目录').click(); await loaded('/host'); await page.evaluate(() => window.lateResponses['/slow']()); await page.waitForTimeout(50);
    check('late read cannot replace current path', await pending.textContent(), '/host');
    await navigate('/slow'); await picker.press('Control+l');
    const editingPath = picker.getByRole('textbox', {name: '目录路径', exact: true});
    await editingPath.fill('/manual'); await page.evaluate(() => window.lateResponses['/slow']()); await page.waitForTimeout(50);
    check('late read cannot erase address editing', await editingPath.inputValue(), '/manual'); check('edited address not selectable before read', await button('选择文件夹').isDisabled(), true);
    await editingPath.press('Enter'); await loaded('/manual');
    await navigate('/empty'); await loaded('/empty'); check('empty directory feedback', await feedback.textContent(), '此目录没有子目录。');
    await navigate('/truncated'); await loaded('/truncated'); check('truncated feedback', await feedback.textContent(), '目录较多，仅显示部分；可输入完整路径。');
    await button('主目录').click(); await loaded('/host'); await option('alpha').click(); await page.evaluate(() => {window.denyChild = true;});
    await button('选择文件夹').click(); await feedback.filter({hasText: '主机目录不可读'}).waitFor();
    check('confirm rechecks selected directory', await field.inputValue(), '/host'); check('failed confirm disables commit', await button('选择文件夹').isDisabled(), true);
    await page.evaluate(() => {window.denyChild = false;}); await option('beta').click(); await button('选择文件夹').click(); await picker.waitFor({state: 'detached'});
    check('confirm commits server path', await field.inputValue(), '/host/beta'); check('focus restored to browse', await browse.evaluate(el => el === document.activeElement), true);
    await browse.click(); await loaded('/host/beta'); await button('主目录').click(); await loaded('/host'); await button('选择文件夹').click(); await picker.waitFor({state: 'detached'}); check('current directory confirm', await field.inputValue(), '/host');
    await browse.click(); await loaded('/host'); await navigate('/closed-late'); await page.waitForFunction(() => !!window.lateResponses['/closed-late']); await button('取消').click();
    await page.evaluate(() => window.lateResponses['/closed-late']()); await page.waitForTimeout(50);
    check('closed dialog ignores late results', await picker.count(), 0); check('cancel preserves value', await field.inputValue(), '/host');
    await page.setViewportSize({width: 390, height: 844}); await page.evaluate(() => {document.documentElement.style.fontSize = '20px';});
    await browse.click(); await loaded('/host'); await geometry('02-narrow-font20');
    check('touch row is at least 44px', (await option('alpha').boundingBox()).height >= 44, true);
    await option('alpha').tap(); await geometry('02a-narrow-selected'); await button('打开目录').tap(); await loaded('/host/alpha'); evidence.checks.push('explicit touch enter, no double tap');
    await button('上一级').tap(); await loaded('/host'); await option('beta').tap(); await button('选择文件夹').tap(); await picker.waitFor({state: 'detached'}); check('touch selection', await field.inputValue(), '/host/beta');
    await browse.click(); await loaded('/host/beta'); await button('上一级').tap(); await loaded('/host');
    await list.focus(); await list.press('Home'); await list.press('Enter'); await loaded('/host/alpha'); await button('后退').tap(); await loaded('/host'); await geometry('03-narrow-return');
    await button('取消').tap(); check('touch cancel preserves value', await field.inputValue(), '/host/beta');
    await browse.click(); await loaded('/host/beta'); await picker.press('Escape'); await picker.waitFor({state: 'detached'}); check('Escape restores focus', await browse.evaluate(el => el === document.activeElement), true);
    evidence.requests = await page.evaluate(() => window.directoryCalls); check('browse never writes', evidence.requests.every(call => call.method === 'GET'), true); check('no script errors', evidence.errors, []);
    await page.evaluate(() => window.fixturePanel.destroy()); evidence.status = 'passed'; console.log(`Folder picker real component browser passed (${evidence.checks.length} checks): ${out}`);
  } catch (error) {
    evidence.status = 'failed'; evidence.failure = error.stack; await page.screenshot({path: path.join(out, 'failure.png')}); throw error;
  } finally { fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(evidence, null, 2)); await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
