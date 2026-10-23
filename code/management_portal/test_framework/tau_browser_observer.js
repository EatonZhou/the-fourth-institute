// Content-free, passive observations for one TAU browser context.
const fs = require('fs');
const path = require('path');
const { randomUUID, createHash } = require('crypto');
const { safeError } = require('./tau_safe_error');

// The current Resident HTTP surface puts identity in a named query field.
// Keep the complete public route, never arbitrary path/query vocabulary.
const residentReadRoutes = new Map([
  ['/api/agent-workbench/residents/projection', ['id', 'projection']],
  ['/api/agent-workbench/residents/navigation-target', ['id', 'navigation_target']],
  ['/api/agent-workbench/residents/session-restart-options', ['id', 'restart_options']],
  ['/api/agent-workbench/residents/watchboard', ['id', 'watchboard']],
  ['/api/agent-workbench/residents/forward-targets', ['resident_id', 'forward_targets']],
  ['/api/agent-workbench/agent-runtime-catalog', ['resident_id', 'runtime_catalog']],
]);

// Unknown path components may contain names or credentials. Keep only public
// endpoint vocabulary; identity is carried by request/frame/document IDs.
function createPathSanitizer(residentPaths) {
  const routes = new Set(residentPaths);
  return function safePath(value) {
    const words = new Set('api instructions execute events agent-workbench residents session-restart-options initialize paper papers session lineage lineages home test chat resource resources file files static assets me health shared-agents account accounts usage catalog search suggest workspace workspaces deployments login notifications inbox viewer pdf entry'.split(' '));
    try {
      const u = new URL(value, 'http://observation.invalid');
      if (!['http:', 'https:'].includes(u.protocol)) return '[non-http]';
      if (routes.has(u.pathname)) return u.pathname;
      // These are complete public routes, not a vocabulary allowance for
      // arbitrary nested paths. Query values remain excluded in both Node and
      // the injected ResourceTiming observer.
      if (['/api/agent-conversations', '/api/scheduled-triggered', '/api/resources/resolve', '/api/resources/reference', '/api/resources/content',
        '/api/agent-workbench/link-file', '/api/agent-workbench/link-file-raw',
        '/test/api/preview', '/test/api/catalog/case'].includes(u.pathname)) return u.pathname;
      return u.pathname.split('/').map(part => {
        if (!part || words.has(part) || /^(home|lineage|test|paper|chat|hatg-panel)\.html$/.test(part)) return part;
        const extension = part.match(/\.(pdf|js|css|png|jpg|jpeg|svg|woff2?)$/i)?.[1];
        return extension ? `[file].${extension.toLowerCase()}` : '[segment]';
      }).join('/');
    } catch { return '[unavailable]'; }
  };
}
const safePath = createPathSanitizer([...residentReadRoutes.keys()]);

// Identify repeated reads of the same resource without recording its address,
// filename, query string or contents. A probe is not a user opening a file.
function resourceScope(request) {
  try {
    const url = new URL(request.url());
    if (request.method() !== 'GET') return undefined;
    const route = residentReadRoutes.get(url.pathname);
    if (route) {
      const targets = url.searchParams.getAll(route[0]);
      const target = targets.length === 1 ? targets[0].trim() : '';
      return /^resident_[0-9a-f]{12}$/.test(target) ? {
        kind: 'resident', mode: route[1],
        target_sha256: createHash('sha256').update(target).digest('hex'),
      } : undefined;
    }
    const file = ['/api/agent-workbench/link-file', '/api/agent-workbench/link-file-raw'].includes(url.pathname);
    if (!file && !['/api/resources/resolve', '/api/resources/reference', '/api/resources/content'].includes(url.pathname)) return undefined;
    const target = url.searchParams.get(file ? 'path' : 'address');
    return {
      kind: file ? 'file' : 'resource',
      mode: file ? (url.pathname.endsWith('/link-file') && url.searchParams.get('meta') === '1' ? 'probe' : 'read') : url.pathname.split('/').pop(),
      target_sha256: target ? createHash('sha256').update(target).digest('hex') : null,
    };
  } catch { return undefined; }
}

