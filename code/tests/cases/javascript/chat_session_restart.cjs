'use strict';
// The Session restart dialog: the snapshot of the current binding, the target
// Provider, account and context choices, one request per submit with its
// idempotency key, tracking an accepted migration until it ends, cancelling
// it, retrying a request whose receipt is unconfirmed, the target model, and
// the compact progress line above the Chat with its acknowledgement.
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
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js',
  'portal_model_api_settings.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const ago = seconds => new Date(Date.now() - seconds * 1000).toISOString();
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);
const calls = [];
const listeners = [];
w.PortalResidentService = {
  runtimeAccounts: async () => ({ accounts: [] }),
  runtimeSettings: async () => ({ settings: {} }),
  runtimeCatalog: async query => {
    calls.push(['runtimeCatalog', { ...query }]);
    return {
      models: ['model-a', 'model-b'], default_model: 'model-a',
      efforts_by_model: { 'model-a': ['low', 'medium'], 'model-b': ['low', 'high'] }, default_efforts_by_model: { 'model-a': 'low' },
    };
  },
  observeSessionRestart(residentId, listener) {
    calls.push(['observe', residentId]);
    listeners.push(listener);
    return () => calls.push(['unobserve', residentId]);
  },
  retrySessionRestart: async residentId => {
    calls.push(['retry', residentId]);
    return { status: 'accepted', restart_id: 'r3' };
  },
};
w.PortalResidentProjection = { load: async () => ({}) };
const notify = view => listeners.at(-1)(view);

const providers = [
  { id: 'codex', label: 'Codex', operations: ['fork', 'compact'] },
  { id: 'claude', label: 'Claude', operations: ['compact'] },
];
const strategies = [
  { id: 'smart', label: '智能' }, { id: 'native', label: '原生 Fork' }, { id: 'fresh', label: '全新上下文' },
  { id: 'visible', label: '可见对话' }, { id: 'compact', label: '精简交接' },
];
const accounts = { codex: [{ id: 'route_a', label: 'Route A', account_id: 'acct_a' }, { id: 'route_b', label: 'Route B', account_id: 'acct_b' }] };
const withSession = (extra = {}) => ({
  resident_id: 'resident_sample', providers, strategies, accounts, runtime_settings: {},
  source: {
    provider: 'codex', account_id: 'route_a', has_session: true, binding_status: 'current',
    backend_session_id: 'sess_1', provider_session_ref: 'ref_1', generation: 3,
  },
  ...extra,
});

function dialogParts() {
  const overlay = w.document.querySelector('.portalSessionRestartOverlay');
  const find = selector => overlay.querySelector(selector);
  const field = name => find(`[data-session-restart="${name}"]`);
  return {
    overlay,
    form: find('[data-session-restart-form]'),
    submit: find('[data-session-restart-submit]'),
    cancel: find('[data-session-restart-cancel]'),
    source: find('[data-session-restart-source]'),
    hint: find('[data-session-restart-hint]'),
    binding: find('[data-session-binding-fields]'),
    accountField: find('[data-session-restart-account-field]'),
    modelField: find('[data-session-restart-model-field]'),
    effortField: find('[data-session-restart-effort-field]'),
    progress: find('.portalSessionRestartProgress'),
    provider: field('provider'), account: field('account'), model: field('model'), effort: field('effort'),
    strategy: field('strategy'), activeExecution: field('active-execution'), resumeFailed: field('resume-failed'),
  };
}
const choose = (select, value) => {
  select.value = value;
  select.dispatchEvent(new w.Event('change', { bubbles: true }));
};
const submitForm = form => form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));

