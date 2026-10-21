// Exercise complete production modules at their transport/descriptor boundaries.
// No browser, live ISA execution, storage migration, or real business writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const STATIC = path.resolve(__dirname, '../../../../management_portal/static');
const plain = value => JSON.parse(JSON.stringify(value));
const i18n = { t: value => value, definition: value => value,
  message: value => value, requestLanguage: () => 'en' };
function load(file, globals = {}) {
  const context = vm.createContext({ console, setTimeout, clearTimeout,
    AbortController, TextDecoder, PortalI18n: i18n, ...globals });
  context.window = context;
  context.location ||= { pathname: '/', search: '', hash: '' };
  context.document ||= { title: '', readyState: 'loading', addEventListener() {} };
  context.addEventListener ||= () => {};
  context.__PORTAL_SITE__ = { compatibility: { format_namespaces: ['archive.'], browser_identifiers: {} } };
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'portal_state.js'), 'utf8'), context,
    { filename: 'portal_state.js' });
  vm.runInContext(fs.readFileSync(path.join(STATIC, file), 'utf8'), context, { filename: file });
  return context;
}

test('execute sends the canonical envelope and retries with unchanged business identity and arguments', async () => {
  const requests = [];
  const context = load('portal_instruction_client.js', {
    PortalAPI: { async requestResult(url, options) {
      requests.push({ url, ...options, body: plain(options.body) });
      return requests.length === 1
        ? { response: { ok: false, status: 503 }, payload: {
          status: 'unknown', error: { code: 'instruction.owner_unavailable', message: 'Isolated retry' },
        } }
        : { response: { ok: true, status: 200 }, payload: {
          schema: 'archive.hatg-instruction-result.v2', status: 'committed',
          instruction_id: 'historical-instruction-id', result: { replayed: true, version: 'existing-version' },
        } };
    } },
  });
  const args = { lineage_id: 'lineage_existing', key: 'existing-state', value: {
    schema: 'archive.existing-document.v1', content_digest: 'stored-digest', text: 'unchanged',
  } };
  const original = plain(args);
  const controller = new AbortController();
  const result = await context.PortalInstructionClient.execute('UpdateSharedState', args, {
    idempotencyKey: 'existing-business-key', expectedVersion: 0,
    signal: controller.signal, timeoutMs: 1200, keepalive: true,
  });
  assert.deepEqual(plain(result), { replayed: true, version: 'existing-version' });
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.url, '/api/instructions/execute');
    assert.deepEqual(request.body, {
      schema: 'tfi.hatg-instruction.v2', instruction: 'UpdateSharedState', arguments: original,
      idempotency_key: 'existing-business-key', expected_version: 0,
    });
    assert.equal(request.signal, controller.signal);
    assert.equal(request.keepalive, true);
    assert.equal(request.timeoutMs, 1200);
  }
  assert.notEqual(requests[0].headers['X-Portal-Request-ID'], requests[1].headers['X-Portal-Request-ID']);
  assert.deepEqual(args, original, 'nested legacy tags/digest must not be rewritten');
});

test('watch writes the canonical envelope while preserving cursor, idempotency, version and SSE settlement', async () => {
  const requests = [], batches = [];
  const bytes = new TextEncoder().encode('event: batch\ndata: {"items":[{"cursor":"cursor-next","value":"event"}]}\n\nevent: end\ndata: {}\n\n');
  const context = load('portal_instruction_client.js', {
    fetch: async (url, options) => {
      requests.push({ url, ...options, body: JSON.parse(options.body) });
      return { ok: true, status: 200, body: new ReadableStream({
        start(controller) { controller.enqueue(bytes); controller.close(); },
      }) };
    },
  });
  const args = { lineage_id: 'lineage_existing', after_cursor: 'cursor-existing' };
  const watch = context.PortalInstructionClient.watch('WatchLineage', args,
    items => batches.push(plain(items)), { idempotencyKey: 'existing-watch-key', expectedVersion: 'opaque-version' });
  await watch.done;
  assert.equal(requests.length, 1, 'explicit end settles instead of reconnecting');
  assert.deepEqual(requests[0].body, {
    schema: 'tfi.hatg-instruction.v2', instruction: 'WatchLineage', arguments: args,
    idempotency_key: 'existing-watch-key', expected_version: 'opaque-version',
  });
  assert.equal(requests[0].headers.Accept, 'text/event-stream');
  assert.deepEqual(batches, [[{ cursor: 'cursor-next', value: 'event' }]]);
  assert.equal(args.after_cursor, 'cursor-existing', 'reader progress must not mutate caller arguments');
});

test('new programs and the served primitive descriptor advertise canonical tags through the actual loader', async () => {
  const context = load('lineage_program.js', {
    PortalAPI: { get: async url => {
      assert.equal(url, '/static/lineage_primitive_definitions.json');
      return JSON.parse(fs.readFileSync(path.join(STATIC, 'lineage_primitive_definitions.json'), 'utf8'));
    } },
  });
  const api = context.PortalLineageProgram;
  const manifest = await api.loadPrimitiveDefinitions();
  assert.equal(manifest.primitive_definitions_schema, 'tfi.hatg-primitive-definitions.v1');
  assert.equal(manifest.default_types.event, 'resident.work_finished');
  assert.equal(manifest.program_schema, 'tfi.hatg-program.v2');
  assert.deepEqual(plain(api.authoredProgram({})), {
    schema: 'tfi.hatg-program.v2', components: [], wires: [],
  });
});

test('reading historical programs preserves the explicit schema and record identity without input mutation', () => {
  const api = load('lineage_program.js').PortalLineageProgram;
  const existing = { program: {
    schema: 'archive.hatg-program.v2', components: [], wires: [],
    record_id: 'record-existing', content_digest: 'digest-existing', idempotency_key: 'key-existing',
  } };
  const before = plain(existing);
  assert.deepEqual(plain(api.normalizeProgram(existing)), before.program);
  assert.deepEqual(plain(api.authoredProgram(existing)), before.program);
  assert.deepEqual(existing, before);
});
