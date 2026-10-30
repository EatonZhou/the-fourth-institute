// Knowledge board: the page state, the event bindings and the start of the page.
//
// Loaded after every knowledge_board_*.js part, whose functions it calls; the
// parts read the state declared here only when they are called.

// ── Filter state ──────────────────────────────────────────────────────
const filter = {
  type: null,       // null = all types (string when set)
  projects: null,   // null = all (Set when set)
  date: "all",      // "all" | "2d" | "7d" | "30d"
  importance: 0,    // min importance (0 = no filter)
  status: "",       // "" = all | "__none__" | "open" | "done" | "dismiss" | "obsolete"
};

const state = {
  raw: null,
  // A confirmed deletion must survive older paginated/index responses.
  deletedKnowledgeIds: new Set(),
  query: "",
  selected: null,
  indexPaging: {
    pageSize: 200,
    requestSeq: 0,
    nextOffset: 0,
    total: 0,
    hasMore: false,
    prefetching: false,
    error: "",
  },
  exactQuery: { seq: 0, id: "", status: "", error: "" },
  projectDisplayDefault: "three",
  projectDisplays: {},
  projectOrder: [],
  projectResizeObservers: [],
  virtualProjects: {
    groups: [],
    heights: new Map(),
    defaultHeight: 245,
    raf: 0,
    bound: false,
    renderStart: -1,
    renderEnd: -1,
    renderTopOffset: -1,
    renderTotalHeight: -1,
    canvasEl: null,
    windowEl: null,
  },
  semanticSearch: null,
  activeSemanticSearchId: "",
  detailFileId: "",
  detailSelectedFileId: "",
  detailSelectedFileKind: "markdown",
  detailSelectedFileLinked: false,
  detailReadOnly: false,
  detailLinkedFiles: new Map(),
  detailLinkedFileProbes: new Set(),
  detailLinkDiscoverySequence: 0,
  detailExpandedFolders: new Set([""]),
  detailPath: "",
  detailRawUrl: "",
  detailDownloadUrl: "",
  detailMediaType: "",
  detailLanguage: "",
  detailText: "",
  detailDraft: "",
  detailBodyDraft: "",
  detailMetaDraft: null,
  detailEditing: false,
  detailSaving: false,
  detailDeleting: false,
  detailGraph: {
    fileId: "",
    payload: null,
    loading: false,
    positions: {},
    basePositions: null,
  },
  detailChat: {
    fileId: "",
    loading: false,
    attachments: [],
  },
  knowledgeMap: {
    open: false,
    project: "",
    type: "",
    relation: "",
    query: "",
    selectedId: "",
    connectedOnly: true,
    neighborsOnly: false,
    graph: null,
    loading: false,
    error: "",
    requestSeq: 0,
    signature: "",
    simulation: null,
    zoom: null,
    currentData: null,
    positions: new Map(),
    renderTimer: null,
    fittedSignature: "",
  },
  knowledgeRadar: { signature: "" },
  archive: {
    open: false,
    jobs: [],
    currentId: "",
    current: null,
    creating: false,
    attachments: [],
    policy: "strict",
    candidateSaveTimer: null,
    candidateDirty: false,
    candidateSaving: false,
    inputCollapsed: false,
    serviceError: "",
    localStatus: "",
  },
};
let portalUsername = "anonymous";
let detailDocumentSession = null;
let detailResourceGuest = null;
let detailPreviewRoot = null;
const knowledgePageState = window.PortalPageState.create("knowledge", {
  getUser: () => portalUsername || "anonymous",
  delay: 160,
});

