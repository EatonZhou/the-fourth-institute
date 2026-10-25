'use strict';
// A Resident's conversation as the Chat shows it: protocol messages become
// Chat messages, local submissions join them, infos delivered with a request
// fold into it, a Family facade's open requests carry their progress, and the
// snapshot's controls, queue, history and runtime fill the Chat contract.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
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
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const chat = w.PortalChatWorkspace;
const plain = value => JSON.parse(JSON.stringify(value));
const text = value => ({ type: 'text', text: value });
const project = (snapshot, metadata = {}) => plain(chat.projectResidentConversation(metadata, snapshot));
const byId = messages => Object.fromEntries(messages.map(message => [message.id, message]));

function protocolMessages() {
  const one = message => plain(chat.residentProtocolMessageToWorkspace(message));
  const reply = one({ message_id: 'm1', role: 'agent', origin: { sender: { id: 'resident_a' } }, usage: { output_tokens: 3 }, content_parts: [text('Hello'), text('world')] });
  assert.equal(reply.id, 'm1');
  assert.equal(reply.role, 'assistant', 'an agent message is the assistant');
  assert.equal(reply.author, 'resident_a', 'the author is who sent it');
  assert.equal(reply.content, 'Hello\nworld', 'text parts join line by line');
  assert.deepEqual(reply.token_usage, { output_tokens: 3 });
  assert.deepEqual(reply.input_media, []);

  const image = { type: 'image', name: 'a.png', source: '/files/a.png' };
  const mediaOnly = one({ message_id: 'm2', role: 'user', author_id: 'sample_admin', content_parts: [text('图片附件：a.png'), image] });
  assert.equal(mediaOnly.content, '[image: /files/a.png]', 'the technical caption of a media-only message is not shown');
  assert.equal(mediaOnly.author, 'sample_admin');
  assert.deepEqual(mediaOnly.input_media, [image]);
  assert.equal(one({ message_id: 'm3', role: 'user', content_parts: [text('see /files/b.pdf'), { type: 'file', name: 'b.pdf', path: '/files/b.pdf' }] }).content,
    'see /files/b.pdf', 'media the text already names is not added again');
  assert.equal(one({ message_id: 'm4', role: 'user', content_parts: [{ type: 'turn_review', segment_id: 's1' }, text('internal wording')] }).content,
    '请审查这一轮的修改', 'a review request reads as one');
  assert.equal(one({ message_id: 'm5', role: 'agent', content_parts: [{ type: 'async_questions', questions: [{ title: 'Q1' }, { title: 'Q2' }] }] }).content, 'Q1\nQ2');
  assert.equal(one({ message_id: 'm6', role: 'agent', content_parts: [{ type: 'subagent_run', agent_type: 'explore', description: 'Look' }] }).content,
    '子 agent · 探索：Look');
  assert.equal(one({ message_id: 'm7', role: 'agent', content_parts: [{ type: 'native_turn', diff: 'x' }] }).content, '本轮修改');
  assert.equal(one({ message_id: 'm8', role: 'agent', content_parts: [{ type: 'tool_call', tool_name: 'request_user_input', questions: [{ question: 'Pick one?' }] }] }).content, 'Pick one?');
  assert.equal(one({ message_id: 'm9', role: 'agent', content_parts: [{ type: 'tool_result', tool_name: 'request_approval', status: 'answered' }] }).content, '回答已送达');
  assert.equal(one({ message_id: 'm10', role: 'agent', content_parts: [{ type: 'tool_call', tool_name: 'other_tool' }] }).content, '', 'other tools add no text');
  const local = one({ message_id: 'm11', role: 'user', local_delivery: true, content_parts: [text('sending')] });
  assert.equal(local.pending, true, 'a message still being delivered is pending');
  assert.equal(one({ message_id: 'm12', content_parts: [] }).role, 'system');
}

