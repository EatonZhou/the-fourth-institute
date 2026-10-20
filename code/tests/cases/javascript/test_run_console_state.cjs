'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHarness} = require('./workbench_visibility_state.cjs');

// Exercise the actual panel controller, API consumer and DOM render. Only the
// HTTP boundary is controlled; no output formatter or DOM method is replaced.
async function withConsole(check) {
  const harness = createHarness();
  const {window, root} = harness;
  const responses = new Map();
  const requests = [];
  window.fetch = async (url, options) => {
    requests.push({url, method: options.method || 'GET'});
    const id = new URL(url, window.location.href).searchParams.get('run_id');
    assert.ok(responses.has(id), `unexpected API read: ${url}`);
    return {ok: true, json: async () => structuredClone(responses.get(id))};
  };
  for (const name of ['test_workbench_core.js', 'test_run_console_panel.js']) {
    const file = path.resolve(__dirname, '../../../management_portal/test_framework/page', name);
    window.eval(fs.readFileSync(file, 'utf8') + `\n//# sourceURL=${file}`);
  }
  const T = window.PortalTest;
  T.store.catalog = {access: {actor: 'fixture-actor', can_operate: true}};
  const controller = T.panelDefinitions.get('run-console').create({root});
  try {
    await check({root, window, requests, open: async value => {
      responses.set(value.run_id, value);
      await controller.openRun(value.run_id);
      assert.equal(root.querySelector('[role="alert"]')?.textContent?.includes('打开历史运行失败'), undefined);
      return root.querySelector('[data-run-raw-output]');
    }});
    assert.ok(requests.every(item => item.method === 'GET'), 'viewing output never starts a run');
    await harness.settle();
  } finally {
    controller.destroy();
    await harness.cleanup();
  }
}

const reason = '等待并发名额期间源码已变化：tests/cases/test_test_framework.py';
const run = (status, extra = {}) => ({run_id: 'console-fixture', status, created_by: 'fixture-actor',
  output: '', error: '', results: [], groups: [], ...extra});

test('source-rejected terminal run has no waiting placeholder and retains its result reason', async () => {
  await withConsole(async ({open, root}) => {
    const failed = {case_id: 'fixture-case', status: 'error', failure_class: 'environment', details: {summary: reason}};
    const output = await open(run('error', {results: [failed], groups: [{group_id: 'pytest-1', status: 'error', results: [failed]}]}));
    assert.doesNotMatch(output.textContent, /等待输出|Waiting for output/, 'terminal no-output must not imply active work');
    assert.match(output.textContent, /结束.*没有.*输出/);
    assert.ok(root.querySelector('[data-run-result]').textContent.includes(reason), 'real result reason remains visible');
    assert.equal(root.querySelector('[data-cancel]').disabled, true);
  });
});

test('all terminal empty outputs differ from queued/running empty outputs', async () => {
  await withConsole(async ({open}) => {
    for (const status of ['passed', 'failed', 'cancelled', 'error', 'unsupported']) {
      const output = await open(run(status));
      assert.match(output.textContent, /结束.*没有.*输出/, status);
      assert.doesNotMatch(output.textContent, /等待输出|Waiting for output/, status);
    }
    for (const status of ['queued', 'running', 'cancelling']) {
      assert.equal((await open(run(status))).textContent, '等待输出…', status);
    }
  });
});

test('retained runner bytes and run-level errors take precedence over empty-state text', async () => {
  await withConsole(async ({open, root}) => {
    const exact = '\n\r\n<error>literal & untouched</error>\r\n';
    assert.equal((await open(run('error', {output: exact, error: 'secondary reason'}))).textContent, exact);
    assert.equal(root.querySelector('error'), null, 'raw markup stays text');
    assert.equal((await open(run('error', {error: reason}))).textContent, reason);
    assert.equal((await open(run('passed', {output: ' \n'}))).textContent, ' \n', 'whitespace is retained output');
  });
});