const el = {
  meta: document.getElementById("meta"),
  q: document.getElementById("q"),
  reload: document.getElementById("reload"),
  semanticBtn: document.getElementById("semanticBtn"),
  semanticPanel: document.getElementById("semanticPanel"),
  semanticIdentity: document.getElementById("knowledgeSemanticIdentity"),
  knowledgeBoardBtn: document.getElementById("knowledgeBoardBtn"),
  knowledgeMapBtn: document.getElementById("knowledgeMapBtn"),
  knowledgeMapPage: document.getElementById("knowledgeMapPage"),
  knowledgeMapSub: document.getElementById("knowledgeMapSub"),
  knowledgeMapProject: document.getElementById("knowledgeMapProject"),
  knowledgeMapType: document.getElementById("knowledgeMapType"),
  knowledgeMapRelation: document.getElementById("knowledgeMapRelation"),
  knowledgeMapSearch: document.getElementById("knowledgeMapSearch"),
  knowledgeMapSuggestions: document.getElementById("knowledgeMapSuggestions"),
  knowledgeMapConnectedOnly: document.getElementById("knowledgeMapConnectedOnly"),
  knowledgeMapNeighborsOnly: document.getElementById("knowledgeMapNeighborsOnly"),
  knowledgeMapViewport: document.getElementById("knowledgeMapViewport"),
  knowledgeMapSvg: document.getElementById("knowledgeMapSvg"),
  knowledgeMapScene: document.getElementById("knowledgeMapScene"),
  knowledgeMapEdges: document.getElementById("knowledgeMapEdges"),
  knowledgeMapEdgeLabels: document.getElementById("knowledgeMapEdgeLabels"),
  knowledgeMapNodes: document.getElementById("knowledgeMapNodes"),
  knowledgeMapOverlay: document.getElementById("knowledgeMapOverlay"),
  knowledgeMapOverlayCard: document.getElementById("knowledgeMapOverlayCard"),
  knowledgeMapLegend: document.getElementById("knowledgeMapLegend"),
  knowledgeMapZoomIn: document.getElementById("knowledgeMapZoomIn"),
  knowledgeMapZoomOut: document.getElementById("knowledgeMapZoomOut"),
  knowledgeMapFit: document.getElementById("knowledgeMapFit"),
  knowledgeMapSide: document.getElementById("knowledgeMapSide"),
  knowledgeRadarPage: document.getElementById("knowledgeRadarPage"),
  knowledgeRadarSub: document.getElementById("knowledgeRadarSub"),
  knowledgeRadarChart: document.getElementById("knowledgeRadarChart"),
  knowledgeRadarMetrics: document.getElementById("knowledgeRadarMetrics"),
  knowledgeRadarSignalCount: document.getElementById("knowledgeRadarSignalCount"),
  knowledgeRadarSignals: document.getElementById("knowledgeRadarSignals"),
  knowledgeRadarProjectCount: document.getElementById("knowledgeRadarProjectCount"),
  knowledgeRadarProjects: document.getElementById("knowledgeRadarProjects"),
  filterBtn: document.getElementById("filterBtn"),
  filterPanel: document.getElementById("filterPanel"),
  fpStatus: document.getElementById("fpStatus"),
  fpType: document.getElementById("fpType"),
  fpProjects: document.getElementById("fpProjects"),
  filterSummary: document.getElementById("knowledgeFilterSummary"),
  grid: document.getElementById("grid"),
  empty: document.getElementById("empty"),
  modal: document.getElementById("modal"),
    mTitle: document.getElementById("mTitle"),
    mResourceIdentity: document.getElementById("mResourceIdentity"),
    mSub: document.getElementById("mSub"),
  mFiles: document.getElementById("mFiles"),
  mGraph: document.getElementById("mGraph"),
  mGraphMeta: document.getElementById("mGraphMeta"),
  mText: document.getElementById("mText"),
  mDownload: document.getElementById("mDownload"),
  mDelete: document.getElementById("mDelete"),
  mEdit: document.getElementById("mEdit"),
  mCancelEdit: document.getElementById("mCancelEdit"),
  mSave: document.getElementById("mSave"),
  mClose: document.getElementById("mClose"),
  detailChatWorkspace: document.getElementById("detailChatWorkspace"),
  archiveBtn: document.getElementById("archiveBtn"),
  archiveModal: document.getElementById("archiveModal"),
  archiveClose: document.getElementById("archiveClose"),
  archiveClear: document.getElementById("archiveClear"),
  archiveBody: document.getElementById("archiveBody"),
  archiveTitle: document.getElementById("archiveTitle"),
  archiveSub: document.getElementById("archiveSub"),
  archiveJobCount: document.getElementById("archiveJobCount"),
  archiveInputToggle: document.getElementById("archiveInputToggle"),
  archiveTitleInput: document.getElementById("archiveTitleInput"),
  archiveProjectHint: document.getElementById("archiveProjectHint"),
  archiveSourceText: document.getElementById("archiveSourceText"),
  archiveFileInput: document.getElementById("archiveFileInput"),
  archiveSourceComposer: document.getElementById("archiveSourceComposer"),
  archiveSourceTray: document.getElementById("archiveSourceTray"),
  archiveSourceMeta: document.getElementById("archiveSourceMeta"),
  archiveAttach: document.getElementById("archiveAttach"),
  archivePolicy: document.getElementById("archivePolicy"),
  archiveTextField: document.getElementById("archiveTextField"),
  archiveCreate: document.getElementById("archiveCreate"),
  archiveJobList: document.getElementById("archiveJobList"),
  archiveDrafts: document.getElementById("archiveDrafts"),
  archiveApply: document.getElementById("archiveApply"),
  archiveCandidates: document.getElementById("archiveCandidates"),
  archiveDraftList: document.getElementById("archiveDraftList"),
  archiveChatWorkspace: document.getElementById("archiveChatWorkspace"),
};

