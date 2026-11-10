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
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
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
      <style>html,body{margin:0;height:100%}#chat{height:650px;width:850px}</style><div id="chat"></div>
      <script>${routeScript}</script>${modules.map(n => `<script src="/${n}"></script>`).join('')}`;
  } else if (modules.includes(name) || styles.includes(name)) {
    body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    if (name.endsWith('.css')) type = 'text/css';
  }
  res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body || 'not found');
});
let browser;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  await page.goto(origin);
  await page.evaluate(() => {
    window.roomId = 'chatroom_111111111111';
    window.reads = []; window.loads = []; window.denied = false; window.readThrough = 0;
    window.messages = Array.from({ length: 150 }, (_, i) => ({ message_id: `msg_${i + 1}`, ordinal: i + 1,
      author_id: 'other', role: 'user', content_parts: [{ type: 'text', text: `Message ${i + 1}\n` + '正文\n'.repeat(8) }] }));
    messages[124] = { ...messages[124], retracted_at: '2026-11-11T01:00:00Z', content_parts: [] };
    window.room = () => ({ chatroom_id: roomId, title: 'Fixture', members: ['viewer', 'other'], permissions: { can_post: true },
      unread_count: 150 - readThrough, unread_mentions: [10, 110, 140].filter(n => n > readThrough).map(n => ({ message_id: `msg_${n}`, ordinal: n })),
      unread_mention_count: [10, 110, 140].filter(n => n > readThrough).length, read_revision: reads.length });
    window.PortalIdentity = { username: () => 'viewer' };
    window.PortalConversationStore = {
      subscribe: () => () => {}, isUnread: () => readThrough < 150,
      readChatrooms: async () => { if (denied) throw Error('forbidden'); return { chatroom: room() }; },
      observeChatroomRead: async (id, through) => { reads.push({ id, through }); readThrough = Math.max(readThrough, through); return room(); },
    };
    window.PortalRefreshCoordinator = { portalSubscription: () => () => {}, create: () => ({ start() {}, destroy() {}, invalidate() {} }) };
    window.PortalInstructionClient = { execute: async (name, args) => {
      if (name !== 'ListChatroomMessages') throw Error('Unexpected mutation ' + name);
      if (denied) throw Error('forbidden');
      loads.push(args);
      if (window.blockHistory && args.cursor) await new Promise(resolve => window.releaseHistory = resolve);
      return args.cursor ? { messages: messages.slice(0, 100), next_cursor: '' }
        : { messages: messages.slice(100), next_cursor: 'older' };
    } };
    window.view = PortalChatroomView.mount({ root: document.getElementById('chat'), me: 'viewer' });
    window.reset = async () => {
      await view.setChatroom(''); reads.length = 0; loads.length = 0; readThrough = 0; denied = false; releaseHistory = null;
    };
  });
  await page.evaluate(() => view.setChatroom(roomId, { messageId: 'msg_110' }));
  assert.equal(await page.locator('.messageQuoteTarget').getAttribute('data-message-key'), 'msg_110');
  await page.waitForTimeout(700); // exercise the debounced read timer after the jump
  let reads = await page.evaluate(() => window.reads);
  assert.ok(reads.some(r => r.through === 110));
  const visibleThrough = await page.evaluate(() => view.visibleReadOrdinal());
  assert.ok(visibleThrough >= 110 && visibleThrough < 140);
  assert.ok(reads.every(r => r.through <= visibleThrough), 'only actually visible positions are acknowledged');
  assert.equal(await page.evaluate(() => loads.length), 1, 'an old unread mention does not force loading/jumping to it');
  await page.evaluate(async () => { await reset(); blockHistory = true; window.opening = view.setChatroom(roomId, { messageId: 'msg_10' }); });
  await page.waitForFunction(() => !!window.releaseHistory);
  await page.waitForTimeout(700);
  assert.equal(await page.evaluate(() => reads.length), 0, 'history loading does not mark the last page read');
  await page.evaluate(async () => { blockHistory = false; releaseHistory(); await opening; });
  assert.equal(await page.locator('.messageQuoteTarget').getAttribute('data-message-key'), 'msg_10');
  assert.ok((await page.evaluate(() => reads)).every(r => r.through < 110));
  await page.evaluate(async () => { await reset(); await view.setChatroom(roomId); });
  assert.equal(await page.locator('.messageQuoteTarget').getAttribute('data-message-key'), 'msg_10', 'no message parameter retains next-mention behavior');
  await page.evaluate(async () => { await reset(); window.failure = ''; try { await view.setChatroom(roomId, { messageId: 'msg_missing' }); } catch (e) { failure = e.message; } });
  await page.waitForTimeout(700);
  assert.ok(await page.evaluate(() => failure));
  assert.equal(await page.evaluate(() => reads.length), 0, 'missing message does not fall back or mark anything read');
  await page.evaluate(async () => { await reset(); denied = true; try { await view.setChatroom(roomId, { messageId: 'msg_110' }); } catch (_) {} });
  assert.equal(await page.locator('.messageList > [data-message-key]').count(), 0, 'denied reads expose no message');
  assert.equal(await page.evaluate(() => reads.length), 0);
  await page.evaluate(async () => { await reset(); await view.setChatroom(roomId, { messageId: 'msg_125' }); });
  assert.equal(await page.locator('.messageQuoteTarget').getAttribute('data-message-key'), 'msg_125');
  assert.ok(!(await page.locator('.messageQuoteTarget').textContent()).includes('Message 125'), 'retraction reveals only a tombstone');
  const previousReads = await page.evaluate(() => reads.length);
  await page.evaluate(async () => { denied = true; try { await view.setChatroom(roomId, { messageId: 'msg_125' }); } catch (_) {} });
  assert.equal(await page.locator('.messageList > [data-message-key]').count(), 0, 'reopening the same room rechecks access');
  assert.equal(await page.evaluate(() => reads.length), previousReads);
  await page.evaluate(async () => {
    await reset(); blockHistory = true;
    window.oldOpening = view.setChatroom(roomId, { messageId: 'msg_10' });
  });
  await page.waitForFunction(() => !!window.releaseHistory);
  await page.evaluate(async () => {
    await view.setChatroom(roomId, { messageId: 'msg_110' });
    blockHistory = false; releaseHistory(); await oldOpening;
  });
  assert.ok((await page.evaluate(() => reads)).every(r => r.through >= 110 && r.through < 140), 'a superseded navigation never sends its read receipt');
  assert.deepEqual(errors, []);
  console.log('PASS: precise target, old mentions, pagination, read boundaries, legacy navigation, missing/denied/retracted messages, navigation race');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { await browser?.close(); server.close(); });
