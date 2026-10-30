'use strict';
// One Chromium, the production Resident panel and question cards, local snapshots.
// DeliverMessage is captured, never sent; no live conversation or credential is issued.
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
const evidence = { checks: [], errors: [],
  boundary: 'real ResidentChatPanel question cards and Resident delivery client; synthetic snapshots; DeliverMessage captured' };
function check(name, actual, expected) {
  evidence.checks.push({ name, actual, expected });
  assert.deepEqual(actual, expected, name);
}
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><main id="chat"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routes;
    else if (modules.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    page.on('pageerror', e => evidence.errors.push(e.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'credential-fixture' };
      window.delivered = [];
      window.PortalAPI = { get: async url => {
        if (!url.includes('session-restart')) throw new Error('Unexpected API: ' + url);
        return { config: {}, restart: null };
      } };
      window.PortalInstructionClient = { execute: async (instruction, args) => {
        if (instruction === 'ListInputHistory') return { entries: [] };
        if (instruction !== 'DeliverMessage') throw new Error('Unexpected instruction: ' + instruction);
        delivered.push(JSON.parse(JSON.stringify(args)));
        return { resident_id: args.resident_id, message_id: args.message.message_id, input_state: { state: 'queued' } };
      } };
      window.LEND = { a: '@contributor', resource_id: 'workbench_0123456789ab' };
      window.asking = (id, familyReply = false) => ({ message_id: 'msg_' + id, role: 'agent', family_reply: familyReply,
        content_parts: [{ type: 'text', text: 'Need your permission' }, { type: 'async_questions', answer_message_id: id, state: 'open',
          questions: [
            { question_index: 0, title: 'Lend to publish?', options: ['同意交出这些权限', '不交出'], credentials: [LEND] },
            { question_index: 1, title: 'Scope?', options: ['定向', '全部'] },
          ] }] });
      window.snap = { messages: [asking('answer_a')], controls: { can_send: true }, queue: { items: [] } };
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'questionCredentials',
        autoLoad: false, target: { resident_id: 'resident_111111111111' }, onError: error => { throw error; } });
      panel.protocol = { snapshot: () => snap, destroy() {} };
      panel.residentProjection = { resident: { resident_id: 'resident_111111111111' },
        chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: 'resident_111111111111' },
          header: { title: 'Question credentials' }, messages: [], composer: { can_send: true } } };
      window.render = () => panel.applySnapshot(snap);
      render();
    });
    const card = () => page.locator('.residentQuestionStepper');
    check('request lists the exact permission', await card().locator('.residentQuestionCredentials li').allInnerTexts(),
      ['@contributor · workbench_0123456789ab']);
    check('request offers only the fixed answers', await card().locator('.residentQuestionChoice span').allInnerTexts(),
      ['同意交出这些权限', '不交出']);
    check('request has no free-text answer', await card().locator('[data-question-custom]').count(), 0);
    await card().locator('[data-question-option="0"]').click();
    await card().locator('[data-question-option="0"]').click();
    await page.waitForFunction(() => delivered.length === 1);
    const approved = await page.evaluate(() => delivered[0]);
    check('approval lends exactly the requested permission with the answer', approved.temporary_credentials, [
      { a: '@contributor', resource_id: 'workbench_0123456789ab' }]);
    check('approval answer keeps the fixed option text', approved.message.content_parts[1].answers, [
      { question_index: 0, answer: '同意交出这些权限' }, { question_index: 1, answer: '定向' }]);
    check('answer goes to its own Resident', approved.resident_id, 'resident_111111111111');

    await page.evaluate(() => { snap.messages = [asking('answer_b')]; render(); });
    await card().locator('[data-question-option="1"]').click();
    await card().locator('[data-question-option="1"]').click();
    await page.waitForFunction(() => delivered.length === 2);
    check('declining lends nothing', await page.evaluate(() => 'temporary_credentials' in delivered[1]), false);

    await page.evaluate(() => { snap.messages = [asking('answer_c', true)]; render(); });
    check('a member request on a facade cannot be approved', await card().locator('[data-question-option="0"]').isDisabled(), true);
    check('a member request on a facade says why', await card().locator('.residentQuestionCredentials > span').first().innerText(),
      '成员在 Family 内提出的权限请求不能经门面交出');
    check('browser errors', evidence.errors, []);
    console.log('PASS question cards request permissions and lend exactly the approved ones with the answer');
  } finally {
    if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    if (process.env.EVIDENCE_PATH) fs.writeFileSync(process.env.EVIDENCE_PATH, JSON.stringify(evidence, null, 2) + '\n');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
