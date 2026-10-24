(() => {
  "use strict";

  if (window.PortalHatgToolbar) return;

  // What the HATG header and toolbar show for the shown Lineage: the sync status,
  // the Lineage's entry in the Lineage list, its title and ID, and which actions
  // are available and visible in the current view and selection.
  const DEPENDENCIES = Object.freeze([
    "$id", "activeSeeds", "canvasObjectCanDelete", "currentNode", "displayNodeById",
    "effectiveNodeStatus", "familyForFacade", "renderAutoLayoutControl", "runningLineageNodes",
    "selectedCanvasObjects", "selectedFamilyCandidateIds", "state", "subgraphMoveBusy",
  ]);

  function createToolbar(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgToolbar is missing: ${missing.join(", ")}`);
    const {
      $id, activeSeeds, canvasObjectCanDelete, currentNode, displayNodeById, effectiveNodeStatus,
      familyForFacade, renderAutoLayoutControl, runningLineageNodes, selectedCanvasObjects,
      selectedFamilyCandidateIds, state, subgraphMoveBusy,
    } = dependencies;

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

    return Object.freeze({
      setSyncStatus, updateLineageSummary, renderLineageHeader, renderControls,
    });
  }

  window.PortalHatgToolbar = Object.freeze({
    createToolbar, dependencies: DEPENDENCIES,
  });
})();
