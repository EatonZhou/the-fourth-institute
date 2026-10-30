'use strict';
// The read receipt of a Resident's conversation: sent while the reader can
// see the conversation, at most once for the same presentation, one at a
// time, held while the page is hidden or the Chat is not shown, and retried
// after a failure once the page comes back or five seconds have passed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"><div id="messages"></div></section>', {
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
const frame = async () => {
  await new Promise(resolve => w.requestAnimationFrame(() => resolve()));
  for (let turn = 0; turn < 6; turn += 1) await new Promise(resolve => setTimeout(resolve, 0));
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

let hidden = false;
Object.defineProperty(w.document, 'hidden', { configurable: true, get: () => hidden });
let now = Date.parse('2026-10-26T01:00:00Z');
w.Date.now = () => now;
const retryTimers = [];
const nativeSetTimeout = w.setTimeout.bind(w);
w.setTimeout = (callback, delay, ...rest) => {
  if (delay === 5000) { retryTimers.push(callback); return 5000 + retryTimers.length; }
  return nativeSetTimeout(callback, delay, ...rest);
};
const nativeClearTimeout = w.clearTimeout.bind(w);
w.clearTimeout = id => (id > 5000 ? undefined : nativeClearTimeout(id));
const warnings = [];
w.console.warn = (...args) => warnings.push(args.map(String).join(' '));

const reads = [];
let answer = null;
const entries = {};
const storeListeners = new Set();
w.PortalConversationStore = {
  subscribe: listener => { storeListeners.add(listener); return () => storeListeners.delete(listener); },
  entry: key => entries[key],
  observeResidentRead: request => {
    reads.push({ ...request });
    return answer ? answer.promise : { item: { read_revision: 1 } };
  },
};

const { ResidentReadObserver } = w.PortalChatWorkspaceReadObserver.createReadObserver();
let loading = false;
let readReceipt = () => true;
const panel = {
  target: () => ({ resident_id: 'resident_sample' }),
  presented: true,
  pendingProtocol: null,
  conversationView: () => ({ readReceipt: snapshot => readReceipt(snapshot) }),
  workspace: { els: { messages: w.document.getElementById('messages') }, isLoading: () => loading },
};
const shown = (cursor, unread = true) => ({ cursor, viewer_state: { unread } });

(async () => {
  const observer = new ResidentReadObserver(panel);
  try {
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    observer.update(shown(5));
    assert.equal(reads.length, 0, 'the receipt waits for the next frame');
    await frame();
    assert.deepEqual(reads, [{ residentId: 'resident_sample', actor: 'sample_admin' }], 'a shown conversation is marked read');
    observer.update(shown(5));
    storeListeners.forEach(listener => listener());
    await frame();
    assert.equal(reads.length, 1, 'the same presentation is not marked again');
    observer.update(shown(6));
    await frame();
    assert.equal(reads.length, 2, 'a new unread presentation is');
    entries['resident:resident_sample'] = { unread: true, read_revision: 1 };
    storeListeners.forEach(listener => listener());
    await frame();
    assert.equal(reads.length, 3, 'an inbox entry unread again is marked again');
    entries['resident:resident_sample'] = { unread: false, read_revision: 4 };
    storeListeners.forEach(listener => listener());
    await frame();
    assert.equal(reads.length, 4, 'a newer read revision elsewhere asks again');
    storeListeners.forEach(listener => listener());
    await frame();
    assert.equal(reads.length, 4);

    hidden = true;
    observer.update(shown(7));
    await frame();
    assert.equal(reads.length, 4, 'a hidden page sends no receipt');
    hidden = false;
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await frame();
    assert.equal(reads.length, 5, 'the receipt goes once the page is seen');

    panel.presented = false;
    observer.update(shown(8));
    await frame();
    loading = true;
    panel.presented = true;
    observer.update(shown(8));
    await frame();
    loading = false;
    readReceipt = () => false;
    observer.update(shown(8));
    await frame();
    assert.equal(reads.length, 5, 'nor while the Chat is not presented, loading, or its view holds the receipt');
    readReceipt = () => true;
    observer.update(shown(8));
    await frame();
    assert.equal(reads.length, 6);

    answer = deferred();
    observer.update(shown(9));
    await frame();
    assert.equal(reads.length, 7);
    observer.update(shown(10));
    await frame();
    assert.equal(reads.length, 7, 'one receipt at a time');
    answer.resolve({ item: { read_revision: 5 } });
    answer = null;
    await frame();
    await frame();
    assert.equal(reads.length, 8, 'the presentation that arrived meanwhile is marked after it');

    answer = deferred();
    observer.update(shown(11));
    await frame();
    answer.reject(new Error('store offline'));
    answer = null;
    await frame();
    assert.equal(reads.length, 9);
    assert.match(warnings.at(-1), /Resident read receipt failed/);
    observer.update(shown(11));
    await frame();
    assert.equal(reads.length, 9, 'a failed receipt waits before trying again');
    w.dispatchEvent(new w.Event('focus'));
    await frame();
    assert.equal(reads.length, 10, 'focusing the page tries again at once');

    answer = deferred();
    observer.update(shown(12));
    await frame();
    answer.resolve(null);
    answer = null;
    await frame();
    assert.equal(reads.length, 11);
    assert.equal(retryTimers.length, 2, 'a refused receipt schedules a retry');
    now += 5000;
    retryTimers.at(-1)();
    await frame();
    assert.equal(reads.length, 12, 'five seconds later it tries again');

    observer.destroy();
    observer.update(shown(13));
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await frame();
    assert.equal(reads.length, 12, 'a destroyed observer sends nothing');
    assert.equal(storeListeners.size, 0, 'and stops listening to the store');
  } finally {
    observer.destroy();
    observers.forEach(item => item.disconnect());
    await frame();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
