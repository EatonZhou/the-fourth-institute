'use strict';

// Passive observation only. Install BEFORE the original UI action, then pass
// its exact POST Response to accept(). This helper sends no requests or clicks.
// It does not establish saved-artifact/UI readiness or reconstruct lost timing.
function readJob(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) ||
      typeof raw.id !== 'string' || !raw.id.trim() ||
      typeof raw.status !== 'string' || !raw.status.trim()) {
    throw new Error('ExpectedTopLevelWorkbenchJob');
  }
  const job = {};
  for (const key of ['id', 'status', 'kind', 'workbench_id', 'resident_id', 'lineage_id',
    'message_id', 'input_state', 'base_revision', 'revision', 'save_receipt', 'plan_hash']) {
    if (Object.hasOwn(raw, key)) job[key] = structuredClone(raw[key]);
  }
  return job;
}

function observeWorkbenchJob(page, { origin, operation, requestId, record }) {
  if (!['generate', 'reverse'].includes(operation) || typeof requestId !== 'function' ||
      typeof record !== 'function') throw new Error('InvalidWorkbenchObservationOptions');
  const site = new URL(origin).origin;
  const createPath = '/api/workbenches/' + operation;
  const requests = new Map();
  const responses = new Map();
  const rows = [];
  const pending = new Set();
  let sequence = 0, accepted = null, initial = null, latest = null, closed = false, recordingError = null;

  function relevant(req) {
    const url = new URL(req.url());
    if (url.origin !== site || req.frame() !== page.mainFrame()) return null;
    if (req.method() === 'POST' && url.pathname === createPath) return { kind: 'acceptance', url };
    if (req.method() === 'GET' && url.pathname === '/api/workbenches/generation' && url.searchParams.get('id')) {
      return { kind: 'snapshot', url };
    }
    return null;
  }
  function onRequest(req) {
    let match;
    try { match = relevant(req); } catch (_) { return; }
    if (match) requests.set(req, { ...match, sequence: ++sequence });
  }
  function onResponse(response) {
    const req = response.request(), info = requests.get(req);
    if (!info) return; // Never adopt a response whose request predates registration.
    const row = { sequence: info.sequence, kind: info.kind, request_id: requestId(req),
      path: info.url.pathname, observed_at: new Date().toISOString(),
      query_job_id: info.url.searchParams.get('id'), method: req.method(), status: response.status() };
    rows.push(row);
    const task = (async () => {
      try {
        if (!response.ok()) throw new Error('WorkbenchResponseNotSuccessful');
        row.job = readJob(await response.json());
      } catch (error) {
        row.error = ['ExpectedTopLevelWorkbenchJob', 'WorkbenchResponseNotSuccessful'].includes(error.message)
          ? error.message : 'OriginalWorkbenchResponseBodyUnavailable';
      }
      // Append each original response once, without later accepted-job backfill.
      try { record(structuredClone(row)); } catch (error) { recordingError = error; }
      return row;
    })();
    responses.set(response, task);
    pending.add(task);
    task.then(() => pending.delete(task), () => pending.delete(task));
  }
  page.on('request', onRequest);
  page.on('response', onResponse);

  function requireOpen() {
    if (closed) throw new Error('WorkbenchObserverClosed');
    if (recordingError) throw recordingError;
  }
  function update() {
    if (recordingError) throw recordingError;
    if (rows.some(row => row.kind === 'acceptance' && row !== accepted)) throw new Error('UnexpectedAdditionalWorkbenchAcceptance');
    const identity = { ...initial };
    latest = initial;
    for (const row of [...rows].sort((a, b) => a.sequence - b.sequence)) {
      if (row.kind !== 'snapshot' || row.sequence <= accepted.sequence || row.query_job_id !== initial.id) continue;
      if (row.error) throw new Error(row.error);
      if (!row.job) continue;
      if (row.job.id !== initial.id) throw new Error('WorkbenchSnapshotJobMismatch');
      for (const key of ['workbench_id', 'resident_id', 'lineage_id', 'message_id']) {
        if (row.job[key] && identity[key] && row.job[key] !== identity[key]) throw new Error('WorkbenchSnapshotIdentityMismatch');
        if (row.job[key]) identity[key] = row.job[key];
      }
      latest = row.job;
    }
    return structuredClone(latest);
  }
  return {
    async accept(response) {
      requireOpen();
      if (accepted) throw new Error('WorkbenchAcceptanceAlreadyBound');
      const info = requests.get(response.request());
      if (!info || info.kind !== 'acceptance' || !responses.has(response)) throw new Error('UnobservedOriginalWorkbenchAcceptance');
      const row = await responses.get(response);
      if (recordingError) throw recordingError;
      if (row.error) throw new Error(row.error);
      accepted = row;
      initial = structuredClone(row.job);
      latest = initial;
      return structuredClone(initial);
    },
    async current() {
      requireOpen();
      if (!accepted) throw new Error('WorkbenchAcceptanceNotBound');
      await Promise.all([...pending]);
      return update();
    },
    async close() {
      if (closed) return;
      closed = true;
      page.off('request', onRequest);
      page.off('response', onResponse);
      await Promise.all([...pending]);
      if (recordingError) throw recordingError;
    },
  };
}

module.exports = { observeWorkbenchJob, readJob };
