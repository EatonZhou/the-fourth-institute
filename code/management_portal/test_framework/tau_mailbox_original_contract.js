'use strict';
const { mailboxResidentEventMetadata } = require('./tau_mailbox_evidence');

// Extract the original finite ISA envelope before assertions or readiness.
// CreateResident has no Mailbox item. An unset target must never match it.
function mailboxInstructionReceipt(body, { entryKey = null } = {}) {
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const b = object(body) ? body : {}, result = object(b.result) ? b.result : {};
  const scalar = value => value === null || ['string', 'number', 'boolean'].includes(typeof value) ? value : null;
  const item = object(result.item) ? result.item : null;
  return {
    instruction_id: scalar(b.instruction_id), status: scalar(b.status),
    replayed: scalar(b.replayed), commit_state: scalar(b.commit_state),
    resident_id: scalar(result.resident_id), message_id: scalar(result.message_id),
    accepted: scalar(result.accepted),
    item: typeof entryKey === 'string' && entryKey.length > 0 && item?.key === entryKey
      ? {key: entryKey, read_revision: scalar(item.read_revision)} : null,
    error_code: typeof b.error?.code === 'string' && /^[a-z][a-z0-9_.-]{1,95}$/.test(b.error.code)
      ? b.error.code : null,
  };
}

// Self-contained for installation in the original browser document. Retain
// actual scoped field arrays, not arbitrary payloads or other Residents' IDs.
function mailboxProjectionEventMetadata(detail, { residentId, entryKey }) {
  if (!/^resident_[a-f0-9]{12}$/.test(residentId || '') || entryKey !== 'resident:' + residentId) {
    throw new TypeError('Explicit Mailbox target required');
  }
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const type = value => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
  const own = (value, key) => object(value) && Object.hasOwn(value, key);
  const field = (container, key) => {
    const present = own(container, key), value = present ? container[key] : undefined;
    return {present, type: present ? type(value) : 'missing',
      value: present && Number.isSafeInteger(value) ? value : null};
  };
  const array = (container, key, allowed) => {
    const present = own(container, key), value = present ? container[key] : undefined;
    return {present, type: present ? type(value) : 'missing',
      length: Array.isArray(value) ? value.length : null,
      values: Array.isArray(value) ? value.filter(v => typeof v === 'string' && allowed.includes(v)) : null,
      omitted_count: Array.isArray(value) ? value.filter(v => typeof v !== 'string' || !allowed.includes(v)).length : null};
  };
  const scope = ['unread', 'read', 'projection', 'identity', 'state', 'runtime', 'session', 'session_restart', 'deleted'];
  const changes = detail?.projection_changes, details = detail?.projection_details;
  return {
    // Absence stays absence; the event often has no read_revision. The exact
    // MarkMailboxItemRead response owns that revision, not this notification.
    snapshot_required: {present: own(detail, 'snapshot_required'),
      type: own(detail, 'snapshot_required') ? type(detail.snapshot_required) : 'missing',
      value: typeof detail?.snapshot_required === 'boolean' ? detail.snapshot_required : null},
    read_revision: field(detail, 'read_revision'), revision: field(detail, 'revision'),
    resident_changes: array(detail?.resident_changes, residentId, scope),
    projection_changes: {
      index: array(changes?.['agent-conversation-index'], 'current', scope),
      target_view: array(changes?.['resident-view-state'], residentId, scope),
      global_view: array(changes?.['resident-view-state'], 'current', scope),
    },
    index_keys: array(details?.['agent-conversation-index']?.current, 'keys', [entryKey]),
  };
}

async function observeMailboxChanges(page, { residentId, entryKey, documentId, record }) {
  mailboxProjectionEventMetadata({}, {residentId, entryKey});
  if (!documentId || typeof record !== 'function') throw new TypeError('Original document and record sink required');
  const key = '__tauMailboxChanges_' + require('node:crypto').randomUUID().replaceAll('-', '');
  await page.exposeBinding(key, (source, row) => { if (source.frame === page.mainFrame()) record(row); });
  const handle = await page.evaluateHandle(({key, residentId, entryKey, documentId, source, eventSource}) => {
    const extract = (0, eval)('(' + source + ')');
    const extractEvents = (0, eval)('(' + eventSource + ')');
    // Verify both serialized extractors in this document before attaching the
    // original-action listener. Do not dispatch an event or create evidence.
    extract({}, {residentId, entryKey});
    extractEvents(undefined, {residentId, entryKey});
    const handler = event => window[key]({kind: 'portal_change', document_id: documentId, epoch_ms: Date.now(),
      time_origin_ms: performance.timeOrigin, browser_ms: performance.now(),
      ...extract(event.detail, {residentId, entryKey}),
      resident_events: extractEvents(event.detail?.resident_events, {residentId, entryKey})}).catch(() => {});
    const ready = event => window[key]({kind: 'events_ready', document_id: documentId,
      epoch_ms: Date.now(), time_origin_ms: performance.timeOrigin, browser_ms: performance.now(),
      reconnected: typeof event.detail?.reconnected === 'boolean' ? event.detail.reconnected : null}).catch(() => {});
    window.addEventListener('portal:change', handler);
    window.addEventListener('portal:events-ready', ready);
    return {stop: () => {window.removeEventListener('portal:change', handler);
      window.removeEventListener('portal:events-ready', ready);}};
  }, {key, residentId, entryKey, documentId, source: mailboxProjectionEventMetadata.toString(), eventSource: mailboxResidentEventMetadata.toString()});
  return {stop: async () => {try {await handle.evaluate(h => h.stop());} finally {await handle.dispose();}}};
}

module.exports = {mailboxInstructionReceipt, mailboxProjectionEventMetadata, observeMailboxChanges};
