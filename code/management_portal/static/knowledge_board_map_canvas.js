// Knowledge board: drawing the Knowledge map with D3: geometry, labels, selection and zoom.

function knowledgeMapNodeRadius(node) {
  const degree = Math.max(0, Number(node.degree || 0));
  const importance = Math.max(0, Number(node.importance || 0));
  return Math.min(15, 5.5 + Math.sqrt(degree) * 1.8 + importance * 0.32);
}

function knowledgeMapHash(value) {
  let hash = 2166136261;
  for (const char of String(value || "")) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function knowledgeMapProjectTargets(nodes, width, height) {
  const projects = [...new Set(nodes.map(node => node.project || "未分类"))].sort();
  const radius = Math.min(width, height) * (projects.length > 1 ? 0.23 : 0);
  return new Map(projects.map((project, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, projects.length) - Math.PI / 2;
    return [
      project,
      {
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
      },
    ];
  }));
}

function knowledgeMapEdgeKey(edge) {
  return `${knowledgeMapEndpointId(edge.source)}→${knowledgeMapEndpointId(edge.target)}:${edge.relation}`;
}

function assignKnowledgeMapEdgeCurves(edges) {
  const groups = new Map();
  for (const edge of edges) {
    const source = knowledgeMapEndpointId(edge.source);
    const target = knowledgeMapEndpointId(edge.target);
    const key = [source, target].sort().join("↔");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(edge);
  }
  for (const group of groups.values()) {
    group.forEach((edge, index) => {
      edge.curve = group.length === 1 ? 0 : index - (group.length - 1) / 2;
    });
  }
}

function knowledgeMapEdgeGeometry(edge) {
  const source = edge.source;
  const target = edge.target;
  if (!source || !target) return null;
  if (source === target || source.kb_id === target.kb_id) {
    const radius = knowledgeMapNodeRadius(source) + 10;
    return {
      path: `M${source.x},${source.y - radius / 2}a${radius},${radius} 0 1,1 1,0`,
      labelX: source.x + radius,
      labelY: source.y - radius,
    };
  }
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / distance;
  const uy = dy / distance;
  const sourceRadius = knowledgeMapNodeRadius(source) + 1;
  const targetRadius = knowledgeMapNodeRadius(target) + 5;
  const sx = source.x + ux * sourceRadius;
  const sy = source.y + uy * sourceRadius;
  const tx = target.x - ux * targetRadius;
  const ty = target.y - uy * targetRadius;
  const curveOffset = Number(edge.curve || 0) * 24;
  const cx = (sx + tx) / 2 - uy * curveOffset;
  const cy = (sy + ty) / 2 + ux * curveOffset;
  return {
    path: `M${sx},${sy}Q${cx},${cy} ${tx},${ty}`,
    labelX: (sx + 2 * cx + tx) / 4,
    labelY: (sy + 2 * cy + ty) / 4,
  };
}

function ensureKnowledgeMapMarkers() {
  const svg = window.d3.select(el.knowledgeMapSvg);
  let defs = svg.select("defs.knowledgeGraphDefs");
  if (defs.empty()) defs = svg.insert("defs", ":first-child").attr("class", "knowledgeGraphDefs");
  for (const [relation, meta] of Object.entries(knowledgeMapRelations)) {
    const markerId = `knowledge-map-arrow-${relation}`;
    let marker = defs.select(`#${markerId}`);
    if (marker.empty()) {
      marker = defs.append("marker")
        .attr("id", markerId)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 9)
        .attr("refY", 0)
        .attr("markerWidth", 5)
        .attr("markerHeight", 5)
        .attr("orient", "auto")
        .attr("markerUnits", "userSpaceOnUse");
      marker.append("path").attr("d", "M0,-4L9,0L0,4Z");
    }
    marker.select("path").attr("fill", meta.color);
  }
}

function truncateKnowledgeMapLabel(value, maxLength = 30) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function renderKnowledgeMapLegend(data) {
  const counts = new Map();
  for (const node of data.nodes) {
    const type = String(node.type || "other");
    counts.set(type, Number(counts.get(type) || 0) + 1);
  }
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  el.knowledgeMapLegend.hidden = entries.length === 0;
  el.knowledgeMapLegend.innerHTML = entries.map(([type, count]) => window.PortalI18n.html`
          <span class="knowledgeMapLegendItem">
            <span class="knowledgeMapLegendDot" style="--legend-color:${knowledgeMapTypeColors[type] || knowledgeMapTypeColors.other}"></span>
            ${escapeHtml(type)} ${count}
          </span>
        `).join("");
}

