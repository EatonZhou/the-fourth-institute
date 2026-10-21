// Exercise the complete production observer and its injected receipt serializer.
// EventEmitter inputs replace Playwright transport only; jsdom does not claim
// native request cancellation, EventSource, navigation or ResourceTiming coverage.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { observeBrowser } = require('../../../management_portal/test_framework/tau_browser_observer');

const origin = 'http://observation.invalid';

async function capture(name, inspect, observe = observeBrowser) {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-observation-state-'));
  const context = new EventEmitter();
  const page = new EventEmitter();
  const frames = [];
  const documents = [];
  const bindings = new Map();
  let initScript, observer;
  context.pages = () => [page];
  context.exposeBinding = async (binding, callback) => bindings.set(binding, callback);
  context.addInitScript = async script => { initScript = script.content; };
  page.frames = () => frames.filter(frame => frame.page() === page);
  page.mainFrame = () => frames[0];
  function openDocument(frame) {
    const dom = new JSDOM('<!doctype html><body>Ready</body>', {
      url: origin, runScripts: 'outside-only',
    });
    documents.push(dom);
    frame.dom = dom;
    for (const [binding, callback] of bindings) {
      dom.window[binding] = data => Promise.resolve(callback({ frame }, data));
    }
    // Execute the production addInitScript unchanged. jsdom has no resource
    // timing observer, so production records observer_unavailable itself.
    dom.window.eval(initScript);
    const closed = [];
    dom.window.PortalEvents = { closedStreams: () => closed };
    return { window: dom.window, closed };
  }
  function newFrame(parent = null, owner = page) {
    const frame = {
      page: () => owner, parentFrame: () => parent, url: () => origin,
      evaluate: async callback => frame.dom.window.eval(`(${callback.toString()})()`),
    };
    frames.push(frame);
    return { frame, ...openDocument(frame) };
  }
  function request(frame, input) {
    return {
      url: () => new URL(input.url, origin).href,
      method: () => input.method || 'GET',
      resourceType: () => input.resourceType || 'fetch',
      headers: () => input.headers || {},
      postDataJSON: () => JSON.parse(input.body || '{}'),
      frame: () => frame, redirectedFrom: () => null,
      isNavigationRequest: () => false,
      failure: () => input.failure || null,
      timing: () => input.timing || {
        startTime: 1000, requestStart: 1, responseStart: 2, responseEnd: 3,
      },
    };
  }
  try {
    observer = await observe(context, { evidenceDir });
    const main = newFrame();
    const output = () => fs.readFileSync(observer.file, 'utf8');
    const rows = () => output().trim().split('\n').map(line => JSON.parse(line));
    await inspect({ context, page, observer, main, newFrame, openDocument, request, rows, output });
  } finally {
    observer?.stop();
    for (const dom of documents) dom.window.close();
    if (process.env.TAU_OBSERVATION_STATE_EVIDENCE && observer) {
      fs.mkdirSync(process.env.TAU_OBSERVATION_STATE_EVIDENCE, { recursive: true });
      fs.copyFileSync(observer.file, path.join(process.env.TAU_OBSERVATION_STATE_EVIDENCE, `${name}.jsonl`));
    }
    fs.rmSync(evidenceDir, { recursive: true, force: true });
  }
}

function excludes(output, secrets) {
  for (const secret of secrets) assert.ok(!output.includes(secret), `must redact ${secret}`);
}

