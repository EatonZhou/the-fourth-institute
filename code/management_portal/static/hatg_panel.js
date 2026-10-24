(() => {
  "use strict";

  function createHatgRuntime() {

  let hatgComponentRoot = document;
  let hatgComponentOptions = {};
  // The panel's first read of its Lineage catalog has finished (successfully or not).
  let lineageCatalogHydrated = false;
  // Dialogs this runtime moved to document.body still belong to it. Several
  // HATG runtimes can be mounted with the same element IDs, so they are searched
  // before the document: getElementById would return the first runtime's copy.
  const detachedRoots = new Set();
  const $id = id => {
    const selector = `#${CSS.escape(id)}`;
    const found = hatgComponentRoot.querySelector?.(selector);
    if (found) return found;
    for (const root of detachedRoots) {
      const match = root.matches?.(selector) ? root : root.querySelector?.(selector);
      if (match) return match;
    }
    return document.getElementById(id) || null;
  };
  const $query = selector => hatgComponentRoot.querySelector(selector);
  const $queryAll = selector => hatgComponentRoot.querySelectorAll(selector);
  const $eventRoot = () => hatgComponentRoot;
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const apiGet = path => window.PortalAPI.get(path, { cache: "no-store" });
  const apiPost = (path, body) => window.PortalAPI.post(path, body);
  const societyService = window.PortalSocietyService.createClient({
    get: apiGet,
    getRevision: id => state.lineage?.lineage_id === id ? state.lineage.revision : undefined,
    onResidentsDeleted: retireDeletedResidents,
    onConflict: ({ lineageId, lineage, error }) => {
      if (state.lineage?.lineage_id !== lineageId) return;
      const roots = [hatgComponentRoot, ...detachedRoots];
      let retainedDrafts = 0;
      roots.forEach(root => root.querySelectorAll?.('[data-program-draft-dirty="true"]').forEach(form => {
        retainedDrafts += 1;
        form.dataset.programDraftConflict = "true";
        if (lineage) definitionRevisions.set(form, lineage.revision);
        else definitionRevisions.delete(form);
      }));
      if (lineage) {
        // Keep the user's fields through this read. Only an explicit save may
        // submit them again against the refreshed definition.
        programFeature.setWireDraft(null);
        commitLineage(lineage);
      }
      if (retainedDrafts) {
        error.message = window.PortalI18n.t(lineage
          ? "图已被其他操作修改，本次修改未保存。草稿已保留，请核对后再次点击保存。"
          : "本次修改未保存，草稿已保留；最新版本读取失败。再次点击保存时会先重读，再尝试保存。");
      }
      setSyncStatus(error.message, "error");
    },
  });
  function retireDeletedResidents(ids) {
    window.PortalResidentMenu?.retireResidents(ids);
    return hatgComponentOptions.onResidentsDeleted?.(ids);
  }
  const residentDiscovery = window.PortalResidentDiscoveryService.createClient({ post: apiPost });
  const clone = value => JSON.parse(JSON.stringify(value));
  const normalizeOptionalLimit = (value, maximum = Number.MAX_SAFE_INTEGER) => (
    window.PortalLineageProgram?.normalizeOptionalLimit?.(value, maximum)
    ?? null
  );
  const definitionRevisions = new WeakMap();
  const FIT_ALL_VIEW = Object.freeze({ duration: 280, maxScale: 1 });
  const DEFAULT_TEMPLATE_TIMEOUT_MS = 360_000;


  const stateFactory = window.PortalHatgPanelState;
  if (!stateFactory) throw new Error("PortalHatgPanelState is not loaded.");
  const controllerFactory = window.PortalLineageWorkbenchController;
  if (!controllerFactory) throw new Error("PortalLineageWorkbenchController is not loaded.");
  // The panel's parts are separate scripts. Each one registers a factory that
  // receives, by name, every runtime value it uses.
  function hatgModule(name) {
    const module = window[name];
    if (!module) throw new Error(`${name} is not loaded.`);
    return module;
  }
  const state = stateFactory.createState();

  const {
    RELATION_LABEL, currentNode, displayedProgram, currentProgram, displayProgramComponentById,
    functionBlockInstanceById, programResourceAddressHtml, programComponentById,
    displayProgramWireById, isProgramComponent, isFunctionBlockInstance, candidateForkOf,
    activeSeeds, displaySeedById, seedById, graphSelectedId, graphSelectedEdgeId,
    displayGraphItemById, graphItemById, displayNodeById, displayEdgeById, nodeById, edgeById,
    isPacketOperatorComponent, isOperatorExecutorNode, packetOperatorImplementation,
    lineageEdgeReferencesResident, manualProgramEventKey, programEventSourceRuntime,
    programEffectRuntime, programExecutionExplanation, relationFamily, relationVisibleInView,
    fillWorkspaceSelect, selectedBoundWorkspace, effectiveNodeStatus, setNodeRuntimeStatus,
    nodeResidentId, nodeIsDerived, runningLineageNodes,
  } = hatgModule("PortalHatgGraphModel").createGraphModel({
    esc, state,
  });


  const {
    clearGraphNodeSearch, bindGraphNodeSearch, refreshGraphNodeSearchResults,
    destroyGraphNodeSearch,
  } = hatgModule("PortalHatgGraphSearch").createGraphSearch({
    $id, $query, activeSeeds, currentPanelActive: () => panelActive, esc, focusGraphNode,
    nodeResidentId, residentDiscovery, state, toast,
    // The canvas projection is created after this part.
    workspaceGraphModel: (...args) => workspaceGraphModel(...args),
  });

  const programFeature = state.features.program;

  // The Lineage copy and the program edits come before the parts that change
  // the Lineage through them.
  const {
    lineageEditQueue, activateLineageEdits, flushLineageLayoutSave, saveLineageLayout, commitLineage,
    applyLineageEventProjection,
  } = hatgModule("PortalHatgLineageState").createLineageState({
    activeSeeds, bindResidentStateCollection, clone,
    currentTemplateController: () => templateController, nodeResidentId, programFeature,
    reconcileGraphInteractionState, renderControls, renderGraphRuntimePresentation,
    renderLineageSurface, setSyncStatus, societyService, state, toast, updateLineageSummary,
    // The program inspector is created after this part and commits through it.
    renderProgramInspector: (...args) => renderProgramInspector(...args),
  });

  const {
    applyProgramMutation, executeHatgInstruction, renderProgramWireHint, cancelProgramWireDraft,
    handleProgramPortClick, createProgramComponentFromDrop, bindPrimitiveLibrary,
  } = hatgModule("PortalHatgProgramEdits").createProgramEdits({
    $eventRoot, $id, $queryAll, commitLineage, currentProgram, displayGraphItemById,
    displayProgramWireById, graphSelectedId, lineageEditQueue, programComponentById,
    programFeature, renderGraph, renderLineageSurface, societyService, state, toast,
    // Layout, the Resident dialogs and the program inspector are created after
    // this part. An edit first saves the pending view and afterwards separates new
    // objects; a wire first saves the inspector's open draft.
    flushHatgViewPreferenceSave: (...args) => flushHatgViewPreferenceSave(...args),
    nextNodePosition: (...args) => nextNodePosition(...args),
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    saveProgramComponentFromInspector: (...args) => saveProgramComponentFromInspector(...args),
    separateGraphObject: (...args) => separateGraphObject(...args),
  });


  const {
    familyMemberTag, functionBlockInstanceFailures, functionBlockInstanceUnacknowledgedFailures,
    acknowledgeFunctionBlockFailures, functionBlockElements, instanceInnerObjects,
    instanceManualSources, functionBlockInstanceTriggersHtml, expandedFunctionBlockIds,
    setFunctionBlockExpanded, functionBlockProjection, graphObjectTitle, familyRoles,
    familyForFacade, familyExpanded, foldedFamilyObjects, edgesThroughFoldedFamilies,
    selectedFamilyPigeonId, familyPigeonLinks, familyTone, familyObjectIds, familyMembership,
    focusedFamily, familySpotlight, familyForNewResident, toggleFamilyFacade, toggleFamilyView,
    showFamilyAlone, revealUnfamilizedMember, removeFamilyInstance,
  } = hatgModule("PortalHatgFamilyBlocks").createFamilyBlocks({
    applyProgramMutation, currentPageStateController: () => pageStateController, displayNodeById,
    esc, executeHatgInstruction, functionBlockInstanceById, graphSelectedId,
    isFunctionBlockInstance, nodeById, nodeResidentId, programComponentById,
    programEffectRuntime, programEventSourceRuntime, programExecutionExplanation, programFeature,
    renderLineageSurface, state,
    // Layout is created after this part and reads its Family focus.
    scheduleHatgViewPreferenceSave: (...args) => scheduleHatgViewPreferenceSave(...args),
    separateGraphObject: (...args) => separateGraphObject(...args),
    // The canvas projection is created after this part.
    graphVisibleModel: (...args) => graphVisibleModel(...args),
  });

  const {
    programEventSourceStatus, syncProgramRunAsGuidance, programComponentPaused,
    programComponentSwitchHtml, syncProgramComponentSwitch, toggleProgramComponent,
    programExecutionExplanationHtml, nodeExecutionGate, nodeIntakeState, nodeWorkPresentation,
    hatgNodeControl, hatgNodeHoverActions, nodeMessageCountText, nodeContextPressure,
    nodeContextHeatBadge, applyNodeMessageCounts,
  } = hatgModule("PortalHatgNodeStatus").createNodeStatus({
    applyProgramMutation, displayNodeById, effectiveNodeStatus, esc, executeHatgInstruction,
    familyRoles, functionBlockInstanceById, instanceManualSources, isFunctionBlockInstance,
    isProgramComponent, manualProgramEventKey, nodeById, nodeResidentId, normalizeOptionalLimit,
    programEffectRuntime, programEventSourceRuntime, programExecutionExplanation,
    renderGraphRuntimePresentation, state,
  });


  const {
    refreshFunctionBlockMenu, openFunctionBlockMenu, functionBlockSourceTitle,
    openEffectRecipientsEditor,
  } = hatgModule("PortalHatgFunctionBlockMenu").createFunctionBlockMenu({
    acknowledgeFunctionBlockFailures, applyProgramMutation,
    currentTemplateController: () => templateController, definitionRevisions, displayNodeById,
    displayProgramComponentById, esc, executeHatgInstruction,
    expandedFunctionBlockIds, familyExpanded, familyForFacade, familyRoles, formatDate,
    functionBlockElements, functionBlockInstanceById, functionBlockInstanceFailures,
    functionBlockInstanceTriggersHtml, functionBlockInstanceUnacknowledgedFailures,
    graphObjectTitle, instanceInnerObjects, nodeIntakeState, programEffectRuntime,
    programEventSourceRuntime, programFeature, removeFamilyInstance,
    selectNode, setFunctionBlockExpanded, state, toast, toggleFamilyView,
    // The program inspector and Resident dialogs are created after this part
    // and use its Menu.
    emitManualProgramEvent: (...args) => emitManualProgramEvent(...args),
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    residentMenuHandlers: (...args) => residentMenuHandlers(...args),
  });

  const {
    effectiveLeaderNode, submitLeaderRequest,
  } = hatgModule("PortalHatgLeader").createLeader({
    $id, closeModal, commitLineage, effectiveNodeStatus, nodeById, nodeResidentId,
    sendPromptToNode, setBusy, societyService, state, toast,
  });


  const {
    seedTypeLabel, renderSeedInspector, positionSeedInspector, dismissSeedSelection, selectSeed,
    setSeedActionChoice, openSeedPromoteModal, submitSeedPromotion, openSeedDismissModal,
    submitSeedDismissal,
  } = hatgModule("PortalHatgSeeds").createSeeds({
    $id, $queryAll, closeModal, commitLineage, displayNodeById, displaySeedById, esc,
    executeHatgInstruction, loadResultFile, loadResults, nodeById, openModal,
    programComponentById, programFeature, renderControls, renderGraph, renderGraphSelection,
    renderLineageHeader, renderLineageSurface, seedById, selectNode, setBusy, setSyncStatus,
    state, toast,
  });


  const {
    renderNodeProviderFields, openNodeModal, nextNodePosition, submitNodeForm,
    openNodeSettingsModal, residentMenuHandlers, absorbNodeIntoTarget, keepAbsorbedSourceNode,
    deleteAbsorbedSourceNode, updateBranchAccountHint, configureBranchProvider,
    resetBranchModalState, openBranchModal, createQuickFork, submitBranch,
  } = hatgModule("PortalHatgResidentDialogs").createResidentDialogs({
    $id, bindResidentStateCollection, clearNodeSelection, clone, closeModal, commitLineage,
    compactText, currentHatgComponentOptions: () => hatgComponentOptions, currentNode,
    displayedProgram, displayNodeById, esc, familyForFacade, familyForNewResident,
    flushLineageLayoutSave, formatDate, lineageEdgeReferencesResident, loadLineagePageData,
    loadResults, nodeById, nodeIntakeState, nodeResidentId, openModal, programFeature,
    removeFamilyInstance, renderGraph, renderLineageSurface, revealUnfamilizedMember,
    selectLineage, selectNode, setBusy, setNodeRuntimeStatus, setSyncStatus, societyService,
    state, toast,
  });

  const {
    renderLineageProjectOptions, subgraphMoveBusy, renewSubgraphMoveIntent, previewSubgraphMove,
    loadSubgraphMoveTargets, openSubgraphMove, submitSubgraphMove, loadLineageRunAsUsers,
    openCreateLineageModal, openEditLineageModal, submitLineageForm, deleteCurrentLineage,
  } = hatgModule("PortalHatgLineageDialogs").createLineageDialogs({
    $id, clearCanvasSelection, clone, closeModal,
    currentHatgComponentOptions: () => hatgComponentOptions, displayNodeById, esc,
    isFunctionBlockInstance, loadLineagePageData, loadLineages, openModal, openNodeModal,
    renderControls, renderLineageSurface, selectLineage, setBusy, societyService, state, toast,
    updateLineageSummary,
  });



  const {
    hatgViewPreferences, HATG_WORLD_SIZE, AUTO_LAYOUT_VIEW, loadHatgViewPreference,
    scheduleHatgViewPreferenceSave, flushHatgViewPreferenceSave, syncWorkspaceNodePosition,
    syncGraphNodePosition, separateGraphObject, separateGraphObjects, autoLayoutScope,
    renderAutoLayoutControl, readableAutoLayoutOptions, autoLayoutGraph,
  } = hatgModule("PortalHatgLayout").createLayout({
    $id, clone, commitLineage, focusedFamily, functionBlockElements, functionBlockInstanceById,
    graphItemById, renderControls, renderGraph, saveLineageLayout, state, toast,
    // The canvas projection is created after this part.
    graphVisibleModel: (...args) => graphVisibleModel(...args),
  });

  const {
    workspaceGraphModel, graphVisibleModel,
  } = hatgModule("PortalHatgGraphProjection").createGraphProjection({
    activeSeeds, autoLayoutScope, displayedProgram, edgesThroughFoldedFamilies,
    effectiveLeaderNode, familyExpanded, familyForFacade, familyMembership, familyObjectIds,
    familyPigeonLinks, familySpotlight, familyTone, focusedFamily, foldedFamilyObjects,
    functionBlockProjection, graphSelectedId, hatgViewPreferences, programExecutionExplanation,
    readableAutoLayoutOptions, relationFamily, relationVisibleInView, state,
  });



  const {
    renderProgramInspector, effectScriptLabel, setSeedCandidateFork,
    saveProgramComponentFromInspector, emitManualProgramEvent, resetProgramEventSource,
  } = hatgModule("PortalHatgProgramInspector").createProgramInspector({
    $id, applyProgramMutation, AUTO_LAYOUT_VIEW, candidateForkOf, clone, commitLineage,
    definitionRevisions, displayedProgram, displayNodeById, displayProgramComponentById,
    displayProgramWireById, esc, executeHatgInstruction, formatDate, isOperatorExecutorNode,
    isPacketOperatorComponent, manualProgramEventKey, nodeById, normalizeOptionalLimit,
    openEditLineageModal, openEffectRecipientsEditor, packetOperatorImplementation,
    programComponentById, programComponentPaused, programComponentSwitchHtml,
    programEffectRuntime, programEventSourceRuntime, programEventSourceStatus,
    programExecutionExplanationHtml, programFeature, programResourceAddressHtml, publishLocation,
    refreshFunctionBlockMenu, renderControls, renderGraph, renderLineageSurface, seedById,
    selectProgramComponent, setBusy, setSyncStatus, societyService, state,
    syncProgramComponentSwitch, syncProgramRunAsGuidance, toast, toggleProgramComponent,
  });

  const {
    activateHandoffMode, cancelHandoffMode, handleHandoffNodeClick, addHandoffItem,
    removeHandoffItem, syncHandoffMaterializerFields, syncHandoffSubmitLabel, submitHandoff,
    removeEditingEdge, selectRelationEdge, closeRelationInspector, renderRelationInspector,
  } = hatgModule("PortalHatgRelations").createRelations({
    $id, clone, closeModal, commitLineage, compactText, displayEdgeById, displayNodeById,
    edgeById, esc, fillWorkspaceSelect, formatBytes, loadWorkspaceGraphBindings, nodeById,
    nodeResidentId, openModal, programFeature, publishLocation, RELATION_LABEL, relationFamily,
    renderControls, renderGraph, renderGraphModeHint, renderGraphSelection, renderLineageSurface,
    renderProgramInspector, renderSeedInspector, seedById, seedTypeLabel, selectedBoundWorkspace,
    selectNode, selectSeed, setBusy, setGraphViewMode, setSeedCandidateFork, societyService,
    state, toast, uid,
  });



  let graphControlObserver = null;
  let seedInspectorObserver = null;
  function disconnectGraphObservers() {
    graphControlObserver?.disconnect();
    graphControlObserver = null;
    seedInspectorObserver?.disconnect();
    seedInspectorObserver = null;
  }
  let disposeLibraryResize = null;
  let panelActive = true;
  let pageStateController = null;
  let templateController = null;

  function uid(prefix) {
    const token = globalThis.PortalUIPrimitives.randomUUID().replace(/-/g, "").slice(0, 12);
    return `${prefix}_${token.toLowerCase()}`;
  }

  function compactText(value, limit = 100) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1).trim()}…` : text;
  }

  function formatDate(value) {
    if (!value) return "";
    return window.PortalUI.formatDateTime(value, { style: "compact" });
  }

  function formatBytes(value) {
    return window.PortalUI.formatBytes(value, {
      emptyFallback: "",
      zeroFallback: "",
      maximumUnit: "MB",
      precision: "compact",
    });
  }

  function toast(message, kind = "success", timeout = 3600, options = {}) {
    return window.PortalUI.toast(message, kind, { ...options, duration: timeout });
  }

  function setBusy(button, busy, text = "处理中…") {
    window.PortalUI.setButtonBusy(button, busy, text);
  }

  const lineageDialogControllers = new Map();
  function lineageDialogController(id) {
    if (lineageDialogControllers.has(id)) return lineageDialogControllers.get(id);
    const modal = $id(id);
    if (!modal) return null;
    const controller = window.PortalUI.createDialogController({
      modal,
      openClass: "portalDialogOpen",
      bodyClass: "agentLineageModalOpen",
      backdropClose: true,
      escapeClose: true,
      onOpen: () => { modal.hidden = false; },
      onClose: () => {
        modal.hidden = true;
        if (id === "lineageModal" && modal.dataset.portalDetached === "1" && hatgComponentRoot) {
          delete modal.dataset.portalDetached;
          hatgComponentRoot.append(modal);
          detachedRoots.delete(modal);
        }
        if (id === "branchModal") resetBranchModalState();
        if (id === "seedDismissModal") state.dismissingSeedId = "";
        if (id === "nodeAbsorptionModal") state.absorptionDecision = null;
        if (id === "handoffModal" && state.handoffMode) cancelHandoffMode();
      },
    });
    lineageDialogControllers.set(id, controller);
    return controller;
  }

  function openModal(id) {
    lineageDialogController(id)?.open();
  }

  function closeModal(id) {
    lineageDialogController(id)?.close();
  }

  function reconcileGraphInteractionState() {
    if (state.selectedProgramObjectId) {
      const selectedId = state.selectedProgramObjectId;
      const valid = Boolean(
        displayProgramComponentById(selectedId)
        || functionBlockInstanceById(selectedId)
        || displayProgramWireById(selectedId)
      );
      if (!valid) state.selectedProgramObjectId = "";
    }
    const focusId = state.graphFocusFamilyFacadeId || graphSelectedId();
    const folded = state.graphFocusMode ? foldedFamilyObjects() : null;
    const focusRemoved = state.graphFocusFamilyFacadeId
      ? !familyForFacade(state.graphFocusFamilyFacadeId)
      : !displayGraphItemById(graphSelectedId());
    // A newly folded outer Family can hide a still-existing focused member.
    // Leave that neighborhood before intersecting it with the visible graph.
    if (state.graphFocusMode && (focusRemoved || folded.residents.has(focusId) || folded.instances.has(focusId))) {
      state.graphFocusMode = false;
      state.graphFocusFamilyFacadeId = "";
      state.familyViewReturn = null;
      // The old focused facade no longer defines a restorable camera. Save
      // this replacement view so reload does not return to its former bounds.
      window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.45, persist: true }));
    }
    if (
      (state.selectedNodeId || state.selectedSeedId)
      && !displayNodeById(state.selectedNodeId)
      && !displaySeedById(state.selectedSeedId)
    ) {
      // A selected node that left the graph is not replaced by the Leader: the
      // Chat keeps its Resident and reports it deleted or no longer readable.
      clearNodeSelection();
    }
    reconcileCanvasSelection();
    if (!state.programWireDraft) return;
    if (!state.lineage) {
      programFeature.setWireDraft(null);
      return;
    }
    const programApi = window.PortalLineageProgram;
    const projection = programApi?.projectGraph?.(
      programApi?.authoredProgram?.(currentProgram()) || currentProgram(),
      state.lineage.nodes || [],
    );
    const ports = (projection?.nodes || []).flatMap(node => (
      programApi?.portsForNode?.(node) || []
    ));
    const draftKey = programApi?.portKey?.(state.programWireDraft);
    if (
      !draftKey
      || !ports.some(port => programApi.portKey(port) === draftKey)
    ) {
      programFeature.setWireDraft(null);
    }
  }


  function publishLocation(lineageId, programItemId = "") {
    return hatgComponentOptions.onLocationChange?.({
      lineageId: String(lineageId || ""),
      programItemId: String(programItemId || ""),
    });
  }

  function setSyncStatus(text, status = "idle") {
    const el = $id("graphSyncStatus");
    if (!el) return;
    el.textContent = text;
    el.dataset.state = status;
  }

  function updateLineageSummary() {
    if (!state.lineage) return;
    const summary = state.lineages.find(item => item.lineage_id === state.lineage.lineage_id);
    const statuses = {};
    state.lineage.nodes.forEach(node => {
      const status = effectiveNodeStatus(node);
      statuses[status] = (statuses[status] || 0) + 1;
    });
    const next = {
      lineage_id: state.lineage.lineage_id,
      title: state.lineage.title,
      description: state.lineage.description,
      leader_resident_id: state.lineage.leader_resident_id,
      node_count: state.lineage.nodes.length,
      edge_count: state.lineage.edges.length,
      active_seed_count: activeSeeds(state.lineage).length,
      statuses,
      revision: state.lineage.revision,
      layout_revision: state.lineage.layout_revision,
      runtime_revision: state.lineage.runtime_revision,
      created_at: state.lineage.created_at,
      updated_at: state.lineage.updated_at,
      graph_updated_at: state.lineage.graph_updated_at,
      layout_updated_at: state.lineage.layout_updated_at,
      runtime_updated_at: state.lineage.runtime_updated_at,
    };
    if (summary) Object.assign(summary, next);
    else state.lineages.unshift(next);
  }

  function renderLineageHeader() {
    const lineage = state.lineage;
    const lineageTitle = lineage?.title || window.PortalI18n.t("选择 Lineage");
    const lineageId = String(lineage?.lineage_id || "").trim();
    const cleanTitle = lineageTitle.replace(/\s*\|\s*Lineage\s*$/i, "").trim() || lineageTitle;
    window.PortalResourceHeader?.mount?.($id("activeLineageIdentity"), {
      title: cleanTitle,
      type: lineage ? "lineage" : "",
      resource_id: lineageId,
      copy_label: "lineage_id",
    }, {
      headingTag: "h1",
      titleId: "activeLineageTitle",
      typeId: "activeLineageType",
      idId: "activeLineageSubtitle",
    });
  }

  function renderControls() {
    const lineage = state.lineage;
    const display = state.lineage;
    const node = currentNode();
    const editable = !!lineage;
    ["lineageSettingsBtn", "addNodeBtn", "autoLayoutBtn", "addTemplateBtn", "reconcileLineageRuntimeBtn", "emptyAddNodeBtn"]
      .forEach(id => { if ($id(id)) $id(id).disabled = !editable; });
    const leaderJump = $id("graphLeaderJumpBtn");
    if (leaderJump) {
      const leaderId = String(lineage?.leader_resident_id || "").trim();
      const leader = leaderId ? displayNodeById(leaderId) : null;
      const label = leader
        ? window.PortalI18n.text`跳转到 Leader：${leader.title || leaderId}`
        : window.PortalI18n.t("当前 Lineage 没有可用的 Leader");
      leaderJump.disabled = !leader;
      leaderJump.title = label;
      leaderJump.setAttribute("aria-label", label);
    }
    renderAutoLayoutControl();
    const familyFacadeSelected = !!familyForFacade(node?.id);
    if ($id("branchNodeBtn")) $id("branchNodeBtn").disabled = !editable || !node || familyFacadeSelected;
    const wrapButton = $id("wrapFunctionBlockBtn");
    const wrapCount = $id("wrapFunctionBlockCount");
    const selectedCanvasCount = state.selectedCanvasObjectIds.size;
    ["saveSelectionTemplateBtn", "saveSelectionRewriteBtn"].forEach(id => {
      const button = $id(id);
      if (button) button.disabled = !state.lineage
        || state.graphViewMode !== "context" || !selectedCanvasCount || state.programMutationInFlight;
    });
    if (wrapButton) {
      wrapButton.disabled = (
        !editable
        || state.graphViewMode !== "context"
        || !selectedCanvasCount
      );
      wrapButton.classList.toggle(
        "is-active",
        selectedCanvasCount > 0,
      );
    }
    const familizeButton = $id("familizeBtn");
    if (familizeButton) {
      familizeButton.disabled = (
        !editable
        || state.graphViewMode !== "context"
        || selectedFamilyCandidateIds().length < 1
      );
    }
    if (wrapCount) {
      wrapCount.textContent = String(selectedCanvasCount);
      wrapCount.dataset.count = String(selectedCanvasCount);
    }
    const moveSubgraphButton = $id("moveSubgraphBtn");
    if (moveSubgraphButton) moveSubgraphButton.disabled = !editable || !selectedCanvasCount
      || state.programMutationInFlight || state.canvasSelectionDeleteInFlight || subgraphMoveBusy();
    const batchDeleteButton = $id("batchDeleteSelectionBtn");
    const cancelSelectionButton = $id("cancelCanvasSelectionBtn");
    const selectionHasDeletableObject = selectedCanvasObjects().some(
      canvasObjectCanDelete,
    );
    if (batchDeleteButton) {
      batchDeleteButton.disabled = (
        !editable
        || state.canvasSelectionDeleteInFlight
        || state.programMutationInFlight
        || !selectionHasDeletableObject
      );
      batchDeleteButton.setAttribute(
        "aria-busy",
        state.canvasSelectionDeleteInFlight ? "true" : "false",
      );
      const tooltip = selectionHasDeletableObject
        ? window.PortalI18n.text`批量删除当前选中的 ${selectedCanvasCount} 个 HATG 元素。`
        : window.PortalI18n.t("当前选区只包含不可删除的同步主轨迹节点。");
      batchDeleteButton.dataset.portalTooltip = tooltip;
      batchDeleteButton.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    if (cancelSelectionButton) {
      cancelSelectionButton.disabled = selectedCanvasCount === 0;
    }
    const stopLineage = $id("stopLineageBtn");
    if (stopLineage) {
      const tooltip = window.PortalI18n.t("全局 Stop：对当前 Lineage 每个正在运行的节点执行一次与 Session 对话栏相同的 Stop。");
      const runningCount = runningLineageNodes().length;
      stopLineage.disabled = !editable
        || state.globalStopInFlight
        || runningCount === 0;
      stopLineage.classList.toggle("busy", state.globalStopInFlight);
      stopLineage.setAttribute(
        "aria-busy",
        state.globalStopInFlight ? "true" : "false",
      );
      stopLineage.querySelector("span").textContent = state.globalStopInFlight
        ? window.PortalI18n.t("Stopping…")
        : window.PortalI18n.t("全局 Stop");
      stopLineage.dataset.portalTooltip = tooltip;
      stopLineage.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    const handoff = $id("handoffModeBtn");
    if (handoff) handoff.disabled = !editable || !display || display.nodes.length < 2;
    handoff?.classList.toggle("is-active", state.handoffMode);
    handoff?.setAttribute("aria-pressed", state.handoffMode ? "true" : "false");
    if (handoff) {
      const tooltip = state.handoffMode
        ? window.PortalI18n.t("工作交接：正在选择交接节点；可通过提示栏退出交接模式。")
        : window.PortalI18n.t("工作交接：选择来源和目标，明确传递工件与任务契约。");
      handoff.dataset.portalTooltip = tooltip;
      handoff.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    ["context", "workspace"].forEach(mode => {
      const tab = $id(`graph${mode[0].toUpperCase()}${mode.slice(1)}ViewBtn`);
      const active = state.graphViewMode === mode;
      tab?.classList.toggle("active", active);
      tab?.setAttribute("aria-selected", active ? "true" : "false");
      if (tab) tab.disabled = !display;
    });
    const visibleByView = {
      addNodeBtn: "context",
      branchNodeBtn: "context",
      wrapFunctionBlockBtn: "context",
      saveSelectionTemplateBtn: "context",
      saveSelectionRewriteBtn: "context",
      familizeBtn: "context",
      batchDeleteSelectionBtn: "context",
      moveSubgraphBtn: "context",
      cancelCanvasSelectionBtn: "context",
      handoffModeBtn: "context",
      stopLineageBtn: "context",
    };
    Object.entries(visibleByView).forEach(([id, mode]) => {
      const button = $id(id);
      if (!button) return;
      const selectionAction = button.hasAttribute("data-canvas-selection-action");
      const selectionOnly = [
        "saveSelectionTemplateBtn",
        "saveSelectionRewriteBtn",
        "batchDeleteSelectionBtn",
        "moveSubgraphBtn",
        "cancelCanvasSelectionBtn",
      ].includes(id);
      button.hidden = (
        state.graphViewMode !== mode
        || (id === "branchNodeBtn" && familyFacadeSelected)
        || (selectedCanvasCount > 0 ? !selectionAction : selectionOnly)
      );
    });
    const graphActions = $id("graphActions");
    if (graphActions) {
      const viewLabel = ({ context: "Context", workspace: "Workspace" })[state.graphViewMode] || "HATG";
      graphActions.dataset.viewMode = state.graphViewMode;
      graphActions.dataset.selectionActive = selectedCanvasCount > 0 ? "true" : "false";
      graphActions.setAttribute(
        "aria-label",
        selectedCanvasCount > 0
          ? window.PortalI18n.text`${viewLabel} 多选操作，已选择 ${selectedCanvasCount} 个元素`
          : window.PortalI18n.text`${viewLabel} 操作`,
      );
    }
  }

  function graphNodeContent(node, status) {
    if (node.accessible === false) return window.PortalI18n.markup('<strong>无权访问的 Lineage</strong><span class="hatgRestrictedNode">需要访问凭证</span>');
    if (node.kind === "seed") {
      const typeLabel = ({ idea: "Idea", plan: "Plan", next_step: "Next", other: "Seed" })[node.seed_type] || "Seed";
      const iconName = ({ idea: "idea", plan: "plan", next_step: "arrow-right", other: "status-dot" })[node.seed_type] || "status-dot";
      return window.PortalI18n.html`<span class="seedNodeGlyph" data-node-drag-handle data-portal-icon="${iconName}" title="拖动种子节点"></span>
        <strong title="${esc(node.display_title || node.title)}">${esc(node.display_title || node.title || window.PortalI18n.t("候选"))}</strong>
        <span class="seedNodeKind">${esc(typeLabel)}</span>`;
    }
    if (isFunctionBlockInstance(node)) {
      const failures = functionBlockInstanceUnacknowledgedFailures(node);
      // A placed block is its pigeon alone: the Family's, its template's, or one from its ID.
      const pigeon = window.LineageTemplates.pigeonPosition(node.family
        ? { builtin_key: "family" }
        : { builtin_key: templateController?.find?.(node.template_id)?.builtin_key, id: node.template_id || node.id });
      const title = node.title || window.PortalI18n.t("Function Block");
      return window.PortalI18n.html`<span class="functionBlockInstanceNode" data-node-drag-handle title="${esc(node.family ? title : window.PortalI18n.text`${title} · ${functionBlockSourceTitle(node)} · ${(node.elements || []).length} 个元素`)}">
        <span class="functionBlockPigeon" aria-hidden="true" style="background-position:${pigeon}"></span>
        ${failures.length ? window.PortalI18n.html`<button type="button" class="functionBlockAlert" data-function-block-alert data-function-block-issues="${esc(node.id)}" aria-label="${esc(window.PortalI18n.text`${failures.length} 个未查看问题，查看具体来源`)}" title="${esc(failures.map(issue => `${issue.title || issue.id}：${issue.error}`).join("\n"))}">${failures.length}</button>` : ""}
      </span>`;
    }
    if (isProgramComponent(node)) {
      const event = node.kind === "event_source";
      const effect = node.kind === "effect";
      const config = node.config || {};
      const explanation = programExecutionExplanation(node.id);
      const eventStatus = event ? programEventSourceStatus(node.id) : null;
      let detail = event
        ? window.PortalLineageProgram?.manifest?.event_types?.[config.event_type]?.title
          || "选择系统事件"
        : effect
          ? effectScriptLabel(config.script) || "编写要运行的脚本"
          : node.kind === "loop"
            ? `最多 ${config.max_rounds || 5} 轮`
          : `${config.operator_implementation === "resident" ? "Resident 语义执行" : "确定性规则执行"}${node.kind === "selector" ? ` · ${(config.branches || []).length} 个分支` : ""}`;
      if (event) {
        const runtime = programEventSourceRuntime(node.id);
        const lastDelivery = runtime.last_delivery || {};
        const maximum = normalizeOptionalLimit(
          runtime.max_fires ?? config.max_fires,
          100000,
        );
        const occurrenceCount = Math.max(
          0,
          Number(runtime.occurrence_count || 0),
        );
        if (maximum !== null) {
          detail += ` · ${occurrenceCount} / ${maximum} 次`;
          if (runtime.exhausted) detail += " · 已耗尽";
        } else {
          detail += ` · 已触发 ${occurrenceCount} 次`;
        }
        if (Number(lastDelivery.dropped_count || 0) > 0) {
          detail += ` · 最近丢弃 ${Number(lastDelivery.dropped_count)} 条`;
        } else if (Number(lastDelivery.failed_count || 0) > 0) {
          detail += ` · 最近失败 ${Number(lastDelivery.failed_count)} 条`;
        } else if (Number(lastDelivery.queued_count || 0) > 0) {
          detail += ` · 最近暂存 ${Number(lastDelivery.queued_count)} 条`;
        }
      }
      return window.PortalI18n.html`<span class="programNodeTopline" data-node-drag-handle title="拖动原语">
          <span class="programNodeGlyph" aria-hidden="true" data-portal-icon="${event ? "event" : effect ? "effect" : node.kind}"></span>
          <span class="programNodeKind">${esc(event ? "EVENT" : effect ? "EFFECT" : node.kind.toUpperCase())}</span>
        </span>
        <strong title="${esc(node.title)}">${esc(node.title || (event ? "Event" : effect ? "Effect" : node.kind))}</strong>
        <span class="programNodeDetail">${esc(detail)}</span>
        ${(event || effect) && programComponentPaused(node)
          ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="paused">已关闭</span>`
          : eventStatus && eventStatus.state !== "paused" ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="${esc(eventStatus.state)}" title="${esc(eventStatus.reason)}">${esc(window.PortalI18n.t(eventStatus.label))}${eventStatus.reason ? ` · ${esc(eventStatus.reason)}` : ""}</span>`
          : explanation ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="${esc(explanation.state || "idle")}" title="${esc(explanation.next_action || explanation.why || "")}">${esc(explanation.summary || "")}</span>` : ""}`;
    }
    const dragTitle = window.PortalI18n.t("拖动节点");
    const activity = "Resident";
    const residentId = nodeResidentId(node);
    const intakeState = nodeIntakeState(node);
    const work = nodeWorkPresentation(node, status);
    const workStatus = `<span class="nodeStatus" data-intake-state="${esc(intakeState)}" data-work-phase="${esc(work.phase)}" title="${esc(work.title)}">${esc(work.label)}</span>`;
    const gate = nodeExecutionGate(node);
    const intakeStatus = gate.text
      ? `<span class="nodeIntakeStatus" data-execution-gate="${esc(gate.state)}" title="${esc(gate.text)}">${esc(gate.label)}</span>`
      : "";
    const memberTag = familyMemberTag(node);
    const family = familyForFacade(node.id);
    if (family) {
      const expanded = familyExpanded(family.roles);
      const count = family.roles.members.length;
      return window.PortalI18n.html`<span class="nodeTopline familyFacadeTopline" data-node-drag-handle title="${esc(dragTitle)}"><span class="nodeIdentity" data-resident-id="${esc(residentId)}" title="${esc(residentId)}">${node._is_leader ? '<em class="nodeLeaderBadge">LEADER</em>' : ""}${esc(compactText(residentId, 18))}</span>${workStatus}${intakeStatus}</span>
      <span class="familyFacadeHeading"><small>FAMILY</small><strong class="nodeTitle" title="${esc(node.title)}">${esc(node.title)}</strong></span>
      <span class="nodeFooter"><span class="nodeTypePill familyFacadePill">门面</span><span class="familyFacadeMembers" title="${esc(expanded ? window.PortalI18n.t("成员已展开，点击 − 折叠为门面") : window.PortalI18n.t("点击 + 展开成员"))}">${count} 位成员</span><span class="nodeMessageCount" title="该 Family 已接受的外部消息数">${esc(nodeMessageCountText(node))}</span></span>${memberTag}`;
    }

    const heading = window.PortalI18n.html`<strong class="nodeTitle" title="${esc(node.title)}">${esc(node.title)}</strong>`;
    const typeLabel = nodeIsDerived(node) ? "derived" : "resident";
    return window.PortalI18n.html`<span class="nodeTopline" data-node-drag-handle title="${esc(dragTitle)}"><span class="nodeIdentity" data-resident-id="${esc(residentId)}" title="${esc(residentId)}">${node._is_leader ? '<em class="nodeLeaderBadge">LEADER</em>' : ""}${esc(compactText(residentId, 18))}</span>${workStatus}${intakeStatus}</span>
      ${heading}
      <span class="nodeFooter"><span class="nodeTypePill">${typeLabel}</span><span class="nodeMessageCount" title="该 Resident 已接受的用户消息数">${esc(nodeMessageCountText(node))}</span>${nodeContextHeatBadge(node)}<span class="nodeFooterState">${esc(activity)}</span></span>${memberTag}`;
  }



  async function openGraphWorkspace(node) {
    if (node?.kind !== "workspace" || !node.workspace_id) return;
    state.selectedWorkspaceId = node.id;
    state.selectedEdgeId = "";
    renderGraph();
    try {
      if (typeof hatgComponentOptions.onWorkspaceOpen !== "function") {
        throw new Error("Navigation 工作区入口尚未就绪");
      }
      await hatgComponentOptions.onWorkspaceOpen(node.workspace_id);
    } catch (error) {
      toast(window.PortalI18n.text`工作区打开失败：${error.message || error}`, "error");
    }
  }

  let workspaceMenuOpenSequence = 0;
  async function openGraphWorkspaceMenu(node) {
    if (node?.kind !== "workspace" || !node.workspace_id) return;
    const lineageId = state.lineage?.lineage_id;
    let bindingSequence = state.workspaceGraphLoadSeq;
    const openSequence = ++workspaceMenuOpenSequence;
    const isCurrent = () => state.lineage?.lineage_id === lineageId
      && state.workspaceGraphLoadSeq === bindingSequence;
    state.selectedWorkspaceId = node.id;
    state.selectedEdgeId = "";
    renderGraph();
    try {
      await Promise.all([
        window.PortalUIPrimitives.loadAsset("/static/portal_resident_menu.css"),
        window.PortalUIPrimitives.loadAsset("/static/portal_workspace_menu.css"),
        window.PortalUIPrimitives.loadAsset("/static/portal_detail_surface.js", {ready: () => window.PortalDetailSurface}),
        window.PortalUIPrimitives.loadAsset("/static/portal_workspace_menu.js", {ready: () => window.PortalWorkspaceMenu}),
      ]);
      if (!isCurrent() || openSequence !== workspaceMenuOpenSequence) return;
      await window.PortalWorkspaceMenu.open({
        workspaceId: node.workspace_id,
        members: (node.member_ids || []).map(id => ({id, title: displayNodeById(id)?.title || id})),
        onOpen: hatgComponentOptions.onWorkspaceOpen,
        onChanged: async () => {
          if (!isCurrent()) return;
          for (const [residentId, binding] of state.workspaceGraphBindings) {
            if (binding.roots?.some(root => root.workspace_id === node.workspace_id)) {
              state.workspaceGraphBindings.delete(residentId);
            }
          }
          bindingSequence = ++state.workspaceGraphLoadSeq;
          const result = await loadWorkspaceGraphBindings(state.lineage, {fit: false});
          if (!result.complete && !result.stale) throw new Error(window.PortalI18n.t("Workspace 绑定读取失败，请重新打开视图。"));
        },
      });
    } catch (error) {
      if (isCurrent()) toast(window.PortalI18n.text`Workspace 菜单打开失败：${error.message || error}`, "error");
    }
  }

  function renderGraphNavigator(model = graphVisibleModel()) {
    renderAutoLayoutControl();
    const hasLineage = !!state.lineage;
    const focus = $id("graphFocusModeBtn");
    const filter = $id("graphRelationFilter");
    const search = $id("graphNodeSearchInput");
    const selectedFacade = state.graphViewMode === "context" && Boolean(familyForFacade(graphSelectedId()));
    focus.disabled = !hasLineage || (!state.graphFocusMode && !model.selectedId);
    focus.classList.toggle("active", state.graphFocusMode);
    focus.setAttribute("aria-pressed", state.graphFocusMode ? "true" : "false");
    focus.textContent = state.graphFocusMode ? window.PortalI18n.t("全图") : window.PortalI18n.t("邻域");
    focus.title = state.graphFocusMode
      ? window.PortalI18n.t("退出邻域聚焦，恢复全部节点")
      : selectedFacade
        ? window.PortalI18n.t("只显示这个 Family：门面、steward、成员与 Router，各自保持原位置")
        : window.PortalI18n.t("只显示选中节点与一跳邻居");
    const filterOptions = state.graphViewMode === "workspace"
      ? [["all", "全部 Workspace 关系"], ["workspace_binding", "Resident 绑定"], ["handoff", "Workspace Handoff"]]
      : [["all", "全部 Context 关系"], ["program", "HATG Program"], ["inheritance", "Fork 继承"], ["handoff", "Workspace Handoff"], ["absorption", "节点吸收"]];
    const optionSignature = filterOptions.map(item => item[0]).join(",");
    if (filter.dataset.options !== optionSignature) {
      filter.innerHTML = filterOptions.map(([value, label]) => window.PortalI18n.html`<option value="${esc(value)}">${esc(window.PortalI18n.t(label))}</option>`).join("");
      filter.dataset.options = optionSignature;
    }
    filter.hidden = false;
    filter.disabled = !hasLineage;
    filter.value = state.graphRelationFilter;
    search.disabled = !hasLineage;
    $id("graphDeepRerankBtn").hidden = state.graphViewMode === "workspace";
    $id("collaborationGraphLegend").hidden = (
      state.graphViewMode !== "context"
      || state.graphRelationFilter === "program"
    );
    const programLegend = $id("programGraphLegend");
    if (programLegend) {
      programLegend.hidden = (
        state.graphViewMode !== "context"
        || state.graphRelationFilter !== "program"
      );
    }
    $id("workspaceGraphLegend").hidden = state.graphViewMode !== "workspace";
    $id("graphVisibilitySummary").textContent = `${model.nodes.length} / ${model.overviewNodes.length}`;
  }

  async function selectProgramComponent(component) {
    if (!component?.id) return;
    if (isPacketOperatorComponent(component)) {
      const config = component.config || {};
      const executorId = String(config.resident_executor_id || "");
      if (
        config.operator_implementation === "resident"
        && executorId
        && displayNodeById(executorId)
      ) {
        await selectNode(executorId, { force: true });
      } else {
        clearNodeSelection();
      }
    }
    await selectProgramObject(component.id);
  }

  function renderGraph() {
    const empty = $id("graphEmpty");
    const display = state.lineage;
    const model = graphVisibleModel();
    reconcileCanvasSelection(model);
    const nodes = model.nodes;
    const edges = model.edges;
    state.renderedFamilySpotlight = model.familySpotlightFacadeId || "";
    state.renderedFamilyPigeon = selectedFamilyPigeonId();
    const sourceAgentId = state.handoffSourceId;
    const projectedSourceId = sourceAgentId;
    empty.hidden = !!nodes.length || !display;
    if (!display) empty.hidden = true;
    const canvasSelectionActive = (
      state.graphViewMode === "context"
      && state.selectedCanvasObjectIds.size > 0
    );
    state.graphController?.render({
      nodes,
      edges,
      functionBlockBoundaries: model.functionBlockBoundaries,
      selectedNodeId: canvasSelectionActive ? "" : model.selectedId,
      selectedNodeIds: canvasSelectionActive
        ? [...state.selectedCanvasObjectIds]
        : [],
      selectableNodeIds: state.graphViewMode === "context"
        ? nodes.filter(canvasNodeCanBeSelected).map(node => node.id)
        : [],
      selectionEnabled: state.graphViewMode === "context",
      selectedEdgeId: graphSelectedEdgeId(),
      handoffSourceId: projectedSourceId,
      handoffAgentId: sourceAgentId,
      handoffMode: state.handoffMode,
      viewMode: state.graphViewMode,
      layoutInteractive: ["context", "workspace"].includes(state.graphViewMode),
      // Workspace coordinates belong to personal preferences, never Context layout.
      navigationFocus: state.graphFocusMode,
      quickForkNodeId: state.quickForkNodeId,
      overviewNodes: model.overviewNodes,
      overviewEdges: model.overviewEdges,
      wireDraft: state.programWireDraft,
    });
    renderGraphNavigator(model);
    renderRelationInspector();
    renderProgramWireHint();
    refreshGraphNodeSearchResults();
  }

  function renderGraphRuntimePresentation() {
    const model = graphVisibleModel();
    if (!state.graphController?.patchRuntimePresentation?.(model)) renderGraph();
  }

  function renderGraphSelection() {
    if (state.graphFocusMode) return false;
    // Selecting or leaving an expanded Family's facade fades the rest of the canvas.
    if ((familySpotlight()?.roles.facade || "") !== state.renderedFamilySpotlight) return false;
    // So does selecting or leaving a Family pigeon, which draws its lines.
    if (selectedFamilyPigeonId() !== state.renderedFamilyPigeon) return false;
    const model = graphVisibleModel();
    const canvasSelectionActive = (
      state.graphViewMode === "context"
      && state.selectedCanvasObjectIds.size > 0
    );
    const patched = state.graphController?.patchSelection?.({
      selectedNodeId: canvasSelectionActive ? "" : model.selectedId,
      selectedNodeIds: canvasSelectionActive
        ? [...state.selectedCanvasObjectIds]
        : [],
      selectedEdgeId: graphSelectedEdgeId(),
    });
    if (!patched) return false;
    renderGraphNavigator(model);
    renderRelationInspector();
    renderProgramWireHint();
    return true;
  }

  function initHatgGraph() {
    const factory = window.PortalHatgGraph?.createGraph;
    if (!factory) throw new Error("HATG 图形引擎未加载");
    state.graphController?.destroy?.();
    state.graphController = factory({
      root: $id("hatgCanvas"),
      world: $id("hatgWorld"),
      edgeLayer: $id("hatgEdgeLayer"),
      effectLayer: $id("hatgEffectLayer"),
      labelLayer: $id("hatgEdgeLabels"),
      nodeLayer: $id("hatgNodes"),
      minimap: $id("hatgMinimap"),
      worldWidth: HATG_WORLD_SIZE.width,
      worldHeight: HATG_WORLD_SIZE.height,
      zoomValue: $id("graphZoomValue"),
      semanticZoomRoot: $id("graphSemanticZoom"),
      nodeDoubleClickDelay: 260,
      zoomInButton: $id("graphZoomInBtn"),
      zoomOutButton: $id("graphZoomOutBtn"),
      resetButton: $id("graphZoomResetBtn"),
      fitButton: $id("graphFitBtn"),
      fitOptions: FIT_ALL_VIEW,
      scaleExtent: [0.05, 3.2],
      readViewport: lineageId => hatgViewPreferences.get(String(lineageId || ""))?.viewport || null,
      onViewportChange: (lineageId, viewport) => {
        scheduleHatgViewPreferenceSave(lineageId, { viewport });
      },
      viewportInsets: () => {
        // The docked Inspector already shrinks hatgCanvas in CSS. Measure
        // actual occlusion of the graph root, not the enclosing viewport.
        const viewport = $id("hatgCanvas")?.getBoundingClientRect();
        const occludingBounds = element => {
          if (!viewport || !element || element.hidden || !element.getClientRects().length) return null;
          const bounds = element.getBoundingClientRect();
          return bounds.left < viewport.right && bounds.right > viewport.left
            && bounds.top < viewport.bottom && bounds.bottom > viewport.top ? bounds : null;
        };
        const inspectorBounds = occludingBounds($id("relationInspectorPanel"));
        const programInspectorBounds = occludingBounds($id("programInspectorPanel"));
        const navigationBounds = occludingBounds($id("graphNavigationBar"));
        const hintBounds = occludingBounds($id("programWireHint"));
        const inspectors = [inspectorBounds, programInspectorBounds].filter(Boolean);
        const spansWidth = bounds => bounds.left <= viewport.left + 1 && bounds.right >= viewport.right - 1;
        return {
          top: Math.max(0, ...[navigationBounds, hintBounds].filter(Boolean).map(bounds => bounds.bottom - viewport.top + 10)),
          right: Math.max(0, ...inspectors.filter(bounds => !spansWidth(bounds)).map(bounds => viewport.right - bounds.left + 10)),
          bottom: Math.max(0, ...inspectors.filter(spansWidth).map(bounds => viewport.bottom - bounds.top + 10)),
        };
      },
      statusForNode: effectiveNodeStatus,
      contextPressureForNode: nodeContextPressure,
      nodeContent: graphNodeContent,
      nodeTooltip: node => {
        if (!isFunctionBlockInstance(node)) return "";
        const title = node.title || window.PortalI18n.t("Function Block");
        return node.family ? title
          : window.PortalI18n.text`${title} · ${functionBlockSourceTitle(node)} · ${(node.elements || []).length} 个元素`;
      },
      nodeDisclosure: node => {
        if (isFunctionBlockInstance(node) && !node.family) return {
          kind: "function_block", expanded: false, label: window.PortalI18n.t("展开"), title: window.PortalI18n.t("展开"),
        };
        const family = familyForFacade(node.id);
        if (family) {
          const expanded = familyExpanded(family.roles);
          const count = family.roles.members.length;
          return {
            kind: "family",
            expanded,
            action: {label: "Unfamilize", iconName: "close", title: "Unfamilize"},
            label: expanded
              ? window.PortalI18n.text`折叠 Family「${node.title || node.id}」，只显示门面`
              : window.PortalI18n.text`只显示 Family「${node.title || node.id}」：门面、${count} 位成员与 Family 模块`,
            title: expanded ? window.PortalI18n.t("折叠为门面") : window.PortalI18n.text`只显示这个 Family（${count} 位成员）`,
          };
        }
        return null;
      },
      nodeControl: node => node.accessible === false ? null : hatgNodeControl(node),
      nodeHoverActions: node => node.accessible === false ? [] : hatgNodeHoverActions(node),
      nodeDisabled: node => node.accessible === false,
      nodeOpenMenu: node => isFunctionBlockInstance(node)
        ? {
          label: window.PortalI18n.text`打开 ${node.title || node.id} 菜单`,
          title: window.PortalI18n.t("配置、展开或解除 Function Block"),
        }
        : residentEditorOpenMenu(node),
      portsForNode: node => (
        node.accessible !== false && state.graphViewMode === "context"
          ? window.PortalLineageProgram?.portsForNode?.(node) || []
          : []
      ),
      onPortClick: port => handleProgramPortClick(port).catch(() => {}),
      connectionTargets: (source, item) => window.PortalLineageProgram.connectionTargets(source, item),
      onWireDraftCancel: () => {
        cancelProgramWireDraft();
      },
      isPortCompatible: (source, target) => Boolean(
        window.PortalLineageProgram?.validateConnection?.(
          source,
          target,
          { existingWires: currentProgram().wires, nodes: [...(state.lineage?.nodes || []), ...currentProgram().components] },
        )?.ok
      ),
      onPrimitiveDrop: (kind, position) => {
        createProgramComponentFromDrop(kind, position).catch(() => {});
      },
      onNodeControl: (node, action, event) => {
        if (action === "emit-manual-event") {
          emitManualProgramEvent(node.id, event?.currentTarget || null).catch(() => {});
          return;
        }
        if (action === "trigger-function-block") {
          openFunctionBlockMenu(node.id);
          return;
        }
        if (action === "reset-event-source") {
          resetProgramEventSource(node.id, event?.currentTarget || null).catch(() => {});
          return;
        }
        if (action === "stop") stopLineageNode(node).catch(() => {});
      },
      onNodeDisclosure: node => {
        if (isFunctionBlockInstance(node) && !node.family) { setFunctionBlockExpanded(node.id, true); return; }
        if (toggleFamilyFacade(node)) return;
      },
      onNodeDisclosureAction: node => {
        const family = familyForFacade(node.id);
        if (family) removeFamilyInstance(family.instance.id).catch(() => {});
      },
      onFunctionBlockCollapse: id => setFunctionBlockExpanded(id, false),
      onFunctionBlockMenu: id => openFunctionBlockMenu(id),
      onNodeHoverAction: (node, action) => {
        if (isFunctionBlockInstance(node) && ["open-router", "open-steward"].includes(action)) {
          const roles = familyRoles(node);
          const resident = displayNodeById(roles?.[action.slice(5)]);
          if (resident) notifyResidentSelection(resident);
          return;
        }
        if (action !== "stop") return;
        stopLineageNode(node).catch(() => {});
      },
      onNodeOpenMenu: (node, _menu, event, trigger) => {
        if (isFunctionBlockInstance(node)) openFunctionBlockMenu(node.id);
        else notifyResidentOpenMenu(node, event, trigger);
      },
      isEdgeFlowing: edge => ["target", "watch"].includes(edge.type)
        ? false
        : ["queued", "running"].includes(effectiveNodeStatus(displayNodeById(edge.source_resident_id || edge.source)))
          || ["queued", "running"].includes(effectiveNodeStatus(displayNodeById(edge.target_resident_id || edge.target))),
      onCandidateForkDrop: (source, seedId) => {
        setSeedCandidateFork(seedId, source.id).catch(() => {});
      },
      onWorkspaceClick: node => openGraphWorkspace(node),
      onWorkspaceDoubleClick: node => openGraphWorkspaceMenu(node),
      onEdgeClick: (edge, event) => {
        if (edge.visual_only || edge.type === "workspace_binding") return;
        if (edge._program_wire && (event?.shiftKey || event?.metaKey || event?.ctrlKey)) {
          toggleCanvasObjectSelection(edge.id);
          renderGraph();
          return;
        }
        clearCanvasSelection({ render: false });
        if (edge._program_wire) {
          selectProgramObject(edge.id);
          return;
        }
        selectRelationEdge(edge.interaction_edge_id || edge.id);
      },
      onNodeClick: (node, event) => {
        if (state.graphViewMode === "workspace") return;
        const modifierSelection = Boolean(
          state.graphViewMode === "context"

          && (event?.shiftKey || event?.metaKey || event?.ctrlKey),
        );
        if (modifierSelection && canvasNodeCanBeSelected(node)) {
          toggleCanvasObjectSelection(node.id);
          return;
        }
        const selectionWasActive = state.selectedCanvasObjectIds.size > 0;
        clearCanvasSelection({ render: false });
        state.canvasSelectionAnchorId = (
          state.graphViewMode === "context" && canvasNodeCanBeSelected(node)
        ) ? node.id : "";
        // A Seed opens its card (candidate, adopt, dismiss); its settings open
        // from the card or on double-click.
        if (node.kind === "seed") selectSeed(node.id);
        else if (isProgramComponent(node)) {
          selectProgramComponent(node);
        }
        else if (isFunctionBlockInstance(node)) {
          selectProgramObject(node.id);
          openFunctionBlockMenu(node.id);
        }
        else if (state.handoffMode) handleHandoffNodeClick(node.id);
        else selectNode(node.id, { force: selectionWasActive, openChat: true });
      },
      onBoxSelect: nodes => {
        addCanvasObjectsToSelection(nodes.map(node => node.id));
      },
      onSelectionCancel: () => {
        clearCanvasSelection();
      },
      onBackgroundClick: () => {
        if (state.graphViewMode === "workspace") {
          state.selectedWorkspaceId = "";
          if (state.selectedEdgeId) closeRelationInspector({ restoreFocus: false });
          renderGraph();
          return;
        }
        if (state.selectedCanvasObjectIds.size) {
          clearCanvasSelection();
        } else if (state.selectedProgramObjectId) {
          state.selectedProgramObjectId = "";
          if (!renderGraphSelection()) renderGraph();
          renderControls();
          renderProgramInspector();
        } else if (state.selectedEdgeId) closeRelationInspector({ restoreFocus: false });
        else dismissSeedSelection();
      },
      onNodeDoubleClick: node => {
        if (state.graphViewMode === "workspace") return;
        if (node.accessible === false) return;
        if (isFunctionBlockInstance(node)) {
          openFunctionBlockMenu(node.id);
          return;
        }
        if (isProgramComponent(node)) {
          selectProgramComponent(node);
          return;
        }
        if (node.kind === "seed") {
          openSeedPromoteModal(node.id, "implement");
          return;
        }
        openNodeSettingsModal(node);
      },
      nodeQuickForkModes: () => Array.from(
        $id("branchModeChoices").querySelectorAll('input[name="branchMode"]'),
        input => ({
          value: input.value,
          label: input.closest("label").querySelector("strong").textContent,
          description: input.closest("label").querySelector("small").textContent,
        }),
      ),
      onNodeQuickFork: (node, _event, mode) => {
        createQuickFork(node, mode).catch(() => {});
      },
      onNodeMove: (node, position) => {
        if (state.graphViewMode === "workspace") {
          syncWorkspaceNodePosition(node, position);
          return;
        }
        syncGraphNodePosition(node, position);
        if (node?.id === state.selectedSeedId) positionSeedInspector();
      },
      onNodeMoveEnd: node => {
        if (state.graphViewMode === "workspace") {
          if (syncWorkspaceNodePosition(node)) scheduleHatgViewPreferenceSave(state.lineage.lineage_id);
          return;
        }
        if (!syncGraphNodePosition(node)) return;
        saveLineageLayout([node]).catch(() => {});
      },
      onNodesMoveEnd: nodes => {
        if (state.graphViewMode === "workspace") return;
        const movedItems = nodes
          .map(node => graphItemById(node.id))
          .filter(Boolean);
        if (!movedItems.length) return;
        saveLineageLayout(movedItems).catch(() => {});
      },
      canNodeAbsorb: (source, target) => (
        state.graphViewMode === "context"
        && !state.absorptionInFlight
        && source?.kind !== "seed"
        && target?.kind !== "seed"
        && !isProgramComponent(source)
        && !isProgramComponent(target)
        && !isFunctionBlockInstance(source)
        && !isFunctionBlockInstance(target)
        && source?.id !== target?.id
      ),
      onNodeAbsorbDrop: (source, target) => {
        absorbNodeIntoTarget(source.id, target.id);
      },
    });
    $id("hatgWorld").dataset.hatgEngine = state.graphController.engine;
  }

  async function revealSelectedGraphNode(nodeId, options = {}) {
    const navigationSequence = state.graphController?.getNavigationSequence?.();
    await new Promise(resolve => window.requestAnimationFrame(resolve));
    if (graphSelectedId() !== nodeId) return false;
    if (navigationSequence !== state.graphController?.getNavigationSequence?.()) return false;
    state.graphController?.focusNode?.(nodeId, {
      scale: Number(options.scale || 1.25),
      duration: Number(options.duration ?? 240),
      compositor: true,
    });
    return true;
  }

  async function focusGraphNode(nodeId) {
    if (state.graphViewMode === "workspace") {
      const node = workspaceGraphModel().overviewNodes.find(item => item.id === nodeId);
      if (!node) return;
      clearGraphNodeSearch();
      state.graphFocusMode = false;
      if (node.kind === "workspace") await openGraphWorkspace(node);
      else renderGraph();
      state.graphController?.focusNode?.(node.id, { scale: 1.25, duration: 240 });
      return;
    }
    const node = displayGraphItemById(nodeId);
    if (!node) return;
    clearGraphNodeSearch();
    if (node.kind === "seed" || displaySeedById(node.id)) {
      await selectSeed(node.id);
      renderGraph();
      await revealSelectedGraphNode(node.id);
    } else {
      await selectNode(node.id, { force: true, revealInGraph: true });
    }
  }

  async function focusLineageLeader() {
    const leaderId = String(state.lineage?.leader_resident_id || "").trim();
    if (!leaderId || !displayNodeById(leaderId)) {
      renderControls();
      toast(window.PortalI18n.t("当前 Lineage 没有可跳转的 Leader。"), "warning");
      return;
    }
    if (state.graphViewMode !== "context") await setGraphViewMode("context");
    await focusGraphNode(leaderId);
  }

  function toggleGraphFocusMode() {
    const selectedId = state.graphViewMode === "workspace" ? state.selectedWorkspaceId : graphSelectedId();
    if (!state.graphFocusMode && !selectedId) return toast(window.PortalI18n.t("先选择一个节点，再聚焦它的一跳邻域。"), "warning");
    // 邻域 on a facade shows its Family alone; a folded Family opens for it.
    const family = !state.graphFocusMode && state.graphViewMode === "context"
      ? familyForFacade(graphSelectedId())
      : null;
    if (family) return showFamilyAlone(family);
    state.graphFocusMode = !state.graphFocusMode;
    state.graphFocusFamilyFacadeId = "";
    renderGraph();
    window.requestAnimationFrame(() => {
      state.graphController?.fit?.({ duration: 220, maxScale: state.graphFocusMode ? 1.15 : 1.45 });
    });
  }

  function changeGraphRelationFilter() {
    state.graphRelationFilter = $id("graphRelationFilter").value || "all";
    renderGraph();
  }

  function setWorkspaceViewLoading(loading) {
    state.workspaceViewLoading = Boolean(loading);
    const status = $id("workspaceViewLoading");
    if (status) status.hidden = !state.workspaceViewLoading;
    const tab = $id("graphWorkspaceViewBtn");
    if (tab) {
      tab.classList.toggle("loading", state.workspaceViewLoading);
      tab.setAttribute("aria-busy", state.workspaceViewLoading ? "true" : "false");
    }
  }

  async function setGraphViewMode(mode) {
    const next = ["context", "workspace"].includes(mode) ? mode : "context";
    if (next !== "context" && state.programWireDraft) cancelProgramWireDraft();
    if (next === "workspace" && state.workspaceViewLoading) return;
    const requestSequence = ++state.graphViewRequestSeq;
    if (next !== "workspace") setWorkspaceViewLoading(false);
    if (state.graphViewMode === next) return;
    if (next === "workspace" && state.lineage) {
      const lineageId = state.lineage.lineage_id;
      const needsBindings = (state.lineage.nodes || [])
        .some(node => !state.workspaceGraphBindings.has(node.id));
      if (needsBindings) {
        setWorkspaceViewLoading(true);
        let bindingResult;
        try {
          bindingResult = await loadWorkspaceGraphBindings(state.lineage, { render: false, fit: false });
        } catch (error) {
          if (requestSequence !== state.graphViewRequestSeq) return;
          setWorkspaceViewLoading(false);
          toast(window.PortalI18n.text`Workspace 视图加载失败：${error.message || error}`, "error", 5600);
          return;
        }
        if (
          requestSequence !== state.graphViewRequestSeq
          || state.lineage?.lineage_id !== lineageId
        ) return;
        setWorkspaceViewLoading(false);
        if (!bindingResult?.complete) {
          const failedCount = Number(bindingResult?.failedCount || 0);
          toast(
            failedCount
              ? window.PortalI18n.text`Workspace 视图暂未切换：${failedCount} 个节点的 Binding 读取失败。`
              : window.PortalI18n.t("Workspace 视图暂未切换：节点 Binding 尚未完整到齐。"),
            "error",
            5600,
          );
          return;
        }
      }
    }
    if (requestSequence !== state.graphViewRequestSeq) return;
    const seedSource = next !== "context" && state.selectedSeedId
      ? displaySeedById(state.selectedSeedId)?.source_resident_id
      : "";
    clearCanvasSelection({ render: false });
    state.graphViewMode = next;
    state.graphFocusMode = false;
    clearGraphNodeSearch();
    state.graphRelationFilter = "all";
    if (next !== "context" && state.handoffMode) cancelHandoffMode();
    const selectedEdge = displayEdgeById(state.selectedEdgeId);
    const clearedCrossViewEdge = Boolean(selectedEdge && !relationVisibleInView(selectedEdge, next));
    if (clearedCrossViewEdge) state.selectedEdgeId = "";
    if (seedSource) {
      state.selectedSeedId = "";
      state.selectedNodeId = seedSource;
      selectNode(seedSource, { force: true }).catch(() => {});
    }
    renderLineageSurface();
    window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.35 }));
  }

  function bindComponentLibraryResize() {
    disposeLibraryResize?.();
    const handle = $id("componentLibrarySplitter");
    const rail = handle.parentElement;
    const primitives = $id("hatgPrimitiveLibrary");
    const savedRatio = pageStateController.read().hatgLibrarySplitRatio;
    let ratio = typeof savedRatio === "number" && Number.isFinite(savedRatio) && savedRatio > 0 && savedRatio < 1
      ? savedRatio : 0.5;
    const availableWidth = () => Math.max(0, rail.getBoundingClientRect().width - handle.getBoundingClientRect().width);
    const minimum = () => Math.min(116, availableWidth() / 2);
    const maximum = () => availableWidth() - minimum();
    const readSize = () => primitives.getBoundingClientRect().width;
    const valueText = value => {
      const percent = Math.round(100 * value / (availableWidth() || 1));
      return window.PortalI18n.text`原语 ${percent}%，功能 ${100 - percent}%`;
    };
    const syncAria = () => {
      handle.setAttribute("aria-valuemin", String(Math.round(minimum())));
      handle.setAttribute("aria-valuemax", String(Math.round(maximum())));
      handle.setAttribute("aria-valuenow", String(Math.round(readSize())));
      handle.setAttribute("aria-valuetext", valueText(readSize()));
    };
    const applySplitRatio = () => {
      rail.style.setProperty("--hatg-library-primitives", `${ratio}fr`);
      rail.style.setProperty("--hatg-library-functions", `${1 - ratio}fr`);
      syncAria();
    };
    const persist = () => pageStateController.write({ hatgLibrarySplitRatio: ratio });
    applySplitRatio();
    const unbind = window.PortalWorkbench.layout.bindSplitter({
      handle,
      axis: "x",
      bodyClass: "",
      disabled: () => availableWidth() <= 0,
      readSize,
      min: minimum,
      max: maximum,
      valueText,
      applySize: size => {
        ratio = size / availableWidth();
        applySplitRatio();
      },
      onDone: persist,
    });
    pageStateController.listen("panel", handle, "dblclick", event => {
      event.preventDefault();
      ratio = 0.5;
      applySplitRatio();
      persist();
    });
    // CSS keeps both groups reachable at narrow widths without overwriting the
    // user's preferred ratio; only the separator's ARIA values need refreshing.
    const observer = new ResizeObserver(syncAria);
    observer.observe(rail);
    disposeLibraryResize = () => {
      unbind();
      observer.disconnect();
      handle.classList.remove("active");
    };
  }

  function renderLineageSurface() {
    reconcileGraphInteractionState();
    renderLineageHeader();
    renderGraph();
    renderProgramInspector();
    templateController?.render();
    renderControls();
    renderSeedInspector();
  }

  function runtimeStatusFromResidentProjection(projection, node) {
    const residentState = window.PortalResidentProtocol.stateView(projection?.resident_state);
    if (residentState.status !== "idle") return residentState.status;
    return ["done", "blocked"].includes(node?.status) ? node.status : "idle";
  }

  function applyResidentStateProjections(states, options = {}) {
    const previousWorkPhases = new Map((state.lineage?.nodes || []).map(node => [node.id, nodeWorkPresentation(node).phase]));
    const previousGates = new Map((state.lineage?.nodes || []).map(node => [node.id, nodeExecutionGate(node).text]));
    const previousIntakeStates = new Map(
      (state.lineage?.nodes || []).map(node => [node.id, nodeIntakeState(node)])
    );
    const previousStatuses = new Map(
      (state.lineage?.nodes || []).map(node => [node.id, effectiveNodeStatus(node)])
    );
    if (options.replace === true) state.residentRuntimeById = new Map();
    for (const [residentId, residentState] of Object.entries(states || {})) {
      const cleanResidentId = String(residentId || "").trim();
      if (!cleanResidentId) continue;
      const normalizedState = window.PortalResidentProtocol.stateView(residentState);
      state.residentRuntimeById.set(cleanResidentId, {
        resident_id: cleanResidentId,
        intake_state: normalizedState.intake_state,
        resident_state: normalizedState,
      });
    }
    if (!state.lineage?.nodes?.length) return false;
    let displayChanged = false;
    for (const node of state.lineage.nodes) {
      const projection = state.residentRuntimeById.get(nodeResidentId(node));
      if (projection) {
        setNodeRuntimeStatus(node.id, runtimeStatusFromResidentProjection(projection, node));
      } else if (options.replace === true) {
        state.runtimeStatus.delete(node.id);
      }
      const previousStatus = previousStatuses.get(node.id) || "idle";
      const nextStatus = effectiveNodeStatus(node);
      if (previousStatus !== nextStatus) {
        displayChanged = true;
      }
      if (previousWorkPhases.get(node.id) !== nodeWorkPresentation(node).phase
        || previousIntakeStates.get(node.id) !== nodeIntakeState(node)
        || previousGates.get(node.id) !== nodeExecutionGate(node).text) {
        displayChanged = true;
      }
    }
    if (!displayChanged) return false;
    if (options.render !== false) {
      updateLineageSummary();
      renderGraph();
      renderControls();
    }
    return true;
  }

  async function reconcileLineageRuntime(options = {}) {
    const lineageId = state.lineage?.lineage_id || "";
    if (!lineageId) return null;
    if (state.runtimeReconcileInFlight) return state.runtimeReconcileInFlight;
    const button = options.button || null;
    if (button) setBusy(button, true, "核对中…");
    // Runtime state reconciles itself in SocietyService; the page re-reads it.
    state.runtimeReconcileInFlight = societyService.loadLineage(lineageId).then(lineage => {
      const response = { lineage };
      if (lineage && state.lineage?.lineage_id === lineageId) commitLineage(lineage);
      if (options.notify !== false) toast(window.PortalI18n.t("HATG Program 运行状态已核对。"), "success");
      return response;
    }).catch(error => {
      if (!options.quiet) toast(window.PortalI18n.text`运行状态核对失败：${error.message || error}`, "error");
      throw error;
    }).finally(() => {
      state.runtimeReconcileInFlight = null;
      if (button) setBusy(button, false);
    });
    return state.runtimeReconcileInFlight;
  }

  function selectedLineageChangeMatches(detail) {
    if (detail?.snapshot_required) return true;
    const lineageId = String(state.lineage?.lineage_id || "");
    if (!lineageId) return false;
    const changed = detail?.changed || {};
    const lineages = Array.isArray(changed.lineage) ? changed.lineage : [];
    if (lineages.includes(lineageId)) return true;
    // Retained historical identities can remain readable after deletion.
    // Their identity notification must revalidate the displayed membership,
    // even when no readable Lineage-domain notification accompanies it.
    const residentIds = new Set((state.lineage.nodes || []).map(nodeResidentId));
    if (Object.entries(detail?.resident_changes || {}).some(([residentId, scopes]) => (
      residentIds.has(residentId)
      && Array.isArray(scopes)
      && scopes.includes("identity")
    ))) return true;
    return (detail?.resident_events || []).some(event => (
      residentIds.has(String(event?.resident_id || ""))
      && event.live !== true
      && event.event_type === "resident.input.state.changed"
      && ["completed", "failed", "cancelled"].includes(event.payload?.input_state?.state)
    ));
  }

  function applyResidentStateCollection(update) {
    if (!state.lineage || update?.type === "error") return;
    const runtimeChanged = applyResidentStateProjections(update?.states || {}, {
      render: false,
      replace: true,
    });
    if (runtimeChanged) {
      updateLineageSummary();
      renderGraphRuntimePresentation();
      renderControls();
      hatgComponentOptions.onRuntimeProjectionChange?.({
        lineageId: state.lineage.lineage_id,
      });
    }
  }

  function selectedLineageMessageCountsChangeMatches(detail) {
    if (selectedLineageChangeMatches(detail)) return true;
    const residentIds = new Set((state.lineage?.nodes || []).map(nodeResidentId));
    if (Object.entries(detail?.resident_changes || {}).some(([residentId, scopes]) => (
      residentIds.has(residentId) && Array.isArray(scopes) && scopes.includes("projection")
    ))) return true;
    return (detail?.resident_events || []).some(event => (
      residentIds.has(String(event?.resident_id || ""))
      && event.live !== true
      && [
        "conversation.message.appended", "conversation.message.updated",
        "resident.input.state.changed", "resident.queue.changed",
      ].includes(String(event.event_type || ""))
    ));
  }

  function bindResidentStateCollection() {
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    const lineageId = String(state.lineage?.lineage_id || "");
    const residentIds = (state.lineage?.nodes || [])
      .filter(node => node.accessible !== false)
      .map(nodeResidentId)
      .filter(Boolean);
    if (!panelActive || !lineageId || !residentIds.length) return;
    state.residentStateCollection = window.PortalResidentProtocol.acquireStates(
      residentIds,
      applyResidentStateCollection,
    );
  }

  function bindLineageProjectionSubscription() {
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    if (!panelActive || !state.lineage) return;
    const coordinator = window.PortalRefreshCoordinator.create({
      key: () => String(state.lineage?.lineage_id || ""),
      interval: 30000,
      fetch: async ({ key }) => ({ lineageId: key, lineage: await societyService.loadLineage(key) }),
      apply: payload => applyLineageEventProjection(payload?.lineageId, payload?.lineage),
      subscribe: window.PortalRefreshCoordinator.portalSubscription({
        domains: ["lineage", "resident"],
        refreshOnAttach: false,
        matches: selectedLineageChangeMatches,
        debounceMs: 150,
      }),
      onError: () => {},
    });
    const countsCoordinator = window.PortalRefreshCoordinator.create({
      key: () => String(state.lineage?.lineage_id || ""),
      interval: 30000,
      fetch: ({ key }) => apiGet(`/lineage/api/node-message-counts?id=${encodeURIComponent(key)}`),
      apply: payload => applyNodeMessageCounts(payload),
      subscribe: window.PortalRefreshCoordinator.portalSubscription({
        domains: ["lineage", "resident"],
        refreshOnAttach: false,
        matches: selectedLineageMessageCountsChangeMatches,
        debounceMs: 150,
      }),
      onError: () => {},
    });
    state.lineageProjectionSubscription = () => {
      coordinator.destroy();
      countsCoordinator.destroy();
    };
    coordinator.start();
    countsCoordinator.start();
    countsCoordinator.invalidate({ delay: 0 });
  }

  function notifyResidentSelection(node, options = {}) {
    if (options.notifyParent === false || !node || node.accessible === false) return undefined;
    const lineageId = options.lineageId || node.home_lineage_id || state.lineage?.lineage_id || "";
    const residentId = nodeResidentId(node);
    return hatgComponentOptions.emit?.("open-resource", {
      type: "chat",
      resource: {
        kind: "chat",
        resident_id: residentId,
        lineage_id: lineageId,
        lineage_resident_id: node.id,
        title: node.title || residentId,
        backend: node.backend,
      },
    });
  }

  function residentEditorOpenMenu(node) {
    if (
      !node
      || node.accessible === false
      || node.kind === "seed"
      || isProgramComponent(node)
      || isFunctionBlockInstance(node)
      || !nodeResidentId(node)
    ) return null;
    return {
      label: window.PortalI18n.text`Open ${node.title || node.id}`,
      title: window.PortalI18n.t("打开这个节点的 Chat Workspace"),
    };
  }

  function notifyResidentOpenMenu(node, event, trigger) {
    if (!residentEditorOpenMenu(node)) return undefined;
    return hatgComponentOptions.onResidentOpenMenu?.({
      lineageId: node.home_lineage_id || state.lineage?.lineage_id || "",
      residentId: nodeResidentId(node),
      lineageResidentId: node.id,
      openDetails: () => openNodeSettingsModal(node),
      isFamilyFacade: Boolean(familyForFacade(nodeResidentId(node))),
      title: node.title || nodeResidentId(node),
      trigger: trigger || event?.currentTarget || null,
      event,
    });
  }

  async function selectNode(nodeId, options = {}) {
    const node = displayNodeById(nodeId);
    if (!node || node.accessible === false) return;
    if (node.id === state.selectedNodeId && !state.selectedSeedId && !state.selectedEdgeId && !state.selectedProgramObjectId && !options.force) {
      // The canvas can hold a card selected without its Chat open, such as the
      // leader when the Lineage loads; clicking that card still opens its Chat.
      if (options.openChat) await notifyResidentSelection(node, options);
      return;
    }
    const workspaceChanged = state.resultWorkspaceNodeId !== node.id;
    state.selectedSeedId = "";
    state.selectedProgramObjectId = "";
    state.selectedEdgeId = "";
    state.selectedNodeId = node.id;
    const residentSelection = Promise.resolve().then(() => notifyResidentSelection(node, options));
    publishLocation(state.lineage?.lineage_id || "");
    state.detail = null;
    state.detailSeq += 1;
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderSeedInspector();
    if (workspaceChanged) {
      state.resultWorkspace = null;
      state.resultWorkspaceNodeId = node.id;
      state.selectedResultPath = "";
      renderLineageHeader();
    }
    await Promise.all([
      residentSelection,
      options.revealInGraph ? revealSelectedGraphNode(node.id, options) : undefined,
    ]);
  }



  async function openFunctionalize() {
    if (!state.lineage) return;
    const elementIds = [...state.selectedCanvasObjectIds];
    if (!elementIds.length) return;
    const title = await window.PortalUI.prompt({
      title: "Functionalize", message: window.PortalI18n.t("为选区命名"),
      value: "Function Block", confirmLabel: "Functionalize",
    });
    if (title === null || title === undefined) return;
    // A refresh/collapse while the prompt was open must not silently change
    // the operation the user confirmed. Keep the service's ownership checks.
    const visibleIds = visibleCanvasObjectIds();
    if (elementIds.some(id => !visibleIds.has(id))) {
      throw new Error(window.PortalI18n.t("选区已变化，请重新选择要封装的元素。"));
    }
    await applyProgramMutation(() => executeHatgInstruction("Functionalize", {
      element_ids: [...new Set(elementIds)], title: title.trim() || "Function Block",
    }), "选区已封装为 Function Block。");
    clearCanvasSelection();
  }

  // Templates use the same selection and coordinate system as canvas gestures.
  function templateCanvasTarget(event) {
    const position = state.graphController?.screenToPosition?.(event.clientX, event.clientY) || { x: 50, y: 50 };
    const hit = event.target?.closest?.("[data-node-id], [data-edge-id], [data-function-block-boundary]");
    const id = hit?.dataset.nodeId || hit?.dataset.edgeId || hit?.dataset.functionBlockBoundary || "";
    if (state.selectedCanvasObjectIds.has(id)) {
      const element_ids = [...state.selectedCanvasObjectIds];
      return { kind: "selection", element_ids, resident_ids: element_ids.filter(id => Boolean(nodeById(id))), ...position };
    }
    if (nodeById(id)) return { kind: "resident", resident_ids: [id], ...position };
    return { kind: "canvas", ...position };
  }


  async function batchDeleteCanvasSelection() {
    if (
      state.canvasSelectionDeleteInFlight
      || !state.selectedCanvasObjectIds.size
    ) return;
    const deletableTargets = selectedCanvasObjects().filter(canvasObjectCanDelete).map(object => ({
      id: object.id,
      title: object.title || object.id,
      kind: isProgramComponent(object) ? "component" : "resident",
    }));
    if (!deletableTargets.length) return;
    const leaderId = String(state.lineage?.leader_resident_id || "");
    const includesLeader = Boolean(leaderId) && deletableTargets.some(target => (
      target.kind === "resident" && target.id === leaderId
    ));
    const residentCount = deletableTargets.filter(target => target.kind === "resident").length;
    const componentCount = deletableTargets.length - residentCount;
    const detail = [
      residentCount ? `${residentCount} 个 Resident 节点` : "",
      componentCount ? `${componentCount} 个 Event / Effect` : "",
    ].filter(Boolean).join("、");
    const confirmed = await window.PortalUI.confirm({
      title: window.PortalI18n.text`批量删除 ${deletableTargets.length} 个 HATG 元素？`,
      message: [
        window.PortalI18n.text`将删除选区中的${detail}，相关连线也会一并移除。`,
        residentCount
          ? window.PortalI18n.t("选中的 Resident 将标记为已删除，并移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。")
          : "",
        includesLeader
          ? window.PortalI18n.t("选区包含这个 Lineage 的 Leader；删除后系统从其余 Resident 中随机选出新的 Leader。")
          : "",
      ].filter(Boolean).join("\n\n"),
      confirmLabel: window.PortalI18n.t("批量删除"),
      tone: "danger",
    });
    if (!confirmed) return;

    const button = $id("batchDeleteSelectionBtn");
    let committed = false;
    if (!programFeature.beginSelectionDelete()) return;
    setBusy(button, true, "删除中…");
    renderControls();
    try {
      const lineageId = state.lineage.lineage_id;
      const residentIds = deletableTargets.filter(target => target.kind === "resident").map(target => target.id);
      const componentIds = deletableTargets.filter(target => target.kind === "component").map(target => target.id);
      const deletedNodes = residentIds.map(nodeById).filter(Boolean);
      const idempotencyKey = `portal:delete-objects:${globalThis.PortalUIPrimitives.randomUUID()}`;
      const result = await applyProgramMutation(() => societyService.deleteObjects( {
        lineage_id: lineageId,
        idempotency_key: idempotencyKey,
        ...(residentIds.length ? { resident_ids: residentIds } : {}),
        ...(componentIds.length ? { component_ids: componentIds } : {}),
      }), "");
      if (!result) return;
      committed = true;
      for (const node of deletedNodes) {
        state.runtimeStatus.delete(node.id);
        state.sessionUserMessageCounts.delete(nodeResidentId(node));
        state.sessionContextPressures.delete(nodeResidentId(node));
      }
      const deletedIds = new Set(result.deleted_resident_ids || []);
      state.residents = state.residents.filter(item => !deletedIds.has(String(item.resident_id || "")));
      if (!programComponentById(state.selectedProgramObjectId)) state.selectedProgramObjectId = "";
    } catch (error) {
      // A transport failure may follow a commit; retain selection until the
      // authoritative graph arrives instead of reporting partial success.
      console.warn("HATG batch deletion failed", error);
    } finally {
      programFeature.endSelectionDelete(
        (committed ? [] : deletableTargets)
          .map(target => target.id)
          .filter(targetId => Boolean(canvasObjectById(targetId))),
      );
      const selectedNodeRemoved = Boolean(state.selectedNodeId) && !nodeById(state.selectedNodeId);
      if (selectedNodeRemoved) clearNodeSelection();
      setBusy(button, false);
      renderLineageSurface();
      if (selectedNodeRemoved || !state.lineage?.nodes.length) {
        void Promise.allSettled([loadResults()]);
      }
    }
    if (committed) {
      toast(
        window.PortalI18n.text`已批量删除 ${deletableTargets.length} 个 HATG 元素。`,
        "success", 4200,
      );
    }
  }

  function canvasNodeCanBeSelected(node) {
    if (!node) return false;
    return Boolean(
      isFunctionBlockInstance(node)
      || isProgramComponent(node)
      || displayProgramComponentById(node.id)
      || displayNodeById(node.id),
    );
  }

  function canvasObjectById(objectId) {
    return functionBlockInstanceById(objectId) || programComponentById(objectId) || displayProgramWireById(objectId) || nodeById(objectId) || null;
  }

  function selectedCanvasObjects() {
    return [...state.selectedCanvasObjectIds]
      .map(canvasObjectById)
      .filter(Boolean);
  }

  function canvasObjectCanDelete(object) {
    // The Leader is deletable too: the service picks a random successor.
    return Boolean(object && !isFunctionBlockInstance(object) && !displayProgramWireById(object.id));
  }

  function renderCanvasSelection() {
    publishLocation(state.lineage?.lineage_id || "");
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderProgramInspector();
  }

  function clearCanvasSelection(options = {}) {
    if (!programFeature.clearSelection()) return false;
    if (options.render !== false) renderCanvasSelection();
    return true;
  }

  function visibleCanvasObjectIds(model = graphVisibleModel()) {
    if (state.graphViewMode !== "context") return new Set();
    return new Set([
      ...model.nodes.map(node => node.id),
      ...model.functionBlockBoundaries.map(block => block.id),
      ...model.edges.filter(edge => !edge.visual_only).map(edge => edge.id),
    ].filter(id => Boolean(canvasObjectById(id))));
  }

  function reconcileCanvasSelection(model = graphVisibleModel()) {
    const visibleIds = visibleCanvasObjectIds(model);
    programFeature.reconcileSelection(id => visibleIds.has(id));
    if (!visibleIds.has(state.canvasSelectionAnchorId)) state.canvasSelectionAnchorId = "";
    return visibleIds;
  }

  function currentCanvasSelectionAnchorId(visibleIds = visibleCanvasObjectIds()) {
    // A wire is selected by its own identity, never its source node. Chat may
    // keep a hidden Resident open without making it a canvas selection anchor.
    return [state.selectedProgramObjectId, state.canvasSelectionAnchorId,
      state.selectedSeedId, state.selectedNodeId].find(id => visibleIds.has(id)) || "";
  }

  function applyCanvasSelection(selected) {
    const visibleIds = visibleCanvasObjectIds();
    programFeature.replaceSelection(
      [...selected].filter(objectId => visibleIds.has(objectId)),
    );
    state.selectedProgramObjectId = "";
    state.selectedSeedId = "";
    state.selectedEdgeId = "";
    renderCanvasSelection();
  }

  function addCanvasObjectsToSelection(objectIds, options = {}) {
    const visibleIds = reconcileCanvasSelection();
    const selected = new Set(state.selectedCanvasObjectIds);
    if (options.includeCurrent && !selected.size) {
      const anchorId = currentCanvasSelectionAnchorId(visibleIds);
      if (anchorId) selected.add(anchorId);
    }
    (objectIds || []).forEach(objectId => {
      if (visibleIds.has(objectId)) selected.add(objectId);
    });
    applyCanvasSelection(selected);
  }

  function toggleCanvasObjectSelection(objectId) {
    const visibleIds = reconcileCanvasSelection();
    if (!visibleIds.has(objectId)) return;
    const selected = new Set(state.selectedCanvasObjectIds);
    if (!selected.size) {
      const anchorId = currentCanvasSelectionAnchorId(visibleIds);
      if (anchorId) selected.add(anchorId);
      selected.add(objectId);
    } else if (selected.has(objectId)) {
      selected.delete(objectId);
    } else {
      selected.add(objectId);
    }
    applyCanvasSelection(selected);
  }

  async function selectProgramObject(objectId, { revealFamily = false } = {}) {
    const component = displayProgramComponentById(objectId);
    const wire = displayProgramWireById(objectId);
    const functionBlockInstance = functionBlockInstanceById(objectId);
    if (!component && !wire && !functionBlockInstance) return;
    state.selectedSeedId = "";
    state.selectedEdgeId = "";
    state.selectedProgramObjectId = objectId;
    clearCanvasSelection({ render: false });
    publishLocation(
      state.lineage?.lineage_id || "",
      component || wire || functionBlockInstance ? objectId : "",
    );
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderProgramInspector();
    if (revealFamily && functionBlockInstance?.family) {
      showFamilyAlone({ instance: functionBlockInstance, roles: familyRoles(functionBlockInstance) });
      return;
    }
    if (component || functionBlockInstance) {
      await revealSelectedGraphNode(objectId, { scale: 1, duration: 0 });
    }
  }

  function clearNodeSelection() {
    state.selectedNodeId = "";
    state.selectedSeedId = "";
    state.detail = null;
  }

  function residentInputBody(node, content) {
    const userContent = String(content || "").trim();
    const body = {
      resident_id: nodeResidentId(node),
      message: {
        message_id: window.PortalChatWorkspace.newMessageIdentity(),
        role: "user",
        content_parts: window.PortalChatWorkspace.composerPayloadContentParts({ text: userContent }),
      },
    };
    return body;
  }

  async function stopResidentTurn(node, options = {}) {
    const target = nodeById(node?.id);
    if (!target || !state.lineage) throw new Error("目标节点已不在当前 Lineage 中");
    if (options.runningOnly && effectiveNodeStatus(target) !== "running") return false;
    const runtime = state.residentRuntimeById.get(nodeResidentId(target));
    const messageId = String(runtime?.resident_state?.active_message_id || "").trim();
    if (!messageId) throw new Error("目标节点没有可停止的活跃消息");
    await window.PortalResidentProtocol.cancelInput({
        resident_id: nodeResidentId(target),
        message_id: messageId,
    });
    return true;
  }

  async function refreshAfterResidentStop(node) {
    const refreshes = [];
    if (state.residentStateCollection) {
      refreshes.push(state.residentStateCollection.load({ force: true }));
    }
    if (refreshes.length) await Promise.allSettled(refreshes);
  }

  async function stopLineageNode(node, options = {}) {
    const target = nodeById(node?.id);
    if (!target || state.nodeStopInFlight.has(target.id)) return false;
    const runningOnly = options.runningOnly !== false;
    if (runningOnly && effectiveNodeStatus(target) !== "running") return false;
    const lineageId = state.lineage?.lineage_id || "";
    state.nodeStopInFlight.add(target.id);
    renderGraph();
    try {
      const stopped = await stopResidentTurn(target, { runningOnly });
      if (!stopped) return false;
      if (state.lineage?.lineage_id !== lineageId) return false;
      toast(window.PortalI18n.text`${target.title} 已 Stop 当前 Session。`, "warning");
      await refreshAfterResidentStop(target);
      return true;
    } catch (error) {
      if (state.lineage?.lineage_id === lineageId) {
        toast(window.PortalI18n.text`Stop ${target.title} 失败：${error.message || error}`, "error", 6200);
      }
      return false;
    } finally {
      state.nodeStopInFlight.delete(target.id);
      if (state.lineage?.lineage_id === lineageId) {
        renderGraph();
        renderControls();
      }
    }
  }

  async function sendPromptToNode(node, prompt, options = {}) {
    const content = String(prompt || "").trim();
    if (!node || !content) throw new Error("目标节点或 prompt 为空");
    await window.PortalResidentProtocol.sendInput(residentInputBody(node, content));
    if (options.select !== false && node.id !== state.selectedNodeId) await selectNode(node.id, { force: true });
    renderGraph();
  }

  async function loadLineages() {
    const payload = await societyService.listLineages();
    state.lineages = Array.isArray(payload?.lineages) ? payload.lineages : [];
  }

  function loadLineageAuxiliaryCatalogs(options = {}) {
    const catalogOptions = { force: options.forceCatalogs === true };
    return Promise.all([
      window.PortalLineageCatalogs.residents(catalogOptions).catch(() => ({ residents: [] })),
      loadLineageRunAsUsers(catalogOptions),
      window.PortalLineageCatalogs.projectCatalog(catalogOptions).catch(() => ({ projects: [] })),
      window.PortalLineageCatalogs.agentBackends(catalogOptions).catch(() => null),
    ]).then(([residents, _users, projects, backendCatalog]) => {
      const nextResidents = Array.isArray(residents?.residents) ? residents.residents : [];
      if (residents?.account_selection) {
        state.accountSelection = residents.account_selection;
      }
      state.residents = nextResidents;
      state.agentBackendCatalog = backendCatalog;
      window.PortalLineageCatalogs.populateBackends($id("nodeBackendSelect"), backendCatalog);
      renderNodeProviderFields();
      state.projects = Array.isArray(projects?.projects) ? projects.projects : [];
      if (!$id("lineageModal")?.hidden) {
        renderLineageProjectOptions($id("lineageProjectInput")?.value || "");
      }
      if (!$id("subgraphMoveModal")?.hidden) {
        renderLineageProjectOptions($id("subgraphMoveProject")?.value || "", $id("subgraphMoveProject"));
      }
      if (state.lineage) renderControls();
    });
  }

  async function loadLineagePageData(options = {}) {
    void loadLineageAuxiliaryCatalogs(options).catch(error => {
      console.warn("Lineage auxiliary catalogs failed to load", error);
    });
    await loadLineages();
  }

  function renderNoLineage(options = {}) {
    const loadError = $id("lineageLoadError");
    if (loadError) loadError.hidden = true;
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    state.graphViewRequestSeq += 1;
    state.workspaceGraphLoadSeq += 1;
    setWorkspaceViewLoading(false);
    state.lineage = null;
    state.expandedFamilyFacadeIds = new Set();
    activateLineageEdits();
    state.graphFocusMode = false;
    state.graphRelationFilter = "all";
    state.graphViewMode = "context";
    state.selectedEdgeId = "";
    state.selectedProgramObjectId = "";
    programFeature.clearForLineageChange();
    state.workspaceGraphBindings = new Map();
    state.selectedWorkspaceId = "";
    state.handoffMode = false;
    state.graphController?.setContext?.("");
    state.resultWorkspace = null;
    state.resultWorkspaceNodeId = "";
    state.resultWorkspaceSeq += 1;
    state.runtimeStatus = new Map();
    state.sessionUserMessageCounts = new Map();
    state.sessionContextPressures = new Map();
    state.selectedResultPath = "";
    state.globalStopInFlight = false;
    state.nodeStopInFlight = new Set();
    if (options.publishLocation !== false) publishLocation("");
    renderLineageSurface();
    clearNodeSelection();
    setSyncStatus("等待创建 Lineage", "idle");
  }

  async function selectLineage(lineageId, options = {}) {
    if (!lineageId) return;
    const loadError = $id("lineageLoadError");
    if (loadError) loadError.hidden = true;
    const sequence = ++state.detailSeq;
    const requestedProgramItemId = String(options.programItemId || "").trim();
    const requestedResidentId = String(options.residentId || "").trim();
    if (state.lineage?.lineage_id === lineageId) {
      resumeDisplayedLineage();
      const requestedSession = (state.lineage.nodes || []).find(node => (
        node.id === requestedResidentId || nodeResidentId(node) === requestedResidentId
      ));
      if (requestedSession) {
        await selectNode(requestedSession.id, {
          force: true,
          notifyParent: false,
          revealInGraph: options.revealInGraph === true,
        });
      }
      if (requestedProgramItemId) {
        await selectProgramObject(requestedProgramItemId, { revealFamily: true });
      }
      return;
    }
    state.graphViewRequestSeq += 1;
    state.workspaceGraphLoadSeq += 1;
    setWorkspaceViewLoading(false);
    if (sequence !== state.detailSeq) return;
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    activateLineageEdits(lineageId);
    setSyncStatus("加载 HATG…", "saving");
    try {
      const [lineage, personalView] = await Promise.all([
        societyService.loadLineage(lineageId).catch(error => {
          if (sequence === state.detailSeq && Number(error?.status) === 404) {
            hatgComponentOptions.emit?.("binding-unavailable");
          }
          throw error;
        }),
        loadHatgViewPreference(lineageId),
      ]);
      if (sequence !== state.detailSeq) return;
      state.lineage = lineage;
      hatgComponentOptions.emit?.("binding-ready");
      // The previous Lineage's selection names no node here. Left in place,
      // rendering would take it for a removed node and open the leader's
      // Chat instead of the Resident this load was asked for.
      state.selectedNodeId = "";
      templateController.load().catch(error => toast(error.message || String(error), "error"));
      programFeature.clearForLineageChange();
      state.expandedFamilyFacadeIds = new Set(
        personalView.expanded_family_facade_ids,
      );
      state.graphFocusMode = false;
      state.graphRelationFilter = "all";
      state.handoffMode = false;
      state.selectedEdgeId = "";
      state.selectedProgramObjectId = "";
      state.workspaceGraphBindings = new Map();
      state.selectedWorkspaceId = "";
      clearGraphNodeSearch();
      const restoredPersonalViewport = state.graphController?.setContext?.(
        lineage.lineage_id,
      ) === true;
      state.resultWorkspace = null;
      state.resultWorkspaceNodeId = "";
      state.resultWorkspaceSeq += 1;
      state.runtimeStatus = new Map();
      state.residentRuntimeById = new Map();
      state.sessionUserMessageCounts = new Map();
      state.sessionContextPressures = new Map();
      state.localVersion += 1;
      state.selectedResultPath = "";
      state.globalStopInFlight = false;
      state.nodeStopInFlight = new Set();
      state.selectedSeedId = "";
      const requestedProgramExists = Boolean(
        requestedProgramItemId
        && (
          displayProgramComponentById(requestedProgramItemId)
          || displayProgramWireById(requestedProgramItemId)
          || functionBlockInstanceById(requestedProgramItemId)
        )
      );
      publishLocation(lineageId);
      updateLineageSummary();
      renderLineageSurface();
      if (restoredPersonalViewport) {
        window.requestAnimationFrame(() => {
          state.graphController?.setContext?.(lineage.lineage_id);
        });
      } else if ((lineage.program?.components || []).length) {
        window.requestAnimationFrame(() => {
          state.graphController?.fit?.({
            duration: 0,
            maxScale: 1.12,
          });
        });
      }
      if (panelActive) {
        bindResidentStateCollection();
        bindLineageProjectionSubscription();
      }
      const currentRevision = Number(lineage.revision || 0);
      setSyncStatus(currentRevision ? `已加载 · revision ${currentRevision}` : "已加载", "saved");
      const requestedSession = (lineage.nodes || []).find(node => (
        node.id === requestedResidentId || nodeResidentId(node) === requestedResidentId
      ));
      const selected = requestedSession
        || lineage.nodes.find(node => node.id === lineage.leader_resident_id)
        || lineage.nodes[0];
      if (selected) {
        await selectNode(selected.id, {
          force: true,
          notifyParent: false,
          revealInGraph: Boolean(requestedSession && options.revealInGraph === true),
        });
      } else {
        clearNodeSelection();
      }
      if (requestedProgramExists) {
        await selectProgramObject(requestedProgramItemId, { revealFamily: true });
      }
    } catch (error) {
      if (sequence !== state.detailSeq) return;
      renderNoLineage({ publishLocation: false });
      setSyncStatus("加载失败", "error");
      if (loadError) {
        loadError.textContent = window.PortalI18n.text`Lineage ${lineageId} 加载失败：${error.message || error}`;
        loadError.hidden = false;
      }
      toast(window.PortalI18n.text`Lineage 加载失败：${error.message || error}`, "error");
    }
  }

  function resumeDisplayedLineage() {
    const lineageId = state.lineage?.lineage_id;
    if (!lineageId) return;
    if (!lineageEditQueue.isActive(lineageId)) {
      activateLineageEdits(lineageId);
      setSyncStatus("已加载", "saved");
    }
    if (panelActive) {
      if (!state.residentStateCollection) bindResidentStateCollection();
      if (!state.lineageProjectionSubscription) bindLineageProjectionSubscription();
    }
  }


  function cancelGraphInteractionMode() {
    if (state.handoffMode) cancelHandoffMode();
  }

  function renderGraphModeHint() {
    const hint = $id("graphModeHint");
    if (!hint) return;
    hint.hidden = !state.handoffMode;
    if (hint.hidden) return;
    const source = nodeById(state.handoffSourceId);
    $id("graphModeHintText").textContent = source
      ? window.PortalI18n.text`交接来源：${source.title}。请选择接收工件与任务契约的目标节点。`
      : window.PortalI18n.t("先选择交接来源节点，再选择目标节点。");
    $id("cancelGraphModeBtn").textContent = window.PortalI18n.t("退出交接模式");
  }

  async function stopAllRunningNodes() {
    if (!state.lineage || state.globalStopInFlight) return;
    const targets = runningLineageNodes();
    if (!targets.length) {
      toast(window.PortalI18n.t("当前 Lineage 没有正在运行的节点。"), "warning");
      return;
    }
    const lineageId = state.lineage.lineage_id;
    state.globalStopInFlight = true;
    targets.forEach(node => state.nodeStopInFlight.add(node.id));
    renderGraph();
    renderControls();
    const failures = [];
    let stoppedCount = 0;
    try {
      for (const snapshot of targets) {
        if (state.lineage?.lineage_id !== lineageId) break;
        const target = nodeById(snapshot.id);
        if (!target) continue;
        if (effectiveNodeStatus(target) !== "running") continue;
        try {
          if (await stopResidentTurn(target, { runningOnly: true })) stoppedCount += 1;
        } catch (error) {
          failures.push({
            title: target.title || target.id,
            error: String(error?.message || error),
          });
        }
      }
      if (state.lineage?.lineage_id !== lineageId) return;
      await refreshAfterResidentStop(null);
      if (!failures.length) {
        toast(window.PortalI18n.text`已 Stop 当前 Lineage 的 ${stoppedCount} 个运行中 Session。`, "warning", 5200);
      } else {
        const failedNames = failures.map(item => item.title).join("、");
        toast(
          window.PortalI18n.text`${stoppedCount} 个 Session 已 Stop；${failures.length} 个节点失败（${failedNames}）。`,
          failures.length === targets.length ? "error" : "warning",
          7200,
        );
      }
    } finally {
      state.globalStopInFlight = false;
      targets.forEach(node => state.nodeStopInFlight.delete(node.id));
      if (state.lineage?.lineage_id === lineageId) renderLineageSurface();
    }
  }

  // Templates own their shelf, editor, drag handling and application forms.
  function mountTemplates() {
    templateController?.destroy();
    templateController = window.LineageTemplates.mount({
      shelf: $id("templateShelf"),
      canvas: $id("hatgCanvas"),
      getContext: () => ({ lineage_id: state.lineage?.lineage_id || "", leader_resident_id: state.lineage?.leader_resident_id, residents: state.lineage?.nodes || [] }),
      resolveTarget: templateCanvasTarget,
      residentSettingsOptions: () => state.agentBackendCatalog,
      onChange: renderGraph,
      toast,
      // Previews change nothing, so they do not wait for or block graph edits.
      request: (instruction, args) => instruction === "ApplyTemplate" && !args?.dry_run
        ? applyProgramMutation(() => executeHatgInstruction(instruction, args))
        : window.PortalInstructionClient.execute(instruction, args),
    });
  }

  function selectedFamilyCandidateIds() {
    const selected = selectedCanvasObjects();
    const objects = selected.length ? selected : [canvasObjectById(graphSelectedId())].filter(Boolean);
    return objects
      .filter(object => !isProgramComponent(object) && !isFunctionBlockInstance(object) && nodeById(object.id))
      .map(object => object.id);
  }

  async function openFamilize() {
    const residentIds = selectedFamilyCandidateIds();
    if (!residentIds.length) return;
    await templateController.applyFamily({ kind: "selection", resident_ids: residentIds, element_ids: residentIds });
  }

  async function requestUnifiedPreview(request = {}) {
    const handler = hatgComponentOptions.onPreviewRequest
      || window.PortalPreview?.request;
    if (typeof handler !== "function") {
      throw new Error("统一 Preview 路由尚未初始化。");
    }
    await handler(request);
    return true;
  }

  function resolveWorkspacePreviewPath(value) {
    const path = String(value || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!path) return "";
    const workspace = state.resultWorkspace || {};
    const entries = Array.isArray(workspace.entries) ? workspace.entries : [];
    const exact = entries.find(entry => String(entry?.path || "") === path);
    if (exact) return String(exact.path || path);

    const roots = Array.isArray(workspace.roots) ? workspace.roots : [];
    const directoryIds = new Set(roots.map(root => String(root?.directory_id || "")).filter(Boolean));
    if (directoryIds.has(path.split("/", 1)[0])) return path;

    const relativeMatches = entries.filter(entry => (
      String(entry?.rel_path || "") === path
      && !["directory", "symlink"].includes(String(entry?.type || entry?.kind || ""))
    ));
    if (relativeMatches.length === 1) return String(relativeMatches[0].path || path);
    const lineageMatch = relativeMatches.find(entry => entry?.is_lineage_workspace === true);
    if (lineageMatch) return String(lineageMatch.path || path);
    const workspaceMatch = relativeMatches.find(entry => String(entry?.root_kind || "") === "workspace");
    if (workspaceMatch) return String(workspaceMatch.path || path);

    const selectedSeed = displaySeedById(state.selectedSeedId) || seedById(state.selectedSeedId);
    const lineageRoot = roots.find(root => root?.is_lineage_workspace === true)
      ;
    if (
      lineageRoot?.directory_id
      && String(selectedSeed?.artifact_path || "").replace(/^\/+|\/+$/g, "") === path
    ) {
      return `${lineageRoot.directory_id}/${path}`;
    }
    const defaultRoot = roots.find(root => root?.is_default_cwd === true)
      || roots.find(root => root?.workspace_usage === "primary");
    return defaultRoot?.directory_id ? `${defaultRoot.directory_id}/${path}` : path;
  }

  async function loadResultFile(path, options = {}) {
    if (!state.lineage || !state.resultWorkspaceNodeId || !path) return false;
    const lineageId = state.lineage.lineage_id;
    const residentId = state.resultWorkspaceNodeId;
    const previewPath = resolveWorkspacePreviewPath(path);
    state.selectedResultPath = previewPath;
    return requestUnifiedPreview({
      locatorKind: "lineage-workspace",
      lineageId,
      residentId,
      path: previewPath,
      title: previewPath.split("/").pop() || window.PortalI18n.t("Preview"),
      source: "hatg",
      reload: options.reload === true,
    });
  }

  async function loadWorkspaceGraphBindings(lineage = state.lineage, options = {}) {
    if (!lineage?.lineage_id) {
      return { complete: false, failedCount: 0, stale: true };
    }
    const render = options.render !== false;
    const fit = options.fit !== false;
    const sequence = state.workspaceGraphLoadSeq;
    const lineageId = lineage.lineage_id;
    const missingNodes = (lineage.nodes || []).filter(node => !state.workspaceGraphBindings.has(node.id));
    if (!missingNodes.length) {
      if (render && state.graphViewMode === "workspace") renderGraph();
      return { complete: true, failedCount: 0, stale: false };
    }
    const results = await Promise.allSettled(missingNodes.map(async node => ({
      nodeId: node.id,
      workspace: await window.PortalResidentService.workspaces(node.id),
    })));
    if (sequence !== state.workspaceGraphLoadSeq || state.lineage?.lineage_id !== lineageId) {
      return { complete: false, failedCount: 0, stale: true };
    }
    const bindings = new Map(state.workspaceGraphBindings);
    results.forEach(result => {
      if (result.status === "fulfilled") bindings.set(result.value.nodeId, result.value.workspace);
    });
    state.workspaceGraphBindings = bindings;
    const failedCount = results.filter(result => result.status === "rejected").length;
    const complete = failedCount === 0
      && (lineage.nodes || []).every(node => bindings.has(node.id));
    if (render && state.graphViewMode === "workspace") {
      renderGraph();
      if (fit) {
        window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.15 }));
      }
    }
    return { complete, failedCount, stale: false };
  }

  async function loadNodeWorkspaces(node = currentNode()) {
    if (!state.lineage || !node) return null;
    const sequence = ++state.resultWorkspaceSeq;
    const lineageId = state.lineage.lineage_id;
    const nodeId = node.id;
    const workspace = await window.PortalResidentService.workspaces(nodeId);
    if (state.lineage?.lineage_id === lineageId) state.workspaceGraphBindings.set(nodeId, workspace);
    if (sequence !== state.resultWorkspaceSeq || state.lineage?.lineage_id !== lineageId || state.selectedNodeId !== nodeId) return null;
    state.resultWorkspace = workspace;
    state.resultWorkspaceNodeId = nodeId;
    renderLineageHeader();
    return workspace;
  }

  async function loadResults() {
    const node = currentNode();
    if (!state.lineage || !node) return;
    try {
      const workspace = await loadNodeWorkspaces(node);
      if (workspace && state.selectedResultPath) {
        await loadResultFile(state.selectedResultPath, { reload: true });
      }
    } catch (error) {
      toast(window.PortalI18n.text`成果列表刷新失败：${error.message || error}`, "error");
    }
  }

  function bindModals() {
    $queryAll("[data-close-modal]").forEach(button => button.addEventListener("click", () => closeModal(button.dataset.closeModal || "")));
    pageStateController.listen("panel", $eventRoot(), "keydown", event => {
      if (event.key !== "Escape") return;
      if (state.handoffMode) cancelHandoffMode();
      else if (state.selectedEdgeId) closeRelationInspector();
    });
  }

  function bindActions() {
    bindComponentLibraryResize();
    bindPrimitiveLibrary();
    $id("refreshLineagesBtn")?.addEventListener("click", () => loadLineages().catch(error => toast(error.message || error, "error")));
    $id("hatgCollapseBtn")?.addEventListener("click", () => {
      hatgComponentOptions.onCollapseRequest?.();
    });
    // The Lineage Menu; project, navigation and deletion stay in the settings dialog it links to.
    $id("lineageSettingsBtn").addEventListener("click", () => {
      if (!state.lineage) return;
      void window.PortalNav.openScopeMenu({
        kind: "lineage", id: state.lineage.lineage_id, record: state.lineage,
        onOpenAdvanced: openEditLineageModal,
      });
    });
    pageStateController.listen("panel", window, "portal:scope-menu-changed", event => {
      const detail = event.detail || {};
      if (detail.kind !== "lineage" || detail.id !== state.lineage?.lineage_id) return;
      if (detail.type === "family") {
        commitLineage(detail.lineage);
        return;
      }
      if (detail.type !== "settings") return;
      Object.assign(state.lineage, clone(detail.changes));
      state.localVersion += 1;
      updateLineageSummary();
      renderLineageSurface();
    });
    $id("lineageForm").addEventListener("submit", submitLineageForm);
    $id("deleteLineageBtn").addEventListener("click", deleteCurrentLineage);
    $id("addNodeBtn").addEventListener("click", openNodeModal);
    $id("emptyAddNodeBtn").addEventListener("click", openNodeModal);
    // Registered before the re-render below, so an explicit route choice is kept.
    $id("nodeRouteSelect").addEventListener("change", () => { state.nodeRouteChosen = true; });
    for (const id of ["nodeBackendSelect", "nodeRouteSelect", "nodeModelSelect"]) {
      $id(id).addEventListener("change", renderNodeProviderFields);
    }
    $id("nodeForm").addEventListener("submit", submitNodeForm);
    $id("nodeAbsorptionKeepBtn").addEventListener("click", keepAbsorbedSourceNode);
    $id("nodeAbsorptionDeleteBtn").addEventListener("click", deleteAbsorbedSourceNode);
    $id("branchNodeBtn").addEventListener("click", () => openBranchModal());
    $id("branchForm").addEventListener("submit", submitBranch);
    $id("branchAccountInput").addEventListener("change", updateBranchAccountHint);
    $id("branchProviderInput").addEventListener("change", event => configureBranchProvider(event.target.value));
    $id("branchModeChoices").querySelectorAll('input[name="branchMode"]').forEach(input => {
      input.addEventListener("change", updateBranchAccountHint);
    });
    $id("handoffModeBtn").addEventListener("click", () => {
      state.handoffPreset = "direct";
      activateHandoffMode();
    });
    $id("cancelGraphModeBtn").addEventListener("click", cancelGraphInteractionMode);
    $id("handoffForm").addEventListener("submit", submitHandoff);
    $id("removeEdgeBtn").addEventListener("click", removeEditingEdge);
    $id("handoffSendNowInput").addEventListener("change", syncHandoffSubmitLabel);
    $id("handoffAddItemBtn").addEventListener("click", addHandoffItem);
    $id("handoffItemsEditor").addEventListener("click", event => {
      const removeButton = event.target.closest("[data-remove-handoff-item]");
      if (removeButton) removeHandoffItem(removeButton);
    });
    $id("handoffItemsEditor").addEventListener("change", event => {
      if (event.target.matches('[data-handoff-field="materializer"]')) {
        syncHandoffMaterializerFields(event.target.closest("[data-handoff-item]"));
      }
    });
    $id("stopLineageBtn").addEventListener("click", stopAllRunningNodes);
    $id("autoLayoutBtn").addEventListener("click", autoLayoutGraph);
    $id("graphLeaderJumpBtn").addEventListener("click", focusLineageLeader);
    $id("addTemplateBtn").addEventListener("click", () => templateController.newTemplate());
    [["saveSelectionTemplateBtn", false], ["saveSelectionRewriteBtn", true]].forEach(([id, rewrite]) => {
      $id(id).addEventListener("click", async event => {
        const elementIds = [...state.selectedCanvasObjectIds];
        if (!elementIds.length) return;
        event.currentTarget.disabled = true;
        try { await templateController.saveSelection(elementIds, { rewrite }); }
        catch (error) { toast(window.PortalI18n.message(error.message || String(error)), "error"); }
        finally { renderControls(); }
      });
    });
    $id("wrapFunctionBlockBtn").addEventListener("click", () => {
      openFunctionalize().catch(error => toast(error.message || String(error), "error"));
    });
    $id("familizeBtn").addEventListener("click", () => {
      openFamilize().catch(error => toast(error.message || String(error), "error"));
    });
    $id("moveSubgraphBtn").addEventListener("click", openSubgraphMove);
    $id("subgraphMoveTarget").addEventListener("change", () => {
      renewSubgraphMoveIntent();
      previewSubgraphMove();
    });
    $id("subgraphMoveProject").addEventListener("change", renewSubgraphMoveIntent);
    $id("subgraphMovePreview").addEventListener("click", () => {
      renewSubgraphMoveIntent();
      loadSubgraphMoveTargets();
    });
    $id("subgraphMoveForm").addEventListener("submit", submitSubgraphMove);
    $id("batchDeleteSelectionBtn").addEventListener(
      "click",
      batchDeleteCanvasSelection,
    );
    $id("cancelCanvasSelectionBtn").addEventListener(
      "click",
      () => clearCanvasSelection(),
    );
    $id("seedPromoteForm").addEventListener("submit", submitSeedPromotion);
    $id("seedDismissBtn").addEventListener("click", () => openSeedDismissModal(state.selectedSeedId));
    $id("seedSettingsBtn")?.addEventListener("click", () => {
      selectProgramObject(state.selectedSeedId).catch(() => {});
    });
    $id("hatgCanvas").addEventListener("hatg:transform", positionSeedInspector);
    const observedCanvas = $id("hatgCanvas");
    seedInspectorObserver = new ResizeObserver(() => {
      if (observedCanvas.isConnected) positionSeedInspector();
    });
    seedInspectorObserver.observe(observedCanvas);
    // The navigation can wrap as docks resize or translated labels change.
    // Reserve its measured block size without forcing synchronous layout reads.
    const graphControlSizes = new Map([
      [$id("graphNavigationBar"), "--hatg-navigation-height"],
    ]);
    const observedViewport = $id("hatgViewport");
    graphControlObserver = new ResizeObserver(entries => {
      if (!observedViewport.isConnected) return;
      for (const entry of entries) {
        const height = entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height;
        observedViewport.style.setProperty(graphControlSizes.get(entry.target), `${height}px`);
      }
    });
    graphControlSizes.forEach((_, element) => graphControlObserver.observe(element));
    $id("seedDismissForm").addEventListener("submit", submitSeedDismissal);
    $queryAll("[data-seed-promote-action]").forEach(button => {
      button.addEventListener("click", () => openSeedPromoteModal(state.selectedSeedId, button.dataset.seedPromoteAction || "custom"));
    });
    $queryAll("[data-seed-action-choice]").forEach(button => {
      button.addEventListener("click", () => setSeedActionChoice(button.dataset.seedActionChoice || "custom"));
    });
    $id("leaderForm").addEventListener("submit", submitLeaderRequest);
    $id("graphFocusModeBtn").addEventListener("click", toggleGraphFocusMode);
    $id("graphContextViewBtn")?.addEventListener("click", () => setGraphViewMode("context"));
    $id("graphWorkspaceViewBtn")?.addEventListener("click", () => setGraphViewMode("workspace"));
    $id("graphRelationFilter").addEventListener("change", changeGraphRelationFilter);
    $id("reconcileLineageRuntimeBtn")?.addEventListener("click", event => reconcileLineageRuntime({ button: event.currentTarget, notify: true }).catch(() => {}));
    bindGraphNodeSearch();
  }

  async function init() {
    mountTemplates();
    try {
      await window.PortalLineageProgram?.loadPrimitiveDefinitions?.();
    } catch (error) {
      setSyncStatus("原语定义加载失败", "error");
      toast(window.PortalI18n.text`HATG 原语定义加载失败：${error.message || error}`, "error", 8000);
      // Let the mount boundary retain the target and show its retry action.
      // A failed prerequisite is not an initialized, empty Lineage panel.
      throw error;
    }
    const hatgPane = $id("hatgPane");
    if (hatgPane) hatgPane.hidden = false;
    bindModals();
    bindActions();
    initHatgGraph();
    renderLineageSurface();
    // Remote reads hydrate the surface; they do not own its interaction lifecycle.
    hatgComponentRoot.inert = false;
    try {
      const me = await window.PortalIdentity.load();
      state.me = String(me?.username || "");
      // HATG needs only SocietyService. Waiting for the host page's whole
      // first load (Resident sidebar, conversations, catalogs) kept a cold
      // HATG idle for about a second, and for many seconds under load. Read
      // now; the service-ready boundary is needed only when that read fails
      // because the service is still starting or recovering.
      try {
        await loadLineagePageData();
      } catch {
        await hatgComponentOptions.waitForServiceReady();
        await loadLineagePageData();
      }
    } catch (error) {
      setSyncStatus("初始化失败", "error");
      toast(window.PortalI18n.text`Lineage 初始化失败：${error.message || error}`, "error", 7000);
    } finally {
      lineageCatalogHydrated = true;
    }
    window.PortalButtonTooltips?.enhance?.(hatgComponentRoot);
  }

  function mountRuntime(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new TypeError("PortalHatgPanel.mountHatgPanel requires root.");
    if (typeof options.waitForServiceReady !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench service-ready boundary.");
    }
    if (typeof options.onLocationChange !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench location owner.");
    }
    if (typeof options.onDeleteLineage !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench Lineage deletion owner.");
    }
    if (!options.pageState || typeof options.pageState.read !== "function" || typeof options.pageState.write !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench page-state owner.");
    }
    hatgComponentRoot = root;
    hatgComponentOptions = { ...options };
    pageStateController = controllerFactory.createController({
      state,
      pageState: options.pageState,
    });
    pageStateController.listen("panel", root, "click", event => {
      const badge = event.target.closest("[data-function-block-issues]");
      if (!badge) return;
      event.preventDefault();
      event.stopPropagation();
      openFunctionBlockMenu(badge.dataset.functionBlockIssues, "issues");
    }, true);
    root.classList.add("pane", "lineageHatgPanel", "portalHatgPanelComponent");
    root.setAttribute("aria-busy", "true");
    root.inert = true;
    let destroyed = false;
    let templateRequestAbort = null;
    let loadPromise = Promise.resolve(null);
    let target = {
      lineageId: String(options.lineageId || ""),
      residentId: String(options.residentId || ""),
      programItemId: String(options.programItemId || ""),
    };
    let appliedTarget = null;
    let targetRequestSeq = 0;
    const parsedTemplateTimeout = options.templateTimeoutMs === undefined
      ? DEFAULT_TEMPLATE_TIMEOUT_MS
      : Number(options.templateTimeoutMs);
    const templateTimeoutMs = Number.isFinite(parsedTemplateTimeout)
      ? parsedTemplateTimeout
      : DEFAULT_TEMPLATE_TIMEOUT_MS;

    function renderTemplateLoadState({ title, detail, retry = false }) {
      if (retry) {
        root.removeAttribute("aria-busy");
        root.inert = false;
      } else {
        root.setAttribute("aria-busy", "true");
        root.inert = true;
      }
      const shell = document.createElement("div");
      shell.className = "lineageHatgEmpty";
      shell.setAttribute("role", retry ? "alert" : "status");
      const heading = document.createElement("strong");
      heading.textContent = title;
      const message = document.createElement("small");
      message.textContent = detail;
      shell.append(heading, message);
      if (retry) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton";
        button.dataset.hatgTemplateRetry = "1";
        button.textContent = window.PortalI18n.t("重试");
        button.addEventListener("click", () => { void startTemplateLoad(); }, { once: true });
        shell.append(button);
      }
      root.replaceChildren(shell);
    }

    async function fetchTemplate() {
      templateRequestAbort?.abort();
      const requestAbort = new AbortController();
      templateRequestAbort = requestAbort;
      let timedOut = false;
      const timeout = templateTimeoutMs > 0
        ? window.setTimeout(() => {
            timedOut = true;
            requestAbort.abort(new DOMException("HATG template timed out", "TimeoutError"));
          }, templateTimeoutMs)
        : 0;
      try {
        return await fetch(options.templateUrl || "/hatg-panel.html", {
          cache: "no-store",
          signal: requestAbort.signal,
        });
      } catch (error) {
        if (timedOut) {
          const seconds = Math.max(1, Math.ceil(templateTimeoutMs / 1000));
          throw new Error(`HATG 模板加载超过 ${seconds} 秒，请重试。`);
        }
        throw error;
      } finally {
        if (timeout) window.clearTimeout(timeout);
        if (templateRequestAbort === requestAbort) templateRequestAbort = null;
      }
    }

    function startTemplateLoad() {
      if (destroyed) return Promise.resolve(null);
      delete root.dataset.hatgError;
      renderTemplateLoadState({ title: window.PortalI18n.t("正在加载 HATG"), detail: "正在读取图编辑模板。" });
      const request = (async () => {
        const [response] = await Promise.all([
          fetchTemplate(),
          window.PortalUIPrimitives.loadAsset("/static/lineage_templates.css"),
        ]);
        if (!response.ok) throw new Error(`HATG template failed (${response.status})`);
        const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
        const fragment = document.createDocumentFragment();
        parsed.querySelectorAll("body > :not(header):not(script)").forEach(node => {
          fragment.append(document.importNode(node, true));
        });
        if (!fragment.childNodes.length) throw new Error("HATG 模板内容为空。");
        if (destroyed) return null;
        root.replaceChildren(fragment);
        window.PortalI18n.renderStatic(root);
        await init();
        if (destroyed) return null;
        root.inert = false;
        if (target.lineageId) await controller.setTarget(target);
        delete root.dataset.hatgError;
        root.removeAttribute("aria-busy");
        return controller;
      })();
      loadPromise = request.catch(error => {
        if (destroyed) return null;
        disconnectGraphObservers();
        root.dataset.hatgError = String(error.message || error);
        renderTemplateLoadState({
          title: window.PortalI18n.t("HATG 加载失败"),
          detail: String(error.message || error),
          retry: true,
        });
        return controller;
      });
      return loadPromise;
    }

    const controller = Object.freeze({
      root,
      get ready() { return loadPromise; },
      retry() { return startTemplateLoad(); },
      suspendLineage(lineageId) {
        const suspendedId = String(lineageId || "");
        if (
          state.lineage?.lineage_id !== suspendedId
          && target.lineageId !== suspendedId
        ) return null;
        const snapshot = controller.snapshot();
        targetRequestSeq += 1;
        state.detailSeq += 1;
        target = { lineageId: "", residentId: "", programItemId: "" };
        appliedTarget = { ...target };
        renderNoLineage({ publishLocation: false });
        return snapshot;
      },
      resumeLineage(snapshot = {}) {
        if (!String(snapshot.lineageId || "")) return Promise.resolve(target);
        return controller.setTarget(snapshot);
      },
      applyLineageDeletion(lineageId) {
        const deletedId = String(lineageId || "");
        state.lineages = state.lineages.filter(item => item.lineage_id !== deletedId);
        if (controller.suspendLineage(deletedId)) publishLocation("");
      },
      async applyLineageMutation(lineage, { separateObjectIds = [] } = {}) {
        await loadPromise;
        if (state.layoutSaveInFlight) await state.layoutSaveInFlight;
        const changed = commitLineage(lineage).changed;
        if (lineage.lineage_id === state.lineage?.lineage_id && separateObjectIds.length) {
          await separateGraphObjects(separateObjectIds);
        }
        return changed;
      },
      async setTarget(next = {}) {
        const requestSeq = ++targetRequestSeq;
        const revealInGraph = next.revealInGraph === true;
        const hasProgramItem = Object.prototype.hasOwnProperty.call(next, "programItemId");
        const requestedLineageId = String(next.lineageId || target.lineageId || "");
        const requestedResidentId = String(next.residentId || target.residentId || "");
        const pendingProgramItemId = (
          target.lineageId === requestedLineageId
          && target.programItemId
          && target.programItemId !== appliedTarget?.programItemId
        ) ? target.programItemId : "";
        const requestedTarget = {
          lineageId: requestedLineageId,
          residentId: requestedResidentId,
          programItemId: hasProgramItem
            ? String(next.programItemId || "")
            : String(state.lineage?.lineage_id === requestedLineageId
              ? state.selectedProgramObjectId || pendingProgramItemId
              : pendingProgramItemId),
        };
        target = requestedTarget;
        state.targetResidentId = requestedTarget.residentId;
        // Before the first catalog read the template load applies this target. After it,
        // an explicit Lineage is opened even when the discoverable list is empty.
        if (!lineageCatalogHydrated) return target;
        const selectedTargetNode = currentNode();
        const selectedTargetMatches = (
          String(state.lineage?.lineage_id || "") === requestedTarget.lineageId
          && (
            !requestedTarget.residentId
            || selectedTargetNode?.id === requestedTarget.residentId
            || nodeResidentId(selectedTargetNode) === requestedTarget.residentId
          )
        );
        if (selectedTargetMatches) {
          // Returning to the displayed graph is still a new navigation. A
          // pending load for another graph must not overwrite this selection.
          state.detailSeq += 1;
          resumeDisplayedLineage();
          let selectionHandledReveal = false;
          if (requestedTarget.programItemId) {
            await selectProgramObject(requestedTarget.programItemId, { revealFamily: true });
          } else if (hasProgramItem && state.selectedProgramObjectId && selectedTargetNode) {
            await selectNode(selectedTargetNode.id, {
              force: true,
              notifyParent: false,
              revealInGraph,
            });
            selectionHandledReveal = revealInGraph;
          }
          if (requestSeq !== targetRequestSeq) return target;
          appliedTarget = { ...requestedTarget };
          if (revealInGraph && selectedTargetNode && !selectionHandledReveal) {
            await revealSelectedGraphNode(selectedTargetNode.id);
          }
          return target;
        }
        if (requestedTarget.lineageId) await selectLineage(requestedTarget.lineageId, {
          residentId: requestedTarget.residentId,
          programItemId: requestedTarget.programItemId,
          revealInGraph,
        });
        if (requestSeq !== targetRequestSeq) return target;
        if (requestSeq === targetRequestSeq) appliedTarget = { ...requestedTarget };
        return target;
      },
      async editLineage(next = {}) {
        await loadPromise;
        await controller.setTarget(next);
        if (!state.lineage) throw new Error("Lineage 尚未加载");
        const modal = $id("lineageModal");
        if (modal && modal.parentElement !== document.body) {
          modal.dataset.portalDetached = "1";
          document.body.append(modal);
          detachedRoots.add(modal);
        }
        openEditLineageModal();
        return state.lineage.lineage_id;
      },
      async createLineage(next = {}) {
        await loadPromise;
        const modal = $id("lineageModal");
        if (modal && modal.parentElement !== document.body) {
          modal.dataset.portalDetached = "1";
          document.body.append(modal);
          detachedRoots.add(modal);
        }
        openCreateLineageModal(next.projectId || "");
      },
      setContext(context = {}) {
        const next = context.hatg || context;
        if (next.lineageId) void controller.setTarget(next);
      },
      snapshot: () => ({
        lineageId: state.lineage?.lineage_id || target.lineageId,
        residentId: state.selectedNodeId || target.residentId,
        programItemId: state.lineage ? state.selectedProgramObjectId : target.programItemId,
      }),
      restore(snapshot = {}) { target = { ...target, ...snapshot }; },
      show() {
        panelActive = true;
        root.hidden = false;
        if (state.lineage) {
          bindResidentStateCollection();
          bindLineageProjectionSubscription();
        }
      },
      hide() {
        panelActive = false;
        clearGraphNodeSearch();
        root.hidden = true;
        state.residentStateCollection?.destroy?.();
        state.residentStateCollection = null;
        state.lineageProjectionSubscription?.();
        state.lineageProjectionSubscription = null;
      },
      destroy() {
        destroyed = true;
        panelActive = false;
        disconnectGraphObservers();
        disposeLibraryResize?.();
        disposeLibraryResize = null;
        destroyGraphNodeSearch();
        activateLineageEdits();
        templateRequestAbort?.abort();
        templateRequestAbort = null;
        root.removeAttribute("aria-busy");
        delete root.dataset.hatgError;
        root.inert = false;
        state.residentStateCollection?.destroy?.();
        state.residentStateCollection = null;
        state.lineageProjectionSubscription?.();
        state.lineageProjectionSubscription = null;
        state.graphController?.destroy?.();
        pageStateController?.destroy?.();
        templateController?.destroy();
        root.replaceChildren();
      },
    });
    loadPromise = startTemplateLoad();
    return controller;
  }

  return Object.freeze({ mount: mountRuntime });
  }

  function mountHatgPanel(options = {}) {
    return createHatgRuntime().mount(options);
  }

  window.PortalHatgPanel = Object.freeze({ mountHatgPanel });
})();
