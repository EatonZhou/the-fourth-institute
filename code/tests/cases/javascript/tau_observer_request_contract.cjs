// Request contracts through the complete production observer and its JSONL writer.
// This fixture supplies transport events, not a browser, DOM or timing observer.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID, webcrypto } = require('node:crypto');
const { observeBrowser } = require('../../../management_portal/test_framework/tau_browser_observer');

const origin = 'http://observer.invalid';

async function capture(inputs, inspect, observe = observeBrowser, injected = false) {
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
    let probe;
    if (injected) {
      probe = documentProbe(scripts[0].content, data => receive({ frame }, data));
    } else {
      await receive({ frame }, {
        kind: 'document_started', token: randomUUID(), at: 1, time_origin: 1000,
      });
    }
    const requests = inputs.map(input => ({
      url: () => new URL(input.url, origin).href,
      method: () => input.method || 'GET',
      postDataJSON: () => input.body,
      headers: () => ({ authorization: 'Bearer private-header', ...input.headers }),
      resourceType: () => input.resourceType || 'fetch',
      frame: () => frame,
      redirectedFrom: () => null,
      isNavigationRequest: () => false,
      failure: () => null,
      timing: () => ({ startTime: 1000, requestStart: 1, responseStart: 2, responseEnd: 3 }),
    }));
    for (const request of requests) context.emit('request', request);
    if (probe) await probe.exercise();
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

// Execute the exact production init script in a separate realm, as a browser
// does. The fixture supplies DOM/Performance events, not sanitized paths or
// manufactured transport associations. Real browser delivery remains UI TAU.
function documentProbe(content, receive) {
  const listeners = new Map(), pending = [];
  const documentListeners = new Map();
  const document = {
    hidden: false,
    addEventListener: (name, listener) => documentListeners.set(name, listener),
  };
  let performanceCallback;
  const window = {
    __portalTauObservationRecord: data => {
      const promise = Promise.resolve(receive(data)); pending.push(promise); return promise;
    },
    addEventListener: (name, listener) => listeners.set(name, listener),
  };
  vm.runInNewContext(content, {
    window, document, URL, crypto: webcrypto, queueMicrotask,
    performance: { timeOrigin: 1000, addEventListener() {} },
    PerformanceObserver: class {
      constructor(callback) { performanceCallback = callback; }
      observe() {}
      takeRecords() { return []; }
    },
  });
  const dispatch = (name, detail) => listeners.get(name)({ detail });
  return { async exercise() {
    dispatch('focus', { private: 'focus-private-content' });
    dispatch('blur', { private: 'focus-private-content' });
    document.hidden = true;
    documentListeners.get('visibilitychange')({ detail: 'focus-private-content' });
    for (const stream_id of ['unique-stream', 'reconnected-stream', 'unknown-stream']) {
      const detail = { url: `${origin}/api/events?portal_stream_id=${stream_id}&token=query-secret`, stream_id,
        at: '2026-10-23T00:00:00Z', reason: 'page_closed' };
      dispatch('portal:connection-ready', detail);
      dispatch('portal:connection-closed', detail);
    }
    dispatch('portal:connection-closed', { url: `${origin}/api/instructions/execute?token=query-secret`,
      transport_id: 'unique-request', at: '2026-10-23T00:00:00Z', reason: 'caller_aborted' });
    for (const transport_id of ['state-request', 'ambiguous-state', 'unknown-state', 'unique-request']) {
      dispatch('portal:state-read', { transport_id, consumer: 'inbox', reader_id: 'state-1',
        reasons: ['focus', 'private-read-reason'], private: 'private-read-content' });
    }
    dispatch('portal:state-read', { transport_id: 'state-request', consumer: 'private-consumer',
      reader_id: 'private-reader', reasons: null });
    performanceCallback({ getEntries: () => [
      { name: `${origin}/api/agent-workbench/residents/projection?id=private-resident&token=query-secret`, initiatorType: 'fetch' },
      { name: `${origin}/api/private-name?token=query-secret`, initiatorType: 'fetch' },
    ] });
    await Promise.all(pending);
  } };
}

async function injectedContract(observe) {
  await capture([
    { url: '/api/events?portal_stream_id=unique-stream', resourceType: 'eventsource' },
    { url: '/api/events?portal_stream_id=reconnected-stream', resourceType: 'eventsource' },
    { url: '/api/events?portal_stream_id=reconnected-stream', resourceType: 'eventsource' },
    { url: '/api/instructions/execute', method: 'POST', headers: { 'x-portal-request-id': 'unique-request' } },
    ...['state-request', 'ambiguous-state', 'ambiguous-state'].map(transportId => ({
      url: '/api/instructions/execute', method: 'POST', headers: { 'x-portal-request-id': transportId },
      body: { instruction: 'ListResidentStates', arguments: { resident_ids: ['private-resident'] } },
    })),
  ], ({ rows, starts, output }) => {
    const ready = rows.filter(row => row.kind === 'portal_stream_ready');
    const closed = rows.filter(row => row.kind === 'portal_connection_closed');
    assert.equal(ready.length, 3); assert.equal(closed.length, 4);
    assert.equal(ready[0].path, '/api/events');
    assert.equal(ready[0].request_id, starts[0].request_id);
    assert.equal(closed[0].request_id, starts[0].request_id);
    assert.equal(closed[3].path, '/api/instructions/execute');
    assert.equal(closed[3].request_id, starts[3].request_id);
    for (const row of [...ready.slice(1), ...closed.slice(1, 3)]) {
      assert.equal(row.request_id, null, 'duplicate and missing identities remain unknown');
      assert.equal(row.request_association, 'unavailable_no_unique_transport_identity');
    }
    assert.deepEqual(rows.filter(row => row.kind === 'resource_timing').map(row => row.path),
      ['/api/agent-workbench/residents/projection', '/api/[segment]']);
    const lifecycle = rows.filter(row => ['focus', 'blur', 'visibilitychange'].includes(row.kind));
    assert.deepEqual(lifecycle.map(row => [row.kind, row.hidden]),
      [['focus', false], ['blur', false], ['visibilitychange', true]]);
    const document = rows.find(row => row.kind === 'document_started');
    for (const event of lifecycle) {
      assert.equal(event.document_id, document.document_id);
      assert.equal(event.document_confirmed, true);
      assert.equal(typeof event.browser_observed_at_ms, 'number');
      assert.equal(event.request_id, undefined, 'a lifecycle event alone does not prove request causation');
    }
    const reads = rows.filter(row => row.kind === 'portal_state_read');
    assert.equal(reads.length, 5);
    assert.equal(reads[0].request_id, starts[4].request_id);
    assert.equal(reads[0].request_association, 'portal_request_identity');
    assert.equal(reads[0].consumer, 'inbox');
    assert.equal(reads[0].reader_id, 'state-1');
    assert.deepEqual(reads[0].reasons, ['focus', 'unknown']);
    for (const row of reads.slice(1, 4)) {
      assert.equal(row.request_id, null, 'missing, ambiguous or different instruction identities are not causal evidence');
      assert.equal(row.request_association, 'unavailable_no_unique_transport_identity');
    }
    assert.equal(reads[4].consumer, 'unknown');
    assert.equal(reads[4].reader_id, null);
    assert.deepEqual(reads[4].reasons, ['unknown']);
    excludes(output, ['query-secret', 'private-name', 'private-resident', 'private-header', 'focus-private-content',
      'private-read-reason', 'private-read-content', 'private-consumer', 'private-reader']);
  }, observe, true);
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

module.exports = { resourceContract, residentContract, injectedContract };
if (require.main === module) {
  const contracts = { resource: resourceContract, resident: residentContract, injected: injectedContract };
  const name = process.argv[2];
  assert.ok(Object.hasOwn(contracts, name), 'choose an explicit contract');
  contracts[name]().then(() => console.log(`contract passed: ${name}`)).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
