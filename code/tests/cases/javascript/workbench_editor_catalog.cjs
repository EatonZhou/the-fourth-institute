'use strict';
const assert = require('node:assert/strict');
const { createHarness } = require('./workbench_visibility_state.cjs');

(async () => {
  const h = createHarness();
  try {
    const w = h.window, d = w.document;
    w.PortalWorkbench.registerPanelPackage({
      type: 'catalog-fixture', version: 1,
      presentation: { title: 'Catalog editor', icon: 'panel' },
      sizing: { minWidth: 180, minHeight: 140 }, instances: 'multiple', capabilities: [],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
      context: { channels: [] }, persistence: { schemaVersion: 1 },
      create: () => { const node = d.createElement('section'); node.textContent = 'Editor ready'; return node; },
    });
    const wb = h.create({
      id: 'editor-catalog', label: 'Editor catalog',
      initialEditors: [{ id: 'initial', type: 'catalog-fixture', title: 'Initial' }],
      defaultLayout: w.PortalWorkbench.model.stack(['initial']),
      editorTypes: [{ type: 'catalog-fixture', title: 'Previous catalog title' }],
      createEditorDescriptor: ({ instanceId, type }) => ({ id: instanceId, type }),
    });
    wb.setLayoutLocked(false);
    d.querySelector('.portalWorkbenchCustomizeButton').click();
    d.querySelector('.portalWorkbenchEditorOptions').open = true;
    // Public controller input used by the asynchronous projection catalog.
    // This is a DOM contract, not an invented UI refresh button.
    wb.setEditorTypes([{ type: 'catalog-fixture', title: 'Updated catalog title' }]);
    const menu = d.querySelector('.portalWorkbenchEditorOptions');
    assert.equal(menu.open, true, 'catalog update preserves expanded creation menu');
    assert.match(menu.textContent, /Updated catalog title/);
    assert.doesNotMatch(menu.textContent, /Previous catalog title/);
    menu.querySelector('[data-workbench-create-editor][data-open-mode="new-tab"]').click();
    await h.settle();
    assert.equal(wb.descriptors.size, 2, 'updated catalog entry creates an actual editor');
    assert.equal(d.querySelector('.portalWorkbenchCustomizeButton').getAttribute('aria-expanded'), 'false');
    assert.equal([...wb.descriptors.values()].every(item => item.element.textContent.includes('Editor ready')), true);
    console.log('PASS catalog update retains expanded menu and opens current editor');
  } finally { await h.cleanup(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
