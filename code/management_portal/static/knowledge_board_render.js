// Knowledge board: cards grouped by Project, rendered in a virtual list.

// ── Render ────────────────────────────────────────────────────────────
function groupByProject(items) {
  const map = new Map();
  for (const it of items) {
    const pid = it.project || "unknown";
    if (!map.has(pid)) map.set(pid, []);
    map.get(pid).push(it);
  }
  // sort items within each project by mtime desc
  for (const list of map.values()) {
    list.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  }
  // Keep a user's explicit order first. New projects still fall back to recency.
  const entries = [...map.entries()];
  const order = new Map(state.projectOrder.map((projectId, index) => [projectId, index]));
  entries.sort((a, b) => {
    const orderA = order.get(a[0]);
    const orderB = order.get(b[0]);
    if (orderA !== undefined || orderB !== undefined) {
      if (orderA === undefined) return 1;
      if (orderB === undefined) return -1;
      if (orderA !== orderB) return orderA - orderB;
    }
    const maxA = a[1].reduce((m, i) => Math.max(m, i.mtime || 0), 0);
    const maxB = b[1].reduce((m, i) => Math.max(m, i.mtime || 0), 0);
    return maxB - maxA;
  });
  return entries;
}

function createKnowledgeCard(it) {
  const card = document.createElement("div");
  card.className = "card";
  card.addEventListener("click", () => openDetail(it.kb_id));

  const title = document.createElement("div");
  title.className = "cardTitle";
  title.textContent = it.title || it.filename || it.kb_id;

  const meta = document.createElement("div");
  meta.className = "cardMeta";
  const left = document.createElement("div");
  left.textContent = knowledgeBoardShell.formatDateTime(it.mtime, {
    unixSeconds: true,
    shortYear: true,
    separator: "-",
    fallback: "",
  });
  left.title = window.PortalI18n.t("最近修改时间");
  const right = document.createElement("div");
  if (Number.isFinite(Number(it.semantic_score))) {
    const sem = document.createElement("span");
    sem.className = "badge";
    sem.textContent = window.PortalI18n.text`relevance ${it.semantic_score}`;
    sem.title = it.semantic_reason || "";
    right.appendChild(sem);
  }
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = window.PortalI18n.text`importance ${it.importance ?? 0}`;
  right.appendChild(badge);
  meta.appendChild(left);
  meta.appendChild(right);

  const preview = document.createElement("p");
  preview.className = "preview";
  preview.textContent = it.preview || "";

  const resourceId = document.createElement("code");
  resourceId.className = "cardResourceId";
  resourceId.textContent = it.kb_id || "";
  resourceId.title = it.kb_id || "";

  card.appendChild(title);
  card.appendChild(meta);
  card.appendChild(resourceId);
  card.appendChild(preview);

  if (it.type) {
    const chip = document.createElement("span");
    chip.className = "typeChip";
    chip.dataset.type = it.type;
    chip.textContent = it.type;
    card.appendChild(chip);
  }

  return card;
}

function getProjectColumnCount() {
  const width = el.grid?.clientWidth || window.innerWidth || 1;
  if (window.innerWidth <= 620) return 1;
  if (window.innerWidth <= 900) return 2;
  if (window.innerWidth <= 1200) return 3;
  return Math.max(1, Math.floor((width - 24 + 10) / 270));
}

function getVirtualProjectGap() {
  const target = state.virtualProjects.windowEl || el.grid;
  const rowGap = Number.parseFloat(getComputedStyle(target).rowGap);
  return Number.isFinite(rowGap) ? rowGap : 0;
}

function projectVirtualHeight(projectId) {
  const measured = state.virtualProjects.heights.get(projectId);
  return Number.isFinite(measured) && measured > 0
    ? measured
    : defaultProjectVirtualHeight(projectId);
}

function projectDisplayFor(projectId) {
  return state.projectDisplays[projectId] || state.projectDisplayDefault;
}

function defaultProjectVirtualHeight(projectId) {
  const display = projectDisplayFor(projectId);
  if (display === "collapsed") return 50;
  if (display === "expanded") return 360;
  return state.virtualProjects.defaultHeight;
}

