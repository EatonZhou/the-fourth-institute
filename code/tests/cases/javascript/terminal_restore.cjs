'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_terminal.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function fixture(t, response, readResponse = () => { throw Object.assign(new Error('expired'), { status: 404 }); }) {
  const dom = new JSDOM('<main></main>', { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const w = dom.window, calls = [], sessions = [], timers = new Map();
  class Terminal {
    constructor() { this.cols = 80; this.rows = 24; this.output = ''; }
    loadAddon() {} open() {} focus() {} dispose() {} reset() { this.output = ''; }
    write(text) { this.output += text; }
    onData(fn) { this.input = fn; } onResize() {}
  }
  Object.assign(w, { Terminal, FitAddon: { FitAddon: class { fit() {} } }, PortalI18n: { t: x => x },
    setTimeout: fn => { const id = {}; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id) });
  w.eval(source);
  const controller = w.PortalTerminal.create({ mount: w.document.querySelector('main'),
    initialSessionId: 'saved', startUrl: '/start', startPayload: () => ({ cwd: '/workspace' }),
    apiPost: async (url, body) => { calls.push({ url, ...body }); return response(url, body); },
    apiGet: readResponse,
    onSessionChange: id => sessions.push(id),
  });
  return { controller, calls, sessions, timers };
}
for (const status of [401, 403]) test(`read failure ${status} stops automatic requests and preserves session for explicit retry`, async t => {
  let reads = 0, denied = true;
  const f = fixture(t, async () => ({session_id: 'saved', alive: true, cursor: 0}), async url => {
    assert.match(url, /session_id=saved/);
    reads++;
    if (denied) throw Object.assign(new Error('access denied'), {status});
    return {session_id: 'saved', alive: true, chunk: 'ready', cursor: 5};
  });
  const pulse = async () => { for (const [id, fn] of [...f.timers]) { f.timers.delete(id); await fn(); } };
  await f.controller.start();
  await pulse();
  assert.equal(reads, 1);
  assert.equal(f.timers.size, 0, 'authorization failure cannot schedule another read');
  assert.deepEqual(f.sessions, [], 'unchanged saved session emits no replacement or exit');
  const output = f.controller.terminal.output;
  f.controller.schedulePoll(100); await pulse();
  assert.equal(reads, 1, 'host resize/resume scheduling cannot override denied reads');
  assert.equal(f.controller.terminal.output, output, 'error text is not appended repeatedly');
  denied = false;
  await f.controller.start(); await pulse();
  assert.equal(reads, 2);
  assert.equal(f.calls.length, 1, 'explicit retry reuses shell, never creates or replays input');
  assert.match(f.controller.terminal.output, /ready/);
  await f.controller.destroy({closeSession: false});
  assert.equal(f.timers.size, 0);
});
test('read failure transient retry and terminal exit retain their distinct semantics', async t => {
  let status = 503, reads = 0;
  const f = fixture(t, async () => ({session_id: 'saved', alive: true, cursor: 0}), async () => {
    reads++; throw Object.assign(new Error('read failure'), {status});
  });
  const pulse = async () => { for (const [id, fn] of [...f.timers]) { f.timers.delete(id); await fn(); } };
  await f.controller.start(); await pulse();
  assert.equal(f.timers.size, 1, 'transient failures remain retryable');
  status = 404; await pulse();
  assert.equal(reads, 2);
  assert.equal(f.timers.size, 0);
  assert.equal(f.sessions.at(-1), '');
  assert.match(f.controller.terminal.output, /会话已结束/);
  await f.controller.destroy({closeSession: false});
});
test('opening a persisted expired terminal negotiates a usable new shell once', async t => {
  const f = fixture(t, async () => ({ session_id: 'new', alive: true, chunk: '$ ', cursor: 2 }));
  await Promise.all([f.controller.start(), f.controller.start()]);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0], { url: '/start', cols: 80, rows: 24, cwd: '/workspace', session_id: 'saved' });
  assert.deepEqual(f.sessions, ['new']);
  assert.match(f.controller.terminal.output, /\$ /);
  await f.controller.start();
  assert.equal(f.calls.length, 1, 'showing an attached panel does not create another shell');
  // A later exit remains an exit, never an automatic restart loop.
  for (const [id, fn] of [...f.timers]) { f.timers.delete(id); await fn(); }
  assert.match(f.controller.terminal.output, /会话已结束/);
  assert.equal(f.calls.length, 1);
});
test('late reconnect of an existing shell after navigation does not close it', async t => {
  let resolve;
  const f = fixture(t, () => new Promise(r => { resolve = r; }));
  const starting = f.controller.start(); await flush();
  assert.equal(f.calls.length, 1);
  await f.controller.destroy({ closeSession: false });
  resolve({ session_id: 'saved', alive: true, cursor: 0 }); await starting;
  assert.equal(f.calls.length, 1, 'navigation only detaches a reused shell');
});
test('late newly created replacement after navigation is closed, never orphaned', async t => {
  let resolve;
  const f = fixture(t, (url) => url === '/start' ? new Promise(r => { resolve = r; }) : {});
  const starting = f.controller.start(); await flush();
  assert.equal(f.calls.length, 1);
  await f.controller.destroy({ closeSession: false });
  resolve({ session_id: 'new', alive: true, cursor: 0 }); await starting;
  assert.equal(f.calls[1].session_id, 'new');
  assert.equal(f.calls[1].action, 'close');
});
for (const status of [403, 503]) test(`reconnect ${status} stays visible without fallback or queued input replay`, async t => {
  const f = fixture(t, () => { throw Object.assign(new Error('unavailable'), { status }); });
  await f.controller.start();
  assert.equal(f.calls.length, 1);
  assert.match(f.controller.terminal.output, /unavailable/);
  await f.controller.terminal.input('do not replay\r');
  assert.equal(f.calls.filter(c => c.url !== '/start').length, 0);
  assert.equal(f.sessions.length, 0);
  assert.equal(f.timers.size, 0, 'a failed attach does not poll or retry without user action');
});
test('restart while reconnect is pending reattaches before replacing the shell', async t => {
  let resolve, attempts = 0;
  const f = fixture(t, (url, body) => {
    if (url === '/start' && !attempts++) return new Promise(r => { resolve = r; });
    return { session_id: body.action === 'restart' ? 'restarted' : 'saved', alive: true, cursor: 0 };
  });
  const starting = f.controller.start(); await flush();
  const restarting = f.controller.control('restart'); await flush();
  resolve({ session_id: 'saved', alive: true, cursor: 0 });
  await starting; await restarting;
  assert.deepEqual(f.sessions, ['restarted']);
  assert.equal(f.calls.filter(c => c.action === 'restart').length, 1);
  assert.equal(f.calls.filter(c => c.action === 'close').length, 0);
});
test('explicit close during reconnect closes the old shell exactly once', async t => {
  let resolve;
  const f = fixture(t, url => url === '/start' ? new Promise(r => { resolve = r; }) : {});
  const starting = f.controller.start(); await flush();
  assert.equal(f.calls.length, 1);
  const closing = f.controller.close();
  resolve({ session_id: 'saved', alive: true, cursor: 0 }); await starting; await closing;
  assert.equal(f.calls.filter(c => c.action === 'close').length, 1);
});
