'use strict';

// On a mouse device the navigation panel is unpinned by default: it stays
// collapsed to the activity rail and slides out while the mouse is on a rail
// view or on the panel. The pin between the rail's two rules docks it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const ANSWERS = {
  '/api/me': { username: 'fixture' },
  '/lineage/api/service-status': { ready: true, state: 'ready' },
  '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { user_residents: [], projects: [
    { project_id: 'alpha', title: 'Alpha', lineage_id: 'lineage_aaaaaaaaaaaa', lineages: [] },
  ] } },
};

test('the navigation slides out on rail hover and the pin docks it', async t => {
  const page = startWorkbenchPage({ answers: ANSWERS, hover: true });
  t.after(page.close);
  const { document: doc, window: w } = page;
  const layout = page.parts.PortalLineageWorkbenchLayout;
  const state = layout.dependencies.state;
  await page.until(() => state.loadAllComplete && doc.querySelector('#lineageActivityRail [data-activity-pin]'));
  const workbench = layout.dependencies.currentLineageWorkbench();
  const stack = () => doc.querySelector('[data-portal-workbench-panel="navigation"]').closest('.portalWorkbenchStack');
  const view = id => doc.querySelector(`#lineageActivityRail [data-activity-view="${id}"]`);
  const pointer = (target, type) => target.dispatchEvent(Object.assign(new w.Event(type, { bubbles: type === 'pointermove' }), { pointerType: 'mouse' }));
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  assert.equal(workbench.isPanelExpanded('navigation'), false, 'unpinned by default: the panel starts collapsed to the rail');
  assert.ok(stack().hasAttribute('data-collapsed-to-rail'));
  assert.equal(doc.querySelector('[data-activity-pin]').getAttribute('aria-pressed'), 'false');

  pointer(view('favorites'), 'pointerenter');
  pointer(view('favorites'), 'pointerleave');
  await wait(180);
  assert.equal(workbench.panelFlyout(), '', 'passing over a view does not slide it out');

  pointer(view('favorites'), 'pointerenter');
  await page.until(() => workbench.panelFlyout() === 'navigation');
  assert.equal(state.sideView, 'favorites', 'the hovered view is shown');
  assert.ok(stack().hasAttribute('data-flyout-open'));
  assert.equal(workbench.isPanelCollapsed('navigation'), true, 'the layout is unchanged');
  assert.equal(view('favorites').classList.contains('active'), true, 'the rail marks the view that is out');

  pointer(view('agents'), 'pointerenter');
  assert.equal(state.sideView, 'agents', 'another view switches at once while the panel is out');

  pointer(stack(), 'pointermove');
  await wait(320);
  assert.equal(workbench.panelFlyout(), 'navigation', 'the panel stays out under the mouse');
  pointer(doc.querySelector('.portalWorkbenchStack:not([data-flyout-open])') || doc.body, 'pointermove');
  await page.until(() => workbench.panelFlyout() === '');
  assert.equal(stack().hasAttribute('data-flyout-open'), false, 'leaving slides it back');

  // "/" slides it out and focuses the search; typing holds it out.
  doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: '/', bubbles: true }));
  await page.until(() => workbench.panelFlyout() === 'navigation');
  assert.equal(doc.activeElement, doc.getElementById('searchInput'));
  pointer(doc.body, 'pointermove');
  await wait(320);
  assert.equal(workbench.panelFlyout(), 'navigation', 'a focused search keeps it out');
  doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(workbench.panelFlyout(), '', 'Escape slides it back');

  doc.querySelector('[data-activity-pin]').click();
  assert.equal(layout.dependencies.readLineageUiState().navigationPinned, true, 'the pin is remembered');
  assert.equal(workbench.isPanelExpanded('navigation'), true, 'pinned, the panel is docked');
  pointer(view('favorites'), 'pointerenter');
  await wait(180);
  assert.equal(workbench.panelFlyout(), '', 'a pinned panel never slides out');
  view('agents').click();
  assert.equal(workbench.isPanelExpanded('navigation'), false, 'pinned, clicking the open view closes the docked panel');
  view('favorites').click();
  assert.equal(workbench.isPanelExpanded('navigation'), true, 'pinned, clicking a view docks it open');
  assert.equal(state.sideView, 'favorites');

  doc.querySelector('[data-activity-pin]').click();
  assert.equal(layout.dependencies.readLineageUiState().navigationPinned, false);
  assert.equal(workbench.isPanelExpanded('navigation'), false, 'unpinned, the panel returns to the rail');
  assert.deepEqual(page.errors, []);
});
