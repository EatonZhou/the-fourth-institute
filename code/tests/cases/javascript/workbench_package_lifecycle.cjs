'use strict';

const assert = require('node:assert/strict');
const { createHarness, plain } = require('./workbench_visibility_state.cjs');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function registeredPackageContract(options) {
  const harness = createHarness(options);
  const { window, root } = harness;
  const lifecycle = [], events = [], domEvents = [];
  let shows = 0, expectedShows = 1, closeDecision = deferred(), stored;
  root.addEventListener('portal:workbench-panel-event', event => domEvents.push(plain(event.detail)));
  try {
    window.PortalWorkbench.registerPanelPackage({
      type: 'lifecycle-fixture', version: 1,
      presentation: { title: 'Lifecycle', icon: 'component' },
      sizing: { minWidth: 180, minHeight: 140 }, instances: 'multiple',
      capabilities: ['lifecycle', 'snapshot', 'restore'],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
      context: { channels: ['scope'] }, persistence: { schemaVersion: 1 },
      create(options) {
        lifecycle.push(['create', options.panelId, options.context.context.scope]);
        options.root.textContent = 'factory panel';
        let count = 0;
        return {
          root: options.root,
          connect() { lifecycle.push(['connect']); options.emit('ready', { panelId: options.panelId }); },
          restore(snapshot) { count = snapshot.count; lifecycle.push(['restore', count]); },
          setContext(context) { lifecycle.push(['context', context.scope]); },
          snapshot() { return { count }; },
          change(value) { count = value; options.emit('persistence-change'); },
          canClose() { return closeDecision.promise; },
          show() { lifecycle.push(['show']); }, hide() { lifecycle.push(['hide']); },
          ping(value) { return `pong:${value}`; },
          disconnect() { lifecycle.push(['disconnect']); },
          destroy() { lifecycle.push(['destroy']); options.root.replaceChildren(); },
        };
      },
    });
    const snapshot = { packageType: 'lifecycle-fixture', packageVersion: 1, schemaVersion: 1, value: { count: 7 } };
    const initial = {
      schemaVersion: 2, panelIds: ['automatic'], openEditors: [], activeWorkspaceName: 'Lifecycle fixture',
      layout: window.PortalWorkbench.model.stack(['automatic']), hidden: [], collapsed: [],
      placements: {}, savedWorkspaces: {}, panelStates: { automatic: snapshot },
    };
    const wb = harness.create({
      id: 'lifecycle-workbench', context: { scope: 'initial' },
      initialEditors: [{ id: 'automatic', type: 'lifecycle-fixture', closable: true,
        onShow: ({ workbench, panel }) => {
          shows += 1;
          assert.ok(shows <= expectedShows, 'show callback must not recursively reenter');
          workbench.focusPanel(panel.id);
        } }],
      defaultLayout: window.PortalWorkbench.model.stack(['automatic']),
      store: { read: () => structuredClone(initial), replace: value => { stored = plain(value); return value; } },
      onPanelEvent: event => events.push(plain(event)),
    });
    const panel = root.querySelector('[data-portal-workbench-panel="automatic"]');
    assert.ok(panel instanceof window.HTMLElement, 'factory root is actual HTMLElement');
    assert.equal(panel.textContent, 'factory panel', 'factory content mounted');
    assert.equal(panel.isConnected, true, 'factory root connected');
    assert.equal(panel.parentElement.dataset.portalWorkbenchPanelFrame, 'automatic', 'real frame owns factory root');
    assert.deepEqual(lifecycle.slice(0, 5), [
      ['create', 'automatic', 'initial'], ['connect'], ['restore', 7], ['context', 'initial'], ['show'],
    ], 'package initialization order');
    assert.equal(shows, 1, 'show callback is not reentered on initial focus');
    const ready = { workbenchId: 'lifecycle-workbench', sourcePanelId: 'automatic', event: 'ready', detail: { panelId: 'automatic' } };
    assert.deepEqual(events[0], ready, 'callback receives production ready event');
    assert.deepEqual(domEvents[0], ready, 'actual DOM event receives ready identity');
    assert.equal(wb.invokePanel('automatic', 'ping', 'value'), 'pong:value', 'invokePanel routes to runtime');

    wb.setContext({ scope: 'updated' });
    wb.hidePanel('automatic');
    expectedShows = 2;
    wb.showPanel('automatic');
    assert.deepEqual(lifecycle.filter(item => item[0] === 'context').at(-1), ['context', 'updated'], 'context propagated');
    assert.deepEqual(stored.panelStates.automatic, snapshot, 'package snapshot envelope persisted');
    wb.invokePanel('automatic', 'change', 8);
    assert.equal(stored.panelStates.automatic.value.count, 8, 'runtime commits save without a layout change');
    assert.equal(shows, 2, 'show callback is not reentered after reopen');
    assert.equal(root.querySelector('[data-portal-workbench-panel="automatic"]'), panel, 'hide/show retains original root');

    const beforeClose = plain(wb.getState());
    const beforeLifecycle = structuredClone(lifecycle);
    const beforeDom = root.innerHTML;
    const rejected = wb.closePanel('automatic');
    assert.deepEqual(plain(wb.getState()), beforeClose, 'pending close preserves state');
    assert.equal(root.innerHTML, beforeDom, 'pending close preserves DOM');
    closeDecision.resolve(false);
    assert.equal(await rejected, false, 'async close veto respected');
    assert.deepEqual(plain(wb.getState()), beforeClose, 'denied close preserves state');
    assert.deepEqual(lifecycle, beforeLifecycle, 'denied close does not run exit lifecycle');
    assert.equal(root.innerHTML, beforeDom, 'denied close preserves DOM');

    closeDecision = deferred();
    const failure = new Error('fixture close rejection');
    const failed = wb.closePanel('automatic');
    const checked = assert.rejects(failed, error => error === failure, 'rejected canClose propagates');
    closeDecision.reject(failure);
    await checked;
    assert.deepEqual(plain(wb.getState()), beforeClose, 'rejected close preserves state');
    assert.deepEqual(lifecycle, beforeLifecycle, 'rejected close does not run exit lifecycle');
    assert.equal(root.innerHTML, beforeDom, 'rejected close preserves DOM');

    closeDecision = deferred();
    const allowed = wb.closePanel('automatic');
    closeDecision.resolve(true);
    assert.equal(await allowed, true, 'approved close succeeds');
    assert.deepEqual(lifecycle.slice(-3), [['hide'], ['disconnect'], ['destroy']], 'exit lifecycle order');
    assert.equal(root.querySelectorAll('[data-portal-workbench-panel="automatic"]').length, 0, 'closed root removed from DOM');
    assert.equal(panel.isConnected, false, 'closed root disconnected');
    assert.equal(panel.parentNode, null, 'closed root explicitly removed');
    assert.equal(panel.textContent, '', 'runtime destroy executed');
    wb.destroy();
    assert.equal(window.PortalWorkbench.get('lifecycle-workbench'), null, 'destroy unregisters controller');
    await harness.settle();
  } finally { await harness.cleanup(); }
}

module.exports = { registeredPackageContract };
if (require.main === module) {
  assert.equal(process.argv[2], 'registered-package', 'explicit independent contract required');
  registeredPackageContract().then(() => console.log('PASS test_ebd70025ddb6 registered-package; DOM cleanup complete'))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
