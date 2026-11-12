'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal');
const tick = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };

function fixture(t, {linked = false, initialList = null, detail = null} = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'notice_board_home.html'), 'utf8'), {
    url: 'https://notice.fixture.invalid/' + (linked ? '?notice_post=post_test' : ''),
    runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  const observers = new Set(), NativeObserver = w.MutationObserver;
  w.MutationObserver = class extends NativeObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  Object.defineProperty(w.document, 'hidden', {get: () => false});
  const tag = (strings, ...values) => strings.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  w.PortalI18n = {t: text => text, html: tag, text: tag};
  w.PortalCompatibility = {identifier: name => 'fixture_' + name};
  w.matchMedia = query => Object.assign(new w.EventTarget(), {media: query, matches: false});
  w.PortalAPI = {createClient: () => ({get() { throw Error('Unexpected API read'); }})};
  // Composer rendering is outside this regression. The actual panel, refresh
  // coordinator, dialog controller and overlay own the lifecycle under test.
  w.PortalChatWorkspace = {
    renderComposerAttachmentTray() {}, refreshComposerAttachmentRefs() {},
    bindComposerAttachmentActions() {}, bindImagePreview() {},
    handleAttachmentDragOver() {},
  };
  const timers = new Map(); let sequence = 0;
  w.setTimeout = (fn, delay) => { const id = ++sequence; timers.set(id, {fn, delay}); return id; };
  w.clearTimeout = id => timers.delete(id);
  let registration;
  w.PortalHomeComponents = {registerPanel: panel => { registration = panel; }};
  const calls = [];
  const payload = {posts: [], post_count: 0};
  w.PortalInstructionClient = {execute(name, args, options) {
    calls.push({name, args, options});
    if (name === 'ListPosts') return initialList ? initialList.promise : Promise.resolve(payload);
    if (name === 'GetPost') return detail ? detail.promise : Promise.resolve({post: {post_id: 'post_test', title: 'Test post', notification_revision: 1}});
    if (name === 'MarkPostRead') return Promise.resolve({unread: false});
    throw Error('Unexpected instruction ' + name);
  }};
  for (const name of ['portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js', 'portal_refresh_coordinator.js', 'notice_board_home.js']) {
    w.eval(fs.readFileSync(path.join(root, 'static', name), 'utf8'));
  }
  const source = w.document.querySelector(registration.source);
  let controller, cleanup;
  const mount = () => {
    controller = new w.AbortController();
    cleanup = registration.connect({root: source, signal: controller.signal,
      context: {currentUserReady: Promise.resolve({username: 'fixture'})}});
  };
  const destroy = () => { controller.abort(); cleanup(); };
  mount();
  t.after(async () => {
    destroy();
    observers.forEach(observer => observer.disconnect());
    await tick();
    w.close();
  });
  return {w, source, mount, destroy, calls, payload, timers,
    reads: name => calls.filter(call => call.name === name).length,
    phase: name => source.dispatchEvent(new w.CustomEvent('portal:home-panel-lifecycle', {detail: {phase: name}})),
    async pulse() { const pending = [...timers.values()]; timers.clear(); pending.forEach(timer => timer.fn()); await tick(); },
  };
}

test('hidden panel stops network until shown, without duplicate refreshes', async t => {
  const f = fixture(t);
  await tick();
  assert.equal(f.reads('ListPosts'), 1);
  f.phase('hide');
  await f.pulse();
  assert.equal(f.reads('ListPosts'), 1, 'hidden Home panel must not issue its minute ListPosts');
  f.phase('show'); await tick();
  assert.equal(f.reads('ListPosts'), 2, 'one catch-up read on show');
  f.phase('show'); await tick();
  assert.equal(f.reads('ListPosts'), 2, 'duplicate show does not enqueue another force refresh');
  f.destroy(); const stopped = f.reads('ListPosts');
  await f.pulse(); assert.equal(f.reads('ListPosts'), stopped);
  f.mount(); await tick();
  assert.equal(f.reads('ListPosts'), stopped + 1, 'remount creates one fresh coordinator');
});

test('visible detail retains explicit read and close semantics', async t => {
  const f = fixture(t, {linked: true});
  await tick();
  assert.equal(f.reads('GetPost'), 1);
  assert.equal(f.reads('MarkPostRead'), 1);
  const mark = f.calls.find(call => call.name === 'MarkPostRead');
  assert.equal(mark.args.post_id, 'post_test');
  assert.equal(mark.args.notification_revision, 1);
  assert.ok(mark.options.idempotencyKey);
  assert.equal(f.w.document.querySelector('#noticeBoardDetailTitle').textContent, 'Test post');
  f.w.document.querySelector('#noticeBoardDetailClose').click();
  assert.ok(!f.w.document.querySelector('#noticeBoardDetailOverlay').classList.contains('open'));
});

test('destroy during detail prevents late render and MarkPostRead', async t => {
  const detail = deferred();
  const f = fixture(t, {linked: true, detail});
  await tick();
  assert.equal(f.reads('GetPost'), 1);
  f.destroy();
  detail.resolve({post: {post_id: 'post_test', title: 'Late post', notification_revision: 1}});
  await tick();
  assert.equal(f.reads('MarkPostRead'), 0, 'destroyed panel must not mark a late detail read');
  assert.ok(!f.w.document.querySelector('#noticeBoardDetailOverlay').classList.contains('open'));
  assert.notEqual(f.w.document.querySelector('#noticeBoardDetailTitle').textContent, 'Late post');
});

test('destroy during initial list does not reopen linked post or resurrect DOM', async t => {
  const list = deferred();
  const f = fixture(t, {linked: true, initialList: list});
  await tick();
  const request = f.calls.find(call => call.name === 'ListPosts');
  f.destroy();
  const html = f.source.innerHTML;
  list.resolve(f.payload); await tick();
  assert.equal(f.reads('GetPost'), 0, 'initial list completion cannot reopen post after teardown');
  assert.equal(f.source.innerHTML, html);
  assert.equal(request.options?.signal?.aborted, true, 'transport receives cancellation');
});
