'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const source = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { openReviewEvidenceWindow } = require(path.join(source, 'management_portal/test_framework/tau_review_evidence.js'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const residentId = 'resident_111111111111';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 820 } });
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => requests.push(r.url()));
    await page.route('**/*', route => route.request().url() === 'https://review.fixture.invalid/'
      ? route.fulfill({ contentType: 'text/html', body: '<!doctype html><main id="fixture" style="width:850px;height:700px"></main>' }) : route.abort());
    await page.goto('https://review.fixture.invalid/');
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'fixture' };
      window.PortalEvents = { isConnected: () => true, connect() {} };
      window.PortalAPI = { get: async () => ({ config: {}, restart: null }) };
      window.PortalInstructionClient = { execute: async name => {
        if (name === 'ListInputHistory') return { entries: [] };
        throw new Error('Unexpected instruction: ' + name);
      } };
    });
    await page.addScriptTag({ content: routeScript });
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_preview_contract.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'portal_resident_service.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({ content: fs.readFileSync(path.join(source, 'management_portal/static', name), 'utf8') });
    }
    await page.addStyleTag({ content: fs.readFileSync(path.join(source, 'management_portal/static/portal_components.css'), 'utf8') });
    await page.evaluate(residentId => {
      window.sent = [];
      window.PortalResidentProtocol = { ...PortalResidentProtocol, sendInput: async payload => {
        sent.push(payload);
        return { resident_id: residentId, message_id: payload.message.message_id, accepted: true, input_state: { state: 'queued' } };
      } };
    }, residentId);

    let index = 0;
    const mount = async () => {
      index += 1;
      const sourceMessage = { message_id: 'msg_source_' + index, author_id: residentId, role: 'agent', content_parts: [{
        type: 'native_turn', segment_id: 'segment_' + index, execution_id: 'execution_' + index,
        thread_id: 'thread_' + index, turn_id: 'turn_' + index, diff: 'diff --git a/a b/a\n+fixture',
      }] };
      await page.evaluate(({ residentId, sourceMessage }) => {
        window.panel?.destroy(); document.querySelector('#fixture').replaceChildren();
        window.panel = PortalChatWorkspace.panelPackage.create({ root: document.querySelector('#fixture'), id: 'reviewFixture', autoLoad: false, target: { resident_id: residentId } });
        window.snap = { messages: [], controls: { can_send: true }, queue: { items: [] } };
        panel.protocol = { snapshot: () => snap, destroy() {} };
        panel.residentProjection = { resident: { resident_id: residentId }, chat_workspace: {
          schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: residentId },
          header: { title: 'Review fixture' }, messages: [], composer: { can_send: true },
        } };
        window.show = messages => { snap = { messages, controls: { can_send: true }, queue: { items: [] } }; panel.applySnapshot(snap); };
        show([sourceMessage]);
      }, { residentId, sourceMessage });
      return sourceMessage;
    };
    const begin = async sourceMessage => {
      const rows = [], requestCount = requests.length;
      const window = await openReviewEvidenceWindow(page.locator('#fixture'), {
        actionId: 'action_' + index, residentId, sourceMessage, record: async row => rows.push(row),
      });
      assert.equal(requests.length, requestCount, 'read-only preflight makes no request');
      assert.equal(rows[0].stage, 'before_click');
      return { window, rows };
    };
    const clickAndFinishData = async sourceMessage => {
      const count = await page.evaluate(() => sent.length);
      await page.locator('[data-turn-review]').click();
      const input = await page.evaluate(() => sent.at(-1).message);
      assert.equal(await page.evaluate(() => sent.length), count + 1, 'one actual native button action');
      const reply = { message_id: 'msg_reply_' + index, role: 'agent', author_id: residentId,
        reply_to_message_id: input.message_id, content_parts: [{ type: 'text', text: 'Fixture review complete.' }] };
      await page.evaluate(messages => show(messages), [sourceMessage, input, reply]);
      return {
        receipt: { instruction: 'DeliverMessage', instruction_id: 'inst_' + index, status: 'committed',
          result: { resident_id: residentId, message_id: input.message_id, accepted: true } },
        input, terminal: { message_id: input.message_id, state: 'completed', reply_message_ids: [reply.message_id] }, replies: [reply],
      };
    };
    const original = await mount(), first = await begin(original), data = await clickAndFinishData(original);
    const beforeFinish = requests.length;
    assert.equal((await first.window.finish(data)).ready, true);
    assert.equal(requests.length, beforeFinish, 'read-only finish makes no request');
    const saved = JSON.stringify(first.rows);
    data.replies[0].content_parts[0].text = 'mutated by caller';
    assert.equal(JSON.stringify(first.rows), saved, 'saved evidence is immutable from caller mutation');
    await assert.rejects(first.window.finish(data), /WindowClosed/);

    for (const [alter, reason] of [
      [p => { p.terminal.state = 'running'; p.terminal.reply_message_ids = []; }, 'OriginalTerminal'],
      [p => { p.receipt.result.resident_id = 'resident_other'; }, 'OriginalAcceptance'],
      [p => { p.input.content_parts[0].segment_id = 'segment_other'; }, 'ReviewInputIdentity'],
      [p => { p.replies[0].reply_to_message_id = 'msg_other'; }, 'ReplyIdentity'],
      [p => { p.replies[0].role = 'assistant'; }, 'ReplyIdentity'],
      [p => { p.replies[0].content_parts[0].text = 'not on screen'; }, 'VisibleFinalMissing'],
    ]) {
      const message = await mount(), observation = await begin(message), proof = await clickAndFinishData(message);
      const correct = structuredClone(proof); alter(proof);
      await assert.rejects(observation.window.finish(proof), new RegExp(reason));
      assert.equal(observation.rows.length, 2, 'failure evidence saved before assertion');
      await assert.rejects(observation.window.finish(correct), /WindowClosed/, 'later correct state cannot fill the original gap');
    }
    for (const hide of [true, false]) {
      const message = await mount(), observation = await begin(message), proof = await clickAndFinishData(message);
      await page.evaluate(({ hide, replyId }) => {
        if (hide) document.querySelector(`[data-message-key="${replyId}"]`).hidden = true;
        else { const root = document.querySelector('#fixture'); root.replaceWith(root.cloneNode(true)); }
      }, { hide, replyId: proof.replies[0].message_id });
      await assert.rejects(observation.window.finish(proof), new RegExp(hide ? 'VisibleFinalMissing' : 'OriginalChatIdentity'));
    }
    await page.evaluate(() => panel.destroy());
    assert.deepEqual(errors, []);
    assert.deepEqual(requests.filter(url => new URL(url).pathname.startsWith('/api/')), [],
      'fixture uses no real service actions; native static asset loads are intercepted');
    console.log('Native Chat Review evidence: exact Diff/input/reply identity, completed visible final, accepted-only rejection and closed-window no-backfill. Isolated Chromium only.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
