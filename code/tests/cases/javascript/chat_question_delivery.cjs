'use strict';
// Real shared cards and send protocol, isolated browser and captured transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'resident_protocol.js', ...chatWorkspaceScripts()];
const routes = execFileSync('python3', ['-c',
  'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
{ cwd: repo, encoding: 'utf8' });
const checks = [];
function check(name, actual, expected) { assert.deepEqual(actual, expected, name); checks.push(name); }
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><title>Which Resident · 提问状态验证</title><main id="chat"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routes;
    else if (modules.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  const errors = [];
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 900, height: 650 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    const boot = async messages => {
      await page.goto(origin);
      for (const name of ['portal_design_system.css', 'portal_components_turn_cards.css']) {
        await page.addStyleTag({ path: path.join(repo, 'management_portal/static', name) });
      }
      await page.evaluate(messages => {
        window.PortalIdentity = { username: () => 'delivery-fixture' };
        window.calls = []; window.mode = 'delayed';
        window.PortalAPI = { get: async () => ({ config: {}, restart: null }) };
        window.PortalInstructionClient = { execute: async (instruction, args) => {
          if (instruction === 'ListInputHistory') return { entries: [] };
          if (instruction !== 'DeliverMessage') throw new Error('Unexpected instruction: ' + instruction);
          calls.push(structuredClone(args));
          if (mode === 'fail') throw Object.assign(new Error('发送失败：测试拒绝受理'), { status: 403 });
          if (mode === 'delayed') await new Promise(resolve => { window.accept = resolve; });
          return { resident_id: args.resident_id, message_id: args.message.message_id, accepted: true };
        } };
        window.question = id => ({ message_id: 'msg_' + id, role: 'agent', content_parts: [
          { type: 'text', text: 'Which Resident · 请确认要采用的方案（隔离测试）' },
          { type: 'async_questions', answer_message_id: id, state: 'open', questions: [
            { question_index: 0, title: '选择检查范围？', options: ['定向检查', '全部检查'] },
            { question_index: 1, title: '是否更新规范？', options: ['更新', '暂不更新'] },
          ] },
        ] });
        window.snap = { messages: messages || [question('answer_a')], controls: { can_send: true }, queue: { items: [] } };
        window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'deliveryFixture',
          autoLoad: false, target: { resident_id: 'resident_111111111111' }, onError: error => { throw error; } });
        panel.protocol = { snapshot: () => snap, destroy() {} };
        panel.residentProjection = { resident: { resident_id: 'resident_111111111111' },
          chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: 'resident_111111111111' },
            header: { title: 'Which Resident' }, messages: [], composer: { can_send: true } } };
        window.render = () => panel.applySnapshot(snap);
        render();
      }, messages);
    };
    const shot = async name => {
      if (!process.env.SCREENSHOT_DIR) return;
      fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
      await page.locator('[data-resident-question]').first().screenshot({ path: path.join(process.env.SCREENSHOT_DIR, name + '.png') });
    };
    await boot();
    const first = page.locator('[data-question-option="0"]');
    await first.click();
    await first.focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => calls.length === 1);
    check('all controls disabled during send', await page.locator('.residentQuestionStepper button:enabled, .residentQuestionStepper input:enabled').count(), 0);
    await shot('sending');
    await page.keyboard.press('1'); await page.keyboard.press('Enter');
    check('keyboard and repeated clicks do not dispatch twice', await page.evaluate(() => calls.length), 1);
    await page.evaluate(() => accept());
    await page.waitForFunction(() => document.querySelector('.residentQuestionSummary[data-state="answered"]'));
    check('receipt closes controls before snapshot changes', await page.locator('[data-question-option], [data-question-confirm], [data-question-pick]').count(), 0);
    check('selected answers remain visible', await page.locator('.residentQuestionSummary strong').allTextContents(), ['定向检查', '更新']);
    check('acceptance has an explicit status', await page.locator('.residentQuestionStatus').innerText(), '回答已接收');
    await shot('answered');
    await page.evaluate(() => { render(); snap.messages.unshift({message_id: 'older', role: 'user', content_parts: [{type: 'text', text: 'Earlier history'}]}); render(); });
    check('stale refresh and history prepend cannot reopen it', await page.locator('.residentQuestionStepper').count(), 0);
    const answeredHistory = await page.evaluate(() => [snap.messages.at(-1), calls[0].message]);
    await boot(answeredHistory);
    check('full page reload restores answers from persisted history', await page.locator('.residentQuestionSummary strong').allTextContents(), ['定向检查', '更新']);
    check('reload does not send business input', await page.evaluate(() => calls.length), 0);
    await shot('reloaded');
    const historyOnly = structuredClone(answeredHistory.slice(0, 1));
    historyOnly[0].content_parts[1].state = 'answered';
    await boot(historyOnly);
    check('history page without answer body remains locked by server state', await page.locator('.residentQuestionStepper, [data-question-pick]').count(), 0);
    await page.evaluate(() => { snap.messages = [question('answer_failure')]; mode = 'fail'; render(); });
    await first.click(); await first.click();
    await page.waitForFunction(() => document.querySelector('.residentQuestionStatus').textContent.includes('测试拒绝受理'));
    check('definite failure allows retry', await first.isEnabled(), true);
    await shot('failed-retry');
    await page.evaluate(() => { mode = 'success'; });
    await first.focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => document.querySelector('.residentQuestionSummary[data-state="answered"]'));
    check('retry really calls transport rather than reusing rejected promise', await page.evaluate(() => calls.length), 2);
    check('retry keeps the same answer identity and content', await page.evaluate(() => JSON.stringify(calls[0]) === JSON.stringify(calls[1])), true);
    check('no browser errors', errors, []);
    console.log(JSON.stringify({ ok: true, singleWorker: true, checks,
      evidence: process.env.SCREENSHOT_DIR || '', boundary: 'Synthetic conversation and transport; production shared card and send protocol; no real user records or business delivery' }));
  } finally { if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
