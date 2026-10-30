(() => {
  "use strict";

  if (window.PortalHatgPanelBindings) return;

  // The panel's controls: closing its dialogs, the Escape key, every
  // toolbar button, form and select of the template, the component
  // library splitter, and the canvas observers that follow the toolbar
  // height and keep the Seed inspector next to its Seed.

  const DEPENDENCIES = Object.freeze([
    "$eventRoot", "$id", "$queryAll", "activateHandoffMode", "addHandoffItem", "autoLayoutGraph",
    "batchDeleteCanvasSelection", "bindGraphNodeSearch", "bindPrimitiveLibrary",
    "cancelGraphInteractionMode", "cancelHandoffMode", "changeGraphRelationFilter",
    "clearCanvasSelection", "clone", "closeModal", "closeRelationInspector", "commitLineage",
    "configureBranchProvider", "deleteAbsorbedSourceNode", "deleteCurrentLineage", "focusLineageLeader",
    "hatgModule", "keepAbsorbedSourceNode", "loadLineages", "loadSubgraphMoveTargets",
    "openBranchModal", "openEditLineageModal", "openFamilize", "openFunctionalize", "openNodeModal",
    "openSeedDismissModal", "openSeedPromoteModal", "openSubgraphMove", "panelHost",
    "positionSeedInspector", "previewSubgraphMove", "reconcileLineageRuntime", "removeEditingEdge",
    "removeHandoffItem", "renderControls", "renderLineageSurface", "renderNodeProviderFields",
    "renewSubgraphMoveIntent", "selectProgramObject", "setGraphViewMode", "setSeedActionChoice",
    "state", "stopAllRunningNodes", "submitBranch", "submitHandoff", "submitLeaderRequest",
    "submitLineageForm", "submitNodeForm", "submitSeedDismissal", "submitSeedPromotion",
    "submitSubgraphMove", "syncHandoffMaterializerFields", "syncHandoffSubmitLabel", "toast",
    "toggleGraphFocusMode", "updateBranchAccountHint", "updateLineageSummary",
  ]);

  function createBindings(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgPanelBindings is missing: ${missing.join(", ")}`);
    const {
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
    } = dependencies;

    let stopNavigationMeasurement = null;
    let seedInspectorObserver = null;
    function disconnectGraphObservers() {
      stopNavigationMeasurement?.();
      stopNavigationMeasurement = null;
      seedInspectorObserver?.disconnect();
      seedInspectorObserver = null;
    }

    function bindComponentLibraryResize() {
      panelHost.disposeLibraryResize?.();
      const handle = $id("componentLibrarySplitter");
      const rail = handle.parentElement;
      const primitives = $id("hatgPrimitiveLibrary");
      const savedRatio = panelHost.pageStateController.read().hatgLibrarySplitRatio;
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
      const persist = () => panelHost.pageStateController.write({ hatgLibrarySplitRatio: ratio });
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
      panelHost.pageStateController.listen("panel", handle, "dblclick", event => {
        event.preventDefault();
        ratio = 0.5;
        applySplitRatio();
        persist();
      });
      // CSS keeps both groups reachable at narrow widths without overwriting the
      // user's preferred ratio; only the separator's ARIA values need refreshing.
      const observer = new ResizeObserver(syncAria);
      observer.observe(rail);
      panelHost.disposeLibraryResize = () => {
        unbind();
        observer.disconnect();
        handle.classList.remove("active");
      };
    }

    function bindModals() {
      $queryAll("[data-close-modal]").forEach(button => button.addEventListener("click", () => closeModal(button.dataset.closeModal || "")));
      panelHost.pageStateController.listen("panel", $eventRoot(), "keydown", event => {
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
        panelHost.hatgComponentOptions.onCollapseRequest?.();
      });
      // The Lineage Menu; project, navigation and deletion stay in the settings dialog it links to.
      $id("lineageSettingsBtn").addEventListener("click", () => {
        if (!state.lineage) return;
        void window.PortalNav.openScopeMenu({
          kind: "lineage", id: state.lineage.lineage_id, record: state.lineage,
          onOpenAdvanced: openEditLineageModal,
        });
      });
      panelHost.pageStateController.listen("panel", window, "portal:scope-menu-changed", event => {
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
      $id("addTemplateBtn").addEventListener("click", () => panelHost.templateController.newTemplate());
      [["saveSelectionTemplateBtn", false], ["saveSelectionRewriteBtn", true]].forEach(([id, rewrite]) => {
        $id(id).addEventListener("click", async event => {
          const elementIds = [...state.selectedCanvasObjectIds];
          if (!elementIds.length) return;
          event.currentTarget.disabled = true;
          try { await panelHost.templateController.saveSelection(elementIds, { rewrite }); }
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
      stopNavigationMeasurement = hatgModule("PortalHatgToolbar").observeNavigationHeight(
        $id("hatgViewport"), $id("graphNavigationBar"),
      );
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

    return {
      bindActions, bindModals, disconnectGraphObservers,
    };
  }

  window.PortalHatgPanelBindings = Object.freeze({ createBindings, dependencies: DEPENDENCIES });
})();
