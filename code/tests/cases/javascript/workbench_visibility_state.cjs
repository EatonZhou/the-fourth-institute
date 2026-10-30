'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const { babelParse } = require('./babel_parse.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');

const repository = path.resolve(__dirname, '../../..');
const scripts = [
  'portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js',
  'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(path.join(repository, 'management_portal/static')),
];
const plain = value => JSON.parse(JSON.stringify(value));

// Shared environment only. Importing this module never runs a contract.
// No geometry, render, selector, visibility, or DOM-tree methods are replaced.
function createHarness({ sourceTransform = (_name, source) => source } = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(String(error.stack || error)));
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body>'
    + '<header><div class="portalNavTop">Portal</div></header>'
    + '<main id="workbenchFixture"></main></body></html>', {
    url: 'https://workbench.fixture.invalid/', runScripts: 'outside-only',
    pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  const observers = new Set();
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  // jsdom has no media-query evaluator. Supply only the desktop viewport and
  // motion-preference inputs; production PortalResponsive still owns its state
  // and subscriptions. This does not replace element geometry or visibility.
  window.matchMedia = query => {
    const media = new window.EventTarget();
    const maxWidth = /^\(max-width: (\d+)px\)$/.exec(query);
    assert.ok(maxWidth || query === '(prefers-reduced-motion: reduce)', 'known fixture media input');
    Object.defineProperties(media, {
      media: { value: query },
      matches: { get: () => maxWidth ? window.innerWidth <= Number(maxWidth[1]) : false },
    });
    return media;
  };
  // These fixtures use only simple CSS identifiers. Reject anything needing an
  // escape instead of implementing another escaping algorithm in the fixture.
  window.CSS = { escape(value) {
    assert.match(String(value), /^[a-zA-Z_][a-zA-Z0-9_-]*$/, 'fixture CSS identifier');
    return String(value);
  } };
  const controllers = [];
  const pendingFrames = new Set();
  const requestFrame = window.requestAnimationFrame.bind(window);
  const cancelFrame = window.cancelAnimationFrame.bind(window);
  window.requestAnimationFrame = callback => {
    const id = requestFrame(time => { pendingFrames.delete(id); callback(time); });
    pendingFrames.add(id);
    return id;
  };
  window.cancelAnimationFrame = id => { pendingFrames.delete(id); cancelFrame(id); };
  try {
    const identitySource = fs.readFileSync(path.join(repository, 'management_portal/static/portal_state.js'), 'utf8');
    const identityModule = babelParse(identitySource, 'portal_state.js').program.body[0];
    window.eval(identitySource.slice(identityModule.start, identityModule.end));
    for (const name of scripts) {
      const source = fs.readFileSync(path.join(repository, 'management_portal/static', name), 'utf8');
      window.eval(sourceTransform(name, source) + `\n//# sourceURL=${name}`);
    }
  } catch (error) {
    window.close();
    throw error;
  }
  const root = window.document.getElementById('workbenchFixture');
  return {
    window, root, errors,
    create(options) {
      const controller = window.PortalWorkbench.create({ root, ...options });
      controllers.push(controller);
      return controller;
    },
    async settle() {
      await new Promise(resolve => requestFrame(resolve));
      await Promise.resolve();
      assert.deepEqual(errors, [], 'production render and callbacks must not log errors');
    },
    async cleanup() {
      try {
        for (const controller of controllers.reverse()) {
          controller.destroy();
          assert.equal(window.PortalWorkbench.get(controller.id), null, 'instance registry cleaned');
        }
        if (controllers.length) {
          assert.equal(root.hasAttribute('data-portal-workbench-id'), false, 'root ownership cleaned');
        }
      } finally {
        for (const id of pendingFrames) cancelFrame(id);
        pendingFrames.clear();
        // Let the real enhancer process destroy mutations before tearing down
        // its document. Observers are native instances, tracked only for cleanup.
        await new Promise(resolve => setImmediate(resolve));
        for (const observer of observers) observer.disconnect();
        observers.clear();
        // jsdom.close removes listeners, descendants and all window timers.
        window.close();
      }
      assert.equal(window.document, undefined, 'DOM window closed');
      assert.equal(pendingFrames.size, 0, 'fixture animation frames cleaned');
      assert.deepEqual(errors, [], 'cleanup must not log production errors');
    },
  };
}

async function fixedCollapsedContract(options) {
  const harness = createHarness(options);
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
    let stored = {};
    const wb = harness.create({
      id: 'fixture', label: '编排工作区',
      initialEditors: [
        { id: 'navigation', type: 'fixture-authored', title: 'Navigation', options: { source: '#panelNavigation' } },
        { id: 'canvas', type: 'fixture-authored', title: 'Canvas', options: { source: '#panelCanvas' }, required: true },
        { id: 'preview', type: 'fixture-authored', title: 'Preview', options: { source: '#panelPreview' }, inset: 'comfortable' },
      ],
      defaultLayout: split('horizontal', [stack(['navigation']), stack(['canvas']), stack(['preview'])], [1, 2, 1]),
      store: { read: () => structuredClone(stored), replace: value => { stored = plain(value); return value; } },
    });
    wb.applyWorkspace({ layout: split('horizontal', [stack(['navigation', 'preview'], 'navigation'), stack(['canvas'])]) });
    assert.equal(wb.collapsePanel('navigation'), true, 'navigation can collapse before lock');
    wb.setLayoutLocked(true);
    const selects = window.document.querySelectorAll('select[aria-label="切换固定面板"]');
    assert.equal(selects.length, 1, 'production creates the fixed-group selector');
    const select = selects[0];
    assert.ok(select instanceof window.HTMLSelectElement, 'actual HTMLSelectElement');
    assert.equal(select.isConnected, true, 'selector connected to actual document');
    assert.equal(select.disabled, true, 'fixed collapsed selector disabled');
    assert.equal(select.matches(':disabled'), true, 'native DOM disabled semantics');
    assert.deepEqual([...select.options].map(option => option.value), ['navigation', 'preview']);
    assert.equal(select.options[0].disabled, true, 'collapsed option disabled');
    const before = plain(wb.getLayout());
    assert.equal(wb.focusPanel('preview'), false, 'fixed collapsed focus is rejected');
    assert.deepEqual(plain(wb.getLayout()), before, 'focus keeps layout');
    assert.equal(wb.showPanel('preview'), false, 'fixed collapsed show is rejected');
    assert.deepEqual(plain(wb.getLayout()), before, 'show keeps layout');
    // Even an explicitly dispatched change cannot bypass the production guard.
    // This is not a claim about physical browser input on a disabled control.
    select.value = 'preview';
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
    assert.deepEqual(plain(wb.getLayout()), before, 'change binding keeps collapsed layout');
    await harness.settle();
  } finally { await harness.cleanup(); }
}

module.exports = { createHarness, fixedCollapsedContract, plain };
if (require.main === module) {
  assert.equal(process.argv[2], 'fixed-collapsed', 'explicit independent contract required');
  fixedCollapsedContract().then(() => console.log('PASS test_0d78eceb488e fixed-collapsed; DOM cleanup complete'))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
