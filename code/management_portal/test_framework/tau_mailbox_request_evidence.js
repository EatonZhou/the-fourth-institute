'use strict';
const {randomUUID} = require('node:crypto');

// Passive evidence for a future original action. No business request, retry,
// response replacement, Store write, or inferred event-to-request causality.
async function observeMailboxRequests({page, origin, residentId, requestId} = {}) {
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || !page?.context
      || !/^resident_[a-f0-9]{12}$/.test(residentId || '') || typeof requestId !== 'function') {
    throw new TypeError('Original Page, origin, owned Resident and portal.requestId required');
  }
  if (new URL(page.url()).origin !== base.origin) throw new Error('Original page origin mismatch');
  const cdp = await page.context().newCDPSession(page);
  const captureId = randomUUID(), records = [], native = [], pending = new Set(), index = new WeakMap();
  let stopped = false, sequence = 0;
  const cleanToken = (value, re) => typeof value === 'string' && re.test(value) ? value : null;
  const transport = headers => cleanToken(Object.entries(headers || {}).find(([k]) => k.toLowerCase() === 'x-portal-request-id')?.[1], /^[a-z0-9-]{1,96}$/);
  const metadata = (urlText, method, raw, headers) => {
    let url;
    try { url = new URL(urlText); } catch { return null; }
    if (url.origin !== base.origin || !url.pathname.startsWith('/api/')) return null;
    let body;
    if (method === 'POST' && url.pathname === '/api/instructions/execute') {
      if (typeof raw === 'string' && Buffer.byteLength(raw) <= 65536) {
        try { body = JSON.parse(raw); } catch { /* retain unavailable metadata */ }
      }
    }
    const submitted = cleanToken(body?.instruction, /^[A-Z][A-Za-z0-9]{1,63}$/);
    const target = body?.arguments?.resident_id;
    const residentRoute = /^\/api\/agent-workbench\/residents\/(projection|navigation-target)$/.test(url.pathname);
    const own = target === residentId || (residentRoute && method === 'GET'
      && url.searchParams.getAll('id').length === 1 && url.searchParams.get('id') === residentId);
    const path = url.pathname === '/api/instructions/execute' || residentRoute ? url.pathname : '/api/[unclassified]';
    const token = transport(headers);
    return {method, path, submitted_instruction: submitted, selected_resident: own,
      transport_id: token, signature: token ? `transport:${token}` : `original:${method}:${url.href}`};
  };
  const stackOf = initiator => {
    const frames = [];
    let stack = initiator?.stack, depth = 0;
    while (stack && depth++ < 4) {
      for (const f of stack.callFrames || []) {
        let url; try { url = new URL(f.url); } catch { continue; }
        if (url.origin !== base.origin || !/^\/static\/[A-Za-z0-9_./-]+\.js$/.test(url.pathname)) continue;
        frames.push({source: url.pathname,
          function_name: cleanToken(f.functionName, /^[A-Za-z_$][A-Za-z0-9_.$]{0,100}$/),
          line: Number.isSafeInteger(f.lineNumber) ? f.lineNumber + 1 : null,
          column: Number.isSafeInteger(f.columnNumber) ? f.columnNumber + 1 : null});
        if (frames.length >= 20) return frames;
      }
      stack = stack.parent;
    }
    return frames;
  };
  const initiated = event => {
    const m = metadata(event.request.url, event.request.method, event.request.postData, event.request.headers);
    if (!m) return;
    native.push({...m, cdp_request_id: event.requestId, frame_id: event.frameId || null,
      loader_id: event.loaderId || null, initiator_type: event.initiator?.type || 'unknown',
      stack: stackOf(event.initiator), observed_at_ms: Date.now()});
  };
  const started = request => {
    const m = metadata(request.url(), request.method(), request.postData(), request.headers());
    if (!m) return;
    const record = {...m, capture_sequence: ++sequence, request_id: requestId(request) || null,
      observed_at_ms: Date.now(), http_status: null, error_code: null, response_complete: false};
    records.push(record); index.set(request, record);
  };
  const received = response => {
    const record = index.get(response.request());
    if (!record) return;
    record.http_status = response.status();
    const job = (async () => {
      try {
        // Only an original error response for the pre-registered owned target.
        // Other Residents, successful results and all error text are omitted.
        if (record.selected_resident && response.status() >= 400
            && /^application\/json(?:\s*;|$)/i.test(response.headers()['content-type'] || '')) {
          const raw = await response.text();
          if (Buffer.byteLength(raw) <= 65536) {
            const body = JSON.parse(raw);
            record.error_code = cleanToken(body?.error?.code ?? body?.code, /^[a-z][a-z0-9_.-]{1,95}$/);
          } else record.response_gap = 'body_exceeds_bound';
        }
      } catch { record.response_gap = 'original_error_unreadable'; }
      finally { record.response_complete = true; record.response_at_ms = Date.now(); }
    })();
    pending.add(job); job.finally(() => pending.delete(job));
  };
  const failed = request => {
    const record = index.get(request);
    if (record) { record.response_complete = true; record.response_gap = 'original_request_failed'; }
  };
  cdp.on('Network.requestWillBeSent', initiated);
  try { await cdp.send('Network.enable'); }
  catch (error) { await cdp.detach(); throw error; }
  const registeredAt = Date.now();
  // Start is awaited before an original click. Requests already in flight do
  // not acquire a Playwright record and cannot be reconstructed afterwards.
  page.on('request', started); page.on('response', received); page.on('requestfailed', failed);
  const snapshot = () => ({capture_id: captureId, registered_at_ms: registeredAt, stopped,
    selected_resident_id: residentId, business_acceptance: false,
    records: records.map(record => {
      const {signature, ...safe} = record;
      const matches = native.filter(x => x.signature === signature && x.observed_at_ms >= registeredAt);
      const unique = matches.length === 1 && records.filter(x => x.signature === signature).length === 1;
      const match = unique ? matches[0] : null;
      return {...safe, initiator_association: match ? 'unique_original_request_signature' : 'unavailable_or_ambiguous',
        cdp_request_id: match?.cdp_request_id || null, frame_id: match?.frame_id || null,
        loader_id: match?.loader_id || null, initiator_type: match?.initiator_type || null,
        caller_stack: match?.stack.map(x => ({...x})) || [],
        trigger_reason: 'not_inferred_from_stack_or_time'};
    })});
  return Object.freeze({snapshot, async stop() {
    if (!stopped) {
      stopped = true;
      page.off('request', started); page.off('response', received); page.off('requestfailed', failed);
      cdp.off('Network.requestWillBeSent', initiated);
      await cdp.detach();
    }
    await Promise.all([...pending]);
    return snapshot();
  }});
}