const knowledgeBoardShell = window.PortalBoardShell.create({ api: { cache: "default" } });
const knowledgeApi = knowledgeBoardShell.api;
const detailModalController = window.PortalUI.createDialogController({
  modal: el.modal,
  initialFocus: () => el.mClose,
  backdropClose: true,
  escapeClose: true,
  requestClose: () => closeModal(),
  dirty: () => detailDocumentSession?.snapshot?.().dirty,
  discardMessage: "放弃未保存的修改？",
  onClose: finalizeDetailClose,
});
const detailHistory = window.PortalResourceDetail.createDetailHistory({
  owner: `resource-board:${window.location.pathname}`,
  parameter: "item",
});
const archiveDialogController = window.PortalUI.createDialogController({
  modal: el.archiveModal,
  backdropClose: true,
  escapeClose: true,
  onClose: finalizeArchiveClose,
});

let detailChatPanel = null;
let detailChatWorkspace = null;
let archiveChatPanel = null;
let archiveChatWorkspace = null;
let archiveChatRefresh = null;
let detailGraphDrag = null;
let detailGraphPan = null;
let detailGraphSuppressClickId = "";
let projectDragId = "";
let semanticSearchReady = false;
const knowledgeMapSearchPopover = window.PortalOverlay.bindPopover({
  trigger: el.knowledgeMapSearch,
  anchor: el.knowledgeMapSearch.closest(".knowledgeMapSearchWrap"),
  panel: el.knowledgeMapSuggestions,
  placement: "bottom-start",
  ariaHaspopup: "listbox",
  portal: true,
  closeOnOutside: true,
  closeOnEscape: true,
  restoreFocusOnEscape: true,
});

// ── Event bindings ────────────────────────────────────────────────────
initChatWorkspaces();

