(() => {
  "use strict";

  if (window.PortalLineageWorkbenchSelection) return;

  // Selecting a Resident: switching the chat panel to it, the empty chat
  // workspace, the Resident cards and member actions that select one, opening
  // a conversation from the Inbox, and retiring the surfaces of a deleted Resident.
  const DEPENDENCIES = Object.freeze([
    "$id", "activeAgentPreviewDocument", "activeChatTargetId", "activeLineageId",
    "activePreviewPayload", "beginChatWorkspaceNavigation", "bindLineageChatWorkspace",
    "clearDeletedResidentLocation", "closeChatExtensionMenu", "closeChatroomView", "commitChatTarget",
    "composerDraftKey", "currentAgentChatPanel", "currentAgentChatWorkspace", "currentChatTarget",
    "currentLineageWorkbench", "currentPreviewSelectionController", "currentPreviewTabsController",
    "currentResident", "currentResidentSidebarRefreshCoordinator", "currentWatchboardController",
    "deleteResidentFromWorkbench", "derivedResidentForSidebar", "editDerivedResidentFromSidebar",
    "lineageHatgTargetMatches", "lineageIdForResident", "lineageSidebarRootByResidentId", "loadDetail",
    "loadFolder", "openNavigationEditor", "refreshLineageChatView", "registeredLineageIdForResident",
    "rememberChatWorkspaceBeforeSwitch", "renderAgentList", "renderChatHeader", "renderFavoriteList",
    "renderPreviewActions", "residentChatTarget", "residentMemberMenuButton", "residentMemberMenuKey",
    "resolveResidentNavigationTarget", "selectChatroom", "selectedResidentId", "setPreviewOpenInput",
    "setPreviewTitle", "setSideView", "state", "stopPreviewRefresh", "syncChatTargetLocation",
    "syncLineageHatgPanel", "syncResidentNavigationSelection", "syncResidentStatusSignatures",
    "unavailableResidentBindings", "writeLastSelection", "writeLineageUiState",
  ]);

  function createSelection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchSelection is missing: ${missing.join(", ")}`);
    const {
      $id, activeAgentPreviewDocument, activeChatTargetId, activeLineageId, activePreviewPayload,
      beginChatWorkspaceNavigation, bindLineageChatWorkspace, clearDeletedResidentLocation,
      closeChatExtensionMenu, closeChatroomView, commitChatTarget, composerDraftKey,
      currentAgentChatPanel, currentAgentChatWorkspace, currentChatTarget, currentLineageWorkbench,
      currentPreviewSelectionController, currentPreviewTabsController, currentResident,
      currentResidentSidebarRefreshCoordinator, currentWatchboardController,
      deleteResidentFromWorkbench, derivedResidentForSidebar, editDerivedResidentFromSidebar,
      lineageHatgTargetMatches, lineageIdForResident, lineageSidebarRootByResidentId, loadDetail,
      loadFolder, openNavigationEditor, refreshLineageChatView, registeredLineageIdForResident,
      rememberChatWorkspaceBeforeSwitch, renderAgentList, renderChatHeader, renderFavoriteList,
      renderPreviewActions, residentChatTarget, residentMemberMenuButton, residentMemberMenuKey,
      resolveResidentNavigationTarget, selectChatroom, selectedResidentId, setPreviewOpenInput,
      setPreviewTitle, setSideView, state, stopPreviewRefresh, syncChatTargetLocation,
      syncLineageHatgPanel, syncResidentNavigationSelection, syncResidentStatusSignatures,
      unavailableResidentBindings, writeLastSelection, writeLineageUiState,
    } = dependencies;

    let residentSelectionRequest = 0;

    async function retireDeletedResidentSurfaces(residentIds) {
      const deleted = new Set(residentIds.map(id => String(id || "").trim()).filter(Boolean));
      if (!deleted.size) return;
      window.PortalResidentMenu?.retireResidents([...deleted]);
      for (const id of deleted) unavailableResidentBindings.add(id);
      if (deleted.has(selectedResidentId())) {
        const resident = currentResident();
        const target = currentChatTarget();
        if (target && !target.title) {
          commitChatTarget({ ...target, title: resident?.title || resident?.name || target.id });
        }
        beginChatWorkspaceNavigation();
        state.chatNavigationError = window.PortalI18n.t("该 Resident 已删除。");
        state.detail = null;
        state.folder = null;
        state.status = null;
        state.selectedFile = "";
        state.folderRequestSequence += 1;
        writeLineageUiState({ lastChatTarget: null });
        resetChatRenderState();
      }
      const panels = new Set([currentAgentChatPanel(), ...(currentLineageWorkbench()?.getPanelsByType?.("chat") || [])]);
      for (const runtime of panels) {
        if (deleted.has(String(runtime?.target?.()?.resident_id || ""))) {
          runtime.rejectUnavailableTarget?.({ status: 404 });
        }
      }
      for (const document of currentPreviewTabsController()?.snapshot?.()?.documents || []) {
        if (deleted.has(String(document.residentId || document.restoreLocator?.residentId || ""))) {
          if (currentPreviewTabsController().isActive(document.key)) stopPreviewRefresh();
          await currentPreviewTabsController().close(document.key, { reason: "resident-deleted" });
        }
      }
      for (const id of deleted) window.PortalConversationStore?.discardResident?.(id);
      clearDeletedResidentLocation(deleted);
    }

    function openLineageConversationFromInbox(event) {
      const request = event?.detail;
      const entry = request?.entry;
      if (!request || String(entry?.surface || "") !== "lineage") return;
      let target;
      try {
        target = new URL(String(entry.url || ""), window.location.href);
      } catch {
        return;
      }
      const residentId = String(target.searchParams.get("resident") || "").trim();
      const lineageId = String(target.searchParams.get("lineage") || "").trim();
      const chatroomId = String(target.searchParams.get("chatroom") || "").trim();
      let navigate = null;
      const residentSelection = residentId
        ? residentChatTarget(residentId, lineageId)
        : null;
      if (residentSelection) {
        navigate = () => selectResident(residentSelection.id, {
          forceView: true,
          lineageId: residentSelection.lineage_id,
          revealInGraph: true,
        });
      } else if (/^chatroom_[0-9a-f]{12}$/.test(chatroomId)) {
        navigate = () => {
          setSideView("chatrooms");
          return selectChatroom(chatroomId);
        };
      }
      if (!navigate) return;
      request.handled = true;
      Promise.resolve(navigate()).catch(error => {
        window.PortalUI.toast(window.PortalI18n.text`打开 Agent 对话失败：${error.message || error}`, "error");
      });
    }

    function resetChatRenderState() {
      state.providerSessionMetadataRequest = "";
      state.detailSignature = "";
      state.residentStatusSignature = "";
      state.residentDetailStatusSignature = "";
      state.residentRuntimeSignature = "";
      state.workspaceMessageItems = [];
      currentAgentChatWorkspace()?.clearForwardSelection?.();
    }

    async function handleResidentCardClick(residentId) {
      const id = String(residentId || "").trim();
      if (!id) return;
      state.agentMenuId = "";
      await selectResident(id, { forceView: true });
    }

    async function selectDerivedResident(residentId, derivedResidentId) {
      const id = String(residentId || "").trim();
      const targetResidentId = String(derivedResidentId || "").trim();
      if (!id || !targetResidentId) return;
      await selectResident(targetResidentId, { forceView: true });
    }

    function publishConversationSelection(chatTarget, reason = "resident-select") {
      if (!chatTarget || !currentLineageWorkbench()?.setSelection) return;
      currentLineageWorkbench().setSelection("conversationTarget", Object.freeze({
        residentId: String(chatTarget.id || ""),
        lineageId: String(chatTarget.lineage_id || ""),
        key: String(chatTarget.key || ""),
      }), { source: "lineage", reason });
    }

    async function selectResident(residentId, options = {}) {
      closeChatroomView();
      let chatTarget = residentChatTarget(
        residentId,
        options.lineageId,
        {
          title: options.title,
          backend: options.backend,
        },
      );
      if (!chatTarget?.key) return false;
      // forceView is an explicit conversation-opening intent, not merely a
      // request to focus a panel that happens to remain visible. Restore the
      // canonical Chat panel before any asynchronous target resolution.
      if (options.forceView === true) {
        currentLineageWorkbench()?.focusPanel?.(currentAgentChatPanel()?.instanceId?.() || "chat");
      }
      if (currentLineageWorkbench() && (
        !currentAgentChatPanel()
        || currentLineageWorkbench().getPanel(currentAgentChatPanel().instanceId()) !== currentAgentChatPanel()
      )) {
        return currentLineageWorkbench().openResource({
          type: "chat",
          mode: "reuse",
          resource: {
            kind: "chat",
            resident_id: chatTarget.id,
            lineage_id: chatTarget.lineage_id,
            title: chatTarget.title,
            backend: chatTarget.backend,
            reveal_in_graph: options.revealInGraph === true,
          },
        });
      }
      const selectionRequest = ++residentSelectionRequest;
      chatTarget = await resolveResidentNavigationTarget(chatTarget);
      if (!chatTarget || selectionRequest !== residentSelectionRequest) return false;
      const selectionId = String(chatTarget.key || "");
      const syncSelectedGraph = target => {
        if (options.revealInGraph !== true && lineageHatgTargetMatches(target)) return undefined;
        return Promise.resolve(syncLineageHatgPanel({
          lineageId: target.lineage_id,
          residentId: target.id,
          revealInGraph: options.revealInGraph === true,
        })).then(() => {
          // A later selection or an authoritative move may supersede this
          // graph navigation while it is loading.
          if (
            selectionRequest === residentSelectionRequest
            && activeChatTargetId() === selectionId
            && state.chatTarget?.lineage_id === target.lineage_id
          ) syncChatTargetLocation(state.chatTarget);
        });
      };
      const sameSelection = activeChatTargetId() === selectionId;
      const sameReadyPanel = sameSelection && currentAgentChatPanel()?.isReadyFor?.({
        resident_id: chatTarget.id,
        lineage_id: chatTarget.lineage_id,
      });
      if (sameReadyPanel) {
        publishConversationSelection(chatTarget, "resident-reselect");
        const routingChanged = String(state.chatTarget?.lineage_id || "") !== chatTarget.lineage_id;
        if (routingChanged) {
          commitChatTarget(chatTarget);
          writeLastSelection();
        }
        syncChatTargetLocation(chatTarget);
        if (options.revealInGraph === true || !lineageHatgTargetMatches(chatTarget)) {
          await syncSelectedGraph(chatTarget);
        }
        if (options.forceView === true) {
          currentLineageWorkbench()?.focusPanel?.(currentAgentChatPanel()?.instanceId?.() || "chat");
        }
        return true;
      }
      const switched = rememberChatWorkspaceBeforeSwitch(chatTarget);
      commitChatTarget(chatTarget);
      syncChatTargetLocation(chatTarget);
      if (switched) window.PortalState.interfaceHistory?.visit();
      beginChatWorkspaceNavigation();
      state.agentMenuId = "";
      state.detail = null;
      state.folder = null;
      state.status = null;
      resetChatRenderState();
      state.selectedFile = "";
      writeLastSelection();
      const detailRequest = loadDetail(false);
      syncResidentNavigationSelection();
      syncSelectedResidentSurface();
      // The graph already has an explicit Resident and Lineage. Let it select
      // and reveal the node while Chat loads, instead of waiting for its detail.
      void syncSelectedGraph(chatTarget);
      await detailRequest;
      if (selectionRequest !== residentSelectionRequest || activeChatTargetId() !== selectionId) return false;
      if (!currentAgentChatPanel()?.isReadyFor?.({
        resident_id: chatTarget.id,
        lineage_id: chatTarget.lineage_id,
      })) return false;
      // The detail response is authoritative if this Resident moved since a
      // sidebar entry or deep link was created. Chat reads use the Resident ID;
      // correct only the route/graph once the server has returned its lineage.
      const resolvedLineageId = String(currentAgentChatPanel().target?.()?.lineage_id || "").trim();
      if (resolvedLineageId && resolvedLineageId !== chatTarget.lineage_id) {
        chatTarget = residentChatTarget(chatTarget.id, resolvedLineageId, chatTarget);
        commitChatTarget(chatTarget);
        syncChatTargetLocation(chatTarget);
        writeLastSelection();
        void syncSelectedGraph(chatTarget);
      }
      publishConversationSelection(chatTarget);
      if (options.forceView === true) {
        currentLineageWorkbench()?.focusPanel?.(currentAgentChatPanel()?.instanceId?.() || "chat");
      }
      loadFolder().catch(() => {});
      return true;
    }

    function renderEmptyChatWorkspace() {
      closeChatExtensionMenu();
      renderChatHeader();
      currentWatchboardController().render({});
      renderPreviewActions();
      currentAgentChatPanel()?.showEmpty?.({
        emptyText: state.chatNavigationError || window.PortalI18n.t("选择一个 Resident 开始工作。"),
        userName: state.me || window.PortalI18n.t("用户"),
        assistantName: "Agent",
        placeholder: window.PortalI18n.t("选择 Resident 后开始对话"),
        meta: window.PortalI18n.t("当前没有可用的对话 Session"),
        historyScope: composerDraftKey(),
      });
      if (!activePreviewPayload() && !activeAgentPreviewDocument()) {
        setPreviewTitle(window.PortalI18n.t("预览"));
        $id("previewPath").textContent = window.PortalI18n.t("选择文件预览。");
        setPreviewOpenInput("");
        currentPreviewSelectionController()?.hide?.();
        $id("previewBox").className = "previewBox";
        $id("previewBox").textContent = window.PortalI18n.t("选择文件后显示内容。");
      }
    }

    function syncSelectedResidentSurface() {
      const resident = currentResident();
      if (!resident) {
        if (currentAgentChatPanel()?.target?.().resident_id) {
          void currentAgentChatPanel().closeSurface();
        }
        renderEmptyChatWorkspace();
        return;
      }
      const residentId = selectedResidentId();
      const panelOwnsTarget = String(currentAgentChatPanel()?.target?.().resident_id || "") === residentId;
      const detail = panelOwnsTarget ? currentAgentChatPanel()?.currentDetail?.() : null;
      if (detail) {
        state.detail = detail;
        syncResidentStatusSignatures(detail);
        refreshLineageChatView();
      }
      renderChatHeader(resident);
      currentWatchboardController().render(detail || {});
      renderPreviewActions();
    }

    function activateLineageChatWorkspace(panel, resource = null, intent = {}) {
      if (!panel) return Promise.resolve(false);
      bindLineageChatWorkspace(panel);
      const target = resource && typeof resource === "object"
        ? resource
        : (panel.target?.() || {});
      const residentId = String(target.resident_id || "").trim();
      if (!residentId) return Promise.resolve(false);
      return selectResident(residentId, {
        forceView: true,
        lineageId: String(target.lineage_id || ""),
        title: String(target.title || ""),
        backend: String(target.backend || ""),
        revealInGraph: target.reveal_in_graph === true
          || intent.revealInGraph === true,
      });
    }

    async function handleResidentMemberCardAction(action, residentId, derivedResidentId) {
      state.residentMemberMenuId = "";
      if (action === "backpack") {
        renderAgentList(); renderFavoriteList();
        residentMemberMenuButton(residentMemberMenuKey(residentId, derivedResidentId))?.focus();
        await window.PortalNav.openPrincipalBackpack({ principalId: derivedResidentId || residentId });
        return;
      }
      if (action === "open-new-tab" || action === "open-side") {
        try {
          const sourceResident = state.residents.find(item => String(item?.resident_id || "") === String(residentId || ""))
            || lineageSidebarRootByResidentId(residentId)
            || {};
          const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, sourceResident) || { resident_id: derivedResidentId };
          await openNavigationEditor({
            type: "resident",
            item: {
              ...derivedResident,
              lineage_id: String(derivedResident?.lineage_id || lineageIdForResident(sourceResident) || ""),
            },
          }, action === "open-side" ? "side" : "new-tab");
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${err.message || err}`, "error");
        }
        renderAgentList();
        renderFavoriteList();
      } else if (action === "delete") {
        await deleteDerivedResidentFromSidebar(residentId, derivedResidentId);
      } else if (action === "edit") {
        await editDerivedResidentFromSidebar(residentId, derivedResidentId);
      }
    }

    async function refreshResidentSidebarIfDue(force = false) {
      if (!force) return false;
      const payload = await currentResidentSidebarRefreshCoordinator().refresh({ force: true });
      return !!payload;
    }

    async function deleteDerivedResidentFromSidebar(residentId, derivedResidentId) {
      if (!await window.PortalUI.confirmAction("delete", "这个 Resident", {
        consequence: "Resident 将标记为已删除，并移除 Session、图引用与自有工作区；对话与运行轨迹保留。",
      })) return;
      const derivedResident = state.residents.find(item => (
        String(item?.resident_id || "").trim() === String(derivedResidentId || "").trim()
      ));
      const parentResident = state.residents.find(item => (
        String(item?.resident_id || "").trim() === String(residentId || "").trim()
      ));
      const parentLineageId = registeredLineageIdForResident(parentResident)
        || activeLineageId();
      await deleteResidentFromWorkbench({
        residentId: derivedResidentId,
        lineageId: registeredLineageIdForResident(derivedResident) || parentLineageId,
        fallbackTarget: residentChatTarget(residentId, parentLineageId, parentResident),
      });
    }

    return Object.freeze({
      retireDeletedResidentSurfaces, openLineageConversationFromInbox, resetChatRenderState,
      handleResidentCardClick, selectDerivedResident, publishConversationSelection, selectResident,
      syncSelectedResidentSurface, activateLineageChatWorkspace, handleResidentMemberCardAction,
      refreshResidentSidebarIfDue,
    });
  }

  window.PortalLineageWorkbenchSelection = Object.freeze({ createSelection, dependencies: DEPENDENCIES });
})();
