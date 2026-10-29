(() => {
  "use strict";

  if (window.PortalPaperProjection) return;

  // Following Paper projection changes: collecting them, syncing what they
  // touch, and suspending and resuming updates while the page is hidden or
  // left.
  const DEPENDENCIES = Object.freeze([
    "alertError", "assetsJobRunning", "cancelPdfRendering", "ccfPaperEnrichment", "ensureCcfPaperEnrichment",
    "loadCollectorJobs", "loadDetailSections", "refreshAssetsProjection", "refreshDeepReadProjection",
    "refreshExpProjection", "refreshFigureProjection", "state",
  ]);

  function createProjection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperProjection is missing: ${missing.join(", ")}`);
    const {
      alertError, assetsJobRunning, cancelPdfRendering, ccfPaperEnrichment, ensureCcfPaperEnrichment,
      loadCollectorJobs, loadDetailSections, refreshAssetsProjection, refreshDeepReadProjection,
      refreshExpProjection, refreshFigureProjection, state,
    } = dependencies;

    let paperProjectionSyncTimer = 0;
    let paperProjectionSyncRunning = false;
    let paperProjectionSnapshotPending = false;
    let paperProjectionEventsBound = false;
    let pendingPaperProjectionChanges = new Map();

    function mergePaperProjectionChanges(changes) {
      if (!changes || typeof changes !== "object") return;
      Object.entries(changes).forEach(([targetId, fields]) => {
        const target = String(targetId || "").trim();
        if (!target) return;
        const pending = pendingPaperProjectionChanges.get(target) || new Set();
        (Array.isArray(fields) ? fields : []).forEach(field => {
          const clean = String(field || "").trim();
          if (clean) pending.add(clean);
        });
        pendingPaperProjectionChanges.set(target, pending);
      });
    }

    function schedulePaperProjectionSync({ snapshot = false, changes = null } = {}) {
      if (snapshot) paperProjectionSnapshotPending = true;
      mergePaperProjectionChanges(changes);
      window.clearTimeout(paperProjectionSyncTimer);
      paperProjectionSyncTimer = 0;
      if (state.leaving || document.hidden) return;
      paperProjectionSyncTimer = window.setTimeout(() => {
        paperProjectionSyncTimer = 0;
        flushPaperProjectionSync().catch(alertError);
      }, 100);
    }

    async function syncPaperProjection(snapshot, changes) {
      const fields = new Set();
      changes.forEach(items => items.forEach(field => fields.add(field)));
      const selectedId = String(state.selectedId || "");
      const selectedChanged = !!selectedId && (snapshot || changes.has(selectedId));

      if (snapshot || fields.has("collector")) {
        await loadCollectorJobs({ render: true });
      }

      if (selectedChanged) {
        const selectedFields = changes.get(selectedId) || new Set();
        const refreshBase = snapshot || [...selectedFields].some(field => !["deep_read", "exp", "figures"].includes(field));
        if (refreshBase) await refreshAssetsProjection(selectedId);
        if (snapshot || selectedFields.has("deep_read")) {
          await refreshDeepReadProjection();
        }
        if (snapshot || selectedFields.has("exp")) {
          await refreshExpProjection({ snapshot });
        }
        if (snapshot || selectedFields.has("figures")) {
          await refreshFigureProjection();
        }
        if (selectedFields.has("review") && state.mode === "review") {
          await loadDetailSections(selectedId, ["reviews"], { render: true, force: true });
        }
        if (
          selectedFields.has("run:assets")
          && !assetsJobRunning()
          && ["refs", "figures"].includes(state.mode)
        ) {
          await loadDetailSections(selectedId, [state.mode], { render: true, force: true });
        }
      }

      if (snapshot || fields.has("ccf_enrichment")) {
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
      if (paperProjectionSyncRunning) return;
      paperProjectionSyncRunning = true;
      const snapshot = paperProjectionSnapshotPending;
      const changes = pendingPaperProjectionChanges;
      paperProjectionSnapshotPending = false;
      pendingPaperProjectionChanges = new Map();
      try {
        await syncPaperProjection(snapshot, changes);
      } finally {
        paperProjectionSyncRunning = false;
        if (paperProjectionSnapshotPending || pendingPaperProjectionChanges.size) {
          schedulePaperProjectionSync();
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

    function suspendPaperUpdates({ includeDeferred = true, includePdf = true } = {}) {
      if (includePdf) cancelPdfRendering();
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
    }

    function resumePaperUpdates() {
      schedulePaperProjectionSync({ snapshot: true });
    }

    return Object.freeze({
      bindPaperProjectionEvents, resumePaperUpdates, suspendPaperUpdates,
    });
  }

  window.PortalPaperProjection = Object.freeze({ createProjection, dependencies: DEPENDENCIES });
})();
