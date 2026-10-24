'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = process.env.SOURCE_ROOT || path.resolve(__dirname, '../../..');
const tick = () => new Promise(resolve => setImmediate(resolve));
const ids = Array.from({length: 105}, (_, index) => `resident_${index.toString(16).padStart(12, '0')}`);

function fixture(t) {
  const dom = new JSDOM('', {url: 'https://states.fixture.invalid', runScripts: 'outside-only'});
  const w = dom.window, calls = [], subscriptions = new Set();
  w.PortalI18n = {t: s => s};
  w.PortalIdentity = {load: async () => {}, username: () => 'fixture'};
  w.PortalEvents = {watchResident: () => () => {}};
  w.PortalResidentService = {loadStates: async (wanted, options) => {
    calls.push({ids: [...wanted], options});
    assert.ok(calls.length <= 3,
      `settling one batch must not recursively issue more batches: ${calls.map(call => call.ids.length)}`);
    return response(wanted, options);
  }};
  w.eval(fs.readFileSync(path.join(root, 'management_portal/static/resident_protocol.js'), 'utf8'));
  t.after(() => { for (const sub of subscriptions) sub.destroy(); dom.window.close(); });
  return {w, p: w.PortalResidentProtocol, calls, acquire(wanted, options = {}, listener = () => {}) {
    const sub = w.PortalResidentProtocol.acquireStates(wanted, listener, {load: false, ...options});
    subscriptions.add(sub); return sub;
  }};
}
function response(wanted, options = {}) {
  return {states: wanted.map(resident_id => ({resident_id, cursor: 7,
    state: {work_state: 'idle'}, controls: {can_send: true},
    ...(options.includeViewerState === false ? {} : {viewer_state: {unread: true}}),
  }))};
}

test('subscription inspection is passive and exposes an unreleased state consumer', t => {
  const f = fixture(t), id = ids[0];
  const empty = f.p.subscriptionSnapshot(id);
  assert.equal(empty.state_consumers, 0);
  assert.equal(empty.stream_active, false);
  const mailbox = f.acquire([id]);
  const background = f.acquire([id]);
  assert.equal(f.p.subscriptionSnapshot(id).state_consumers, 2);
  background.destroy();
  const onlyMailbox = f.p.subscriptionSnapshot(id);
  assert.equal(onlyMailbox.state_consumers, 1);
  assert.equal(onlyMailbox.conversation_consumers, 0);
  mailbox.destroy();
  const released = f.p.subscriptionSnapshot(id);
  assert.equal(released.state_consumers, 0);
  assert.equal(released.stream_active, false);
  assert.equal(onlyMailbox.state_consumers, 1, 'a captured observation is not a live mutable store');
  assert.ok(Object.isFrozen(onlyMailbox));
  assert.equal(empty.state_consumers, 0);
  assert.equal(f.calls.length, 0, 'inspection must never create a state read');
});

test('a subscribed batch larger than the idle cache keeps every state and unread projection', async t => {
  const f = fixture(t), sub = f.acquire(ids);
  await sub.load();
  assert.equal(f.calls.length, 1);
  for (const id of ids) assert.equal(f.p.stateSnapshot(id).viewer_state.unread, true);
  await sub.load();
  assert.equal(f.calls.length, 1);
});

