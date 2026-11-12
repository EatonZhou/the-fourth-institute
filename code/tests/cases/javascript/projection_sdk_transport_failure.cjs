'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const events = new Map(), timers = new Map(), delivered = [];
  let timerId = 0;
  // Use the real structured-clone algorithm. A failed clone never reaches the host.
  const host = { postMessage(packet) { delivered.push(structuredClone(packet)); } };
  const scope = {
    document: { currentScript: { dataset: { channel: 'transport-fixture' } } }, parent: host,
    addEventListener(type, listener) {
      const listeners = events.get(type) || []; listeners.push(listener); events.set(type, listeners);
    },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); }, performance: { now: () => 0 }, console,
  };
  scope.window = scope;
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/projection_sdk.js'), 'utf8'), scope);
  const emit = (type, event) => (events.get(type) || []).forEach(listener => listener(event));
  const message = data => emit('message', { source: host, data: { channel: 'transport-fixture', ...data } });
  const reply = (packet, result) => message({ type: 'portal-projection-result', requestId: packet.requestId, result });
  return { sdk: scope.Projection, timers, delivered, message, reply, close: () => emit('pagehide', { persisted: false }) };
}

test('request clone failure releases its budget while another read and corrected submission succeed', async () => {
  const h = fixture();
  try {
    const reading = h.sdk.context();
    reading.catch(() => {}); // Keep teardown rejection handled if an assertion fails.
    const readPacket = h.delivered[0];
    for (let i = 0; i < 3; i++) {
      await assert.rejects(h.sdk.state({ draft: () => 'not cloneable' }), { name: 'DataCloneError' });
    }
    assert.equal(h.delivered.length, 1, 'invalid payloads never reach the host or retry automatically');
    assert.equal(h.timers.size, 1, 'only the independent context request may retain a timer');
    assert.equal([...h.timers.values()][0].delay, 270000);
    h.reply(readPacket, { visible: true });
    assert.equal((await reading).visible, true);
    assert.equal(h.timers.size, 0);
    const corrected = h.sdk.state({ draft: 'serializable' });
    assert.equal(h.delivered.length, 2);
    h.reply(h.delivered[1], { saved: true });
    assert.equal((await corrected).saved, true);
    assert.equal(h.timers.size, 0);
    console.log(JSON.stringify({ scenario: 'send-failure', invalidAttempts: 3, invalidDelivered: 0, remainingTimers: h.timers.size }));
  } finally { h.close(); }
});

test('watch clone failure preserves the accepted listener and permits later replacement', async () => {
  const h = fixture(), seen = [];
  try {
    const first = h.sdk.watch(['article_fixture'], () => seen.push('first'));
    h.reply(h.delivered.at(-1), {});
    const stopFirst = await first;
    await assert.rejects(h.sdk.watch([() => 'invalid ref'], () => seen.push('invalid')), { name: 'DataCloneError' });
    assert.equal(h.delivered.length, 1);
    assert.equal(h.timers.size, 0, 'failed watch send must not retain its request timeout');
    h.message({ type: 'portal-projection-change' });
    assert.deepEqual(seen, ['first']);
    const next = h.sdk.watch(['article_replacement'], () => seen.push('next'));
    h.reply(h.delivered.at(-1), {});
    const stopNext = await next;
    await stopFirst();
    assert.equal(h.delivered.length, 2, 'obsolete disposer cannot stop the replacement');
    h.message({ type: 'portal-projection-change' });
    assert.deepEqual(seen, ['first', 'next']);
    const stopping = stopNext();
    h.reply(h.delivered.at(-1), {});
    await stopping;
    h.message({ type: 'portal-projection-change' });
    assert.deepEqual(seen, ['first', 'next']);
    assert.equal(h.timers.size, 0);
  } finally { h.close(); }
});
