'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = process.env.SOURCE_ROOT || path.resolve(__dirname, '../../..');
const tick = () => new Promise(resolve => setImmediate(resolve));
const ids = Array.from({length: 105}, (_, index) => `resident_${index.toString(16).padStart(12, '0')}`);

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

async function submissionFixture(t) {
  const f = fixture(t), residentId = ids[0], sends = [], reads = [], updates = [];
  let snapshot = {resident: {resident_id: residentId}, cursor: 1,
    messages: [], input_states: [], queue: {items: []}, state: {}, controls: {can_send: true}};
  f.w.PortalResidentService.snapshot = async () => snapshot;
  f.w.PortalResidentService.sendInput = payload => {
    const request = deferred(); sends.push({payload, ...request}); return request.promise;
  };
  f.w.PortalResidentService.inputState = async (resident_id, message_id) => {
    reads.push({resident_id, message_id}); throw Object.assign(new Error('not found'), {status: 404});
  };
  const sub = f.track(f.p.acquire(residentId, update => updates.push(update), {load: false}));
  await sub.load();
  const submit = (message_id = 'msg_local') => f.p.sendInput({resident_id: residentId,
    message: {message_id, role: 'user', content_parts: [{type: 'text', text: 'I grant the credentials'}]}});
  const receipt = (message_id = 'msg_canonical') => ({resident_id: residentId, message_id, cursor: 2,
    message: {message_id, role: 'user', content_parts: [{type: 'text', text: 'canonical answer'}]},
    input_state: {resident_id: residentId, message_id, state: 'completed'}});
  return {...f, residentId, sub, sends, reads, updates, submit, receipt,
    async refresh(value) { snapshot = value; await sub.load({force: true}); },
    snapshot(value = {}) { return {...snapshot, ...value}; }};
}

for (const order of ['receipt-first', 'snapshot-first']) {
  test(`canonical submission identity reconciles once with ${order}`, async t => {
    const f = await submissionFixture(t), pending = f.submit(), ack = f.receipt();
    let settled = false; pending.then(() => { settled = true; });
    await tick();
    assert.equal(f.sub.snapshot().local_submissions[0].message_id, 'msg_local');
    const committed = f.snapshot({cursor: 2, messages: [ack.message], input_states: [ack.input_state]});
    if (order === 'snapshot-first') {
      await f.refresh(committed);
      assert.equal(settled, false, 'an unrelated ID in a snapshot cannot prove this request succeeded');
      assert.equal(f.sub.snapshot().local_submissions[0].message_id, 'msg_local');
    }
    const marker = f.updates.length;
    f.sends[0].resolve(ack);
    assert.equal((await pending).message_id, 'msg_canonical');
    await tick();
    if (order === 'receipt-first') {
      // An older in-flight snapshot cannot restore the local ID or duplicate.
      await f.refresh(f.snapshot());
      await f.refresh(committed);
    }
    assert.equal(f.sub.snapshot().local_submissions?.length || 0, 0);
    assert.equal(f.sub.snapshot().messages.length, 1);
    assert.equal(f.sub.snapshot().messages[0].message_id, 'msg_canonical');
    for (const update of f.updates.slice(marker).filter(u => u.snapshot)) {
      assert.equal(update.snapshot.local_submissions?.length || 0, 0);
      assert.equal(update.snapshot.messages.length, 1);
    }
    assert.equal(f.sends.length, 1);
    assert.equal(f.sends[0].payload.message.message_id, 'msg_local', 'request identity remains immutable');
  });
}

test('minimal canonical receipt retains one accepted preview until its queue projection arrives', async t => {
  const f = await submissionFixture(t), first = f.submit(), second = f.submit('msg_next');
  await tick();
  assert.equal(f.sends.length, 1);
  f.sends[0].resolve({resident_id: f.residentId, message_id: 'msg_canonical', cursor: 2,
    input_state: {message_id: 'msg_canonical', state: 'queued'}});
  await first; await tick();
  const local = f.sub.snapshot().local_submissions;
  assert.equal(local[0].message_id, 'msg_canonical');
  assert.equal(local[0].local_delivery.state, 'accepted');
  assert.equal(f.sends.length, 2, 'next submission is admitted only after receipt');
  await f.refresh(f.snapshot({cursor: 2, queue: {items: [{message_id: 'msg_canonical'}]}}));
  assert.deepEqual(Array.from(f.sub.snapshot().local_submissions, m => m.message_id), ['msg_next']);
  f.sends[1].resolve(f.receipt('msg_next')); await second;
});