async function receiptIdentity(kind, reused, observe, reason = "AbortError") {
  const stream = kind === 'stream';
  const name = `${kind}_${reused ? 'reused' : 'unique'}`;
  await capture(name, async ({ context, observer, main, newFrame, openDocument, request, rows, output }) => {
    const ids = stream
      ? ['stream-one', reused ? 'stream-one' : 'stream-two']
      : ['request-one', reused ? 'request-one' : 'request-two'];
    const input = id => stream ? {
      url: `/api/events?portal_stream_id=${id}&auth=event-query-secret`,
      resourceType: 'eventsource',
    } : {
      url: '/api/instructions/execute?secret=fetch-query-secret', method: 'POST',
      headers: { 'x-portal-request-id': id, authorization: 'Bearer fetch-header-secret' },
      body: 'fetch-body-secret', failure: { errorText: 'net::ERR_ABORTED' },
    };
    // A previous document and a child frame reuse the same identifier. Neither
    // may contaminate this document's unique match or become a fallback match.
    const prior = request(main.frame, input(ids[0]));
    context.emit('request', prior);
    const priorDocument = rows().find(row => row.request_id === observer.requestId(prior)).document_id;
    const current = openDocument(main.frame);
    const child = newFrame(main.frame);
    const childRequest = request(child.frame, input(ids[0]));
    context.emit('request', childRequest);
    const requests = ids.map(id => request(main.frame, input(id)));
    for (const item of requests) context.emit('request', item);
    if (stream) {
      for (const item of requests) context.emit('response', { request: () => item, status: () => 200 });
    } else context.emit('requestfailed', requests[0]);
    current.closed.push({
      url: stream ? '/api/events?auth=event-query-secret' : '/api/instructions/execute?secret=fetch-query-secret',
      ...(stream ? { stream_id: ids[0] } : { transport_id: ids[0] }),
      reason: stream ? 'handover_complete' : reason, at: '2026-10-16T01:02:03.000Z',
    });
    await observer.flush();
    const starts = rows().filter(row => row.kind === 'request_started'
      && requests.some(item => observer.requestId(item) === row.request_id));
    assert.equal(starts.length, 2, 'both current HTTP attempts are observed');
    assert.notEqual(starts[0].request_id, starts[1].request_id, 'attempt identity is not the transport label');
    assert.notEqual(starts[0].document_id, priorDocument, 'a fresh document has a fresh observation identity');
    const sameId = starts.filter(row => (stream ? row.stream_id : row.transport_id) === ids[0]);
    assert.equal(sameId.length, reused ? 2 : 1, 'reused identifier retains both candidates');
    const closes = rows().filter(row => row.kind === 'portal_connection_closed');
    assert.equal(closes.length, 1, 'one owner receipt is recorded');
    const closed = closes[0];
    assert.equal(closed.request_id, reused ? null : observer.requestId(requests[0]), 'only a unique same-frame/document attempt may match');
    assert.equal(closed.request_association, reused ? 'unavailable_no_unique_transport_identity'
      : (stream ? 'portal_stream_identity' : 'portal_request_identity'));
    assert.equal(closed.document_id, starts[0].document_id);
    assert.equal(closed.frame_id, starts[0].frame_id);
    assert.equal(closed.document_confirmed, true);
    assert.equal(closed.reason, stream ? 'handover_complete' : (reason === 'private-unknown-reason' ? 'unknown' : reason));
    assert.equal(closed.closed_at_ms, 1792112523000, 'production document probe serializes the receipt timestamp');
    assert.equal(closed.path, stream ? '/api/events' : '/api/instructions/execute');
    assert.equal(stream ? closed.stream_id : closed.transport_id, ids[0]);
    if (!stream) {
      const failed = rows().filter(row => row.kind === 'request_failed');
      assert.equal(failed.length, 1);
      assert.equal(failed[0].request_id, observer.requestId(requests[0]));
      assert.equal(failed[0].failed_reason, 'net::ERR_ABORTED');
      assert.equal(failed[0].cancellation_cause, 'unknown', 'receipt association does not rewrite transport failure');
    }
    await observer.flush();
    assert.equal(rows().filter(row => row.kind === 'portal_connection_closed').length, 1,
      'supplemental snapshots do not duplicate the same receipt');
    excludes(output(), stream ? ['event-query-secret']
      : ['fetch-query-secret', 'fetch-body-secret', 'fetch-header-secret']);
  }, observe);
}

