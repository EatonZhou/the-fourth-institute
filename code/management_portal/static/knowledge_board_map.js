// Knowledge board: the board and map views, the map's data, controls, side panel and search.

// ── D3 Knowledge graph ────────────────────────────────────────────────
const knowledgeMapTypeOrder = [
  "question",
  "idea",
  "concept",
  "decision",
  "insight",
  "thinking",
  "information",
];
const knowledgeMapTypeColors = {
  question: "#c18158",
  idea: "#b99a35",
  concept: "#3594a9",
  decision: "#607fc1",
  insight: "#3c9875",
  thinking: "#8e70b6",
  information: "#747b88",
  hypothesis: "#5479ba",
  experiment: "#c87340",
  risk: "#bd5e67",
  plan: "#4f936c",
  other: "#7a808a",
};
const knowledgeMapRelations = {
  related: { label: window.PortalI18n.t("相关"), color: "#419176" },
  source: { label: window.PortalI18n.t("来源"), color: "#567fc1" },
  supersedes: { label: window.PortalI18n.t("取代"), color: "#b76370" },
  derived_from: { label: window.PortalI18n.t("派生"), color: "#8c69b2" },
};

function activeKnowledgeView() {
  return state.knowledgeMap.open ? "map" : "board";
}

function syncKnowledgeView() {
  const mapOpen = activeKnowledgeView() === "map";
  el.knowledgeMapPage.hidden = !mapOpen;
  el.knowledgeRadarPage.hidden = mapOpen;
  el.grid.hidden = mapOpen;
  if (mapOpen) el.empty.style.display = "none";
  el.knowledgeBoardBtn.classList.toggle("active", !mapOpen);
  el.knowledgeMapBtn.classList.toggle("active", mapOpen);
  el.knowledgeBoardBtn.setAttribute("aria-selected", mapOpen ? "false" : "true");
  el.knowledgeMapBtn.setAttribute("aria-selected", mapOpen ? "true" : "false");
}

function stopKnowledgeMapSimulation() {
  const current = state.knowledgeMap.currentData;
  for (const node of current?.nodes || []) {
    if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
      state.knowledgeMap.positions.set(node.kb_id, { x: node.x, y: node.y });
    }
  }
  state.knowledgeMap.simulation?.stop?.();
  state.knowledgeMap.simulation = null;
}

function setKnowledgeView(view) {
  const mapOpen = view === "map";
  state.knowledgeMap.open = mapOpen;
  if (!mapOpen) {
    stopKnowledgeMapSimulation();
    state.knowledgeMap.signature = "";
    knowledgeMapSearchPopover.close();
  }
  syncKnowledgeView();
  saveKnowledgeUiState();
  if (mapOpen) {
    buildKnowledgeMapControls();
    renderKnowledgeMap({ force: true });
  }
}

function knowledgeMapBaseItems() {
  return (state.raw?.items || [])
    .filter(item => matchesFilter(item))
    .filter(item => matchesQuery(item, state.query));
}

function knowledgeMapRelationInfo(relation) {
  return knowledgeMapRelations[relation] || {
    label: String(relation || window.PortalI18n.t("关系")),
    color: knowledgeMapTypeColors.other,
  };
}

function knowledgeMapEndpointId(endpoint) {
  return typeof endpoint === "object"
    ? String(endpoint?.kb_id || endpoint?.id || "")
    : String(endpoint || "");
}

