'use strict';
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { observeWorkbenchJob, readJob } = require(process.env.TFI_WORKBENCH_OBSERVER_MODULE || path.join(codeRoot, 'management_portal/test_framework/tau_workbench_job_observer'));

(async () => {
  const received = [];
  let body = { id: 'job-own', status: 'submitting', workbench_id: 'workbench-own' };
  const server = http.createServer((req, res) => {
    received.push({ method: req.method, path: req.url });
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<button id="generate">Generate</button><script>generate.onclick=()=>fetch("/api/workbenches/generate",{method:"POST"}).then(r=>r.json());</script>'); return; }
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const page = await browser.newPage(); await page.goto(origin);
    let n = 0; const ids = new WeakMap(), rows = [];
    const opts = { origin, operation: 'generate', requestId(req) { if (!ids.has(req)) ids.set(req, 'fixture-' + ++n); return ids.get(req); }, record(row) { rows.push(row); } };
    const fetch = async path => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === path.split('?')[0]);
      await page.evaluate(path => window.fetch(path).then(r => r.json()), path); return response;
    };
    const original = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    const observer = observeWorkbenchJob(page, opts);
    await assert.rejects(observer.accept(original[0]), /UnobservedOriginal/);
    const [response] = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    const initial = await observer.accept(response); assert.equal(initial.id, 'job-own'); assert.equal(initial.message_id, undefined);
    const initialEvidence = JSON.stringify(rows[0]);
    body = { id: 'job-other', status: 'completed', workbench_id: 'workbench-other' };
    await fetch('/api/workbenches/generation?id=job-other'); assert.equal((await observer.current()).status, 'submitting');
    body = { id: 'job-own', status: 'running', workbench_id: 'workbench-own', message_id: 'message-own' };
    await fetch('/api/workbenches/generation?id=job-own'); assert.equal((await observer.current()).message_id, 'message-own');
    body = { ...body, status: 'completed', revision: 2, save_receipt: { revision: 2 } };
    await fetch('/api/workbenches/generation?id=job-own'); assert.equal((await observer.current()).save_receipt.revision, 2);
    assert.equal(JSON.stringify(rows[0]), initialEvidence); assert.equal(initial.status, 'submitting');
    const beforeReads = received.length; await observer.current(); await observer.current(); assert.equal(received.length, beforeReads);
    body = { ...body, workbench_id: 'workbench-wrong' };
    await fetch('/api/workbenches/generation?id=job-own'); await assert.rejects(observer.current(), /IdentityMismatch/);
    await observer.close(); const count = rows.length;
    await fetch('/api/workbenches/generation?id=job-own'); assert.equal(rows.length, count);
    await assert.rejects(observer.current(), /Closed/);

    const reverseRows = [], reverse = observeWorkbenchJob(page, { ...opts, operation: 'reverse', record: row => reverseRows.push(row) });
    body = { id: 'reverse-own', kind: 'reverse', status: 'completed', plan_hash: 'plan-own' };
    const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/workbenches/reverse');
    await page.evaluate(() => window.fetch('/api/workbenches/reverse', { method: 'POST' }).then(r => r.json()));
    assert.equal((await reverse.accept(await r)).plan_hash, 'plan-own'); await reverse.close();

    const wrapped = observeWorkbenchJob(page, opts); body = { job: { id: 'wrapped', status: 'completed' } };
    const [bad] = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    await assert.rejects(wrapped.accept(bad), /ExpectedTopLevel/); await wrapped.close();
    body = { id: 'job-duplicate', status: 'submitting' };
    const duplicate = observeWorkbenchJob(page, opts);
    const [first] = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    await duplicate.accept(first);
    await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    await assert.rejects(duplicate.current(), /UnexpectedAdditional/); await duplicate.close();
    const brokenRecord = observeWorkbenchJob(page, { ...opts, record() { throw Error('fixture-record-unavailable'); } });
    const [unrecorded] = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST'), page.locator('#generate').click()]);
    await assert.rejects(brokenRecord.accept(unrecorded), /fixture-record-unavailable/);
    await assert.rejects(brokenRecord.close(), /fixture-record-unavailable/);
    assert.throws(() => readJob({ id: '', status: 'running' }), /ExpectedTopLevel/);
    assert.equal(received.filter(r => r.method === 'POST').length, 7);
    assert.ok(reverseRows[0].request_id);
    console.log(JSON.stringify({ status: 'passed', browser: 'Chromium', scopes: ['original-request registration', 'top-level id without premature message requirement', 'same-job terminal snapshot', 'unrelated job isolation', 'identity mismatch rejection', 'immutable original receipt', 'passive zero requests', 'close detaches listener', 'reverse exact endpoint', 'wrapped receipt rejection'], production_calls: 0, business_acceptance: false }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
