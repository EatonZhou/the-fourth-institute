'use strict';
// How a Resident's messages show beyond their text: delivery state, infos read
// with a request or waiting for the next one, a Mailbox group and its members,
// the member a Family reply came from, token use, the body of turn, question
// and Mailbox messages, a Family request's progress, what a facade's Stop
// stops and where its requests can be reassigned.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section>', {
  url: 'https://chat.fixture.invalid/lineage', pretendToBeVisual: true, runScripts: 'outside-only',
});
const w = dom.window;
const observers = new Set();
const NativeMutationObserver = w.MutationObserver;
w.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) { super(callback); observers.add(this); }
};
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const chat = w.PortalChatWorkspace;
const text = value => ({ type: 'text', text: value });
const html = markup => {
  const host = w.document.createElement('div');
  host.innerHTML = markup;
  return host;
};
const words = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const footer = (item, index = 0, items = [item]) => html(chat.residentMessageFooter(item, index, items));

function delivery() {
  const status = state => footer({ id: 'm', role: 'user', local_delivery: { state, error: 'boom' } }).querySelector('[data-message-delivery]');
  const actions = node => [...node.querySelectorAll('[data-portal-message-action]')].map(button => button.dataset.portalMessageAction);
  assert.equal(words(status('sending')), '发送中…');
  assert.deepEqual(actions(status('sending')), []);
  assert.equal(words(status('failed').querySelector('span')), '发送失败：boom');
  assert.ok(status('failed').classList.contains('is-failed'));
  assert.deepEqual(actions(status('failed')), ['submission-retry', 'submission-dismiss'], 'a failed send can be retried or removed');
  assert.equal(words(status('confirming').querySelector('span')), '正在确认发送状态');
  assert.deepEqual(actions(status('confirming')), ['submission-check']);
  assert.equal(status('accepted'), null, 'an accepted message shows no delivery state');
}

function infos() {
  const attached = footer({
    id: 'req', role: 'user', attached_infos_steward: true,
    attached_infos: [
      { message_id: 'i1', author_id: 'resident_0123456789ab', created_at: '2026-10-26T01:00:00Z', content_parts: [text('FYI one')] },
      { message_id: 'i2', origin: { sender: { id: 'sample_admin' } }, content_parts: [text('two'), { type: 'image' }, text('lines')] },
    ],
  }).querySelector('details.attachedInfos');
  assert.ok(attached, 'infos read with a request show inside it');
  assert.equal(attached.open, false, 'collapsed');
  assert.equal(words(attached.querySelector('summary')), '附带背景更新 2 条 · 已交给 steward');
  const rows = [...attached.querySelectorAll('[data-attached-info]')];
  assert.deepEqual(rows.map(row => row.dataset.attachedInfo), ['i1', 'i2']);
  assert.equal(rows[0].querySelector('[data-resource-label]')?.dataset.resourceLabel, 'resident_0123456789ab', 'a Resident author shows as its label');
  assert.match(words(rows[0].querySelector('.attachedInfoMeta')), /^resident_0123456789ab · \S/, 'with the time it was sent');
  assert.equal(words(rows[1].querySelector('.attachedInfoMeta')), 'sample_admin');
  assert.equal(rows[1].querySelector('.attachedInfoText').textContent, 'two\nlines', 'only the text parts');
  assert.equal(footer({ id: 'req', role: 'user', attached_infos: [{ message_id: 'i1' }] }).querySelector('summary').textContent, '附带背景更新 1 条');
  assert.equal(footer({ id: 'req', role: 'user', attached_infos: [] }).querySelector('.attachedInfos'), null);

  const held = item => footer({ id: 'info', role: 'user', ...item }).querySelector('[data-held-info]');
  assert.equal(held({}), null, 'an ordinary message is no info');
  assert.deepEqual([held({ held_info: true }).dataset.heldInfo, words(held({ held_info: true }))], ['true', '背景更新 · 随下一条任务送达']);
  assert.deepEqual([held({ released_with: 'req' }).dataset.heldInfo, words(held({ released_with: 'req' }))],
    ['released', '背景更新 · 已作为背景随一条请求送达，没有单独打断工作']);
  assert.equal(words(held({ held_info: true, held_info_steward: true })), '背景更新 · 已交给 steward');
  assert.equal(words(footer({ id: 'x', role: 'user', grouped_into_message_id: 'g' }).querySelector('[data-mailbox-member]')), '已并入 Mailbox 群体消息');
}

