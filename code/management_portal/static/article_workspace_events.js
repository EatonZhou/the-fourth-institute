(() => {
  "use strict";

  if (window.PortalArticleWorkspaceEvents) return;

  // The page's event wiring, bound when the part is created: the popovers,
  // delegated clicks, the header, registration, file, settings, editor,
  // compile and PDF controls, Portal change events, leaving the page and
  // browser history navigation.
  const DEPENDENCIES = Object.freeze([
    "$", "articleContentRefresh", "scheduleArticleContentRefresh", "articleEntryDialog", "articleGitRefresh", "articleRegisterDialog",
    "askBuildDiagnostic", "captureActiveDocument",
    "closeArticleGitDiff", "commitArticleGit", "createEntry",
    "currentArticleProjectPopover", "detailHistory", "flushArticleNavigationPreference", "goToLine", "hasDirtyDocuments", "initializeArticleGit", "initializeArticlePopovers", "inspectRegistration",
    "loadArticleGit", "handleArticleGitAction", "openArticleFile", "openArticleGitCommit", "openArticleGitDiff", "openBuildDiagnostic",
    "openEntryModal", "openRegisterModal", "pullArticleGit", "queryState", "registerArticle", "renderArticleGitDiffFile",
    "renderArticleResourceSearch", "renderArticleSelectors", "renderLog",
    "renderSemanticNavigation", "resetEditorPreferences", "saveSettings",
    "scheduleArticleGitRefresh", "searchProject", "selectArticle", "setArticleResourceSearchOpen", "setToolVisible",
    "setProjectMenu", "setSettingsSection", "setSideView", "showToast",
    "state", "toggleArticleGitDiffLayout", "toggleWordWrap", "canToggleArticleSidebar", "toggleArticleSidebar", "unregisterCurrentArticle", "updateArticleGitStage",
    "updateEditorCodeCheck",
    "updateEditorPreference", "updateSemanticUrl", "uploadFile", "persistReadingPosition", ]);

  function createEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceEvents is missing: ${missing.join(", ")}`);
    const {
      $, articleContentRefresh, scheduleArticleContentRefresh, articleEntryDialog, articleGitRefresh, articleRegisterDialog, askBuildDiagnostic,
      captureActiveDocument, closeArticleGitDiff,
      commitArticleGit, createEntry, currentArticleProjectPopover, detailHistory, flushArticleNavigationPreference,
      goToLine, hasDirtyDocuments, initializeArticleGit, initializeArticlePopovers,
      inspectRegistration, loadArticleGit, handleArticleGitAction, openArticleFile, openArticleGitCommit, openArticleGitDiff,
      openBuildDiagnostic, openEntryModal, openRegisterModal, pullArticleGit, queryState, registerArticle,
      renderArticleGitDiffFile, renderArticleResourceSearch, renderArticleSelectors, renderLog, renderSemanticNavigation, resetEditorPreferences, saveSettings,
      scheduleArticleGitRefresh, searchProject, selectArticle, setArticleResourceSearchOpen, setToolVisible, setProjectMenu, setSettingsSection, setSideView, showToast, state, toggleArticleGitDiffLayout, toggleWordWrap, canToggleArticleSidebar, toggleArticleSidebar,
      unregisterCurrentArticle, updateArticleGitStage, updateEditorCodeCheck, updateEditorPreference, updateSemanticUrl, uploadFile, persistReadingPosition, } = dependencies;

    initializeArticlePopovers();

    document.addEventListener("click", event => {
      const view = event.target.closest("[data-article-view]");
      if (view) {
        setSideView(view.dataset.articleView, { toggle: true });
        return;
      }
      if (event.target.closest("[data-side-close]")) {
        setToolVisible(false, event.target.closest("[data-article-panel]")?.dataset.articlePanel);
        return;
      }
      const gitAction = event.target.closest("[data-git-action]");
      if (gitAction) {
        handleArticleGitAction(gitAction).catch(error => showToast(error.message || error, "error"));
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
        openArticleGitDiff(gitDiff.dataset.gitDiff, gitDiff.dataset.gitDiffStaged === "true")
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


    $("articleProjectButton").addEventListener("click", event => {
      setProjectMenu(!currentArticleProjectPopover()?.isOpen(), event);
    });
    $("articleGitRefresh").addEventListener("click", () => loadArticleGit({ includeHistory: true }).catch(error => showToast(error.message || error, "error")));
    $("articleGitPull").addEventListener("click", pullArticleGit);
    $("articleGitCommitForm").addEventListener("submit", event => commitArticleGit(event).catch(error => showToast(error.message || error, "error")));
    $("articleGitMessage").addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        $("articleGitCommitForm").requestSubmit();
      }
    });
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
    $("articleSideCollapse").addEventListener("click", () => setToolVisible(false, "files"));

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

    window.PortalCommandRegistry.root.register({
      id: "article.toggleSidebar", title: window.PortalI18n.t("显示或隐藏左侧栏"), category: "Article",
      keybindings: ["Ctrl+B", "Meta+B"], allowInInput: true,
      enabled: () => {
        if (!canToggleArticleSidebar()) return false;
        const active = document.activeElement;
        if (active?.closest('[role="dialog"], dialog, .portalOverlay')) return false;
        const editable = active?.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]');
        return !editable || active === $("articleSourceEditor") || Boolean(active?.closest("#articleMonacoEditor"));
      },
      execute: ({ event }) => event?.repeat ? false : toggleArticleSidebar(),
    });


    window.addEventListener("portal:change", event => {
      const detail = event?.detail || {};
      const changed = Object.values(detail.resource_projection_changes || {}).flat();
      if (detail.snapshot_required || changed.includes(state.selectedId)) {
        scheduleArticleGitRefresh(120);
        scheduleArticleContentRefresh();
      }
    });
    window.addEventListener("portal:events-ready", event => {
      if (event?.detail?.snapshot_required) {
        scheduleArticleGitRefresh(0);
        scheduleArticleContentRefresh(0);
      }
    });
    window.addEventListener("focus", () => scheduleArticleContentRefresh(0));
    document.addEventListener("visibilitychange", () => { if (document.hidden) persistReadingPosition(); });
    window.addEventListener("pagehide", () => {
      persistReadingPosition();
      articleContentRefresh.stop();
    });
    window.addEventListener("pageshow", () => scheduleArticleContentRefresh(0));
    window.addEventListener("beforeunload", event => {
      articleGitRefresh.stop();
      captureActiveDocument();
      if (state.preferenceSaveTimer) flushArticleNavigationPreference({ keepalive: true });
      persistReadingPosition();
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
