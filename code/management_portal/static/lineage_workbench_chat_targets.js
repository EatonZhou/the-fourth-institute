(() => {
  "use strict";

  if (window.PortalLineageWorkbenchChatTargets) return;

  // The chat target: which Resident (or Chatroom) the chat panel shows, how a
  // navigation request resolves to one, the page's last selection, and going
  // back to the previous chat workspace.
  const DEPENDENCIES = Object.freeze([
    "currentChatSelectionContextController", "currentPersonalWorkspaces", "lineageLeaderResidentId",
    "lineageSidebarResidentsForItem", "lineageSidebarRootByResidentId", "lineageSidebarRoots",
    "readLineageUiState", "residentDiscovery", "selectResident", "state", "writeLineageUiState",
  ]);

  function createChatTargets(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchChatTargets is missing: ${missing.join(", ")}`);
    const {
      currentChatSelectionContextController, currentPersonalWorkspaces, lineageLeaderResidentId,
      lineageSidebarResidentsForItem, lineageSidebarRootByResidentId, lineageSidebarRoots,
      readLineageUiState, residentDiscovery, selectResident, state, writeLineageUiState,
    } = dependencies;

    function residentSelectionId(value) {
      const residentId = typeof value === "string" ? value : value?.resident_id;
      return residentId ? `resident:${residentId}` : "";
    }

    let chatTargetEpochSequence = 0;
    const chatTargetEpochs = new WeakMap();

    function chatTargetEpoch(selection) {
      return selection && typeof selection === "object"
        ? Number(chatTargetEpochs.get(selection) || 0)
        : 0;
    }

    function createChatTarget(targetKind, targetId, lineageId = "", epoch = 0, presentation = {}) {
      const kind = String(targetKind || "").trim();
      const id = String(targetId || "").trim();
      if (!id || kind !== "resident") return null;
      const chatTarget = {
        key: `${kind}:${id}`,
        kind,
        id,
        lineage_id: String(lineageId || "").trim(),
        title: String(presentation?.title || presentation?.name || "").trim(),
        backend: String(presentation?.backend || "").trim(),
      };
      chatTargetEpochs.set(
        chatTarget,
        Number(epoch) || ++chatTargetEpochSequence,
      );
      return Object.freeze(chatTarget);
    }

    function activeChatTargetId() {
      return String(state.chatTarget?.key || "");
    }

    function activeLineageId() {
      return String(state.chatTarget?.lineage_id || "");
    }

    function commitChatTarget(chatTarget) {
      if (!chatTarget) {
        state.chatTarget = null;
        currentPersonalWorkspaces()?.controller.setResident();
        return null;
      }
      const canonical = createChatTarget(
        chatTarget.kind,
        chatTarget.id,
        chatTarget.lineage_id,
        chatTargetEpoch(chatTarget),
        chatTarget,
      );
      if (!canonical || canonical.key !== String(chatTarget.key || "")) {
        throw new Error("ChatTarget 合同无效");
      }
      if (residentIdForSelection(canonical) !== selectedResidentId()) {
        currentPersonalWorkspaces()?.controller.setResident({ id: residentIdForSelection(canonical) });
      }
      state.chatTarget = canonical;
      state.chatNavigationError = "";
      return canonical;
    }

    function selectedResidentId() {
      const selectionId = activeChatTargetId();
      return selectionId.startsWith("resident:") ? selectionId.slice("resident:".length) : "";
    }

    function residentExactId(value) {
      return String(value?.resident_id || "").trim();
    }

    function residentRootForExactId(residentId) {
      const exactId = String(residentId || "").trim();
      if (!exactId) return null;
      return state.residents.find(item => (
        String(item?.resident_id || "") === exactId
      )) || null;
    }

    async function resolveResidentNavigationTarget(chatTarget) {
      if (String(chatTarget?.lineage_id || "").trim()) return chatTarget;
      try {
        const target = await residentDiscovery.resolveNavigationTarget(chatTarget?.id);
        return residentChatTarget(target?.resident_id, target?.lineage_id, chatTarget);
      } catch (error) {
        const status = Number(error?.status || 0);
        if (status === 404 || status === 403) {
          window.PortalConversationStore?.discardResident?.(chatTarget?.id);
          window.PortalUI.toast(
            status === 404
              ? window.PortalI18n.t("该 Resident 已删除或不存在。")
              : window.PortalI18n.t("没有查看这个 Resident 的权限。"),
            "warning",
          );
          return null;
        }
        throw error;
      }
    }

    function firstAvailableResidentChatTarget({
      excludedLineageIds = new Set(),
      excludedResidentIds = new Set(),
    } = {}) {
      for (const root of lineageSidebarRoots("", { respectFilters: false })) {
        const lineageId = registeredLineageIdForResident(root);
        if (excludedLineageIds.has(lineageId)) continue;
        const resident = lineageSidebarResidentsForItem(root).find(candidate => {
          const residentId = residentExactId(candidate);
          return residentId && !excludedResidentIds.has(residentId);
        });
        if (resident) {
          return residentChatTarget(residentExactId(resident), lineageId, resident);
        }
      }
      return null;
    }

    function currentResident() {
      const residentId = selectedResidentId();
      if (!residentId) return null;
      return residentRootForExactId(residentId)
        || (String(state.detail?.resident_id || state.detail?.resident?.resident_id || "") === residentId ? state.detail.resident : null)
        // Chat targets an exact Resident identity. Navigation is only a
        // display projection and must not decide whether that target exists.
        || {
          resident_id: residentId,
          lineage_id: activeLineageId(),
          title: String(state.chatTarget?.title || "").trim(),
          backend: String(state.chatTarget?.backend || "").trim(),
        };
    }

    function residentChatTarget(residentId, lineageId = "", presentation = {}) {
      const targetId = String(residentId || "").trim();
      const resident = lineageSidebarRootByResidentId(targetId);
      return createChatTarget(
        "resident",
        targetId,
        String(lineageId || registeredLineageIdForResident(resident)).trim(),
        0,
        presentation,
      );
    }

    function residentChatTargetForLineage(lineageId) {
      const targetLineageId = String(lineageId || "").trim();
      if (!targetLineageId) return null;
      const lineage = lineageSidebarRoots("", { respectFilters: false })
        .find(item => registeredLineageIdForResident(item) === targetLineageId);
      if (!lineage) return null;
      const residentId = lineageLeaderResidentId(lineage)
        || residentExactId(lineageSidebarResidentsForItem(lineage)[0]);
      return residentChatTarget(residentId, targetLineageId, lineage);
    }

    const unavailableResidentBindings = new Set();

    function readLastSelection() {
      const payload = readLineageUiState().lastChatTarget;
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
      const selection = createChatTarget(
        payload.kind,
        payload.id,
        payload.lineage_id,
        0,
        payload,
      );
      return selection?.key === String(payload.key || "")
        && !unavailableResidentBindings.has(selection.id) ? selection : null;
    }

    function writeLastSelection(selection = state.chatTarget) {
      if (!selection) return;
      if (isResidentChatSelection(selection) && unavailableResidentBindings.has(selection.id)) return;
      writeLineageUiState({
        lastChatTarget: selection,
      });
    }

    function chatWorkspaceSnapshotKey(snapshot) {
      if (!snapshot || typeof snapshot !== "object") return "";
      return String(snapshot.key || "");
    }

    function currentChatTarget() {
      return state.chatTarget;
    }

    function chatTargetForPanel(panel, target = panel?.target?.() || {}) {
      const residentId = String(target.resident_id || "").trim();
      if (!residentId) return currentChatTarget();
      if (currentChatTarget()?.id === residentId) return currentChatTarget();
      return createChatTarget("resident", residentId, target.lineage_id, 0, target);
    }

    function residentIdForSelection(selection) {
      if (!selection || selection.kind !== "resident") return "";
      return String(selection.id || "").trim();
    }

    function runChatTargetEffect(selection, effect) {
      if (selection !== state.chatTarget) return undefined;
      return effect();
    }

    function beginChatWorkspaceNavigation() {
      currentChatSelectionContextController()?.clear?.();
      return state.chatTarget;
    }

    function rememberChatWorkspaceBeforeSwitch(nextSnapshot) {
      const current = currentChatTarget();
      if (!current || !nextSnapshot) return false;
      if (chatWorkspaceSnapshotKey(current) === chatWorkspaceSnapshotKey(nextSnapshot)) return false;
      state.previousChatWorkspace = current;
      // The tab's interface record names the Resident being left, so the
      // shortcut finds it after a reload or from another page.
      window.PortalState.interfaceHistory?.keepCurrent();
      return true;
    }

    // The Resident of a Society page address this tab left, when the page's
    // own memory of it went with a reload or a visit to another page.
    function previousChatWorkspaceFromInterfaceHistory() {
      const url = String(window.PortalState.interfaceHistory?.previousUrl?.() || "");
      if (!url) return null;
      let target;
      try {
        target = new URL(url, window.location.origin);
      } catch {
        return null;
      }
      if (!/^\/lineage(?:\.html)?$/.test(target.pathname)) return null;
      return createChatTarget("resident", target.searchParams.get("resident"), target.searchParams.get("lineage") || "");
    }

    function isResidentChatSelection(snapshot) {
      const selectionId = String(snapshot?.key || "").trim();
      if (!selectionId) return false;
      return snapshot?.kind === "resident";
    }

    async function goPreviousChatWorkspace(previous = state.previousChatWorkspace) {
      if (!isResidentChatSelection(previous)) return false;
      const current = currentChatTarget();
      if (current && chatWorkspaceSnapshotKey(current) === chatWorkspaceSnapshotKey(previous)) return false;
      await selectResident(previous.id, {
        forceView: true,
        lineageId: previous.lineage_id,
      });
      return true;
    }

    function lineageIdForResident(item) {
      return String(item?.lineage_id || item?.lineage?.lineage_id || item?.resident_id || "").trim();
    }

    function registeredLineageIdForResident(item) {
      return String(item?.lineage_id || item?.lineage?.lineage_id || "").trim();
    }

    return Object.freeze({
      residentSelectionId, activeChatTargetId, activeLineageId, commitChatTarget, selectedResidentId,
      residentExactId, residentRootForExactId, resolveResidentNavigationTarget,
      firstAvailableResidentChatTarget, currentResident, residentChatTarget,
      residentChatTargetForLineage, unavailableResidentBindings, readLastSelection, writeLastSelection,
      currentChatTarget, chatTargetForPanel, residentIdForSelection, runChatTargetEffect,
      beginChatWorkspaceNavigation, rememberChatWorkspaceBeforeSwitch,
      previousChatWorkspaceFromInterfaceHistory, isResidentChatSelection, goPreviousChatWorkspace,
      lineageIdForResident, registeredLineageIdForResident,
    });
  }

  window.PortalLineageWorkbenchChatTargets = Object.freeze({
    createChatTargets, dependencies: DEPENDENCIES,
  });
})();
