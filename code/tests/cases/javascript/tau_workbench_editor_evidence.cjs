'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { createHash } = require('node:crypto');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { readEditorEvidence, requirePublishedEditor, observeEditorResponses } = require(path.join(codeRoot, 'management_portal/test_framework/tau_workbench_editor_evidence'));
const native = fs.readFileSync(path.join(codeRoot, 'management_portal/static/workbench_studio.js'), 'utf8');
const template = native.match(/element\("studioContent"\)\.innerHTML = window\.PortalI18n\.markup\(`([\s\S]*?)`\);/)[1];
const expected = { workbenchId: 'workbench-owned', revision: 2, publishedRevision: 2 };

(async () => {
  const network = [];
  const server = http.createServer(async (req, res) => {
    network.push({ method: req.method, url: req.url });
    if (!req.url.startsWith('/api/')) { res.setHeader('Content-Type', 'text/html'); res.end('<main id="studioContent"></main>'); return; }
    let text = ''; for await (const chunk of req) text += chunk;
    const body = text ? JSON.parse(text) : {};
    res.setHeader('Content-Type', 'application/json');
    if (req.url.endsWith('/facade')) {
      res.statusCode = 409; res.end(JSON.stringify({ code: 'fixture.conflict', message: 'DO-NOT-LOG-PRIVATE', details: { token: 'DO-NOT-LOG-PRIVATE' }, commit_state: 'not_committed', retryable: false }));
    } else if (req.url.endsWith('/delete')) {
      res.end('intentionally-not-JSON');
    } else res.end(JSON.stringify({ workbench_id: body.id, revision: 2, published_revision: 2, html: 'DO-NOT-LOG-PRIVATE' }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const page = await browser.newPage();
    await page.goto(origin + '/workbenches?panel=settings&id=workbench-owned');
    await page.locator('#studioContent').evaluate((root, html) => { root.innerHTML = html; }, template);
    let sample = await readEditorEvidence(page);
    assert.throws(() => requirePublishedEditor(sample, expected), /NotReady/);
    await page.evaluate(() => {
      document.querySelector('#workbenchSettings').hidden = false;
      document.querySelector('#publish').disabled = false;
      document.querySelector('#versionInfo').textContent = 'Draft v2 · Published v2';
      const status = document.querySelector('#generationStatus'); status.hidden = false;
      status.innerHTML = '<strong>Published successfully · v2</strong><p><a href="/workbenches/view?id=workbench-owned">Open published workbench</a></p>';
      document.querySelector('#activationStatus').textContent = 'unrelated activation';
    });
    sample = await readEditorEvidence(page); requirePublishedEditor(sample, expected);
    const initial = JSON.stringify(sample);
    for (const wrong of [
      () => { document.querySelector('#generationStatus strong').textContent = 'Published successfully · v22'; },
      () => { document.querySelector('#generationStatus strong').textContent = 'Published successfully · v2'; document.querySelector('#generationStatus').hidden = true; },
      () => { document.querySelector('#generationStatus').hidden = false; document.querySelector('#generationStatus a').href = '/workbenches/view?id=workbench-other'; },
      () => { document.querySelector('#generationStatus a').href = '/workbenches/view?id=workbench-owned'; document.querySelector('#studioContent').setAttribute('aria-busy', 'true'); },
      () => { document.querySelector('#studioContent').removeAttribute('aria-busy'); history.replaceState(null, '', '?panel=settings&id=workbench-other'); },
    ]) { await page.evaluate(wrong);
      const current = await readEditorEvidence(page); assert.throws(() => requirePublishedEditor(current, expected), /NotReady/); }
    assert.equal(JSON.stringify(sample), initial);
    await page.evaluate(() => {
      history.replaceState(null, '', '?panel=settings&id=workbench-owned');
      const advanced = document.querySelector('#workbenchAdvanced'); advanced.hidden = false;
      document.querySelector('#delete').hidden = false;
      const team = document.querySelector('#teamInfo'); team.hidden = false; team.dataset.lineageId = 'lineage-owned';
      team.innerHTML = '<p>Own team · 1 member</p><ul><li data-resident-id="resident-owned">Member</li></ul>';
      const status = document.querySelector('#generationStatus');
      status.innerHTML = '<details><summary>人工检查与修订计划</summary><textarea aria-label="反投影计划 JSON"></textarea></details>';
      status.querySelector('textarea').value = '{"summary":"owned","steps":[]}';
    });
    sample = await readEditorEvidence(page);
    assert.equal(sample.controls.delete.visible, false); assert.equal(sample.plan.visible, false);
    assert.equal(sample.team.lineage_id, 'lineage-owned'); assert.deepEqual(sample.team.member_ids, ['resident-owned']);
    assert.equal(sample.plan.value, undefined);
    const beforeSampleReads = network.length;
    await readEditorEvidence(page); assert.equal(network.length, beforeSampleReads);
    await page.locator('#workbenchAdvanced > summary').click();
    await page.locator('#generationStatus details > summary').click();
    sample = await readEditorEvidence(page); assert.equal(sample.controls.delete.visible, true); assert.equal(sample.plan.visible, true);
    assert.equal(sample.plan.value_sha256, createHash('sha256').update('{"summary":"owned","steps":[]}').digest('hex'));
    await page.locator('#generationStatus').evaluate(status => { const clone = status.firstElementChild.cloneNode(true); clone.open = false; status.replaceChildren(clone); });
    sample = await readEditorEvidence(page); assert.equal(sample.plan.visible, false); assert.equal(sample.plan.open, false);
    const post = async (operation, id = 'workbench-owned') => {
      const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/workbenches/' + operation);
      await page.evaluate(({ operation, id }) => fetch('/api/workbenches/' + operation, { method: 'POST', body: JSON.stringify({ id, expected_revision: 1, private: 'DO-NOT-LOG-PRIVATE' }) }).then(r => r.text()), { operation, id });
      return waiting;
    };
    const previous = await post('publish');
    const rows = [], ids = new WeakMap(); let n = 0;
    const options = { origin, workbenchId: 'workbench-owned', requestId: req => { if (!ids.has(req)) ids.set(req, 'fixture-' + ++n); return ids.get(req); }, record: row => { rows.push(row); } };
    const observer = observeEditorResponses(page, options);
    await assert.rejects(observer.original(previous), /UnobservedOriginal/);
    const other = await post('update', 'workbench-other'); await assert.rejects(observer.original(other), /UnobservedOriginal/);
    const update = await post('update'), receipt = await observer.original(update);
    assert.equal(receipt.identity.workbench_id, 'workbench-owned'); assert.equal(receipt.pathname, '/api/workbenches/update');
    const priorRows = JSON.stringify(rows);
    receipt.identity.revision = 999; assert.equal((await observer.original(update)).identity.revision, 2); assert.equal(JSON.stringify(rows), priorRows);
    const conflict = await post('facade'), failure = await observer.original(conflict);
    assert.equal(failure.status, 409); assert.equal(failure.error_code, 'fixture.conflict'); assert.equal(failure.commit_state, 'not_committed');
    const deletion = await post('delete'), deleted = await observer.original(deletion);
    assert.equal(deleted.status, 200); assert.equal(deleted.body_read, 'unavailable');
    assert(rows.some(r => r.request_id === deleted.request_id && r.phase === 'response' && r.status === 200));
    assert(!JSON.stringify(rows).includes('DO-NOT-LOG-PRIVATE'));
    assert.equal(new Set(rows.map(r => r.request_id + ':' + r.phase)).size, rows.length);
    const netBeforeReads = network.length; await observer.original(conflict); await observer.close(); assert.equal(network.length, netBeforeReads);
    const rowCount = rows.length; await post('publish'); assert.equal(rows.length, rowCount);
    const broken = observeEditorResponses(page, { ...options, record: () => { throw new Error('EvidenceDiskFailure'); } });
    const last = await post('publish'); await assert.rejects(broken.original(last), /EvidenceDiskFailure/); await assert.rejects(broken.close(), /EvidenceDiskFailure/);
    console.log(JSON.stringify({ status: 'passed', scope: 'isolated Chromium native editor template and passive original HTTP responses', production_calls: 0, business_acceptance: false,
      checks: ['published identity and exact v2/v22', 'hidden status/busy/wrong target rejection', 'native collapsed Advanced and plan remount', 'team data identity', 'immutable samples', 'pre-registration and other identity rejection', 'original response/status/body gap preserved', '409 safe code only', 'no private payload leakage', 'passive reads and detach', 'record failure propagation'] }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
