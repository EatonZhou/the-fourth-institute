(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarAgentList) return;

  // The Agent list: Which Resident and the personal assistant above the
  // Projects with their Lineages, cards, groups that open and close, and
  // highlighting the selected conversation.

  const DEPENDENCIES = Object.freeze([
    "$id", "activeChatTargetId", "activeLineageId", "bindAgentCardControls", "bindWhichResidentControls", "esc",
    "isFavoriteAgent", "isFavoriteLineageSidebarItem", "isPersonalAmaResident", "lineageFavoriteSelectionId",
    "lineageIdForResident", "lineageLeaderResidentId", "lineageSidebarResidentsForItem", "lineageSidebarRoots",
    "projectFavoriteSelectionId", "refreshRecommendationChats", "refreshResidentSidebarIfDue",
    "renderActivityRecentRail", "renderAgentFloatingMenu", "renderFavoriteList", "renderProjectFilterOptions",
    "residentSelectionId", "selectedResidentId", "state", "syncAgentTools", "whichResidentCardHtml",
    "writeLineageUiState",
  ]);

  function createSidebarAgentList(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarAgentList is missing: ${missing.join(", ")}`);
    const {
      $id, activeChatTargetId, activeLineageId, bindAgentCardControls, bindWhichResidentControls, esc,
      isFavoriteAgent, isFavoriteLineageSidebarItem, isPersonalAmaResident, lineageFavoriteSelectionId,
      lineageIdForResident, lineageLeaderResidentId, lineageSidebarResidentsForItem, lineageSidebarRoots,
      projectFavoriteSelectionId, refreshRecommendationChats, refreshResidentSidebarIfDue, renderActivityRecentRail,
      renderAgentFloatingMenu, renderFavoriteList, renderProjectFilterOptions, residentSelectionId,
      selectedResidentId, state, syncAgentTools, whichResidentCardHtml, writeLineageUiState,
    } = dependencies;

    let personalAssistantRead = 0;

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

    return {
      favoriteStar, projectCardHtml, refreshPersonalAssistantShortcut, renderAgentList, renderRegisteredResidentItem,
      residentNavigationShortcutsHtml, residentSidebarPendingText, syncResidentNavigationSelection,
      toggleResidentProjectGroup,
    };
  }

  window.PortalLineageWorkbenchSidebarAgentList = Object.freeze({ createSidebarAgentList, dependencies: DEPENDENCIES });
})();
