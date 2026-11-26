'use strict';
// One Chromium, the production Resident panel and question cards, local snapshots.
// All instructions are intercepted. No live conversation or credential is issued.
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
  boundary: 'production panel and Resident client; synthetic snapshots and captured instructions' };
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
    else if (modules.includes(name)) {
      const baseline = process.env.PORTAL_QUESTION_FIXTURE_STATIC && path.join(process.env.PORTAL_QUESTION_FIXTURE_STATIC, name);
      body = fs.readFileSync(baseline && fs.existsSync(baseline) ? baseline : path.join(repo, 'management_portal/static', name));
    }
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
    for (const name of ['portal_design_system.css', 'portal_components_turn_cards.css']) {
      await page.addStyleTag({path: path.join(repo, 'management_portal/static', name)});
    }
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'credential-fixture' };
      window.delivered = [];
      window.calls = [];
      window.prepared = new Map();
      window.prepareOverrides = new Map();
      window.failResource = '';
      window.lostRelease = false;
      window.closedRequests = [];
      window.failClose = false;
      window.PortalAPI = { get: async url => {
        if (!url.includes('session-restart')) throw new Error('Unexpected API: ' + url);
        return { config: {}, restart: null };
      } };
      window.PortalInstructionClient = { execute: async (instruction, args, options = {}) => {
        if (['ListInputHistory', 'ListResidentInputHistory'].includes(instruction)) return { entries: [] };
        calls.push(JSON.parse(JSON.stringify({instruction, args, options})));
        if (instruction === 'CancelResidentRequest') {
          if (failClose) throw new Error('Close fixture failed');
          closedRequests.push(JSON.parse(JSON.stringify(args)));
          return { status: 'cancelled' };
        }
        if (instruction === 'RelayMessage') return {status: 'relayed'};
        if (instruction === 'PrepareResidentInput') {
          const id = args.message.message_id;
          if (!prepared.has(id)) prepared.set(id, {args: JSON.stringify(args), status: 'prepared'});
          const row = prepared.get(id);
          assertSame(row.args, JSON.stringify(args));
          const projectedMember = snap.messages.find(message => message.content_parts.some(part => part.answer_message_id === id))
            ?.family_reply?.resident_id;
          row.credentialRecipient = projectedMember || args.resident_id;
          return {resident_id: args.resident_id, message_id: id, status: row.status,
            credential_recipient_id: row.credentialRecipient, credential_message_id: id,
            ...prepareOverrides.get(id)};
        }
        if (instruction === 'CreateTemporaryCredential') {
          const row = prepared.get(args.message_id);
          assertSame(row?.status, 'prepared');
          assertSame(args.principal_id, row.credentialRecipient);
          if (args.resource_id === failResource) throw new Error('Grant fixture denied');
          return {...args, state: 'pending', lifetime: 'task'};
        }
        if (instruction === 'CancelInput') {
          prepared.get(args.message_id).status = 'cancelled';
          return {resident_id: args.resident_id, message_id: args.message_id, input_state: {state: 'cancelled', message_id: args.message_id}};
        }
        if (instruction !== 'DeliverMessage') throw new Error('Unexpected instruction: ' + instruction);
        const row = prepared.get(args.message.message_id);
        if (row) {
          assertSame(row.args, JSON.stringify(args));
          row.status = 'released';
        }
        if (lostRelease) throw Object.assign(new Error('Lost release receipt'), {status: 409});
        delivered.push(JSON.parse(JSON.stringify(args)));
        return { resident_id: args.resident_id, message_id: args.message.message_id, input_state: { state: 'queued' } };
      } };
      function assertSame(actual, expected) {
        if (actual !== expected) throw new Error('Fixture contract mismatch: ' + actual + ' != ' + expected);
      }
      window.LEND = { a: '@contributor', resource_id: 'workbench_0123456789ab' };
      window.asking = (id, familyReply = false) => ({ message_id: 'msg_' + id, role: 'agent',
        family_reply: familyReply ? {resident_id: 'resident_222222222222', title: '资源与权限开发'} : null,
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
    check('approval stages the exact grants and releases the exact prepared input', await page.evaluate(() => calls.slice(0, 3).map(call => ({instruction: call.instruction, args: call.args}))), [
      {instruction: 'PrepareResidentInput', args: approved},
      {instruction: 'CreateTemporaryCredential', args: {principal_id: 'resident_111111111111', message_id: 'answer_a',
        a: '@contributor', resource_id: 'workbench_0123456789ab'}},
      {instruction: 'DeliverMessage', args: approved},
    ]);
    check('public delivery contains no credentials field', Object.hasOwn(approved, 'temporary_credentials'), false);
    check('approval answer keeps the fixed option text', approved.message.content_parts[1].answers, [
      { question_index: 0, answer: '同意交出这些权限' }, { question_index: 1, answer: '定向' }]);
    check('answer goes to its own Resident', approved.resident_id, 'resident_111111111111');

    await page.evaluate(() => { snap.messages = [asking('answer_b')]; render(); });
    await card().locator('[data-question-option="1"]').click();
    await card().locator('[data-question-option="1"]').click();
    await page.waitForFunction(() => delivered.length === 2);
    check('declining sends only the answer', await page.evaluate(() => calls.slice(3).map(call => call.instruction)), ['DeliverMessage']);

    await page.evaluate(() => { snap.messages = [asking('answer_c', true)]; render(); });
    check('a member request on a facade can be approved', await card().locator('[data-question-option="0"]').isEnabled(), true);
    check('a member request identifies the actual recipient', await card().locator('.residentQuestionCredentials > span').first().innerText(),
      '同意后，以下权限只交给提问成员：资源与权限开发 · resident_222222222222');
    for (const width of [945, 625, 390]) {
      await page.setViewportSize({width, height: 900});
      check(`permission card fits at ${width}px`, await card().evaluate(element => element.scrollWidth <= element.clientWidth), true);
      if (process.env.SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
        await card().screenshot({path: path.join(process.env.SCREENSHOT_DIR, `facade-permission-${width}.png`)});
      }
    }
    await card().locator('[data-question-option="0"]').click();
    await card().locator('[data-question-option="0"]').click();
    await page.waitForFunction(() => delivered.length === 3 || document.querySelector('.residentQuestionStepper.has-error'));
    const facadeApproval = await page.evaluate(() => delivered[2]);
    check('facade approval keeps the original input and grants only to the reported member',
      await page.evaluate(() => calls.slice(4).map(({instruction, args}) => ({instruction, args}))), [
        {instruction: 'PrepareResidentInput', args: facadeApproval},
        {instruction: 'CreateTemporaryCredential', args: {principal_id: 'resident_222222222222',
          message_id: 'answer_c', a: '@contributor', resource_id: 'workbench_0123456789ab'}},
        {instruction: 'DeliverMessage', args: facadeApproval},
      ]);
    check('facade and question identity survive preparation and release', {
      recipient: facadeApproval.resident_id, answer: facadeApproval.message.message_id,
      source: facadeApproval.message.content_parts[1].source_message_id,
      hasCredentials: Object.hasOwn(facadeApproval, 'temporary_credentials'),
    }, {recipient: 'resident_111111111111', answer: 'answer_c', source: 'msg_answer_c', hasCredentials: false});

    for (const [id, override] of [
      ['answer_missing', {credential_recipient_id: undefined, credential_message_id: undefined}],
      ['answer_wrong_target', {credential_recipient_id: 'resident_111111111111'}],
      ['answer_wrong_message', {credential_message_id: 'another_answer'}],
    ]) {
      await page.evaluate(({id, override}) => {
        prepareOverrides.set(id, override);
        snap.messages = [asking(id, true)]; render();
      }, {id, override});
      const start = await page.evaluate(() => calls.length);
      await card().locator('[data-question-option="0"]').click();
      await card().locator('[data-question-option="0"]').click();
      await card().getByRole('status').filter({hasText: '无法确认这条授权回答的准备状态'}).waitFor();
      check(id + ' stops before any grant or delivery',
        await page.evaluate(start => calls.slice(start).map(call => call.instruction), start), ['PrepareResidentInput']);
    }
    const retryStart = await page.evaluate(() => calls.length);

    await page.evaluate(() => {
      window.SECOND = {a: '@viewer', resource_id: 'workbench_333333333333'};
      const request = asking('answer_retry', true);
      request.content_parts[1].questions[0].credentials.push(SECOND);
      failResource = SECOND.resource_id;
      snap.messages = [request]; render();
    });
    await card().locator('[data-question-option="0"]').click();
    await card().locator('[data-question-option="0"]').click();
    await card().getByRole('status').filter({hasText: 'Grant fixture denied'}).waitFor();
    check('partial grant failure leaves the answer unsubmitted', await page.evaluate(start => calls.slice(start).map(call => call.instruction), retryStart),
      ['PrepareResidentInput', 'CreateTemporaryCredential', 'CreateTemporaryCredential']);
    check('the attempted answer is fixed until retry or cancellation', await card().locator('[data-question-option="1"]').isDisabled(), true);
    await page.evaluate(() => { failResource = ''; lostRelease = true; });
    await card().locator('[data-question-retry]').click();
    await card().getByRole('status').filter({hasText: 'Lost release receipt'}).waitFor();
    const failedRelease = await page.evaluate(() => calls.at(-1));
    check('retry after a partial grant preserves the fixed message', failedRelease.args.message.message_id, 'answer_retry');
    await page.evaluate(() => { lostRelease = false; });
    await card().locator('[data-question-retry]').click();
    await page.waitForFunction(() => delivered.length === 4);
    check('released retry reads preparation and replays only delivery', await page.evaluate(() => calls.slice(-2).map(call => call.instruction)),
      ['PrepareResidentInput', 'DeliverMessage']);
    check('released retry preserves wire body and delivery idempotency key', await page.evaluate(() => calls.at(-1)), failedRelease);

    await page.evaluate(() => {
      const request = asking('answer_cancel_staged', true);
      request.content_parts[1].questions[0].credentials.push(SECOND);
      failResource = SECOND.resource_id;
      snap.messages = [request]; render();
    });
    await card().locator('[data-question-option="0"]').click();
    await card().locator('[data-question-option="0"]').click();
    await card().getByRole('status').filter({hasText: 'Grant fixture denied'}).waitFor();
    await card().locator('[data-question-close]').click();
    await page.waitForFunction(() => closedRequests.length === 1);
    check('closing cancels preparation at the facade using the fixed input',
      await page.evaluate(() => calls.slice(-2).map(call => [call.instruction, call.args.resident_id, call.args.message_id])),
      [['CancelInput', 'resident_111111111111', 'answer_cancel_staged'],
       ['CancelResidentRequest', 'resident_111111111111', 'msg_answer_cancel_staged']]);

    await page.evaluate(() => PortalResidentService.reassignFamilyRequest({
      resident_id: 'resident_111111111111', member_id: 'resident_222222222222', message_id: 'msg_original_request',
      reason: ' fix route ', idempotency_key: 'stable-reassignment-key',
    }));
    check('reassign uses RelayMessage with the old request and idempotency key', await page.evaluate(() => calls.at(-1)), {
      instruction: 'RelayMessage', options: {idempotencyKey: 'stable-reassignment-key'}, args: {
        resident_id: 'resident_111111111111', member_id: 'resident_222222222222', message_id: 'msg_original_request',
        reassign: true, reason: 'fix route',
      },
    });
    await page.evaluate(() => { snap.messages = [asking('answer_close', true)]; render(); });
    check('a member permission question can still be closed without lending', await card().locator('[data-question-close]').count(), 1);
    await card().locator('[data-question-close]').click();
    await page.waitForFunction(() => closedRequests.length === 2);
    check('close uses the facade and original source message', await page.evaluate(() => closedRequests.at(-1)), {
      resident_id: 'resident_111111111111', message_id: 'msg_answer_close',
    });
    check('closed member question shows its outcome', await page.locator('[data-resident-question="answer_close"] .residentQuestionStatus').innerText(), '已关闭，不再回答');

    await page.evaluate(() => {
      window.plain = id => {
        const message = asking(id, {resident_id: 'resident_222222222222', message_id: 'msg_' + id});
        message.content_parts[1].questions = [{question_index: 0, title: '执行后，请贴出 repair 命令的 JSON 输出，以及新请求的 Router 状态。', options: []}];
        return message;
      };
      snap.messages = [plain('close_older'), plain('close_active')]; render();
    });
    const older = page.locator('[data-resident-question="close_older"]');
    check('older facade question offers close in summary', await older.locator('[data-question-close]').count(), 1);
    for (const width of [945, 625, 390]) {
      await page.setViewportSize({width, height: 900});
      const button = card().locator('[data-question-close]');
      check(`close button visible at ${width}px`, await button.isVisible(), true);
      check(`card fits at ${width}px`, await card().evaluate(element => element.scrollWidth <= element.clientWidth), true);
      if (process.env.SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
        await card().screenshot({path: path.join(process.env.SCREENSHOT_DIR, `facade-question-close-${width}.png`)});
      }
    }
    await older.locator('[data-question-close]').click();
    check('summary close leaves the active question available', await card().locator('[data-question-custom]').count(), 1);
    check('summary close records the correct question', await page.evaluate(() => closedRequests.at(-1).message_id), 'msg_close_older');
    await page.evaluate(() => { failClose = true; });
    await card().locator('[data-question-close]').click();
    check('failed close leaves the question open for retry', await card().locator('[data-question-close]').isEnabled(), true);
    await page.evaluate(() => { failClose = false; });
    await card().locator('[data-question-close]').click();
    check('active facade question is closed', await page.locator('[data-resident-question="close_active"] .residentQuestionStatus').innerText(), '已关闭，不再回答');
    check('close never sends an answer or credentials', await page.evaluate(() => delivered.length), 4);
    await page.evaluate(() => {
      const message = plain('closed_on_server'); message.content_parts[1].state = 'closed';
      snap.messages = [message]; render();
    });
    check('server-projected closure is shown without local close history', await page.locator('[data-resident-question="closed_on_server"] .residentQuestionStatus').innerText(), '已关闭，不再回答');
    await page.evaluate(() => { snap.messages = [plain('read_only')]; snap.controls.can_send = false; render(); });
    check('read-only viewers cannot close questions', await page.locator('[data-question-close]').count(), 0);
    check('browser errors', evidence.errors, []);
    console.log('PASS prepared facade approvals, exact member grants, retry/cancel and RelayMessage');
  } finally {
    if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    if (process.env.EVIDENCE_PATH) fs.writeFileSync(process.env.EVIDENCE_PATH, JSON.stringify(evidence, null, 2) + '\n');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
