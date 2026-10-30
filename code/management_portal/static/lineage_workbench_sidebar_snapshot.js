(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSidebarSnapshot) return;

  // The Residents list snapshot: reading and refreshing it, applying it and
  // the shortcut Residents to the workbench state, waiting for the first
  // ready snapshot, and holding snapshots back while a change is under way.

  const DEPENDENCIES = Object.freeze([
    "currentAgentChatPanel", "refreshOpenResidentRegistrationOptions", "renderActivityRecentRail", "renderAgentList",
    "renderFavoriteList", "residentService", "state", "subscribeResidentSidebar",
  ]);

  function createSidebarSnapshot(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSidebarSnapshot is missing: ${missing.join(", ")}`);
    const {
      currentAgentChatPanel, refreshOpenResidentRegistrationOptions, renderActivityRecentRail, renderAgentList,
      renderFavoriteList, residentService, state, subscribeResidentSidebar,
    } = dependencies;

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

    return {
      applyResidentSidebarShortcuts, beginResidentSidebarMutation, endResidentSidebarMutation,
      fetchResidentSidebarSnapshot, residentSidebarRefreshCoordinator, waitForResidentSidebarProjection,
    };
  }

  window.PortalLineageWorkbenchSidebarSnapshot = Object.freeze({ createSidebarSnapshot, dependencies: DEPENDENCIES });
})();
