'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const staticRoot = path.join(process.env.SOURCE_ROOT || path.resolve(__dirname, '../../..'), 'management_portal/static');
const drain = () => new Promise(resolve => setImmediate(resolve));

// The old browser network fixture, expressed as literal protocol input. The
// harness never calculates Mailbox entries, unread state, banner or retry policy.
const payload = { entries: Array.from({ length: 18 }, (_, index) => ({
  key: `resident:fixture_${index}`, title: `Conversation ${index}`, kind: 'resident-main',
  section: 'conversation', unread: false, pinned: false, updated_at: '2026-10-15T00:00:00Z', read_revision: 0,
})), content_signature: 'loaded', ready: true, unread_count: 0, next_page_token: 'next' };

function mailbox({ connected = false, targetRead, transform = (_name, source) => source } = {}) {
  const errors = [], reads = [], responses = [], readAttempts = [], receipts = [];
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
  window.addEventListener('portal:conversation-read', event => receipts.push(JSON.parse(JSON.stringify(event.detail))));
  window.PortalAPI = { get(url, options) {
    readAttempts.push({ url, options });
    if (url.startsWith('/api/agent-conversations?resident=') && targetRead) return targetRead(url, options);
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
    return { window, root, reads, banner, readAttempts, receipts,
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
  async 'state-omission'() {
    const ids = ['resident_111111111111', 'resident_222222222222'];
    const app = mailbox({ connected: true, transform: (name, source) =>
      name === 'resident_protocol.js' && process.env.STATE_PROTOCOL_SOURCE
        ? fs.readFileSync(process.env.STATE_PROTOCOL_SOURCE, 'utf8') : source });
    let missing = false, requests = 0;
    app.window.PortalResidentService = {loadStates: async selected => {
      requests += 1;
      assert.ok(requests <= 3, 'valid omission must not automatically re-read');
      return {states: selected.filter(id => !missing || id !== ids[0]).map(resident_id => ({
        resident_id, state: {work_state: 'running'}, viewer_state: {unread: false}, cursor: 1,
      }))};
    }};
    let sub;
    try {
      app.queue({...payload, entries: ids.map((resident_id, i) => ({
        ...payload.entries[i], key: `resident:${resident_id}`, resident_id,
      })), next_page_token: null});
      await app.advance(300); // Let the native mount finish its scheduled initial read.
      assert.equal(app.reads.length, 1);
      assert.equal(app.banner.hidden, true);
      assert.equal(app.rows().length, 2);
      sub = app.window.PortalResidentProtocol.acquireStates(ids, () => {}, {load: false});
      missing = true;
      await sub.load({force: true}); await app.advance(40);
      const entries = app.store.snapshot().entries;
      const absent = entries.find(e => e.resident_id === ids[0]);
      const present = entries.find(e => e.resident_id === ids[1]);
      assert.equal(absent.status, 'unknown');
      assert.equal(absent.resident_state_ready, false);
      assert.equal(absent.resident_state, null);
      assert.equal(present.status, 'running');
      assert.equal(app.store.snapshot().error, '');
      assert.equal(app.banner.hidden, true);
      assert.equal(app.rows().length, 2, 'omission is not deletion');
      const row = id => app.rows().find(r => r.dataset.agentConversationKey === `resident:${id}`);
      assert.equal(row(ids[0]).querySelector('.portalAgentInboxStatus.running'), null);
      assert.ok(row(ids[1]).querySelector('.portalAgentInboxStatus.running'));
      missing = false;
      await sub.load(); await app.advance(40);
      assert.ok(row(ids[0]).querySelector('.portalAgentInboxStatus.running'));
      assert.equal(requests, 3);
    } finally { sub?.destroy(); await app.close(); }
  },
  async 'read-provenance'() {
    const pending = [];
    const resident = 'resident_123456789abc';
    const app = mailbox({ connected: true, targetRead: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
    const change = cursor => app.window.dispatchEvent(new app.window.CustomEvent('portal:change', { detail: {
      resident_events: [{ resident_id: resident, cursor, event_type: 'message.created' }],
      projection_changes: { 'resident-view-state': { [resident]: cursor } },
    } }));
    const target = cursor => ({ target_state: 'present', target_present: true, resident_cursor: cursor, projection_cursor: cursor,
      entries: [{ key: `resident:${resident}`, resident_id: resident, title: 'private-conversation-body',
        cursor, kind: 'resident-main', section: 'conversation', unread: false }] });
    try {
      app.queue(payload);
      await Promise.all([app.refresh(), app.refresh()]);
      assert.equal(app.readAttempts.length, 1, 'a joined index refresh makes no extra HTTP attempt');
      assert.equal(app.receipts.length, 1, 'cache/in-flight joins do not fabricate reader receipts');
      change(1);
      await app.advance(80);
      assert.equal(pending.length, 1, 'two causal event types coalesce into the existing single read');
      change(2);
      pending[0].resolve(target(1));
      await drain();
      await app.advance(160);
      assert.equal(pending.length, 2, 'the newer event retains the original follow-up schedule');
      pending[1].resolve(target(2));
      await drain();
      const reads = app.receipts.filter(item => item.reader === 'resident');
      assert.deepEqual(reads[0].reasons.sort(), ['resident_event', 'viewer_state']);
      assert.deepEqual(reads[1].reasons.sort(), ['cursor_reconciliation', 'resident_event', 'viewer_state']);
      assert.equal(app.store.entry(`resident:${resident}`).cursor, 2);
      const failure = Object.assign(new Error('private-error-content'), { status: 503, code: 'private-error-code' });
      const pin = app.store.loadPin(resident);
      await drain();
      pending[2].reject(failure);
      await assert.rejects(pin, error => error === failure, 'diagnostics preserve the original failure object');
      assert.equal(app.receipts.at(-1).reader, 'pin');
      assert.equal(app.receipts.at(-1).outcome, 'failed');
      assert.equal(app.receipts.at(-1).error_status, 503);
      assert.equal(app.receipts.length, app.readAttempts.length);
      assert.equal(new Set(app.receipts.map(item => item.transport_id)).size, app.receipts.length);
      for (const [index, attempt] of app.readAttempts.entries()) {
        assert.equal(app.receipts[index].transport_id, attempt.options.headers['X-Portal-Request-ID']);
      }
      assert.doesNotMatch(JSON.stringify(app.receipts), /private-|123456789abc|page_token|entries/);
      await app.advance(1000);
      assert.equal(app.readAttempts.length, 4, 'observation schedules no new read after settled work');
    } finally { await app.close(); }
  },
  async 'background-index-budget'() {
    const requests = [], active = new Set();
    let peak = 0;
    const app = mailbox({ connected: true, targetRead(url) {
      const resident = new URL(url, 'https://fixture.invalid').searchParams.get('resident');
      let resolve;
      const promise = new Promise(yes => { resolve = yes; });
      const request = { resident, finish(cursor) {
        active.delete(request);
        resolve({ target_state: 'present', target_present: true, resident_cursor: cursor, projection_cursor: cursor,
          entries: [{ key: `resident:${resident}`, resident_id: resident, title: resident,
            cursor, kind: 'resident-main', section: 'conversation', unread: false }] });
      } };
      active.add(request);
      requests.push(request);
      peak = Math.max(peak, active.size);
      return promise;
    } });
    const residents = Array.from({ length: 16 }, (_, i) => `resident_${i.toString(16).padStart(12, '0')}`);
    const change = (ids, cursor) => app.window.dispatchEvent(new app.window.CustomEvent('portal:change', {
      detail: { resident_events: ids.map(resident_id => ({ resident_id, cursor, event_type: 'message.created' })) },
    }));
    try {
      app.queue(payload);
      await app.refresh();
      change(residents, 1);
      await app.advance(80);
      assert.equal(active.size, 2, 'background reads leave browser connections for foreground controls');
      // Coalesce newer events for both an active target and a still queued one.
      change([residents[0], residents[15]], 3);
      requests[0].finish(1);
      await drain();
      assert.equal(active.size, 2, 'free capacity drains the next waiting Resident');
      for (let step = 0; active.size && step < 20; step += 1) {
        for (const request of [...active]) request.finish(request.resident === residents[15] ? 3 : 1);
        await drain();
      }
      await app.advance(160);
      assert.deepEqual([...active].map(item => item.resident).sort(), [residents[0], residents[15]],
        'newer active and queued cursors survive their refresh timers');
      for (const request of [...active]) request.finish(3);
      await drain();
      assert.equal(active.size, 0);
      assert.equal(peak, 2);
      assert.deepEqual([...new Set(requests.map(item => item.resident))].sort(), residents.sort(), 'no queued target is lost');
      assert.equal(requests.filter(item => item.resident === residents[15]).length, 1, 'queued changes coalesce');
      for (const resident of residents) assert.ok(app.store.entry(`resident:${resident}`), 'each target reaches the store');
      assert.equal(app.store.entry(`resident:${residents[0]}`).cursor, 3);
      assert.equal(app.store.entry(`resident:${residents[15]}`).cursor, 3);
    } finally { await app.close(); }
  },
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
