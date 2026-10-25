'use strict';

// Passive observation of explicitly named, finite instruction responses.
// Register before the original action; this never sends or repeats a request.
// A bounded body read is an observation deadline, not a server timeout and not
// evidence of business readiness. Unread bodies remain explicit gaps.
function observeFiniteInstructionResponses(page, {
  origin, targets, requestId, record, frame = page.mainFrame(), bodyTimeoutMs = 5000,
}) {
  if (!Array.isArray(targets) || !targets.length || typeof requestId !== 'function' || typeof record !== 'function'
      || !Number.isInteger(bodyTimeoutMs) || bodyTimeoutMs < 1 || bodyTimeoutMs > 60000) throw new TypeError('InvalidFiniteObservationOptions');
  const site = new URL(origin).origin;
  const specs = targets.map(target => {
    if (!target || !/^[A-Z][A-Za-z0-9]+$/.test(target.instruction) || target.instruction.startsWith('Watch')
        || !target.arguments || !Object.keys(target.arguments).length
        || Object.values(target.arguments).some(v => !['string', 'number', 'boolean'].includes(typeof v))) {
      throw new TypeError('FiniteObservationRequiresExactNonStreamingTarget');
    }
    return structuredClone(target);
  });
  const requests = new Map(), ids = new Map(), responses = new Map(), pending = new Set();
  let stopped = false, failure = null, closePromise;
  const emit = row => {
    try {
      const value = record(structuredClone({ observed_at: new Date().toISOString(), ...row }));
      if (value && typeof value.then === 'function') {
        value.catch(() => {});
        throw new TypeError('FiniteObservationRecorderMustBeSynchronous');
      }
    } catch (error) { failure ||= error; }
  };
  const onRequest = request => {
    try {
      const url = new URL(request.url());
      if (url.origin !== site || url.pathname !== '/api/instructions/execute' || request.method() !== 'POST' || request.frame() !== frame) return;
      const body = request.postDataJSON();
      const matching = specs.filter(s => body?.instruction === s.instruction && Object.entries(s.arguments).every(([k, v]) => body.arguments?.[k] === v));
      if (!matching.length) return;
      if (matching.length !== 1) throw new Error('AmbiguousOriginalInstructionTarget');
      const id = requestId(request);
      if (typeof id !== 'string' || !id || (ids.has(id) && ids.get(id) !== request)) throw new Error('OriginalInstructionRequestIdentityMissingOrAmbiguous');
      ids.set(id, request);
      const metadata = { request_id: id, instruction: body.instruction };
      for (const key of ['resident_id', 'message_id', 'lineage_id', 'restart_id']) {
        if (typeof matching[0].arguments[key] === 'string') metadata[key] = matching[0].arguments[key];
      }
      requests.set(request, { metadata, response: null });
      emit({ ...metadata, phase: 'request_observed' });
    } catch (error) { failure ||= error; }
  };
  const onResponse = response => {
    const request = requests.get(response.request());
    if (!request || request.response) return;
    request.response = response;
    const metadata = { ...request.metadata, http_status: response.status() };
    emit({ ...metadata, phase: 'response_observed' });
    let resolve;
    const entry = { metadata, settled: false, timer: null, outcome: null, promise: new Promise(r => { resolve = r; }) };
    entry.finish = (bodyStatus, body, errorName) => {
      if (entry.settled) return;
      entry.settled = true;
      clearTimeout(entry.timer);
      pending.delete(entry);
      entry.outcome = { ...metadata, body_status: bodyStatus, ...(bodyStatus === 'read' ? { body } : {}), ...(errorName ? { error_name: errorName } : {}) };
      // Never write the response body, arbitrary arguments, headers or errors.
      emit({ ...metadata, phase: 'body_observation_ended', body_status: bodyStatus, ...(errorName ? { error_name: errorName } : {}) });
      resolve(entry.outcome);
    };
    responses.set(response, entry); pending.add(entry);
    const contentType = response.headers()['content-type'] || '';
    if (!/^application\/(?:[a-z0-9.-]+\+)?json(?:\s*;|\s*$)/i.test(contentType)) {
      entry.finish('not_finite_json'); return;
    }
    entry.timer = setTimeout(() => entry.finish('body_observation_timeout'), bodyTimeoutMs);
    // Consume rejection even after the deadline, but never replace the original
    // timeout/close outcome with a later successful body.
    Promise.resolve().then(() => response.json()).then(
      body => entry.finish('read', body),
      () => entry.finish('body_unavailable', undefined, 'OriginalResponseBodyUnavailable'),
    );
  };
  page.on('request', onRequest); page.on('response', onResponse);
  return {
    async original(response) {
      const entry = responses.get(response);
      if (!entry) throw new Error('UnobservedOriginalInstructionResponse');
      const outcome = await entry.promise;
      if (failure) throw failure;
      return structuredClone(outcome);
    },
    pending() {
      return [...pending].map(entry => structuredClone(entry.metadata));
    },
    close({ drainMs = 1000 } = {}) {
      if (closePromise) return closePromise;
      if (!Number.isInteger(drainMs) || drainMs < 0 || drainMs > 60000) throw new TypeError('InvalidFiniteObservationDrainDeadline');
      stopped = true;
      page.off('request', onRequest); page.off('response', onResponse);
      closePromise = (async () => {
        let timer;
        try {
          if (pending.size) await Promise.race([
            Promise.all([...pending].map(entry => entry.promise)),
            new Promise(resolve => { timer = setTimeout(resolve, drainMs); }),
          ]);
        } finally {
          clearTimeout(timer);
          for (const entry of [...pending]) entry.finish('observer_close_deadline');
        }
        const missingResponses = [...requests.values()].filter(r => !r.response).map(r => r.metadata);
        missingResponses.forEach(metadata => emit({ ...metadata, phase: 'response_not_observed' }));
        const outcomes = [...responses.values()].map(entry => ({ ...entry.metadata, body_status: entry.outcome.body_status }));
        const report = { stopped, observation_complete: !missingResponses.length && outcomes.every(x => x.body_status === 'read'), outcomes, missing_responses: missingResponses };
        emit({ phase: 'observer_closed', ...report });
        if (failure) throw failure;
        return report;
      })();
      return closePromise;
    },
  };
}

module.exports = { observeFiniteInstructionResponses };