async function submitTrackAndCancel() {
  const closes = [];
  const dialog = w.PortalChatWorkspace.createSessionRestartDialog({ id: 'restartSample', onClose: context => closes.push(context) });
  const ui = dialogParts();
  const requests = [];
  const pending = deferred();
  const cancels = [];
  const cancelPending = deferred();
  const reloads = [];
  try {
    dialog.open(withSession(), values => { requests.push(values); return pending.promise; },
      () => reloads.push('reload'), values => { cancels.push({ ...values }); return cancelPending.promise; });
    await settle();
    assert.equal(ui.overlay.hidden, false, 'the dialog opens');
    assert.equal(text(ui.source), '当前：Codex · route_a。切换成功前会继续保留当前 session。');
    assert.deepEqual([...ui.binding.children].map(text), ['Provider', 'Codex', '账号', 'route_a', '绑定状态', '当前绑定',
      'Session ID', 'sess_1', 'Provider Session 引用', 'ref_1', '状态版本（Generation）', '3'],
    'the snapshot shows the binding the dialog opened on');
    assert.equal(ui.provider.value, 'codex');
    assert.deepEqual([...ui.account.options].map(option => option.value), ['route_a', 'route_b']);
    assert.equal(ui.account.value, 'route_a', 'the current account is selected');
    assert.equal(ui.accountField.hidden, false);
    assert.equal(text(ui.submit), '重启 Session');
    assert.equal(ui.submit.disabled, false);
    assert.equal(ui.cancel.hidden, true, 'nothing to cancel before a request');
    assert.equal(text(ui.hint), '系统优先使用同 Provider 原生 Fork，否则使用精简交接。');
    assert.deepEqual(calls.filter(call => call[0] !== 'runtimeCatalog'), [['observe', 'resident_sample']]);
    assert.equal(calls.some(call => call[0] === 'runtimeCatalog'), false, 'no target model is read unless the config asks');

    choose(ui.strategy, 'native');
    assert.equal(text(ui.hint), '原生 Fork 仅适用于同一 Provider/Account；旧 session 会保留为只读 generation。');
    choose(ui.provider, 'claude');
    assert.equal(ui.strategy.value, 'smart', 'a Provider that cannot fork drops the native strategy');
    assert.equal([...ui.strategy.options].find(option => option.value === 'native').disabled, true);
    assert.equal(ui.accountField.hidden, true, 'a Provider without routes hides the account');
    assert.deepEqual([...ui.account.options].map(option => [option.value, text(option)]), [['', '自动选择']]);
    choose(ui.provider, 'codex');
    assert.equal([...ui.strategy.options].find(option => option.value === 'native').disabled, false);
    choose(ui.account, 'route_b');
    choose(ui.strategy, 'fresh');
    assert.equal(text(ui.hint), '只初始化新的系统上下文，不带入旧对话；旧 session 会保留。');
    ui.activeExecution.value = 'cancel';
    ui.resumeFailed.checked = false;

    submitForm(ui.form);
    assert.equal(requests.length, 1);
    const [values] = requests;
    assert.match(values.idempotency_key, /^portal-session-restart:[0-9a-f-]{36}$/);
    assert.deepEqual({ ...values, idempotency_key: '' }, {
      target_provider: 'codex', target_account: 'route_b', context_strategy: 'fresh',
      active_execution_policy: 'cancel', resume_failed_input: false, idempotency_key: '',
    }, 'the request carries the chosen target');
    assert.equal(ui.submit.disabled, true, 'a request in flight locks the submit button');
    assert.equal(text(ui.submit), '正在切换…');
    assert.equal(ui.provider.disabled, true, 'and the target');
    assert.equal(text(ui.source), '正在提交迁移请求，请等待后台受理回执；请勿重复操作。');
    submitForm(ui.form);
    assert.equal(requests.length, 1, 'a second submit while the first is in flight sends nothing');

    pending.resolve({ status: 'accepted', restart_id: 'r1' });
    await settle();
    assert.equal(text(ui.submit), '查看上方迁移状态', 'an accepted request is tracked');
    assert.equal(ui.submit.disabled, true);
    assert.equal(ui.cancel.hidden, false, 'a tracked migration can be cancelled');
    assert.equal(ui.overlay.hidden, false, 'an accepted request keeps the dialog open');

    const accepted = {
      status: 'accepted', phase: 'waiting_current_execution', restart_id: 'r1', submitted_at: ago(75),
      source: { provider: 'codex', account_id: 'route_a' }, target: { provider: 'codex', account_id: 'route_b' },
    };
    notify({ restart: accepted });
    assert.equal(text(ui.source), '正在跟踪已提交的迁移；目标已锁定。关闭窗口不会取消，可使用“取消迁移”终止。');
    assert.equal(ui.progress.hidden, false);
    assert.equal(ui.progress.dataset.restartStatus, 'accepted');
    const progressLines = [...ui.progress.children].map(text);
    assert.equal(progressLines[0], 'Session：route_a → route_b · 已受理，等待本轮完成后迁移');
    assert.match(progressLines[1], /^已耗时 1 分 1[5-9] 秒$/, 'the time counts from the submit');
    assert.equal(progressLines[2], '关闭窗口不会取消迁移；需要终止时请使用“取消迁移”。');
    assert.equal(ui.progress.querySelector('.portalSessionRestartDiagnostics').hidden, true, 'no error, no diagnostics');
    assert.equal(ui.account.value, 'route_b', 'the target stays locked to the request');

    ui.cancel.click();
    assert.deepEqual(cancels, [{ restart_id: 'r1' }], 'cancel names the tracked restart');
    ui.cancel.click();
    assert.equal(cancels.length, 1, 'cancel in flight is not sent twice');
    cancelPending.resolve({ ...accepted, status: 'cancelling' });
    await settle();
    assert.deepEqual(toasts.at(-1), ['Session 迁移取消请求已受理，原 Session 将保留。', 'success']);
    assert.equal(ui.cancel.disabled, true, 'a cancelling migration cannot be cancelled again');
    assert.equal(ui.cancel.hidden, false);

    notify({ restart: { ...accepted, status: 'cancelling' } });
    assert.equal(text(ui.source), '正在取消已提交的迁移；原 Session 将继续保留。');
    assert.equal([...ui.progress.children].map(text)[2], '取消请求已受理，原 Session 将继续保留。');
    notify({ restart: { ...accepted, status: 'cancelled', restarted_at: ago(1) } });
    assert.equal(ui.overlay.hidden, true, 'the dialog closes once the tracked migration ends');
    assert.deepEqual(closes.map(context => ({ ...context })), [{ accepted: true }]);
    assert.deepEqual(calls.at(-1), ['unobserve', 'resident_sample'], 'closing stops observing');
    assert.equal(ui.progress.hidden, true, 'a cancelled migration hides the progress');
    assert.deepEqual(reloads, []);
  } finally {
    dialog.destroy();
  }
  assert.equal(w.document.querySelector('.portalSessionRestartOverlay'), null, 'destroy removes the dialog');
}

