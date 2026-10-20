import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {observeModelRequests} from '../../../management_portal/dsh_runtime/request_timing.mjs';

const receipts = [];
const server = createServer(async (req, res) => {
  receipts.push(Date.now());
  await new Promise(resolve => setTimeout(resolve, 30));
  res.end('response');
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const original = globalThis.fetch;
const starts = [];
const stop = observeModelRequests((id, at) => starts.push({id, at: Date.parse(at)}));
try {
  const url = `http://127.0.0.1:${server.address().port}/v1/messages`;
  const response = await fetch(url, {method:'POST', headers:{'x-deepseek-harness-session-id':'root'}});
  assert.equal(await response.text(), 'response');
  assert.equal(starts[0].id, 'root');
  assert.ok(starts[0].at <= receipts[0]);
  assert.ok(Date.now() - starts[0].at >= 30);
  await (await fetch(new Request(url, {method:'POST', headers:{'x-deepseek-harness-session-id':'child'}}))).text();
  assert.equal(starts[1].id, 'child');
  await (await fetch(url, {headers:{'x-deepseek-harness-session-id':'root', 'x-deepseek-harness-compact':'1'}})).text();
  await (await fetch(url)).text();
  assert.equal(starts.length, 2); // Neither compaction nor unrelated traffic borrows a model start.
  stop();
  const calls = [];
  const sentinel = Promise.resolve({status: 403});
  const stopBroken = observeModelRequests(() => { throw new Error('collector unavailable'); },
    function (...args) { calls.push(args); return sentinel; });
  try {
    const init = {method: 'POST', headers: {'x-deepseek-harness-session-id': 'root', Authorization: 'test-only'},
      body: 'unchanged', signal: new AbortController().signal};
    assert.equal(fetch(url, init), sentinel);
    assert.deepEqual(calls, [[url, init]]);
    assert.equal(calls[0][1], init);
  } finally { stopBroken(); globalThis.fetch = original; }
} finally {
  stop();
  assert.equal(globalThis.fetch, original);
  server.closeAllConnections();
  server.close();
}