function setProjectDisplay(projectId, display) {
  if (!projectId || !["collapsed", "three", "expanded"].includes(display) || display === projectDisplayFor(projectId)) return;
  state.projectDisplays = { ...state.projectDisplays, [projectId]: display };
  state.virtualProjects.heights.delete(projectId);
  saveKnowledgeUiState();
  renderVirtualProjects({ force: true });
}

function clearProjectDragMarkers() {
  document.querySelectorAll(".project.dragOverBefore, .project.dragOverAfter").forEach(section => {
    section.classList.remove("dragOverBefore", "dragOverAfter");
  });
}

function moveProjectBefore(projectId, targetId, after) {
  if (!projectId || !targetId || projectId === targetId) return;
  const knownProjects = new Set((state.raw?.items || []).map(item => item.project || "unknown"));
  const existing = state.projectOrder.filter(project => knownProjects.has(project));
  for (const [project] of groupByProject(state.raw?.items || [])) {
    if (!existing.includes(project)) existing.push(project);
  }
  const from = existing.indexOf(projectId);
  const target = existing.indexOf(targetId);
  if (from < 0 || target < 0) return;
  existing.splice(from, 1);
  const nextTarget = existing.indexOf(targetId);
  existing.splice(nextTarget + (after ? 1 : 0), 0, projectId);
  state.projectOrder = existing;
  state.virtualProjects.heights.clear();
  saveKnowledgeUiState();
  renderAll();
}

function ensureVirtualCanvas() {
  let canvas = state.virtualProjects.canvasEl;
  let windowEl = state.virtualProjects.windowEl;
  if (canvas && windowEl && el.grid.contains(canvas)) return { canvas, windowEl };

  canvas = document.createElement("div");
  canvas.className = "virtualCanvas";
  windowEl = document.createElement("div");
  windowEl.className = "virtualWindow";
  canvas.appendChild(windowEl);
  el.grid.replaceChildren(canvas);
  state.virtualProjects.canvasEl = canvas;
  state.virtualProjects.windowEl = windowEl;
  return { canvas, windowEl };
}

function renderProjectSection(projectId, list) {
  const section = document.createElement("div");
  section.className = "project";
  section.dataset.projectId = projectId;
  const display = projectDisplayFor(projectId);
  section.classList.toggle("collapsed", display === "collapsed");

  const header = document.createElement("div");
  header.className = "projectHeader";
  header.draggable = true;
  header.title = window.PortalI18n.t("拖动项目排序");
  const h2 = document.createElement("h2");
  h2.textContent = projectId;
  const count = document.createElement("div");
  count.className = "count";
  count.textContent = window.PortalI18n.text`${list.length} 项`;
  const actions = document.createElement("div");
  actions.className = "projectHeaderActions";
  actions.appendChild(count);
  const nextDisplays = { collapsed: "three", three: "expanded", expanded: "collapsed" };
  const displayLabels = { collapsed: window.PortalI18n.t("全部折叠"), three: window.PortalI18n.t("三行"), expanded: window.PortalI18n.t("全部展开") };
  const expandBtn = document.createElement("button");
  expandBtn.className = "projectExpandBtn";
  expandBtn.type = "button";
  expandBtn.innerHTML = verticalDisclosureIcon(display === "expanded");
  expandBtn.title = window.PortalI18n.text`当前：${displayLabels[display]}；点击切换到${displayLabels[nextDisplays[display]]}`;
  expandBtn.setAttribute("aria-label", expandBtn.title);
  expandBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    setProjectDisplay(projectId, nextDisplays[display]);
  });
  actions.appendChild(expandBtn);
  header.appendChild(h2);
  header.appendChild(actions);
  section.appendChild(header);

  const itemsEl = document.createElement("div");
  itemsEl.className = "items";
  const columns = getProjectColumnCount();
  const visibleCount = Math.max(1, columns) * 3;
  const renderedItems = display === "expanded"
    ? list
    : display === "three"
      ? list.slice(0, visibleCount)
      : [];

  for (const it of renderedItems) {
    itemsEl.appendChild(createKnowledgeCard(it));
  }

  header.addEventListener("dragstart", (event) => {
    if (event.target.closest("button")) {
      event.preventDefault();
      return;
    }
    projectDragId = projectId;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", projectId);
  });
  header.addEventListener("dragend", () => {
    projectDragId = "";
    clearProjectDragMarkers();
  });
  section.addEventListener("dragover", (event) => {
    const sourceId = projectDragId || event.dataTransfer.getData("text/plain");
    if (!sourceId || sourceId === projectId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const after = event.clientY > section.getBoundingClientRect().top + section.getBoundingClientRect().height / 2;
    clearProjectDragMarkers();
    section.classList.add(after ? "dragOverAfter" : "dragOverBefore");
  });
  section.addEventListener("dragleave", (event) => {
    if (!section.contains(event.relatedTarget)) section.classList.remove("dragOverBefore", "dragOverAfter");
  });
  section.addEventListener("drop", (event) => {
    const sourceId = projectDragId || event.dataTransfer.getData("text/plain");
    if (!sourceId || sourceId === projectId) return;
    event.preventDefault();
    const after = event.clientY > section.getBoundingClientRect().top + section.getBoundingClientRect().height / 2;
    projectDragId = "";
    clearProjectDragMarkers();
    moveProjectBefore(sourceId, projectId, after);
  });

  if (renderedItems.length) section.appendChild(itemsEl);
  return section;
}

