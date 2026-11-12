(() => {
  "use strict";

  if (window.PortalPaperLayout) return;

  // The Workbench of the page: its Library, workspace and Chat panes, their
  // collapse, the reader's side panel, and the mode tabs.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "activeChatWorkspaceId", "alertError", "cancelPdfRendering",
    "ensureDetailForMode", "isCollectionMode", "loadChatHistory", "loadLibrary", "paperChatDescriptor",
    "renderWorkspace", "state", "writeUiState",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperLayout is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, cancelPdfRendering, ensureDetailForMode,
      isCollectionMode, loadChatHistory, loadLibrary, paperChatDescriptor, renderWorkspace, state,
      writeUiState,
    } = dependencies;

    let paperWorkbench = null;
    function rememberMainList() {
      const summary = state.library?.summary || {};
      if (!isCollectionMode(state.mode) || summary.semantic_search || summary.paper_collection
          || state.activeSemanticSearchId || state.activeCollectorJobId) return;
      state.mainListReturns ||= {};
      state.mainListReturns[state.leftFace] = {
        principal: state.principalId, mode: state.mode,
        scrollTop: state.listScrollTop ?? $("workspaceMain")?.scrollTop ?? 0,
        selection: {library: state.library, query: state.query, filters: {...state.filters},
          libraryRenderLimit: state.libraryRenderLimit},
      };
    }
    window.addEventListener("portal:paper-main-list-save", rememberMainList);

    function applyPaneCollapse() {
      const leftBtn = $("toggleLibraryPaneBtn");
      window.PortalWorkbench.layout.syncCollapseButton(leftBtn, paperWorkbench?.isPanelCollapsed("library"), {
        expand: "Expand Paper Library",
        collapse: "Collapse Paper Library",
      });
      const chatBtn = $("toggleChatPaneBtn");
      window.PortalWorkbench.layout.syncCollapseButton(chatBtn, !paperWorkbench?.isPanelVisible("chat"), {
        expand: "Expand Research Chat",
        collapse: "Collapse Research Chat",
      });
    }

    function togglePane(which) {
      const panelId = which === "left" ? "library" : which === "chat" ? "chat" : "";
      if (panelId === "library") paperWorkbench?.toggleCollapsed(panelId);
      else if (panelId) paperWorkbench?.togglePanel(panelId);
      applyPaneCollapse();
    }

    function initPaperWorkbench() {
      const { split, stack } = window.PortalWorkbench.model;
      const research = split("horizontal", [stack(["library"]), stack(["workspace"]), stack(["chat"])], [310, 760, 370]);
      const reading = split("horizontal", [stack(["workspace"]), stack(["chat"])], [900, 420]);
      const library = split("horizontal", [stack(["library"]), stack(["workspace"])], [340, 980]);
      paperWorkbench = window.PortalWorkbench.create({
        id: "paper",
        root: "#paperWorkbench",
        label: window.PortalI18n.t("Paper Workbench"),
        initialEditors: [
          { id: "library", type: "authored", presentation: "navigation", options: { source: "#paperLibraryPane" }, title: window.PortalI18n.t("Paper Library"), icon: "book" },
          { id: "workspace", type: "authored", presentation: "document", options: { source: "#paperWorkspacePane" }, title: window.PortalI18n.t("Research Workspace"), minWidth: 420 },
          paperChatDescriptor(),
        ],
        defaultLayout: research,
        presets: [
          { id: "research", name: window.PortalI18n.t("研究"), layout: research },
          { id: "reading", name: window.PortalI18n.t("阅读与讨论"), layout: reading, hidden: ["library"] },
          { id: "library", name: window.PortalI18n.t("论文库"), layout: library, hidden: ["chat"] },
        ],
        defaultPreset: "research",
        onChange: () => {
          applyPaneCollapse();
          if (state.mode === "reader") window.requestAnimationFrame(() => renderWorkspace());
        },
      });
    }

    function toggleReaderPanel(which) {
      if (which === "analysis") {
        state.readerAnalysisCollapsed = !state.readerAnalysisCollapsed;
        if (state.readerAnalysisCollapsed && state.readerPdfCollapsed) state.readerPdfCollapsed = false;
      }
      if (which === "pdf") {
        state.readerPdfCollapsed = !state.readerPdfCollapsed;
        if (state.readerPdfCollapsed && state.readerAnalysisCollapsed) state.readerAnalysisCollapsed = false;
      }
      writeUiState();
      if (state.mode === "reader") renderWorkspace();
    }

    async function setMode(mode, { fromHistory = false, searchId = "" } = {}) {
      if (mode === "main-list" || mode === "history-search") {
        if (!await cancelPdfRendering()) return;
        const saved = state.mainListReturns?.[state.leftFace];
        const main = saved?.principal === state.principalId ? saved : null;
        state.libraryRequestSeq += 1;
        state.detailRequestSeq += 1;
        state.selectedId = ""; state.detail = null; state.ccfSelectedPaper = null;
        state.activeSemanticSearchId = mode === "history-search" ? searchId : "";
        state.activeCollectorJobId = "";
        state.semanticSearch?.setActiveId?.(state.activeSemanticSearchId, {notify:false});
        state.mode = mode === "main-list" ? main?.mode || "library" : "library";
        if (mode === "main-list" && main) Object.assign(state, main.selection, {filters:{...main.selection.filters}});
        else {state.query = ""; state.filters = {};}
        if (mode === "history-search") {
          state.leftFace = "library";
          window.dispatchEvent(new CustomEvent('portal:paper-history-open'));
        }
        if ($("searchInput")) $("searchInput").value = state.query || "";
        if (!fromHistory) {
          const address = new URL(window.location.href);
          for (const key of ["paper", "semantic", "collector"]) address.searchParams.delete(key);
          history.pushState(history.state || {}, "", address.href);
        }
        syncModeTabs(); writeUiState();
        if (state.leftFace !== "ccf" && (mode === "history-search" || !main?.selection.library)) {
          await loadLibrary({keepDetail:false,hydrateDetail:false});
        } else {state.libraryLoading = false; renderWorkspace();}
        requestAnimationFrame(() => {if ($("workspaceMain")) $("workspaceMain").scrollTop = mode === "main-list" ? main?.scrollTop || 0 : 0;});
        return;
      }
      if (mode === "list-return") {
        if (!await cancelPdfRendering()) return;
        state.mode = state.listReturn?.mode || "library";
        if (state.listReturn) Object.assign(state, state.listReturn.selection);
        try {
          const address = new URL(window.location.href);
          address.searchParams.delete("paper");
          if (state.activeSemanticSearchId) address.searchParams.set("semantic", state.activeSemanticSearchId);
          else address.searchParams.delete("semantic");
          history.replaceState(history.state || {}, "", address.href);
        } catch (_) { /* The list is still usable when URL rewriting is unavailable. */ }
        syncModeTabs();
        writeUiState();
        renderWorkspace();
        requestAnimationFrame(() => { if ($("workspaceMain")) $("workspaceMain").scrollTop = state.listReturn?.scrollTop || 0; });
        return;
      }
      const previousMode = state.mode;
      if (isCollectionMode(previousMode) && !isCollectionMode(mode)) rememberMainList();
      if (isCollectionMode(previousMode) && !isCollectionMode(mode)) {
        state.listReturn = {mode: previousMode, scrollTop: state.listScrollTop ?? $("workspaceMain")?.scrollTop ?? 0,
          selection: {library: state.library, query: state.query, filters: {...state.filters},
            activeSemanticSearchId: state.activeSemanticSearchId, activeCollectorJobId: state.activeCollectorJobId,
            libraryRenderLimit: state.libraryRenderLimit}};
        delete state.listScrollTop;
      }
      state.requestedChatWorkspaceId = "";
      if (mode !== "reader" && !await cancelPdfRendering()) return;
      // Explicit navigation leaves a saved-result view; history clicks set their
      // own Library mode and selection without calling this navigation owner.
      if (mode === 'today' || mode === 'library') {
        if (state.activeSemanticSearchId || state.activeCollectorJobId) {
          state.query = ''; state.filters = {};
          const search = $('searchInput'); if (search) search.value = '';
        }
        state.activeSemanticSearchId = ''; state.activeCollectorJobId = '';
        state.semanticSearch?.setActiveId?.('', {notify:false});
        // A search deep link must not reactivate its saved selection on reload.
        try {
          const address = new URL(window.location.href);
          if (address.searchParams.has('semantic')) {
            address.searchParams.delete('semantic');
            history.replaceState(history.state || {}, '', address.href);
          }
        } catch (_) { /* State navigation still works if the host cannot rewrite its URL. */ }
      }
      if (state.leftFace === "ccf") {
        if (mode === "library") {
          state.mode = "library";
          state.ccfSelectedPaper = null;
          writeUiState();
          syncModeTabs();
          renderWorkspace();
          return;
        }
        if (mode === "reader") {
          state.mode = state.ccfSelectedPaper ? "reader" : "library";
          writeUiState();
          syncModeTabs();
          renderWorkspace();
          return;
        }
        return;
      }
      state.mode = mode;
      syncModeTabs();
      writeUiState();
      if (isCollectionMode(mode)) {
        state.libraryLoading = true;
        renderWorkspace();
        loadLibrary({ keepDetail: true }).catch(alertError);
        return;
      }
      state.libraryRequestSeq += 1;
      renderWorkspace();
      ensureDetailForMode(mode).catch(alertError);
      if (state.chatHistoryLoadedFor !== activeChatWorkspaceId()) {
        loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true }).catch(alertError);
      }
    }

    function syncModeTabs() {
      const list = isCollectionMode(state.mode);
      if ($("registeredFilter")) $("registeredFilter").value = state.mode === "today" || (!list && state.listReturn?.mode === "today") ? "today" : "";
      if ($("modeTabs")) { $("modeTabs").hidden = list; $("modeTabs").style.display = list ? "none" : ""; }
      const back = $("returnToPaperList");
      if (back) back.hidden = list && !state.activeSemanticSearchId && !state.activeCollectorJobId;
      const isCcf = state.leftFace === "ccf";
      const activeMode = isCcf && !["library", "reader"].includes(state.mode) ? "library" : state.mode;
      document.querySelectorAll("#modeTabs button[data-mode]").forEach(btn => {
        const mode = btn.dataset.mode || "";
        btn.classList.toggle("hidden", isCcf && !["library", "reader"].includes(mode));
        btn.classList.toggle("active", mode === activeMode);
      });
    }

    return Object.freeze({
      applyPaneCollapse, initPaperWorkbench, setMode, syncModeTabs, togglePane, toggleReaderPanel,
      currentPaperWorkbench: () => paperWorkbench,
    });
  }

  window.PortalPaperLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