function buildKnowledgeMapControls() {
  const items = knowledgeMapBaseItems();
  const projects = [...new Set(items.map(item => item.project).filter(Boolean))].sort();
  const types = [...new Set(items.map(item => item.type).filter(Boolean))]
    .sort((a, b) => {
      const ai = knowledgeMapTypeOrder.indexOf(a);
      const bi = knowledgeMapTypeOrder.indexOf(b);
      if (ai >= 0 || bi >= 0) return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
      return a.localeCompare(b);
    });
  state.knowledgeMap.project = projects.includes(state.knowledgeMap.project)
    ? state.knowledgeMap.project
    : "";
  state.knowledgeMap.type = types.includes(state.knowledgeMap.type)
    ? state.knowledgeMap.type
    : "";
  if (!(state.knowledgeMap.relation in knowledgeMapRelations)) {
    state.knowledgeMap.relation = "";
  }
  el.knowledgeMapProject.innerHTML = [
    window.PortalI18n.html`<option value="">全部 Project</option>`,
    ...projects.map(project => window.PortalI18n.html`<option value="${escapeHtml(project)}">${escapeHtml(project)}</option>`),
  ].join("");
  el.knowledgeMapType.innerHTML = [
    window.PortalI18n.html`<option value="">全部类型</option>`,
    ...types.map(type => window.PortalI18n.html`<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`),
  ].join("");
  const counts = state.knowledgeMap.graph?.relation_counts || {};
  el.knowledgeMapRelation.innerHTML = [
    window.PortalI18n.html`<option value="">全部关系</option>`,
    ...Object.entries(knowledgeMapRelations).map(([relation, meta]) => {
      const count = Number(counts[relation] || 0);
      return window.PortalI18n.html`<option value="${relation}">${escapeHtml(meta.label)}${count ? ` · ${count}` : ""}</option>`;
    }),
  ].join("");
  el.knowledgeMapProject.value = state.knowledgeMap.project;
  el.knowledgeMapType.value = state.knowledgeMap.type;
  el.knowledgeMapRelation.value = state.knowledgeMap.relation;
  el.knowledgeMapConnectedOnly.classList.toggle("active", state.knowledgeMap.connectedOnly);
  el.knowledgeMapConnectedOnly.setAttribute("aria-pressed", state.knowledgeMap.connectedOnly ? "true" : "false");
  el.knowledgeMapNeighborsOnly.classList.toggle("active", state.knowledgeMap.neighborsOnly);
  el.knowledgeMapNeighborsOnly.setAttribute("aria-pressed", state.knowledgeMap.neighborsOnly ? "true" : "false");
  el.knowledgeMapNeighborsOnly.disabled = !state.knowledgeMap.selectedId;
}

function setKnowledgeMapOverlay(kind, message) {
  if (!kind) {
    el.knowledgeMapOverlay.hidden = true;
    return;
  }
  el.knowledgeMapOverlay.hidden = false;
  const spinner = kind === "loading"
    ? window.PortalI18n.html`<span class="knowledgeMapSpinner" aria-hidden="true"></span>`
    : "";
  const action = kind === "error"
    ? window.PortalI18n.html`<button class="portalButton" type="button" data-map-retry>重新加载</button>`
    : kind === "empty" && state.knowledgeMap.connectedOnly
      ? window.PortalI18n.html`<button class="portalButton" type="button" data-map-show-isolated>显示无关系条目</button>`
      : "";
  el.knowledgeMapOverlayCard.innerHTML = window.PortalI18n.html`${spinner}<span>${escapeHtml(message)}</span>${action}`;
}

async function loadKnowledgeGraph({ refresh = false } = {}) {
  const seq = state.knowledgeMap.requestSeq + 1;
  state.knowledgeMap.requestSeq = seq;
  state.knowledgeMap.loading = true;
  state.knowledgeMap.error = "";
  if (state.knowledgeMap.open) {
    setKnowledgeMapOverlay("loading", "正在读取真实知识关系…");
    el.knowledgeMapSub.textContent = window.PortalI18n.t("正在加载图谱…");
  }
  try {
    const path = `/knowledge/api/graph${refresh ? "?refresh=1" : ""}`;
    const result = await knowledgeApi.requestResult(path, {
      cache: "no-store",
      detectLoginRedirect: false,
    });
    if (!result.response.ok) throw new Error(`HTTP ${result.response.status}`);
    const payload = knowledgeApi.requireJson(result);
    if (seq !== state.knowledgeMap.requestSeq) return;
    state.knowledgeMap.graph = {
      ...payload,
      nodes: Array.isArray(payload.nodes) ? payload.nodes : [],
      edges: Array.isArray(payload.edges) ? payload.edges : [],
    };
    state.knowledgeMap.signature = "";
    state.knowledgeMap.fittedSignature = "";
    buildKnowledgeMapControls();
  } catch (error) {
    if (seq !== state.knowledgeMap.requestSeq) return;
    state.knowledgeMap.error = error?.message || String(error);
  } finally {
    if (seq === state.knowledgeMap.requestSeq) {
      state.knowledgeMap.loading = false;
      if (state.knowledgeMap.open) renderKnowledgeMap({ force: true });
    }
  }
}