function knowledgeMapVisibleLabelLimit(data, scale = 1) {
  if (data.nodes.length <= 60) return data.nodes.length;
  if (scale < 0.55) return 16;
  if (scale < 0.9) return 28;
  if (scale < 1.4) return 54;
  if (scale < 2.1) return 100;
  return data.nodes.length;
}

function updateKnowledgeMapLabelVisibility(scale) {
  const data = state.knowledgeMap.currentData;
  if (!data?.nodeLabelSelection) return;
  data.zoomScale = Number.isFinite(Number(scale)) ? Number(scale) : Number(data.zoomScale || 1);
  const selectedId = data.byIdMap.has(state.knowledgeMap.selectedId)
    ? state.knowledgeMap.selectedId
    : "";
  const neighbors = new Set(selectedId ? [selectedId] : []);
  for (const edge of data.edges) {
    const source = knowledgeMapEndpointId(edge.source);
    const target = knowledgeMapEndpointId(edge.target);
    if (source === selectedId) neighbors.add(target);
    if (target === selectedId) neighbors.add(source);
  }
  const limit = knowledgeMapVisibleLabelLimit(data, data.zoomScale);
  data.nodeLabelSelection.attr("opacity", node => {
    if (neighbors.has(node.kb_id)) return 1;
    return Number(node.labelRank || 0) < limit ? 0.82 : 0;
  });
}

function updateKnowledgeMapSelection({ renderSide = true } = {}) {
  const data = state.knowledgeMap.currentData;
  if (!data) return;
  const selectedId = data.byIdMap.has(state.knowledgeMap.selectedId)
    ? state.knowledgeMap.selectedId
    : "";
  if (!selectedId) state.knowledgeMap.selectedId = "";
  const neighbors = new Set(selectedId ? [selectedId] : []);
  for (const edge of data.edges) {
    const source = knowledgeMapEndpointId(edge.source);
    const target = knowledgeMapEndpointId(edge.target);
    if (source === selectedId) neighbors.add(target);
    if (target === selectedId) neighbors.add(source);
  }
  data.nodeSelection
    ?.classed("is-selected", node => node.kb_id === selectedId)
    .classed("is-muted", node => Boolean(selectedId) && !neighbors.has(node.kb_id));
  data.linkSelection
    ?.classed("is-active", edge => {
      const source = knowledgeMapEndpointId(edge.source);
      const target = knowledgeMapEndpointId(edge.target);
      return Boolean(selectedId) && (source === selectedId || target === selectedId);
    })
    .classed("is-muted", edge => {
      const source = knowledgeMapEndpointId(edge.source);
      const target = knowledgeMapEndpointId(edge.target);
      return Boolean(selectedId) && source !== selectedId && target !== selectedId;
    });
  updateKnowledgeMapLabelVisibility(data.zoomScale);
  data.edgeLabelSelection?.attr("display", edge => {
    const source = knowledgeMapEndpointId(edge.source);
    const target = knowledgeMapEndpointId(edge.target);
    return selectedId && (source === selectedId || target === selectedId) ? null : "none";
  });
  el.knowledgeMapNeighborsOnly.disabled = !selectedId;
  el.knowledgeMapNeighborsOnly.classList.toggle("active", state.knowledgeMap.neighborsOnly);
  el.knowledgeMapNeighborsOnly.setAttribute("aria-pressed", state.knowledgeMap.neighborsOnly ? "true" : "false");
  if (renderSide) renderKnowledgeMapSide(data);
}

function fitKnowledgeMap({ animate = true } = {}) {
  if (!state.knowledgeMap.zoom || !state.knowledgeMap.currentData?.nodes.length) return;
  let bounds;
  try {
    bounds = el.knowledgeMapScene.getBBox();
  } catch (_) {
    return;
  }
  if (!bounds.width || !bounds.height) return;
  const rect = el.knowledgeMapViewport.getBoundingClientRect();
  const width = Math.max(320, rect.width);
  const height = Math.max(320, rect.height);
  const scale = Math.max(0.18, Math.min(2.1, 0.86 / Math.max(bounds.width / width, bounds.height / height)));
  const transform = window.d3.zoomIdentity
    .translate(width / 2 - scale * (bounds.x + bounds.width / 2), height / 2 - scale * (bounds.y + bounds.height / 2))
    .scale(scale);
  const svg = window.d3.select(el.knowledgeMapSvg);
  const target = animate ? svg.transition().duration(260) : svg;
  target.call(state.knowledgeMap.zoom.transform, transform);
}

