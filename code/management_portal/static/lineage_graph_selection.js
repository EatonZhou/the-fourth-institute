(() => {
  "use strict";

  if (window.PortalHatgGraphSelection) return;

  // Selection: showing which nodes and edges are selected, patching a new
  // selection into the drawn graph, and shift-dragging a box over the
  // background to add the nodes inside it.

  const DEPENDENCIES = Object.freeze([
    "clamp", "edgeIsActive", "edgeIsSelected", "edgeSelectionId", "graphGeometry", "hasNodeElement", "layers",
    "minimapLayers", "nodeElement", "nodeHalfExtents", "nodePosition", "options", "root", "screenToWorld", "state",
    "syncPortOwnerVisibility",
  ]);

  function createSelection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphSelection is missing: ${missing.join(", ")}`);
    const {
      clamp, edgeIsActive, edgeIsSelected, edgeSelectionId, graphGeometry, hasNodeElement, layers, minimapLayers,
      nodeElement, nodeHalfExtents, nodePosition, options, root, screenToWorld, state, syncPortOwnerVisibility,
    } = dependencies;

    const selectionMarquee = document.createElement("div");
    selectionMarquee.className = "hatgSelectionMarquee";
    selectionMarquee.dataset.testid = "hatg-selection-marquee";
    selectionMarquee.hidden = true;
    selectionMarquee.setAttribute("aria-hidden", "true");
    root.appendChild(selectionMarquee);

    let marqueeState = null;

    let suppressBackgroundClick = false;

    function syncNodeSelectionPresentation(selectedIds = state.model.selectedNodeIds) {
      const selected = new Set(selectedIds);
      layers.nodes.selectAll(".hatgNode")
        .classed("selected", node => (
          node.id === state.model.selectedNodeId || selected.has(node.id)
        ))
        .attr("aria-pressed", node => (
          node.id === state.model.selectedNodeId || selected.has(node.id)
        ) ? "true" : "false");
      syncNodeOpenMenuSelectionPresentation(selected);
    }

    function syncNodeOpenMenuSelectionPresentation(selectedIds = state.model.selectedNodeIds) {
      const selected = selectedIds instanceof Set ? selectedIds : new Set(selectedIds);
      layers.nodes.selectAll("button.hatgNodeOpenMenu")
        .attr("data-node-owner-selected", item => (
          item.node.id === state.model.selectedNodeId || selected.has(item.node.id)
        ) ? "true" : null);
    }

    function patchSelection(nextSelection = {}) {
      const nextSelectedNodeId = String(nextSelection.selectedNodeId || "");
      const nextSelectedNodeIds = Array.isArray(nextSelection.selectedNodeIds)
        ? nextSelection.selectedNodeIds.map(String)
        : [];
      const nextSelectedEdgeId = String(nextSelection.selectedEdgeId || "");
      if (nextSelectedNodeId && !state.nodeMap.has(nextSelectedNodeId)) return false;
      if (nextSelectedNodeIds.some(nodeId => !state.nodeMap.has(nodeId))) return false;
      if (
        nextSelectedEdgeId
        && !state.model.edges.some(edge => edgeSelectionId(edge) === nextSelectedEdgeId)
      ) return false;

      const previousSelectedNodeId = state.model.selectedNodeId;
      const previousSelectedNodeIds = new Set([
        ...(state.model.selectedNodeIds || []),
        ...(previousSelectedNodeId ? [previousSelectedNodeId] : []),
      ]);
      const previousSelectedEdgeId = state.model.selectedEdgeId;
      const selectedNodeIds = new Set([
        ...nextSelectedNodeIds,
        ...(nextSelectedNodeId ? [nextSelectedNodeId] : []),
      ]);
      const changedNodeIds = new Set(
        [...previousSelectedNodeIds, ...selectedNodeIds].filter(
          nodeId => previousSelectedNodeIds.has(nodeId) !== selectedNodeIds.has(nodeId),
        ),
      );
      if ([...changedNodeIds].some(nodeId => !hasNodeElement(nodeId))) {
        return false;
      }

      state.model.selectedNodeId = nextSelectedNodeId;
      state.model.selectedNodeIds = nextSelectedNodeIds;
      state.model.selectedEdgeId = nextSelectedEdgeId;

      changedNodeIds.forEach(nodeId => {
        const element = nodeElement(nodeId);
        const selected = selectedNodeIds.has(nodeId);
        element.classList.toggle("selected", selected);
        element.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      syncNodeOpenMenuSelectionPresentation();

      const previousEndpoint = previousSelectedNodeId;
      const nextEndpoint = nextSelectedNodeId;
      const affectedEndpoints = new Set([previousEndpoint, nextEndpoint].filter(Boolean));
      const affectedEdgeIds = new Set([
        previousSelectedEdgeId,
        nextSelectedEdgeId,
      ].filter(Boolean));
      layers.edges.selectAll("path.hatgEdge")
        .filter(edge => (
          affectedEdgeIds.has(edgeSelectionId(edge))
          || affectedEndpoints.has(edge.source)
          || affectedEndpoints.has(edge.target)
        ))
        .classed("active", edge => edgeIsActive(edge))
        .classed("selected", edgeIsSelected);
      layers.labels.selectAll("button.edgeLabelBtn")
        .filter(edge => affectedEdgeIds.has(edgeSelectionId(edge)))
        .classed("selected", edgeIsSelected);

      if (minimapLayers) {
        minimapLayers.nodes.selectAll(".hatgMinimapNode")
          .filter(node => changedNodeIds.has(node.id))
          .classed("selected", node => node.id === state.model.selectedNodeId)
          .attr("r", node => node.id === state.model.selectedNodeId ? 2.35 : 1.55);
      }
      syncPortOwnerVisibility();
      root.dataset.selectionCount = String(state.model.selectedNodeIds.length);
      return true;
    }

    function marqueeBounds(marquee = marqueeState) {
      if (!marquee) return null;
      const left = Math.min(marquee.startX, marquee.currentX);
      const top = Math.min(marquee.startY, marquee.currentY);
      return {
        left,
        top,
        right: Math.max(marquee.startX, marquee.currentX),
        bottom: Math.max(marquee.startY, marquee.currentY),
        width: Math.abs(marquee.currentX - marquee.startX),
        height: Math.abs(marquee.currentY - marquee.startY),
      };
    }

    function marqueeHitIds(bounds) {
      if (!bounds) return [];
      const rootBounds = root.getBoundingClientRect();
      const selectable = new Set(state.model.selectableNodeIds);
      const start = screenToWorld(
        rootBounds.left + bounds.left,
        rootBounds.top + bounds.top,
        rootBounds,
      );
      const end = screenToWorld(
        rootBounds.left + bounds.right,
        rootBounds.top + bounds.bottom,
        rootBounds,
      );
      const selectionBounds = {
        left: Math.min(start.x, end.x),
        top: Math.min(start.y, end.y),
        right: Math.max(start.x, end.x),
        bottom: Math.max(start.y, end.y),
      };
      const hits = [];
      state.model.nodes.forEach(node => {
        if (!selectable.has(node?.id)) return;
        const position = nodePosition(node);
        const half = nodeHalfExtents(node);
        if (graphGeometry.boxIntersectsRect(position, half, selectionBounds)) hits.push(node.id);
      });
      return hits;
    }

    function updateMarquee(clientX, clientY) {
      if (!marqueeState) return;
      marqueeState.currentX = clamp(clientX - marqueeState.rect.left, 0, marqueeState.rect.width);
      marqueeState.currentY = clamp(clientY - marqueeState.rect.top, 0, marqueeState.rect.height);
      const bounds = marqueeBounds();
      marqueeState.moved = bounds.width >= 3 || bounds.height >= 3;
      selectionMarquee.style.left = `${bounds.left}px`;
      selectionMarquee.style.top = `${bounds.top}px`;
      selectionMarquee.style.width = `${bounds.width}px`;
      selectionMarquee.style.height = `${bounds.height}px`;
      marqueeState.hitIds = marqueeState.moved ? marqueeHitIds(bounds) : [];
      selectionMarquee.dataset.count = String(marqueeState.hitIds.length);
      syncNodeSelectionPresentation([
        ...new Set([
          ...marqueeState.baseSelectionIds,
          ...marqueeState.hitIds,
        ]),
      ]);
    }

    function finishMarquee(event, { cancelled = false } = {}) {
      if (!marqueeState || (
        event?.pointerId !== undefined
        && event.pointerId !== marqueeState.pointerId
      )) return;
      const completed = marqueeState;
      marqueeState = null;
      selectionMarquee.hidden = true;
      selectionMarquee.style.cssText = "";
      delete selectionMarquee.dataset.count;
      root.classList.remove("isBoxSelecting");
      delete root.dataset.selectionBoxCount;
      if (cancelled || !completed.moved) {
        syncNodeSelectionPresentation();
        return;
      }
      suppressBackgroundClick = true;
      window.setTimeout(() => { suppressBackgroundClick = false; }, 0);
      const selectedNodes = completed.hitIds
        .map(nodeId => state.nodeMap.get(nodeId))
        .filter(Boolean);
      options.onBoxSelect?.(selectedNodes, event, { additive: true });
    }

    function onSelectionPointerDown(event) {
      if (
        !state.model.selectionEnabled
        || !state.model.layoutInteractive
        || state.model.readOnly
        || !event.shiftKey
        || event.button !== 0
        || event.target?.closest?.(".hatgNode, .hatgPort, .edgeLabelBtn, .hatgEdge, button, input, textarea, select, a")
      ) return;
      const rect = root.getBoundingClientRect();
      marqueeState = {
        pointerId: event.pointerId,
        rect: {
          left: rect.left,
          top: rect.top,
          width: Math.max(1, rect.width),
          height: Math.max(1, rect.height),
        },
        startX: clamp(event.clientX - rect.left, 0, rect.width),
        startY: clamp(event.clientY - rect.top, 0, rect.height),
        currentX: clamp(event.clientX - rect.left, 0, rect.width),
        currentY: clamp(event.clientY - rect.top, 0, rect.height),
        baseSelectionIds: [...state.model.selectedNodeIds],
        hitIds: [],
        moved: false,
      };
      event.preventDefault();
      event.stopPropagation();
      root.focus({ preventScroll: true });
      root.classList.add("isBoxSelecting");
      selectionMarquee.hidden = false;
      updateMarquee(event.clientX, event.clientY);
    }

    function onSelectionPointerMove(event) {
      if (!marqueeState || event.pointerId !== marqueeState.pointerId) return;
      event.preventDefault();
      updateMarquee(event.clientX, event.clientY);
      root.dataset.selectionBoxCount = String(marqueeState.hitIds.length);
    }

    function onSelectionPointerUp(event) {
      if (!marqueeState || event.pointerId !== marqueeState.pointerId) return;
      event.preventDefault();
      finishMarquee(event);
    }

    function onSelectionPointerCancel(event) {
      finishMarquee(event, { cancelled: true });
    }

    root.addEventListener("pointerdown", onSelectionPointerDown, true);
    window.addEventListener("pointermove", onSelectionPointerMove, true);
    window.addEventListener("pointerup", onSelectionPointerUp, true);
    window.addEventListener("pointercancel", onSelectionPointerCancel, true);

    // The click that ends a box selection does not also clear the selection.
    const backgroundClickSuppressed = () => suppressBackgroundClick;

    function destroySelection() {
      finishMarquee(null, { cancelled: true });
      root.removeEventListener("pointerdown", onSelectionPointerDown, true);
      window.removeEventListener("pointermove", onSelectionPointerMove, true);
      window.removeEventListener("pointerup", onSelectionPointerUp, true);
      window.removeEventListener("pointercancel", onSelectionPointerCancel, true);
      selectionMarquee.remove();
    }

    return {
      backgroundClickSuppressed, destroySelection, patchSelection,
    };
  }

  window.PortalHatgGraphSelection = Object.freeze({ createSelection, dependencies: DEPENDENCIES });
})();
