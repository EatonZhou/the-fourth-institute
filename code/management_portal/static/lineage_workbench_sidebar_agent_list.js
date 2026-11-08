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
        empty: residentSidebarPendingText("Project") || (state.query.trim()
          ? window.PortalI18n.text`没有与「${state.query.trim()}」匹配的项目。`
          : state.projectFilter !== "all" || (state.projectDisplayFilter === "non-empty" && state.principalHierarchy?.projects?.length)
            ? window.PortalI18n.t("当前筛选条件下没有项目。") : window.PortalI18n.t("暂无可访问的 Project。")) }];
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
      const hasProjectGroups = state.residentSidebarReady && Array.isArray(group.projectGroups) && group.projectGroups.length;
      const bodyHtml = hasProjectGroups
        ? renderProjectSubgroups(group)
        : group.items.length
          ? group.items.map(renderRegisteredResidentItem).join("")
          : window.PortalI18n.html`<div class="residentEmpty" ${state.residentSidebarError ? 'role="alert"' : 'role="status"'}>${esc(group.empty)}
              ${state.residentSidebarReady ? (queryActive || state.projectFilter !== "all" || state.projectDisplayFilter !== "all"
                ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-sidebar-show-all>${queryActive ? window.PortalI18n.t("清除搜索，显示全部项目") : window.PortalI18n.t("显示全部项目")}</button>` : "")
                : state.residentSidebarError ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-sidebar-retry>重新读取项目</button>` : ""}
            </div>`;
      return window.PortalI18n.html`<section class="residentSection ${open ? "open" : "collapsed"}" data-resident-group="${esc(group.id)}">
          <div class="residentGroupHead">
            <button class="residentToggle" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
            <button class="residentGroupName" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
            ${group.id === "lineage" ? window.PortalI18n.html`<button class="residentAddBtn" type="button" data-project-create aria-label="创建 Project" title="创建 Project" data-portal-icon="plus"></button>` : ""}
          </div>
          ${queryActive ? window.PortalI18n.html`<div class="sidebarSearchSummary" role="status"><span>${window.PortalI18n.text`正在筛选：${esc(state.query.trim())}`}</span><span>${state.residentSidebarReady ? window.PortalI18n.text`匹配 ${group.projectGroups.length} 个项目` : window.PortalI18n.t(state.residentSidebarError ? "项目读取失败" : "正在读取项目…")}</span><button class="portalButton is-icon is-compact" type="button" data-sidebar-search-clear aria-label="清除搜索" title="清除搜索">×</button></div>` : ""}
          ${open ? window.PortalI18n.html`<div class="residentGroupBody">
            ${bodyHtml}
          </div>` : ""}
        </section>`;
    }

    // Project and Lineage rows use the shared navigation tree rows
    // (portalNavRow, portal_workbench.css); their star and menu are icon buttons.
    function favoriteStar(selectionId, favorite = isFavoriteAgent(selectionId)) {
      const label = favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏");
      return `<button type="button" class="portalButton is-icon is-quiet" data-nav-favorite data-agent-favorite="${esc(selectionId)}" aria-pressed="${favorite ? "true" : "false"}" aria-label="${esc(label)}" title="${esc(label)}" data-portal-icon="${favorite ? "star-filled" : "star"}"></button>`;
    }

    function rowMenuButton(menuId, label, disabled = false) {
      return `<button type="button" class="portalButton is-icon is-quiet" data-agent-menu="${esc(menuId)}" aria-haspopup="menu" aria-expanded="${menuId === state.agentMenuId ? "true" : "false"}" aria-label="${esc(label)}" title="${esc(window.PortalI18n.t("更多"))}" data-portal-icon="more-horizontal" ${disabled ? "disabled" : ""}></button>`;
    }

    function projectCardHtml(project) {
      const open = !!state.query.trim() || state.residentProjectGroups[project.id] === true;
      const accessible = project.accessible !== false;
      const denied = !accessible && !project.can_initialize;
      const menuId = `project:${project.project_id}`;
      const count = project.items.length;
      const active = Boolean(project.resident_id) && state.chatTarget?.id === project.resident_id;
      const countHtml = project.unavailable
        ? `<span class="portalNavRowCount">${esc(window.PortalI18n.t("无访问权限"))}</span>`
        : `<span class="portalNavRowCount ${count ? "" : "is-empty"}" title="${esc(window.PortalI18n.text`共 ${count} 个 Lineage`)}" aria-label="${esc(window.PortalI18n.text`共 ${count} 个 Lineage`)}">${count}</span>`;
      const children = open && !project.unavailable
        ? `<div class="portalNavTreeChildren">${project.items.map(item => renderRegisteredResidentItem(item, { asLineage: true })).join("") || window.PortalI18n.t('<div class="residentEmpty">暂无可访问的 Lineage。</div>')}</div>`
        : "";
      return `<section class="portalNavTreeGroup principalProject" data-resident-project-group="${esc(project.id)}">
          <div class="portalNavRow is-group principalProjectCard ${denied ? "is-locked" : ""} ${active ? "is-active" : ""} ${menuId === state.agentMenuId ? "is-menu-open" : ""}" data-project-resident="${esc(project.resident_id || "")}">
            <button type="button" class="portalNavRowMain" data-resident-project-toggle="${esc(project.id)}" aria-expanded="${open}" ${project.unavailable ? "disabled" : ""}>
              <span class="portalNavRowChevron" data-portal-icon="${open ? "chevron-down" : "chevron-right"}"></span>
              <span class="portalNavRowMark" style="--portal-nav-mark-hue:${window.PortalUIPrimitives.stableHue(project.project_id)}" aria-hidden="true">${esc(window.PortalUIPrimitives.titleGlyph(project.title))}</span>
              <span class="portalNavRowLabel" title="${esc(project.title)}">${esc(project.title)}</span>
              ${countHtml}
            </button>
            ${denied ? window.PortalI18n.t('<span class="portalNavRowLock principalAccessLock" data-portal-icon="lock" aria-label="无访问权限" title="当前无权打开 Project 对话"></span>') : ""}
            <span class="portalNavRowActions">
              ${favoriteStar(project.favoriteSelectionId || projectFavoriteSelectionId(project))}
              ${rowMenuButton(menuId, window.PortalI18n.text`${project.title} 菜单`, project.unavailable || project.project_readable === false)}
            </span>
          </div>
          ${children}
        </section>`;
    }

    function renderProjectSubgroups(group) {
      return `<div class="portalNavTree">${(group.projectGroups || []).map(projectCardHtml).join("")}</div>`;
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
      const menuOpen = menuId === state.agentMenuId;
      if (!isPersonalAma) {
        const title = item.lineage?.title || item.lineage_title || item.title || lineageId;
        return `<div class="residentCardWrap" data-resident-card="${esc(residentId)}" data-lineage-card="${esc(lineageId)}">
          <div class="portalNavRow is-leaf principalLineageCard ${active ? "is-active" : ""} ${menuOpen ? "is-menu-open" : ""}" data-resident-id="${esc(residentId)}">
            <button type="button" class="portalNavRowMain" data-principal-open="${esc(lineageLeaderResidentId(item))}" data-lineage-open="${esc(lineageId)}" title="${esc(title)}">
              <span class="portalNavRowLabel">${esc(title)}</span>
              <span class="portalNavRowCount ${memberCount ? "" : "is-empty"}" title="${esc(memberLabel)}" aria-label="${esc(memberLabel)}">${memberCount}</span>
            </button>
            <span class="portalNavRowActions" data-reveal="hover">
              ${favoriteStar(favoriteSelectionId, favorite)}
              ${rowMenuButton(menuId, window.PortalI18n.t("Lineage 菜单"))}
            </span>
          </div>
        </div>`;
      }
      const title = esc(item.title || item.resident_id || window.PortalI18n.t("Ask Me Anything"));
      return window.PortalI18n.html`<div class="residentCardWrap" data-resident-card="${esc(residentId)}" data-lineage-card="${esc(lineageId)}">
        <div class="agentItem ${active ? "active" : ""} ${menuOpen ? "menuOpen" : ""}" data-resident-id="${esc(residentId)}">
          <button type="button" class="agentSelect noStatusDot" data-resident-select="${esc(residentId)}" title="${esc(item.workspace_path || "")}">
            <span class="agentMain">
              <span class="agentTitle">${title}</span>
              <span class="agentMeta"><span class="principalKind">Resident</span></span>
            </span>
          </button>
          <button type="button" class="agentMenuBtn ${menuOpen ? "active" : ""}" data-agent-menu="${esc(menuId)}" aria-label="Resident 菜单" title="更多" data-portal-icon="more-horizontal"></button>
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
      list.querySelectorAll("[data-sidebar-search-clear], [data-sidebar-show-all]").forEach(button => {
        button.addEventListener("click", () => {
          if (button.hasAttribute("data-sidebar-show-all")) {
            state.projectFilter = "all";
            state.projectDisplayFilter = "all";
            writeLineageUiState({ projectFilter: "all", projectDisplayFilter: "all" });
          }
          const input = $id("searchInput");
          input.value = "";
          input.dispatchEvent(new Event("input", { bubbles: true }));
        });
      });
      list.querySelector("[data-sidebar-retry]")?.addEventListener("click", () => {
        refreshResidentSidebarIfDue(true).catch(error => window.PortalUI.toast(error.message || String(error), "error"));
      });
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
        writeLineageUiState({ projectFilter: "all", projectDisplayFilter: "all" });
        window.PortalUI.toast(window.PortalI18n.t("Project 已创建，可在它的「⋯」菜单中添加 Lineage。"), "success");
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
          const lineageRow = card.querySelector(".principalLineageCard");
          if (lineageRow) {
            lineageRow.classList.toggle("is-active", Boolean(lineageId && card.dataset.lineageCard === lineageId));
            return;
          }
          card.querySelector(".agentItem")?.classList.toggle("active", card.dataset.residentCard === residentId);
        });
        root.querySelectorAll(".principalProjectCard").forEach(row => {
          row.classList.toggle("is-active", Boolean(row.dataset.projectResident) && row.dataset.projectResident === residentId);
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