// Keep only the public operation and a digest of its explicit target. This
// associates reads with lifecycle operations without saving IDs or contents;
// an HTTP status or matching digest alone still does not prove causality.
function instructionScope(request) {
  try {
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/api/instructions/execute') return {};
    const body = request.postDataJSON();
    if (body?.instruction === 'ListResidentStates') {
      const ids = body.arguments?.resident_ids;
      return { instruction: body.instruction,
        residentCount: Array.isArray(ids) && ids.every(item => typeof item === 'string') ? new Set(ids).size : null };
    }
    const operations = new Set(['GetResident', 'GetResidentSettings', 'UpdateResidentSettings',
      'GetResidentInitialization', 'GetResidentRequest', 'ListResidentRequests',
      'ListMessages', 'ListInputHistory', 'SearchMessages', 'GetMessage', 'GetSentPrompt',
      'PreviewPromptInjection', 'GetMailbox', 'WatchMailbox', 'ListMailboxTodos',
      'GetConversation', 'WatchConversationEvents', 'GetInputState', 'DeleteResident', 'ClearContext',
      'RestartSession', 'GetSessionRestart', 'CancelSessionRestart', 'CancelInput', 'DeliverMessage']);
    if (!operations.has(body?.instruction)) return {};
    const target = body.arguments?.resident_id;
    return { instruction: body.instruction, resourceScope: typeof target === 'string' && /^resident_[0-9a-f]{12}$/.test(target)
      ? { kind: 'resident', mode: body.instruction, target_sha256: createHash('sha256').update(target).digest('hex') }
      : undefined };
  } catch { return {}; }
}

function requestTiming(request) {
  const raw = request.timing();
  const fields = ['startTime', 'domainLookupStart', 'domainLookupEnd', 'connectStart', 'secureConnectionStart', 'connectEnd', 'requestStart', 'responseStart', 'responseEnd'];
  const values = {}, unavailable = {};
  for (const field of fields) {
    const value = raw?.[field];
    // Zero epoch is a placeholder; a zero requestStart cannot prove dispatch.
    const valid = Number.isFinite(value) && value >= 0 && (!['startTime', 'requestStart', 'responseStart', 'responseEnd'].includes(field) || value > 0);
    values[field] = valid ? value : null;
    if (!valid) unavailable[field] = 'not_started_not_exposed_or_zero_placeholder';
  }
  return { values, unavailable, clock: 'startTime_epoch_ms_other_fields_relative_ms', server_execution_time: null };
}

