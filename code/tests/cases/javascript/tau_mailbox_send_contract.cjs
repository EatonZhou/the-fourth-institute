'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const code = process.env.TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { observeBrowser } = require(path.join(code, 'management_portal/test_framework/tau_browser_observer'));
const { matchesMailboxTextSubmission: matches } = require(process.env.TAU_MAILBOX_SEND_MODULE || path.join(code, 'management_portal/test_framework/tau_mailbox_send_contract'));
const expected = { residentId: 'resident_123456789abc', text: 'original fixture input\nEXACT-TEXT' };
const args = { resident_id: expected.residentId, message: { role: 'user', content_parts: [{ type: 'text', text: expected.text }] } };
assert(matches(args, expected));
assert(!matches({ resident_id: expected.residentId, content: expected.text }, expected), 'legacy content must not match');
assert(!matches({ ...args, resident_id: 'resident_deadbeefabcd' }, expected));
assert(!matches({ ...args, user_id: 'user_other' }, expected));
assert(!matches({ ...args, activation_policy: 'attach_to_next' }, expected));
assert(!matches({ ...args, message: { ...args.message, role: 'assistant' } }, expected));
assert(!matches({ ...args, message: { ...args.message, quote: { message_id: 'msg_old' } } }, expected));
for (const parts of [[], [{ type: 'text', text: expected.text + ' ' }], [{ type: 'text', text: expected.text }, { type: 'image', url: 'private' }]]) {
  assert(!matches({ ...args, message: { content_parts: parts } }, expected));
}
assert.throws(() => matches(args, { residentId: expected.residentId, text: '' }), /nonempty/);

(async () => {
  const received = [], dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-send-contract-'));
  let browser, context, primary;
  const server = http.createServer((request, response) => {
    if (request.method !== 'POST') {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><section id="chat"><button id="send">Send message</button></section>'); return;
    }
    let raw = '';
    request.on('data', chunk => { raw += chunk; });
    request.on('end', () => {
      const body = JSON.parse(raw); received.push(body);
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ instruction: body.instruction, instruction_id: 'inst_web_fixture',
        status: 'committed', replayed: false, result: { resident_id: body.arguments.resident_id,
          message_id: 'msg_123456789abcdef0123456789abcdef0', accepted: true } }));
    });
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    context = await browser.newContext(); primary = await observeBrowser(context, { evidenceDir: dir });
    const A = await context.newPage(), B = await context.newPage();
    await A.goto(origin); await B.goto(origin);
    await B.addScriptTag({ path: path.join(code, 'management_portal/static/portal_resident_service.js') });
    await B.evaluate(payload => {
      const service = window.PortalResidentService.createClient({ execute: (instruction, args, controls) =>
        fetch('/api/instructions/execute', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction, arguments: args, idempotency_key: controls.idempotencyKey }) }).then(r => r.json()) });
      document.querySelector('#send').onclick = () => { window.originalSend = service.sendInput({ ...payload, idempotency_key: 'fixture-one-input' }); };
    }, args);
    const observed = [];
    B.on('request', request => {
      if (request.method() !== 'POST') return;
      const body = request.postDataJSON();
      if (body?.instruction === 'DeliverMessage' && matches(body.arguments, expected)) observed.push(primary.requestId(request));
    });
    const original = B.waitForResponse(response => {
      const request = response.request(), url = new URL(response.url());
      if (url.origin !== origin || url.pathname !== '/api/instructions/execute' || request.method() !== 'POST') return false;
      const body = request.postDataJSON();
      return body?.instruction === 'DeliverMessage' && matches(body.arguments, expected);
    }, { timeout: 3000 });
    // A different Page cannot satisfy B's original waiter, even for the same payload.
    await A.evaluate(payload => fetch('/api/instructions/execute', { method: 'POST', body: JSON.stringify({ instruction: 'DeliverMessage', arguments: payload }) }).then(r => r.json()), args);
    assert.equal(observed.length, 0);
    await B.getByRole('button', { name: 'Send message', exact: true }).click();
    const response = await original, receipt = await response.json();
    assert.equal(receipt.status, 'committed'); assert.equal(receipt.replayed, false);
    assert.equal(receipt.result.resident_id, expected.residentId); assert.equal(receipt.result.accepted, true);
    assert.equal(observed.length, 1); assert.equal(observed[0], primary.requestId(response.request()));
    const submitted = response.request().postDataJSON();
    assert.equal(submitted.arguments.content, undefined);
    assert(matches(submitted.arguments, expected), 'match the actual native service payload');
    await B.evaluate(() => window.originalSend);
    assert.equal(received.length, 2, 'one isolated fixture call on each Page; no observer replay');
    await context.close(); await primary.flush(); primary.stop();
    const rows = fs.readFileSync(primary.file, 'utf8').trim().split('\n').map(JSON.parse);
    const native = rows.filter(row => row.request_id === observed[0]);
    assert(native.some(row => row.page_id && row.frame_id && row.document_id));
    assert(native.some(row => row.kind === 'request_finished'));
    console.log(JSON.stringify({ status: 'passed', browser: 'Chromium', isolated: true, business_acceptance: false,
      checks: ['official message.content_parts shape', 'old content predicate rejected', 'exact target/text/role/policy/quote checks', 'native PortalResidentService.sendInput payload', 'original B Page and primary request identity', 'one fixture click and no replay', 'context and primary capture closed'], production_requests: 0 }));
  } finally {
    if (context) await context.close(); primary?.stop(); if (browser) await browser.close();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
