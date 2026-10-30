// Knowledge board: the saved page state, the filter panel and the Resource matrix filter.

function readKnowledgeUiState() {
  return knowledgePageState.read();
}

function writeKnowledgeUiState(patch) {
  knowledgePageState.write(patch || {});
}

function writeKnowledgeUiStateSoon(patch) {
  knowledgePageState.debounceWrite(patch || {});
}

function filterSnapshot() {
  return {
    status: filter.status || "",
    type: filter.type || null,
    projects: filter.projects ? knowledgePageState.setToArray(filter.projects) : null,
    date: filter.date || "all",
    importance: Number(filter.importance || 0),
  };
}

function compactKnowledgeFilterValues(values) {
  const items = [...values].map(String).filter(Boolean);
  if (items.length <= 2) return items.join("、");
  return `${items.slice(0, 2).join("、")} 等 ${items.length} 项`;
}

function knowledgeFilterSummaryItems() {
  const items = [];
  const dateLabels = {
    "2d": window.PortalI18n.t("最近两天"),
    "7d": window.PortalI18n.t("最近一周"),
    "30d": window.PortalI18n.t("最近一月"),
  };
  if (filter.date !== "all") items.push({ label: window.PortalI18n.t("日期"), value: dateLabels[filter.date] || filter.date });
  if (filter.status) {
    items.push({ label: window.PortalI18n.t("状态"), value: filter.status === "__none__" ? "无状态" : filter.status });
  }
  if (filter.projects instanceof Set) {
    items.push({ label: window.PortalI18n.t("Project"), value: compactKnowledgeFilterValues(filter.projects) });
  }
  if (filter.type) items.push({ label: window.PortalI18n.t("类型"), value: filter.type });
  if (filter.importance > 0) items.push({ label: "Importance", value: `≥ ${filter.importance}` });
  return items;
}

function syncKnowledgeFilterInputs() {
  const statusInput = document.querySelector(`input[name="fp_status"][value="${CSS.escape(filter.status || "")}"]`);
  const dateInput = document.querySelector(`input[name="fp_date"][value="${CSS.escape(filter.date || "all")}"]`);
  const importanceInput = document.querySelector(`input[name="fp_imp"][value="${CSS.escape(String(filter.importance || 0))}"]`);
  if (statusInput) statusInput.checked = true;
  if (dateInput) dateInput.checked = true;
  if (importanceInput) importanceInput.checked = true;
}

function clearKnowledgeFilters() {
  filter.status = "";
  filter.type = null;
  filter.projects = null;
  filter.date = "all";
  filter.importance = 0;
  saveKnowledgeUiState();
  buildFilterOptions();
  syncKnowledgeFilterInputs();
  updateKnowledgeMatrixUrl("", "");
  renderAll();
  knowledgeBoardShell.syncFilterState();
}

function applyKnowledgeUiState() {
  const saved = readKnowledgeUiState();
  if (typeof saved.query === "string") state.query = saved.query;
  if (saved.view === "map") state.knowledgeMap.open = true;
  if (typeof saved.activeSemanticSearchId === "string") state.activeSemanticSearchId = saved.activeSemanticSearchId;
  if (["collapsed", "three", "expanded"].includes(saved.projectDisplay)) state.projectDisplayDefault = saved.projectDisplay;
  if (saved.projectDisplays && typeof saved.projectDisplays === "object" && !Array.isArray(saved.projectDisplays)) {
    state.projectDisplays = Object.fromEntries(
      Object.entries(saved.projectDisplays).filter(([, display]) => ["collapsed", "three", "expanded"].includes(display)),
    );
  }
  if (Array.isArray(saved.projectOrder)) state.projectOrder = [...new Set(saved.projectOrder.map(String).filter(Boolean))];
  const savedFilter = saved.filter && typeof saved.filter === "object" ? saved.filter : {};
  if (typeof savedFilter.status === "string") filter.status = savedFilter.status;
  if (typeof savedFilter.type === "string") filter.type = savedFilter.type.trim() || null;
  else if (savedFilter.type === null) filter.type = null;
  if (Array.isArray(savedFilter.projects)) {
    const savedProjects = savedFilter.projects.map(String).filter(Boolean);
    filter.projects = savedProjects.length ? new Set(savedProjects) : null;
  }
  else if (savedFilter.projects === null) filter.projects = null;
  if (typeof savedFilter.date === "string") filter.date = savedFilter.date || "all";
  if (savedFilter.importance !== undefined) filter.importance = Number(savedFilter.importance || 0);
}

