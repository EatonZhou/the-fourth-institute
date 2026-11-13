'use strict';
// Production Chat and protocol; synthetic source/transport only, one browser worker.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js',
  'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'resident_protocol.js', ...chatWorkspaceScripts()];
const routes = execFileSync('python3', ['-c', 'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'], {cwd: repo, encoding: 'utf8'});
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    const body = !name ? '<!doctype html><meta charset="utf-8"><main id="chat"></main><script src="/routes.js"></script>' + scripts.map(n => `<script src="/${n}"></script>`).join('')
      : name === 'routes.js' ? routes : scripts.includes(name) ? fs.readFileSync(path.join(repo, 'management_portal/static', name)) : '';
    res.writeHead(200, {'Content-Type': name ? 'text/javascript' : 'text/html'}); res.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 900, height: 700}});
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  try {
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    async function boot(accepted = false) {
      await page.goto(origin);
      for (const name of ['portal_design_system.css', 'portal_components.css', 'portal_components_chat_frame.css',
        'portal_components_messages.css', 'portal_components_markdown.css', 'portal_components_composer.css',
        'portal_components_conversation.css', 'portal_components_turn_cards.css', 'portal_components_agent_inbox.css']) {
        await page.addStyleTag({path: path.join(repo, 'management_portal/static', name)});
      }
      await page.evaluate(accepted => {
        window.PortalIdentity = {username: () => 'handoff-fixture'};
        window.calls = []; window.mode = 'delayed'; window.failures = [];
        window.PortalAPI = {get: async () => ({config: {}, restart: null})};
        window.PortalInstructionClient = {execute: async (name, args) => {
          if (name === 'ListInputHistory') return {entries: []};
          if (name !== 'DeliverMessage') throw new Error('Unexpected instruction ' + name);
          calls.push(structuredClone(args));
          if (mode === 'fail') throw Object.assign(new Error('测试拒绝受理'), {status: 403});
          if (mode === 'delayed') await new Promise(resolve => {window.accept = resolve;});
          return {resident_id: args.resident_id, message_id: args.message.message_id, accepted: true};
        }};
        const card = {resident_id: 'resident_222222222222', title: 'Family 实现（隔离测试）', reason: '负责个人助手前后端', ask_message: '不应出现在用户气泡里的完整建议草稿', recommended_action: 'ask'};
        window.answerId = 'msg_' + 'c'.repeat(32);
        window.source = {message_id: 'msg_' + 'b'.repeat(32), role: 'agent', content_parts: [
          {type: 'text', text: '建议咨询这位协作者。\n```portal-resident-handoff\n' + JSON.stringify(card) + '\n```'},
          {type: 'resident_handoff_cards', cards: [{card_index: 0, selection_message_id: answerId}]},
        ]};
        window.snap = {messages: [source], input_states: accepted ? [{message_id: answerId, state: 'completed'}] : [], controls: {can_send: true}, queue: {items: []}};
        window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('chat'), id: 'handoffFixture', autoLoad: false,
          target: {resident_id: 'resident_111111111111'}, onError: e => failures.push(e.message)});
        panel.protocol = {snapshot: () => snap, destroy() {}};
        panel.residentProjection = {resident: {resident_id: 'resident_111111111111'}, chat_workspace: {schema: 'portal.chat_workspace.v3',
          target: {kind: 'resident', id: 'resident_111111111111'}, header: {title: '推荐选择 · 隔离验证'}, messages: [], composer: {can_send: true}}};
        window.render = () => panel.applySnapshot(snap); render();
      }, accepted);
    }
    const ask = () => page.locator('[data-portal-message-action="resident-handoff-ask"]');
    async function shot(name) {
      if (process.env.SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
        await page.screenshot({path: path.join(process.env.SCREENSHOT_DIR, name + '.png')});
      }
    }
    await boot();
    await ask().focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(() => calls.length === 1);
    assert.equal(await ask().isDisabled(), true);
    await page.evaluate(() => {render(); document.querySelector('[data-portal-message-action="resident-handoff-ask"]').click();});
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => calls.length), 1);
    await shot('sending');
    const sent = await page.evaluate(() => calls[0].message);
    assert.equal(sent.message_id, 'msg_' + 'c'.repeat(32));
    assert.equal(sent.content_parts[0].text, '替我问一下');
    assert.equal(sent.quote.message_id, 'msg_' + 'b'.repeat(32));
    assert.equal(JSON.stringify(sent).includes('完整建议草稿'), false);
    await page.evaluate(() => accept());
    await page.waitForFunction(() => document.querySelector('[data-portal-message-action="resident-handoff-ask"]').textContent.includes('已选择'));
    await page.evaluate(() => {
      snap.messages.push({...calls[0].message, author_id: 'handoff-fixture'});
      snap.input_states = [{message_id: answerId, state: 'queued'}]; render();
    });
    assert.equal(await ask().isDisabled(), true);
    assert.match(await page.locator('.residentHandoffSelection').innerText(), /Family 实现/);
    await shot('accepted-short-message');
    await boot(true);
    assert.equal(await ask().isDisabled(), true, 'reload with historical input state locks card even outside message window');
    assert.equal(await page.evaluate(() => calls.length), 0);
    await boot();
    await page.evaluate(() => {mode = 'fail';}); await ask().click();
    await page.waitForFunction(() => document.querySelector('[data-portal-message-action="resident-handoff-ask"]').textContent.includes('重试'));
    assert.equal(await ask().isEnabled(), true); await shot('failed-retry');
    await page.evaluate(() => {mode = 'success';}); await ask().focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => document.querySelector('[data-portal-message-action="resident-handoff-ask"]').textContent.includes('已选择'));
    assert.equal(await page.evaluate(() => calls.length), 2);
    assert.equal(await page.evaluate(() => JSON.stringify(calls[0]) === JSON.stringify(calls[1])), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ok: true, checks: ['short text', 'source selection', 'stable identity', 'keyboard', 'pending redraw', 'accepted lock', 'history reload', 'failure retry'], evidence: process.env.SCREENSHOT_DIR || ''}));
  } finally {await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error); process.exitCode = 1;});
