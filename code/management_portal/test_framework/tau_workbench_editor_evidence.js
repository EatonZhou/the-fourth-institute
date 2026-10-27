'use strict';

const { createHash } = require('node:crypto');

// Samples only. Never opens details, changes values, clicks, fetches, or repairs
// earlier evidence. Caller must retain its original action/page/document IDs.
async function readEditorEvidence(page) {
  const sample = await page.evaluate(() => {
    const visible = e => {
      if (!e || !e.getClientRects().length) return false;
      for (let p = e; p; p = p.parentElement) {
        const css = getComputedStyle(p);
        if (p.hidden || css.display === 'none' || css.visibility === 'hidden' || Number(css.opacity) === 0) return false;
        if (p.tagName === 'DETAILS' && !p.open && e !== p && !p.querySelector(':scope > summary')?.contains(e)) return false;
      }
      return true;
    };
    const roots = [...document.querySelectorAll('#studioContent')];
    const root = roots.length === 1 ? roots[0] : null;
    const one = selector => {
      const found = root ? [...root.querySelectorAll(selector)] : [];
      return found.length === 1 ? found[0] : null;
    };
    const state = e => ({ present: !!e, visible: visible(e), disabled: e ? !!e.disabled : null });
    const status = one('#generationStatus'), version = one('#versionInfo');
    const advanced = one('#workbenchAdvanced'), team = one('#teamInfo');
    const planFields = status ? [...status.querySelectorAll('details textarea')] : [];
    const field = planFields.length === 1 ? planFields[0] : null;
    const details = field?.closest('details');
    const url = new URL(location.href);
    return {
      sampled_at: new Date().toISOString(), pathname: url.pathname,
      panel: url.searchParams.get('panel'), workbench_id: url.searchParams.get('id'),
      root_count: roots.length, root_visible: visible(root), busy: root?.getAttribute('aria-busy') === 'true',
      settings_visible: visible(one('#workbenchSettings')),
      title: one('#appTitle')?.value ?? null,
      version: { visible: visible(version), text: version?.textContent.trim() ?? '' },
      generation: {
        visible: visible(status), text: status?.innerText ?? '',
        headings: status ? [...status.querySelectorAll('strong')].filter(visible).map(e => e.textContent.trim()) : [],
        links: status ? [...status.querySelectorAll('a[href]')].filter(visible).map(e => {
          const u = new URL(e.href); return { origin: u.origin, pathname: u.pathname, id: u.searchParams.get('id') };
        }) : [],
      },
      origin: url.origin,
      controls: Object.fromEntries(['save', 'publish', 'delete', 'inspect'].map(id => [id, state(one('#' + id))])),
      advanced: { ...state(advanced), open: !!advanced?.open, summary: advanced?.querySelector(':scope > summary')?.textContent.trim() ?? '' },
      team: { ...state(team), lineage_id: team?.getAttribute('data-lineage-id'), busy: team?.getAttribute('aria-busy') === 'true',
        member_ids: team ? [...team.querySelectorAll('[data-resident-id]')].map(e => e.getAttribute('data-resident-id')) : [] },
      plan: { count: planFields.length, visible: visible(field), open: !!details?.open,
        summary: details?.querySelector(':scope > summary')?.textContent.trim() ?? '', value: field?.value ?? null },
    };
  });
  // Only the digest is emitted. Plan contents stay in the caller's explicit,
  // owned review flow and are never confused with the job observer's summary.
  sample.plan.value_sha256 = sample.plan.value === null ? null : createHash('sha256').update(sample.plan.value).digest('hex');
  delete sample.plan.value;
  return sample;
}

function requirePublishedEditor(sample, { workbenchId, revision, publishedRevision }) {
  if (!workbenchId || !Number.isSafeInteger(revision) || !Number.isSafeInteger(publishedRevision) || publishedRevision < 1) throw new Error('InvalidPublishedEditorExpectation');
  const versions = [...sample.version.text.matchAll(/\bv(\d+)\b/g)].map(m => Number(m[1]));
  const headingVersions = sample.generation.headings.flatMap(t => [...t.matchAll(/\bv(\d+)\b/g)].map(m => Number(m[1])));
  const identity = sample.pathname === '/workbenches' && sample.panel === 'settings' && sample.workbench_id === workbenchId;
  if (!identity || sample.root_count !== 1 || !sample.root_visible || sample.busy || !sample.settings_visible ||
      !sample.version.visible || versions.length !== 2 || versions[0] !== revision || versions[1] !== publishedRevision ||
      !sample.generation.visible || headingVersions.length !== 1 || headingVersions[0] !== publishedRevision ||
      sample.generation.links.length !== 1 || !sample.generation.links.some(l => l.origin === sample.origin && l.pathname === '/workbenches/view' && l.id === workbenchId) ||
      !sample.controls.publish.visible || sample.controls.publish.disabled) throw new Error('OriginalPublishedEditorNotReady');
  return sample;
}

