(() => {
  "use strict";

  if (window.PortalPaperDetail) return;

  // A Paper's detail: the sections each mode needs, loading and merging them,
  // and the assets job that extracts its figures and references.
  const DEPENDENCIES = Object.freeze([
    "alertError", "isCollectionMode", "loadChatHistory", "loadDeepReadStatus", "loadExpStatus",
    "loadReviewStatus", "paperApi", "renderAssistantContext", "renderWorkspace", "state", "syncPaperLocation",
    "syncSelectedPaperInTree", "updateDeepReadControls", "writeUiState",
  ]);

  function createDetail(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperDetail is missing: ${missing.join(", ")}`);
    const {
      alertError, isCollectionMode, loadChatHistory, loadDeepReadStatus, loadExpStatus, loadReviewStatus,
      paperApi, renderAssistantContext, renderWorkspace, state, syncPaperLocation, syncSelectedPaperInTree,
      updateDeepReadControls, writeUiState,
    } = dependencies;

    const DETAIL_SECTIONS_BY_MODE = {
      reader: ["analysis", "summary"],
      review: ["reviews"],
      exp: ["exp"],
      figures: ["figures"],
      notes: ["notes"],
      refs: ["refs"],
    };

    function mergeDetailPayload(payload, { replace = false } = {}) {
      if (!payload?.paper) return state.detail;
      const previousId = String(state.detail?.paper?.id || "");
      const nextId = String(payload.paper.id || "");
      state.detail = replace || previousId !== nextId ? payload : { ...state.detail, ...payload, paper: payload.paper };
      if (replace || previousId !== nextId) {
        state.detailSections = new Set();
        state.detailSectionPromises = {};
      }
      (payload.loaded_sections || []).forEach(section => state.detailSections.add(String(section)));
      return state.detail;
    }

    function detailSectionsLoaded(sections) {
      return (sections || []).every(section => state.detailSections.has(section));
    }

    async function loadDetailSections(id, sections, { render = true, force = false } = {}) {
      const paperId = String(id || "").trim();
      const requested = [...new Set((sections || []).map(String).filter(Boolean))];
      const missing = force ? requested : requested.filter(section => !state.detailSections.has(section));
      if (!paperId || !missing.length) return state.detail;
      const key = `${paperId}:${missing.slice().sort().join(",")}`;
      if (state.detailSectionPromises[key]) return state.detailSectionPromises[key];
      const promise = paperApi.request(`/paper/api/papers/detail?id=${encodeURIComponent(paperId)}&sections=${encodeURIComponent(missing.join(","))}`)
        .then(payload => {
          if (String(state.selectedId || "") !== paperId) return null;
          mergeDetailPayload(payload);
          if (render) renderWorkspace();
          return state.detail;
        })
        .finally(() => { delete state.detailSectionPromises[key]; });
      state.detailSectionPromises[key] = promise;
      return promise;
    }

    async function ensureDetailForMode(mode = state.mode) {
      const sections = DETAIL_SECTIONS_BY_MODE[mode] || [];
      const detail = await loadDetailSections(state.selectedId, sections, { render: true });
      if (["reader", "review", "exp", "figures", "notes"].includes(mode)) {
        window.MD.ensureKatex().then(() => {
          if (state.mode === mode) renderWorkspace();
        }).catch(alertError);
      }
      return detail;
    }

    async function loadDetail(id, { render = true } = {}) {
      state.requestedChatWorkspaceId = "";
      const requestSeq = ++state.detailRequestSeq;
      const previousSelectedId = String(state.selectedId || "");
      if (previousSelectedId !== String(id || "")) state.selectedFigureId = "";
      state.selectedId = id;
      if (previousSelectedId !== String(id || "")) {
        clearTimeout(state.reviewPollTimer);
        state.reviewPollTimer = 0;
        state.reviewJob = null;
        state.reviewInFlight = false;
        state.reviewPollError = "";
      }
      const payload = await paperApi.request(`/paper/api/papers/detail?id=${encodeURIComponent(id)}&sections=base`);
      if (requestSeq !== state.detailRequestSeq) return null;
      mergeDetailPayload(payload, { replace: true });
      state.selectedId = state.detail?.paper?.id || id;
      writeUiState();
      syncPaperLocation(state.selectedId);
      syncSelectedPaperInTree(previousSelectedId, state.selectedId);
      renderAssistantContext();
      if (render) renderWorkspace();
      const initialSections = ["analysis", "summary", ...(DETAIL_SECTIONS_BY_MODE[state.mode] || [])];
      Promise.allSettled([
        loadDetailSections(state.selectedId, initialSections, { render: true }),
        loadDeepReadStatus(state.selectedId, { render: false }),
        loadExpStatus(state.selectedId, { render: false }),
        loadReviewStatus(state.selectedId),
        isCollectionMode()
          ? Promise.resolve(null)
          : loadChatHistory({ paperId: state.selectedId, render: true }),
      ]).then(() => {
        if (requestSeq !== state.detailRequestSeq) return;
        updateDeepReadControls();
        if (state.mode === "exp") renderWorkspace();
      });
      return state.detail;
    }

    function currentAssetsJob() {
      return state.detail?.assets_job && typeof state.detail.assets_job === "object" ? state.detail.assets_job : {};
    }

    function assetsJobRunning(job = currentAssetsJob()) {
      return ["queued", "running"].includes(String(job?.status || ""));
    }

    function assetsJobStatusText(job = currentAssetsJob()) {
      const status = String(job?.status || "");
      if (status === "queued") return window.PortalI18n.t("Automatic refs and figures extraction is queued.");
      if (status === "running") return window.PortalI18n.t("Automatic refs and figures extraction is running.");
      if (status === "done") {
        const refs = Number(job.refs ?? 0);
        const figures = Number(job.figures ?? 0);
        return window.PortalI18n.text`Automatic extraction finished: ${refs} refs, ${figures} figures.`;
      }
      if (status === "skipped") return window.PortalI18n.text`Automatic extraction skipped: ${job.reason || window.PortalI18n.t("local PDF unavailable")}.`;
      if (status === "error") return window.PortalI18n.text`Automatic extraction failed: ${job.error || (job.errors || []).join("; ") || window.PortalI18n.t("unknown error")}.`;
      return "";
    }

    async function refreshAssetsProjection(paperId) {
      const id = String(paperId || "").trim();
      if (!id || String(state.selectedId || "") !== id) return;
      const detail = await paperApi.request(`/paper/api/papers/detail?id=${encodeURIComponent(id)}&sections=base`);
      if (String(state.selectedId || "") !== id) return;
      mergeDetailPayload(detail);
      renderAssistantContext();
      const shouldRenderWorkspace = state.mode === "refs" || state.mode === "figures";
      if (shouldRenderWorkspace) renderWorkspace();
      return detail;
    }

    return Object.freeze({
      assetsJobRunning, assetsJobStatusText, currentAssetsJob, detailSectionsLoaded, ensureDetailForMode,
      loadDetail, loadDetailSections, mergeDetailPayload, refreshAssetsProjection,
    });
  }

  window.PortalPaperDetail = Object.freeze({ createDetail, dependencies: DEPENDENCIES });
})();
