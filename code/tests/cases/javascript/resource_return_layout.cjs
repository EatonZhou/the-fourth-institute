const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_resource_detail.js'), 'utf8');
function harness() {
  const frames = [], calls = [], listeners = new Map();
  const main = {}, root = {scrollHeight: 900, clientHeight: 900, scrollWidth: 100, clientWidth: 100};
  let resized, disconnected = false;
  const window = {
    location: {href: 'http://portal/resource/knowledge', pathname: '/resource/knowledge'},
    history: {state: {portalResourceDetailReturn: {owner: 'board', x: 0, y: 50}}},
    document: {scrollingElement: root, documentElement: {}, body: {}, querySelector: () => main},
    PortalResourceRoutes: {routes: []},
    requestAnimationFrame: fn => frames.push(fn), setTimeout: () => 1, clearTimeout() {},
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
    scrollTo: value => calls.push(value),
    ResizeObserver: class {constructor(fn) {resized = fn;} observe() {} disconnect() {disconnected = true;}},
  };
  vm.runInNewContext(source, {window, URL});
  const history = window.PortalResourceDetail.createDetailHistory({owner: 'board'});
  return {history, root, calls, listeners, resize: () => resized(), frames,
    disconnected: () => disconnected};
}
test('saved return waits for collection layout instead of clamping to zero', () => {
  const h = harness(); h.history.restoreReturnPosition();
  while (h.frames.length) h.frames.shift()();
  assert.equal(h.calls.length, 0);
  h.root.scrollHeight = 1012; h.resize();
  assert.equal(h.calls[0].top, 50);
  assert.ok(h.disconnected());
});
test('user interaction cancels pending history restoration', () => {
  const h = harness(); h.history.restoreReturnPosition(); h.listeners.get('wheel')();
  h.root.scrollHeight = 1012; h.resize();
  while (h.frames.length) h.frames.shift()();
  assert.equal(h.calls.length, 0); assert.ok(h.disconnected());
});
