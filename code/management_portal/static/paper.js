(() => {
  "use strict";

  // The Paper page's parts live in module scripts that paper.html loads
  // before this one; paperModule(name) returns what a script registers:
  //   paper_location.js  the page state kept in the URL and in page state,
  //     and going back to the previous Paper workspace
  //   paper_layout.js  the Workbench panes and the mode tabs
  //   paper_library.js  loading the Library, its filters, semantic search,
  //     the collection a search or Collector job selects, Markdown export
  //   paper_library_view.js  the Library tree, Paper rows and favorites
  //   paper_collector.js  Collector jobs
  //   paper_ccf_catalog.js  CCF venues, years and Papers, their filters and
  //     the enrichment of a CCF Paper
  //   paper_ccf_view.js  the Library and CCF faces, the CCF tree and
  //     workspace, registering a CCF Paper
  //   paper_detail.js  loading a Paper's detail sections and assets job
  //   paper_workspace_view.js  the workspace header, details and actions, and
  //     drawing the active mode
  //   paper_reader.js  the reader: PDF and analysis views, the reading place,
  //     the selection menu and its actions
  //   paper_reviews.js  AI reviews, experiments and the deep read
  //   paper_notes_refs.js  notes and references
  //   paper_figures.js  figures: list, detail, inputs, drafts, explanations
  //   paper_chat.js  the Paper Chat and its context
  //   paper_registration.js  registering a Paper from a PDF
  //   paper_record.js  deleting a Paper, uploading or deleting its PDF
  //   paper_projection.js  following Paper projection changes
  //   paper_events.js  the page's event wiring
  // This script holds the page's state and the helpers every part shares,
  // creates the parts in that order and starts the page; a part that calls a
  // function of a part created after it gets an arrow forwarding the call.
  function paperModule(name) {
    const module = window[name];
    if (!module) throw new Error(`${name} is not loaded.`);
    return module;
  }

  const $ = id => document.getElementById(id);
  const paperDialog = window.PortalUI.createDialogController({
    modal: "paperModal",
    bodyClass: null,
    backdropClose: true,
    escapeClose: true,
  });
  const paperSelectionPopover = window.PortalOverlay.bindFloatingPopover({
    panel: "selectionMenu",
    placement: "top",
    align: "center",
    closeOnOutside: true,
    closeOnEscape: true,
    closeOnScroll: true,
  });
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const paperStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
    state: options.state || "empty",
    title: options.title || "",
    message,
    density: options.density || "panel",
    icon: options.icon,
    actionsHtml: options.actionsHtml || "",
    className: options.className || "",
    id: options.id || "",
  });
  const disclosureIcon = expanded => window.PortalI18n.html`<span class="folderDisclosureIcon" data-portal-icon="${expanded ? "chevron-up" : "chevron-down"}"></span>`;
  function normalizeMarkdownInput(value) {
    const text = String(value || "").replace(/\r\n/g, "\n").trim();
    const fencePattern = /^```(?:markdown|md)?[ \t]*\n([\s\S]*?)\n```[ \t]*$/i;
    const direct = text.match(fencePattern);
    if (direct) return direct[1].trim();
    const frontmatter = text.match(/^(---\n[\s\S]*?\n---\n*)([\s\S]*)$/);
    if (frontmatter) {
      const body = String(frontmatter[2] || "").trim();
      const fencedBody = body.match(fencePattern);
      if (fencedBody) return `${frontmatter[1].trimEnd()}\n\n${fencedBody[1].trim()}`;
    }
    return text;
  }
  const md = value => {
    const normalized = normalizeMarkdownInput(value);
    return window.MD?.render ? window.MD.render(normalized) : window.PortalI18n.html`<pre>${esc(normalized)}</pre>`;
  };
  const state = {
    username: "",
    mode: "library",
    library: null,
    libraryLoading: false,
    libraryRequestSeq: 0,
    favoriteIds: new Set(),
    favoritePending: new Set(),
    favoriteRevision: 0,
    libraryRenderLimit: 100,
    paperTreeRenderLimit: 240,
    libraryPageLoading: false,
    detail: null,
    detailRequestSeq: 0,
    detailSections: new Set(),
    detailSectionPromises: {},
    selectedId: "",
    query: "",
    filters: { year: "", venue: "", tag: "", project: "" },
    paperTreeExpansion: {},
    leftFace: "library",
    previousPaperWorkspace: null,
    activeSemanticSearchId: "",
    semanticSearch: null,
    collectorJobs: [],
    collectorStarting: false,
    expandedCollectorJobId: "",
    activeCollectorJobId: "",
    chat: [],
    chatSession: null,
    chatMetadata: null,
    chatSessions: [],
    chatHistoryLoadedFor: "",
    chatHistoryLoading: false,
    requestedChatWorkspaceId: "",
    deepReadStatus: null,
    expStatus: null,
    searchTimer: null,
    leaving: false,
    chatSendInFlight: false,
    reviewInFlight: false,
    reviewJob: null,
    reviewPollTimer: 0,
    reviewPollError: "",
    figureExtractInFlight: "",
    figureExplainInFlight: "",
    figureGenerateInFlight: "",
    figureDraftGeneratedId: "",
    selectedFigureId: "",
    figureInputImages: {},
    pdfUploadInFlight: false,
    pdfDeleteInFlight: false,
    deletingPaperId: "",
    registrationUploadId: "",
    registrationEditedFields: new Set(),
    registrationDraftInFlight: false,
    registrationDraftPollTimer: 0,
    registrationSubmitInFlight: false,
    refExtractInFlight: "",
    refsAutoExtracted: {},
    refExtractErrors: {},
    refRegisterInFlight: "",
    readerAnalysisCollapsed: false,
    readerPdfCollapsed: false,
    selectionText: "",
    pdfResizeTimer: null,
    ccfVenues: null,
    ccfYearsByVenue: {},
    ccfYearsErrors: {},
    ccfPapersByYear: {},
    ccfRenderLimits: {},
    ccfExpandedVenue: "",
    ccfSelectedVenue: "",
    ccfSelectedYear: "",
    ccfYearFilter: "",
    ccfPaperQuery: "",
    ccfCollapsedRanks: {},
    ccfCollapsedCategories: {},
    ccfFilterTimer: null,
    ccfEnrichmentByKey: {},
    ccfEnrichmentInFlight: {},
    ccfSelectedPaper: null,
    ccfRegisterInFlight: "",
    ccfPaperFetchInFlight: "",
    ccfYearsFetchInFlight: "",
  };
  const alertError = err => window.PortalUI.alertError(err, { leaving: state.leaving });
  const paperApi = window.PortalAPI.createClient({
    cache: "default",
    detectLoginRedirect: false,
  });

  const paperPageState = window.PortalPageState.create("paper", {
    user: () => state.username || "anonymous",
  });
  let semanticSearchReady = false;
  let paperIdentityReady = Promise.resolve();

  async function init() {
    initPaperWorkbench();
    bindEvents();
    paperIdentityReady = loadMe();
    // Saved filters and reader state are scoped to the authenticated user.
    // Reading them as "anonymous" loses the selection on every reload.
    await paperIdentityReady;
    readUiState();
    const requestedPaperId = requestedPaperIdFromUrl();
    const requestedSemanticSearchId = requestedSemanticSearchIdFromUrl();
    const requestedChatWorkspaceId = requestedChatWorkspaceIdFromUrl();
    if (requestedChatWorkspaceId) {
      state.requestedChatWorkspaceId = requestedChatWorkspaceId;
      state.leftFace = "library";
      state.mode = "library";
      state.selectedId = "";
      state.detail = null;
    } else if (requestedPaperId) {
      state.leftFace = "library";
      state.selectedId = requestedPaperId;
      state.mode = "reader";
      state.ccfSelectedPaper = null;
      state.detail = null;
    } else if (requestedSemanticSearchId) {
      state.leftFace = "library";
      state.mode = "library";
      state.selectedId = "";
      state.detail = null;
      state.activeCollectorJobId = "";
      state.activeSemanticSearchId = requestedSemanticSearchId;
    }
    initSemanticSearch();
    if (requestedChatOpenFromUrl() && !currentPaperWorkbench()?.isPanelVisible("chat")) currentPaperWorkbench()?.showPanel("chat");
    applyPaneCollapse();
    syncModeTabs();
    // Auxiliary services must not gate library browsing, even while pending.
    loadCollectorJobs({ render: true }).catch(error => window.PortalUI.toast(`Collector：${error.message || error}`, "warning"));
    state.semanticSearch.load({ fresh: true }).catch(error => window.PortalUI.toast(window.PortalI18n.text`语义搜索：${error.message || error}`, "warning")).finally(() => {
      semanticSearchReady = true;
    });
    const libraryReady = loadLibrary({ keepDetail: true, hydrateDetail: false });
    renderChat();
    await libraryReady;
    if (state.leftFace === "ccf") {
      await loadCcfVenues();
      await ensureCcfYearsForFilter();
      if (state.ccfExpandedVenue) await loadCcfYears(state.ccfExpandedVenue);
      if (state.ccfSelectedVenue && state.ccfSelectedYear) await loadCcfPapers(state.ccfSelectedVenue, state.ccfSelectedYear);
      renderTree();
      renderWorkspace();
      await loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true });
    }
    if (state.selectedId && !isCollectionMode()) {
      try {
        await loadDetail(state.selectedId, { render: true });
      } catch {
        state.selectedId = "";
        state.detail = null;
        writeUiState();
        renderWorkspace();
      }
    }
    renderAssistantContext();
    bindPaperProjectionEvents();
  }

  const {
    canRestorePaperWorkspace, currentPaperWorkspaceSnapshot, goPreviousPaperWorkspace,
    isPreviousPaperWorkspaceShortcut, readUiState, rememberPreviousPaperWorkspace, requestedChatOpenFromUrl,
    requestedChatWorkspaceIdFromUrl, requestedPaperIdFromUrl, requestedSemanticSearchIdFromUrl,
    syncPaperLocation, writeUiState,
  } = paperModule("PortalPaperLocation").createLocation({
    activeChatPaperId: (...args) => activeChatPaperId(...args),
    activeChatWorkspaceId: (...args) => activeChatWorkspaceId(...args),
    isCollectionMode: (...args) => isCollectionMode(...args),
    loadCcfPapers: (...args) => loadCcfPapers(...args), loadCcfVenues: (...args) => loadCcfVenues(...args),
    loadCcfYears: (...args) => loadCcfYears(...args), loadChatHistory: (...args) => loadChatHistory(...args),
    loadDetail: (...args) => loadDetail(...args), loadLibrary: (...args) => loadLibrary(...args),
    paperPageState, renderTree: (...args) => renderTree(...args),
    renderWorkspace: (...args) => renderWorkspace(...args), state,
    syncModeTabs: (...args) => syncModeTabs(...args),
    updateLeftFaceTabs: (...args) => updateLeftFaceTabs(...args),
  });

  const {
    applyPaneCollapse, initPaperWorkbench, setMode, syncModeTabs, togglePane, toggleReaderPanel,
    currentPaperWorkbench,
  } = paperModule("PortalPaperLayout").createLayout({
    $, activeChatPaperId: (...args) => activeChatPaperId(...args),
    activeChatWorkspaceId: (...args) => activeChatWorkspaceId(...args), alertError,
    cancelPdfRendering: (...args) => cancelPdfRendering(...args),
    ensureDetailForMode: (...args) => ensureDetailForMode(...args),
    isCollectionMode: (...args) => isCollectionMode(...args),
    loadChatHistory: (...args) => loadChatHistory(...args), loadLibrary: (...args) => loadLibrary(...args),
    paperChatDescriptor: (...args) => paperChatDescriptor(...args),
    renderWorkspace: (...args) => renderWorkspace(...args), state, writeUiState,
  });

  const {
    activeCollectorJobHasResults, collectionChatScope, collectionItems, collectionPaperIds,
    collectionVisibleCount, exportLibraryMarkdown, hashString, initSemanticSearch, isCollectionMode,
    loadLibrary, loadMoreLibrary,
  } = paperModule("PortalPaperLibrary").createLibrary({
    $, activeChatPaperId: (...args) => activeChatPaperId(...args),
    activeChatWorkspaceId: (...args) => activeChatWorkspaceId(...args), alertError,
    ccfCollectionChatScope: (...args) => ccfCollectionChatScope(...args),
    ccfCollectionPapers: (...args) => ccfCollectionPapers(...args),
    ccfPaperAuthors: (...args) => ccfPaperAuthors(...args),
    ccfVenueYearLabel: (...args) => ccfVenueYearLabel(...args),
    currentCcfEntry: (...args) => currentCcfEntry(...args),
    currentCcfVenue: (...args) => currentCcfVenue(...args),
    currentPaperIdentityReady: () => paperIdentityReady, esc,
    isSemanticSearchReady: () => semanticSearchReady, loadChatHistory: (...args) => loadChatHistory(...args),
    loadDetail: (...args) => loadDetail(...args), paperApi,
    renderAssistantContext: (...args) => renderAssistantContext(...args),
    renderChat: (...args) => renderChat(...args),
    renderCollectorJobs: (...args) => renderCollectorJobs(...args),
    renderTree: (...args) => renderTree(...args), renderWorkspace: (...args) => renderWorkspace(...args),
    state, updateChatComposerState: (...args) => updateChatComposerState(...args), writeUiState,
  });

  const {
    libraryHeaderMeta, renderLibraryMode, renderTree, syncFavoriteButtons, syncSelectedPaperInTree,
    togglePaperFavorite,
  } = paperModule("PortalPaperLibraryView").createLibraryView({
    $, bindPdfSelectionEvents: (...args) => bindPdfSelectionEvents(...args), chip: (...args) => chip(...args),
    disclosureIcon, esc, isCollectionMode, loadLibrary, paperApi, paperStateHtml,
    paperTags: (...args) => paperTags(...args), renderCcfTree: (...args) => renderCcfTree(...args), state,
    updateLeftFaceTabs: (...args) => updateLeftFaceTabs(...args),
  });

  const {
    deleteCollectorJob, loadCollectorJobs, renderCollectorJobs, selectCollectorJob, submitCollector,
  } = paperModule("PortalPaperCollector").createCollector({
    $, activeCollectorJobHasResults, alertError, esc, loadLibrary, paperApi, state, writeUiState,
  });

  const {
    applyCcfPaperListFilter, ccfCategoryKey, ccfCollectionChatScope, ccfCollectionPapers,
    ccfEntryMatchesYearFilter, ccfPaperAuthors, ccfPaperEnrichment, ccfPaperEnrichmentKey, ccfRankLabel,
    ccfVenueEntryId, ccfVenueLabel, ccfVenueMatchesFilter, ccfVenueShortName, ccfVenueYearLabel, ccfYearKey,
    currentCcfEntry, currentCcfPayload, currentCcfVenue, ensureCcfPaperEnrichment, ensureCcfYearsForFilter,
    fetchCcfYearsForVenue, fetchSelectedCcfPapers, loadCcfPapers, loadCcfVenues, loadCcfYears,
    scheduleCcfYearFilterRender,
  } = paperModule("PortalPaperCcfCatalog").createCcfCatalog({
    $, activeChatPaperId: (...args) => activeChatPaperId(...args),
    activeChatWorkspaceId: (...args) => activeChatWorkspaceId(...args), alertError, hashString,
    loadChatHistory: (...args) => loadChatHistory(...args), paperApi,
    renderCcfPaperRow: (...args) => renderCcfPaperRow(...args), renderTree,
    renderWorkspace: (...args) => renderWorkspace(...args), state,
  });

  const {
    ccfWorkspaceMeta, refreshCurrentLeftFace, registerCcfPaper, renderCcfPaperRow, renderCcfTree,
    renderCcfWorkspace, showCcfFace, showLibraryFace, updateLeftFaceTabs,
  } = paperModule("PortalPaperCcfView").createCcfView({
    $, activeChatPaperId: (...args) => activeChatPaperId(...args),
    activeChatWorkspaceId: (...args) => activeChatWorkspaceId(...args), alertError, applyCcfPaperListFilter,
    ccfCategoryKey, ccfCollectionPapers, ccfEntryMatchesYearFilter, ccfPaperAuthors, ccfPaperEnrichment,
    ccfPaperEnrichmentKey, ccfRankLabel, ccfVenueEntryId, ccfVenueLabel, ccfVenueMatchesFilter,
    ccfVenueShortName, ccfVenueYearLabel, ccfYearKey, chip: (...args) => chip(...args), currentCcfEntry,
    currentCcfPayload, currentCcfVenue, currentPaperWorkspaceSnapshot, disclosureIcon,
    ensureCcfPaperEnrichment, ensureCcfYearsForFilter, esc, loadCcfPapers, loadCcfVenues, loadCcfYears,
    loadChatHistory: (...args) => loadChatHistory(...args), loadDetail: (...args) => loadDetail(...args),
    loadLibrary, paperApi, paperStateHtml, rememberPreviousPaperWorkspace, renderTree,
    renderWorkspace: (...args) => renderWorkspace(...args), state, syncModeTabs, writeUiState,
  });

  const {
    assetsJobRunning, assetsJobStatusText, currentAssetsJob, detailSectionsLoaded, ensureDetailForMode,
    loadDetail, loadDetailSections, mergeDetailPayload, refreshAssetsProjection,
  } = paperModule("PortalPaperDetail").createDetail({
    alertError, isCollectionMode, loadChatHistory: (...args) => loadChatHistory(...args),
    loadDeepReadStatus: (...args) => loadDeepReadStatus(...args),
    loadExpStatus: (...args) => loadExpStatus(...args),
    loadReviewStatus: (...args) => loadReviewStatus(...args), paperApi,
    renderAssistantContext: (...args) => renderAssistantContext(...args),
    renderWorkspace: (...args) => renderWorkspace(...args), state, syncPaperLocation, syncSelectedPaperInTree,
    updateDeepReadControls: (...args) => updateDeepReadControls(...args), writeUiState,
  });

  const {
    chip, detailSectionLoadingHtml, needPaperHtml, paperTags, renderWorkspace, updateDeepReadControls,
  } = paperModule("PortalPaperWorkspaceView").createWorkspaceView({
    $, capturePaperReaderViewState: (...args) => capturePaperReaderViewState(...args), ccfVenueYearLabel,
    ccfWorkspaceMeta, currentCcfEntry, currentCcfVenue, esc, libraryHeaderMeta, paperApi, paperStateHtml,
    renderCcfWorkspace, renderExpMode: (...args) => renderExpMode(...args),
    renderFiguresMode: (...args) => renderFiguresMode(...args), renderLibraryMode,
    renderNotesMode: (...args) => renderNotesMode(...args),
    renderReaderMode: (...args) => renderReaderMode(...args),
    renderRefsMode: (...args) => renderRefsMode(...args),
    renderReviewMode: (...args) => renderReviewMode(...args), state, syncFavoriteButtons, syncModeTabs,
    writeUiState,
  });

  const {
    bindPdfSelectionEvents, cancelPdfRendering, capturePaperReaderViewState, maybeShowSelectionMenu,
    renderReaderMode, runSelectionAction,
  } = paperModule("PortalPaperReader").createReader({
    $, addSelectionToChatDraft: (...args) => addSelectionToChatDraft(...args), alertError, applyPaneCollapse,
    currentPaperWorkbench, detailSectionLoadingHtml, detailSectionsLoaded, esc, md, needPaperHtml,
    paperSelectionPopover, paperStateHtml, state, submitChatMessage: (...args) => submitChatMessage(...args),
  });

  const {
    createExp, createReview, deleteReview, loadDeepReadStatus, loadExpStatus, loadReviewStatus,
    refreshDeepReadProjection, refreshExpProjection, renderExpMode, renderReviewMode, startDeepRead,
  } = paperModule("PortalPaperReviews").createReviews({
    $, chip, detailSectionLoadingHtml, detailSectionsLoaded, esc, loadDetailSections, loadLibrary, md,
    needPaperHtml, paperApi, paperStateHtml, renderWorkspace, setMode, state, updateDeepReadControls,
  });

  const {
    deleteNote, openRefPaper, registerRef, renderNotesMode, renderRefsMode,
  } = paperModule("PortalPaperNotesRefs").createNotesRefs({
    $, alertError, assetsJobRunning, assetsJobStatusText, chip, currentAssetsJob, detailSectionLoadingHtml,
    detailSectionsLoaded, esc, loadDetail, loadLibrary, md, needPaperHtml, paperApi, paperStateHtml,
    renderWorkspace, setMode, state,
  });

  const {
    deleteGeneratedFigure, explainFigure, extractFigures, figureContextFromInput, figureContextFromKey,
    figureInputItems, generateFigureDraft, openFigureLightbox, pastedImageFiles, refreshFigureInputTray,
    refreshFigureProjection, renderFiguresMode, uploadFigureInputFile,
  } = paperModule("PortalPaperFigures").createFigures({
    $, alertError, assetsJobRunning, assetsJobStatusText, chip, currentAssetsJob, detailSectionLoadingHtml,
    detailSectionsLoaded, esc, loadDetailSections, md, needPaperHtml, paperApi, paperStateHtml,
    renderWorkspace, state,
  });

  const {
    activeChatPaperId, activeChatWorkspaceId, addSelectionToChatDraft, loadChatHistory, loadMe,
    paperChatDescriptor, renderAssistantContext, renderChat, submitChatMessage, updateChatComposerState,
    currentPaperChatPanel, currentPaperChatWorkspace,
  } = paperModule("PortalPaperChat").createChat({
    $, alertError, applyPaneCollapse, collectionChatScope, collectionItems, collectionPaperIds,
    collectionVisibleCount, currentPaperWorkbench, esc, isCollectionMode, md, paperApi, state,
  });

  const {
    closePaperModal, openPaperModal, submitPaper, uploadPaperRegistrationPdf,
  } = paperModule("PortalPaperRegistration").createRegistration({
    $, loadLibrary, mergeDetailPayload, paperApi, paperDialog, setMode, state, syncPaperLocation,
  });

  const {
    deleteCurrentPaper, deleteCurrentPaperPdf, openPdfUploadPicker, uploadCurrentPaperPdf,
  } = paperModule("PortalPaperRecord").createRecord({
    $, activeChatPaperId, alertError, currentPaperChatPanel, loadChatHistory, loadDeepReadStatus, loadLibrary,
    mergeDetailPayload, paperApi, renderAssistantContext, renderTree, renderWorkspace, setMode, state,
  });

  const {
    bindPaperProjectionEvents, resumePaperUpdates, suspendPaperUpdates,
  } = paperModule("PortalPaperProjection").createProjection({
    alertError, assetsJobRunning, cancelPdfRendering, ccfPaperEnrichment, ensureCcfPaperEnrichment,
    loadCollectorJobs, loadDetailSections, refreshAssetsProjection, refreshDeepReadProjection,
    refreshExpProjection, refreshFigureProjection, state,
  });

  const {
    bindEvents,
  } = paperModule("PortalPaperEvents").createEvents({
    $, activeChatPaperId, activeChatWorkspaceId, alertError, applyCcfPaperListFilter,
    canRestorePaperWorkspace, ccfYearKey, closePaperModal, createExp, createReview, currentCcfPayload,
    currentPaperWorkspaceSnapshot, deleteCollectorJob, deleteCurrentPaper, deleteCurrentPaperPdf,
    deleteGeneratedFigure, deleteNote, deleteReview, ensureCcfPaperEnrichment, explainFigure,
    exportLibraryMarkdown, extractFigures, fetchCcfYearsForVenue, fetchSelectedCcfPapers,
    figureContextFromInput, figureContextFromKey, figureInputItems, generateFigureDraft,
    goPreviousPaperWorkspace, isCollectionMode, isPreviousPaperWorkspaceShortcut, loadCcfPapers, loadCcfYears,
    loadChatHistory, loadDetail, loadLibrary, loadMoreLibrary, maybeShowSelectionMenu, openFigureLightbox,
    openPaperModal, openPdfUploadPicker, openRefPaper, pastedImageFiles, refreshCurrentLeftFace,
    refreshFigureInputTray, registerCcfPaper, registerRef, rememberPreviousPaperWorkspace,
    renderCollectorJobs, renderTree, renderWorkspace, resumePaperUpdates, runSelectionAction,
    scheduleCcfYearFilterRender, selectCollectorJob, setMode, showCcfFace, showLibraryFace, startDeepRead,
    state, submitCollector, submitPaper, suspendPaperUpdates, syncModeTabs, togglePane, togglePaperFavorite,
    toggleReaderPanel, updateLeftFaceTabs, uploadCurrentPaperPdf, uploadFigureInputFile,
    uploadPaperRegistrationPdf, writeUiState,
  });

  init().catch(alertError);
})();
