'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { documentFixture, serverBoundary } = require('./route_message_limit_dom.cjs');
const source = path.resolve(__dirname, '../../..');
const statics = path.join(source, 'management_portal/static');
const { chromium } = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { openRouteLimitSaveEvidence } = require(path.join(source, 'management_portal/test_framework/tau_route_limit_evidence.js'));
const copy = x => JSON.parse(JSON.stringify(x));
const translations = fs.readFileSync(path.join(statics, 'portal_translations.json'), 'utf8');
const translator = fs.readFileSync(path.join(statics, 'portal_i18n.js'), 'utf8');
const textSource = fs.readFileSync(path.join(statics, 'portal_text.js'), 'utf8');
const setupLanguage = language => `window.PortalCompatibility={identifier:()=>"fixture_language"};window.__PORTAL_LANGUAGE__=${JSON.stringify(language)};window.__PORTAL_TRANSLATIONS__=${translations};`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1400 } });
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => requests.push(r.url()));
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><body></body>' }));
    let index = 0;
    const prepare = async (expected = { max: 1, sent: 0, remaining: 1, reached: false }, language = 'original') => {
      const boundary = serverBoundary();
      const graph = boundary.server, wire = graph.program.wires[0];
      wire.config.max_messages = expected.max;
      Object.assign(graph.program_invoke_wire_runtime[wire.id], {
        max_messages: expected.max, sent_count: expected.sent, remaining_messages: expected.remaining, message_limit_reached: expected.reached,
      });
      // Production HATG renderer/client produce the Inspector. Only server data
      // is stubbed. Chromium measures this native DOM; no hand-written Inspector.
      const fixture = await documentFixture(boundary, (name, code) => name === 'portal_text.js'
        ? code + '\n' + setupLanguage(language) + '\n' + translator : code);
      let html;
      try { html = fixture.window.document.querySelector('#programInspectorPanel').outerHTML; }
      finally { await fixture.close(); }
      assert.ok(boundary.calls.every(c => ['GetLineage', 'GetLineageDiagnostics', 'ListPendingDeliveries'].includes(c.instruction)), 'no native mutation in isolated render');
      await page.goto('https://route-evidence.fixture.invalid/' + (++index));
      await page.addScriptTag({ content: textSource + '\n' + setupLanguage(language) + '\n' + translator });
      await page.locator('body').evaluate((body, html) => { body.innerHTML = html; }, html);
      const options = { actionId: 'save_' + index, lineageId: graph.lineage_id, wireId: wire.id,
        sourceId: wire.source, targetId: wire.target, expected };
      const data = {
        receipt: { instruction: 'UpdateWire', instruction_id: 'inst_fixture_' + index, status: 'committed', result: { wire: copy(wire) } },
        lineageResult: { lineage: copy(graph), version: graph.revision },
      };
      const rows = [], requestCount = requests.length;
      const window = await openRouteLimitSaveEvidence(page.locator('#programInspectorPanel'), { ...options, record: async r => rows.push(r) });
      return { window, rows, data, requestCount, options };
    };
    for (const [expected, language] of [
      [{ max: 1, sent: 0, remaining: 1, reached: false }, 'original'],
      [{ max: 1, sent: 1, remaining: 0, reached: true }, 'en'],
      [{ max: 0, sent: 0, remaining: 0, reached: true }, 'original'],
      [{ max: null, sent: 3, remaining: null, reached: false }, 'en'],
    ]) {
      const h = await prepare(expected, language);
      if (expected.max === null) delete h.data.lineageResult.lineage.program.wires[0].config.max_messages;
      assert.equal((await h.window.finish(h.data)).ready, true);
      const sample = h.rows.at(-1).sample;
      assert.equal(sample.input.value, expected.max === null ? '' : String(expected.max));
      if (expected.max === null) assert.equal(sample.remaining.text, 'Remaining: unlimited');
      assert.equal(requests.length, h.requestCount, 'sampler makes no request');
    }
    // The formerly opaque disagreement now persists the actual field before it
    // throws, including the difference between a missing count and zero.
    for (const [field, mutate] of [
      ['public_read.runtime.sent_count', h => { delete h.data.lineageResult.lineage.program_invoke_wire_runtime[h.options.wireId].sent_count; }],
      ['public_read.runtime.remaining_messages', h => { h.data.lineageResult.lineage.program_invoke_wire_runtime[h.options.wireId].remaining_messages = 0; }],
      ['receipt.wire.target', h => { h.data.receipt.result.wire.target = 'resident_wrong'; }],
      ['public_read.lineage_id', h => { h.data.lineageResult.lineage.lineage_id = 'lineage_wrong'; }],
      ['dom.input.value', async () => { await page.locator('[name="max_messages"]').fill('0'); }],
      ['dom.sent.count', async () => { await page.locator('[data-wire-sent-count]').evaluate(e => e.after(e.cloneNode(true))); }],
      ['dom.remaining.visible', async () => { await page.locator('[data-wire-remaining]').evaluate(e => { e.hidden = true; }); }],
      ['dom.state.text', async () => { await page.locator('[data-wire-limit-state]').evaluate(e => { e.textContent = 'not ready'; }); }],
      ['inspector.identity', async () => { await page.locator('#programInspectorPanel').evaluate(e => { e.dataset.wireId = 'wire_wrong'; }); }],
      ['inspector.identity', async () => { await page.locator('#programInspectorPanel').evaluate(e => e.replaceWith(e.cloneNode(true))); }],
    ]) {
      const h = await prepare(), original = copy(h.data);
      await mutate(h);
      const proofData = copy(h.data);
      await assert.rejects(h.window.finish(h.data), error => {
        const saved = h.rows.at(-1);
        assert.equal(saved.stage, 'finish', 'evidence saved before rejection');
        assert.deepEqual(saved.receipt, proofData.receipt);
        assert.ok(saved.failures.some(f => f.field === field), JSON.stringify(saved.failures));
        if (field === 'public_read.runtime.sent_count') assert.deepEqual(saved.failures.find(f => f.field === field).actual, { missing: true });
        return error.message.includes(field);
      });
      const savedJson = JSON.stringify(h.rows), count = h.rows.length;
      h.data.receipt.result.wire.target = 'changed_after_record';
      assert.equal(JSON.stringify(h.rows), savedJson, 'saved evidence is not aliased');
      await assert.rejects(h.window.finish(original), /WindowClosed/, 'later correct evidence cannot fill original failure');
      assert.equal(h.rows.length, count);
      assert.equal(requests.length, h.requestCount, 'failed sampler never fetches/retries');
    }
    assert.deepEqual(errors, []);
    console.log('Route native Inspector evidence: original tuple, localized counts, failures-before-throw and closed windows verified');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