function buildKnowledgeMapData() {
  const payload = state.knowledgeMap.graph || { nodes: [], edges: [] };
  const indexItems = new Map(knowledgeMapBaseItems().map(item => [String(item.kb_id || ""), item]));
  const eligibleNodes = [];
  const eligibleIds = new Set();
  for (const raw of payload.nodes || []) {
    const kbId = String(raw?.kb_id || "").trim();
    const indexItem = indexItems.get(kbId);
    if (!kbId || !indexItem) continue;
    const node = { ...raw, ...indexItem, kb_id: kbId, degree: Number(raw.degree || 0) };
    if (state.knowledgeMap.project && node.project !== state.knowledgeMap.project) continue;
    if (state.knowledgeMap.type && node.type !== state.knowledgeMap.type) continue;
    eligibleNodes.push(node);
    eligibleIds.add(kbId);
  }

  const relationEdges = [];
  for (const raw of payload.edges || []) {
    const source = knowledgeMapEndpointId(raw.source);
    const target = knowledgeMapEndpointId(raw.target);
    const relation = String(raw.relation || "related");
    if (!eligibleIds.has(source) || !eligibleIds.has(target)) continue;
    if (state.knowledgeMap.relation && relation !== state.knowledgeMap.relation) continue;
    relationEdges.push({ source, target, relation });
  }

  const connectedIds = new Set();
  for (const edge of relationEdges) {
    connectedIds.add(edge.source);
    connectedIds.add(edge.target);
  }
  if (state.knowledgeMap.selectedId && !eligibleIds.has(state.knowledgeMap.selectedId)) {
    state.knowledgeMap.selectedId = "";
    state.knowledgeMap.neighborsOnly = false;
  }

  let visibleIds = new Set(eligibleIds);
  if (state.knowledgeMap.neighborsOnly && state.knowledgeMap.selectedId) {
    visibleIds = new Set([state.knowledgeMap.selectedId]);
    for (const edge of relationEdges) {
      if (edge.source === state.knowledgeMap.selectedId) visibleIds.add(edge.target);
      if (edge.target === state.knowledgeMap.selectedId) visibleIds.add(edge.source);
    }
  } else if (state.knowledgeMap.connectedOnly) {
    visibleIds = connectedIds;
  }

  const nodes = eligibleNodes
    .filter(node => visibleIds.has(node.kb_id))
    .map(node => ({ ...node }));
  const nodeIds = new Set(nodes.map(node => node.kb_id));
  const edges = relationEdges
    .filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map(edge => ({ ...edge }));
  const byIdMap = new Map(nodes.map(node => [node.kb_id, node]));
  const projects = new Set(nodes.map(node => node.project).filter(Boolean));
  return {
    nodes,
    edges,
    byIdMap,
    eligibleCount: eligibleNodes.length,
    connectedCount: connectedIds.size,
    hiddenIsolatedCount: Math.max(0, eligibleNodes.length - connectedIds.size),
    projectCount: projects.size,
    unresolvedLinks: Number(payload.unresolved_links || 0),
  };
}

