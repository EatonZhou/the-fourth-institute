(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebar) return;

  // The Agent and favourites side views: the Projects, Lineages and Residents the
  // user can reach, filtered and grouped, the Residents list snapshot and its
  // refresh, the personal assistant entry, favourites, and revealing a target
  // in the list.
  const DEPENDENCIES = Object.freeze([
    "$id", "activeChatTargetId", "activeLineageId", "bindAgentCardControls",
    "bindWhichResidentControls", "consoleStateHtml", "currentAgentChatPanel",
    "currentAgentFilterDropdown", "currentAgentFilterSummary", "esc", "lineageIdForResident",
    "recentTimeMs", "refreshOpenResidentRegistrationOptions", "refreshRecommendationChats",
    "refreshResidentSidebarIfDue", "renderActivityRecentRail", "renderAgentFloatingMenu",
    "residentExactId", "residentSelectionId", "residentService", "selectedResidentId",
    "setSideView", "state", "whichResidentCardHtml", "writeLineageUiState",
  ]);

  function fmtDateTime(value) {
    return window.PortalUI.formatDateTime(value, { style: "compact" });
  }

  function normalizeProjectOptions(projects) {
    return [...new Map((projects || []).flatMap(project => {
      const id = String(project?.project_id || "").trim();
      return id ? [[id, { ...project, id, name: id }]] : [];
    })).values()];
  }

  function residentSearchText(item) {
    return [
      item?.title,
      item?.label,
      item?.name,
      item?.resident_id,
      item?.agent_id,
      item?.project_id,
      item?.project,
      item?.kind,
      item?.backend,
      item?.workspace_path,
      item?.created_by,
    ].join(" ").toLowerCase();
  }

  function lineageLeaderResidentId(item) {
    return String(
      item?.lineage_leader_resident_id
      || item?.lineage?.leader_resident_id
      || ""
    ).trim();
  }

  function isLineageLeaderResident(item) {
    const residentId = String(item?.resident_id || "").trim();
    return item?.is_lineage_leader === true
      || (!!residentId && residentId === lineageLeaderResidentId(item));
  }

  function normalizedProjectDisplayFilter(filter) {
    return ["all", "non-empty"].includes(filter) ? filter : "non-empty";
  }

  function projectFavoriteSelectionId(project) { return `project:${project.project_id}`; }

  function subscribeResidentSidebar(callbacks) {
    const subscription = window.PortalRefreshCoordinator.portalSubscription({
      domains: ["projection"],
      matches: detail => {
        if (detail?.snapshot_required) return true;
        return Object.prototype.hasOwnProperty.call(
          detail?.projection_changes?.["resident-sidebar"] || {},
          "snapshot",
        );
      },
    });
    return subscription(callbacks);
  }

  function lineageSidebarResidentsForItem(item) {
    if (Array.isArray(item?._lineageSidebarResidents)) return item._lineageSidebarResidents;
    return [];
  }

  function sidebarElementForTarget(panel, { residentId = "" } = {}) {
    if (!panel) return null;
    const cleanResidentId = String(residentId || "").trim();
    if (cleanResidentId) {
      const forkButton = panel.querySelector(`[data-member-resident-id="${CSS.escape(cleanResidentId)}"]`);
      if (forkButton) return forkButton.closest(".residentMemberItem") || forkButton;
      return panel.querySelector(`[data-resident-card="${CSS.escape(cleanResidentId)}"]`)
        || panel.querySelector(`[data-resident-id="${CSS.escape(cleanResidentId)}"]`);
    }
    return null;
  }

  function createSidebar(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebar is missing: ${missing.join(", ")}`);
    const {
      $id, activeChatTargetId, activeLineageId, bindAgentCardControls, bindWhichResidentControls,
      consoleStateHtml, currentAgentChatPanel, currentAgentFilterDropdown,
      currentAgentFilterSummary, esc, lineageIdForResident, recentTimeMs,
      refreshOpenResidentRegistrationOptions, refreshRecommendationChats,
      refreshResidentSidebarIfDue, renderActivityRecentRail, renderAgentFloatingMenu,
      residentExactId, residentSelectionId, residentService, selectedResidentId, setSideView,
      state, whichResidentCardHtml, writeLineageUiState,
    } = dependencies;

    let personalAssistantRead = 0;

    const residentSidebarRefreshCoordinator = window.PortalRefreshCoordinator.create({
      key: () => String(state.me || "").toLowerCase(),
      interval: 120000,
      initialDelay: () => 1000 + Math.floor(Math.random() * 1000),
      hiddenInterval: 300000,
      pauseWhenHidden: true,
      repairInterval: false,
      shouldRefresh: () => !!state.me,
      fetch: fetchResidentSidebarSnapshot,
      apply: applyResidentSidebarSnapshot,
      subscribe: subscribeResidentSidebar,
      onError: () => {},
    });

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

    function residentTimeValue(item) {
      return recentTimeMs(item?.last_interaction_at, item?.updated_at, item?.created_at);
    }

    function sortLineageResidents(items) {
      return [...(items || [])].sort((left, right) => {
        const leftLeader = isLineageLeaderResident(left) ? 1 : 0;
        const rightLeader = isLineageLeaderResident(right) ? 1 : 0;
        if (leftLeader !== rightLeader) return rightLeader - leftLeader;
        return residentTimeValue(right) - residentTimeValue(left);
      });
    }

    function lineageSidebarRoots(_kind = "", { respectFilters = true } = {}) {
      const groups = new Map();
      for (const item of state.residents || []) {
        const lineageId = lineageIdForResident(item) || String(item.resident_id || "");
        if (!lineageId) continue;
        if (!groups.has(lineageId)) groups.set(lineageId, []);
        groups.get(lineageId).push(item);
      }
      const roots = [];
      const lineageCards = Array.isArray(state.lineages) ? state.lineages : [];
      for (const lineage of lineageCards) {
        const lineageId = String(lineage?.lineage_id || "").trim();
        if (!lineageId) continue;
        const allMembers = sortLineageResidents(groups.get(lineageId) || []);
        const projectId = String(lineage?.project_id || "").trim();
        if (
          respectFilters
          && state.projectFilter !== "all"
          && state.projectFilter !== projectId
        ) continue;
        const query = state.query.trim().toLowerCase();
        const visibleMembers = respectFilters
          ? allMembers.filter(item => !query || residentSearchText(item).includes(query))
          : allMembers;
        const lineageMatches = !query
          || [lineage?.title, lineage?.description, lineageId]
            .some(value => String(value || "").toLowerCase().includes(query));
        if (respectFilters && !visibleMembers.length && !lineageMatches) continue;
        const leaderId = String(lineage?.leader_resident_id || "").trim();
        const carrier = allMembers.find(item => String(item?.resident_id || "") === leaderId)
          || allMembers[0]
          || {};
        const root = {
          ...carrier,
          resident_id: String(carrier?.resident_id || leaderId || `lineage:${lineageId}`),
          lineage_id: lineageId,
          kind: "lineage",
          project_id: projectId,
          created_at: String(lineage?.created_at || carrier?.created_at || ""),
          updated_at: String(lineage?.updated_at || carrier?.updated_at || ""),
          lineage_leader_resident_id: leaderId,
          created_by: String(lineage?.created_by || ""),
          lineage_node_ids: Array.isArray(lineage?.lineage_node_ids) ? lineage.lineage_node_ids : [],
          lineage: {
            lineage_id: lineageId,
            title: String(lineage?.title || lineageId),
            project_id: String(lineage?.project_id || ""),
            leader_resident_id: leaderId,
            created_by: String(lineage?.created_by || ""),
            lineage_node_ids: Array.isArray(lineage?.lineage_node_ids) ? lineage.lineage_node_ids : [],
          },
          _lineageSidebarResidents: allMembers,
          _lineageSidebarCarriers: allMembers,
          _lineageSidebarResidentCount: Number(lineage?.resident_count || allMembers.length),
          _lineageSidebarMatchedResidentIds: new Set(visibleMembers.map(item => String(item?.resident_id || ""))),
        };
        roots.push(root);
      }
      return roots.sort((left, right) => {
        const leftMembers = left._lineageSidebarResidents || [];
        const rightMembers = right._lineageSidebarResidents || [];
        const leftTime = leftMembers.length
          ? Math.max(...leftMembers.map(residentTimeValue))
          : residentTimeValue(left);
        const rightTime = rightMembers.length
          ? Math.max(...rightMembers.map(residentTimeValue))
          : residentTimeValue(right);
        return rightTime - leftTime;
      });
    }

    function lineageSidebarRootByResidentId(residentId) {
      const id = String(residentId || "").trim();
      if (!id) return null;
      const roots = lineageSidebarRoots("", { respectFilters: false });
      // Membership determines navigation; another graph may also reference
      // this Resident. Sidebar sort order must not make that graph the owner.
      return roots.find(item => lineageSidebarResidentsForItem(item).some(member => (
        String(member?.resident_id || "").trim() === id
      )))
        || state.residents.find(item => String(item?.resident_id || "").trim() === id)
        || roots.find(item => (
          (Array.isArray(item?.lineage_node_ids) && item.lineage_node_ids.some(nodeId => (
            String(nodeId || "").trim() === id
          )))
          || (Array.isArray(item?.lineage?.lineage_node_ids) && item.lineage.lineage_node_ids.some(nodeId => (
            String(nodeId || "").trim() === id
          )))
        ))
        || null;
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

    function projectMetaForAgent(item) {
      const projectId = String(item?.project_id || item?.project || "").trim();
      if (!projectId) return null;
      const project = projectOptionById(projectId);
      const label = String(project?.name || item?.project || project?.id || projectId).trim();
      return {
        id: String(project?.id || projectId).trim(),
        label,
      };
    }

    function residentGroups() {
      const projectGroups = window.PortalPrincipalNavigation.projectGroups(
        state.principalHierarchy,
        lineageSidebarRoots("", { respectFilters: false }),
        {
          query: state.query,
          projectFilter: state.projectFilter, nonEmptyOnly: state.projectDisplayFilter === "non-empty",
        },
      );
      return [{ id: "lineage", title: window.PortalI18n.t("My Projects"), items: [], itemType: "resident", projectGroups,
        empty: residentSidebarPendingText("Project") || (state.query ? "没有匹配的 Project。" : "暂无可访问的 Project。") }];
    }

    // Until the first ready sidebar snapshot arrives nothing is known about
    // Projects or Which Resident: say it is being read (or failed), never
    // that there is none or the service is down.
    function residentSidebarPendingText(subject) {
      if (state.residentSidebarReady) return "";
      return state.residentSidebarError ? `${subject} 读取失败：${state.residentSidebarError}` : `正在读取 ${subject}…`;
    }

    function isPersonalAmaResident(item) {
      return !!state.personalAmaResidentId
        && String(item?.resident_id || "").trim() === state.personalAmaResidentId;
    }

    function lineageContainsPersonalAmaLeader(item) {
      return lineageSidebarResidentsForItem(item).some(member => isPersonalAmaResident(member));
    }

    function renderResidentGroup(group) {
      const queryActive = !!state.query.trim();
      const open = queryActive || state.residentGroups[group.id] !== false;
      const hasProjectGroups = Array.isArray(group.projectGroups) && group.projectGroups.length;
      const bodyHtml = hasProjectGroups
        ? renderProjectSubgroups(group)
        : group.items.length
          ? group.items.map(renderRegisteredResidentItem).join("")
          : window.PortalI18n.html`<div class="residentEmpty">${esc(group.empty)}</div>`;
      return window.PortalI18n.html`<section class="residentSection ${open ? "open" : "collapsed"}" data-resident-group="${esc(group.id)}">
          <div class="residentGroupHead">
            <button class="residentToggle" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
            <button class="residentGroupName" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
            ${group.id === "lineage" ? window.PortalI18n.html`<button class="residentAddBtn" type="button" data-project-create aria-label="创建 Project" title="创建 Project" data-portal-icon="plus"></button>` : ""}
          </div>
          ${open ? window.PortalI18n.html`<div class="residentGroupBody">
            ${bodyHtml}
          </div>` : ""}
        </section>`;
    }

    function favoriteStar(selectionId) {
      const favorite = isFavoriteAgent(selectionId);
      return window.PortalI18n.html`<button type="button" class="agentFavoriteBtn ${favorite ? "active" : ""}" data-agent-favorite="${esc(selectionId)}" aria-pressed="${favorite}" aria-label="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" title="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" data-portal-icon="${favorite ? "star-filled" : "star"}"></button>`;
    }

    function projectCardHtml(project) {
      const open = !!state.query.trim() || state.residentProjectGroups[project.id] === true;
      const accessible = project.accessible !== false;
      const denied = !accessible && !project.can_initialize;
      const canOpen = accessible && !!project.resident_id;
      const menuId = `project:${project.project_id}`;
      return window.PortalI18n.html`<section class="residentSubgroup principalProject" data-resident-project-group="${esc(project.id)}">
          <div class="agentItem principalProjectCard ${denied ? "locked" : ""} ${project.resident_id && state.chatTarget?.id === project.resident_id ? "active" : ""}">
            <button type="button" class="agentSelect noStatusDot" data-resident-project-toggle="${esc(project.id)}" aria-expanded="${open}" ${project.unavailable ? "disabled" : ""}>
              <span class="principalProjectChevron" data-portal-icon="${open ? "chevron-down" : "chevron-right"}"></span>
              <span class="agentMain"><span class="agentTitle">${esc(project.title)}</span><span class="agentMeta"><span class="principalKind">Project</span><span class="principalMetaDetail" title="${project.unavailable ? window.PortalI18n.t("无访问权限") : window.PortalI18n.text`共 ${project.items.length} 个 Lineage`}" ${project.unavailable ? "" : window.PortalI18n.text`aria-label="共 ${project.items.length} 个 Lineage"`}>${project.unavailable ? window.PortalI18n.t("无访问权限") : window.PortalI18n.text`${project.items.length} 组`}</span></span></span>
            </button>
            ${!denied ? "" : window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限" title="当前无权打开 Project 对话"></span>')}
            ${favoriteStar(project.favoriteSelectionId || projectFavoriteSelectionId(project))}
            <button type="button" class="principalCreateBtn" data-project-lineage-create="${esc(project.project_id)}" ${project.unavailable || project.project_readable === false ? "disabled" : ""} aria-label="在 ${esc(project.title)} 中添加 Lineage" title="添加 Lineage" data-portal-icon="plus"></button>
            <button type="button" class="principalOpenBtn" data-principal-open="${esc(project.resident_id || "")}" ${canOpen ? "" : "disabled"} aria-label="打开 ${esc(project.title)} 对话和 HATG" title="打开 Project 对话和 HATG" data-portal-icon="arrow-right"></button>
            <button type="button" class="agentMenuBtn" data-agent-menu="${esc(menuId)}" ${project.unavailable || project.project_readable === false ? "disabled" : ""} aria-label="${esc(project.title)} 菜单" data-portal-icon="more-horizontal"></button>
          </div>
          ${open && !project.unavailable ? window.PortalI18n.html`<div class="residentSubgroupBody">${project.items.map(item => renderRegisteredResidentItem(item, { asLineage: true })).join("") || window.PortalI18n.t('<div class="residentEmpty">暂无可访问的 Lineage。</div>')}</div>` : ""}
        </section>`;
    }

    function renderProjectSubgroups(group) {
      return window.PortalI18n.html`<div class="residentSubgroups">${(group.projectGroups || []).map(projectCardHtml).join("")}</div>`;
    }

    function residentSidebarSignature(residents) {
      return JSON.stringify([
        (Array.isArray(state.lineages) ? state.lineages : []).map(item => [
          item?.lineage_id || "",
          item?.title || "",
          item?.description || "",
          item?.project_id || "",
          item?.leader_resident_id || "",
          item?.allows || {},
          String(item?.created_by || ""),
          Array.isArray(item?.lineage_node_ids) ? item.lineage_node_ids : [],
          item?.resident_count || 0,
        ]),
        (Array.isArray(residents) ? residents : []).map(item => [
        item?.resident_id || "",
        item?.title || "",
        item?.lineage_id || "",
        String(item?.created_by || ""),
        item?.last_token_usage_at || "",
        item?.last_user_actor || "",
        item?.unread_signature || null,
        item?.conversation_signature || "",
      ]),
      ]);
    }

    // The navigation lists every Lineage and Resident the user can access;
    // favorites only add the "我的收藏" view.
    function applyResidentSidebarResidents(residents, lineages = []) {
      const uniqueResidents = new Map();
      for (const item of Array.isArray(residents) ? residents : []) {
        const residentId = String(item?.resident_id || "");
        if (!residentId) continue;
        uniqueResidents.set(residentId, { ...uniqueResidents.get(residentId), ...item });
      }
      const nextResidents = [...uniqueResidents.values()];
      const nextLineages = Array.isArray(lineages) ? lineages : [];
      const nextSignature = JSON.stringify([residentSidebarSignature(nextResidents), nextLineages]);
      const changed = nextSignature !== state.residentSidebarSignature;
      state.residentSidebarSignature = nextSignature;
      if (!changed) return false;
      state.lineages = nextLineages;
      state.residents = nextResidents;
      renderAgentList();
      renderFavoriteList();
      renderActivityRecentRail();
      return true;
    }

    async function fetchResidentSidebarSnapshot(options = {}) {
      return window.PortalLineageCatalogs.residents({
        values: {},
        force: options.force === true,
        load: values => residentService.list(values),
      });
    }

    function applyResidentSidebarSnapshotNow(payload) {
      const data = payload && typeof payload === "object" ? payload : {};
      state.residentSidebarReady = data.ready !== false;
      state.residentSidebarError = String(data.error || "");
      if (data.account_selection
        && JSON.stringify(data.account_selection) !== JSON.stringify(state.accountSelection)) {
        state.accountSelection = data.account_selection;
        refreshOpenResidentRegistrationOptions();
      }
      if (!state.residentSidebarReady) return false;

      const hierarchyChanged = JSON.stringify(state.principalHierarchy) !== JSON.stringify(data.principal_hierarchy);
      state.principalHierarchy = data.principal_hierarchy || { projects: [], user_residents: [] };
      if (Object.prototype.hasOwnProperty.call(data, "recommendation_resident")) {
        state.recommendationResident = data.recommendation_resident || null;
        state.recommendationStatus = data.recommendation_status || "unavailable";
      }
      state.sidebarNavigationResidents = data.residents || [];
      const changed = applyResidentSidebarResidents(
        [...state.sidebarNavigationResidents, ...state.sidebarShortcutResidents],
        data.lineages || [],
      );
      if (hierarchyChanged && !changed) renderAgentList();
      return changed || hierarchyChanged;
    }

    function applyResidentSidebarShortcuts(data) {
      state.sidebarShortcutResidents = Array.isArray(data?.residents) ? data.residents : [];
      if (data?.account_selection
        && JSON.stringify(data.account_selection) !== JSON.stringify(state.accountSelection)) {
        state.accountSelection = data.account_selection;
        refreshOpenResidentRegistrationOptions();
      }
      const recommendationChanged = JSON.stringify(state.recommendationResident) !== JSON.stringify(data?.recommendation_resident || null)
        || state.recommendationStatus !== (data?.recommendation_status || "unavailable");
      state.recommendationResident = data?.recommendation_resident || null;
      state.recommendationStatus = data?.recommendation_status || "unavailable";
      applyResidentSidebarResidents(
        [...state.sidebarNavigationResidents, ...state.sidebarShortcutResidents],
        state.lineages,
      );
      if (recommendationChanged) {
        renderAgentList();
        currentAgentChatPanel()?.refreshView?.();
      }
    }

    function applyResidentSidebarSnapshot(payload) {
      if (state.residentSidebarMutationDepth > 0) {
        state.residentSidebarDeferredSnapshot = payload;
        return false;
      }
      return applyResidentSidebarSnapshotNow(payload);
    }

    async function waitForResidentSidebarProjection(initialPayload, loadSeq) {
      let payload = initialPayload;
      while (loadSeq === state.loadAllSeq) {
        applyResidentSidebarSnapshot(payload);
        if (state.residentSidebarReady) return true;
        const retryAfterMs = Math.max(
          250,
          Math.min(2000, Number(payload?.retry_after_ms) || 500),
        );
        await new Promise(resolve => window.setTimeout(resolve, retryAfterMs));
        if (loadSeq !== state.loadAllSeq) return false;
        payload = await fetchResidentSidebarSnapshot({ force: true });
      }
      return false;
    }

    function beginResidentSidebarMutation() {
      state.residentSidebarMutationDepth += 1;
    }

    function endResidentSidebarMutation(options = {}) {
      state.residentSidebarMutationDepth = Math.max(0, state.residentSidebarMutationDepth - 1);
      if (state.residentSidebarMutationDepth > 0) return false;
      const pending = state.residentSidebarDeferredSnapshot;
      state.residentSidebarDeferredSnapshot = null;
      if (!pending) return false;
      const preservedResidents = Array.isArray(options.preserveResidents)
        ? options.preserveResidents.filter(item => item?.resident_id)
        : [];
      if (!preservedResidents.length) return applyResidentSidebarSnapshotNow(pending);
      const data = pending && typeof pending === "object" ? pending : {};
      const pendingResidents = Array.isArray(data.residents) ? data.residents : [];
      const pendingIds = new Set(pendingResidents.map(item => String(item?.resident_id || "")));
      return applyResidentSidebarSnapshotNow({
        ...data,
        residents: [
          ...preservedResidents.filter(item => !pendingIds.has(String(item.resident_id || ""))),
          ...pendingResidents,
        ],
      });
    }

    function renderRegisteredResidentItem(item, options = {}) {
      const selected = residentSelectionId(item);
      const residentId = String(item.resident_id || "");
      const isPersonalAma = options.asResident === true || (options.asLineage !== true && isPersonalAmaResident(item));
      const favoriteSelectionId = String(options.favoriteSelectionId || lineageFavoriteSelectionId(item) || selected);
      const favorite = isFavoriteLineageSidebarItem(item);
      const lineageId = lineageIdForResident(item) || residentId;
      const menuId = isPersonalAma ? selected : `lineage:${lineageId}`;
      const listedCount = new Set(
        lineageSidebarResidentsForItem(item).map(value => String(value?.resident_id || "").trim()).filter(Boolean),
      ).size;
      const memberCount = Math.max(listedCount, Number(item._lineageSidebarResidentCount || 0));
      const memberLabel = window.PortalI18n.text`${memberCount} 个 Resident`;
      const selectedResident = selectedResidentId();
      const active = isPersonalAma
        ? selected && selected === activeChatTargetId()
        : (item?.lineage_node_ids || item?.lineage?.lineage_node_ids || []).includes(selectedResident);
      const showMenu = true;
      const menuOpen = showMenu && menuId === state.agentMenuId;
      const titleIcons = "";
      const title = esc(
        isPersonalAma
          ? (item.title || item.resident_id || window.PortalI18n.t("Ask Me Anything"))
          : (item.lineage?.title || item.lineage_title || item.title || lineageId)
      );
      const titleHtml = titleIcons
        ? window.PortalI18n.html`<span class="agentTitle withIcon"><span class="agentTitleText">${title}</span>${titleIcons}</span>`
        : window.PortalI18n.html`<span class="agentTitle">${title}</span>`;
      return window.PortalI18n.html`<div class="residentCardWrap" data-resident-card="${esc(residentId)}" data-lineage-card="${esc(lineageId)}">
        <div class="agentItem ${isPersonalAma ? "" : "principalLineageCard"} ${active ? "active" : ""} ${menuOpen ? "menuOpen" : ""}" data-resident-id="${esc(residentId)}">
          <button type="button" class="agentSelect noStatusDot" ${isPersonalAma ? `data-resident-select="${esc(residentId)}"` : `data-principal-open="${esc(lineageLeaderResidentId(item))}" data-lineage-open="${esc(lineageId)}"`} title="${esc(isPersonalAma ? (item.workspace_path || "") : lineageId)}">
            <span class="agentMain">
              ${titleHtml}
              <span class="agentMeta">${isPersonalAma ? `<span class="principalKind">Resident</span>` : window.PortalI18n.html`<span class="principalKind">Lineage</span><span class="principalMetaDetail" title="${esc(memberLabel)}" aria-label="${esc(memberLabel)}">${memberCount}</span>`}</span>
            </span>
          </button>
          ${isPersonalAma ? "" : window.PortalI18n.html`<button type="button" class="agentFavoriteBtn ${favorite ? "active" : ""}" data-agent-favorite="${esc(favoriteSelectionId)}" aria-pressed="${favorite ? "true" : "false"}" aria-label="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" title="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" data-portal-icon="${favorite ? "star-filled" : "star"}"></button>`}
          ${showMenu ? window.PortalI18n.html`<button type="button" class="agentMenuBtn ${menuOpen ? "active" : ""}" data-agent-menu="${esc(menuId)}" aria-label="${isPersonalAma ? "Resident" : "Lineage"} 菜单" title="更多" data-portal-icon="more-horizontal"></button>` : ""}
        </div>
        </div>`;
    }

    function derivedResidentForSidebar(residentId, derivedResidentId) {
      return state.residents.find(item => residentExactId(item) === String(derivedResidentId || "")) || null;
    }

    function toggleResidentGroup(groupId) {
      if (!Object.prototype.hasOwnProperty.call(state.residentGroups, groupId)) return;
      state.residentGroups[groupId] = state.residentGroups[groupId] === false;
      writeLineageUiState({ residentGroups: state.residentGroups });
      state.agentMenuId = "";
      renderAgentList();
    }

    function toggleResidentProjectGroup(groupId) {
      if (!groupId) return;
      state.residentProjectGroups[groupId] = state.residentProjectGroups[groupId] !== true;
      writeLineageUiState({ residentProjectGroups: state.residentProjectGroups });
      state.agentMenuId = "";
      renderAgentList();
    }

    function projectOptionById(projectId) {
      const wanted = String(projectId || "").trim();
      if (!wanted) return null;
      return (state.projects || []).find(project => project.id === wanted) || null;
    }

    function knownUsers() {
      const seen = new Set();
      const users = [];
      for (const user of state.users || []) {
        const id = String(user.id || "").trim();
        if (!id || seen.has(id)) continue;
        seen.add(id);
        users.push({ ...user, id });
      }
      if (state.me && !seen.has(state.me)) {
        users.unshift({ id: state.me, name: state.me, type: "human" });
      }
      return users;
    }

    async function refreshPersonalAssistantShortcut() {
      const request = ++personalAssistantRead;
      const target = await window.PortalNav.personalAssistantTarget().catch(() => null);
      if (request !== personalAssistantRead) return;
      state.personalAssistant = target;
      renderAgentList();
      renderFavoriteList();
    }

    function residentNavigationShortcutsHtml() {
      const assistant = state.personalAssistant;
      return window.PortalI18n.html`<div class="whichResidentWrap">${whichResidentCardHtml()}</div>${assistant ? `<div class="personalAssistantResident">${renderRegisteredResidentItem(assistant, { asResident: true })}</div>` : ""}`;
    }

    function renderAgentList() {
      renderProjectFilterOptions();
      syncAgentTools();
      const list = $id("agentList");
      const groups = residentGroups();
      list.innerHTML = window.PortalI18n.html`<div class="agentList">${residentNavigationShortcutsHtml()}${groups.map(renderResidentGroup).join("")}</div>`;
      renderActivityRecentRail();
      list.querySelectorAll("[data-resident-toggle]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          toggleResidentGroup(btn.dataset.residentToggle || "");
        });
      });
      list.querySelectorAll("[data-resident-project-toggle]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          toggleResidentProjectGroup(btn.dataset.residentProjectToggle || "");
        });
      });
      bindAgentCardControls(list);
      list.querySelector("[data-project-create]")?.addEventListener("click", async event => {
        event.stopPropagation();
        const result = await window.PortalProjectCreation.open();
        if (!result) return;
        state.query = "";
        state.projectDisplayFilter = "all";
        state.projectFilter = "all";
        state.residentGroups.lineage = true;
        $id("searchInput").value = "";
        writeLineageUiState({ query: "", projectFilter: "all", projectDisplayFilter: "all" });
        window.PortalUI.toast(window.PortalI18n.t("Project 已创建，可在项目卡片中添加 Lineage。"), "success");
        try {
          await refreshResidentSidebarIfDue(true);
        } catch (error) {
          window.PortalUI.toast(window.PortalI18n.text`Project 已创建，导航刷新失败：${error.message || error}`, "error");
        }
      });
      bindWhichResidentControls(list);
      refreshRecommendationChats();
      renderAgentFloatingMenu();
    }

    // A conversation selection changes only highlighting, not the contents
    // of Navigation. Keep the existing cards and their handlers mounted.
    function syncResidentNavigationSelection() {
      const residentId = selectedResidentId();
      const lineageId = activeLineageId();
      for (const rootId of ["agentList", "favoriteAgentList"]) {
        const root = $id(rootId);
        if (!root) continue;
        root.querySelectorAll(".residentCardWrap").forEach(card => {
          const item = card.querySelector(".agentItem");
          if (!item) return;
          const personal = Boolean(item.querySelector("[data-resident-select]"));
          item.classList.toggle("active", personal
            ? card.dataset.residentCard === residentId
            : Boolean(lineageId && card.dataset.lineageCard === lineageId));
        });
        root.querySelectorAll(".principalProjectCard").forEach(card => {
          card.classList.toggle("active",
            card.querySelector("[data-principal-open]")?.dataset.principalOpen === residentId);
        });
      }
      renderActivityRecentRail();
    }

    function favoriteTargets() {
      const targets = [];
      const representedSelections = new Set();
      const projects = window.PortalPrincipalNavigation.projectGroups(state.principalHierarchy, state.residents || []);
      for (const project of projects) {
        const selectionId = projectFavoriteSelectionId(project);
        if (!isFavoriteAgent(selectionId)) continue;
        representedSelections.add(selectionId);
        targets.push({ selectionId, target: { type: "project", item: { ...project, favoriteSelectionId: selectionId } } });
      }
      for (const item of lineageSidebarRoots("", { respectFilters: false })) {
        const selectionId = lineageFavoriteSelectionId(item);
        if (!selectionId || representedSelections.has(selectionId) || !isFavoriteLineageSidebarItem(item)) continue;
        representedSelections.add(selectionId);
        lineageSidebarResidentsForItem(item)
          .map(member => residentSelectionId(member))
          .filter(Boolean)
          .forEach(memberSelectionId => representedSelections.add(memberSelectionId));
        targets.push({
          selectionId,
          target: { type: "resident", item },
        });
      }
      for (const selectionId of state.favoriteAgentIds) {
        if (representedSelections.has(selectionId)) continue;
        const status = state.favoriteStatuses[selectionId] || { status: "unavailable" };
        const resident = residentByMenuId(selectionId);
        if (resident) targets.push({ selectionId, target: { type: "resident", item: resident } });
        else if (selectionId.startsWith("project:") && status.status === "denied") targets.push({ selectionId, target: { type: "project", item: {
          id: selectionId, project_id: selectionId.slice(8), title: selectionId.slice(8), items: [],
          accessible: false, unavailable: true, favoriteSelectionId: selectionId,
        } } });
        else if (selectionId.startsWith("lineage:") && status.status === "available" && status.leader_resident_id) {
          targets.push({ selectionId, target: { type: "resident", item: {
            resident_id: status.leader_resident_id, lineage_id: selectionId.slice(8),
            lineage: { title: status.title, leader_resident_id: status.leader_resident_id },
          } } });
        } else if (/^(project|lineage):/.test(selectionId)) {
          targets.push({ selectionId, target: { type: "unavailable", item: status } });
        }
      }
      return targets;
    }

    function favoriteTargetHtml(row) {
      const target = row?.target;
      if (!target) return "";
      if (target.type === "project") return projectCardHtml(target.item);
      if (target.type === "unavailable") {
        const kind = row.selectionId.startsWith("project:") ? "Project" : "Lineage";
        const status = target.item.status;
        const denied = status === "denied";
        const reason = { deleted: "已删除", missing: "资源不存在", denied: "无访问权限", available: "目录暂未载入", unavailable: "暂时无法确认状态" }[status] || "暂时无法确认状态";
        return window.PortalI18n.html`<div class="agentItem principal${kind}Card principalUnavailableCard ${denied ? "locked" : ""}" data-favorite-status="${esc(status)}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">${esc(target.item.title || window.PortalI18n.text`${reason}的 ${kind}`)}</span><span class="agentMeta">${kind} · ${reason}</span></span></div>${denied ? window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限"></span>') : ''}${favoriteStar(row.selectionId)}</div>`;
      }
      return renderRegisteredResidentItem(target.item, {
        asLineage: true,
        favoriteSelectionId: row.selectionId,
      });
    }

    function favoriteResidentGroups() {
      const rows = favoriteTargets();
      return [
        {
          id: "favorite_lineage",
          title: window.PortalI18n.t("Projects 与 Lineages"),
          rows,
          projectGroups: [],
          empty: "暂无收藏的 Project 或 Lineage。",
        },
      ];
    }

    function renderFavoriteProjectSubgroups(groups) {
      return window.PortalI18n.html`<div class="residentSubgroups">${(groups || []).map(group => {
          const open = state.favoriteProjectGroups[group.id] !== false;
          return window.PortalI18n.html`<section class="residentSubgroup" data-favorite-project-group="${esc(group.id)}">
            <div class="residentSubgroupHead">
              <button class="residentSubgroupToggle" type="button" data-favorite-project-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
              <button class="residentSubgroupName" type="button" data-favorite-project-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
              <span class="residentSubgroupCount">${group.rows.length}</span>
            </div>
            ${open ? window.PortalI18n.html`<div class="residentSubgroupBody">${group.rows.map(favoriteTargetHtml).join("")}</div>` : ""}
          </section>`;
        }).join("")}</div>`;
    }

    function renderFavoriteResidentGroup(group) {
      const open = state.favoriteGroups[group.id] !== false;
      const bodyHtml = group.projectGroups?.length
        ? renderFavoriteProjectSubgroups(group.projectGroups)
        : group.rows.length
          ? group.rows.map(favoriteTargetHtml).join("")
          : window.PortalI18n.html`<div class="residentEmpty">${esc(group.empty)}</div>`;
      return window.PortalI18n.html`<section class="residentSection ${open ? "open" : "collapsed"}" data-favorite-group="${esc(group.id)}">
          <div class="residentGroupHead">
            <button class="residentToggle" type="button" data-favorite-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
            <button class="residentGroupName" type="button" data-favorite-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
          </div>
          ${open ? window.PortalI18n.html`<div class="residentGroupBody">${bodyHtml}</div>` : ""}
        </section>`;
    }

    function toggleFavoriteGroup(groupId) {
      if (!Object.prototype.hasOwnProperty.call(state.favoriteGroups, groupId)) return;
      state.favoriteGroups[groupId] = state.favoriteGroups[groupId] === false;
      writeLineageUiState({ favoriteGroups: state.favoriteGroups });
      state.agentMenuId = "";
      renderFavoriteList();
    }

    function toggleFavoriteProjectGroup(groupId) {
      if (!groupId) return;
      state.favoriteProjectGroups[groupId] = state.favoriteProjectGroups[groupId] === false;
      writeLineageUiState({ favoriteProjectGroups: state.favoriteProjectGroups });
      state.agentMenuId = "";
      renderFavoriteList();
    }

    function renderFavoriteList() {
      const root = $id("favoriteAgentList");
      if (!root) return;
      const content = state.favoriteLoadError
        ? consoleStateHtml("收藏加载失败，请点击刷新重试。", { state: "error" })
        : favoriteResidentGroups().map(renderFavoriteResidentGroup).join("");
      root.innerHTML = window.PortalI18n.html`<div class="agentList favoriteAgentList">${residentNavigationShortcutsHtml()}${content}</div>`;
      renderActivityRecentRail();
      root.querySelectorAll("[data-favorite-toggle]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          toggleFavoriteGroup(btn.dataset.favoriteToggle || "");
        });
      });
      root.querySelectorAll("[data-favorite-project-toggle]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          toggleFavoriteProjectGroup(btn.dataset.favoriteProjectToggle || "");
        });
      });
      root.querySelectorAll("[data-resident-project-toggle]").forEach(button => {
        button.addEventListener("click", () => {
          toggleResidentProjectGroup(button.dataset.residentProjectToggle);
          renderFavoriteList();
        });
      });
      bindAgentCardControls(root);
      renderAgentFloatingMenu();
    }

    function residentByMenuId(menuId) {
      const residentId = String(menuId || "").startsWith("resident:") ? String(menuId || "").slice("resident:".length) : "";
      if (!residentId) return null;
      return state.residents.find(item => String(item.resident_id || "") === residentId)
        || (state.personalAssistant?.resident_id === residentId ? state.personalAssistant : null);
    }

    function lineageByMenuId(menuId) {
      const lineageId = String(menuId || "").startsWith("lineage:")
        ? String(menuId || "").slice("lineage:".length)
        : "";
      if (!lineageId) return null;
      return lineageSidebarRoots("", { respectFilters: false })
        .find(item => lineageIdForResident(item) === lineageId) || null;
    }

    function menuTargetById(menuId) {
      if (String(menuId || "").startsWith("project:")) {
        const projectId = String(menuId).slice("project:".length);
        const project = (state.principalHierarchy?.projects || []).find(item => item.project_id === projectId);
        return project ? { type: "project", item: project } : null;
      }
      const lineage = lineageByMenuId(menuId);
      if (lineage) return { type: "lineage", item: lineage };
      const resident = residentByMenuId(menuId);
      if (resident) return { type: "resident", item: resident };
      return null;
    }

    function isFavoriteAgent(selectionId) {
      return !!selectionId && state.favoriteAgentIds.has(selectionId);
    }

    function lineageFavoriteSelectionId(item) {
      const lineageId = lineageIdForResident(item);
      return lineageId ? `lineage:${lineageId}` : "";
    }

    function isFavoriteLineageSidebarItem(item) {
      const selectionId = lineageFavoriteSelectionId(item);
      if (isFavoriteAgent(selectionId)) return true;
      const lineageResidents = lineageSidebarResidentsForItem(item);
      const candidates = lineageResidents.length ? lineageResidents : (item ? [item] : []);
      return candidates.some(member => isFavoriteAgent(residentSelectionId(member)));
    }

    function favoriteLineageRootByResidentId(residentId) {
      const root = lineageSidebarRootByResidentId(residentId);
      return isFavoriteLineageSidebarItem(root) ? root : null;
    }

    function sidebarPanelForView(view) {
      return document.querySelector(`[data-side-panel="${CSS.escape(view || state.sideView || "agents")}"]`);
    }

    function scrollSidebarTargetIntoView(view, target) {
      window.requestAnimationFrame(() => {
        const panel = sidebarPanelForView(view);
        const el = sidebarElementForTarget(panel, target);
        if (el) el.scrollIntoView({ block: "center", inline: "nearest" });
      });
    }

    function revealResidentSidebarTarget(record) {
      const residentId = String(record?.resident_id || "").trim();
      if (!residentId) {
        renderAgentList();
        renderFavoriteList();
        return;
      }
      focusPreferredSidebarTarget({ residentId });
    }

    function ensureResidentSidebarTargetVisible(item, { preserveQuery = false } = {}) {
      if (!item) return;
      state.residentGroups.lineage = true;
      const meta = projectMetaForAgent(item);
      if (meta) state.residentProjectGroups[`lineage:${meta.id}`] = true;
      const patch = {
        residentGroups: state.residentGroups,
        residentProjectGroups: state.residentProjectGroups,
      };
      if (!preserveQuery && state.query.trim()) {
        state.query = "";
        const input = $id("searchInput");
        if (input) input.value = "";
        patch.query = state.query;
      }
      const itemProject = String(item.project_id || item.project || "");
      if (state.projectFilter !== "all" && state.projectFilter !== itemProject) {
        state.projectFilter = "all";
        patch.projectFilter = state.projectFilter;
      }
      writeLineageUiState(patch);
    }

    function ensureFavoriteSidebarTargetVisible(item) {
      if (!item) return;
      state.favoriteGroups.favorite_lineage = true;
      const meta = projectMetaForAgent(item);
      if (meta) state.favoriteProjectGroups[`favorite_lineage:${meta.id}`] = true;
      writeLineageUiState({
        favoriteGroups: state.favoriteGroups,
        favoriteProjectGroups: state.favoriteProjectGroups,
      });
    }

    function focusPreferredSidebarTarget(target = {}) {
      const residentId = String(target.residentId || "").trim();
      const selectionId = residentId ? residentSelectionId(residentId) : "";
      const menuTarget = selectionId ? menuTargetById(selectionId) : null;
      const lineageRoot = residentId ? lineageSidebarRootByResidentId(residentId) : null;
      const favoriteLineageRoot = residentId ? favoriteLineageRootByResidentId(residentId) : null;
      if (favoriteLineageRoot) {
        ensureFavoriteSidebarTargetVisible(favoriteLineageRoot);
        setSideView("favorites");
        renderFavoriteList();
        scrollSidebarTargetIntoView("favorites", { residentId });
        return;
      }
      if (menuTarget?.type === "resident") {
        ensureResidentSidebarTargetVisible(
          lineageRoot || menuTarget.item,
          { preserveQuery: Boolean(target.preserveQuery) },
        );
        setSideView("agents");
        renderAgentList();
        scrollSidebarTargetIntoView("agents", { residentId });
        return;
      }
      setSideView("agents");
      renderAgentList();
      scrollSidebarTargetIntoView("agents", { residentId });
    }

    return Object.freeze({
      residentSidebarRefreshCoordinator, fmtDateTime, updateProjectOptions,
      lineageLeaderResidentId, isLineageLeaderResident, lineageSidebarRoots,
      lineageSidebarRootByResidentId, projectFilterOptions, normalizedProjectDisplayFilter,
      syncAgentTools, setSearchOpen, setProjectFilter, setProjectDisplayFilter,
      residentSidebarPendingText, isPersonalAmaResident, lineageContainsPersonalAmaLeader,
      fetchResidentSidebarSnapshot, applyResidentSidebarShortcuts,
      waitForResidentSidebarProjection, beginResidentSidebarMutation, endResidentSidebarMutation,
      renderRegisteredResidentItem, derivedResidentForSidebar, lineageSidebarResidentsForItem,
      knownUsers, refreshPersonalAssistantShortcut, renderAgentList,
      syncResidentNavigationSelection, renderFavoriteList, menuTargetById, isFavoriteAgent,
      revealResidentSidebarTarget, focusPreferredSidebarTarget,
    });
  }

  window.PortalLineageWorkbenchSidebar = Object.freeze({
    createSidebar, dependencies: DEPENDENCIES, fmtDateTime, lineageLeaderResidentId,
    isLineageLeaderResident, normalizedProjectDisplayFilter, lineageSidebarResidentsForItem,
  });
})();