function familyReplies() {
  const reply = (id, extra = {}) => ({
    id, role: 'assistant', family_reply: { request_message_id: 'r1', resident_id: 'resident_m', title: 'Member', message_id: `member_${id}`, ...extra },
  });
  const items = [reply('o1'), reply('o2', { via: [{ title: 'Router' }, { resident_id: 'resident_s' }], reassigned_from: [{ title: 'Old' }, { title: 'Older' }] })];
  assert.equal(footer(items[0], 0, items).querySelector('.familyReply'), null, 'an output followed by another for the same request names no member');
  const last = footer(items[1], 1, items).querySelector('.familyReply');
  assert.equal(last.dataset.familyReplyMember, 'resident_m');
  assert.equal(words(last), '由 Member 回复（经 Router → resident_s）（改派自 Older）');
  assert.equal(last.querySelector('[data-family-reply-source]').getAttribute('href'), '/lineage?resident=resident_m&message=member_o2&open_chat=1',
    'the member\'s reply opens in its own Conversation');
  assert.equal(words(footer({ id: 'u', role: 'user', family_reply: { title: 'Member' } }).querySelector('.familyReply')), '来自 Member');
  assert.equal(footer({ ...reply('o3'), family_status: { state: 'running' } }).querySelector('.familyReply'), null,
    'while the request is open its progress names the member');
}

function tokens() {
  assert.equal(words(footer({ id: 't', role: 'assistant', token_usage: { total_tokens: 1234567, input_tokens: 1500, output_tokens: 12, reasoning_output_tokens: 0 } })
    .querySelector('.messageTokenNote')), 'tokens 1.2M · in 1.5K · out 12');
  assert.equal(footer({ id: 't', role: 'assistant', token_usage: {} }).querySelector('.messageTokenNote'), null);
  assert.deepEqual([0, -1, 999, 1000, 'x'].map(chat.formatTokenCount), ['0', '0', '999', '1.0K', '0']);

  const all = footer({
    id: 'a', role: 'user', local_delivery: { state: 'sending' }, attached_infos: [{ message_id: 'i' }], held_info: true,
    recipients: [{ kind: 'user', id: 'sample_admin' }], token_usage: { total_tokens: 5 },
  });
  assert.deepEqual([...all.children].map(node => node.className.split(' ').pop()),
    ['messageDeliveryStatus', 'attachedInfos', 'heldInfo', 'outwardMessage', 'messageTokenNote'], 'the footers keep their order');
}