knowledgeBoardShell.bindSearch({
  input: el.q,
  normalize: value => value.trim(),
  onChange: value => {
    state.query = value;
    el.q.title = value ? window.PortalI18n.text`当前本地筛选：${value}` : window.PortalI18n.t("本地筛选标题、预览或 Knowledge ID");
    state.activeSemanticSearchId = "";
    state.semanticSearch?.clearActive?.({ notify: false });
    writeKnowledgeUiStateSoon({
      query: state.query,
      view: activeKnowledgeView(),
      activeSemanticSearchId: state.activeSemanticSearchId || "",
      filter: filterSnapshot(),
      projectDisplayDefault: state.projectDisplayDefault,
      projectDisplays: state.projectDisplays,
      projectOrder: state.projectOrder,
    });
    renderAll();
    resolveExactKnowledgeQuery(value);
  },
});
el.reload.addEventListener("click", async () => {
  try {
    await loadIndex({ refresh: true });
    if (state.knowledgeMap.open || state.knowledgeMap.graph) {
      await loadKnowledgeGraph({ refresh: true });
    }
  } catch (error) {
    alertError(error);
  }
});
el.knowledgeBoardBtn.addEventListener("click", () => setKnowledgeView("board"));
el.knowledgeMapBtn.addEventListener("click", () => setKnowledgeView("map"));
el.knowledgeMapProject.addEventListener("change", () => {
  state.knowledgeMap.project = el.knowledgeMapProject.value || "";
  state.knowledgeMap.selectedId = "";
  state.knowledgeMap.neighborsOnly = false;
  renderKnowledgeMap({ force: true });
});
el.knowledgeMapType.addEventListener("change", () => {
  state.knowledgeMap.type = el.knowledgeMapType.value || "";
  state.knowledgeMap.selectedId = "";
  state.knowledgeMap.neighborsOnly = false;
  renderKnowledgeMap({ force: true });
});
el.knowledgeMapRelation.addEventListener("change", () => {
  state.knowledgeMap.relation = el.knowledgeMapRelation.value || "";
  renderKnowledgeMap({ force: true });
});
el.knowledgeMapSearch.addEventListener("input", () => {
  state.knowledgeMap.query = el.knowledgeMapSearch.value.trim();
  renderKnowledgeMapSuggestions();
});
el.knowledgeMapSearch.addEventListener("keydown", event => {
  if (event.key !== "Enter") return;
  const first = el.knowledgeMapSuggestions.querySelector("[data-map-suggestion]");
  if (!first) return;
  event.preventDefault();
  revealKnowledgeMapNode(first.dataset.mapSuggestion || "");
});
el.knowledgeMapSuggestions.addEventListener("click", event => {
  const button = event.target.closest("[data-map-suggestion]");
  if (!button) return;
  revealKnowledgeMapNode(button.dataset.mapSuggestion || "");
});
el.knowledgeMapConnectedOnly.addEventListener("click", () => {
  state.knowledgeMap.connectedOnly = !state.knowledgeMap.connectedOnly;
  renderKnowledgeMap({ force: true });
});
el.knowledgeMapNeighborsOnly.addEventListener("click", () => {
  if (!state.knowledgeMap.selectedId) return;
  state.knowledgeMap.neighborsOnly = !state.knowledgeMap.neighborsOnly;
  renderKnowledgeMap({ force: true });
});
el.knowledgeMapZoomIn.addEventListener("click", () => {
  if (!state.knowledgeMap.zoom) return;
  window.d3.select(el.knowledgeMapSvg)
    .transition()
    .duration(160)
    .call(state.knowledgeMap.zoom.scaleBy, 1.3);
});
el.knowledgeMapZoomOut.addEventListener("click", () => {
  if (!state.knowledgeMap.zoom) return;
  window.d3.select(el.knowledgeMapSvg)
    .transition()
    .duration(160)
    .call(state.knowledgeMap.zoom.scaleBy, 1 / 1.3);
});
el.knowledgeMapFit.addEventListener("click", () => fitKnowledgeMap());
el.knowledgeMapOverlay.addEventListener("click", event => {
  if (event.target.closest("[data-map-retry]")) {
    state.knowledgeMap.error = "";
    loadKnowledgeGraph({ refresh: true }).catch(alertError);
    return;
  }
  if (event.target.closest("[data-map-show-isolated]")) {
    state.knowledgeMap.connectedOnly = false;
    renderKnowledgeMap({ force: true });
  }
});
el.knowledgeMapSide.addEventListener("click", (event) => {
  const selectBtn = event.target.closest("[data-map-select]");
  if (selectBtn) {
    selectKnowledgeMapNode(selectBtn.dataset.mapSelect || "", { focus: true });
    return;
  }
  const focusBtn = event.target.closest("[data-map-focus]");
  if (focusBtn) {
    focusKnowledgeMapNode(focusBtn.dataset.mapFocus || "");
    return;
  }
  const openBtn = event.target.closest("[data-map-open-detail]");
  if (!openBtn) return;
  const fileId = openBtn.dataset.mapOpenDetail || "";
  openDetail(fileId);
});
let knowledgeMapResizeTimer = null;
window.addEventListener("resize", () => {
  if (!state.knowledgeMap.open || !state.knowledgeMap.graph) return;
  window.clearTimeout(knowledgeMapResizeTimer);
  knowledgeMapResizeTimer = window.setTimeout(
    () => renderKnowledgeMap({ force: true }),
    160,
  );
});
el.archiveBtn.addEventListener("click", openArchiveModal);
el.archiveClose.addEventListener("click", closeArchiveModal);
el.archiveClear.addEventListener("click", () => clearArchiveWorkspace().catch(alertError));
el.archiveCreate.addEventListener("click", () => createArchiveJob().catch(alertError));
el.archiveInputToggle.addEventListener("click", () => {
  state.archive.inputCollapsed = !state.archive.inputCollapsed;
  renderArchiveInputCollapse();
});
el.archiveAttach.addEventListener("click", () => el.archiveFileInput.click());
el.archiveFileInput.addEventListener("change", () => {
  addArchiveFiles(el.archiveFileInput.files).catch(alertError).finally(() => { el.archiveFileInput.value = ""; });
});
el.archiveSourceTray.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-archive-remove-attachment]");
  if (!btn) return;
  removeArchiveAttachment(btn.dataset.archiveRemoveAttachment || "");
});
el.archiveSourceComposer.addEventListener("dragover", (event) => {
  window.PortalChatWorkspace.handleAttachmentDragOver(event);
});
el.archiveSourceComposer.addEventListener("drop", (event) => {
  window.PortalChatWorkspace.handleAttachmentDrop(event, {
    onFiles: files => addArchiveFiles(files).catch(alertError),
  });
});
el.archiveSourceText.addEventListener("paste", (event) => {
  window.PortalChatWorkspace.handleAttachmentPaste(el.archiveSourceText, event, {
    onFiles: files => addArchiveFiles(files).catch(alertError),
  });
});
el.archivePolicy.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-archive-policy]");
  if (!btn) return;
  state.archive.policy = btn.dataset.archivePolicy || "strict";
  renderArchivePolicy();
});
el.archiveJobList.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-archive-job]");
  if (!btn) return;
  const nextId = btn.dataset.archiveJob || "";
  const switchJob = () => loadArchiveJob(nextId).catch(alertError);
  if (state.archive.candidateDirty) {
    clearTimeout(state.archive.candidateSaveTimer);
    state.archive.candidateSaveTimer = null;
    saveArchiveCandidates({ silent: true }).then(switchJob).catch(alertError);
    return;
  }
  switchJob();
});
el.archiveCandidates.addEventListener("change", (event) => {
  if (!event.target.closest("[data-candidate-field]")) return;
  scheduleArchiveCandidateAutosave();
});
el.archiveCandidates.addEventListener("input", (event) => {
  if (!event.target.closest("[data-candidate-field]")) return;
  scheduleArchiveCandidateAutosave();
});
el.archiveDraftList.addEventListener("focusin", (event) => {
  const input = event.target.closest("[data-draft-content-id]");
  if (!input) return;
  input.dataset.originalContent = input.value;
});
el.archiveDraftList.addEventListener("focusout", (event) => {
  const input = event.target.closest("[data-draft-content-id]");
  if (!input || input.disabled) return;
  const draftId = input.dataset.draftContentId || "";
  if (input.value === (input.dataset.originalContent || "")) return;
  input.dataset.originalContent = input.value;
  saveArchiveDraftContent(draftId, input.value).catch(alertError);
});
el.archiveDrafts.addEventListener("click", () => generateArchiveDrafts().catch(alertError));
el.archiveApply.addEventListener("click", () => applyArchiveDrafts().catch(alertError));
el.mClose.addEventListener("click", closeModal);
el.mDelete.addEventListener("click", () => deleteDetail());
el.mEdit.addEventListener("click", () => setDetailEditing(true));
el.mCancelEdit.addEventListener("click", () => setDetailEditing(false));
el.mSave.addEventListener("click", () => saveDetail());
const openDetailGraphNode = (fileId) => {
  if (!fileId || fileId === state.detailFileId) return;
  openDetail(fileId).catch(alertError);
};
el.mGraph.addEventListener("click", (event) => {
  const target = event.target.closest?.("[data-detail-graph-open]");
  const fileId = target?.dataset?.detailGraphOpen || "";
  if (fileId && detailGraphSuppressClickId === fileId) {
    detailGraphSuppressClickId = "";
    return;
  }
  openDetailGraphNode(fileId);
});
el.mGraph.addEventListener("pointerdown", (event) => {
  const target = event.target.closest?.("[data-detail-graph-open]");
  const fileId = target?.dataset?.detailGraphOpen || "";
  const svg = event.target.closest?.("svg");
  if (!svg || event.button !== 0) return;
  if (!fileId) {
    detailGraphPan = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: el.mGraph.scrollLeft,
      scrollTop: el.mGraph.scrollTop,
    };
    el.mGraph.classList.add("panning");
    el.mGraph.setPointerCapture?.(event.pointerId);
    event.preventDefault();
    return;
  }
  detailGraphDrag = {
    fileId,
    pointerId: event.pointerId,
    svg,
    start: detailGraphPointerPosition(event, svg),
    moved: false,
  };
  svg.setPointerCapture?.(event.pointerId);
});
el.mGraph.addEventListener("pointermove", (event) => {
  const pan = detailGraphPan;
  if (pan && pan.pointerId === event.pointerId) {
    el.mGraph.scrollLeft = pan.scrollLeft - (event.clientX - pan.startX);
    el.mGraph.scrollTop = pan.scrollTop - (event.clientY - pan.startY);
    event.preventDefault();
    return;
  }
  const drag = detailGraphDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const point = detailGraphPointerPosition(event, drag.svg);
  if (Math.hypot(point.x - drag.start.x, point.y - drag.start.y) > 2) drag.moved = true;
  state.detailGraph.positions = { ...(state.detailGraph.positions || {}), [drag.fileId]: point };
  syncDetailGraphSvgPositions(drag.svg);
  event.preventDefault();
});
const finishDetailGraphDrag = (event) => {
  const pan = detailGraphPan;
  if (pan && pan.pointerId === event.pointerId) {
    el.mGraph.releasePointerCapture?.(event.pointerId);
    el.mGraph.classList.remove("panning");
    detailGraphPan = null;
    return;
  }
  const drag = detailGraphDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  drag.svg.releasePointerCapture?.(event.pointerId);
  detailGraphDrag = null;
  detailGraphSuppressClickId = drag.fileId;
  setTimeout(() => {
    if (detailGraphSuppressClickId === drag.fileId) detailGraphSuppressClickId = "";
  }, 120);
  if (!drag.moved) openDetailGraphNode(drag.fileId);
};
el.mGraph.addEventListener("pointerup", finishDetailGraphDrag);
el.mGraph.addEventListener("pointercancel", finishDetailGraphDrag);
el.mGraph.addEventListener("keydown", (event) => {
  if (!['Enter', ' '].includes(event.key)) return;
  const target = event.target.closest?.("[data-detail-graph-open]");
  if (!target) return;
  event.preventDefault();
  target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
});
el.mText.addEventListener("dblclick", (event) => {
  const target = event.target.closest("[data-copy-kb-id]");
  if (!target) return;
  copyKnowledgeIdFromDetail(target).catch(alertError);
});
knowledgeBoardShell.bindFilter({
  button: el.filterBtn,
  panel: el.filterPanel,
  summaryTarget: el.filterSummary,
  summary: knowledgeFilterSummaryItems,
  onClear: clearKnowledgeFilters,
  isActive: () => filter.status !== ""
    || filter.type !== null
    || filter.projects !== null
    || filter.date !== "all"
    || filter.importance > 0,
});

