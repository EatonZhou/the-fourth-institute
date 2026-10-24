'use strict';

function requireTarget({ residentId, entryKey }) {
  if (!/^resident_[0-9a-f]+$/.test(residentId) || entryKey !== 'resident:' + residentId) {
    throw new Error('Explicit target Resident and entry key required');
  }
}

// Passive, target-only evidence. Never refresh the Store, open a panel, or
// manufacture a request. Call with the page which owns the original action.
// Arm while collapsed, before the native open. A missing/wrong first sample
// stays missing/wrong even when the UI subsequently converges.
function createMailboxProbe({ residentId, entryKey, documentId }) {
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const bool = value => typeof value === 'boolean' ? value : null;
  const visible = element => !!element?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const geometry = element => {
    if (!element) return null;
    const r = element.getBoundingClientRect(), s = getComputedStyle(element);
    return { visible: visible(element), rect: [r.x, r.y, r.width, r.height],
      intersects_viewport: r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight,
      display: s.display, visibility: s.visibility, opacity: s.opacity,
      color: s.color, background_color: s.backgroundColor, font_weight: s.fontWeight };
  };
  const count = element => {
    const value = element?.textContent?.trim();
    return { visible: visible(element), value: /^\d+\+?$/.test(value || '') ? value : null };
  };
  function sample() {
    const snapshot = window.PortalConversationStore?.snapshot?.();
    const entries = Array.isArray(snapshot?.entries) ? snapshot.entries : null;
    const entry = entries?.find(item => item.key === entryKey && item.resident_id === residentId);
    const root = document.querySelector('[data-portal-agent-inbox]');
    // Personal Assistant has the same panel class; it is not Mailbox.
    const panel = root?.querySelector('.portalAgentInboxPanel:not(.portalPersonalAssistantPanel)');
    const rows = [...(panel?.querySelectorAll('[data-agent-conversation-key]') || [])]
      .filter(row => row.getAttribute('data-agent-conversation-key') === entryKey);
    const section = key => ({ count: number(snapshot?.sections?.[key]?.count),
      unreadCount: number(snapshot?.sections?.[key]?.unreadCount) });
    return { document_id: documentId, time_origin_ms: performance.timeOrigin,
      browser_ms: performance.now(), epoch_ms: Date.now(), visibility_state: document.visibilityState,
      panel: geometry(panel), expanded: root?.querySelector('[data-agent-conversation-trigger]')?.getAttribute('aria-expanded') ?? null,
      badge: count(root?.querySelector('[data-agent-conversation-trigger] [data-agent-conversation-count]')),
      filters: [...(root?.querySelectorAll('[data-agent-inbox-filter]') || [])].map(button => ({
        filter: ['messages', 'todo'].includes(button.dataset.agentInboxFilter) ? button.dataset.agentInboxFilter : 'unknown',
        selected: button.getAttribute('aria-pressed'), count: count(button.querySelector('[data-agent-section-count]')) })),
      store: { available: !!snapshot, ready: bool(snapshot?.ready), loading: bool(snapshot?.loading),
        refreshing: bool(snapshot?.refreshing), syncing: bool(snapshot?.syncing), paged: bool(snapshot?.paged),
        error_present: snapshot?.error == null ? null : Boolean(snapshot.error), revision: number(snapshot?.revision),
        loaded_entry_count: entries?.length ?? null, unreadCount: number(snapshot?.unreadCount),
        sections: { conversation: section('conversation'), notification: section('notification') },
        target: entry ? { key: entryKey, resident_id: residentId, unread: bool(entry.unread),
          attention_unread: bool(entry.attention_unread), read_revision: number(entry.read_revision) } : null },
      target_dom: rows.map(row => ({ ...geometry(row), key: entryKey,
        unread_class: row.classList.contains('unread'), status: row.getAttribute('data-status'),
        unread_dot: geometry(row.querySelector('.portalAgentInboxUnreadDot')),
        unread_count: count(row.querySelector('.portalAgentInboxUnreadCount')),
        status_badge: geometry(row.querySelector('.portalAgentInboxStatus')) })),
      errors: [...(panel?.querySelectorAll('[data-agent-conversation-error]') || [])].map(element => ({
        visible: visible(element), empty: !element.textContent?.trim() })) };
  }
  let current = null, frame, stopped = false;
  const captures = [];
  function observe(kind) {
    if (!current) return;
    const value = sample();
    if (!value.panel?.visible) return;
    current.first_visible_observation ??= { kind, ...value };
    if (kind === 'animation_frame') {
      current.first_visible_animation_frame = value;
      current = null;
    }
  }
  const mutations = new MutationObserver(() => observe('mutation'));
  mutations.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  function tick() { observe('animation_frame'); frame = requestAnimationFrame(tick); }
  frame = requestAnimationFrame(tick);
  return {
    sample,
    arm(label) {
      if (stopped || current || captures.some(item => item.label === label)) throw new Error('Mailbox capture cannot be rearmed');
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(label)) throw new Error('Use a static capture label');
      const before = sample();
      if (!before.panel || before.panel.visible) throw new Error('Arm before opening the collapsed Mailbox');
      current = { label, before, first_visible_observation: null, first_visible_animation_frame: null };
      captures.push(current);
      return before;
    },
    take: () => captures,
    stop() { stopped = true; mutations.disconnect(); cancelAnimationFrame(frame); return captures; },
  };
}

