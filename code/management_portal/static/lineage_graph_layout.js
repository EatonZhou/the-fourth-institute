(() => {
  "use strict";

  if (window.PortalHatgGraphLayout) return;

  // Automatic layouts of a Lineage graph: positions for the nodes of a
  // Workspace view and for a branch-aware layered view of a Lineage. They read
  // only their arguments; createGraph is not needed.

  const DEFAULT_WORLD_WIDTH = 1280;
  const DEFAULT_WORLD_HEIGHT = 900;

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function computeWorkspaceGraphLayout(nodes = [], edges = []) {
    // Binding graphs are not execution layers: shared roots would put every
    // Workspace and Resident into two unbounded columns. Place markers near
    // their most specific binding, then pack the resulting neighborhoods in
    // both dimensions. These are positions only, never enclosing UI groups.
    const ordered = nodes.filter(node => node?.id).slice()
      .sort((a, b) => String(a.id).localeCompare(String(b.id)));
    if (!ordered.length) return new Map();
    const workspaces = ordered.filter(node => node.kind === "workspace");
    const members = new Map(workspaces.map(node => [node.id, new Set()]));
    const bindings = new Map(ordered.filter(node => node.kind !== "workspace").map(node => [node.id, new Set()]));
    const neighbors = new Map(workspaces.map(node => [node.id, new Set()]));
    edges.forEach(edge => {
      if (edge.type === "workspace_binding" && members.has(edge.source) && bindings.has(edge.target)) {
        members.get(edge.source).add(edge.target);
        bindings.get(edge.target).add(edge.source);
      } else if (edge.type === "handoff" && neighbors.has(edge.source) && neighbors.has(edge.target)) {
        neighbors.get(edge.source).add(edge.target);
        neighbors.get(edge.target).add(edge.source);
      }
    });
    const assigned = new Map(workspaces.map(node => [node.id, []]));
    const unbound = [];
    bindings.forEach((roots, residentId) => {
      const closest = [...roots].sort((a, b) => members.get(a).size - members.get(b).size || String(a).localeCompare(String(b)))[0];
      if (closest) assigned.get(closest).push(residentId);
      else unbound.push(residentId);
    });
    const workspaceOrder = [];
    const visited = new Set();
    const hubsFirst = workspaces.slice().sort((a, b) => members.get(b.id).size - members.get(a.id).size || String(a.id).localeCompare(String(b.id)));
    for (const node of hubsFirst) {
      const pending = [node.id];
      while (pending.length) {
        const id = pending.pop();
        if (visited.has(id)) continue;
        visited.add(id);
        workspaceOrder.push(id);
        pending.push(...[...neighbors.get(id)].sort().reverse().filter(next => !visited.has(next)));
      }
    }
    const tiles = workspaceOrder.map(id => {
      const residents = assigned.get(id);
      const columns = Math.max(1, Math.ceil(Math.sqrt(residents.length * 18 / 22)));
      const rows = Math.ceil(residents.length / columns);
      const width = residents.length ? 44 + (columns - 1) * 22 : 18;
      const height = Math.max(14, 14 + (rows - 1) * 18);
      return { width, height, positions: [
        [id, { x: 9, y: height / 2 }],
        ...residents.map((residentId, i) => [residentId, { x: 35 + (i % columns) * 22, y: 7 + Math.floor(i / columns) * 18 }]),
      ] };
    });
    unbound.forEach(id => tiles.push({ width: 18, height: 14, positions: [[id, { x: 9, y: 7 }]] }));
    const targetAspect = DEFAULT_WORLD_HEIGHT / DEFAULT_WORLD_WIDTH * 1.2;
    const area = tiles.reduce((sum, tile) => sum + (tile.width + 8) * (tile.height + 10), 0);
    const widest = Math.max(...tiles.map(tile => tile.width));
    let best = null;
    // A small deterministic shelf search avoids a final sparse, tall column,
    // including one shared Workspace with many markers and disconnected nodes.
    for (let step = 0; step <= 12; step += 1) {
      const limit = Math.max(widest, Math.sqrt(area * targetAspect) * (0.65 + step * 0.075));
      let x = 0, y = 0, rowHeight = 0, width = 0;
      const positions = new Map();
      tiles.forEach(tile => {
        if (x && x + tile.width > limit) { y += rowHeight + 10; x = 0; rowHeight = 0; }
        tile.positions.forEach(([id, p]) => positions.set(id, { x: x + p.x, y: y + p.y }));
        width = Math.max(width, x + tile.width);
        rowHeight = Math.max(rowHeight, tile.height);
        x += tile.width + 8;
      });
      const height = y + rowHeight;
      const score = width * height / area + Math.abs(Math.log(width / height / targetAspect));
      if (!best || score < best.score) best = { positions, width, height, score };
    }
    return new Map([...best.positions].map(([id, p]) => [id, { x: 50 + p.x - best.width / 2, y: 50 + p.y - best.height / 2 }]));
  }

  function computeLayeredLayout(nodes = [], edges = [], options = {}) {
    const sourceNodes = (Array.isArray(nodes) ? nodes : [])
      .filter(node => String(node?.id || ""));
    const sourceOrder = new Map(sourceNodes.map((node, index) => [String(node.id), index]));
    const orderByCreatedAt = options.nodeOrder === "created_at";
    const preserveSemanticOrder = orderByCreatedAt || options.nodeOrder === "branch";
    const createdTime = node => {
      const parsed = Date.parse(String(node?.created_at || ""));
      return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
    };
    const compareCreated = (left, right) => {
      const leftTime = createdTime(left);
      const rightTime = createdTime(right);
      if (leftTime !== rightTime) return leftTime < rightTime ? -1 : 1;
      const sourceDifference = sourceOrder.get(String(left.id)) - sourceOrder.get(String(right.id));
      return sourceDifference || String(left.id).localeCompare(String(right.id));
    };
    const orderedNodes = orderByCreatedAt
      ? [...sourceNodes].sort(compareCreated)
      : sourceNodes;
    const nodeById = new Map(orderedNodes.map(node => [String(node.id), node]));
    const nodeOrder = new Map(orderedNodes.map((node, index) => [String(node.id), index]));
    if (!orderedNodes.length) return new Map();

    const feedbackTypes = new Set(options.feedbackEdgeTypes || []);
    const rankEdges = (Array.isArray(edges) ? edges : []).filter(edge => {
      const source = String(edge?.source || "");
      const target = String(edge?.target || "");
      return source !== target
        && nodeById.has(source)
        && nodeById.has(target)
        && !feedbackTypes.has(String(edge?.type || ""));
    });
    const outgoing = new Map(orderedNodes.map(node => [String(node.id), []]));
    const incoming = new Map(orderedNodes.map(node => [String(node.id), []]));
    rankEdges.forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      outgoing.get(source).push(target);
      incoming.get(target).push(source);
    });
    const byNodeOrder = (left, right) => nodeOrder.get(left) - nodeOrder.get(right);
    outgoing.forEach(targets => targets.sort(byNodeOrder));
    incoming.forEach(sources => sources.sort(byNodeOrder));

    // Candidate seeds whose Markdown sits in one directory (one explorer's set)
    // are one semantic choice set; seeds without a file group by creation.
    // Keep that set together when a busy logical layer wraps into visual columns;
    // otherwise crossing minimization can scatter adjacent candidates across lanes.
    const seedGroupByNode = new Map();
    const seedMembersByGroup = new Map();
    orderedNodes.forEach(node => {
      const nodeId = String(node.id);
      if (String(node?.kind || "") !== "seed") return;
      const sourceId = String(node?.source_resident_id || "");
      const artifactPath = String(node?.artifact_path || "");
      const directory = artifactPath.slice(0, Math.max(0, artifactPath.lastIndexOf("/")));
      const created = `${String(node?.seed_type || "other")}\u0000${String(node?.created_at || "")}`;
      const groupId = `${sourceId}\u0000${directory || created}`;
      seedGroupByNode.set(nodeId, groupId);
      if (!seedMembersByGroup.has(groupId)) seedMembersByGroup.set(groupId, []);
      seedMembersByGroup.get(groupId).push(nodeId);
    });
    seedMembersByGroup.forEach(members => members.sort(byNodeOrder));

    // Collapse real cycles into one layer before assigning visual ranks.
    let sequence = 0;
    const stack = [];
    const onStack = new Set();
    const indexes = new Map();
    const lowLinks = new Map();
    const components = [];
    function visit(nodeId) {
      indexes.set(nodeId, sequence);
      lowLinks.set(nodeId, sequence);
      sequence += 1;
      stack.push(nodeId);
      onStack.add(nodeId);
      for (const targetId of outgoing.get(nodeId)) {
        if (!indexes.has(targetId)) {
          visit(targetId);
          lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId), lowLinks.get(targetId)));
        } else if (onStack.has(targetId)) {
          lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId), indexes.get(targetId)));
        }
      }
      if (lowLinks.get(nodeId) !== indexes.get(nodeId)) return;
      const component = [];
      let memberId = "";
      do {
        memberId = stack.pop();
        onStack.delete(memberId);
        component.push(memberId);
      } while (memberId !== nodeId);
      component.sort(byNodeOrder);
      components.push(component);
    }
    orderedNodes.forEach(node => {
      const nodeId = String(node.id);
      if (!indexes.has(nodeId)) visit(nodeId);
    });
    components.sort((left, right) => byNodeOrder(left[0], right[0]));

    const componentByNode = new Map();
    components.forEach((component, componentIndex) => {
      component.forEach(nodeId => componentByNode.set(nodeId, componentIndex));
    });
    const componentOutgoing = components.map(() => new Set());
    const componentIndegree = components.map(() => 0);
    rankEdges.forEach(edge => {
      const source = componentByNode.get(String(edge.source));
      const target = componentByNode.get(String(edge.target));
      if (source === target || componentOutgoing[source].has(target)) return;
      componentOutgoing[source].add(target);
      componentIndegree[target] += 1;
    });
    const componentOrder = componentIndex => nodeOrder.get(components[componentIndex][0]);
    const queue = components
      .map((_component, index) => index)
      .filter(index => componentIndegree[index] === 0)
      .sort((left, right) => componentOrder(left) - componentOrder(right));
    const componentDepth = components.map(() => 0);
    while (queue.length) {
      const source = queue.shift();
      const targets = [...componentOutgoing[source]]
        .sort((left, right) => componentOrder(left) - componentOrder(right));
      targets.forEach(target => {
        componentDepth[target] = Math.max(componentDepth[target], componentDepth[source] + 1);
        componentIndegree[target] -= 1;
        if (componentIndegree[target] === 0) {
          queue.push(target);
          queue.sort((left, right) => componentOrder(left) - componentOrder(right));
        }
      });
    }

    const depthByNode = new Map();
    const levels = new Map();
    orderedNodes.forEach(node => {
      const nodeId = String(node.id);
      const depth = componentDepth[componentByNode.get(nodeId)] || 0;
      depthByNode.set(nodeId, depth);
      if (!levels.has(depth)) levels.set(depth, []);
      levels.get(depth).push(nodeId);
    });

    // Keep each target close to the predecessor group that points to it. This is
    // the stable crossing-minimizing order for Lineage: relationship affinity
    // is primary, creation time orders siblings and unrelated nodes, and current
    // coordinates never participate in sorting.
    const rowByNode = new Map();
    const levelNumbers = [...levels.keys()].sort((left, right) => left - right);
    levelNumbers.forEach(depth => {
      const group = levels.get(depth);
      const affinity = nodeId => {
        const rows = incoming.get(nodeId)
          .filter(sourceId => depthByNode.get(sourceId) < depth && rowByNode.has(sourceId))
          .map(sourceId => rowByNode.get(sourceId));
        if (!rows.length) return null;
        return rows.reduce((total, row) => total + row, 0) / rows.length;
      };
      group.sort((left, right) => {
        const leftAffinity = affinity(left);
        const rightAffinity = affinity(right);
        if (leftAffinity !== null && rightAffinity !== null && leftAffinity !== rightAffinity) {
          return leftAffinity - rightAffinity;
        }
        if (leftAffinity !== null && rightAffinity === null) return -1;
        if (leftAffinity === null && rightAffinity !== null) return 1;
        return byNodeOrder(left, right);
      });
      group.forEach((nodeId, row) => rowByNode.set(nodeId, row));
    });

    const horizontalGap = Math.max(6, number(options.horizontalGap, 28));
    const verticalGap = Math.max(6, number(options.verticalGap, 20));
    const seedVerticalGap = Math.max(6, Math.min(verticalGap, number(options.seedVerticalGap, verticalGap * 0.68)));
    const centerX = number(options.centerX, 50);
    const centerY = number(options.centerY, 50);
    const configuredRowsPerLane = Math.floor(number(options.maxRowsPerLane, Number.POSITIVE_INFINITY));
    const maxRowsPerLane = Number.isFinite(configuredRowsPerLane)
      ? Math.max(2, configuredRowsPerLane)
      : Number.POSITIVE_INFINITY;
    function laneSizes(nodeCount) {
      if (nodeCount <= 0) return [];
      const laneCount = Number.isFinite(maxRowsPerLane)
        ? Math.max(1, Math.ceil(nodeCount / maxRowsPerLane))
        : 1;
      const baseSize = Math.floor(nodeCount / laneCount);
      const remainder = nodeCount % laneCount;
      return Array.from({ length: laneCount }, (_value, lane) => baseSize + (lane < remainder ? 1 : 0));
    }
    function lanesForLevel(group) {
      const groupIndex = new Map(group.map((nodeId, index) => [nodeId, index]));
      const groupedSeedIds = new Set();
      const seedLanes = [];
      for (const nodeId of group) {
        const groupId = seedGroupByNode.get(nodeId);
        if (!groupId || groupedSeedIds.has(groupId)) continue;
        const members = (seedMembersByGroup.get(groupId) || [])
          .filter(memberId => groupIndex.has(memberId));
        if (members.length < 2) continue;
        groupedSeedIds.add(groupId);
        seedLanes.push({
          nodes: members,
          order: Math.min(...members.map(memberId => groupIndex.get(memberId))),
        });
      }

      // A lone seed behaves like an ordinary node. A multi-candidate batch gets
      // a dedicated lane so all candidates share x and remain consecutive in y.
      const ordinaryNodes = group.filter(nodeId => {
        const groupId = seedGroupByNode.get(nodeId);
        return !groupId || !groupedSeedIds.has(groupId);
      });
      const ordinaryLanes = [];
      let offset = 0;
      laneSizes(ordinaryNodes.length).forEach(laneSize => {
        const lane = ordinaryNodes.slice(offset, offset + laneSize);
        ordinaryLanes.push({
          nodes: lane,
          order: Math.min(...lane.map(nodeId => groupIndex.get(nodeId))),
        });
        offset += laneSize;
      });
      return [...ordinaryLanes, ...seedLanes]
        .sort((left, right) => left.order - right.order)
        .map(lane => lane.nodes);
    }
    const columnStartByDepth = new Map();
    let columnCount = 0;
    levelNumbers.forEach(depth => {
      columnStartByDepth.set(depth, columnCount);
      columnCount += lanesForLevel(levels.get(depth)).length;
    });

    function buildLayout() {
      const layout = new Map();
      levelNumbers.forEach(depth => {
        const lanes = lanesForLevel(levels.get(depth));
        lanes.forEach((laneNodes, lane) => {
          const laneSize = laneNodes.length;
          const seedGroupId = seedGroupByNode.get(laneNodes[0]);
          const isSeedGroupLane = laneSize > 1
            && seedGroupId
            && laneNodes.every(nodeId => seedGroupByNode.get(nodeId) === seedGroupId);
          const firstSeed = isSeedGroupLane ? nodeById.get(laneNodes[0]) : null;
          const seedSourceId = String(firstSeed?.source_resident_id || "");
          const seedSourcePosition = layout.get(seedSourceId);
          const laneCenterY = seedSourcePosition?.y ?? centerY;
          for (let row = 0; row < laneSize; row += 1) {
            const nodeId = laneNodes[row];
            const column = columnStartByDepth.get(depth) + lane;
            layout.set(nodeId, {
              x: centerX + (column - (columnCount - 1) / 2) * horizontalGap,
              y: isSeedGroupLane
                ? laneCenterY + (row - (laneSize - 1) / 2) * seedVerticalGap
                : centerY + (row - (laneSize - 1) / 2) * verticalGap,
            });
          }
        });
      });
      return layout;
    }

    const nodeHalfWidth = Math.max(0.8, number(options.nodeWidth, horizontalGap * 0.48) / 2 + 0.35);
    const nodeHalfHeight = Math.max(0.8, number(options.nodeHeight, verticalGap * 0.52) / 2 + 0.35);
    const preferredRows = new Map();
    levelNumbers.forEach(depth => levels.get(depth).forEach((nodeId, row) => preferredRows.set(nodeId, row)));

    function segmentIntersectsNode(source, target, node) {
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      let start = 0;
      let end = 1;
      const boundaries = [
        [-dx, source.x - (node.x - nodeHalfWidth)],
        [dx, node.x + nodeHalfWidth - source.x],
        [-dy, source.y - (node.y - nodeHalfHeight)],
        [dy, node.y + nodeHalfHeight - source.y],
      ];
      for (const [direction, distance] of boundaries) {
        if (Math.abs(direction) < 1e-9) {
          if (distance < 0) return false;
          continue;
        }
        const ratio = distance / direction;
        if (direction < 0) {
          if (ratio > end) return false;
          start = Math.max(start, ratio);
        } else {
          if (ratio < start) return false;
          end = Math.min(end, ratio);
        }
      }
      return start <= end && end > 0.04 && start < 0.96;
    }

    function segmentsCross(left, right) {
      if ([left.edge.source, left.edge.target].some(nodeId => nodeId === right.edge.source || nodeId === right.edge.target)) return false;
      const orient = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
      const first = orient(left.source, left.target, right.source);
      const second = orient(left.source, left.target, right.target);
      const third = orient(right.source, right.target, left.source);
      const fourth = orient(right.source, right.target, left.target);
      return first * second < -1e-7 && third * fourth < -1e-7;
    }

    function layoutScore(layout) {
      const segments = rankEdges.map(edge => ({
        edge,
        source: layout.get(String(edge.source)),
        target: layout.get(String(edge.target)),
      })).filter(segment => segment.source && segment.target);
      let nodeIntersections = 0;
      for (const segment of segments) {
        for (const node of orderedNodes) {
          const nodeId = String(node.id);
          if (nodeId === String(segment.edge.source) || nodeId === String(segment.edge.target)) continue;
          const position = layout.get(nodeId);
          if (position && segmentIntersectsNode(segment.source, segment.target, position)) nodeIntersections += 1;
        }
      }
      let edgeCrossings = 0;
      for (let left = 0; left < segments.length; left += 1) {
        for (let right = left + 1; right < segments.length; right += 1) {
          if (segmentsCross(segments[left], segments[right])) edgeCrossings += 1;
        }
      }
      const verticalTravel = segments.reduce((total, segment) => total + Math.abs(segment.target.y - segment.source.y), 0);
      let displacement = 0;
      levelNumbers.forEach(depth => levels.get(depth).forEach((nodeId, row) => {
        displacement += Math.abs(row - preferredRows.get(nodeId));
      }));
      return edgeCrossings * 1e9 + nodeIntersections * 1e8 + displacement * 1e6 + verticalTravel * 100;
    }

    // A caller can provide a semantic order (creation order or a branch-first
    // traversal) that already expresses which subtrees belong together. Other
    // graph consumers may opt into unconstrained crossing-minimizing sifting by
    // omitting nodeOrder.
    if (!preserveSemanticOrder) {
      // Lane wrapping can put a long edge through a node in an earlier lane even
      // when the logical layer order is correct. Sift the small/medium layers
      // against the final wrapped coordinates for graph consumers without a
      // fixed semantic order.
      for (let pass = 0; pass < 10; pass += 1) {
        let improved = false;
        for (const depth of levelNumbers) {
          const group = levels.get(depth);
          if (group.length < 2 || group.length > 18) continue;
          const currentScore = layoutScore(buildLayout());
          let bestScore = currentScore;
          let bestOrder = null;
          for (let from = 0; from < group.length; from += 1) {
            for (let to = 0; to < group.length; to += 1) {
              if (from === to) continue;
              const candidate = [...group];
              const [nodeId] = candidate.splice(from, 1);
              candidate.splice(to, 0, nodeId);
              levels.set(depth, candidate);
              const candidateScore = layoutScore(buildLayout());
              if (candidateScore < bestScore) {
                bestScore = candidateScore;
                bestOrder = candidate;
              }
            }
          }
          levels.set(depth, bestOrder || group);
          if (bestOrder) improved = true;
        }
        if (!improved) break;
      }
    }
    return buildLayout();
  }

  function computeBranchAwareLayout(nodes = [], edges = [], options = {}) {
    const sourceNodes = (Array.isArray(nodes) ? nodes : [])
      .filter(node => String(node?.id || ""));
    if (!sourceNodes.length) return new Map();

    // Give the Leader its own column alongside the work it coordinates. Flow
    // direction chooses the side; adjacent objects choose its vertical anchor.
    // A detached top row would stretch every feedback path in a tall graph.
    const leaderId = String(options.leaderId || "");
    if (sourceNodes.length > 1 && sourceNodes.some(node => String(node.id) === leaderId)) {
      const workingNodes = sourceNodes.filter(node => String(node.id) !== leaderId);
      const workingEdges = (Array.isArray(edges) ? edges : []).filter(edge => (
        String(edge?.source || "") !== leaderId
        && String(edge?.target || "") !== leaderId
      ));
      const positions = computeBranchAwareLayout(workingNodes, workingEdges, {
        ...options,
        leaderId: "",
        rootId: options.rootId === leaderId ? "" : options.rootId,
        rootIds: (Array.isArray(options.rootIds) ? options.rootIds : [])
          .filter(nodeId => String(nodeId) !== leaderId),
      });
      const values = [...positions.values()];
      const incoming = new Set();
      const outgoing = new Set();
      const references = new Set();
      const feedbackTypes = new Set(options.feedbackEdgeTypes || []);
      (Array.isArray(edges) ? edges : []).forEach(edge => {
        const source = String(edge?.source || "");
        const target = String(edge?.target || "");
        const neighbor = source === leaderId ? target : target === leaderId ? source : "";
        if (!positions.has(neighbor)) return;
        if (feedbackTypes.has(String(edge?.type || ""))) {
          references.add(neighbor);
        } else {
          (target === leaderId ? incoming : outgoing).add(neighbor);
        }
      });
      const neighbors = new Set([...incoming, ...outgoing]);
      const anchorIds = neighbors.size ? neighbors : references;
      const anchorY = [...anchorIds].map(id => positions.get(id).y).sort((a, b) => a - b);
      const middle = Math.floor(anchorY.length / 2);
      // Median minimizes total vertical travel without distant, unconnected
      // components pulling the Leader away from its collaborators.
      const leaderY = anchorY.length
        ? (anchorY[middle] + anchorY[Math.floor((anchorY.length - 1) / 2)]) / 2
        : (Math.min(...values.map(position => position.y)) + Math.max(...values.map(position => position.y))) / 2;
      const horizontalGap = Math.max(6, number(options.horizontalGap, 28));
      const nodeWidth = Math.max(1.6, number(options.nodeWidth, horizontalGap * 0.48));
      const sideGap = Math.max(horizontalGap, nodeWidth + 3);
      const left = Math.min(...values.map(position => position.x));
      const right = Math.max(...values.map(position => position.x));
      const leaderX = incoming.size && incoming.size >= outgoing.size
        ? right + sideGap
        : left - sideGap;
      const shiftX = number(options.centerX, 50) - (Math.min(left, leaderX) + Math.max(right, leaderX)) / 2;
      positions.forEach(position => {
        position.x = Math.round((position.x + shiftX) * 100) / 100;
      });
      positions.set(leaderId, {
        x: Math.round((leaderX + shiftX) * 100) / 100,
        y: Math.round(leaderY * 100) / 100,
      });
      return positions;
    }

    const sourceOrder = new Map(sourceNodes.map((node, index) => [String(node.id), index]));
    const nodeById = new Map(sourceNodes.map(node => [String(node.id), node]));
    const finitePosition = (node, axis) => {
      const value = Number(node?.[axis]);
      return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
    };
    const createdTime = node => {
      const value = Date.parse(String(node?.created_at || ""));
      return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
    };
    const comparePosition = (leftId, rightId) => {
      const left = nodeById.get(leftId);
      const right = nodeById.get(rightId);
      const yDifference = finitePosition(left, "y") - finitePosition(right, "y");
      if (Number.isFinite(yDifference) && Math.abs(yDifference) > 0.01) return yDifference;
      const xDifference = finitePosition(left, "x") - finitePosition(right, "x");
      if (Number.isFinite(xDifference) && Math.abs(xDifference) > 0.01) return xDifference;
      const createdDifference = createdTime(left) - createdTime(right);
      if (Number.isFinite(createdDifference) && createdDifference) return createdDifference;
      return sourceOrder.get(leftId) - sourceOrder.get(rightId)
        || leftId.localeCompare(rightId);
    };

    const validEdges = (Array.isArray(edges) ? edges : []).filter(edge => {
      const source = String(edge?.source || "");
      const target = String(edge?.target || "");
      return source !== target
        && nodeById.has(source)
        && nodeById.has(target);
    });
    const preferredRootIds = [
      ...(Array.isArray(options.rootIds) ? options.rootIds : []),
      options.rootId,
    ].map(value => String(value || "")).filter(value => nodeById.has(value));

    // Independent HATG subgraphs are separate units of meaning. Laying all of
    // their roots into one global forest lets a small graph fill a contour gap
    // inside an unrelated large graph, and a convergent component falls back
    // to global ranks that interleave every component. Split by weak
    // connectivity first, lay out each component with the same topology rules,
    // then pack their complete bounds into stable reading rows. A short list
    // remains one vertical band; a long list wraps into a compact two-
    // dimensional block so isolated nodes cannot make the fitted camera tiny.
    // Feedback and reference wires still establish component membership even
    // when they are excluded from forward rank calculation below.
    if (options.packDisconnectedComponents !== false) {
      const adjacency = new Map(sourceNodes.map(node => [String(node.id), new Set()]));
      validEdges.forEach(edge => {
        const source = String(edge.source);
        const target = String(edge.target);
        adjacency.get(source).add(target);
        adjacency.get(target).add(source);
      });
      adjacency.forEach(neighbors => {
        const ordered = [...neighbors].sort(comparePosition);
        neighbors.clear();
        ordered.forEach(nodeId => neighbors.add(nodeId));
      });

      const unseen = new Set(sourceNodes.map(node => String(node.id)));
      const connectedComponents = [];
      sourceNodes.forEach(node => {
        const startId = String(node.id);
        if (!unseen.has(startId)) return;
        const component = [];
        const stack = [startId];
        unseen.delete(startId);
        while (stack.length) {
          const nodeId = stack.pop();
          component.push(nodeId);
          const neighbors = [...adjacency.get(nodeId)];
          for (let index = neighbors.length - 1; index >= 0; index -= 1) {
            const neighborId = neighbors[index];
            if (!unseen.has(neighborId)) continue;
            unseen.delete(neighborId);
            stack.push(neighborId);
          }
        }
        component.sort((left, right) => sourceOrder.get(left) - sourceOrder.get(right));
        connectedComponents.push(component);
      });

      if (connectedComponents.length > 1) {
        const preferredOrder = new Map(preferredRootIds.map((nodeId, index) => [nodeId, index]));
        const finiteAverage = (component, axis) => {
          const values = component
            .map(nodeId => finitePosition(nodeById.get(nodeId), axis))
            .filter(Number.isFinite);
          return values.length
            ? values.reduce((total, value) => total + value, 0) / values.length
            : Number.POSITIVE_INFINITY;
        };
        const componentAnchor = component => ({
          y: finiteAverage(component, "y"),
          x: finiteAverage(component, "x"),
          preferred: Math.min(
            Number.POSITIVE_INFINITY,
            ...component.map(nodeId => preferredOrder.get(nodeId) ?? Number.POSITIVE_INFINITY),
          ),
          source: Math.min(...component.map(nodeId => sourceOrder.get(nodeId))),
        });
        const compareFinite = (left, right) => {
          if (left === right) return 0;
          if (!Number.isFinite(left)) return 1;
          if (!Number.isFinite(right)) return -1;
          return left - right;
        };
        connectedComponents.sort((left, right) => {
          const leftAnchor = componentAnchor(left);
          const rightAnchor = componentAnchor(right);
          return compareFinite(leftAnchor.y, rightAnchor.y)
            || compareFinite(leftAnchor.x, rightAnchor.x)
            || compareFinite(leftAnchor.preferred, rightAnchor.preferred)
            || leftAnchor.source - rightAnchor.source;
        });

        const horizontalGap = Math.max(6, number(options.horizontalGap, 28));
        const verticalGap = Math.max(6, number(options.verticalGap, 16));
        const nodeWidth = Math.max(1.6, number(options.nodeWidth, horizontalGap * 0.48));
        const nodeHeight = Math.max(1.6, number(options.nodeHeight, verticalGap * 0.52));
        const componentGap = Math.max(
          3,
          number(options.componentGap, Math.max(verticalGap * 0.72, nodeHeight * 0.55)),
        );
        const centerX = number(options.centerX, 50);
        const centerY = number(options.centerY, 50);
        const nodeHalfWidth = nodeWidth / 2;
        const nodeHalfHeight = nodeHeight / 2;
        const componentLayouts = connectedComponents.map(component => {
          const componentIds = new Set(component);
          const componentNodes = component.map(nodeId => nodeById.get(nodeId));
          const componentEdges = validEdges.filter(edge => (
            componentIds.has(String(edge.source))
            && componentIds.has(String(edge.target))
          ));
          const componentRoots = preferredRootIds.filter(nodeId => componentIds.has(nodeId));
          const positions = computeBranchAwareLayout(componentNodes, componentEdges, {
            ...options,
            centerX: 0,
            centerY: 0,
            rootId: "",
            rootIds: componentRoots,
            packDisconnectedComponents: false,
          });
          const values = component.map(nodeId => positions.get(nodeId) || { x: 0, y: 0 });
          return {
            component,
            positions,
            bounds: {
              left: Math.min(...values.map(position => position.x - nodeHalfWidth)),
              right: Math.max(...values.map(position => position.x + nodeHalfWidth)),
              top: Math.min(...values.map(position => position.y - nodeHalfHeight)),
              bottom: Math.max(...values.map(position => position.y + nodeHalfHeight)),
            },
          };
        });

        const columnGap = Math.max(
          3,
          number(options.componentColumnGap, Math.max(horizontalGap * 0.5, nodeWidth * 0.55)),
        );
        const comfortableSpan = Math.max(
          nodeHeight + componentGap * 2,
          number(options.componentWrapSpan, 78),
        );
        const verticalStackSpan = componentLayouts.reduce((total, layout, index) => (
          total
          + layout.bounds.bottom - layout.bounds.top
          + (index ? componentGap : 0)
        ), 0);

        function packInRows(columnCount) {
          const rows = [];
          for (let index = 0; index < componentLayouts.length; index += columnCount) {
            const items = componentLayouts.slice(index, index + columnCount);
            const widths = items.map(({ bounds }) => bounds.right - bounds.left);
            const heights = items.map(({ bounds }) => bounds.bottom - bounds.top);
            rows.push({
              items,
              width: widths.reduce((total, width) => total + width, 0)
                + columnGap * Math.max(0, items.length - 1),
              height: Math.max(...heights),
            });
          }
          const width = Math.max(...rows.map(row => row.width));
          const height = rows.reduce((total, row, index) => (
            total + row.height + (index ? componentGap : 0)
          ), 0);
          const placements = [];
          let rowTop = 0;
          rows.forEach(row => {
            let itemLeft = (width - row.width) / 2;
            row.items.forEach(layout => {
              const itemWidth = layout.bounds.right - layout.bounds.left;
              const itemHeight = layout.bounds.bottom - layout.bounds.top;
              placements.push({
                layout,
                xOffset: itemLeft - layout.bounds.left,
                yOffset: rowTop + (row.height - itemHeight) / 2 - layout.bounds.top,
              });
              itemLeft += itemWidth + columnGap;
            });
            rowTop += row.height + componentGap;
          });
          return { width, height, placements };
        }

        let packedLayout = packInRows(1);
        if (verticalStackSpan > comfortableSpan) {
          const aspectRatio = Math.max(0.1, number(options.componentAspectRatio, 1));
          let bestScore = Math.max(
            packedLayout.width / (comfortableSpan * aspectRatio),
            packedLayout.height / comfortableSpan,
          );
          for (let columnCount = 2; columnCount <= componentLayouts.length; columnCount += 1) {
            const candidate = packInRows(columnCount);
            const score = Math.max(
              candidate.width / (comfortableSpan * aspectRatio),
              candidate.height / comfortableSpan,
            );
            if (score >= bestScore) continue;
            bestScore = score;
            packedLayout = candidate;
          }
        }

        const packed = new Map();
        const horizontalOffset = centerX - packedLayout.width / 2;
        const verticalOffset = centerY - packedLayout.height / 2;
        packedLayout.placements.forEach(({ layout, xOffset, yOffset }) => {
          layout.component.forEach(nodeId => {
            const position = layout.positions.get(nodeId) || { x: 0, y: 0 };
            packed.set(nodeId, {
              x: Math.round((position.x + xOffset + horizontalOffset) * 100) / 100,
              y: Math.round((position.y + yOffset + verticalOffset) * 100) / 100,
            });
          });
        });
        return packed;
      }
    }

    const feedbackTypes = new Set(options.feedbackEdgeTypes || []);
    const edgeTypePriority = {
      inheritance: 0,
      event: 1,
      packet: 3,
      target: 4,
      handoff: 8,
      absorption: 10,
      ...(options.edgeTypePriority || {}),
    };
    const rankableEdges = validEdges.filter(
      edge => !feedbackTypes.has(String(edge?.type || "")),
    );
    const outgoing = new Map(sourceNodes.map(node => [String(node.id), []]));
    const incomingCount = new Map(sourceNodes.map(node => [String(node.id), 0]));
    rankableEdges.forEach((edge, edgeIndex) => {
      const source = String(edge.source);
      const target = String(edge.target);
      outgoing.get(source).push({ edge, edgeIndex, target });
      incomingCount.set(target, incomingCount.get(target) + 1);
    });
    outgoing.forEach(items => items.sort((left, right) => {
      const priorityDifference = number(
        edgeTypePriority[String(left.edge?.type || "")],
        100,
      ) - number(
        edgeTypePriority[String(right.edge?.type || "")],
        100,
      );
      return priorityDifference
        || comparePosition(left.target, right.target)
        || left.edgeIndex - right.edgeIndex;
    }));

    // The previous Lineage layout wrapped one busy logical rank into several
    // visual columns. Descendants still advanced from the logical rank, so a
    // large fan-out interleaved unrelated branches and produced long crossings.
    // Build a stable branch-first source order instead: explicit roots first,
    // then zero-indegree roots, while each branch remains contiguous.
    const preferredRoots = preferredRootIds;
    const positionOrder = [...nodeById.keys()].sort(comparePosition);
    const rootOrder = [];
    const rootSet = new Set();
    const rememberRoot = nodeId => {
      if (!nodeId || rootSet.has(nodeId)) return;
      rootSet.add(nodeId);
      rootOrder.push(nodeId);
    };
    preferredRoots.forEach(rememberRoot);
    positionOrder
      .filter(nodeId => incomingCount.get(nodeId) === 0)
      .forEach(rememberRoot);
    positionOrder.forEach(rememberRoot);

    const visited = new Set();
    const branchOrder = [];
    rootOrder.forEach(rootId => {
      if (visited.has(rootId)) return;
      const stack = [rootId];
      while (stack.length) {
        const nodeId = stack.pop();
        if (visited.has(nodeId)) continue;
        visited.add(nodeId);
        branchOrder.push(nodeId);
        const children = [];
        const childSet = new Set();
        outgoing.get(nodeId).forEach(item => {
          if (visited.has(item.target) || childSet.has(item.target)) return;
          childSet.add(item.target);
          children.push(item.target);
        });
        for (let index = children.length - 1; index >= 0; index -= 1) {
          stack.push(children[index]);
        }
      }
    });

    const orderedNodes = branchOrder.map(nodeId => nodeById.get(nodeId));
    const branchIndex = new Map(branchOrder.map((nodeId, index) => [nodeId, index]));
    const structuralOutgoing = new Map(sourceNodes.map(node => [String(node.id), new Set()]));
    const structuralIncoming = new Map(sourceNodes.map(node => [String(node.id), new Set()]));
    rankableEdges.forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      structuralOutgoing.get(source).add(target);
      structuralIncoming.get(target).add(source);
    });
    const structuralIndegree = new Map(
      [...structuralIncoming].map(([nodeId, parents]) => [nodeId, parents.size]),
    );
    const acyclicQueue = branchOrder.filter(nodeId => structuralIndegree.get(nodeId) === 0);
    let acyclicNodeCount = 0;
    for (let cursor = 0; cursor < acyclicQueue.length; cursor += 1) {
      const nodeId = acyclicQueue[cursor];
      acyclicNodeCount += 1;
      structuralOutgoing.get(nodeId).forEach(targetId => {
        structuralIndegree.set(targetId, structuralIndegree.get(targetId) - 1);
        if (structuralIndegree.get(targetId) === 0) acyclicQueue.push(targetId);
      });
    }
    const isForest = (
      sourceNodes.length <= 400
      && acyclicNodeCount === sourceNodes.length
      && [...structuralIncoming.values()].every(parents => parents.size <= 1)
    );

    if (isForest) {
      const horizontalGap = Math.max(6, number(options.horizontalGap, 28));
      const verticalGap = Math.max(6, number(options.verticalGap, 16));
      const componentGap = Math.max(verticalGap, number(options.componentGap, verticalGap * 1.35));
      const centerX = number(options.centerX, 50);
      const centerY = number(options.centerY, 50);
      const childrenByNode = new Map(sourceNodes.map(node => [String(node.id), []]));
      structuralOutgoing.forEach((targets, sourceId) => {
        childrenByNode.set(
          sourceId,
          [...targets].sort((left, right) => (
            branchIndex.get(left) - branchIndex.get(right)
          )),
        );
      });

      // A contour-packed tidy tree keeps every branch in one continuous
      // vertical band. Unary chains reuse the same row, while sibling subtrees
      // only consume extra height at depths where their nodes could collide.
      function layoutTree(nodeId) {
        const childTrees = childrenByNode.get(nodeId).map(layoutTree);
        if (!childTrees.length) {
          return {
            positions: new Map([[nodeId, { depth: 0, y: 0 }]]),
            top: [0],
            bottom: [0],
          };
        }

        const packedTop = [];
        const packedBottom = [];
        const packedChildren = [];
        childTrees.forEach((child, index) => {
          let offset = 0;
          if (index) {
            const overlapDepth = Math.min(packedBottom.length, child.top.length);
            for (let depth = 0; depth < overlapDepth; depth += 1) {
              offset = Math.max(
                offset,
                packedBottom[depth] + verticalGap - child.top[depth],
              );
            }
          }
          packedChildren.push({ child, offset });
          child.top.forEach((value, depth) => {
            const shifted = value + offset;
            packedTop[depth] = packedTop[depth] === undefined
              ? shifted
              : Math.min(packedTop[depth], shifted);
          });
          child.bottom.forEach((value, depth) => {
            const shifted = value + offset;
            packedBottom[depth] = packedBottom[depth] === undefined
              ? shifted
              : Math.max(packedBottom[depth], shifted);
          });
        });

        const parentY = (
          packedChildren[0].offset
          + packedChildren[packedChildren.length - 1].offset
        ) / 2;
        const positions = new Map([[nodeId, { depth: 0, y: 0 }]]);
        packedChildren.forEach(({ child, offset }) => {
          child.positions.forEach((position, childId) => {
            positions.set(childId, {
              depth: position.depth + 1,
              y: position.y + offset - parentY,
            });
          });
        });
        return {
          positions,
          top: [0, ...packedTop.map(value => value - parentY)],
          bottom: [0, ...packedBottom.map(value => value - parentY)],
        };
      }

      const forestRoots = branchOrder.filter(
        nodeId => structuralIncoming.get(nodeId).size === 0,
      );
      const rootTrees = forestRoots.map(layoutTree);
      const forestPositions = new Map();
      const forestTop = [];
      const forestBottom = [];
      rootTrees.forEach((tree, index) => {
        let offset = 0;
        if (index) {
          const overlapDepth = Math.min(forestBottom.length, tree.top.length);
          for (let depth = 0; depth < overlapDepth; depth += 1) {
            offset = Math.max(
              offset,
              forestBottom[depth] + componentGap - tree.top[depth],
            );
          }
        }
        tree.positions.forEach((position, nodeId) => {
          forestPositions.set(nodeId, {
            depth: position.depth,
            y: position.y + offset,
          });
        });
        tree.top.forEach((value, depth) => {
          const shifted = value + offset;
          forestTop[depth] = forestTop[depth] === undefined
            ? shifted
            : Math.min(forestTop[depth], shifted);
        });
        tree.bottom.forEach((value, depth) => {
          const shifted = value + offset;
          forestBottom[depth] = forestBottom[depth] === undefined
            ? shifted
            : Math.max(forestBottom[depth], shifted);
        });
      });

      const positions = [...forestPositions.values()];
      const maximumDepth = Math.max(0, ...positions.map(position => position.depth));
      const minimumY = Math.min(0, ...positions.map(position => position.y));
      const maximumY = Math.max(0, ...positions.map(position => position.y));
      const verticalCenter = (minimumY + maximumY) / 2;
      return new Map([...forestPositions].map(([nodeId, position]) => [
        nodeId,
        {
          x: Math.round((
            centerX + (position.depth - maximumDepth / 2) * horizontalGap
          ) * 100) / 100,
          y: Math.round((centerY + position.y - verticalCenter) * 100) / 100,
        },
      ]));
    }

    return computeLayeredLayout(orderedNodes, rankableEdges, {
      ...options,
      // One semantic depth always owns one visual column. A tall graph can be
      // fitted by the camera without sacrificing the topology's readability.
      maxRowsPerLane: Number.POSITIVE_INFINITY,
      // Convergent DAGs have secondary parents rather than independent
      // subtrees. Start from branch order, then permit crossing minimization.
      nodeOrder: undefined,
    });
  }

  window.PortalHatgGraphLayout = Object.freeze({
    computeBranchAwareLayout,
    computeWorkspaceGraphLayout,
    // The world the layouts fill; a graph uses it unless its options size the world.
    defaultWorldSize: Object.freeze({ width: DEFAULT_WORLD_WIDTH, height: DEFAULT_WORLD_HEIGHT }),
  });
})();
