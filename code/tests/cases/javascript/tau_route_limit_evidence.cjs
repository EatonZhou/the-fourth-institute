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
      assert.equal((await h.window.finish(h.data)).public_read.wires.length, 1);
      const sample = h.rows.at(-1).sample;
      assert.equal(sample.input.value, expected.max === null ? '' : String(expected.max));
      if (expected.max === null) assert.equal(sample.remaining.text, 'Remaining: unlimited');
      assert.equal(requests.length, h.requestCount, 'sampler makes no request');
      await h.window.close();
    }
    const partial = await prepare();
    const report = await partial.window.finish({});
    assert.ok(report.evidence_gaps.length);
    assert.equal(Object.hasOwn(report, 'ready'), false);
    const supplement = await partial.window.finish(partial.data);
    assert.ok(supplement.public_read.wires.length);
    await partial.window.close();
    assert.deepEqual(errors, []);
    console.log('Route native Inspector observations and supplementary report material verified');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
