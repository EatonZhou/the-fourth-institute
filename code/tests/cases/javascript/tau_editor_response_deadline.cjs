'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const code = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { observeEditorResponses } = require(process.env.EDITOR_OBSERVER_SOURCE || path.join(code, 'management_portal/test_framework/tau_workbench_editor_evidence.js'));
const native = fs.readFileSync(path.join(code, 'management_portal/static/portal_projections.js'), 'utf8');
const workbenchId = 'workbench_111111111111';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const reports = [], calls = [], held = [];
let mode = 'held';
const body = JSON.stringify({ code: 'fixture.binding_conflict', error: 'PRIVATE-FIXTURE-ERROR', details: { secret: 'PRIVATE-FIXTURE' }, commit_state: 'not_committed', retryable: false });
const server = http.createServer(async (req, res) => {
  if (req.url === '/') return res.end('<!doctype html><script src="/static/portal_projections.js"></script><main>Local transport fixture</main>');
  if (req.url === '/static/portal_projections.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(native); return; }
  if (req.url !== '/api/workbenches/facade') { res.statusCode = 404; res.end(); return; }
  let text = ''; for await (const chunk of req) text += chunk;
  calls.push(JSON.parse(text));
  res.writeHead(409, { 'Content-Type': 'application/json' });
  if (mode === 'held') { res.write(body.slice(0, 8)); held.push(() => res.end(body.slice(8))); }
  else res.end(mode === 'malformed' ? '{' : body);
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    const ids = new WeakMap(); let next = 0;
    const requestId = req => { if (!ids.has(req)) ids.set(req, 'fixture-' + ++next); return ids.get(req); };
    const post = async () => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/workbenches/facade');
      await page.evaluate(id => {
        window.nativeSettled = false;
        window.nativeRequest = PortalProjections.request('facade', { id, preview: false }).then(
          () => { window.nativeSettled = true; }, error => { window.nativeSettled = true; window.nativeCode = error.code; });
      }, workbenchId);
      return response;
    };
    const release = async () => { held.shift()(); await page.evaluate(() => nativeRequest); };
    const rows = [];
    const options = { origin, workbenchId, requestId, record: row => rows.push(row) };
    if (process.argv.includes('--probe')) {
      const observer = observeEditorResponses(page, options), response = await post();
      let originalSettled = false, closeSettled = false;
      const original = observer.original(response).then(value => { originalSettled = true; return value; });
      const close = observer.close().then(() => { closeSettled = true; });
      await delay(150);
      assert.equal(originalSettled, false); assert.equal(closeSettled, false);
      assert.equal(rows.find(r => r.phase === 'response').status, 409);
      assert.equal(rows.some(r => r.phase === 'body'), false);
      await release(); const result = await original; await close;
      assert.equal(result.error_code, 'fixture.binding_conflict');
      console.log(JSON.stringify({ reproduced: 'body and close remain pending while original native HTTP response is held open', status_retained: 409,
        safe_code_after_release: result.error_code, formal_acceptance: false, scope: 'native PortalProjections request client / local HTTP; not original UI or business' }));
      return;
    }
    // Deadline must settle observation without cancelling the native request.
    let observer = observeEditorResponses(page, { ...options, bodyTimeoutMs: 60 });
    let response = await post(), result = await observer.original(response);
    assert.equal(result.body_read, 'timeout'); assert.equal(result.status, 409);
    assert.equal(await page.evaluate(() => nativeSettled), false);
    assert.equal(result.error.stage, 'original_response_body');
    const beforeLateBody = JSON.stringify(rows);
    await release(); await delay(20);
    assert.equal(JSON.stringify(rows), beforeLateBody);
    assert.deepEqual(await observer.original(response), result);
    await observer.close(); reports.push(result);
    // Closing has its own drain bound even with a longer per-body deadline.
    rows.length = 0;
    observer = observeEditorResponses(page, { ...options, bodyTimeoutMs: 10000 });
    response = await post(); const original = observer.original(response);
    const closing = observer.close({ drainMs: 0 });
    assert.strictEqual(observer.close({ drainMs: 0 }), closing);
    await closing; result = await original;
    assert.equal(result.body_read, 'closed'); assert.equal(result.status, 409);
    assert.equal(await page.evaluate(() => nativeSettled), false);
    const closedRows = JSON.stringify(rows); await release(); await delay(20);
    assert.equal(JSON.stringify(rows), closedRows); reports.push(result);
    await assert.rejects(observer.original(response), /Closed/);
    // In-window successful body reading still preserves safe errors only.
    mode = 'complete'; rows.length = 0;
    observer = observeEditorResponses(page, { ...options, bodyTimeoutMs: 2000 });
    await assert.rejects(observer.original(response), /Unobserved/);
    response = await post(); result = await observer.original(response);
    assert.equal(result.body_read, 'success'); assert.equal(result.error_code, 'fixture.binding_conflict');
    assert.equal(result.commit_state, 'not_committed'); assert.equal(result.retryable, false);
    assert.ok(!JSON.stringify(rows).includes('PRIVATE-FIXTURE'));
    reports.push(result); await observer.close();
    mode = 'malformed'; rows.length = 0;
    observer = observeEditorResponses(page, options); response = await post(); result = await observer.original(response);
    assert.equal(result.body_read, 'unavailable'); assert.equal(result.status, 409); reports.push(result);
    await observer.close();
    // A persistence error must survive bounded settling and closure.
    mode = 'held';
    observer = observeEditorResponses(page, { ...options, bodyTimeoutMs: 60, record: () => { throw new Error('fixture-disk-failure'); } });
    response = await post(); await assert.rejects(observer.original(response), /fixture-disk-failure/);
    await assert.rejects(observer.close(), /fixture-disk-failure/); await release();
    const callCount = calls.length;
    await delay(20); assert.equal(calls.length, callCount, 'observer sends no retries or other requests');
    assert.throws(() => observeEditorResponses(page, { ...options, bodyTimeoutMs: Infinity }), /Invalid/);
    const report = { status: 'passed', reports, native_client: 'portal_projections.js',
      scope: 'local HTTP only; intentionally held original native responses; no production UI/business replay; observation deadlines are not caller RTT', formal_acceptance: false };
    if (process.env.TAU_EDITOR_DEADLINE_EVIDENCE) fs.writeFileSync(process.env.TAU_EDITOR_DEADLINE_EVIDENCE, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  } finally {
    while (held.length) held.shift()();
    await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
