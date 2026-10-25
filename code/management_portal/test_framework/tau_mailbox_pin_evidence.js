'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');

// Start on the actual page before its one native Pin click. Observe only that
// target's original request; never issue an instruction, retry, or read a Store.
function observeMailboxPin({page, origin, entryKey, evidenceDir, requestId} = {}) {
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || !page?.on
      || !/^resident:resident_[a-f0-9]{12}$/.test(entryKey || '')
      || typeof requestId !== 'function' || !fs.statSync(evidenceDir).isDirectory()) {
    throw new TypeError('Use the original page, Portal origin, exact entry key, evidence directory and portal.requestId');
  }
  const captureId = randomUUID(), records = [], requests = new WeakMap(), tasks = new Set(), waiters = new Set();
  const file = path.join(evidenceDir, 'tau-mailbox-pin.jsonl');
  let stopped = false;
  const snapshot = () => ({capture_id: captureId, entry_key: entryKey, stopped,
    matching_requests: records.length, ambiguous: records.length > 1,
    records: records.map(record => ({...record, item: record.item ? {...record.item} : null}))});
  const write = (kind, record = {}) => fs.appendFileSync(file,
    JSON.stringify({capture_id: captureId, kind, observed_at_ms: Date.now(), entry_key: entryKey, ...record}) + '\n');
  const notify = () => { for (const waiter of [...waiters]) waiter(); };
  const started = request => {
    let url, body;
    try {
      url = new URL(request.url());
      if (url.origin !== base.origin || url.pathname !== '/api/instructions/execute' || request.method() !== 'POST') return;
      const data = request.postData();
      if (!data || Buffer.byteLength(data) > 8192) return;
      body = JSON.parse(data);
    } catch { return; }
    if (body?.instruction !== 'PinMailboxItem' || body?.arguments?.key !== entryKey || body.arguments.pinned !== true) return;
    const record = {request_id: requestId(request) || null, instruction: 'PinMailboxItem',
      http_status: null, instruction_id: null, status: null, replayed: null,
      item: null, committed: false, completed: false, error: null};
    records.push(record); requests.set(request, record);
    write('pin_request', record); notify();
  };
  const received = response => {
    const record = requests.get(response.request());
    if (!record) return;
    const task = (async () => {
      record.http_status = response.status();
      try {
        if (!record.request_id) { record.error = 'missing_original_request_identity'; return; }
        if (!/^application\/json(?:\s*;|$)/i.test(response.headers()['content-type'] || '')) {
          record.error = 'not_json'; return;
        }
        const text = await response.text();
        if (Buffer.byteLength(text) > 65536) { record.error = 'body_exceeds_bound'; return; }
        const body = JSON.parse(text), item = body?.result?.item;
        record.instruction_id = typeof body?.instruction_id === 'string' && /^inst_[a-z0-9_]{1,100}$/.test(body.instruction_id)
          ? body.instruction_id : null;
        record.status = ['committed', 'failed', 'rejected', 'not_committed'].includes(body?.status) ? body.status : null;
        record.replayed = typeof body?.replayed === 'boolean' ? body.replayed : null;
        // Do not persist another item's identity, response messages, headers or contents.
        record.item = item?.key === entryKey ? {key: entryKey, pinned: typeof item.pinned === 'boolean' ? item.pinned : null,
          read_revision: Number.isSafeInteger(item.read_revision) ? item.read_revision : null} : null;
        record.committed = record.http_status >= 200 && record.http_status < 300
          && body?.instruction === 'PinMailboxItem' && record.instruction_id !== null
          && record.status === 'committed' && record.replayed === false && record.item?.pinned === true;
        if (!record.committed) record.error = 'original_receipt_not_committed_for_target';
      } catch { record.error = 'original_response_unreadable'; }
      finally { record.completed = true; write('pin_response', record); notify(); }
    })();
    tasks.add(task); task.then(() => tasks.delete(task), () => tasks.delete(task));
  };
  const failed = request => {
    const record = requests.get(request);
    if (!record || record.completed) return;
    record.completed = true; record.error = 'original_request_failed';
    write('pin_request_failed', record); notify();
  };
  page.on('request', started); page.on('response', received); page.on('requestfailed', failed);
  write('pin_capture_started');
  return Object.freeze({file, snapshot,
    waitForReceipt({timeoutMs = 20000} = {}) {
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60000) throw new TypeError('Use a bounded receipt wait');
      return new Promise((resolve, reject) => {
        let timer;
        const finish = (error, value) => { clearTimeout(timer); waiters.delete(check); error ? reject(new Error(error)) : resolve(value); };
        const check = () => {
          if (records.length > 1) return finish('Ambiguous original PinMailboxItem requests');
          if (records[0]?.completed) return records[0].committed ? finish(null, snapshot()) : finish('Original PinMailboxItem receipt is incomplete or not committed');
          if (stopped) finish('Original PinMailboxItem capture stopped before receipt');
        };
        timer = setTimeout(() => finish('Original PinMailboxItem receipt timeout'), timeoutMs);
        waiters.add(check); check();
      });
    },
    async stop() {
      if (!stopped) {
        stopped = true;
        page.off('request', started); page.off('response', received); page.off('requestfailed', failed);
        notify();
      }
      await Promise.all([...tasks]);
      return snapshot();
    },
  });
}
module.exports = {observeMailboxPin};
