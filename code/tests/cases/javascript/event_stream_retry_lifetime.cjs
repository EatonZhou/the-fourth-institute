const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function fixture() {
  const timeouts = new Map(), intervals = new Map(), streams = [], events = [];
  let seq = 0, now = 100;
  class Stream extends EventTarget {
    static CLOSED = 2;
    constructor(url) {super(); this.url = url; this.readyState = 0; streams.push(this);}
    close() {this.readyState = 2; this.closed = true;}
    error(state) {this.readyState = state; this.onerror();}
    ready() {this.readyState = 1; this.dispatchEvent(new Event('portal-ready'));}
  }
  class DetailEvent extends Event {constructor(type, init) {super(type); this.detail = init.detail; this.data = '{}';}}
  const document = new EventTarget(); document.hidden = false;
  const window = new EventTarget();
  Object.assign(window, {document, top: window, location: {href: 'http://polling.test/'},
    PortalCompatibility: {identifier: String},
    setTimeout: fn => {timeouts.set(++seq, fn); return seq;}, clearTimeout: id => timeouts.delete(id),
    setInterval: fn => {intervals.set(++seq, fn); return seq;}, clearInterval: id => intervals.delete(id)});
  window.addEventListener('portal:events-error', e => events.push(e.detail));
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_events.js'), 'utf8'), {
    window, document, navigator: {}, URL, URLSearchParams, EventSource: Stream,
    CustomEvent: DetailEvent, Date: class extends Date {static now() {return now;}},
  });
  return {streams, events, service: window.PortalEvents,
    timeout() {const due = [...timeouts.values()]; timeouts.clear(); due.forEach(fn => fn());},
    stale() {now += 100_000; [...intervals.values()].forEach(fn => fn());}};
}

test('a permanently closed event transport is not reopened by the watchdog or subscription changes', () => {
  const f = fixture(); const stop = f.service.watchResident('resident_one'); f.timeout();
  const first = f.streams.at(-1), initialCount = f.streams.length;
  first.error(2); f.stale();
  const stop2 = f.service.watchResident('resident_one'); f.timeout(); f.stale();
  assert.equal(f.streams.length, initialCount);
  assert.equal(first.closed, true); assert.equal(f.events.at(-1).terminal, true);
  f.service.retry(); assert.equal(f.streams.length, initialCount + 1, 'explicit retry is still available');
  f.streams.at(-1).ready(); assert.equal(f.service.isConnected(), true); stop(); stop2(); f.service.close();
});

test('reconnecting and silent streams retain bounded recovery; explicit close never reopens', () => {
  const f = fixture(); f.streams[0].error(0);
  assert.equal(f.events.at(-1).terminal, false);
  assert.equal(f.streams.length, 1, 'browser reconnects the same transient stream');
  f.stale(); assert.equal(f.streams.length, 2, 'silent reconnecting stream can be replaced');
  f.service.close(); f.stale(); assert.equal(f.streams.length, 2);
});

test('a rejected replacement keeps the previous stream; changed targets can subscribe without retrying the rejected URL', () => {
  const f = fixture(); const first = f.streams[0]; first.ready();
  const stop = f.service.watchResident('resident_one'); f.timeout(); assert.equal(f.streams.length, 2);
  f.streams[1].error(2); f.timeout();
  const same = f.service.watchResident('resident_one'); f.timeout();
  assert.equal(f.streams.length, 2); assert.notEqual(first.closed, true);
  assert.equal(f.streams[1].closed, true); assert.equal(f.events.at(-1).terminal, true);
  const stop2 = f.service.watchResident('resident_two'); f.timeout();
  assert.equal(f.streams.length, 3, 'changed scope gets one fresh subscription attempt');
  assert.notEqual(first.closed, true, 'old stream delivers until the new one is ready');
  f.streams[2].ready(); assert.equal(first.closed, true);
  stop(); same(); stop2(); f.service.close();
});
