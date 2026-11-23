'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createHarness } = require('./workbench_visibility_state.cjs');

function setup() {
  const h = createHarness(), w = h.window;
  const handle = w.document.createElement('button');
  h.root.append(handle);
  let captured = null;
  handle.setPointerCapture = id => { captured = id; };
  handle.hasPointerCapture = id => captured === id;
  handle.releasePointerCapture = () => { captured = null; };
  const moves = [], ends = [];
  const unbind = w.PortalWorkbench.layout.bindPointerResize({ handle,
    readStart: () => 100, onMove: value => moves.push(value.delta), onDone: value => ends.push(value),
  });
  function fire(target, type, extra = {}) {
    const e = new w.Event(type, { bubbles: true, cancelable: true });
    Object.assign(e, { pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1, clientX: 20, ...extra });
    target.dispatchEvent(e);
  }
  const start = () => fire(handle, 'pointerdown');
  const clean = () => {
    assert.equal(w.document.body.classList.contains('resizingWorkbenchPane'), false, 'text selection is released');
    assert.equal(handle.classList.contains('active'), false);
    assert.equal(captured, null);
  };
  return { ...h, w, handle, start, fire, clean, moves, ends, unbind };
}

test('resize interruption releases selection and the next drag works', async () => {
  const h = setup();
  try {
    for (const reason of ['lostpointercapture', 'blur', 'pagehide', 'escape', 'pointercancel', 'outside-up', 'buttons-released']) {
      h.start(); h.fire(h.handle, 'pointermove', { clientX: 30 });
      const before = h.ends.length;
      if (reason === 'escape') h.w.dispatchEvent(new h.w.KeyboardEvent('keydown', { key: 'Escape' }));
      else if (reason === 'outside-up') h.fire(h.w.document.body, 'pointerup');
      else if (reason === 'buttons-released') h.fire(h.handle, 'pointermove', { buttons: 0 });
      else if (['blur', 'pagehide'].includes(reason)) h.w.dispatchEvent(new h.w.Event(reason));
      else h.fire(h.handle, reason);
      h.clean();
      h.fire(h.handle, 'pointerup');
      assert.equal(h.ends.length, before + 1, `${reason} finishes exactly once`);
      h.start(); h.fire(h.handle, 'pointermove', { clientX: 45 }); h.fire(h.handle, 'pointerup');
      h.clean(); assert.equal(h.moves.at(-1), 25, 'next resize receives movement');
    }
  } finally { h.unbind(); await h.cleanup(); }
});

test('removed handle, unbind and failed capture cannot leave a resize active', async () => {
  const h = setup();
  try {
    h.start(); h.handle.remove(); await Promise.resolve(); h.clean();
    h.root.append(h.handle); h.start(); h.unbind(); h.clean();
    assert.equal(h.ends.length, 2);
    h.start(); assert.equal(h.handle.classList.contains('active'), false, 'unbound handle does not start a drag');
  } finally { h.unbind(); await h.cleanup(); }
  const broken = setup();
  try {
    broken.handle.setPointerCapture = () => { throw new Error('capture unavailable'); };
    broken.start(); broken.clean();
    assert.deepEqual(broken.errors, [], 'capture loss does not escape as an uncaught exception');
  } finally { broken.unbind(); await broken.cleanup(); }
});

test('only the initiating pointer can move or finish the resize', async () => {
  const h = setup();
  try {
    h.start();
    h.fire(h.handle, 'pointermove', { pointerId: 2, clientX: 70 });
    h.fire(h.handle, 'pointerup', { pointerId: 2 });
    assert.deepEqual(h.moves, []); assert.equal(h.ends.length, 0);
    h.fire(h.handle, 'pointermove', { clientX: 38 }); h.fire(h.handle, 'pointerup');
    h.clean(); assert.deepEqual(h.moves, [18]); assert.equal(h.ends.length, 1);
  } finally { h.unbind(); await h.cleanup(); }
});
