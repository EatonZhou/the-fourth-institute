(() => {
  "use strict";

  if (window.PortalHatgCanvasSelection) return;

  // What is selected on the HATG canvas: the selected Resident, Seed or program
  // object and the Chat that follows it, the multi-selection with its anchor and
  // batch delete, wrapping the selection in a Function Block or a Family, and
  // clearing a selection, focus or wire draft that no longer matches the Lineage.
  const DEPENDENCIES = Object.freeze([
    "$id", "applyProgramMutation", "currentHatgComponentOptions", "currentProgram",
    "currentTemplateController", "displayGraphItemById", "displayNodeById",
    "displayProgramComponentById", "displayProgramWireById", "displaySeedById",
    "executeHatgInstruction", "familyForFacade", "familyRoles", "foldedFamilyObjects",
    "functionBlockInstanceById", "graphSelectedId", "graphVisibleModel",
    "isFunctionBlockInstance", "isPacketOperatorComponent", "isProgramComponent", "loadResults",
    "nodeById", "nodeResidentId", "openNodeSettingsModal", "programComponentById",
    "programFeature", "publishLocation", "renderControls", "renderGraph", "renderGraphSelection",
    "renderLineageHeader", "renderLineageSurface", "renderProgramInspector",
    "renderSeedInspector", "revealSelectedGraphNode", "setBusy", "showFamilyAlone",
    "societyService", "state", "toast",
  ]);

  function createCanvasSelection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgCanvasSelection is missing: ${missing.join(", ")}`);
    const {
      $id, applyProgramMutation, currentHatgComponentOptions, currentProgram,
      currentTemplateController, displayGraphItemById, displayNodeById,
      displayProgramComponentById, displayProgramWireById, displaySeedById,
      executeHatgInstruction, familyForFacade, familyRoles, foldedFamilyObjects,
      functionBlockInstanceById, graphSelectedId, graphVisibleModel, isFunctionBlockInstance,
      isPacketOperatorComponent, isProgramComponent, loadResults, nodeById, nodeResidentId,
      openNodeSettingsModal, programComponentById, programFeature, publishLocation,
      renderControls, renderGraph, renderGraphSelection, renderLineageHeader,
      renderLineageSurface, renderProgramInspector, renderSeedInspector, revealSelectedGraphNode,
      setBusy, showFamilyAlone, societyService, state, toast,
    } = dependencies;

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

    function notifyResidentSelection(node, options = {}) {
      if (options.notifyParent === false || !node || node.accessible === false) return undefined;
      const lineageId = options.lineageId || node.home_lineage_id || state.lineage?.lineage_id || "";
      const residentId = nodeResidentId(node);
      return currentHatgComponentOptions().emit?.("open-resource", {
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
      return currentHatgComponentOptions().onResidentOpenMenu?.({
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
      await currentTemplateController().applyFamily({ kind: "selection", resident_ids: residentIds, element_ids: residentIds });
    }

    return Object.freeze({
      reconcileGraphInteractionState, canvasNodeCanBeSelected, selectedCanvasObjects,
      canvasObjectCanDelete, clearCanvasSelection, reconcileCanvasSelection,
      addCanvasObjectsToSelection, toggleCanvasObjectSelection, selectProgramObject,
      clearNodeSelection, selectNode, notifyResidentSelection, residentEditorOpenMenu,
      notifyResidentOpenMenu, selectProgramComponent, batchDeleteCanvasSelection,
      openFunctionalize, selectedFamilyCandidateIds, openFamilize,
    });
  }

  window.PortalHatgCanvasSelection = Object.freeze({
    createCanvasSelection, dependencies: DEPENDENCIES,
  });
})();
