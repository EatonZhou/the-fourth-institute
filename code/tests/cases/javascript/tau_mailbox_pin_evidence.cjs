'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const code = process.env.TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser} = require(path.join(code, 'management_portal/test_framework/tau_browser_observer'));
const {observeMailboxPin} = require(process.env.TAU_PIN_MODULE || path.join(code, 'management_portal/test_framework/tau_mailbox_pin_evidence'));

(async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-pin-evidence-'));
  const key = 'resident:resident_123456789abc';
  let browser, observer, capture, mode = 'normal', release, sawRequest;
  const counts = {pin: 0, states: 0}, bodyReads = [];
  const server = http.createServer((request, response) => {
    if (request.method !== 'POST') {
      response.setHeader('Content-Type', 'text/html');
      response.end('<main><button id="pin">Pin</button></main>'); return;
    }
    let raw = '';
    request.on('data', chunk => { raw += chunk; });
    request.on('end', () => {
      const body = JSON.parse(raw);
      const target = body.instruction === 'PinMailboxItem' && body.arguments.key === key && body.arguments.pinned === true;
      if (target) counts.pin++; else counts.states++;
      const send = () => {
        response.writeHead(200, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({instruction: mode === 'wrong_instruction' ? 'ListResidentStates' : body.instruction,
          instruction_id: 'inst_web_12345678', status: mode === 'failed' ? 'failed' : 'committed',
          replayed: mode === 'replayed', private_text: 'private-response-content',
          result: {item: {key: mode === 'wrong_target' ? 'private-other-entry' : body.arguments.key,
            pinned: body.arguments.pinned, read_revision: 42}}}));
      };
      if (target && mode === 'delay') { release = send; sawRequest?.(); }
      else send();
    });
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext();
    observer = await observeBrowser(context, {evidenceDir});
    const page = await context.newPage();
    page.on('response', response => {
      const read = response.text.bind(response);
      response.text = () => { bodyReads.push(response.request()); return read(); };
    });
    await page.goto(origin);
    await page.evaluate(key => {
      window.pin = () => fetch('/api/instructions/execute', {method: 'POST',
        body: JSON.stringify({instruction: 'PinMailboxItem', arguments: {key, pinned: true}})}).then(r => r.json());
      document.querySelector('#pin').onclick = () => {
        window.pinAction = Promise.all([
          fetch('/api/instructions/execute', {method: 'POST', body: JSON.stringify({instruction: 'ListResidentStates', arguments: {}})}),
          fetch('/api/instructions/execute', {method: 'POST', body: JSON.stringify({instruction: 'PinMailboxItem', arguments: {key: 'private-unselected-target', pinned: true}})}),
          fetch('/api/instructions/execute', {method: 'POST', body: JSON.stringify({instruction: 'PinMailboxItem', arguments: {key, pinned: false}})}),
          window.pin(),
        ]);
      };
    }, key);
    const start = (identity = observer.requestId) => observeMailboxPin({page, origin, entryKey: key, evidenceDir, requestId: identity});
    const outcome = promise => promise.then(value => ({value}), error => ({error: error.message}));

    // The state query and other Pin responses arrive first. Only the original
    // selected Pin may satisfy the waiter, with its public observer identity.
    mode = 'delay';
    const seen = new Promise(resolve => { sawRequest = resolve; });
    capture = start();
    let resolved = false;
    const waiting = capture.waitForReceipt().then(value => { resolved = true; return value; });
    await page.click('#pin'); await seen;
    assert.equal(resolved, false);
    assert.equal(capture.snapshot().matching_requests, 1);
    release(); release = null;
    await page.evaluate(() => window.pinAction);
    const result = await waiting, final = await capture.stop();
    assert.equal(final.matching_requests, 1); assert.equal(final.records[0].committed, true);
    assert.equal(result.records[0].item.key, key);
    assert.equal(bodyReads.length, 1, 'do not read state, wrong-target or unpin bodies');
    assert.equal(counts.pin, 1, 'observer cannot replay the original click');
    await observer.flush();
    const native = fs.readFileSync(observer.file, 'utf8').trim().split('\n').map(JSON.parse);
    const original = native.filter(row => row.kind === 'request_started' && row.request_id === result.records[0].request_id);
    assert.equal(original.length, 1);
    assert.ok(original[0].page_id && original[0].frame_id && original[0].document_id && original[0].document_confirmed);

    // Capture started after a request cannot recover its missing original start.
    mode = 'delay';
    const lateSeen = new Promise(resolve => { sawRequest = resolve; });
    await page.evaluate(() => { window.late = window.pin(); }); await lateSeen;
    capture = start(); const lateWait = outcome(capture.waitForReceipt({timeoutMs: 60}));
    release(); release = null; await page.evaluate(() => window.late);
    assert.match((await lateWait).error, /timeout/);
    assert.equal((await capture.stop()).matching_requests, 0);

    for (mode of ['failed', 'wrong_target', 'wrong_instruction', 'replayed', 'missing_identity']) {
      capture = start(mode === 'missing_identity' ? () => null : observer.requestId);
      const wait = outcome(capture.waitForReceipt());
      await page.evaluate(() => window.pin());
      assert.match((await wait).error, /incomplete or not committed/);
      assert.equal((await capture.stop()).records[0].committed, false);
    }
    mode = 'normal';
    capture = start();
    await page.evaluate(() => Promise.all([window.pin(), window.pin()]));
    assert.match((await outcome(capture.waitForReceipt())).error, /Ambiguous/);
    assert.equal((await capture.stop()).ambiguous, true);
    assert.equal(page.listenerCount('request'), 0);
    capture = start();
    const stopped = outcome(capture.waitForReceipt()); await capture.stop();
    assert.match((await stopped).error, /stopped/);
    const output = fs.readFileSync(capture.file, 'utf8');
    for (const secret of ['private-response-content', 'private-other-entry', 'private-unselected-target']) assert.ok(!output.includes(secret));
    console.log('Mailbox original Pin evidence: concurrent state queries, exact request identity, no late backfill, invalid/replayed/ambiguous receipts and listener cleanup passed');
  } finally {
    release?.(); await capture?.stop(); await observer?.flush(); observer?.stop(); await browser?.close();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(evidenceDir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
