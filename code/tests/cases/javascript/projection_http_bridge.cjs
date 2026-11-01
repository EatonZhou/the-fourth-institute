'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { createHarness } = require('./projection_watch_contracts.cjs');
const ref = 'workspace_111111111111/file/report.json';

test('resource and watch preserve gateway failure across the host and SDK', async () => {
  const calls = [];
  const h = await createHarness({ apiResponse(url) {
    calls.push(url);
    return new Response('<html>Gateway Timeout</html>', { status: 504 });
  } });
  try {
    for (const call of [() => h.sdk.resource('read', { address: ref }), () => h.sdk.watch([ref], () => {})]) {
      await assert.rejects(call(), error => {
        assert.equal(error.status, 504);
        assert.equal(error.http_status, 504);
        assert.equal(error.code, 'projection.http_error');
        assert.equal(error.message, 'HTTP 504');
        assert.equal(error.retryable, false);
        assert.equal(error.commit_state, 'unknown');
        return true;
      });
    }
    assert.deepEqual(calls, ['/api/workbenches/resource', '/api/workbenches/observe']);
  } finally { await h.close(); }
});

test('change notifications retain structured failures without partial refs', async () => {
  let fail = false;
  const h = await createHarness({ apiResponse() {
    if (fail) return Response.json({ code: 'resource.unavailable', error: 'Temporarily unavailable',
      retryable: true, commit_state: 'not_committed', details: { retry_after_ms: 1000 } }, { status: 503 });
  } });
  try {
    const changes = [];
    await h.sdk.watch([ref], value => changes.push(value));
    fail = true;
    h.refresh();
    const end = Date.now() + 3000;
    while (!changes.length && Date.now() < end) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(changes.length, 1);
    const error = changes[0];
    assert.equal(error.status, 503);
    assert.equal(error.http_status, 503);
    assert.equal(error.retryable, true);
    assert.equal(error.commit_state, 'not_committed');
    assert.equal(error.details.retry_after_ms, 1000);
    assert.equal(error.refs.length, 0);
    assert.equal(error.data.code, 'resource.unavailable');
  } finally { await h.close(); }
});
