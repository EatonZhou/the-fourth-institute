const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { babelParse } = require('./babel_parse.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const clone = value => JSON.parse(JSON.stringify(value));
const drain = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// A write-only rendering surface, not a DOM/layout emulator. It records the
// production renderer's text, hidden flags, row markup and registered handlers.
// It never computes Mailbox state, messages, unread dots, or API results.
class Surface {
  constructor(markup = '') {
    this.dataset = {};
    this.hidden = false;
    this.textContent = '';
    this.attributes = [];
    this.children = [];
    this.listeners = new Map();
    this.slots = new Map();
    this.innerHTML = markup;
  }
  setAttribute(name, value) {
    const previous = this.attributes.find(item => item.name === name);
    if (previous) previous.value = String(value);
    else this.attributes.push({ name, value: String(value) });
    if (name.startsWith('data-')) {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    }
  }
  set innerHTML(markup) {
    this.raw = String(markup);
    this.children = [];
  }
  get innerHTML() { return this.raw + this.children.map(child => child.markup).join(''); }
  get markup() {
    return `<button ${this.attributes.map(({ name, value }) => `${name}="${value}"`).join(' ')}>${this.raw}</button>`;
  }
  get childNodes() { return [this.raw]; }
  replaceChildren(...nodes) { this.innerHTML = nodes.join(''); }
  insertBefore(child, before) {
    child.remove();
    this.raw = '';
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0, 'insertBefore target must exist');
    this.children.splice(index, 0, child);
    child.parent = this;
  }
  remove() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  contains(node) { return this.children.includes(node); }
  closest(selector) {
    const attribute = /^\[([^\]]+)\]$/.exec(selector)?.[1];
    return this.attributes.some(item => item.name === attribute) ? this : null;
  }
  querySelector(selector) {
    const attribute = /^\[([^\]]+)\]$/.exec(selector)?.[1];
    // A panel class, optionally excluding another one: `.a` or `.a:not(.b)`.
    const [, wanted, excluded] = /^\.([\w-]+)(?::not\(\.([\w-]+)\))?$/.exec(selector) || [];
    const tag = attribute ? this.raw.match(new RegExp(`<[^>]*\\s${attribute}(?=[\\s=>])[^>]*>`))
      : wanted && [...this.raw.matchAll(/<[^>]*\sclass="([^"]*)"[^>]*>/g)].find(([, names]) => {
        const classes = names.split(/\s+/);
        return classes.includes(wanted) && !(excluded && classes.includes(excluded));
      });
    if (!tag) return null;
    if (!this.slots.has(selector)) {
      const slot = new Surface();
      if (attribute) slot.setAttribute(attribute, '');
      slot.hidden = /\shidden(?:[\s=>])/.test(tag[0]);
      this.slots.set(selector, slot);
    }
    return this.slots.get(selector);
  }
  querySelectorAll() { return []; } // Decorative badges/filters are browser responsibilities.
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatch(type, target) {
    assert.equal(this.listeners.get(type)?.length, 1, `Production ${type} binding must exist`);
    this.listeners.get(type)[0]({ target, preventDefault() {} });
  }
}

function template() {
  return {
    content: {},
    set innerHTML(markup) {
      const match = /^<button\s+([^>]*)>([\s\S]*)<\/button>\s*$/.exec(markup);
      assert.ok(match, 'Production row renderer must produce a button');
      const button = new Surface(match[2]);
      for (const [, name, value] of match[1].matchAll(/([\w-]+)="([^"]*)"/g)) button.setAttribute(name, value);
      this.content.firstElementChild = button;
    },
  };
}

function inboxSource() {
  const filename = path.join(staticRoot, 'portal_agent_inbox.js');
  const source = fs.readFileSync(filename, 'utf8');
  const scope = babelParse(source, filename).program.body[0].expression.callee.body;
  const mount = scope.body.find(node => node.type === 'FunctionDeclaration' && node.id.name === 'mount');
  assert.ok(mount, 'Locate the production Inbox mount');
  const statements = mount.body.body;
  const member = (node, object, property) => node?.type === 'MemberExpression'
    && node.object.name === object && node.property.name === property;
  const select = (predicate, label) => {
    const matches = statements.filter(predicate);
    assert.equal(matches.length, 1, `Locate production ${label}`);
    return source.slice(matches[0].start, matches[0].end);
  };
  const assign = (object, property) => select(node => node.type === 'ExpressionStatement'
    && node.expression.type === 'AssignmentExpression' && member(node.expression.left, object, property), `${object}.${property}`);
  const bind = event => select(node => node.type === 'ExpressionStatement'
    && node.expression.type === 'CallExpression' && member(node.expression.callee, 'root', 'addEventListener')
    && node.expression.arguments[0].value === event, `${event} handler`);

  // Execute original mount assignments/subscription and actual event bindings.
  // Skip only DOM placement/dragging/focus/todo setup. All Inbox functions stay
  // unchanged in their original lexical scope; there is no copied controller.
  const fixture = `
    window.__bindMailboxSurface = function (surface) {
      root = surface;
      const signal = new AbortController().signal;
      ${assign('root', 'innerHTML')}
      ${assign('state', 'unsubscribe')}
      ${bind('click')}
      ${bind('dblclick')}
      return () => { state.unsubscribe(); root = null; };
    };
  `;
  return source.slice(0, scope.end - 1) + fixture + source.slice(scope.end - 1);
}