function normalizeFilterStateForIndex() {
  const allowedStatuses = new Set(["", "__none__", "open", "done", "dismiss", "obsolete"]);
  if (!allowedStatuses.has(filter.status)) filter.status = "";

  const allowedDates = new Set(["all", "2d", "7d", "30d"]);
  if (!allowedDates.has(filter.date)) filter.date = "all";

  const allowedImportance = new Set([0, 3, 5]);
  if (!allowedImportance.has(Number(filter.importance || 0))) filter.importance = 0;

  const allowedTypes = new Set(state.raw?.allowed_types || []);
  if (filter.type && !allowedTypes.has(filter.type)) filter.type = null;

  const projects = [...new Set([
    ...(state.raw?.items || []).map(i => i.project).filter(Boolean),
    ...(state.raw?.projects || []).map(project => project?.id).filter(Boolean),
  ])];
  if (filter.projects instanceof Set) {
    const allowedProjects = new Set(projects);
    const next = new Set([...filter.projects].filter(p => allowedProjects.has(p)));
    filter.projects = next.size ? next : null;
  } else {
    filter.projects = null;
  }
}

function saveKnowledgeUiState() {
  writeKnowledgeUiState({
    query: state.query || "",
    view: activeKnowledgeView(),
    activeSemanticSearchId: state.activeSemanticSearchId || "",
    filter: filterSnapshot(),
    projectDisplayDefault: state.projectDisplayDefault,
    projectDisplays: state.projectDisplays,
    projectOrder: state.projectOrder,
  });
}

function requestedKnowledgeMatrixFilter() {
  const params = new URLSearchParams(window.location.search);
  const rawType = String(params.get("type") || "").trim();
  const rawProject = String(params.get("project") || "").trim();
  return {
    hasType: params.has("type"),
    type: rawType && rawType !== "__all__" ? rawType : "",
    hasProject: params.has("project"),
    project: rawProject && rawProject !== "__all__" ? rawProject : "",
  };
}

function updateKnowledgeMatrixUrl(type, project) {
  const url = new URL(window.location.href);
  if (type) url.searchParams.set("type", type);
  else url.searchParams.delete("type");
  if (project) url.searchParams.set("project", project);
  else url.searchParams.delete("project");
  history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function syncKnowledgeMatrixSelection() {
  window.PortalResourceTabs?.setKnowledgeSelection?.({
    type: filter.type || "",
    projects: filter.projects ? [...filter.projects] : null,
  });
}

function applyKnowledgeMatrixFilter(type, project) {
  filter.type = String(type || "").trim() || null;
  const selectedProject = String(project || "").trim();
  filter.projects = selectedProject ? new Set([selectedProject]) : null;
  if (state.raw) normalizeFilterStateForIndex();
  buildFilterOptions();
  renderAll();
  knowledgeBoardShell.syncFilterState();
  updateKnowledgeMatrixUrl(filter.type || "", filter.projects ? [...filter.projects][0] || "" : "");
}

// ── Date helpers ──────────────────────────────────────────────────────
function cutoffDate(range) {
  if (range === "all") return null;
  const now = Date.now();
  const days = range === "2d" ? 2 : range === "7d" ? 7 : 30;
  return new Date(now - days * 86400 * 1000);
}

// ── Filter application ────────────────────────────────────────────────
function matchesFilter(item) {
  if (filter.status) {
    const itemStatus = String(item.status || "").trim();
    if (filter.status === "__none__") {
      if (itemStatus) return false;
    } else if (itemStatus !== filter.status) return false;
  }
  // Type
  if (filter.type && item.type !== filter.type) return false;
  // Project
  if (filter.projects !== null && !filter.projects.has(item.project)) return false;
  // Date (based on file mtime, not created_at)
  if (filter.date !== "all") {
    const cutoff = cutoffDate(filter.date);
    const d = item.mtime ? new Date(item.mtime * 1000) : null;
    if (!d || d < cutoff) return false;
  }
  // Importance
  if (filter.importance > 0 && (item.importance ?? 0) < filter.importance) return false;
  return true;
}

// ── Build filter panel options ────────────────────────────────────────
function buildFilterOptions() {
  const items = state.raw?.items || [];
  const allowedTypes = state.raw?.allowed_types || [];
  const projects = [...new Set(items.map(i => i.project).filter(Boolean))].sort();

  knowledgeBoardShell.renderRadioGroup({
    container: el.fpType,
    name: "fp_type",
    selected: filter.type || "",
    items: [
      { value: "", label: window.PortalI18n.t("全部") },
      ...allowedTypes.map(type => ({
        value: type,
        label: type,
        count: items.filter(item => item.type === type).length,
      })),
    ],
    onChange: value => {
      filter.type = value || null;
      saveKnowledgeUiState();
      renderAll();
      knowledgeBoardShell.syncFilterState();
    },
  });

  knowledgeBoardShell.renderCheckboxGroup({
    container: el.fpProjects,
    items: projects.map(project => ({
      value: project,
      label: project,
      count: items.filter(item => item.project === project).length,
    })),
    selected: filter.projects,
    onChange: selected => {
      filter.projects = selected;
      saveKnowledgeUiState();
      renderAll();
      buildFilterOptions();
      knowledgeBoardShell.syncFilterState();
    },
  });
}
