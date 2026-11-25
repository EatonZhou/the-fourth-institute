'use strict';
// Real composer, instruction client, XHR and clipboard; isolated HTTP uploads.
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
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_api.js', 'portal_instruction_client.js',
  'portal_overlay.js', 'portal_preview_contract.js', 'portal_resource_chips.js', 'portal_file_links.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const uploads = new Map();
const instructions = [];
const errors = [];
const server = http.createServer(async (request, response) => {
  try {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    const send = value => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(value)); };
    if (name === 'api/instructions/execute') {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const call = JSON.parse(Buffer.concat(chunks)); instructions.push(call.instruction);
      const args = call.arguments;
      if (call.instruction === 'CreateUpload') {
        const id = 'upload_' + String(uploads.size + 1).padStart(24, '0');
        uploads.set(id, { args });
        return send({ status: 'committed', result: { upload_id: id, upload_url: '/api/uploads/' + id } });
      }
      assert.equal(call.instruction, 'ReadResource');
      const id = args.address.upload_id, upload = uploads.get(id);
      assert.ok(upload.bytes);
      return send({ status: 'committed', result: { name: upload.args.file_name,
        content_type: upload.args.media_type, size: upload.bytes.length,
        kind: upload.args.media_type.startsWith('image/') ? 'image' : 'text',
        content_url: '/api/uploads/' + id, file_reference: { upload_id: id } } });
    }
    if (name.startsWith('api/uploads/')) {
      const upload = uploads.get(name.split('/').at(-1)); assert.ok(upload);
      if (request.method === 'PUT') {
        const chunks = []; for await (const chunk of request) chunks.push(chunk);
        upload.bytes = Buffer.concat(chunks);
        assert.equal(upload.bytes.length, upload.args.size);
        return send({ ready: true });
      }
      response.setHeader('Content-Type', upload.args.media_type);
      return response.end(upload.bytes);
    }
    if (!name) {
      response.setHeader('Content-Type', 'text/html');
      return response.end('<!doctype html><meta charset="utf-8"><main id="chat"></main>'
        + '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join(''));
    }
    if (name === 'routes.js' || modules.includes(name)) {
      response.setHeader('Content-Type', 'text/javascript');
      return response.end(name === 'routes.js' ? routeScript : fs.readFileSync(path.join(repo, 'management_portal/static', name)));
    }
    response.writeHead(404); response.end();
  } catch (error) { errors.push(error.stack); response.writeHead(500); response.end(String(error)); }
});
(async () => {
  let browser;
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.stack));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.sent = [];
      window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'),
        id: 'empty-image', refs: false,
        contract: { schema: 'portal.chat_workspace.v3', messages: [],
          composer: { can_send: true, can_attach: true, queue: { items: [] } } },
        onSend: payload => sent.push(payload),
      });
      const clipboard = new DataTransfer();
      clipboard.items.add(new File([], 'image.png', { type: 'image/png' }));
      document.querySelector('.composerInput').dispatchEvent(new ClipboardEvent('paste', { bubbles: true, clipboardData: clipboard }));
    });
    await page.waitForFunction(() => workspace.attachments()[0]?.error?.includes('图片内容为空'));
    const tray = page.locator('.composerImageTray');
    assert.match(await tray.textContent(), /重新复制图片或选择原始图片文件/);
    assert.equal(await page.locator('#empty-imageSend').isDisabled(), true);
    await page.locator('.composerInput').focus();
    await page.keyboard.press('Control+Enter');
    assert.equal(await page.evaluate(() => sent.length), 0);
    assert.deepEqual(instructions, []); // No empty upload registered as success.
    await tray.locator('[data-remove-portal-composer-attachment]').click();
    await page.evaluate(async () => {
      await workspace.addFiles([new File([], 'selected.png', { type: 'image/png' })]);
    });
    assert.match(await tray.textContent(), /图片内容为空/);
    assert.deepEqual(instructions, []);
    await tray.locator('[data-remove-portal-composer-attachment]').click();

    // The normal OS clipboard path keeps real pixels through File -> XHR -> preview.
    await page.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = 7; canvas.height = 5;
      canvas.getContext('2d').fillRect(0, 0, 7, 5);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      // The OS clipboard may re-encode PNG. Compare the bytes the paste supplies.
      document.addEventListener('paste', event => {
        window.pastedBytes = event.clipboardData.files[0].arrayBuffer()
          .then(bytes => Array.from(new Uint8Array(bytes)));
      }, { capture: true, once: true });
    });
    await page.locator('.composerInput').focus();
    await page.keyboard.press('Control+V');
    await page.waitForFunction(() => workspace.attachments().length === 1 && !!workspace.attachments()[0].upload_id);
    const expected = await page.evaluate(() => pastedBytes);
    assert.ok(expected.length > 0);
    assert.equal(uploads.size, 1);
    const stored = [...uploads.values()][0];
    assert.deepEqual(stored.bytes, Buffer.from(expected));
    assert.equal(await page.locator('#empty-imageSend').isEnabled(), true);
    await page.waitForFunction(() => document.querySelector('.composerImageTray img')?.naturalWidth === 7);
    const attachment = await page.evaluate(() => workspace.attachments().map(({ file, ...rest }) => rest));
    assert.equal(attachment[0].size, expected.length);
    assert.equal(attachment[0].error, '');
    // Stored URL renders the same image after the local blob preview is gone.
    await page.evaluate(items => workspace.setAttachments(items.map(item => ({ ...item, preview_url: '', data_url: '' }))), attachment);
    await page.waitForFunction(() => document.querySelector('.composerImageTray img')?.naturalWidth === 7);
    const text = await page.evaluate(() => PortalInstructionClient.upload(new File([], 'empty.txt', { type: 'text/plain' }), { purpose: 'conversation_attachment' }));
    assert.equal(text.size, 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'passed', pngBytes: expected.length,
      checks: ['empty pasted and selected image blocked before HTTP', 'error blocks keyboard send',
        'native PNG clipboard bytes preserved by XHR', 'local and uploaded thumbnails decode', 'empty text remains valid'] }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
