'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { performance } = require('node:perf_hooks');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { observeBrowser } = require(path.join(codeRoot, 'management_portal/test_framework/tau_browser_observer'));
const { observeFiniteInstructionResponses } = require(path.join(codeRoot, 'management_portal/test_framework/tau_finite_instruction_evidence'));

(async () => {
  const delayed = new Map(), received = [];
  const server = http.createServer(async (req, res) => {
    if (req.method !== 'POST') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><body>Original-response fixture</body></html>'); return; }
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text), mode = body.arguments.mode || 'normal';
    received.push(body);
    if (mode === 'stream') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write('data: {"secret":"DO-NOT-LOG"}\n\n');
      delayed.set(mode, res); return;
    }
    res.writeHead(mode === 'conflict' ? 409 : 200, { 'Content-Type': 'application/json' });
    if (mode === 'hung' || mode === 'closing') { res.write('{"result":'); delayed.set(mode, res); return; }
    if (mode === 'invalid') { res.end('not JSON'); return; }
    res.end(JSON.stringify({ status: mode === 'conflict' ? 'failed' : 'committed', instruction_id: 'fixture-original', result: { resident_id: body.arguments.resident_id, message_id: 'msg_fixture', reply_to_message_id: 'msg_original', content: 'DO-NOT-LOG' } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-finite-receipt-'));
  let browser, context, primary, observer;
  try {
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    context = await browser.newContext();
    primary = await observeBrowser(context, { evidenceDir: dir });
    const page = await context.newPage(); await page.goto(origin);
    const post = async (mode, instruction = 'GetConversation', residentId = 'resident_owned', target = page) => {
      const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/instructions/execute' && r.request().postDataJSON()?.arguments?.mode === mode);
      await target.evaluate(({ mode, instruction, residentId }) => {
        void fetch('/api/instructions/execute', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruction, arguments: { resident_id: residentId, mode }, private: 'DO-NOT-LOG' }) }).catch(() => {});
      }, { mode, instruction, residentId });
      return waiting;
    };
    const previous = await post('previous');
    const rows = [];
    const options = { origin, targets: [{ instruction: 'GetConversation', arguments: { resident_id: 'resident_owned' } }], requestId: r => primary.requestId(r), record: row => rows.push(row), bodyTimeoutMs: 150 };
    observer = observeFiniteInstructionResponses(page, options);
    await assert.rejects(observer.original(previous), /UnobservedOriginal/);
    const other = await post('other', 'GetConversation', 'resident_other');
    await assert.rejects(observer.original(other), /UnobservedOriginal/);
    const watch = await post('excluded-watch', 'WatchConversationEvents');
    await assert.rejects(observer.original(watch), /UnobservedOriginal/);
    assert.throws(() => observeFiniteInstructionResponses(page, { ...options, targets: [{ instruction: 'WatchConversationEvents', arguments: { resident_id: 'resident_owned' } }] }), /NonStreamingTarget/);
    const goodResponse = await post('normal'), good = await observer.original(goodResponse);
    assert.equal(good.body_status, 'read'); assert.equal(good.body.result.reply_to_message_id, 'msg_original');
    assert.equal(good.request_id, primary.requestId(goodResponse.request()));
    good.body.result.message_id = 'changed'; assert.equal((await observer.original(goodResponse)).body.result.message_id, 'msg_fixture');
    const conflict = await observer.original(await post('conflict'));
    assert.equal(conflict.http_status, 409); assert.equal(conflict.body.status, 'failed');
    const invalid = await observer.original(await post('invalid')); assert.equal(invalid.body_status, 'body_unavailable');
    const stream = await observer.original(await post('stream')); assert.equal(stream.body_status, 'not_finite_json');
    const hungResponse = await post('hung');
    assert(observer.pending().some(x => x.request_id === primary.requestId(hungResponse.request())));
    const started = performance.now(), hung = await observer.original(hungResponse);
    assert.equal(hung.body_status, 'body_observation_timeout'); assert(performance.now() - started < 1500);
    const originalTimeout = JSON.stringify(hung), originalRows = JSON.stringify(rows);
    delayed.get('hung').end('{"message_id":"late","content":"DO-NOT-LOG"}}');
    await hungResponse.finished();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(JSON.stringify(await observer.original(hungResponse)), originalTimeout);
    assert.equal(JSON.stringify(rows), originalRows, 'late completion cannot backfill the timeout');
    const closing = await post('closing');
    const beforeClose = performance.now(), report = await observer.close({ drainMs: 20 });
    assert(performance.now() - beforeClose < 1500); assert.equal(report.observation_complete, false);
    assert.equal((await observer.original(closing)).body_status, 'observer_close_deadline');
    const afterClose = JSON.stringify(rows), calls = received.length;
    await observer.close(); assert.equal(received.length, calls); assert.equal(JSON.stringify(rows), afterClose);
    await post('after-detach'); assert.equal(JSON.stringify(rows), afterClose);
    assert(!JSON.stringify(rows).includes('DO-NOT-LOG')); assert(!JSON.stringify(rows).includes('content'));
    const failed = observeFiniteInstructionResponses(page, { ...options, record: () => { throw new Error('EvidenceDiskFailure'); } });
    let originalCloseError;
    try {
      await failed.original(await post('record-failure'));
    } catch (error) { assert.match(error.message, /EvidenceDiskFailure/); }
    finally {
      try { await failed.close({ drainMs: 20 }); } catch (error) { originalCloseError = error; }
      // The application helper must use this nested finally shape. Observation
      // errors do not prevent the existing browser owner from closing.
      await context.close();
      await primary.flush(); primary.stop();
    }
    assert.match(originalCloseError.message, /EvidenceDiskFailure/);
    const capture = fs.readFileSync(path.join(dir, 'tau-browser-observations.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert(capture.some(r => r.kind === 'capture_ended'));
    assert(capture.some(r => r.kind === 'page_closed'));
    // Context closure may leave a request without a Playwright terminal event.
    // The primary observer must retain that gap rather than invent an abort.
    const closingRequest = capture.filter(r => r.request_id === primary.requestId(closing.request()));
    const closingEnd = closingRequest.find(r => ['request_failed', 'request_finished', 'request_pending_at_capture_end'].includes(r.kind));
    assert(closingEnd, 'the exact original request must retain a terminal event or an explicit capture-end gap');
    console.log(JSON.stringify({ status: 'passed', scope: 'isolated Chromium original HTTP response observation and final cleanup', checks: ['exact before-action request identity', 'old/foreign-target/Watch rejection', 'finite JSON success and HTTP error preserved', 'non-JSON stream excluded', 'real unfinished body deadline', 'late body cannot overwrite original timeout', 'bounded drain and detach', 'recorder error still closes original context/capture', 'safe metadata only and zero request replay'], original_close_observation: closingEnd.kind, production_calls: 0, business_acceptance: false, timing_scope: 'local helper deadline check, not outer tool RTT' }));
  } finally {
    if (observer) await observer.close().catch(() => {});
    if (context) await context.close();
    primary?.stop();
    if (browser) await browser.close();
    for (const response of delayed.values()) response.destroy();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
