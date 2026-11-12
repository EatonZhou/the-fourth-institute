(() => {
  "use strict";

  if (window.PortalPaperProjection) return;

  // Following Paper projection changes: collecting them, syncing what they
  // touch, and suspending and resuming updates while the page is hidden or
  // left.
  const DEPENDENCIES = Object.freeze([
    "alertError", "assetsJobRunning", "cancelPdfRendering", "ccfPaperEnrichment", "ensureCcfPaperEnrichment",
    "loadCollectorJobs", "loadDetailSections", "refreshAssetsProjection", "refreshDeepReadProjection",
    "refreshExpProjection", "refreshFigureProjection", "resumePaperRegistrationPolling", "resumeReviewPolling", "state",
  ]);

  function createProjection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperProjection is missing: ${missing.join(", ")}`);
    const {
      alertError, assetsJobRunning, cancelPdfRendering, ccfPaperEnrichment, ensureCcfPaperEnrichment,
      loadCollectorJobs, loadDetailSections, refreshAssetsProjection, refreshDeepReadProjection,
      refreshExpProjection, refreshFigureProjection, resumePaperRegistrationPolling, resumeReviewPolling, state,
    } = dependencies;

    let paperProjectionSyncTimer = 0;
    let paperProjectionSyncRunning = false;
    let paperProjectionEpoch = 0;
    let paperProjectionSnapshotPending = false;
    let paperProjectionEventsBound = false;
    let pendingPaperProjectionChanges = new Map();
    let paperProjectionRetryCount = 0;
    let paperProjectionRetryStopped = false;

    function mergePaperProjectionChanges(changes) {
      if (!changes || typeof changes !== "object") return;
      const entries = changes instanceof Map ? changes : Object.entries(changes);
      for (const [targetId, fields] of entries) {
        const target = String(targetId || "").trim();
        if (!target) continue;
        const pending = pendingPaperProjectionChanges.get(target) || new Set();
        (Array.isArray(fields) || fields instanceof Set ? fields : []).forEach(field => {
          const clean = String(field || "").trim();
          if (clean) pending.add(clean);
        });
        pendingPaperProjectionChanges.set(target, pending);
      }
    }

    function queuePaperProjectionSync(delay) {
      if (state.leaving || document.hidden || paperProjectionRetryStopped) return;
      const epoch = paperProjectionEpoch;
      window.clearTimeout(paperProjectionSyncTimer);
      const timer = window.setTimeout(() => {
        if (epoch !== paperProjectionEpoch || paperProjectionSyncTimer !== timer) return;
        paperProjectionSyncTimer = 0;
        flushPaperProjectionSync().catch(alertError);
      }, delay);
      paperProjectionSyncTimer = timer;
    }

    function schedulePaperProjectionSync({ snapshot = false, changes = null } = {}) {
      if (snapshot) paperProjectionSnapshotPending = true;
      mergePaperProjectionChanges(changes);
      if (paperProjectionSyncRunning || (paperProjectionSyncTimer && paperProjectionRetryCount)) return;
      queuePaperProjectionSync(100);
    }

    async function syncPaperProjection(snapshot, changes, { retry = false } = {}) {
      const epoch = paperProjectionEpoch;
      const current = () => !state.leaving && !document.hidden && epoch === paperProjectionEpoch;
      if (!current()) return;
      const fields = new Set();
      changes.forEach(items => items.forEach(field => fields.add(field)));
      const selectedId = String(state.selectedId || "");
      const selectedChanged = !!selectedId && (snapshot || changes.has(selectedId));

      if (snapshot || fields.has("collector")) {
        await loadCollectorJobs({ render: true });
        if (!current()) return;
      }

      if (selectedChanged) {
        const selectedFields = changes.get(selectedId) || new Set();
        const refreshBase = snapshot || [...selectedFields].some(field => !["deep_read", "exp", "figures"].includes(field));
        if (refreshBase) await refreshAssetsProjection(selectedId);
        if (!current()) return;
        const deepReadArtifactsChanged = selectedFields.has("analysis") || selectedFields.has("summary");
        if (snapshot || selectedFields.has("deep_read") || deepReadArtifactsChanged) {
          await refreshDeepReadProjection({ snapshot, artifactsChanged: deepReadArtifactsChanged });
          if (!current()) return;
        }
        if (snapshot || selectedFields.has("exp")) {
          await refreshExpProjection({ snapshot });
          if (!current()) return;
        }
        if (snapshot || selectedFields.has("figures")) {
          await refreshFigureProjection();
          if (!current()) return;
        }
        if (selectedFields.has("review") && state.mode === "review") {
          await loadDetailSections(selectedId, ["reviews"], { render: true, force: true });
          if (!current()) return;
        }
        if (
          selectedFields.has("run:assets")
          && !assetsJobRunning()
          && ["refs", "figures"].includes(state.mode)
        ) {
          await loadDetailSections(selectedId, [state.mode], { render: true, force: true });
          if (!current()) return;
        }
      }

      // The existing CCF observer uses a business POST. Recovery must not replay it.
      if (!snapshot && !retry && fields.has("ccf_enrichment")) {
        const selectedPaper = state.ccfSelectedPaper;
        if (selectedPaper) {
          const enrichment = ccfPaperEnrichment(selectedPaper);
          await ensureCcfPaperEnrichment(selectedPaper, {
            refresh: true,
            web: String(enrichment?.status || "") === "web_pending",
          });
        }
      }
    }

    async function flushPaperProjectionSync() {
      if (paperProjectionSyncRunning || paperProjectionRetryStopped || state.leaving || document.hidden) return;
      paperProjectionSyncRunning = true;
      const epoch = paperProjectionEpoch;
      const snapshot = paperProjectionSnapshotPending;
      const changes = pendingPaperProjectionChanges;
      paperProjectionSnapshotPending = false;
      pendingPaperProjectionChanges = new Map();
      const retain = () => {
        paperProjectionSnapshotPending ||= snapshot;
        mergePaperProjectionChanges(changes);
      };
      try {
        await syncPaperProjection(snapshot, changes, { retry: paperProjectionRetryCount > 0 });
        if (epoch !== paperProjectionEpoch || state.leaving || document.hidden) retain();
        else paperProjectionRetryCount = 0;
      } catch (error) {
        retain();
        if (epoch === paperProjectionEpoch && !state.leaving && !document.hidden) {
          // Read-only retries: 1s, 2s, 4s, then retain the batch without further automatic attempts.
          paperProjectionRetryCount += 1;
          paperProjectionRetryStopped = [401, 403, 404].includes(Number(error?.status))
            || paperProjectionRetryCount > 3;
        }
        throw error;
      } finally {
        paperProjectionSyncRunning = false;
        if (paperProjectionSnapshotPending || pendingPaperProjectionChanges.size) {
          queuePaperProjectionSync(paperProjectionRetryCount
            ? Math.min(1000 * 2 ** (paperProjectionRetryCount - 1), 4000) : 100);
        }
      }
    }

    function bindPaperProjectionEvents() {
      if (paperProjectionEventsBound) return;
      paperProjectionEventsBound = true;
      window.addEventListener("portal:change", event => {
        const changes = event?.detail?.projection_changes?.paper;
        if (changes && typeof changes === "object") {
          schedulePaperProjectionSync({ changes });
        }
      });
      window.addEventListener("portal:events-ready", event => {
        if (event?.detail?.snapshot_required) {
          schedulePaperProjectionSync({ snapshot: true });
        }
      });
    }

    async function suspendPaperUpdates({ includeDeferred = true, includePdf = true } = {}) {
      if (includePdf && !await cancelPdfRendering()) return false;
      paperProjectionEpoch += 1;
      state.paperPollEpoch = (state.paperPollEpoch || 0) + 1;
      clearTimeout(state.registrationDraftPollTimer);
      state.registrationDraftPollTimer = 0;
      clearTimeout(state.reviewPollTimer);
      state.reviewPollTimer = 0;
      window.clearTimeout(paperProjectionSyncTimer);
      paperProjectionSyncTimer = 0;
      clearTimeout(state.pdfResizeTimer);
      state.pdfResizeTimer = null;
      if (includeDeferred) {
        clearTimeout(state.searchTimer);
        state.searchTimer = null;
        clearTimeout(state.ccfFilterTimer);
        state.ccfFilterTimer = null;
      }
      return true;
    }

    function resumePaperUpdates() {
      if (state.leaving || document.hidden) return;
      paperProjectionEpoch += 1;
      paperProjectionRetryCount = 0;
      paperProjectionRetryStopped = false;
      schedulePaperProjectionSync({ snapshot: true });
      resumePaperRegistrationPolling();
      resumeReviewPolling();
    }

    return Object.freeze({
      bindPaperProjectionEvents, resumePaperUpdates, suspendPaperUpdates,
    });
  }

  window.PortalPaperProjection = Object.freeze({ createProjection, dependencies: DEPENDENCIES });
})();
