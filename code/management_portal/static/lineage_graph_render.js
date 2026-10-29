(() => {
  "use strict";

  if (window.PortalHatgGraphRender) return;

  // Rendering: a structural render draws a new model with every part and
  // measures and routes again only when the geometry's inputs changed; a
  // runtime patch updates status and content in place while the structure
  // stays; refreshing the geometry measures and routes the drawn graph again.

  const DEPENDENCIES = Object.freeze([
    "edgeGeometry", "fitUnsavedContext", "hasNodeElement", "invalidateEdgeGeometry", "isActiveStatus",
    "isWorkspaceNode", "layers", "minimapNodeElement", "nodeContent", "nodeContextHeat", "nodeElement",
    "nodePosition", "nodeStatus", "normalizeWireDraft", "portIdentity", "portsForNode", "primitiveNodeKind",
    "refreshInteractionIndexes", "refreshNodeExtentCache", "refreshParallelEdgeLanes", "renderEdges",
    "renderFunctionBlockBoundaries", "renderLabels", "renderMinimap", "renderNodeHoverActions",
    "renderNodeOpenMenus", "renderNodes", "renderPorts", "resetPortCache", "resolveEdgeLabelCollisions", "root",
    "state", "syncNodeHoverActionOffsets", "syncPortOwnerVisibility", "syncQuickForkMenu", "updateMinimapGeometry",
    "updatePortGeometry", "validEdges",
  ]);

  function createRender(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphRender is missing: ${missing.join(", ")}`);
    const {
      edgeGeometry, fitUnsavedContext, hasNodeElement, invalidateEdgeGeometry, isActiveStatus, isWorkspaceNode,
      layers, minimapNodeElement, nodeContent, nodeContextHeat, nodeElement, nodePosition, nodeStatus,
      normalizeWireDraft, portIdentity, portsForNode, primitiveNodeKind, refreshInteractionIndexes,
      refreshNodeExtentCache, refreshParallelEdgeLanes, renderEdges, renderFunctionBlockBoundaries, renderLabels,
      renderMinimap, renderNodeHoverActions, renderNodeOpenMenus, renderNodes, renderPorts, resetPortCache,
      resolveEdgeLabelCollisions, root, state, syncNodeHoverActionOffsets, syncPortOwnerVisibility,
      syncQuickForkMenu, updateMinimapGeometry, updatePortGeometry, validEdges,
    } = dependencies;

    let geometryInputSignature = "";

    function updateGeometry(options = {}) {
      if (options.measureNodes) {
        refreshNodeExtentCache();
        syncNodeHoverActionOffsets();
      }
      renderFunctionBlockBoundaries();
      invalidateEdgeGeometry();
      resolveEdgeLabelCollisions(validEdges());
      layers.edges.selectAll("path.hatgEdge")
        .attr("data-routing", edge => edgeGeometry(edge)?.routing || "direct")
        .attr("data-route-axis", edge => edgeGeometry(edge)?.routeAxis || null)
        .attr("d", edge => edgeGeometry(edge)?.d || "");
      layers.labels.selectAll("button.edgeLabelBtn")
        .style("left", edge => `${edgeGeometry(edge)?.labelX ?? 50}%`)
        .style("top", edge => `${edgeGeometry(edge)?.labelY ?? 50}%`);
      updatePortGeometry();
      updateMinimapGeometry();
    }

    function edgeGeometryInputSignature(edges) {
      return edges.map(edge => [
        edge.id,
        edge.source,
        edge.target,
        edge.type || "",
        edge.source_port || "",
        edge.target_port || "",
      ].join(":")).join("|");
    }

    function currentGeometryInputSignature(edges) {
      const nodeSignature = state.model.nodes.map(node => {
        const position = nodePosition(node);
        const ports = portsForNode(node)
          .map(port => `${portIdentity(port)}:${port.type}:${port.direction}`)
          .join(",");
        return [
          node.id,
          position.x,
          position.y,
          primitiveNodeKind(node),
          "node",
          node.kind || "",
          node.seed_type || "",
          node._family_facade ? `family-${node._family_expanded ? "expanded" : "folded"}` : "",
          ports,
        ].join(":");
      }).join("|");
      const edgeSignature = edgeGeometryInputSignature(edges);
      return [
        state.model.viewMode,
        state.model.readOnly ? "readonly" : "editable",
        state.semanticZoomLevel || "operate",
        nodeSignature,
        edgeSignature,
      ].join("\u0001");
    }

    function render(nextModel = {}) {
      const readOnly = Boolean(nextModel.readOnly);
      const layoutInteractive = typeof nextModel.layoutInteractive === "boolean"
        ? nextModel.layoutInteractive
        : !readOnly;
      state.model = {
        nodes: Array.isArray(nextModel.nodes) ? nextModel.nodes : [],
        functionBlockBoundaries: Array.isArray(nextModel.functionBlockBoundaries) ? nextModel.functionBlockBoundaries : [],
        edges: Array.isArray(nextModel.edges) ? nextModel.edges : [],
        selectedNodeId: String(nextModel.selectedNodeId || ""),
        selectedNodeIds: Array.isArray(nextModel.selectedNodeIds)
          ? nextModel.selectedNodeIds.map(String)
          : [],
        selectableNodeIds: Array.isArray(nextModel.selectableNodeIds)
          ? nextModel.selectableNodeIds.map(String)
          : [],
        selectionEnabled: Boolean(nextModel.selectionEnabled),
        selectedEdgeId: String(nextModel.selectedEdgeId || ""),
        handoffSourceId: String(nextModel.handoffSourceId || ""),
        handoffAgentId: String(nextModel.handoffAgentId || ""),
        handoffMode: !!nextModel.handoffMode,
        viewMode: ["context", "workspace"].includes(nextModel.viewMode) ? nextModel.viewMode : "context",
        navigationFocus: !!nextModel.navigationFocus,
        readOnly,
        layoutInteractive,
        quickForkNodeId: String(nextModel.quickForkNodeId || ""),
        overviewNodes: Array.isArray(nextModel.overviewNodes) ? nextModel.overviewNodes : [],
        overviewEdges: Array.isArray(nextModel.overviewEdges) ? nextModel.overviewEdges : [],
        wireDraft: nextModel.wireDraft && typeof nextModel.wireDraft === "object"
          ? { ...nextModel.wireDraft }
          : null,
      };
      state.nodeMap = new Map(state.model.nodes.map(node => [node.id, node]));
      state.edgeById = new Map(state.model.edges.map(edge => [edge.id, edge]));
      syncQuickForkMenu();
      resetPortCache();
      state.model.wireDraft = normalizeWireDraft(state.model.wireDraft);
      const edges = validEdges();
      const nextGeometryInputSignature = currentGeometryInputSignature(edges);
      let geometryChanged = nextGeometryInputSignature !== geometryInputSignature;
      const nodeContentChanged = renderNodes(state.model.nodes);
      geometryChanged = geometryChanged || nodeContentChanged;
      if (geometryChanged) {
        // Node controls were placed before this measurement.
        refreshNodeExtentCache();
        syncNodeHoverActionOffsets();
      }
      renderFunctionBlockBoundaries();
      renderNodeHoverActions(state.model.nodes);
      renderNodeOpenMenus(state.model.nodes);
      if (geometryChanged) {
        refreshParallelEdgeLanes(edges);
        invalidateEdgeGeometry();
        resolveEdgeLabelCollisions(edges);
      }
      renderEdges(edges, { geometryChanged });
      renderLabels(edges);
      renderPorts(state.model.nodes, { geometryChanged });
      renderMinimap({ geometryChanged });
      refreshInteractionIndexes(edges);
      syncPortOwnerVisibility({ full: geometryChanged });
      geometryInputSignature = nextGeometryInputSignature;
      root.dataset.nodeCount = String(state.model.nodes.length);
      root.dataset.edgeCount = String(edges.length);
      root.dataset.overviewNodeCount = String(state.model.overviewNodes.length || state.model.nodes.length);
      root.dataset.workspaceCount = String(state.model.nodes.filter(node => node.kind === "workspace").length);
      root.dataset.navigationFocus = state.model.navigationFocus ? "true" : "false";
      root.dataset.viewMode = state.model.viewMode;
      root.dataset.readOnly = state.model.readOnly ? "true" : "false";
      root.dataset.layoutInteractive = state.model.layoutInteractive ? "true" : "false";
      root.dataset.selectionEnabled = state.model.selectionEnabled ? "true" : "false";
      root.dataset.selectionCount = String(state.model.selectedNodeIds.length);
      root.dataset.programWireCount = String(edges.filter(edge => edge?._program_wire).length);
      fitUnsavedContext();
      root.dispatchEvent(new CustomEvent("hatg:render", {
        detail: { nodeCount: state.model.nodes.length, edgeCount: edges.length, contextId: state.contextId },
      }));
    }

    function patchRuntimePresentation(nextModel = {}) {
      const nextNodes = Array.isArray(nextModel.nodes) ? nextModel.nodes : [];
      const nextOverviewNodes = Array.isArray(nextModel.overviewNodes)
        ? nextModel.overviewNodes
        : nextNodes;
      if (!Array.isArray(nextNodes) || nextNodes.length !== state.model.nodes.length) return false;
      const nextById = new Map(nextNodes.map(node => [String(node?.id || ""), node]));
      const nextOverviewById = new Map(
        nextOverviewNodes.map(node => [String(node?.id || ""), node]),
      );
      if (state.model.nodes.some(node => !nextById.has(String(node.id || "")))) return false;
      if (
        nextOverviewNodes.length !== state.model.overviewNodes.length
        || state.model.overviewNodes.some(node => !nextOverviewById.has(String(node.id || "")))
      ) return false;

      const nextEdges = Array.isArray(nextModel.edges) ? nextModel.edges : state.model.edges;
      const nextOverviewEdges = Array.isArray(nextModel.overviewEdges)
        ? nextModel.overviewEdges
        : state.model.overviewEdges;
      // Runtime can introduce/remove relations as well as change their labels.
      // Structural render owns paths, ports, interaction indexes and minimap
      // geometry. Reject that change before mutating the current render model.
      if (
        edgeGeometryInputSignature(nextEdges) !== edgeGeometryInputSignature(state.model.edges)
        || edgeGeometryInputSignature(nextOverviewEdges) !== edgeGeometryInputSignature(state.model.overviewEdges)
      ) return false;
      const edgesChanged = JSON.stringify([nextEdges, nextOverviewEdges])
        !== JSON.stringify([state.model.edges, state.model.overviewEdges]);

      if (nextNodes.some(node => !hasNodeElement(String(node.id)))) return false;

      state.model.nodes = state.model.nodes.map(node => nextById.get(String(node.id)));
      state.model.overviewNodes = state.model.overviewNodes.map(node => (
        nextOverviewById.get(String(node.id || ""))
      ));
      state.model.edges = nextEdges;
      state.edgeById = new Map(state.model.edges.map(edge => [edge.id, edge]));
      state.model.overviewEdges = nextOverviewEdges;
      state.nodeMap = new Map(state.model.nodes.map(node => [node.id, node]));

      const changedNodeIds = new Set();
      for (const node of state.model.nodes) {
        const nodeId = String(node.id || "");
        const target = nodeElement(nodeId);
        if (isWorkspaceNode(node)) {
          target.__data__ = node;
          target.title = [node.title, node.workspace_path, node.workspace_id || node.resident_id || node.id].filter(Boolean).join("\n");
          target.setAttribute("aria-label", [node.title, node.workspace_id || node.resident_id || node.id].filter(Boolean).join(" · "));
        }
        const status = nodeStatus(node);
        const contextHeat = nodeContextHeat(node);
        const content = nodeContent(node);
        if (
          target.__hatgNodeContent === content
          && String(target.dataset.status || "") === status
          && String(target.dataset.contextHeat || "") === contextHeat
        ) continue;
        target.dataset.status = status;
        if (contextHeat) target.dataset.contextHeat = contextHeat;
        else delete target.dataset.contextHeat;
        if (isActiveStatus(status)) target.dataset.runtimeEffect = "activity-rail";
        else delete target.dataset.runtimeEffect;
        if (target.__hatgNodeContent !== content) {
          target.innerHTML = content;
          target.__hatgNodeContent = content;
        }
        const minimapNode = minimapNodeElement(nodeId);
        if (minimapNode) {
          if (contextHeat) minimapNode.setAttribute("data-context-heat", contextHeat);
          else minimapNode.removeAttribute("data-context-heat");
        }
        changedNodeIds.add(nodeId);
      }
      for (const node of state.model.overviewNodes) {
        const nodeId = String(node.id || "");
        const minimapNode = minimapNodeElement(nodeId);
        if (!minimapNode || changedNodeIds.has(nodeId)) continue;
        const contextHeat = nodeContextHeat(node);
        if (String(minimapNode.dataset.contextHeat || "") === contextHeat) continue;
        if (contextHeat) minimapNode.setAttribute("data-context-heat", contextHeat);
        else minimapNode.removeAttribute("data-context-heat");
      }
      if (!changedNodeIds.size && !edgesChanged) return true;

      // Runtime state changes node chrome and whether incident edges flow.
      // Geometry, ports and interaction indexes remain owned by structural render().
      renderEdges(validEdges(), { geometryChanged: false });
      if (edgesChanged) {
        renderLabels(validEdges());
        renderMinimap({ geometryChanged: false });
      }
      return true;
    }

    return {
      patchRuntimePresentation, render, updateGeometry,
    };
  }

  window.PortalHatgGraphRender = Object.freeze({ createRender, dependencies: DEPENDENCIES });
})();
