'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {createHarness, plain} = require('./workbench_visibility_state.cjs');

test('same-tab clicks and panel pointer focus persist the next Open target across reload', async () => {
  const harness = createHarness();
  const {window, root} = harness;
  let stored = {}, writes = 0;
  try {
    window.PortalWorkbench.registerPanelPackage({
      type: 'target-fixture', version: 1, instances: 'multiple', capabilities: [],
      presentation: {title: 'Target', icon: 'panel'}, sizing: {minWidth: 100, minHeight: 100},
      behavior: {canHide: true, canCollapse: true, canMove: true, closable: true},
      context: {channels: ['*']}, persistence: {schemaVersion: 1},
      create: () => window.document.createElement('textarea'),
    });
    const {split, stack} = window.PortalWorkbench.model;
    const options = {
      id: 'targetFixture', label: 'Target fixture',
      initialEditors: ['chat', 'preview'].map(id => ({id, type: 'target-fixture', title: id})),
      defaultLayout: split('horizontal', [stack(['chat']), stack(['preview'])]),
      store: {read: () => structuredClone(stored), replace: value => {writes++; stored = plain(value);}},
    };
    let wb = harness.create(options);
    await harness.settle();
    wb.setActiveOpenTarget('preview');
    const chatTab = [...root.querySelectorAll('[role="tab"]')].find(tab => tab.textContent.includes('chat'));
    assert.ok(chatTab);
    chatTab.click();
    assert.equal(stored.activeOpenTargetPanelId, 'chat', 'already active tab saves its target');
    wb.setActiveOpenTarget('preview');
    const chatStack = chatTab.closest('[data-portal-workbench-stack]');
    chatStack.dispatchEvent(new window.Event('pointerdown', {bubbles: true}));
    assert.equal(stored.activeOpenTargetPanelId, 'chat', 'click inside another stack saves its target');
    const firstWrites = writes;
    chatStack.dispatchEvent(new window.Event('pointerdown', {bubbles: true}));
    assert.equal(writes, firstWrites, 'unchanged pointer target does not rewrite the layout');
    wb.destroy();
    wb = harness.create(options);
    await harness.settle();
    assert.equal(wb.getActiveOpenTarget(), 'chat', 'new controller restores the selected Open target');
  } finally { await harness.cleanup(); }
});
