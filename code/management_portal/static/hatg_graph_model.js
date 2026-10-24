(() => {
  "use strict";

  if (window.PortalHatgGraphModel) return;

  // Lookups over the Lineage the HATG panel shows: its objects by ID in the stored
  // graph and in the authored program, Seeds and their candidate forks, the object
  // the canvas selection points at, relation families, a node's Workspace bindings
  // and its run status. Every other part reads the Lineage through these.
  const DEPENDENCIES = Object.freeze([
    "esc", "state",
  ]);

  const RELATION_FAMILY = Object.freeze({
    inheritance: "context",
    absorption: "context",
    candidate_fork: "context",
    handoff: "workspace",
  });

  function isPacketOperatorComponent(node) {
    return window.PortalLineageElementFields.isOperatorKind(node?.kind);
  }

  function isOperatorExecutorNode(node) {
    return Boolean(node?.operator_executor?.component_id);
  }

  function packetOperatorImplementation(config, kind = "") {
    return window.PortalLineageElementFields.operatorImplementation(config, kind);
  }

  function lineageEdgeReferencesResident(edge, residentId) {
    const normalizedId = String(residentId || "");
    if (!edge || !normalizedId) return false;
    return edge.source === normalizedId || edge.target === normalizedId;
  }

  function relationFamily(edgeOrType) {
    const type = typeof edgeOrType === "string" ? edgeOrType : edgeOrType?.type;
    return RELATION_FAMILY[type] || "context";
  }

  function relationVisibleInView(edge, mode) {
    if (mode === "context") {
      return relationFamily(edge) === "context" || edge?.type === "handoff";
    }
    return relationFamily(edge) === mode;
  }

  function nodeResidentId(node) {
    return String(node?.id || "").trim();
  }

  function nodeIsDerived(node) {
    return Boolean(String(node?.source_resident_id || "").trim());
  }

  function createGraphModel(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphModel is missing: ${missing.join(", ")}`);
    const {
      esc, state,
    } = dependencies;

    const RELATION_LABEL = Object.freeze({
      inheritance: window.PortalI18n.t("Fork 继承"),
      absorption: window.PortalI18n.t("节点吸收"),
      candidate_fork: window.PortalI18n.t("候选 Fork"),
      handoff: window.PortalI18n.t("Workspace Handoff"),
    });

    function currentNode() {
      return state.lineage?.nodes?.find(node => node.id === state.selectedNodeId) || null;
    }

    function displayedProgram() {
      const authored = window.PortalLineageProgram?.authoredProgram?.(
        state.lineage?.program || {},
      ) || { components: [], wires: [] };
      return authored;
    }

    function currentProgram() {
      return window.PortalLineageProgram?.normalizeProgram?.(
        state.lineage?.program || {},
      ) || { components: [], wires: [] };
    }

    function displayProgramComponentById(componentId) {
      return displayedProgram().components.find(component => component.id === componentId) || null;
    }

    function functionBlockInstanceById(instanceId) {
      return (state.lineage?.function_blocks || []).find(
        instance => instance.id === instanceId,
      ) || null;
    }

    function programResourceAddress(namespace, itemId, lineage = state.lineage) {
      const lineageId = String(lineage?.lineage_id || "").trim();
      const localId = String(itemId || "").trim();
      if (
        !lineageId
        || !localId
        || !["component", "wire"].includes(namespace)
      ) return "";
      return `${lineageId}/program/${namespace}/${localId}`;
    }

    function inspectorResourceAddressHtml(address, label = "资源地址") {
      if (!address) return "";
      return window.PortalI18n.html`<div class="programInspectorResource" data-program-resource-address="${esc(address)}">
      <span>${esc(label)}</span>
      <div>
        <code title="${esc(address)}">${esc(address)}</code>
        <button type="button" data-program-copy-resource aria-label="复制资源地址" title="复制资源地址">复制</button>
      </div>
    </div>`;
    }

    function programResourceAddressHtml(namespace, itemId) {
      return inspectorResourceAddressHtml(
        programResourceAddress(namespace, itemId),
        "嵌套资源地址",
      );
    }

    function programComponentById(componentId) {
      return (state.lineage?.program?.components || []).find(component => (
        component?.id === componentId
      )) || null;
    }

    function displayProgramWireById(wireId) {
      return displayedProgram().wires.find(wire => wire.id === wireId) || null;
    }

    function isProgramComponent(node) {
      return Boolean(
        !isFunctionBlockInstance(node)
        && (
          node?._program_component
          || window.PortalLineageProgram?.componentKinds?.includes(
            String(node?.kind || ""),
          )
        )
      );
    }

    function isFunctionBlockInstance(node) {
      return Boolean(
        node?._function_block
        || String(node?.kind || "") === "function_block"
        || (node?.id && functionBlockInstanceById(node.id))
      );
    }

    // A Seed's source is its candidate fork, a relation of the Lineage
    // (decision_4adcc1ff2f2c); a Seed view carries it as source_resident_id.
    function candidateForkOf(seedId, lineage = state.lineage) {
      return (lineage?.edges || []).find(edge => edge?.type === "candidate_fork" && edge.target === seedId) || null;
    }

    function seedView(component, lineage = state.lineage) {
      return {
        ...component,
        ...(component.config || {}),
        source_resident_id: candidateForkOf(component.id, lineage)?.source || "",
      };
    }

    function activeSeeds(lineage = state.lineage) {
      return (lineage?.program?.components || [])
        .filter(component => component?.kind === "seed")
        .map(component => seedView(component, lineage))
        .filter(seed => String(seed.status || "active") === "active");
    }

    function displaySeedById(seedId) {
      return activeSeeds(state.lineage).find(seed => seed.id === seedId) || null;
    }

    function seedById(seedId) {
      const component = programComponentById(seedId);
      return component?.kind === "seed" ? seedView(component) : null;
    }

    function graphSelectedId() {
      const programObjectId = state.selectedProgramObjectId;
      if (programObjectId) {
        const wire = displayProgramWireById(programObjectId);
        if (wire) return wire.source;
        if (
          displayProgramComponentById(programObjectId)
          || functionBlockInstanceById(programObjectId)
          || displayNodeById(programObjectId)
        ) return programObjectId;
      }
      return state.selectedSeedId || state.selectedNodeId;
    }

    function graphSelectedEdgeId() {
      return displayProgramWireById(state.selectedProgramObjectId)?.id
        || displayProgramWireById(state.programReferenceFeedbackWireId)?.id
        || state.selectedEdgeId;
    }

    function displayGraphItemById(itemId) {
      return displayNodeById(itemId)
        || displaySeedById(itemId)
        || functionBlockInstanceById(itemId)
        || displayProgramComponentById(itemId);
    }

    function graphItemById(itemId) {
      // Layout edits must update the stored graph objects. Seed and Function Block
      // display views are copies and cannot own position changes.
      return nodeById(itemId)
        || (state.lineage?.function_blocks || []).find(instance => instance?.id === itemId)
        || programComponentById(itemId);
    }

    function displayNodeById(nodeId) {
      return state.lineage?.nodes?.find(node => node.id === nodeId) || null;
    }

    function displayEdgeById(edgeId) {
      return state.lineage?.edges?.find(edge => edge.id === edgeId) || null;
    }

    function nodeById(nodeId) {
      return state.lineage?.nodes?.find(node => node.id === nodeId) || null;
    }

    function edgeById(edgeId) {
      return state.lineage?.edges?.find(edge => edge.id === edgeId) || null;
    }

    function manualProgramEventKey(componentId, lineageId = state.lineage?.lineage_id) {
      return `${String(lineageId || "")}:${String(componentId || "")}`;
    }

    function programEventSourceRuntime(componentId) {
      return state.lineage?.program_event_source_runtime?.[componentId] || {};
    }

    function programEffectRuntime(componentId) {
      return state.lineage?.program_effect_runtime?.[componentId] || {};
    }

    function programExecutionExplanation(objectId) {
      const projection = state.lineage?.program_execution_explanations || {};
      return projection.nodes?.[objectId] || projection.wires?.[objectId] || null;
    }

    function runningLineageNodes() {
      return (state.lineage?.nodes || []).filter(
        node => effectiveNodeStatus(node) === "running",
      );
    }

    function workspaceBindingForNode(nodeId) {
      return state.workspaceGraphBindings.get(nodeId) || null;
    }

    function boundWorkspacesForNode(nodeId) {
      return window.PortalHatgWorkspaceProjection.workspaces(workspaceBindingForNode(nodeId));
    }

    function preferredWorkspaceForNode(nodeId) {
      const workspaces = boundWorkspacesForNode(nodeId);
      return workspaces.find(item => item.is_default_cwd)
        || workspaces[0]
        || null;
    }

    function fillWorkspaceSelect(select, nodeId, selectedDirectoryId = "") {
      const workspaces = boundWorkspacesForNode(nodeId);
      select.innerHTML = workspaces.length
        ? workspaces.map(workspace => {
          const directoryId = String(workspace.workspace_id || "");
          const suffix = workspace.is_default_cwd ? " · default cwd" : "";
          return window.PortalI18n.html`<option value="${esc(directoryId)}">${esc(workspace.label || "Workspace")}${esc(suffix)} · ${esc(workspace.path)}</option>`;
        }).join("")
        : window.PortalI18n.markup('<option value="">没有可交接的 Workspace</option>');
      const preferred = selectedDirectoryId
        ? workspaces.find(item => item.workspace_id === selectedDirectoryId)
        : preferredWorkspaceForNode(nodeId);
      if (preferred) select.value = String(preferred.workspace_id || "");
      return workspaces;
    }

    function selectedBoundWorkspace(select, nodeId) {
      const directoryId = String(select?.value || "");
      return boundWorkspacesForNode(nodeId).find(item => (
        directoryId === String(item.workspace_id || "")
      )) || null;
    }

    function effectiveNodeStatus(node) {
      return state.runtimeStatus.get(node?.id) || node?.status || "idle";
    }

    function setNodeRuntimeStatus(nodeId, status) {
      if (!nodeId) return;
      state.runtimeStatus.set(nodeId, status);
    }

    return Object.freeze({
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
    });
  }

  window.PortalHatgGraphModel = Object.freeze({
    createGraphModel, dependencies: DEPENDENCIES, isPacketOperatorComponent,
    isOperatorExecutorNode, packetOperatorImplementation, lineageEdgeReferencesResident,
    relationFamily, relationVisibleInView, nodeResidentId, nodeIsDerived,
  });
})();