function bodies() {
  const context = { renderContent: value => `<p>${value}</p>`, bodyHtml: '<p>plain</p>', content: 'plain' };
  const body = item => html(chat.residentMessageBody(item, context));
  const group = body({
    id: 'g', role: 'user', expanded_members: [
      { message_id: 'x1', author_id: 'u1', content_parts: [text('short')] },
      { message_id: 'x2', author_id: 'u2', content_parts: [text('\nline one\nline two')] },
    ],
  });
  assert.equal(words(group.querySelector('.mailboxGroupNote')), '合并了 2 条消息，作为一次输入处理', 'a Mailbox group shows its members');
  assert.equal(group.querySelector('[data-mailbox-member="x1"]').tagName, 'SECTION', 'a short member reads in full');
  const long = group.querySelector('[data-mailbox-member="x2"]');
  assert.equal(long.tagName, 'DETAILS', 'a long member opens from a preview');
  assert.equal(words(long.querySelector('.mailboxMemberPreview')), 'line one');
  assert.equal(long.dataset.messageDisclosure, 'mailbox-member:x2');

  const turn = body({ id: 'turn', role: 'assistant', content_parts: [{ type: 'native_turn', plan: [{ step: 'Write', status: 'completed' }, { step: 'Ship', status: 'pending' }], diff: '--- a\n+++ b' }] });
  const card = turn.querySelector('[data-native-turn="turn"]');
  assert.equal(words(card.querySelector('.residentQuestionTitle')), '本轮计划与修改');
  assert.ok(card.querySelector('[data-turn-review]'), 'a turn with changes can be reviewed');
  assert.deepEqual([...card.querySelectorAll('ol li')].map(words), ['已完成 Write', '待处理 Ship']);
  assert.equal(card.querySelector('[data-turn-diff]').textContent, '--- a\n+++ b');
  const changes = body({ id: 'rc', role: 'assistant', content_parts: [{ type: 'resource_changes', changes: [{ address: 'article_0123456789ab', before_version: { number: 2 }, after_sha256: 'abcdef0123456789' }] }] });
  assert.equal(words(changes.querySelector('.resourceTurnChanges li')), 'article_0123456789ab v2 → abcdef01');
  assert.equal(words(body({ id: 'e', role: 'assistant', content_parts: [{ type: 'native_turn' }] }).querySelector('.nativeTurnEmpty')),
    '本轮未记录到可展示的计划或文件差异');
  assert.equal(body({ id: 'h', role: 'user', content_parts: [{ type: 'native_turn', diff: 'x' }] }).innerHTML, '<p>plain</p>',
    'a person\'s message is never a turn card');

  const interaction = body({ id: 'n', role: 'assistant', content_parts: [
    { type: 'tool_call', tool_name: 'request_user_input', request_id: 'req_1' },
    { type: 'tool_result', tool_name: 'request_user_input', status: 'answered', answers: { a: { answers: ['Yes'] }, b: { answers: ['Later'] } } },
    { type: 'tool_result', tool_name: 'request_approval', status: 'expired' },
  ] });
  assert.equal(interaction.querySelector('[data-resident-question]').dataset.residentQuestion, 'req_1', 'an open request gets a card host');
  assert.deepEqual([...interaction.querySelectorAll('.residentQuestionStatus')].map(words), ['回答已送达：Yes；Later', '请求已失效']);
  const asked = body({ id: 'q', role: 'assistant', content_parts: [text('Before'), { type: 'async_questions', answer_message_id: 'ans_1' }] });
  assert.equal(asked.innerHTML, '<p>Before</p><div class="residentQuestionHost" data-resident-question="ans_1"></div>');
  assert.equal(body({ id: 'p', role: 'assistant', content_parts: [text('plain')] }).innerHTML, '<p>plain</p>');
}

function requestProgress() {
  const progress = (status, options) => html(chat.requestProgressHtml({ request_message_id: 'r1', title: 'Member', ...status }, { key: 'k', anchorKey: 'a' }, options))
    .querySelector('[data-family-request]');
  const actions = node => [...node.querySelectorAll('[data-portal-message-action]')].map(button => `${button.dataset.portalMessageAction}${button.disabled ? ':disabled' : ''}`);
  const queued = progress({ state: 'queued', ahead: 2, resident_id: 'resident_m', message_id: 'mm' });
  assert.equal(words(queued.querySelector('.familyRequestLabel')), '已交给 Member · 排队中（前面还有 2 条）');
  assert.deepEqual(actions(queued), ['family-request-inject', 'family-request-correct', 'family-request-withdraw']);
  assert.equal(queued.dataset.requestResident, 'resident_m');
  assert.deepEqual([queued.dataset.messageKey, queued.dataset.messageAfterOf], ['k', 'a']);
  assert.equal(queued.querySelector('[data-family-request-source]').getAttribute('href'), '/lineage?resident=resident_m&open_chat=1');
  const unconfirmed = progress({ state: 'queued', ahead: 0 });
  assert.equal(words(unconfirmed.querySelector('.familyRequestLabel')), '已交给 Member · 排队中（下一条处理）');
  assert.deepEqual(actions(unconfirmed), ['family-request-inject:disabled', 'family-request-correct', 'family-request-withdraw'],
    'inject waits until the member\'s queued message is known');
  assert.deepEqual(actions(progress({ state: 'routing' })), ['family-request-withdraw']);
  const stopping = progress({ state: 'running', work_state: 'stopping' });
  assert.equal(stopping.dataset.familyRequestState, 'stopping');
  assert.equal(words(stopping.querySelector('.familyRequestLabel')), 'Member · 正在停止当前任务');
  assert.deepEqual(actions(stopping), [], 'a request already stopping offers no Stop');
  assert.deepEqual(actions(progress({ state: 'running' })), ['family-request-stop']);
  assert.deepEqual(actions(progress({ state: 'queued', ahead: 1 }, { actions: false })), [], 'a Chatroom shows the progress without actions');
  assert.equal(chat.requestProgressHtml({ state: 'completed' }), '', 'a finished request shows no progress');
}

