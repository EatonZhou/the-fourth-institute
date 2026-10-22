// Request contracts through the complete production observer and its JSONL writer.
// This fixture supplies transport events, not a browser, DOM or timing observer.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { observeBrowser } = require('../../../management_portal/test_framework/tau_browser_observer');

const origin = 'http://observer.invalid';

async function capture(inputs, inspect, observe = observeBrowser) {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-request-contract-'));
  const context = new EventEmitter();
  const page = new EventEmitter();
  const bindings = new Map();
  const scripts = [];
  // The document snapshot boundary returns no browser observations. Real
  // PerformanceObserver, frame disposal and transport wiring remain UI tests.
  const frame = {
    page: () => page, parentFrame: () => null, url: () => origin,
    evaluate: async () => undefined,
  };
  page.frames = () => [frame];
  page.mainFrame = () => frame;
  context.pages = () => [page];
  context.exposeBinding = async (name, callback) => bindings.set(name, callback);
  context.addInitScript = async script => scripts.push(script);
  let observer;
  let bodyReads = 0;
  try {
    observer = await observe(context, { evidenceDir });
    for (const name of ['request', 'response', 'requestfinished']) {
      assert.equal(context.listenerCount(name), 1, `production listener registered: ${name}`);
    }
    assert.equal(scripts.length, 1, 'production document probe registered');
    assert.equal(typeof scripts[0].content, 'string');
    const receive = bindings.get('__portalTauObservationRecord');
    assert.equal(typeof receive, 'function', 'production binding registered');
    // This is an input at the browser binding boundary, not a manufactured
    // request/document association. The production observer creates that ID.
    await receive({ frame }, {
      kind: 'document_started', token: randomUUID(), at: 1, time_origin: 1000,
    });
    const requests = inputs.map(input => ({
      url: () => new URL(input.url, origin).href,
      method: () => input.method || 'GET',
      postDataJSON: () => input.body,
      headers: () => ({ authorization: 'Bearer private-header' }),
      resourceType: () => 'fetch',
      frame: () => frame,
      redirectedFrom: () => null,
      isNavigationRequest: () => false,
      failure: () => null,
      timing: () => ({ startTime: 1000, requestStart: 1, responseStart: 2, responseEnd: 3 }),
    }));
    for (const request of requests) context.emit('request', request);
    // Responses deliberately arrive in a different order. Status and payload
    // are fixed fixture inputs; no scope, digest or expected join is computed.
    for (const request of [...requests].reverse()) {
      context.emit('response', {
        request: () => request,
        status: () => 404,
        text: async () => { bodyReads += 1; return 'private-response-body'; },
        body: async () => { bodyReads += 1; return Buffer.from('private-response-body'); },
      });
      context.emit('requestfinished', request);
    }
    await observer.flush();
    observer.stop();
    const output = fs.readFileSync(observer.file, 'utf8');
    const rows = output.trim().split('\n').map(line => JSON.parse(line));
    const starts = rows.filter(row => row.kind === 'request_started');
    assert.equal(starts.length, inputs.length, 'every request recorded once');
    assert.equal(new Set(starts.map(row => row.request_id)).size, inputs.length, 'request identities remain distinct');
    const document = rows.find(row => row.kind === 'document_started');
    assert.ok(document?.document_id, 'production document identity exists');
    for (let index = 0; index < starts.length; index += 1) {
      const start = starts[index];
      assert.equal(start.request_id, observer.requestId(requests[index]), 'public request identity matches output');
      assert.equal(start.document_id, document.document_id, 'request belongs to confirmed document');
      assert.equal(start.document_confirmed, true);
      assert.equal(start.document_association, 'frame_at_request_event');
      const responses = rows.filter(row => row.kind === 'response_received' && row.request_id === start.request_id);
      assert.equal(responses.length, 1, 'one response joined to each request');
      assert.equal(responses[0].response_status, 404, 'fixed failed response status preserved');
      const finished = rows.filter(row => row.kind === 'request_finished' && row.request_id === start.request_id);
      assert.equal(finished.length, 1, 'one completion joined to each request');
      assert.equal(finished[0].response_status, 404);
    }
    assert.equal(bodyReads, 0, 'observer must not read response content');
    assert.equal(rows.at(-1).kind, 'capture_ended');
    assert.equal(rows.at(-1).pending_request_count, 0);
    for (const name of context.eventNames()) assert.equal(context.listenerCount(name), 0, 'transport listener released');
    for (const name of page.eventNames()) assert.equal(page.listenerCount(name), 0, 'page listener released');
    inspect({ starts, rows, output });
  } finally {
    observer?.stop();
    fs.rmSync(evidenceDir, { recursive: true, force: true });
  }
}

function excludes(output, secrets) {
  for (const secret of secrets) assert.ok(!output.includes(secret), `must redact ${secret}`);
}

