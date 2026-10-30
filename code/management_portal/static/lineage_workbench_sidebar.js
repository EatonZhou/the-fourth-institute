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

  // createSidebar builds the Agent and favourites side views. Its parts live in
  // module scripts that load before this one; sidebarPart(name) returns what a
  // script registers:
  //   lineage_workbench_sidebar_targets.js  the Lineages, Residents, Projects
  //     and favourites the views list, and a card menu's target
  //   lineage_workbench_sidebar_filters.js  searching and filtering the Agent
  //     list
  //   lineage_workbench_sidebar_snapshot.js  the Residents list snapshot and
  //     its refresh
  //   lineage_workbench_sidebar_agent_list.js  the Agent list and the personal
  //     assistant entry
  //   lineage_workbench_sidebar_favorites.js  the favourites list
  //   lineage_workbench_sidebar_reveal.js  revealing a Resident in the views
  // createSidebar creates the parts in that order and returns the views'
  // functions; a part that calls a function of a part created after it gets
  // an arrow forwarding the call.
  function sidebarPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
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

    const {
      derivedResidentForSidebar, favoriteLineageRootByResidentId, isFavoriteAgent, isFavoriteLineageSidebarItem,
      isPersonalAmaResident, knownUsers, lineageContainsPersonalAmaLeader, lineageFavoriteSelectionId,
      lineageSidebarRootByResidentId, lineageSidebarRoots, menuTargetById, projectMetaForAgent, projectOptionById,
      residentByMenuId,
    } = sidebarPart("PortalLineageWorkbenchSidebarTargets").createSidebarTargets({
      isLineageLeaderResident, lineageIdForResident, lineageSidebarResidentsForItem, recentTimeMs, residentExactId,
      residentSearchText, residentSelectionId, state,
    });
    const {
      projectFilterOptions, renderProjectFilterOptions, setProjectDisplayFilter, setProjectFilter, setSearchOpen,
      syncAgentTools, updateProjectOptions,
    } = sidebarPart("PortalLineageWorkbenchSidebarFilters").createSidebarFilters({
      $id, currentAgentFilterDropdown, currentAgentFilterSummary, esc, lineageIdForResident, normalizeProjectOptions,
      normalizedProjectDisplayFilter, projectOptionById, refreshOpenResidentRegistrationOptions,
      renderActivityRecentRail, renderAgentList: (...args) => renderAgentList(...args),
      renderFavoriteList: (...args) => renderFavoriteList(...args), state, writeLineageUiState,
    });
    const {
      applyResidentSidebarShortcuts, beginResidentSidebarMutation, endResidentSidebarMutation,
      fetchResidentSidebarSnapshot, residentSidebarRefreshCoordinator, waitForResidentSidebarProjection,
    } = sidebarPart("PortalLineageWorkbenchSidebarSnapshot").createSidebarSnapshot({
      currentAgentChatPanel, refreshOpenResidentRegistrationOptions, renderActivityRecentRail,
      renderAgentList: (...args) => renderAgentList(...args),
      renderFavoriteList: (...args) => renderFavoriteList(...args), residentService, state, subscribeResidentSidebar,
    });
    const {
      favoriteStar, projectCardHtml, refreshPersonalAssistantShortcut, renderAgentList, renderRegisteredResidentItem,
      residentNavigationShortcutsHtml, residentSidebarPendingText, syncResidentNavigationSelection,
      toggleResidentProjectGroup,
    } = sidebarPart("PortalLineageWorkbenchSidebarAgentList").createSidebarAgentList({
      $id, activeChatTargetId, activeLineageId, bindAgentCardControls, bindWhichResidentControls, esc,
      isFavoriteAgent, isFavoriteLineageSidebarItem, isPersonalAmaResident, lineageFavoriteSelectionId,
      lineageIdForResident, lineageLeaderResidentId, lineageSidebarResidentsForItem, lineageSidebarRoots,
      projectFavoriteSelectionId, refreshRecommendationChats, refreshResidentSidebarIfDue, renderActivityRecentRail,
      renderAgentFloatingMenu, renderFavoriteList: (...args) => renderFavoriteList(...args),
      renderProjectFilterOptions, residentSelectionId, selectedResidentId, state, syncAgentTools,
      whichResidentCardHtml, writeLineageUiState,
    });
    const { renderFavoriteList } = sidebarPart("PortalLineageWorkbenchSidebarFavorites").createSidebarFavorites({
      $id, bindAgentCardControls, consoleStateHtml, esc, favoriteStar, isFavoriteAgent, isFavoriteLineageSidebarItem,
      lineageFavoriteSelectionId, lineageSidebarResidentsForItem, lineageSidebarRoots, projectCardHtml,
      projectFavoriteSelectionId, renderActivityRecentRail, renderAgentFloatingMenu, renderRegisteredResidentItem,
      residentByMenuId, residentNavigationShortcutsHtml, residentSelectionId, state, toggleResidentProjectGroup,
      writeLineageUiState,
    });
    const {
      focusPreferredSidebarTarget, revealResidentSidebarTarget,
    } = sidebarPart("PortalLineageWorkbenchSidebarReveal").createSidebarReveal({
      $id, favoriteLineageRootByResidentId, lineageSidebarRootByResidentId, menuTargetById, projectMetaForAgent,
      renderAgentList, renderFavoriteList, residentSelectionId, setSideView, sidebarElementForTarget, state,
      writeLineageUiState,
    });

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
