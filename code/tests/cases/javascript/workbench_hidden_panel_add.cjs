'use strict';

// A panel added with { hidden: true } joins the hidden rail without touching the layout,
// so a page can add a permitted Terminal after /api/me answers, even with the layout fixed.
const assert = require('node:assert/strict');
const { createHarness, plain } = require('./workbench_visibility_state.cjs');

async function main() {
  const harness = createHarness();
  const { window, root } = harness;
  try {
    root.innerHTML = '<section id="panelChat">Chat</section><section id="panelTerminal">Shell</section>';
    window.PortalWorkbench.registerPanelPackage({
      type: 'fixture-hidden', version: 1,
      presentation: { title: 'Fixture', icon: 'panel' },
      sizing: { minWidth: 180, minHeight: 140 }, instances: 'multiple', capabilities: [],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
      context: { channels: ['*'] }, persistence: { schemaVersion: 1 },
      create: options => window.document.querySelector(options.source),
    });
    const { stack } = window.PortalWorkbench.model;
    let stored = {};
    const wb = harness.create({
      id: 'hidden-add', label: '终端加入',
      initialEditors: [{ id: 'chat', type: 'fixture-hidden', title: 'Chat', options: { source: '#panelChat' }, required: true }],
      defaultLayout: stack(['chat']),
      store: { read: () => structuredClone(stored), replace: value => { stored = plain(value); return value; } },
    });
    wb.setLayoutLocked(true);
    const before = plain(wb.getLayout());
    const descriptor = { id: 'terminal', type: 'fixture-hidden', title: 'Terminal', options: { source: '#panelTerminal' } };
    assert.throws(() => wb.addPanel({ ...descriptor, id: 'visible' }), /布局已固定/, 'a visible add still needs an unlocked layout');
    wb.addPanel(descriptor, { hidden: true });
    await harness.settle();
    assert.deepEqual(plain(wb.getLayout()), before, 'a hidden add leaves the fixed layout as it is');
    assert.ok(wb.getState().hidden.includes('terminal'), 'the panel waits in the hidden set');
    assert.equal(window.document.querySelectorAll('[data-workbench-restore-panel="terminal"]').length, 1, 'the hidden rail offers it');
    wb.setLayoutLocked(false);
    assert.notEqual(wb.showPanel('terminal'), false, 'it can be shown like any hidden panel');
    await harness.settle();
    assert.equal(wb.getState().hidden.includes('terminal'), false, 'shown panels leave the hidden set');
  } finally { await harness.cleanup(); }
}

main().catch(error => { console.error(error); process.exit(1); });