async function failuresRetryAndReload() {
  const dialog = w.PortalChatWorkspace.createSessionRestartDialog({ id: 'restartRetry' });
  const ui = dialogParts();
  const requests = [];
  const reloads = [];
  try {
    const failures = [Object.assign(new Error('receipt lost'), { restartPending: true }), new Error('target refused'), new Error('still refused')];
    dialog.open(withSession(), async values => {
      requests.push(values);
      throw failures.shift();
    }, async () => reloads.push('reload'));
    await settle();
    submitForm(ui.form);
    await settle();
    assert.equal(toasts.some(([message]) => message.includes('receipt lost')), false, 'a pending receipt is not reported as a failure');
    assert.equal(text(ui.submit), '重启 Session', 'the dialog can be submitted again');
    submitForm(ui.form);
    await settle();
    assert.equal(requests[1].idempotency_key, requests[0].idempotency_key, 'a pending receipt is resubmitted under the same key');
    assert.match(toasts.at(-1)[0], /target refused/);
    assert.equal(toasts.at(-1)[1], 'error');
    assert.equal(requests.length, 2);
    assert.equal(ui.submit.disabled, false);
    submitForm(ui.form);
    await settle();
    assert.notEqual(requests[2].idempotency_key, requests[1].idempotency_key, 'a refused request starts a new key');

    // An unconfirmed receipt is retried as the same request through the service.
    notify({ restart: { status: 'confirming', restart_id: 'r3', target: { provider: 'codex', account_id: 'route_a' } } });
    assert.equal(text(ui.submit), '重试同一请求');
    assert.equal(ui.submit.disabled, false);
    submitForm(ui.form);
    await settle();
    assert.deepEqual(calls.at(-1), ['retry', 'resident_sample']);
    assert.equal(requests.length, 3, 'the retry does not build a new request');

    w.document.querySelector('.portalSessionRestartOverlay [data-session-reload]').click();
    await settle();
    assert.equal(ui.overlay.hidden, true, 'reloading closes the dialog');
    assert.deepEqual(reloads, ['reload']);

    // A failed migration opens on its own target and offers to migrate again.
    const failed = {
      status: 'failed', restart_id: 'r2', target: { provider: 'claude' }, requested_strategy: 'compact',
      resume_failed_input: false, previous_session_retained: true, error: 'provider said no',
      submitted_at: ago(180), restarted_at: ago(60),
    };
    dialog.open(withSession({ restart: failed }), async () => ({ status: 'accepted' }));
    await settle();
    assert.equal(ui.provider.value, 'claude');
    assert.equal(ui.strategy.value, 'compact');
    assert.equal(ui.resumeFailed.checked, false);
    assert.equal(text(ui.submit), '重新迁移');
    assert.equal(text(ui.hint), '迁移最近的重要对话与当前工作，适合跨 Provider，开销较小。');
    notify({ restart: failed });
    const lines = [...ui.progress.children].map(text);
    assert.equal(lines[0], 'Session：当前 Session → claude · 迁移失败');
    assert.equal(lines[1], '耗时 2 分');
    assert.equal(lines[2], '迁移未完成，原会话已保留。请核对目标设置后重新迁移。');
    assert.equal(ui.progress.querySelector('.portalSessionRestartDiagnostics').hidden, false);
    assert.equal(text(ui.progress.querySelector('pre')), 'provider said no');
  } finally {
    dialog.destroy();
  }
}

