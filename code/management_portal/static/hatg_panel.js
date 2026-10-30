(() => {
  "use strict";

  function createHatgRuntime() {

  // What mounting attaches to the runtime, read by the runtime and its parts
  // whenever they run: the element the panel is mounted in and the mount
  // options (set by mounting, which also creates the page-state controller),
  // the component library splitter's disposer (set by the bindings), whether
  // the panel is shown (set by the controller) and the template shelf's
  // controller (created by the shelf).
  const panelHost = {
    hatgComponentRoot: document, hatgComponentOptions: {}, disposeLibraryResize: null,
    panelActive: true, pageStateController: null, templateController: null,
  };
  // Dialogs this runtime moved to document.body still belong to it. Several
  // HATG runtimes can be mounted with the same element IDs, so they are searched
  // before the document: getElementById would return the first runtime's copy.
  const detachedRoots = new Set();
  const $id = id => {
    const selector = `#${CSS.escape(id)}`;
    const found = panelHost.hatgComponentRoot.querySelector?.(selector);
    if (found) return found;
    for (const root of detachedRoots) {
      const match = root.matches?.(selector) ? root : root.querySelector?.(selector);
      if (match) return match;
    }
    return document.getElementById(id) || null;
  };
  const $query = selector => panelHost.hatgComponentRoot.querySelector(selector);
  const $queryAll = selector => panelHost.hatgComponentRoot.querySelectorAll(selector);
  const $eventRoot = () => panelHost.hatgComponentRoot;
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const apiGet = path => window.PortalAPI.get(path, { cache: "no-store" });
  const apiPost = (path, body) => window.PortalAPI.post(path, body);
  const societyService = window.PortalSocietyService.createClient({
    get: apiGet,
    getRevision: id => state.lineage?.lineage_id === id ? state.lineage.revision : undefined,
    onResidentsDeleted: retireDeletedResidents,
    onConflict: ({ lineageId, lineage, error }) => {
      if (state.lineage?.lineage_id !== lineageId) return;
      const roots = [panelHost.hatgComponentRoot, ...detachedRoots];
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
    return panelHost.hatgComponentOptions.onResidentsDeleted?.(ids);
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
    $id, $query, activeSeeds, currentPanelActive: () => panelHost.panelActive, esc, nodeResidentId,
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
    activeSeeds, clone, currentTemplateController: () => panelHost.templateController, nodeResidentId,
    programFeature, renderLineageSurface, societyService, state, toast,
    // The program inspector is created after this part and commits through it.
    renderProgramInspector: (...args) => renderProgramInspector(...args),
    // The canvas selection part is created after this part.
    reconcileGraphInteractionState: (...args) => reconcileGraphInteractionState(...args),
    // The canvas view is created after this part.
    renderGraphRuntimePresentation: (...args) => renderGraphRuntimePresentation(...args),
    // The Resident running state is created after this part.
    bindResidentStateCollection: (...args) => bindResidentStateCollection(...args),
    // The Lineage header and toolbar is created after this part.
    renderControls: (...args) => renderControls(...args),
    setSyncStatus: (...args) => setSyncStatus(...args),
    updateLineageSummary: (...args) => updateLineageSummary(...args),
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
    applyProgramMutation, currentPageStateController: () => panelHost.pageStateController, displayNodeById,
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
    $id, applyProgramMutation, currentHatgComponentOptions: () => panelHost.hatgComponentOptions,
    currentProgram, currentTemplateController: () => panelHost.templateController, displayGraphItemById,
    displayNodeById, displayProgramComponentById, displayProgramWireById, displaySeedById,
    executeHatgInstruction, familyForFacade, familyRoles, foldedFamilyObjects,
    functionBlockInstanceById, graphSelectedId, isFunctionBlockInstance,
    isPacketOperatorComponent, isProgramComponent, nodeById, nodeResidentId,
    programComponentById, programFeature, publishLocation, renderLineageSurface, setBusy,
    showFamilyAlone, societyService, state, toast,
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
    // The Lineage header and toolbar is created after this part.
    renderControls: (...args) => renderControls(...args),
    renderLineageHeader: (...args) => renderLineageHeader(...args),
  });

  const {
    programEventSourceStatus, syncProgramLeaderGuidance, programComponentPaused,
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
    currentTemplateController: () => panelHost.templateController, definitionRevisions, displayNodeById,
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
    renderLineageSurface, seedById, selectNode, setBusy, state, toast,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    renderGraphSelection: (...args) => renderGraphSelection(...args),
    // The Workspace results is created after this part.
    loadResultFile: (...args) => loadResultFile(...args),
    loadResults: (...args) => loadResults(...args),
    // The Lineage header and toolbar is created after this part.
    renderControls: (...args) => renderControls(...args),
    renderLineageHeader: (...args) => renderLineageHeader(...args),
    setSyncStatus: (...args) => setSyncStatus(...args),
  });

  const {
    renderNodeProviderFields, openNodeModal, nextNodePosition, submitNodeForm,
    openNodeSettingsModal, residentMenuHandlers, absorbNodeIntoTarget, keepAbsorbedSourceNode,
    deleteAbsorbedSourceNode, updateBranchAccountHint, configureBranchProvider,
    resetBranchModalState, openBranchModal, createQuickFork, submitBranch,
  } = hatgModule("PortalHatgResidentDialogs").createResidentDialogs({
    $id, clearNodeSelection, clone, closeModal, commitLineage, compactText,
    currentHatgComponentOptions: () => panelHost.hatgComponentOptions, currentNode, displayedProgram,
    displayNodeById, esc, familyForFacade, familyForNewResident, flushLineageLayoutSave,
    formatDate, lineageEdgeReferencesResident, nodeById, nodeIntakeState,
    nodeResidentId, openModal, programFeature, removeFamilyInstance, renderLineageSurface,
    revealUnfamilizedMember, selectNode, setBusy, setNodeRuntimeStatus,
    societyService, state, toast,
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    // Layout is initialized after the Resident dialogs.
    separateGraphObject: (...args) => separateGraphObject(...args),
    // The Resident running state is created after this part.
    bindResidentStateCollection: (...args) => bindResidentStateCollection(...args),
    // The Workspace results is created after this part.
    loadResults: (...args) => loadResults(...args),
    // The Lineage header and toolbar is created after this part.
    setSyncStatus: (...args) => setSyncStatus(...args),
    // Lineage loading is created after this part.
    loadLineagePageData: (...args) => loadLineagePageData(...args),
    selectLineage: (...args) => selectLineage(...args),
  });

  const {
    renderLineageProjectOptions, subgraphMoveBusy, renewSubgraphMoveIntent, previewSubgraphMove,
    loadSubgraphMoveTargets, openSubgraphMove, submitSubgraphMove,
    openCreateLineageModal, openEditLineageModal, submitLineageForm, deleteCurrentLineage,
  } = hatgModule("PortalHatgLineageDialogs").createLineageDialogs({
    $id, clearCanvasSelection, clone, closeModal,
    currentHatgComponentOptions: () => panelHost.hatgComponentOptions, displayNodeById, esc,
    isFunctionBlockInstance, openModal, openNodeModal, renderLineageSurface, setBusy, societyService,
    state, toast,
    // The Lineage header and toolbar is created after this part.
    renderControls: (...args) => renderControls(...args),
    updateLineageSummary: (...args) => updateLineageSummary(...args),
    // Lineage loading is created after this part.
    loadLineagePageData: (...args) => loadLineagePageData(...args),
    loadLineages: (...args) => loadLineages(...args),
    selectLineage: (...args) => selectLineage(...args),
  });

  const {
    hatgViewPreferences, HATG_WORLD_SIZE, AUTO_LAYOUT_VIEW, loadHatgViewPreference,
    scheduleHatgViewPreferenceSave, flushHatgViewPreferenceSave, syncWorkspaceNodePosition,
    syncGraphNodePosition, separateGraphObject, separateGraphObjects, autoLayoutScope,
    renderAutoLayoutControl, readableAutoLayoutOptions, autoLayoutGraph,
  } = hatgModule("PortalHatgLayout").createLayout({
    $id, clone, commitLineage, focusedFamily, functionBlockElements, functionBlockInstanceById,
    graphItemById, saveLineageLayout, state, toast,
    // The canvas projection is created after this part.
    graphVisibleModel: (...args) => graphVisibleModel(...args),
    // The canvas view is created after this part.
    renderGraph: (...args) => renderGraph(...args),
    // The Lineage header and toolbar is created after this part.
    renderControls: (...args) => renderControls(...args),
  });

  const {
    setSyncStatus, updateLineageSummary, renderLineageHeader, renderControls,
  } = hatgModule("PortalHatgToolbar").createToolbar({
    $id, activeSeeds, canvasObjectCanDelete, currentNode, displayNodeById, effectiveNodeStatus,
    familyForFacade, renderAutoLayoutControl, runningLineageNodes, selectedCanvasObjects,
    selectedFamilyCandidateIds, state, subgraphMoveBusy,
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
    syncProgramComponentSwitch, syncProgramLeaderGuidance, toast, toggleProgramComponent,
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
    currentHatgComponentOptions: () => panelHost.hatgComponentOptions, currentProgram,
    currentTemplateController: () => panelHost.templateController, dismissSeedSelection, displayEdgeById,
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
    currentHatgComponentOptions: () => panelHost.hatgComponentOptions, currentNode, displaySeedById,
    renderGraph, renderLineageHeader, seedById, state, toast,
  });

  const {
    reconcileLineageRuntime, bindResidentStateCollection, bindLineageProjectionSubscription,
    stopLineageNode, sendPromptToNode, stopAllRunningNodes,
  } = hatgModule("PortalHatgResidentRuntime").createResidentRuntime({
    apiGet, applyLineageEventProjection, applyNodeMessageCounts, commitLineage,
    currentHatgComponentOptions: () => panelHost.hatgComponentOptions,
    currentPanelActive: () => panelHost.panelActive, effectiveNodeStatus, nodeById, nodeExecutionGate,
    nodeIntakeState, nodeResidentId, nodeWorkPresentation, renderControls, renderGraph,
    renderGraphRuntimePresentation, renderLineageSurface, runningLineageNodes, selectNode,
    setBusy, setNodeRuntimeStatus, societyService, state, toast, updateLineageSummary,
  });

  // The panel's own work is in parts too, created after every part they call:
  //   hatg_panel_lineage_loading.js  the Lineage list and the dialogs'
  //     catalogs, opening a Lineage, showing none, resuming the shown one
  //   hatg_panel_template_shelf.js  the Lineage template shelf on the canvas
  //   hatg_panel_bindings.js  the template's dialogs, buttons and forms, the
  //     component library splitter and the canvas observers
  //   hatg_panel_mount.js  loading the panel template, the first
  //     initialization and the controller the Workbench drives
  const {
    loadLineagePageData, loadLineages, renderNoLineage, resumeDisplayedLineage, selectLineage,
  } = hatgModule("PortalHatgPanelLineageLoading").createLineageLoading({
    $id, activateLineageEdits, bindLineageProjectionSubscription, bindResidentStateCollection,
    clearGraphNodeSearch, clearNodeSelection, displayProgramComponentById, displayProgramWireById,
    functionBlockInstanceById, lineageEditQueue, loadHatgViewPreference, nodeResidentId, panelHost,
    programFeature, publishLocation, renderControls, renderLineageProjectOptions, renderLineageSurface,
    renderNodeProviderFields, selectNode, selectProgramObject, setSyncStatus, setWorkspaceViewLoading,
    societyService, state, toast, updateLineageSummary,
  });

  const { mountTemplates } = hatgModule("PortalHatgPanelTemplateShelf").createTemplateShelf({
    $id, applyProgramMutation, executeHatgInstruction, nodeById, panelHost, renderGraph, state, toast,
  });

  const {
    bindActions, bindModals, disconnectGraphObservers,
  } = hatgModule("PortalHatgPanelBindings").createBindings({
    $eventRoot, $id, $queryAll, activateHandoffMode, addHandoffItem, autoLayoutGraph,
    batchDeleteCanvasSelection, bindGraphNodeSearch, bindPrimitiveLibrary, cancelGraphInteractionMode,
    cancelHandoffMode, changeGraphRelationFilter, clearCanvasSelection, clone, closeModal,
    closeRelationInspector, commitLineage, configureBranchProvider, deleteAbsorbedSourceNode,
    deleteCurrentLineage, focusLineageLeader, hatgModule, keepAbsorbedSourceNode, loadLineages,
    loadSubgraphMoveTargets, openBranchModal, openEditLineageModal, openFamilize, openFunctionalize,
    openNodeModal, openSeedDismissModal, openSeedPromoteModal, openSubgraphMove, panelHost,
    positionSeedInspector, previewSubgraphMove, reconcileLineageRuntime, removeEditingEdge,
    removeHandoffItem, renderControls, renderLineageSurface, renderNodeProviderFields,
    renewSubgraphMoveIntent, selectProgramObject, setGraphViewMode, setSeedActionChoice, state,
    stopAllRunningNodes, submitBranch, submitHandoff, submitLeaderRequest, submitLineageForm,
    submitNodeForm, submitSeedDismissal, submitSeedPromotion, submitSubgraphMove,
    syncHandoffMaterializerFields, syncHandoffSubmitLabel, toast, toggleGraphFocusMode,
    updateBranchAccountHint, updateLineageSummary,
  });

  const { mountRuntime } = hatgModule("PortalHatgPanelMount").createMount({
    $id, DEFAULT_TEMPLATE_TIMEOUT_MS, activateLineageEdits, bindActions,
    bindLineageProjectionSubscription, bindModals, bindResidentStateCollection, clearGraphNodeSearch,
    commitLineage, controllerFactory, currentNode, destroyGraphNodeSearch, detachedRoots,
    disconnectGraphObservers, initHatgGraph, loadLineagePageData, mountTemplates, nodeResidentId,
    openCreateLineageModal, openEditLineageModal, openFunctionBlockMenu, panelHost, publishLocation,
    renderLineageSurface, renderNoLineage, resumeDisplayedLineage, revealSelectedGraphNode,
    selectLineage, selectNode, selectProgramObject, separateGraphObjects, setSyncStatus, state, toast,
  });

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
        if (id === "lineageModal" && modal.dataset.portalDetached === "1" && panelHost.hatgComponentRoot) {
          delete modal.dataset.portalDetached;
          panelHost.hatgComponentRoot.append(modal);
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
    return panelHost.hatgComponentOptions.onLocationChange?.({
      lineageId: String(lineageId || ""),
      programItemId: String(programItemId || ""),
    });
  }

  function renderLineageSurface() {
    reconcileGraphInteractionState();
    renderLineageHeader();
    renderGraph();
    renderProgramInspector();
    panelHost.templateController?.render();
    renderControls();
    renderSeedInspector();
  }

  return Object.freeze({ mount: mountRuntime });
  }

  function mountHatgPanel(options = {}) {
    return createHatgRuntime().mount(options);
  }

  window.PortalHatgPanel = Object.freeze({ mountHatgPanel });
})();
