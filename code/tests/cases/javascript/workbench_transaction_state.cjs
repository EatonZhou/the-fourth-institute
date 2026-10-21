'use strict';
const assert = require('node:assert/strict');
const { createHarness, plain } = require('./workbench_visibility_state.cjs');

async function atomicFailure(options) {
  const h = createHarness(options), w = h.window, root = h.root;
  try {
    root.innerHTML = '<section id="panelNavigation">Tree</section><section id="panelCanvas">Graph</section><section id="panelPreview">Document</section>';
    w.PortalWorkbench.registerPanelPackage({
      type: 'transaction-fixture', version: 1, presentation: { title: 'Fixture', icon: 'panel' },
      sizing: { minWidth: 180, minHeight: 140 }, instances: 'multiple', capabilities: [],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
      context: { channels: ['*'] }, persistence: { schemaVersion: 1 },
      create: options => w.document.querySelector(options.source),
    });
    const { split, stack } = w.PortalWorkbench.model;
    let stored = {};
    const wb = h.create({ id: 'fixture', initialEditors: [
      { id: 'navigation', type: 'transaction-fixture', options: { source: '#panelNavigation' } },
      { id: 'canvas', type: 'transaction-fixture', options: { source: '#panelCanvas' }, required: true },
      { id: 'preview', type: 'transaction-fixture', options: { source: '#panelPreview' } },
    ], defaultLayout: split('horizontal', [stack(['navigation']), stack(['canvas']), stack(['preview'])], [1, 2, 1]),
    store: { read: () => structuredClone(stored), replace: value => { stored = plain(value); return value; } } });
    const before = plain(wb.getState().layout), roots = [...wb.descriptors.values()].map(item => item.element);
    const descriptor = Object.getOwnPropertyDescriptor(w.Element.prototype, 'moveBefore');
    // The browser regression injects this same failed platform operation.
    Object.defineProperty(w.Element.prototype, 'moveBefore', { configurable: true, writable: true, value() {} });
    let applied;
    try {
      applied = wb.applyWorkspace({ layout: split('horizontal', [stack(['preview']), stack(['canvas']), stack(['navigation'])], [1, 2, 1]), hidden: ['preview'] });
    } finally {
      if (descriptor) Object.defineProperty(w.Element.prototype, 'moveBefore', descriptor);
      else delete w.Element.prototype.moveBefore;
    }
    assert.deepEqual(h.errors.splice(0), [
      'PortalWorkbench fixture preserved the previous layout because a Panel could not be moved atomically.',
    ], 'only the expected failed-move diagnostic is emitted');
    assert.equal(applied, false, 'failed atomic move rejects layout commit');
    assert.deepEqual(plain(wb.getState().layout), before, 'failed atomic move preserves layout');
    assert.equal(wb.isPanelVisible('preview'), true, 'failed commit restores visibility state');
    assert.equal(w.document.getElementById('panelPreview').hidden, false, 'failed commit restores DOM hidden');
    for (const [panelId, elementId, content] of [['navigation', 'panelNavigation', 'Tree'], ['canvas', 'panelCanvas', 'Graph'], ['preview', 'panelPreview', 'Document']]) {
      const frame = root.querySelector(`[data-portal-workbench-panel-frame="${panelId}"]`), element = w.document.getElementById(elementId);
      assert.equal(element.parentElement, frame, 'failed commit preserves actual DOM ownership');
      assert.ok(element.textContent.includes(content), 'failed commit preserves content');
      assert.ok(roots.includes(element) && element.isConnected, 'original roots remain connected');
    }
    await h.settle();
  } finally { await h.cleanup(); }
}
module.exports = { atomicFailure };
if (require.main === module) {
  assert.equal(process.argv[2], 'atomic-failure');
  atomicFailure().then(() => console.log('PASS atomic-failure')).catch(error => { console.error(error); process.exitCode = 1; });
}