function familyControls() {
  const { familyExecution } = w.PortalChatWorkspaceResidentMessages;
  const request = (message_id, relay, role = 'user') => ({ message_id, role, relay });
  const execution = familyExecution({ messages: [
    request('a', { state: 'running', title: 'M1' }),
    request('b', { state: 'running', work_state: 'stopping' }),
    request('c', { state: 'routing', resident_id: 'resident_x' }),
    request('d', { state: 'queued' }),
    request('e', { state: 'scope_review' }),
    request('f', { state: 'completed' }),
    request('g', { state: 'running' }, 'agent'),
    { message_id: 'h', role: 'user' },
  ] });
  assert.deepEqual(JSON.parse(JSON.stringify(execution.active.map(item => [item.message_id, item.member]))), [['a', 'M1'], ['c', 'resident_x']],
    'Stop stops what is running or being routed, not what is already stopping');
  assert.equal(execution.waiting, 2, 'and says how many requests stay queued');
  assert.deepEqual(JSON.parse(JSON.stringify(familyExecution({}))), { active: [], waiting: 0 });

  const unused = name => () => { throw new Error(`${name} is not used here`); };
  const dependencies = Object.fromEntries(w.PortalChatWorkspaceResidentMessages.dependencies.map(name => [name, unused(name)]));
  Object.assign(dependencies, {
    escapeHtml: w.PortalUIPrimitives.escapeHtml, messageRole: chat.messageRole,
    messageSubmenuHtml: (id, label, items) => `<div data-submenu="${id}" data-label="${label}">${items}</div>`,
  });
  const { familyReassignMenuItems } = w.PortalChatWorkspaceResidentMessages.createResidentMessages(dependencies);
  const family = { members: [{ resident_id: 'resident_a', title: 'A' }, { resident_id: 'resident_b', title: 'B' }, { resident_id: 'resident_c' }] };
  const menu = html(familyReassignMenuItems({ role: 'user', relay: { resident_id: 'resident_a' } }, family)).firstElementChild;
  assert.deepEqual([menu.dataset.submenu, menu.dataset.label], ['family-reassign', '改派']);
  assert.deepEqual([...menu.querySelectorAll('[data-family-member]')].map(button => [button.dataset.familyMember, words(button)]),
    [['resident_b', 'B'], ['resident_c', 'resident_c']], 'a request moves to any other member');
  assert.equal(familyReassignMenuItems({ role: 'assistant', relay: { resident_id: 'resident_a' } }, family), '', 'only a request can be reassigned');
  assert.equal(familyReassignMenuItems({ role: 'user' }, family), '', 'only once a member has it');
  assert.equal(familyReassignMenuItems({ role: 'user', relay: { resident_id: 'resident_a' } }, { members: [family.members[0]] }), '', 'and another member exists');
}

(async () => {
  try {
    // The page's enhancers start once the document has loaded, as on a real page.
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    delivery();
    infos();
    familyReplies();
    tokens();
    bodies();
    requestProgress();
    familyControls();
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    observers.forEach(observer => observer.disconnect());
    await new Promise(resolve => setTimeout(resolve, 0));
    w.close();
  }
})();
