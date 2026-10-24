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
    $id, $query, activeSeeds, currentPanelActive: () => panelActive, esc, nodeResidentId,
    residentDiscovery, state, toast,
    // The canvas projection is created after this part.
    workspaceGraphModel: (...args) => workspaceGraphModel(...args),
    // The canvas view is created after this part.
    focusGraphNode: (...args) => focusGraphNode(...args),
  });

  const programFeature = state.features.program;

  // The Lineage copy and the program edits come before the parts that change
  // the Lineage through them.
  const {
    lineageEditQueue, activateLineageEdits, flushLineageLayoutSave, saveLineageLayout, commitLineage,
    applyLineageEventProjection,
  } = hatgModule("PortalHatgLineageState").createLineageState({
    activeSeeds, clone, currentTemplateController: () => templateController, nodeResidentId,
    programFeature, renderControls, renderLineageSurface, setSyncStatus, societyService, state,
    toast, updateLineageSummary,
    // The program inspector is created after this part and commits through it.
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    // The canvas selection part is created after this part.
    reconcileGraphInteractionState: (...args) => reconcileGraphInteractionState(...args),
    // The canvas view is created after this part.
    renderGraphRuntimePresentation: (...args) => renderGraphRuntimePresentation(...args),
    // The Resident running state is created after this part.
    bindResidentStateCollection: (...args) => bindResidentStateCollection(...args),
  });

  const {
    applyProgramMutation, executeHatgInstruction, renderProgramWireHint, cancelProgramWireDraft,
    handleProgramPortClick, createProgramComponentFromDrop, bindPrimitiveLibrary,
  } = hatgModule("PortalHatgProgramEdits").createProgramEdits({
    $eventRoot, $id, $queryAll, commitLineage, currentProgram, displayGraphItemById,
    displayProgramWireById, graphSelectedId, lineageEditQueue, programComponentById,
    programFeature, renderLineageSurface, societyService, state, toast,
    // Layout, the Resident dialogs and the program inspector are created after
    // this part. An edit first saves the pending view and afterwards separates new
    // objects; a wire first saves the inspector's open draft.
    flushHatgViewPreferenceSave: (...args) => flushHatgViewPreferenceSave(...args),
    nextNodePosition: (...args) => nextNodePosition(...args),
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    saveProgramComponentFromInspector: (...args) => saveProgramComponentFromInspector(...args),
    separateGraphObject: (...args) => separateGraphObject(...args),
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
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
    reconcileGraphInteractionState, canvasNodeCanBeSelected, selectedCanvasObjects,
    canvasObjectCanDelete, clearCanvasSelection, reconcileCanvasSelection,
    addCanvasObjectsToSelection, toggleCanvasObjectSelection, selectProgramObject,
    clearNodeSelection, selectNode, notifyResidentSelection, residentEditorOpenMenu,
    notifyResidentOpenMenu, selectProgramComponent, batchDeleteCanvasSelection,
    openFunctionalize, selectedFamilyCandidateIds, openFamilize,
  } = hatgModule("PortalHatgCanvasSelection").createCanvasSelection({
    $id, applyProgramMutation, currentHatgComponentOptions: () => hatgComponentOptions,
    currentProgram, currentTemplateController: () => templateController, displayGraphItemById,
    displayNodeById, displayProgramComponentById, displayProgramWireById, displaySeedById,
    executeHatgInstruction, familyForFacade, familyRoles, foldedFamilyObjects,
    functionBlockInstanceById, graphSelectedId, isFunctionBlockInstance,
    isPacketOperatorComponent, isProgramComponent, nodeById, nodeResidentId,
    programComponentById, programFeature, publishLocation, renderControls, renderLineageHeader,
    renderLineageSurface, setBusy, showFamilyAlone, societyService, state, toast,
    // The canvas projection, the program inspector, Seeds and Resident dialogs
    // are created after this part: it selects among the drawn objects, and they
    // show what it selects.
    graphVisibleModel: (...args) => graphVisibleModel(...args),
    openNodeSettingsModal: (...args) => openNodeSettingsModal(...args),
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    renderSeedInspector: (...args) => renderSeedInspector(...args),
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    renderGraphSelection: (...args) => renderGraphSelection(...args),
    revealSelectedGraphNode: (...args) => revealSelectedGraphNode(...args),
    // The Workspace results is created after this part.
    loadResults: (...args) => loadResults(...args),
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
    programEffectRuntime, programEventSourceRuntime, programExecutionExplanation, state,
    // The canvas view is created after this part.
    renderGraphRuntimePresentation: (...args) => renderGraphRuntimePresentation(...args),
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
    $id, closeModal, commitLineage, effectiveNodeStatus, nodeById, nodeResidentId, setBusy,
    societyService, state, toast,
    // The Resident running state is created after this part.
    sendPromptToNode: (...args) => sendPromptToNode(...args),
  });


  const {
    seedTypeLabel, renderSeedInspector, positionSeedInspector, dismissSeedSelection, selectSeed,
    setSeedActionChoice, openSeedPromoteModal, submitSeedPromotion, openSeedDismissModal,
    submitSeedDismissal,
  } = hatgModule("PortalHatgSeeds").createSeeds({
    $id, $queryAll, closeModal, commitLineage, displayNodeById, displaySeedById, esc,
    executeHatgInstruction, nodeById, openModal, programComponentById, programFeature,
    renderControls, renderLineageHeader, renderLineageSurface, seedById, selectNode, setBusy,
    setSyncStatus, state, toast,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    renderGraphSelection: (...args) => renderGraphSelection(...args),
    // The Workspace results is created after this part.
    loadResultFile: (...args) => loadResultFile(...args),
    loadResults: (...args) => loadResults(...args),
  });


  const {
    renderNodeProviderFields, openNodeModal, nextNodePosition, submitNodeForm,
    openNodeSettingsModal, residentMenuHandlers, absorbNodeIntoTarget, keepAbsorbedSourceNode,
    deleteAbsorbedSourceNode, updateBranchAccountHint, configureBranchProvider,
    resetBranchModalState, openBranchModal, createQuickFork, submitBranch,
  } = hatgModule("PortalHatgResidentDialogs").createResidentDialogs({
    $id, clearNodeSelection, clone, closeModal, commitLineage, compactText,
    currentHatgComponentOptions: () => hatgComponentOptions, currentNode, displayedProgram,
    displayNodeById, esc, familyForFacade, familyForNewResident, flushLineageLayoutSave,
    formatDate, lineageEdgeReferencesResident, loadLineagePageData, nodeById, nodeIntakeState,
    nodeResidentId, openModal, programFeature, removeFamilyInstance, renderLineageSurface,
    revealUnfamilizedMember, selectLineage, selectNode, setBusy, setNodeRuntimeStatus,
    setSyncStatus, societyService, state, toast,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    // The Resident running state is created after this part.
    bindResidentStateCollection: (...args) => bindResidentStateCollection(...args),
    // The Workspace results is created after this part.
    loadResults: (...args) => loadResults(...args),
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
    graphItemById, renderControls, saveLineageLayout, state, toast,
    // The canvas projection is created after this part.
    graphVisibleModel: (...args) => graphVisibleModel(...args),
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
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
    refreshFunctionBlockMenu, renderControls, renderLineageSurface, seedById,
    selectProgramComponent, setBusy, setSyncStatus, societyService, state,
    syncProgramComponentSwitch, syncProgramRunAsGuidance, toast, toggleProgramComponent,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
  });

  const {
    activateHandoffMode, cancelHandoffMode, handleHandoffNodeClick, addHandoffItem,
    removeHandoffItem, syncHandoffMaterializerFields, syncHandoffSubmitLabel, submitHandoff,
    removeEditingEdge, selectRelationEdge, closeRelationInspector, renderRelationInspector,
  } = hatgModule("PortalHatgRelations").createRelations({
    $id, clone, closeModal, commitLineage, compactText, displayEdgeById, displayNodeById,
    edgeById, esc, fillWorkspaceSelect, formatBytes, nodeById, nodeResidentId, openModal,
    programFeature, publishLocation, RELATION_LABEL, relationFamily, renderControls,
    renderLineageSurface, renderProgramInspector, renderSeedInspector, seedById, seedTypeLabel,
    selectedBoundWorkspace, selectNode, selectSeed, setBusy, setSeedCandidateFork,
    societyService, state, toast, uid,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    renderGraphModeHint: (...args) => renderGraphModeHint(...args),
    renderGraphSelection: (...args) => renderGraphSelection(...args),
    setGraphViewMode: (...args) => setGraphViewMode(...args),
    // The Workspace results is created after this part.
    loadWorkspaceGraphBindings: (...args) => loadWorkspaceGraphBindings(...args),
  });

  const {
    renderGraph, renderGraphRuntimePresentation, renderGraphSelection, initHatgGraph,
    revealSelectedGraphNode, focusGraphNode, focusLineageLeader, toggleGraphFocusMode,
    changeGraphRelationFilter, setWorkspaceViewLoading, setGraphViewMode,
    cancelGraphInteractionMode, renderGraphModeHint,
  } = hatgModule("PortalHatgGraphView").createGraphView({
    $id, absorbNodeIntoTarget, addCanvasObjectsToSelection, cancelHandoffMode,
    cancelProgramWireDraft, canvasNodeCanBeSelected, clearCanvasSelection, clearGraphNodeSearch,
    closeRelationInspector, compactText, createProgramComponentFromDrop, createQuickFork,
    currentHatgComponentOptions: () => hatgComponentOptions, currentProgram,
    currentTemplateController: () => templateController, dismissSeedSelection, displayEdgeById,
    displayGraphItemById, displayNodeById, displaySeedById, effectiveNodeStatus,
    effectScriptLabel, emitManualProgramEvent, esc, familyExpanded, familyForFacade,
    familyMemberTag, familyRoles, familySpotlight, FIT_ALL_VIEW,
    functionBlockInstanceUnacknowledgedFailures, functionBlockSourceTitle, graphItemById,
    graphSelectedEdgeId, graphSelectedId, graphVisibleModel, handleHandoffNodeClick,
    handleProgramPortClick, HATG_WORLD_SIZE, hatgNodeControl, hatgNodeHoverActions,
    hatgViewPreferences, isFunctionBlockInstance, isProgramComponent, nodeById,
    nodeContextHeatBadge, nodeContextPressure, nodeExecutionGate, nodeIntakeState, nodeIsDerived,
    nodeMessageCountText, nodeResidentId, nodeWorkPresentation, normalizeOptionalLimit,
    notifyResidentOpenMenu, notifyResidentSelection, openFunctionBlockMenu,
    openNodeSettingsModal, openSeedPromoteModal, positionSeedInspector, programComponentPaused,
    programEventSourceRuntime, programEventSourceStatus, programExecutionExplanation,
    reconcileCanvasSelection, refreshGraphNodeSearchResults, relationVisibleInView,
    removeFamilyInstance, renderAutoLayoutControl, renderControls, renderLineageSurface,
    renderProgramInspector, renderProgramWireHint, renderRelationInspector,
    resetProgramEventSource, residentEditorOpenMenu, saveLineageLayout,
    scheduleHatgViewPreferenceSave, selectedFamilyPigeonId, selectNode, selectProgramComponent,
    selectProgramObject, selectRelationEdge, selectSeed, setFunctionBlockExpanded,
    setSeedCandidateFork, showFamilyAlone, state, syncGraphNodePosition,
    syncWorkspaceNodePosition, toast, toggleCanvasObjectSelection, toggleFamilyFacade,
    workspaceGraphModel,
    // The Resident running state is created after this part.
    stopLineageNode: (...args) => stopLineageNode(...args),
    // The Workspace results is created after this part.
    loadWorkspaceGraphBindings: (...args) => loadWorkspaceGraphBindings(...args),
  });

  const {
    loadResultFile, loadWorkspaceGraphBindings, loadResults,
  } = hatgModule("PortalHatgWorkspaceResults").createWorkspaceResults({
    currentHatgComponentOptions: () => hatgComponentOptions, currentNode, displaySeedById,
    renderGraph, renderLineageHeader, seedById, state, toast,
  });


  const {
    reconcileLineageRuntime, bindResidentStateCollection, bindLineageProjectionSubscription,
    stopLineageNode, sendPromptToNode, stopAllRunningNodes,
  } = hatgModule("PortalHatgResidentRuntime").createResidentRuntime({
    apiGet, applyLineageEventProjection, applyNodeMessageCounts, commitLineage,
    currentHatgComponentOptions: () => hatgComponentOptions,
    currentPanelActive: () => panelActive, effectiveNodeStatus, nodeById, nodeExecutionGate,
    nodeIntakeState, nodeResidentId, nodeWorkPresentation, renderControls, renderGraph,
    renderGraphRuntimePresentation, renderLineageSurface, runningLineageNodes, selectNode,
    setBusy, setNodeRuntimeStatus, societyService, state, toast, updateLineageSummary,
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
