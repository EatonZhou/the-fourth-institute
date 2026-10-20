(function () {
  "use strict";

  if (window.PortalResidentProtocol) return;

  const stores = new Map();
  let actorRevision = 0;
  const MAX_CACHED_RESIDENTS = 32;

  async function currentActorResponse(request) {
    const requestActorRevision = actorRevision;
    const response = await request;
    if (requestActorRevision !== actorRevision) {
      throw new DOMException("Resident identity changed", "AbortError");
    }
    return response;
  }

  function touchStore(store) {
    stores.delete(store.residentId);
    stores.set(store.residentId, store);
    while (stores.size > MAX_CACHED_RESIDENTS) {
      const candidate = [...stores.values()].find(item => (
        item.refCount === 0 && item.stateRefCount === 0 && !item.loading && !item.submissions.size
      ));
      if (!candidate) break;
      candidate.dispose();
    }
  }

  async function deliverInput(payload) {
    const requestActorRevision = actorRevision;
    try {
      return await controlAcknowledgement(window.PortalResidentService.sendInput(payload));
    } catch (error) {
      if (requestActorRevision !== actorRevision) throw error;
      const residentId = String(payload?.resident_id || "").trim();
      const messageId = String(payload?.message?.message_id || "").trim();
      const status = Number(error?.status || 0);
      if (!residentId || !messageId || (status >= 400 && status < 500 && status !== 408)) throw error;
      let snapshot = stores.get(residentId)?.snapshot;
      let inputState = snapshot?.input_states?.find(item => item.message_id === messageId);
      let message = snapshot?.messages?.find(item => item.message_id === messageId);
      if (!inputState && !message) {
        try {
          inputState = await window.PortalResidentService.inputState(residentId, messageId);
        } catch {
          throw error;
        }
        if (requestActorRevision !== actorRevision) throw error;
        if (inputState?.message_id !== messageId) throw error;
        snapshot = stores.get(residentId)?.snapshot;
        message = snapshot?.messages?.find(item => item.message_id === messageId);
      }
      const acknowledgement = clone({
        resident_id: residentId,
        ...(inputState ? { input_state: inputState } : {}),
        ...(message ? { message } : {}),
        ...(snapshot?.state ? { state: snapshot.state } : {}),
        ...(snapshot?.controls ? { controls: snapshot.controls } : {}),
        ...(snapshot?.queue ? { queue: snapshot.queue } : {}),
      });
      acceptControlAcknowledgement(acknowledgement);
      return acknowledgement;
    }
  }

  // Local submissions belong to the shared Resident store, separately from
  // server messages/cursors. Editors can clear immediately without owning a
  // second copy of delivery, retry or ordering rules.
  function sendInput(payload, options = {}) {
    return residentStore(payload.resident_id).submit(payload, options);
  }

  function acceptControlAcknowledgement(acknowledgement) {
    stores.get(String(acknowledgement?.resident_id || "").trim())?.acceptAcknowledgement(acknowledgement);
    return acknowledgement;
  }

  async function controlAcknowledgement(request) {
    return acceptControlAcknowledgement(await currentActorResponse(request));
  }

  function injectInput(payload) {
    return controlAcknowledgement(window.PortalResidentService.injectInput(payload));
  }

  function cancelInput(payload) {
    return controlAcknowledgement(window.PortalResidentService.cancelInput(payload));
  }

  function clearMailbox(payload) {
    return controlAcknowledgement(window.PortalResidentService.clearMailbox(payload));
  }

  // Mailbox organization (decision_b5d349190743): merge, split and reorder waiting messages.
  function mergeMailboxItems(payload) {
    return currentActorResponse(window.PortalResidentService.mergeMailboxItems(payload));
  }

  function splitMailboxItem(payload) {
    return currentActorResponse(window.PortalResidentService.splitMailboxItem(payload));
  }

  function reorderMailbox(payload) {
    return currentActorResponse(window.PortalResidentService.reorderMailbox(payload));
  }

  // The Mailbox tab: read what waits and what ran, and put a stopped or failed round back.
  function loadMailbox(residentId) {
    return currentActorResponse(window.PortalResidentService.mailbox(residentId));
  }

  function requeueMessage(payload) {
    return currentActorResponse(window.PortalResidentService.requeueMessage(payload));
  }

  function loadMessage(residentId, messageId) {
    return currentActorResponse(window.PortalResidentService.message(residentId, messageId));
  }

  function loadSentPrompt(residentId, messageId) {
    return currentActorResponse(window.PortalResidentService.sentPrompt(residentId, messageId));
  }

  function previewPromptInjection(residentId, turn) {
    return currentActorResponse(window.PortalResidentService.promptInjectionPreview(residentId, turn));
  }

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function isTransientSyncError(error) {
    const status = Number(error?.status || 0);
    return status === 408 || status === 429 || (status >= 500 && status <= 599)
      || (!status && (error instanceof TypeError || ["NetworkError", "TimeoutError", "AbortError"].includes(error?.name)));
  }

  let activeStreams = 0;
  function handlePortalReady(event) {
    for (const store of [...stores.values()]) {
      if (store.streamStarted) store.onPortalReady(event);
    }
  }

  function handlePortalResubscribed(event) {
    const added = new Set(event?.detail?.added_resident_ids || []);
    for (const store of [...stores.values()]) {
      if (store.streamStarted && added.has(store.residentId)) store.onStreamExtended();
    }
  }

  function handlePortalChange(event) {
    const detail = event?.detail || {};
    if (detail.snapshot_required) {
      for (const store of [...stores.values()]) {
        if (store.streamStarted) store.onPortalChange(event);
      }
      return;
    }
    const residentIds = new Set(
      (Array.isArray(detail.resident_events) ? detail.resident_events : [])
        .map(item => String(item?.resident_id || "").trim())
        .filter(Boolean),
    );
    for (const [residentId, scopes] of Object.entries(detail.resident_changes || {})) {
      if (Array.isArray(scopes) && (scopes.includes("identity") || scopes.includes("projection"))) {
        residentIds.add(residentId);
      }
    }
    for (const residentId of Object.keys(detail.projection_changes?.["resident-view-state"] || {})) {
      residentIds.add(residentId);
    }
    const viewerStateChangedForActor = Boolean(detail.projection_changes?.["resident-view-state"]?.current);
    if (viewerStateChangedForActor) {
      for (const residentId of stores.keys()) residentIds.add(residentId);
    }
    for (const residentId of residentIds) {
      const store = stores.get(residentId);
      if (store?.streamStarted) store.onPortalChange(event);
    }
  }

  let stateReconciliationTimer = null;

  function reconcileSubscribedStates() {
    if (document.hidden) return;
    const chatOnly = [];
    for (const store of stores.values()) {
      // Preserve state projections' existing grouped refresh and retry contract.
      if (store.stateRefCount) store.invalidateState("reconciliation");
      else if (store.refCount) chatOnly.push(store);
    }
    // Quota protection has no conversation cursor/event. Chat-only views also
    // need a formal batch read, without adding per-view quota polling.
    if (chatOnly.length) loadResidentStateSnapshots(chatOnly.map(store => store.residentId), {
      force: true, includeViewerState: false,
    }).catch(error => chatOnly.forEach(store => store.emitError(error)));
  }

  function retainEventStream(store) {
    if ((store.refCount || store.stateRefCount) && !store.releaseConversationInterest) {
      store.releaseConversationInterest = window.PortalEvents?.watchResident?.(store.residentId) || null;
    }
    if (!store.refCount && !store.stateRefCount && store.releaseConversationInterest) {
      store.releaseConversationInterest();
      store.releaseConversationInterest = null;
    }
    if (store.streamStarted) return;
    store.streamStarted = true;
    activeStreams += 1;
    if (activeStreams !== 1) return;
    window.addEventListener("portal:change", handlePortalChange);
    window.addEventListener("portal:events-ready", handlePortalReady);
    window.addEventListener("portal:events-resubscribed", handlePortalResubscribed);
    document.addEventListener("visibilitychange", reconcileSubscribedStates);
    window.addEventListener("focus", reconcileSubscribedStates);
    stateReconciliationTimer = window.setInterval(reconcileSubscribedStates, 120_000);
  }

  function releaseEventStream(store) {
    store.releaseConversationInterest?.();
    store.releaseConversationInterest = null;
    if (!store.streamStarted) return;
    store.streamStarted = false;
    activeStreams = Math.max(0, activeStreams - 1);
    if (activeStreams) return;
    window.removeEventListener("portal:change", handlePortalChange);
    window.removeEventListener("portal:events-ready", handlePortalReady);
    window.removeEventListener("portal:events-resubscribed", handlePortalResubscribed);
    document.removeEventListener("visibilitychange", reconcileSubscribedStates);
    window.removeEventListener("focus", reconcileSubscribedStates);
    window.clearInterval(stateReconciliationTimer);
    stateReconciliationTimer = null;
  }

  function executionGate(value = {}) {
    const gate = value?.execution_gate;
    if (String(value?.intake_state || "").toLowerCase() === "closed") return { state: "closed", reason: "manual" };
    if (!gate || !["open", "closed", "unknown"].includes(gate.state)) {
      return { state: "unknown", reason: "quota_unknown" };
    }
    // A gate-less OpenIntake receipt cannot confirm quota recovery.
    if (gate.reason === "manual") return { state: "unknown", reason: "quota_unknown" };
    return { ...gate };
  }

  function executionGateView(value = {}) {
    const gate = executionGate(value);
    const t = text => window.PortalI18n.t(text);
    const reasons = {
      manual: "人工接单已关闭",
      quota_used_up: "账号额度已用尽",
      quota_reserved: "已达到配置的额度保护阈值",
      quota_recovery_unconfirmed: "额度恢复尚未确认",
      quota_unknown: "账号额度尚未确认",
      account_disabled: "执行账号已停用",
      account_unavailable: "执行账号状态尚未确认",
      execution_unavailable: "执行服务暂不可达",
    };
    const label = gate.state === "closed" ? "CLOSED" : gate.state === "unknown" ? t("执行状态未确认") : "";
    const reasonText = gate.state === "open" ? "" : t(reasons[gate.reason] || "执行保护状态尚未确认");
    return { ...gate, label, reasonText, text: label ? `${label} · ${reasonText}` : "" };
  }

  function mergeEventState(previous, incoming) {
    const next = clone(incoming || {});
    if (!Object.hasOwn(next, "execution_gate") && previous?.execution_gate) {
      next.execution_gate = clone(previous.execution_gate);
    }
    return next;
  }

  function mergeEventControls(previous, incoming, state, previousState) {
    const next = clone(incoming || {});
    // Minimal events/receipts contain manual controls but no current quota read.
    // Keep the backend queue hint while still taking new permission controls.
    if (!Object.hasOwn(state || {}, "execution_gate")
      && state?.intake_state === previousState?.intake_state) {
      next.send_hint = previous?.send_hint || next.send_hint || "";
    }
    return next;
  }

  function stateView(value = {}) {
    const source = value && typeof value === "object" ? value : {};
    const rawWorkState = String(source.work_state || "idle").trim().toLowerCase();
    const workState = ["queued", "running", "stopping"].includes(rawWorkState)
      ? rawWorkState
      : "idle";
    const intakeState = String(source.intake_state || "open").trim().toLowerCase() === "closed"
      ? "closed"
      : "open";
    const activeMessageId = String(source.active_message_id || "").trim();
    const rawWorkPhase = String(source.work_phase || "").trim().toLowerCase();
    const workPhase = ["starting_session", "waiting_capacity", "waiting_dispatch", "waiting_quota", "family_routing", "family_waiting"].includes(rawWorkPhase)
      ? rawWorkPhase
      : "";
    const familyState = source.family_activity?.work_state;
    const displayWorkState = ["idle", "queued", "running"].includes(familyState) ? familyState : workState;
    return {
      ...source,
      execution_gate: executionGate(source),
      work_state: workState,
      status: displayWorkState === "stopping" ? "running" : displayWorkState,
      intake_state: intakeState,
      active_message_id: activeMessageId,
      work_phase: workPhase,
      queued_count: Math.max(0, Number(source.queued_count || 0)),
      display_work_state: displayWorkState,
      queued: displayWorkState === "queued",
      working: ["running", "stopping"].includes(displayWorkState),
    };
  }

  class ResidentStore {
    constructor(residentId) {
      this.residentId = String(residentId || "").trim();
      this.snapshot = null;
      this.snapshotHistoryLimit = 0;
      this.cursor = null;
      this.loading = null;
      this.loadingForce = false;
      this.recovering = false;
      this.recoveryLoading = null;
      this.recoveryRequested = false;
      this.recoveryTimer = null;
      this.recoveryRetryMs = 1000;
      this.snapshotRetryTimer = null;
      this.snapshotRetryMs = 1000;
      this.listeners = new Set();
      this.refCount = 0;
      this.reloadRequested = false;
      this.pendingEvents = [];
      this.pendingLiveEvents = [];
      this.pendingAcknowledgements = [];
      this.streamStarted = false;
      this.releaseConversationInterest = null;
      this.historyLoading = null;
      this.stateProjection = null;
      this.viewerState = null;
      this.stateCursor = null;
      this.stateListeners = new Set();
      this.stateRefCount = 0;
      this.pendingStateEvents = [];
      this.stateSnapshotLoaded = false;
      this.stateLoading = null;
      this.submissions = new Map();
      this.submissionTimer = null;
    }

    submissionSnapshot() {
      if (!this.snapshot) return null;
      const snapshot = clone(this.snapshot);
      if (this.submissions.size) snapshot.local_submissions = [...this.submissions.values()].map(entry => ({
        ...clone(entry.preview), local_delivery: { state: entry.state, error: entry.error || "" },
      }));
      return snapshot;
    }

    submit(payload, { prepare, preview = {} } = {}) {
      const id = String(payload.message?.message_id || "");
      if (!id) return Promise.reject(new Error("message_id required"));
      const existing = this.submissions.get(id);
      if (existing) return existing.promise;
      const entry = { payload: clone(payload), prepare, state: "sending", settled: false,
        preview: { ...clone(payload.message), created_at: new Date().toISOString(), ...clone(preview) } };
      entry.promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject; });
      this.submissions.set(id, entry);
      this.emit({ type: "submission", snapshot: this.snapshot });
      this.pumpSubmissions();
      return entry.promise;
    }

    settleSubmission(entry, acknowledgement, error = null) {
      if (entry.settled) return;
      entry.settled = true;
      if (error) {
        error.localSubmission = true;
        entry.reject(error);
      } else entry.resolve(acknowledgement);
      queueMicrotask(() => this.pumpSubmissions());
    }

    reconcileSubmissions() {
      if (!this.snapshot) return;
      for (const [id, entry] of this.submissions) {
        const message = this.snapshot.messages?.find(item => item.message_id === id);
        const input = this.snapshot.input_states?.find(item => item.message_id === id);
        const queued = this.snapshot.queue?.items?.find(item => item.message_id === id);
        if (!message && !input && !queued) continue;
        this.settleSubmission(entry, { resident_id: this.residentId,
          ...(message ? { message } : {}), ...(input ? { input_state: input } : {}) });
        if (message || queued || ["cancelled", "failed", "completed"].includes(input?.state)) {
          this.submissions.delete(id);
        } else {
          entry.state = "accepted";
          entry.error = "";
        }
      }
    }

    pumpSubmissions() {
      const entry = [...this.submissions.values()].find(item => !item.settled);
      if (!entry || entry.started) return;
      entry.started = true;
      const revision = actorRevision;
      // Preparation can be async; reserve its position before awaiting it.
      Promise.resolve().then(async () => {
        if (entry.prepare) await entry.prepare(entry.payload, clone(this.snapshot));
        entry.prepared = true;
        if (revision !== actorRevision) return;
        entry.dispatched = true;
        return deliverInput(entry.payload);
      }).then(result => {
        if (revision !== actorRevision || !result) return;
        entry.state = "accepted";
        this.settleSubmission(entry, result);
        this.emit({ type: "submission", snapshot: this.snapshot });
      }).catch(error => {
        if (revision !== actorRevision || entry.settled) return;
        const status = Number(error?.status || 0);
        const uncertain = entry.dispatched && (!status || status === 408 || status >= 500);
        entry.state = uncertain ? "confirming" : "failed";
        entry.error = String(error.message || error);
        if (uncertain) this.scheduleSubmissionCheck();
        else this.settleSubmission(entry, null, error);
        this.emit({ type: "submission", snapshot: this.snapshot });
      });
    }

    scheduleSubmissionCheck() {
      if (this.submissionTimer !== null || !this.refCount) return;
      this.submissionTimer = window.setTimeout(() => {
        this.submissionTimer = null;
        this.checkSubmissions();
      }, 2000);
    }

    async checkSubmissions() {
      const revision = actorRevision;
      for (const [id, entry] of this.submissions) {
        if (entry.state !== "confirming" || entry.checking) continue;
        entry.checking = true;
        try {
          const input = await window.PortalResidentService.inputState(this.residentId, id);
          if (revision !== actorRevision) return;
          if (input?.message_id !== id) continue;
          this.acceptAcknowledgement({ resident_id: this.residentId, input_state: input });
          // The input lookup proves receipt; obtain its authoritative queue and
          // conversation projection rather than inventing either locally.
          this.load({ force: true }).catch(() => {});
        } catch (_) {
          // Even not-found can race an in-flight admission. Keep the original
          // identity and never automatically redeliver an uncertain request.
        } finally { entry.checking = false; }
      }
      if ([...this.submissions.values()].some(entry => entry.state === "confirming")) this.scheduleSubmissionCheck();
    }

    retrySubmission(id) {
      const entry = this.submissions.get(id);
      if (!entry || entry.state !== "failed") return Promise.resolve();
      this.submissions.delete(id);
      return this.submit(entry.payload, { preview: entry.preview, prepare: entry.prepared ? null : entry.prepare });
    }

    dismissSubmission(id) {
      if (this.submissions.get(id)?.state !== "failed") return;
      this.submissions.delete(id);
      this.emit({ type: "submission", snapshot: this.snapshot });
    }

    subscribe(listener) {
      const alreadyAttached = this.refCount > 0;
      this.listeners.add(listener);
      this.refCount += 1;
      touchStore(this);
      this.startStream();
      if (alreadyAttached && this.snapshot) {
        listener({ type: "snapshot", snapshot: this.submissionSnapshot() });
      }
      if ([...this.submissions.values()].some(entry => entry.state === "confirming")) this.scheduleSubmissionCheck();
      return () => {
        if (!this.listeners.delete(listener)) return;
        this.refCount = Math.max(0, this.refCount - 1);
        if (!this.refCount) {
          window.clearTimeout(this.submissionTimer);
          this.submissionTimer = null;
          this.resetConversationRequests();
          this.snapshot = null;
          this.cursor = null;
          this.pendingEvents = [];
          this.pendingLiveEvents = [];
          this.pendingAcknowledgements = [];
          if (!this.stateRefCount) this.stopStream();
          else this.startStream();
        }
      };
    }

    dispose() {
      this.resetConversationRequests();
      this.stopStream();
      stores.delete(this.residentId);
    }

    resetConversationRequests() {
      if (this.recoveryTimer !== null) window.clearTimeout(this.recoveryTimer);
      if (this.snapshotRetryTimer !== null) window.clearTimeout(this.snapshotRetryTimer);
      if (this.projectionRefreshTimer) window.clearTimeout(this.projectionRefreshTimer);
      this.recoveryTimer = null;
      this.snapshotRetryTimer = null;
      this.projectionRefreshTimer = null;
      this.snapshotRetryMs = 1000;
      this.recoveryLoading = null;
      this.recoveryRequested = false;
      this.recovering = false;
      this.recoveryRetryMs = 1000;
      this.loading = null;
      this.loadingForce = false;
      this.reloadRequested = false;
      this.historyLoading = null;
    }

    subscribeState(listener) {
      this.stateListeners.add(listener);
      this.stateRefCount += 1;
      touchStore(this);
      this.startStream();
      if (this.stateProjection && this.stateSnapshotLoaded) {
        listener({
          type: "snapshot",
          resident_id: this.residentId,
          state: clone(this.stateProjection),
          cursor: Number(this.stateCursor || 0),
        });
      }
      return () => {
        if (!this.stateListeners.delete(listener)) return;
        this.stateRefCount = Math.max(0, this.stateRefCount - 1);
        if (!this.refCount && !this.stateRefCount) this.stopStream();
        if (!this.refCount && !this.stateRefCount) this.stateSnapshotLoaded = false;
      };
    }

    emit(value) {
      if (value.snapshot) {
        this.reconcileSubmissions();
        value = { ...value, snapshot: this.submissionSnapshot() };
      }
      for (const listener of this.listeners) {
        try {
          listener(value);
        } catch (error) {
          console.error(window.PortalI18n.t("Resident protocol subscriber failed"), error);
        }
      }
    }

    emitError(error) {
      this.emit({ type: "error", error });
    }

    emitState(value) {
      const update = {
        ...value,
        resident_id: this.residentId,
        state: clone(this.stateProjection || {}),
        cursor: Number(this.stateCursor || 0),
      };
      for (const listener of this.stateListeners) {
        try {
          listener(update);
        } catch (error) {
          console.error(window.PortalI18n.t("Resident state subscriber failed"), error);
        }
      }
    }

    acceptStateSnapshot(state, cursor, viewerState, controls) {
      const nextCursor = Number(cursor || 0);
      const older = Number.isFinite(this.stateCursor) && nextCursor < this.stateCursor;
      // Formal quota reads can advance independently of the event cursor.
      if (older && !controls) return;
      this.stateProjection = older
        ? { ...this.stateProjection, execution_gate: clone(state?.execution_gate || { state: "unknown", reason: "quota_unknown" }) }
        : clone(state || {});
      if (controls && this.snapshot) {
        this.snapshot.state = nextCursor >= this.cursor ? clone(this.stateProjection)
          : { ...this.snapshot.state, execution_gate: clone(this.stateProjection.execution_gate
            || { state: "unknown", reason: "quota_unknown" }) };
        this.snapshot.controls = older
          ? { ...this.snapshot.controls, send_hint: controls.send_hint || "" }
          : clone(controls);
        this.emit({ type: "state_snapshot", snapshot: clone(this.snapshot) });
      }
      if (viewerState && typeof viewerState.unread === "boolean") {
        this.viewerState = clone(viewerState);
      }
      this.stateCursor = older ? this.stateCursor : nextCursor;
      this.stateSnapshotLoaded = true;
      const pending = this.pendingStateEvents;
      this.pendingStateEvents = [];
      for (const event of pending) {
        const eventCursor = Number(event?.cursor || 0);
        if (eventCursor <= this.stateCursor) continue;
        this.applyStateEvent(event);
      }
      this.emitState({ type: "snapshot" });
    }

    applyStateEvent(event, { emit = true } = {}) {
      if (String(event?.event_type || "") !== "resident.state.changed") return;
      if (!Number.isFinite(this.stateCursor)) {
        this.pendingStateEvents.push(clone(event));
        return;
      }
      const cursor = Number(event?.cursor || 0);
      if (!Number.isSafeInteger(cursor) || cursor < 0) {
        this.emitState({ type: "invalid", reason: "invalid_cursor" });
        return;
      }
      if (cursor <= this.stateCursor) return;
      if (
        event?.payload_omitted === true || !event?.payload?.state
          || typeof event.payload.state !== "object"
      ) {
        this.emitState({ type: "invalid", reason: "payload_omitted" });
        return;
      }
      if (this.stateProjection?.family_activity && !event.payload.state.family_activity) {
        // Canonical events describe the facade's own queue, not the Family.
        this.scheduleProjectionRefresh();
        return;
      }
      this.stateCursor = cursor;
      this.stateProjection = mergeEventState(this.stateProjection, event?.payload?.state);
      if (emit) this.emitState({ type: "event", event: clone(event) });
    }

    invalidateState(reason) {
      this.stateSnapshotLoaded = false;
      if (this.stateRefCount) this.emitState({ type: "invalid", reason });
    }

    async load({ force = false, historyLimit } = {}) {
      await window.PortalIdentity?.load?.();
      // Identity initialization can resume a reader and an internal recovery
      // together. Reuse that reader's depth after the identity wait settles;
      // only an explicit request for more history expands the shared snapshot.
      historyLimit ??= this.loadingHistoryLimit || this.snapshotHistoryLimit || 200;
      if (this.snapshot && !force && this.snapshotHistoryLimit >= historyLimit) return clone(this.snapshot);
      if (this.loading) {
        if (historyLimit > this.loadingHistoryLimit) {
          await this.loading;
          return this.load({ force, historyLimit });
        }
        if (force && !this.loadingForce) this.reloadRequested = true;
        return this.loading;
      }
      this.loadingHistoryLimit = historyLimit;
      this.loadingForce = force;
      this.recovering = this.loadingForce;
      const requestActorRevision = actorRevision;
      const request = window.PortalResidentService.snapshot(this.residentId, { historyLimit })
        .then(snapshot => {
          if (requestActorRevision !== actorRevision || this.loading !== request) throw new DOMException("Resident request superseded", "AbortError");
          if (String(snapshot?.resident?.resident_id || "") !== this.residentId) {
            throw new Error("Resident Snapshot target mismatch");
          }
          if (!this.refCount) return clone(snapshot);
          this.snapshot = clone(snapshot);
          this.snapshotHistoryLimit = historyLimit;
          if (this.snapshotRetryTimer !== null) window.clearTimeout(this.snapshotRetryTimer);
          this.snapshotRetryTimer = null;
          this.snapshotRetryMs = 1000;
          this.cursor = Number(snapshot?.cursor || 0);
          this.acceptStateSnapshot(snapshot?.state || {}, this.cursor);
          this.reapplyAcknowledgements();
          touchStore(this);
          const pending = this.pendingEvents;
          this.pendingEvents = [];
          for (const event of pending) {
            const eventCursor = Number(event?.cursor || 0);
            if (eventCursor <= this.cursor) continue;
            if (event?.payload_omitted === true || eventCursor !== this.cursor + 1) {
              this.recoveryRequested = true;
              break;
            }
            this.applyEvent(event);
          }
          // Live events that arrived while this Snapshot loaded are at least as new.
          const live = this.pendingLiveEvents;
          this.pendingLiveEvents = [];
          for (const event of live) this.applyLivePayload(event);
          this.recovering = false;
          this.emit({ type: "snapshot", snapshot: clone(this.snapshot) });
          return clone(this.snapshot);
        })
        .catch(error => {
          if (requestActorRevision !== actorRevision || this.loading !== request) throw error;
          this.recovering = false;
          this.emitError(error);
          throw error;
        })
        .finally(() => {
          if (this.loading !== request) return;
          this.loading = null;
          this.loadingForce = false;
          if (!this.reloadRequested) return;
          this.reloadRequested = false;
          this.load({ force: true, historyLimit }).catch(() => {});
        });
      this.loading = request;
      request.finally(() => {
        if (requestActorRevision === actorRevision && this.recoveryRequested) this.scheduleRecovery(0);
      }).catch(() => {});
      return request;
    }

    stopStream() {
      releaseEventStream(this);
    }

    startStream() {
      if (!this.refCount && !this.stateRefCount) return;
      retainEventStream(this);
    }

    onPortalReady(event) {
      if (event?.detail?.snapshot_required !== true) return;
      this.invalidateState("stream_reset");
      if (!this.refCount) return;
      this.refreshSnapshotInBackground();
    }

    // The stream carries this Resident's events only from now on; what happened
    // since its Snapshot comes from the event log.
    onStreamExtended() {
      if (this.stateRefCount) this.invalidateState("subscription");
      if (this.refCount) this.scheduleRecovery(0);
    }

    scheduleProjectionRefresh() {
      if ((!this.refCount && !this.stateRefCount) || this.projectionRefreshTimer) return;
      // A member's live activity arrives in bursts; one reload covers each burst.
      this.projectionRefreshTimer = window.setTimeout(() => {
        this.projectionRefreshTimer = null;
        this.invalidateState("projection");
        // The in-flight response may predate this member's progress. Forced
        // loads share that request, so remember to fetch again after it settles.
        if (this.loading) this.reloadRequested = true;
        this.refreshSnapshotInBackground();
      }, 400);
    }

    refreshSnapshotInBackground() {
      if (!this.refCount) return;
      const requestActorRevision = actorRevision;
      this.load({ force: true }).catch(error => {
        if (requestActorRevision !== actorRevision || !this.refCount
          || !isTransientSyncError(error) || this.snapshotRetryTimer !== null) return;
        const delay = this.snapshotRetryMs;
        this.snapshotRetryMs = Math.min(delay * 2, 10000);
        this.snapshotRetryTimer = window.setTimeout(() => {
          this.snapshotRetryTimer = null;
          this.refreshSnapshotInBackground();
        }, delay);
      });
    }

    onPortalChange(notification) {
      const detail = notification?.detail || {};
      if (detail.projection_changes?.["resident-view-state"]?.[this.residentId]
        || detail.projection_changes?.["resident-view-state"]?.current) {
        this.invalidateState("viewer-state");
      }
      if (detail.resident_changes?.[this.residentId]?.includes("identity") && this.refCount) {
        this.refreshSnapshotInBackground();
      }
      // A Family facade shows its members' replies (decision_44cf587c1721):
      // they change when a member works on a relayed message.
      if (detail.resident_changes?.[this.residentId]?.includes("projection")) {
        this.scheduleProjectionRefresh();
      }
      if (detail.snapshot_required) {
        this.invalidateState("stream_reset");
        if (!this.refCount) return;
        this.refreshSnapshotInBackground();
        return;
      }
      const events = Array.isArray(detail.resident_events)
        ? detail.resident_events
        : [];
      for (const notificationEvent of events) {
        if (String(notificationEvent?.resident_id || "") !== this.residentId) continue;
        if (notificationEvent?.live === true) {
          this.applyLiveEvent(notificationEvent);
          continue;
        }
        this.applyStateEvent(notificationEvent);
        if (notificationEvent.event_type === "resident.input.state.changed") {
          const inputState = String(notificationEvent.payload?.input_state?.state || "");
          if (["completed", "failed", "cancelled"].includes(inputState)) {
            this.invalidateState("completion");
          }
          // A facade message closes when the Router relays it; its member appears in the projection.
          if (this.snapshot?.family_facade) this.scheduleProjectionRefresh();
        }
        const event = { ...notificationEvent };
        if (event.event_type === "resident.state.changed") {
          const controls = event.payload?.controls;
          const completeControls = controls
            && typeof controls.can_send === "boolean"
            && typeof controls.can_stop === "boolean"
            && typeof controls.disabled_reason === "string"
            && typeof controls.send_hint === "string";
          if (!completeControls) {
            delete event.payload;
            event.payload_omitted = true;
          }
        }
        // State and Chat are projections of the same ordered event stream,
        // but either Snapshot may be loading independently.
        if (!this.refCount) continue;
        if (
          !this.snapshot
          || this.cursor == null
          || this.recovering
          || event?.payload_omitted === true
        ) {
          this.pendingEvents.push(clone(event));
          if (this.snapshot && this.cursor != null) {
            this.scheduleRecovery(event?.payload_omitted === true ? 0 : 250);
          }
          continue;
        }
        const eventCursor = Number(event?.cursor || 0);
        if (eventCursor <= this.cursor) continue;
        try {
          this.applyEvent(event);
        } catch (_error) {
          this.pendingEvents.push(clone(event));
          this.scheduleRecovery(0);
        }
      }
    }

    // A live event carries current observation state (activities). It is not
    // in the event log and never moves the cursor.
    applyLiveEvent(event) {
      if (!this.refCount) return;
      if (event?.payload_omitted === true) {
        this.refreshSnapshotInBackground();
        return;
      }
      if (!this.snapshot || this.cursor == null || this.loading) {
        this.pendingLiveEvents.push(clone(event));
        return;
      }
      this.applyLivePayload(event);
      this.emit({ type: "event", event: clone(event), snapshot: clone(this.snapshot) });
    }

    applyLivePayload(event) {
      if (String(event?.event_type || "") === "resident.activity.changed") {
        this.snapshot.activities = clone(event?.payload?.activities || []);
      }
    }

    scheduleRecovery(delay = 250) {
      if (!this.refCount) return;
      this.recoveryRequested = true;
      if (!this.snapshot || this.cursor == null || this.loading || this.recoveryLoading) return;
      const wait = Math.max(0, Number(delay || 0));
      if (this.recoveryTimer !== null) {
        if (wait > 0) return;
        window.clearTimeout(this.recoveryTimer);
        this.recoveryTimer = null;
      }
      this.recoveryTimer = window.setTimeout(() => {
        this.recoveryTimer = null;
        this.recoverEvents().catch(() => {});
      }, wait);
    }

    recoverEvents() {
      if (!this.snapshot || this.cursor == null) return this.load({ force: true });
      if (this.loading) {
        this.recoveryRequested = true;
        return this.loading;
      }
      if (this.recoveryLoading) {
        this.recoveryRequested = true;
        return this.recoveryLoading;
      }
      this.recoveryRequested = false;
      this.recovering = true;
      const afterCursor = Number(this.cursor || 0);
      const requestActorRevision = actorRevision;
      let retryDelay = 0;
      const isCurrent = () => requestActorRevision === actorRevision && this.recoveryLoading === request;
      const request = window.PortalResidentService.events(this.residentId, {
        afterCursor,
        limit: 500,
      })
        .then(payload => {
          if (!isCurrent()) throw new DOMException("Resident request superseded", "AbortError");
          if (String(payload?.resident_id || "") !== this.residentId) {
            throw new Error("Resident Event recovery target mismatch");
          }
          const events = (Array.isArray(payload?.events) ? payload.events : [])
            .slice()
            .sort((left, right) => Number(left?.cursor || 0) - Number(right?.cursor || 0));
          const highWatermark = Number(payload?.high_watermark ?? payload?.cursor ?? 0);
          let expectedCursor = this.cursor;
          for (const event of events) {
            const cursor = Number(event?.cursor || 0);
            if (cursor <= expectedCursor) continue;
            if (cursor !== expectedCursor + 1 || event?.payload_omitted === true) {
              this.recoveryRequested = false;
              return this.load({ force: true });
            }
            expectedCursor = cursor;
          }
          if (expectedCursor === this.cursor && highWatermark > this.cursor) {
            this.recoveryRequested = false;
            return this.load({ force: true });
          }
          for (const event of events) {
            const eventCursor = Number(event?.cursor || 0);
            if (eventCursor <= this.cursor) continue;
            this.applyStateEvent(event, { emit: false });
            try {
              this.applyEvent(event, { emit: false });
            } catch (_error) {
              this.recoveryRequested = false;
              return this.load({ force: true });
            }
          }
          const pendingCursor = this.pendingEvents.reduce(
            (latest, event) => Math.max(latest, Number(event?.cursor || 0)),
            0,
          );
          this.pendingEvents = [];
          if (highWatermark > this.cursor || pendingCursor > this.cursor) {
            this.recoveryRequested = true;
          }
          touchStore(this);
          this.emitState({ type: "recovery" });
          this.emit({ type: "recovery", snapshot: clone(this.snapshot) });
          return clone(this.snapshot);
        })
        .then(value => {
          if (isCurrent()) this.recoveryRetryMs = 1000;
          return value;
        })
        .catch(error => {
          if (!isCurrent()) throw error;
          const transient = isTransientSyncError(error);
          this.recoveryRequested = transient;
          if (transient) {
            retryDelay = this.recoveryRetryMs;
            this.recoveryRetryMs = Math.min(this.recoveryRetryMs * 2, 10000);
          }
          this.emitError(error);
          throw error;
        })
        .finally(() => {
          if (this.recoveryLoading !== request) return;
          this.recoveryLoading = null;
          this.recovering = false;
          if (this.recoveryRequested) this.scheduleRecovery(retryDelay);
        });
      this.recoveryLoading = request;
      return request;
    }

    acceptAcknowledgement(value) {
      const acknowledgement = value && typeof value === "object" ? value : {};
      if (String(acknowledgement.resident_id || "") !== this.residentId) return;
      if (!this.snapshot) {
        if (acknowledgement.state && typeof acknowledgement.state === "object") {
          this.acceptStateSnapshot(mergeEventState(this.stateProjection, acknowledgement.state), acknowledgement.cursor);
        }
        return;
      }
      const cursor = Number(acknowledgement.cursor ?? this.cursor);
      if (cursor < this.cursor) return;
      if (Number.isFinite(cursor) && cursor > this.cursor) {
        const messageId = String(acknowledgement.message_id || acknowledgement.input_state?.message_id || "");
        this.pendingAcknowledgements = this.pendingAcknowledgements.filter(item => (
          item.cursor !== cursor
          || String(item.message_id || item.input_state?.message_id || "") !== messageId
        ));
        this.pendingAcknowledgements.push({ ...clone(acknowledgement), cursor });
        this.pendingAcknowledgements.sort((left, right) => left.cursor - right.cursor);
      }
      this.applyAcknowledgedProjection(acknowledgement);
      this.reapplyAcknowledgements();
      if (acknowledgement.state && typeof acknowledgement.state === "object") {
        this.acceptStateSnapshot(mergeEventState(this.stateProjection, acknowledgement.state), cursor);
      }
      this.emit({
        type: "acknowledgement",
        acknowledgement: clone(acknowledgement),
        snapshot: clone(this.snapshot),
      });
    }

    reapplyAcknowledgements() {
      // A receipt describes a newer committed projection, not a contiguous
      // event history. Replay older events without rolling controls back or
      // advancing the message cursor past events that have not arrived yet.
      this.pendingAcknowledgements = this.pendingAcknowledgements.filter(
        acknowledgement => acknowledgement.cursor > this.cursor,
      );
      for (const acknowledgement of this.pendingAcknowledgements) {
        this.applyAcknowledgedProjection(acknowledgement);
      }
    }

    applyAcknowledgedProjection(acknowledgement) {
      if (acknowledgement.state && typeof acknowledgement.state === "object") {
        this.snapshot.controls = mergeEventControls(this.snapshot.controls, acknowledgement.controls, acknowledgement.state, this.snapshot.state);
        this.snapshot.state = mergeEventState(this.snapshot.state, acknowledgement.state);
      }
      const inputState = acknowledgement.input_state;
      if (inputState?.message_id) {
        if (!Array.isArray(this.snapshot.input_states)) this.snapshot.input_states = [];
        const next = clone(inputState);
        const index = this.snapshot.input_states.findIndex(
          item => String(item?.message_id || "") === String(next.message_id)
        );
        if (index < 0) this.snapshot.input_states.push(next);
        else this.snapshot.input_states[index] = next;
      }
      if (acknowledgement.queue && typeof acknowledgement.queue === "object") {
        this.snapshot.queue = clone(acknowledgement.queue);
      }
      const acceptedMessage = acknowledgement.message;
      const acceptedInputState = String(inputState?.state || "").trim().toLowerCase();
      if (acceptedMessage?.message_id && acceptedInputState !== "queued") {
        const messageId = String(acceptedMessage.message_id);
        const index = this.snapshot.messages.findIndex(
          message => String(message?.message_id || "") === messageId,
        );
        if (index < 0) this.snapshot.messages.push(clone(acceptedMessage));
        else this.snapshot.messages[index] = clone(acceptedMessage);
      }
    }

    applyEvent(event, { emit = true } = {}) {
      if (!this.snapshot || this.cursor == null) {
        throw new Error("Resident Event arrived before Snapshot");
      }
      const cursor = Number(event?.cursor || 0);
      if (cursor !== this.cursor + 1) {
        throw new Error(
          `Resident Event cursor gap: expected ${this.cursor + 1}, received ${cursor}`
        );
      }
      const payload = event?.payload && typeof event.payload === "object"
        ? event.payload
        : {};
      switch (String(event?.event_type || "")) {
        case "conversation.message.appended":
          if (!payload.message?.message_id) throw new Error("Message append is incomplete");
          {
            const messageId = String(payload.message.message_id);
            const index = this.snapshot.messages.findIndex(
              message => String(message?.message_id || "") === messageId,
            );
            if (index < 0) this.snapshot.messages.push(clone(payload.message));
            else this.snapshot.messages[index] = clone(payload.message);
          }
          break;
        case "conversation.message.updated": {
          const messageId = String(payload.message_id || payload.message?.message_id || "");
          const index = this.snapshot.messages.findIndex(
            message => String(message?.message_id || "") === messageId
          );
          if (index < 0) throw new Error(`Message update target is absent: ${messageId}`);
          if (payload.removed === true) this.snapshot.messages.splice(index, 1);
          else if (payload.message?.message_id) this.snapshot.messages[index] = clone(payload.message);
          else throw new Error("Message update is incomplete");
          break;
        }
        case "conversation.context.cleared":
          if (this.snapshot.inheritance && payload.context_cleared === true) {
            this.snapshot.inheritance = { ...this.snapshot.inheritance, context_cleared: true };
          }
          break;
        case "resident.state.changed":
          this.snapshot.controls = mergeEventControls(this.snapshot.controls, payload.controls, payload.state, this.snapshot.state);
          this.snapshot.state = mergeEventState(this.snapshot.state, payload.state);
          break;
        case "resident.input.state.changed": {
          const next = clone(payload.input_state || {});
          const index = this.snapshot.input_states.findIndex(
            item => item?.message_id === next.message_id
          );
          if (index < 0) this.snapshot.input_states.push(next);
          else this.snapshot.input_states[index] = next;
          break;
        }
        case "resident.queue.changed":
          this.snapshot.queue = clone(payload.queue || { resident_id: this.residentId, items: [] });
          break;
        case "resident.runtime_settings.changed":
          this.snapshot.runtime_settings = clone(payload.runtime_settings || {});
          break;
        default:
          break;
      }
      this.cursor = cursor;
      this.snapshot.cursor = cursor;
      this.reapplyAcknowledgements();
      if (emit) {
        this.emit({ type: "event", event: clone(event), snapshot: clone(this.snapshot) });
      }
    }

    async loadOlder({ limit = 200 } = {}) {
      const pageToken = String(this.snapshot?.next_page_token || "").trim();
      if (!pageToken) return clone(this.snapshot);
      if (this.historyLoading) return this.historyLoading;
      const requestActorRevision = actorRevision;
      const request = window.PortalResidentService.conversationMessages(this.residentId, {
        pageToken,
        limit,
      }).then(page => {
        if (requestActorRevision !== actorRevision || this.historyLoading !== request) {
          throw new DOMException("Resident request superseded", "AbortError");
        }
        if (String(page?.resident_id || "") !== this.residentId) {
          throw new Error("Resident message page target mismatch");
        }
        const existing = new Set(
          (this.snapshot?.messages || []).map(message => String(message?.message_id || "")),
        );
        const older = (Array.isArray(page?.messages) ? page.messages : [])
          .filter(message => message?.message_id && !existing.has(String(message.message_id)))
          .map(clone);
        this.snapshot.messages = [...older, ...(this.snapshot?.messages || [])];
        const inputStates = new Map((this.snapshot.input_states || []).map(input => [input.message_id, input]));
        for (const input of page.input_states || []) {
          const existingInput = inputStates.get(input.message_id);
          if (!existingInput || String(input.updated_at || "") >= String(existingInput.updated_at || "")) {
            inputStates.set(input.message_id, clone(input));
          }
        }
        this.snapshot.input_states = [...inputStates.values()];
        this.snapshot.next_page_token = String(page?.next_page_token || "");
        this.emit({ type: "history", snapshot: clone(this.snapshot) });
        return clone(this.snapshot);
      }).finally(() => {
        if (this.historyLoading === request) this.historyLoading = null;
      });
      this.historyLoading = request;
      return request;
    }

  }

  async function loadResidentStateSnapshots(residentIds, { force = false, includeViewerState = true } = {}) {
    await window.PortalIdentity?.load?.();
    const requestActorRevision = actorRevision;
    const ids = [...new Set((residentIds || []).map(String).map(value => value.trim()).filter(Boolean))];
    const waits = new Set();
    const missing = [];
    for (const residentId of ids) {
      const store = residentStore(residentId);
      if (store.stateLoading) {
        waits.add(store.stateLoading);
      } else if (force || !store.stateSnapshotLoaded || (includeViewerState && !store.viewerState)) {
        missing.push(residentId);
      }
    }
    if (missing.length) {
      const request = window.PortalResidentService.loadStates(missing, { includeViewerState })
        .then(response => {
          if (requestActorRevision !== actorRevision) throw new DOMException("Resident identity changed", "AbortError");
          if (!Array.isArray(response?.states)) {
            throw new Error("Resident State Snapshot states are invalid");
          }
          const received = new Set();
          for (const item of response.states) {
            const residentId = String(item?.resident_id || "").trim();
            if (!missing.includes(residentId)) continue;
            if (!item?.state || typeof item.state !== "object") {
              throw new Error("Resident State Snapshot state is invalid");
            }
            if (includeViewerState && typeof item?.viewer_state?.unread !== "boolean") {
              throw new Error("Resident State Snapshot viewer state is invalid");
            }
            received.add(residentId);
            residentStore(residentId).acceptStateSnapshot(item.state, Number(item?.cursor || 0), item.viewer_state, item.controls);
          }
          const absent = missing.filter(residentId => !received.has(residentId));
          if (absent.length) throw new Error(`Resident State Snapshot missing: ${absent.join(", ")}`);
        })
        .finally(() => {
          for (const residentId of missing) {
            const store = residentStore(residentId);
            if (store.stateLoading === request) store.stateLoading = null;
          }
        });
      for (const residentId of missing) residentStore(residentId).stateLoading = request;
      waits.add(request);
    }
    await Promise.all([...waits]);
    // A cheaper state-only batch may have been in flight when an Inbox view
    // requested unread state. Fetch that missing projection after it settles.
    if (includeViewerState) {
      const withoutViewer = ids.filter(residentId => !residentStore(residentId).viewerState);
      if (withoutViewer.length) {
        await loadResidentStateSnapshots(withoutViewer, { includeViewerState: true });
      }
    }
    return Object.fromEntries(ids.map(residentId => [
      residentId,
      clone(residentStore(residentId).stateProjection || {}),
    ]));
  }

  class ResidentStateProjection {
    constructor(residentIds, { includeViewerState = true } = {}) {
      this.residentIds = [...new Set(
        (Array.isArray(residentIds) ? residentIds : [])
          .map(value => String(value || "").trim())
          .filter(Boolean)
      )];
      this.states = {};
      this.includeViewerState = includeViewerState;
      this.loading = null;
      this.loadingForce = false;
      this.reloadRequested = false;
      this.listener = null;
      this.subscriptions = [];
      this.batching = false;
      this.retryTimer = null;
      this.retryDelayMs = 1000;
    }

    emit(value) {
      try {
        this.listener?.({ ...value, states: clone(this.states) });
      } catch (error) {
        console.error(window.PortalI18n.t("Resident state subscriber failed"), error);
      }
    }

    async load({ force = false } = {}) {
      if (this.loading) {
        if (force && !this.loadingForce) this.reloadRequested = true;
        const loading = this.loading;
        // A forced read queued behind an older snapshot is not finished when
        // that old snapshot settles. Its finally starts the requested refresh.
        const followRefresh = () => force && this.loading && this.loading !== loading ? this.loading : loading;
        return loading.then(followRefresh, followRefresh);
      }
      if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.batching = true;
      const loading = loadResidentStateSnapshots(this.residentIds, {
        force, includeViewerState: this.includeViewerState,
      })
        .then(states => {
          this.retryDelayMs = 1000;
          this.states = clone(states);
          this.batching = false;
          this.emit({ type: "snapshot" });
          return clone(this.states);
        }).catch(error => {
          this.batching = false;
          this.emit({ type: "error", error });
          this.scheduleRetry(error);
          throw error;
        }).finally(() => {
          if (this.loading !== loading) return;
          this.loading = null;
          this.loadingForce = false;
          if (!this.reloadRequested) return;
          this.reloadRequested = false;
          if (this.listener) this.load({ force: true }).catch(() => {});
        });
      this.loading = loading;
      this.loadingForce = force;
      return loading;
    }

    scheduleRetry(error) {
      const transient = isTransientSyncError(error);
      if (!this.listener || !transient || this.retryTimer !== null) return;
      this.retryTimer = window.setTimeout(() => {
        this.retryTimer = null;
        if (this.listener) this.load({ force: true }).catch(() => {});
      }, this.retryDelayMs);
      this.retryDelayMs = Math.min(this.retryDelayMs * 2, 10000);
    }

    start(listener, options = {}) {
      this.listener = listener;
      this.subscriptions = this.residentIds.map(residentId => (
        residentStore(residentId).subscribeState(update => this.onStateUpdate(update))
      ));
      if (options.load !== false) this.load(options).catch(() => {});
      return this;
    }

    destroy() {
      if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.reloadRequested = false;
      this.subscriptions.forEach(unsubscribe => unsubscribe());
      this.subscriptions = [];
      this.listener = null;
    }

    onStateUpdate(update) {
      if (update?.type === "invalid") {
        if (this.loading) {
          if (this.batching || !this.loadingForce) this.reloadRequested = true;
          return;
        }
        this.load({ force: true }).catch(() => {});
        return;
      }
      const residentId = String(update?.resident_id || "");
      if (!residentId || !this.residentIds.includes(residentId)) return;
      this.states[residentId] = clone(update?.state || {});
      if (!this.batching) this.emit({ type: update.type, event: clone(update?.event) });
    }
  }

  function residentStore(residentId) {
    const key = String(residentId || "").trim();
    if (!key) throw new Error("resident_id required");
    let store = stores.get(key);
    if (!store) {
      store = new ResidentStore(key);
      stores.set(key, store);
    }
    touchStore(store);
    return store;
  }

  function acquire(residentId, listener, options = {}) {
    const key = String(residentId || "").trim();
    if (!key) throw new Error("resident_id required");
    const store = residentStore(key);
    const unsubscribe = store.subscribe(listener);
    if (options.load !== false) store.load(options).catch(() => {});
    return {
      residentId: key,
      load: options => store.load(options),
      loadOlder: options => store.loadOlder(options),
      acceptAcknowledgement: value => store.acceptAcknowledgement(value),
      snapshot: () => store.submissionSnapshot(),
      destroy: unsubscribe,
    };
  }

  function acquireStates(residentIds, listener, options = {}) {
    const collection = new ResidentStateProjection(residentIds, options);
    collection.start(listener, options);
    return {
      load: options => collection.load(options),
      states: () => clone(collection.states),
      destroy: () => collection.destroy(),
    };
  }

  function stateSnapshot(residentId) {
    const store = stores.get(String(residentId || "").trim());
    return {
      resident_id: String(residentId || "").trim(),
      ready: Boolean(store?.stateSnapshotLoaded),
      cursor: store?.stateCursor ?? null,
      state: store?.stateProjection ? stateView(clone(store.stateProjection)) : null,
      viewer_state: store?.viewerState ? clone(store.viewerState) : null,
    };
  }

  function invalidateViewerState(residentId) {
    stores.get(String(residentId || "").trim())?.invalidateState("viewer-state");
  }

  function resetActorState() {
    actorRevision += 1;
    const activeStores = [...stores.values()];
    for (const store of activeStores) {
      window.clearTimeout(store.submissionTimer);
      store.submissionTimer = null;
      for (const entry of store.submissions.values()) {
        store.settleSubmission(entry, null, new DOMException("Resident identity changed", "AbortError"));
      }
      store.submissions.clear();
      store.resetConversationRequests();
      store.snapshot = null;
      store.cursor = null;
      store.loading = null;
      store.pendingEvents = [];
      store.pendingLiveEvents = [];
      store.pendingAcknowledgements = [];
      store.stateProjection = null;
      store.viewerState = null;
      store.stateCursor = null;
      store.stateLoading = null;
      store.pendingStateEvents = [];
      store.stateSnapshotLoaded = false;
    }
    for (const store of activeStores) {
      store.emitState({ type: "invalid", reason: "identity" });
      // All previous-actor data and requests were cleared above. Join any read
      // resuming with the new identity instead of queuing a second snapshot.
      if (store.refCount) store.load().catch(() => {});
    }
  }

  function watchInputState(residentId, messageId, listener, options = {}) {
    const targetResidentId = String(residentId || "").trim();
    if (!targetResidentId) throw new Error("resident_id required");
    const targetMessageId = String(messageId || "").trim();
    if (!targetMessageId) throw new Error("message_id required");
    if (typeof listener !== "function") throw new TypeError("listener required");
    let lastSignature = "";
    let active = true;
    const requestActorRevision = actorRevision;
    const emitInputState = inputState => {
      if (!active || !inputState) return;
      const signature = JSON.stringify(inputState);
      if (signature === lastSignature) return;
      lastSignature = signature;
      listener({
        type: "input-state",
        resident_id: targetResidentId,
        message_id: targetMessageId,
        input_state: clone(inputState),
      });
    };
    const subscription = acquire(
      targetResidentId,
      update => {
        if (update?.type === "error") {
          listener({ type: "error", error: update.error });
          return;
        }
        const inputState = (update?.snapshot?.input_states || []).find(
          item => String(item?.message_id || "") === targetMessageId,
        );
        emitInputState(inputState);
      },
      {
        ...options,
        historyLimit: Math.max(1, Number(options.historyLimit || 1)),
      },
    );
    Promise.resolve(
      window.PortalResidentService.inputState(targetResidentId, targetMessageId),
    ).then(inputState => {
      if (requestActorRevision === actorRevision) emitInputState(inputState);
    }).catch(error => {
      if (active && requestActorRevision === actorRevision) listener({ type: "error", error });
    });
    return {
      residentId: subscription.residentId,
      messageId: targetMessageId,
      load: subscription.load,
      destroy() {
        active = false;
        subscription.destroy();
      },
    };
  }

  async function loadSnapshot(residentId, options = {}) {
    const subscription = acquire(residentId, () => {}, { load: false });
    try {
      return await subscription.load(options);
    } finally {
      subscription.destroy();
    }
  }

  window.PortalResidentProtocol = Object.freeze({
    acquire,
    acquireStates,
    clearMailbox,
    mergeMailboxItems,
    splitMailboxItem,
    reorderMailbox,
    loadMailbox,
    requeueMessage,
    loadMessage,
    loadSentPrompt,
    previewPromptInjection,
    cancelInput,
    injectInput,
    loadSnapshot,
    sendInput,
    retrySubmission: (residentId, messageId) => residentStore(residentId).retrySubmission(messageId),
    dismissSubmission: (residentId, messageId) => residentStore(residentId).dismissSubmission(messageId),
    checkSubmissions: residentId => residentStore(residentId).checkSubmissions(),
    respondUserInput: payload => window.PortalResidentService.respondUserInput(payload),
    isTransientSyncError,
    stateView,
    executionGateView,
    stateSnapshot,
    invalidateViewerState,
    watchInputState,
  });
  window.addEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", resetActorState);
})();
