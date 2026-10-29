(() => {
  "use strict";

  if (window.PortalHatgGraphMinimap) return;

  // The minimap: the whole graph in miniature with the visible part outlined;
  // a click or drag on it moves the view there.

  const DEPENDENCIES = Object.freeze([
    "centerAt", "clamp", "currentViewportInsets", "d3", "isProgramWire", "minimap", "minimapEdgeElement",
    "minimapLayers", "minimapNodeElement", "minimapSelection", "nodeContextHeat", "nodePosition", "number",
    "options", "primitiveNodeKind", "state", "visibleWorldBounds",
  ]);

  function createMinimap(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphMinimap is missing: ${missing.join(", ")}`);
    const {
      centerAt, clamp, currentViewportInsets, d3, isProgramWire, minimap, minimapEdgeElement, minimapLayers,
      minimapNodeElement, minimapSelection, nodeContextHeat, nodePosition, number, options, primitiveNodeKind,
      state, visibleWorldBounds,
    } = dependencies;

    let minimapRenderState = null;

    let minimapDomain = { minX: 0, maxX: 100, minY: 0, maxY: 100 };

    function worldBounds(nodes) {
      const positions = (Array.isArray(nodes) ? nodes : []).map(nodePosition);
      let minX = Math.min(0, ...positions.map(position => position.x));
      let maxX = Math.max(100, ...positions.map(position => position.x));
      let minY = Math.min(0, ...positions.map(position => position.y));
      let maxY = Math.max(100, ...positions.map(position => position.y));
      if (minX < 0 || maxX > 100) {
        const padding = Math.max(6, (maxX - minX) * 0.06);
        minX -= padding;
        maxX += padding;
      }
      if (minY < 0 || maxY > 100) {
        const padding = Math.max(6, (maxY - minY) * 0.06);
        minY -= padding;
        maxY += padding;
      }
      return { minX, maxX, minY, maxY };
    }

    function worldToMinimap(x, y) {
      const width = Math.max(1, minimapDomain.maxX - minimapDomain.minX);
      const height = Math.max(1, minimapDomain.maxY - minimapDomain.minY);
      return {
        x: (number(x, 50) - minimapDomain.minX) / width * 100,
        y: (number(y, 50) - minimapDomain.minY) / height * 100,
      };
    }

    function minimapToWorld(x, y) {
      return {
        x: minimapDomain.minX + number(x, 50) / 100 * (minimapDomain.maxX - minimapDomain.minX),
        y: minimapDomain.minY + number(y, 50) / 100 * (minimapDomain.maxY - minimapDomain.minY),
      };
    }

    function updateDraggedMinimapGeometry(movedNodeIds, affectedEdgeIds) {
      if (
        !minimapLayers
        || !minimap
        || minimap.hasAttribute("hidden")
        || !minimapRenderState
      ) return;
      if (state.model.viewMode === "workspace") {
        updateMinimapGeometry();
        return;
      }
      movedNodeIds.forEach(nodeId => {
        const node = state.nodeMap.get(nodeId) || minimapRenderState.overviewMap.get(nodeId);
        const nodeElement = minimapNodeElement(nodeId);
        if (!node || !nodeElement) return;
        const position = nodePosition(node);
        const point = worldToMinimap(position.x, position.y);
        nodeElement.setAttribute("cx", String(point.x));
        nodeElement.setAttribute("cy", String(point.y));
      });
      affectedEdgeIds.forEach(edgeId => {
        const edge = state.edgeById.get(edgeId);
        const edgeElement = minimapEdgeElement(edgeId);
        const source = state.nodeMap.get(edge?.source)
          || minimapRenderState.endpointMap.get(edge?.source);
        const target = state.nodeMap.get(edge?.target)
          || minimapRenderState.endpointMap.get(edge?.target);
        if (!edgeElement || !source || !target) return;
        const sourcePosition = nodePosition(source);
        const targetPosition = nodePosition(target);
        const start = worldToMinimap(sourcePosition.x, sourcePosition.y);
        const end = worldToMinimap(targetPosition.x, targetPosition.y);
        edgeElement.setAttribute("x1", String(start.x));
        edgeElement.setAttribute("y1", String(start.y));
        edgeElement.setAttribute("x2", String(end.x));
        edgeElement.setAttribute("y2", String(end.y));
      });
      updateMinimapViewport();
    }

    function updateMinimapViewport(viewport = state.viewportSize) {
      if (!minimapLayers || minimap?.hasAttribute("hidden")) return;
      const bounds = visibleWorldBounds(state.transform, viewport, currentViewportInsets(viewport));
      const start = worldToMinimap(bounds.minX, bounds.minY);
      const end = worldToMinimap(bounds.maxX, bounds.maxY);
      const left = clamp(Math.min(start.x, end.x), 0, 100);
      const top = clamp(Math.min(start.y, end.y), 0, 100);
      const right = clamp(Math.max(start.x, end.x), 0, 100);
      const bottom = clamp(Math.max(start.y, end.y), 0, 100);
      minimapLayers.viewport
        .attr("x", left)
        .attr("y", top)
        .attr("width", Math.max(1.5, right - left))
        .attr("height", Math.max(1.5, bottom - top));
    }

    function renderMinimap({ geometryChanged = true } = {}) {
      if (!minimapLayers || !minimap) {
        minimapRenderState = null;
        return;
      }
      const overviewNodes = state.model.overviewNodes.length ? state.model.overviewNodes : state.model.nodes;
      const overviewEdges = state.model.overviewEdges.length ? state.model.overviewEdges : state.model.edges;
      const overviewMap = new Map(overviewNodes.map(node => [node.id, node]));
      const visibleIds = new Set(state.model.nodes.map(node => node.id));
      const endpointMap = overviewMap;
      minimapRenderState = {
        overviewNodes,
        overviewEdges,
        overviewMap,
        visibleIds,
        endpointMap,
      };
      const threshold = Math.max(2, number(options.minimapThreshold, 8));
      const shouldHide = overviewNodes.length < threshold;
      minimapDomain = worldBounds(overviewNodes);
      minimap.toggleAttribute("hidden", shouldHide);
      minimap.dataset.nodeCount = String(overviewNodes.length);
      minimap.dataset.worldMinX = String(minimapDomain.minX);
      minimap.dataset.worldMaxX = String(minimapDomain.maxX);
      minimap.dataset.worldMinY = String(minimapDomain.minY);
      minimap.dataset.worldMaxY = String(minimapDomain.maxY);
      if (shouldHide) return;
      minimapLayers.edges.selectAll("line.hatgMinimapEdge")
        .data(overviewEdges.filter(edge => endpointMap.has(edge.source) && endpointMap.has(edge.target)), edge => edge.id)
        .join("line")
        .attr("class", edge => `hatgMinimapEdge ${edge.type || "handoff"}${visibleIds.has(edge.source) && visibleIds.has(edge.target) ? " visible" : " muted"}`)
        .attr("data-program-wire", edge => isProgramWire(edge) ? "true" : null)
        .attr("data-wire-type", edge => isProgramWire(edge) ? (edge.wire_type || edge.type || "") : null);
      minimapLayers.nodes.selectAll(".hatgMinimapNode")
        .data(overviewNodes, node => `${node.kind === "workspace" ? "workspace" : "node"}:${node.id}`)
        .join(enter => enter.append(node => document.createElementNS("http://www.w3.org/2000/svg", node.kind === "workspace" ? "rect" : "circle")))
        .attr("class", node => `hatgMinimapNode${node.kind === "seed" ? " seed" : ""}${visibleIds.has(node.id) ? " visible" : " muted"}${node.id === state.model.selectedNodeId ? " selected" : ""}`)
        .attr("data-minimap-node-id", node => node.id)
        .attr("data-node-kind", primitiveNodeKind)
        .attr("width", node => node.kind === "workspace" ? 4 : null)
        .attr("height", node => node.kind === "workspace" ? 3 : null)
        .attr("rx", node => node.kind === "workspace" ? 0.4 : null)
        .attr("data-context-heat", node => nodeContextHeat(node) || null)
        .attr("r", node => node.id === state.model.selectedNodeId ? 2.35 : 1.55);
      if (geometryChanged) updateMinimapGeometry();
      else updateMinimapViewport();
    }

    function updateMinimapGeometry() {
      if (!minimapLayers || !minimap || minimap.hasAttribute("hidden") || !minimapRenderState) return;
      const {
        overviewNodes,
        endpointMap,
      } = minimapRenderState;
      minimapDomain = worldBounds(overviewNodes);
      minimap.dataset.worldMinX = String(minimapDomain.minX);
      minimap.dataset.worldMaxX = String(minimapDomain.maxX);
      minimap.dataset.worldMinY = String(minimapDomain.minY);
      minimap.dataset.worldMaxY = String(minimapDomain.maxY);
      minimapLayers.edges.selectAll("line.hatgMinimapEdge")
        .attr("x1", edge => worldToMinimap(nodePosition(endpointMap.get(edge.source)).x, nodePosition(endpointMap.get(edge.source)).y).x)
        .attr("y1", edge => worldToMinimap(nodePosition(endpointMap.get(edge.source)).x, nodePosition(endpointMap.get(edge.source)).y).y)
        .attr("x2", edge => worldToMinimap(nodePosition(endpointMap.get(edge.target)).x, nodePosition(endpointMap.get(edge.target)).y).x)
        .attr("y2", edge => worldToMinimap(nodePosition(endpointMap.get(edge.target)).x, nodePosition(endpointMap.get(edge.target)).y).y);
      minimapLayers.nodes.selectAll(".hatgMinimapNode")
        .attr("x", node => worldToMinimap(nodePosition(node).x, nodePosition(node).y).x - 2)
        .attr("y", node => worldToMinimap(nodePosition(node).x, nodePosition(node).y).y - 1.5)
        .attr("cx", node => worldToMinimap(nodePosition(node).x, nodePosition(node).y).x)
        .attr("cy", node => worldToMinimap(nodePosition(node).x, nodePosition(node).y).y);
      updateMinimapViewport();
    }

    if (minimapSelection) {
      minimapSelection
        .on("click.hatg", event => {
          const [x, y] = d3.pointer(event, minimap);
          const position = minimapToWorld(x, y);
          centerAt(position.x, position.y);
        })
        .call(d3.drag()
          .container(() => minimap)
          .on("drag.hatg", event => {
            const position = minimapToWorld(event.x, event.y);
            centerAt(position.x, position.y, { duration: 0 });
          }));
    }

    function destroyMinimap() {
      minimapSelection?.on(".hatg", null).on(".drag", null);
    }

    return {
      destroyMinimap, renderMinimap, updateDraggedMinimapGeometry, updateMinimapGeometry, updateMinimapViewport,
    };
  }

  window.PortalHatgGraphMinimap = Object.freeze({ createMinimap, dependencies: DEPENDENCIES });
})();