async function observeBrowser(context, { evidenceDir }) {
  const file = path.join(evidenceDir, 'tau-browser-observations.jsonl');
  const capture = randomUUID();
  const pageStates = new WeakMap(), frameStates = new WeakMap(), requests = new WeakMap();
  const live = new Map(), tokens = new Map(), releases = [], snapshots = new Set();
  const transportRequests = new Map();
  const actions = new WeakMap();
  let sequence = 0, stopped = false;
  const id = kind => `${capture}:${kind}:${++sequence}`;
  const write = data => {
    if (stopped) return;
    fs.appendFileSync(file, JSON.stringify({ schema: 'tfi.tau-browser-observation.v1', capture_id: capture, observed_at_ms: Date.now(), ...data }) + '\n', { mode: 0o600 });
  };
  const listen = (object, event, callback) => {
    object.on(event, callback);
    releases.push(() => object.off(event, callback));
  };
  const document = () => ({ id: id('document'), confirmed: false });
  const pageState = page => {
    if (!pageStates.has(page)) {
      pageStates.set(page, { id: id('page') });
      write({ kind: 'page_created', page_id: pageStates.get(page).id });
      listen(page, 'pageerror', error => write({ kind: 'page_error', page_id: pageStates.get(page).id,
        frame_id: null, document_id: null, document_association: 'not_exposed_by_pageerror', ...safeError(error) }));
      listen(page, 'frameattached', frame => frameState(frame));
      listen(page, 'framenavigated', frame => {
        const state = frameState(frame);
        const previous = state.navigation_source || state.document;
        if (state.pending) { state.document = state.pending; state.pending = null; }
        write({ kind: 'frame_navigated', ...location(state), previous_document_id: previous.id, path: safePath(frame.url()) });
        state.navigation_source = null;
      });
      listen(page, 'framedetached', frame => {
        const state = frameState(frame);
        write({ kind: 'frame_detached', ...location(state) });
      });
      listen(page, 'close', () => write({ kind: 'page_closed', page_id: pageStates.get(page).id }));
    }
    return pageStates.get(page);
  };
  const location = state => ({ page_id: state.page_id, frame_id: state.id, parent_frame_id: state.parent_id, document_id: state.document.id, document_confirmed: state.document.confirmed });
  const frameState = frame => {
    if (!frameStates.has(frame)) {
      const page = pageState(frame.page());
      const parent = frame.parentFrame();
      const state = { id: id('frame'), page_id: page.id, parent_id: parent ? frameState(parent).id : null, document: document(), pending: null };
      frameStates.set(frame, state);
      write({ kind: 'frame_observed', ...location(state), path: safePath(frame.url()) });
    }
    return frameStates.get(frame);
  };
  const identity = request => {
    try { return frameState(request.frame()); } catch { return null; }
  };
  const start = request => {
    if (requests.has(request)) return requests.get(request);
    const frame = identity(request), source = frame?.document;
    let target = source;
    const prior = requests.get(request.redirectedFrom());
    if (frame && request.isNavigationRequest()) {
      target = prior?.navigation_document || document();
      frame.navigation_source = source;
      frame.pending = target;
    }
    const operation = instructionScope(request);
    const { instruction = null, residentCount = null } = operation;
    const record = { id: id('request'), frame, navigation_document: request.isNavigationRequest() ? target : null,
      document: target, started: Date.now(), status: null, request };
    const action = frame ? actions.get(request.frame().page()) : null;
    if (action) action.request_ids.push(record.id);
    // This content-free identifier is minted by Portal for one EventSource.
    // Native reconnection can reuse it; multiple requests stay ambiguous.
    const streamValue = request.resourceType() === 'eventsource' && safePath(request.url()) === '/api/events'
      ? new URL(request.url()).searchParams.get('portal_stream_id') : null;
    const streamId = /^[a-z0-9-]{1,96}$/.test(streamValue || '') ? streamValue : null;
    const pdfRequest = request.method() === 'GET'
      && /^\/api\/articles\/[^/]+\/pdf$/.test(new URL(request.url()).pathname);
    const transportValue = (safePath(request.url()) === '/api/instructions/execute' && request.method() === 'POST') || pdfRequest
      ? request.headers()['x-portal-request-id'] : null;
    const transportId = /^[a-z0-9-]{1,96}$/.test(transportValue || '') ? transportValue : null;
    const transportKey = streamId ? `stream:${streamId}` : (transportId ? `request:${transportId}` : null);
    if (transportKey) {
      if (!transportRequests.has(transportKey)) transportRequests.set(transportKey, []);
      transportRequests.get(transportKey).push(record);
    }
    requests.set(request, record); live.set(record.id, record);
    write({ kind: 'request_started', request_id: record.id, ...(frame ? location(frame) : { page_id: null, frame_id: null, document_id: null }),
      action_id: action?.id || null, action_association: action ? 'observed_in_explicit_page_action_scope' : null,
      document_id: target?.id || null, document_confirmed: target?.confirmed || false,
      document_association: request.isNavigationRequest() ? 'navigation_target' : (frame?.pending ? 'unconfirmed_during_navigation' : 'frame_at_request_event'),
      source_document_id: request.isNavigationRequest() ? source?.id || null : null,
      redirected_from_request_id: prior?.id || null,
      stream_id: streamId, transport_id: transportId,
      resource_scope: operation.resourceScope || resourceScope(request),
      method: /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$/.test(request.method()) ? request.method() : 'OTHER',
      path: safePath(request.url()), resource_type: request.resourceType(), instruction, resident_count: residentCount, resident_count_unavailable: residentCount === null ? 'no_explicit_selected_request_scope' : null, started_at_ms: record.started, timing: requestTiming(request) });
    return record;
  };
  const snapshot = async frame => {
    try { await frame.evaluate(() => window.__portalTauObservation?.flush()); }
    catch { write({ kind: 'observation_gap', ...location(frameState(frame)), reason: 'document_unavailable_for_final_snapshot' }); }
  };
  listen(context, 'page', page => pageState(page));
  listen(context, 'request', start);
  listen(context, 'response', response => {
    const record = start(response.request()); record.status = response.status();
    // Only fixed, content-free Portal duration metrics may enter evidence. Never
    // persist arbitrary headers/descriptions or infer database execution time.
    const metrics = {}, seen = new Set();
    const header = response.headers?.()['server-timing'];
    if (typeof header === 'string' && header.length <= 4096) {
      for (const part of header.split(',')) {
        const match = part.trim().match(/^(portal_queue|portal_handler|portal_conv_(?:read_state|identity|cursor|entries|decorate|summary));dur=(\d+(?:\.\d+)?)$/);
        if (!match) continue;
        const [, name, raw] = match;
        if (seen.has(name)) { delete metrics[name]; continue; }
        seen.add(name);
        const duration = Number(raw);
        if (Number.isFinite(duration)) metrics[name] = duration;
      }
    }
    record.serverTiming = Object.keys(metrics).length ? metrics : null;
    write({ kind: 'response_received', request_id: record.id, response_status: record.status,
      server_timing_ms: record.serverTiming, timing: requestTiming(record.request) });
    if (record.status >= 400 && record.frame) {
      const task = snapshot(record.request.frame()); snapshots.add(task); task.finally(() => snapshots.delete(task));
    }
  });
  const finish = (request, failed) => {
    const record = start(request);
    const rawReason = request.failure()?.errorText;
    write({ kind: failed ? 'request_failed' : 'request_finished', request_id: record.id,
      ended_at_ms: Date.now(), response_status: record.status,
      failed_reason: failed ? (/^net::ERR_[A-Z0-9_]+$/.test(rawReason || '') ? rawReason : 'unavailable') : null,
      cancellation_cause: failed ? 'unknown' : null, server_timing_ms: record.serverTiming || null,
      timing: requestTiming(request) });
    live.delete(record.id);
    if (failed && record.frame) {
      const task = snapshot(request.frame()); snapshots.add(task); task.finally(() => snapshots.delete(task));
    }
  };
  listen(context, 'requestfinished', request => finish(request, false));
  listen(context, 'requestfailed', request => finish(request, true));
  await context.exposeBinding('__portalTauObservationRecord', ({ frame }, data) => {
    if (!data || !/^[0-9a-f-]{36}$/.test(data.token || '')) return;
    const state = frameState(frame);
    if (data.kind === 'document_started' || data.kind === 'pageshow') {
      if (state.pending && data.kind === 'document_started') { state.document = state.pending; state.pending = null; }
      if (tokens.has(data.token)) state.document = tokens.get(data.token);
      else {
        if (state.document.confirmed) state.document = document();
        tokens.set(data.token, state.document);
        state.document.confirmed = true;
      }
      write({ kind: data.kind, ...location(state), browser_observed_at_ms: Number.isFinite(data.at) ? data.at : null, time_origin_ms: Number.isFinite(data.time_origin) ? data.time_origin : null });
    } else if (['beforeunload', 'pagehide', 'focus', 'blur', 'visibilitychange', 'click_observed', 'document_error', 'portal_connections', 'portal_stream_ready', 'pdf_reader_cancelled', 'resource_timing', 'resource_timing_buffer_full', 'observer_unavailable'].includes(data.kind)) {
      const doc = tokens.get(data.token);
      const associated = { ...location(state), document_id: doc?.id || null, document_confirmed: !!doc };
      if (data.kind === 'click_observed') {
        if (!Number.isSafeInteger(data.sequence) || data.sequence < 1) return;
        const tags = new Set('a button input select option textarea label span div svg path use img li ul nav header main body html'.split(' '));
        const action = actions.get(frame.page());
        write({ kind: data.kind, ...associated, click_id: `${doc?.id || data.token}:click:${data.sequence}`,
          stage: ['capture', 'after_dispatch', 'beforeunload', 'pagehide'].includes(data.stage) ? data.stage : 'unknown',
          browser_observed_at_ms: Number.isFinite(data.at) ? data.at : null,
          target_tag: tags.has(data.target_tag) ? data.target_tag : 'other',
          control_tag: tags.has(data.control_tag) ? data.control_tag : null,
          target_indices: Array.isArray(data.target_indices) ? data.target_indices.slice(0, 16).map(index => Number.isSafeInteger(index) && index >= 0 ? index : null) : [],
          link_path: typeof data.link_path === 'string' ? safePath(data.link_path) : null,
          default_prevented: typeof data.default_prevented === 'boolean' ? data.default_prevented : null,
          is_trusted: data.is_trusted === true,
          button: Number.isSafeInteger(data.button) ? data.button : null,
          client_x: Number.isFinite(data.client_x) ? data.client_x : null,
          client_y: Number.isFinite(data.client_y) ? data.client_y : null,
          action_id: action?.id || null,
          action_association: action ? 'observed_in_explicit_page_action_scope' : null });
      } else if (['focus', 'blur', 'visibilitychange'].includes(data.kind)) {
        write({ kind: data.kind, ...associated,
          browser_observed_at_ms: Number.isFinite(data.at) ? data.at : null,
          hidden: typeof data.hidden === 'boolean' ? data.hidden : null });
      } else if (data.kind === 'document_error') {
        // This is a separate document-owned receipt, not a time-based match to
        // Playwright's pageerror (which exposes no frame). Keep both facts.
        write({ kind: data.kind, ...associated,
          event_type: ['error', 'unhandledrejection'].includes(data.event_type) ? data.event_type : 'unknown',
          browser_observed_at_ms: Number.isFinite(data.at) ? data.at : null,
          ...safeError({ name: data.error_name }),
          line: Number.isSafeInteger(data.line) && data.line > 0 ? data.line : null,
          column: Number.isSafeInteger(data.column) && data.column > 0 ? data.column : null,
          page_error_association: 'unavailable_no_shared_event_identity' });
      } else if (data.kind === 'resource_timing') {
        for (const entry of data.entries || []) {
          const numericFields = ['startTime','duration','fetchStart','redirectStart','redirectEnd','domainLookupStart','domainLookupEnd','connectStart','connectEnd','secureConnectionStart','requestStart','responseStart','responseEnd','transferSize','encodedBodySize','decodedBodySize'];
          const values = {}, unavailable = {};
          for (const field of numericFields) {
            const value = entry.values?.[field];
            values[field] = Number.isFinite(value) && value >= 0 ? value : null;
            if (values[field] === null) unavailable[field] = 'not_exposed_or_zero_placeholder';
          }
          write({ kind: 'resource_timing', ...associated, resource_entry_id: id('resource'),
            path: safePath(entry.path), initiator_type: /^(fetch|xmlhttprequest|img|script|link|css|iframe|other|eventsource|audio|video|track|navigation|beacon|icon|object|embed|font|internal)$/.test(entry.initiator_type || '') ? entry.initiator_type : 'other',
            time_origin_ms: Number.isFinite(entry.time_origin_ms) ? entry.time_origin_ms : null,
            values, unavailable, request_id: null, request_association: 'unavailable_resource_timing_has_no_transport_id', server_execution_time: null });
        }
      } else if (data.kind === 'portal_stream_ready') {
        const streamId = /^[a-z0-9-]{1,96}$/.test(data.stream_id || '') ? data.stream_id : null;
        const predecessor = /^[a-z0-9-]{1,96}$/.test(data.predecessor_stream_id || '') ? data.predecessor_stream_id : null;
        const path = safePath(data.path);
        const candidates = (transportRequests.get(path === '/api/events' && streamId ? `stream:${streamId}` : null) || [])
          .filter(request => request.document === doc && request.frame === state);
        const match = doc && candidates.length === 1 ? candidates[0] : null;
        write({ kind: 'portal_stream_ready', ...associated, path, stream_id: streamId, predecessor_stream_id: predecessor,
          ready_at_ms: Number.isFinite(data.ready_at_ms) ? data.ready_at_ms : null,
          request_id: match?.id || null,
          request_association: match ? 'portal_stream_identity' : 'unavailable_no_unique_transport_identity' });
      } else if (data.kind === 'pdf_reader_cancelled') {
        const transportId = /^[a-z0-9-]{1,96}$/.test(data.transport_id || '') ? data.transport_id : null;
        const path = safePath(data.path);
        const candidates = (transportRequests.get(transportId ? `request:${transportId}` : null) || [])
          .filter(request => request.document === doc && request.frame === state
            && request.request.method() === 'GET'
            && /^\/api\/articles\/[^/]+\/pdf$/.test(new URL(request.request.url()).pathname)
            && safePath(request.request.url()) === path);
        const match = doc && candidates.length === 1 ? candidates[0] : null;
        write({ kind: 'pdf_reader_cancelled', ...associated, path, transport_id: transportId,
          reason: data.reason === 'pdf_range_handover' ? data.reason : 'unknown',
          cancelled_at_ms: Number.isFinite(data.cancelled_at_ms) ? data.cancelled_at_ms : null,
          request_id: match?.id || null,
          request_association: match ? 'pdf_fetch_identity' : 'unavailable_no_unique_transport_identity' });
      } else if (data.kind === 'portal_connections') {
        for (const entry of data.entries || []) {
          const reason = /^(subscription_unchanged|subscription_changed|handover_complete|next_stream_closed|closed|stream_error|stale_stream|handover_timeout|next_stream_error|restart|leadership_released|page_closed|retry|caller_aborted|stream_batch_read|first_batch_read|backlog_read|watch_closed|AbortError)$/.test(entry.reason || '') ? entry.reason : 'unknown';
          const streamId = /^[a-z0-9-]{1,96}$/.test(entry.stream_id || '') ? entry.stream_id : null;
          const transportId = /^[a-z0-9-]{1,96}$/.test(entry.transport_id || '') ? entry.transport_id : null;
          const successor = /^[a-z0-9-]{1,96}$/.test(entry.successor_stream_id || '') ? entry.successor_stream_id : null;
          const safe = { path: safePath(entry.path), stream_id: streamId, transport_id: transportId, successor_stream_id: successor, reason, closed_at_ms: Number.isFinite(entry.closed_at_ms) ? entry.closed_at_ms : null };
          const key = `${doc?.id}:${JSON.stringify(safe)}`;
          if (state.connections?.has(key)) continue;
          (state.connections ||= new Set()).add(key);
          const transportKey = safe.path === '/api/events' && streamId ? `stream:${streamId}`
            : (safe.path === '/api/instructions/execute' && transportId ? `request:${transportId}` : null);
          const candidates = (transportRequests.get(transportKey) || []).filter(request => request.document === doc && request.frame === state);
          const match = doc && candidates.length === 1 ? candidates[0] : null;
          write({ kind: 'portal_connection_closed', ...associated, ...safe,
            request_id: match?.id || null,
            request_association: match ? (streamId ? 'portal_stream_identity' : 'portal_request_identity') : 'unavailable_no_unique_transport_identity' });
        }
      } else write({ kind: data.kind, ...associated, browser_observed_at_ms: Number.isFinite(data.at) ? data.at : null, persisted: data.persisted === true });
    }
  });
  function documentObserver(sanitize) {
    // getRandomValues also works on the plain HTTP DEV entry and about:blank.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    const token = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    const emit = data => window.__portalTauObservationRecord({ token, at: Date.now(), ...data }).catch(() => {});
    // Capture all hit targets, including a nested icon and non-interactive
    // overlays. No labels, IDs, text, values or raw URLs enter the evidence.
    const pendingClicks = new Map();
    let clickSequence = 0;
    const finishClick = (sequence, stage) => {
      const pending = pendingClicks.get(sequence);
      if (!pending) return;
      pendingClicks.delete(sequence);
      clearTimeout(pending.timer);
      emit({ ...pending.record, stage, default_prevented: pending.event.defaultPrevented });
    };
    window.addEventListener('click', event => {
      const target = event.composedPath()[0];
      const control = target?.closest?.('a,button,input,select,textarea');
      const link = target?.closest?.('a[href]');
      const indices = [];
      for (let node = target; node?.parentElement && indices.length < 16; node = node.parentElement) {
        indices.push(Array.prototype.indexOf.call(node.parentElement.children, node));
      }
      const sequence = ++clickSequence;
      const record = { kind: 'click_observed', sequence,
        target_tag: target?.localName, control_tag: control?.localName,
        target_indices: indices, link_path: link ? sanitize(link.href) : null,
        default_prevented: event.defaultPrevented, is_trusted: event.isTrusted,
        button: event.button, client_x: event.clientX, client_y: event.clientY };
      emit({ ...record, stage: 'capture' });
      // A microtask can run between native event listeners. Sample after the
      // dispatch task, or at unload, so later preventDefault is not missed.
      const timer = setTimeout(() => finishClick(sequence, 'after_dispatch'), 0);
      pendingClicks.set(sequence, { event, record, timer });
    }, { capture: true, passive: true });
    for (const stage of ['beforeunload', 'pagehide']) window.addEventListener(stage, () => {
      for (const sequence of pendingClicks.keys()) finishClick(sequence, stage);
    }, { capture: true, passive: true });
    // PDF.js cancels its full-body reader when it selects byte ranges. Observe
    // that actual call, not a nearby navigation or a subsequent successful PDF.
    // Each same-origin fetch gets its own opaque ID; no content enters evidence.
    if (typeof window.fetch === 'function') {
      const nativeFetch = window.fetch.bind(window);
      let pdfFetchSequence = 0;
      window.fetch = async (input, init) => {
        let url;
        try { url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href); }
        catch { return nativeFetch(input, init); }
        const method = String(init?.method || input?.method || 'GET').toUpperCase();
        if (url.origin !== location.origin || method !== 'GET'
          || !/^\/api\/articles\/[^/]+\/pdf$/.test(url.pathname)) return nativeFetch(input, init);
        const transportId = `${token}-pdf-${++pdfFetchSequence}`;
        const headers = new Headers(init?.headers || input?.headers);
        headers.set('X-Portal-Request-ID', transportId);
        const response = await nativeFetch(input, { ...init, headers });
        const body = response.body;
        if (body) {
          const getReader = body.getReader.bind(body);
          body.getReader = (...args) => {
            const reader = getReader(...args);
            const cancel = reader.cancel.bind(reader);
            reader.cancel = reason => {
              const result = cancel(reason);
              emit({ kind: 'pdf_reader_cancelled', path: sanitize(url.href), transport_id: transportId,
                reason: response.status === 200 && reason?.name === 'AbortException'
                  && reason?.message === 'Streaming is disabled.' ? 'pdf_range_handover' : 'unknown',
                cancelled_at_ms: Date.now() });
              return result;
            };
            return reader;
          };
        }
        return response;
      };
    }
    const numeric = value => Number.isFinite(value) && value >= 0 ? value : null;
    const fields = ['startTime','duration','fetchStart','redirectStart','redirectEnd','domainLookupStart','domainLookupEnd','connectStart','connectEnd','secureConnectionStart','requestStart','responseStart','responseEnd','transferSize','encodedBodySize','decodedBodySize'];
    const entries = list => emit({ kind: 'resource_timing', entries: list.map(entry => {
      const values = {}, unavailable = {};
      for (const field of fields) {
        let value = numeric(entry[field]);
        if (['requestStart', 'responseStart', 'responseEnd'].includes(field) && value === 0) value = null;
        values[field] = value;
        if (value === null) unavailable[field] = 'not_exposed_or_zero_placeholder';
      }
      return { path: sanitize(entry.name), initiator_type: entry.initiatorType, time_origin_ms: performance.timeOrigin, values, unavailable, request_id: null,
        request_association: 'unavailable_resource_timing_has_no_transport_id', server_execution_time: null };
    }) });
    let observer;
    try {
      observer = new PerformanceObserver(list => entries(list.getEntries()));
      observer.observe({ type: 'resource', buffered: true });
    } catch { emit({ kind: 'observer_unavailable' }); }
    const connectionReasons = new Set('subscription_unchanged subscription_changed handover_complete next_stream_closed closed stream_error stale_stream handover_timeout next_stream_error restart leadership_released page_closed retry caller_aborted stream_batch_read first_batch_read backlog_read watch_closed AbortError'.split(' '));
    const connections = items => emit({ kind: 'portal_connections', entries: items.map(item => ({
      path: sanitize(item.url), reason: connectionReasons.has(item.reason) ? item.reason : 'unknown',
      stream_id: /^[a-z0-9-]{1,96}$/.test(item.stream_id || '') ? item.stream_id : null,
      successor_stream_id: /^[a-z0-9-]{1,96}$/.test(item.successor_stream_id || '') ? item.successor_stream_id : null,
      transport_id: /^[a-z0-9-]{1,96}$/.test(item.transport_id || '') ? item.transport_id : null,
      closed_at_ms: Number.isFinite(Date.parse(item.at)) ? Date.parse(item.at) : null,
    })) });
    // A periodic/unload flush can precede the owner's pagehide handler. Take
    // the actual close receipt synchronously when the owner emits it instead.
    window.addEventListener('portal:connection-closed', event => {
      if (event.detail && typeof event.detail === 'object') connections([event.detail]);
    });
    window.addEventListener('portal:connection-ready', event => {
      const item = event.detail;
      if (!item || typeof item !== 'object') return;
      emit({ kind: 'portal_stream_ready', path: sanitize(item.url),
        stream_id: /^[a-z0-9-]{1,96}$/.test(item.stream_id || '') ? item.stream_id : null,
        predecessor_stream_id: /^[a-z0-9-]{1,96}$/.test(item.predecessor_stream_id || '') ? item.predecessor_stream_id : null,
        ready_at_ms: Number.isFinite(Date.parse(item.at)) ? Date.parse(item.at) : null,
      });
    });
    const flush = () => {
      const remaining = observer?.takeRecords() || []; if (remaining.length) entries(remaining);
      connections(window.PortalEvents?.closedStreams?.() || []);
    };
    window.__portalTauObservation = { flush };
    emit({ kind: 'document_started', time_origin: performance.timeOrigin });
    for (const eventType of ['error', 'unhandledrejection']) window.addEventListener(eventType, event => {
      // No error message, arbitrary rejection value, filename or stack crosses
      // the binding. Do not preventDefault: native diagnostics must survive.
      let name;
      try {
        const value = eventType === 'error' ? event.error : event.reason;
        const candidate = value?.name;
        if (typeof candidate === 'string' && candidate.length <= 32) name = candidate;
      } catch { /* A rejection may have a throwing name getter. */ }
      emit({ kind: 'document_error', event_type: eventType, error_name: name,
        line: eventType === 'error' && Number.isSafeInteger(event.lineno) ? event.lineno : null,
        column: eventType === 'error' && Number.isSafeInteger(event.colno) ? event.colno : null });
    });
    window.addEventListener('pageshow', () => emit({ kind: 'pageshow', time_origin: performance.timeOrigin }));
    for (const kind of ['focus', 'blur']) window.addEventListener(kind, () => {
      emit({ kind, hidden: document.hidden });
    });
    document.addEventListener('visibilitychange', () => {
      emit({ kind: 'visibilitychange', hidden: document.hidden });
    });
    for (const kind of ['beforeunload', 'pagehide']) window.addEventListener(kind, event => {
      emit({ kind, persisted: event.persisted }); flush();
      // The product's later pagehide handler may close connections. Capture its
      // receipts afterwards; destroyed contexts may still prevent delivery.
      queueMicrotask(flush);
    });
    performance.addEventListener('resourcetimingbufferfull', () => emit({ kind: 'resource_timing_buffer_full' }));
  }
  // Serialize the factory with its explicit public configuration. Serializing
  // a closure alone loses Node-side dependencies inside the page's realm.
  await context.addInitScript({ content: `(${documentObserver.toString()})((${createPathSanitizer.toString()})(${JSON.stringify([...residentReadRoutes.keys()])}));` });
  for (const page of context.pages()) pageState(page);
  write({ kind: 'capture_started', clocks: { observed_at_ms: 'node_epoch_ms', resource_values: 'browser_time_origin_plus_relative_ms' },
    limitations: ['No request bodies, arbitrary headers or query strings are persisted; only Portal EventSource and instruction transport identities are retained', 'Request/document association during navigation can be unconfirmed', 'Document destruction can prevent unload/Portal receipt delivery; absence does not prove no close occurred', 'Portal transport identity is matched only to a unique request in the same frame/document; native reconnects remain ambiguous, ResourceTiming lacks transport identity', 'Browser timing is not backend execution timing', 'server_timing_ms contains only allowlisted response-header durations: portal_queue is Web executor wait; portal_handler includes RPC/cache wait and excludes body upload/response streaming; portal_conv_* stages are request-local conversation read durations including RPC/cache waits, not database CPU time', 'Zero browser sizes/phases may reflect reuse, cache or restricted exposure; never infer zero server cost'] });
  return {
    file,
    requestId(request) { return requests.get(request)?.id || null; },
    async action(page, name, operation) {
      if (stopped) throw new Error('Browser observation has ended');
      if (typeof name !== 'string' || !/^[a-zA-Z0-9_.:-]{1,80}$/.test(name) || typeof operation !== 'function') {
        throw new TypeError('Use a static action label and a callback containing the operation and its ready check');
      }
      if (actions.has(page)) throw new Error('An action already owns this page; include its ready check in the same callback');
      const action = { id: id('action'), request_ids: [] };
      const started = performance.now();
      actions.set(page, action);
      write({ kind: 'action_started', action_id: action.id, name, ...location(frameState(page.mainFrame())) });
      let status = 'failed', error;
      try {
        const result = await operation(page);
        status = 'completed';
        return result;
      } catch (caught) {
        error = safeError(caught);
        throw caught;
      } finally {
        actions.delete(page);
        write({ kind: 'action_finished', action_id: action.id, name, status, error,
          ...location(frameState(page.mainFrame())), duration_ms: performance.now() - started,
          request_ids: action.request_ids,
          request_association: 'observed_in_explicit_page_action_scope',
          causality: 'not_inferred_background_requests_may_share_scope' });
      }
    },
    async flush() {
      for (const page of context.pages()) for (const frame of page.frames()) await snapshot(frame);
      await Promise.allSettled([...snapshots]);
    },
    stop() {
      if (stopped) return;
      try {
      for (const record of live.values()) write({ kind: 'request_pending_at_capture_end', request_id: record.id,
        response_status: record.status, ended_at_ms: null, cancellation_cause: 'unknown', timing: requestTiming(record.request) });
      write({ kind: 'capture_ended', pending_request_count: live.size });
      } finally { stopped = true; releases.forEach(release => release()); }
    },
  };
}
module.exports = { observeBrowser };
