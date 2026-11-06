const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = () => new Promise(resolve => setImmediate(resolve));
const accepted = {restart: {restart_id: 'op1', request_id: 'key1', status: 'accepted'}};
function fixture() {
  const events = new Map(), timers = new Map(), storage = new Map();
  let sequence = 0;
  const f = {calls: 0, mutations: [], response: async () => accepted};
  const document = new EventTarget();
  Object.defineProperty(document, 'hidden', {value: false, writable: true});
  const context = {
    console, URLSearchParams, document,
    setTimeout: fn => {const id = ++sequence; timers.set(id, fn); return id;},
    clearTimeout: id => timers.delete(id),
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key)},
    PortalI18n: {t: String},
    PortalUIPrimitives: {randomUUID: () => 'key1'},
    PortalAPI: {get: async () => {f.calls++; return f.response();}},
    PortalInstructionClient: {execute: async (name, args) => {
      f.mutations.push({name, args}); throw Object.assign(new Error('network'), {status: 0});
    }},
    addEventListener(name, fn) {if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn);},
    removeEventListener(name, fn) {events.get(name)?.delete(fn);},
  };
  context.window = context;
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_resident_service.js'), 'utf8'), context);
  f.service = context.PortalResidentService;
  f.timers = timers;
  f.tick = async () => {const due = [...timers.values()]; timers.clear(); for (const fn of due) fn(); await flush();};
  f.visible = async hidden => {document.hidden = hidden; document.dispatchEvent(new Event('visibilitychange')); await flush();};
  f.change = async () => {for (const fn of events.get('portal:change') || []) fn({detail: {resident_changes: {r1: ['session_restart']}}}); await flush();};
  f.storage = storage;
  return f;
}

test('restart polls only while a visible observer remains, without cancelling the operation', async () => {
  const f = fixture();
  const stop1 = f.service.observeSessionRestart('r1', () => {});
  const stop2 = f.service.createClient().observeSessionRestart('r1', () => {});
  await flush(); assert.equal(f.calls, 1);
  stop1(); await f.tick(); assert.equal(f.calls, 2, 'other observer retains polling');
  await f.visible(true); await f.tick(); await f.change();
  assert.equal(f.calls, 2, 'hidden view has no recurring/event reads');
  await f.visible(false); assert.equal(f.calls, 3, 'visible view reconciles once');
  stop2(); await f.tick(); assert.equal(f.calls, 3);
  assert.deepEqual(f.mutations, [], 'closing the UI never cancels the backend operation');
  const stop3 = f.service.observeSessionRestart('r1', () => {});
  await flush(); assert.equal(f.calls, 4); stop3();
  const initiallyHidden = fixture(); await initiallyHidden.visible(true);
  const hiddenStop = initiallyHidden.service.observeSessionRestart('r1', () => {});
  await flush(); assert.equal(initiallyHidden.calls, 0);
  await initiallyHidden.visible(false); assert.equal(initiallyHidden.calls, 1); hiddenStop();
});

test('an inflight read cannot restart polling after the last observer leaves', async () => {
  const f = fixture(); let finish;
  f.response = () => new Promise(resolve => {finish = resolve;});
  const stop = f.service.observeSessionRestart('r1', () => {});
  stop(); finish(accepted); await flush(); await f.tick();
  assert.equal(f.calls, 1); assert.equal(f.timers.size, 0);
});

test('401/403/404 stop polling; an explicit successful refresh permits subsequent polling', async () => {
  for (const status of [401, 403, 404]) {
    const f = fixture(); const stop = f.service.observeSessionRestart('r1', () => {});
    await flush();
    f.response = async () => {throw Object.assign(new Error('unavailable'), {status});};
    await f.tick(); assert.equal(f.calls, 2);
    await f.tick(); assert.equal(f.calls, 2, `HTTP ${status} is terminal until explicit refresh`);
    f.response = async () => accepted;
    await f.service.refreshSessionRestart('r1');
    await f.tick(); assert.equal(f.calls, 4, 'successful refresh clears the prior denial');
    f.response = async () => ({restart: {...accepted.restart, status: 'completed'}});
    await f.tick(); await f.tick(); assert.equal(f.calls, 5, 'terminal job stops polling'); stop();
  }
});

test('ambiguous submission persists its original key and only reconciles after reopening', async () => {
  const f = fixture(); f.response = async () => ({restart: null});
  const stop = f.service.observeSessionRestart('r1', () => {}); await flush();
  await assert.rejects(f.service.restartSession({resident_id: 'r1', operation: 'switch_account',
    idempotency_key: 'key1', target_provider: 'codex', target_account: 'two'}));
  await flush(); stop(); const calls = f.calls; await f.tick(); assert.equal(f.calls, calls);
  assert.equal(JSON.parse(f.storage.get('portal:pending-session-restart:r1')).payload.idempotency_key, 'key1');
  f.response = async () => accepted;
  const reopened = f.service.observeSessionRestart('r1', () => {}); await flush();
  assert.equal(f.calls, calls + 1); assert.equal(f.mutations.length, 1, 'reopening cannot resubmit');
  assert.equal(f.storage.has('portal:pending-session-restart:r1'), false); reopened();
});

test('temporary read failure retries only while the operation has a visible observer', async () => {
  const f = fixture(); const stop = f.service.observeSessionRestart('r1', () => {}); await flush();
  f.response = async () => {throw Object.assign(new Error('service temporarily unavailable'), {status: 503});};
  await f.tick(); await f.tick(); assert.equal(f.calls, 3);
  stop(); await f.tick(); assert.equal(f.calls, 3);
});