function ensureVirtualProjectListeners() {
  if (state.virtualProjects.bound) return;
  state.virtualProjects.bound = true;
  window.addEventListener("scroll", () => scheduleVirtualProjectRender(), { passive: true });
  window.addEventListener("resize", () => {
    state.virtualProjects.heights.clear();
    scheduleVirtualProjectRender({ force: true });
  }, { passive: true });
}

function scheduleVirtualProjectRender(options = {}) {
  if (!state.virtualProjects.groups.length) return;
  if (state.virtualProjects.raf) return;
  state.virtualProjects.raf = window.requestAnimationFrame(() => {
    state.virtualProjects.raf = 0;
    renderVirtualProjects(options);
  });
}

function getProjectVirtualExtent(index, gap) {
  const groups = state.virtualProjects.groups;
  const total = groups.length;
  const projectHeight = projectVirtualHeight(groups[index][0]);
  return projectHeight + (index < total - 1 ? gap : 0);
}

function getVirtualProjectWindow() {
  const groups = state.virtualProjects.groups;
  const total = groups.length;
  const gridRect = el.grid.getBoundingClientRect();
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 1;
  const scrollTop = Math.max(0, -gridRect.top);
  const startBoundary = Math.max(0, scrollTop - viewportHeight * 1.2);
  const endBoundary = scrollTop + viewportHeight * 2.2;
  const gap = getVirtualProjectGap();

  let start = 0;
  let topOffset = 0;
  let offset = 0;
  while (start < total) {
    const extent = getProjectVirtualExtent(start, gap);
    if (offset + extent >= startBoundary) break;
    offset += extent;
    start += 1;
  }
  topOffset = offset;

  let end = start;
  while (end < total && offset < endBoundary) {
    offset += getProjectVirtualExtent(end, gap);
    end += 1;
  }

  let totalHeight = 0;
  for (let i = 0; i < total; i += 1) {
    totalHeight += getProjectVirtualExtent(i, gap);
  }

  return { start, end, topOffset, totalHeight };
}

function measureRenderedProjects() {
  let changed = false;
  for (const section of el.grid.querySelectorAll(".project[data-project-id]")) {
    const projectId = section.dataset.projectId;
    const height = Math.ceil(section.getBoundingClientRect().height);
    const previous = state.virtualProjects.heights.get(projectId);
    if (Number.isFinite(height) && height > 0 && Math.abs((previous || 0) - height) > 8) {
      state.virtualProjects.heights.set(projectId, height);
      changed = true;
    }
  }
  if (changed) scheduleVirtualProjectRender();
}

