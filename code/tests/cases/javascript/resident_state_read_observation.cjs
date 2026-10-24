'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = process.env.SOURCE_ROOT || path.resolve(__dirname, '../../..');
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

function fixture(t) {
  const dom = new JSDOM('', {url: 'https://state-observation.invalid', runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, calls = [], receipts = [], subs = [], intervals = [], timers = [];
  w.PortalI18n = {t: s => s, message: s => s};
  w.PortalIdentity = {load: async () => {}, username: () => 'fixture'};
  w.PortalEvents = {watchResident: () => () => {}};
  w.setInterval = (callback, delay) => { intervals.push({callback, delay}); return intervals.length; };
  w.clearInterval = () => {};
  w.setTimeout = (callback, delay) => { timers.push({callback, delay}); return timers.length; };
  w.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].cancelled = true; };
  const result = call => ({response: {ok: true, status: 200}, payload: {status: 'committed', result: {
    states: (call.body.arguments.resident_ids || []).map(resident_id => ({resident_id, cursor: 7,
      state: {work_state: 'idle'}, controls: {can_send: true},
      ...(call.body.arguments.include_viewer_state === false ? {} : {viewer_state: {unread: true}}),
    })),
  }}});
  let respond = result;
  w.PortalAPI = {requestResult: async (url, call) => {
    assert.equal(url, '/api/instructions/execute');
    calls.push(plain(call));
    assert.ok(calls.length < 15, 'observation must not cause an unbounded read loop');
    return respond(call);
  }};
  w.addEventListener('portal:state-read', event => receipts.push(plain(event.detail)));
  for (const name of ['portal_instruction_client', 'portal_resident_service', 'resident_protocol']) {
    w.eval(fs.readFileSync(path.join(root, `management_portal/static/${name}.js`), 'utf8'));
  }
  t.after(() => { subs.forEach(sub => sub.destroy()); dom.window.close(); });
  return {w, calls, receipts, intervals, timers, result,
    respond: callback => { respond = callback; },
    acquire(options = {}) {
      const sub = w.PortalResidentProtocol.acquireStates(['private-resident'], () => {}, {load: false, ...options});
      subs.push(sub); return sub;
    },
  };
}

test('only the dispatching reader emits provenance; joining and cached reads add no request', async t => {
  const f = fixture(t), inbox = f.acquire({consumer: 'inbox'}), other = f.acquire();
  let finish;
  f.respond(call => new Promise(resolve => { finish = () => resolve(f.result(call)); }));
  const first = inbox.load(); await tick();
  const joined = other.load(); await tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.receipts.length, 0, 'the receipt describes the settled transport attempt');
  finish(); await Promise.all([first, joined]);
  await other.load();
  assert.equal(f.calls.length, 1);
  assert.equal(f.receipts.length, 1);
  const receipt = f.receipts[0];
  assert.equal(receipt.consumer, 'inbox');
  assert.equal(receipt.reader_id, 'state-1');
  assert.deepEqual(receipt.reasons, ['explicit_read']);
  assert.equal(receipt.transport_id, f.calls[0].headers['X-Portal-Request-ID']);
  assert.deepEqual(f.calls[0].body, {schema: 'tfi.hatg-instruction.v2', instruction: 'ListResidentStates',
    arguments: {resident_ids: ['private-resident']}});
  assert.ok(!JSON.stringify(f.receipts).includes('private-resident'));
});

test('queued focus and visibility causes survive the old read; timer has its own cause', async t => {
  const f = fixture(t), sub = f.acquire({consumer: 'inbox'});
  let finish;
  f.respond(call => f.calls.length === 1 ? new Promise(resolve => { finish = () => resolve(f.result(call)); }) : f.result(call));
  const first = sub.load(); await tick();
  f.w.dispatchEvent(new f.w.Event('focus'));
  f.w.document.dispatchEvent(new f.w.Event('visibilitychange'));
  finish(); await first; await tick();
  assert.equal(f.calls.length, 2, 'the existing queued refresh still coalesces both causes');
  assert.deepEqual(f.receipts[1].reasons, ['focus', 'visibilitychange']);
  assert.equal(f.receipts[1].reader_id, f.receipts[0].reader_id);
  f.intervals.find(timer => timer.delay === 120000).callback(); await tick();
  assert.equal(f.calls.length, 3);
  assert.deepEqual(f.receipts[2].reasons, ['reconciliation_timer']);
  assert.equal(new Set(f.receipts.map(item => item.transport_id)).size, 3);
});

test('a viewer follow-up is attributed to its actual reader, not the in-flight state-only reader', async t => {
  const f = fixture(t), stateOnly = f.acquire({includeViewerState: false});
  const viewer = f.acquire({consumer: 'inbox'});
  let finish;
  f.respond(call => f.calls.length === 1 ? new Promise(resolve => { finish = () => resolve(f.result(call)); }) : f.result(call));
  const first = stateOnly.load(); await tick();
  const second = viewer.load(); await tick(); finish();
  await Promise.all([first, second]);
  assert.equal(f.calls.length, 2);
  assert.equal(f.receipts[0].reader_id, 'state-1');
  assert.equal(f.receipts[1].reader_id, 'state-2');
  assert.equal(f.receipts[1].consumer, 'inbox');
  assert.deepEqual(f.receipts[1].reasons, ['explicit_read', 'viewer_followup']);
});

test('transient retry retains the initiating cause and adds only the retry marker', async t => {
  const f = fixture(t), sub = f.acquire({consumer: 'inbox'});
  f.respond(call => { if (f.calls.length === 1) throw new f.w.TypeError('fixture offline'); return f.result(call); });
  f.w.dispatchEvent(new f.w.Event('focus')); await tick();
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.receipts[0].reasons, ['focus']);
  f.timers.find(timer => timer.delay === 1000 && !timer.cancelled).callback(); await tick();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.receipts[1].reasons, ['focus', 'transient_retry']);
  assert.equal(f.receipts[1].reader_id, f.receipts[0].reader_id);
});

test('unclassified readers remain unknown and observation failures cannot fail a read', async t => {
  const f = fixture(t);
  await f.w.PortalResidentService.loadStates(['private-resident'], {readObservation: {
    consumer: 'private-label', reader_id: 'private-id', reasons: ['private-reason'], extra: 'private-body',
  }});
  assert.equal(f.receipts[0].consumer, 'unknown');
  assert.equal(f.receipts[0].reader_id, null);
  assert.deepEqual(f.receipts[0].reasons, ['unknown']);
  assert.ok(!JSON.stringify(f.receipts).includes('private-'));
  await f.acquire().load({force: true, reasons: null});
  assert.deepEqual(f.receipts[1].reasons, ['unknown']);
  f.w.dispatchEvent = () => { throw new Error('fixture unavailable observer'); };
  const response = await f.w.PortalResidentService.loadStates(['private-resident']);
  assert.equal(response.states.length, 1);
  assert.equal(f.calls.length, 3);
});
