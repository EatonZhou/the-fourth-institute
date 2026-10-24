(function () {
  "use strict";

  if (window.top !== window || window.PortalConversationStore) return;

  const API_URL = "/api/agent-conversations";
  const SNAPSHOT_SCHEMA_VERSION = 7;
  const SNAPSHOT_STORAGE_PREFIX = window.PortalCompatibility.identifier("conversation_snapshot");
  const RECENT_STORAGE_PREFIX = window.PortalCompatibility.identifier("conversation_recent");
  const RECENT_LIMIT = 32;
  const PAGE_SIZE = 50;
  const EVENT_REFRESH_DELAY = 80;
  const TARGET_RETRY_LIMIT = 6;
  // These are background index reads. Six simultaneous reads can occupy an
  // HTTP/1 origin's entire browser connection pool and queue foreground actions
  // such as Stop before they reach the server. Leave room for control and SSE.
  const TARGET_REQUEST_LIMIT = 2;
  const NON_INDEX_RESIDENT_EVENT_TYPES = new Set([
    "resident.activity.changed",
    "resident.queue.changed",
    "resident.runtime_settings.changed",
  ]);
  const listeners = new Set();
  let residentStateCollection = null;
  let residentStateSignature = "";
  let residentStateFrame = 0;
  let residentStateError = "";
  let foregroundStateRefresh = null;
  const sharedFetcher = window.PortalRefreshCoordinator?.createSharedFetcher?.(
    window.PortalCompatibility.identifier("conversation_refresh_channel"),
  );
  const state = {
    entries: [],
    recent: [],
    contentSignature: "",
    loaded: false,
    ready: false,
    loading: false,
    refreshing: false,
    syncing: false,
    error: "",
    revision: 0,
    request: null,
    clearRequest: null,
    pageRequest: null,
    nextPageToken: "",
    loadedPageTokens: new Set(),
    paged: false,
    unverifiedResidentIds: new Set(),
    residentCursors: new Map(),
    residentEventCursors: new Map(),
    targetRequests: new Map(),
    targetWantedCursors: new Map(),
    targetReadReasons: new Map(),
    targetRetryTimers: new Map(),
    targetRetryAttempts: new Map(),
    residentInvalidations: new Map(),
    nextResidentInvalidation: 0,
    // Each conversation's read state at the highest server revision seen from
    // any source; a change in flight shows its intended state until it settles.
    readStates: new Map(),
    pendingReads: new Map(),
    nextPendingRead: 0,
    pinRequests: new Map(),
    actorRevision: 0,
    summaryEntries: new Map(),
    inboxOpen: false,
  };

  function username() {
    return String(window.PortalIdentity?.username?.() || "").trim();
  }

  function storageKey(prefix) {
    const actor = username();
    return actor ? `${prefix}${encodeURIComponent(actor)}` : "";
  }

  function cloneEntry(entry) {
    return entry && typeof entry === "object" ? { ...entry } : entry;
  }

  function publicSnapshot() {
    const frontier = pageFrontier();
    const entries = state.entries.filter(item => !hidden(item)
      && (!frontier || compareEntries(item, frontier) <= 0)).map(projectEntry);
    const allEntries = [...new Map([
      ...[...state.summaryEntries.values()].filter(item => !hidden(item)).map(item => [item.key, projectEntry(item)]),
      ...entries.map(item => [item.key, item]),
    ]).values()];
    return Object.freeze({
      entries,
      recent: state.recent.map(cloneEntry),
      unreadCount: allEntries.filter(item => item?.attention_unread ?? item?.unread).length,
      sections: Object.fromEntries(["conversation", "notification"].map(section => {
        const items = allEntries.filter(item => (item.section || "conversation") === section);
        return [section, {
          count: entries.filter(item => (item.section || "conversation") === section).length,
          unreadCount: items.filter(item => item.attention_unread ?? item.unread).length,
        }];
      })),
      nextPageToken: state.nextPageToken,
      loadingMore: Boolean(state.pageRequest),
      paged: state.paged,
      loaded: state.loaded,
      ready: state.ready,
      loading: state.loading,
      refreshing: state.refreshing || Boolean(foregroundStateRefresh),
      syncing: state.syncing,
      clearing: Boolean(state.clearRequest),
      error: state.error || residentStateError,
      revision: state.revision,
    });
  }

  function emit(reason = "change") {
    syncResidentStates();
    const snapshot = publicSnapshot();
    for (const listener of listeners) {
      try { listener(snapshot, reason); } catch (error) { console.error(window.PortalI18n.t("Conversation Store subscriber failed"), error); }
    }
  }

  // Every source of a conversation's read state (snapshot entries, the badge
  // summary, Resident viewer states, instruction results) carries its row's
  // revision; only a state at least as new as the held one is accepted.
  function acceptReadState(key, value) {
    const revision = Number(value?.read_revision);
    if (!key || !value || !Number.isFinite(revision)) return false;
    const current = state.readStates.get(key);
    if (current && current.revision > revision) return false;
    state.readStates.set(key, {
      revision,
      unread: Boolean(value.unread),
      pinned: typeof value.pinned === "boolean" ? value.pinned : Boolean(current?.pinned),
      dismissedThrough: typeof value.dismissed_through === "string"
        ? value.dismissed_through : String(current?.dismissedThrough || ""),
    });
    return true;
  }

  // "清除全部" hides a conversation until its activity passes the clear.
  function hidden(item) {
    const accepted = state.readStates.get(String(item?.key || ""));
    if (!accepted?.dismissedThrough || accepted.pinned) return false;
    return Date.parse(String(item?.updated_at || "")) <= Date.parse(accepted.dismissedThrough);
  }

  function acceptEntries(items) {
    for (const item of Array.isArray(items) ? items : []) acceptReadState(String(item?.key || ""), item);
  }

  function readState(item) {
    const key = String(item?.key || "");
    const accepted = state.readStates.get(key);
    const pending = state.pendingReads.get(key);
    return {
      unread: pending ? pending.unread : (accepted ? accepted.unread : Boolean(item?.unread)),
      pinned: accepted ? accepted.pinned : Boolean(item?.pinned),
      read_revision: accepted ? accepted.revision : Number(item?.read_revision || 0),
    };
  }

  function beginPendingRead(key, unread) {
    const pending = { id: ++state.nextPendingRead, unread: Boolean(unread) };
    state.pendingReads.set(key, pending);
    return pending;
  }

  function settlePendingRead(key, pending) {
    if (state.pendingReads.get(key) === pending) state.pendingReads.delete(key);
  }

  function projectEntry(item) {
    if (!item) return item;
    const key = String(item.key || "");
    if (item.resident_id) {
      const viewerState = window.PortalResidentProtocol?.stateSnapshot?.(item.resident_id);
      if (viewerState?.ready) acceptReadState(key, viewerState.viewer_state);
    }
    const read = readState(item);
    if (item.kind === "chatroom") {
      return { ...item, ...read, unread_count: read.unread ? Math.max(1, Number(item.unread_count || 0)) : 0,
        attention_unread: Boolean(read.unread && (item.attention_unread ?? true)),
        unread_mention_count: read.unread ? Number(item.unread_mention_count || 0) : 0,
        unread_mentions: read.unread ? (item.unread_mentions || []) : [] };
    }
    if (!item.resident_id) return { ...item, ...read };
    const snapshot = window.PortalResidentProtocol?.stateSnapshot?.(item.resident_id);
    const residentState = snapshot?.state || null;
    const terminalStatus = ["failed", "interrupted", "archived"].includes(item.status) ? item.status : "idle";
    return {
      ...item,
      ...read,
      status: residentState ? (residentState.status === "idle" && !residentState.family_activity ? terminalStatus : residentState.status) : "unknown",
      intake_state: residentState?.intake_state || "",
      resident_state: residentState,
      resident_state_cursor: snapshot?.cursor ?? null,
      resident_state_ready: Boolean(snapshot?.ready),
    };
  }

  function syncResidentStates() {
    const residentIds = [...new Set(state.entries.map(item => String(item?.resident_id || "")).filter(Boolean))].sort();
    const signature = JSON.stringify([username(), residentIds]);
    if (signature === residentStateSignature) return;
    residentStateSignature = signature;
    residentStateError = "";
    const previousCollection = residentStateCollection;
    residentStateCollection = null;
    if (!residentIds.length) {
      previousCollection?.destroy();
      return;
    }
    residentStateCollection = window.PortalResidentProtocol.acquireStates(residentIds, update => {
      if (update.type === "error") residentStateError = String(update.error?.message || update.error);
      else if (update.type === "snapshot") residentStateError = "";
      if (residentStateFrame) return;
      residentStateFrame = window.requestAnimationFrame(() => {
        residentStateFrame = 0;
        emit("resident-state");
      });
    }, { consumer: "inbox" });
    previousCollection?.destroy();
  }

  function subscribe(listener, options = {}) {
    if (typeof listener !== "function") return () => {};
    listeners.add(listener);
    if (options.immediate !== false) listener(publicSnapshot(), "subscribe");
    return () => listeners.delete(listener);
  }

  // Summary membership covers unloaded pages; loaded rows and the badge take
  // their read state from the same accepted revisions. A replacing summary
  // lists the unread conversations at its ``readRevision``: one changed after
  // that keeps its place until a newer summary.
  function updateSummary(items, { replace = false, readRevision = Infinity } = {}) {
    acceptEntries(items);
    const next = replace ? new Map() : new Map(state.summaryEntries);
    for (const item of items) {
      if (!item?.key || state.residentInvalidations.has(item.resident_id)) continue;
      next.set(item.key, item);
    }
    if (replace) for (const [key, item] of state.summaryEntries) {
      if (!next.has(key) && Number(state.readStates.get(key)?.revision || 0) > readRevision) next.set(key, item);
    }
    state.summaryEntries = next;
  }

  function removeSummary(key) {
    state.summaryEntries.delete(key);
  }

  function readStored(key, fallback) {
    if (!key) return fallback;
    return window.PortalState.storage.readJson(key, fallback);
  }

  function writeStored(key, value) {
    if (!key) return;
    window.PortalState.storage.writeJson(key, value);
  }

  // Pins first, then newest activity; equal times by key, as the server pages them.
  function compareEntries(left, right) {
    const leftPinned = readState(left).pinned;
    if (leftPinned !== readState(right).pinned) return leftPinned ? -1 : 1;
    const leftTime = Date.parse(String(left?.updated_at || ""));
    const rightTime = Date.parse(String(right?.updated_at || ""));
    const delta = (Number.isFinite(rightTime) ? rightTime : 0)
      - (Number.isFinite(leftTime) ? leftTime : 0);
    if (delta) return delta;
    const leftKey = String(left?.key || "");
    const rightKey = String(right?.key || "");
    return leftKey < rightKey ? -1 : (leftKey > rightKey ? 1 : 0);
  }

  function sortEntries(entries) {
    return entries.sort(compareEntries);
  }

  // Only Resident activity is paged; page one carries every notification,
  // mention and Chatroom. While older pages remain, the oldest Resident loaded
  // is how far the list reaches: an entry after it waits for its page, so
  // loading more never inserts conversations above entries already shown.
  const PAGED_KINDS = new Set(["resident-main", "resident"]);

  function pageFrontier() {
    if (!state.nextPageToken) return null;
    return state.entries.reduce((oldest, item) => (
      PAGED_KINDS.has(item?.kind) && !readState(item).pinned && (!oldest || compareEntries(item, oldest) > 0)
        ? item : oldest
    ), null);
  }

  function uniqueEntries(entries) {
    const unique = new Map();
    for (const entry of Array.isArray(entries) ? entries : []) {
      const key = String(entry?.key || "").trim();
      if (key) unique.set(key, entry);
    }
    return [...unique.values()];
  }

  function residentCursorMap(entries) {
    const cursors = new Map();
    for (const item of Array.isArray(entries) ? entries : []) {
      const residentId = String(item?.resident_id || "").trim();
      if (!residentId) continue;
      cursors.set(
        residentId,
        Math.max(
          Number(cursors.get(residentId) || 0),
          Math.max(0, Number(item?.cursor || 0)),
        ),
      );
    }
    return cursors;
  }

  function persistSnapshot() {
    writeStored(storageKey(SNAPSHOT_STORAGE_PREFIX), {
      schema_version: SNAPSHOT_SCHEMA_VERSION,
      entries: state.entries.slice(0, PAGE_SIZE),
      next_page_token: "",
      paged: state.paged,
      content_signature: state.contentSignature,
      revision: state.revision,
      saved_at: new Date().toISOString(),
    });
  }

  function restoreSnapshot() {
    const payload = readStored(storageKey(SNAPSHOT_STORAGE_PREFIX), null);
    if (Number(payload?.schema_version) !== SNAPSHOT_SCHEMA_VERSION || !Array.isArray(payload?.entries)) return false;
    state.entries = sortEntries(uniqueEntries(payload.entries.slice(0, PAGE_SIZE).map(entry => (
      ["running", "queued"].includes(String(entry?.status || "")) ? { ...entry, status: "idle" } : entry
    ))));
    acceptEntries(state.entries);
    state.contentSignature = "";
    state.revision = Math.max(0, Number(payload.revision || 0));
    state.nextPageToken = String(payload.next_page_token || "");
    state.paged = payload.paged === true;
    state.residentCursors = residentCursorMap(state.entries);
    state.unverifiedResidentIds = new Set(state.residentCursors.keys());
    state.residentEventCursors = new Map(state.residentCursors);
    state.loaded = true;
    state.ready = true;
    return true;
  }

  function recentKey(entry) {
    return String(entry?.key || (entry?.resident_id ? `resident:${entry.resident_id}` : "")).trim();
  }

  function recentResidentId(entry) {
    return String(entry?.resident_id || "").trim();
  }

  function restoreRecent() {
    const value = readStored(storageKey(RECENT_STORAGE_PREFIX), []);
    const unique = new Map();
    for (const raw of Array.isArray(value) ? value : []) {
      const key = recentKey(raw);
      if (!raw.resident_id || !key || unique.has(key)) continue;
      unique.set(key, { ...raw, key, t: Number(raw?.t || 0) || Date.now() });
    }
    state.recent = [...unique.values()].sort((a, b) => b.t - a.t).slice(0, RECENT_LIMIT);
    persistRecent();
  }

  function persistRecent() {
    writeStored(storageKey(RECENT_STORAGE_PREFIX), state.recent);
  }

  function remember(entry) {
    const key = recentKey(entry);
    if (!key || !entry?.resident_id) return false;
    const normalized = { ...(entry || {}), key, t: Date.now() };
    state.recent = [normalized, ...state.recent.filter(item => recentKey(item) !== key)].slice(0, RECENT_LIMIT);
    persistRecent();
    emit("recent");
    return true;
  }

  function dismissRecent(key) {
    const exact = String(key || "").trim();
    const next = state.recent.filter(item => recentKey(item) !== exact);
    if (next.length === state.recent.length) return false;
    state.recent = next;
    persistRecent();
    emit("recent");
    return true;
  }

  function clearRecent() {
    if (!state.recent.length) return [];
    const removed = state.recent.map(cloneEntry);
    state.recent = [];
    persistRecent();
    emit("recent");
    return removed;
  }

  function restoreRecentEntries(entries) {
    const restored = (Array.isArray(entries) ? entries : [])
      .map(entry => ({ ...cloneEntry(entry), key: recentKey(entry) }))
      .filter(entry => entry.key);
    if (!restored.length) return false;
    const currentKeys = new Set(state.recent.map(recentKey));
    const additions = restored.filter(entry => !currentKeys.has(entry.key));
    if (!additions.length) return false;
    state.recent = [...state.recent, ...additions]
      .sort((left, right) => Number(right?.t || 0) - Number(left?.t || 0))
      .slice(0, RECENT_LIMIT);
    persistRecent();
    emit("recent");
    return true;
  }

  function entry(key) {
    const exact = String(key || "");
    return state.entries.find(item => String(item?.key || "") === exact) || null;
  }

  function discard(key) {
    const exact = String(key || "").trim();
    if (!exact || (!entry(exact) && !state.summaryEntries.has(exact))) return false;
    removeSummary(exact);
    state.entries = state.entries.filter(item => String(item?.key || "") !== exact);
    state.contentSignature = "";
    persistSnapshot();
    emit("discard");
    return true;
  }

  function discardResident(residentId, reason = "resident-tombstone") {
    const exact = String(residentId || "").trim();
    if (!exact) return false;
    window.PortalResidentProjection?.invalidate?.(exact);
    for (const item of state.summaryEntries.values()) {
      if (item.resident_id === exact) removeSummary(item.key);
    }
    const next = state.entries.filter(item => String(item?.resident_id || "") !== exact);
    const nextRecent = state.recent.filter(item => recentResidentId(item) !== exact);
    const entriesChanged = next.length !== state.entries.length;
    const recentChanged = nextRecent.length !== state.recent.length;
    state.entries = next;
    state.recent = nextRecent;
    state.residentInvalidations.set(exact, ++state.nextResidentInvalidation);
    state.residentCursors.delete(exact);
    state.unverifiedResidentIds.delete(exact);
    state.residentEventCursors.delete(exact);
    state.targetWantedCursors.delete(exact);
    state.targetReadReasons.delete(exact);
    state.targetRetryAttempts.delete(exact);
    const timer = state.targetRetryTimers.get(exact);
    if (timer) window.clearTimeout(timer);
    state.targetRetryTimers.delete(exact);
    state.contentSignature = "";
    if (entriesChanged) persistSnapshot();
    if (recentChanged) persistRecent();
    if (entriesChanged || recentChanged) {
      emit(reason);
    }
    return entriesChanged || recentChanged;
  }

  function applyPayload(payload, options = {}) {
    const paged = payload?.paged === true;
    const pageEntries = Array.isArray(payload?.entries) ? payload.entries : [];
    acceptEntries(pageEntries);
    const candidates = uniqueEntries(
      paged && options.authoritativeRemoval !== true
        ? [...state.entries, ...pageEntries]
        : pageEntries,
    );
    const invalidatedResidentIds = new Set(candidates
      .map(item => String(item?.resident_id || ""))
      .filter(residentId => residentId && state.residentInvalidations.has(residentId)));
    const projected = sortEntries(candidates.filter(
      item => !invalidatedResidentIds.has(String(item?.resident_id || "")),
    ));
    const projectedCursors = residentCursorMap(projected);
    const projectedTimes = new Map(projected.map(item => [String(item?.resident_id || ""), Date.parse(item.updated_at)]));
    const currentTimes = new Map(state.entries.map(item => [String(item?.resident_id || ""), Date.parse(item.updated_at)]));
    const protectedResidentIds = options.authoritativeRemoval === true
      ? new Set()
      : new Set([...state.residentCursors]
        .filter(([residentId, cursor]) => cursor > Number(projectedCursors.get(residentId) || 0)
          || (cursor === Number(projectedCursors.get(residentId) || 0)
            && currentTimes.get(residentId) > projectedTimes.get(residentId)))
        .map(([residentId]) => residentId));
    for (const item of pageEntries) state.unverifiedResidentIds.delete(String(item?.resident_id || ""));
    if (options.authoritativeRemoval === true) state.unverifiedResidentIds.clear();
    else for (const residentId of state.unverifiedResidentIds) protectedResidentIds.add(residentId);
    const incoming = protectedResidentIds.size
      ? sortEntries([
        ...projected.filter(item => !protectedResidentIds.has(String(item?.resident_id || ""))),
        ...state.entries.filter(item => protectedResidentIds.has(String(item?.resident_id || ""))),
      ])
      : projected;
    for (const residentId of protectedResidentIds) {
      const cursor = Number(state.residentCursors.get(residentId) || 0);
      state.targetWantedCursors.set(
        residentId,
        Math.max(cursor, Number(state.targetWantedCursors.get(residentId) || 0)),
      );
    }
    for (const residentId of invalidatedResidentIds) {
      const cursor = candidates
        .filter(item => String(item?.resident_id || "") === residentId)
        .reduce((latest, item) => Math.max(latest, Number(item?.cursor || 0)), 0);
      state.targetWantedCursors.set(
        residentId,
        Math.max(cursor, Number(state.targetWantedCursors.get(residentId) || 0)),
      );
    }
    const signature = String(payload?.content_signature || "");
    const changed = options.force === true || !state.loaded || signature !== state.contentSignature
      || Number(payload?.revision || 0) !== state.revision || String(payload?.error || "") !== state.error;
    state.entries = incoming;
    const nextPageToken = String(payload?.next_page_token || "");
    // A head refresh can finish after an older page. Keep the pagination
    // frontier when that head points to a page already consumed.
    if (options.advancePage || !state.loadedPageTokens.has(nextPageToken)) {
      state.nextPageToken = nextPageToken;
    }
    state.paged = paged;
    state.contentSignature = protectedResidentIds.size ? "" : signature;
    state.revision = Math.max(0, Number(payload?.revision || 0));
    state.error = String(payload?.error || "");
    state.loaded = true;
    state.ready = true;
    state.residentCursors = residentCursorMap(incoming);
    for (const [residentId, cursor] of state.residentCursors) {
      state.residentEventCursors.set(
        residentId,
        Math.max(cursor, Number(state.residentEventCursors.get(residentId) || 0)),
      );
    }
    updateSummary(payload?.mailbox_summary?.entries || incoming, {
      replace: Boolean(payload?.mailbox_summary) || !paged,
      readRevision: Number(payload?.read_revision ?? Infinity),
    });
    updateSummary(incoming);
    if (!state.error) persistSnapshot();
    if (changed) emit("index");
    return payload;
  }

  const readClient = `mailbox-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let readSequence = 0;
  function noteTargetRead(residentId, reason) {
    const reasons = state.targetReadReasons.get(residentId) || new Set();
    reasons.add(reason);
    state.targetReadReasons.set(residentId, reasons);
  }

  // Content-free provenance of an actual HTTP attempt. Cache joins emit no
  // receipt; errors keep the same identity and still propagate to their owner.
  async function readIndexResource(url, reader, reasons) {
    const transportId = `${readClient}-${++readSequence}`;
    const record = (outcome, payload, error) => {
      try {
        window.dispatchEvent(new CustomEvent("portal:conversation-read", { detail: {
          transport_id: transportId, reader, reasons,
          outcome, payload_error: Boolean(payload?.error),
          error_status: Number.isInteger(error?.status) ? error.status : null,
          at: new Date().toISOString(),
        } }));
      } catch (_) { /* Observation must never change a business result. */ }
    };
    try {
      const payload = await window.PortalAPI.get(url, { headers: { "X-Portal-Request-ID": transportId } });
      record("returned", payload, null);
      return payload;
    } catch (error) {
      record("failed", null, error);
      throw error;
    }
  }

  function fetchIndex() {
    const fetch = () => readIndexResource(API_URL, "index", ["index_refresh"]);
    if (!sharedFetcher) return fetch();
    return sharedFetcher.fetch("agent-conversations:snapshot", fetch, {
      maxAgeMs: 0,
      dedupeWindowMs: 500,
      freshAfter: Date.now(),
    });
  }

  function readIndex(options, context) {
    const actorRevision = state.actorRevision;
    state.loading = !state.loaded;
    state.refreshing = state.loaded && options.foreground === true;
    state.syncing = state.loaded && !state.refreshing;
    emit(state.loading ? "loading" : (state.refreshing ? "refreshing" : "syncing"));
    const request = fetchIndex()
      .then(payload => {
        if (actorRevision !== state.actorRevision || !context.isCurrent()) return null;
        return {payload, options, actorRevision};
      })
      .finally(() => {
        if (state.request !== request) return;
        state.loading = false;
        state.refreshing = false;
        state.syncing = false;
        state.request = null;
        emit("settled");
        drainResidentRefreshes();
      });
    state.request = request;
    return request;
  }

  let foregroundRefresh = false;
  let indexRetryAttempts = 0;
  const indexRefresh = window.PortalRefreshCoordinator.create({
    key: () => `${username()}:${state.actorRevision}`,
    interval: false,
    initialDelay: 250,
    repairInterval: 300_000,
    nextDelay: (_result, error) => {
      if (!error) {
        indexRetryAttempts = 0;
        return null;
      }
      if (!window.PortalResidentProtocol.isTransientSyncError(error) || indexRetryAttempts >= 3) return null;
      return 1000 * (2 ** indexRetryAttempts++);
    },
    fetch: async context => {
      const foreground = foregroundRefresh;
      foregroundRefresh = false;
      // First verification announces an identity change, even for the same
      // cached username. Do not start a costly snapshot that reset will discard.
      await window.PortalIdentity?.load?.();
      if (!context.isCurrent()) return null;
      return readIndex({ force: context.force, foreground }, context);
    },
    onError: error => {
      if (error?.code === "portal_event_stream_unavailable") {
        // A disconnected event stream does not mean the snapshot read failed.
        // Verify it once; its HTTP result owns the Mailbox refresh error.
        indexRefresh.invalidate({ delay: EVENT_REFRESH_DELAY });
        return;
      }
      state.error = String(error?.message || error || "读取失败");
      emit("error");
    },
    apply: result => {
      if (result.actorRevision !== state.actorRevision) return;
      applyPayload(result.payload, result.options);
      // Applying the snapshot can add targets after the fetch has settled.
      drainResidentRefreshes();
    },
    subscribe: window.PortalRefreshCoordinator.portalSubscription({
      debounceMs: EVENT_REFRESH_DELAY,
      matches: detail => {
        if (detail?.snapshot_required) return true;
        if (!detail?.projection_changes?.["agent-conversation-index"]) return false;
        const keys = detail?.projection_details?.["agent-conversation-index"]?.current?.keys;
        return !Array.isArray(keys) || !keys.length || keys.some(key => !/^resident:resident_[0-9a-f]{12}$/.test(key));
      },
    }),
  });

  function setInboxOpen(open) {
    const next = Boolean(open);
    if (state.inboxOpen === next) return;
    state.inboxOpen = next;
    if (next) refresh({ force: true, foreground: true }).catch(() => {});
  }

  function refresh(options = {}) {
    foregroundRefresh ||= options.foreground === true;
    if (state.loaded && options.foreground === true && indexRefresh.isRefreshing()) {
      state.refreshing = true;
      state.syncing = false;
      emit("refreshing");
    }
    indexRefresh.start();
    const indexRead = indexRefresh.refresh({ force: options.force === true }).then(result => result?.payload ?? null);
    if (options.foreground !== true) return indexRead;
    if (foregroundStateRefresh) return Promise.all([indexRead, foregroundStateRefresh]).then(([payload]) => payload);
    const actorRevision = state.actorRevision;
    const hadStates = Boolean(residentStateCollection);
    // Reopening or refreshing Mailbox must calibrate both authorities. Merely
    // rereading the index leaves an unchanged membership's cached states intact.
    const request = indexRead.then(async payload => {
      if (actorRevision !== state.actorRevision) return null;
      const collection = residentStateCollection;
      if (collection) await collection.load({ force: hadStates });
      return payload;
    }).finally(() => {
      if (foregroundStateRefresh !== request) return;
      foregroundStateRefresh = null;
      emit("settled");
    });
    foregroundStateRefresh = request;
    emit("refreshing");
    return request;
  }

  function refreshVisible(options = {}) {
    return refresh(options);
  }

  async function readChatrooms(chatroomId = "") {
    const actorRevision = state.actorRevision;
    const payload = await window.PortalInstructionClient.execute(
      chatroomId ? "GetChatroom" : "ListChatrooms",
      chatroomId ? { chatroom_id: chatroomId } : { user_id: await window.PortalIdentity.userId() },
    );
    if (actorRevision !== state.actorRevision) return payload;
    const rooms = chatroomId ? [payload.chatroom] : payload.chatrooms;
    for (const room of rooms || []) {
      if (!room?.chatroom_id) continue;
      const key = `chatroom:${room.chatroom_id}`;
      const current = entry(key);
      const unread = Number(room.unread_count || 0) > 0;
      const mentions = room.unread_mentions || [];
      const incoming = { ...current, key, kind: "chatroom", chatroom_id: room.chatroom_id,
        title: room.title, updated_at: room.updated_at, muted: Boolean(room.muted),
        unread, unread_count: Number(room.unread_count || 0), unread_mentions: mentions,
        unread_mention_count: Number(room.unread_mention_count ?? mentions.length),
        attention_unread: unread && (!room.muted || mentions.length > 0),
        read_revision: Number(room.read_revision || 0),
      };
      if (current) Object.assign(current, incoming);
      else state.entries.push(incoming);
    }
    updateSummary(state.entries.filter(item => item.kind === "chatroom"));
    emit("chatroom");
    return payload;
  }

  function loadMore() {
    if (state.pageRequest) return state.pageRequest;
    const pageToken = state.nextPageToken;
    if (!pageToken) return Promise.resolve(null);
    const actorRevision = state.actorRevision;
    const request = readIndexResource(`${API_URL}?page_token=${encodeURIComponent(pageToken)}`, "page", ["pagination"])
      .then(payload => {
        if (actorRevision !== state.actorRevision || pageToken !== state.nextPageToken) return null;
        state.loadedPageTokens.add(pageToken);
        return applyPayload(payload, { force: true, advancePage: true });
      })
      .catch(error => {
        if (actorRevision === state.actorRevision) {
          state.error = String(error?.message || error);
          emit("page-error");
        }
        throw error;
      })
      .finally(() => {
        if (state.pageRequest !== request) return;
        state.pageRequest = null;
        emit("page-settled");
      });
    state.pageRequest = request;
    emit("page-loading");
    return request;
  }

  function applyResidentPayload(
    residentId,
    payload,
    requiredCursor = 0,
    requestInvalidation = 0,
  ) {
    const incoming = uniqueEntries(Array.isArray(payload?.entries) ? payload.entries : []);
    acceptEntries(incoming);
    const targetEntries = incoming.filter(
      item => String(item?.resident_id || "") === residentId,
    );
    const currentCursor = Number(state.residentCursors.get(residentId) || 0);
    const currentEventCursor = Number(state.residentEventCursors.get(residentId) || 0);
    const residentCursor = Math.max(0, Number(payload?.resident_cursor || 0));
    const projectionCursor = Math.max(0, Number(payload.projection_cursor));
    const targetState = String(payload.target_state).trim().toLowerCase();
    state.unverifiedResidentIds.delete(residentId);
    const targetPresent = Boolean(payload.target_present);
    if (["deleted", "forbidden"].includes(targetState)) {
      discardResident(residentId);
      return true;
    }
    if (Number(state.residentInvalidations.get(residentId) || 0) > requestInvalidation) {
      return true;
    }
    if (
      residentCursor < Number(requiredCursor || 0)
      || residentCursor < currentEventCursor
    ) return false;
    if (
      targetPresent
      && (
        !targetEntries.length
        || projectionCursor < Number(requiredCursor || 0)
        || projectionCursor < currentCursor
        || projectionCursor < residentCursor
      )
    ) return false;
    // Targeted reads are upserts and cursor acknowledgements. Actor activity
    // can lag the canonical Resident event stream, so an empty response is not
    // evidence that an indexed conversation was deleted. Only a full index
    // snapshot may remove an entry by omission.
    if (targetPresent) {
      state.residentInvalidations.delete(residentId);
      const incomingKeys = new Set(incoming.map(item => String(item?.key || "")));
      const incomingResidentIds = new Set(incoming
        .map(item => String(item?.resident_id || ""))
        .filter(Boolean));
      state.entries = sortEntries(uniqueEntries([
        ...state.entries.filter(item => (
          !incomingKeys.has(String(item?.key || ""))
          && !incomingResidentIds.has(String(item?.resident_id || ""))
        )),
        ...incoming,
      ]));
      for (const item of incoming) {
        const itemResidentId = String(item?.resident_id || "");
        if (!itemResidentId) continue;
        const itemCursor = Math.max(0, Number(item?.cursor || 0));
        state.residentCursors.set(itemResidentId, itemCursor);
        state.residentEventCursors.set(
          itemResidentId,
          Math.max(itemCursor, Number(state.residentEventCursors.get(itemResidentId) || 0)),
        );
      }
      updateSummary(incoming);
      state.contentSignature = "";
      state.loaded = true;
      state.ready = true;
      persistSnapshot();
    }
    state.residentEventCursors.set(
      residentId,
      Math.max(residentCursor, projectionCursor, currentEventCursor),
    );
    state.targetRetryAttempts.delete(residentId);
    if (targetPresent) emit("resident");
    return true;
  }

  function scheduleResidentRefresh(residentId, wantedCursor) {
    const exact = String(residentId || "").trim();
    if (!exact || document.hidden || state.targetRetryTimers.has(exact)) return;
    const attempt = Number(state.targetRetryAttempts.get(exact) || 0);
    if (attempt >= TARGET_RETRY_LIMIT) {
      state.targetRetryAttempts.delete(exact);
      state.targetWantedCursors.delete(exact);
      state.targetReadReasons.delete(exact);
      refresh({ force: true }).catch(() => {});
      return;
    }
    const delay = Math.min(1_000, EVENT_REFRESH_DELAY * (2 ** Math.min(attempt, 4)));
    state.targetRetryAttempts.set(exact, attempt + 1);
    const timer = window.setTimeout(() => {
      state.targetRetryTimers.delete(exact);
      refreshResident(exact, wantedCursor).catch(() => {});
    }, delay);
    state.targetRetryTimers.set(exact, timer);
  }

  function drainResidentRefreshes() {
    if (document.hidden) return;
    let available = Math.max(0, TARGET_REQUEST_LIMIT - state.targetRequests.size);
    if (!available) return;
    for (const [residentId, cursor] of state.targetWantedCursors) {
      if (!available) break;
      if (
        state.targetRequests.has(residentId)
        || state.targetRetryTimers.has(residentId)
      ) continue;
      available -= 1;
      refreshResident(residentId, cursor).catch(() => {});
    }
  }

  function refreshResident(residentId, wantedCursor = 0) {
    const exact = String(residentId || "").trim();
    if (!exact) return Promise.resolve(null);
    state.targetWantedCursors.set(exact, Math.max(Number(state.targetWantedCursors.get(exact) || 0), Number(wantedCursor || 0)));
    if (state.targetRequests.has(exact)) return state.targetRequests.get(exact);
    if (state.targetRetryTimers.has(exact)) return Promise.resolve(null);
    if (state.targetRequests.size >= TARGET_REQUEST_LIMIT) {
      return Promise.resolve(null);
    }
    const actorRevision = state.actorRevision;
    const requiredCursor = Number(state.targetWantedCursors.get(exact) || 0);
    const requestInvalidation = Number(state.residentInvalidations.get(exact) || 0);
    const reasons = [...(state.targetReadReasons.get(exact) || ["cursor_reconciliation"])];
    state.targetReadReasons.delete(exact);
    let applied = false;
    const promise = readIndexResource(`${API_URL}?resident=${encodeURIComponent(exact)}`, "resident", reasons)
      .then(payload => {
        if (actorRevision !== state.actorRevision) {
          applied = true;
          return null;
        }
        applied = applyResidentPayload(
          exact,
          payload,
          requiredCursor,
          requestInvalidation,
        );
        return payload;
      })
      .finally(() => {
        if (state.targetRequests.get(exact) !== promise) return;
        state.targetRequests.delete(exact);
        if (actorRevision !== state.actorRevision) {
          drainResidentRefreshes();
          return;
        }
        const wanted = Number(state.targetWantedCursors.get(exact) || 0);
        const current = Number(state.residentEventCursors.get(exact) || 0);
        if (!applied || wanted > current) {
          noteTargetRead(exact, "cursor_reconciliation");
          scheduleResidentRefresh(exact, wanted);
        } else {
          state.targetWantedCursors.delete(exact);
          state.targetReadReasons.delete(exact);
        }
        drainResidentRefreshes();
      });
    state.targetRequests.set(exact, promise);
    return promise;
  }

  function handleResidentEvents(events) {
    for (const event of Array.isArray(events) ? events : []) {
      if (NON_INDEX_RESIDENT_EVENT_TYPES.has(String(event?.event_type || ""))) continue;
      const residentId = String(event?.resident_id || "").trim();
      const cursor = Math.max(0, Number(event?.cursor || 0));
      if (!residentId || !cursor || cursor <= Number(state.residentEventCursors.get(residentId) || 0)) continue;
      noteTargetRead(residentId, "resident_event");
      state.targetWantedCursors.set(residentId, Math.max(cursor, Number(state.targetWantedCursors.get(residentId) || 0)));
      if (!state.request && !document.hidden) scheduleResidentRefresh(residentId, cursor);
    }
  }

  // A Family facade's requests are answered in its members' Conversations; the
  // facade's own entry learns of that through its projection changes.
  function handleResidentProjectionChanges(changes) {
    if (!changes || typeof changes !== "object") return;
    for (const [residentId, scopes] of Object.entries(changes)) {
      if (!Array.isArray(scopes) || !scopes.includes("projection")) continue;
      if (!state.entries.some(entry => String(entry?.resident_id || "") === residentId)) continue;
      noteTargetRead(residentId, "resident_projection");
      state.targetWantedCursors.set(residentId, Number(state.targetWantedCursors.get(residentId) || 0));
      if (!state.request && !document.hidden) scheduleResidentRefresh(residentId, 0);
    }
  }

  function readChanged(reason = "read-state") {
    state.contentSignature = "";
    persistSnapshot();
    emit(reason);
  }

  // An accepted result settles the change; a failed one leaves the state the
  // store held before it.
  async function changeReadState(key, unread, request) {
    const pending = beginPendingRead(key, unread);
    const actorRevision = state.actorRevision;
    readChanged();
    try {
      const payload = await request();
      if (actorRevision !== state.actorRevision) return null;
      acceptReadState(key, payload?.item || payload);
      return payload;
    } finally {
      if (actorRevision === state.actorRevision) {
        settlePendingRead(key, pending);
        readChanged();
      }
    }
  }

  async function setReadState(keyOrEntry, unread) {
    const item = typeof keyOrEntry === "string" ? entry(keyOrEntry) : keyOrEntry;
    if (!item?.key) return null;
    const key = String(item.key);
    // The revision shown: a newer alert since then refuses the change.
    const seen = readState(item).read_revision;
    try {
      const payload = await changeReadState(key, unread, () => window.PortalInstructionClient.execute(
        "MarkMailboxItemRead", { key, unread: Boolean(unread) }, {
          keepalive: true,
          idempotencyKey: `inbox-read:${globalThis.PortalUIPrimitives.randomUUID()}`,
          expectedVersion: { [key]: String(seen) },
        },
      ));
      if (item.resident_id) window.PortalResidentProtocol?.invalidateViewerState?.(item.resident_id);
      if (item.kind === "chatroom") refresh({ force: true }).catch(() => {});
      return payload;
    } catch (error) {
      if (error?.code !== "instruction.version_conflict") throw error;
      refresh({ force: true }).catch(() => {});
      return null;
    }
  }

  function markRead(key) {
    const item = entry(key);
    if (!projectEntry(item)?.unread) return Promise.resolve(null);
    return setReadState(item, false);
  }

  async function observeChatroomRead(chatroomId, through = null) {
    const exact = String(chatroomId || "").trim();
    if (!/^chatroom_[0-9a-f]{12}$/.test(exact)) return null;
    const key = `chatroom:${exact}`;
    const actorRevision = state.actorRevision;
    const payload = await changeReadState(key, false, async () => {
      const room = await window.PortalInstructionClient.execute("MarkChatroomRead", {
        chatroom_id: exact,
        ...(through === null ? {} : { through }),
      }, {
        keepalive: true,
        idempotencyKey: `chatroom-read:${exact}:${globalThis.PortalUIPrimitives.randomUUID()}`,
      });
      const current = entry(key);
      if (current && actorRevision === state.actorRevision) {
        Object.assign(current, room);
        current.unread_count = Math.max(0, Number(room?.unread_count || 0));
      }
      return { ...room, read_revision: Number(room?.read_revision || 0), unread: Number(room?.unread_count || 0) > 0 };
    });
    if (payload) refresh({ force: true }).catch(() => {});
    return payload;
  }

  // The presented Chat is the observation. The server selects the current
  // completion; a stale or missing local inbox entry cannot prevent the read.
  async function observeResidentRead({ residentId, actor } = {}) {
    if (!actor || actor !== username()) return Promise.resolve(null);
    const exact = String(residentId || "").trim();
    if (!/^resident_[0-9a-f]{12}$/.test(exact)) return null;
    const key = `resident:${exact}`;
    const actorRevision = state.actorRevision;
    const payload = await changeReadState(key, false, () => window.PortalInstructionClient.execute(
      "MarkMailboxItemRead", { key }, {
        keepalive: true,
        idempotencyKey: `resident-chat-read:${globalThis.PortalUIPrimitives.randomUUID()}`,
      },
    ));
    if (actorRevision !== state.actorRevision) return null;
    // Reading one Chat changes its viewer state, not Mailbox membership.
    // Keep an in-flight full index so other offline conversations still load.
    window.PortalResidentProtocol?.invalidateViewerState?.(exact);
    // A targeted read bypasses the shared snapshot cache. Let any earlier
    // in-flight target read settle before fetching the confirmed state.
    await state.targetRequests.get(exact)?.catch(() => {});
    noteTargetRead(exact, "chat_read");
    await refreshResident(exact).catch(() => {});
    return payload;
  }

  async function loadPin(residentId) {
    const exact = String(residentId || "").trim();
    if (!/^resident_[0-9a-f]{12}$/.test(exact)) throw new TypeError("Invalid Resident ID");
    await window.PortalIdentity?.load?.();
    const actorRevision = state.actorRevision;
    // A node Menu only needs this conversation, not every Mailbox page and badge.
    const payload = await readIndexResource(`${API_URL}?resident=${encodeURIComponent(exact)}`, "pin", ["pin_menu"]);
    if (actorRevision !== state.actorRevision) return null;
    if (payload?.error) throw new Error(payload.error);
    if (payload?.target_state !== "present") throw new Error(window.PortalI18n.t("没有访问权限"));
    const key = `resident:${exact}`;
    const incoming = (payload.entries || []).find(item => item.key === key);
    // Preserve live unread/runtime facts already held by this shared store.
    if (incoming && !entry(key)) state.entries.push(incoming);
    if (incoming) updateSummary([incoming]);
    sortEntries(state.entries);
    readChanged("pin");
    return {pinned: readState(entry(key) || incoming || { key }).pinned};
  }

  function setPinned(key, pinned) {
    if (state.pinRequests.has(key)) return state.pinRequests.get(key);
    const actorRevision = state.actorRevision;
    const request = window.PortalInstructionClient.execute("PinMailboxItem", { key, pinned }, {
      idempotencyKey: `inbox-pin:${globalThis.PortalUIPrimitives.randomUUID()}`,
    }).then(payload => {
      if (actorRevision !== state.actorRevision) return null;
      const incoming = payload.item;
      const item = entry(key);
      if (incoming?.key === key) {
        if (item) Object.assign(item, incoming);
        else state.entries.push(incoming);
        updateSummary([incoming]);
      } else {
        // An unpinned conversation with no activity no longer belongs in Mailbox.
        state.entries = state.entries.filter(item => item.key !== key);
        removeSummary(key);
      }
      sortEntries(state.entries);
      state.contentSignature = "";
      persistSnapshot();
      emit("pin");
      return payload;
    }).finally(() => {
      if (state.pinRequests.get(key) === request) state.pinRequests.delete(key);
    });
    state.pinRequests.set(key, request);
    return request;
  }

  function clear(mode, section) {
    if (state.clearRequest || !["unread", "all"].includes(mode)) return state.clearRequest;
    const actorRevision = state.actorRevision;
    const known = [...new Map([...state.summaryEntries.values(), ...state.entries]
      .map(item => [String(item?.key || ""), item])).values()]
      .filter(item => item && (!section || (item.section || "conversation") === section));
    // The server clears what it lists at the request; the rows it changed come back.
    const pendings = mode === "unread"
      ? known.filter(item => readState(item).unread).map(item => [String(item.key), beginPendingRead(String(item.key), false)])
      : [];
    if (pendings.length) readChanged("clear-optimistic");
    const request = window.PortalInstructionClient.execute("ClearMailbox", { user_id: username(), mode, ...(section ? {section} : {}) }, {
      idempotencyKey: `inbox-clear:${globalThis.PortalUIPrimitives.randomUUID()}`,
    })
      .then(payload => {
        if (actorRevision !== state.actorRevision) return null;
        state.error = "";
        state.targetWantedCursors.clear();
        state.targetRetryAttempts.clear();
        for (const timer of state.targetRetryTimers.values()) window.clearTimeout(timer);
        state.targetRetryTimers.clear();
        acceptEntries(payload?.items);
        // A Chatroom keeps its own read positions, without a row revision.
        for (const [key] of pendings) {
          if (key.startsWith("chatroom:")) acceptReadState(key, { read_revision: 0, unread: false });
        }
        for (const item of known) {
          if (item.resident_id) window.PortalResidentProtocol?.invalidateViewerState?.(item.resident_id);
        }
        return payload;
      })
      .catch(error => {
        if (actorRevision === state.actorRevision) state.error = String(error?.message || error || "清除失败");
        throw error;
      })
      .finally(() => {
        if (actorRevision === state.actorRevision) {
          for (const [key, pending] of pendings) settlePendingRead(key, pending);
          readChanged(state.error ? "clear-error" : "clear");
        }
        if (state.clearRequest !== request) return;
        state.clearRequest = null;
        emit("settled");
      });
    state.clearRequest = request;
    emit("clearing");
    return request;
  }

  function resetActor() {
    indexRefresh.stop();
    indexRetryAttempts = 0;
    residentStateCollection?.destroy();
    residentStateCollection = null;
    residentStateSignature = "";
    residentStateError = "";
    foregroundStateRefresh = null;
    state.actorRevision += 1;
    state.summaryEntries.clear();
    state.readStates.clear();
    state.pendingReads.clear();
    state.unverifiedResidentIds.clear();
    state.pageRequest = null;
    state.nextPageToken = "";
    state.loadedPageTokens.clear();
    state.paged = false;
    state.entries = [];
    state.recent = [];
    state.contentSignature = "";
    state.loaded = false;
    state.ready = false;
    state.loading = false;
    state.refreshing = false;
    state.syncing = false;
    state.error = "";
    state.request = null;
    state.clearRequest = null;
    state.residentCursors.clear();
    state.residentEventCursors.clear();
    state.targetRequests.clear();
    state.targetWantedCursors.clear();
    state.targetReadReasons.clear();
    for (const timer of state.targetRetryTimers.values()) window.clearTimeout(timer);
    state.targetRetryTimers.clear();
    state.targetRetryAttempts.clear();
    state.residentInvalidations.clear();
    state.pinRequests.clear();
    restoreSnapshot();
    restoreRecent();
    emit("identity");
    refresh({ force: true }).catch(() => {});
  }

  restoreSnapshot();
  restoreRecent();
  window.addEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", resetActor);
  window.addEventListener("portal:change", event => {
    handleResidentEvents(event?.detail?.resident_events);
    handleResidentProjectionChanges(event?.detail?.resident_changes);
    const detail = event?.detail || {};
    const keys = detail.projection_details?.["agent-conversation-index"]?.current?.keys || [];
    const residents = new Set([
      ...keys.filter(key => /^resident:resident_[0-9a-f]{12}$/.test(key)).map(key => key.slice(9)),
      ...Object.keys(detail.projection_changes?.["resident-view-state"] || {}).filter(key => /^resident_[0-9a-f]{12}$/.test(key)),
    ]);
    for (const residentId of residents) {
      if (keys.includes(`resident:${residentId}`)) noteTargetRead(residentId, "index_projection");
      if (Object.hasOwn(detail.projection_changes?.["resident-view-state"] || {}, residentId)) {
        noteTargetRead(residentId, "viewer_state");
      }
      state.targetWantedCursors.set(residentId, Number(state.targetWantedCursors.get(residentId) || 0));
      if (!state.request && !document.hidden) scheduleResidentRefresh(residentId, 0);
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    indexRefresh.invalidate({ delay: EVENT_REFRESH_DELAY });
    for (const [residentId, cursor] of state.targetWantedCursors) {
      scheduleResidentRefresh(residentId, cursor);
    }
    drainResidentRefreshes();
  });
  window.addEventListener("focus", () => {
    indexRefresh.invalidate({ delay: EVENT_REFRESH_DELAY });
  });
  window.addEventListener("pagehide", () => {
    indexRefresh.stop();
    residentStateCollection?.destroy();
    residentStateCollection = null;
    residentStateSignature = "";
  });
  window.addEventListener("pageshow", () => {
    indexRefresh.start();
    indexRefresh.invalidate({ delay: EVENT_REFRESH_DELAY });
  });

  window.PortalConversationStore = Object.freeze({
    snapshot: publicSnapshot,
    subscribe,
    refresh,
    refreshVisible,
    readChatrooms,
    setInboxOpen,
    loadMore,
    entry: key => projectEntry(entry(key)),
    entries: () => state.entries.map(projectEntry),
    discard,
    discardResident,
    isUnread: key => Boolean(projectEntry(entry(key))?.unread),
    setReadState,
    loadPin,
    setPinned,
    markRead,
    observeChatroomRead,
    observeResidentRead,
    clear,
    remember,
    dismissRecent,
    clearRecent,
    restoreRecent: restoreRecentEntries,
    recent: () => state.recent.map(cloneEntry),
  });

  indexRefresh.start();
  indexRefresh.invalidate({ delay: 250 });
}());
