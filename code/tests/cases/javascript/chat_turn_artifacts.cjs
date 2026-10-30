'use strict';
// The plan and changes card of a turn in the production Resident panel: the
// review button sends one turn_review and keeps its message ID for a retry,
// the diff opens in a read-only editor with a file picker and Preview, the
// editor is released when its card leaves, and an answered question group is
// sent once however often it is answered again while sending.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<main id="chat"></main>', {
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
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

const RESIDENT = 'resident_111111111111';
w.PortalAPI = { get: async () => ({ config: {}, restart: null }) };
w.PortalInstructionClient = {
  execute: async instruction => {
    if (instruction === 'ListInputHistory') return { entries: [] };
    throw new Error(`Unexpected instruction: ${instruction}`);
  },
};
const sent = [];
let reply = null;
w.PortalResidentProtocol = {
  ...w.PortalResidentProtocol,
  sendInput: async payload => {
    sent.push(plain(payload));
    if (reply) return reply.promise;
    return { resident_id: payload.resident_id, message_id: payload.message.message_id, input_state: { state: 'queued' } };
  },
};
const previews = [];
let previewResult = true;
w.PortalPreview = { request: async request => { previews.push({ ...request }); return previewResult; } };
const editors = [];
const FILE_A = 'diff --git a/src/a.js b/src/a.js\n--- a/src/a.js\n+++ b/src/a.js\n@@ -1 +1 @@\n-old\n+new';
const FILE_B = 'diff --git a/src/b.js b/src/b.js\ndeleted file mode 100644\n--- a/src/b.js\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone';
const DIFF = `${FILE_A}\n${FILE_B}`;
w.PortalCodeEditor = {
  splitDiffFiles: diff => (diff === DIFF
    ? [{ label: 'src/a.js', path: 'src/a.js', diff: FILE_A }, { label: 'src/b.js', path: 'src/b.js', deleted: true, diff: FILE_B }]
    : [{ label: 'one', path: 'one', diff }, { label: 'two', path: 'two', diff }]),
  mount: async ({ root, value, readOnly, language }) => {
    const editor = { root, values: [value], readOnly, language, destroyed: 0, setValue(next) { this.values.push(next); }, destroy() { this.destroyed += 1; } };
    editors.push(editor);
    return editor;
  },
};

const errors = [];
const panel = w.PortalChatWorkspace.panelPackage.create({
  root: w.document.getElementById('chat'), id: 'turnArtifacts', autoLoad: false, target: { resident_id: RESIDENT },
  onError: error => errors.push(error.message),
});
let snap = { messages: [], controls: { can_send: true }, queue: { items: [] } };
panel.protocol = { snapshot: () => snap, destroy() {} };
panel.residentProjection = {
  resident: { resident_id: RESIDENT },
  chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: RESIDENT },
    header: { title: 'Turn artifacts' }, messages: [], composer: { can_send: true } },
};
const show = (messages, controls = { can_send: true }) => {
  snap = { messages, controls, queue: { items: [] } };
  panel.applySnapshot(snap);
};
const turn = (id, part) => ({ message_id: id, role: 'agent', created_at: '2026-10-26T01:00:00Z', content_parts: [part] });
const card = id => w.document.querySelector(`[data-native-turn="${id}"]`);

