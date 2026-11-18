'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '../../../management_portal/static');
const {chromium} = require(process.env.PORTAL_UI_PLAYWRIGHT || path.join(root, '../test_framework/tau_runtime/node_modules/playwright'));
const {RESIDENT_MENU_SCRIPTS} = require('./resident_menu_scripts.cjs');
const {PORTAL_COMPONENTS_STYLES} = require('./portal_components_styles.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1100, height: 850}});
    page.setDefaultTimeout(5000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('http://fixture.test/**', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, name.slice(8))), contentType: 'text/javascript; charset=utf-8'});
      return route.fulfill({body: '<!doctype html><html lang="zh-CN" data-portal-theme="light"><head><meta charset="utf-8"></head><body></body></html>', contentType: 'text/html; charset=utf-8'});
    });
    await page.goto('http://fixture.test/');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) {
      await page.addStyleTag({path: path.join(root, name + '.css')});
    }
    for (const name of ['portal_text', 'portal_state', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_detail_surface', ...RESIDENT_MENU_SCRIPTS]) {
      await page.addScriptTag({path: path.join(root, name + '.js')});
    }
    await page.evaluate(() => {
      window.calls = []; window.approve = false; window.deferSave = false; window.slowRead = false;
      PortalUI.confirm = async () => approve;
      window.PortalSocietyService = {createClient: () => ({})};
      window.PortalAvatarEditor = {mount: () => ({close() {}, destroy() {}, isBusy: () => false})};
      window.PortalResidentProjection = {invalidate() {}, subscribe: () => () => {}};
      window.PortalResidentRuntimeController = {invalidate() {}, bind: () => ({mount: async () => {}, destroy() {}})};
      window.PortalResidentService = {runtimeSettings: async () => ({settings: {}, settings_revision: 'rev1'}), projection: async () => ({allows: {}})};
      window.PortalChatWorkspace = {mountResidentHarnessPanel: () => ({destroy() {}})};
      window.PortalInstructionClient = {execute: async name => {throw Error('Unexpected instruction ' + name);}};
      const config = {schema: 'tfi.resident-capabilities.v1', tools: [], skills: [], mcp: []};
      window.PortalAPI = {
        get: async url => {
          calls.push(['get', url]);
          const id = new URL(url, location.href).searchParams.get('resident_id');
          const result = {applications: [{ref: 'application:paper2', name: 'Paper2', description: '论文工具', input_schema: {type: 'object'}}], resident: {resident_id: id, can_edit: true, settings_revision: 'rev1', capability_config: structuredClone(config)}};
          if (slowRead) { slowRead = false; await new Promise(resolve => {window.finishRead = resolve;}); }
          return result;
        },
        request: async (url, options) => {
          calls.push(['save', structuredClone(options.body)]);
          if (deferSave) await new Promise(resolve => {window.finishSave = resolve;});
          return {settings: {capability_config: options.body.capability_config}, settings_revision: 'rev2'};
        },
      };
      window.openResident = async id => {
        const node = {resident_id: id, id, title: id.endsWith('aaa') ? '视觉研究' : '方案评审', lineage_id: 'fixture', intake_state: 'open'};
        window.menu = await PortalResidentMenu.open({node, lineage: {lineage_id: 'fixture', nodes: [node]}, tab: 'tools'});
      };
    });
    const a = 'resident_aaaaaaaaaaaa', b = 'resident_bbbbbbbbbbbb';
    await page.evaluate(id => openResident(id), a);
    const tools = page.locator('#nodeToolsPanel');
    await tools.getByText('Paper2', {exact: true}).waitFor();
    assert.equal(await tools.locator('input[type=search]').count(), 0);
    assert.deepEqual(await page.evaluate(() => calls), [['get', '/api/tool-settings?resident_id=' + a]]);
    await tools.getByRole('button', {name: '注册', exact: true}).click();
    await page.locator('#nodeHarnessTab').click();
    assert.equal(await tools.isVisible(), true, 'cancelled discard retains tools and draft');
    await page.evaluate(id => openResident(id), b);
    assert.match(await tools.textContent(), new RegExp(a), 'cancelled target switch retains original owner');
    await page.locator('[data-portal-detail-close]').click();
    assert.equal(await tools.isVisible(), true, 'cancelled close retains draft');
    await tools.getByRole('button', {name: '保存工具设置'}).click();
    await page.waitForFunction(() => calls.some(row => row[0] === 'save'));
    assert.equal(await page.evaluate(() => calls.find(row => row[0] === 'save')[1].resident_id), a);
    await page.evaluate(id => openResident(id), b);
    await tools.getByRole('button', {name: '注册', exact: true}).waitFor();
    assert.match(await tools.textContent(), new RegExp(b));
    await tools.getByRole('button', {name: '注册', exact: true}).click();
    await page.evaluate(() => {approve = true; deferSave = true;});
    await tools.getByRole('button', {name: '保存工具设置'}).click();
    await page.waitForFunction(() => !!window.finishSave);
    await page.locator('#nodeHarnessTab').click();
    await page.locator('[data-portal-detail-close]').click();
    await page.evaluate(id => openResident(id), a);
    assert.equal(await tools.isVisible(), true, 'saving blocks leaving even when discard would be approved');
    assert.match(await tools.textContent(), new RegExp(b));
    await page.evaluate(() => {deferSave = false; finishSave();});
    await tools.getByText('配置已保存；旧 Session 的实际装配状态待确认。新设置按后端应用时机生效。', {exact: true}).waitFor();
    assert.equal(await page.evaluate(() => calls.filter(row => row[0] === 'save').at(-1)[1].resident_id), b);
    await page.locator('#nodeHarnessTab').click();
    await page.locator('#nodeHarnessTab').focus(); await page.keyboard.press('ArrowDown');
    await tools.getByRole('button', {name: '注册', exact: true}).waitFor();
    // A pending old read cannot replace a newly opened Resident's editor.
    await page.evaluate(() => {slowRead = true;});
    await page.evaluate(id => openResident(id), a);
    await page.evaluate(id => openResident(id), b);
    await tools.getByText('Paper2', {exact: true}).waitFor();
    await page.evaluate(() => finishRead());
    assert.match(await tools.textContent(), new RegExp(b));
    for (const [width, theme] of [[1100, 'light'], [390, 'dark']]) {
      await page.setViewportSize({width, height: 850});
      await page.evaluate(theme => document.documentElement.dataset.portalTheme = theme, theme);
      assert.equal(await tools.evaluate(node => node.scrollWidth <= node.clientWidth + 1), true, 'tools fit the Resident menu');
      if (process.env.PORTAL_TOOL_EVIDENCE) {
        fs.mkdirSync(process.env.PORTAL_TOOL_EVIDENCE, {recursive: true});
        await page.screenshot({path: path.join(process.env.PORTAL_TOOL_EVIDENCE, `resident-tools-${width}.png`)});
      }
    }
    await tools.getByRole('button', {name: '注册', exact: true}).click();
    await page.locator('[data-portal-detail-close]').click();
    await page.locator('#nodeSettingsModal').waitFor({state: 'hidden'});
    assert.equal(await tools.textContent(), '', 'confirmed close disposes the tool editor');
    assert.deepEqual(errors, []);
    console.log('PASS: Resident menu owns tool edits, direct target, save/close/switch guards, stale reads, keyboard navigation and responsive layout');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
