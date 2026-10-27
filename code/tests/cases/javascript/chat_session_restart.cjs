'use strict';
// The shared progress line and per-reader failure acknowledgement. The
// purpose-specific dialogs are exercised by resident_setup_protocol.cjs.
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
    assert.equal(head(first), '会话操作：当前 Session → route_b · 已受理，正在准备');
    assert.equal(first.element.children.length, 3, 'the compact line has the heading and two buttons');
    assert.equal(text(button(first, 'progress-open')), '查看操作进度');
    assert.equal(button(first, 'dismiss').hidden, true, 'a running migration cannot be dismissed');
    button(first, 'progress-open').click();
    assert.deepEqual(opened, ['first']);

    const failure = { config, restart: { status: 'failed', restart_id: 'r9', target: { account_id: 'route_b' } } };
    first.update(failure);
    second.update(failure);
    assert.equal(head(first), '会话操作：当前 Session → route_b · 失败');
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