test('passive consumer identities distinguish retained hidden Chat and release without reading', t => {
  const f = fixture(t), id = ids[0], root = f.w.document.createElement('section');
  f.w.document.body.append(root);
  root.checkVisibility = () => !root.hidden;
  const mailbox = f.acquire([id], {consumer: 'inbox'});
  const chat = f.p.acquire(id, () => {}, {load: false, consumer: 'chat', observationRoot: root});
  const other = f.p.acquire(id, () => {}, {load: false, consumer: 'private unrecognized label'});
  const first = f.p.subscriptionSnapshot(id);
  assert.equal(first.consumers.length, 3);
  assert.equal(new Set(first.consumers.map(c => c.id)).size, 3);
  const chatRecord = first.consumers.find(c => c.consumer === 'chat');
  assert.equal(chatRecord.visible, true);
  assert.equal(chatRecord.connected, true);
  assert.equal(first.consumers.find(c => c.consumer === 'inbox').scope, 'state');
  assert.equal(first.consumers.find(c => c.consumer === 'unknown').visible, null);
  assert.equal(JSON.stringify(first).includes('private'), false);
  root.hidden = true;
  const hidden = f.p.subscriptionSnapshot(id).consumers.find(c => c.id === chatRecord.id);
  assert.equal(hidden.visible, false);
  assert.equal(hidden.connected, true, 'hidden presentation is not a released subscription');
  assert.equal(chatRecord.visible, true, 'earlier observations do not mutate');
  assert.ok(Object.isFrozen(first.consumers) && Object.isFrozen(chatRecord));
  root.remove();
  assert.equal(f.p.subscriptionSnapshot(id).consumers.find(c => c.id === chatRecord.id).connected, false);
  chat.destroy(); chat.destroy(); other.destroy(); mailbox.destroy();
  assert.equal(f.p.subscriptionSnapshot(id).consumers.length, 0);
  assert.equal(f.p.subscriptionSnapshot(id).conversation_consumers, 0);
  assert.equal(f.calls.length, 0, 'observing and hiding a consumer must never issue a read');
});

test('an unsubscribed in-flight batch settles once after the Mailbox list is replaced', async t => {
  const f = fixture(t), sub = f.acquire(ids);
  const original = f.w.PortalResidentService.loadStates;
  let complete;
  f.w.PortalResidentService.loadStates = (...args) => {
    if (complete) return original(...args);
    f.calls.push({ids: [...args[0]], options: args[1]});
    return new Promise(resolve => { complete = () => resolve(response(...args)); });
  };
  const read = sub.load();
  await tick(); sub.destroy();
  f.acquire(ids.slice(0, 32));
  complete();
  const states = await read;
  assert.equal(Object.keys(states).length, ids.length);
  assert.equal(f.calls.length, 1);
  await f.acquire(['resident_replacement']).load();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1].ids, ['resident_replacement']);
  assert.equal(ids.filter(id => f.p.stateSnapshot(id).ready).length, 32,
    'released read pins must not turn the idle cache into permanent retention');
});

test('an old-actor batch cannot restore unread after identity changes during release', async t => {
  const f = fixture(t), sub = f.acquire(ids);
  let complete;
  f.w.PortalResidentService.loadStates = wanted => new Promise(resolve => {
    complete = () => resolve(response(wanted));
  });
  const read = sub.load(); await tick(); sub.destroy();
  f.w.dispatchEvent(new f.w.Event('portal:identity-changed'));
  complete();
  await assert.rejects(read, {name: 'AbortError'});
  for (const id of ids) assert.equal(f.p.stateSnapshot(id).viewer_state, null);
});

test('a viewer subscriber joins a state-only batch and fetches unread once after release', async t => {
  const f = fixture(t), stateOnly = f.acquire(ids, {includeViewerState: false});
  const original = f.w.PortalResidentService.loadStates;
  let complete;
  f.w.PortalResidentService.loadStates = (...args) => {
    if (complete) return original(...args);
    f.calls.push({ids: [...args[0]], options: args[1]});
    return new Promise(resolve => { complete = () => resolve(response(...args)); });
  };
  const stateRead = stateOnly.load(); await tick();
  const viewer = f.acquire(ids), viewerRead = viewer.load(); await tick();
  stateOnly.destroy(); viewer.destroy(); complete();
  await Promise.all([stateRead, viewerRead]);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[0].options.includeViewerState, false);
  assert.equal(f.calls[1].options.includeViewerState, true);
});

test('a failed released read does not retain its batch or retry without subscribers', async t => {
  const f = fixture(t), sub = f.acquire(ids);
  let fail;
  f.w.PortalResidentService.loadStates = () => new Promise((_, reject) => {fail = reject;});
  const read = sub.load(); await tick(); sub.destroy();
  fail(new Error('fixture read failure'));
  await assert.rejects(read, /fixture read failure/);
  assert.equal(ids.filter(id => f.p.stateSnapshot(id).ready).length, 0);
  f.w.PortalResidentService.loadStates = async wanted => response(wanted);
  const next = f.acquire(ids);
  await next.load();
  assert.equal(Object.keys(next.states()).length, ids.length);
});
