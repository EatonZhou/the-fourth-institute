(function () {
  "use strict";

  // createGraph draws a Lineage graph with D3. Its parts live in module
  // scripts that load before this one; graphPart(name) returns what a script
  // registers:
  //   lineage_graph_layout.js  automatic layouts and the default world size
  //   lineage_graph_viewport.js  the viewport's size and insets, the world it
  //     shows and screen to world conversion
  //   lineage_graph_nodes.js  a node's kind, status, content, position and
  //     measured size
  //   lineage_graph_element_index.js  the rendered elements of each node and
  //     edge, indexed after every render
  //   lineage_graph_ports.js  program ports, their positions and the wire draft
  //   lineage_graph_hover.js  showing a node's ports and chrome while it is
  //     hovered, focused or selected
  //   lineage_graph_edge_routes.js  edge geometry: routes, detours, parallel
  //     lanes and label positions
  //   lineage_graph_edges.js  drawing edges and their labels
  //   lineage_graph_minimap.js  the minimap and navigating from it
  //   lineage_graph_function_blocks.js  Function Block boundaries and object
  //     separation
  //   lineage_graph_camera.js  zoom, semantic zoom, fitting, centering,
  //     focusing, saved views and following the viewport's size
  //   lineage_graph_selection.js  selection presentation and box selection
  //   lineage_graph_wiring.js  dragging a wire out of an output port
  //   lineage_graph_quick_fork.js  a Resident's quick Fork button, menu and
  //     candidate-fork drag
  //   lineage_graph_drag.js  dragging node cards
  //   lineage_graph_cards.js  node cards and the chrome around them
  //   lineage_graph_canvas_events.js  the canvas's click, keyboard, hover and
  //     palette drop events
  //   lineage_graph_render.js  structural renders, runtime patches and
  //     geometry refreshes
  // This script resolves the graph's elements and layers, holds the render
  // state the parts share, creates the parts in that order and owns teardown;
  // a part that calls a function of a part created after it gets an arrow
  // forwarding the call.
  function graphPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

  const graphGeometry = window.PortalGraphGeometry;
  if (!graphGeometry) throw new Error("Portal graph geometry must load before LineageGraph");
  const layout = graphPart("PortalHatgGraphLayout");

  const DEFAULT_SCALE_EXTENT = [0.35, 3.2];

  // An element the options leave out is null: the parts take optional
  // elements such as the minimap as dependencies, which must not be undefined.
  function element(value) {
    return typeof value === "string" ? document.querySelector(value) : value ?? null;
  }

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function createGraph(options = {}) {
    const d3 = window.d3;
    if (!d3?.zoom || !d3?.drag || !d3?.select) throw new Error("HATG requires D3 zoom, drag and selection");

    const root = element(options.root);
    const world = element(options.world);
    const edgeLayer = element(options.edgeLayer);
    const effectLayer = element(options.effectLayer);
    const labelLayer = element(options.labelLayer);
    const nodeLayer = element(options.nodeLayer);
    const minimap = element(options.minimap);
    const zoomValue = element(options.zoomValue);
    const semanticZoomRoot = element(options.semanticZoomRoot);
    const semanticZoomTitle = element(options.semanticZoomTitle);
    const semanticZoomHint = element(options.semanticZoomHint);
    if (!root || !world || !edgeLayer || !effectLayer || !labelLayer || !nodeLayer) {
      throw new Error("HATG graph layers are incomplete");
    }

    const scaleExtent = Array.isArray(options.scaleExtent) && options.scaleExtent.length === 2
      ? [number(options.scaleExtent[0], DEFAULT_SCALE_EXTENT[0]), number(options.scaleExtent[1], DEFAULT_SCALE_EXTENT[1])]
      : DEFAULT_SCALE_EXTENT;
    const nodeDoubleClickDelay = Math.max(0, number(options.nodeDoubleClickDelay, 0));
    const worldSize = {
      width: Math.max(320, number(options.worldWidth, layout.defaultWorldSize.width)),
      height: Math.max(240, number(options.worldHeight, layout.defaultWorldSize.height)),
    };
    root.style.setProperty("--hatg-world-width", `${worldSize.width}px`);
    root.style.setProperty("--hatg-world-height", `${worldSize.height}px`);
    root.dataset.worldWidth = String(worldSize.width);
    root.dataset.worldHeight = String(worldSize.height);
    world.dataset.coordinateSpace = "fixed";
    const rootSelection = d3.select(root);
    const systemMotionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)") || null;
    const prefersReducedMotion = () => {
      const mode = window.PortalMotion?.mode?.()
        || document.documentElement.dataset.portalMotion
        || "system";
      return mode === "reduced" || (mode !== "full" && systemMotionQuery?.matches === true);
    };
    let reducedMotion = prefersReducedMotion();
    const animationDuration = value => reducedMotion ? 0 : number(value, 0);
    const relationLayer = d3.select(edgeLayer);
    const layers = {
      edges: relationLayer,
      effects: d3.select(effectLayer),
      labels: d3.select(labelLayer),
      nodes: d3.select(nodeLayer),
    };
    const minimapSelection = minimap ? d3.select(minimap) : null;
    if (minimapSelection) minimapSelection.selectAll("*").remove();
    const minimapLayers = minimapSelection ? {
      edges: minimapSelection.append("g").attr("class", "hatgMinimapEdges"),
      nodes: minimapSelection.append("g").attr("class", "hatgMinimapNodes"),
      viewport: minimapSelection.append("rect").attr("class", "hatgMinimapViewport"),
    } : null;

    // The render state every part reads: the drawn model with its nodes and
    // edges by id, the camera's transform, the viewport's last measured size,
    // the semantic zoom level and the context whose view the camera saves.
    // Each part keeps the rest of its state to itself.
    const state = {
      model: {
        nodes: [],
        edges: [],
        selectedNodeId: "",
        selectedNodeIds: [],
        selectableNodeIds: [],
        selectionEnabled: false,
        selectedEdgeId: "",
        handoffSourceId: "",
        handoffAgentId: "",
        handoffMode: false,
        viewMode: "context",
        navigationFocus: false,
        readOnly: false,
        layoutInteractive: true,
        overviewNodes: [],
        overviewEdges: [],
        wireDraft: null,
      },
      nodeMap: new Map(),
      edgeById: new Map(),
      transform: d3.zoomIdentity,
      viewportSize: {
        width: Math.max(1, root.clientWidth),
        height: Math.max(1, root.clientHeight),
      },
      semanticZoomLevel: "",
      contextId: "",
    };

    function syncMotionPreference() {
      reducedMotion = prefersReducedMotion();
    }

    systemMotionQuery?.addEventListener?.("change", syncMotionPreference);
    window.addEventListener("portal:user-settings-changed", syncMotionPreference);

    const {
      currentViewportInsets, currentViewportSize, screenToWorld, transformForCenter, viewCenter, visibleWorldBounds,
    } = graphPart("PortalHatgGraphViewport").createViewport({
      d3, graphGeometry, minimap, number, options, root, scaleExtent, state, worldSize,
    });

    const {
      getNodeSizes, getPlacementGeometry, isActiveStatus, isWorkspaceNode, nodeContent, nodeContextHeat,
      nodeHalfExtents, nodeHasQuickFork, nodePosition, nodeStatus, primitiveNodeKind, refreshNodeExtentCache,
    } = graphPart("PortalHatgGraphNodes").createNodes({
      invalidateEdgeGeometry: (...args) => invalidateEdgeGeometry(...args), layers, nodeLayer, number, options,
      state, worldSize,
    });

    const {
      edgeLabelElement, edgePathElement, hasNodeElement, incidentEdgeIds, minimapEdgeElement, minimapNodeElement,
      nodeElement, ownerChromeElements, portElements, positionedElements, refreshInteractionIndexes,
    } = graphPart("PortalHatgGraphElementIndex").createElementIndex({ layers, minimapLayers, state });

    const {
      connectToItem, normalizeWireDraft, portCompatibility, portIdentity, portPosition, portsForNode, renderPorts,
      resetPortCache, updatePortGeometry,
    } = graphPart("PortalHatgGraphPorts").createPorts({
      clamp, isWorkspaceNode, layers, nodeHalfExtents, nodeHasQuickFork, nodePosition, options, portElements,
      primitiveNodeKind, root, state, worldSize,
    });

    const {
      concealFocusedPorts, concealHoveredPorts, destroyHover, revealFocusedPorts, revealHoveredPorts,
      syncPortOwnerVisibility,
    } = graphPart("PortalHatgGraphHover").createHover({ ownerChromeElements, portElements, root, state });

    const {
      computeEdgeGeometry, edgeGeometry, endpointById, forgetEdgeGeometry, invalidateEdgeGeometry, isProgramWire,
      parallelEdgeLane, refreshParallelEdgeLanes, rememberEdgeGeometry, resolveEdgeLabelCollisions, validEdges,
    } = graphPart("PortalHatgGraphEdgeRoutes").createEdgeRoutes({
      clamp, edgeLabelElement, graphGeometry, nodeHalfExtents, nodePosition, number, portPosition, portsForNode,
      state, worldSize,
    });

    const {
      edgeIsActive, edgeIsSelected, edgeSelectionId, renderEdges, renderLabels,
    } = graphPart("PortalHatgGraphEdges").createEdges({
      connectToItem, edgeGeometry, endpointById, isActiveStatus, isProgramWire, layers, nodeStatus, options,
      parallelEdgeLane, state,
    });

    const {
      destroyMinimap, renderMinimap, updateDraggedMinimapGeometry, updateMinimapGeometry, updateMinimapViewport,
    } = graphPart("PortalHatgGraphMinimap").createMinimap({
      centerAt: (...args) => centerAt(...args), clamp, currentViewportInsets, d3, isProgramWire, minimap,
      minimapEdgeElement, minimapLayers, minimapNodeElement, minimapSelection, nodeContextHeat, nodePosition,
      number, options, primitiveNodeKind, state, visibleWorldBounds,
    });

    const {
      functionBlockBoundaryBounds, getObjectSeparation, renderFunctionBlockBoundaries,
    } = graphPart("PortalHatgGraphFunctionBlocks").createFunctionBlocks({
      d3, layers, nodeHalfExtents, nodePosition, options, portPosition, portsForNode, refreshNodeExtentCache,
      state, worldSize,
    });

    const {
      applyTransformImmediately, center, centerAt, destroyCamera, ensureNodeVisible, fit, fitUnsavedContext,
      focusNode, getNavigationSequence, noteNavigation, persistTransform, reset, setContext, setTransform,
      startCamera, zoomBy,
    } = graphPart("PortalHatgGraphCamera").createCamera({
      animationDuration, clamp, closeQuickForkMenu: (...args) => closeQuickForkMenu(...args),
      currentViewportInsets, currentViewportSize, d3, element, functionBlockBoundaryBounds, invalidateEdgeGeometry,
      layers, nodeHalfExtents, nodePosition, number, options,
      repositionQuickForkMenu: (...args) => repositionQuickForkMenu(...args), root, rootSelection, scaleExtent,
      semanticZoomHint, semanticZoomRoot, semanticZoomTitle, state, transformForCenter,
      updateGeometry: (...args) => updateGeometry(...args), updateMinimapViewport, viewCenter, world, worldSize,
      zoomValue,
    });

    const {
      backgroundClickSuppressed, destroySelection, patchSelection,
    } = graphPart("PortalHatgGraphSelection").createSelection({
      clamp, edgeIsActive, edgeIsSelected, edgeSelectionId, graphGeometry, hasNodeElement, layers, minimapLayers,
      nodeElement, nodeHalfExtents, nodePosition, options, root, screenToWorld, state, syncPortOwnerVisibility,
    });

    const { cancelWireGesture, destroyWiring } = graphPart("PortalHatgGraphWiring").createWiring({
      connectToItem, options, portCompatibility, portIdentity, root, state,
    });

    const {
      candidateForkTargetId, closeQuickForkMenu, destroyQuickFork, renderQuickForkButtons, repositionQuickForkMenu,
      syncQuickForkMenu,
    } = graphPart("PortalHatgGraphQuickFork").createQuickFork({
      animationDuration, layers, nodeHasQuickFork, nodePosition, options, root, state,
    });

    const { nodeDrag, stopDragAutoPan } = graphPart("PortalHatgGraphDrag").createDrag({
      applyTransformImmediately, cancelPendingNodeClick: (...args) => cancelPendingNodeClick(...args), clamp,
      computeEdgeGeometry, d3, edgeGeometry, edgeLabelElement, edgePathElement, forgetEdgeGeometry,
      incidentEdgeIds, layers, nodeElement, nodePosition, number, options, persistTransform, portElements,
      portPosition, positionedElements, rememberEdgeGeometry, renderFunctionBlockBoundaries,
      resolveEdgeLabelCollisions, root, screenToWorld, state, updateDraggedMinimapGeometry, validEdges,
    });

    const {
      renderNodeHoverActions, renderNodeOpenMenus, renderNodes, resolveNodeOpenMenu, syncNodeHoverActionOffsets,
    } = graphPart("PortalHatgGraphCards").createCards({
      animationDuration, candidateForkTargetId, concealFocusedPorts, concealHoveredPorts, isActiveStatus,
      isWorkspaceNode, layers, nodeContent, nodeContextHeat, nodeDrag, nodeHalfExtents, nodePosition, nodeStatus,
      options, primitiveNodeKind, renderQuickForkButtons, revealFocusedPorts, revealHoveredPorts, state,
      worldSize,
    });

    const {
      cancelPendingNodeClick, destroyCanvasEvents,
    } = graphPart("PortalHatgGraphCanvasEvents").createCanvasEvents({
      backgroundClickSuppressed, cancelWireGesture, concealFocusedPorts, concealHoveredPorts, connectToItem, fit,
      isWorkspaceNode, nodeDoubleClickDelay, nodeElement, nodeLayer, noteNavigation, options, portCompatibility,
      portElements, reset, resolveNodeOpenMenu, revealFocusedPorts, revealHoveredPorts, root, screenToWorld, state,
      zoomBy,
    });

    const { patchRuntimePresentation, render, updateGeometry } = graphPart("PortalHatgGraphRender").createRender({
      edgeGeometry, fitUnsavedContext, hasNodeElement, invalidateEdgeGeometry, isActiveStatus, isWorkspaceNode,
      layers, minimapNodeElement, nodeContent, nodeContextHeat, nodeElement, nodePosition, nodeStatus,
      normalizeWireDraft, portIdentity, portsForNode, primitiveNodeKind, refreshInteractionIndexes,
      refreshNodeExtentCache, refreshParallelEdgeLanes, renderEdges, renderFunctionBlockBoundaries, renderLabels,
      renderMinimap, renderNodeHoverActions, renderNodeOpenMenus, renderNodes, renderPorts, resetPortCache,
      resolveEdgeLabelCollisions, root, state, syncNodeHoverActionOffsets, syncPortOwnerVisibility,
      syncQuickForkMenu, updateMinimapGeometry, updatePortGeometry, validEdges,
    });

    startCamera();

    return {
      engine: `d3-${d3.version || "7"}`,
      screenToPosition: screenToWorld,
      render,
      patchRuntimePresentation,
      patchSelection,
      setContext,
      reset,
      center,
      fit,
      focusNode,
      ensureNodeVisible,
      getNavigationSequence,
      refresh: updateGeometry,
      getTransform: () => ({ x: state.transform.x, y: state.transform.y, k: state.transform.k }),
      setTransform,
      getVisibleWorldBounds: () => {
        const viewport = currentViewportSize();
        return visibleWorldBounds(state.transform, viewport, currentViewportInsets(viewport));
      },
      getPlacementGeometry,
      getNodeSizes,
      getObjectSeparation,
      destroy() {
        destroyWiring();
        destroyQuickFork();
        destroyCamera();
        systemMotionQuery?.removeEventListener?.("change", syncMotionPreference);
        window.removeEventListener("portal:user-settings-changed", syncMotionPreference);
        destroyHover();
        destroyCanvasEvents();
        stopDragAutoPan();
        destroySelection();
        destroyMinimap();
      },
    };
  }

  window.PortalHatgGraph = Object.freeze({
    schema: "portal.hatg.graph.v1",
    computeBranchAwareLayout: layout.computeBranchAwareLayout,
    computeWorkspaceGraphLayout: layout.computeWorkspaceGraphLayout,
    createGraph,
  });
})();
