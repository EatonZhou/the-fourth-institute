'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os');
const path = require('node:path'), http = require('node:http');
const code = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser} = require(path.join(code, 'management_portal/test_framework/tau_browser_observer'));
const probe = process.argv.includes('--probe');
const observe = probe ? null : require(path.join(code, 'management_portal/test_framework/tau_paper_review_evidence')).observePaperReviewSubmission;
const api = fs.readFileSync(path.join(code, 'management_portal/static/portal_api.js'), 'utf8');
const reviews = fs.readFileSync(path.join(code, 'management_portal/static/paper_reviews.js'), 'utf8');
const create = reviews.match(/    async function createReview\(\) \{[\s\S]*?(?=    async function createExp\()/)[0];
const paperId = 'paper_111111111111', privateText = 'PRIVATE-REVIEW-FIXTURE';
let mode = 'held', requestCount = 0;
const held = [];
const job = {job_id: 'review_fixture', paper_id: paperId, status: 'queued', content: privateText};
const server = http.createServer(async (req, res) => {
  if (req.url === '/') return res.end('<!doctype html><main>Local Paper transport fixture</main>');
  if (req.url !== '/paper/api/reviews/start') { res.statusCode = 404; res.end(); return; }
  let body = ''; for await (const chunk of req) body += chunk;
  requestCount++;
  if (mode === 'held') { held.push(res); return; }
  const status = mode === 'error' ? 409 : 200;
  res.writeHead(status, {'Content-Type': 'application/json'});
  if (mode === 'body-held') { res.write('{'); held.push(res); return; }
  res.end(mode === 'malformed' ? '{' : JSON.stringify(mode === 'error'
    ? {code: 'fixture.conflict', error: privateText}
    : mode === 'wrong' ? {...job, paper_id: 'paper_222222222222'}
    : mode === 'nested' ? {job} : job));
});
const turn = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-paper-review-'));
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  let publicObserver;
  try {
    const context = await browser.newContext();
    publicObserver = await observeBrowser(context, {evidenceDir});
    const page = await context.newPage();
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => { window.PortalI18n = {requestLanguage: () => 'en', message: String}; });
    await page.addScriptTag({content: api});
    await page.addScriptTag({content: `(() => {
      const state = {selectedId: '${paperId}', mode: 'review', reviewInFlight: false};
      const renderWorkspace = () => {};
      const loadReviewStatus = async () => {}; // Fixture: this check does not accept progress/body readiness.
      const paperApi = {request: (url, options) => PortalAPI.request(url, {...options, signal: window.fixtureAbort.signal})};
      ${create}
      window.startNative = () => { state.reviewInFlight = false; window.fixtureAbort = new AbortController();
        window.nativeResult = createReview().then(() => 'returned', e => e.name); };
      window.selectFixturePaper = id => { state.selectedId = id; };
    })();`});
    const start = async () => {
      const wait = page.waitForRequest(r => r.method() === 'POST' && new URL(r.url()).pathname === '/paper/api/reviews/start');
      await page.evaluate(() => startNative()); return wait;
    };
    const abort = async request => {
      const failed = page.waitForEvent('requestfailed', {predicate: r => r === request});
      await page.evaluate(() => fixtureAbort.abort()); await failed; await page.evaluate(() => nativeResult);
    };
    const rows = [], options = {origin, paperId, requestId: publicObserver.requestId, record: row => rows.push(row)};
    if (probe) {
      let returned = false;
      const received = response => { if (new URL(response.url()).pathname === '/paper/api/reviews/start') returned = true; };
      page.on('response', received);
      const original = await start(); await abort(original); await turn();
      assert.equal(returned, false);
      await publicObserver.flush();
      const events = fs.readFileSync(path.join(evidenceDir, 'tau-browser-observations.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
      const id = publicObserver.requestId(original), failed = events.find(e => e.request_id === id && e.kind === 'request_failed');
      assert.equal(failed.failed_reason, 'net::ERR_ABORTED'); assert.equal(failed.cancellation_cause, 'unknown');
      page.off('response', received);
      console.log(JSON.stringify({status: 'reproduced', response_only_wait_settled: returned, request_id: id,
        terminal: failed.kind, cancellation_cause: failed.cancellation_cause,
        fixture: 'Native Paper createReview function and PortalAPI; local HTTP, explicit fixture abort, stub state/render/status',
        original_watch_definition_available: false, original_failure_cause_proven: false, formal_acceptance: false}));
      return;
    }
    // The same requestfailed must settle an armed receipt read without a Response.
    let capture = observe(page, options), pending = capture.waitForReceipt({timeoutMs: 5000});
    let request = await start(); await abort(request); let result = await pending;
    assert.equal(result.receipt_ready, false); assert.equal(result.records[0].terminal, 'request_failed');
    assert.equal(result.records[0].request_id, publicObserver.requestId(request));
    assert.equal(result.records[0].cancellation_cause, 'unknown'); capture.close();
    // Both published response shapes work. Status/body/identity failures remain evidence, not acceptance.
    for (mode of ['complete', 'nested', 'error', 'wrong', 'malformed']) {
      capture = observe(page, options); pending = capture.waitForReceipt({timeoutMs: 5000});
      await start(); result = await pending; await page.evaluate(() => nativeResult);
      assert.equal(result.receipt_ready, ['complete', 'nested'].includes(mode));
      assert.equal(result.records[0].http_status, mode === 'error' ? 409 : 200);
      if (mode === 'error') assert.equal(result.records[0].error_code, 'fixture.conflict');
      assert(!JSON.stringify(result).includes(privateText)); capture.close();
    }
    // An unrelated paper or frame must not supply the original request identity.
    mode = 'complete'; capture = observe(page, options);
    await page.evaluate(() => selectFixturePaper('paper_222222222222'));
    await start(); await page.evaluate(() => nativeResult);
    assert.equal(capture.snapshot().records.length, 0);
    await page.evaluate(() => selectFixturePaper('paper_111111111111'));
    await page.evaluate(() => new Promise(resolve => {
      const iframe = document.createElement('iframe'); iframe.src = '/'; iframe.onload = resolve; document.body.append(iframe);
    }));
    const child = page.frames().find(frame => frame !== page.mainFrame());
    await child.evaluate(async id => { await fetch('/paper/api/reviews/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paper_id: id}),
    }).then(response => response.text()); }, paperId);
    assert.equal(capture.snapshot().records.length, 0);
    result = await capture.waitForReceipt({timeoutMs: 40});
    assert.equal(result.gap, 'receipt_deadline'); assert.equal(result.receipt_ready, false);
    await page.locator('iframe').evaluate(element => element.remove());
    // A pre-registration request's later failure is not the armed action's request.
    mode = 'held'; request = await start(); capture = observe(page, options); await abort(request);
    assert.equal(capture.snapshot().records.length, 0); capture.close();
    // Missing public IDs and duplicate matching requests are explicit evidence gaps.
    capture = observe(page, {...options, requestId: () => null}); pending = capture.waitForReceipt({timeoutMs: 5000});
    request = await start(); result = await pending;
    assert.equal(result.records[0].terminal, 'identity_unavailable'); capture.close(); await abort(request);
    capture = observe(page, options); pending = capture.waitForReceipt({timeoutMs: 5000});
    const firstRequest = await start();
    await page.evaluate(() => { window.firstAbort = fixtureAbort; window.firstResult = nativeResult; });
    request = await start(); result = await pending;
    assert.equal(result.gap, 'ambiguous_original_requests'); assert.equal(result.receipt_ready, false);
    assert.equal(new Set(result.records.map(row => row.request_id)).size, 2); capture.close();
    const firstFailed = page.waitForEvent('requestfailed', {predicate: r => r === firstRequest});
    await page.evaluate(() => firstAbort.abort()); await firstFailed; await page.evaluate(() => firstResult);
    await abort(request);
    // Logical observation deadlines/closure never cancel a held original request or accept its late body.
    for (const close of [false, true]) {
      mode = 'body-held'; capture = observe(page, {...options, bodyTimeoutMs: close ? 5000 : 40});
      const headers = page.waitForResponse(r => new URL(r.url()).pathname === '/paper/api/reviews/start');
      headers.catch(() => {}); pending = capture.waitForReceipt({timeoutMs: 5000}); await start(); await headers;
      if (close) capture.close();
      result = await pending; assert.equal(result.receipt_ready, false);
      const frozen = JSON.stringify(capture.snapshot());
      const response = held.pop(); response.end('"job_id":"review_fixture","paper_id":"' + paperId + '","status":"queued"}');
      await page.evaluate(() => nativeResult); await turn();
      assert.equal(JSON.stringify(capture.snapshot()), frozen); capture.close();
    }
    // A pending observation closed before any receipt cannot hang or later collect another request.
    mode = 'held'; capture = observe(page, options); pending = capture.waitForReceipt({timeoutMs: 5000});
    request = await start(); capture.close(); result = await pending; assert.equal(result.gap, 'capture_closed');
    const frozen = JSON.stringify(capture.snapshot()); await abort(request); assert.equal(JSON.stringify(capture.snapshot()), frozen);
    capture = observe(page, {...options, record: () => { throw new Error('fixture-write-failure'); }});
    pending = capture.waitForReceipt({timeoutMs: 5000}); request = await start(); result = await pending;
    assert.equal(result.receipt_ready, false); assert.equal(result.gap, 'record_error'); capture.close(); await abort(request);
    const before = requestCount; capture.snapshot(); capture.close(); assert.equal(requestCount, before);
    console.log(JSON.stringify({status: 'passed', fixture: 'Native Paper createReview and PortalAPI; local HTTP and state/status adapters',
      request_failed_settled: true, late_body_ignored: true, business_requests_replayed: false,
      formal_acceptance: false, original_error_cause_proven: false, caller_rtt_evidence: false}));
  } finally {
    publicObserver?.stop(); await browser.close();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    fs.rmSync(evidenceDir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