window.addEventListener("portal:knowledge-filter-request", (event) => {
  if (document.body.dataset.resourceDetailReturn) return;
  event.preventDefault();
  applyKnowledgeMatrixFilter(event.detail?.type || "", event.detail?.project || "");
});
window.addEventListener("popstate", () => {
  applyDetailHistoryState().catch(alertError);
});

// Status filter
el.fpStatus.addEventListener("change", (e) => {
  if (e.target.name === "fp_status") { filter.status = e.target.value; saveKnowledgeUiState(); renderAll(); knowledgeBoardShell.syncFilterState(); }
});

// Date filter
document.getElementById("fpDate").addEventListener("change", (e) => {
  if (e.target.name === "fp_date") { filter.date = e.target.value; saveKnowledgeUiState(); renderAll(); knowledgeBoardShell.syncFilterState(); }
});

// Importance filter
document.getElementById("fpImportance").addEventListener("change", (e) => {
  if (e.target.name === "fp_imp") { filter.importance = parseInt(e.target.value, 10); saveKnowledgeUiState(); renderAll(); knowledgeBoardShell.syncFilterState(); }
});

async function init() {
  initDetailDocumentSession();
  initDetailResourceGuest();
  const currentUser = await window.PortalUI.loadCurrentUser().catch(() => ({ username: "anonymous" }));
  portalUsername = currentUser.username || "anonymous";
  detailChatWorkspace?.restoreDraft?.();
  archiveChatWorkspace?.restoreDraft?.();
  applyKnowledgeUiState();
  const targetItemId = initialKnowledgeItemId();
  if (targetItemId && detailResourceGuest.embedded()) {
    document.body.classList.remove("knowledgeInitializing");
    await openDetail(targetItemId, { updateHistory: false }).catch(error => {
      detailResourceGuest.failed({ error: error.message || String(error) });
      alertError(error);
    });
    return;
  }
  const requestedSearchId = requestedSemanticSearchId();
  if (requestedSearchId) {
    state.activeSemanticSearchId = requestedSearchId;
    state.query = "";
    state.knowledgeMap.open = false;
  }
  const requestedFilter = requestedKnowledgeMatrixFilter();
  if (requestedFilter.hasType) filter.type = requestedFilter.type || null;
  if (requestedFilter.hasProject) {
    filter.projects = requestedFilter.project ? new Set([requestedFilter.project]) : null;
  }
  el.q.value = state.query || "";
  syncKnowledgeView();
  const semanticSearch = initSemanticSearch();
  await semanticSearch.load({ fresh: true });
  semanticSearchReady = true;
  await loadIndex();
  document.body.classList.remove("knowledgeInitializing");
  if (targetItemId) openDetail(targetItemId, { updateHistory: false }).catch(alertError);
}

init();
