(() => {
  "use strict";

  if (window.PortalPaperLocation) return;

  // Where the page is: the state kept in page state (mode, face, filters,
  // pane sizes) and in the URL (the Paper, semantic search and Chat asked
  // for), and going back to the previous Paper workspace with its shortcut.
  const DEPENDENCIES = Object.freeze([
    "activeChatPaperId", "activeChatWorkspaceId", "isCollectionMode", "loadCcfPapers", "loadCcfVenues",
    "loadCcfYears", "loadChatHistory", "loadDetail", "loadLibrary", "paperPageState", "renderTree",
    "renderWorkspace", "state", "syncModeTabs", "updateLeftFaceTabs",
  ]);

  function createLocation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperLocation is missing: ${missing.join(", ")}`);
    const {
      activeChatPaperId, activeChatWorkspaceId, isCollectionMode, loadCcfPapers, loadCcfVenues, loadCcfYears,
      loadChatHistory, loadDetail, loadLibrary, paperPageState, renderTree, renderWorkspace, state,
      syncModeTabs, updateLeftFaceTabs,
    } = dependencies;

    function readUiState() {
      const saved = paperPageState.read();
      state.mode = saved.mode || state.mode;
      state.selectedId = saved.selectedId || state.selectedId;
      state.leftFace = saved.leftFace === "ccf" ? "ccf" : "library";
      state.previousPaperWorkspace = saved.previousPaperWorkspace && typeof saved.previousPaperWorkspace === "object"
        ? saved.previousPaperWorkspace
        : null;
      state.ccfExpandedVenue = saved.ccfExpandedVenue || "";
      state.ccfSelectedVenue = saved.ccfSelectedVenue || "";
      state.ccfSelectedYear = saved.ccfSelectedYear || "";
      state.ccfYearFilter = saved.ccfYearFilter || "";
      state.ccfCollapsedRanks = saved.ccfCollapsedRanks && typeof saved.ccfCollapsedRanks === "object"
        ? saved.ccfCollapsedRanks
        : {};
      state.ccfCollapsedCategories = saved.ccfCollapsedCategories && typeof saved.ccfCollapsedCategories === "object"
        ? saved.ccfCollapsedCategories
        : {};
      state.query = saved.query || "";
      state.filters = { ...state.filters, ...(saved.filters || {}) };
      delete state.filters.analysis;
      state.activeSemanticSearchId = saved.activeSemanticSearchId || "";
      state.activeCollectorJobId = saved.activeCollectorJobId || "";
      state.readerAnalysisCollapsed = !!saved.readerAnalysisCollapsed;
      state.readerPdfCollapsed = !!saved.readerPdfCollapsed;
      if (state.readerAnalysisCollapsed && state.readerPdfCollapsed) state.readerPdfCollapsed = false;
    }

    function requestedPaperIdFromUrl() {
      try {
        const params = new URLSearchParams(window.location.search || "");
        return String(params.get("paper") || "").trim();
      } catch {
        return "";
      }
    }

    // The address names the paper being read: a reload opens the URL's
    // paper first, so it follows each paper the reader switches to.
    function syncPaperLocation(id) {
      const paperId = String(id || "").trim();
      if (!paperId) return;
      try {
        const target = new URL(window.location.href);
        if (target.searchParams.get("paper") === paperId) return;
        target.searchParams.set("paper", paperId);
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      } catch {
        // An address that cannot be rewritten keeps the reader working.
      }
    }

    function requestedSemanticSearchIdFromUrl() {
      try {
        const params = new URLSearchParams(window.location.search || "");
        return String(params.get("semantic") || "").trim();
      } catch {
        return "";
      }
    }

    function requestedChatWorkspaceIdFromUrl() {
      try {
        const params = new URLSearchParams(window.location.search || "");
        return String(params.get("chat_workspace") || "").trim();
      } catch {
        return "";
      }
    }

    function requestedChatOpenFromUrl() {
      const value = String(new URLSearchParams(window.location.search || "").get("chat") || "").toLowerCase();
      return ["1", "true", "open"].includes(value);
    }

    function writeUiState(patch = {}) {
      paperPageState.write({
        mode: state.mode,
        selectedId: state.selectedId,
        leftFace: state.leftFace,
        previousPaperWorkspace: state.previousPaperWorkspace,
        ccfExpandedVenue: state.ccfExpandedVenue,
        ccfSelectedVenue: state.ccfSelectedVenue,
        ccfSelectedYear: state.ccfSelectedYear,
        ccfYearFilter: state.ccfYearFilter,
        ccfCollapsedRanks: state.ccfCollapsedRanks,
        ccfCollapsedCategories: state.ccfCollapsedCategories,
        query: state.query,
        filters: state.filters,
        activeSemanticSearchId: state.activeSemanticSearchId,
        activeCollectorJobId: state.activeCollectorJobId,
        readerAnalysisCollapsed: state.readerAnalysisCollapsed,
        readerPdfCollapsed: state.readerPdfCollapsed,
        ...patch,
      });
    }

    function currentPaperWorkspaceSnapshot(overrides = {}) {
      return {
        leftFace: state.leftFace === "ccf" ? "ccf" : "library",
        mode: state.mode || "library",
        selectedId: String(state.selectedId || ""),
        query: String(state.query || ""),
        filters: { ...(state.filters || {}) },
        activeSemanticSearchId: String(state.activeSemanticSearchId || ""),
        activeCollectorJobId: String(state.activeCollectorJobId || ""),
        ccfExpandedVenue: String(state.ccfExpandedVenue || ""),
        ccfSelectedVenue: String(state.ccfSelectedVenue || ""),
        ccfSelectedYear: String(state.ccfSelectedYear || ""),
        ...overrides,
      };
    }

    function normalizePaperWorkspaceSnapshot(snapshot) {
      if (!snapshot || typeof snapshot !== "object") return null;
      const leftFace = snapshot.leftFace === "ccf" ? "ccf" : "library";
      return {
        leftFace,
        mode: String(snapshot.mode || "library"),
        selectedId: String(snapshot.selectedId || ""),
        query: String(snapshot.query || ""),
        filters: { ...(snapshot.filters && typeof snapshot.filters === "object" ? snapshot.filters : {}) },
        activeSemanticSearchId: String(snapshot.activeSemanticSearchId || ""),
        activeCollectorJobId: String(snapshot.activeCollectorJobId || ""),
        ccfExpandedVenue: String(snapshot.ccfExpandedVenue || ""),
        ccfSelectedVenue: String(snapshot.ccfSelectedVenue || ""),
        ccfSelectedYear: String(snapshot.ccfSelectedYear || ""),
      };
    }

    function paperWorkspaceSnapshotKey(snapshot) {
      const item = normalizePaperWorkspaceSnapshot(snapshot);
      if (!item) return "";
      return JSON.stringify({
        leftFace: item.leftFace,
        mode: item.mode,
        selectedId: item.selectedId,
        query: item.query,
        filters: item.filters,
        activeSemanticSearchId: item.activeSemanticSearchId,
        activeCollectorJobId: item.activeCollectorJobId,
        ccfExpandedVenue: item.ccfExpandedVenue,
        ccfSelectedVenue: item.ccfSelectedVenue,
        ccfSelectedYear: item.ccfSelectedYear,
      });
    }

    function rememberPreviousPaperWorkspace(nextSnapshot = null) {
      const current = currentPaperWorkspaceSnapshot();
      const next = normalizePaperWorkspaceSnapshot(nextSnapshot);
      if (next && paperWorkspaceSnapshotKey(current) === paperWorkspaceSnapshotKey(next)) return;
      state.previousPaperWorkspace = current;
      writeUiState();
    }

    function isPreviousPaperWorkspaceShortcut(event) {
      if (event.defaultPrevented || event.altKey) return false;
      const withModifier = event.ctrlKey || event.metaKey;
      if (!withModifier || event.shiftKey) return false;
      return event.key === "Tab" || event.key === "`" || event.code === "Backquote";
    }

    function canRestorePaperWorkspace(snapshot) {
      const item = normalizePaperWorkspaceSnapshot(snapshot);
      if (!item) return false;
      if (paperWorkspaceSnapshotKey(item) === paperWorkspaceSnapshotKey(currentPaperWorkspaceSnapshot())) return false;
      if (item.leftFace === "library" && !isCollectionMode(item.mode) && !item.selectedId) return false;
      if (item.leftFace === "ccf" && item.mode !== "library" && (!item.ccfSelectedVenue || !item.ccfSelectedYear)) return false;
      return true;
    }

    async function restorePaperWorkspace(snapshot) {
      const target = normalizePaperWorkspaceSnapshot(snapshot);
      if (!target) return false;
      const current = currentPaperWorkspaceSnapshot();
      state.previousPaperWorkspace = current;
      state.leftFace = target.leftFace;
      state.mode = target.leftFace === "ccf"
        ? (target.mode === "reader" ? "reader" : "library")
        : (target.mode || "library");
      state.selectedId = target.selectedId;
      state.query = target.query;
      state.filters = { year: "", venue: "", tag: "", project: "", ...target.filters };
      delete state.filters.analysis;
      state.activeSemanticSearchId = target.activeSemanticSearchId;
      state.semanticSearch?.setActiveId?.(state.activeSemanticSearchId, { notify: false });
      state.activeCollectorJobId = target.activeCollectorJobId;
      state.ccfExpandedVenue = target.ccfExpandedVenue;
      state.ccfSelectedVenue = target.ccfSelectedVenue;
      state.ccfSelectedYear = target.ccfSelectedYear;
      state.ccfSelectedPaper = null;
      state.detail = null;
      updateLeftFaceTabs();
      syncModeTabs();
      writeUiState();
      if (state.leftFace === "ccf") {
        await loadCcfVenues();
        if (state.ccfExpandedVenue) await loadCcfYears(state.ccfExpandedVenue);
        if (state.ccfSelectedVenue && state.ccfSelectedYear) await loadCcfPapers(state.ccfSelectedVenue, state.ccfSelectedYear);
        renderTree();
        renderWorkspace();
        await loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true });
        return true;
      }
      await loadLibrary({ keepDetail: true });
      if (state.selectedId && !isCollectionMode()) await loadDetail(state.selectedId, { render: true });
      return true;
    }

    async function goPreviousPaperWorkspace() {
      const previous = state.previousPaperWorkspace;
      if (!canRestorePaperWorkspace(previous)) return false;
      return restorePaperWorkspace(previous);
    }

    return Object.freeze({
      canRestorePaperWorkspace, currentPaperWorkspaceSnapshot, goPreviousPaperWorkspace,
      isPreviousPaperWorkspaceShortcut, readUiState, rememberPreviousPaperWorkspace, requestedChatOpenFromUrl,
      requestedChatWorkspaceIdFromUrl, requestedPaperIdFromUrl, requestedSemanticSearchIdFromUrl,
      syncPaperLocation, writeUiState,
    });
  }

  window.PortalPaperLocation = Object.freeze({ createLocation, dependencies: DEPENDENCIES });
})();