function renderVirtualProjects(options = {}) {
  const groups = state.virtualProjects.groups;
  if (!groups.length) {
    el.grid.innerHTML = "";
    state.virtualProjects.renderStart = -1;
    state.virtualProjects.renderEnd = -1;
    state.virtualProjects.renderTopOffset = -1;
    state.virtualProjects.renderTotalHeight = -1;
    state.virtualProjects.canvasEl = null;
    state.virtualProjects.windowEl = null;
    return;
  }

  const { canvas, windowEl } = ensureVirtualCanvas();
  const { start, end, topOffset, totalHeight } = getVirtualProjectWindow();
  canvas.style.height = `${Math.max(1, Math.round(totalHeight))}px`;
  windowEl.style.transform = `translateY(${Math.round(topOffset)}px)`;

  if (!options.force
    && start === state.virtualProjects.renderStart
    && end === state.virtualProjects.renderEnd
    && Math.abs(topOffset - state.virtualProjects.renderTopOffset) <= 1
    && Math.abs(totalHeight - state.virtualProjects.renderTotalHeight) <= 1) {
    return;
  }

  state.virtualProjects.renderStart = start;
  state.virtualProjects.renderEnd = end;
  state.virtualProjects.renderTopOffset = topOffset;
  state.virtualProjects.renderTotalHeight = totalHeight;

  const fragment = document.createDocumentFragment();
  for (let i = start; i < end; i += 1) {
    const [projectId, list] = groups[i];
    fragment.appendChild(renderProjectSection(projectId, list));
  }
  windowEl.replaceChildren(fragment);

  window.requestAnimationFrame(measureRenderedProjects);
}

function renderProjects(items) {
  for (const ro of state.projectResizeObservers) ro.disconnect();
  state.projectResizeObservers = [];

  state.virtualProjects.groups = groupByProject(items);
  state.virtualProjects.renderStart = -1;
  state.virtualProjects.renderEnd = -1;
  state.virtualProjects.renderTopOffset = -1;
  state.virtualProjects.renderTotalHeight = -1;
  ensureVirtualProjectListeners();
  renderVirtualProjects({ force: true });
}

function renderAll() {
  if (!state.raw) return;
  const items = (state.raw.items || [])
    .filter((x) => matchesFilter(x))
    .filter((x) => matchesQuery(x, state.query));

  const loadedTotal = state.raw.items?.length || 0;
  const total = state.indexPaging.total || loadedTotal;
  const typeLabel = filter.type || window.PortalI18n.t("全部类型");
  const semantic = state.raw.semantic_search || null;
  const summary = semantic
    ? window.PortalI18n.text`语义检索：${semantic.query || ""} · ${items.length} 项`
    : window.PortalI18n.text`${typeLabel} · ${items.length} / ${total} 项`;
  el.meta.replaceChildren(
    Object.assign(document.createElement("span"), { textContent: summary }),
    Object.assign(document.createElement("span"), {
      textContent: state.indexPaging.error
        ? window.PortalI18n.text`已加载 ${loadedTotal}/${total} · 索引加载失败`
        : window.PortalI18n.text`已加载 ${loadedTotal}/${total}`,
      title: state.indexPaging.error || "",
    }),
  );
  if (!items.length) {
    if (state.exactQuery.status === "loading" && state.exactQuery.id === state.query) {
      el.empty.textContent = window.PortalI18n.text`正在按 Resource ID 查询 ${state.query}…`;
    } else if (state.exactQuery.status === "error" && state.exactQuery.id === state.query) {
      el.empty.textContent = window.PortalI18n.text`${state.exactQuery.error}。请检查 ID 或访问权限后重试。`;
    } else if (state.indexPaging.error) {
      el.empty.textContent = window.PortalI18n.text`索引加载失败：${state.indexPaging.error}。请点击刷新后重试。`;
    } else if (state.query) {
      el.empty.textContent = window.PortalI18n.t("没有本地匹配。自然语言问题可使用搜索框旁的“语义检索”。");
    } else {
      el.empty.textContent = window.PortalI18n.t("没有匹配的条目");
    }
  }
  el.empty.style.display = items.length ? "none" : "block";
  renderProjects(items);
  if (state.knowledgeMap.open) {
    buildKnowledgeMapControls();
    renderKnowledgeMap();
  }
  renderKnowledgeRadar();
  syncKnowledgeView();
  syncKnowledgeMatrixSelection();
}
