'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const settle = async () => { for (let n = 0; n < 8; n++) await new Promise(resolve => setImmediate(resolve)); };
function fixture() {
  const dom = new JSDOM('<main></main>', {url: 'https://poll.fixture.invalid/', runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, timers = new Map(), intervals = new Map();
  let hidden = false, id = 0;
  Object.defineProperty(w.document, 'hidden', {configurable: true, get: () => hidden});
  w.setTimeout = fn => { timers.set(++id, fn); return id; };
  w.clearTimeout = id => timers.delete(id);
  w.setInterval = fn => { intervals.set(++id, fn); return id; };
  w.clearInterval = id => intervals.delete(id);
  w.PortalI18n = {t: s => s, markup: s => s, html: (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '')};
  const load = name => w.eval(fs.readFileSync(path.join(STATIC, name), 'utf8').replaceAll('export ', ''));
  return {w, dom, timers, intervals, load, root: w.document.querySelector('main'),
    tick: async () => { const entry = timers.entries().next().value; if (entry) { timers.delete(entry[0]); entry[1](); await settle(); } },
    visible: async value => { hidden = !value; w.document.dispatchEvent(new w.Event('visibilitychange')); await settle(); }};
}
async function instances() {
  const h = fixture(); const {w} = h;
  let reads = 0, fail = 0, finish;
  w.fetch = async () => {
    reads++;
    if (finish === true) await new Promise(resolve => { finish = resolve; });
    return {ok: !fail, status: fail || 200, json: async () => fail ? {error: 'denied'} : {instances: []}};
  };
  h.load('portal_management_instances.js');
  const view = await w.mount(h.root);
  try {
    assert.equal(reads, 1);
    await h.visible(false); await h.tick();
    assert.equal(reads, 1, 'hidden instance page makes no automatic request');
    await h.visible(true); assert.equal(reads, 2, 'visible page resumes once');
    finish = true; const running = view.refresh(); await settle();
    await view.refresh(); assert.equal(reads, 3, 'concurrent instance refresh is deduplicated');
    finish(); await running; finish = null;
    for (const status of [401, 403, 404]) {
      fail = status; await h.tick(); const denied = reads;
      await h.visible(false); await h.visible(true); await h.tick();
      assert.equal(reads, denied, `instance HTTP ${status} stops automatic reads`);
      fail = 0; h.root.querySelector('[data-instance-refresh]').click(); await settle();
      assert.equal(reads, denied + 1, 'explicit refresh can recover');
    }
    view.destroy(); await h.tick(); await h.visible(false); await h.visible(true);
    assert.equal(h.timers.size, 0);
  } finally { view.destroy(); h.dom.window.close(); }
}
async function todos() {
  const h = fixture(); const {w} = h;
  let reads = 0, fail = 0, resolveIdentity;
  w.PortalIdentity = {userId: async () => 'fixture_user'};
  w.PortalUIPrimitives = {randomUUID: () => 'uuid', mountIcons() {}};
  w.PortalInstructionClient = {execute: async name => {
    assert.equal(name, 'ListMailboxTodos'); reads++;
    if (fail) throw Object.assign(new Error('denied'), {status: fail});
    return {items: []};
  }};
  h.load('portal_mailbox_todos.js');
  let view = w.PortalMailboxTodos.create(h.root);
  const pulse = async () => { for (const fn of h.intervals.values()) fn(); await settle(); };
  try {
    view.setView({visible: true, active: true}); await settle();
    assert.equal(reads, 1);
    await h.visible(false); await pulse(); assert.equal(reads, 1, 'Todo interval is quiet when document hidden');
    await h.visible(true); assert.equal(reads, 2);
    for (const status of [401, 403, 404]) {
      fail = status; await pulse(); const denied = reads;
      await pulse(); w.dispatchEvent(new w.Event('focus')); await h.visible(false); await h.visible(true);
      assert.equal(reads, denied, `Todo HTTP ${status} stops timer/focus/visibility retries`);
      fail = 0; await view.refresh(); assert.equal(reads, denied + 1, 'explicit Todo refresh recovers');
    }
    view.setView({visible: false, active: false}); const closed = reads; await pulse(); assert.equal(reads, closed);
    view.destroy();
    w.PortalIdentity.userId = () => new Promise(resolve => { resolveIdentity = resolve; });
    view = w.PortalMailboxTodos.create(h.root);
    const late = view.refresh(); view.destroy(); resolveIdentity('fixture_user'); await late;
    assert.equal(reads, closed, 'destroy before identity resolution must not start a network read');
    assert.equal(h.intervals.size, 0);
  } finally { view.destroy(); h.dom.window.close(); }
}
(async () => { await instances(); await todos(); console.log('PASS instance/Todo network polling lifecycle'); })().catch(error => { console.error(error); process.exitCode = 1; });