async function streamReady(observe) {
  await capture('stream_ready', async ({ context, observer, main, request, rows, output }) => {
    const win = main.window;
    delete win.PortalEvents;
    Object.defineProperty(win.document, 'hidden', { get: () => false });
    const timers = new Map(), streams = [];
    let timerId = 0;
    win.setTimeout = (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; };
    win.clearTimeout = id => timers.delete(id);
    win.setInterval = () => ++timerId;
    win.clearInterval = () => {};
    win.EventSource = class extends win.EventTarget {
      constructor(url) {
        super(); this.url = url; this.closed = false;
        this.request = request(main.frame, { url, resourceType: 'eventsource' });
        streams.push(this); context.emit('request', this.request);
      }
      close() { this.closed = true; }
    };
    const source = fs.readFileSync(path.join(__dirname, '../../../management_portal/static/portal_events.js'), 'utf8');
    win.eval(source);
    const readyRows = () => rows().filter(row => row.kind === 'portal_stream_ready');
    const emitReady = stream => stream.dispatchEvent(new win.MessageEvent('portal-ready', {
      data: '{"private":"event-body-secret"}',
    }));
    assert.equal(streams.length, 1);
    context.emit('response', { request: () => streams[0].request, status: () => 200 });
    streams[0].dispatchEvent(new win.Event('open'));
    assert.equal(readyRows().length, 0, 'HTTP/open cannot substitute for application ready');
    emitReady(streams[0]);
    assert.equal(readyRows()[0].request_id, observer.requestId(streams[0].request));
    assert.equal(readyRows()[0].predecessor_stream_id, null);
    win.PortalEvents.watchResident('resident-test');
    const refresh = [...timers].find(([, timer]) => timer.delay === 150);
    assert.ok(refresh); timers.delete(refresh[0]); refresh[1].callback();
    assert.equal(streams.length, 2);
    assert.equal(streams[0].closed, false, 'old stream stays live until successor ready');
    emitReady(streams[1]);
    const successor = readyRows()[1];
    const closed = rows().find(row => row.kind === 'portal_connection_closed');
    assert.equal(successor.request_id, observer.requestId(streams[1].request));
    assert.equal(successor.predecessor_stream_id, readyRows()[0].stream_id);
    assert.equal(closed.stream_id, successor.predecessor_stream_id);
    assert.equal(closed.successor_stream_id, successor.stream_id);
    assert.equal(closed.reason, 'handover_complete');
    assert.equal(closed.request_id, observer.requestId(streams[0].request));
    assert.equal(successor.document_id, closed.document_id);
    assert.equal(successor.frame_id, closed.frame_id);
    assert.ok(successor.ready_at_ms <= closed.closed_at_ms);
    assert.equal(streams[0].closed, true);
    emitReady(streams[0]);
    assert.equal(readyRows().length, 2, 'late ready from retired stream is ignored');
    // A native reconnect can reuse a label. Preserve the actual ready event,
    // but never guess which HTTP attempt supplied it.
    context.emit('request', request(main.frame, { url: streams[1].url, resourceType: 'eventsource' }));
    emitReady(streams[1]);
    assert.equal(readyRows()[2].request_id, null);
    assert.equal(readyRows()[2].request_association, 'unavailable_no_unique_transport_identity');
    win.PortalEvents.close();
    await observer.flush();
    const finalClose = rows().filter(row => row.kind === 'portal_connection_closed').at(-1);
    assert.equal(finalClose.reason, 'page_closed');
    assert.equal(finalClose.successor_stream_id, null, 'terminal close has no invented successor');
    excludes(output(), ['event-body-secret']);
  }, observe);
}

async function pendingCapture(observe) {
  await capture('pending_capture', async ({ context, page, observer, main, request, rows, output }) => {
    const pendingRequest = request(main.frame, {
      url: '/api/resources/file',
      timing: { startTime: 1000, requestStart: 1, responseStart: -1, responseEnd: -1 },
    });
    context.emit('request', pendingRequest);
    const requestId = observer.requestId(pendingRequest);
    assert.ok(requestId);
    const start = rows().find(row => row.kind === 'request_started' && row.request_id === requestId);
    assert.equal(start.path, '/api/resources/file');
    await observer.flush();
    observer.stop();
    observer.stop();
    const pending = rows().filter(row => row.kind === 'request_pending_at_capture_end' && row.request_id === requestId);
    assert.equal(pending.length, 1, 'capture stop records a pending attempt once');
    assert.equal(pending[0].ended_at_ms, null, 'no terminal time is invented');
    assert.equal(pending[0].response_status, null, 'no HTTP status is invented');
    assert.equal(pending[0].cancellation_cause, 'unknown');
    assert.equal(pending[0].timing.values.responseEnd, null);
    assert.ok(pending[0].timing.unavailable.responseEnd, 'unavailable timing remains explicit');
    assert.equal(pending[0].timing.server_execution_time, null);
    assert.equal(rows().filter(row => ['request_finished', 'request_failed'].includes(row.kind)).length, 0);
    const ended = rows().filter(row => row.kind === 'capture_ended');
    assert.equal(ended.length, 1, 'capture stop is idempotent');
    assert.equal(ended[0].pending_request_count, 1);
    const beforeClose = output();
    page.emit('close');
    context.emit('requestfailed', pendingRequest);
    main.window.dispatchEvent(new main.window.Event('pagehide'));
    await observer.flush();
    assert.equal(output(), beforeClose, 'late transport and document events cannot change a stopped capture');
    for (const event of context.eventNames()) assert.equal(context.listenerCount(event), 0, 'transport listener released');
    for (const event of page.eventNames()) assert.equal(page.listenerCount(event), 0, 'page listener released');
  }, observe);
}

const contracts = {
  stream_unique: observe => receiptIdentity('stream', false, observe),
  stream_reused: observe => receiptIdentity('stream', true, observe),
  stream_ready: streamReady,
  cancellation_unique: observe => receiptIdentity('cancellation', false, observe),
  cancellation_reused: observe => receiptIdentity('cancellation', true, observe),
  first_batch_read: observe => receiptIdentity('cancellation', false, observe, 'first_batch_read'),
  unknown_close_reason: observe => receiptIdentity('cancellation', false, observe, 'private-unknown-reason'),
  pending_capture: pendingCapture,
};
module.exports = { contracts, capture };
if (require.main === module) {
  const name = process.argv[2];
  assert.ok(Object.hasOwn(contracts, name), 'choose an explicit observation contract');
  contracts[name]().then(() => console.log(`contract passed: ${name}`)).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