async function reviewButton() {
  reply = deferred();
  show([turn('turn_1', { type: 'native_turn', segment_id: 'seg_1', diff: DIFF }), turn('turn_2', { type: 'native_turn', segment_id: 'seg_2', diff: FILE_A })]);
  const review = () => card('turn_1').querySelector('[data-turn-review]');
  assert.equal(text(card('turn_1').querySelector('.residentQuestionTitle')), '本轮修改');
  assert.equal(review().disabled, false, 'a turn with changes can be reviewed');
  review().click();
  assert.equal(sent.length, 1);
  assert.match(sent[0].message.message_id, /^msg_[0-9a-f]{32}$/);
  assert.deepEqual({ ...sent[0], message: { ...sent[0].message, message_id: '' } }, {
    resident_id: RESIDENT, message: { message_id: '', role: 'user', content_parts: [{ type: 'turn_review', segment_id: 'seg_1' }] },
  }, 'review asks the Resident to review this turn');
  assert.equal(review().disabled, true, 'a review in flight cannot be sent again');
  show(snap.messages);
  assert.equal(review().disabled, true, 'not even after the conversation is drawn again');
  review().click();
  assert.equal(sent.length, 1);
  reply.resolve({ resident_id: RESIDENT, input_state: { state: 'queued' } });
  await settle();
  assert.equal(text(review()), '审查已排队');
  assert.equal(review().disabled, true);

  reply = deferred();
  const second = () => card('turn_2').querySelector('[data-turn-review]');
  second().click();
  reply.reject(new Error('delivery refused'));
  await settle();
  assert.equal(text(second()), '重试审查', 'a refused review can be retried');
  assert.equal(second().title, 'delivery refused');
  assert.equal(second().disabled, false);
  reply = null;
  second().click();
  await settle();
  assert.equal(sent[2].message.message_id, sent[1].message.message_id, 'the retry sends the same message');
  assert.equal(sent[2].message.content_parts[0].segment_id, 'seg_2');

  show([turn('turn_3', { type: 'native_turn', segment_id: 'seg_3', diff: FILE_A })], { can_send: false });
  assert.equal(card('turn_3').querySelector('[data-turn-review]').disabled, true, 'a Chat that cannot send cannot review');
  show([turn('turn_4', { type: 'native_turn', segment_id: 'seg_4', diff: FILE_A, diff_truncated: true })]);
  assert.equal(card('turn_4').querySelector('[data-turn-review]').disabled, true, 'a cut diff cannot be reviewed');
}

async function diffEditor() {
  show([turn('turn_5', { type: 'native_turn', segment_id: 'seg_5', diff: DIFF })]);
  const host = card('turn_5');
  const details = host.querySelector('details');
  const select = host.querySelector('[data-turn-diff-file]');
  const preview = host.querySelector('[data-turn-file-preview]');
  assert.equal(select.parentElement.hidden, true, 'the file picker waits for the diff to open');
  details.open = true;
  details.dispatchEvent(new w.Event('toggle'));
  await settle();
  assert.equal(editors.length, 1, 'opening the diff mounts an editor');
  assert.deepEqual([editors[0].values[0], editors[0].readOnly, editors[0].language], [DIFF, true, 'diff']);
  assert.equal(editors[0].root, host.querySelector('[data-turn-diff]'));
  assert.deepEqual([...select.options].map(option => [option.value, option.textContent]),
    [['', '全部修改（2 个文件）'], ['0', 'src/a.js'], ['1', 'src/b.js']]);
  assert.equal(select.parentElement.hidden, false);
  assert.equal(select.disabled, false);
  assert.equal(preview.disabled, true, 'all changes together have no file to open');
  assert.equal(preview.title, '请先选择一个文件');

  select.value = '0';
  select.dispatchEvent(new w.Event('change'));
  assert.equal(editors[0].values.at(-1), FILE_A, 'a chosen file shows its own diff');
  assert.equal(preview.disabled, false);
  assert.equal(preview.title, '在 Preview 中打开当前文件');
  preview.click();
  await settle();
  assert.deepEqual(previews.map(request => [request.href, request.path, request.source]), [['src/a.js', 'src/a.js', 'chat']]);
  previewResult = false;
  preview.click();
  await settle();
  assert.deepEqual(errors, ['当前页面没有可用的 Preview'], 'a page without Preview says so');
  select.value = '1';
  select.dispatchEvent(new w.Event('change'));
  assert.equal(preview.disabled, true, 'a deleted file can only be read as a diff');
  assert.equal(preview.title, '文件已删除，仅可查看 Diff');

  details.open = false;
  details.dispatchEvent(new w.Event('toggle'));
  details.open = true;
  details.dispatchEvent(new w.Event('toggle'));
  await settle();
  assert.equal(editors.length, 1, 'reopening keeps the editor');

  show([turn('turn_6', { type: 'resource_changes', segment_id: 'seg_6', diff: 'resource diff',
    changes: [{ address: 'article_0123456789ab', path: 'Doc title' }, { address: 'skill_0123456789ab' }] })]);
  await settle();
  assert.equal(editors[0].destroyed, 1, 'the editor of a card that left is released');
  const resource = card('turn_6');
  resource.querySelector('details').open = true;
  resource.querySelector('details').dispatchEvent(new w.Event('toggle'));
  await settle();
  const resourceSelect = resource.querySelector('[data-turn-diff-file]');
  assert.deepEqual([...resourceSelect.options].map(option => option.textContent), ['全部修改（2 个文件）', 'Doc title', 'skill_0123456789ab'],
    'a resource change is named by its path or address');
  resourceSelect.value = '0';
  resourceSelect.dispatchEvent(new w.Event('change'));
  previewResult = true;
  resource.querySelector('[data-turn-file-preview]').click();
  await settle();
  assert.deepEqual([previews.at(-1).href, previews.at(-1).resourceAddress], ['article_0123456789ab', 'article_0123456789ab'],
    'Preview opens the changed resource by its address');
}