function renderKnowledgeMapSide(data) {
  const selected = data.byIdMap.get(state.knowledgeMap.selectedId) || null;
  if (!selected) {
    const relationCounts = new Map();
    for (const edge of data.edges) {
      relationCounts.set(edge.relation, Number(relationCounts.get(edge.relation) || 0) + 1);
    }
    el.knowledgeMapSide.innerHTML = window.PortalI18n.html`
            <div class="knowledgeMapPanel">
              <div class="knowledgeMapPanelTitle">知识关系图谱</div>
              <div class="knowledgeMapPanelText">点击节点查看真实关系。拖拽节点整理位置，拖动画布或滚轮缩放；双击节点可快速聚焦。</div>
              <div class="knowledgeMapStats">
                <div class="knowledgeMapStat"><strong>${data.nodes.length}</strong><span>当前节点</span></div>
                <div class="knowledgeMapStat"><strong>${data.edges.length}</strong><span>真实关系</span></div>
                <div class="knowledgeMapStat"><strong>${data.projectCount}</strong><span>Projects</span></div>
                <div class="knowledgeMapStat"><strong>${data.hiddenIsolatedCount}</strong><span>无关系条目</span></div>
              </div>
            </div>
            <div class="knowledgeMapPanel">
              <div class="knowledgeMapPanelTitle">关系分布</div>
              <div class="knowledgeMapRelatedList">
                ${[...relationCounts.entries()].length
            ? [...relationCounts.entries()].map(([relation, count]) => {
                const meta = knowledgeMapRelationInfo(relation);
                return window.PortalI18n.html`<span class="knowledgeMapRelation" style="--relation-color:${meta.color}">${escapeHtml(meta.label)} · ${count}</span>`;
              }).join("")
            : window.PortalI18n.html`<div class="knowledgeMapPanelText">当前范围内尚未建立真实关系。可以关闭“只看关联”检查孤立条目。</div>`}
              </div>
            </div>
          `;
    return;
  }

  const relations = data.edges
    .filter(edge => knowledgeMapEndpointId(edge.source) === selected.kb_id || knowledgeMapEndpointId(edge.target) === selected.kb_id)
    .map(edge => {
      const outgoing = knowledgeMapEndpointId(edge.source) === selected.kb_id;
      const otherId = outgoing ? knowledgeMapEndpointId(edge.target) : knowledgeMapEndpointId(edge.source);
      return {
        edge,
        outgoing,
        other: data.byIdMap.get(otherId),
      };
    })
    .filter(entry => entry.other)
    .sort((a, b) => String(a.other.title || "").localeCompare(String(b.other.title || "")));
  el.knowledgeMapSide.innerHTML = window.PortalI18n.html`
          <div class="knowledgeMapPanel">
            <div class="knowledgeMapPanelTitle">${escapeHtml(selected.title || selected.filename || selected.kb_id)}</div>
            <div class="knowledgeMapPanelMeta">${escapeHtml(selected.project || window.PortalI18n.t("无 Project"))} · ${escapeHtml(selected.type || "other")} · importance ${escapeHtml(selected.importance ?? 0)}</div>
            <div class="knowledgeMapPanelText">${escapeHtml(selected.preview || window.PortalI18n.t("暂无预览内容。"))}</div>
            <div class="knowledgeMapPanelActions">
              <button class="portalButton" type="button" data-map-open-detail="${escapeHtml(selected.kb_id)}">打开条目</button>
              <button class="portalButton" type="button" data-map-focus="${escapeHtml(selected.kb_id)}">居中</button>
            </div>
          </div>
          <div class="knowledgeMapPanel">
            <div class="knowledgeMapPanelTitle">直接关系 · ${relations.length}</div>
            <div class="knowledgeMapRelatedList">
              ${relations.length ? relations.map(({ edge, outgoing, other }) => {
          const meta = knowledgeMapRelationInfo(edge.relation);
          return window.PortalI18n.html`
                  <button class="knowledgeMapRelated" type="button" data-map-select="${escapeHtml(other.kb_id)}">
                    <span class="knowledgeMapRelation" style="--relation-color:${meta.color}">${outgoing ? "→" : "←"} ${escapeHtml(meta.label)}</span>
                    <span>${escapeHtml(other.title || other.filename || other.kb_id)}</span>
                    <span class="knowledgeMapPanelMeta">${escapeHtml(other.project || "")} · ${escapeHtml(other.type || "")}</span>
                  </button>
                `;
        }).join("") : window.PortalI18n.html`<div class="knowledgeMapPanelText">这个条目尚未建立当前类型的关系。</div>`}
            </div>
          </div>
        `;
}

function selectKnowledgeMapNode(kbId, { focus = false } = {}) {
  const cleanId = String(kbId || "");
  const clearedNeighborScope = !cleanId && state.knowledgeMap.neighborsOnly;
  state.knowledgeMap.selectedId = cleanId;
  if (!cleanId) state.knowledgeMap.neighborsOnly = false;
  if (state.knowledgeMap.neighborsOnly || clearedNeighborScope) {
    renderKnowledgeMap({ force: true });
  } else {
    updateKnowledgeMapSelection();
  }
  if (focus && cleanId) {
    window.requestAnimationFrame(() => focusKnowledgeMapNode(cleanId));
  }
}

function knowledgeMapSummaryText(data) {
  const hidden = state.knowledgeMap.connectedOnly && data.hiddenIsolatedCount
    ? window.PortalI18n.text` · 隐藏 ${data.hiddenIsolatedCount} 个孤立节点`
    : "";
  return window.PortalI18n.text`${data.nodes.length} 节点 · ${data.edges.length} 关系${hidden}`;
}