function mailbox(t) {
  const reads = [], saves = [], toasts = [], errors = [], invalidated = [];
  const queuedReads = [], queuedSaves = [], stored = new Map(), timers = new Map();
  let timerId = 0;
  let now = Date.parse('2026-10-15T00:00:01Z');
  class ClockDate extends Date { static now() { return now; } }
  const schedule = callback => { timers.set(++timerId, callback); return timerId; };
  const clear = id => timers.delete(id);
  const document = Object.assign(new EventTarget(), {
    readyState: 'loading', hidden: false, activeElement: null, documentElement: { lang: 'zh-CN' },
    createElement(name) { assert.equal(name, 'template'); return template(); },
  });
  // Literal protocol snapshot is a separate authority, deliberately stale after
  // save. This fixture never derives a response from setReadState arguments.
  const residentSnapshot = Object.freeze({ ready: true, cursor: 1,
    viewer_state: Object.freeze({ unread: true }),
    state: Object.freeze({ status: 'idle', intake_state: 'open', queued_count: 0 }),
  });
  const window = Object.assign(new EventTarget(), {
    setTimeout: schedule, clearTimeout: clear, requestAnimationFrame: schedule,
    PortalIdentity: { username: () => 'playwright', load: async () => {} },
    PortalState: { storage: {
      readJson: (key, fallback) => stored.has(key) ? clone(stored.get(key)) : fallback,
      writeJson: (key, value) => stored.set(key, clone(value)),
    } },
    PortalAPI: { get(url) {
      assert.match(url, /^\/api\/agent-conversations(\?page_token=\w+)?$/);
      reads.push(url);
      assert.ok(queuedReads.length, 'Unexpected Mailbox request');
      return queuedReads.shift().promise;
    } },
    PortalInstructionClient: { execute(instruction, args, options) {
      saves.push(clone({ instruction, args, options }));
      assert.ok(queuedSaves.length, 'Unexpected save request');
      return queuedSaves.shift().promise;
    } },
    PortalResidentProtocol: {
      stateSnapshot: () => residentSnapshot,
      acquireStates: () => ({ load: async () => {}, destroy() {} }),
      invalidateViewerState: residentId => invalidated.push(residentId),
    },
    PortalUI: { toast: (...args) => toasts.push(args), formatDateTime: () => '10/15' },
    PortalUIPrimitives: { randomUUID, escapeHtml(value) {
      // Fixture inputs need no escaping; do not reimplement the escaping owner.
      const text = String(value);
      assert.doesNotMatch(text, /[&<>"']/);
      return text;
    } },
  });
  window.top = window;
  const context = vm.createContext({ window, document, navigator: {}, Date: ClockDate, AbortController,
    setTimeout: schedule, clearTimeout: clear, PortalUIPrimitives: window.PortalUIPrimitives,
    console: { error: (...args) => errors.push(args), warn: (...args) => errors.push(args) },
  });
  // The retry coordinator delegates error classification to this production
  // function. Load its actual body instead of a fixture that always retries.
  const protocolPath = path.join(staticRoot, 'resident_protocol.js');
  const protocolSource = fs.readFileSync(protocolPath, 'utf8');
  const protocolScope = babelParse(protocolSource, protocolPath).program.body[0].expression.callee.body;
  const classifier = protocolScope.body.find(node => node.type === 'FunctionDeclaration'
    && node.id.name === 'isTransientSyncError');
  assert.ok(classifier, 'Locate the production transient error classifier');
  window.PortalResidentProtocol.isTransientSyncError = vm.runInContext(
    `(${protocolSource.slice(classifier.start, classifier.end)})`, context, { filename: protocolPath },
  );
  for (const file of ['portal_state.js', 'portal_text.js', 'portal_refresh_coordinator.js', 'portal_conversation_store.js']) {
    {
    const source = fs.readFileSync(path.join(staticRoot, file), 'utf8');
    const program = file === 'portal_state.js' ? babelParse(source, file).program.body[0] : null;
    vm.runInContext(program ? source.slice(program.start, program.end) : source, context, { filename: file });
  }
  }
  vm.runInContext(inboxSource(), context, { filename: 'portal_agent_inbox.js' });
  const surface = new Surface();
  const unbind = window.__bindMailboxSurface(surface);
  t.after(() => { unbind(); window.dispatchEvent(new Event('pagehide')); assert.deepEqual(errors, []); });
  const get = name => {
    const slot = surface.querySelector(`[data-agent-conversation-${name}]`);
    assert.ok(slot, `Production ${name} surface must exist`);
    return slot;
  };
  return {
    store: window.PortalConversationStore, inbox: window.PortalAgentInbox, surface, reads, saves, toasts, invalidated,
    banner: () => get('error'), list: () => get('list'),
    request() { const next = deferred(); queuedReads.push(next); now += 1000; return next; },
    save() { const next = deferred(); queuedSaves.push(next); return next; },
    refresh() { surface.dispatch('click', get('refresh')); },
    doubleClick() { surface.dispatch('dblclick', get('list').children[0]); },
  };
}

const key = 'resident:resident_777777777777';
const unreadEntry = { key, resident_id: 'resident_777777777777', title: '未读对话', kind: 'resident-main',
  section: 'conversation', unread: true, updated_at: '2026-10-15T00:00:00Z', read_revision: 5 };

// test_5e7d4d705fad: leaking raw errors, leaving a stale banner, or losing the
// refresh handler must fail. No layout/scroll/viewport assertion is migrated.
test('initial Mailbox failure is actionable and retry renders the recovered entries', async t => {
  const app = mailbox(t);
  const failed = app.request();
  const initial = app.inbox.refresh();
  const rejected = assert.rejects(initial, /Portal request failed/);
  await drain();
  assert.equal(app.store.snapshot().loading, true);
  failed.reject(Object.assign(new Error('Portal request failed'), { status: 500, code: 'operation.failed' }));
  await rejected;
  assert.equal(app.banner().hidden, false);
  assert.equal(app.banner().textContent, 'Mailbox 暂时无法加载，请点击右上角刷新重试。');
  assert.equal(app.list().innerHTML, '', 'No duplicate error in the list');
  assert.deepEqual(app.toasts, [], 'Initial failure must not add a raw-error toast');
  assert.doesNotMatch(app.surface.innerHTML
    + [...app.surface.slots.values()].map(slot => slot.textContent + slot.innerHTML).join(''), /Portal request failed/);
  assert.equal(app.store.snapshot().error, 'Portal request failed', 'Store retains the cause, view translates it');
  const recovered = app.request();
  app.refresh();
  await drain();
  assert.equal(app.reads.length, 2, 'Refresh binding starts a new read');
  recovered.resolve({ ready: true, content_signature: 'loaded', unread_count: 0, next_page_token: 'next',
    entries: Array.from({ length: 18 }, (_, index) => ({
      key: `resident:fixture_${index}`, title: `Conversation ${index}`, kind: 'resident-main',
      section: 'conversation', unread: false, pinned: false, updated_at: '2026-10-15T00:00:00Z', read_revision: 0,
    })),
  });
  await drain();
  assert.equal(app.store.snapshot().loading, false);
  assert.equal(app.store.snapshot().refreshing, false);
  assert.equal(app.store.snapshot().error, '');
  assert.equal(app.banner().hidden, true);
  assert.equal(app.banner().textContent, '');
  assert.equal(app.list().children.length, 18);
  assert.deepEqual(app.list().children.map(row => row.dataset.agentConversationKey).sort(),
    Array.from({ length: 18 }, (_, index) => `resident:fixture_${index}`).sort());
});

// test_5a563a8c221f: omitting optimistic rendering, rollback, the user-facing
// failure, or retry must fail. Physical double-click and cross-authority reload
// wiring remain covered by the adjacent browser success case.
test('failed Mailbox read rolls back optimistic state and can be retried', async t => {
  const app = mailbox(t);
  const initial = app.request();
  const loading = app.inbox.refresh();
  await drain();
  initial.resolve({ ready: true, unread_count: 1, content_signature: '1:true', entries: [unreadEntry] });
  await loading;
  const assertUnread = unread => {
    assert.equal(app.store.entry(key).unread, unread);
    assert.equal(app.store.snapshot().unreadCount, unread ? 1 : 0);
    assert.equal(/class="portalAgentInboxUnreadDot"/.test(app.list().innerHTML), unread);
    assert.equal(/class="portalAgentInboxItem unread"/.test(app.list().innerHTML), unread);
  };
  assertUnread(true);
  const failed = app.save();
  app.doubleClick();
  assertUnread(false); // Promise still pending: optimism must precede the API result.
  assert.deepEqual(app.toasts, []);
  assert.deepEqual(app.saves[0].args, { key, unread: false });
  assert.equal(app.saves[0].instruction, 'MarkMailboxItemRead');
  // The revision shown travels with the change: a newer alert refuses it.
  assert.deepEqual(app.saves[0].options.expectedVersion, { [key]: '5' });
  failed.reject(Object.assign(new Error('fixture save failed'), { code: 'instruction.failed' }));
  await drain();
  assertUnread(true);
  assert.deepEqual(app.toasts, [['未读状态保存失败，请重试。', 'error']]);
  const retry = app.save();
  app.doubleClick();
  assert.equal(app.saves.length, 2);
  assert.deepEqual(app.saves[1].args, { key, unread: false });
  assertUnread(false);
  retry.resolve({ item: { key, unread: false, read_revision: 6 } });
  await drain();
  assertUnread(false);
  // A snapshot read before the change lands afterwards: its older revision is ignored.
  const stale = app.request();
  app.refresh();
  await drain();
  assert.equal(app.reads.length, 2);
  stale.resolve({ ready: true, unread_count: 1, content_signature: '1:stale', entries: [unreadEntry] });
  await drain();
  assertUnread(false);
  assert.equal(app.store.snapshot().refreshing, false);
  // A newer alert is accepted from any source.
  const alerted = app.request();
  app.refresh();
  await drain();
  alerted.resolve({ ready: true, unread_count: 1, content_signature: '1:alerted',
    entries: [{ ...unreadEntry, read_revision: 7 }] });
  await drain();
  assertUnread(true);
  assert.deepEqual(app.toasts, [['未读状态保存失败，请重试。', 'error']], 'Successful retry emits no further failure');
});

// Page one carries every notification. One older than the oldest Resident
// loaded waits for its page instead of showing above the Residents still to load.
test('Mailbox shows entries only as far as the loaded Resident pages reach', async t => {
  const app = mailbox(t);
  const resident = (id, at) => ({ key: `resident:resident_${id}`, resident_id: `resident_${id}`, title: id,
    kind: 'resident-main', section: 'conversation', unread: false, updated_at: at, read_revision: 0 });
  const notice = (id, at) => ({ key: `lineage-notification:lineage_aaaaaaaaaaaa:${id}`, title: id,
    kind: 'lineage-notification', section: 'notification', unread: false, updated_at: at, read_revision: 0 });
  const keys = () => app.list().children.map(row => row.dataset.agentConversationKey);
  const first = app.request();
  const loading = app.inbox.refresh();
  await drain();
  first.resolve({ ready: true, paged: true, unread_count: 0, content_signature: 'p1', next_page_token: 'p2', entries: [
    resident('aaaaaaaaaaaa', '2026-10-10T00:00:00Z'), notice('n1', '2026-10-09T00:00:00Z'),
    resident('bbbbbbbbbbbb', '2026-10-08T00:00:00Z'), notice('n2', '2026-10-03T00:00:00Z')] });
  await loading;
  assert.deepEqual(keys(), ['resident:resident_aaaaaaaaaaaa', 'lineage-notification:lineage_aaaaaaaaaaaa:n1',
    'resident:resident_bbbbbbbbbbbb']);
  const second = app.request();
  const more = app.store.loadMore();
  await drain();
  assert.equal(app.reads.at(-1), '/api/agent-conversations?page_token=p2');
  second.resolve({ ready: true, paged: true, unread_count: 0, content_signature: 'p2', next_page_token: '',
    entries: [resident('cccccccccccc', '2026-10-05T00:00:00Z')] });
  await more;
  await drain();
  assert.deepEqual(keys(), ['resident:resident_aaaaaaaaaaaa', 'lineage-notification:lineage_aaaaaaaaaaaa:n1',
    'resident:resident_bbbbbbbbbbbb', 'resident:resident_cccccccccccc', 'lineage-notification:lineage_aaaaaaaaaaaa:n2']);
});
