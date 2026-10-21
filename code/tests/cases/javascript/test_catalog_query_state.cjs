'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHarness} = require('./workbench_visibility_state.cjs');

async function until(check) {
  const deadline = Date.now() + 2000;
  while (!check()) {
    assert.ok(Date.now() < deadline, 'expected UI transition did not occur');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

async function withCatalog(check) {
  const harness = createHarness();
  const {window, root} = harness;
  const requests = [];
  window.fetch = url => new Promise((resolve, reject) => requests.push({
    search: new URL(url, window.location.href).searchParams.get('search'), reject,
    finish(ids) { resolve({ok: true, json: async () => ({case_ids: ids})}); },
  }));
  for (const name of ['test_workbench_core.js', 'test_catalog_panel.js']) {
    window.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/test_framework/page', name), 'utf8'));
  }
  const T = window.PortalTest;
  T.store.catalogStatus = 'loaded';
  T.store.catalog = {summary: {}, filters: {modules: []}};
  T.store.cases = ['alpha', 'beta'].map(id => ({
    id, key: id, title: id, module: 'operations.test-workbench', importance: 'P1',
    aspects: [], lifecycle: 'active', implementations: [], detail_loaded: true,
  }));
  const opened = [];
  T.store.subscribe((event, detail) => { if (event === 'selection') opened.push(detail.caseId); });
  const controller = T.panelDefinitions.get('test-catalog').create({root});
  const search = value => {
    const input = root.querySelector('[data-search]');
    input.value = value;
    input.dispatchEvent(new window.Event('input', {bubbles: true}));
  };
  try {
    await check({root, window, T, requests, search, opened, controller});
    assert.deepEqual(harness.errors, []);
  } finally {
    controller.destroy();
    await harness.cleanup();
  }
}

test('failed search replaces stale results with a retryable error and retains the query', async () => {
  await withCatalog(async ({root, requests, search, opened}) => {
    assert.equal(root.querySelectorAll('[data-case-id]').length, 2);
    search('alpha');
    assert.equal(root.querySelectorAll('[data-case-id]').length, 0);
    assert.match(root.querySelector('[role="status"]').textContent, /筛选/);
    await until(() => requests.length === 1);
    requests[0].reject(new Error('query <unavailable>'));
    await until(() => root.querySelector('[data-retry-query]'));
    assert.equal(root.querySelectorAll('[data-case-id]').length, 0);
    assert.match(root.querySelector('[role="alert"]').textContent, /query <unavailable>/);
    assert.equal(root.querySelector('unavailable'), null);
    assert.equal(root.querySelector('[data-search]').value, 'alpha');
    assert.deepEqual(opened, [], 'failure does not navigate to a stale case');
    root.querySelector('[data-retry-query]').click();
    await until(() => requests.length === 2);
    assert.equal(requests[1].search, 'alpha');
    requests[1].finish(['alpha']);
    await until(() => root.querySelector('[data-case-id="alpha"]'));
    assert.equal(root.querySelectorAll('[data-case-id]').length, 1);
    assert.equal(root.querySelector('[role="alert"]'), null);
  });
});

test('superseded responses cannot render during debounce or after filters are cleared', async () => {
  await withCatalog(async ({root, requests, search, controller}) => {
    search('alpha');
    await until(() => requests.length === 1);
    search('beta');
    requests[0].finish(['alpha']);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(root.querySelectorAll('[data-case-id]').length, 0);
    assert.equal(controller.visibleCaseIds().length, 0);
    await until(() => requests.length === 2);
    search('');
    assert.equal(root.querySelectorAll('[data-case-id]').length, 2);
    requests[1].reject(new Error('obsolete failure'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(root.querySelectorAll('[data-case-id]').length, 2);
    assert.equal(root.querySelector('[role="alert"]'), null);
  });
});
