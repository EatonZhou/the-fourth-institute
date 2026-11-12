'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createHarness, plain } = require('./workbench_visibility_state.cjs');

function setup() {
  const baseline = process.env.WORKBENCH_DRAG_BASELINE_DIR;
  const h = createHarness({ sourceTransform: (name, source) => baseline && ['portal_workbench.js', 'portal_workbench_tab_drag.js'].includes(name)
    ? fs.readFileSync(path.join(baseline, name), 'utf8') : source }), w = h.window;
  h.root.innerHTML = '<section id="nested"></section>';
  w.PortalWorkbench.registerPanelPackage({
    type: 'fixture-drag', version: 1, presentation: { title: 'Fixture', icon: 'panel' },
    sizing: { minWidth: 100, minHeight: 100 }, instances: 'multiple', capabilities: [],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
    context: { channels: ['*'] }, persistence: { schemaVersion: 1 },
    create: options => options.source ? w.document.querySelector(options.source) : w.document.createElement('section'),
  });
  const { stack, split } = w.PortalWorkbench.model;
  const store = () => ({ read: () => ({}), replace: value => value });
  const outer = h.create({ id: 'outer', initialEditors: [{ id: 'nested', type: 'fixture-drag', options: { source: '#nested' } }],
    defaultLayout: stack(['nested']), store: store() });
  const innerRoot = w.document.querySelector('#nested');
  const inner = h.create({ root: innerRoot, id: 'inner', initialEditors: ['a', 'b'].map(id => ({ id, type: 'fixture-drag' })),
    defaultLayout: split('vertical', [stack(['a']), stack(['b'])]), store: store() });
  let values = {};
  const transfer = { types: ['application/x-portal-workbench-panel'], setData: (key, value) => { values[key] = value; }, getData: key => values[key] || '' };
  function fire(target, type, extra = {}) {
    const event = new w.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperties(event, Object.fromEntries(Object.entries({ dataTransfer: transfer, clientX: 0, clientY: 0, relatedTarget: null, ...extra }).map(([k, value]) => [k, { value }])));
    target.dispatchEvent(event);
    return event;
  }
  const tab = id => innerRoot.querySelector(`[data-portal-workbench-tab="${id}"]`);
  const target = () => tab('b').closest('.portalWorkbenchStack');
  const overlays = () => w.document.querySelectorAll('[data-portal-workbench-drop-zone]');
  return { ...h, w, inner, outer, innerRoot, fire, tab, target, overlays,
    start() { fire(tab('a'), 'dragstart'); },
    clean() { assert.equal(overlays().length, 0, 'no drop preview remains'); assert.equal(w.document.body.classList.contains('draggingWorkbenchTab'), false, 'drag mode ends'); },
  };
}

test('nested drag has one owning preview and never moves the outer layout', async () => {
  const h = setup();
  try {
    const before = plain(h.outer.getLayout());
    h.start(); h.fire(h.target(), 'dragover');
    assert.equal(h.overlays().length, 1, 'inner drag must not paint outer stack');
    assert.equal(h.overlays()[0], h.target());
    h.fire(h.target(), 'drop');
    h.clean();
    assert.deepEqual(plain(h.outer.getLayout()), before);
    await h.settle();
  } finally { await h.cleanup(); }
});

test('drop cleans before render replaces the dragged tab, without relying on dragend', async () => {
  const h = setup();
  try {
    const source = h.tab('a');
    h.start();
    const tabs = h.target().querySelector('.portalWorkbenchTabs');
    h.fire(tabs, 'dragover'); h.fire(tabs, 'drop');
    assert.equal(source.isConnected, false, 'real layout render replaces the source tab');
    h.clean();
    assert.equal(h.tab('a').closest('.portalWorkbenchStack'), h.tab('b').closest('.portalWorkbenchStack'));
    assert.equal(h.inner.getState().hidden.length, 0);
    await h.settle();
  } finally { await h.cleanup(); }
});

test('cancellation and owner destruction release drag state; next drag still works', async () => {
  const h = setup();
  try {
    const before = plain(h.inner.getLayout());
    for (const reason of ['escape', 'blur', 'dragend', 'outside-drop', 'pagehide']) {
      h.start(); h.fire(h.target(), 'dragover');
      if (reason === 'escape') h.w.document.dispatchEvent(new h.w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      else if (reason === 'outside-drop') h.fire(h.w.document.body, 'drop');
      else if (reason === 'dragend') h.fire(h.tab('a'), 'dragend');
      else h.w.dispatchEvent(new h.w.Event(reason));
      h.clean(); assert.deepEqual(plain(h.inner.getLayout()), before);
    }
    h.start(); h.fire(h.target(), 'dragover'); h.inner.destroy(); h.clean();
  } finally { await h.cleanup(); }
});