async function answersSentOnce() {
  const asking = id => ({
    message_id: `msg_${id}`, role: 'agent', created_at: '2026-10-26T02:00:00Z',
    content_parts: [{ type: 'text', text: 'Two questions' }, { type: 'async_questions', answer_message_id: id, state: 'open',
      questions: [{ question_index: 0, title: 'Scope?', options: ['定向', '全部'] }, { question_index: 1, title: 'Publish?', options: ['发布', '不发布'] }] }],
  });
  const stepper = () => w.document.querySelector('.residentQuestionStepper');
  sent.length = 0;
  reply = deferred();
  show([asking('answer_a')]);
  stepper().querySelector('[data-question-option="0"]').click();
  stepper().querySelector('[data-question-option="1"]').click();
  assert.equal(sent.length, 1, 'the last answer sends the group');
  assert.deepEqual(sent[0].message.content_parts[1], {
    type: 'async_question_answer', source_message_id: 'msg_answer_a',
    answers: [{ question_index: 0, answer: '定向' }, { question_index: 1, answer: '不发布' }],
  });
  stepper().querySelector('[data-question-option="0"]').click();
  stepper().dispatchEvent(new w.KeyboardEvent('keydown', { key: '1', bubbles: true }));
  const custom = stepper().querySelector('[data-question-custom]');
  if (custom) {
    custom.value = 'something else';
    custom.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  }
  panel.questions.answer('again');
  assert.equal(sent.length, 1, 'answering again while the group is sending sends nothing');
  reply.reject(new Error('mailbox full'));
  await settle();
  assert.match(text(stepper()), /mailbox full/, 'a refused answer says why');
  reply = null;
  stepper().querySelector('[data-question-option="0"]').click();
  await settle();
  assert.equal(sent.length, 2, 'and can be sent again');
  assert.equal(sent[1].message.message_id, 'answer_a', 'into the same answer slot');
}

(async () => {
  try {
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    await reviewButton();
    await answersSentOnce();
    await diffEditor();
    assert.equal(editors[1].root.isConnected, true);
    assert.equal(editors[1].destroyed, 0);
    panel.questions.destroy();
    assert.equal(editors[1].destroyed, 1, 'question cards destroyed with their cards still shown release the editors');
    panel.destroy();
    assert.ok(editors.every(editor => editor.destroyed === 1), 'destroying the panel releases every editor once');
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
