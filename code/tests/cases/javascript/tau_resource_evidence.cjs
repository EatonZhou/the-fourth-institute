'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {observeBrowser} = require('../../../management_portal/test_framework/tau_browser_observer');
const {observeResourceReferences} = require('../../../management_portal/test_framework/tau_resource_evidence');

(async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-reference-evidence-'));
  const target = 'article_123456789abc', lateTarget = 'article_abcdef123456';
  let releaseLate, releaseFirst, browser, observer, capture, sawFirst, sawLate;
  const firstSeen = new Promise(resolve => { sawFirst = resolve; });
  const lateSeen = new Promise(resolve => { sawLate = resolve; });
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    if (url.pathname !== '/api/resources/reference') {
      response.setHeader('Content-Type', 'text/html');
      response.end(`<main><span data-resource-address="${target}">Selected</span></main>`);
      return;
    }
    const send = (code, status = 404) => {
      if (response.writableEnded) return;
      response.writeHead(status, {'Content-Type': 'application/json'});
      response.end(JSON.stringify({code, error: 'private-error-message', contents: 'private-response-content'}));
    };
    const variant = url.searchParams.get('variant');
    if (variant === 'first') { releaseFirst = () => send('resource_not_found'); sawFirst(); }
    else if (variant === 'second') { send('resource_unavailable'); releaseFirst(); }
    else if (variant === 'late') { releaseLate = () => send('late_private_code'); sawLate(); }
    else send('resource_not_found', variant === 'success' ? 200 : 404);
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext();
    observer = await observeBrowser(context, {evidenceDir});
    const page = await context.newPage();
    const bodyReads = [];
    // Count passive body reads without changing the server or browser response.
    page.on('response', response => {
      const read = response.text.bind(response);
      response.text = () => { bodyReads.push(response.request()); return read(); };
    });
    capture = observeResourceReferences({page, origin, evidenceDir, requestId: observer.requestId});
    await page.goto(origin);
    const observedAddress = await page.locator('main [data-resource-address]').getAttribute('data-resource-address');
    capture.allow(observedAddress, {sourceKind: 'visible_reference', sourceLabel: 'selected-card'});
    assert.throws(() => capture.allow(target, {sourceKind: 'guessed', sourceLabel: 'card'}), TypeError);
    const requestUrl = (address, variant) => `${origin}/api/resources/reference?address=${address}&variant=${variant}&secret=private-query`;
    // Two original requests for the same resource finish in reverse order.
    await page.evaluate(url => { window.first = fetch(url).then(r => r.text()); }, requestUrl(target, 'first'));
    await firstSeen;
    await page.evaluate(url => fetch(url).then(r => r.text()), requestUrl(target, 'second'));
    await page.evaluate(() => window.first);
    await page.evaluate(url => { window.late = fetch(url).then(r => r.text()); }, requestUrl(lateTarget, 'late'));
    await lateSeen;
    capture.allow(lateTarget, {sourceKind: 'original_metadata', sourceLabel: 'later-metadata'});
    releaseLate();
    await page.evaluate(() => window.late);
    await page.evaluate(url => fetch(url).then(r => r.text()), requestUrl('private-unselected-target', 'unknown'));
    await page.evaluate(url => fetch(url).then(r => r.text()), requestUrl(target, 'success'));
    await page.evaluate(url => fetch(url).then(r => r.text()), requestUrl(target, 'duplicate') + '&address=private-second-target');
    await capture.flush();
    await capture.stop();
    await observer.flush();
    const output = fs.readFileSync(capture.file, 'utf8');
    const rows = output.trim().split('\n').map(line => JSON.parse(line));
    const requests = rows.filter(row => row.kind === 'reference_request');
    assert.equal(requests.length, 6);
    assert.equal(new Set(requests.map(row => row.request_id)).size, 6);
    const native = fs.readFileSync(observer.file, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    for (const record of requests) {
      const start = native.find(row => row.kind === 'request_started' && row.request_id === record.request_id);
      assert.ok(start?.page_id && start?.frame_id && start?.document_id, 'join to original confirmed browser identity');
      assert.equal(start.document_confirmed, true);
    }
    assert.equal(requests[0].registration.address, observedAddress);
    assert.equal(requests[1].registration.registration_id, requests[0].registration.registration_id);
    assert.equal(requests[2].registration, null, 'late allow cannot backfill the original request');
    assert.equal(requests[3].registration, null, 'unselected address is never retained');
    assert.equal(requests[5].registration, null, 'ambiguous duplicate address is not allowlisted');
    const codes = rows.filter(row => row.kind === 'reference_error_code');
    assert.equal(codes.length, 2, 'only pre-registered errors are read');
    assert.equal(codes.find(row => row.request_id === requests[0].request_id).code, 'resource_not_found');
    assert.equal(codes.find(row => row.request_id === requests[1].request_id).code, 'resource_unavailable');
    assert.equal(bodyReads.length, 2, 'no success, late, ambiguous or unselected response content read');
    for (const secret of ['private-query', 'private-error-message', 'private-response-content',
      'private-unselected-target', 'private-second-target', 'late_private_code']) assert.ok(!output.includes(secret));
    assert.equal(page.listenerCount('request'), 0, 'passive listener released');
    assert.throws(() => capture.allow(target, {sourceKind: 'visible_reference', sourceLabel: 'after-stop'}));
    console.log('resource reference evidence passed');
  } finally {
    releaseFirst?.(); releaseLate?.();
    await capture?.stop();
    await observer?.flush(); observer?.stop();
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(evidenceDir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