for (const mismatch of ['recipient', 'nested-recipient', 'identity', 'missing-identity']) {
  test(`a ${mismatch} receipt cannot settle or remove the original submission`, async t => {
    const f = await submissionFixture(t), pending = f.submit();
    let settled = false; pending.then(() => { settled = true; }, () => { settled = true; });
    await tick();
    const ack = f.receipt();
    if (mismatch === 'recipient') ack.resident_id = ids[1];
    if (mismatch === 'nested-recipient') ack.input_state.resident_id = ids[1];
    if (mismatch === 'identity') ack.input_state.message_id = 'msg_unrelated';
    if (mismatch === 'missing-identity') { delete ack.message_id; delete ack.message; delete ack.input_state; }
    f.sends[0].resolve(ack); await tick();
    assert.equal(settled, false);
    assert.equal(f.sub.snapshot().local_submissions[0].message_id, 'msg_local');
    assert.equal(f.sub.snapshot().local_submissions[0].local_delivery.state, 'confirming');
    assert.equal(f.sub.snapshot().messages.length, 0);
    await f.p.checkSubmissions(f.residentId);
    assert.ok(f.reads.every(read => read.message_id === 'msg_local' && read.resident_id === f.residentId));
    assert.equal(settled, false);
    assert.equal(f.sends.length, 1, 'invalid receipt cannot trigger redelivery');
  });
}

test('uncertain network result keeps original identity and blocks the next submission until proven', async t => {
  const f = await submissionFixture(t), first = f.submit(), second = f.submit('msg_next');
  let settled = false; first.then(() => { settled = true; });
  await tick();
  f.sends[0].reject(new TypeError('network disconnected')); await tick();
  await f.p.checkSubmissions(f.residentId);
  assert.equal(settled, false);
  assert.equal(f.sends.length, 1);
  assert.equal(f.sub.snapshot().local_submissions[0].local_delivery.state, 'confirming');
  const ack = f.receipt('msg_local');
  await f.refresh(f.snapshot({cursor: 2, messages: [ack.message], input_states: [ack.input_state]}));
  await first; await tick();
  assert.equal(f.sends.length, 2);
  f.sends[1].resolve(f.receipt('msg_next')); await second;
});

test('a receipt received while Chat is detached reconciles when the same Resident is reopened', async t => {
  const f = await submissionFixture(t), pending = f.submit();
  await tick(); f.sub.destroy();
  f.sends[0].resolve(f.receipt());
  assert.equal((await pending).message_id, 'msg_canonical');
  const reopened = f.track(f.p.acquire(f.residentId, () => {}, {load: false}));
  await reopened.load();
  assert.equal(reopened.snapshot().local_submissions[0].message_id, 'msg_canonical');
  assert.equal(reopened.snapshot().local_submissions[0].local_delivery.state, 'accepted');
  const ack = f.receipt();
  await f.refresh(f.snapshot({cursor: 2, messages: [ack.message], input_states: [ack.input_state]}));
  assert.equal(reopened.snapshot().local_submissions?.length || 0, 0);
  assert.equal(reopened.snapshot().messages.length, 1);
});

for (const idempotencyKey of [undefined, 'caller-delivery-key']) {
test(`explicit rejection retry preserves prepared payload and ${idempotencyKey ? 'caller' : 'generated'} key`, async t => {
  const f = await submissionFixture(t); let prepared = 0;
  const pending = f.p.sendInput({resident_id: f.residentId,
    ...(idempotencyKey ? {idempotency_key: idempotencyKey} : {}),
    message: {message_id: 'msg_local', content_parts: []}}, {prepare: payload => {
      prepared++; payload.message.content_parts.push({type: 'text', text: 'prepared'});
    }});
  const rejected = assert.rejects(pending, /forbidden/);
  await tick();
  const preparedPayload = JSON.stringify(f.sends[0].payload);
  assert.equal(f.sends[0].payload.idempotency_key,
    idempotencyKey || `portal-deliver:${f.residentId}:msg_local`);
  f.sends[0].reject(Object.assign(new Error('forbidden'), {status: 403}));
  await rejected; await tick();
  assert.equal(f.sub.snapshot().local_submissions[0].local_delivery.state, 'failed');
  const retry = f.p.retrySubmission(f.residentId, 'msg_local'); await tick();
  assert.equal(prepared, 1);
  assert.equal(f.sends[1].payload.message.message_id, 'msg_local');
  assert.equal(f.sends[1].payload.message.content_parts.length, 1);
  assert.equal(JSON.stringify(f.sends[1].payload), preparedPayload);
  f.sends[1].resolve(f.receipt()); await retry; await tick();
  f.sub.acceptAcknowledgement(f.receipt());
  assert.equal(f.sub.snapshot().local_submissions?.length || 0, 0);
  assert.equal(f.sub.snapshot().messages.length, 1, 'repeated canonical receipt cannot duplicate the message');
});
}

