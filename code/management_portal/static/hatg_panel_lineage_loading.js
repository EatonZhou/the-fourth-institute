(() => {
  "use strict";

  if (window.PortalHatgPanelLineageLoading) return;

  // Which Lineage the panel shows: reading the Lineage list and the
  // catalogs its dialogs offer (Residents, Projects, Agent backends),
  // opening a Lineage with the Resident or program object asked for,
  // showing no Lineage, and resuming the one already shown.

  const DEPENDENCIES = Object.freeze([
    "$id", "activateLineageEdits", "bindLineageProjectionSubscription", "bindResidentStateCollection",
    "clearGraphNodeSearch", "clearNodeSelection", "displayProgramComponentById",
    "displayProgramWireById", "functionBlockInstanceById", "lineageEditQueue", "loadHatgViewPreference",
    "nodeResidentId", "panelHost", "programFeature", "publishLocation", "renderControls",
    "renderLineageProjectOptions", "renderLineageSurface", "renderNodeProviderFields", "selectNode",
    "selectProgramObject", "setSyncStatus", "setWorkspaceViewLoading", "societyService", "state",
    "toast", "updateLineageSummary",
  ]);

  function createLineageLoading(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgPanelLineageLoading is missing: ${missing.join(", ")}`);
    const {
      $id, activateLineageEdits, bindLineageProjectionSubscription, bindResidentStateCollection,
      clearGraphNodeSearch, clearNodeSelection, displayProgramComponentById, displayProgramWireById,
      functionBlockInstanceById, lineageEditQueue, loadHatgViewPreference, nodeResidentId, panelHost,
      programFeature, publishLocation, renderControls, renderLineageProjectOptions,
      renderLineageSurface, renderNodeProviderFields, selectNode, selectProgramObject, setSyncStatus,
      setWorkspaceViewLoading, societyService, state, toast, updateLineageSummary,
    } = dependencies;

    async function loadLineages() {
      const payload = await societyService.listLineages();
      state.lineages = Array.isArray(payload?.lineages) ? payload.lineages : [];
    }

    function loadLineageAuxiliaryCatalogs(options = {}) {
      const catalogOptions = { force: options.forceCatalogs === true };
      return Promise.all([
        window.PortalLineageCatalogs.residents(catalogOptions).catch(() => ({ residents: [] })),
        window.PortalLineageCatalogs.projectCatalog(catalogOptions).catch(() => ({ projects: [] })),
        window.PortalLineageCatalogs.agentBackends(catalogOptions).catch(() => null),
      ]).then(([residents, projects, backendCatalog]) => {
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
              panelHost.hatgComponentOptions.emit?.("binding-unavailable");
            }
            throw error;
          }),
          loadHatgViewPreference(lineageId),
        ]);
        if (sequence !== state.detailSeq) return;
        state.lineage = lineage;
        panelHost.hatgComponentOptions.emit?.("binding-ready");
        // The previous Lineage's selection names no node here. Left in place,
        // rendering would take it for a removed node and open the leader's
        // Chat instead of the Resident this load was asked for.
        state.selectedNodeId = "";
        panelHost.templateController.load().catch(error => toast(error.message || String(error), "error"));
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
        if (panelHost.panelActive) {
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
      if (panelHost.panelActive) {
        if (!state.residentStateCollection) bindResidentStateCollection();
        if (!state.lineageProjectionSubscription) bindLineageProjectionSubscription();
      }
    }

    return {
      loadLineageAuxiliaryCatalogs, loadLineagePageData, loadLineages, renderNoLineage,
      resumeDisplayedLineage, selectLineage,
    };
  }

  window.PortalHatgPanelLineageLoading = Object.freeze({ createLineageLoading, dependencies: DEPENDENCIES });
})();