function focusKnowledgeMapNode(kbId, { scale = 1.45 } = {}) {
  const node = state.knowledgeMap.currentData?.byIdMap.get(String(kbId || ""));
  if (!node || !state.knowledgeMap.zoom || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
  const rect = el.knowledgeMapViewport.getBoundingClientRect();
  const transform = window.d3.zoomIdentity
    .translate(rect.width / 2 - node.x * scale, rect.height / 2 - node.y * scale)
    .scale(scale);
  window.d3.select(el.knowledgeMapSvg)
    .transition()
    .duration(280)
    .call(state.knowledgeMap.zoom.transform, transform);
}

function renderKnowledgeMapD3(data, signature) {
  stopKnowledgeMapSimulation();
  ensureKnowledgeMapMarkers();
  const rect = el.knowledgeMapViewport.getBoundingClientRect();
  const width = Math.max(640, rect.width || 0);
  const height = Math.max(500, rect.height || 0);
  el.knowledgeMapSvg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const targets = knowledgeMapProjectTargets(data.nodes, width, height);
  const labelRanks = new Map(
    data.nodes
      .slice()
      .sort((a, b) => {
        const scoreA = Number(a.degree || 0) * 12 + Number(a.importance || 0);
        const scoreB = Number(b.degree || 0) * 12 + Number(b.importance || 0);
        return scoreB - scoreA || Number(b.mtime || 0) - Number(a.mtime || 0);
      })
      .map((node, index) => [node.kb_id, index]),
  );

  for (const node of data.nodes) {
    const previous = state.knowledgeMap.positions.get(node.kb_id);
    const target = targets.get(node.project || "未分类") || { x: width / 2, y: height / 2 };
    const hash = knowledgeMapHash(node.kb_id);
    const angle = ((hash % 360) * Math.PI) / 180;
    const spread = 24 + (hash % 110);
    node.x = Number.isFinite(previous?.x) ? previous.x : target.x + Math.cos(angle) * spread;
    node.y = Number.isFinite(previous?.y) ? previous.y : target.y + Math.sin(angle) * spread;
    node.labelRank = Number(labelRanks.get(node.kb_id) || 0);
    node.showLabel = data.nodes.length <= 60 || node.labelRank < 28;
  }
  assignKnowledgeMapEdgeCurves(data.edges);

  const d3 = window.d3;
  const svg = d3.select(el.knowledgeMapSvg);
  const scene = d3.select(el.knowledgeMapScene);
  if (!state.knowledgeMap.zoom) {
    state.knowledgeMap.zoom = d3.zoom()
      .scaleExtent([0.16, 4])
      .on("zoom", event => {
        scene.attr("transform", event.transform);
        updateKnowledgeMapLabelVisibility(event.transform.k);
      });
  }
  svg.call(state.knowledgeMap.zoom).on("dblclick.zoom", null);
  svg.on("click.knowledge-map", event => {
    if (event.target.closest?.(".knowledgeGraphNode")) return;
    selectKnowledgeMapNode("");
  });

  const linkSelection = d3.select(el.knowledgeMapEdges)
    .selectAll("path.knowledgeGraphEdge")
    .data(data.edges, knowledgeMapEdgeKey)
    .join("path")
    .attr("class", "knowledgeGraphEdge")
    .attr("stroke", edge => knowledgeMapRelationInfo(edge.relation).color)
    .attr("marker-end", edge => `url(#knowledge-map-arrow-${edge.relation in knowledgeMapRelations ? edge.relation : "related"})`);

  const edgeLabelSelection = d3.select(el.knowledgeMapEdgeLabels)
    .selectAll("text.knowledgeGraphRelationLabel")
    .data(data.edges, knowledgeMapEdgeKey)
    .join("text")
    .attr("class", "knowledgeGraphRelationLabel")
    .text(edge => knowledgeMapRelationInfo(edge.relation).label)
    .attr("display", "none");

  const nodeSelection = d3.select(el.knowledgeMapNodes)
    .selectAll("g.knowledgeGraphNode")
    .data(data.nodes, node => node.kb_id)
    .join(enter => {
      const group = enter.append("g")
        .attr("class", "knowledgeGraphNode")
        .attr("role", "button")
        .attr("tabindex", 0);
      group.append("circle").attr("class", "knowledgeGraphNodeHalo");
      group.append("circle").attr("class", "knowledgeGraphNodeDot");
      group.append("text").attr("class", "knowledgeGraphNodeLabel");
      group.append("title");
      return group;
    })
    .attr("aria-label", node => `${node.title || node.kb_id}，${node.type || "other"}，${Number(node.degree || 0)} 个关系`)
    .on("click", (event, node) => {
      event.stopPropagation();
      selectKnowledgeMapNode(node.kb_id);
    })
    .on("dblclick", (event, node) => {
      event.stopPropagation();
      selectKnowledgeMapNode(node.kb_id, { focus: true });
    })
    .on("keydown", (event, node) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectKnowledgeMapNode(node.kb_id, { focus: true });
    });

  nodeSelection.select(".knowledgeGraphNodeHalo")
    .attr("r", node => knowledgeMapNodeRadius(node) + 5);
  nodeSelection.select(".knowledgeGraphNodeDot")
    .attr("r", knowledgeMapNodeRadius)
    .attr("fill", node => knowledgeMapTypeColors[node.type] || knowledgeMapTypeColors.other);
  const nodeLabelSelection = nodeSelection.select(".knowledgeGraphNodeLabel")
    .attr("x", node => knowledgeMapNodeRadius(node) + 6)
    .attr("y", 3.5)
    .text(node => truncateKnowledgeMapLabel(node.title || node.filename || node.kb_id))
    .attr("opacity", node => node.showLabel ? 0.82 : 0);
  nodeSelection.select("title")
    .text(node => `${node.title || node.kb_id}\n${node.project || ""} · ${node.type || ""}\n${Number(node.degree || 0)} 个关系`);

  const drag = d3.drag()
    .on("start", (event, node) => {
      event.sourceEvent?.stopPropagation?.();
      if (!event.active) state.knowledgeMap.simulation?.alphaTarget(0.16).restart();
      node.fx = node.x;
      node.fy = node.y;
    })
    .on("drag", (event, node) => {
      node.fx = event.x;
      node.fy = event.y;
    })
    .on("end", (event, node) => {
      if (!event.active) state.knowledgeMap.simulation?.alphaTarget(0);
      node.fx = null;
      node.fy = null;
      state.knowledgeMap.positions.set(node.kb_id, { x: node.x, y: node.y });
    });
  nodeSelection.call(drag);

  data.nodeSelection = nodeSelection;
  data.nodeLabelSelection = nodeLabelSelection;
  data.linkSelection = linkSelection;
  data.edgeLabelSelection = edgeLabelSelection;
  state.knowledgeMap.currentData = data;
  data.zoomScale = Number(svg.property("__zoom")?.k || 1);

  const simulation = d3.forceSimulation(data.nodes)
    .force("link", d3.forceLink(data.edges)
      .id(node => node.kb_id)
      .distance(edge => edge.relation === "related" ? 92 : 112)
      .strength(0.38))
    .force("charge", d3.forceManyBody().strength(node => -58 - Math.min(70, Number(node.degree || 0) * 5)))
    .force("collide", d3.forceCollide().radius(node => knowledgeMapNodeRadius(node) + (node.showLabel ? 20 : 5)).iterations(2))
    .force("x", d3.forceX(node => (targets.get(node.project || "未分类") || { x: width / 2 }).x).strength(0.032))
    .force("y", d3.forceY(node => (targets.get(node.project || "未分类") || { y: height / 2 }).y).strength(0.032))
    .alpha(0.9)
    .alphaDecay(0.045);
  state.knowledgeMap.simulation = simulation;
  let ticks = 0;
  simulation.on("tick", () => {
    ticks += 1;
    linkSelection.attr("d", edge => knowledgeMapEdgeGeometry(edge)?.path || "");
    edgeLabelSelection
      .attr("x", edge => knowledgeMapEdgeGeometry(edge)?.labelX || 0)
      .attr("y", edge => knowledgeMapEdgeGeometry(edge)?.labelY || 0);
    nodeSelection.attr("transform", node => `translate(${node.x},${node.y})`);
    if (ticks === 28 && state.knowledgeMap.fittedSignature !== signature) {
      state.knowledgeMap.fittedSignature = signature;
      fitKnowledgeMap();
    }
  });
  updateKnowledgeMapSelection();
}