// Attach before the original UI action. Each phase is appended once; status is
// retained even when navigation makes the original body unreadable. No request
// replay, polling, response adoption from before registration, or ready claim.
function observeEditorResponses(page, { origin, workbenchId, frame = page.mainFrame(), requestId, record, bodyTimeoutMs = 5000 }) {
  if (!workbenchId || typeof requestId !== 'function' || typeof record !== 'function'
      || !Number.isInteger(bodyTimeoutMs) || bodyTimeoutMs < 1 || bodyTimeoutMs > 60000) throw new Error('InvalidEditorObservationOptions');
  const site = new URL(origin).origin;
  const allowed = new Set(['update', 'publish', 'delete', 'facade'].map(x => '/api/workbenches/' + x));
  const requests = new Map(), responses = new Map(), pending = new Set();
  let closed = false, failure = null, closePromise;
  const emit = row => {
    const copy = structuredClone(row);
    try {
      const result = record(copy);
      if (result && typeof result.then === 'function') {
        result.catch(() => {});
        throw new Error('EditorEvidenceRecorderMustBeSynchronous');
      }
    } catch (error) { failure ||= error; }
  };
  const onRequest = req => {
    try {
      const url = new URL(req.url());
      if (url.origin !== site || !allowed.has(url.pathname) || req.method() !== 'POST' || req.frame() !== frame) return;
      const body = req.postDataJSON();
      if (!body || body.id !== workbenchId) return;
      const id = requestId(req);
      if (typeof id !== 'string' || !id) throw new Error('OriginalRequestIdentityMissing');
      const row = { request_id: id, pathname: url.pathname, method: 'POST', workbench_id: workbenchId,
        expected_revision: Number.isSafeInteger(body.expected_revision) ? body.expected_revision : null };
      requests.set(req, row);
      emit({ ...row, phase: 'request', observed_at: new Date().toISOString() });
    } catch (error) { failure ||= error; }
  };
  const onResponse = response => {
    const row = requests.get(response.request());
    if (!row || responses.has(response)) return;
    const header = { ...row, phase: 'response', observed_at: new Date().toISOString(), status: response.status() };
    emit(header);
    let resolve;
    const entry = { settled: false, timer: null, promise: new Promise(r => { resolve = r; }) };
    const finish = result => {
      if (entry.settled) return;
      entry.settled = true;
      clearTimeout(entry.timer); pending.delete(entry);
      const outcome = { ...header, phase: 'body', ...result, observed_at: new Date().toISOString() };
      emit(outcome); resolve(outcome);
    };
    entry.finish = finish;
    responses.set(response, entry); pending.add(entry);
    // These bounds end only evidence collection, never the native request.
    // A late body must not replace the original timeout/close observation.
    entry.timer = setTimeout(() => finish({ body_read: 'timeout',
      error: { name: 'TimeoutError', stage: 'original_response_body', reason: 'observation_deadline' } }), bodyTimeoutMs);
    Promise.resolve().then(() => response.status() === 204 ? null : response.json()).then(body => {
      if (entry.settled) return;
      const result = { body_read: 'success' };
      try {
        if (body && typeof body === 'object' && !Array.isArray(body)) {
          result.identity = {};
          if (typeof body.workbench_id === 'string') result.identity.workbench_id = body.workbench_id;
          for (const key of ['revision', 'published_revision']) if (Number.isSafeInteger(body[key])) result.identity[key] = body[key];
          if (!response.ok()) {
            const code = body.code ?? body.error?.code;
            if (typeof code === 'string' && /^[A-Za-z][\w.-]{0,99}$/.test(code)) result.error_code = code;
            if (['committed', 'not_committed', 'unknown'].includes(body.commit_state)) result.commit_state = body.commit_state;
            if (typeof body.retryable === 'boolean') result.retryable = body.retryable;
          }
        }
      } catch (error) {
        result.body_read = 'unavailable';
        result.error = { name: error?.name || 'Error', stage: 'original_response_body' };
      }
      finish(result);
    }, error => finish({ body_read: 'unavailable', error: { name: error?.name || 'Error', stage: 'original_response_body' } }));
  };
  page.on('request', onRequest); page.on('response', onResponse);
  return {
    async original(response) {
      if (closed) throw new Error('EditorObserverClosed');
      if (!responses.has(response)) throw new Error('UnobservedOriginalEditorResponse');
      const row = await responses.get(response).promise;
      if (failure) throw failure;
      return structuredClone(row);
    },
    close({ drainMs = 1000 } = {}) {
      if (closePromise) return closePromise;
      if (!Number.isInteger(drainMs) || drainMs < 0 || drainMs > 60000) throw new Error('InvalidEditorObservationDrainDeadline');
      closed = true; page.off('request', onRequest); page.off('response', onResponse);
      closePromise = (async () => {
        let timer;
        try {
          if (pending.size) await Promise.race([
            Promise.all([...pending].map(entry => entry.promise)),
            new Promise(resolve => { timer = setTimeout(resolve, drainMs); }),
          ]);
        } finally {
          clearTimeout(timer);
          for (const entry of [...pending]) entry.finish({ body_read: 'closed',
            error: { name: 'ObservationClosed', stage: 'original_response_body', reason: 'observer_close_deadline' } });
        }
        if (failure) throw failure;
      })();
      return closePromise;
    },
  };
}

module.exports = { readEditorEvidence, requirePublishedEditor, observeEditorResponses };