function localSubmissions() {
  const projection = project({
    messages: [{ message_id: 'm1', role: 'user', content_parts: [text('first')] }],
    local_submissions: [
      { message_id: 'm1', role: 'user', content_parts: [text('first again')] },
      { message_id: 'local_1', role: 'user', local_delivery: true, content_parts: [text('queued')] },
    ],
  });
  assert.deepEqual(projection.messages.map(message => [message.id, message.content, !!message.pending]),
    [['m1', 'first', false], ['local_1', 'queued', true]], 'a local submission shows once until the trace has it');
}

function deliveredInfos() {
  const messages = [
    { message_id: 'req', role: 'user', content_parts: [text('Do it')], attached_infos: [{ message_id: 'info0' }] },
    { message_id: 'info0', role: 'user', released_with: 'req', content_parts: [text('known')] },
    { message_id: 'info1', role: 'user', released_with: 'req', author_id: 'resident_b', created_at: '2026-10-26T01:00:00Z', content_parts: [text('FYI')], extra: 'x' },
    { message_id: 'info2', role: 'user', released_with: 'req', held_info: true, content_parts: [text('waiting again')] },
    { message_id: 'info3', role: 'user', released_with: 'not_shown', content_parts: [text('request not loaded')] },
  ];
  const plainChat = project({ messages });
  assert.deepEqual(plainChat.messages.map(message => message.id), ['req', 'info2', 'info3'],
    'infos read with a shown request leave the list; a held info and one whose request is not shown stay');
  const request = plainChat.messages[0];
  assert.deepEqual(request.attached_infos, [
    { message_id: 'info0' },
    { message_id: 'info1', author_id: 'resident_b', created_at: '2026-10-26T01:00:00Z', content_parts: [text('FYI')] },
  ], 'the request keeps the infos it showed and adds what each new info said');
  assert.equal(request.attached_infos_steward, undefined);
  const facade = project({ messages, family_facade: { title: 'Fam', members: [] } });
  assert.equal(facade.messages[0].attached_infos_steward, true, 'a facade hands folded infos to its Steward');
}

function familyRequests() {
  const projection = project({
    family_facade: { title: 'Fam', members: [{ resident_id: 'resident_m', title: 'Member' }] },
    state: { work_state: 'running' },
    activities: [{ kind: 'tool' }],
    messages: [
      { message_id: 'r1', role: 'user', relay: { state: 'running', member: 'resident_m' }, content_parts: [text('one')] },
      { message_id: 'o1', role: 'agent', family_reply: { request_message_id: 'r1' }, content_parts: [text('working')] },
      { message_id: 'o2', role: 'agent', family_reply: { request_message_id: 'r1' }, content_parts: [text('still working')] },
      { message_id: 'r2', role: 'user', relay: { state: 'completed' }, content_parts: [text('two')] },
      { message_id: 'r3', role: 'user', relay: { state: 'queued' }, content_parts: [text('three')] },
    ],
  });
  const messages = byId(projection.messages);
  assert.equal(messages.r1.family_status, undefined, 'the progress follows the request\'s last output');
  assert.deepEqual(messages.o2.family_status, { state: 'running', member: 'resident_m', request_message_id: 'r1' });
  assert.deepEqual([messages.o1.live_output, messages.o2.live_output], [true, true], 'outputs of a running request stay open');
  assert.equal(messages.r2.family_status, undefined, 'a finished request shows no progress');
  assert.deepEqual(messages.r3.family_status, { state: 'queued', request_message_id: 'r3' });
  assert.equal(messages.r3.live_output, undefined);
  assert.deepEqual(projection.runtime_activity, { live: false, details: [], phase: '' }, 'a facade shows work under each request');

  const ordinary = project({ messages: [{ message_id: 'r1', role: 'user', relay: { state: 'running' }, content_parts: [text('one')] }] });
  assert.equal(ordinary.messages[0].family_status, undefined, 'only a facade tracks requests');
}

