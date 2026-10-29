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

    function applyPaneCollapse() {
      const leftBtn = $("toggleLibraryPaneBtn");
      window.PortalWorkbench.layout.syncCollapseButton(leftBtn, !paperWorkbench?.isPanelVisible("library"), {
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
      if (panelId) paperWorkbench?.togglePanel(panelId);
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

    function setMode(mode) {
      const previousMode = state.mode;
      state.requestedChatWorkspaceId = "";
      if (mode !== "reader") cancelPdfRendering();
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
