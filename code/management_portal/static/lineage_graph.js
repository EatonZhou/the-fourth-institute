(function () {
  "use strict";

  const graphGeometry = window.PortalGraphGeometry;
  if (!graphGeometry) throw new Error("Portal graph geometry must load before LineageGraph");
  const { sampledRoute, routeLength, routePath } = graphGeometry;

  const ACTIVE_STATUSES = new Set(["queued", "running", "in_progress"]);
  const DEFAULT_SCALE_EXTENT = [0.35, 3.2];
  const DEFAULT_WORLD_WIDTH = 1280;
  const DEFAULT_WORLD_HEIGHT = 900;
  const DRAG_AUTOPAN_MARGIN = 96;
  const DRAG_AUTOPAN_MAX_SPEED = 14;
  const QUICK_FORK_PORT_CLEARANCE_PX = 24;
  const PROGRAM_WIRE_TYPES = new Set(["route", "target", "watch"]);
  const PROGRAM_PORT_TYPES = new Set([
    ...PROGRAM_WIRE_TYPES,
    "invoke",
  ]);
  const HATG_PRIMITIVE_TRANSFER_TYPE = window.PortalCompatibility.identifier("primitive_transfer");
  const SEMANTIC_ZOOM = Object.freeze({
    overview: { scale: 0.52, title: window.PortalI18n.t("总览镜头"), hint: window.PortalI18n.t("拓扑、状态与实时活动流") },
    operate: { scale: 1, title: window.PortalI18n.t("操作镜头"), hint: window.PortalI18n.t("目标、状态与协作关系") },
    inspect: { scale: 1.62, title: window.PortalI18n.t("检视镜头"), hint: window.PortalI18n.t("完整节点上下文与产物线索") },
  });

  function element(value) {
    return typeof value === "string" ? document.querySelector(value) : value;
  }

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
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

  const isWorkspaceNode = node => ["workspace", "workspace_resident"].includes(node?.kind);

  function workspaceNodeContent(node) {
    const escape = value => String(value || "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
    if (node.kind === "workspace_resident") {
      return `<span class="hatgWorkspaceResidentDot" aria-hidden="true"></span><span class="hatgWorkspaceResidentLabel"><strong>${escape(node.title || node.id)}</strong><span class="hatgWorkspaceNodeId">${escape(node.resident_id || node.id)}</span></span>`;
    }
    return `<span class="hatgWorkspaceNodeKind">Workspace</span><strong>${escape(node.title || node.workspace_id)}</strong><span class="hatgWorkspacePath">${escape(node.workspace_path)}</span><span class="hatgWorkspaceNodeId">${escape(node.workspace_id)}</span>`;
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
      width: Math.max(320, number(options.worldWidth, DEFAULT_WORLD_WIDTH)),
      height: Math.max(240, number(options.worldHeight, DEFAULT_WORLD_HEIGHT)),
    };
    root.style.setProperty("--hatg-world-width", `${worldSize.width}px`);
    root.style.setProperty("--hatg-world-height", `${worldSize.height}px`);
    root.dataset.worldWidth = String(worldSize.width);
    root.dataset.worldHeight = String(worldSize.height);
    world.dataset.coordinateSpace = "fixed";
    const rootSelection = d3.select(root);
    const selectionMarquee = document.createElement("div");
    selectionMarquee.className = "hatgSelectionMarquee";
    selectionMarquee.dataset.testid = "hatg-selection-marquee";
    selectionMarquee.hidden = true;
    selectionMarquee.setAttribute("aria-hidden", "true");
    root.appendChild(selectionMarquee);
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
    let contextId = "";
    const pendingNodeClickTimers = new Map();
    function cancelPendingNodeClick(nodeId) {
      window.clearTimeout(pendingNodeClickTimers.get(nodeId)?.timer);
      pendingNodeClickTimers.delete(nodeId);
    }
    function dispatchNodeClick(node, event, callback, hasDoubleClick) {
      if (!nodeDoubleClickDelay || !hasDoubleClick) return callback?.(node, event);
      const nodeId = String(node.id || "");
      cancelPendingNodeClick(nodeId);
      const pending = { run: () => callback?.(node, event) };
      pending.timer = window.setTimeout(() => {
        if (pendingNodeClickTimers.get(nodeId) === pending) pendingNodeClickTimers.delete(nodeId);
        pending.run();
      }, nodeDoubleClickDelay);
      pendingNodeClickTimers.set(nodeId, pending);
    }
    function flushPendingNodeClicks(keepNodeId = "") {
      for (const [nodeId, pending] of [...pendingNodeClickTimers]) {
        if (nodeId === keepNodeId) continue;
        window.clearTimeout(pending.timer);
        pendingNodeClickTimers.delete(nodeId);
        pending.run();
      }
    }
    function settleNodeSelection(event) {
      if (!pendingNodeClickTimers.size) return;
      // A click elsewhere commits the preceding single click before a toolbar
      // action reads the selection. Only a second click on the same card can
      // still become its double-click action.
      const card = event.target?.closest?.(".hatgNode");
      flushPendingNodeClicks(nodeLayer.contains(card) ? String(card.dataset.nodeId || "") : "");
    }
    root.ownerDocument.addEventListener("click", settleNodeSelection, true);
    let quickForkMenu = null;
    let quickForkPopover = null;
    let quickForkSourceId = "";
    let quickForkCloseTimer = 0;
    let transform = d3.zoomIdentity;
    let model = {
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
    };
    let nodeMap = new Map();
    let portsByNodeId = new Map();
    let parallelEdgeLaneMap = new Map();
    let nodeExtentMap = new Map();
    let edgeGeometryMap = new Map();
    let geometryInputSignature = "";
    let minimapRenderState = null;
    let dragState = null;
    let marqueeState = null;
    let suppressBackgroundClick = false;
    let resizeFrame = 0;
    let viewportMoving = false;
    let zoomDetailFrame = 0;
    let focusAnimation = null;
    let navigationSequence = 0;
    let viewportSize = {
      width: Math.max(1, root.clientWidth),
      height: Math.max(1, root.clientHeight),
    };
    let centerOnNextRender = false;
    let pendingViewportFit = null;
    let minimapDomain = { minX: 0, maxX: 100, minY: 0, maxY: 100 };
    let positionElementsByNode = new Map();
    let primaryNodeElementsById = new Map();
    let portElementsByNode = new Map();
    let ownerChromeElementsByNode = new Map();
    let incidentEdgeIdsByNode = new Map();
    let edgeById = new Map();
    let edgePathElementsById = new Map();
    let edgeLabelElementsById = new Map();
    let minimapNodeElementsById = new Map();
    let minimapEdgeElementsById = new Map();
    let semanticZoomLevel = "";
    let hoveredPortNodeId = "";
    let focusedPortNodeId = "";
    let syncedHoveredPortNodeId = "";
    let syncedFocusedPortNodeId = "";
    let syncedSelectedPortNodeIds = new Set();
    let portHoverClearTimer = 0;

    function interactiveNodeId(target) {
      if (!(target instanceof Element)) return "";
      const action = target.closest("button[data-action-node-id]");
      if (action) return String(action.dataset.actionNodeId || "");
      const port = target.closest("button.hatgPort[data-port-node-id]");
      if (port) return String(port.dataset.portNodeId || "");
      const node = target.closest(".hatgNode[data-node-id]");
      return String(node?.dataset.nodeId || "");
    }

    function syncPortOwnerVisibility({ full = false } = {}) {
      if (hoveredPortNodeId && !nodeMap.has(hoveredPortNodeId)) {
        hoveredPortNodeId = "";
      }
      if (focusedPortNodeId && !nodeMap.has(focusedPortNodeId)) {
        focusedPortNodeId = "";
      }
      if (hoveredPortNodeId) root.dataset.portHoverNodeId = hoveredPortNodeId;
      else delete root.dataset.portHoverNodeId;
      if (focusedPortNodeId) root.dataset.portFocusNodeId = focusedPortNodeId;
      else delete root.dataset.portFocusNodeId;
      const selectedNodeIds = new Set([
        ...(model.selectedNodeIds || []),
        ...(model.selectedNodeId ? [model.selectedNodeId] : []),
      ]);
      const affectedNodeIds = new Set();
      if (full) {
        nodeMap.forEach((_node, nodeId) => affectedNodeIds.add(nodeId));
      } else {
        [
          syncedHoveredPortNodeId,
          hoveredPortNodeId,
          syncedFocusedPortNodeId,
          focusedPortNodeId,
        ].filter(Boolean).forEach(nodeId => affectedNodeIds.add(nodeId));
        syncedSelectedPortNodeIds.forEach(nodeId => {
          if (!selectedNodeIds.has(nodeId)) affectedNodeIds.add(nodeId);
        });
        selectedNodeIds.forEach(nodeId => {
          if (!syncedSelectedPortNodeIds.has(nodeId)) affectedNodeIds.add(nodeId);
        });
      }
      affectedNodeIds.forEach(nodeId => {
        const hovered = nodeId === hoveredPortNodeId;
        const focused = nodeId === focusedPortNodeId;
        const selected = selectedNodeIds.has(nodeId);
        (portElementsByNode.get(nodeId) || []).forEach(port => {
          port.toggleAttribute("data-port-owner-hovered", hovered);
          port.toggleAttribute("data-port-owner-focused", focused);
          port.toggleAttribute("data-port-owner-selected", selected);
          if (hovered) port.dataset.portOwnerHovered = "true";
          if (focused) port.dataset.portOwnerFocused = "true";
          if (selected) port.dataset.portOwnerSelected = "true";
        });
        (ownerChromeElementsByNode.get(nodeId) || []).forEach(action => {
          action.toggleAttribute("data-node-owner-hovered", hovered);
          action.toggleAttribute("data-node-owner-focused", focused);
          if (hovered) action.dataset.nodeOwnerHovered = "true";
          if (focused) action.dataset.nodeOwnerFocused = "true";
        });
      });
      syncedHoveredPortNodeId = hoveredPortNodeId;
      syncedFocusedPortNodeId = focusedPortNodeId;
      syncedSelectedPortNodeIds = selectedNodeIds;
    }

    function revealHoveredPorts(nodeId) {
      window.clearTimeout(portHoverClearTimer);
      portHoverClearTimer = 0;
      hoveredPortNodeId = String(nodeId || "");
      syncPortOwnerVisibility();
    }

    function concealHoveredPorts(nodeId, relatedTarget) {
      if (interactiveNodeId(relatedTarget) === String(nodeId || "")) return;
      window.clearTimeout(portHoverClearTimer);
      portHoverClearTimer = window.setTimeout(() => {
        if (hoveredPortNodeId !== String(nodeId || "")) return;
        hoveredPortNodeId = "";
        syncPortOwnerVisibility();
      }, 240);
    }

    function revealFocusedPorts(nodeId) {
      focusedPortNodeId = String(nodeId || "");
      syncPortOwnerVisibility();
    }

    function concealFocusedPorts(nodeId, relatedTarget) {
      if (interactiveNodeId(relatedTarget) === String(nodeId || "")) return;
      if (focusedPortNodeId !== String(nodeId || "")) return;
      focusedPortNodeId = "";
      syncPortOwnerVisibility();
    }

    function currentViewportSize() {
      return {
        width: Math.max(1, root.clientWidth),
        height: Math.max(1, root.clientHeight),
      };
    }

    function currentViewportInsets(viewport = currentViewportSize()) {
      let configured = {};
      try {
        configured = typeof options.viewportInsets === "function"
          ? options.viewportInsets()
          : options.viewportInsets || {};
      } catch (_error) {
        configured = {};
      }
      // The engine owns the bottom minimap. Fit/focus must keep cards above
      // its actual footprint, including CSS sizing and responsive placement.
      // Do not reserve space when it is hidden or outside this graph root.
      if (minimap && !minimap.hasAttribute("hidden") && minimap.getClientRects().length) {
        const canvasBounds = root.getBoundingClientRect();
        const minimapBounds = minimap.getBoundingClientRect();
        if (minimapBounds.left < canvasBounds.right && minimapBounds.right > canvasBounds.left
          && minimapBounds.top < canvasBounds.bottom && minimapBounds.bottom > canvasBounds.top) {
          configured = { ...configured, bottom: Math.max(number(configured?.bottom, 0),
            canvasBounds.bottom - minimapBounds.top + 10) };
        }
      }
      // An overlay can cover most of a narrow canvas. Preserve its actual
      // occlusion; only constrain opposing insets when they leave no space.
      return graphGeometry.viewportInsets(configured, viewport);
    }

    function viewCenter(viewTransform = transform, viewport = viewportSize) {
      return graphGeometry.viewCenter(viewTransform, viewport, worldSize);
    }

    function visibleWorldBounds(
      viewTransform = transform,
      viewport = currentViewportSize(),
      insets = { top: 0, right: 0, bottom: 0, left: 0 },
    ) {
      return graphGeometry.visibleWorldBounds(viewTransform, viewport, worldSize, insets);
    }

    function transformForCenter(center, scale = 1, viewport = viewportSize) {
      const next = graphGeometry.transformForCenter(center, scale, viewport, worldSize, scaleExtent);
      return d3.zoomIdentity.translate(next.x, next.y).scale(next.k);
    }

    function nodeStatus(node) {
      if (isWorkspaceNode(node)) return "idle";
      // Read-only projections own their displayed status instead of following
      // the live Resident runtime resolver.
      if (model.readOnly) return String(node?.status || "idle");
      return String(options.statusForNode?.(node) || node?.status || "idle");
    }

    const primitiveNodeKind = node => isWorkspaceNode(node) ? node.kind : window.PortalLineageProgram?.primitiveKind(node) || "agent";
    const nodeContent = node => isWorkspaceNode(node) ? workspaceNodeContent(node) : String(options.nodeContent?.(node, nodeStatus(node)) || "");

    function nodeHasQuickFork(node) {
      return (
        !model.readOnly
        && !isWorkspaceNode(node)
        && !node._family_facade
        && typeof options.onNodeQuickFork === "function"
        && primitiveNodeKind(node) === "agent"
      );
    }

    function closeQuickForkMenu() {
      window.clearTimeout(quickForkCloseTimer);
      quickForkPopover?.destroy();
      quickForkPopover = null;
      quickForkMenu?.remove();
      quickForkMenu = null;
      quickForkSourceId = "";
    }

    function scheduleQuickForkClose(event) {
      if (quickForkMenu?.contains(event.relatedTarget)
        || quickForkPopover?.trigger?.contains(event.relatedTarget)) return;
      window.clearTimeout(quickForkCloseTimer);
      quickForkCloseTimer = window.setTimeout(() => {
        if (quickForkMenu?.contains(document.activeElement)) return;
        closeQuickForkMenu();
      }, 220);
    }

    function invokeQuickFork(sourceId, event, mode) {
      event.preventDefault();
      event.stopPropagation();
      const source = nodeMap.get(sourceId);
      closeQuickForkMenu();
      if (!source || !nodeHasQuickFork(source) || model.quickForkNodeId) return;
      options.onNodeQuickFork?.(source, event, mode);
    }

    // Dragging a Resident's “+” onto a Seed makes that Resident the Seed's
    // candidate fork (decision_4adcc1ff2f2c); a click on “+” still forks.
    const CANDIDATE_FORK_DRAG_DISTANCE = 6;
    let candidateForkDrag = null;
    let suppressQuickForkClick = false;

    function candidateForkDropTarget(event) {
      const element = document.elementFromPoint(event.clientX, event.clientY);
      const seed = element?.closest?.('button.hatgNode[data-node-kind="seed"][data-node-id]');
      return seed && root.contains(seed) ? seed : null;
    }

    function endCandidateForkDrag() {
      if (!candidateForkDrag) return;
      candidateForkDrag.target?.classList.remove("candidateForkDropTarget");
      candidateForkDrag.overlay?.remove();
      root.classList.remove("isCandidateForkDragging");
      candidateForkDrag = null;
    }

    function onCandidateForkPointerDown(event, node) {
      if (
        event.button !== 0
        || event.pointerType === "touch"
        || model.readOnly
        || model.quickForkNodeId
        || typeof options.onCandidateForkDrop !== "function"
      ) return;
      endCandidateForkDrag();
      candidateForkDrag = {
        sourceId: node.id,
        pointerId: event.pointerId,
        trigger: event.currentTarget,
        startX: event.clientX,
        startY: event.clientY,
        active: false,
        overlay: null,
        line: null,
        target: null,
      };
    }

    function onCandidateForkPointerMove(event) {
      const drag = candidateForkDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (!drag.active) {
        if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < CANDIDATE_FORK_DRAG_DISTANCE) return;
        drag.active = true;
        closeQuickForkMenu();
        // The HATG styles apply inside an overlay component root.
        const svgNamespace = "http://www.w3.org/2000/svg";
        drag.overlay = document.createElement("div");
        drag.overlay.className = "portalHatgOverlayComponent";
        const svg = document.createElementNS(svgNamespace, "svg");
        svg.setAttribute("class", "hatgCandidateForkDraft");
        svg.setAttribute("aria-hidden", "true");
        drag.line = document.createElementNS(svgNamespace, "line");
        svg.appendChild(drag.line);
        drag.overlay.appendChild(svg);
        document.body.appendChild(drag.overlay);
        root.classList.add("isCandidateForkDragging");
      }
      event.preventDefault();
      const box = drag.trigger.getBoundingClientRect();
      drag.line.setAttribute("x1", String(box.left + box.width / 2));
      drag.line.setAttribute("y1", String(box.top + box.height / 2));
      drag.line.setAttribute("x2", String(event.clientX));
      drag.line.setAttribute("y2", String(event.clientY));
      const target = candidateForkDropTarget(event);
      if (target !== drag.target) drag.target?.classList.remove("candidateForkDropTarget");
      target?.classList.add("candidateForkDropTarget");
      drag.target = target;
    }

    function onCandidateForkPointerUp(event) {
      const drag = candidateForkDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const target = drag.active ? candidateForkDropTarget(event) : null;
      const source = nodeMap.get(drag.sourceId);
      const active = drag.active;
      endCandidateForkDrag();
      if (!active) return;
      // The click that ends this drag is not a Fork.
      suppressQuickForkClick = true;
      window.setTimeout(() => { suppressQuickForkClick = false; }, 0);
      if (source && target?.dataset.nodeId) options.onCandidateForkDrop?.(source, target.dataset.nodeId, event);
    }

    function onCandidateForkPointerCancel(event) {
      if (candidateForkDrag && event.pointerId === candidateForkDrag.pointerId) endCandidateForkDrag();
    }

    function openQuickForkMenu(node, event, { focus = false, last = false } = {}) {
      if (!nodeHasQuickFork(node) || model.quickForkNodeId) return;
      const trigger = event.currentTarget;
      if (quickForkSourceId !== node.id || !quickForkPopover?.isOpen()) {
        closeQuickForkMenu();
        const modes = options.nodeQuickForkModes?.(node) || [];
        if (!modes.length) return;
        quickForkSourceId = node.id;
        const panel = document.createElement("div");
        panel.className = "portalHatgOverlayComponent hatgQuickForkMenu";
        panel.setAttribute("role", "menu");
        panel.setAttribute("aria-label", window.PortalI18n.text`从「${node.title || node.id}」Fork`);
        const heading = document.createElement("div");
        heading.className = "hatgQuickForkMenuHeading";
        heading.textContent = window.PortalI18n.t("选择 Fork 方式");
        panel.appendChild(heading);
        for (const mode of modes) {
          const button = document.createElement("button");
          button.type = "button";
          button.setAttribute("role", "menuitem");
          button.dataset.forkMode = mode.value;
          button.dataset.portalTooltipOptOut = "1";
          button.setAttribute("aria-label", mode.label);
          const label = document.createElement("strong");
          label.textContent = mode.label;
          const hint = document.createElement("small");
          hint.textContent = mode.description;
          button.append(label, hint);
          button.addEventListener("click", actionEvent => {
            invokeQuickFork(node.id, actionEvent, mode.value);
          });
          panel.appendChild(button);
        }
        panel.addEventListener("pointerenter", () => window.clearTimeout(quickForkCloseTimer));
        panel.addEventListener("pointerleave", scheduleQuickForkClose);
        panel.addEventListener("focusout", scheduleQuickForkClose);
        panel.addEventListener("keydown", keyEvent => {
          const buttons = Array.from(panel.querySelectorAll('[role="menuitem"]'));
          const index = buttons.indexOf(document.activeElement);
          const target = {
            ArrowDown: (index + 1) % buttons.length,
            ArrowUp: (index - 1 + buttons.length) % buttons.length,
            Home: 0,
            End: buttons.length - 1,
          }[keyEvent.key];
          if (target === undefined) return;
          keyEvent.preventDefault();
          buttons[target].focus();
        });
        quickForkMenu = panel;
        root.appendChild(panel);
        quickForkPopover = window.PortalOverlay.bindPopover({
          trigger, panel, portal: true, placement: "bottom-start", gap: 8,
          onClose: () => { panel.remove(); },
        });
        quickForkPopover.open(event);
      }
      window.clearTimeout(quickForkCloseTimer);
      if (focus) {
        const buttons = quickForkMenu.querySelectorAll('[role="menuitem"]');
        buttons[last ? buttons.length - 1 : 0]?.focus();
      }
    }

    function isProgramWire(edge) {
      return !!(edge?._program_wire);
    }

    function normalizePort(node, port, index = 0) {
      const nodeId = String(node?.id || "");
      const nodeKind = String(port?.node_kind || primitiveNodeKind(node));
      const portId = String(port?.port_id || port?.id || "");
      const type = String(port?.type || "");
      const direction = String(port?.direction || "");
      return {
        ...port,
        key: String(port?.key || `${nodeId}:${direction}:${portId}:${index}`),
        node_id: nodeId,
        node_kind: nodeKind,
        port_id: portId,
        id: portId,
        type,
        direction,
        label: String(port?.label || type || portId),
      };
    }

    function portsForNode(node) {
      if (isWorkspaceNode(node)) return [];
      const nodeId = String(node?.id || "");
      if (nodeId && portsByNodeId.has(nodeId)) {
        return portsByNodeId.get(nodeId);
      }
      let ports = [];
      try {
        ports = typeof options.portsForNode === "function"
          ? options.portsForNode(node)
          : node?.ports;
      } catch (_error) {
        ports = [];
      }
      const normalized = (Array.isArray(ports) ? ports : [])
        .map((port, index) => normalizePort(node, port, index))
        .filter(port => (
          port.node_id
          && port.port_id
          && PROGRAM_PORT_TYPES.has(port.type)
          && ["in", "out"].includes(port.direction)
        ));
      if (nodeId) portsByNodeId.set(nodeId, normalized);
      return normalized;
    }

    function normalizeWireDraft(value) {
      if (!value || typeof value !== "object") return null;
      const nodeId = String(value.node_id || "");
      const node = nodeMap.get(nodeId);
      if (!node) return null;
      const normalized = normalizePort(node, value);
      if (
        !normalized.port_id
        || normalized.direction !== "out"
        || !PROGRAM_WIRE_TYPES.has(normalized.type)
      ) return null;
      return portsForNode(node).find(port => portIdentity(port) === portIdentity(normalized)) || null;
    }

    function portIdentity(port) {
      return [
        String(port?.node_id || ""),
        String(port?.direction || ""),
        String(port?.port_id || ""),
        String(port?.type || ""),
      ].join("\u0000");
    }

    function portCompatibility(port) {
      const source = model.wireDraft;
      if (!source || portIdentity(source) === portIdentity(port)) return null;
      if (typeof options.isPortCompatible === "function") {
        try {
          const result = options.isPortCompatible(source, port, {
            nodes: model.nodes,
            edges: model.edges,
            wireDraft: source,
          });
          return typeof result === "object" && result !== null ? !!result.ok : !!result;
        } catch (_error) {
          return false;
        }
      }
      return false;
    }

    function syncWireDraftPresentation() {
      if (model.wireDraft) {
        root.dataset.wireDraftType = model.wireDraft.type;
        root.dataset.wireDraftSourceId = model.wireDraft.node_id;
        root.dataset.wireDraftSourcePort = model.wireDraft.port_id;
      } else {
        delete root.dataset.wireDraftType;
        delete root.dataset.wireDraftSourceId;
        delete root.dataset.wireDraftSourcePort;
      }
      layers.nodes.selectAll("button.hatgPort")
        .attr("data-port-compatible", port => {
          const compatible = portCompatibility(port);
          return compatible === null ? null : String(compatible);
        })
        .attr("data-wire-draft-source", port => (
          model.wireDraft && portIdentity(port) === portIdentity(model.wireDraft) ? "true" : null
        ))
        .attr("aria-pressed", port => (
          model.wireDraft && portIdentity(port) === portIdentity(model.wireDraft) ? "true" : "false"
        ));
      layers.nodes.selectAll(".hatgNode")
        .attr("data-wire-target", node => targetChoices(node).length || null);
      layers.labels.selectAll("button.edgeLabelBtn")
        .attr("data-wire-target", edge => targetChoices(edge).length || null);
    }

    function targetChoices(item) {
      if (!model.wireDraft || model.readOnly) return [];
      const candidates = options.connectionTargets?.(model.wireDraft, item)
        || portsForNode(item).filter(port => port.direction === "in");
      return candidates.filter(port => portCompatibility(port) === true);
    }

    function connectToItem(item, event) {
      if (!model.wireDraft || model.readOnly) return false;
      const choices = targetChoices(item);
      if (choices.length === 1) options.onPortClick?.(choices[0], event);
      else if (choices.length > 1) {
        // Loop has two meaningful inputs; let the user choose explicitly.
        const first = (portElementsByNode.get(item.id) || [])
          .find(element => portIdentity(element.__data__) === portIdentity(choices[0]));
        first?.focus({ preventScroll: true });
      }
      return true;
    }

    let wireGesture = null;
    let suppressWireClick = false;
    const wirePreview = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    wirePreview.classList.add("hatgWirePreview");
    wirePreview.setAttribute("aria-hidden", "true");
    wirePreview.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="5 4" />';
    root.append(wirePreview);
    function cancelWireGesture() {
      if (wireGesture) wireGesture.cancelled = true;
      wireGesture = null;
      wirePreview.style.display = "none";
    }
    function onWirePointerDown(event) {
      const control = event.target?.closest?.("button.hatgPort.out");
      if (event.button !== 0 || !control || model.readOnly || control.disabled) return;
      const port = control.__data__;
      if (model.wireDraft && portIdentity(port) !== portIdentity(model.wireDraft)) return;
      wireGesture = { port, pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    }
    function onWirePointerMove(event) {
      const gesture = wireGesture;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      if (!gesture.ready && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) < 5) return;
      event.preventDefault();
      if (!gesture.ready) {
        gesture.ready = Promise.resolve(model.wireDraft ? null : options.onPortClick?.(gesture.port, event))
          .then(() => {
            if (gesture.cancelled && portIdentity(model.wireDraft) === portIdentity(gesture.port)) {
              options.onWireDraftCancel?.(model.wireDraft);
            }
          });
      }
      const bounds = root.getBoundingClientRect();
      const x = gesture.x - bounds.left, y = gesture.y - bounds.top;
      const tx = event.clientX - bounds.left, ty = event.clientY - bounds.top;
      wirePreview.style.display = "block";
      wirePreview.firstChild.setAttribute("d", `M${x},${y} C${x + 60},${y} ${tx - 60},${ty} ${tx},${ty}`);
    }
    async function onWirePointerUp(event) {
      const gesture = wireGesture;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      wireGesture = null;
      wirePreview.style.display = "none";
      if (!gesture.ready) return;
      suppressWireClick = true;
      window.setTimeout(() => { suppressWireClick = false; }, 0);
      const element = document.elementFromPoint(event.clientX, event.clientY);
      const port = element?.closest("button.hatgPort")?.__data__;
      const item = element?.closest("button.hatgNode, button.edgeLabelBtn")?.__data__;
      await gesture.ready;
      if (gesture.cancelled || model.readOnly || portIdentity(model.wireDraft) !== portIdentity(gesture.port)) return;
      if (port && portCompatibility(port) === true) options.onPortClick?.(port, event);
      else if (item) connectToItem(item, event);
    }
    function onWireGestureClick(event) {
      if (!suppressWireClick) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    root.addEventListener("pointerdown", onWirePointerDown);
    root.addEventListener("click", onWireGestureClick, true);
    document.addEventListener("pointermove", onWirePointerMove, { passive: false });
    document.addEventListener("pointerup", onWirePointerUp);
    document.addEventListener("pointercancel", cancelWireGesture);

    function nodeContextHeat(node) {
      if (!node || node.kind === "seed" || isWorkspaceNode(node)) return "";
      const pressure = options.contextPressureForNode?.(node);
      const level = String(pressure?.heat_level || "");
      return ["warm", "hot"].includes(level) ? level : "";
    }

    function nodePosition(node) {
      return {
        x: number(node?.x, 50),
        y: number(node?.y, 50),
      };
    }

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

    function invalidateEdgeGeometry() {
      edgeGeometryMap.clear();
    }

    function refreshNodeExtentCache() {
      const next = new Map();
      layers.nodes.selectAll(".hatgNode").each(function rememberExtent(node) {
        if (!nodeMap.has(node?.id)) return;
        next.set(node.id, {
          x: Math.max(1.5, this.offsetWidth / worldSize.width * 50),
          y: Math.max(1.5, this.offsetHeight / worldSize.height * 50),
        });
      });
      const changed = next.size !== nodeExtentMap.size || [...next].some(([nodeId, extent]) => {
        const previous = nodeExtentMap.get(nodeId);
        return !previous || previous.x !== extent.x || previous.y !== extent.y;
      });
      nodeExtentMap = next;
      if (changed) {
        invalidateEdgeGeometry();
      }
      return changed;
    }

    function nodeHalfExtents(node) {
      return nodeExtentMap.get(node?.id) || { x: 7, y: 5 };
    }

    function getNodeSizes() {
      const nodeSizes = {};
      layers.nodes.selectAll(".hatgNode").each(function measureNode(node) {
        if (node?.id && this.offsetWidth && this.offsetHeight) nodeSizes[node.id] = {
          width: this.offsetWidth / worldSize.width * 100,
          height: this.offsetHeight / worldSize.height * 100,
        };
      });
      return nodeSizes;
    }

    function getPlacementGeometry(child) {
      const probe = document.createElement("button");
      probe.className = "hatgNode";
      probe.dataset.nodeKind = primitiveNodeKind(child);
      if (child.kind === "seed") probe.classList.add("seedNode");
      probe.type = "button";
      probe.tabIndex = -1;
      probe.setAttribute("aria-hidden", "true");
      probe.style.visibility = "hidden";
      probe.style.pointerEvents = "none";
      probe.innerHTML = String(options.nodeContent?.(child, "idle") || "");
      nodeLayer.appendChild(probe);
      try {
        return { node_sizes: getNodeSizes(), child_size: {
          width: probe.offsetWidth / worldSize.width * 100,
          height: probe.offsetHeight / worldSize.height * 100,
        } };
      } finally {
        probe.remove();
      }
    }

    function endpointById(endpointId) {
      const node = nodeMap.get(endpointId);
      if (node) return node;
      const observed = edgeById.get(endpointId);
      if (!observed || !["event", "handoff"].includes(observed.type)
          || !nodeMap.has(observed.source) || !nodeMap.has(observed.target)) return null;
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
      const sourceNode = nodeMap.get(String(edge?.source || ""));
      const targetNode = nodeMap.get(String(edge?.target || ""));
      if (!sourceNode) return null;
      const wireType = String(edge?.wire_type || edge?.type || "");
      const sourcePort = portsForNode(sourceNode).find(port =>
        port.direction === "out" && port.type === wireType
        && port.port_id === String(edge.source_port || wireType));
      if (!sourcePort) return null;
      const source = portPosition(sourcePort);
      if (wireType === "watch" && edge.watch_subject_wire_id) {
        const observed = edgeById.get(edge.watch_subject_wire_id);
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
      return model.nodes.filter(node => {
        if (node.id === edge.source || node.id === edge.target) return false;
        const position = nodePosition(node);
        const half = nodeHalfExtents(node);
        const marginX = half.x + 0.65;
        const marginY = half.y + 0.85;
        return points.some(point => graphGeometry.pointInBox(point, position, { x: marginX, y: marginY }));
      });
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
      const labelElement = edgeLabelElementsById.get(String(edge.id));
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
      const visualScale = 1 / Math.max(0.01, transform.k);
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
      edgeGeometryMap.clear();
      const placed = [];
      const labelWidth = 112;
      const labelHeight = 52;
      const offsets = [0, -56, 56, -112, 112, -168, 168];
      [...edges]
        .filter(edge => edge.type !== "workspace_binding")
        .sort((left, right) => String(left?.id || "").localeCompare(String(right?.id || "")))
        .forEach(edge => {
          const geometry = computeEdgeGeometry(edge);
          if (!geometry) {
            edgeGeometryMap.set(String(edge?.id || ""), geometry);
            return;
          }
          const x = geometry.labelX / 100 * worldSize.width;
          const baseY = geometry.labelY / 100 * worldSize.height;
          const offset = offsets.find(candidate => placed.every(label => (
            Math.abs(x - label.x) >= labelWidth
            || Math.abs(baseY + candidate - label.y) >= labelHeight
          ))) ?? offsets[offsets.length - 1] + placed.length * labelHeight;
          geometry.labelY += offset / worldSize.height * 100;
          placed.push({ x, y: baseY + offset });
          edgeGeometryMap.set(String(edge.id || ""), geometry);
        });
    }

    function validEdges() {
      return model.edges.filter(edge => endpointById(edge.source) && endpointById(edge.target));
    }

    function edgeIsFlowing(edge) {
      if (model.readOnly || edge?.type === "workspace_binding") return false;
      if (typeof options.isEdgeFlowing === "function") return !!options.isEdgeFlowing(edge, nodeMap);
      return ACTIVE_STATUSES.has(nodeStatus(endpointById(edge.source)))
        || ACTIVE_STATUSES.has(nodeStatus(endpointById(edge.target)));
    }

    function edgeFlowState(edge) {
      const states = [endpointById(edge?.source), endpointById(edge?.target)].map(nodeStatus);
      return states.some(status => ["running", "in_progress"].includes(status)) ? "running" : "queued";
    }

    function syncMotionPreference() {
      reducedMotion = prefersReducedMotion();
    }

    systemMotionQuery?.addEventListener?.("change", syncMotionPreference);
    window.addEventListener("portal:user-settings-changed", syncMotionPreference);

    function edgeIsActive(edge) {
      const selectedEndpoint = model.selectedNodeId;
      return edge.source === selectedEndpoint || edge.target === selectedEndpoint;
    }

    function edgeSelectionId(edge) {
      return String(edge.interaction_edge_id || edge.id || "");
    }

    function edgeIsSelected(edge) {
      return Boolean(model.selectedEdgeId) && edgeSelectionId(edge) === model.selectedEdgeId;
    }

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

    function appendIndexedElement(index, key, target) {
      const cleanKey = String(key || "");
      if (!cleanKey || !target) return;
      if (!index.has(cleanKey)) index.set(cleanKey, []);
      index.get(cleanKey).push(target);
    }

    function refreshInteractionIndexes(edges) {
      const indexedEdges = [...edges];
      const indexedEdgeIds = new Set(indexedEdges.map(edge => String(edge?.id || "")));
      model.overviewEdges.forEach(edge => {
        const edgeId = String(edge?.id || "");
        if (!edgeId || indexedEdgeIds.has(edgeId)) return;
        indexedEdges.push(edge);
        indexedEdgeIds.add(edgeId);
      });
      positionElementsByNode = new Map();
      primaryNodeElementsById = new Map();
      portElementsByNode = new Map();
      ownerChromeElementsByNode = new Map();
      incidentEdgeIdsByNode = new Map();
      edgeById = new Map(indexedEdges.map(edge => [String(edge.id || ""), edge]));
      edgePathElementsById = new Map();
      edgeLabelElementsById = new Map();
      minimapNodeElementsById = new Map();
      minimapEdgeElementsById = new Map();

      layers.nodes.selectAll([
        ".hatgNode",
        "button.hatgNodeQuickFork",
        "button.hatgNodeHoverAction",
        "button.hatgNodeOpenMenu",
        "button.hatgNodeControl",
        "div.hatgNodeDisclosureGroup",
      ].join(",")).each(function rememberNodeElement() {
        const nodeId = this.dataset.nodeId
          || this.dataset.sourceNodeId
          || this.dataset.actionNodeId;
        appendIndexedElement(positionElementsByNode, nodeId, this);
        if (this.classList.contains("hatgNode")) {
          primaryNodeElementsById.set(String(nodeId || ""), this);
        } else if (
          this.classList.contains("hatgNodeHoverAction")
          || this.classList.contains("hatgNodeOpenMenu")
          || (
            this.classList.contains("hatgNodeControl")
            && this.classList.contains("is-hover-action")
          )
        ) {
          appendIndexedElement(ownerChromeElementsByNode, nodeId, this);
        }
      });
      layers.nodes.selectAll("button.hatgPort").each(function rememberPort(port) {
        appendIndexedElement(portElementsByNode, port?.node_id, this);
      });
      layers.edges.selectAll("path.hatgEdge").each(function rememberEdgePath(edge) {
        edgePathElementsById.set(String(edge?.id || ""), this);
      });
      layers.labels.selectAll("button.edgeLabelBtn").each(function rememberEdgeLabel(edge) {
        edgeLabelElementsById.set(String(edge?.id || ""), this);
      });
      minimapLayers?.nodes.selectAll(".hatgMinimapNode")
        .each(function rememberMinimapNode(node) {
          minimapNodeElementsById.set(String(node?.id || ""), this);
        });
      minimapLayers?.edges.selectAll("line.hatgMinimapEdge")
        .each(function rememberMinimapEdge(edge) {
          minimapEdgeElementsById.set(String(edge?.id || ""), this);
        });
      indexedEdges.forEach(edge => {
        const edgeId = String(edge?.id || "");
        const observed = edge.type === "watch" ? edgeById.get(edge.watch_subject_wire_id || edge.target) : null;
        [edge?.source, edge?.target, observed?.source, observed?.target].forEach(nodeId => {
          const cleanNodeId = String(nodeId || "");
          if (!cleanNodeId || !edgeId) return;
          if (!incidentEdgeIdsByNode.has(cleanNodeId)) {
            incidentEdgeIdsByNode.set(cleanNodeId, new Set());
          }
          incidentEdgeIdsByNode.get(cleanNodeId).add(edgeId);
        });
      });
    }

    function updateDraggedGeometry(positionById, options = {}) {
      renderFunctionBlockBoundaries();
      const movedNodeIds = new Set(positionById.keys());
      const affectedEdgeIds = new Set();
      movedNodeIds.forEach(nodeId => {
        incidentEdgeIdsByNode.get(nodeId)?.forEach(edgeId => {
          affectedEdgeIds.add(edgeId);
          edgeGeometryMap.delete(edgeId);
        });
      });
      [...affectedEdgeIds].sort((a, b) => Number(edgeById.get(a)?.type === "watch") - Number(edgeById.get(b)?.type === "watch")).forEach(edgeId => {
        const edge = edgeById.get(edgeId);
        if (!edge) return;
        const geometry = options.final
          ? edgeGeometry(edge)
          : computeEdgeGeometry(edge, { avoidObstacles: false });
        if (geometry) edgeGeometryMap.set(edgeId, geometry);
        const edgePath = edgePathElementsById.get(edgeId);
        if (edgePath) {
          edgePath.setAttribute("data-routing", geometry?.routing || "direct");
          if (geometry?.routeAxis) edgePath.setAttribute("data-route-axis", geometry.routeAxis);
          else edgePath.removeAttribute("data-route-axis");
          edgePath.setAttribute("d", geometry?.d || "");
        }
        const label = edgeLabelElementsById.get(edgeId);
        if (label) {
          label.style.left = `${geometry?.labelX ?? 50}%`;
          label.style.top = `${geometry?.labelY ?? 50}%`;
        }
      });
      movedNodeIds.forEach(nodeId => {
        (portElementsByNode.get(nodeId) || []).forEach(portElement => {
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

    function updateDraggedMinimapGeometry(movedNodeIds, affectedEdgeIds) {
      if (
        !minimapLayers
        || !minimap
        || minimap.hasAttribute("hidden")
        || !minimapRenderState
      ) return;
      if (model.viewMode === "workspace") {
        updateMinimapGeometry();
        return;
      }
      movedNodeIds.forEach(nodeId => {
        const node = nodeMap.get(nodeId) || minimapRenderState.overviewMap.get(nodeId);
        const nodeElement = minimapNodeElementsById.get(nodeId);
        if (!node || !nodeElement) return;
        const position = nodePosition(node);
        const point = worldToMinimap(position.x, position.y);
        nodeElement.setAttribute("cx", String(point.x));
        nodeElement.setAttribute("cy", String(point.y));
      });
      affectedEdgeIds.forEach(edgeId => {
        const edge = edgeById.get(edgeId);
        const edgeElement = minimapEdgeElementsById.get(edgeId);
        const source = nodeMap.get(edge?.source)
          || minimapRenderState.endpointMap.get(edge?.source);
        const target = nodeMap.get(edge?.target)
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

    function updateMinimapViewport(viewport = viewportSize) {
      if (!minimapLayers || minimap?.hasAttribute("hidden")) return;
      const bounds = visibleWorldBounds(transform, viewport, currentViewportInsets(viewport));
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
      const overviewNodes = model.overviewNodes.length ? model.overviewNodes : model.nodes;
      const overviewEdges = model.overviewEdges.length ? model.overviewEdges : model.edges;
      const overviewMap = new Map(overviewNodes.map(node => [node.id, node]));
      const visibleIds = new Set(model.nodes.map(node => node.id));
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
        .attr("class", node => `hatgMinimapNode${node.kind === "seed" ? " seed" : ""}${visibleIds.has(node.id) ? " visible" : " muted"}${node.id === model.selectedNodeId ? " selected" : ""}`)
        .attr("data-minimap-node-id", node => node.id)
        .attr("data-node-kind", primitiveNodeKind)
        .attr("width", node => node.kind === "workspace" ? 4 : null)
        .attr("height", node => node.kind === "workspace" ? 3 : null)
        .attr("rx", node => node.kind === "workspace" ? 0.4 : null)
        .attr("data-context-heat", node => nodeContextHeat(node) || null)
        .attr("r", node => node.id === model.selectedNodeId ? 2.35 : 1.55);
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

    function renderEdges(edges, _options = {}) {
      layers.edges.selectAll("path.hatgEdge")
        .interrupt()
        .style("opacity", 1)
        .data(edges, edge => edge.id)
        .join("path")
        .attr("data-edge-id", edge => edge.id)
        .attr("data-source-id", edge => edge.source || null)
        .attr("data-target-id", edge => edge.target || null)
        .attr("data-program-wire", edge => isProgramWire(edge) ? "true" : null)
        .attr("data-wire-type", edge => isProgramWire(edge) ? (edge.wire_type || edge.type || "") : null)
        .attr("data-source-port", edge => isProgramWire(edge) ? (edge.source_port || "") : null)
        .attr("data-target-port", edge => isProgramWire(edge) ? (edge.target_port || "") : null)
        .attr("data-derived-edge", edge => edge.derived_edge ? "true" : null)
        .attr("data-parallel-count", edge => String(parallelEdgeLaneMap.get(edge.id)?.count || 1))
        .attr("data-parallel-lane", edge => String(parallelEdgeLaneMap.get(edge.id)?.lane || 0))
        .attr("class", edge => {
          const active = edgeIsActive(edge);
          const selected = edgeIsSelected(edge) || model.selectedNodeIds.includes(edge.id);
          const flowing = edgeIsFlowing(edge);
          const dimmed = edge._family_dimmed ? " familyDimmed" : "";
          return `hatgEdge ${edge.type || "handoff"}${active ? " active" : ""}${selected ? " selected" : ""}${flowing ? " flowing" : ""}${dimmed}`;
        })
        .attr("data-through-family", edge => edge.through_family_facade ? "true" : null)
        .attr("data-through-function-block", edge => edge.through_function_block ? "true" : null)
        .attr("data-flow-state", edge => edgeIsFlowing(edge) ? edgeFlowState(edge) : null)
        .attr("data-truth-verified", edge => (edge.type !== "inheritance" || edge.truth?.verified) ? "true" : "false")
        .attr("data-absorption-status", edge => edge.type === "absorption" ? (edge.absorption_status || "queued") : null)
        .attr("data-handoff-status", edge => edge.type === "handoff" ? (edge.handoff_status || "draft") : null)
        .attr("data-draft-preview", edge => edge.draft_preview ? "true" : null)
        .attr("data-routing", edge => edgeGeometry(edge)?.routing || "direct")
        .attr("data-route-axis", edge => edgeGeometry(edge)?.routeAxis || null)
        .attr("d", edge => edgeGeometry(edge)?.d || "");
    }

    function edgeTooltip(edge) {
      if (edge.execution_summary) {
        return [edge.execution_summary, edge.execution_next_action]
          .filter(Boolean)
          .join("；");
      }
      if (["route"].includes(edge.type)) {
        const pending = Math.max(0, Number(edge.pending_event_count || 0));
        if (pending > 0) {
          const policy = ({
            drop: window.PortalI18n.t("目标忙碌时丢弃"),
            latest: window.PortalI18n.t("目标忙碌时只保留最新一次"),
            queue: window.PortalI18n.text`目标忙碌时最多排队 ${Number(edge.event_queue_limit || 10)} 次`,
          })[edge.event_unavailable_policy] || window.PortalI18n.t("使用当前等待策略");
          return window.PortalI18n.text`有 ${pending} 次触发正在等待目标接收；${policy}。点击查看或修改这条触发连线。`;
        }
        return window.PortalI18n.t("打开这条触发连线的设置；可配置发送内容、目标忙碌时如何处理，以及等待多久后过期。");
      }
      if (edge.type === "absorption") {
        return edge.absorption_status === "failed"
          ? window.PortalI18n.t("查看这次上下文吸收为什么投递失败，以及可以如何恢复。")
          : window.PortalI18n.t("查看这次上下文吸收向目标节点传递了什么。");
      }
      if (edge.type === "target") {
        return window.PortalI18n.t("查看这个动作绑定了哪个作用对象；这条目标绑定本身不会启动目标节点。");
      }
      if (edge.type === "watch") {
        return window.PortalI18n.t("查看事件正在监听哪个对象；监听关系本身不会启动被监听对象。");
      }
      if (edge.type === "candidate_fork") {
        return window.PortalI18n.t("候选 Fork：这个 Seed 的候选从来源 Resident 的工作出发，采纳时 Fork 它。点击可以删除这条关系。");
      }
      if (edge.draft_preview) {
        return window.PortalI18n.t("这是一条尚未生效的工作区交接；打开并保存配置后才会正式创建。");
      }
      if (edge.type === "handoff") {
        return window.PortalI18n.t("查看这次工作区交接的来源、目标、准备状态和目标 Agent 启动规则。");
      }
      return window.PortalI18n.t("打开这条关系的详情，并修改其中可编辑的设置。");
    }

    function renderLabels(edges) {
      layers.labels.selectAll("button.edgeLabelBtn")
        .data(edges.filter(edge => edge.type !== "workspace_binding"), edge => edge.id)
        .join("button")
        .attr("class", edge => `edgeLabelBtn ${edge.type || "handoff"}${edgeIsSelected(edge) ? " selected" : ""}${Number(edge.pending_event_count || 0) > 0 ? " hasPendingEvents" : ""}`)
        .attr("type", "button")
        .attr("data-edge-id", edge => edge.id)
        .attr("data-source-id", edge => edge.source || null)
        .attr("data-target-id", edge => edge.target || null)
        .attr("data-program-wire", edge => isProgramWire(edge) ? "true" : null)
        .attr("data-wire-type", edge => isProgramWire(edge) ? (edge.wire_type || edge.type || "") : null)
        .attr("data-source-port", edge => isProgramWire(edge) ? (edge.source_port || "") : null)
        .attr("data-target-port", edge => isProgramWire(edge) ? (edge.target_port || "") : null)
        .attr("data-pending-event-count", edge => (
          ["route"].includes(edge.type)
            ? String(Math.max(0, Number(edge.pending_event_count || 0)))
            : null
        ))
        .attr("data-derived-edge", edge => edge.derived_edge ? "true" : null)
        .attr("data-parallel-count", edge => String(parallelEdgeLaneMap.get(edge.id)?.count || 1))
        .attr("data-parallel-lane", edge => String(parallelEdgeLaneMap.get(edge.id)?.lane || 0))
        .attr("data-absorption-status", edge => edge.type === "absorption" ? (edge.absorption_status || "queued") : null)
        .attr("data-handoff-status", edge => edge.type === "handoff" ? (edge.handoff_status || "draft") : null)
        .attr("data-draft-preview", edge => edge.draft_preview ? "true" : null)
        .attr("aria-disabled", edge => model.readOnly || edge.visual_only ? "true" : null)
        .attr("disabled", edge => edge.visual_only ? true : null)
        .attr("title", null)
        .attr("data-portal-tooltip", edgeTooltip)
        .style("left", edge => `${edgeGeometry(edge)?.labelX ?? 50}%`)
        .style("top", edge => `${edgeGeometry(edge)?.labelY ?? 50}%`)
        .text(edge => edge.display_label || edge.label || edge.type || window.PortalI18n.t("关系"))
        .on("click.hatg", (event, edge) => {
          event.preventDefault();
          event.stopPropagation();
          if (edge.visual_only) return;
          if (connectToItem(edge, event)) return;
          options.onEdgeClick?.(edge, event);
        });
    }

    function primitiveTransfer(event) {
      return (
        !model.readOnly
        && typeof options.onPrimitiveDrop === "function"
        && Array.from(event.dataTransfer?.types || []).includes(HATG_PRIMITIVE_TRANSFER_TYPE)
      );
    }

    function screenToWorld(clientX, clientY, rect) {
      const bounds = rect || root.getBoundingClientRect();
      return graphGeometry.screenToWorld({ x: clientX, y: clientY }, transform, worldSize, bounds);
    }

    function dragMembersFor(node) {
      const selectedIds = new Set(model.selectedNodeIds);
      if (!selectedIds.has(node.id) || selectedIds.size < 2) return [node];
      return model.nodes.filter(candidate => selectedIds.has(candidate.id));
    }

    function updateDraggedNodePresentation(positionById) {
      positionById.forEach((position, nodeId) => {
        (positionElementsByNode.get(nodeId) || []).forEach(target => {
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
        new Set([member.node, nodeMap.get(member.node.id)]).forEach(currentNode => {
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
        primaryNodeElementsById.get(dragState.absorptionTargetId)
          ?.classList.remove("absorptionDropTarget");
      }
      root.classList.remove("isNodeAbsorptionDrop");
      delete root.dataset.absorptionTargetId;
    }

    function updateAbsorptionDropTarget(event, { force = false } = {}) {
      if (model.viewMode === "workspace" || !dragState?.moved || dragState.members.length > 1) return;
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
        const candidate = nodeMap.get(targetId);
        if (!candidate || options.canNodeAbsorb?.(dragState.node, candidate, event) === false) continue;
        target = candidate;
        break;
      }
      const previousTargetId = dragState.absorptionTargetId;
      dragState.absorptionTargetId = target?.id || "";
      if (previousTargetId !== dragState.absorptionTargetId) {
        primaryNodeElementsById.get(previousTargetId)
          ?.classList.remove("absorptionDropTarget");
        primaryNodeElementsById.get(dragState.absorptionTargetId)
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
        .translate(transform.x + panX, transform.y + panY)
        .scale(transform.k);
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
        if (!model.layoutInteractive) return false;
        if (model.navigationFocus && model.viewMode !== "workspace") return false;
        if (model.handoffMode) return false;
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
          ? nodeMap.get(dragState.absorptionTargetId)
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

    function nodeFootprint(node) {
      const position = nodePosition(node), half = nodeHalfExtents(node);
      const boxes = [{ left: position.x - half.x, right: position.x + half.x,
        top: position.y - half.y, bottom: position.y + half.y },
        ...portsForNode(node).map(port => {
          const point = portPosition(port);
          return { left: point.x - 3000 / worldSize.width, right: point.x + 3000 / worldSize.width,
            top: point.y - 1200 / worldSize.height, bottom: point.y + 1200 / worldSize.height };
        })];
      return { left: Math.min(...boxes.map(box => box.left)), right: Math.max(...boxes.map(box => box.right)),
        top: Math.min(...boxes.map(box => box.top)), bottom: Math.max(...boxes.map(box => box.bottom)) };
    }

    function functionBlockBoundaryBounds(block, seen = new Set()) {
      if (seen.has(block.id)) return null;
      seen.add(block.id);
      const boxes = block.member_ids.map(id => nodeMap.get(id)).filter(Boolean).map(nodeFootprint);
      for (const id of block.child_block_ids || []) {
        const child = (model.functionBlockBoundaries || []).find(candidate => candidate.id === id);
        const bounds = child && functionBlockBoundaryBounds(child, seen);
        if (bounds) boxes.push(bounds);
      }
      // Wire-only and empty blocks still need an accessible header and collapse control.
      if (!boxes.length) {
        const x = Number.isFinite(block.x) ? block.x : 50;
        const y = Number.isFinite(block.y) ? block.y : 50;
        return { left: x - 12100 / worldSize.width, right: x + 12100 / worldSize.width,
          top: y - 5000 / worldSize.height, bottom: y + 5000 / worldSize.height };
      }
      const left = Math.min(...boxes.map(box => box.left)) - 1600 / worldSize.width;
      const right = Math.max(...boxes.map(box => box.right)) + 1600 / worldSize.width;
      const boundary = layers.nodes.node()?.querySelector(`[data-function-block-boundary="${CSS.escape(block.id)}"]`);
      // Long identities wrap. Reserve their actual unscaled header height so
      // nested blocks and member ports remain below the title at every zoom.
      if (boundary) boundary.style.width = `${right - left}%`;
      const headerHeight = Math.max(42, (boundary?.querySelector("header")?.offsetHeight || 0) + 12);
      return {
        left,
        right,
        top: Math.min(...boxes.map(box => box.top)) - headerHeight * 100 / worldSize.height,
        bottom: Math.max(...boxes.map(box => box.bottom)) + 1600 / worldSize.height,
      };
    }

    // A title edit or explicit expansion can enlarge an object after its
    // original placement. Move that object alone, preserving all other layout.
    function getObjectSeparation(id) {
      refreshNodeExtentCache();
      renderFunctionBlockBoundaries();
      const blocks = model.functionBlockBoundaries || [];
      const block = blocks.find(item => item.id === id);
      const members = new Set(block ? block.member_ids : [id]);
      const node = nodeMap.get(id);
      const box = block ? functionBlockBoundaryBounds(block) : node && nodeFootprint(node);
      if (!box) return null;
      const contains = (parent, target, seen = new Set()) => {
        if (seen.has(parent.id)) return false;
        seen.add(parent.id);
        return (parent.child_block_ids || []).some(childId => childId === target
          || (blocks.find(item => item.id === childId) && contains(blocks.find(item => item.id === childId), target, seen)));
      };
      const obstacles = model.nodes.filter(item => !members.has(item.id)).map(nodeFootprint);
      for (const other of blocks) {
        if (other.id === id || other.member_ids.includes(id)
          || (block && (contains(other, id) || contains(block, other.id)))) continue;
        const bounds = functionBlockBoundaryBounds(other);
        if (bounds) obstacles.push(bounds);
      }
      const gapX = 24 * 100 / worldSize.width, gapY = 24 * 100 / worldSize.height;
      const rectangles = obstacles.map(other => ({
        left: other.left - box.right - gapX, right: other.right - box.left + gapX,
        top: other.top - box.bottom - gapY, bottom: other.bottom - box.top + gapY,
      }));
      let best = { x: 0, y: 0 }, score = Infinity;
      const columns = new Set([0, ...rectangles.flatMap(rect => [rect.left, rect.right])]);
      for (const x of columns) {
        const intervals = rectangles.filter(rect => rect.left < x && x < rect.right)
          .sort((a, b) => a.top - b.top);
        const merged = [];
        for (const interval of intervals) {
          const last = merged.at(-1);
          if (last && interval.top < last.bottom) last.bottom = Math.max(last.bottom, interval.bottom);
          else merged.push({ top: interval.top, bottom: interval.bottom });
        }
        const covering = merged.find(interval => interval.top < 0 && interval.bottom > 0);
        const y = covering ? (Math.abs(covering.top) < covering.bottom ? covering.top : covering.bottom) : 0;
        const distance = (x * worldSize.width) ** 2 + (y * worldSize.height) ** 2;
        if (distance < score) { best = { x, y }; score = distance; }
      }
      return score > 0.000001 ? best : null;
    }

    function renderFunctionBlockBoundaries() {
      const boundaries = layers.nodes.selectAll("section.functionBlockBoundary")
        .data(model.functionBlockBoundaries || [], block => block.id)
        .join(enter => {
          const boundary = enter.append("section").attr("class", "functionBlockBoundary");
          const header = boundary.append("header");
          header.append("button").attr("type", "button").attr("class", "functionBlockBoundaryTitle");
          header.append("button").attr("type", "button").attr("class", "functionBlockBoundaryCollapse");
          return boundary;
        })
        .attr("data-function-block-boundary", block => block.id);
      boundaries.select(".functionBlockBoundaryTitle").text(block => block.title || "Function Block");
      boundaries.select(".functionBlockBoundaryCollapse").text("−");
      boundaries.each(function positionBoundary(block) {
          const bounds = functionBlockBoundaryBounds(block);
          this.hidden = !bounds;
          if (!bounds) return;
          const { left, right, top, bottom } = bounds;
          Object.assign(this.style, { left: `${left}%`, top: `${top}%`, width: `${right - left}%`, height: `${bottom - top}%` });
          const boundary = d3.select(this);
          boundary.select(".functionBlockBoundaryTitle").text(block.title || "Function Block")
            .on("click.hatg", event => { event.stopPropagation(); options.onFunctionBlockMenu?.(block.id); });
          boundary.select(".functionBlockBoundaryCollapse").text("−")
            .attr("aria-label", window.PortalI18n.t("折叠"))
            .on("click.hatg", event => { event.stopPropagation(); options.onFunctionBlockCollapse?.(block.id); });
        });
    }

    function renderNodes(nodes) {
      let contentChanged = false;
      const actionNodes = nodes.filter(node => !isWorkspaceNode(node));
      const selection = layers.nodes.selectAll(".hatgNode")
        .interrupt()
        .style("opacity", 1)
        .data(nodes, node => `${node.kind === "workspace_resident" ? "marker" : "card"}:${node.id}`)
        .join(
          enter => enter.append(node => document.createElement(node.kind === "workspace_resident" ? "div" : "button"))
            .attr("type", node => node.kind === "workspace_resident" ? null : "button").style("opacity", 0)
            .call(items => items.transition().duration(animationDuration(180)).style("opacity", 1)),
          update => update,
          exit => exit.transition().duration(animationDuration(120)).style("opacity", 0).remove(),
        )
        .attr("class", node => {
          const selected = (
            node.id === model.selectedNodeId
            || model.selectedNodeIds.includes(node.id)
          );
          const handoffSource = model.handoffMode && node.id === (model.handoffAgentId || model.handoffSourceId);
          const seedClass = node.kind === "seed" ? ` seedNode seed-${node.seed_type || "other"}` : "";
          const familyClass = node._family_facade
            ? ` familyFacadeNode${node._family_expanded ? " is-expanded" : ""}`
            : "";
          const familyMemberClass = node._family_member ? " familyMemberNode" : "";
          const familyDimmedClass = node._family_dimmed ? " familyDimmed" : "";
          const dropTarget = candidateForkDrag?.target?.dataset.nodeId === node.id ? " candidateForkDropTarget" : "";
          return `hatgNode${seedClass}${familyClass}${familyMemberClass}${familyDimmedClass}${selected ? " selected" : ""}${handoffSource ? " handoffSource" : ""}${model.readOnly ? " readOnly" : ""}${dropTarget}`;
        })
        .attr("data-family-tone", node => (Number.isInteger(node._family_tone) ? String(node._family_tone) : null))
        .attr("data-family-member-tone", node => (
          Number.isInteger(node._family_member?.tone) ? String(node._family_member.tone) : null
        ))
        .attr("data-family-role", node => node._family_member?.role || null)
        .attr("data-function-block-family", node => node.family ? "true" : null)
        .attr("data-node-id", node => node.id)
        .attr("role", node => node.kind === "workspace_resident" ? "img" : null)
        .attr("aria-label", node => (
          isWorkspaceNode(node) ? [node.title, node.workspace_id || node.resident_id || node.id].filter(Boolean).join(" · ")
            : primitiveNodeKind(node) === "function_block" ? node.title || node.id : null
        ))
        .attr("title", node => isWorkspaceNode(node) ? [node.title, node.workspace_path, node.workspace_id || node.resident_id || node.id].filter(Boolean).join("\n") : null)
        .attr("data-portal-tooltip", node => isWorkspaceNode(node) ? null : options.nodeTooltip?.(node) || null)
        .property("disabled", node => !isWorkspaceNode(node) && options.nodeDisabled?.(node) === true)
        .attr("aria-disabled", node => !isWorkspaceNode(node) && options.nodeDisabled?.(node) === true ? "true" : null)
        .attr("data-node-kind", primitiveNodeKind)
        .attr("data-block-tone", node => (
          primitiveNodeKind(node) === "function_block"
            ? node.tone || "violet"
            : null
        ))
        .attr("data-seed-type", node => node.kind === "seed" ? (node.seed_type || "other") : null)
        .attr("data-status", node => nodeStatus(node))
        .attr("data-context-heat", node => nodeContextHeat(node) || null)
        .attr("data-runtime-effect", node => ACTIVE_STATUSES.has(nodeStatus(node)) ? "activity-rail" : null)
        .attr("data-semantic-level", semanticZoomLevel || "operate")
        .attr("aria-pressed", node => node.kind === "workspace_resident" ? null : (
          node.id === model.selectedNodeId
          || model.selectedNodeIds.includes(node.id)
        ) ? "true" : "false")
        .style("left", node => `${nodePosition(node).x}%`)
        .style("top", node => `${nodePosition(node).y}%`)
        .each(function updateNodeContent(node) {
          const content = nodeContent(node);
          if (this.__hatgNodeContent === content) return;
          this.innerHTML = content;
          this.__hatgNodeContent = content;
          contentChanged = true;
        });
      selection.call(nodeDrag);

      const disclosures = typeof options.nodeDisclosure === "function"
        ? actionNodes.map(node => {
            let disclosure = null;
            try {
              disclosure = options.nodeDisclosure(node);
            } catch (_error) {
              disclosure = null;
            }
            return disclosure && typeof disclosure === "object"
              ? { node, disclosure }
              : null;
          }).filter(Boolean)
        : [];
      const disclosureGroups = layers.nodes.selectAll("div.hatgNodeDisclosureGroup")
        .data(disclosures, item => item.node.id)
        .join("div")
        .attr("class", "hatgNodeDisclosureGroup")
        .attr("data-action-node-id", item => item.node.id)
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => nodeActionOffset(item, "x"))
        .style("margin-top", item => nodeActionOffset(item, "y"));
      disclosureGroups.selectAll("button.hatgNodeDisclosure")
        .data(item => [item]).join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeDisclosure")
        .attr("data-node-action", "family-disclosure")
        .attr("data-disclosure-kind", item => String(item.disclosure.kind || "family"))
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-expanded", item => item.disclosure.expanded ? "true" : "false")
        .attr("aria-expanded", item => item.disclosure.expanded ? "true" : "false")
        .attr("aria-label", item => item.disclosure.label || "展开内部 Channel")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.disclosure.title || item.disclosure.label || "展开内部 Channel")
        .text(item => item.disclosure.expanded ? "−" : "+")
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          options.onNodeDisclosure?.(item.node, item.disclosure, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        });

      disclosureGroups.selectAll("button.hatgNodeDisclosureAction")
        .data(item => !model.readOnly && item.disclosure.action ? [item] : []).join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeDisclosureAction")
        .attr("data-node-action", "disclosure-action")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-portal-tooltip", item => item.disclosure.action.title || item.disclosure.action.label)
        .attr("aria-label", item => item.disclosure.action.label)
        .attr("data-portal-icon", item => item.disclosure.action.iconName || null)
        .text(item => item.disclosure.action.iconName ? null : (item.disclosure.action.symbol || item.disclosure.action.label))
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault(); event.stopPropagation();
          options.onNodeDisclosureAction?.(item.node, item.disclosure, event);
        })
        .on("dblclick.hatg", event => { event.preventDefault(); event.stopPropagation(); });

      const quickForkNodes = actionNodes.filter(nodeHasQuickFork);
      layers.nodes.selectAll("button.hatgNodeQuickFork")
        .data(quickForkNodes, node => node.id)
        .join(
          enter => enter.append("button").attr("type", "button").style("opacity", 0)
            .call(items => items.transition().duration(animationDuration(140)).style("opacity", 1)),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", "hatgNodeQuickFork")
        .attr("data-node-quick-fork", "")
        .attr("data-source-node-id", node => node.id)
        .attr("data-busy", node => model.quickForkNodeId === node.id ? "true" : null)
        .attr("aria-busy", node => model.quickForkNodeId === node.id ? "true" : "false")
        .attr("aria-label", node => `从「${node.title || node.id}」完整上下文 Fork 新 Resident`)
        .attr("aria-description", "点击直接 Fork；悬停或按方向键选择其他创建方式；拖到 Seed 上建立候选 Fork。")
        .attr("aria-haspopup", "menu")
        .attr("data-portal-tooltip-opt-out", "1")
        .attr("title", null)
        .attr("data-portal-tooltip", null)
        .property("disabled", () => Boolean(model.quickForkNodeId))
        .style("left", node => `${nodePosition(node).x}%`)
        .style("top", node => `${nodePosition(node).y}%`)
        .attr("data-portal-icon", node => model.quickForkNodeId === node.id ? null : "plus")
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("pointerenter.hatg", (event, node) => {
          if (event.pointerType !== "touch" && !candidateForkDrag?.active) openQuickForkMenu(node, event);
        })
        .on("pointerdown.hatg", (event, node) => onCandidateForkPointerDown(event, node))
        .on("pointerleave.hatg", scheduleQuickForkClose)
        .on("blur.hatg", scheduleQuickForkClose)
        .on("keydown.hatg", (event, node) => {
          if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
          event.preventDefault();
          event.stopPropagation();
          openQuickForkMenu(node, event, { focus: true, last: event.key === "ArrowUp" });
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("click.hatg", (event, node) => {
          event.preventDefault();
          event.stopPropagation();
          if (event.detail > 1 || suppressQuickForkClick) return;
          invokeQuickFork(node.id, event, "full");
        });

      const nodeControls = !model.readOnly && typeof options.nodeControl === "function"
        ? actionNodes.map(node => {
            let control = null;
            try {
              control = options.nodeControl(node);
            } catch (_error) {
              control = null;
            }
            return control && typeof control === "object" ? { node, control } : null;
          }).filter(Boolean)
        : [];
      layers.nodes.selectAll("button.hatgNodeControl")
        .data(nodeControls, item => item.node.id)
        .join(
          enter => enter.append("button").attr("type", "button"),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", item => `hatgNodeControl action-${item.control.action || "custom"}${item.control.presentation === "hover" ? " is-hover-action" : ""}`)
        .attr("data-node-action", "")
        .attr("data-node-control", "")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-control-kind", item => item.control.kind || "custom")
        .attr("data-manual-event-trigger", item => (
          item.control.action === "emit-manual-event" ? "" : null
        ))
        .attr("data-event-source-control", item => (
          item.control.kind === "event_source" ? "" : null
        ))
        .attr("data-event-source-id", item => (
          item.control.kind === "event_source" ? item.node.id : null
        ))
        .attr("data-action", item => item.control.action || "custom")
        .attr("data-busy", item => item.control.busy ? "true" : null)
        .attr("aria-busy", item => item.control.busy ? "true" : "false")
        .attr("aria-label", item => item.control.label || item.control.title || "节点操作")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.control.title || "执行这个节点操作。")
        .property("disabled", item => Boolean(item.control.busy || item.control.disabled))
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        // "hover" and "corner" controls sit at the node's top right corner;
        // a hover control shows only while the node is pointed at.
        .style("margin-left", item => {
          if (!["hover", "corner"].includes(item.control.presentation)) return null;
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          if (!["hover", "corner"].includes(item.control.presentation)) return null;
          return nodeActionOffset(item, "y");
        })
        .attr("data-portal-icon", item => item.control.busy ? null : (item.control.iconName || null))
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          if (item.control.busy || item.control.disabled) return;
          options.onNodeControl?.(item.node, item.control.action, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("pointerenter.hatgPorts", (_event, item) => {
          revealHoveredPorts(item.node.id);
        })
        .on("pointerleave.hatgPorts", (event, item) => {
          concealHoveredPorts(item.node.id, event.relatedTarget);
        })
        .on("focus.hatgPorts", (_event, item) => {
          revealFocusedPorts(item.node.id);
        })
        .on("blur.hatgPorts", (event, item) => {
          concealFocusedPorts(item.node.id, event.relatedTarget);
        });
      return contentChanged;
    }

    function nodeActionOffset(item, axis) {
      if (item.action?.placement === "below") {
        if (axis === "x") return `${(item.action.index - (item.actionCount - 1) / 2) * 28}px`;
        return `${nodeHalfExtents(item.node).y / 100 * worldSize.height + 8}px`;
      }
      const half = nodeHalfExtents(item.node)[axis] / 100
        * (axis === "x" ? worldSize.width : worldSize.height);
      if (primitiveNodeKind(item.node) === "function_block") {
        // Keep expand, run and menu on separate corners of the measured icon.
        const offset = Math.max(12, half - 8);
        if (axis === "x") return `${item.menu ? -offset : offset}px`;
        return `${item.menu || item.control ? offset : -offset}px`;
      }
      const offset = Math.max(item.menu ? 0 : 12, half - (item.menu ? 18 : 8));
      // Keep Family folding opposite the stop action at the top-right corner.
      if (axis === "x" && item.disclosure?.kind === "family") return `${-offset}px`;
      return `${axis === "x" ? offset : -offset}px`;
    }

    function syncNodeHoverActionOffsets() {
      layers.nodes.selectAll("div.hatgNodeDisclosureGroup, button.hatgNodeHoverAction, button.hatgNodeControl, button.hatgNodeOpenMenu")
        // Node controls follow the node's corner only when they sit there.
        .filter(item => !item?.control || ["hover", "corner"].includes(item.control.presentation))
        .style("margin-left", item => nodeActionOffset(item, "x"))
        .style("margin-top", item => nodeActionOffset(item, "y"));
    }

    function renderNodeHoverActions(nodes) {
      const actions = !model.readOnly && typeof options.nodeHoverActions === "function"
        ? nodes.filter(node => !isWorkspaceNode(node)).flatMap(node => {
            let actions = [];
            try {
              actions = options.nodeHoverActions(node);
            } catch (_error) {
              actions = [];
            }
            return Array.isArray(actions)
              ? actions.map(action => ({ node, action, actionCount: actions.length }))
              : [];
          })
        : [];
      layers.nodes.selectAll("button.hatgNodeHoverAction")
        .data(actions, item => `${item.node.id}:${item.action.action}`)
        .join("button")
        .attr("type", "button")
        .attr("class", item => `hatgNodeHoverAction action-${item.action.action || "custom"}`)
        .attr("data-node-action", "")
        .attr("data-node-hover-action", "")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-node-kind", item => primitiveNodeKind(item.node))
        .attr("data-action", item => item.action.action || "custom")
        .attr("data-action-kind", item => item.action.kind || "custom")
        .attr("data-busy", item => item.action.busy ? "true" : null)
        .attr("aria-busy", item => item.action.busy ? "true" : "false")
        .attr("aria-label", item => item.action.label || item.action.title || "节点操作")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.action.title || "执行这个节点操作。")
        .property("disabled", item => Boolean(item.action.busy || item.action.disabled))
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => {
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          return nodeActionOffset(item, "y");
        })
        .attr("data-portal-icon", item => item.action.busy ? null : (item.action.iconName || null))
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          if (item.action.busy || item.action.disabled) return;
          options.onNodeHoverAction?.(item.node, item.action.action, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("pointerenter.hatgPorts", (_event, item) => {
          revealHoveredPorts(item.node.id);
        })
        .on("pointerleave.hatgPorts", (event, item) => {
          concealHoveredPorts(item.node.id, event.relatedTarget);
        })
        .on("focus.hatgPorts", (_event, item) => {
          revealFocusedPorts(item.node.id);
        })
        .on("blur.hatgPorts", (event, item) => {
          concealFocusedPorts(item.node.id, event.relatedTarget);
        });
      syncNodeHoverActionOffsets();
    }

    function resolveNodeOpenMenu(node) {
      if (isWorkspaceNode(node) || typeof options.nodeOpenMenu !== "function") return null;
      try {
        const menu = options.nodeOpenMenu(node);
        return menu && typeof menu === "object" ? menu : null;
      } catch (_error) {
        return null;
      }
    }

    function renderNodeOpenMenus(nodes) {
      const menus = nodes
        .map(node => {
          const menu = resolveNodeOpenMenu(node);
          return menu ? { node, menu } : null;
        })
        .filter(Boolean);
      layers.nodes.selectAll("button.hatgNodeOpenMenu")
        .data(menus, item => item.node.id)
        .join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeOpenMenu")
        .attr("data-node-action", "")
        .attr("data-node-open-menu", item => item.node.id)
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-node-owner-selected", item => (
          item.node.id === model.selectedNodeId || model.selectedNodeIds.includes(item.node.id)
        ) ? "true" : null)
        .attr("aria-label", item => item.menu.label || `Open ${item.node.title || item.node.id}`)
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.menu.title || "打开方式")
        .attr("data-portal-icon", "more-horizontal")
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => {
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          return nodeActionOffset(item, "y");
        })
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); });
    }

    function portPosition(port) {
      const node = nodeMap.get(port.node_id);
      if (!node) return { x: 50, y: 50 };
      const position = nodePosition(node);
      const half = nodeHalfExtents(node);
      const siblings = portsForNode(node).filter(candidate => candidate.direction === port.direction);
      const index = Math.max(0, siblings.findIndex(candidate => portIdentity(candidate) === portIdentity(port)));
      // Ports are real controls, not decorative dots. Distribute their
      // centers over the node height with enough room for the 20px pills;
      // dividing by the number of siblings made 3–4 foundational ports stack
      // on top of one another and let a different protocol intercept clicks.
      const portStep = clamp(
        half.y * 1.35 / Math.max(1, siblings.length - 1),
        2.35,
        4.2,
      );
      let offset = (index - (siblings.length - 1) / 2) * portStep;
      if (port.direction === "out" && port.port_id === "output" && primitiveNodeKind(node) === "agent") {
        // Keep Resident results below the central action, including in embedded
        // editors. Edges share this position with the interactive port.
        offset = Math.max(half.y * 0.55, QUICK_FORK_PORT_CLEARANCE_PX / worldSize.height * 100);
      } else if (port.direction === "out" && nodeHasQuickFork(node)) {
        // The quick-fork button owns the Agent's right-edge midpoint. Keep
        // protocol outputs above/below that stable action instead of stacking
        // a higher-z-index port over the original "+" control.
        const centerClearance = QUICK_FORK_PORT_CLEARANCE_PX / worldSize.height * 100;
        const upperCount = Math.ceil(siblings.length / 2);
        offset = index < upperCount
          ? -centerClearance - (upperCount - index - 1) * portStep
          : centerClearance + (index - upperCount) * portStep;
      }
      return {
        x: position.x + (port.direction === "in" ? -1 : 1) * (half.x + 0.28),
        y: position.y + offset,
      };
    }

    function updatePortGeometry() {
      layers.nodes.selectAll("button.hatgPort")
        .style("left", port => `${portPosition(port).x}%`)
        .style("top", port => `${portPosition(port).y}%`);
    }

    function renderPorts(nodes, { geometryChanged = true } = {}) {
      const ports = nodes.flatMap(portsForNode);
      layers.nodes.selectAll("button.hatgPort")
        .data(ports, port => port.key)
        .join(
          enter => enter.append("button").attr("type", "button"),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", port => `hatgPort ${port.direction} ${port.type}`)
        .attr("data-port-node-id", port => port.node_id)
        .attr("data-port-node-kind", port => port.node_kind)
        .attr("data-port-id", port => port.port_id)
        .attr("data-port-type", port => port.type)
        .attr("data-port-direction", port => port.direction)
        .attr("data-node-action", "program-port")
        .attr("aria-label", port => (
          port.tooltip
            ? `${port.label} 端口：${port.tooltip}`
            : `${port.direction === "out" ? "输出" : "输入"} ${port.label}`
        ))
        .attr("title", null)
        .attr("data-portal-tooltip", port => (
          port.tooltip
          || `${port.direction === "out" ? "从这里开始建立连线" : "把兼容连线接到这里"}。`
        ))
        .property("disabled", model.readOnly)
        .style("position", "absolute")
        .style("transform", "translate(-50%, -50%)")
        .style("pointer-events", model.readOnly ? "none" : null)
        .text(port => port.label);
      if (geometryChanged) updatePortGeometry();
      layers.nodes.selectAll(".hatgNode")
        .attr("aria-keyshortcuts", node => !model.readOnly && portsForNode(node).some(port => port.direction === "out") ? "ArrowRight" : null);
      syncWireDraftPresentation();
    }

    // Cards, menus and ports are recreated as the graph changes. Handle their
    // ordinary DOM events at the stable layer; D3's node drag gesture remains
    // attached to each card.
    const delegatedNodeEvents = [
      "click", "dblclick", "contextmenu", "pointerenter", "pointerleave",
      "focusin", "focusout",
    ];
    function onNodeLayerEvent(event) {
      const target = event.target instanceof Element ? event.target : null;
      const control = target?.closest("button.hatgPort, button.hatgNodeOpenMenu, .hatgNode");
      if (!control || !nodeLayer.contains(control)) return;
      const port = control.classList.contains("hatgPort") ? control.__data__ : null;
      const menuItem = control.classList.contains("hatgNodeOpenMenu") ? control.__data__ : null;
      const node = control.classList.contains("hatgNode") ? control.__data__ : null;
      if (!port && !menuItem && !node) return;
      if (node && isWorkspaceNode(node)) {
        if (["click", "dblclick", "contextmenu"].includes(event.type)) {
          event.preventDefault();
          event.stopPropagation();
          if (event.type === "click" && node.kind === "workspace" && event.detail < 2) {
            dispatchNodeClick(node, event, options.onWorkspaceClick, typeof options.onWorkspaceDoubleClick === "function");
          } else if (event.type === "dblclick" && node.kind === "workspace") {
            cancelPendingNodeClick(String(node.id || ""));
            options.onWorkspaceDoubleClick?.(node, event);
          }
        }
        return;
      }

      if (event.type === "pointerenter" || event.type === "pointerleave"
          || event.type === "focusin" || event.type === "focusout") {
        if ((event.type === "pointerenter" || event.type === "pointerleave")
            && event.target !== control) return;
        if (event.relatedTarget instanceof Node && control.contains(event.relatedTarget)) return;
        const nodeId = port?.node_id || menuItem?.node.id || node?.id;
        if (event.type === "pointerenter") revealHoveredPorts(nodeId);
        else if (event.type === "pointerleave") concealHoveredPorts(nodeId, event.relatedTarget);
        else if (event.type === "focusin") {
          if (!node || control.matches(":focus-visible")) revealFocusedPorts(nodeId);
        } else concealFocusedPorts(nodeId, event.relatedTarget);
        return;
      }

      if (event.type === "click") {
        if (node && connectToItem(node, event)) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        if (port) {
          event.preventDefault();
          event.stopPropagation();
          if (!model.readOnly) options.onPortClick?.(port, event, {
            wireDraft: model.wireDraft,
            compatible: portCompatibility(port),
          });
        } else if (menuItem) {
          event.preventDefault();
          event.stopPropagation();
          options.onNodeOpenMenu?.(menuItem.node, menuItem.menu, event, control);
        } else {
          event.stopPropagation();
          if (event.shiftKey || event.metaKey || event.ctrlKey) {
            flushPendingNodeClicks();
            options.onNodeClick?.(node, event);
          } else {
            dispatchNodeClick(node, event, options.onNodeClick, typeof options.onNodeDoubleClick === "function");
          }
        }
        return;
      }
      if (event.type === "dblclick") {
        event.preventDefault();
        event.stopPropagation();
        if (model.wireDraft) return;
        if (node) {
          const nodeId = String(node.id || "");
          cancelPendingNodeClick(nodeId);
          options.onNodeDoubleClick?.(node, event);
        }
        return;
      }
      if (event.type === "contextmenu" && node) {
        const menu = resolveNodeOpenMenu(node);
        if (!menu) return;
        event.preventDefault();
        event.stopPropagation();
        options.onNodeOpenMenu?.(node, menu, event, control);
        return;
      }
    }
    delegatedNodeEvents.forEach(type => nodeLayer.addEventListener(
      type, onNodeLayerEvent, type === "pointerenter" || type === "pointerleave",
    ));

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
      const nodeSignature = model.nodes.map(node => {
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
        model.viewMode,
        model.readOnly ? "readonly" : "editable",
        semanticZoomLevel || "operate",
        nodeSignature,
        edgeSignature,
      ].join("\u0001");
    }

    function render(nextModel = {}) {
      const readOnly = Boolean(nextModel.readOnly);
      const layoutInteractive = typeof nextModel.layoutInteractive === "boolean"
        ? nextModel.layoutInteractive
        : !readOnly;
      model = {
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
      nodeMap = new Map(model.nodes.map(node => [node.id, node]));
      edgeById = new Map(model.edges.map(edge => [edge.id, edge]));
      if (quickForkMenu) {
        const source = nodeMap.get(quickForkSourceId);
        if (!source || !nodeHasQuickFork(source) || model.quickForkNodeId) closeQuickForkMenu();
        else quickForkPopover?.reposition();
      }
      portsByNodeId = new Map();
      model.wireDraft = normalizeWireDraft(model.wireDraft);
      const edges = validEdges();
      const nextGeometryInputSignature = currentGeometryInputSignature(edges);
      let geometryChanged = nextGeometryInputSignature !== geometryInputSignature;
      const nodeContentChanged = renderNodes(model.nodes);
      geometryChanged = geometryChanged || nodeContentChanged;
      if (geometryChanged) {
        // Node controls were placed before this measurement.
        refreshNodeExtentCache();
        syncNodeHoverActionOffsets();
      }
      renderFunctionBlockBoundaries();
      renderNodeHoverActions(model.nodes);
      renderNodeOpenMenus(model.nodes);
      if (geometryChanged) {
        refreshParallelEdgeLanes(edges);
        invalidateEdgeGeometry();
        resolveEdgeLabelCollisions(edges);
      }
      renderEdges(edges, { geometryChanged });
      renderLabels(edges);
      renderPorts(model.nodes, { geometryChanged });
      renderMinimap({ geometryChanged });
      refreshInteractionIndexes(edges);
      syncPortOwnerVisibility({ full: geometryChanged });
      geometryInputSignature = nextGeometryInputSignature;
      root.dataset.nodeCount = String(model.nodes.length);
      root.dataset.edgeCount = String(edges.length);
      root.dataset.overviewNodeCount = String(model.overviewNodes.length || model.nodes.length);
      root.dataset.workspaceCount = String(model.nodes.filter(node => node.kind === "workspace").length);
      root.dataset.navigationFocus = model.navigationFocus ? "true" : "false";
      root.dataset.viewMode = model.viewMode;
      root.dataset.readOnly = model.readOnly ? "true" : "false";
      root.dataset.layoutInteractive = model.layoutInteractive ? "true" : "false";
      root.dataset.selectionEnabled = model.selectionEnabled ? "true" : "false";
      root.dataset.selectionCount = String(model.selectedNodeIds.length);
      root.dataset.programWireCount = String(edges.filter(edge => edge?._program_wire).length);
      if (centerOnNextRender) {
        centerOnNextRender = false;
        fit({ duration: 0, maxScale: 1 });
      }
      root.dispatchEvent(new CustomEvent("hatg:render", {
        detail: { nodeCount: model.nodes.length, edgeCount: edges.length, contextId },
      }));
    }

    function patchRuntimePresentation(nextModel = {}) {
      const nextNodes = Array.isArray(nextModel.nodes) ? nextModel.nodes : [];
      const nextOverviewNodes = Array.isArray(nextModel.overviewNodes)
        ? nextModel.overviewNodes
        : nextNodes;
      if (!Array.isArray(nextNodes) || nextNodes.length !== model.nodes.length) return false;
      const nextById = new Map(nextNodes.map(node => [String(node?.id || ""), node]));
      const nextOverviewById = new Map(
        nextOverviewNodes.map(node => [String(node?.id || ""), node]),
      );
      if (model.nodes.some(node => !nextById.has(String(node.id || "")))) return false;
      if (
        nextOverviewNodes.length !== model.overviewNodes.length
        || model.overviewNodes.some(node => !nextOverviewById.has(String(node.id || "")))
      ) return false;

      const nextEdges = Array.isArray(nextModel.edges) ? nextModel.edges : model.edges;
      const nextOverviewEdges = Array.isArray(nextModel.overviewEdges)
        ? nextModel.overviewEdges
        : model.overviewEdges;
      // Runtime can introduce/remove relations as well as change their labels.
      // Structural render owns paths, ports, interaction indexes and minimap
      // geometry. Reject that change before mutating the current render model.
      if (
        edgeGeometryInputSignature(nextEdges) !== edgeGeometryInputSignature(model.edges)
        || edgeGeometryInputSignature(nextOverviewEdges) !== edgeGeometryInputSignature(model.overviewEdges)
      ) return false;
      const edgesChanged = JSON.stringify([nextEdges, nextOverviewEdges])
        !== JSON.stringify([model.edges, model.overviewEdges]);

      if (nextNodes.some(node => !primaryNodeElementsById.has(String(node.id)))) return false;

      model.nodes = model.nodes.map(node => nextById.get(String(node.id)));
      model.overviewNodes = model.overviewNodes.map(node => (
        nextOverviewById.get(String(node.id || ""))
      ));
      model.edges = nextEdges;
      edgeById = new Map(model.edges.map(edge => [edge.id, edge]));
      model.overviewEdges = nextOverviewEdges;
      nodeMap = new Map(model.nodes.map(node => [node.id, node]));

      const changedNodeIds = new Set();
      for (const node of model.nodes) {
        const nodeId = String(node.id || "");
        const target = primaryNodeElementsById.get(nodeId);
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
        if (ACTIVE_STATUSES.has(status)) target.dataset.runtimeEffect = "activity-rail";
        else delete target.dataset.runtimeEffect;
        if (target.__hatgNodeContent !== content) {
          target.innerHTML = content;
          target.__hatgNodeContent = content;
        }
        const minimapNode = minimapNodeElementsById.get(nodeId);
        if (minimapNode) {
          if (contextHeat) minimapNode.setAttribute("data-context-heat", contextHeat);
          else minimapNode.removeAttribute("data-context-heat");
        }
        changedNodeIds.add(nodeId);
      }
      for (const node of model.overviewNodes) {
        const nodeId = String(node.id || "");
        const minimapNode = minimapNodeElementsById.get(nodeId);
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

    function patchSelection(nextSelection = {}) {
      const nextSelectedNodeId = String(nextSelection.selectedNodeId || "");
      const nextSelectedNodeIds = Array.isArray(nextSelection.selectedNodeIds)
        ? nextSelection.selectedNodeIds.map(String)
        : [];
      const nextSelectedEdgeId = String(nextSelection.selectedEdgeId || "");
      if (nextSelectedNodeId && !nodeMap.has(nextSelectedNodeId)) return false;
      if (nextSelectedNodeIds.some(nodeId => !nodeMap.has(nodeId))) return false;
      if (
        nextSelectedEdgeId
        && !model.edges.some(edge => edgeSelectionId(edge) === nextSelectedEdgeId)
      ) return false;

      const previousSelectedNodeId = model.selectedNodeId;
      const previousSelectedNodeIds = new Set([
        ...(model.selectedNodeIds || []),
        ...(previousSelectedNodeId ? [previousSelectedNodeId] : []),
      ]);
      const previousSelectedEdgeId = model.selectedEdgeId;
      const selectedNodeIds = new Set([
        ...nextSelectedNodeIds,
        ...(nextSelectedNodeId ? [nextSelectedNodeId] : []),
      ]);
      const changedNodeIds = new Set(
        [...previousSelectedNodeIds, ...selectedNodeIds].filter(
          nodeId => previousSelectedNodeIds.has(nodeId) !== selectedNodeIds.has(nodeId),
        ),
      );
      if ([...changedNodeIds].some(nodeId => !primaryNodeElementsById.has(nodeId))) {
        return false;
      }

      model.selectedNodeId = nextSelectedNodeId;
      model.selectedNodeIds = nextSelectedNodeIds;
      model.selectedEdgeId = nextSelectedEdgeId;

      changedNodeIds.forEach(nodeId => {
        const element = primaryNodeElementsById.get(nodeId);
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
          .classed("selected", node => node.id === model.selectedNodeId)
          .attr("r", node => node.id === model.selectedNodeId ? 2.35 : 1.55);
      }
      syncPortOwnerVisibility();
      root.dataset.selectionCount = String(model.selectedNodeIds.length);
      return true;
    }

    function semanticZoomForScale(scale) {
      if (scale < 0.68) return "overview";
      if (scale >= 1.38) return "inspect";
      return "operate";
    }

    function syncSemanticZoom() {
      const nextLevel = semanticZoomForScale(transform.k);
      const changed = semanticZoomLevel !== nextLevel;
      if (!changed) return false;
      semanticZoomLevel = nextLevel;
      root.dataset.semanticZoom = nextLevel;
      root.setAttribute("aria-description", `${SEMANTIC_ZOOM[nextLevel].title}：${SEMANTIC_ZOOM[nextLevel].hint}`);
      layers.nodes.selectAll(".hatgNode").attr("data-semantic-level", nextLevel);
      semanticZoomRoot?.querySelectorAll?.("[data-semantic-zoom]").forEach(button => {
        const active = button.dataset.semanticZoom === nextLevel;
        button.classList.toggle("active", active);
        if (active) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
      if (semanticZoomTitle) semanticZoomTitle.textContent = SEMANTIC_ZOOM[nextLevel].title;
      if (semanticZoomHint) semanticZoomHint.textContent = SEMANTIC_ZOOM[nextLevel].hint;
      options.onSemanticZoomChange?.(nextLevel, { ...SEMANTIC_ZOOM[nextLevel], scale: transform.k });
      return true;
    }

    function syncZoomReadout() {
      const text = `${Math.round(transform.k * 100)}%`;
      if (zoomValue && zoomValue.textContent !== text) zoomValue.textContent = text;
      const scale = transform.k.toFixed(3);
      if (root.dataset.zoom !== scale) root.dataset.zoom = scale;
    }

    function syncZoomDetail() {
      const nextLod = transform.k < 0.62 ? "compact" : transform.k < 0.9 ? "reduced" : "detail";
      const changed = root.dataset.lod !== nextLod;
      if (changed) root.dataset.lod = nextLod;
      const semanticChanged = syncSemanticZoom();
      if (changed || semanticChanged) updateGeometry({ measureNodes: true });
      return changed || semanticChanged;
    }

    function syncGridPosition() {
      const gridSize = 28 * transform.k;
      root.style.setProperty("--hatg-grid-size", `${gridSize}px`);
      root.style.setProperty("--hatg-grid-x", `${transform.x % gridSize}px`);
      root.style.setProperty("--hatg-grid-y", `${transform.y % gridSize}px`);
    }

    function applyTransform(nextTransform) {
      const previousScale = transform.k;
      transform = nextTransform;
      world.style.transform = `translate(${transform.x}px, ${transform.y}px) scale(${transform.k})`;
      quickForkPopover?.reposition();
      const scaleChanged = !root.dataset.zoom
        || Math.abs(previousScale - transform.k) > 1e-6;
      let lodChanged = false;
      if (scaleChanged) {
        syncZoomReadout();
        invalidateEdgeGeometry();
      }
      // The world keeps its geometry throughout a viewport gesture. Changing
      // card sizes here would force layout and reroute every edge mid-frame.
      if (!viewportMoving) {
        syncGridPosition();
        lodChanged = syncZoomDetail();
      }
      updateMinimapViewport();
      root.dispatchEvent(new CustomEvent("hatg:transform", {
        detail: { x: transform.x, y: transform.y, k: transform.k, contextId },
      }));
      return lodChanged;
    }

    function applyTransformImmediately(nextTransform) {
      cancelFocusAnimation();
      rootSelection.property("__zoom", nextTransform);
      return applyTransform(nextTransform);
    }

    function persistTransform() {
      if (!contextId) return;
      const center = viewCenter();
      const savedView = {
        centerX: center.x,
        centerY: center.y,
        k: transform.k,
        updatedAt: Date.now(),
      };
      options.onViewportChange?.(contextId, { ...savedView });
    }

    function syncNodeSelectionPresentation(selectedIds = model.selectedNodeIds) {
      const selected = new Set(selectedIds);
      layers.nodes.selectAll(".hatgNode")
        .classed("selected", node => (
          node.id === model.selectedNodeId || selected.has(node.id)
        ))
        .attr("aria-pressed", node => (
          node.id === model.selectedNodeId || selected.has(node.id)
        ) ? "true" : "false");
      syncNodeOpenMenuSelectionPresentation(selected);
    }

    function syncNodeOpenMenuSelectionPresentation(selectedIds = model.selectedNodeIds) {
      const selected = selectedIds instanceof Set ? selectedIds : new Set(selectedIds);
      layers.nodes.selectAll("button.hatgNodeOpenMenu")
        .attr("data-node-owner-selected", item => (
          item.node.id === model.selectedNodeId || selected.has(item.node.id)
        ) ? "true" : null);
    }

    function marqueeBounds(state = marqueeState) {
      if (!state) return null;
      const left = Math.min(state.startX, state.currentX);
      const top = Math.min(state.startY, state.currentY);
      return {
        left,
        top,
        right: Math.max(state.startX, state.currentX),
        bottom: Math.max(state.startY, state.currentY),
        width: Math.abs(state.currentX - state.startX),
        height: Math.abs(state.currentY - state.startY),
      };
    }

    function marqueeHitIds(bounds) {
      if (!bounds) return [];
      const rootBounds = root.getBoundingClientRect();
      const selectable = new Set(model.selectableNodeIds);
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
      model.nodes.forEach(node => {
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
        .map(nodeId => nodeMap.get(nodeId))
        .filter(Boolean);
      options.onBoxSelect?.(selectedNodes, event, { additive: true });
    }

    function onSelectionPointerDown(event) {
      if (
        !model.selectionEnabled
        || !model.layoutInteractive
        || model.readOnly
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
        baseSelectionIds: [...model.selectedNodeIds],
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

    function zoomFilter(event) {
      if (event.type === "wheel") return true;
      if (event.button !== undefined && event.button !== 0) return false;
      if (event.shiftKey && model.selectionEnabled) return false;
      return !event.target?.closest?.(".hatgNode, .edgeLabelBtn, .graphEmpty, button, input, textarea, select");
    }

    const zoomBehavior = d3.zoom()
      .scaleExtent(scaleExtent)
      .filter(zoomFilter)
      .on("start.hatg", event => {
        if (event.sourceEvent) navigationSequence += 1;
        window.cancelAnimationFrame(zoomDetailFrame);
        zoomDetailFrame = 0;
        viewportMoving = true;
        root.classList.add("isViewportMoving");
      })
      .on("zoom.hatg", event => applyTransform(event.transform))
      .on("end.hatg", event => {
        viewportMoving = false;
        root.classList.remove("isViewportMoving");
        syncGridPosition();
        window.cancelAnimationFrame(zoomDetailFrame);
        zoomDetailFrame = window.requestAnimationFrame(() => {
          zoomDetailFrame = 0;
          syncZoomDetail();
        });
        if (event.sourceEvent) persistTransform();
      });
    rootSelection.call(zoomBehavior).on("dblclick.zoom", null);
    root.addEventListener("pointerdown", cancelFocusAnimation, true);
    root.addEventListener("wheel", cancelFocusAnimation, { capture: true, passive: true });
    root.addEventListener("pointerdown", onSelectionPointerDown, true);
    window.addEventListener("pointermove", onSelectionPointerMove, true);
    window.addEventListener("pointerup", onSelectionPointerUp, true);
    window.addEventListener("pointercancel", onSelectionPointerCancel, true);
    window.addEventListener("pointermove", onCandidateForkPointerMove, true);
    window.addEventListener("pointerup", onCandidateForkPointerUp, true);
    window.addEventListener("pointercancel", onCandidateForkPointerCancel, true);

    function cancelFocusAnimation() {
      if (!focusAnimation) return;
      const visual = new DOMMatrixReadOnly(window.getComputedStyle(world).transform);
      focusAnimation.cancel();
      focusAnimation = null;
      rootSelection.call(
        zoomBehavior.transform,
        d3.zoomIdentity.translate(visual.m41, visual.m42).scale(visual.a),
      );
    }

    function animateTransform(target, duration = 220, persist = false, compositor = false) {
      rootSelection.interrupt();
      cancelFocusAnimation();
      window.cancelAnimationFrame(zoomDetailFrame);
      zoomDetailFrame = 0;
      const effectiveDuration = animationDuration(duration);
      if (effectiveDuration <= 0) {
        rootSelection.call(zoomBehavior.transform, target);
        if (persist) persistTransform();
      } else if (compositor && world.animate) {
        const from = window.getComputedStyle(world).transform;
        rootSelection.call(zoomBehavior.transform, target);
        focusAnimation = world.animate(
          [{ transform: from }, { transform: world.style.transform }],
          { duration: effectiveDuration, easing: "ease-out" },
        );
        focusAnimation.onfinish = () => { focusAnimation = null; };
        if (persist) persistTransform();
      } else {
        const transition = rootSelection.transition().duration(effectiveDuration).call(zoomBehavior.transform, target);
        if (persist) transition.on("end.persist", persistTransform);
      }
    }

    function zoomBy(factor) {
      rootSelection.interrupt().call(zoomBehavior.scaleBy, factor);
      persistTransform();
    }

    function setSemanticZoom(level, options = {}) {
      const semantic = SEMANTIC_ZOOM[level];
      if (!semantic) return;
      if (level !== "overview" && nodeMap.has(model.selectedNodeId)) {
        focusNode(model.selectedNodeId, { scale: semantic.scale, duration: number(options.duration, 260), persist: true });
        return;
      }
      const viewport = currentViewportSize();
      viewportSize = viewport;
      animateTransform(
        transformForCenter(viewCenter(transform, viewport), semantic.scale, viewport),
        number(options.duration, 260),
        true,
      );
    }

    function reset(options = {}) {
      center({ ...options, scale: 1 });
    }

    function graphPixelBounds(nodeIds = null) {
      const wanted = Array.isArray(nodeIds) && nodeIds.length ? new Set(nodeIds.map(String)) : null;
      const nodes = wanted ? model.nodes.filter(node => wanted.has(String(node.id))) : model.nodes;
      // A card reaches its measured half size around its center, plus a small
      // margin for ports; never less than a standard 154 x 94 card.
      const boxes = (nodes.length ? nodes : model.nodes).map(node => {
        const position = nodePosition(node);
        const half = nodeHalfExtents(node);
        const x = position.x / 100 * worldSize.width;
        const y = position.y / 100 * worldSize.height;
        const halfWidth = Math.max(84, half.x / 100 * worldSize.width + 7);
        const halfHeight = Math.max(55, half.y / 100 * worldSize.height + 8);
        return { minX: x - halfWidth, maxX: x + halfWidth, minY: y - halfHeight, maxY: y + halfHeight };
      });
      for (const block of model.functionBlockBoundaries || []) {
        if (wanted && !block.member_ids.some(id => wanted.has(id))) continue;
        const bounds = functionBlockBoundaryBounds(block);
        if (bounds) boxes.push({ minX: bounds.left * worldSize.width / 100, maxX: bounds.right * worldSize.width / 100,
          minY: bounds.top * worldSize.height / 100, maxY: bounds.bottom * worldSize.height / 100 });
      }
      if (!boxes.length) {
        return {
          minX: 0,
          maxX: worldSize.width,
          minY: 0,
          maxY: worldSize.height,
        };
      }
      return {
        minX: d3.min(boxes, box => box.minX),
        maxX: d3.max(boxes, box => box.maxX),
        minY: d3.min(boxes, box => box.minY),
        maxY: d3.max(boxes, box => box.maxY),
      };
    }

    function graphFitsViewport(candidate = transform, viewport = viewportSize, margin = 10, nodeIds = null) {
      if (!model.nodes.length) return true;
      const bounds = graphPixelBounds(nodeIds);
      const insets = currentViewportInsets(viewport);
      const left = candidate.x + bounds.minX * candidate.k;
      const right = candidate.x + bounds.maxX * candidate.k;
      const top = candidate.y + bounds.minY * candidate.k;
      const bottom = candidate.y + bounds.maxY * candidate.k;
      return left >= insets.left + margin
        && right <= viewport.width - insets.right - margin
        && top >= insets.top + margin
        && bottom <= viewport.height - insets.bottom - margin;
    }

    function center(options = {}) {
      const viewport = currentViewportSize();
      viewportSize = viewport;
      const bounds = graphPixelBounds();
      const centerPoint = {
        x: (bounds.minX + bounds.maxX) / 2 / worldSize.width * 100,
        y: (bounds.minY + bounds.maxY) / 2 / worldSize.height * 100,
      };
      animateTransform(
        transformForCenter(centerPoint, number(options.scale, transform.k), viewport),
        number(options.duration, 220),
        options.persist === true,
      );
    }

    function fit(options = {}) {
      // A collapsed panel has no usable viewport. Fitting its synthetic
      // 1px size would leave the graph at minimum zoom when it reopens.
      if (!root.clientWidth || !root.clientHeight) {
        pendingViewportFit = { ...options };
        return;
      }
      pendingViewportFit = null;
      if (!model.nodes.length) return reset(options);
      const viewport = currentViewportSize();
      viewportSize = viewport;
      const { width, height } = viewport;
      const insets = currentViewportInsets(viewport);
      const availableWidth = Math.max(1, width - insets.left - insets.right);
      const availableHeight = Math.max(1, height - insets.top - insets.bottom);
      const padding = clamp(
        number(options.padding, availableWidth <= 720 ? 40 : 72),
        20,
        Math.min(availableWidth, availableHeight) / 3,
      );
      // `nodeIds` fits a part of the graph, such as an expanded Family.
      const { minX, maxX, minY, maxY } = graphPixelBounds(options.nodeIds);
      const contentWidth = Math.max(168, maxX - minX);
      const contentHeight = Math.max(110, maxY - minY);
      const minimumScale = clamp(
        number(options.minScale, scaleExtent[0]),
        scaleExtent[0],
        scaleExtent[1],
      );
      const targetScale = clamp(Math.min(
        (availableWidth - padding * 2) / contentWidth,
        (availableHeight - padding * 2) / contentHeight,
        number(options.maxScale, 1.45),
      ), minimumScale, scaleExtent[1]);
      const centerX = (minX + maxX) / 2;
      const centerY = (minY + maxY) / 2;
      const target = d3.zoomIdentity
        .translate(
          insets.left + availableWidth / 2 - centerX * targetScale,
          insets.top + availableHeight / 2 - centerY * targetScale,
        )
        .scale(targetScale);
      animateTransform(target, number(options.duration, 260), options.persist === true);
    }

    function focusNode(nodeId, options = {}) {
      const node = nodeMap.get(nodeId);
      if (!node) return;
      const viewport = currentViewportSize();
      viewportSize = viewport;
      const insets = currentViewportInsets(viewport);
      const availableWidth = Math.max(1, viewport.width - insets.left - insets.right);
      const availableHeight = Math.max(1, viewport.height - insets.top - insets.bottom);
      const bounds = graphPixelBounds([nodeId]);
      const targetScale = clamp(Math.min(
        number(options.scale, Math.max(1.15, transform.k)),
        Math.max(1, availableWidth - 20) / (bounds.maxX - bounds.minX),
        Math.max(1, availableHeight - 20) / (bounds.maxY - bounds.minY),
      ), scaleExtent[0], scaleExtent[1]);
      const target = d3.zoomIdentity
        .translate(
          insets.left + availableWidth / 2 - (bounds.minX + bounds.maxX) / 2 * targetScale,
          insets.top + availableHeight / 2 - (bounds.minY + bounds.maxY) / 2 * targetScale,
        )
        .scale(targetScale);
      animateTransform(target, number(options.duration, 240), options.persist === true, options.compositor === true);
    }

    function ensureNodeVisible(nodeId, options = {}) {
      if (!nodeMap.has(nodeId)) return;
      // Settle any structural fit before deciding what is visible or saving
      // its camera. A later animation frame must not replace this reveal.
      rootSelection.interrupt();
      cancelFocusAnimation();
      if (graphFitsViewport(transform, currentViewportSize(), 10, [nodeId])) {
        // Structural changes may already have fitted the new graph. Save that
        // view when requested, even if revealing the retained card needn't pan.
        if (options.persist) persistTransform();
        return;
      }
      // Placement may avoid an occupied position outside the current viewport.
      // Reveal the committed card without changing coordinates or zooming in.
      focusNode(nodeId, { scale: transform.k, duration: 0, persist: true });
    }

    function centerAt(x, y, options = {}) {
      const viewport = currentViewportSize();
      viewportSize = viewport;
      const scale = clamp(number(options.scale, transform.k), scaleExtent[0], scaleExtent[1]);
      const insets = currentViewportInsets(viewport);
      const centered = transformForCenter({ x, y }, scale, viewport);
      const target = d3.zoomIdentity.translate(
        centered.x + (insets.left - insets.right) / 2,
        centered.y + (insets.top - insets.bottom) / 2,
      ).scale(scale);
      animateTransform(target, number(options.duration, 160), true);
    }

    // Returns to a view read with getTransform, such as the canvas before a Family view.
    function setTransform(view = {}, options = {}) {
      const k = clamp(number(view.k, transform.k), scaleExtent[0], scaleExtent[1]);
      animateTransform(
        d3.zoomIdentity.translate(number(view.x, transform.x), number(view.y, transform.y)).scale(k),
        number(options.duration, 220),
      );
    }

    function setContext(nextContextId) {
      closeQuickForkMenu();
      pendingViewportFit = null;
      contextId = String(nextContextId || "");
      const stored = contextId ? options.readViewport?.(contextId) : null;
      viewportSize = currentViewportSize();
      const k = clamp(number(stored?.k, 1), scaleExtent[0], scaleExtent[1]);
      const next = transformForCenter({
        x: number(stored?.centerX, 50),
        y: number(stored?.centerY, 50),
      }, k, viewportSize);
      centerOnNextRender = !stored;
      animateTransform(next, 0);
      return Boolean(stored);
    }

    function onKeydown(event) {
      if (event.key === "Escape") cancelWireGesture();
      if (event.key === "Escape" && model.wireDraft) {
        event.preventDefault();
        event.stopPropagation();
        options.onWireDraftCancel?.(model.wireDraft, event);
        return;
      }
      if (!model.readOnly && event.key === "ArrowRight") {
        const node = event.target?.closest?.(".hatgNode")?.__data__;
        const output = node && (portElementsByNode.get(node.id) || [])
          .find(element => element.__data__.direction === "out");
        if (output && !model.wireDraft) {
          event.preventDefault();
          revealFocusedPorts(node.id);
          output.focus({ preventScroll: true });
          return;
        }
      }
      if (event.key === "ArrowLeft") {
        const port = event.target?.closest?.("button.hatgPort")?.__data__;
        if (port) {
          event.preventDefault();
          primaryNodeElementsById.get(port.node_id)?.focus({ preventScroll: true });
          return;
        }
      }
      if (event.target !== root) return;
      if (["+", "=", "-", "_", "0", "f"].includes(event.key.toLowerCase())) navigationSequence += 1;
      if (event.key === "Escape" && model.selectedNodeIds.length) {
        event.preventDefault();
        options.onSelectionCancel?.(event);
      } else if (["+", "="].includes(event.key)) {
        event.preventDefault();
        zoomBy(1.25);
      } else if (["-", "_"].includes(event.key)) {
        event.preventDefault();
        zoomBy(0.8);
      } else if (event.key === "0") {
        event.preventDefault();
        reset({ persist: true });
      } else if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fit({ persist: true });
      }
    }
    root.addEventListener("keydown", onKeydown);

    function onBackgroundClick(event) {
      if (event.defaultPrevented || suppressBackgroundClick || event.shiftKey) return;
      if (event.target?.closest?.(".hatgNode, .edgeLabelBtn, button, input, textarea, select, a")) return;
      if (model.wireDraft) {
        options.onWireDraftCancel?.(model.wireDraft, event);
        return;
      }
      options.onBackgroundClick?.(event);
    }
    root.addEventListener("click", onBackgroundClick);

    function onRootPaletteDragOver(event) {
      if (!primitiveTransfer(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    }

    function onRootPaletteDrop(event) {
      const primitive = primitiveTransfer(event);
      if (!primitive) return;
      event.preventDefault();
      event.stopPropagation();
      const position = screenToWorld(event.clientX, event.clientY);
      // World coordinates are unbounded, including palette drops after zoom/pan.
      const normalizedPosition = {
        x: Math.round(position.x * 100) / 100,
        y: Math.round(position.y * 100) / 100,
      };
      if (primitive) {
        const kind = event.dataTransfer?.getData(HATG_PRIMITIVE_TRANSFER_TYPE) || "";
        if (kind) options.onPrimitiveDrop?.(kind, normalizedPosition, event);
        return;
      }
    }

    root.addEventListener("dragover", onRootPaletteDragOver);
    root.addEventListener("drop", onRootPaletteDrop);

    const buttonBindings = [];
    function bindButton(target, action) {
      const button = element(target);
      if (!button) return;
      const navigate = event => {
        navigationSequence += 1;
        action(event);
      };
      button.addEventListener("click", navigate);
      buttonBindings.push([button, navigate]);
    }
    bindButton(options.zoomInButton, () => zoomBy(1.25));
    bindButton(options.zoomOutButton, () => zoomBy(0.8));
    bindButton(options.resetButton, () => reset({ persist: true }));
    bindButton(options.fitButton, () => fit({ ...options.fitOptions, persist: true }));
    semanticZoomRoot?.querySelectorAll?.("[data-semantic-zoom]").forEach(button => {
      bindButton(button, () => setSemanticZoom(button.dataset.semanticZoom));
    });

    const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      window.cancelAnimationFrame(resizeFrame);
      resizeFrame = window.requestAnimationFrame(() => {
        // Keep the last visible geometry while hidden, including its scale.
        if (!root.clientWidth || !root.clientHeight) return;
        if (pendingViewportFit) {
          fit({ ...pendingViewportFit, duration: 0 });
          return;
        }
        const nextViewport = currentViewportSize();
        if (nextViewport.width === viewportSize.width && nextViewport.height === viewportSize.height) return;
        const keptWholeGraphVisible = graphFitsViewport(transform, viewportSize);
        const center = viewCenter(transform, viewportSize);
        const previousScale = transform.k;
        viewportSize = nextViewport;
        const nextTransform = transformForCenter(center, previousScale, nextViewport);
        const geometryUpdated = applyTransformImmediately(nextTransform);
        if (keptWholeGraphVisible && !graphFitsViewport(nextTransform, nextViewport)) {
          fit({ duration: 0, maxScale: previousScale });
          return;
        }
        if (!geometryUpdated) updateGeometry({ measureNodes: true });
      });
    }) : null;
    resizeObserver?.observe(root);
    applyTransform(transform);
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
      getNavigationSequence: () => navigationSequence,
      refresh: updateGeometry,
      getTransform: () => ({ x: transform.x, y: transform.y, k: transform.k }),
      setTransform,
      getVisibleWorldBounds: () => {
        const viewport = currentViewportSize();
        return visibleWorldBounds(transform, viewport, currentViewportInsets(viewport));
      },
      getPlacementGeometry,
      getNodeSizes,
      getObjectSeparation,
      destroy() {
        cancelWireGesture();
        wirePreview.remove();
        root.removeEventListener("pointerdown", onWirePointerDown);
        root.removeEventListener("click", onWireGestureClick, true);
        document.removeEventListener("pointermove", onWirePointerMove);
        document.removeEventListener("pointerup", onWirePointerUp);
        document.removeEventListener("pointercancel", cancelWireGesture);
        closeQuickForkMenu();
        resizeObserver?.disconnect();
        systemMotionQuery?.removeEventListener?.("change", syncMotionPreference);
        window.removeEventListener("portal:user-settings-changed", syncMotionPreference);
        window.cancelAnimationFrame(resizeFrame);
        window.clearTimeout(portHoverClearTimer);
        for (const pending of pendingNodeClickTimers.values()) {
          window.clearTimeout(pending.timer);
        }
        pendingNodeClickTimers.clear();
        root.ownerDocument.removeEventListener("click", settleNodeSelection, true);
        delegatedNodeEvents.forEach(type => nodeLayer.removeEventListener(
          type, onNodeLayerEvent, type === "pointerenter" || type === "pointerleave",
        ));
        stopDragAutoPan();
        finishMarquee(null, { cancelled: true });
        root.removeEventListener("keydown", onKeydown);
        root.removeEventListener("click", onBackgroundClick);
        root.removeEventListener("pointerdown", cancelFocusAnimation, true);
        root.removeEventListener("wheel", cancelFocusAnimation, true);
        root.removeEventListener("pointerdown", onSelectionPointerDown, true);
        window.removeEventListener("pointermove", onSelectionPointerMove, true);
        window.removeEventListener("pointerup", onSelectionPointerUp, true);
        window.removeEventListener("pointercancel", onSelectionPointerCancel, true);
        window.removeEventListener("pointermove", onCandidateForkPointerMove, true);
        window.removeEventListener("pointerup", onCandidateForkPointerUp, true);
        window.removeEventListener("pointercancel", onCandidateForkPointerCancel, true);
        endCandidateForkDrag();
        root.removeEventListener("dragover", onRootPaletteDragOver);
      root.removeEventListener("drop", onRootPaletteDrop);
        rootSelection.on(".zoom", null).interrupt();
        focusAnimation?.cancel();
        focusAnimation = null;
        window.cancelAnimationFrame(zoomDetailFrame);
        minimapSelection?.on(".hatg", null).on(".drag", null);
        buttonBindings.forEach(([button, action]) => button.removeEventListener("click", action));
        selectionMarquee.remove();
      },
    };
  }

  window.PortalHatgGraph = Object.freeze({
    schema: "portal.hatg.graph.v1",
    computeBranchAwareLayout,
    computeWorkspaceGraphLayout,
    createGraph,
  });
})();