// Navigation readiness and panel readiness are separate. A collapsed HATG is
// not a failed navigation and must be expanded by its own original UI action.
async function readMailboxSocietyPrerequisites(page) {
  const nav = page.getByRole('navigation', {name: 'Primary', exact: true});
  if (await nav.count() !== 1) return {navigation_ready: false, reason: 'primary_navigation_not_unique'};
  const links = nav.locator('a[href="/lineage"]');
  if (await links.count() !== 1) return {navigation_ready: false, reason: 'society_link_not_unique'};
  const link = await links.evaluate(node => {
    const r = node.getBoundingClientRect(), hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {visible: node.checkVisibility(), hit: hit === node || node.contains(hit), text: node.textContent.trim(),
      href: node.getAttribute('href'), disabled: node.getAttribute('aria-disabled') === 'true'};
  });
  const graph = page.getByRole('region', {name: 'HATG', exact: true});
  const visible = await graph.count() === 1 && await graph.isVisible();
  const show = page.getByRole('button', {name: 'Show HATG', exact: true});
  return {navigation_ready: link.visible && link.hit && !link.disabled, link,
    pathname: new URL(page.url()).pathname, society_document: new URL(page.url()).pathname === '/lineage',
    hatg_visible: visible, show_hatg_ready: !visible && await show.count() === 1
      && await show.isVisible() && await show.isEnabled()};
}
module.exports = {observeMailboxRequests, readMailboxSocietyPrerequisites};
