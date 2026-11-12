'use strict';
// Real message rendering, file-address conversion and image lightbox. Only file
// bytes and the signed-in session are supplied by the isolated HTTP fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
const file = '/share/Projects/示例/residents/resident_0123456789ab/workspace/图片/首页 对比 & 建议.jpg';
const denied = '/share/Projects/私有/不可读.png';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1sAAAAASUVORK5CYII=', 'base64');
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_preview_contract.js', 'portal_file_links.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_workbench_context.js',
  ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const reads = [], serverErrors = [];
const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<link rel="stylesheet" href="/static/portal_components.css">
<link rel="stylesheet" href="/static/portal_image_editor.css">
<main id="chat"></main><script>${input.routeScript}</script>
${scripts.map(name => '<script src="/static/' + name + '"></script>').join('')}</html>`;
const server = http.createServer((request, response) => {
  try {
    const url = new URL(request.url, 'http://fixture');
    if (url.pathname === '/api/agent-workbench/link-file-raw') {
      assert.equal(request.method, 'GET');
      assert.equal(request.headers.cookie, 'fixture_session=isolated');
      const requested = url.searchParams.get('path');
      reads.push(requested);
      if (requested === denied) { response.writeHead(403); return response.end(); }
      assert.equal(requested, file);
      response.writeHead(200, {'Content-Type': 'image/png'});
      return response.end(png);
    }
    if (url.pathname.startsWith('/static/')) {
      const name = url.pathname.slice('/static/'.length);
      assert.ok(!name.includes('..'));
      response.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
      return response.end(fs.readFileSync(path.join(root, name)));
    }
    response.setHeader('Content-Type', 'text/html');
    response.end(html);
  } catch (error) { serverErrors.push(error.message); response.writeHead(500); response.end(); }
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext({viewport: {width: 1000, height: 800}});
    await context.addCookies([{name: 'fixture_session', value: 'isolated', url: origin}]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.evaluate(({file, denied}) => {
      window.copied = [];
      PortalUI.copyText = text => { copied.push(text); };
      const urlFor = PortalFileLinks.imagePreviewUrl;
      const check = (value, label) => { if (!value) throw Error(label); };
      const mapped = new URL(urlFor(file), location.origin);
      check(mapped.pathname === '/api/agent-workbench/link-file-raw' && mapped.searchParams.get('path') === file && !mapped.hash,
        'workspace image has one encoded, authenticated byte URL');
      for (const address of ['notes.txt', '/share/main.tex', 'relative/image.png', 'https://other.invalid/photo.png', 'javascript:alert(1)']) {
        check(urlFor(address) === '', 'nonimage or unresolved address stays outside local image mapping');
      }
      check(urlFor('/srv/uploads/upload_0123456789ab/photo.png') === '/api/uploads/upload_0123456789ab', 'upload keeps its owner');
      check(urlFor('/srv/resident_chat_images/a/photo.png').includes('chat-attachments/image?file_id=a%2Fphoto.png'), 'old image keeps its owner');
      window.chat = PortalChatWorkspace.createWorkspace({root: document.querySelector('#chat'), id: 'image-chat',
        historyScope: () => 'local-image-fixture', refs: false, can_attach: false, previewUrlForPath: urlFor});
      chat.setMessages([{id: 'image-message', role: 'assistant', content: '首页建议\n\n[image: ' + file + ']\n\n[image: ' + denied + ']'}]);
    }, {file, denied});
    const imageRef = page.locator('[data-message-attachment-kind="image"]').first();
    await imageRef.locator('img').evaluate(img => img.decode());
    await imageRef.click();
    const overlay = page.locator('#portalImageEditorLightbox');
    await overlay.waitFor({state: 'visible'});
    await overlay.locator('[data-lightbox-image]').evaluate(img => img.decode());
    assert.equal(await overlay.locator('[data-lightbox-image]').evaluate(img => img.naturalWidth > 0), true);
    assert.equal(await page.locator('#portalImageEditorLightbox').count(), 1);
    const box = await overlay.locator('.imageLightboxContent').boundingBox();
    assert.ok(Math.abs(box.x + box.width / 2 - 500) < 3, 'image viewer is centered in the page');
    assert.deepEqual(await page.evaluate(() => copied), [], 'click opens instead of copying a path');
    await page.keyboard.press('Escape');
    await overlay.waitFor({state: 'hidden'});
    await page.locator('[data-message-attachment-kind="image"]').nth(1).click();
    await page.waitForFunction(() => document.querySelector('[data-image-load-status]').textContent === '图片加载失败');
    assert.equal(await overlay.locator('[data-image-draw-save]').isDisabled(), true);
    await page.keyboard.press('Escape');
    await overlay.waitFor({state: 'hidden'});
    assert.ok(reads.includes(file) && reads.includes(denied));
    assert.equal(page.url(), origin + '/');
    assert.deepEqual(errors, []);
    assert.deepEqual(serverErrors, []);
    console.log('PASS local image reference thumbnail, authorized bytes, centered lightbox, close, and visible permission failure');
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
