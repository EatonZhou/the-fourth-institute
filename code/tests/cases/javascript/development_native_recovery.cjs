'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHarness} = require('./workbench_visibility_state.cjs');
const code = path.resolve(__dirname, '../../..');
const evaluate = (window, file) => window.eval(fs.readFileSync(path.join(code, file), 'utf8'));
function storageFixture(window) {
  window.PortalState = {storage: {
    readJson: (key, fallback) => JSON.parse(window.localStorage.getItem(key) || 'null') ?? fallback,
    writeJson: (key, value) => window.localStorage.setItem(key, JSON.stringify(value)),
  }};
}
async function until(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('expected workspace transition');
}

test('native workspace shows progress through asset loading and activation, then recovers a failed asset', async () => {
  const harness = createHarness();
  const {window, root} = harness;
  storageFixture(window);
  window.PortalAPI = {get: async url => url.includes('?view=') ? {html:
    '<template id="testNativeView"><div hidden>Native content</div></template>' +
    '<script type="application/json" id="testNativeViewAssets">' +
    JSON.stringify({styles: [], scripts: ['/static/fixture-native.js']}) + '</script>'
  } : {views: [{id: 'test', label: 'Test'}]}};
  evaluate(window, 'management_portal/static/development_workspace.js');
  const owner = window.PortalDevelopmentWorkspace.mount(root, {
    openDevelopment: async () => ({setVisible() {}, destroy() {}}),
  });
  try {
    const first = owner.selectView('test');
    const host = () => root.querySelector('[data-development-view="test"]');
    await until(() => window.document.querySelector('script[src="/static/fixture-native.js"]'));
    assert.equal(host().getAttribute('aria-busy'), 'true');
    assert.match(host().querySelector('[role="status"]').textContent, /加载/);
    window.document.querySelector('script[src="/static/fixture-native.js"]').dispatchEvent(new window.Event('error'));
    assert.equal(await first, false);
    assert.equal(host().hasAttribute('aria-busy'), false);
    assert.equal(host().querySelector('[data-development-loading]'), null);
    assert.match(host().querySelector('[role="alert"]').textContent, /无法加载/);
    let activate;
    window.PortalDevelopmentWorkspace.registerView('test', {activate: () => new Promise(resolve => { activate = resolve; })});
    host().querySelector('[role="alert"] button').click();
    await until(() => window.document.querySelector('script[src="/static/fixture-native.js"]'));
    window.document.querySelector('script[src="/static/fixture-native.js"]').dispatchEvent(new window.Event('load'));
    await until(() => activate);
    assert.equal(host().getAttribute('aria-busy'), 'true', 'activation is still pending');
    assert.ok(host().querySelector('[data-development-loading]'));
    activate();
    await until(() => !host().hasAttribute('aria-busy'));
    assert.equal(host().querySelector('[role="alert"]'), null);
    assert.equal(host().querySelector('[data-development-loading]'), null);
    assert.match(host().textContent, /Native content/);
  } finally { owner.destroy(); await harness.cleanup(); }
});

test('Test initialization can retry after a broken template, shares concurrent opens and remounts once after destroy', async () => {
  const harness = createHarness();
  const {window, root} = harness;
  storageFixture(window);
  root.innerHTML = '<section id="wallTestView"></section>';
  let controller;
  window.PortalDevelopmentWorkspace = {registerView: (_id, value) => { controller = value; }};
  window.PortalAPI = {get: async () => ({workbench: null})};
  window.fetch = async () => ({ok: true, json: async () => ({
    summary: {total: 0, by_importance: {}}, filters: {modules: []}, modules: [], recent_runs: [], catalog_errors: [],
    capabilities: {runners: []}, tau_residents: [], access: {can_operate: true},
  })});
  for (const name of ['test_page_core', 'test_page_overview', 'test_page_catalog', 'test_page_case', 'test_page_run', 'test_page_plan', 'test_page_tau', 'test_page']) {
    evaluate(window, `management_portal/test_framework/page/${name}.js`);
  }
  const pages = () => root.querySelectorAll('.testTabs').length;
  try {
    await assert.rejects(controller.activate(), /模板不完整/);
    root.querySelector('#wallTestView').innerHTML = '<div class="testPage" id="testPage"></div>';
    const retry = controller.activate();
    assert.equal(controller.activate(), retry, 'concurrent opens share one initialization');
    await retry;
    await until(() => root.querySelector('.testOverview'));
    assert.equal(pages(), 1);
    assert.doesNotMatch(root.textContent, /初始化失败/);
    await controller.activate();
    assert.equal(pages(), 1, 'a later visit routes the same page again');
    controller.destroy();
    assert.equal(pages(), 0);
    await controller.activate();
    assert.equal(pages(), 1);
    controller.destroy();
    const pending = controller.activate();
    controller.destroy();
    await pending;
    assert.equal(pages(), 0, 'a view destroyed while mounting stays unmounted');
  } finally { controller.destroy(); await harness.cleanup(); }
});
