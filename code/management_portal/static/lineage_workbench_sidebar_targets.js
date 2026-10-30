(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarTargets) return;

  // What the side views list: the Lineages with their Residents in recent
  // order, the Lineage of a Resident, Projects, known users, the personal
  // AMA, favourites, and the Project, Lineage or Resident a card menu is
  // for.

  const DEPENDENCIES = Object.freeze([
    "isLineageLeaderResident", "lineageIdForResident", "lineageSidebarResidentsForItem", "recentTimeMs",
    "residentExactId", "residentSearchText", "residentSelectionId", "state",
  ]);

  function createSidebarTargets(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarTargets is missing: ${missing.join(", ")}`);
    const {
      isLineageLeaderResident, lineageIdForResident, lineageSidebarResidentsForItem, recentTimeMs, residentExactId,
      residentSearchText, residentSelectionId, state,
    } = dependencies;

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

    function isPersonalAmaResident(item) {
      return !!state.personalAmaResidentId
        && String(item?.resident_id || "").trim() === state.personalAmaResidentId;
    }

    function lineageContainsPersonalAmaLeader(item) {
      return lineageSidebarResidentsForItem(item).some(member => isPersonalAmaResident(member));
    }

    function derivedResidentForSidebar(residentId, derivedResidentId) {
      return state.residents.find(item => residentExactId(item) === String(derivedResidentId || "")) || null;
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

    return {
      derivedResidentForSidebar, favoriteLineageRootByResidentId, isFavoriteAgent, isFavoriteLineageSidebarItem,
      isPersonalAmaResident, knownUsers, lineageContainsPersonalAmaLeader, lineageFavoriteSelectionId,
      lineageSidebarRootByResidentId, lineageSidebarRoots, menuTargetById, projectMetaForAgent, projectOptionById,
      residentByMenuId,
    };
  }

  window.PortalLineageWorkbenchSidebarTargets = Object.freeze({ createSidebarTargets, dependencies: DEPENDENCIES });
})();
