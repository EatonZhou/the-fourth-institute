(() => {
  "use strict";

  if (window.PortalHatgGraphEdgeRoutes) return;

  // Edge geometry: where an edge starts and ends, its curve, the detour around
  // the cards in its way, the lanes of parallel edges and where its label
  // goes. Computed routes are kept until the geometry they depend on changes.

  const DEPENDENCIES = Object.freeze([
    "clamp", "edgeLabelElement", "graphGeometry", "nodeHalfExtents", "nodePosition", "number", "portPosition",
    "portsForNode", "state", "worldSize",
  ]);

  function createEdgeRoutes(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphEdgeRoutes is missing: ${missing.join(", ")}`);
    const {
      clamp, edgeLabelElement, graphGeometry, nodeHalfExtents, nodePosition, number, portPosition, portsForNode,
      state, worldSize,
    } = dependencies;

    const { sampledRoute, routeLength, routePath } = graphGeometry;

    let parallelEdgeLaneMap = new Map();

    let edgeGeometryMap = new Map();
    let routingNodes = null;

    function isProgramWire(edge) {
      return !!(edge?._program_wire);
    }

    function invalidateEdgeGeometry() {
      edgeGeometryMap.clear();
      routingNodes = null;
    }

    function forgetEdgeGeometry(edgeId) {
      edgeGeometryMap.delete(edgeId);
      routingNodes = null;
    }

    function rememberEdgeGeometry(edgeId, geometry) {
      edgeGeometryMap.set(edgeId, geometry);
    }

    const parallelEdgeLane = edgeId => parallelEdgeLaneMap.get(edgeId);

    function endpointById(endpointId) {
      const node = state.nodeMap.get(endpointId);
      if (node) return node;
      const observed = state.edgeById.get(endpointId);
      if (!observed || !["event", "handoff"].includes(observed.type)
          || !state.nodeMap.has(observed.source) || !state.nodeMap.has(observed.target)) return null;
      const geometry = edgeGeometry(observed);
      return geometry ? { id: endpointId, x: geometry.labelX, y: geometry.labelY } : null;
    }

    function edgeTrimDistance(node, ux, uy, distance) {
      if (node.kind === "workspace_resident") {
        const radiusX = 11 / worldSize.width * 100;
        const radiusY = 11 / worldSize.height * 100;
        return Math.min(1 / Math.hypot(ux / radiusX, uy / radiusY) + 0.35, distance * 0.44);
      }
      const half = nodeHalfExtents(node);
      const horizontal = Math.abs(ux) > 0.001 ? half.x / Math.abs(ux) : Number.POSITIVE_INFINITY;
      const vertical = Math.abs(uy) > 0.001 ? half.y / Math.abs(uy) : Number.POSITIVE_INFINITY;
      return Math.min(Math.min(horizontal, vertical) + 0.65, distance * 0.44);
    }

    function trimmedEdgePoints(source, target) {
      const anchor = node => {
        const position = nodePosition(node);
        if (node.kind === "workspace_resident") {
          // The circle sits at the left of its informational name/ID label.
          position.x -= nodeHalfExtents(node).x - 11 / worldSize.width * 100;
        }
        return position;
      };
      const sourcePosition = anchor(source);
      const targetPosition = anchor(target);
      const dx = targetPosition.x - sourcePosition.x;
      const dy = targetPosition.y - sourcePosition.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const ux = dx / distance;
      const uy = dy / distance;
      const sourceTrim = edgeTrimDistance(source, ux, uy, distance);
      const targetTrim = edgeTrimDistance(target, -ux, -uy, distance);
      return {
        x1: sourcePosition.x + ux * sourceTrim,
        y1: sourcePosition.y + uy * sourceTrim,
        x2: targetPosition.x - ux * targetTrim,
        y2: targetPosition.y - uy * targetTrim,
      };
    }

    function programWirePoints(edge) {
      if (!isProgramWire(edge)) return null;
      const sourceNode = state.nodeMap.get(String(edge?.source || ""));
      const targetNode = state.nodeMap.get(String(edge?.target || ""));
      if (!sourceNode) return null;
      const wireType = String(edge?.wire_type || edge?.type || "");
      const sourcePort = portsForNode(sourceNode).find(port =>
        port.direction === "out" && port.type === wireType
        && port.port_id === String(edge.source_port || wireType));
      if (!sourcePort) return null;
      const source = portPosition(sourcePort);
      if (wireType === "watch" && edge.watch_subject_wire_id) {
        const observed = state.edgeById.get(edge.watch_subject_wire_id);
        const geometry = observed && edgeGeometry(observed);
        if (!geometry) return null;
        return { x1: source.x, y1: source.y, x2: geometry.labelX, y2: geometry.labelY };
      }
      if (!targetNode) return null;
      if (["watch", "target"].includes(wireType)) {
        const center = nodePosition(targetNode);
        const dx = center.x - source.x, dy = center.y - source.y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const trim = edgeTrimDistance(targetNode, -dx / distance, -dy / distance, distance);
        return { x1: source.x, y1: source.y,
          x2: center.x - dx / distance * trim, y2: center.y - dy / distance * trim };
      }
      const targetPort = portsForNode(targetNode).find(port => port.direction === "in"
        && port.port_id === String(edge.target_port || "invoke"));
      if (!targetPort) return null;
      const target = portPosition(targetPort);
      return { x1: source.x, y1: source.y, x2: target.x, y2: target.y };
    }

    function routeBlockingNodes(edge, segments) {
      const points = sampledRoute(segments);
      // A geometry generation owns one snapshot of card bounds. Routing each
      // candidate must not repeatedly resolve every node's position and size.
      routingNodes ||= state.model.nodes.map(node => ({ node,
        position: nodePosition(node), half: nodeHalfExtents(node) }));
      const minX = Math.min(...points.map(point => point.x));
      const maxX = Math.max(...points.map(point => point.x));
      const minY = Math.min(...points.map(point => point.y));
      const maxY = Math.max(...points.map(point => point.y));
      return routingNodes.filter(({ node, position, half }) => {
        // An endpoint may touch its own boundary, but its curve must not
        // cross back through the card to reach a port on the opposite side.
        const endpoint = node.id === edge.source || node.id === edge.target;
        const marginX = Math.max(0, half.x + (endpoint ? -0.1 : 0.65));
        const marginY = Math.max(0, half.y + (endpoint ? -0.1 : 0.85));
        if (maxX < position.x - marginX || minX > position.x + marginX
            || maxY < position.y - marginY || minY > position.y + marginY) return false;
        return points.some(point => graphGeometry.pointInBox(point, position, { x: marginX, y: marginY }));
      }).map(box => box.node);
    }

    function detourSegments(base, entryX, exitX, trackY) {
      const firstSpan = entryX - base.x1;
      const trackSpan = exitX - entryX;
      const finalSpan = base.x2 - exitX;
      return [
        {
          x1: base.x1,
          y1: base.y1,
          c1x: base.x1 + firstSpan * 0.42,
          c1y: base.y1,
          c2x: entryX - firstSpan * 0.28,
          c2y: trackY,
          x2: entryX,
          y2: trackY,
        },
        {
          x1: entryX,
          y1: trackY,
          c1x: entryX + trackSpan / 3,
          c1y: trackY,
          c2x: entryX + trackSpan * 2 / 3,
          c2y: trackY,
          x2: exitX,
          y2: trackY,
        },
        {
          x1: exitX,
          y1: trackY,
          c1x: exitX + finalSpan * 0.28,
          c1y: trackY,
          c2x: base.x2 - finalSpan * 0.42,
          c2y: base.y2,
          x2: base.x2,
          y2: base.y2,
        },
      ];
    }

    function routeAroundNodes(edge, baseGeometry) {
      const vertical = baseGeometry.routeAxis === "vertical";
      const transposeSegment = segment => ({
        x1: segment.y1, y1: segment.x1, c1x: segment.c1y, c1y: segment.c1x,
        c2x: segment.c2y, c2y: segment.c2x, x2: segment.y2, y2: segment.x2,
      });
      const originalSegment = {
        x1: baseGeometry.x1,
        y1: baseGeometry.y1,
        c1x: baseGeometry.c1x,
        c1y: baseGeometry.c1y,
        c2x: baseGeometry.c2x,
        c2y: baseGeometry.c2y,
        x2: baseGeometry.x2,
        y2: baseGeometry.y2,
      };
      const blockers = routeBlockingNodes(edge, [originalSegment]);
      if (!blockers.length) return baseGeometry;
      if (isProgramWire(edge) && (edge.wire_type || edge.type) === "route"
          && blockers.some(node => node.id === edge.source || node.id === edge.target)) {
        // Program Route ports face right (output) and left (input), regardless
        // of the relative positions of the cards. Leave and approach on those
        // sides; choosing tangents solely from the destination reverses them.
        const tracks = [blockers, state.model.nodes].flatMap(nodes => {
          const boxes = nodes.map(node => ({position: nodePosition(node), half: nodeHalfExtents(node)}));
          return [Math.min(...boxes.map(box => box.position.y - box.half.y)) - 2,
            Math.max(...boxes.map(box => box.position.y + box.half.y)) + 2];
        });
        const candidates = tracks.map(trackY => {
          const segments = detourSegments(originalSegment, originalSegment.x1 + 2, originalSegment.x2 - 2, trackY);
          return {segments, blocked: routeBlockingNodes(edge, segments).length, distance: routeLength(segments)};
        }).sort((left, right) => left.blocked - right.blocked || left.distance - right.distance);
        const best = candidates[0];
        if (best.blocked < blockers.length) {
          const routePoints = sampledRoute(best.segments, 48);
          const label = routePoints[Math.floor(routePoints.length / 2)];
          return {...baseGeometry, d: routePath(best.segments), labelPathPoints: routePoints,
            labelX: label.x, labelY: label.y, routing: "obstacle",
            routeDirection: originalSegment.x2 >= originalSegment.x1 ? "forward" : "backward"};
        }
      }
      const baseSegment = vertical ? transposeSegment(originalSegment) : originalSegment;
      const routeBase = baseSegment;
      const transposePoint = point => vertical ? { x: point.y, y: point.x } : point;
      const blockerDetails = blockers.map(node => ({
        node,
        position: transposePoint(nodePosition(node)),
        half: transposePoint(nodeHalfExtents(node)),
      }));
      const direction = routeBase.x2 >= routeBase.x1 ? 1 : -1;
      const minimumX = Math.min(routeBase.x1, routeBase.x2);
      const maximumX = Math.max(routeBase.x1, routeBase.x2);
      if (maximumX - minimumX < 7) return baseGeometry;
      const blockerLeft = Math.min(...blockerDetails.map(detail => detail.position.x - detail.half.x)) - 1.1;
      const blockerRight = Math.max(...blockerDetails.map(detail => detail.position.x + detail.half.x)) + 1.1;
      const entryX = direction > 0
        ? clamp(blockerLeft, routeBase.x1 + 1.4, routeBase.x2 - 2.8)
        : clamp(blockerRight, routeBase.x2 + 2.8, routeBase.x1 - 1.4);
      const exitX = direction > 0
        ? clamp(blockerRight, entryX + 1.4, routeBase.x2 - 1.4)
        : clamp(blockerLeft, routeBase.x2 + 1.4, entryX - 1.4);
      const labelElement = edgeLabelElement(String(edge.id));
      const labelClearance = vertical
        ? (labelElement?.offsetWidth || 92) / worldSize.width * 50
        : (labelElement?.offsetHeight || 24) / worldSize.height * 50;
      const clearance = Math.max(1.35, labelClearance + 0.65);
      const top = Math.min(...blockerDetails.map(detail => detail.position.y - detail.half.y)) - clearance;
      const bottom = Math.max(...blockerDetails.map(detail => detail.position.y + detail.half.y)) + clearance;
      const candidates = [top, bottom].map(trackY => {
        const routeSegments = detourSegments(baseSegment, entryX, exitX, trackY);
        const segments = vertical ? routeSegments.map(transposeSegment) : routeSegments;
        const blocked = routeBlockingNodes(edge, segments).length;
        const distance = routeLength(segments);
        const directionBias = Math.abs(trackY - routeBase.y2) * 0.04;
        return { segments, blocked, distance, directionBias };
      }).sort((left, right) => (
        left.blocked - right.blocked
        || left.distance + left.directionBias - right.distance - right.directionBias
      ));
      const best = candidates[0];
      if (!best || best.blocked >= blockers.length) return baseGeometry;
      const routePoints = sampledRoute(best.segments, 16);
      const labelPoint = routePoints[Math.floor(routePoints.length / 2)] || { x: (entryX + exitX) / 2, y: best.segments[0].y2 };
      return {
        ...baseGeometry,
        d: routePath(best.segments),
        labelPathPoints: routePoints,
        labelX: labelPoint.x,
        labelY: labelPoint.y,
        routing: "obstacle",
        routeDirection: direction > 0 ? "forward" : "backward",
      };
    }

    function refreshParallelEdgeLanes(edges = []) {
      const groups = new Map();
      edges.forEach(edge => {
        const sourceId = String(edge?.source || "");
        const targetId = String(edge?.target || "");
        if (!sourceId || !targetId || sourceId === targetId) return;
        const endpoints = [sourceId, targetId].sort();
        const key = `${endpoints[0]}\u0000${endpoints[1]}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(edge);
      });
      parallelEdgeLaneMap = new Map();
      groups.forEach(group => {
        const ordered = [...group].sort((left, right) => (
          String(left?.type || "").localeCompare(String(right?.type || ""))
          || String(left?.id || "").localeCompare(String(right?.id || ""))
        ));
        ordered.forEach((edge, index) => {
          const sourceId = String(edge?.source || "");
          const targetId = String(edge?.target || "");
          const lane = index - (ordered.length - 1) / 2;
          parallelEdgeLaneMap.set(edge.id, {
            count: ordered.length,
            index,
            lane,
            pathLane: lane * (sourceId <= targetId ? 1 : -1),
          });
        });
      });
    }

    function parallelEdgeOffset(edge, points) {
      const parallel = parallelEdgeLaneMap.get(edge?.id);
      if (!parallel || parallel.count < 2 || !parallel.pathLane) {
        return { x: 0, y: 0, labelY: 0 };
      }
      const dx = (points.x2 - points.x1) / 100 * worldSize.width;
      const dy = (points.y2 - points.y1) / 100 * worldSize.height;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const visualScale = 1 / Math.max(0.01, state.transform.k);
      const pathDistance = parallel.pathLane * 30 * visualScale;
      const labelLane = Math.abs(dx) > 0.5
        ? parallel.pathLane * Math.sign(dx)
        : parallel.lane;
      return {
        x: -dy / distance * pathDistance / worldSize.width * 100,
        y: dx / distance * pathDistance / worldSize.height * 100,
        labelY: labelLane * 24 * visualScale / worldSize.height * 100,
      };
    }

    function computeEdgeGeometry(edge, options = {}) {
      const source = endpointById(edge?.source);
      const target = endpointById(edge?.target);
      if (!source || !target) return null;
      if (source.id === target.id && !edge.watch_subject_wire_id) {
        const position = nodePosition(source);
        const half = nodeHalfExtents(source);
        const loopWidth = Math.max(7, half.x * 0.72);
        const loopHeight = Math.max(7, half.y * 0.9);
        const x1 = position.x + half.x * 0.24;
        const y1 = position.y - half.y - 0.35;
        const x2 = position.x + half.x + 0.35;
        const y2 = position.y - half.y * 0.18;
        const c1x = position.x + half.x + loopWidth;
        const c1y = position.y - half.y - loopHeight;
        const c2x = position.x + half.x + loopWidth;
        const c2y = position.y + half.y * 0.35;
        return {
          x1, y1, x2, y2,
          c1x, c1y, c2x, c2y,
          labelPathPoints: sampledRoute([{x1, y1, x2, y2, c1x, c1y, c2x, c2y}], 48),
          labelX: position.x + half.x + loopWidth * 0.62,
          labelY: position.y - half.y - loopHeight * 0.72,
          d: `M ${x1} ${y1} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${x2} ${y2}`,
        };
      }
      const points = programWirePoints(edge) || trimmedEdgePoints(source, target);
      const parallelOffset = parallelEdgeOffset(edge, points);
      const geometry = {
        ...graphGeometry.bezierGeometry(points, worldSize, parallelOffset),
        labelX: (number(source.x, 50) + number(target.x, 50)) / 2 + parallelOffset.x,
        labelY: (number(source.y, 50) + number(target.y, 50)) / 2 + parallelOffset.labelY,
      };
      geometry.labelPathPoints = sampledRoute([geometry], 48);
      return options.avoidObstacles === false ? geometry : routeAroundNodes(edge, geometry);
    }

    function edgeGeometry(edge) {
      const edgeId = String(edge?.id || "");
      if (edgeId && edgeGeometryMap.has(edgeId)) return edgeGeometryMap.get(edgeId);
      const geometry = computeEdgeGeometry(edge);
      if (edgeId) edgeGeometryMap.set(edgeId, geometry);
      return geometry;
    }

    function resolveEdgeLabelCollisions(edges = []) {
      invalidateEdgeGeometry();
      const placed = [];
      // Node placement does not reserve space for edge labels. Include endpoint
      // cards as obstacles here too, including the source and destination.
      // Keep their measured bounds: the label box below already includes the
      // clearance. Padding both boxes rejects labels that fit between cards.
      const cards = state.model.nodes.map(node => {
        const position = nodePosition(node), half = nodeHalfExtents(node);
        return { x: position.x / 100 * worldSize.width, y: position.y / 100 * worldSize.height,
          halfWidth: half.x / 100 * worldSize.width,
          halfHeight: half.y / 100 * worldSize.height };
      });
      // Query only boxes near a candidate label. Cards and labels retain the
      // same exact collision predicate and deterministic placement order.
      const cells = new Map();
      const cellSize = 256;
      function visitCells(box, visit) {
        for (let x = Math.floor((box.x - box.halfWidth) / cellSize);
          x <= Math.floor((box.x + box.halfWidth) / cellSize); x += 1) {
          for (let y = Math.floor((box.y - box.halfHeight) / cellSize);
            y <= Math.floor((box.y + box.halfHeight) / cellSize); y += 1) {
            if (visit(`${x}:${y}`) === false) return false;
          }
        }
        return true;
      }
      function indexBox(box) {
        visitCells(box, key => {
          if (!cells.has(key)) cells.set(key, []);
          cells.get(key).push(box);
        });
      }
      cards.forEach(indexBox);
      function place(box) {
        placed.push(box);
        indexBox(box);
      }
      function isFree(point, width, height) {
        return visitCells({ ...point, halfWidth: width / 2, halfHeight: height / 2 }, key => (
          (cells.get(key) || []).every(box => (
            Math.abs(point.x - box.x) >= width / 2 + box.halfWidth
            || Math.abs(point.y - box.y) >= height / 2 + box.halfHeight
          ))
        ));
      }
      [...edges]
        .filter(edge => edge.type !== "workspace_binding")
        .sort((left, right) => String(left?.id || "").localeCompare(String(right?.id || "")))
        .forEach(edge => {
          const geometry = computeEdgeGeometry(edge);
          if (!geometry) {
            edgeGeometryMap.set(String(edge?.id || ""), geometry);
            return;
          }
          const element = edgeLabelElement(String(edge.id));
          const labelWidth = (element?.offsetWidth || 92) + 16;
          const labelHeight = (element?.offsetHeight || 24) + 16;
          const preferredX = geometry.labelX / 100 * worldSize.width;
          const baseY = geometry.labelY / 100 * worldSize.height;
          // Follow the routed curve before displacing a label away from it.
          // Moving only vertically can put a Route label beside a different
          // edge, especially after a detour around a card or a geometry refresh.
          const onPath = geometry.labelPathPoints.map(point => ({
            x: point.x / 100 * worldSize.width, y: point.y / 100 * worldSize.height,
          })).sort((left, right) => (
            Math.hypot(left.x - preferredX, left.y - baseY)
            - Math.hypot(right.x - preferredX, right.y - baseY)
          )).find(point => isFree(point, labelWidth, labelHeight));
          if (onPath) {
            geometry.labelX = onPath.x / worldSize.width * 100;
            geometry.labelY = onPath.y / worldSize.height * 100;
            place({...onPath, halfWidth: labelWidth / 2, halfHeight: labelHeight / 2});
            edgeGeometryMap.set(String(edge.id || ""), geometry);
            return;
          }
          // A short gap between endpoint cards may have no room on its path.
          // Keep the existing unobscured placement in that case.
          const x = preferredX;
          const obstacles = [...cards, ...placed].filter(box =>
            Math.abs(x - box.x) < labelWidth / 2 + box.halfWidth);
          // Nearest free vertical position lies at the desired point or at an
          // obstacle boundary. Unlike a fixed list of offsets, this also works
          // for tall cards and crowded diagrams without an unchecked fallback.
          const candidates = [baseY, ...obstacles.flatMap(box => [
            box.y - box.halfHeight - labelHeight / 2 - 1,
            box.y + box.halfHeight + labelHeight / 2 + 1,
          ])].sort((left, right) => Math.abs(left - baseY) - Math.abs(right - baseY) || left - right);
          // Candidate order is unchanged; use the same local index for the
          // fallback instead of scanning every vertically aligned obstacle.
          const y = candidates.find(candidate => isFree({ x, y: candidate }, labelWidth, labelHeight));
          geometry.labelY = y / worldSize.height * 100;
          // When the gap cannot hold the label, preserve its association with
          // this precise curve instead of leaving an unanchored floating label.
          const anchor = geometry.labelPathPoints.reduce((nearest, point) => {
            const distance = Math.hypot(point.x / 100 * worldSize.width - x,
              point.y / 100 * worldSize.height - y);
            return !nearest || distance < nearest.distance ? { ...point, distance } : nearest;
          }, null);
          geometry.labelAnchor = { x: anchor.x, y: anchor.y };
          place({ x, y, halfWidth: labelWidth / 2, halfHeight: labelHeight / 2 });
          edgeGeometryMap.set(String(edge.id || ""), geometry);
        });
    }

    function validEdges() {
      return state.model.edges.filter(edge => endpointById(edge.source) && endpointById(edge.target));
    }

    return {
      computeEdgeGeometry, edgeGeometry, endpointById, forgetEdgeGeometry, invalidateEdgeGeometry, isProgramWire,
      parallelEdgeLane, refreshParallelEdgeLanes, rememberEdgeGeometry, resolveEdgeLabelCollisions, validEdges,
    };
  }

  window.PortalHatgGraphEdgeRoutes = Object.freeze({ createEdgeRoutes, dependencies: DEPENDENCIES });
})();
