const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { observeBrowser } = require('../../../management_portal/test_framework/tau_browser_observer');

test('passive observation writer emits tfi and retains capture identity', async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tfi-writer-'));
  const context = new EventEmitter();
  context.pages = () => [];
  context.exposeBinding = async () => {};
  context.addInitScript = async () => {};
  try {
    const observer = await observeBrowser(context, { evidenceDir });
    await observer.flush();
    observer.stop();
    const lines = fs.readFileSync(observer.file, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(lines.map(row => row.kind), ['capture_started', 'capture_ended']);
    assert.equal(new Set(lines.map(row => row.capture_id)).size, 1);
    for (const row of lines) {
      assert.equal(row.schema, 'tfi.tau-browser-observation.v1');
      assert.equal(typeof row.observed_at_ms, 'number');
      assert.match(row.capture_id, /^[0-9a-f-]{36}$/);
    }
    assert.equal(lines[1].pending_request_count, 0);
  } finally { fs.rmSync(evidenceDir, { recursive: true, force: true }); }
});

function instructionWriter(context) {
  const source = fs.readFileSync(path.join(__dirname, '../../../management_portal/test_framework/tau_browser.js'), 'utf8');
  const start = source.indexOf('  async function executeInstruction(');
  const end = source.indexOf('\n  async function inputState(', start);
  assert.ok(start >= 0 && end > start);
  // Execute the original private request writer, without openPortal's host,
  // login or browser launch. Transport, retry clock and request IDs are explicit fakes.
  let next = 0;
  return new Function('context', 'origin', 'safeTransport', 'setTimeout', 'randomUUID', 'instructionFailure',
    source.slice(start, end) + '\nreturn executeInstruction;')(
      context, 'http://fixture.invalid', async (_, operation) => operation(), callback => callback(),
      () => `request-${++next}`, error => ({ message: String(error?.message || error) }));
}

test('ISA producer preserves request payload and one idempotency key across retries', async () => {
  const calls = [];
  const log = [];
  const writer = instructionWriter({ request: { post: async (url, options) => {
    calls.push({ url, options });
    const body = calls.length === 1
      ? { status: 'failed', error: { code: 'instruction.projection_pending' } }
      : { status: 'committed', result: { message_id: 'msg_original' } };
    return { ok: () => true, json: async () => body, status: () => 200 };
  } } });
  const args = { resident_id: 'resident_0123456789ab', message_id: 'msg_original' };
  assert.deepEqual(await writer('GetInputState', args, { key: 'original-key', attempts: 2, append: entry => log.push(entry) }), { message_id: 'msg_original' });
  assert.equal(calls.length, 2);
  // Each HTTP attempt has its own request ID; the ISA call and its idempotency key stay the same.
  calls.forEach((call, index) => {
    assert.equal(call.url, '/api/instructions/execute');
    assert.deepEqual(call.options, { data: { schema: 'tfi.hatg-instruction.v2', instruction: 'GetInputState',
      arguments: args, idempotency_key: 'original-key' }, headers: { Origin: 'http://fixture.invalid', 'X-Portal-Request-ID': `request-${index + 1}` } });
  });
  assert.equal(calls[0].options.data, calls[1].options.data);
  assert.deepEqual(args, { resident_id: 'resident_0123456789ab', message_id: 'msg_original' });
  assert.deepEqual(log.map(entry => [entry.kind, entry.attempt, entry.request_id, entry.instruction_status ?? null]), [
    ['request_started', 1, 'request-1', null], ['request_finished', 1, 'request-1', 'failed'],
    ['request_started', 2, 'request-2', null], ['request_finished', 2, 'request-2', 'committed'],
  ]);
});

test('ISA producer retains error and does not invent a missing key', async () => {
  let data;
  const writer = instructionWriter({ request: { post: async (_, options) => {
    data = options.data;
    return { ok: () => false, status: () => 409,
      json: async () => ({ error: { code: 'instruction.conflict', message: 'fixture', details: { unchanged: true } } }) };
  } } });
  await assert.rejects(writer('GetInputState', {}, { key: '', attempts: 1, append: () => {} }), error => {
    assert.equal(error.code, 'instruction.conflict');
    assert.equal(error.status, 409);
    assert.deepEqual(error.details, { unchanged: true });
    return true;
  });
  assert.equal(data.schema, 'tfi.hatg-instruction.v2');
  assert.equal(Object.hasOwn(data, 'idempotency_key'), false);
});
