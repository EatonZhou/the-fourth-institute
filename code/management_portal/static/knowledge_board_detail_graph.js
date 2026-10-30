// Knowledge board: the link graph around the open detail.

function detailGraphEnabled() {
  return !document.body.dataset.resourceDetailHideGraph;
}

const DETAIL_GRAPH_TYPE_COLORS = {
  decision: "#6684c6",
  question: "#bd8558",
  thinking: "#9b79c8",
  insight: "#4ca485",
  information: "#7b858f",
  idea: "#b39742",
  concept: "#3f97ad",
  plan: "#5d9b78",
  task: "#bd7063",
  experiment: "#a56c88",
};
const DETAIL_GRAPH_RELATION_COLORS = {
  source: "#8b96a3",
  related: "#45a392",
  supersedes: "#d07a59",
  derived_from: "#7f8fc8",
};

function detailGraphNodeColor(type) {
  return DETAIL_GRAPH_TYPE_COLORS[String(type || "").toLowerCase()] || "#78939a";
}

function detailGraphRelationColor(relation) {
  return DETAIL_GRAPH_RELATION_COLORS[String(relation || "").toLowerCase()] || "#87939d";
}

function resetDetailGraph() {
  state.detailGraph = { fileId: "", payload: null, loading: false, positions: {}, basePositions: null };
  renderDetailGraph();
}

function detailGraphRingPositions(root, first, second) {
  const size = 360;
  const center = { x: size / 2, y: size / 2 };
  const positions = new Map();
  if (root) positions.set(String(root.kb_id || ""), center);
  const placeRing = (items, radius) => {
    items.forEach((node, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index / Math.max(1, items.length));
      positions.set(String(node.kb_id || ""), {
        x: center.x + Math.cos(angle) * radius,
        y: center.y + Math.sin(angle) * radius,
      });
    });
  };
  placeRing(first, 84);
  placeRing(second, 148);
  return { size, positions };
}

function detailGraphSegmentsCross(a, b, c, d) {
  const cross = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const abC = cross(a, b, c);
  const abD = cross(a, b, d);
  const cdA = cross(c, d, a);
  const cdB = cross(c, d, b);
  const epsilon = 0.001;
  return ((abC > epsilon && abD < -epsilon) || (abC < -epsilon && abD > epsilon))
    && ((cdA > epsilon && cdB < -epsilon) || (cdA < -epsilon && cdB > epsilon));
}

function detailGraphCrossingCount(root, first, second, edges) {
  const positions = detailGraphRingPositions(root, first, second).positions;
  const uniqueEdges = new Map();
  for (const edge of edges) {
    const source = String(edge.source || "");
    const target = String(edge.target || "");
    if (source && target && source !== target) uniqueEdges.set(`${source}:${target}`, { source, target });
  }
  const segments = [...uniqueEdges.values()].filter(edge => positions.has(edge.source) && positions.has(edge.target));
  let crossings = 0;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      const a = segments[i];
      const b = segments[j];
      if (a.source === b.source || a.source === b.target || a.target === b.source || a.target === b.target) continue;
      if (detailGraphSegmentsCross(positions.get(a.source), positions.get(a.target), positions.get(b.source), positions.get(b.target))) crossings += 1;
    }
  }
  return crossings;
}

function optimizeDetailGraphLayerOrder(root, first, second, edges) {
  let currentFirst = first.slice();
  let currentSecond = second.slice();
  // Each attempted swap scores every edge pair. Two-hop graphs are not
  // necessarily small: cap total work and keep the preceding barycentric
  // ordering when exhaustive refinement would block document rendering.
  const edgePairs = Math.max(1, edges.length * (edges.length - 1) / 2);
  const evaluationLimit = Math.min(512, Math.floor(200000 / edgePairs));
  if (evaluationLimit < 2) return { first: currentFirst, second: currentSecond };
  let currentCost = detailGraphCrossingCount(root, currentFirst, currentSecond, edges);
  let evaluations = 1;
  // Greedy pair swaps converge quickly for the small two-level detail graph.
  for (let pass = 0; pass < 16; pass += 1) {
    let best = null;
    for (const layer of ["first", "second"]) {
      const items = layer === "first" ? currentFirst : currentSecond;
      for (let i = 0; i < items.length; i += 1) {
        for (let j = i + 1; j < items.length; j += 1) {
          if (evaluations >= evaluationLimit) {
            return best
              ? { first: best.first, second: best.second }
              : { first: currentFirst, second: currentSecond };
          }
          const candidateFirst = currentFirst.slice();
          const candidateSecond = currentSecond.slice();
          const candidate = layer === "first" ? candidateFirst : candidateSecond;
          [candidate[i], candidate[j]] = [candidate[j], candidate[i]];
          const cost = detailGraphCrossingCount(root, candidateFirst, candidateSecond, edges);
          evaluations += 1;
          if (cost < currentCost && (!best || cost < best.cost)) {
            best = { cost, first: candidateFirst, second: candidateSecond };
          }
        }
      }
    }
    if (!best) break;
    currentFirst = best.first;
    currentSecond = best.second;
    currentCost = best.cost;
  }
  return { first: currentFirst, second: currentSecond };
}

