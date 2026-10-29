(function () {
  "use strict";

  // The Article workspace's parts live in module scripts that
  // article_workspace.html loads before this one; articleModule(name) returns
  // what a script registers:
  //   article_workspace_monaco.js  the Monaco editor: languages, themes,
  //     options, models, the value, caret and selection of the open document
  //   article_workspace_texlab.js  the TexLab language server: completion,
  //     hover and definition providers, syncing the open document, its status
  //   article_workspace_popovers.js  the Project menu, the header's Article
  //     search and the compile settings popovers
  //   article_workspace_catalog.js  the registered Articles: loading, the
  //     selectors and search, the Project identity, selecting an Article
  //   article_workspace_registration.js  registering an Article directory and
  //     unregistering the current Article
  //   article_workspace_layout.js  the Workbench panels, splitters, the side
  //     view and the kept layout
  //   article_workspace_git.js  the Git side view: status, history, staging,
  //     commit, initializing, its refresh, and the diff viewer
  //   article_workspace_file_tree.js  the file tree, files linked from LaTeX,
  //     new files and folders, uploads
  //   article_workspace_documents.js  open documents: tabs, activation, code
  //     and visual modes, linked files and previews, going to a line, saving
  //   article_workspace_outline.js  the outline and semantic navigation
  //   article_workspace_search.js  searching the project's files
  //   article_workspace_syntax.js  LaTeX highlighting and checks, editor
  //     markers and the syntax status, cursor and dirty state
  //   article_workspace_build.js  build settings, preflight, compiling and
  //     stopping, the build summary and log, build diagnostics
  //   article_workspace_preferences.js  editor preferences and word wrap
  //   article_workspace_pdf.js  the PDF output: rendering, zoom and gestures,
  //     SyncTeX between the source and the PDF
  //   article_workspace_chat.js  the Article's Chat and the context sent to it
  //   article_workspace_events.js  the page's event wiring, created last
  // This script holds the page's state, dialogs and the requests every part
  // shares, creates the parts in that order and starts the page; a part that
  // calls a function of a part created after it gets an arrow forwarding the
  // call.
  function articleModule(name) {
    const module = window[name];
    if (!module) throw new Error(`${name} is not loaded.`);
    return module;
  }

  const $ = id => document.getElementById(id);
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const articleStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
    state: options.state || "empty",
    title: options.title || "",
    message,
    density: options.density || "compact",
    icon: options.icon,
  });
  const queryState = () => new URLSearchParams(window.location.search);
  const articleRegisterDialog = window.PortalUI.createDialogController({
    modal: $("articleRegisterModal"),
    openClass: "portalDialogOpen",
    bodyClass: null,
    backdropClose: true,
    escapeClose: true,
    onOpen: () => { $("articleRegisterModal").hidden = false; },
    onClose: () => { $("articleRegisterModal").hidden = true; },
  });
  const articleEntryDialog = window.PortalUI.createDialogController({
    modal: $("articleEntryModal"),
    openClass: "portalDialogOpen",
    bodyClass: null,
    backdropClose: true,
    escapeClose: true,
    onOpen: () => { $("articleEntryModal").hidden = false; },
    onClose: () => { $("articleEntryModal").hidden = true; },
  });
  const embedded = queryState().get("embedded") === "1";
  const EDITOR_PREFERENCE_KEY = "portal.articleWorkspace.editorPreferences";
  const ARTICLE_GIT_REFRESH_MS = 5000;
  const articleLayoutStore = window.PortalWorkbench.layout.createStore({
    storageKey: "portal.articleWorkspace.layout",
  });
  const WORD_WRAP_STORAGE_KEY = "portal.articleWorkspace.wordWrap";
  const EDITOR_PREFERENCE_DEFAULTS = Object.freeze({
    autoCloseBrackets: true,
    solidCursor: false,
    fontSize: 12,
    tabSize: 2,
  });
  const state = {
    articles: [],
    selectedId: "",
    sideView: "files",
    gitStatus: null,
    gitHistory: [],
    gitDiffPath: "",
    gitDiffTitle: "",
    gitDiffEditor: null,
    gitDiffModels: [],
    gitDiffFiles: [],
    gitDiffActiveFile: 0,
    gitDiffSideBySide: true,
    files: [],
    filesByPath: new Map(),
    linkedFiles: new Map(),
    linkedFileProbes: new Set(),
    collapsedDirectories: new Set(),
    selectedDirectory: "",
    openFile: "",
    openTabs: [],
    documents: new Map(),
    source: "",
    savedSource: "",
    editor: null,
    monacoReady: false,
    editorSuppressChange: false,
    monacoDisposables: [],
    renderFrame: 0,
    outlineTimer: 0,
    semanticTimer: 0,
    semanticIndex: null,
    semanticView: "outline",
    settings: null,
    build: null,
    buildLoading: false,
    logFilter: "all",
    outputView: "pdf",
    pdfSyncLocation: null,
    pdfSyncPending: false,
    pdfZoom: 1,
    pdfScaleVersion: 0,
    pdfLayoutFrame: 0,
    editorLayoutFrame: 0,
    pdfRedrawTimer: 0,
    pdfGestureCommitTimer: 0,
    pdfGestureAnchor: null,
    pdfTouchGesture: null,
    chatController: null,
    chatPanel: null,
    chatLoadRequest: null,
    activeDiagnostic: null,
    entryKind: "file",
    autoCompileTimer: 0,
    autoCompileQueued: false,
    compilingArticles: new Set(),
    stopRequested: false,
    wordWrap: true,
    settingsSection: "editor",
    editorPreferences: { ...EDITOR_PREFERENCE_DEFAULTS },
    syntaxDiagnostics: [],
    texlabDiagnostics: [],
    texlabTimer: 0,
    texlabVersion: 0,
    texlabStatus: "starting",
    collapsedSearchFiles: new Set(),
    semanticSearch: null,
    resourceSearchOpen: false,
    lastSelectedId: "",
    preferenceSaveTimer: 0,
  };
  const detailHistory = window.PortalResourceDetail.createDetailHistory({
    owner: "resource-workspace:article",
    parameter: "item",
  });
  const detailResourceGuest = window.PortalResourceDetail.createGuest({
    resourceId: () => state.selectedId || queryState().get("item"),
    resourceType: "article",
    detailKind: "article",
    guardClose: () => {
      captureActiveDocument();
      if (!hasDirtyDocuments()) return true;
      return window.PortalUI.confirmAction("close", "当前 Article", {
        consequence: "关闭会放弃所有尚未保存的文件修改。",
      });
    },
  });


  async function request(url, options = {}) {
    return window.PortalAPI.request(url, options);
  }

  function articleRawUrl(path) {
    return `/api/articles/${encodeURIComponent(state.selectedId)}/raw?path=${encodeURIComponent(path)}`;
  }

  function articleRelativePath(basePath, relativePath) {
    const base = String(basePath || "").split("/");
    base.pop();
    for (const part of String(relativePath || "").split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") base.pop();
      else base.push(part);
    }
    return base.join("/");
  }

  window.MD.registerImagePathResolver("article-workspace", function (rawPath, options = {}) {
    const path = String(rawPath || "").trim();
    if (!options.articleMarkdown || !path) return "";
    if (/^(?:data:image\/|blob:|https?:\/\/|\/)/i.test(path)) return "";
    return articleRawUrl(articleRelativePath(options.basePath, path));
  });

  function selectedArticle() {
    return state.articles.find(article => article.id === state.selectedId) || null;
  }

  function articleRoot(article = selectedArticle()) {
    return (article?.roots || []).find(root => root.path === article.canonical_root)
      || (article?.roots || [])[0]
      || {};
  }

  function articleCanonicalRootPath() {
    return String(articleRoot()?.path || selectedArticle()?.canonical_root || "")
      .replaceAll("\\", "/")
      .replace(/\/+$/, "");
  }

  function articleAbsolutePath(path) {
    const target = String(path || "").replaceAll("\\", "/");
    if (!target || target.startsWith("/") || target.startsWith("~/")) return target;
    const root = articleCanonicalRootPath();
    return root ? `${root}/${target.replace(/^\/+/, "")}` : target;
  }

  function articleInternalPath(path) {
    const target = String(path || "").replaceAll("\\", "/");
    const root = articleCanonicalRootPath();
    if (!root || !target.startsWith(`${root}/`)) return "";
    const relative = target.slice(root.length + 1);
    const entry = state.filesByPath.get(relative);
    return entry && entry.type !== "directory" ? relative : "";
  }

  function articleProjects(article) {
    return Array.isArray(article?.project_ids) ? article.project_ids.filter(Boolean) : [];
  }

  function updateUrl(id) {
    detailHistory.replace({ item: id });
  }

  function updateSemanticUrl(searchId) {
    detailHistory.replace({ semantic: searchId });
  }

  function formatDate(value) {
    if (!value) return "";
    return window.PortalUI.formatDateTime(value, { style: "status" });
  }

  function showToast(message, kind = "") {
    return window.PortalUI.toast(message, kind || "success");
  }

  const {
    activeEditorModel, articleModelUri, documentForModel, editorPosition, editorSelectionContext, editorValue,
    initializeMonacoEditor, isMarkdownPath, languageForPath, monacoApi,
  } = articleModule("PortalArticleWorkspaceMonaco").createMonaco({
    $, activateDocument: (...args) => activateDocument(...args),
    activeDocument: (...args) => activeDocument(...args),
    captureActiveDocument: (...args) => captureActiveDocument(...args),
    registerTexlabProviders: (...args) => registerTexlabProviders(...args),
    renderEditorLayers: (...args) => renderEditorLayers(...args),
    saveOpenFile: (...args) => saveOpenFile(...args),
    scheduleAutoCompile: (...args) => scheduleAutoCompile(...args),
    scheduleSemanticReload: (...args) => scheduleSemanticReload(...args),
    scheduleTexlabSync: (...args) => scheduleTexlabSync(...args), state,
    updateCursorStatus: (...args) => updateCursorStatus(...args),
    updateDirtyState: (...args) => updateDirtyState(...args),
  });

  const {
    registerTexlabProviders, scheduleTexlabSync, setTexlabStatus,
  } = articleModule("PortalArticleWorkspaceTexlab").createTexlab({
    $, activeDocument: (...args) => activeDocument(...args), activeEditorModel, documentForModel,
    openArticleFile: (...args) => openArticleFile(...args), request, state,
    updateAllEditorMarkers: (...args) => updateAllEditorMarkers(...args),
  });

  const {
    initializeArticlePopovers, setArticleResourceSearchOpen, setCompileMenu, setProjectMenu,
    currentArticleProjectPopover, currentArticleCompilePopover,
  } = articleModule("PortalArticleWorkspacePopovers").createPopovers({
    $, renderArticleResourceSearch: (...args) => renderArticleResourceSearch(...args),
    renderArticleSelectors: (...args) => renderArticleSelectors(...args),
    renderCompileMenu: (...args) => renderCompileMenu(...args), state,
  });

  const {
    articleGitRefresh, closeArticleGitDiff, commitArticleGit, initializeArticleGit, loadArticleGit,
    openArticleGitCommit, openArticleGitDiff, renderArticleGitDiffFile, resetArticleGit,
    scheduleArticleGitRefresh, toggleArticleGitDiffLayout, unregisteringArticles, updateArticleGitStage,
  } = articleModule("PortalArticleWorkspaceGit").createGit({
    $, ARTICLE_GIT_REFRESH_MS, esc, languageForPath, monacoApi,
    renderDocumentTabs: (...args) => renderDocumentTabs(...args), request, showToast, state,
  });

  const {
    flushArticleNavigationPreference, initializeArticleSemanticSearch, loadArticleNavigationPreference,
    loadArticles, persistArticleSelection, renderArticleResourceSearch, renderArticleSelectors, selectArticle,
  } = articleModule("PortalArticleWorkspaceCatalog").createCatalog({
    $, activeDocument: (...args) => activeDocument(...args), articleProjects, articleRoot, articleStateHtml,
    cancelPdf: (...args) => cancelPdf(...args), clearEditor: (...args) => clearEditor(...args), detailHistory,
    detailResourceGuest, documentIsDirty: (...args) => documentIsDirty(...args), esc,
    hasDirtyDocuments: (...args) => hasDirtyDocuments(...args),
    loadArticleChat: (...args) => loadArticleChat(...args), loadArticleGit,
    loadBuild: (...args) => loadBuild(...args), loadFiles: (...args) => loadFiles(...args),
    loadSemanticIndex: (...args) => loadSemanticIndex(...args),
    loadSettings: (...args) => loadSettings(...args), openArticleFile: (...args) => openArticleFile(...args),
    queryState, renderBuildSummary: (...args) => renderBuildSummary(...args),
    renderCompileMenu: (...args) => renderCompileMenu(...args),
    renderFileTree: (...args) => renderFileTree(...args), renderPdf: (...args) => renderPdf(...args),
    renderSemanticNavigation: (...args) => renderSemanticNavigation(...args),
    renderSettings: (...args) => renderSettings(...args), request, resetArticleGit, scheduleArticleGitRefresh,
    selectedArticle, setArticleResourceSearchOpen, setOutputView: (...args) => setOutputView(...args),
    setProjectMenu, setTexlabStatus, showPdfError: (...args) => showPdfError(...args), showToast, state,
    updateSemanticUrl, updateUrl,
  });

  const {
    inspectRegistration, openRegisterModal, registerArticle, unregisterCurrentArticle,
  } = articleModule("PortalArticleWorkspaceRegistration").createRegistration({
    $, articleGitRefresh, articleRegisterDialog, detailHistory, detailResourceGuest, esc, loadArticles,
    persistArticleSelection, request, scheduleArticleGitRefresh, selectedArticle, setProjectMenu, showToast,
    state, unregisteringArticles, updateUrl,
  });

  const {
    bindSplitters, initArticleWorkbench, restoreLayout, setNavigationPanelVisible, setSideView,
    currentArticleWorkbench,
  } = articleModule("PortalArticleWorkspaceLayout").createLayout({
    $, applyEditorPreferences: (...args) => applyEditorPreferences(...args),
    applyWordWrap: (...args) => applyWordWrap(...args), articleLayoutStore, EDITOR_PREFERENCE_KEY,
    loadArticleChat: (...args) => loadArticleChat(...args), loadArticleGit,
    normalizedEditorPreferences: (...args) => normalizedEditorPreferences(...args),
    scheduleArticlePdfLayout: (...args) => scheduleArticlePdfLayout(...args),
    setSettingsSection: (...args) => setSettingsSection(...args), showToast, state, WORD_WRAP_STORAGE_KEY,
  });

  const {
    createEntry, loadFiles, openArticleLinkedReference, openEntryModal, registerArticleLinkedFiles,
    renderFileTree, uploadFile,
  } = articleModule("PortalArticleWorkspaceFileTree").createFileTree({
    $, activeDocument: (...args) => activeDocument(...args), articleAbsolutePath, articleEntryDialog,
    articleInternalPath, articleStateHtml, loadSemanticIndex: (...args) => loadSemanticIndex(...args),
    openArticleFile: (...args) => openArticleFile(...args),
    openArticleLinkedFile: (...args) => openArticleLinkedFile(...args),
    openArticleResourcePreview: (...args) => openArticleResourcePreview(...args),
    renderSettings: (...args) => renderSettings(...args), request, showToast, state,
  });

  const {
    activateDocument, activeDocument, captureActiveDocument, clearEditor, documentIsDirty, goToLine,
    hasDirtyDocuments, openArticleFile, openArticleLinkedFile, openArticleResourcePreview, renderDocumentTabs,
    saveAllDirtyDocuments, saveOpenFile, setEditorMode,
  } = articleModule("PortalArticleWorkspaceDocuments").createDocuments({
    $, articleAbsolutePath, articleModelUri, closeArticleGitDiff, detailResourceGuest, editorValue,
    isMarkdownPath, languageForPath, loadSemanticIndex: (...args) => loadSemanticIndex(...args), monacoApi,
    openArticleLinkedReference, pdfUrl: (...args) => pdfUrl(...args), registerArticleLinkedFiles,
    renderEditorLayers: (...args) => renderEditorLayers(...args), renderFileTree,
    renderSemanticNavigation: (...args) => renderSemanticNavigation(...args), request,
    scheduleArticleGitRefresh, scheduleTexlabSync, showToast, state,
    syncEditorScroll: (...args) => syncEditorScroll(...args),
    updateCursorStatus: (...args) => updateCursorStatus(...args),
    updateDirtyState: (...args) => updateDirtyState(...args),
  });

  const {
    loadSemanticIndex, renderSemanticNavigation, scheduleSemanticReload,
  } = articleModule("PortalArticleWorkspaceOutline").createOutline({
    $, articleStateHtml, esc, request, showToast, state,
    updateAllEditorMarkers: (...args) => updateAllEditorMarkers(...args),
  });

  const {
    searchProject,
  } = articleModule("PortalArticleWorkspaceSearch").createSearch({
    $, articleStateHtml, esc, request, state,
  });

  const {
    handleEditorPairing, normalizedDiagnosticPath, renderEditorLayers, syncEditorScroll,
    updateAllEditorMarkers, updateCursorStatus, updateDirtyState,
  } = articleModule("PortalArticleWorkspaceSyntax").createSyntax({
    $, activeDocument, articleRoot, captureActiveDocument,
    compileSettingValue: (...args) => compileSettingValue(...args), editorPosition, editorSelectionContext,
    esc, monacoApi, renderDocumentTabs, renderSemanticNavigation, selectedArticle, state,
  });

  const {
    askBuildDiagnostic, compileArticle, compileSettingValue, loadBuild, loadSettings, openBuildDiagnostic,
    renderBuildSummary, renderCompileMenu, renderLog, renderSettings, saveSettings, scheduleAutoCompile,
    setOutputView, stopCompilation, updateCompilePreference, updateEditorCodeCheck,
  } = articleModule("PortalArticleWorkspaceBuild").createBuild({
    $, addArticleChatReference: (...args) => addArticleChatReference(...args), articleStateHtml, esc,
    formatDate, hasDirtyDocuments, loadArticles, loadFiles, loadSemanticIndex, normalizedDiagnosticPath,
    openArticleFile, renderEditorLayers,
    renderEditorPreferenceControls: (...args) => renderEditorPreferenceControls(...args),
    renderPdf: (...args) => renderPdf(...args), request, saveAllDirtyDocuments, setCompileMenu, setSideView,
    showPdfError: (...args) => showPdfError(...args), showToast, state, updateAllEditorMarkers,
    updateArticlePdfZoomControls: (...args) => updateArticlePdfZoomControls(...args),
  });

  const {
    applyEditorPreferences, applyWordWrap, normalizedEditorPreferences, renderEditorPreferenceControls,
    resetEditorPreferences, setSettingsSection, toggleWordWrap, updateEditorPreference,
  } = articleModule("PortalArticleWorkspacePreferences").createPreferences({
    $, activeEditorModel, captureActiveDocument, compileSettingValue, EDITOR_PREFERENCE_DEFAULTS,
    EDITOR_PREFERENCE_KEY, renderEditorLayers, showToast, state, syncEditorScroll, WORD_WRAP_STORAGE_KEY,
  });

  const {
    bindArticlePdfGestures, cancelPdf, pdfUrl, renderPdf, scheduleArticlePdfLayout, setArticlePdfZoom,
    showPdfError, syncPdfToSource, syncSourceToPdf, updateArticlePdfZoomControls,
  } = articleModule("PortalArticleWorkspacePdf").createPdf({
    $, editorPosition, esc, openArticleFile, request, selectedArticle, setOutputView, showToast, state,
  });

  const {
    addArticleChatReference, bringCurrentContextToChat, loadArticleChat,
  } = articleModule("PortalArticleWorkspaceChat").createChat({
    $, activeDocument, articleProjects, articleRoot, documentIsDirty, editorPosition, editorSelectionContext,
    languageForPath, selectedArticle, setSideView, showToast, state,
  });

  articleModule("PortalArticleWorkspaceEvents").createEvents({
    $, activeDocument, articleEntryDialog, articleGitRefresh, articleRegisterDialog, askBuildDiagnostic,
    bindArticlePdfGestures, bringCurrentContextToChat, captureActiveDocument, closeArticleGitDiff,
    commitArticleGit, compileArticle, createEntry, currentArticleCompilePopover, currentArticleProjectPopover,
    currentArticleWorkbench, detailHistory, flushArticleNavigationPreference, goToLine, handleEditorPairing,
    hasDirtyDocuments, initializeArticleGit, initializeArticlePopovers, inspectRegistration, loadArticleGit,
    openArticleFile, openArticleGitCommit, openArticleGitDiff, openBuildDiagnostic, openEntryModal,
    openRegisterModal, queryState, registerArticle, renderArticleGitDiffFile, renderArticleResourceSearch,
    renderArticleSelectors, renderEditorLayers, renderLog, renderSemanticNavigation, resetEditorPreferences,
    saveOpenFile, saveSettings, scheduleArticleGitRefresh, scheduleAutoCompile, searchProject, selectArticle,
    setArticlePdfZoom, setArticleResourceSearchOpen, setCompileMenu, setEditorMode, setNavigationPanelVisible,
    setOutputView, setProjectMenu, setSettingsSection, setSideView, showPdfError, showToast, state,
    stopCompilation, syncEditorScroll, syncPdfToSource, syncSourceToPdf, toggleArticleGitDiffLayout,
    toggleWordWrap, unregisterCurrentArticle, updateArticleGitStage, updateCompilePreference,
    updateCursorStatus, updateDirtyState, updateEditorCodeCheck, updateEditorPreference, updateSemanticUrl,
    uploadFile,
  });

  initArticleWorkbench();
  restoreLayout();
  if (["1", "true", "open"].includes(String(queryState().get("chat") || "").toLowerCase())) {
    setSideView("chat");
  }
  bindSplitters();
  window.PortalPreview.registerHost("article-workspace", request => {
    if (!state.selectedId) return false;
    return openArticleLinkedReference(request).then(() => true);
  }, { priority: 100 });
  if (embedded) document.body.dataset.articleEmbedded = "1";
  window.PortalResourceTabs?.render(document.querySelector("[data-resource-tabs]"));
  initializeArticleSemanticSearch();
  window.ArticleWorkspaceTest = {
    value: () => editorValue(),
    setValue: value => {
      if (state.monacoReady && state.editor) state.editor.setValue(String(value ?? ""));
      else {
        const editor = $("articleSourceEditor");
        editor.value = String(value ?? "");
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      }
    },
    position: () => editorPosition(),
    setPosition: (line, column = 1) => goToLine(line, column),
    selection: () => editorSelectionContext(),
    setSelection: (startLine, startColumn, endLine = startLine, endColumn = startColumn) => {
      if (state.monacoReady && state.editor) {
        const selection = {
          startLineNumber: Number(startLine),
          startColumn: Number(startColumn),
          endLineNumber: Number(endLine),
          endColumn: Number(endColumn),
        };
        state.editor.setSelection(selection);
        state.editor.revealRangeInCenter(selection);
        state.editor.focus();
      }
    },
    openFile: (path, options = {}) => openArticleFile(path, options),
    activeFile: () => state.openFile,
    pdfZoom: () => state.pdfZoom,
    markers: () => {
      const monaco = monacoApi();
      const model = activeEditorModel();
      return monaco && model
        ? monaco.editor.getModelMarkers({ resource: model.uri })
        : [];
    },
    editorOptions: () => state.editor?.getRawOptions?.() || {},
    editorLayout: () => state.editor?.getLayoutInfo?.() || null,
    editorLineTop: line => state.editor?.getTopForLineNumber?.(Number(line)) || 0,
    gitDiff: () => ({
      files: state.gitDiffFiles.map(file => file.path),
      activeFile: state.gitDiffFiles[state.gitDiffActiveFile]?.path || "",
      original: state.gitDiffModels[0]?.getValue?.() || "",
      modified: state.gitDiffModels[1]?.getValue?.() || "",
      sideBySide: state.gitDiffSideBySide,
    }),
    refreshGit: () => loadArticleGit({ includeHistory: state.sideView === "git", silent: true }),
  };
  const articleNavigationReady = loadArticleNavigationPreference();
  const articleListReady = articleNavigationReady.then(() => loadArticles());
  const articleEditorReady = Promise.all([
    initializeMonacoEditor(),
    articleNavigationReady,
  ]).then(() => {
    applyEditorPreferences();
    applyWordWrap();
  });
  window.ArticleWorkspaceReady = Promise.all([articleListReady, articleEditorReady]).catch(error => {
    showToast(error.message || error, "error");
  });
  state.semanticSearch?.load().catch(error => {
    showToast(window.PortalUI?.errorMessage?.(window.PortalI18n.t("加载 Article 语义检索"), error) || error, "error");
  });
}());
