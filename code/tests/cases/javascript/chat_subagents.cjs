'use strict';
// Subagents in the Chat: running ones show as live cards that update in
// place; a finished one shows as its result card where its live card stood;
// the detail dialog reads the run's whole trace page by page; the full
// result, the saved Markdown and the page title badge.
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
// JSDOM lays nothing out; a live card needs a size for its place to be kept.
w.Element.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 120, width: 320, height: 120 });
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const ago = seconds => new Date(Date.now() - seconds * 1000).toISOString();
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();

const startedA = ago(65);
const runA = {
  tool_use_id: 'tu_a', agent_type: 'Explore', description: 'Search the repo', started_at: startedA, model: 'model-a',
  usage: { total_tokens: 1500 }, steps_total: 3,
  steps: [
    { kind: 'tool', tool_name: 'Grep', text: 'subagent' },
    { kind: 'message', text: 'found three files' },
    { kind: 'tool', tool_name: 'Read', text: 'a.js', running: true, started_at: ago(3) },
  ],
};
const runB = { tool_use_id: 'tu_b', agent_type: 'plan', description: 'Plan the change', started_at: ago(5), steps: [] };
const live = (...runs) => runs.map(run => ({ kind: 'subagent', status: 'in_progress', execution_id: `exec_${run.tool_use_id}`, subagent: run }));

async function liveAndFinishedCards() {
  const finished = [];
  const root = w.document.getElementById('fixture');
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: 'chat', historyScope: () => 'conversation-subagents',
    renderMessageBody: w.PortalChatWorkspace.residentMessageBody,
    onSubagentFinished: run => finished.push(run),
    contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
  });
  const group = () => root.querySelector('[data-message-key="__subagents__"]');
  const card = id => root.querySelector(`[data-subagent-live="${id}"]`);
  const part = (id, name) => card(id)?.querySelector(`[data-subagent-part="${name}"]`);
  try {
    workspace.setMessages([], { history: { can_load_older: false } });
    workspace.setRuntime(live(runB, runA), true);
    await settle();
    assert.equal(group()?.dataset.count, '2', 'an empty Chat shows the runs that started');
    assert.equal(text(group().querySelector('.subagentLiveGroupHead')), '2 个子 agent 正在并行工作');
    assert.deepEqual([...group().querySelectorAll('.subagentLiveCard')].map(node => node.dataset.subagentLive), ['tu_a', 'tu_b'],
      'the earliest started run comes first');
    assert.equal(text(card('tu_a').querySelector('.subagentLiveType')), '探索前台', 'a built-in kind shows by what it does');
    assert.equal(text(card('tu_a').querySelector('.subagentLiveTask')), 'Search the repo');
    assert.match(text(part('tu_a', 'head').querySelector('.subagentLiveClock')), /^1:0[5-9]$/, 'the clock counts from the start');
    assert.match(text(part('tu_a', 'now')), /^Reada\.js0:0\d$/, 'the running call shows with its own clock');
    assert.deepEqual([...part('tu_a', 'trail').children].map(text), ['Grepsubagent', '说found three files']);
    assert.equal(text(part('tu_a', 'foot')), '第 3 步 · 1.5k tokensmodel-a');
    assert.equal(text(part('tu_b', 'now')), '正在启动…');
    assert.equal(text(part('tu_b', 'foot')), '等待第一步');

    const cardB = card('tu_b');
    const headA = part('tu_a', 'head');
    const trailA = part('tu_a', 'trail');
    const nextA = { ...runA, steps_total: 4, steps: [...runA.steps.slice(0, 2), { ...runA.steps[2], running: false }, { kind: 'message', text: 'reading done' }] };
    workspace.setRuntime(live(nextA, runB), true);
    await settle();
    assert.equal(card('tu_b'), cardB, 'a card that did not change stays the same node');
    assert.equal(part('tu_a', 'head'), headA, 'a part that did not change stays the same node');
    assert.notEqual(part('tu_a', 'trail'), trailA, 'a part that changed is replaced');
    assert.equal(text(part('tu_a', 'now')), '说reading done');
    assert.equal(text(part('tu_a', 'foot')), '第 4 步 · 1.5k tokensmodel-a');

    workspace.setRuntime(live(runB), true);
    await settle();
    assert.equal(card('tu_a'), null, 'a run that ended leaves the live cards');
    assert.equal(group().querySelector('.subagentLiveGroupHead'), null, 'one run needs no group heading');
    assert.equal(group().dataset.count, '1');
    assert.deepEqual(finished, [], 'nothing has finished until the result card is drawn');

    const result = {
      type: 'subagent_run', tool_use_id: 'tu_a', agent_type: 'Explore', description: 'Search the repo', status: 'completed',
      started_at: startedA, finished_at: new Date(Date.parse(startedA) + 65000).toISOString(), usage: { total_tokens: 1500 },
      steps_total: 4, steps: nextA.steps, result: '## Found\n\nThree **files** use it.', prompt: 'Find who uses subagents',
      files: [{ path: 'src/pkg/b.py', tools: ['Edit'], count: 2 }],
    };
    workspace.setMessages([{ id: 'msg_result', role: 'assistant', content: '', content_parts: [result] }], {
      history: { can_load_older: false }, runtimeDetails: live(runB), runtimeLive: true,
    });
    await settle();
    const runCard = root.querySelector('.subagentRunCard[data-subagent-tool-use="tu_a"]');
    assert.ok(runCard, 'the finished run shows as its result card');
    assert.equal(text(runCard.querySelector('.residentQuestionTitle')), '子 agent · 探索：Search the repo');
    assert.equal(text(runCard.querySelector('.messageSystemBadge')), '子 agent · 已完成');
    assert.equal(text(runCard.querySelector('.subagentRunMeta')), '用时 1 分 5 秒 · 4 步 · 1.5k tokens');
    assert.equal(text(runCard.querySelector('.subagentRunPreview')), 'Found Three files use it.', 'the folded card shows the opening words as plain text');
    assert.deepEqual([...runCard.querySelectorAll('.subagentRunTools code')].map(text), ['Grep', 'Read']);
    assert.equal(text(runCard.querySelector('.subagentFileChip')), 'b.py×2');
    assert.equal(runCard.querySelector('.subagentFileChip').dataset.portalSourcePath, 'src/pkg/b.py');
    assert.ok(runCard.hasAttribute('data-just-finished'), 'the result card lights up once');
    assert.deepEqual(finished.map(run => [run.tool_use_id, run.status, run.execution_id]), [['tu_a', 'completed', 'exec_tu_a']],
      'the Chat hears which run ended, with the Execution its live card named');
    assert.ok(card('tu_b'), 'the other run keeps running');

    workspace.setRuntime(live({ ...runB, steps_total: 1, steps: [{ kind: 'message', text: 'drafting' }] }), true);
    await settle();
    assert.equal(text(part('tu_b', 'now')), '说drafting');
    assert.equal(finished.length, 1, 'a run is announced once');
  } finally {
    workspace.destroy?.();
  }
}