function detailGraphOrderedLayers(nodes, edges) {
  const byDepth = new Map([[0, []], [1, []], [2, []]]);
  const nodeById = new Map();
  for (const node of nodes) {
    const depth = Math.max(0, Math.min(2, Number(node.depth || 0)));
    const item = { ...node, depth };
    byDepth.get(depth).push(item);
    nodeById.set(String(item.kb_id || ""), item);
  }
  const titleOrder = (a, b) => String(a.title || a.kb_id || "").localeCompare(String(b.title || b.kb_id || ""));
  let first = (byDepth.get(1) || []).slice().sort(titleOrder);
  let second = (byDepth.get(2) || []).slice().sort(titleOrder);
  const parents = new Map(second.map(node => [String(node.kb_id || ""), []]));
  const children = new Map(first.map(node => [String(node.kb_id || ""), []]));
  for (const edge of edges) {
    const source = String(edge.source || "");
    const target = String(edge.target || "");
    if (nodeById.get(source)?.depth === 1 && nodeById.get(target)?.depth === 2) {
      parents.get(target)?.push(source);
      children.get(source)?.push(target);
    }
  }
  const averageIndex = (ids, indexById) => {
    const indexes = ids.map(id => indexById.get(id)).filter(index => Number.isFinite(index));
    if (!indexes.length) return Number.MAX_SAFE_INTEGER;
    return indexes.reduce((total, index) => total + index, 0) / indexes.length;
  };
  // Alternate the two layer orders so related nodes share a radial sector.
  for (let pass = 0; pass < 3; pass += 1) {
    const firstIndex = new Map(first.map((node, index) => [String(node.kb_id || ""), index]));
    second = second.slice().sort((a, b) => {
      const delta = averageIndex(parents.get(String(a.kb_id || "")) || [], firstIndex)
        - averageIndex(parents.get(String(b.kb_id || "")) || [], firstIndex);
      return delta || titleOrder(a, b);
    });
    const secondIndex = new Map(second.map((node, index) => [String(node.kb_id || ""), index]));
    first = first.slice().sort((a, b) => {
      const delta = averageIndex(children.get(String(a.kb_id || "")) || [], secondIndex)
        - averageIndex(children.get(String(b.kb_id || "")) || [], secondIndex);
      return delta || titleOrder(a, b);
    });
  }
  const root = (byDepth.get(0) || [])[0] || null;
  const optimized = optimizeDetailGraphLayerOrder(root, first, second, edges);
  return { root, first: optimized.first, second: optimized.second };
}

function detailGraphBasePositions(nodes, edges) {
  const { root, first, second } = detailGraphOrderedLayers(nodes, edges);
  return detailGraphRingPositions(root, first, second);
}

function detailGraphPositions(nodes, edges, manualPositions = {}, basePositions = null) {
  const { size, positions: automaticPositions } = basePositions
    ? { size: 360, positions: new Map(Object.entries(basePositions)) }
    : detailGraphBasePositions(nodes, edges);
  const positions = new Map(automaticPositions);
  for (const [fileId, point] of Object.entries(manualPositions || {})) {
    if (!positions.has(fileId)) continue;
    const x = Number(point?.x);
    const y = Number(point?.y);
    if (Number.isFinite(x) && Number.isFinite(y)) positions.set(fileId, { x, y });
  }
  return { size, positions };
}

function detailGraphLinePosition(source, target, offset) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const length = Math.hypot(dx, dy) || 1;
  const shiftX = (-dy / length) * offset;
  const shiftY = (dx / length) * offset;
  return {
    x1: source.x + shiftX,
    y1: source.y + shiftY,
    x2: target.x + shiftX,
    y2: target.y + shiftY,
  };
}

function syncDetailGraphSvgPositions(svg) {
  const graph = state.detailGraph || {};
  const nodes = Array.isArray(graph.payload?.nodes) ? graph.payload.nodes : [];
  const edges = Array.isArray(graph.payload?.edges) ? graph.payload.edges : [];
  const { positions } = detailGraphPositions(nodes, edges, graph.positions || {}, graph.basePositions);
  svg.querySelectorAll("[data-detail-graph-node]").forEach(circle => {
    const point = positions.get(circle.dataset.detailGraphNode || "");
    if (!point) return;
    circle.setAttribute("cx", point.x);
    circle.setAttribute("cy", point.y);
  });
  svg.querySelectorAll("[data-detail-graph-edge]").forEach(line => {
    const source = positions.get(line.dataset.detailGraphSource || "");
    const target = positions.get(line.dataset.detailGraphTarget || "");
    if (!source || !target) return;
    const point = detailGraphLinePosition(source, target, Number(line.dataset.detailGraphOffset || 0));
    line.setAttribute("x1", point.x1);
    line.setAttribute("y1", point.y1);
    line.setAttribute("x2", point.x2);
    line.setAttribute("y2", point.y2);
  });
}