async function noSessionAndTargetModel() {
  const dialog = w.PortalChatWorkspace.createSessionRestartDialog({ id: 'restartConfig' });
  const ui = dialogParts();
  const requests = [];
  try {
    dialog.open(withSession({ source: { provider: 'claude', account_id: '', has_session: false } }), async values => {
      requests.push(values);
      return { status: 'completed', configuration_only: true };
    });
    await settle();
    assert.equal(text(ui.source), '尚未创建 Provider session。当前配置：Claude。可以直接修改，不会启动模型。');
    assert.equal(text(ui.submit), '保存 Provider');
    assert.equal(ui.strategy.disabled, true, 'without a session there is no context to carry');
    assert.equal(text(ui.hint), '仅保存 Provider / 账号选择，已有对话和 Fork 上下文保持不变；下次运行时使用新配置。');
    choose(ui.provider, 'codex');
    assert.equal(text(ui.submit), '保存账号设置');
    submitForm(ui.form);
    assert.equal(text(ui.source), '正在保存 Provider / 账号选择，不创建 Session。');
    await settle();
    assert.equal(ui.overlay.hidden, true, 'a completed save closes the dialog');
    assert.equal(requests[0].target_account, 'route_a');

    const catalog = deferred();
    const service = w.PortalResidentService;
    const loaded = service.runtimeCatalog;
    service.runtimeCatalog = async query => { await catalog.promise; return loaded(query); };
    dialog.open(withSession({ choose_runtime_settings: true, runtime_settings: { model: 'model-b', reasoning_effort: 'high' } }),
      async values => { requests.push(values); return { status: 'accepted' }; });
    await settle();
    assert.equal(ui.submit.disabled, true, 'the target model is read before submitting');
    assert.equal(ui.model.disabled, true);
    catalog.resolve();
    await settle();
    service.runtimeCatalog = loaded;
    assert.deepEqual(calls.filter(call => call[0] === 'runtimeCatalog').at(-1), ['runtimeCatalog', { backend: 'codex', accountId: 'acct_a' }],
      'the catalog is read for the route account');
    assert.equal(ui.modelField.hidden, false);
    assert.equal(ui.model.value, 'model-b', 'the saved model is selected');
    assert.equal(ui.effortField.hidden, false);
    assert.deepEqual([...ui.effort.options].map(option => option.value), ['low', 'high']);
    assert.equal(ui.effort.value, 'high');
    choose(ui.model, 'model-a');
    assert.equal(ui.effort.value, 'low', 'another model takes its own default effort');
    choose(ui.model, 'model-b');
    choose(ui.effort, 'high');
    submitForm(ui.form);
    await settle();
    assert.equal(requests.at(-1).model, 'model-b');
    assert.equal(requests.at(-1).reasoning_effort, 'high');
  } finally {
    dialog.destroy();
  }
}