async function observeMailboxFrames(page, { residentId, entryKey, documentId }) {
  requireTarget({ residentId, entryKey });
  if (!documentId) {
    throw new Error('Explicit target identity and original observer document ID required');
  }
  const handle = await page.evaluateHandle(createMailboxProbe, { residentId, entryKey, documentId });
  return {
    sample: () => handle.evaluate(probe => probe.sample()),
    arm: label => handle.evaluate((probe, value) => probe.arm(value), label),
    take: () => handle.evaluate(probe => probe.take()),
    stop: async () => { try { return await handle.evaluate(probe => probe.stop()); } finally { await handle.dispose(); } },
  };
}

// Apply to the ORIGINAL parsed /api/agent-conversations response. Keep root
// page counts distinct from mailbox_summary global counts. Missing != zero.
// Caller retains actual request/transport/frame IDs and response timestamps.
function mailboxResponseMetadata(payload, { residentId, entryKey }) {
  requireTarget({ residentId, entryKey });
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const boolean = value => typeof value === 'boolean' ? value : null;
  const counts = value => ({ unread_count: number(value?.unread_count),
    sections: Object.fromEntries(['conversation', 'notification'].map(section => [section, {
      count: number(value?.sections?.[section]?.count), unread_count: number(value?.sections?.[section]?.unread_count) }])) });
  const entries = value => Array.isArray(value?.entries) ? value.entries : null;
  const target = value => entries(value)?.filter(item => item.key === entryKey && item.resident_id === residentId)
    .map(item => ({ key: entryKey, resident_id: residentId, unread: boolean(item.unread),
      attention_unread: boolean(item.attention_unread), read_revision: number(item.read_revision), cursor: number(item.cursor) })) ?? null;
  const summary = payload?.mailbox_summary;
  return { page: { ...counts(payload), entry_count: entries(payload)?.length ?? null, target_entries: target(payload),
      paged: boolean(payload?.paged), read_revision: number(payload?.read_revision), revision: number(payload?.revision),
      resident_cursor: number(payload?.resident_cursor), projection_cursor: number(payload?.projection_cursor),
      target_present: boolean(payload?.target_present),
      next_page_present: typeof payload?.next_page_token === 'string' ? Boolean(payload.next_page_token) : null },
    global_summary: summary && typeof summary === 'object' && !Array.isArray(summary)
      ? { ...counts(summary), unread_membership_count: entries(summary)?.length ?? null, target_entries: target(summary) } : null };
}

// Only original target Resident events, never message bodies or error details.
// Missing/null/malformed containers remain distinguishable. This is evidence
// extraction, not a completion verdict: state events with omitted payloads do
// not establish input identity, and later responses must not fill these slots.
function mailboxResidentEventMetadata(events, target) {
  requireTarget(target);
  const type = value => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const fields = (value, names) => {
    if (!object(value)) return { type: type(value), fields: null };
    return { type: 'object', fields: Object.fromEntries(names.map(name => {
      const field = value[name], present = Object.hasOwn(value, name);
      const scalar = field === null || ['boolean', 'number', 'string'].includes(typeof field);
      return [name, { present, type: present ? type(field) : 'missing',
        value: present && scalar ? field : null }];
    })) };
  };
  return { type: type(events), target_events: Array.isArray(events)
    ? events.filter(event => object(event) && event.resident_id === target.residentId).map(event => ({
      ...fields(event, ['resident_id', 'event_type', 'cursor', 'version', 'payload_omitted']),
      payload: fields(event.payload, ['message_id', 'input_message_id', 'completion_id']),
      input_state: fields(object(event.payload) ? event.payload.input_state : undefined,
        ['message_id', 'state', 'updated_at']),
    })) : null };
}

module.exports = { observeMailboxFrames, mailboxResponseMetadata, mailboxResidentEventMetadata };
