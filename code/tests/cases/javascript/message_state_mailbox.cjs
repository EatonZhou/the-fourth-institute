'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const drain = () => new Promise(resolve => setImmediate(resolve));

// The old browser network fixture, expressed as literal protocol input. The
// harness never calculates Mailbox entries, unread state, banner or retry policy.
const payload = { entries: Array.from({ length: 18 }, (_, index) => ({
  key: `resident:fixture_${index}`, title: `Conversation ${index}`, kind: 'resident-main',
  section: 'conversation', unread: false, pinned: false, updated_at: '2026-10-15T00:00:00Z', read_revision: 0,
})), content_signature: 'loaded', ready: true, unread_count: 0, next_page_token: 'next' };

function mailbox({ connected = false, transform = (_name, source) => source } = {}) {
  const errors = [], reads = [], responses = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body></body></html>', {
    url: 'https://message-state.fixture.invalid/settings.html', runScripts: 'outside-only',
    pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  const observers = new Set();
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  let now = Date.parse('2026-10-15T00:00:01Z'), sequence = 0;
  const timers = new Map();
  const NativeDate = window.Date;
  window.Date = class extends NativeDate { static now() { return now; } };
  const schedule = (callback, delay = 0, interval = 0) => {
    const id = ++sequence;
    timers.set(id, { callback, at: now + Number(delay), interval });
    return id;
  };
  window.setTimeout = (callback, delay) => schedule(callback, delay);
  window.clearTimeout = id => timers.delete(id);
  window.setInterval = (callback, delay) => schedule(callback, delay, delay);
  window.clearInterval = id => timers.delete(id);
  window.requestAnimationFrame = callback => schedule(() => callback(now), 16);
  window.cancelAnimationFrame = id => timers.delete(id);
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  // The stream's connectivity is an external input. The real coordinator owns
  // subscription listeners, disconnect classification, invalidation and timing.
  window.EventSource = class { constructor() { throw new Error('Unexpected network stream'); } };
  window.PortalEvents = { isConnected: () => connected, connect() {} };
  window.PortalIdentity = { username: () => 'playwright', load: async () => ({ username: 'playwright' }), userId: async () => 'playwright' };
  // Pages load portal_nav.js before the Inbox; this viewer has no personal assistant.
  window.PortalNav = { personalAssistantTarget: async () => null };
  window.PortalAPI = { get(url) {
    assert.equal(url, '/api/agent-conversations');
    reads.push(url);
    assert.ok(responses.length, 'Unexpected snapshot request');
    const response = responses.shift();
    return response instanceof Error ? Promise.reject(response) : Promise.resolve(response);
  } };
  window.PortalInstructionClient = { execute: async (instruction, args) => {
    assert.equal(instruction, 'ListMailboxTodos');
    assert.deepEqual({ ...args }, { user_id: 'playwright' });
    return { items: [], pending_count: 0 };
  } };
  try {
    for (const name of ['portal_state.js', 'portal_text.js', 'portal_ui_primitives.js',
      'portal_refresh_coordinator.js', 'resident_protocol.js', 'portal_conversation_store.js',
      'portal_mailbox_todos.js', 'portal_agent_inbox.js']) {
      window.eval(transform(name, fs.readFileSync(path.join(staticRoot, name), 'utf8')) + `\n//# sourceURL=${name}`);
    }
    const style = window.document.createElement('style');
    style.textContent = transform('portal_components.css', fs.readFileSync(path.join(staticRoot, 'portal_components.css'), 'utf8'));
    window.document.head.append(style);
    window.PortalAgentInbox.mount();
    const root = window.document.querySelector('[data-portal-agent-inbox]');
    assert.ok(root, 'production mount creates the actual Inbox');
    const banner = root.querySelector('[data-agent-conversation-error]');
    return { window, root, reads, banner,
      queue: value => responses.push(value),
      rows: () => [...root.querySelectorAll('[data-agent-conversation-key]')],
      store: window.PortalConversationStore,
      refresh: () => window.PortalAgentInbox.refresh(),
      async advance(milliseconds) {
        const end = now + milliseconds;
        for (let limit = 0; limit < 100; limit += 1) {
          await drain();
          const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
          if (!next) { now = end; await drain(); return; }
          const [id, timer] = next;
          timers.delete(id);
          now = timer.at;
          if (timer.interval) timers.set(id, { ...timer, at: now + timer.interval });
          timer.callback();
        }
        assert.fail('Fixture clock encountered runaway production timers');
      },
      assertRecovered(expectedReads) {
        assert.equal(reads.length, expectedReads, 'the real coordinator made the snapshot verification');
        assert.equal(banner.hidden, true);
        assert.equal(window.getComputedStyle(banner).display, 'none', 'production CSS honors the banner hidden state');
        assert.equal(banner.textContent, '');
        assert.equal(this.rows().length, 18);
        assert.deepEqual(this.rows().map(row => row.dataset.agentConversationKey).sort(),
          payload.entries.map(entry => entry.key).sort());
        assert.equal(this.store.snapshot().refreshing, false);
        assert.equal(this.store.snapshot().error, '');
      },
      async close() {
        window.PortalAgentInbox.destroy();
        window.dispatchEvent(new window.Event('pagehide'));
        for (const observer of observers) observer.disconnect();
        await drain();
        window.close();
        assert.deepEqual(errors, [], 'no production/jsdom errors');
      },
    };
  } catch (error) { window.close(); throw error; }
}

const checks = {
  async 'realtime-disconnect'(options = {}) {
    const app = mailbox({ ...options, connected: true });
    try {
      app.queue(payload);
      await app.refresh();
      app.assertRecovered(1);
      app.queue(payload);
      app.window.dispatchEvent(new app.window.CustomEvent('portal:events-error'));
      assert.equal(app.banner.hidden, true, 'disconnect must not create a false snapshot error');
      await app.advance(500);
      app.assertRecovered(2);
    } finally { await app.close(); }
  },
  async 'transient-retry'(options = {}) {
    const app = mailbox(options);
    try {
      app.queue(payload);
      await app.refresh();
      app.assertRecovered(1);
      await app.advance(1); // A later user click must not reuse a same-millisecond cache entry.
      app.queue(Object.assign(new Error('Portal request failed'), { status: 500, code: 'operation.failed' }));
      app.root.querySelector('[data-agent-conversation-refresh]').click();
      await drain();
      assert.equal(app.reads.length, 2, 'production click binding requests refresh');
      assert.equal(app.banner.hidden, false);
      assert.notEqual(app.window.getComputedStyle(app.banner).display, 'none');
      assert.match(app.banner.textContent, /Mailbox 刷新失败，当前显示上次加载的内容/);
      assert.equal(app.rows().length, 18, 'failed read preserves cached rows');
      app.queue(payload);
      await app.advance(2000);
      app.assertRecovered(3);
    } finally { await app.close(); }
  },
};

module.exports = { checks };
if (require.main === module) {
  const name = process.argv[2];
  assert.ok(Object.hasOwn(checks, name), `Unknown independent contract: ${name}`);
  checks[name]().then(() => process.stdout.write(`PASS ${name}\n`));
}
