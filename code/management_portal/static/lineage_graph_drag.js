(() => {
  "use strict";

  if (window.PortalHatgGraphDrag) return;

  // Dragging node cards: the D3 drag gesture moves one card or the selected
  // group, keeps its edges, ports and minimap dots with it, pans the view near
  // the canvas edge and offers the card under the pointer as an absorption
  // target.

  const DRAG_AUTOPAN_MARGIN = 96;
  const DRAG_AUTOPAN_MAX_SPEED = 14;

  const DEPENDENCIES = Object.freeze([
    "applyTransformImmediately", "cancelPendingNodeClick", "clamp", "computeEdgeGeometry", "d3", "edgeGeometry",
    "edgeLabelElement", "edgePathElement", "forgetEdgeGeometry", "incidentEdgeIds", "layers", "nodeElement",
    "nodePosition", "number", "options", "persistTransform", "portElements", "portPosition", "positionedElements",
    "rememberEdgeGeometry", "renderFunctionBlockBoundaries", "resolveEdgeLabelCollisions", "root", "screenToWorld",
    "state", "updateDraggedMinimapGeometry", "validEdges",
  ]);

  function createDrag(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphDrag is missing: ${missing.join(", ")}`);
    const {
      applyTransformImmediately, cancelPendingNodeClick, clamp, computeEdgeGeometry, d3, edgeGeometry,
      edgeLabelElement, edgePathElement, forgetEdgeGeometry, incidentEdgeIds, layers, nodeElement, nodePosition,
      number, options, persistTransform, portElements, portPosition, positionedElements, rememberEdgeGeometry,
      renderFunctionBlockBoundaries, resolveEdgeLabelCollisions, root, screenToWorld, state,
      updateDraggedMinimapGeometry, validEdges,
    } = dependencies;

    let dragState = null;

    function updateDraggedGeometry(positionById, options = {}) {
      renderFunctionBlockBoundaries();
      const movedNodeIds = new Set(positionById.keys());
      const affectedEdgeIds = new Set();
      movedNodeIds.forEach(nodeId => {
        incidentEdgeIds(nodeId)?.forEach(edgeId => {
          affectedEdgeIds.add(edgeId);
          forgetEdgeGeometry(edgeId);
        });
      });
      [...affectedEdgeIds].sort((a, b) => Number(state.edgeById.get(a)?.type === "watch") - Number(state.edgeById.get(b)?.type === "watch")).forEach(edgeId => {
        const edge = state.edgeById.get(edgeId);
        if (!edge) return;
        const geometry = options.final
          ? edgeGeometry(edge)
          : computeEdgeGeometry(edge, { avoidObstacles: false });
        if (geometry) rememberEdgeGeometry(edgeId, geometry);
        const edgePath = edgePathElement(edgeId);
        if (edgePath) {
          edgePath.setAttribute("data-routing", geometry?.routing || "direct");
          if (geometry?.routeAxis) edgePath.setAttribute("data-route-axis", geometry.routeAxis);
          else edgePath.removeAttribute("data-route-axis");
          edgePath.setAttribute("d", geometry?.d || "");
        }
        const label = edgeLabelElement(edgeId);
        if (label) {
          label.style.left = `${geometry?.labelX ?? 50}%`;
          label.style.top = `${geometry?.labelY ?? 50}%`;
        }
      });
      movedNodeIds.forEach(nodeId => {
        portElements(nodeId).forEach(portElement => {
          const position = portPosition(portElement.__data__);
          portElement.style.left = `${position.x}%`;
          portElement.style.top = `${position.y}%`;
        });
      });
      updateDraggedMinimapGeometry(
        movedNodeIds,
        options.final ? affectedEdgeIds : new Set(),
      );
    }

    function finalizeDraggedGeometry(members) {
      const positionById = new Map(
        members.map(member => [member.node.id, nodePosition(member.node)]),
      );
      if (!positionById.size) return;
      updateDraggedGeometry(positionById, { final: true });
      resolveEdgeLabelCollisions(validEdges());
      layers.labels.selectAll("button.edgeLabelBtn")
        .style("left", edge => `${edgeGeometry(edge)?.labelX ?? 50}%`)
        .style("top", edge => `${edgeGeometry(edge)?.labelY ?? 50}%`);
    }

    function dragMembersFor(node) {
      const selectedIds = new Set(state.model.selectedNodeIds);
      if (!selectedIds.has(node.id) || selectedIds.size < 2) return [node];
      return state.model.nodes.filter(candidate => selectedIds.has(candidate.id));
    }

    function updateDraggedNodePresentation(positionById) {
      positionById.forEach((position, nodeId) => {
        positionedElements(nodeId).forEach(target => {
          target.style.left = `${position.x}%`;
          target.style.top = `${position.y}%`;
        });
      });
    }

    function updateDraggedNode(event) {
      if (!dragState) return;
      const pointer = screenToWorld(dragState.lastClientX, dragState.lastClientY, dragState.rect);
      const anchorPosition = {
        x: Math.round((pointer.x - dragState.grabOffsetX) * 100) / 100,
        y: Math.round((pointer.y - dragState.grabOffsetY) * 100) / 100,
      };
      const deltaX = anchorPosition.x - dragState.startPosition.x;
      const deltaY = anchorPosition.y - dragState.startPosition.y;
      const positionById = new Map();
      dragState.members.forEach(member => {
        const position = {
          x: Math.round((member.startPosition.x + deltaX) * 100) / 100,
          y: Math.round((member.startPosition.y + deltaY) * 100) / 100,
        };
        // A runtime render may replace model node objects while the pointer
        // gesture is still active. Keep both the pointer-down object and the
        // current renderer-owned node in sync so incremental edge geometry
        // always reads the live drag position.
        new Set([member.node, state.nodeMap.get(member.node.id)]).forEach(currentNode => {
          if (!currentNode) return;
          currentNode.x = position.x;
          currentNode.y = position.y;
        });
        positionById.set(member.node.id, position);
        options.onNodeMove?.(
          member.node,
          position,
          event || dragState.lastEvent,
          { group: dragState.members.length > 1 },
        );
      });
      updateDraggedNodePresentation(positionById);
      updateDraggedGeometry(positionById);
    }

    function clearAbsorptionDropTarget() {
      if (dragState?.absorptionTargetId) {
        nodeElement(dragState.absorptionTargetId)
          ?.classList.remove("absorptionDropTarget");
      }
      root.classList.remove("isNodeAbsorptionDrop");
      delete root.dataset.absorptionTargetId;
    }

    function updateAbsorptionDropTarget(event, { force = false } = {}) {
      if (state.model.viewMode === "workspace" || !dragState?.moved || dragState.members.length > 1) return;
      const now = window.performance?.now?.() || Date.now();
      if (!force && now - dragState.lastAbsorptionCheckAt < 80) return;
      dragState.lastAbsorptionCheckAt = now;
      const stack = typeof document.elementsFromPoint === "function"
        ? document.elementsFromPoint(dragState.lastClientX, dragState.lastClientY)
        : [];
      let target = null;
      for (const elementAtPoint of stack) {
        const targetElement = elementAtPoint?.closest?.(".hatgNode[data-node-id]");
        const targetId = String(targetElement?.dataset?.nodeId || "");
        if (!targetId || targetId === dragState.nodeId) continue;
        const candidate = state.nodeMap.get(targetId);
        if (!candidate || options.canNodeAbsorb?.(dragState.node, candidate, event) === false) continue;
        target = candidate;
        break;
      }
      const previousTargetId = dragState.absorptionTargetId;
      dragState.absorptionTargetId = target?.id || "";
      if (previousTargetId !== dragState.absorptionTargetId) {
        nodeElement(previousTargetId)
          ?.classList.remove("absorptionDropTarget");
        nodeElement(dragState.absorptionTargetId)
          ?.classList.add("absorptionDropTarget");
      }
      root.classList.toggle("isNodeAbsorptionDrop", !!dragState.absorptionTargetId);
      if (dragState.absorptionTargetId) root.dataset.absorptionTargetId = dragState.absorptionTargetId;
      else delete root.dataset.absorptionTargetId;
    }

    function runDraggedNodeUpdate() {
      if (!dragState) return;
      dragState.updateFrame = 0;
      if (!dragState.updatePending) return;
      dragState.updatePending = false;
      updateDraggedNode(dragState.lastEvent);
      updateAbsorptionDropTarget(dragState.lastEvent);
    }

    function queueDraggedNodeUpdate() {
      if (dragState?.updatePending && !dragState.updateFrame) {
        dragState.updateFrame = window.requestAnimationFrame(runDraggedNodeUpdate);
      }
    }

    function flushDraggedNodeUpdate() {
      if (!dragState) return;
      if (dragState.updateFrame) window.cancelAnimationFrame(dragState.updateFrame);
      dragState.updateFrame = 0;
      runDraggedNodeUpdate();
    }

    function autoPanVelocity(pointer, start, size) {
      const local = pointer - start;
      if (local < DRAG_AUTOPAN_MARGIN) {
        return DRAG_AUTOPAN_MAX_SPEED * clamp((DRAG_AUTOPAN_MARGIN - local) / DRAG_AUTOPAN_MARGIN, 0, 1);
      }
      if (local > size - DRAG_AUTOPAN_MARGIN) {
        return -DRAG_AUTOPAN_MAX_SPEED * clamp((local - (size - DRAG_AUTOPAN_MARGIN)) / DRAG_AUTOPAN_MARGIN, 0, 1);
      }
      return 0;
    }

    function runDragAutoPan() {
      if (!dragState) return;
      dragState.autoPanFrame = 0;
      if (!dragState.moved) return;
      const panX = autoPanVelocity(dragState.lastClientX, dragState.rect.left, dragState.rect.width);
      const panY = autoPanVelocity(dragState.lastClientY, dragState.rect.top, dragState.rect.height);
      if (!panX && !panY) return;
      const nextTransform = d3.zoomIdentity
        .translate(state.transform.x + panX, state.transform.y + panY)
        .scale(state.transform.k);
      applyTransformImmediately(nextTransform);
      dragState.autoPanned = true;
      dragState.updatePending = true;
      flushDraggedNodeUpdate();
      dragState.autoPanFrame = window.requestAnimationFrame(runDragAutoPan);
    }

    function queueDragAutoPan() {
      if (!dragState?.autoPanFrame) dragState.autoPanFrame = window.requestAnimationFrame(runDragAutoPan);
    }

    function stopDragAutoPan() {
      if (dragState?.autoPanFrame) window.cancelAnimationFrame(dragState.autoPanFrame);
      if (dragState?.updateFrame) window.cancelAnimationFrame(dragState.updateFrame);
      if (dragState) {
        dragState.autoPanFrame = 0;
        dragState.updateFrame = 0;
      }
    }

    const nodeDrag = d3.drag()
      .clickDistance(3)
      .filter(event => {
        if (!state.model.layoutInteractive) return false;
        if (state.model.navigationFocus && state.model.viewMode !== "workspace") return false;
        if (state.model.handoffMode) return false;
        if (event.button !== undefined && event.button !== 0) return false;
        return !!event.target?.closest?.(".hatgNode")
          && !event.target?.closest?.("[data-node-action]");
      })
      .on("start.hatg", function (event, node) {
        const source = event.sourceEvent || event;
        const rect = root.getBoundingClientRect();
        const clientX = number(source.clientX, event.x);
        const clientY = number(source.clientY, event.y);
        const position = nodePosition(node);
        const pointer = screenToWorld(clientX, clientY, rect);
        const members = dragMembersFor(node).map(member => ({
          node: member,
          startPosition: nodePosition(member),
        }));
        const memberIds = new Set(members.map(member => member.node.id));
        dragState = {
          nodeId: node.id,
          node,
          selection: layers.nodes.selectAll(".hatgNode")
            .filter(member => memberIds.has(member?.id)),
          members,
          rect: { left: rect.left, top: rect.top, width: Math.max(1, rect.width), height: Math.max(1, rect.height) },
          startX: clientX,
          startY: clientY,
          lastClientX: clientX,
          lastClientY: clientY,
          grabOffsetX: pointer.x - position.x,
          grabOffsetY: pointer.y - position.y,
          autoPanFrame: 0,
          updateFrame: 0,
          updatePending: false,
          autoPanned: false,
          lastEvent: event,
          moved: false,
          startPosition: position,
          absorptionTargetId: "",
          lastAbsorptionCheckAt: 0,
        };
        root.classList.add("isNodeDragging");
        root.dataset.dragNodeCount = String(members.length);
        dragState.selection.classed("draggingNode", true);
      })
      .on("drag.hatg", function (event, node) {
        if (!dragState || dragState.nodeId !== node.id) return;
        const source = event.sourceEvent || event;
        const clientX = number(source.clientX, dragState.startX);
        const clientY = number(source.clientY, dragState.startY);
        dragState.lastClientX = clientX;
        dragState.lastClientY = clientY;
        dragState.lastEvent = event;
        const dx = clientX - dragState.startX;
        const dy = clientY - dragState.startY;
        if (!dragState.moved && Math.hypot(dx, dy) < 3) return;
        if (!dragState.moved) {
          cancelPendingNodeClick(String(node.id || ""));
          dragState.moved = true;
          dragState.selection.raise();
        }
        dragState.updatePending = true;
        queueDragAutoPan();
        queueDraggedNodeUpdate();
      })
      .on("end.hatg", function (event, node) {
        if (dragState?.moved) flushDraggedNodeUpdate();
        if (dragState?.moved) updateAbsorptionDropTarget(event, { force: true });
        const moved = !!dragState?.moved;
        const autoPanned = !!dragState?.autoPanned;
        const members = dragState?.members || [];
        const draggedSelection = dragState?.selection;
        const absorptionTarget = dragState?.absorptionTargetId
          ? state.nodeMap.get(dragState.absorptionTargetId)
          : null;
        const startPosition = dragState?.startPosition;
        stopDragAutoPan();
        clearAbsorptionDropTarget();
        dragState = null;
        root.classList.remove("isNodeDragging");
        delete root.dataset.dragNodeCount;
        draggedSelection?.classed("draggingNode", false);
        if (autoPanned) persistTransform();
        if (moved && absorptionTarget && startPosition) {
          node.x = startPosition.x;
          node.y = startPosition.y;
          options.onNodeMove?.(node, startPosition, event);
          updateDraggedNodePresentation(new Map([[node.id, startPosition]]));
          finalizeDraggedGeometry([{ node }]);
          options.onNodeAbsorbDrop?.(node, absorptionTarget, event);
          return;
        }
        if (moved) finalizeDraggedGeometry(members);
        if (moved && members.length > 1) {
          options.onNodesMoveEnd?.(
            members.map(member => member.node),
            event,
          );
        } else if (moved) {
          options.onNodeMoveEnd?.(node, event);
        }
      });

    return {
      nodeDrag, stopDragAutoPan,
    };
  }

  window.PortalHatgGraphDrag = Object.freeze({ createDrag, dependencies: DEPENDENCIES });
})();
