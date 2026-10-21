'use strict';

// Playwright page/request events are explicit boundary inputs. The production
// observer, injected document probe, JSONL writer and action lifecycle run here.
// HTTP echo uses an actual loopback server and Node fetch; native browser fetch,
// ResourceTiming and navigation remain in tau_browser_observability.spec.js.
const assert = require('node:assert/strict');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { capture } = require('./tau_observation_state.cjs');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function actionPageScope(observe) {
  const server = http.createServer((request, response) => {
    let body = '';
    request.setEncoding('utf8');
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      response.writeHead(200, { 'Content-Type': 'text/plain' });
      response.end(body || 'fetch-result-secret');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/api/instructions/execute`;
  try {
    await capture('action_page_scope', async ({ context, page, observer, main, newFrame, request, rows, output }) => {
      const other = new EventEmitter();
      const secondary = newFrame(null, other);
      other.mainFrame = () => secondary.frame;
      other.frames = () => [secondary.frame];
      context.emit('page', other);
      const pages = context.pages;
      context.pages = () => [...pages(), other];

      // The adapter reports real I/O outcomes to the same callbacks Playwright
      // invokes. It never calculates action IDs, membership or expected rows.
      async function post(frame, body = '') {
        const attempt = request(frame, { url, method: 'POST', body });
        context.emit('request', attempt);
        const response = await fetch(url, { method: 'POST', body });
        context.emit('response', { request: () => attempt, status: () => response.status });
        const text = await response.text();
        context.emit('requestfinished', attempt);
        return text;
      }

      const requestsDone = deferred();
      const ready = deferred();
      let operation;
      try {
        operation = observer.action(page, 'submit-and-ready', async () => {
          try {
            const values = await Promise.all([main.frame, secondary.frame]
              .map(frame => post(frame, 'private-action-body')));
            assert.deepEqual(values, ['private-action-body', 'private-action-body'], 'HTTP responses preserved');
          } finally { requestsDone.resolve(); }
          await ready.promise;
          return 'caller-result';
        });
        await requestsDone.promise;
        await observer.flush();
        assert.equal(rows().filter(row => row.kind === 'action_finished').length, 0,
          'action stays open until the callback ready check finishes');
        ready.resolve();
        assert.equal(await operation, 'caller-result', 'caller result preserved');
        assert.equal(await post(main.frame), 'fetch-result-secret', 'later HTTP response preserved');
        await observer.flush();

        const evidence = rows();
        const begins = evidence.filter(row => row.kind === 'action_started');
        const ends = evidence.filter(row => row.kind === 'action_finished');
        assert.equal(begins.length, 1, 'one action start');
        assert.equal(ends.length, 1, 'one action end');
        const [begin] = begins, [end] = ends;
        assert.deepEqual({ id: end.action_id, status: end.status, frame: end.frame_id, document: end.document_id },
          { id: begin.action_id, status: 'completed', frame: begin.frame_id, document: begin.document_id },
          'completed action retains frame and document');
        assert.ok(begin.frame_id && begin.document_id && begin.document_confirmed, 'actual injected probe identifies document');
        assert.ok(end.duration_ms > 0, 'action duration includes asynchronous work');

        const starts = evidence.filter(row => row.kind === 'request_started' && row.path === '/api/instructions/execute');
        assert.equal(starts.length, 3, 'both concurrent requests and the post-action request recorded');
        assert.equal(new Set(starts.map(row => row.request_id)).size, 3, 'each attempt has an independent ID');
        const owned = starts.filter(row => row.action_id === begin.action_id);
        assert.equal(owned.length, 1, 'only the observed page within the ready interval is owned');
        assert.equal(owned[0].page_id, begin.page_id, 'owned request belongs to the action page');
        assert.equal(owned[0].document_id, begin.document_id, 'owned request belongs to the active document');
        assert.equal(owned[0].action_association, 'observed_in_explicit_page_action_scope');
        const foreign = starts.find(row => row.page_id !== begin.page_id);
        assert.ok(foreign, 'other page has a distinct identity');
        assert.equal(foreign.action_id, null, 'same-URL foreign page is unowned');
        assert.notEqual(foreign.document_id, begin.document_id, 'separate document probe identity');
        const after = starts.at(-1);
        assert.equal(after.page_id, begin.page_id, 'later request belongs to the observed page');
        assert.equal(after.action_id, null, 'action ownership released before later request');
        assert.deepEqual(end.request_ids, [owned[0].request_id], 'only owned request appears in action receipt');
        assert.equal(end.causality, 'not_inferred_background_requests_may_share_scope', 'scope never asserts causality');
        assert.ok(!output().includes('private-action-body'), 'request/response content excluded');
        assert.ok(!output().includes('fetch-result-secret'), 'later response content excluded');
      } finally {
        ready.resolve();
        if (operation) await operation.catch(() => {});
        observer.stop();
        for (const event of other.eventNames()) assert.equal(other.listenerCount(event), 0, 'secondary page listeners cleaned');
      }
    }, observe);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

module.exports = { actionPageScope };
if (require.main === module) {
  assert.equal(process.argv[2], 'action-page-scope', 'explicit independent contract required');
  actionPageScope().then(() => console.log('PASS test_f0c7b8f3b651 action-page-scope; HTTP and DOM cleanup complete'))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
