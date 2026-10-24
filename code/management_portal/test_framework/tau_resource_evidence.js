'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {createHash, randomUUID} = require('node:crypto');

// Opt-in evidence for exact resource references selected for this case. This
// observes original browser traffic; it never resolves a resource or retries it.
// Register targets from the visible scoped UI or its original public metadata
// BEFORE the action. A late registration cannot repair an earlier request.
function observeResourceReferences({page, origin, evidenceDir, requestId} = {}) {
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || typeof requestId !== 'function'
      || !page?.on || !fs.statSync(evidenceDir).isDirectory()) {
    throw new TypeError('Use the actual page, Portal origin, evidence directory and portal.requestId');
  }
  const captureId = randomUUID(), allowed = new Map(), requests = new WeakMap();
  const tasks = new Set();
  const file = path.join(evidenceDir, 'tau-resource-references.jsonl');
  let stopped = false;
  const write = row => fs.appendFileSync(file, JSON.stringify({
    capture_id: captureId, observed_at_ms: Date.now(), ...row,
  }) + '\n');
  const started = request => {
    let url;
    try { url = new URL(request.url()); } catch { return; }
    if (url.origin !== base.origin || url.pathname !== '/api/resources/reference'
        || request.method() !== 'GET') return;
    const targets = url.searchParams.getAll('address');
    const address = targets.length === 1 ? targets[0] : null;
    const registration = address === null ? null : allowed.get(address);
    const record = {request_id: requestId(request), target_sha256: address === null ? null
      : createHash('sha256').update(address).digest('hex'),
      registration: registration || null};
    requests.set(request, record);
    write({kind: 'reference_request', ...record,
      association: record.request_id ? 'portal_request_identity' : 'missing_original_request_identity'});
  };
  const received = response => {
    const record = requests.get(response.request());
    if (!record) return;
    const status = response.status();
    write({kind: 'reference_response', ...record, status});
    // Do not read an unmatched target's body, success content, SSE, or other
    // endpoints. No headers, error messages, titles or resource contents persist.
    if (!record.registration || !record.request_id || status < 400) return;
    if (!/^application\/json(?:\s*;|$)/i.test(response.headers()['content-type'] || '')) {
      write({kind: 'reference_error_code', request_id: record.request_id,
        available: false, reason: 'not_json'});
      return;
    }
    const task = (async () => {
      try {
        const text = await response.text();
        if (Buffer.byteLength(text, 'utf8') > 65536) {
          write({kind: 'reference_error_code', request_id: record.request_id,
            available: false, reason: 'body_exceeds_bound'});
          return;
        }
        const body = JSON.parse(text);
        const code = typeof body?.code === 'string'
          && /^[a-z][a-z0-9_]{0,63}(?:\.[a-z][a-z0-9_]{0,63}){0,3}$/.test(body.code)
          ? body.code : null;
        write({kind: 'reference_error_code', request_id: record.request_id,
          available: code !== null, code, reason: code === null ? 'no_structured_code' : null});
      } catch {
        write({kind: 'reference_error_code', request_id: record.request_id,
          available: false, reason: 'original_response_unreadable'});
      }
    })();
    tasks.add(task);
    task.then(() => tasks.delete(task), () => tasks.delete(task));
  };
  page.on('request', started);
  page.on('response', received);
  write({kind: 'reference_capture_started', limits: [
    'Targets require prior explicit registration from this case; no retrospective matching',
    'Join request_id to the original browser observer for page/frame/document; missing identity is a gap',
    'Structured error code only; unknown targets and messages are never persisted',
  ]});
  return Object.freeze({
    file,
    allow(address, {sourceKind, sourceLabel} = {}) {
      if (stopped) throw new Error('Resource reference observation has stopped');
      if (typeof address !== 'string' || !address || address.length > 4096
          || /[\x00-\x1f\x7f]/.test(address)
          || !['visible_reference', 'original_metadata'].includes(sourceKind)
          || !/^[a-z][a-z0-9_-]{0,63}$/.test(sourceLabel || '')) {
        throw new TypeError('Use an exact observed address and static source kind/label');
      }
      if (allowed.has(address)) return allowed.get(address);
      if (allowed.size >= 128) throw new Error('Scope resource evidence to this case, at most 128 targets');
      const registration = Object.freeze({address, source_kind: sourceKind,
        source_label: sourceLabel, registered_at_ms: Date.now(), registration_id: randomUUID()});
      allowed.set(address, registration);
      write({kind: 'reference_target_registered', ...registration});
      return registration;
    },
    async flush() { await Promise.all([...tasks]); },
    async stop() {
      if (stopped) return;
      stopped = true;
      page.off('request', started);
      page.off('response', received);
      await Promise.all([...tasks]);
      write({kind: 'reference_capture_ended'});
    },
  });
}

module.exports = {observeResourceReferences};