async function detailDialog() {
  assert.throws(() => w.PortalChatWorkspaceSubagents.createSubagents({}), /PortalChatWorkspaceSubagents is missing: bindLinkPreview/);
  const content = w.PortalChatWorkspaceMessageContent.createMessageContent({
    escapeHtml: w.PortalUIPrimitives.escapeHtml, resourceChips: w.PortalResourceChips,
  });
  const dependencies = Object.fromEntries(w.PortalChatWorkspaceSubagents.dependencies.map(name => [name, content[name]]));
  dependencies.escapeHtml = w.PortalUIPrimitives.escapeHtml;
  const subagents = w.PortalChatWorkspaceSubagents.createSubagents(dependencies);

  const TOTAL = 503;
  const trace = Array.from({ length: TOTAL }, (_, index) => index === TOTAL - 1
    ? { index, kind: 'message', final: true, text: 'The whole answer, longer than what the card kept.' }
    : index === 7
      ? { index, kind: 'tool', tool_name: 'Bash', input: { command: 'make test' }, error: true, error_text: 'exit 2' }
      : { index, kind: 'tool', tool_name: 'Read', input: { file_path: `src/f${index}.py` }, output: 'ok' });
  const requests = [];
  let missing = false;
  w.PortalInstructionClient = {
    execute: async (name, args) => {
      requests.push([name, args.trace_ref, args.offset, args.limit]);
      if (missing) throw Object.assign(new Error('no trace'), { code: 'instruction.not_found' });
      return { run: { total: TOTAL, steps: trace.slice(args.offset, args.offset + args.limit) } };
    },
  };
  const run = {
    tool_use_id: 'tu_a', agent_type: 'explore', description: 'Search', status: 'completed', model: 'model-a',
    prompt: 'Find things', result: 'The whole', result_truncated: true,
    files: [{ path: 'src/pkg/b.py', tools: ['Edit'], count: 2 }], usage: { total_tokens: 1500, tool_uses: 7 },
    started_at: '2026-10-26T01:00:00Z', finished_at: '2026-10-26T01:01:05Z',
  };
  const actions = [];
  const runtime = subagents.openSubagentDetail({
    residentId: 'resident_sample', run, live: false,
    actions: isLive => [{ action: 'save', label: '保存为文件' }, { action: 'stop', label: '停止', danger: true, hidden: !isLive }],
    onAction: (name, context) => actions.push([name, context.steps.length, context.live]),
  });
  await settle();
  const el = name => runtime.overlay.querySelector(`[data-subagent-detail-${name}]`);
  try {
    assert.equal(runtime.overlay.hidden, false, 'the detail dialog opens');
    assert.deepEqual(requests, [['GetSubagentRun', 'tu_a', 0, 500], ['GetSubagentRun', 'tu_a', 500, 500]], 'the trace is read page by page');
    assert.equal(el('steps').children.length, TOTAL, 'every step of the trace shows');
    assert.equal(text(el('title')), '子 agent · 探索：Search');
    assert.deepEqual([...el('meta').children].map(text), ['已完成', '用时 1 分 5 秒', '1.5k tokens', '7 次工具调用', 'model-a']);
    assert.equal(text(el('prompt').querySelector('.subagentDetailPrompt')), 'Find things');
    assert.equal(text(el('files').querySelector('.subagentFileChip')), 'b.py×2');
    assert.equal(el('result').hidden, false);
    assert.equal(el('status').textContent, '');
    const failed = el('steps').querySelector('[data-step-index="7"]');
    assert.ok(failed.hasAttribute('data-step-error') && failed.querySelector('details').open, 'a failed call is marked and open');
    assert.equal(text(failed.querySelector('.subagentStepText')), 'make test', 'a call sums up as its command or path');
    assert.ok(el('steps').querySelector(`[data-step-index="${TOTAL - 1}"]`).hasAttribute('data-step-final'));
    assert.deepEqual([...el('footer').querySelectorAll('button')].map(button => button.dataset.subagentDetailAction), ['save', 'close'],
      'Stop shows only while the run is live');
    el('footer').querySelector('[data-subagent-detail-action="save"]').click();
    assert.deepEqual(actions, [['save', TOTAL, false]], 'a footer action gets the run and its steps');
    el('footer').querySelector('[data-subagent-detail-action="close"]').click();
    await settle();
    assert.equal(runtime.overlay.hidden, true, 'Close closes the dialog');

    missing = true;
    subagents.openSubagentDetail({ residentId: 'resident_sample', run: { tool_use_id: 'tu_new', status: 'running' } });
    await settle();
    assert.equal(el('status').textContent, '这个子 agent 还没有记录的步骤', 'a run with no trace yet says so');
    assert.equal(el('steps').children.length, 0, 'the steps of the previous run are gone');
    runtime.dialog.close();
    missing = false;

    requests.length = 0;
    assert.equal(await subagents.subagentFullResult('resident_sample', run), 'The whole answer, longer than what the card kept.',
      'a cut result is read whole from the end of the trace');
    assert.deepEqual(requests, [['GetSubagentRun', 'tu_a', 0, 1], ['GetSubagentRun', 'tu_a', TOTAL - 40, 500]]);
    assert.equal(await subagents.subagentFullResult('resident_sample', { ...run, result_truncated: false }), 'The whole');

    const markdown = subagents.subagentResultMarkdown(run, 'Answer');
    assert.match(markdown, /^# 子 agent · 探索：Search\n\n- 状态：已完成\n/);
    assert.match(markdown, /\n## 任务\n\nFind things\n\n## 结果\n\nAnswer\n\n## 改动的文件\n\n- src\/pkg\/b\.py\n$/);
    assert.equal(subagents.subagentSlug('Search the Repo!'), 'search-the-repo');
    assert.equal(subagents.subagentSlug('***'), 'subagent');

    w.document.title = 'Chat';
    subagents.badgeTitleForSubagent();
    subagents.badgeTitleForSubagent();
    assert.equal(w.document.title, '✓ 2 个子 agent 已结束 · Chat', 'the page title counts the runs that ended');
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    assert.equal(w.document.title, 'Chat', 'the title comes back once the page is seen');
  } finally {
    runtime.dialog.close();
  }
}

(async () => {
  try {
    await liveAndFinishedCards();
    await detailDialog();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
