'use strict';
// One Chromium, the production Resident panel/renderer/cards, local snapshots.
// No live conversation, business send, or provider request is made.
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
const evidence = { checks: [], requests: [], errors: [], singleWorker: true,
  boundary: 'real ResidentChatPanel and question cards; synthetic snapshots and transport; synthetic composition events, not OS IME; no live business' };
function check(name, actual, expected) {
  evidence.checks.push({ name, actual, expected });
  assert.deepEqual(actual, expected, name);
}
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    evidence.requests.push(name);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><main id="chat"></main><input id="outside">' +
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
    const mount = (options = {}) => page.evaluate(({ owner = 'focus-fixture', resident = 'resident_111111111111', messages = null, ownerCallback = false }) => {
      window.PortalIdentity = { username: () => owner };
      window.calls = [];
      window.PortalAPI = { get: async url => {
        if (!url.includes('session-restart')) throw new Error('Unexpected API: ' + url);
        return { config: {}, restart: null };
      } };
      window.PortalInstructionClient = { execute: async (instruction, args) => {
        calls.push({ instruction, args });
        if (instruction === 'ListInputHistory') return { entries: [] };
        throw new Error('Unexpected instruction: ' + instruction);
      } };
      window.question = (id, kind = 'async') => ({ message_id: 'msg_' + id, role: 'agent',
        content_parts: [{ type: 'text', text: 'Question ' + id }, kind === 'async'
          ? { type: 'async_questions', answer_message_id: id, state: 'open', questions: [{ question_index: 0, title: id, options: ['One', 'Two'] }] }
          : { type: 'tool_call', tool_name: 'request_user_input', request_id: id, status: 'pending',
            questions: [{ id: 'q', question: id, options: [{ label: 'One' }] }] }] });
      window.snap = { messages: messages || [question('answer_a')], controls: { can_send: true }, queue: { items: [] } };
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'questionFocus',
        autoLoad: false, ...(ownerCallback ? { draftOwner: () => owner } : {}),
        target: { resident_id: resident }, onError: error => { throw error; } });
      panel.protocol = { snapshot: () => snap, destroy() {} };
      panel.residentProjection = { resident: { resident_id: resident },
        chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: resident },
          header: { title: 'Question focus' }, messages: [], composer: { can_send: true } } };
      window.render = () => panel.applySnapshot(snap);
      render();
    }, options);
    await mount();
    const input = () => page.locator('[data-question-custom]');
    const prepare = async locator => {
      await locator.fill('正在输入 draft'); await locator.focus();
      await locator.evaluate(e => {
        window.originalInput = e; window.blurCount = 0; window.detached = false;
        e.setSelectionRange(2, 4);
        e.addEventListener('blur', () => window.blurCount++);
        window.inputObserver?.disconnect();
        window.inputObserver = new MutationObserver(records => {
          for (const record of records) for (const node of record.removedNodes) {
            if (node === e || node.contains(e)) window.detached = true;
          }
        });
        inputObserver.observe(panel.workspace.els.messages, { childList: true, subtree: true });
        e.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '输入' }));
      });
    };
    const state = () => page.evaluate(() => ({ focused: document.activeElement === originalInput,
      connected: originalInput.isConnected, detached, blurs: blurCount,
      value: originalInput.value, selection: [originalInput.selectionStart, originalInput.selectionEnd] }));
    const stable = { focused: true, connected: true, detached: false, blurs: 0, value: '正在输入 draft', selection: [2, 4] };
    await prepare(input());
    await page.evaluate(() => { snap.messages[0].content_parts[0].text = 'Updated question context'; render(); });
    check('message context refresh keeps the mounted input and composition', await state(), stable);
    check('context still updates around the live card', await page.locator('.messageContent').first().textContent().then(s => s.includes('Updated question context')), true);
    await page.evaluate(() => {
      snap.messages.unshift({ message_id: 'older', role: 'user', content_parts: [{ type: 'text', text: 'Earlier request' }] });
      snap.messages.push({ message_id: 'later', role: 'agent', content_parts: [{ type: 'text', text: 'Progress update' }] });
      render();
    });
    check('history prepend and new messages preserve the active question', await state(), stable);
    await page.evaluate(() => { snap.messages.push(question('answer_b')); render(); });
    check('new question does not replace the group being answered', await state(), stable);
    check('new question remains available in summary', await page.locator('[data-resident-question="answer_b"] [data-question-pick]').count(), 1);
    await page.evaluate(() => {
      originalInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
      originalInput.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '输入' }));
    });
    check('IME Enter does not submit an answer', await page.evaluate(() => calls.filter(c => c.instruction !== 'ListInputHistory')), []);
    await page.keyboard.type('X');
    check('typing continues at the original selection', await input().inputValue(), '正在X draft');
    await page.locator('#outside').focus();
    await page.evaluate(() => { snap.messages[1].content_parts[0].text += ' again'; render(); });
    check('passive refresh does not focus the question when user left it', await page.locator('#outside').evaluate(e => document.activeElement === e), true);
    await page.locator('[data-resident-question="answer_b"] [data-question-pick]').click();
    check('explicit question switch still works', await input().getAttribute('aria-label'), '回答：answer_b');
    await page.evaluate(() => { snap.controls.can_send = false; render(); });
    check('permission loss removes answerable inputs', await input().count(), 0);
    await page.evaluate(() => { snap = { messages: [question('native_a', 'native')], controls: { can_send: true } }; render(); });
    await prepare(input());
    await page.evaluate(() => { snap.messages[0].content_parts[0].text += ' refreshed'; render(); });
    check('native question refresh preserves its input', await state(), stable);
    await page.evaluate(() => { snap.messages[0].content_parts[1].status = 'expired'; render(); });
    check('expired native question cannot be answered', await input().count(), 0);
    await page.evaluate(() => {
      snap.messages = [{ message_id: 'mcp_message', role: 'agent', content_parts: [{ type: 'text', text: 'Tool context' },
        { type: 'tool_call', tool_name: 'mcp_elicitation', request_id: 'mcp_a', status: 'pending', is_blocking: false,
          interaction: { kind: 'mcp', request: { serverName: 'fixture', message: 'Name', mode: 'form',
            requestedSchema: { type: 'object', properties: { name: { type: 'string' } } } } } }] }]; render();
    });
    await prepare(page.locator('.residentMcpField input'));
    await page.evaluate(() => { snap.messages[0].content_parts[0].text += ' refreshed'; render(); });
    check('MCP card uses the same stable mount contract', await state(), stable);
    await page.evaluate(() => { snap.messages = []; render(); });
    check('removed question host is actually removed', await page.locator('[data-resident-question]').count(), 0);
    for (const terminal of ['expired', 'declined', 'cancelled']) {
      await page.evaluate(status => {
        window.answerCalls = 0;
        PortalInstructionClient.execute = async instruction => {
          if (instruction === 'ListInputHistory') return { entries: [] };
          if (instruction !== 'AnswerResidentRequest') throw new Error('Unexpected instruction: ' + instruction);
          answerCalls++; return { status };
        };
        snap.messages = [{ message_id: 'approval_' + status, role: 'agent', content_parts: [
          { type: 'tool_call', tool_name: 'request_approval', request_id: 'approval_' + status,
            status: 'pending', is_blocking: true,
            interaction: { kind: 'file_change', request: { reason: 'Inspect fixture scope' } } },
        ] }]; render();
      }, terminal);
      const card = page.locator('.residentQuestionCard');
      await card.getByRole('button', { name: '拒绝', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.residentQuestionCard.answered'));
      check('terminal ' + terminal + ' has no stale submission error', await card.locator('[role=status]').innerText(),
        { expired: '请求已失效，不能再提交', declined: '已拒绝', cancelled: '已取消' }[terminal]);
      check('terminal ' + terminal + ' disables all answers', await card.locator('button:enabled').count(), 0);
      check('terminal ' + terminal + ' sends once', await page.evaluate(() => answerCalls), 1);
    }
    await page.evaluate(() => {
      PortalInstructionClient.execute = async instruction => {
        if (instruction === 'ListInputHistory') return { entries: [] };
        if (instruction !== 'AnswerResidentRequest') throw new Error('Unexpected instruction: ' + instruction);
        answerCalls++; throw new Error('Original status read failed');
      };
      snap.messages[0].message_id = 'approval_late';
      const part = snap.messages[0].content_parts[0]; part.request_id = 'approval_late'; part.status = 'pending'; render();
    });
    await page.locator('.residentQuestionCard').getByRole('button', { name: '拒绝', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.residentQuestionStatus')?.textContent === 'Original status read failed');
    await page.evaluate(() => { snap.messages[0].content_parts[0].status = 'expired'; render(); });
    check('late authoritative expiry clears earlier transport error', await page.locator('.residentQuestionStatus').innerText(), '请求已失效，不能再提交');
    check('late expiry never resends answer', await page.evaluate(() => answerCalls), 2);

    // A server restart reloads the page: exercise real localStorage, not a
    // reused panel or a copied in-memory progress map.
    const reload = async (options = {}) => { await page.reload(); await mount(options); };
    await reload();
    check('switching away and remounting restores the earlier unconfirmed draft', await input().inputValue(), '正在X draft');
    const draft = '  <未提交 & "回答草稿">  ';
    await input().fill(draft);
    await reload();
    check('reload restores raw unconfirmed text including whitespace and HTML characters', await input().inputValue(), draft);
    check('draft restoration never sends an answer', await page.evaluate(() => calls.filter(c => c.instruction !== 'ListInputHistory')), []);
    check('a restored draft is not a confirmed choice', await page.locator('[data-question-option][aria-pressed="true"]').count(), 0);
    await reload({ ownerCallback: true });
    check('a host owner callback resolves to the same user scope', await input().inputValue(), draft);
    await reload({ owner: 'another-user' });
    check('another user does not receive the draft', await input().inputValue(), '');
    await input().fill('另一用户草稿');
    await reload({ resident: 'resident_222222222222' });
    check('another conversation does not receive the draft', await input().inputValue(), '');
    await reload();
    check('isolated scopes leave the original draft intact', await input().inputValue(), draft);
    await input().fill('');
    await reload();
    check('clearing a draft also survives reload', await input().inputValue(), '');

    const batch = await page.evaluate(() => {
      const message = question('draft_batch');
      message.content_parts[1].questions.push({ question_index: 1, title: 'Second question', options: [] });
      return [message];
    });
    await reload({ messages: batch });
    await input().fill('第一题已确认');
    await page.locator('[data-question-confirm]').click();
    await input().fill('第二题未提交');
    await page.locator('[data-question-back]').click();
    check('back restores the confirmed custom answer', await input().inputValue(), '第一题已确认');
    await input().fill('');
    await reload({ messages: batch });
    check('a deliberately cleared answer is not repopulated from its confirmed value', await input().inputValue(), '');
    await page.locator('[data-question-confirm]').click();
    check('empty draft cannot silently submit the old answer', await page.locator('.residentQuestionStepProgress').innerText(), '问题 1/2');
    await input().fill('第一题修订');
    await page.locator('[data-question-confirm]').click();
    check('moving forward preserves the second question draft', await input().inputValue(), '第二题未提交');
    await reload({ messages: batch });
    check('reload restores the active step and its unfinished answer', [await page.locator('.residentQuestionStepProgress').innerText(), await input().inputValue()], ['问题 2/2', '第二题未提交']);
    await page.evaluate(() => { window.PortalResidentProtocol = { ...PortalResidentProtocol,
      sendInput: async () => { throw new Error('模拟断线'); } }; });
    await page.locator('[data-question-confirm]').click();
    await page.waitForFunction(() => document.querySelector('.residentQuestionStatus').textContent === '模拟断线');
    await reload({ messages: batch });
    check('failed submission retains the confirmed answer after reload', await input().inputValue(), '第二题未提交');
    await page.evaluate(() => { window.PortalResidentProtocol = { ...PortalResidentProtocol, sendInput: async payload => {
      window.sentAnswers = payload.message.content_parts.find(part => part.type === 'async_question_answer').answers;
      snap.messages[0].content_parts[1].state = 'answered';
      return {};
    } }; });
    await page.locator('[data-question-confirm]').click();
    await page.waitForFunction(() => !document.querySelector('[data-question-custom]'));
    check('explicit successful submission sends only the confirmed answers', await page.evaluate(() => sentAnswers.map(entry => entry.answer)), ['第一题修订', '第二题未提交']);
    await reload({ messages: batch });
    check('successful submission removes the saved group', await input().inputValue(), '');
    await input().fill('不再回答的草稿');
    await page.evaluate(() => { window.PortalResidentProtocol = { ...PortalResidentProtocol, respondUserInput: async () => ({}) }; });
    await page.locator('[data-question-close]').click();
    await page.waitForFunction(() => !document.querySelector('[data-question-custom]'));
    await reload({ messages: batch });
    check('closing a group removes its saved draft', await input().inputValue(), '');

    const native = await page.evaluate(() => [question('native_draft', 'native')]);
    await reload({ messages: native });
    await input().fill('原生问题未确认草稿');
    await reload({ messages: native });
    check('native question drafts also survive reload', await input().inputValue(), '原生问题未确认草稿');
    await page.evaluate(() => { snap.messages[0].content_parts[1].status = 'expired'; render(); });
    await reload({ messages: native });
    check('authoritative expiry clears the native draft', await input().inputValue(), '');
    check('browser errors', evidence.errors, []);
    console.log('PASS question input focus, composition, draft reload, scope isolation, submission and native/MCP lifecycle');
  } finally {
    if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    if (process.env.EVIDENCE_PATH) fs.writeFileSync(process.env.EVIDENCE_PATH, JSON.stringify(evidence, null, 2) + '\n');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
