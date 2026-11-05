(() => {
  "use strict";
  const {normalizeIntent, displayCommand, results, messageOriginKey, normalizeOriginKey, normalizeMessageOrigin} = window.PortalDisplayProtocol;
  // The host owns selection. Requests only acquire permission to present at the
  // user's current navigation epoch; data loaders never acquire that permission.
  function create({ uuid = () => window.PortalUIPrimitives.randomUUID() } = {}) {
    let epoch = 0, latest = "", active = null, pinned = false;
    const requests = new Map(), entries = [];
    function issue(residentId, baseline = []) {
      const id = uuid(); latest = id;
      requests.set(id, { id, epoch, residentId, baseline: new Set(baseline), version: 0, acknowledged: false });
      return id;
    }
    function acknowledge(requestId, residentId) {
      const request = requests.get(requestId);
      if (!request || request.residentId !== residentId) return false;
      request.acknowledged = true;
      return true;
    }
    function accept(value, { residentId, messageId, dirty = false } = {}) {
      const request = requests.get(value?.request_id);
      if (!request || !request.acknowledged || request.residentId !== residentId || request.baseline.has(messageId)
        || value.protocol !== "portal-home-display/v1" || !Number.isSafeInteger(value.version) || value.version <= request.version) return null;
      const intent = normalizeIntent(value);
      request.version = value.version;
      return { intent, present: request.id === latest && request.epoch === epoch && !pinned && !dirty };
    }
    return {
      issue, acknowledge, accept, entries,
      add(entry) { entries.push(entry); return entry; },
      select(entry, manual = true) { if (manual) epoch++; active = entry; },
      manual() { epoch++; },
      pin(value) { pinned = !!value; epoch++; },
      get active() { return active; }, get pinned() { return pinned; },
      clear() { requests.clear(); entries.length = 0; active = null; },
    };
  }
  function migrateHomeState(state) {
    if (state.contentSchemaVersion >= 2) return state;
    const next = JSON.parse(JSON.stringify(state));
    for (const item of [next, ...Object.values(next.savedWorkspaces || {})]) {
      item.hidden = [...new Set([...(item.hidden || []), "dialogue-display"])];
    }
    return next;
  }
  window.PortalHomeDialogueState = Object.freeze({ create, normalizeIntent, migrateHomeState, results, displayCommand, messageOriginKey, normalizeOriginKey, normalizeMessageOrigin });
})();
