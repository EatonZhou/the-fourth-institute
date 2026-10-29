(() => {
  "use strict";

  if (window.PortalArticleWorkspaceEvents) return;

  // The page's event wiring, bound when the part is created: the popovers,
  // delegated clicks, the header, registration, file, settings, editor,
  // compile and PDF controls, Portal change events, leaving the page and
  // browser history navigation.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "articleEntryDialog", "articleGitRefresh", "articleRegisterDialog",
    "askBuildDiagnostic", "bindArticlePdfGestures", "bringCurrentContextToChat", "captureActiveDocument",
    "closeArticleGitDiff", "commitArticleGit", "compileArticle", "createEntry",
    "currentArticleCompilePopover", "currentArticleProjectPopover", "currentArticleWorkbench",
    "detailHistory", "flushArticleNavigationPreference", "goToLine", "handleEditorPairing",
    "hasDirtyDocuments", "initializeArticleGit", "initializeArticlePopovers", "inspectRegistration",
    "loadArticleGit", "openArticleFile", "openArticleGitCommit", "openArticleGitDiff", "openBuildDiagnostic",
    "openEntryModal", "openRegisterModal", "queryState", "registerArticle", "renderArticleGitDiffFile",
    "renderArticleResourceSearch", "renderArticleSelectors", "renderEditorLayers", "renderLog",
    "renderSemanticNavigation", "resetEditorPreferences", "saveOpenFile", "saveSettings",
    "scheduleArticleGitRefresh", "scheduleAutoCompile", "searchProject", "selectArticle", "setArticlePdfZoom",
    "setArticleResourceSearchOpen", "setCompileMenu", "setEditorMode", "setNavigationPanelVisible",
    "setOutputView", "setProjectMenu", "setSettingsSection", "setSideView", "showPdfError", "showToast",
    "state", "stopCompilation", "syncEditorScroll", "syncPdfToSource", "syncSourceToPdf",
    "toggleArticleGitDiffLayout", "toggleWordWrap", "unregisterCurrentArticle", "updateArticleGitStage",
    "updateCompilePreference", "updateCursorStatus", "updateDirtyState", "updateEditorCodeCheck",
    "updateEditorPreference", "updateSemanticUrl", "uploadFile",
  ]);

  function createEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceEvents is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleEntryDialog, articleGitRefresh, articleRegisterDialog, askBuildDiagnostic,
      bindArticlePdfGestures, bringCurrentContextToChat, captureActiveDocument, closeArticleGitDiff,
      commitArticleGit, compileArticle, createEntry, currentArticleCompilePopover,
      currentArticleProjectPopover, currentArticleWorkbench, detailHistory, flushArticleNavigationPreference,
      goToLine, handleEditorPairing, hasDirtyDocuments, initializeArticleGit, initializeArticlePopovers,
      inspectRegistration, loadArticleGit, openArticleFile, openArticleGitCommit, openArticleGitDiff,
      openBuildDiagnostic, openEntryModal, openRegisterModal, queryState, registerArticle,
      renderArticleGitDiffFile, renderArticleResourceSearch, renderArticleSelectors, renderEditorLayers,
      renderLog, renderSemanticNavigation, resetEditorPreferences, saveOpenFile, saveSettings,
      scheduleArticleGitRefresh, scheduleAutoCompile, searchProject, selectArticle, setArticlePdfZoom,
      setArticleResourceSearchOpen, setCompileMenu, setEditorMode, setNavigationPanelVisible, setOutputView,
      setProjectMenu, setSettingsSection, setSideView, showPdfError, showToast, state, stopCompilation,
      syncEditorScroll, syncPdfToSource, syncSourceToPdf, toggleArticleGitDiffLayout, toggleWordWrap,
      unregisterCurrentArticle, updateArticleGitStage, updateCompilePreference, updateCursorStatus,
      updateDirtyState, updateEditorCodeCheck, updateEditorPreference, updateSemanticUrl, uploadFile,
    } = dependencies;

    initializeArticlePopovers();

    document.addEventListener("click", event => {
      const view = event.target.closest("[data-article-view]");
      if (view) {
        if (view.classList.contains("active") && currentArticleWorkbench()?.isPanelVisible("navigation")) setNavigationPanelVisible(false);
        else setSideView(view.dataset.articleView);
        return;
      }
      if (event.target.closest("[data-side-close]")) {
        setNavigationPanelVisible(false);
        return;
      }
      const gitStage = event.target.closest("[data-git-stage]");
      if (gitStage) {
        updateArticleGitStage(gitStage.dataset.gitStage, gitStage.dataset.gitStaged === "true")
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      if (event.target.closest("[data-git-init]")) {
        initializeArticleGit().catch(error => showToast(error.message || error, "error"));
        return;
      }
      const gitDiff = event.target.closest("[data-git-diff]");
      if (gitDiff) {
        openArticleGitDiff(gitDiff.dataset.gitDiff)
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const gitCommit = event.target.closest("[data-git-commit]");
      if (gitCommit) {
        openArticleGitCommit(gitCommit.dataset.gitCommit, gitCommit.dataset.gitCommitShort, gitCommit.dataset.gitCommitSubject)
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const gitDiffFile = event.target.closest("[data-git-diff-file]");
      if (gitDiffFile) {
        renderArticleGitDiffFile(Number(gitDiffFile.dataset.gitDiffFile));
        return;
      }
      const headerSelect = event.target.closest("[data-article-header-select]");
      if (headerSelect) {
        setArticleResourceSearchOpen(false);
        selectArticle(headerSelect.dataset.articleHeaderSelect)
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const select = event.target.closest("[data-article-select]");
      if (select) {
        selectArticle(select.dataset.articleSelect).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const semanticView = event.target.closest("[data-semantic-view]");
      if (semanticView) {
        state.semanticView = semanticView.dataset.semanticView || "outline";
        renderSemanticNavigation();
        return;
      }
      const editorLocation = event.target.closest("[data-editor-path]");
      if (editorLocation) {
        openArticleFile(editorLocation.dataset.editorPath, {
          line: Number(editorLocation.dataset.editorLine || 1),
          column: Number(editorLocation.dataset.editorColumn || 1),
        });
        return;
      }
      const lineButton = event.target.closest("[data-editor-line]");
      if (lineButton) {
        goToLine(Number(lineButton.dataset.editorLine || 1));
        return;
      }
      const searchGroup = event.target.closest("[data-search-group]");
      if (searchGroup) {
        const path = searchGroup.dataset.searchGroup;
        const section = searchGroup.closest(".articleSearchGroup");
        const collapsed = !section.classList.contains("collapsed");
        section.classList.toggle("collapsed", collapsed);
        searchGroup.setAttribute("aria-expanded", collapsed ? "false" : "true");
        if (collapsed) state.collapsedSearchFiles.add(path);
        else state.collapsedSearchFiles.delete(path);
        return;
      }
      const result = event.target.closest("[data-search-file]");
      if (result) {
        openArticleFile(result.dataset.searchFile, {
          line: Number(result.dataset.searchLine || 1),
          column: Number(result.dataset.searchColumn || 1),
        });
        return;
      }
      const filter = event.target.closest("[data-log-filter]");
      if (filter) {
        state.logFilter = filter.dataset.logFilter;
        renderLog();
        return;
      }
      const askDiagnostic = event.target.closest("[data-ask-build-diagnostic]");
      if (askDiagnostic) {
        askBuildDiagnostic(Number(askDiagnostic.dataset.askBuildDiagnostic)).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const openDiagnostic = event.target.closest("[data-open-build-diagnostic], [data-build-diagnostic]");
      if (openDiagnostic) {
        const index = Number(openDiagnostic.dataset.openBuildDiagnostic ?? openDiagnostic.dataset.buildDiagnostic);
        openBuildDiagnostic(index).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const diagnostic = event.target.closest("[data-diagnostic-file]");
      if (diagnostic) {
        const path = diagnostic.dataset.diagnosticFile.replace(/^\.\//, "");
        if (state.filesByPath.has(path)) {
          openArticleFile(path, { line: Number(diagnostic.dataset.diagnosticLine || 1) });
        }
      }
    });

    document.addEventListener("dblclick", event => {
      const page = event.target.closest(".articlePdfPage");
      if (!page) return;
      event.preventDefault();
      syncPdfToSource(page, event).catch(error => showToast(error.message || error, "error"));
    });

    $("articleProjectButton").addEventListener("click", event => {
      setProjectMenu(!currentArticleProjectPopover()?.isOpen(), event);
    });
    $("articleGitRefresh").addEventListener("click", () => loadArticleGit({ includeHistory: true }).catch(error => showToast(error.message || error, "error")));
    $("articleGitCommitForm").addEventListener("submit", event => commitArticleGit(event).catch(error => showToast(error.message || error, "error")));
    $("articleGitDiffClose").addEventListener("click", closeArticleGitDiff);
    $("articleGitDiffLayout").addEventListener("click", toggleArticleGitDiffLayout);
    $("articleProjectMenuClose").addEventListener("click", () => setProjectMenu(false));
    $("articleResourceQuery").addEventListener("focus", () => {
      setProjectMenu(false);
      setArticleResourceSearchOpen(true);
    });
    $("articleResourceQuery").addEventListener("input", () => {
      if (state.semanticSearch?.activeId()) {
        state.semanticSearch.clearActive({ notify: false });
        updateSemanticUrl("");
      }
      renderArticleResourceSearch();
      setArticleResourceSearchOpen(true);
    });
    $("articleResourceQuery").addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      const first = $("articleResourceSearchResults").querySelector("[data-article-header-select]");
      if (!first) return;
      event.preventDefault();
      first.click();
    });
    $("articleMenuSearch").addEventListener("input", renderArticleSelectors);
    $("articleLibrarySearch").addEventListener("input", renderArticleSelectors);
    $("articleRegisterOpen").addEventListener("click", openRegisterModal);
    $("articleLibraryRegister").addEventListener("click", openRegisterModal);
    $("articleUnregisterCurrent").addEventListener("click", unregisterCurrentArticle);
    [$("articleRegisterClose"), $("articleRegisterCancel")].forEach(button => button.addEventListener("click", () => articleRegisterDialog.close()));
    $("articleRegisterInspect").addEventListener("click", () => inspectRegistration().catch(error => showToast(error.message || error, "error")));
    $("articleRegisterPath").addEventListener("input", () => {
      state.registerPreflight = null;
      state.registerPrimaryConfirmed = false;
      $("articleRegisterProjects").value = "";
      $("articleRegisterPreflight").dataset.status = "idle";
      $("articleRegisterPreflight").textContent = window.PortalI18n.t("目录已变化，请重新检查。");
    });
    $("articleRegisterPrimary").addEventListener("change", () => {
      state.registerPrimaryConfirmed = true;
      inspectRegistration().catch(error => showToast(error.message || error, "error"));
    });
    $("articleRegisterCompiler").addEventListener("change", () => {
      state.registerPrimaryConfirmed = true;
      inspectRegistration().catch(error => showToast(error.message || error, "error"));
    });
    $("articleRegisterForm").addEventListener("submit", registerArticle);

    $("articleNewFile").addEventListener("click", () => openEntryModal("file"));
    $("articleNewFolder").addEventListener("click", () => openEntryModal("directory"));
    $("articleUploadFile").addEventListener("click", () => state.selectedId && $("articleUploadInput").click());
    $("articleUploadInput").addEventListener("change", event => uploadFile(event.target.files?.[0]));
    [$("articleEntryClose"), $("articleEntryCancel")].forEach(button => button.addEventListener("click", () => articleEntryDialog.close()));
    $("articleEntryForm").addEventListener("submit", createEntry);
    $("articleSideCollapse").addEventListener("click", () => setNavigationPanelVisible(false));

    $("articleTreeToggle").addEventListener("click", () => {
      const open = $("articleTreeToggle").getAttribute("aria-expanded") !== "true";
      $("articleTreeToggle").setAttribute("aria-expanded", open ? "true" : "false");
      $("articleFileTree").classList.toggle("collapsed", !open);
      $("articleOutlineSplitter").hidden = !open;
    });
    $("articleOutlineToggle").addEventListener("click", () => {
      const open = $("articleOutlineToggle").getAttribute("aria-expanded") !== "true";
      $("articleOutlineToggle").setAttribute("aria-expanded", open ? "true" : "false");
      $("articleOutlineSection").classList.toggle("collapsed", !open);
    });
    $("articleGlobalSearch").addEventListener("submit", searchProject);
    $("articleSettingsForm").addEventListener("submit", saveSettings);
    document.querySelectorAll("[data-settings-section]").forEach(button => {
      button.addEventListener("click", () => setSettingsSection(button.dataset.settingsSection));
    });
    $("articleEditorAutoClose").addEventListener("change", event => {
      updateEditorPreference("autoCloseBrackets", event.currentTarget.checked);
    });
    $("articleEditorSolidCursor").addEventListener("change", event => {
      updateEditorPreference("solidCursor", event.currentTarget.checked);
    });
    $("articleEditorCodeCheck").addEventListener("change", event => {
      updateEditorCodeCheck(event.currentTarget);
    });
    $("articleEditorWordWrap").addEventListener("change", event => {
      if (state.wordWrap !== event.currentTarget.checked) toggleWordWrap();
    });
    $("articleEditorFontSize").addEventListener("change", event => {
      updateEditorPreference("fontSize", Number(event.currentTarget.value));
    });
    $("articleEditorTabSize").addEventListener("change", event => {
      updateEditorPreference("tabSize", Number(event.currentTarget.value));
    });
    $("articleEditorSettingsReset").addEventListener("click", resetEditorPreferences);

    $("articleSourceEditor").addEventListener("input", event => {
      if (!activeDocument()?.editable) return;
      state.source = event.currentTarget.value;
      renderEditorLayers();
      updateDirtyState();
      updateCursorStatus();
      scheduleAutoCompile();
    });
    $("articleSourceEditor").addEventListener("scroll", () => {
      syncEditorScroll();
      captureActiveDocument();
    });
    ["click", "keyup", "select"].forEach(name => $("articleSourceEditor").addEventListener(name, () => {
      updateCursorStatus();
      captureActiveDocument();
    }));
    $("articleSourceEditor").addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveOpenFile();
        return;
      }
      if (handleEditorPairing(event)) return;
      if (event.key === "Tab") {
        event.preventDefault();
        const editor = event.currentTarget;
        const start = editor.selectionStart;
        const end = editor.selectionEnd;
        editor.setRangeText(" ".repeat(state.editorPreferences.tabSize), start, end, "end");
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    $("articleSaveFile").addEventListener("click", () => saveOpenFile());
    $("articleCodeMode").addEventListener("click", () => setEditorMode("code"));
    $("articleVisualMode").addEventListener("click", () => {
      captureActiveDocument();
      setEditorMode("visual");
    });
    $("articleUndo").addEventListener("click", () => {
      if (state.monacoReady) state.editor?.trigger?.("article-toolbar", "undo", null);
      else {
        $("articleSourceEditor").focus();
        document.execCommand("undo");
      }
    });
    $("articleRedo").addEventListener("click", () => {
      if (state.monacoReady) state.editor?.trigger?.("article-toolbar", "redo", null);
      else {
        $("articleSourceEditor").focus();
        document.execCommand("redo");
      }
    });
    $("articleWordWrap").addEventListener("click", toggleWordWrap);
    $("articleSyncToPdf").addEventListener("click", syncSourceToPdf);
    $("articleSendContext").addEventListener("click", bringCurrentContextToChat);

    $("articleCompile").addEventListener("click", () => compileArticle());
    $("articleCompileSettings").addEventListener("click", event => {
      setCompileMenu(!currentArticleCompilePopover()?.isOpen(), event);
    });
    document.querySelectorAll("[data-compile-setting]").forEach(button => {
      button.addEventListener("click", () => updateCompilePreference(button));
    });
    $("articleStopCompile").addEventListener("click", stopCompilation);
    $("articleRecompileScratch").addEventListener("click", () => compileArticle({ fromScratch: true }));
    $("articleLogButton").addEventListener("click", () => setOutputView("log"));
    $("articleBackToPdf").addEventListener("click", () => setOutputView("pdf"));
    $("articlePdfZoomOut").addEventListener("click", () => {
      setArticlePdfZoom(state.pdfZoom - 0.1).catch(error => showPdfError(error));
    });
    $("articlePdfZoomIn").addEventListener("click", () => {
      setArticlePdfZoom(state.pdfZoom + 0.1).catch(error => showPdfError(error));
    });
    $("articlePdfZoomFit").addEventListener("click", () => {
      setArticlePdfZoom(1).catch(error => showPdfError(error));
    });
    bindArticlePdfGestures();

    window.addEventListener("portal:change", event => {
      const detail = event?.detail || {};
      const changed = Object.values(detail.resource_projection_changes || {}).flat();
      if (detail.snapshot_required || changed.includes(state.selectedId)) scheduleArticleGitRefresh(120);
    });
    window.addEventListener("portal:events-ready", event => {
      if (event?.detail?.snapshot_required) {
        scheduleArticleGitRefresh(0);
      }
    });
    window.addEventListener("beforeunload", event => {
      articleGitRefresh.stop();
      captureActiveDocument();
      if (state.preferenceSaveTimer) flushArticleNavigationPreference({ keepalive: true });
      if (!hasDirtyDocuments()) return;
      event.preventDefault();
      event.returnValue = "";
    });
    window.addEventListener("popstate", () => {
      const requestedId = String(queryState().get("item") || "");
      if (requestedId === state.selectedId) return;
      const currentId = state.selectedId;
      selectArticle(requestedId, {
        initial: true,
        updateHistory: false,
      }).then(accepted => {
        if (accepted === false && currentId === state.selectedId) detailHistory.restoreAccepted();
      }).catch(error => showToast(error.message || error, "error"));
    });
  }

  window.PortalArticleWorkspaceEvents = Object.freeze({ createEvents, dependencies: DEPENDENCIES });
})();