function chatContract() {
  const snapshot = {
    resident: { resident_id: 'resident_a' },
    state: { work_state: 'running', work_phase: 'waiting_quota' },
    activities: [{ kind: 'tool', status: 'running' }],
    controls: { can_send: true, can_stop: true, active_turn_input: true, send_hint: '发送后排队', disabled_reason: '' },
    next_page_token: 'page-2',
    queue: { resident_id: 'resident_a', items: [{ message_id: 'q1' }] },
    runtime_settings: { backend: 'codex' },
    input_states: [{ message_id: 'q1', state: 'queued' }],
    messages: [],
  };
  const metadata = { title: 'A', chat_workspace: { schema: 'portal.chat_workspace.v3', composer: { placeholder: 'Say' }, history: { stale: true } } };
  const projection = project(snapshot, metadata);
  const contract = projection.chat_workspace;
  assert.equal(projection.title, 'A', 'the projection keeps what the Resident page knows');
  assert.deepEqual(contract.runtime, { live: true, details: snapshot.activities, phase: 'waiting_quota' });
  assert.deepEqual(projection.runtime_activity, contract.runtime);
  assert.equal(contract.composer.placeholder, 'Say', 'the page\'s composer settings stay');
  assert.deepEqual([contract.composer.can_send, contract.composer.can_stop, contract.composer.send_hint], [true, true, '发送后排队']);
  assert.deepEqual(contract.composer.queue, { resident_id: 'resident_a', items: [{ message_id: 'q1' }], active_turn_input_supported: true });
  assert.deepEqual(contract.composer.runtime_settings, { backend: 'codex' });
  assert.deepEqual(contract.history, { can_load_older: true }, 'a next page means older messages can load');
  assert.equal(contract.inheritance, null);
  assert.deepEqual(projection.input_states, snapshot.input_states);
  assert.equal(project({ ...snapshot, state: { work_state: 'stopping' } }, metadata).chat_workspace.runtime.phase, 'stopping');
  const idle = project({ resident: { resident_id: 'resident_a' }, messages: [] }, metadata).chat_workspace;
  assert.deepEqual([idle.runtime.live, idle.composer.can_send, idle.history.can_load_older], [false, false, false]);
  assert.deepEqual(idle.composer.queue, { resident_id: 'resident_a', items: [], active_turn_input_supported: false },
    'a snapshot without a queue gets an empty one');
  assert.equal(project(snapshot, { title: 'A' }).chat_workspace, undefined, 'a page without a Chat contract gets none');
}

function queueNotices() {
  const toasts = [];
  w.PortalUI.toast = (message, kind) => toasts.push([message, kind]);
  chat.noticeQueueCleared({ cancelled: 2 });
  assert.deepEqual(toasts, [['已清除 2 条排队消息，正在执行的消息不受影响。', 'success']]);
}

async function loading() {
  const calls = [];
  w.PortalResidentProjection = { load: async (id, options) => { calls.push(['projection', id, plain(options)]); return { title: 'A' }; } };
  w.PortalResidentProtocol = {
    ...w.PortalResidentProtocol,
    loadSnapshot: async (id, options) => {
      calls.push(['snapshot', id, plain(options)]);
      return { messages: [{ message_id: 'm1', role: 'agent', content_parts: [text('hi')] }] };
    },
  };
  const loaded = plain(await chat.loadResidentConversation({ resident_id: ' resident_a ' }));
  assert.deepEqual(calls, [['projection', 'resident_a', { force: false }], ['snapshot', 'resident_a', { force: false, historyLimit: 50 }]],
    'the page and the snapshot load together, 50 messages by default');
  assert.deepEqual([loaded.title, loaded.messages[0].content], ['A', 'hi']);
  calls.length = 0;
  await chat.loadResidentConversation({ resident_id: 'resident_a' }, { force: true, historyLimit: 5 });
  assert.deepEqual(calls.map(call => call[2]), [{ force: true }, { force: true, historyLimit: 5 }]);
  await assert.rejects(chat.loadResidentConversation({}), /resident_id required/);
}

(async () => {
  try {
    protocolMessages();
    localSubmissions();
    deliveredInfos();
    familyRequests();
    chatContract();
    queueNotices();
    await loading();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await new Promise(resolve => setTimeout(resolve, 0));
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
