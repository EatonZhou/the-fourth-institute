'use strict';
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { createHarness } = require('./projection_watch_contracts.cjs');
const sources = {};
function sourceTransform(name, source) {
  const hash = createHash('sha256').update(source).digest('hex');
  if (sources[name]) assert.equal(hash, sources[name], 'source stays fixed between scenarios');
  sources[name] = hash;
  return source;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate) {
  const end = Date.now() + 3000;
  while (!predicate()) { assert.ok(Date.now() < end, 'object authorization reached'); await pause(5); }
}
const object = ref => ({ ref, kind: 'resource', status: 'current', source: '', observed_at: '2026-10-19T00:00:00Z' });
const settle = promise => promise.then(value => ({ value }), error => ({ code: error.code }));
async function run() {
  const requests = [];
  const h = await createHarness({
    sourceTransform,
    context: [{ channel: 'focus', publishers: ['architecture', 'progress'], subscribers: ['architecture', 'progress'] }],
    objectResponse: payload => new Promise(resolve => requests.push({ payload, resolve })),
  });
  const second = h.window.document.querySelectorAll('iframe')[1].contentWindow.Projection;
  async function start(sdk, ref) {
    const count = requests.length;
    const result = settle(sdk.focus(object(ref)));
    await until(() => requests.length === count + 1);
    const pending = requests[count];
    assert.equal(pending.payload.id, 'workbench_111111111111');
    assert.equal(pending.payload.preview, false);
    return { result, reply(status = 200) {
      pending.resolve(Response.json(status === 200 ? pending.payload.object : { code: 'resource.forbidden', error: 'denied' }, { status }));
    } };
  }
  try {
    const a = await start(h.sdk, 'resource_A');
    const b = await start(second, 'resource_B');
    b.reply(); const latest = (await b.result).value;
    a.reply(); const stale = await a.result;
    assert.equal(stale.code, 'projection.cancelled', 'late A must not publish over B');
    assert.equal((await h.sdk.context()).focus.object.ref, 'resource_B');
    assert.equal((await h.sdk.context()).focus.revision, latest.revision, 'cancelled requests allocate no revision');

    const old = await start(h.sdk, 'resource_old');
    const denied = await start(second, 'resource_denied');
    denied.reply(403); assert.equal((await denied.result).code, 'resource.forbidden');
    old.reply(); assert.equal((await old.result).code, 'projection.cancelled');
    assert.equal((await h.sdk.context()).focus.object.ref, 'resource_B', 'failure keeps last committed selection');

    // The stale result must not even enter the host object registry, otherwise
    // its status poisons a later, freshly authorized selection of the same ref.
    const retry = await start(h.sdk, 'resource_old');
    requests.at(-1).payload.object.status = 'proposed';
    retry.reply(); assert.equal((await retry.result).value.object.status, 'proposed');
    const changes = h.messages.filter(x => x.type === 'portal-projection-focus');
    assert.deepEqual(changes.map(x => x.object.ref), ['resource_B', 'resource_B', 'resource_old', 'resource_old']);
    const closing = await start(h.sdk, 'resource_after_destroy');
    h.host.destroy(); closing.reply(); await pause(30);
    assert.equal(h.messages.filter(x => x.type === 'portal-projection-focus').length, changes.length, 'destroyed page cannot publish');
    console.log(JSON.stringify({ result: 'PASS', committed: changes.map(x => [x.object.ref, x.revision]), stale, denied: 'resource.forbidden', errors: h.errors }));
  } finally { await h.close(); }
}
async function publisherGuard() {
  let reply;
  const h = await createHarness({
    sourceTransform,
    context: [{ channel: 'focus', publishers: ['architecture'], subscribers: ['progress'] }],
    objectResponse: payload => new Promise(resolve => { reply = () => resolve(Response.json(payload.object)); }),
  });
  try {
    const valid = settle(h.sdk.focus(object('resource_valid')));
    await until(() => reply);
    const second = h.window.document.querySelectorAll('iframe')[1].contentWindow.Projection;
    assert.equal((await settle(second.focus(object('resource_forbidden')))).code, 'projection.context_forbidden');
    reply(); assert.equal((await valid).value.object.ref, 'resource_valid', 'non-publisher cannot invalidate an authorized request');
    assert.equal((await second.context()).focus.object.ref, 'resource_valid');
    assert.equal(h.messages.filter(x => x.type === 'portal-projection-focus').length, 1, 'only subscribers receive notifications');
    console.log(JSON.stringify({ result: 'PASS', publisher_guard: true, subscriber_scope: true, errors: h.errors }));
  } finally { await h.close(); }
}
run().then(publisherGuard).then(() => console.log(JSON.stringify({ sources })))
  .catch(error => { console.error(error); process.exitCode = 1; });