function detailGraphPointerPosition(event, svg) {
  const rect = svg.getBoundingClientRect();
  const viewBox = svg.viewBox.baseVal;
  const width = viewBox.width || 360;
  const height = viewBox.height || 360;
  const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * width;
  const y = ((event.clientY - rect.top) / Math.max(1, rect.height)) * height;
  return {
    x: Math.max(8, Math.min(width - 8, x)),
    y: Math.max(8, Math.min(height - 8, y)),
  };
}

function renderDetailGraph() {
  if (!detailGraphEnabled()) {
    el.mGraphMeta.textContent = "";
    el.mGraph.innerHTML = "";
    return;
  }
  const graph = state.detailGraph || {};
  const payload = graph.payload;
  if (!state.detailFileId) {
    el.mGraphMeta.textContent = "";
    el.mGraph.innerHTML = "";
    return;
  }
  if (graph.loading) {
    el.mGraphMeta.textContent = "";
    el.mGraph.innerHTML = window.PortalI18n.markup('<div class="knowledgeRelationGraphEmpty">读取关系图谱…</div>');
    return;
  }
  const nodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const edges = Array.isArray(payload?.edges) ? payload.edges : [];
  if (!nodes.length) {
    el.mGraphMeta.textContent = "";
    el.mGraph.innerHTML = window.PortalI18n.markup('<div class="knowledgeRelationGraphEmpty">当前条目没有可定位的知识库连接。</div>');
    return;
  }
  el.mGraphMeta.textContent = `${nodes.length} nodes · ${edges.length} links`;
  if (!graph.basePositions) {
    graph.basePositions = Object.fromEntries(detailGraphBasePositions(nodes, edges).positions);
  }
  const { size, positions } = detailGraphPositions(nodes, edges, graph.positions || {}, graph.basePositions);
  const edgesByPair = new Map();
  for (const edge of edges) {
    const key = `${edge.source || ""}:${edge.target || ""}`;
    if (!edgesByPair.has(key)) edgesByPair.set(key, []);
    edgesByPair.get(key).push(edge);
  }
  const edgeSvg = [...edgesByPair.values()].flatMap(group => group.map((edge, index) => {
    const source = positions.get(String(edge.source || ""));
    const target = positions.get(String(edge.target || ""));
    if (!source || !target) return "";
    const offset = (index - (group.length - 1) / 2) * 3;
    const point = detailGraphLinePosition(source, target, offset);
    const relation = String(edge.relation || "related");
    return window.PortalI18n.html`<line class="detailGraphEdge" data-detail-graph-edge data-detail-graph-source="${escapeHtml(edge.source || "")}" data-detail-graph-target="${escapeHtml(edge.target || "")}" data-detail-graph-offset="${offset}" style="--graph-edge:${detailGraphRelationColor(relation)}" x1="${point.x1}" y1="${point.y1}" x2="${point.x2}" y2="${point.y2}"><title>${escapeHtml(relation)}</title></line>`;
  })).join("");
  const nodeSvg = nodes.map(node => {
    const position = positions.get(String(node.kb_id || ""));
    if (!position) return "";
    const depth = Number(node.depth || 0);
    const secondLevelCount = nodes.filter(item => Number(item.depth || 0) === 2).length;
    const radius = depth === 0 ? 19 : depth === 1 ? 10 : (secondLevelCount > 64 ? 4 : 5.5);
    const title = `${node.title || node.kb_id}${node.type ? ` · ${node.type}` : ""}`;
    const rootClass = depth === 0 ? " detailGraphRoot" : "";
    return window.PortalI18n.html`<g data-detail-graph-open="${escapeHtml(node.kb_id || "")}" role="button" tabindex="0"><title>${escapeHtml(title)}</title><circle class="detailGraphNode${rootClass}" data-detail-graph-node="${escapeHtml(node.kb_id || "")}" style="--graph-node:${detailGraphNodeColor(node.type)}" cx="${position.x}" cy="${position.y}" r="${radius}"></circle></g>`;
  }).join("");
  el.mGraph.innerHTML = window.PortalI18n.html`<svg data-portal-visualization="knowledge-detail-graph" viewBox="0 0 ${size} ${size}" role="img" aria-label="以当前知识条目为中心的两级关系图谱">${edgeSvg}${nodeSvg}</svg>`;
}

async function loadDetailGraph(fileId) {
  const cleanId = String(fileId || "").trim();
  if (!cleanId || !detailGraphEnabled()) return;
  state.detailGraph = { fileId: cleanId, payload: null, loading: true, positions: {}, basePositions: null };
  renderDetailGraph();
  try {
    const payload = await knowledgeApi.request(`/knowledge/api/item-graph?kb_id=${encodeURIComponent(cleanId)}`, { cache: "no-store" });
    if (state.detailFileId !== cleanId) return;
    state.detailGraph = { fileId: cleanId, payload, loading: false, positions: {}, basePositions: null };
  } catch (error) {
    if (state.detailFileId === cleanId) {
      state.detailGraph = { fileId: cleanId, payload: null, loading: false, positions: {}, basePositions: null };
    }
    throw error;
  } finally {
    if (state.detailFileId === cleanId) renderDetailGraph();
  }
}
