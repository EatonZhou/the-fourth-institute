'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1100, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<!doctype html><html><body><main></main><aside id="chat"></aside></body></html>');
    await page.addStyleTag({path: path.join(root, 'portal_resident_menu.css')});
    await page.evaluate(() => {
      const tag = (strings, ...values) => strings.reduce((s, value, i) => s + value + (values[i] ?? ''), '');
      window.PortalI18n = {t: x => x, html: tag, text: tag, locale: () => 'zh-CN'};
      window.calls = []; window.notices = []; window.blockMutation = false; window.failMutation = false;
      window.view = {can_organize: true, pending: [
        {message_id: 'agent', kind: 'request', held: false, preview: 'Agent request', author_id: 'agent'},
        {message_id: 'human', kind: 'request', held: false, preview: 'Human request', author_id: 'person'},
        {message_id: 'info', kind: 'info', preview: 'Background'},
      ], active: {message_id: 'active', kind: 'request', preview: 'Already running'}, recent: []};
      // Only the ISA transport is mocked: service, protocol, rendering, event
      // handlers and operation locks are production code, exercised by clicks.
      window.PortalInstructionClient = {execute: async (instruction, args, options) => {
        calls.push({instruction, args: structuredClone(args), key: options.idempotencyKey});
        if (instruction === 'GetMailbox') return structuredClone(view);
        if (blockMutation) await new Promise(resolve => { window.releaseMutation = resolve; });
        if (failMutation) {
          view.pending = view.pending.filter(item => item.message_id !== args.message_id);
          throw Error('request already admitted');
        }
        if (instruction === 'SetMailboxItemHold') view.pending.find(item => item.message_id === args.message_id).held = args.held;
        else if (instruction === 'ReorderMailbox') {
          const item = view.pending.find(entry => entry.message_id === args.message_id);
          view.pending = view.pending.filter(entry => entry !== item);
          const before = view.pending.findIndex(entry => entry.message_id === args.before_message_id);
          const after = view.pending.findIndex(entry => entry.message_id === args.after_message_id);
          view.pending.splice(before >= 0 ? before : after + 1, 0, item);
        } else if (!['MergeMailboxItems', 'SplitMailboxItem'].includes(instruction)) throw Error('Unexpected ' + instruction);
        return {resident_id: args.resident_id};
      }};
    });
    for (const name of ['portal_ui_primitives.js', 'portal_resident_service.js', 'resident_protocol.js', 'hatg_mailbox_panel.js', 'chat_workspace_inputs.js']) {
      const content = process.env.PORTAL_MAILBOX_UI_BASELINE === '1'
        ? execFileSync('git', ['show', `HEAD:code/management_portal/static/${name}`], {cwd: root, encoding: 'utf8'})
        : fs.readFileSync(path.join(root, name), 'utf8');
      await page.addScriptTag({content});
    }
    await page.evaluate(() => {
      window.panel = PortalMailboxPanel.mount({container: document.querySelector('main'), residentId: 'resident_fixture',
        toast: (text, kind) => notices.push({text, kind})});
    });
    const row = id => page.locator(`[data-mailbox-item="${id}"]`);
    const action = (id, name) => row(id).locator(`[data-mailbox-action="${name}"]`);
    await page.locator('[data-mailbox-state="ready"]').waitFor();
    assert.equal(await action('human', 'first').count(), 1, 'explicit move-to-first button');
    assert.equal(await action('agent', 'first').isDisabled(), true);
    assert.equal(await action('info', 'hold').count(), 0, 'info cannot be held');
    assert.equal(await action('active', 'hold').count(), 0, 'running work cannot be held');
    assert.match(await page.locator('.mailboxHint').first().textContent(), /保留并跳过.*不打断/);

    await page.evaluate(() => {blockMutation = true;});
    await action('agent', 'hold').click();
    await page.waitForFunction(() => !!window.releaseMutation);
    assert.equal(await action('human', 'first').isDisabled(), true, 'in-flight operations lock all organizers');
    await page.evaluate(() => {document.querySelector('[data-mailbox-item="agent"] [data-mailbox-action="hold"]').click();});
    assert.equal(await page.evaluate(() => calls.filter(c => c.instruction === 'SetMailboxItemHold').length), 1);
    await page.evaluate(() => {blockMutation = false; releaseMutation();});
    await action('agent', 'resume').waitFor();
    assert.match(await row('agent').textContent(), /已暂存/);
    assert.deepEqual(await page.evaluate(() => calls.find(c => c.instruction === 'SetMailboxItemHold').args),
      {resident_id: 'resident_fixture', message_id: 'agent', held: true});
    assert.deepEqual(await page.evaluate(() => view.pending.map(item => item.message_id)), ['agent', 'human', 'info'], 'hold preserves position');

    await row('agent').locator('[data-mailbox-select]').check();
    await row('human').locator('[data-mailbox-select]').check();
    assert.equal(await page.locator('[data-mailbox-action="merge"]').isDisabled(), true, 'mixed hold state cannot merge');
    await action('human', 'hold').click();
    await action('human', 'resume').waitFor();
    assert.equal(await page.locator('[data-mailbox-action="merge"]').isDisabled(), false, 'all held items remain mergeable');
    await action('agent', 'resume').click();
    await action('agent', 'hold').waitFor();
    assert.deepEqual(await page.evaluate(() => view.pending.map(item => item.message_id)), ['agent', 'human', 'info'], 'resume preserves position');
    assert.equal(await page.evaluate(() => calls.filter(c => c.instruction === 'SetMailboxItemHold').at(-1).args.held), false);
    await action('human', 'first').click();
    await page.waitForFunction(() => view.pending[0].message_id === 'human' && !document.querySelector('main [aria-busy="true"]'));
    assert.deepEqual(await page.evaluate(() => calls.find(c => c.instruction === 'ReorderMailbox').args),
      {resident_id: 'resident_fixture', message_id: 'human', before_message_id: 'agent'});
    assert.equal(await page.evaluate(() => view.active.message_id), 'active', 'move never preempts running work');
    await row('agent').focus();
    await page.keyboard.press('Alt+ArrowUp');
    await page.waitForFunction(() => view.pending[0].message_id === 'agent');
    await page.evaluate(() => {failMutation = true;});
    await action('agent', 'hold').click();
    await row('agent').waitFor({state: 'detached'});
    assert.match(await page.evaluate(() => notices.at(-1).text), /request already admitted/, 'race error is reported and view refreshed');
    await page.evaluate(async () => {
      failMutation = false; view.can_organize = false; await panel.reload();
    });
    assert.equal(await page.locator('[data-mailbox-action="hold"], [data-mailbox-action="resume"], [data-mailbox-action="first"]').count(), 0, 'read-only viewers have no organizing controls');
    await page.evaluate(async () => {
      view.can_organize = true; delete view.pending[0].held; await panel.reload();
    });
    assert.equal(await action('human', 'hold').count() + await action('human', 'resume').count(), 0, 'old backend without held does not advertise hold');
    assert.equal(await page.evaluate(() => {
      try {PortalResidentService.setMailboxItemHold({resident_id:'r',message_id:'m',held:'false'}); return false;}
      catch (error) {return error instanceof TypeError;}
    }), true, 'hold API requires a boolean');

    const chat = await page.evaluate(() => {
      const deps = Object.fromEntries(PortalChatWorkspaceInputs.dependencies.map(name => [name, () => {}]));
      deps.escapeHtml = PortalUIPrimitives.escapeHtml;
      deps.renderMarkdownWithAttachmentRefs = (value, options) => options.renderMarkdown(value);
      const inputs = PortalChatWorkspaceInputs.createInputs(deps);
      const root = document.querySelector('#chat');
      root.innerHTML = inputs.workspaceQueueHtml({items:[
        {message_id:'held', held:true, content_preview:'Agent request'},
        {message_id:'waiting', held:false, content_preview:'Human request'},
      ], active_turn_input_supported:true}, {mailbox:true});
      let opened = 0;
      inputs.bindWorkspaceQueue(root, {onMailbox: () => {opened++;}});
      root.querySelector('[data-workspace-queue-mailbox]').click();
      return {opened, text:root.textContent, heldInject:!!root.querySelector('[data-workspace-queue-inject="held"]'),
        waitingInject:!!root.querySelector('[data-workspace-queue-inject="waiting"]')};
    });
    assert.equal(chat.opened, 1, 'Chat queue visibly links to Mailbox operations');
    assert.match(chat.text, /暂存、移到队首/);
    assert.match(chat.text, /已暂存/);
    assert.equal(chat.heldInject, false, 'held item has no injection bypass');
    assert.equal(chat.waitingInject, true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({status:'passed', browser:'chromium', workers:1,
      scope:'production Mailbox/Service/Protocol + Chat queue renderer; transport fixture only',
      checks:['hold/resume boolean and identity','preserved order','move first','keyboard reorder','active unchanged',
        'inflight lock','mixed/all-held merge','admission race refresh','permission','old backend','Chat discovery/injection guard']}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