function renderKnowledgeMap({ force = false } = {}) {
  if (!state.knowledgeMap.open) return;
  buildKnowledgeMapControls();
  if (!window.d3) {
    setKnowledgeMapOverlay("error", "D3.js 未能加载，无法绘制知识图谱。");
    return;
  }
  if (state.knowledgeMap.loading) {
    setKnowledgeMapOverlay("loading", "正在读取真实知识关系…");
    return;
  }
  if (state.knowledgeMap.error) {
    setKnowledgeMapOverlay("error", `图谱加载失败：${state.knowledgeMap.error}`);
    el.knowledgeMapSub.textContent = window.PortalI18n.t("加载失败");
    return;
  }
  if (!state.knowledgeMap.graph) {
    loadKnowledgeGraph().catch(error => {
      state.knowledgeMap.error = error?.message || String(error);
      renderKnowledgeMap({ force: true });
    });
    return;
  }
  if (state.indexPaging.prefetching && !force) {
    window.clearTimeout(state.knowledgeMap.renderTimer);
    state.knowledgeMap.renderTimer = window.setTimeout(
      () => renderKnowledgeMap({ force: true }),
      180,
    );
    return;
  }

  const data = buildKnowledgeMapData();
  const signature = JSON.stringify({
    nodes: data.nodes.map(node => node.kb_id),
    edges: data.edges.map(knowledgeMapEdgeKey),
    project: state.knowledgeMap.project,
    type: state.knowledgeMap.type,
    relation: state.knowledgeMap.relation,
    connectedOnly: state.knowledgeMap.connectedOnly,
    neighborsOnly: state.knowledgeMap.neighborsOnly,
    neighborRoot: state.knowledgeMap.neighborsOnly ? state.knowledgeMap.selectedId : "",
  });
  el.knowledgeMapSub.textContent = knowledgeMapSummaryText(data);
  if (!data.nodes.length) {
    stopKnowledgeMapSimulation();
    state.knowledgeMap.currentData = data;
    window.d3.select(el.knowledgeMapEdges).selectAll("*").remove();
    window.d3.select(el.knowledgeMapEdgeLabels).selectAll("*").remove();
    window.d3.select(el.knowledgeMapNodes).selectAll("*").remove();
    el.knowledgeMapLegend.hidden = true;
    renderKnowledgeMapSide(data);
    setKnowledgeMapOverlay(
      "empty",
      state.knowledgeMap.connectedOnly
        ? "当前筛选范围内还没有真实知识关系。"
        : "当前筛选范围内没有可显示的知识条目。",
    );
    state.knowledgeMap.signature = signature;
    return;
  }
  setKnowledgeMapOverlay("", "");
  renderKnowledgeMapLegend(data);
  if (!force && signature === state.knowledgeMap.signature) {
    state.knowledgeMap.currentData = state.knowledgeMap.currentData || data;
    updateKnowledgeMapSelection();
    return;
  }
  state.knowledgeMap.signature = signature;
  renderKnowledgeMapD3(data, signature);
}

function knowledgeMapSearchCandidates() {
  const query = String(state.knowledgeMap.query || "").trim().toLowerCase();
  if (!query || !state.knowledgeMap.graph) return [];
  const baseIds = new Set(knowledgeMapBaseItems().map(item => String(item.kb_id || "")));
  return (state.knowledgeMap.graph.nodes || [])
    .filter(node => baseIds.has(String(node.kb_id || "")))
    .filter(node => !state.knowledgeMap.project || node.project === state.knowledgeMap.project)
    .filter(node => !state.knowledgeMap.type || node.type === state.knowledgeMap.type)
    .map(node => {
      const haystack = `${node.title || ""}\n${node.kb_id || ""}\n${node.preview || ""}`.toLowerCase();
      return { node, haystack, starts: String(node.title || "").toLowerCase().startsWith(query) };
    })
    .filter(entry => entry.haystack.includes(query))
    .sort((a, b) => Number(b.starts) - Number(a.starts) || Number(b.node.degree || 0) - Number(a.node.degree || 0))
    .slice(0, 8)
    .map(entry => entry.node);
}

function renderKnowledgeMapSuggestions() {
  const candidates = knowledgeMapSearchCandidates();
  el.knowledgeMapSuggestions.innerHTML = candidates.map((node, index) => window.PortalI18n.html`
          <button class="knowledgeMapSuggestion${index === 0 ? " active" : ""}" type="button" role="option" data-map-suggestion="${escapeHtml(node.kb_id)}">
            <span class="knowledgeMapSuggestionTitle">${escapeHtml(node.title || node.kb_id)}</span>
            <span class="knowledgeMapSuggestionMeta">${escapeHtml(node.type || "")}</span>
            <span class="knowledgeMapSuggestionMeta">${escapeHtml(node.project || "")}</span>
            <span class="knowledgeMapSuggestionMeta">${Number(node.degree || 0)} 关系</span>
          </button>
        `).join("");
  if (candidates.length) knowledgeMapSearchPopover.open();
  else knowledgeMapSearchPopover.close();
}

function revealKnowledgeMapNode(kbId) {
  const cleanId = String(kbId || "");
  if (!cleanId) return;
  state.knowledgeMap.selectedId = cleanId;
  const visible = state.knowledgeMap.currentData?.byIdMap.has(cleanId);
  if (!visible) state.knowledgeMap.connectedOnly = false;
  buildKnowledgeMapControls();
  renderKnowledgeMap({ force: !visible || state.knowledgeMap.neighborsOnly });
  knowledgeMapSearchPopover.close();
  window.requestAnimationFrame(() => focusKnowledgeMapNode(cleanId));
}
