'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(process.env.PORTAL_UI_PLAYWRIGHT || path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const source = path.join(root, 'management_portal/static/portal_tool_settings.js');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 900, height: 800}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<main id="host"></main>');
    for (const name of ['portal_state', 'portal_text', 'portal_i18n']) await page.addScriptTag({path: path.join(root, `management_portal/static/${name}.js`)});
    await page.evaluate(() => {
      window.calls = []; window.confirmResult = false; window.conflict = false; window.invalidations = [];
      window.PortalUI = {confirm: async () => window.confirmResult};
      window.PortalResidentProjection = {invalidate: id => invalidations.push(['projection', id])};
      window.PortalResidentRuntimeController = {invalidate: id => invalidations.push(['runtime', id])};
      window.config = {schema: 'tfi.resident-capabilities.v1', skills: [{ref: 'skill_hidden', enabled: true, required: false}], tools: [{ref: 'builtin:hidden', enabled: true, required: true}], mcp: [{ref: 'article_connection/file/mcp.json', enabled: false, required: false, tools: ['legacy']}]};
      const response = id => ({schema: 'tfi.tool-settings.v1', applications: [{ref: 'application:paper2', name: 'Paper2', description: '<img src=x onerror=alert(1)>', input_schema: {type: 'object'}}], ...(id ? {resident: {resident_id: id, title: id, can_edit: id !== 'readonly', settings_revision: 'rev1', capability_config: structuredClone(config)}} : {})});
      window.PortalAPI = {
        get: async (url, options) => {
          calls.push(['get', url]); const id = url.includes('?') ? decodeURIComponent(url.split('=')[1]) : '';
          if (id === 'slow') return new Promise(resolve => { window.resolveSlow = () => resolve(response(id)); });
          return response(id);
        },
        post: async (url, body) => {
          calls.push(['discover', body]);
          return {ref: body.ref, title: 'My connection', tools: [{name: 'custom.read', description: 'Read custom data', inputSchema: {type: 'object'}}]};
        },
        request: async (url, options) => {
          calls.push(['save', structuredClone(options.body)]);
          if (conflict) throw Object.assign(new Error('conflict'), {status: 409});
          if (window.deferSave) await new Promise(resolve => {window.resolveSave = resolve;});
          return {settings: {capability_config: options.body.capability_config}, settings_revision: 'rev2', application: {items: []}};
        },
      };
      window.PortalInstructionClient = {execute: async (name, args) => {calls.push(['search', args]); return {residents: [{resident_id: args.q, title: args.q}]};}};
    });
    await page.addScriptTag({path: source});
    const mount = () => page.evaluate(() => {window.panel = PortalToolSettings.mount(document.querySelector('#host'));});
    const select = async id => {
      await page.getByPlaceholder('搜索 Resident 名称或 ID').fill(id);
      await page.getByRole('button', {name: `${id} · ${id}`, exact: true}).click();
      await page.waitForFunction(id => document.querySelector('#host strong')?.textContent.includes(id), id);
    };
    if (process.argv.includes('--corrections')) {
      await page.evaluate(() => {
        window.confirmations = [];
        PortalUI.confirm = async options => { confirmations.push(options); return confirmResult; };
        config.tools.push({ref: 'application:retired', enabled: true, required: true},
          {ref: 'application:unavailable', enabled: false, required: false});
        window.pendingDiscovery = [];
        PortalAPI.post = (url, body, options) => new Promise((resolve, reject) => {
          pendingDiscovery.push({ref: body.ref, signal: options.signal, resolve, reject});
        });
      });
      await mount(); await select('editable');
      const retired = page.locator('.toolSettingsCard').filter({hasText: 'application:retired'});
      const unavailable = page.locator('.toolSettingsCard').filter({hasText: 'application:unavailable'});
      assert.match(await retired.textContent(), /当前目录未提供/);
      assert.equal(await retired.locator('details').count(), 0);
      assert.equal(await unavailable.getByLabel('启用', {exact: true}).isChecked(), false);
      assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), false);
      assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'save').length), 0);
      await retired.getByLabel('启用', {exact: true}).uncheck();
      assert.equal(await retired.getByLabel('必须可用').isChecked(), false);
      await unavailable.getByRole('button', {name: '撤销注册'}).click();
      await page.evaluate(() => {conflict = true;});
      await page.getByRole('button', {name: '保存工具设置'}).click();
      const status = page.locator('#host > [role="status"]');
      await status.filter({hasText: '草稿已保留'}).waitFor();
      assert.match(await status.textContent(), /重新加载最新设置后重新选择/);
      assert.doesNotMatch(await status.textContent(), /重试保存/);
      assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), true);
      const submitted = await page.evaluate(() => calls.filter(c => c[0] === 'save').at(-1)[1]);
      assert.deepEqual(submitted.capability_config.tools.find(row => row.ref === 'application:retired'),
        {ref: 'application:retired', enabled: false, required: false});
      assert.equal(submitted.capability_config.tools.some(row => row.ref === 'application:unavailable'), false);
      const reads = await page.evaluate(() => calls.filter(c => c[0] === 'get').length);
      await page.getByRole('button', {name: '重新加载'}).click();
      assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), true);
      assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'get').length), reads);
      assert.equal(await page.evaluate(() => confirmations.length), 1);
      await page.evaluate(() => {confirmResult = true; conflict = false;});
      await page.getByRole('button', {name: '重新加载'}).click();
      await retired.getByLabel('启用', {exact: true}).waitFor();
      assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), false);
      assert.equal(await retired.getByLabel('启用', {exact: true}).isChecked(), true);
      assert.equal(await page.evaluate(() => confirmations.length), 2);
      const custom = page.getByLabel('添加自定义 MCP 连接资源地址');
      const discover = page.getByRole('button', {name: '发现工具', exact: true});
      await custom.fill('connection-a'); await discover.click(); await discover.click();
      await custom.fill('connection-b'); await discover.click();
      assert.deepEqual(await page.evaluate(() => pendingDiscovery.map(row => row.signal.aborted)), [true, false, false]);
      // Deliberately ignore abort in the mock: stale success/error must still be rejected by the owner.
      await page.evaluate(() => {
        pendingDiscovery[1].resolve({ref: 'connection-a', title: 'Latest A', tools: [{name: 'a.latest'}]});
        pendingDiscovery[2].resolve({ref: 'connection-b', title: 'Independent B', tools: [{name: 'b.latest'}]});
      });
      await page.getByLabel('a.latest', {exact: true}).waitFor();
      await page.getByLabel('b.latest', {exact: true}).waitFor();
      await page.evaluate(async () => {
        pendingDiscovery[0].resolve({ref: 'connection-a', title: 'Stale A', tools: [{name: 'a.stale'}]});
        await new Promise(resolve => setTimeout(resolve, 0));
      });
      assert.equal(await page.getByText('Stale A', {exact: true}).count(), 0);
      await page.getByLabel('a.latest', {exact: true}).waitFor();
      await custom.fill('connection-a'); await discover.click(); await discover.click();
      await page.evaluate(() => pendingDiscovery[4].resolve({ref: 'connection-a', title: 'Newest A', tools: [{name: 'a.newest'}]}));
      await page.getByLabel('a.newest', {exact: true}).waitFor();
      await page.evaluate(async () => {pendingDiscovery[3].reject(new Error('stale failure')); await new Promise(resolve => setTimeout(resolve, 0));});
      assert.doesNotMatch(await status.textContent(), /stale failure/);
      await page.getByLabel('b.latest', {exact: true}).waitFor();
      assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), false);
      assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'save').length), 1);
      assert.deepEqual(errors, []);
      console.log('PASS corrections: same-ref cancellation plus stale success/error guard; independent refs; 409 draft preserved and reload deny/confirm; missing catalog application visibility, no schema/autoactivation, disable/revoke in explicit CAS');
      return;
    }
    await mount();
    await page.getByText('Paper2', {exact: true}).waitFor();
    assert.equal(await page.locator('img').count(), 0);
    assert.equal(await page.getByRole('button', {name: '注册', exact: true}).isDisabled(), true);
    assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'save' || c[0] === 'discover').length), 0);
    await select('readonly');
    assert.equal(await page.getByRole('button', {name: '注册', exact: true}).isDisabled(), true);
    await select('editable');
    await page.getByRole('button', {name: '注册', exact: true}).click();
    const app = page.locator('.toolSettingsCard').filter({hasText: 'Paper2'});
    await app.getByLabel('必须可用').check(); await app.getByLabel('启用', {exact: true}).uncheck();
    assert.equal(await app.getByLabel('必须可用').isChecked(), false);
    assert.equal(await app.getByLabel('必须可用').isDisabled(), true);
    await page.getByPlaceholder('搜索 Resident 名称或 ID').fill('other');
    await page.getByRole('button', {name: 'other · other', exact: true}).click(); // confirmation denies
    assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), true);
    assert.match(await page.locator('#host > strong').textContent(), /editable/);
    await page.getByRole('button', {name: '保存工具设置'}).click();
    const first = await page.evaluate(() => calls.filter(c => c[0] === 'save').at(-1)[1]);
    assert.equal(first.resident_id, 'editable'); assert.equal(first.expected_revision, 'rev1');
    assert.deepEqual(first.capability_config.skills, [{ref: 'skill_hidden', enabled: true, required: false}]);
    assert.deepEqual(first.capability_config.tools[0], {ref: 'builtin:hidden', enabled: true, required: true});
    assert.deepEqual(first.capability_config.mcp[0].tools, ['legacy']);
    assert.deepEqual(first.capability_config.tools[1], {ref: 'application:paper2', enabled: false, required: false});
    await page.getByLabel('添加自定义 MCP 连接资源地址').fill('article_custom/file/mcp.json');
    await page.getByRole('button', {name: '发现工具', exact: true}).click();
    assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), false);
    await page.getByLabel('custom.read', {exact: true}).check();
    await page.evaluate(() => {window.conflict = true;});
    await page.getByRole('button', {name: '保存工具设置'}).click();
    await page.getByRole('status').filter({hasText: '草稿已保留'}).waitFor();
    assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), true);
    await page.getByRole('button', {name: '重新加载'}).click();
    assert.equal(await page.evaluate(() => panel.hasUnsavedChanges()), true);
    await page.evaluate(() => {window.conflict = false;});
    await page.getByRole('button', {name: '保存工具设置'}).click();
    const customSaved = await page.evaluate(() => calls.filter(c => c[0] === 'save').at(-1)[1]);
    assert.equal(customSaved.expected_revision, 'rev2');
    assert.deepEqual(customSaved.capability_config.mcp[1].tools, ['custom.read']);
    await app.getByRole('button', {name: '撤销注册'}).click();
    await page.evaluate(() => {window.deferSave = true;});
    await page.getByRole('button', {name: '保存工具设置'}).click();
    await page.waitForFunction(() => !!window.resolveSave);
    await page.evaluate(() => {panel.destroy(); resolveSave();});
    await page.waitForFunction(() => invalidations.length === 6);
    assert.equal(await page.locator('#host').textContent(), '');
    await mount();
    await select('slow');
    await select('fast');
    await page.evaluate(() => resolveSlow());
    assert.match(await page.locator('#host > strong').textContent(), /fast/);
    await page.evaluate(() => panel.destroy());
    await mount();
    await page.getByPlaceholder('搜索 Resident 名称或 ID').fill('abc');
    await page.getByPlaceholder('搜索 Resident 名称或 ID').fill('abcd');
    await page.getByPlaceholder('搜索 Resident 名称或 ID').fill('abcde');
    await page.getByRole('button', {name: 'abcde · abcde', exact: true}).waitFor();
    assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'search' && ['abc', 'abcd', 'abcde'].includes(c[1].q)).length), 1);
    assert.deepEqual(errors, []);
    console.log('PASS: directory, XSS text, readonly, explicit registration/disable/revoke, required invariant, draft protection, complete config preservation, discover-only and exact selection, CAS409/retry, cache invalidation, destroyed write, stale read, throttled search');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