test('recreating an original request retains its key after canonical reconciliation', async t => {
  const f = await submissionFixture(t);
  const first = f.submit(); await tick();
  const firstPayload = JSON.stringify(f.sends[0].payload);
  f.sends[0].resolve(f.receipt()); await first; await tick();
  const repeated = f.submit(); await tick();
  assert.equal(JSON.stringify(f.sends[1].payload), firstPayload);
  f.sends[1].resolve(f.receipt()); await repeated; await tick();
  assert.equal(f.sub.snapshot().local_submissions?.length || 0, 0);
  assert.equal(f.sub.snapshot().messages.length, 1);
  const next = f.submit('msg_different'); await tick();
  assert.notEqual(f.sends[2].payload.idempotency_key, f.sends[0].payload.idempotency_key);
  f.sends[2].resolve(f.receipt('msg_different')); await next;
});

test('identity switch discards late canonical receipts and never dispatches the old queued request', async t => {
  const f = await submissionFixture(t), first = f.submit(), second = f.submit('msg_old_next');
  const rejected = [assert.rejects(first, {name: 'AbortError'}), assert.rejects(second, {name: 'AbortError'})];
  await tick();
  f.w.dispatchEvent(new f.w.Event('portal:identity-changed'));
  await Promise.all(rejected); await tick();
  const fresh = f.submit('msg_new_actor'); await tick();
  f.sends[0].resolve(f.receipt()); await tick();
  assert.equal(f.sub.snapshot().messages.length, 0);
  assert.equal(f.sub.snapshot().local_submissions.length, 1);
  assert.equal(f.sub.snapshot().local_submissions[0].message_id, 'msg_new_actor');
  assert.deepEqual(f.sends.map(send => send.payload.message.message_id), ['msg_local', 'msg_new_actor']);
  f.sends[1].resolve(f.receipt('msg_new_actor')); await fresh;
});

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
  return {w, p: w.PortalResidentProtocol, calls, track(sub) { subscriptions.add(sub); return sub; },
    acquire(wanted, options = {}, listener = () => {}) {
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

test('one invalid Resident refreshes only its projection and preserves its peers', async t => {
  const f = fixture(t), wanted = ids.slice(0, 3), sub = f.acquire(wanted);
  await sub.load();
  f.p.invalidateViewerState(wanted[1]); await tick();
  assert.deepEqual(f.calls.map(call => call.ids), [wanted, [wanted[1]]]);
  assert.deepEqual(Object.keys(sub.states()).sort(), wanted);
  await sub.load({force: true});
  assert.deepEqual(f.calls[2].ids, wanted, 'explicit calibration still refreshes the complete collection');
});

test('focus still groups calibration of every subscribed Resident into one batch', async t => {
  const f = fixture(t), wanted = ids.slice(0, 3), sub = f.acquire(wanted);
  Object.defineProperty(f.w.document, 'hidden', {value: false});
  await sub.load();
  f.w.dispatchEvent(new f.w.Event('focus')); await tick();
  assert.deepEqual(f.calls.map(call => call.ids), [wanted, wanted]);
});

test('invalidations during a read coalesce only the affected Residents and recover omissions', async t => {
  const f = fixture(t), wanted = ids.slice(0, 3), sub = f.acquire(wanted);
  await sub.load();
  const pending = [];
  f.w.PortalResidentService.loadStates = (selected, options) => {
    f.calls.push({ids: [...selected], options});
    const read = deferred(); pending.push({...read, selected, options}); return read.promise;
  };
  f.p.invalidateViewerState(wanted[0]); await tick();
  f.p.invalidateViewerState(wanted[1]);
  f.p.invalidateViewerState(wanted[1]);
  pending[0].resolve(response(pending[0].selected, pending[0].options)); await tick();
  assert.deepEqual(f.calls.slice(1).map(call => call.ids), [[wanted[0]], [wanted[1]]]);
  pending[1].resolve({states: []}); await tick();
  assert.deepEqual(Object.keys(sub.states()).sort(), [wanted[0], wanted[2]]);
  const recovery = sub.load(); await tick();
  assert.deepEqual(Array.from(pending[2].selected), [wanted[1]]);
  pending[2].resolve(response(pending[2].selected, pending[2].options)); await recovery;
  assert.deepEqual(Object.keys(sub.states()).sort(), wanted);
});

test('a full calibration behind a targeted read also refreshes the other members', async t => {
  const f = fixture(t), wanted = ids.slice(0, 3), sub = f.acquire(wanted);
  await sub.load();
  const pending = [];
  f.w.PortalResidentService.loadStates = (selected, options) => {
    f.calls.push({ids: [...selected], options});
    const read = deferred(); pending.push({...read, selected, options}); return read.promise;
  };
  f.p.invalidateViewerState(wanted[0]); await tick();
  const full = sub.load({force: true});
  pending[0].resolve(response(pending[0].selected, pending[0].options)); await tick();
  assert.deepEqual(f.calls[2].ids, wanted);
  pending[1].resolve(response(pending[1].selected, pending[1].options)); await full;
});

test('a new invalidation cannot cancel a different member\'s pending recovery', async t => {
  const f = fixture(t), wanted = ids.slice(0, 3), sub = f.acquire(wanted);
  await sub.load();
  f.w.PortalResidentService.loadStates = async (selected, options) => {
    f.calls.push({ids: [...selected], options});
    if (f.calls.length === 2) throw new f.w.TypeError('offline');
    return response(selected, options);
  };
  f.p.invalidateViewerState(wanted[0]); await tick();
  f.p.invalidateViewerState(wanted[1]); await tick();
  assert.deepEqual(f.calls[2].ids, wanted.slice(0, 2));
  assert.deepEqual(Object.keys(sub.states()).sort(), wanted);
});

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


test('a valid omitted target stays unknown while visible batch members remain available', async t => {
  const f = fixture(t), wanted = ids.slice(0, 2), events = [];
  f.w.PortalResidentService.loadStates = async (selected, options) => {
    f.calls.push({ids: [...selected], options});
    return response(selected.filter(id => id !== wanted[0]), options);
  };
  const sub = f.acquire(wanted, {}, event => events.push(event));
  const states = await sub.load();
  assert.deepEqual(Object.keys(states), [wanted[1]]);
  const missing = f.p.stateSnapshot(wanted[0]);
  assert.equal(missing.ready, false);
  assert.equal(missing.state, null);
  assert.equal(missing.viewer_state, null);
  assert.equal(missing.cursor, null);
  assert.equal(f.p.stateSnapshot(wanted[1]).ready, true);
  assert.equal(events.some(event => event.type === 'error'), false);
  await tick();
  assert.equal(f.calls.length, 1, 'omission neither retries nor recurses for unread');
});

test('omission clears cached state and unread in every subscriber and a later real read recovers', async t => {
  const f = fixture(t), wanted = ids.slice(0, 2), sub = f.acquire(wanted), peer = f.acquire(wanted);
  await sub.load();
  let visible = false;
  f.w.PortalResidentService.loadStates = async (selected, options) => {
    f.calls.push({ids: [...selected], options});
    return response(selected.filter(id => visible || id !== wanted[0]), options);
  };
  await sub.load({force: true});
  for (const reader of [sub, peer]) assert.deepEqual(Object.keys(reader.states()), [wanted[1]]);
  assert.equal(f.p.stateSnapshot(wanted[0]).state, null, 'old idle state must not survive');
  assert.equal(f.p.stateSnapshot(wanted[0]).viewer_state, null, 'old unread must not survive');
  assert.equal(f.p.stateSnapshot(wanted[1]).viewer_state.unread, true);
  visible = true;
  await sub.load();
  assert.equal(f.calls.length, 3);
  assert.deepEqual(f.calls[2].ids, [wanted[0]], 'only the unavailable target requires a new read');
  assert.equal(f.p.stateSnapshot(wanted[0]).ready, true);
  assert.equal(f.p.stateSnapshot(wanted[0]).viewer_state.unread, true);
  for (const reader of [sub, peer]) assert.equal(Object.keys(reader.states()).length, 2);
});

test('viewer joins an omitted state-only batch without recursively querying the unavailable target', async t => {
  const f = fixture(t), wanted = ids.slice(0, 2), stateOnly = f.acquire(wanted, {includeViewerState: false});
  let complete;
  f.w.PortalResidentService.loadStates = (selected, options) => {
    f.calls.push({ids: [...selected], options});
    const result = () => response(selected.filter(id => id !== wanted[0]), options);
    if (!complete) return new Promise(resolve => { complete = () => resolve(result()); });
    return Promise.resolve(result());
  };
  const original = stateOnly.load(); await tick();
  const viewer = f.acquire(wanted), joined = viewer.load(); await tick();
  stateOnly.destroy(); complete();
  await Promise.all([original, joined]);
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1].ids, [wanted[1]], 'unread follow-up is only for the returned target');
  assert.equal(f.p.stateSnapshot(wanted[0]).ready, false);
  assert.equal(f.p.stateSnapshot(wanted[1]).viewer_state.unread, true);
});

test('malformed state payloads still fail instead of being treated as valid omissions', async t => {
  const f = fixture(t), id = ids[0], sub = f.acquire([id]);
  for (const [payload, error] of [
    [{}, /states are invalid/],
    [{states: [{resident_id: id}]}, /state is invalid/],
    [{states: [{resident_id: id, state: {status: 'idle'}}]}, /viewer state is invalid/],
  ]) {
    f.w.PortalResidentService.loadStates = async () => payload;
    await assert.rejects(sub.load({force: true}), error);
    assert.equal(f.p.stateSnapshot(id).ready, false);
  }
});
