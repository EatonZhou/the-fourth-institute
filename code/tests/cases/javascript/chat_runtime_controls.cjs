'use strict';
// The runtime controls of a Resident's Chat. The composer's runtime button
// shows the Resident's settings and opens its runtime dialog; it waits while
// the Resident is read, a setting is saved or a restart runs, stays closed for
// a Resident the reader may only look at, and keeps the saved settings when
// the composer is drawn again. The header button opens the Session restart
// when the Chat offers one and refreshes the Chat otherwise; the header shows a
// restart in progress, and the Chat refreshes once when a restart ends or a
// setting is saved. While the host changes the Resident the controls wait,
// another Resident's Chat follows that Resident even when its composer looks
// the same, a late answer about the previous one changes nothing, and
// destroying the Chat lets the Resident go. The real
// PortalResidentRuntimeController runs on a recording Resident service and
// dialogs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section><section id="plain"></section>', {
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
w.PortalIdentity = { username: () => 'viewer' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js',
  'portal_resident_runtime_controller.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);
const take = list => list.splice(0);

const A = 'resident_aaaaaaaaaaaa';
const B = 'resident_bbbbbbbbbbbb';
const C = 'resident_cccccccccccc';
const D = 'resident_dddddddddddd';
// A may restart its Session; B and C may only change their settings; D's the
// reader may only look at.
const residents = {
  [A]: { restart: true, settings: { model: 'gpt-x', reasoning_effort: 'high' } },
  [B]: { restart: false, settings: { model: 'gpt-y', reasoning_effort: 'low' } },
  [C]: { restart: false, settings: { model: 'gpt-c', reasoning_effort: 'low' } },
  [D]: { restart: false, edit: false, settings: {} },
};
const held = new Map();
let heldSave = null;
const listeners = new Map();
const service = {
  async projection({ residentId }) {
    await held.get(residentId)?.promise;
    return {
      resident: { backend: 'codex' }, execution_account: { account_id: 'acct_a' },
      backend_capabilities: { runtime_capabilities: { settings: ['model'] } },
      chat_workspace: { actions: { runtime_settings: {} } },
      allows: { UpdateResidentSettings: residents[residentId].edit !== false, RestartSession: residents[residentId].restart },
      runtime_settings: { ...residents[residentId].settings },
    };
  },
  runtimeSettings: async id => ({ settings: { ...residents[id].settings } }),
  runtimeCatalog: async () => ({ runtime_capabilities: { settings: ['model', 'reasoning_effort'] } }),
  observeSessionRestart(id, listener) {
    if (!listeners.has(id)) listeners.set(id, new Set());
    listeners.get(id).add(listener);
    return () => listeners.get(id).delete(listener);
  },
  sessionRestartOptions: async id => ({ resident_id: id }),
  async updateRuntimeSettings(id, changes) {
    await heldSave?.promise;
    Object.assign(residents[id].settings, changes);
    return { settings: { ...residents[id].settings }, next_turn_fields: Object.keys(changes) };
  },
  restartSession: async () => ({ status: 'accepted', restart_id: 'r9' }),
  cancelSessionRestart: async () => ({ status: 'cancelling' }),
  rememberSessionRestart() {},
};
const emit = (id, view) => [...(listeners.get(id) || [])].forEach(listener => listener(view));
const observing = id => listeners.get(id)?.size || 0;
const opened = [];
const dialog = (kind, { onClose }) => {
  const record = { kind, onClose, open(...args) { record.args = args; opened.push(record); }, close() {}, destroy() {} };
  return record;
};
w.PortalResidentService = service;
w.PortalResidentRuntimeController = w.PortalResidentRuntimeController.create({
  service, dialogs: { settings: options => dialog('settings', options), restart: options => dialog('restart', options) },
});

const contractFor = residentId => {
  const restart = residents[residentId].restart;
  return {
    schema: 'portal.chat_workspace.v3', messages: [],
    target: { kind: 'resident', id: residentId, resident_id: residentId },
    capabilities: ['history', 'refresh', 'runtime_settings', ...(restart ? ['session_restart'] : [])],
    actions: {
      runtime_settings: { instruction: 'UpdateResidentSettings', arguments: { resident_id: residentId } },
      ...(restart ? { session_restart: { instruction: 'RestartSession', options_url: '/restart-options', arguments: { resident_id: residentId } } } : {}),
    },
    composer: { can_send: true, queue: { items: [] }, runtime_backend: 'codex', runtime_settings: { ...residents[residentId].settings } },
  };
};
const refreshes = [];
const runtimeButton = (rootId = 'fixture') => w.document.querySelector(`#${rootId} [data-workspace-runtime-settings-open]`);
const runtimeLabel = () => runtimeButton()?.querySelector('.composerRuntimeLabel')?.textContent;
const refreshButton = () => w.document.getElementById('chatRefresh');
const progress = () => w.document.querySelector('#fixture .portalSessionRestartProgress');
const click = async node => { node.click(); await settle(); };
const workspaces = [];

(async () => {
  try {
    const reading = deferred();
    held.set(A, reading);
    const workspace = w.PortalChatWorkspace.createWorkspace({
      root: w.document.getElementById('fixture'), id: 'chat', historyScope: () => 'conversation', draftOwner: 'viewer',
      contract: contractFor(A), onRefresh: () => { refreshes.push('refresh'); },
    });
    workspaces.push(workspace);
    await settle();
    assert.equal(runtimeButton().disabled, true, 'the runtime button waits while the Resident is read');
    workspace.setComposer({ ...contractFor(A).composer, placeholder: 'Ask A' });
    await settle();
    assert.equal(runtimeButton().disabled, true, 'a runtime button drawn meanwhile waits too');
    held.delete(A);
    reading.resolve();
    await settle();
    assert.equal(runtimeLabel(), 'gpt-x · high', 'the runtime button shows the Resident\'s settings');
    assert.equal(runtimeButton().title, '切换模型与运行设置：gpt-x · high', 'and names them');
    assert.equal(runtimeButton().disabled, false, 'it can be used');
    assert.equal(refreshButton().getAttribute('aria-label'), 'Session 操作', 'the header button offers the Session restart');

    await click(runtimeButton());
    assert.deepEqual([opened.length, opened.at(-1).args[0].resident_id], [1, A], 'the runtime button opens the Resident\'s runtime dialog');
    opened.at(-1).onClose();
    assert.equal(w.document.activeElement, runtimeButton(), 'closing the dialog returns to the runtime button');
    await click(refreshButton());
    assert.deepEqual([opened.length, opened.at(-1).kind], [2, 'restart'], 'the header button opens the Session restart');
    assert.deepEqual(refreshes, [], 'instead of refreshing');

    const restart = (status, restartId = 'r1') => ({
      restart: { status, restart_id: restartId, source: { account_id: 'acct_a' }, target: { account_id: 'acct_b' } },
      config: { resident_id: A },
    });
    emit(A, restart('accepted'));
    await settle();
    assert.equal(progress().hidden, false, 'a restart in progress shows in the header');
    assert.match(progress().textContent, /acct_a → acct_b/, 'from where to where');
    assert.equal(runtimeButton().disabled, true, 'and locks the runtime button');
    workspace.applyContract(contractFor(A));
    await settle();
    assert.equal(progress().hidden, false, 'a new contract for the same Resident keeps the restart in the header');
    assert.equal(runtimeButton().disabled, true, 'and the runtime button locked');
    await click(progress().querySelector('[data-session-restart-progress-open]'));
    assert.deepEqual([opened.length, opened.at(-1).kind], [3, 'restart'], 'the progress opens the Session restart');
    emit(A, restart('completed'));
    await settle();
    assert.equal(progress().hidden, true, 'a finished restart leaves the header');
    assert.deepEqual(take(refreshes), ['refresh'], 'and the Chat refreshes once');
    assert.equal(runtimeButton().disabled, false, 'the runtime button can be used again');

    emit(A, restart('accepted', 'r2'));
    emit(A, restart('failed', 'r2'));
    await settle();
    assert.equal(progress().hidden, false, 'a failed restart stays in the header');
    take(refreshes);
    await click(progress().querySelector('[data-session-restart-dismiss]'));
    assert.equal(progress().hidden, true, 'until the reader dismisses it');
    assert.equal(w.document.activeElement, refreshButton(), 'which returns to the header button');

    const options = service.sessionRestartOptions;
    service.sessionRestartOptions = async () => { throw new Error('options offline'); };
    take(toasts);
    await click(refreshButton());
    service.sessionRestartOptions = options;
    assert.deepEqual(take(toasts), [['options offline', 'error']], 'a dialog that cannot open says why');

    // A Chat without runtime controls, such as a Family facade's.
    emit(A, restart('failed', 'r5'));
    await settle();
    assert.equal(progress().hidden, false);
    workspace.applyContract({
      ...contractFor(C), capabilities: ['history', 'refresh'], actions: {},
      composer: { can_send: true, queue: { items: [] } },
    });
    await settle();
    assert.equal(runtimeButton(), null, 'a Chat without runtime settings has no runtime button');
    assert.equal(progress().hidden, true, 'nor the previous Resident\'s restart');
    assert.equal(observing(A), 0, 'and lets the previous Resident go');
    workspace.applyContract(contractFor(A));
    await settle();
    assert.equal(runtimeLabel(), 'gpt-x · high');

    // The host changes the Resident: the controls wait for its Chat.
    workspace.releaseResidentRuntime();
    assert.equal(runtimeButton().disabled, true, 'the runtime button waits for the next Resident');
    emit(A, restart('accepted', 'r6'));
    emit(A, restart('completed', 'r6'));
    await settle();
    assert.equal(runtimeButton().disabled, true, 'news about the previous Resident leaves it waiting');
    assert.deepEqual(take(refreshes), [], 'and refreshes nothing');
    workspace.setComposer({ ...contractFor(A).composer, runtime_settings: { model: 'gpt-w' } });
    await settle();
    assert.equal(runtimeButton().disabled, true, 'a runtime button drawn meanwhile waits too');
    await click(refreshButton());
    assert.deepEqual(take(refreshes), ['refresh'], 'the header button refreshes meanwhile');
    assert.equal(opened.length, 3, 'and opens nothing');

    workspace.applyContract(contractFor(B));
    await settle();
    assert.equal(observing(A), 0, 'another Resident\'s Chat stops following the first one\'s restarts');
    assert.equal(runtimeLabel(), 'gpt-y · low', 'and shows that Resident\'s settings');
    assert.equal(refreshButton().getAttribute('aria-label'), '刷新 Chat Workspace', 'a Chat without a Session restart offers a refresh');
    await click(refreshButton());
    assert.deepEqual(take(refreshes), ['refresh'], 'which refreshes the Chat');

    await click(runtimeButton());
    assert.deepEqual([opened.length, opened.at(-1).kind, opened.at(-1).args[2].residentId], [4, 'settings', B],
      'the runtime button opens that Resident\'s settings');
    take(toasts);
    heldSave = deferred();
    const saved = opened.at(-1).args[1]({ model: 'gpt-z', reasoning_effort: 'low' });
    await settle();
    assert.equal(runtimeButton().disabled, true, 'the runtime button waits while a setting is saved');
    heldSave.resolve();
    heldSave = null;
    await saved;
    await settle();
    assert.equal(runtimeLabel(), 'gpt-z · low', 'a saved setting shows on the button');
    assert.equal(runtimeButton().disabled, false);
    assert.deepEqual(take(refreshes), ['refresh'], 'and refreshes the Chat once');
    assert.deepEqual(take(toasts), [['Runtime 设置已保存，从下一轮生效。', 'success']], 'saying when it applies');
    const rereading = deferred();
    held.set(B, rereading);
    w.dispatchEvent(new w.CustomEvent('portal:change', { detail: { resident_changes: { [B]: ['runtime'] } } }));
    await settle();
    assert.equal(runtimeButton().disabled, true, 'a change made elsewhere has the runtime button wait while the Resident is read again');
    held.delete(B);
    rereading.resolve();
    await settle();
    assert.equal(runtimeButton().disabled, false);
    workspace.applyContract({
      ...contractFor(B), composer: { ...contractFor(B).composer, runtime_settings: { model: 'gpt-y', reasoning_effort: 'low' } },
    });
    await settle();
    assert.equal(runtimeLabel(), 'gpt-z · low', 'a composer drawn from an older contract still shows the saved setting');

    // A late answer about the previous Resident.
    const late = deferred();
    held.set(C, late);
    workspace.applyContract(contractFor(C));
    await settle();
    workspace.applyContract(contractFor(B));
    await settle();
    late.resolve();
    await settle();
    assert.equal(runtimeLabel(), 'gpt-z · low', 'a late answer about the previous Resident changes nothing');

    // D's composer is B's: only the Resident tells them apart.
    Object.assign(residents[D].settings, residents[B].settings);
    assert.deepEqual({ ...contractFor(D).composer }, { ...contractFor(B).composer });
    workspace.applyContract(contractFor(D));
    await settle();
    assert.equal(runtimeLabel(), 'gpt-z', 'a Resident the reader may only look at shows the settings its Backend names');
    assert.equal(runtimeButton().disabled, true, 'on a button that stays closed');

    workspace.applyContract(contractFor(A));
    await settle();
    assert.ok(observing(A) > 0);
    const header = progress();
    const before = opened.length;
    workspace.destroy();
    assert.equal(observing(A), 0, 'destroying the Chat lets the Resident go');
    assert.equal(header.isConnected, false, 'and takes the restart progress away');
    await click(refreshButton());
    assert.deepEqual([refreshes.length, opened.length], [0, before], 'its header button no longer answers');

    const plainRefreshes = [];
    const plain = w.PortalChatWorkspace.createWorkspace({
      root: w.document.getElementById('plain'), id: 'plain', historyScope: () => 'plain',
      contract: { ...contractFor(A), target: { kind: 'workspace', id: 'ws' } },
      onRefresh: () => { plainRefreshes.push('refresh'); },
    });
    workspaces.push(plain);
    await settle();
    assert.equal(runtimeButton('plain'), null, 'a Chat without a Resident has no runtime button');
    const plainHeader = w.document.getElementById('plainRefresh');
    assert.equal(plainHeader.getAttribute('aria-label'), '刷新 Chat Workspace', 'nor a Session restart');
    await click(plainHeader);
    assert.deepEqual(plainRefreshes, ['refresh'], 'its header button refreshes');
    console.log('PASS a Resident\'s Chat opens its runtime settings and Session restart, follows the Resident and lets it go');
  } finally {
    for (const workspace of workspaces) workspace.destroy();
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
