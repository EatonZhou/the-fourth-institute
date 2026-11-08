'use strict';

// A panel declared collapseToRail leaves no collapsed strip: the page's
// activity rail restores it. Other collapsed panels keep their strip.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./workbench_visibility_state.cjs');

test('a rail-restored panel collapses to nothing, others keep their strip', async () => {
  const harness = createHarness();
  const { window, root } = harness;
  try {
    root.innerHTML = '<section id="panelNavigation">Tree</section>'
      + '<section id="panelCanvas">Graph</section><section id="panelPreview">Document</section>';
    window.PortalWorkbench.registerPanelPackage({
      type: 'fixture-authored', version: 1,
      presentation: { title: 'Fixture', icon: 'panel' },
      sizing: { minWidth: 180, minHeight: 140 }, instances: 'multiple', capabilities: [],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
      context: { channels: ['*'] }, persistence: { schemaVersion: 1 },
      create: options => window.document.querySelector(options.source),
    });
    const { split, stack } = window.PortalWorkbench.model;
    const wb = harness.create({
      id: 'fixture', label: '编排工作区',
      initialEditors: [
        { id: 'navigation', type: 'fixture-authored', title: 'Navigation', options: { source: '#panelNavigation' }, collapseToRail: true },
        { id: 'canvas', type: 'fixture-authored', title: 'Canvas', options: { source: '#panelCanvas' }, required: true },
        { id: 'preview', type: 'fixture-authored', title: 'Preview', options: { source: '#panelPreview' } },
      ],
      defaultLayout: split('horizontal', [stack(['navigation']), stack(['canvas']), stack(['preview'])], [1, 2, 1]),
      store: { read: () => ({}), replace: value => value },
    });
    const stackOf = panelId => window.document.querySelector(`[data-portal-workbench-panel="${panelId}"]`)
      .closest('.portalWorkbenchStack');

    assert.equal(wb.collapsePanel('navigation'), true);
    await harness.settle();
    assert.ok(stackOf('navigation').hasAttribute('data-collapsed-to-rail'), 'the navigation stack is marked for the rail');
    assert.equal(stackOf('navigation').style.flexBasis, '0px', 'it takes no room');

    assert.equal(wb.collapsePanel('preview'), true);
    await harness.settle();
    assert.equal(stackOf('preview').hasAttribute('data-collapsed-to-rail'), false, 'an ordinary panel keeps its strip');
    assert.equal(stackOf('preview').style.flexBasis, '44px');

    assert.equal(wb.expandPanel('navigation'), true);
    await harness.settle();
    assert.equal(stackOf('navigation').hasAttribute('data-collapsed-to-rail'), false, 'expanding restores the panel');
    assert.equal(wb.isPanelExpanded('navigation'), true);
  } finally { await harness.cleanup(); }
});
