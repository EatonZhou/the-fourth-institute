'use strict';

// Read once in the original pre-navigation window. inputMessageId/finalMessageId
// must come from the original accepted input and its verified Final, not from the
// index being checked. The caller saves this result before asserting it, alongside
// its original observer identities/receipts. This is not a read-mark or RTT test.
async function readHomeUnreadPrerequisites(root, { residentId, lineageId, inputMessageId, finalMessageId, requestActor }) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '') || !/^lineage_[a-z0-9]+$/.test(lineageId || '')
    || !/^msg_[a-zA-Z0-9_]+$/.test(inputMessageId || '') || !/^msg_[a-zA-Z0-9_]+$/.test(finalMessageId || '')
    || typeof requestActor !== 'string' || !requestActor.trim()) {
    throw new TypeError('Expected exact Resident/Lineage, original input, verified Final and request actor');
  }
  const expected = { resident_id: residentId, lineage_id: lineageId, input_message_id: inputMessageId,
    completion_id: finalMessageId, request_actor: requestActor };
  const result = { expected, ready: false, observation: null, error: null };
  try {
    result.observation = await root.evaluateAll((roots, expected) => {
      const observation = { roots: [], icons: [], protocol: null, store: null, entries: [],
        checks: {}, ready: false, error: null };
      let phase = 'roots';
      const value = item => item === undefined ? null : item;
      const fields = (object, keys) => Object.fromEntries(keys.map(key => [key, value(object?.[key])]));
      try {
        const visible = el => !!el && el.isConnected && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
          && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0
          && !el.closest('[hidden],[aria-hidden="true"]');
        observation.roots = roots.map(el => ({ tag: el.tagName, id: el.id,
          component: el.getAttribute('data-home-component'), connected: el.isConnected, visible: visible(el) }));
        const uniqueRoot = roots.length === 1 && roots[0].matches('[data-home-component="agent-status"]') && visible(roots[0]);
        observation.checks.root = uniqueRoot;
        if (!uniqueRoot) return observation;
        const document = roots[0].ownerDocument, win = document.defaultView;
        observation.location = { origin: win.location.origin, pathname: win.location.pathname };
        phase = 'icons';
        observation.icons = [...roots[0].querySelectorAll('a.agent-status-creature[data-agent-status-resident]')]
          .filter(el => el.getAttribute('data-agent-status-resident') === expected.resident_id).map(el => {
            const href = el.getAttribute('href');
            let link = null;
            try {
              const url = new URL(href, win.location.href);
              const one = (key, id) => { const values = url.searchParams.getAll(key); return values.length === 1 && values[0] === id; };
              link = { origin: url.origin, path: url.pathname, resident: url.searchParams.getAll('resident'),
                lineage: url.searchParams.getAll('lineage'), open_chat: url.searchParams.getAll('open_chat'),
                matches: !!href && !url.username && !url.password && url.origin === win.location.origin
                  && url.pathname === '/lineage' && one('resident', expected.resident_id)
                  && one('lineage', expected.lineage_id) && one('open_chat', '1') };
            } catch (_) { /* Malformed links remain unknown, not ready. */ }
            return { resident_id: el.getAttribute('data-agent-status-resident'), lineage_id: el.getAttribute('data-agent-status-lineage'),
              href, link, visible: visible(el), label: el.getAttribute('aria-label'), class: el.getAttribute('class'),
              unread: el.classList.contains('unread'), running: el.classList.contains('running'), queued: el.classList.contains('queued') };
          });
        const icon = observation.icons.length === 1 ? observation.icons[0] : null;
        observation.checks.unread_icon = !!icon && icon.visible && icon.lineage_id === expected.lineage_id
          && icon.link?.matches === true && icon.unread && !icon.running && !icon.queued;
        phase = 'resident_state';
        const snapshot = win.PortalResidentProtocol?.stateSnapshot?.(expected.resident_id);
        observation.protocol = { ...fields(snapshot, ['resident_id', 'ready', 'cursor']),
          state: fields(snapshot?.state, ['status', 'work_state', 'working', 'queued', 'queued_count', 'active_message_id', 'intake_state']),
          viewer_state: fields(snapshot?.viewer_state, ['unread', 'read_revision']) };
        const protocol = observation.protocol, state = protocol.state;
        observation.checks.resident_ready = protocol.resident_id === expected.resident_id && protocol.ready === true;
        observation.checks.idle = state.status === 'idle' && state.work_state === 'idle'
          && state.working === false && state.queued === false && state.queued_count === 0 && state.active_message_id === '';
        observation.checks.viewer_unread = protocol.viewer_state.unread === true;
        phase = 'conversation_index';
        const store = win.PortalConversationStore?.snapshot?.();
        observation.store = { ...fields(store, ['loaded', 'ready', 'loading', 'refreshing', 'syncing', 'error']),
          entries_present: Array.isArray(store?.entries) };
        observation.checks.index_ready = observation.store.entries_present && observation.store.loaded === true
          && observation.store.ready === true && observation.store.loading === false
          && observation.store.refreshing === false && observation.store.syncing === false && !observation.store.error;
        observation.entries = (Array.isArray(store?.entries) ? store.entries : [])
          .filter(entry => entry?.key === 'resident:' + expected.resident_id || entry?.resident_id === expected.resident_id)
          .map(entry => fields(entry, ['key', 'resident_id', 'lineage_id', 'unread', 'status', 'completion_signature',
            'cursor', 'read_revision', 'resident_state_cursor', 'resident_state_ready']));
        const entry = observation.entries.length === 1 ? observation.entries[0] : null;
        observation.checks.entry = !!entry && entry.key === 'resident:' + expected.resident_id
          && entry.resident_id === expected.resident_id && (entry.lineage_id === null || entry.lineage_id === expected.lineage_id)
          && entry.status === 'idle'
          && entry.unread === true && entry.resident_state_ready === true;
        phase = 'completion_marker';
        observation.completion = { payload: null, schema_supported: false, parse_error: null };
        const marker = entry?.completion_signature, prefix = 'actor-completion-v7:';
        if (typeof marker === 'string' && marker.startsWith(prefix)) {
          try {
            const payload = JSON.parse(marker.slice(prefix.length));
            observation.completion.payload = fields(payload, ['schema_version', 'request_actor', 'input_message_id', 'completion_id', 'completed_at']);
            observation.completion.schema_supported = payload?.schema_version === 1;
          } catch (error) {
            observation.completion.parse_error = { name: error.name, message: error.message, stack: error.stack };
          }
        }
        const completion = observation.completion.payload;
        observation.checks.current_final = observation.completion.schema_supported && completion?.request_actor === expected.request_actor
          && completion.input_message_id === expected.input_message_id && completion.completion_id === expected.completion_id
          && typeof completion.completed_at === 'string' && completion.completed_at.length > 0;
        observation.ready = Object.values(observation.checks).every(check => check === true);
      } catch (error) {
        observation.error = { phase, name: error.name, message: error.message, stack: error.stack };
      }
      return observation;
    }, expected);
    result.ready = result.observation.ready === true && !result.observation.error;
    result.error = result.observation.error;
  } catch (error) {
    result.error = { phase: 'original_document', name: error.name, message: error.message, stack: error.stack };
  }
  return result;
}

module.exports = { readHomeUnreadPrerequisites };
