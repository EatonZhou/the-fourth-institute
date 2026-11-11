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
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_preview_contract.js', 'portal_file_links.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts(),
  'portal_chatroom_view.js'];
const styles = ['portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const server = http.createServer((req, res) => {
  const name = new URL(req.url, 'http://fixture').pathname.slice(1);
  let body, type = 'text/javascript';
  if (!name) {
    type = 'text/html';
    body = `<!doctype html><meta charset="utf-8">${styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('')}
      <style>#chat{height:650px;width:850px}</style><div id="chat"></div>
      <script src="/routes.js"></script>${modules.map(n => `<script src="/${n}"></script>`).join('')}`;
  } else if (name === 'routes.js') body = routeScript;
  else if (name === 'api/uploads/upload_aa') {
    type = 'image/svg+xml';
    body = '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="teal"/></svg>';
  }
  else if (modules.includes(name) || styles.includes(name)) {
    body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    if (styles.includes(name)) type = 'text/css';
  }
  res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body ?? 'not found');
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      const roomId = 'chatroom_111111111111';
      window.sent = [];
      window.PortalIdentity = { username: () => 'viewer' };
      window.PortalConversationStore = { subscribe: () => () => {}, isUnread: () => false };
      window.PortalRefreshCoordinator = { portalSubscription: () => () => {}, create: () => ({ start() {}, destroy() {}, invalidate() {} }) };
      window.PortalInstructionClient = { execute: async (name, args) => {
        if (name !== 'CreateChatroomMessage') throw Error('Unexpected instruction ' + name);
        sent.push(args);
        return { message: { message_id: `msg_reply_${sent.length}`, ordinal: sent.length + 10,
          author_id: 'viewer', role: 'user', content_parts: args.content_parts,
          quote: view.controller.quote() } };
      } };
      window.view = PortalChatroomView.mount({ root: document.getElementById('chat'), me: 'viewer' });
      view.room = { chatroom_id: roomId, title: '附件引用', members: ['viewer', 'alice'], permissions: { can_post: true } };
      view.chatroomId = roomId;
      view.mergeMessages(['image', 'file'].map((type, i) => ({ message_id: `msg_media_${type}`, ordinal: i + 1,
        author_id: type === 'image' ? 'alice' : 'resident_0123456789ab', role: type === 'image' ? 'user' : 'agent', content_parts: [{ type, name: type === 'image' ? '图.png' : '方案.pdf',
          upload_id: type === 'image' ? 'upload_aa' : 'upload_bb' }] })));
      view.apply();
    });
    for (const kind of ['image', 'file']) {
      const messageId = `msg_media_${kind}`;
      const label = kind === 'image' ? '[图片] 图.png' : '[文件] 方案.pdf';
      await page.locator(`[data-message-key="${messageId}"] .messageContent`).click({ button: 'right' });
      await page.locator('[data-portal-message-action="quote"]:visible').click();
      assert.equal(await page.locator('.composerQuoteText').textContent(), label);
      const author = kind === 'image' ? 'alice' : 'resident_0123456789ab';
      assert.equal(await page.evaluate(() => view.controller.draftText()), `@${author} `);
      // Delete the suggested mention through the actual editable input. A view
      // refresh and submission must retain the quote without restoring the @.
      await page.locator('.composerInput').press('Control+A');
      await page.locator('.composerInput').press('Backspace');
      await page.keyboard.insertText('只作引用');
      await page.evaluate(() => view.apply());
      assert.equal(await page.evaluate(() => view.controller.draftText()), '只作引用');
      assert.equal(await page.locator('.composerQuoteText').textContent(), label);
      await page.locator('.composerInput').press('Control+Enter');
      await page.waitForFunction(id => sent.some(args => args.quote?.message_id === id), messageId);
      const args = await page.evaluate(() => sent.at(-1));
      assert.deepEqual(args.quote, { message_id: messageId }, 'send only the source ID for server-side resolution');
      assert.deepEqual(args.content_parts, [{ type: 'text', text: '只作引用' }], 'a deleted user/Resident @ stays deleted');
      assert.ok(args.content_parts.every(part => part.type === 'text'), 'do not copy attachments into the reply');
      await page.locator(`[data-message-quote="${messageId}"]`).click();
      await page.waitForFunction(id => document.querySelector('.messageQuoteTarget')?.dataset.messageKey === id, messageId);
    }
    assert.deepEqual(errors, []);
    console.log('PASS image/file quote → delete suggested user/Resident mention → refresh → send exact body and quote ID → jump to original');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
