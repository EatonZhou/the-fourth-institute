'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_preview_contract.js',
  'portal_resource_chips.js', 'portal_file_links.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(),
  'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const output = process.env.SCREENSHOT_DIR;
if (output) fs.mkdirSync(output, { recursive: true });
(async () => {
  const server = http.createServer((request, response) => {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('')
        + '<style>html,body{margin:0;background:var(--panel);color:var(--text)}#chat{height:500px;width:100%;max-width:880px;margin:auto}</style><main id="chat"></main>'
        + '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routeScript;
    else if (modules.includes(name) || styles.includes(name)) {
      body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
      if (name.endsWith('.css')) type = 'text/css';
    }
    response.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type });
    response.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  const result = { boundary: 'Native component with fixture uploads and preview callback; no real business data or User TAU.', checks: [], errors: [] };
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1000, height: 680 }, hasTouch: true });
    const page = await context.newPage();
    page.on('pageerror', error => result.errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'attachment-fixture' };
      window.opened = []; window.deleted = [];
      window.PortalPreview = { request: request => opened.push(request) };
      window.workspace = PortalChatWorkspace.createWorkspace({
        root: document.getElementById('chat'), id: 'attachment-presentation', refs: false,
        contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, can_attach: true, queue: { items: [] } } },
        uploadAttachment: (file, context) => new Promise((resolve, reject) => { window.upload = { resolve, reject, id: context.item.id }; }),
        deleteAttachment: item => { deleted.push(item.upload_id); return Promise.resolve({ deleted: true }); },
      });
      window.adding = workspace.addFiles([new File([new Uint8Array(1536)], 'hdl.zip', { type: 'application/zip' })]);
    });
    const tray = page.locator('.composerImageTray .composerImageChip');
    const inline = page.locator('.composerInput .composerImageRef');
    assert.equal(await tray.count(), 1); assert.equal(await inline.count(), 1);
    await page.evaluate(() => workspace.updateAttachment(upload.id, { progress: 37 }));
    assert.match(await tray.locator('.composerImagePath').textContent(), /37%/);
    await page.evaluate(async () => { upload.reject(new Error('上传失败，请重试')); await adding; });
    await tray.locator('[data-retry-portal-composer-attachment]').click();
    await page.evaluate(async () => {
      upload.resolve({ upload_id: 'upload_aaaaaaaaaaaaaaaaaaaaaaaa', path: '/api/uploads/upload_aaaaaaaaaaaaaaaaaaaaaaaa', filename: 'hdl.zip', content_type: 'application/zip', size: 1536 });
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(await tray.locator('.composerImagePath').textContent(), 'ZIP · 1.5 KB');
    assert.equal((await tray.textContent()).split('hdl.zip').length - 1, 1);
    assert.ok(!(await tray.textContent()).includes('/api/'));
    assert.equal(await tray.locator('[data-portal-icon="file"] svg').count(), 1);
    assert.equal(await inline.locator('[data-portal-icon="file"] svg').count(), 1);
    result.checks.push('two intentional representations; one name per item, native icons, readable status and size');
    await tray.locator('[data-portal-file-path]').click();
    await inline.locator('[data-portal-file-path]').focus();
    await page.keyboard.press('Enter');
    const opened = await page.evaluate(() => opened);
    assert.equal(opened.length, 2);
    assert.ok(opened.every(r => r.file_reference?.upload_id === 'upload_aaaaaaaaaaaaaaaaaaaaaaaa'));
    result.checks.push('both file labels use the same native preview reference; mouse and keyboard open');
    const screenshot = async name => { if (output) await page.locator('#chat').screenshot({ path: path.join(output, name + '.png') }); };
    await screenshot('desktop');
    await page.evaluate(() => workspace.updateAttachment(upload.id, { name: '一个用于检查窄屏省略和图标间距的非常长的附件文件名-hdl.zip' }));
    await page.setViewportSize({ width: 390, height: 700 });
    await page.evaluate(() => document.documentElement.style.fontSize = '20px');
    const geometry = await tray.evaluate(n => {
      const icon = n.querySelector('.composerFileThumb').getBoundingClientRect();
      const title = n.querySelector('.composerImageName').getBoundingClientRect();
      const remove = n.querySelector('[data-remove-portal-composer-attachment]').getBoundingClientRect();
      return { fits: n.scrollWidth <= n.clientWidth + 1, separated: icon.right <= title.left && title.right <= remove.left, removeWidth: remove.width, removeHeight: remove.height };
    });
    assert.ok(geometry.fits && geometry.separated);
    assert.ok(geometry.removeWidth >= 44 && geometry.removeHeight >= 44);
    assert.ok(await inline.evaluate(n => n.scrollWidth <= n.clientWidth + 1));
    await screenshot('narrow-font20');
    await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
    await screenshot('narrow-dark');
    await inline.locator('[data-remove-portal-composer-attachment]').tap();
    assert.equal(await tray.count(), 0); assert.equal(await inline.count(), 0);
    assert.deepEqual(await page.evaluate(() => deleted), ['upload_aaaaaaaaaaaaaaaaaaaaaaaa']);
    result.checks.push('390px/font20/coarse pointer fits, filename ellipsis without overlap, native removal from inline clears both');
    assert.deepEqual(result.errors, []);
    result.geometry = geometry; result.status = 'passed';
  } catch (error) {
    result.status = 'failed'; result.failure = String(error); process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
    if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  }
})();
