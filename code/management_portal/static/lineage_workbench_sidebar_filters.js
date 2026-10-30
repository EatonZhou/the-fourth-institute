(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarFilters) return;

  // Searching and filtering the Agent list: the Project options, the
  // filter panel, the search and filter buttons and changing the filters.

  const DEPENDENCIES = Object.freeze([
    "$id", "currentAgentFilterDropdown", "currentAgentFilterSummary", "esc", "lineageIdForResident",
    "normalizeProjectOptions", "normalizedProjectDisplayFilter", "projectOptionById",
    "refreshOpenResidentRegistrationOptions", "renderActivityRecentRail", "renderAgentList", "renderFavoriteList",
    "state", "writeLineageUiState",
  ]);

  function createSidebarFilters(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarFilters is missing: ${missing.join(", ")}`);
    const {
      $id, currentAgentFilterDropdown, currentAgentFilterSummary, esc, lineageIdForResident, normalizeProjectOptions,
      normalizedProjectDisplayFilter, projectOptionById, refreshOpenResidentRegistrationOptions,
      renderActivityRecentRail, renderAgentList, renderFavoriteList, state, writeLineageUiState,
    } = dependencies;

    function updateProjectOptions({ render = false } = {}) {
      const projects = normalizeProjectOptions(state.registryProjects);
      const signature = JSON.stringify(projects);
      if (signature === state.projectOptionsSignature) return false;
      state.projectOptionsSignature = signature;
      state.projects = projects;
      refreshOpenResidentRegistrationOptions();
      if (render) {
        renderAgentList();
        renderFavoriteList();
        renderActivityRecentRail();
      }
      return true;
    }

    function projectFilterOptions() {
      const seen = new Map();
      for (const lineage of state.lineages || []) {
        const value = String(lineage?.project_id || "").trim();
        if (!value || seen.has(value)) continue;
        const project = projectOptionById(value);
        seen.set(value, project?.name || value);
      }
      for (const item of state.residents) {
        if (lineageIdForResident(item)) continue;
        const value = item.project_id || item.project || "";
        if (!value || seen.has(value)) continue;
        seen.set(value, item.project || value);
      }
      return [...seen.entries()]
        .map(([value, label]) => ({ value, label }))
        .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
    }

    function renderProjectFilterOptions() {
      const options = projectFilterOptions();
      if (state.projectFilter !== "all" && !options.some(item => item.value === state.projectFilter)) {
        state.projectFilter = "all";
      }
      state.projectDisplayFilter = normalizedProjectDisplayFilter(state.projectDisplayFilter);
      const projectOptions = [
        { value: "all", label: window.PortalI18n.t("全部项目") },
        ...options,
      ].map(item => window.PortalI18n.html`
          <button class="filterOption" type="button" data-project-filter="${esc(item.value)}" title="${esc(item.label)}">${esc(item.label)}</button>
        `).join("");
      const displayOptions = [
        { value: "non-empty", label: window.PortalI18n.t("有内容") },
        { value: "all", label: window.PortalI18n.t("全部") },
      ].map(item => window.PortalI18n.html`
          <button class="filterOption" type="button" data-project-display-filter="${esc(item.value)}" title="${esc(item.label)}">${esc(item.label)}</button>
        `).join("");
      $id("agentFilterPanel").innerHTML = window.PortalI18n.html`
          <div class="filterSection">
            <div class="filterLabel">显示范围</div>
            ${displayOptions}
          </div>
          <div class="filterSection">
            <div class="filterLabel">项目</div>
            ${projectOptions}
          </div>`;
    }

    function syncAgentTools() {
      const searchWrap = $id("agentSearchWrap");
      const searchBtn = $id("searchToggleBtn");
      const filterBtn = $id("filterToggleBtn");
      const filterPanel = $id("agentFilterPanel");
      const queryActive = !!state.query.trim();
      searchWrap.classList.toggle("open", state.searchOpen);
      searchBtn.classList.toggle("is-active", state.searchOpen || queryActive);
      searchBtn.setAttribute("aria-expanded", state.searchOpen ? "true" : "false");
      filterBtn.classList.toggle("is-active", state.filterOpen || state.projectFilter !== "all" || state.projectDisplayFilter !== "non-empty");
      filterBtn.setAttribute("aria-expanded", state.filterOpen ? "true" : "false");
      filterPanel.classList.toggle("open", state.filterOpen);
      filterPanel.setAttribute("aria-hidden", state.filterOpen ? "false" : "true");
      document.querySelectorAll("[data-project-filter]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.projectFilter === state.projectFilter);
      });
      document.querySelectorAll("[data-project-display-filter]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.projectDisplayFilter === state.projectDisplayFilter);
      });
      currentAgentFilterSummary()?.sync?.();
    }

    function setSearchOpen(open) {
      state.searchOpen = !!open;
      writeLineageUiState({ searchOpen: state.searchOpen });
      syncAgentTools();
      if (state.searchOpen) {
        window.setTimeout(() => $id("searchInput").focus(), 0);
      }
    }

    function setProjectFilter(filter) {
      state.projectFilter = filter || "all";
      currentAgentFilterDropdown()?.close();
      writeLineageUiState({ projectFilter: state.projectFilter });
      renderAgentList();
    }

    function setProjectDisplayFilter(filter) {
      state.projectDisplayFilter = normalizedProjectDisplayFilter(filter);
      currentAgentFilterDropdown()?.close();
      writeLineageUiState({ projectDisplayFilter: state.projectDisplayFilter });
      renderAgentList();
    }

    return {
      projectFilterOptions, renderProjectFilterOptions, setProjectDisplayFilter, setProjectFilter, setSearchOpen,
      syncAgentTools, updateProjectOptions,
    };
  }

  window.PortalLineageWorkbenchSidebarFilters = Object.freeze({ createSidebarFilters, dependencies: DEPENDENCIES });
})();