async function compactProgress() {
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
  const runtime = w.PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({ escapeHtml });
  assert.deepEqual(['accepted', 'submitting', 'confirming', 'cancelling', 'queued', 'failed', 'completed']
    .map(status => runtime.sessionRestartActive({ status })), [true, true, true, true, false, false, false]);
  const opened = [];
  const dismissed = [];
  const first = runtime.createSessionRestartProgress({ compact: true, owner: () => 'sample_admin', onOpen: () => opened.push('first'), onDismiss: () => dismissed.push('first') });
  const second = runtime.createSessionRestartProgress({ compact: true, owner: 'sample_admin' });
  w.document.body.append(first.element, second.element);
  const head = progress => text(progress.element.querySelector('[role="status"]'));
  const button = (progress, name) => progress.element.querySelector(`[data-session-restart-${name}]`);
  const config = { resident_id: 'resident_sample' };
  try {
    assert.equal(first.element.hidden, true, 'nothing to show before a restart');
    first.update({ config, restart: { status: 'accepted', restart_id: 'r8', target: { account_id: 'route_b' } } });
    assert.equal(first.element.hidden, false);
    assert.equal(head(first), 'Session：当前 Session → route_b · 已受理，正在准备');
    assert.equal(first.element.children.length, 3, 'the compact line has the heading and two buttons');
    assert.equal(text(button(first, 'progress-open')), '查看迁移进度');
    assert.equal(button(first, 'dismiss').hidden, true, 'a running migration cannot be dismissed');
    button(first, 'progress-open').click();
    assert.deepEqual(opened, ['first']);

    const failure = { config, restart: { status: 'failed', restart_id: 'r9', target: { account_id: 'route_b' } } };
    first.update(failure);
    second.update(failure);
    assert.equal(head(first), 'Session：当前 Session → route_b · 迁移失败');
    assert.equal(text(button(first, 'progress-open')), '查看详情');
    assert.equal(button(first, 'dismiss').hidden, false);
    button(first, 'dismiss').click();
    assert.equal(w.PortalState.storage.readText('portal.sessionRestartAcknowledged.v1:sample_admin:resident_sample', ''), 'r9',
      'the acknowledgement is kept per reader and Resident');
    assert.equal(first.element.hidden, true, 'an acknowledged failure is hidden');
    assert.equal(second.element.hidden, true, 'in every Chat of the page');
    assert.deepEqual(dismissed, ['first']);
    first.update({ config, restart: { ...failure.restart, restart_id: 'r10' } });
    assert.equal(first.element.hidden, false, 'a new failure shows again');
    first.update({ config, restart: { status: 'completed', restart_id: 'r10' } });
    assert.equal(first.element.hidden, true, 'a completed migration hides the line');
    first.update({});
    assert.equal(first.element.hidden, true);
  } finally {
    first.destroy();
    second.destroy();
  }
  assert.equal(first.element.isConnected, false, 'destroy removes the line');
}

(async () => {
  try {
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    await submitTrackAndCancel();
    await failuresRetryAndReload();
    await noSessionAndTargetModel();
    await compactProgress();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
