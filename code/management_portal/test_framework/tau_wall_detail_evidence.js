'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash, randomUUID} = require('node:crypto');

const hash = text => createHash('sha256').update(text).digest('hex');
const resident = value => typeof value === 'string' && (value === '' || /^resident_[a-f0-9]{12}$/.test(value));

// Observe the original click's exact GET; never open a card, issue a request,
// inspect application state, or fill in an earlier missing response.
function observeWallDetail({page, origin, projectId, blockId, evidenceDir, requestId} = {}) {
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || !page?.on || typeof requestId !== 'function'
      || !fs.statSync(evidenceDir).isDirectory()
      || [projectId, blockId].some(id => typeof id !== 'string' || !id || id.length > 4096 || /[\x00-\x1f\x7f]/.test(id))) {
    throw new TypeError('Use the original page, visible project/block IDs and portal.requestId');
  }
  const captureId = randomUUID(), requests = new WeakMap(), records = [], tasks = new Set(), waiters = new Set();
  const file = path.join(evidenceDir, 'tau-wall-detail.jsonl');
  let stopped = false;
  const snapshot = () => JSON.parse(JSON.stringify({capture_id: captureId, project_id: projectId,
    block_id: blockId, matching_requests: records.length, ambiguous: records.length > 1, stopped, records}));
  const write = (kind, record = {}) => fs.appendFileSync(file, JSON.stringify({capture_id: captureId,
    observed_at_ms: Date.now(), kind, project_id: projectId, block_id: blockId, ...record}) + '\n');
  const notify = () => { for (const waiter of [...waiters]) waiter(); };
  const started = request => {
    let url;
    try { url = new URL(request.url()); } catch { return; }
    if (url.origin !== base.origin || url.pathname !== '/api/wall/block' || request.method() !== 'GET'
        || url.searchParams.getAll('project').length !== 1 || url.searchParams.get('project') !== projectId
        || url.searchParams.getAll('block').length !== 1 || url.searchParams.get('block') !== blockId) return;
    const record = {request_id: requestId(request) || null, http_status: null, completed: false,
      authoritative: false, block: null, error: null};
    requests.set(request, record); records.push(record); write('detail_request', record); notify();
  };
  const received = response => {
    const record = requests.get(response.request());
    if (!record) return;
    const task = (async () => {
      record.http_status = response.status();
      try {
        if (!record.request_id) { record.error = 'missing_original_request_identity'; return; }
        if (record.http_status !== 200 || !/^application\/json(?:\s*;|$)/i.test(response.headers()['content-type'] || '')) {
          record.error = 'original_detail_response_failed'; return;
        }
        const text = await response.text();
        if (Buffer.byteLength(text) > 2097152) { record.error = 'body_exceeds_bound'; return; }
        const block = JSON.parse(text)?.block;
        // A missing discussion field is unknown, not an empty discussion. The
        // top-level resident_id can belong to work and cannot substitute for it.
        if (block?.id !== blockId || typeof block.content !== 'string' || !block.content.trim()
            || !resident(block.discussion?.resident_id) || !block.job || typeof block.job !== 'object'
            || Array.isArray(block.job)) { record.error = 'incomplete_original_detail'; return; }
        const job = block.job;
        if ((job.resident_id !== undefined && !resident(job.resident_id))
            || (job.message_id && !/^msg_[a-f0-9]+$/.test(job.message_id))) {
          record.error = 'invalid_original_job_identity'; return;
        }
        record.block = {id: blockId, content_sha256: hash(block.content), content_bytes: Buffer.byteLength(block.content),
          discussion: {resident_id: block.discussion.resident_id},
          job: {resident_id: job.resident_id || '', message_id: job.message_id || '',
            status: typeof job.status === 'string' && /^[a-z_]{1,40}$/.test(job.status) ? job.status : null}};
        record.authoritative = true;
      } catch { record.error = 'original_response_unreadable'; }
      finally { record.completed = true; write('detail_response', record); notify(); }
    })();
    tasks.add(task); task.then(() => tasks.delete(task), () => tasks.delete(task));
  };
  const failed = request => {
    const record = requests.get(request);
    if (!record || record.completed) return;
    record.completed = true; record.error = 'original_request_failed';
    write('detail_request_failed', record); notify();
  };
  page.on('request', started); page.on('response', received); page.on('requestfailed', failed);
  write('detail_capture_started');
  return Object.freeze({file, snapshot,
    waitForReceipt({timeoutMs = 20000} = {}) {
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60000) throw new TypeError('Use a bounded original receipt wait');
      return new Promise((resolve, reject) => {
        let timer;
        const finish = (error, value) => { clearTimeout(timer); waiters.delete(check); error ? reject(new Error(error)) : resolve(value); };
        const check = () => {
          if (records.length > 1) return finish('Ambiguous original WALL detail requests');
          if (records[0]?.completed) return records[0].authoritative
            ? finish(null, snapshot()) : finish('Original WALL detail response is incomplete');
          if (stopped) finish('Original WALL detail capture stopped before receipt');
        };
        timer = setTimeout(() => finish('Original WALL detail receipt timeout'), timeoutMs);
        waiters.add(check); check();
      });
    },
    async stop() {
      if (!stopped) {
        stopped = true; page.off('request', started); page.off('response', received); page.off('requestfailed', failed); notify();
      }
      await Promise.all([...tasks]); return snapshot();
    },
  });
}

// Feed contemporaneous readings from the visible detail/selected tab, never
// board metadata or a later recovered page. This is a necessary identity gate;
// callers still retain the original action clock, UI samples and screenshot.
function checkWallDetailReady(capture, ui, {kind = 'discussion'} = {}) {
  if (!['discussion', 'work'].includes(kind)) throw new TypeError('Choose the actual discussion or work tab');
  const record = capture?.records?.[0];
  if (capture?.matching_requests !== 1 || capture.ambiguous || !record?.authoritative || !record.request_id)
    return {ready: false, reason: 'missing_unique_original_detail'};
  const block = record.block;
  if (ui?.block_id !== capture.block_id || ui?.visible !== true || ui?.loading !== false
      || ui?.selected_tab !== kind || ui?.panel_visible !== true || ui?.composer_editable !== true
      || typeof ui?.body_value !== 'string' || hash(ui.body_value) !== block.content_sha256)
    return {ready: false, reason: 'original_detail_ui_not_ready'};
  const expected = block[kind === 'work' ? 'job' : 'discussion'].resident_id;
  if (typeof ui.resident_id !== 'string' || ui.resident_id !== expected
      || (kind === 'work' && (!expected || !block.job.message_id || ui.message_id !== block.job.message_id)))
    return {ready: false, reason: 'original_detail_identity_not_ready'};
  if (!expected && ui.explicit_empty !== true) return {ready: false, reason: 'empty_discussion_not_observed'};
  return {ready: true, request_id: record.request_id, resident_id: expected,
    explicit_empty: !expected, content_sha256: block.content_sha256};
}

module.exports = {observeWallDetail, checkWallDetailReady};
