'use strict';
// The Family status view: each member's work state, execution account and
// weekly quota, a session move in progress, the member's runtime settings,
// opening and closing work intake, the expanded details, the summary line,
// and the view opened as a popover from the Chat or mounted in a container.
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
const settle = async () => { for (let turn = 0; turn < 12; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);

const now = Math.floor(Date.now() / 1000);
const fresh = windows => ({ fresh: true, valid_until: now + 600, windows });
const accounts = [
  { account_id: 'acct_lead', label: 'Lead account', quota_reserve: fresh([{ window: 'seven_day', remaining_percent: 10 }]) },
  { account_id: 'acct_a', label: 'Account A', quota_reserve: fresh([
    { window: 'seven_day', remaining_percent: 42, resets_at: '2026-11-01T00:00:00Z' }, { window_duration_mins: 10080, remaining_percent: 35 },
    { window: 'five_hour', remaining_percent: 1 }]) },
  { account_id: 'acct_route', provider: 'claude', backend_route_id: 'route_x', label: 'Routed account',
    quota_reserve: { fresh: false, valid_until: now + 600, windows: [{ window: 'seven_day', remaining_percent: 50 }] } },
  { account_id: 'acct_low', label: 'Low account', quota_reserve: fresh([{ window: 'seven_day', remaining_percent: 8 }]) },
  { account_id: 'acct_usd', label: 'Balance account', quota_reserve: { fresh: true, currency: 'USD', remaining_balance: 12.5 } },
  { account_id: 'acct_expired', label: 'Expired account', quota_reserve: { fresh: true, valid_until: now - 5, windows: [{ window: 'seven_day', remaining_percent: 70 }] } },
];
const member = (resident_id, title, accountId, extra = {}) => ({ resident_id, title, role: 'member', accountId, ...extra });
const nodes = [
  member('res_lead', 'Lead', 'acct_lead', { role: 'steward' }),
  member('res_run', 'Runner', 'acct_a'),
  member('res_queue', 'Queued', ''),
  member('res_stop', 'Stopping', 'acct_low'),
  member('res_idle', 'Idle', 'acct_usd'),
  member('res_fail', 'Failed', 'acct_expired'),
  member('res_move', 'Moving', 'acct_a'),
  member('res_wait', 'Waiting', 'acct_a'),
];
const state = extra => ({ execution_gate: { state: 'open' }, ...extra });
const snapshots = {
  res_lead: { state: state({ work_state: 'idle' }), messages: [] },
  res_run: {
    state: state({ work_state: 'running', queued_count: 2, active_message_id: 'msg_run' }),
    runtime_settings: { model: 'model-x', reasoning_effort: 'high' },
    messages: [{ message_id: 'msg_run', role: 'user', content_parts: [{ type: 'text', text: 'Write the tests' }], activated_at: '2026-10-26T01:00:00Z' }],
  },
  res_queue: { state: state({ work_state: 'queued', queued_count: 1, active_message_id: 'msg_far' }), messages: [] },
  res_stop: { state: state({ work_state: 'stopping', execution_gate: { state: 'closed', reason: 'quota_used_up' } }), messages: [] },
  res_idle: { state: state({ work_state: 'idle', intake_state: 'closed' }), messages: [] },
  res_fail: { state: state({ work_state: 'idle' }), input_states: [{ state: 'failed', error: { message: 'model refused' } }], messages: [] },
  res_move: { state: state({ work_state: 'idle' }), messages: [] },
  res_wait: { state: state({ work_state: 'idle' }), messages: [] },
};

const calls = [];
const runtimes = new Map();
let quotaFailure = null;
w.PortalAPI = { get: async url => { calls.push(['get', url]); if (quotaFailure) throw quotaFailure; return { accounts }; } };
let family = { title: 'Build Family', lineage_id: 'lineage_sample', nodes };
w.PortalResidentService = {
  projection: async ({ residentId }) => { calls.push(['projection', residentId]); return { family_facade: family }; },
  setIntake: async payload => { calls.push(['setIntake', { ...payload }]); },
};
w.PortalResidentRuntimeController = {
  bind(residentId, handlers) {
    const node = nodes.find(row => row.resident_id === residentId);
    const runtime = {
      handlers, opened: [], closed: 0, destroyed: 0,
      canEdit: residentId !== 'res_idle',
      state: () => ({ canEdit: runtime.canEdit, canRestart: false }),
      load: async () => ({
        detail: {
          resident: { backend: 'codex', description: `${node.title} duties` },
          execution_account: node.accountId ? { account_id: node.accountId } : {},
          ...(residentId === 'res_queue' ? { provider_session: { provider: 'claude', account_id: 'route_x' } } : {}),
          allows: { OpenIntake: true },
        },
        settings: residentId === 'res_run' ? { model: 'model-x', reasoning_effort: 'high' }
          : residentId === 'res_stop' ? { model: 'detail-model', reasoning_effort: 'medium' } : {},
      }),
      openSettings: async focus => { runtime.opened.push(focus()); },
      close: () => { runtime.closed += 1; },
      destroy: () => { runtime.destroyed += 1; },
    };
    runtimes.set(residentId, runtime);
    return runtime;
  },
};
const released = [];
w.PortalResidentProtocol = {
  ...w.PortalResidentProtocol,
  acquire: (residentId, _listener, options) => {
    calls.push(['acquire', residentId, options.consumer]);
    return { load: async () => JSON.parse(JSON.stringify(snapshots[residentId])), destroy: () => released.push(residentId) };
  },
};
let chatPreference = {muted_member_ids: [], solo_member_id: ''};
w.PortalInstructionClient = {
  execute: async (instruction, args) => {
    calls.push([instruction, { ...args }]);
    if (instruction === 'GetFamilyChatPreferences') return {...chatPreference};
    if (instruction === 'UpdateFamilyChatPreferences') {
      if (args.control === 'mute') chatPreference.muted_member_ids = args.enabled ? [args.member_id] : [];
      else chatPreference.solo_member_id = args.enabled ? args.member_id : '';
      return {...chatPreference};
    }

    return { message: { message_id: args.message_id, role: 'user', content_parts: [{ type: 'text', text: 'Fetched task' }] } };
  },
};

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
const familyStatus = w.PortalChatWorkspaceFamilyStatus.createFamilyStatus({
  escapeHtml, footerTime: value => (value ? `at ${value}` : ''),
  sessionRestartActive: w.PortalChatWorkspaceRuntimeSettings.sessionRestartActive,
});
const row = (root, id) => root.querySelector(`[data-family-status-row="${id}"]`);
const cells = (root, id) => [...row(root, id).children];
const work = (root, id) => text(cells(root, id)[1].querySelector('.status-badge'));

async function membersQuotaAndSummary() {
  const summaries = [];
  const anchor = w.document.createElement('button');
  w.document.body.append(anchor);
  const view = familyStatus.createFamilyStatusView('res_facade', anchor, line => summaries.push(line));
  const root = () => w.document.querySelector('.familyStatusPanel');
  try {
    view.open();
    assert.equal(view.isOpen(), true);
    assert.equal(root().getAttribute('role'), 'dialog', 'opened from the Chat it is a popover');
    await settle();
    assert.equal(root().getAttribute('aria-label'), 'Build Family · 成员状态');
    assert.deepEqual(calls.filter(call => call[0] === 'get'), [['get', '/api/agent-status']], 'the quota comes from the Home summary');
    assert.deepEqual(calls.filter(call => call[0] === 'acquire').map(call => call[2]), nodes.map(() => 'family_member'));
    assert.deepEqual(calls.filter(call => call[0] === 'GetMessage'), [['GetMessage', { resident_id: 'res_queue', message_id: 'msg_far' }]],
      'an active message missing from the snapshot is read');

    runtimes.get('res_move').handlers.onState({ restart: { status: 'accepted', phase: 'creating_session', restart_id: 'r1' } });
    runtimes.get('res_wait').handlers.onState({ restart: { status: 'accepted', phase: 'waiting_current_execution', restart_id: 'r2' } });
    assert.deepEqual(nodes.map(node => work(root(), node.resident_id)),
      ['空闲', '工作中', '等待执行', '停止中', '空闲', '失败', '迁移中', '等待本轮后迁移']);
    assert.deepEqual(nodes.map(node => cells(root(), node.resident_id)[1].querySelector('.status-badge').className),
      ['status-badge idle', 'status-badge running', 'status-badge queued', 'status-badge running', 'status-badge idle',
        'status-badge failed', 'status-badge queued', 'status-badge queued']);
    assert.equal(text(cells(root(), 'res_run')[1].querySelector('.familyStatusQueue')), '· 排队 2');
    const gate = id => cells(root(), id)[1].querySelector('[data-execution-gate]');
    assert.equal(gate('res_run'), null, 'an open gate shows nothing');
    assert.equal(text(gate('res_stop')), 'CLOSED');
    assert.equal(gate('res_stop').getAttribute('title'), 'CLOSED · 账号额度已用尽');
    assert.equal(gate('res_idle').getAttribute('title'), 'CLOSED · 人工接单已关闭');
    assert.equal(summaries.at(-1), '运行 2 · 排队 3 · 需关注 5', 'the summary counts work, queue and members that need attention');

    const quota = id => cells(root(), id)[3];
    assert.equal(text(quota('res_run').querySelector('.familyStatusQuotaValue')), '35%', 'the lowest weekly window counts');
    assert.equal(quota('res_run').getAttribute('title'), '周额度重置：at 2026-11-01T00:00:00Z');
    assert.equal(quota('res_run').querySelector('.familyStatusQuotaTrack span').getAttribute('style'), 'width:35%');
    assert.equal(text(quota('res_queue')), '未知', 'a stale reading shows no number');
    assert.equal(quota('res_queue').getAttribute('title'), '观测过期，待刷新');
    assert.equal(text(cells(root(), 'res_queue')[2].querySelector('span span')), 'claude · Routed account', 'a routed member finds its account by route');
    assert.equal(text(quota('res_fail')), '未知', 'an expired reading shows no number');
    assert.equal(quota('res_fail').getAttribute('title'), '无有效周额度观测');
    assert.equal(text(quota('res_idle')), '12.5 USD', 'a balance account shows its balance');
    assert.equal(quota('res_stop').className, 'familyStatusAttention');
    assert.equal(quota('res_stop').getAttribute('title'), '低于 10% · 新请求须 steward 评估');
    assert.equal(text(quota('res_stop')), '8%!');
    assert.equal(quota('res_lead').getAttribute('title'), '额度不足 · 平台负责迁移', 'a steward is low at 10%');
    assert.equal(quota('res_run').className, '');

    const mix = (id, mode) => row(root(), id).querySelector(`[data-family-mix="${mode}"]`);
    assert.equal(mix('res_lead', 'mute'), null, 'roles have no personal routing controls');
    assert.ok(mix('res_run', 'mute'), 'members offer M');
    mix('res_run', 'mute').click(); await settle();
    assert.equal(mix('res_run', 'mute').getAttribute('aria-pressed'), 'true');
    mix('res_run', 'solo').click(); await settle();
    assert.equal(mix('res_run', 'solo').getAttribute('aria-pressed'), 'true');
    assert.equal(mix('res_run', 'mute').getAttribute('aria-pressed'), 'false');
    mix('res_run', 'solo').click(); await settle();
    assert.equal(mix('res_run', 'mute').getAttribute('aria-pressed'), 'true', 'exit Solo restores M');
    const intake = id => cells(root(), id)[4].querySelector('button');
    assert.equal(text(intake('res_run')), '开');
    assert.equal(intake('res_run').getAttribute('aria-checked'), 'true');
    assert.equal(text(intake('res_idle')), '关');
    assert.equal(text(intake('res_move')), '迁移暂关', 'a member being moved takes no work');
    assert.equal(intake('res_move').disabled, true);
    const settings = id => cells(root(), id)[2].querySelector('[data-family-status-settings]');
    assert.equal(settings('res_move').disabled, true, 'nor settings while it moves');
    assert.equal(settings('res_idle').disabled, true, 'a member whose runtime cannot be changed has no settings');
    assert.match(text(settings('res_run').querySelector('.familyStatusRuntime')), /model-x/, 'the member model is visible without expanding details');
    assert.match(text(settings('res_run').querySelector('.familyStatusRuntime')), /high/);
    assert.match(text(settings('res_stop').querySelector('.familyStatusRuntime')), /detail-model/, 'a lightweight snapshot without settings retains the loaded runtime model');
    assert.match(text(settings('res_lead').querySelector('.familyStatusRuntime')), /Provider default/, 'unresolved provider defaults must be explicit');
    root().querySelector('.familyStatusBody').scrollLeft = 80;
    runtimes.get('res_lead').handlers.onState({
      detail: { resident: { backend: 'codex' } }, settings: {},
      catalog: { default_model: 'catalog-model', runtime_capabilities: { settings: ['model', 'service_tier'] },
        service_tiers_by_model: { 'catalog-model': ['priority'] } },
    });
    assert.match(text(settings('res_lead').querySelector('.familyStatusRuntime')), /catalog-model/, 'the catalog resolves an inherited default model');
    assert.equal(root().querySelector('.familyStatusBody').scrollLeft, 80, 'a live member refresh preserves the horizontal reading position');
    assert.match(text(settings('res_lead').querySelector('.familyStatusRuntime')), /Fast 关/);
    runtimes.get('res_lead').handlers.onState({
      detail: { resident: { backend: 'claude' } }, settings: { model: 'claude-model', reasoning_effort: 'high' }, catalog: {},
    });
    assert.match(text(settings('res_lead').querySelector('.familyStatusRuntime')), /claude-model/, 'a changed backend/model replaces the prior catalog model');
    assert.doesNotMatch(text(settings('res_lead').querySelector('.familyStatusRuntime')), /catalog-model/);
    settings('res_run').click();
    await settle();
    assert.deepEqual(runtimes.get('res_run').opened, [settings('res_run')], 'settings open for the member with its button to return to');

    cells(root(), 'res_run')[1].querySelector('button').click();
    const details = row(root(), 'res_run').nextElementSibling.querySelector('dl');
    const pairs = Object.fromEntries([...details.querySelectorAll('dt')].map(dt => [text(dt), text(dt.nextElementSibling)]));
    assert.deepEqual({ ...pairs }, {
      职责: 'Runner duties', 模型: 'model-x · high', 当前任务: 'Write the tests', 开始处理: 'at 2026-10-26T01:00:00Z',
      最近活动: '—', 周额度重置: 'at 2026-11-01T00:00:00Z',
    }, 'the details show the member’s work');
    assert.equal(cells(root(), 'res_run')[1].querySelector('button').getAttribute('aria-expanded'), 'true');
    cells(root(), 'res_fail')[1].querySelector('button').click();
    const failure = row(root(), 'res_fail').nextElementSibling;
    assert.match(text(failure), /错误model refused/);
    assert.match(text(row(root(), 'res_queue')), /等待执行/);
    cells(root(), 'res_queue')[1].querySelector('button').click();
    assert.match(text(row(root(), 'res_queue').nextElementSibling), /当前任务Fetched task/, 'the fetched active message is the task');
    cells(root(), 'res_run')[1].querySelector('button').click();
    assert.equal(row(root(), 'res_run').nextElementSibling.dataset.familyStatusRow, 'res_queue', 'a second click folds the details');

    const projections = calls.filter(call => call[0] === 'projection').length;
    intake('res_idle').click();
    await settle();
    assert.deepEqual(calls.find(call => call[0] === 'setIntake'), ['setIntake', { resident_id: 'res_idle', open: true }],
      'a closed intake is opened');
    assert.equal(calls.filter(call => call[0] === 'projection').length, projections + 1, 'and the Family is read again');

    view.close();
    assert.equal(view.isOpen(), false);
    assert.ok([...runtimes.values()].every(runtime => runtime.closed >= 1), 'closing closes each member’s runtime dialogs');
  } finally {
    view.destroy();
  }
  assert.equal(w.document.querySelector('.familyStatusPanel'), null, 'destroy removes the view');
  assert.deepEqual([...released].sort(), nodes.map(node => node.resident_id).sort(), 'and releases every member subscription');
  assert.ok([...runtimes.values()].every(runtime => runtime.destroyed === 1));
  anchor.remove();
}

async function openedAndMounted() {
  const chat = w.PortalChatWorkspace;
  released.length = 0;
  family = { title: 'Small Family', lineage_id: 'lineage_sample', nodes: [nodes[1]] };
  const anchor = w.document.createElement('button');
  w.document.body.append(anchor);
  const popover = await chat.openFamilyStatus('res_facade', { anchor });
  try {
    assert.equal(popover.isOpen(), true, 'the Chat opens the view at once');
    await settle();
    const root = w.document.querySelector('.familyStatusPanel');
    assert.equal(root.parentElement, w.document.body);
    assert.deepEqual([...root.querySelectorAll('[data-family-status-row]')].map(node => node.dataset.familyStatusRow), ['res_run']);
    assert.equal(text(row(root, 'res_run').querySelector('.familyStatusName')), 'Runner');
    assert.equal(row(root, 'res_run').querySelector('.familyStatusName').getAttribute('href'),
      '/lineage?resident=res_run&lineage=lineage_sample&open_chat=1');
    assert.equal(text(root.querySelector('.familyStatusIntakeNote')), '关闭接单只暂停后续任务，当前工作继续');
  } finally {
    popover.destroy();
  }
  assert.equal(w.document.querySelector('.familyStatusPanel'), null);
  assert.deepEqual([...new Set(released)], ['res_run'], 'destroying an open popover releases its member');
  released.length = 0;

  const container = w.document.createElement('div');
  w.document.body.append(container);
  quotaFailure = new Error('quota offline');
  const inline = chat.mountFamilyStatus('res_facade', container);
  try {
    const root = container.querySelector('.familyStatusPanel');
    assert.ok(root, 'mounting puts the view in the container');
    assert.equal(root.getAttribute('role'), 'region');
    assert.ok(root.classList.contains('is-inline'));
    assert.equal(root.hidden, false);
    assert.equal(inline.isOpen(), true);
    assert.equal(text(root), '正在加载…', 'the view says it is loading before the Family arrives');
    await settle();
    assert.equal(text(root.querySelector('.paneNote')), '额度暂不可用：quota offline');
    const before = calls.filter(call => call[0] === 'projection').length;
    inline.refresh();
    inline.refresh();
    inline.refresh();
    await settle();
    assert.equal(calls.filter(call => call[0] === 'projection').length, before + 2,
      'refreshes that arrive during a load are folded into one more load');
    inline.close();
    assert.equal(root.hidden, true, 'closing hides a mounted view');
    assert.equal(inline.isOpen(), false);
    inline.refresh();
    await settle();
    assert.equal(calls.filter(call => call[0] === 'projection').length, before + 2, 'a closed view is not refreshed');
  } finally {
    inline.destroy();
    quotaFailure = null;
  }
  assert.equal(container.childElementCount, 0);
  assert.deepEqual(released, ['res_run'], 'a mounted view releases its member once destroyed');

  family = { title: 'Empty Family', nodes: [], members: [] };
  const empty = chat.mountFamilyStatus('res_facade', container);
  try {
    await settle();
    assert.equal(text(container), '暂无成员状态');
  } finally {
    empty.destroy();
    container.remove();
    anchor.remove();
  }
}

(async () => {
  try {
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    await membersQuotaAndSummary();
    await openedAndMounted();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
