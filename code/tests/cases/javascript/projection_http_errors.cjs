'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname,
  '../../../management_portal/static/portal_projections.js'), 'utf8');

function host(reply) {
  const calls = [];
  const window = { addEventListener() {} };
  vm.runInNewContext(source, {
    window, URL, location: { pathname: '/workbenches', origin: 'https://portal.test' },
    fetch: async (...args) => { calls.push(args); return reply(); },
  }, { filename: 'portal_projections.js' });
  return { request: window.PortalProjections.request, calls };
}

test('HTML gateway errors retain HTTP failure instead of a JSON parser error', async () => {
  for (const endpoint of ['resource', 'observe']) {
    const { request, calls } = host(() => new Response('<html><h1>504 Gateway Time-out</h1></html>', {
      status: 504, headers: { 'Content-Type': 'text/html' },
    }));
    const body = { id: 'workbench_test', operation: 'read' };
    await assert.rejects(request(endpoint, body), error => {
      assert.equal(error.status, 504);
      assert.equal(error.http_status, 504);
      assert.equal(error.code, 'projection.http_error');
      assert.equal(error.message, 'HTTP 504');
      // A gateway response cannot certify that replaying a mutation is safe.
      assert.equal(error.retryable, false);
      assert.equal(error.commit_state, 'unknown');
      assert.deepEqual(Object.keys(error.data), []);
      return true;
    });
    assert.equal(calls.length, 1, 'request must never retry a POST implicitly');
    assert.equal(calls[0][0], `/api/workbenches/${endpoint}`);
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(calls[0][1].credentials, 'same-origin');
    assert.equal(calls[0][1].headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(calls[0][1].body), body);
  }
});

test('structured errors keep domain code, retry and commit semantics and details', async () => {
  for (const retryable of [true, false]) {
    const payload = {
      error: 'Resource service unavailable', message: 'secondary message',
      code: 'resource.unavailable', retryable, commit_state: 'not_committed',
      details: { retry_after_ms: 1500 }, http_status: 500,
    };
    const { request, calls } = host(() => Response.json(payload, { status: 503 }));
    await assert.rejects(request('resource', {}), error => {
      assert.equal(error.message, payload.error);
      assert.equal(error.code, payload.code);
      assert.equal(error.status, 503);
      assert.equal(error.http_status, 503, 'actual HTTP status wins over body metadata');
      assert.equal(error.retryable, retryable);
      assert.equal(error.commit_state, 'not_committed');
      assert.deepEqual(error.details, payload.details);
      assert.deepEqual(error.data, payload);
      return true;
    });
    assert.equal(calls.length, 1, 'even explicit retryable does not trigger automatic replay');
  }
});

test('message-only and legacy JSON failures remain usable without granting retries', async () => {
  for (const payload of [
    { message: 'No access', code: 'operation.forbidden' },
    { error: 'No access', code: 'projection.forbidden', retryable: 'true' },
  ]) {
    const { request } = host(() => Response.json(payload, { status: 403 }));
    await assert.rejects(request('resource', {}), error => {
      assert.equal(error.message, 'No access');
      assert.equal(error.status, 403);
      assert.equal(error.code, payload.code);
      assert.equal(error.retryable, false);
      assert.equal(error.commit_state, 'unknown');
      assert.deepEqual(Object.keys(error.details), []);
      return true;
    });
  }
});

test('empty, plain-text, malformed and non-object error bodies retain their status', async () => {
  for (const [status, body] of [
    [401, ''], [404, 'Not Found'], [429, '{broken'], [500, 'null'],
    [502, '[1,2]'], [503, '"unavailable"'], [504, '{"error":{},"code":42}'],
  ]) {
    const { request } = host(() => new Response(body, { status }));
    await assert.rejects(request('observe', {}), error => {
      assert.equal(error.status, status);
      assert.equal(error.http_status, status);
      assert.equal(error.code, 'projection.http_error');
      assert.equal(error.message, `HTTP ${status}`);
      assert.equal(error.retryable, false);
      assert.equal(error.commit_state, 'unknown');
      return true;
    });
  }
});

test('body read failure cannot discard an already received HTTP error status', async () => {
  const { request } = host(() => new Response(new ReadableStream({
    start(controller) { controller.error(new TypeError('connection interrupted')); },
  }), { status: 502 }));
  await assert.rejects(request('observe', {}), error => {
    assert.equal(error.status, 502);
    assert.equal(error.code, 'projection.http_error');
    assert.equal(error.commit_state, 'unknown');
    return true;
  });
});

test('successful JSON and GET paths keep their existing contract', async () => {
  const payload = { workbenches: [{ title: 'Example' }], version: 7 };
  const { request, calls } = host(() => Response.json(payload));
  assert.deepEqual(await request('?lineage_id=test'), payload);
  assert.deepEqual(await request(), payload);
  assert.equal(calls[0][0], '/api/workbenches?lineage_id=test');
  assert.equal(calls[1][0], '/api/workbenches');
  assert.equal(calls[0][1].method, undefined);
  assert.equal(calls[0][1].body, undefined);
  assert.equal(calls[0][1].credentials, 'same-origin');
});

test('invalid successful responses and transport cancellation are not hidden', async () => {
  const invalid = host(() => new Response('<html>not JSON</html>'));
  await assert.rejects(invalid.request('resource', {}), SyntaxError);
  for (const failure of [new TypeError('Failed to fetch'), new DOMException('Cancelled', 'AbortError')]) {
    const { request, calls } = host(() => { throw failure; });
    await assert.rejects(request('resource', {}), error => error === failure);
    assert.equal(calls.length, 1);
  }
});
