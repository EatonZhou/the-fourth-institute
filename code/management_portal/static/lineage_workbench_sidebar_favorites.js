(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarFavorites) return;

  // The favourites list: the favourite Projects and Lineages, also those
  // no longer reachable, in groups that open and close.

  const DEPENDENCIES = Object.freeze([
    "$id", "bindAgentCardControls", "consoleStateHtml", "esc", "favoriteStar", "isFavoriteAgent",
    "isFavoriteLineageSidebarItem", "lineageFavoriteSelectionId", "lineageSidebarResidentsForItem",
    "lineageSidebarRoots", "projectCardHtml", "projectFavoriteSelectionId", "renderActivityRecentRail",
    "renderAgentFloatingMenu", "renderRegisteredResidentItem", "residentByMenuId", "residentNavigationShortcutsHtml",
    "residentSelectionId", "state", "toggleResidentProjectGroup", "writeLineageUiState",
  ]);

  function createSidebarFavorites(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarFavorites is missing: ${missing.join(", ")}`);
    const {
      $id, bindAgentCardControls, consoleStateHtml, esc, favoriteStar, isFavoriteAgent, isFavoriteLineageSidebarItem,
      lineageFavoriteSelectionId, lineageSidebarResidentsForItem, lineageSidebarRoots, projectCardHtml,
      projectFavoriteSelectionId, renderActivityRecentRail, renderAgentFloatingMenu, renderRegisteredResidentItem,
      residentByMenuId, residentNavigationShortcutsHtml, residentSelectionId, state, toggleResidentProjectGroup,
      writeLineageUiState,
    } = dependencies;

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

    return {
      renderFavoriteList,
    };
  }

  window.PortalLineageWorkbenchSidebarFavorites = Object.freeze({ createSidebarFavorites, dependencies: DEPENDENCIES });
})();
