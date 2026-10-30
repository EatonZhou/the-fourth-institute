(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarReveal) return;

  // Revealing a Resident in the side views: opening its groups, clearing
  // filters that hide it, switching to the view that lists it and
  // scrolling to its card.

  const DEPENDENCIES = Object.freeze([
    "$id", "favoriteLineageRootByResidentId", "lineageSidebarRootByResidentId", "menuTargetById",
    "projectMetaForAgent", "renderAgentList", "renderFavoriteList", "residentSelectionId", "setSideView",
    "sidebarElementForTarget", "state", "writeLineageUiState",
  ]);

  function createSidebarReveal(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarReveal is missing: ${missing.join(", ")}`);
    const {
      $id, favoriteLineageRootByResidentId, lineageSidebarRootByResidentId, menuTargetById, projectMetaForAgent,
      renderAgentList, renderFavoriteList, residentSelectionId, setSideView, sidebarElementForTarget, state,
      writeLineageUiState,
    } = dependencies;

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

    return {
      focusPreferredSidebarTarget, revealResidentSidebarTarget,
    };
  }

  window.PortalLineageWorkbenchSidebarReveal = Object.freeze({ createSidebarReveal, dependencies: DEPENDENCIES });
})();
