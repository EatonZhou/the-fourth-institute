'use strict';

// Install before the original click. Only observes its Request/Response objects;
// never submits, retries, cancels, navigates, or infers a transport abort cause.
// A receipt is not a refreshed-running UI sample or a successful review body.
// record must persist synchronously; join request_id to the original public
// action/page/frame/document record before accepting the caller's receipt.
function observePaperReviewSubmission(page, {origin, paperId, requestId, record,
  frame = page.mainFrame(), bodyTimeoutMs = 5000} = {}) {
  const base = new URL(origin);
  const bounded = n => Number.isFinite(n) && n > 0 && n <= 600000;
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password
      || !/^paper_[a-z0-9]+$/.test(paperId || '') || typeof requestId !== 'function'
      || typeof record !== 'function' || !bounded(bodyTimeoutMs)) throw new TypeError('Invalid original Paper observation scope');
  const rows = [], requests = new WeakMap(), timers = new Map(), waiters = new Set();
  let closed = false, gap = null, recordError = null;
  const copy = value => JSON.parse(JSON.stringify(value));
  const token = value => typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,160}$/.test(value) ? value : null;
  const snapshot = () => copy({paper_id: paperId, stopped: closed,
    gap: recordError ? 'record_error' : gap || (rows.length > 1 ? 'ambiguous_original_requests' : null),
    record_error: recordError, records: rows, receipt_ready: !recordError && !gap && rows.length === 1
      && rows[0].completed && !rows[0].error && !!rows[0].job});
  const notify = () => { for (const check of [...waiters]) check(); };
  const publish = (kind, row) => {
    try { record(copy({kind, paper_id: paperId, ...row})); }
    catch (error) { recordError = {phase: 'record', name: token(error?.name) || 'Error'}; }
  };
  function finish(row, terminal, error = null) {
    if (row.completed) return;
    clearTimeout(timers.get(row)); timers.delete(row);
    row.completed = true; row.terminal = terminal; row.error = error;
    publish('paper_review_terminal', row); notify();
  }
  function started(req) {
    if (closed) return;
    let url, body;
    try {
      url = new URL(req.url());
      if (url.origin !== base.origin || url.username || url.password || url.search
          || url.pathname !== '/paper/api/reviews/start' || req.method() !== 'POST' || req.frame() !== frame) return;
      body = req.postDataJSON();
    } catch { return; }
    if (!body || body.paper_id !== paperId) return;
    let id;
    try { id = token(requestId(req)); } catch { id = null; }
    const row = {request_id: id, method: 'POST', path: url.pathname, http_status: null,
      completed: false, terminal: null, job: null, error: null, error_code: null,
      failed_reason: null, cancellation_cause: 'unknown'};
    requests.set(req, row); rows.push(row); publish('paper_review_request', row);
    if (!id) finish(row, 'identity_unavailable', {phase: 'request', reason: 'missing_public_request_id'});
    notify();
  }
  function received(response) {
    const row = requests.get(response.request());
    if (closed || !row || row.completed) return;
    row.http_status = response.status(); publish('paper_review_response', row); notify();
    timers.set(row, setTimeout(() => finish(row, 'body_timeout',
      {phase: 'response_body', reason: 'observation_deadline'}), bodyTimeoutMs));
    (async () => {
      try {
        const text = await response.text();
        if (closed || row.completed) return;
        if (Buffer.byteLength(text) > 65536) {
          finish(row, 'response_rejected', {phase: 'response_body', reason: 'body_exceeds_bound'}); return;
        }
        const body = JSON.parse(text);
        if (row.http_status < 200 || row.http_status >= 300) {
          row.error_code = token(body?.error?.code ?? body?.code);
          finish(row, 'http_error', {phase: 'response', reason: 'http_status'}); return;
        }
        const job = body?.job || body;
        if (job?.paper_id !== paperId || !token(job?.job_id) || !token(job?.status)) {
          finish(row, 'identity_rejected', {phase: 'response_body', reason: 'invalid_original_job_identity'}); return;
        }
        row.job = {paper_id: job.paper_id, job_id: job.job_id, status: job.status};
        finish(row, 'response_complete');
      } catch {
        if (!closed && !row.completed) finish(row, 'body_unavailable', {phase: 'response_body', reason: 'original_body_unreadable'});
      }
    })();
  }
  function failed(req) {
    const row = requests.get(req);
    if (closed || !row || row.completed) return;
    let text;
    try { text = req.failure()?.errorText; } catch { /* unavailable stays unknown */ }
    row.failed_reason = typeof text === 'string' && /^net::ERR_[A-Z0-9_]+$/.test(text) ? text : null;
    finish(row, 'request_failed', {phase: 'transport', reason: 'original_request_failed'});
  }
  function close(reason = 'capture_closed') {
    if (closed) return snapshot();
    closed = true;
    page.off('request', started); page.off('response', received); page.off('requestfailed', failed);
    if (!rows.length || rows.some(row => !row.completed)) gap = reason;
    for (const row of rows) if (!row.completed) finish(row, reason, {phase: 'observation', reason});
    notify(); return snapshot();
  }
  page.on('request', started); page.on('response', received); page.on('requestfailed', failed);
  return Object.freeze({snapshot, close,
    waitForReceipt({timeoutMs} = {}) {
      if (!bounded(timeoutMs)) throw new TypeError('Pass the finite remaining original-action observation budget');
      return new Promise(resolve => {
        let timer, done = false;
        const check = () => {
          if (done || (!closed && !recordError && rows.length <= 1 && !rows[0]?.completed)) return;
          done = true; clearTimeout(timer); waiters.delete(check); resolve(snapshot());
        };
        timer = setTimeout(() => close('receipt_deadline'), timeoutMs);
        waiters.add(check); check();
      });
    },
  });
}

module.exports = {observePaperReviewSubmission};