async function resourceContract(observe) {
  const urls = [
    '/api/agent-workbench/link-file?path=%2Fprivate-name.md&meta=1&token=query-secret',
    '/api/agent-workbench/link-file?path=%2Fprivate-name.md&token=other-secret',
    '/api/agent-workbench/link-file-raw?path=%2Fprivate-name.md',
    '/api/resources/resolve?address=resident_111111111111',
    '/api/resources/resolve?address=resident_222222222222',
    '/api/resources/reference?address=resident_111111111111',
    '/api/resources/content?address=resident_111111111111',
    '/api/private-name?path=%2Fprivate-name.md&meta=1',
    '/test/api/preview?token=query-secret',
    '/test/api/catalog/case?case_id=private-case-id',
    '/private-name/preview?token=other-secret',
  ];
  await capture(urls.map(url => ({ url })), ({ starts, output }) => {
    assert.deepEqual(starts.slice(0, 8).map(row => row.resource_scope?.mode || null),
      ['probe', 'read', 'read', 'resolve', 'resolve', 'reference', 'content', null], 'resource modes');
    assert.deepEqual(starts.slice(0, 7).map(row => row.resource_scope.kind),
      ['file', 'file', 'file', 'resource', 'resource', 'resource', 'resource']);
    const digests = starts.slice(0, 7).map(row => row.resource_scope.target_sha256);
    assert.equal(new Set(digests.slice(0, 3)).size, 1, 'same file retains probe/read identity');
    assert.equal(new Set(digests).size, 3, 'different resource targets stay distinct');
    for (const digest of digests) assert.match(digest, /^[0-9a-f]{64}$/, 'target identity is a SHA256 digest');
    assert.equal(starts[7].resource_scope, undefined, 'unknown endpoint has no resource scope');
    assert.deepEqual(starts.slice(8).map(row => row.path),
      ['/test/api/preview', '/test/api/catalog/case', '/[segment]/[segment]'],
      'public test routes remain distinguishable without exposing arbitrary path words');
    excludes(output, ['private-name', 'query-secret', 'other-secret', 'private-response-body',
      'resident_111111111111', 'resident_222222222222', 'private-header', 'private-case-id']);
  }, observe);
}

async function residentContract(observe) {
  const resident_id = 'resident_111111111111';
  const actualReads = [];
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../../../management_portal/static/portal_resident_service.js'), 'utf8'), { window, URLSearchParams });
  const client = window.PortalResidentService.createClient({ api: {
    get: async url => { actualReads.push({ url: `${url}&token=query-secret` }); return {}; },
  }});
  // Exercise the real client routes: hand-written obsolete ID-in-path examples
  // previously passed while every actual Resident GET lost target identity.
  await client.projection({ residentId: resident_id });
  await client.navigationTarget(resident_id);
  await client.sessionRestartOptions(resident_id);
  await client.watchboard(resident_id);
  await client.forwardTargets({ residentId: resident_id });
  await client.runtimeCatalog({ residentId: resident_id });
  const operations = ['DeleteResident', 'GetConversation', 'GetInputState', 'GetMailbox',
    'GetResidentInitialization', 'ListMessages', 'GetResidentRequest'];
  const inputs = [
    ...actualReads,
    ...operations.map(instruction => ({
      url: '/api/instructions/execute', method: 'POST',
      body: { instruction, arguments: { resident_id, message: 'private-message', token: 'private-token' } },
    })),
    { url: '/api/instructions/execute', method: 'POST',
      body: { instruction: 'private-instruction', arguments: { resident_id } } },
    ...['resident_222222222222', 'private-invalid-id'].map(target => ({
      url: '/api/instructions/execute', method: 'POST',
      body: { instruction: 'GetResident', arguments: { resident_id: target } },
    })),
    { url: `/api/agent-workbench/residents/${resident_id}` },
    { url: '/api/agent-workbench/residents/projection?id=private-invalid-id' },
    { url: `/api/agent-workbench/residents/projection?id=${resident_id}&id=resident_222222222222` },
    { url: `/api/agent-workbench/residents/projection?resident_id=${resident_id}` },
  ];
  await capture(inputs, ({ starts, output }) => {
    const scopes = starts.map(row => row.resource_scope);
    const known = actualReads.length + operations.length;
    assert.deepEqual(scopes.slice(0, known).map(scope => scope.mode),
      ['projection', 'navigation_target', 'restart_options', 'watchboard', 'forward_targets', 'runtime_catalog',
        ...operations], 'Resident lifecycle modes');
    assert.deepEqual(starts.slice(0, actualReads.length).map(row => row.path),
      actualReads.map(input => new URL(input.url, origin).pathname), 'complete public route retained');
    assert.equal(new Set(scopes.slice(0, known).map(scope => scope.target_sha256)).size, 1,
      'read and lifecycle operations share target identity');
    assert.equal(scopes[0].kind, 'resident');
    assert.match(scopes[0].target_sha256, /^[0-9a-f]{64}$/, 'resident identity is a SHA256 digest');
    assert.notEqual(scopes[known + 1].target_sha256, scopes[0].target_sha256, 'different Resident identity');
    assert.equal(scopes[known], undefined, 'private instruction has no resource scope');
    assert.equal(starts[known].instruction, null, 'private instruction name is excluded');
    for (const scope of scopes.slice(known + 2)) assert.equal(scope, undefined,
      'invalid, ambiguous, obsolete or missing targets have no inferred resource scope');
    excludes(output, ['resident_111111111111', 'resident_222222222222', 'query-secret',
      'private-response-body', 'private-message', 'private-token', 'private-instruction',
      'private-invalid-id', 'private-header']);
  }, observe);
}

module.exports = { resourceContract, residentContract };
if (require.main === module) {
  const contracts = { resource: resourceContract, resident: residentContract };
  const name = process.argv[2];
  assert.ok(Object.hasOwn(contracts, name), 'choose an explicit contract');
  contracts[name]().then(() => console.log(`contract passed: ${name}`)).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
