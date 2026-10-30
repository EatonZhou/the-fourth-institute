(() => {
  "use strict";

  if (window.PortalLineageWorkbenchChatDetail) return;

  // The selected Resident's detail in the chat panel: loading and applying it,
  // the signatures that tell a real change from a repeat, and the chat
  // header's extension menu and Watchboard installation.
  const DEPENDENCIES = Object.freeze([
    "activeLineageId", "commitChatTarget", "currentAgentChatPanel", "currentAgentChatWorkspace",
    "currentChatTarget", "currentLineageWorkbench", "currentResident", "currentWatchboardController",
    "loadFolder", "publishConversationSelection", "refreshLineageChatView",
    "refreshResidentSidebarIfDue", "renderChatHeader", "residentChatTarget", "residentIdForSelection",
    "residentService", "selectedResidentId", "state", "syncChatTargetLocation",
    "syncLineageChatWorkspaceProjection", "syncLineageHatgPanel", "unavailableResidentBindings",
    "writeLastSelection", "writeLineageUiState",
  ]);

  function createChatDetail(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchChatDetail is missing: ${missing.join(", ")}`);
    const {
      activeLineageId, commitChatTarget, currentAgentChatPanel, currentAgentChatWorkspace,
      currentChatTarget, currentLineageWorkbench, currentResident, currentWatchboardController,
      loadFolder, publishConversationSelection, refreshLineageChatView, refreshResidentSidebarIfDue,
      renderChatHeader, residentChatTarget, residentIdForSelection, residentService, selectedResidentId,
      state, syncChatTargetLocation, syncLineageChatWorkspaceProjection, syncLineageHatgPanel,
      unavailableResidentBindings, writeLastSelection, writeLineageUiState,
    } = dependencies;

    function closeChatExtensionMenu() {
      currentAgentChatWorkspace()?.closeExtensionMenu?.();
    }

    function renderChatExtensionMenu(detail = state.detail || {}) {
      currentAgentChatPanel()?.renderExtensions?.();
    }

    async function updateCurrentResidentWatchboard(action, residentId) {
      if (residentId !== selectedResidentId()) return;
      if (action === "remove" && !await window.PortalUI.confirm({ title: window.PortalI18n.t("移除 Watchboard？"), message: window.PortalI18n.t("只会解除 Portal 挂载，不会删除脚本文件。"), confirmLabel: window.PortalI18n.t("移除") })) return;
      if (residentId !== selectedResidentId()) return;
      if (!residentId || state.watchboardMutationInFlight) return;
      state.watchboardMutationInFlight = true;
      renderChatExtensionMenu();
      async function refreshProjection() {
        if (residentId !== selectedResidentId()) return;
        // Refresh the Chat owner as well as the graph after a committed
        // update or a conflicting write. Older reads must not restore it.
        const panel = currentAgentChatPanel();
        window.PortalResidentProjection.invalidate(residentId);
        const detail = await window.PortalResidentProjection.load(residentId, { force: true });
        if (residentId !== selectedResidentId() || panel !== currentAgentChatPanel()) return;
        panel.applyDetail(detail);
        writeLineageUiState({ watchboardHiddenResidents: [...state.watchboardHiddenResidents] });
        closeChatExtensionMenu();
        currentWatchboardController().render(state.detail);
        await Promise.allSettled([loadFolder(), refreshResidentSidebarIfDue(true)]);
      }
      try {
        const result = await residentService.updateWatchboard(action, {
          resident_id: residentId,
        });
        // Newly installed cards must be visible when their rendered bounds
        // are measured. Revealing them afterwards leaves their default slots.
        state.watchboardHiddenResidents.delete(residentId);
        if (result.lineageRefreshError) {
          window.PortalUI.toast(window.PortalI18n.text`Watchboard 已更新，但图刷新失败；请刷新查看，勿重复安装：${result.lineageRefreshError.message || result.lineageRefreshError}`, "warning");
        }
        if (result.lineage) {
          try {
            await currentLineageWorkbench()?.getPanel("hatg")?.applyLineageMutation(result.lineage, {
              separateObjectIds: result.created_component_ids,
            });
          } catch (error) {
            // The definition is committed. A failed placement must not
            // leave the extension menu offering the installation again.
            window.PortalUI.toast(window.PortalI18n.text`Watchboard 已更新，但画布布局未保存：${error.message || error}`, "warning");
          }
        }
        await refreshProjection();
      } catch (err) {
        if (err.watchboardConflict) {
          let refreshed = !err.watchboardConflict.refreshError;
          try {
            if (err.watchboardConflict.lineage) {
              await currentLineageWorkbench()?.getPanel("hatg")?.applyLineageMutation(err.watchboardConflict.lineage);
            }
            await refreshProjection();
          } catch (_) { refreshed = false; }
          window.PortalUI.toast(refreshed
            ? window.PortalI18n.t("图已被其他操作修改，Watchboard 操作未提交。请检查最新状态后重新操作。")
            : window.PortalI18n.t("Watchboard 操作未提交，最新状态读取失败。请刷新后重新操作。"), "error");
          return;
        }
        window.PortalUI.toast(window.PortalI18n.text`更新 Watchboard 失败：${err.message || err}`, "error");
      } finally {
        state.watchboardMutationInFlight = false;
        renderChatExtensionMenu();
      }
    }

    function compactMessageSignature(items) {
      return (Array.isArray(items) ? items : []).map(item => ({
        id: item?.id || "",
        at: item?.created_at || item?.at || "",
        source: item?.source || "",
        kind: item?.kind || "",
        author: item?.author || "",
        title: item?.title || "",
        len: item?.content_length || String(item?.content || item?.text || item?.message || "").length,
        text: String(item?.content || item?.text || item?.message || "").slice(-220),
        refs: item?.context_refs || {},
        command: item?.slash_command?.command || item?.slash_command || "",
        runtimeStatus: item?.runtime_status || "",
        runtimeDetails: item?.runtime_details || [],
        responseSources: item?.response_sources || {},
        tokenUsage: item?.token_usage || item?.turn_usage || {},
        tokenUsageAt: item?.token_usage_at || "",
      }));
    }

    function workspaceMessageEntries(workspace = currentAgentChatWorkspace()) {
      const items = workspace?.messages?.() || [];
      return items.map((item, idx) => ({
        item,
        idx,
        key: window.PortalChatWorkspace.messageIdentity(item),
      }));
    }

    function workspaceMessageByKey(key, workspace = currentAgentChatWorkspace()) {
      const target = String(key || "");
      return workspaceMessageEntries(workspace).find(entry => entry.key === target)?.item || null;
    }

    function detailSignatureFor(detail, status = null) {
      const resident = currentResident();
      if (resident) {
        return JSON.stringify({
          type: "resident",
          id: detail?.resident_id || resident?.resident_id || "",
          residentState: detail?.resident_state || null,
          runtime: compactRuntimeSignature(detail?.runtime_settings || {}),
          defaultDerivedRuntime: compactRuntimeSignature(detail?.default_derived_runtime_settings || {}),
          watchboard: detail?.watchboard?.signature || "",
          messages: compactMessageSignature(detail?.messages || []),
          managedResidentCount: detail?.managed_resident_count || 0,
          managedResidentLimit: detail?.managed_resident_limit || 0,
          canCreateDerivedResident: !!detail?.can_create_derived_resident,
        });
      }
      return "";
    }

    function compactRuntimeSignature(settings) {
      return {
        model: settings?.model || "",
        effort: settings?.reasoning_effort || "",
        tier: settings?.service_tier || "",
        permissions: settings?.permissions || "",
        reviewer: settings?.approvals_reviewer || "",
        sandbox: settings?.sandbox || "",
        source: settings?.runtime_settings_source || "",
      };
    }

    function syncResidentStatusSignatures(payload) {
      state.residentStatusSignature = String(payload?.content_signature || "");
      state.residentDetailStatusSignature = String(payload?.detail_signature || "");
      state.residentRuntimeSignature = String(payload?.runtime_signature || "");
    }

    function composerDraftKey() {
      const resident = currentResident();
      if (resident) {
        const residentId = selectedResidentId();
        if (!residentId) return "";
        return window.PortalChatWorkspace.residentSessionScope({
          resident_id: residentId,
        });
      }
      return "";
    }

    const messageRole = item => window.PortalChatWorkspace.messageRole(item);

    async function refreshProviderSessionMetadata() {
      const residentId = residentIdForSelection(currentChatTarget());
      if (
        !residentId
        || state.detail?.provider_session?.id
        || state.providerSessionMetadataRequest === residentId
      ) return;
      state.providerSessionMetadataRequest = residentId;
      try {
        const metadata = await window.PortalResidentProjection.load(
          residentId,
          { force: true },
        );
        if (residentId !== residentIdForSelection(currentChatTarget()) || !state.detail) return;
        const providerSession = metadata?.provider_session;
        if (!providerSession?.id) return;
        state.detail.provider_session = providerSession;
        refreshLineageChatView();
        renderChatHeader(currentResident());
      } catch (error) {
        console.error(window.PortalI18n.t("Provider session metadata refresh failed"), error);
      } finally {
        if (state.providerSessionMetadataRequest === residentId) {
          state.providerSessionMetadataRequest = "";
        }
      }
    }

    function handleLineageChatPanelDetail(nextDetail, panel, notification = null) {
      const residentId = selectedResidentId();
      if (!residentId || panel.target()?.resident_id !== residentId) return;
      const projectedResidentId = String(
        nextDetail?.resident_id
        || nextDetail?.resident?.resident_id
        || ""
      ).trim();
      if (projectedResidentId && projectedResidentId !== residentId) return;
      const previousTarget = state.chatTarget;
      const lineageId = String(
        nextDetail?.resident?.lineage_id
        || nextDetail?.chat_workspace?.target?.lineage_id
        || ""
      ).trim();
      if (lineageId && lineageId !== previousTarget?.lineage_id) {
        const graphLineageId = String(currentLineageWorkbench()?.getPanel("hatg")?.snapshot?.()?.lineageId || "");
        const nextTarget = commitChatTarget(residentChatTarget(residentId, lineageId, previousTarget));
        publishConversationSelection(nextTarget, "resident-membership-changed");
        // Menu reloads stay on the same Chat identity. Follow its new
        // membership when the canvas still shows the source, while leaving
        // a separately opened graph alone.
        if (!graphLineageId || graphLineageId === previousTarget?.lineage_id) {
          syncChatTargetLocation(nextTarget);
          void Promise.resolve(syncLineageHatgPanel({ lineageId, residentId }))
            .catch(error => window.PortalUI.toast(
              window.PortalI18n.text`Resident 归属已更新，但图刷新失败，请刷新查看：${error.message || error}`,
              "warning",
            ));
        }
      }
      state.status = null;
      state.detail = nextDetail;
      syncLineageChatWorkspaceProjection(nextDetail);
      syncResidentStatusSignatures(nextDetail);
      writeLastSelection();
      const nextSignature = detailSignatureFor(nextDetail, null);
      const changed = nextSignature !== state.detailSignature;
      const eventType = String(notification?.event?.event_type || "");
      renderChatHeader(nextDetail?.resident || currentResident());
      if (!eventType || !eventType.startsWith("conversation.message.")) {
        currentWatchboardController().render(nextDetail);
      }
      if (changed) state.detailSignature = nextSignature;
      if (!state.detailSignature) state.detailSignature = nextSignature;
      const message = notification?.event?.payload?.message;
      if (eventType === "conversation.message.appended" && messageRole(message) === "agent") {
        void refreshProviderSessionMetadata();
      }
    }

    async function loadDetail(force = false) {
      const resident = currentResident();
      const residentId = selectedResidentId();
      if (!resident || !residentId || !currentAgentChatPanel()) return;
      if (unavailableResidentBindings.has(residentId)) return;
      if (state.lineageDeleteSaving === activeLineageId()) return;
      const selection = currentChatTarget();
      try {
        await currentAgentChatPanel().setResident({
          resident_id: residentId,
          lineage_id: activeLineageId(),
          title: resident.title || resident.name || selection?.title || "",
          backend: resident.backend || selection?.backend || "",
        }, {
          load: true,
          force,
        });
      } catch (_err) {
        // The shared chat panel owns target-unavailable and load errors.
      }
    }

    return Object.freeze({
      closeChatExtensionMenu, renderChatExtensionMenu, updateCurrentResidentWatchboard,
      workspaceMessageByKey, syncResidentStatusSignatures, composerDraftKey, messageRole,
      handleLineageChatPanelDetail, loadDetail,
    });
  }

  window.PortalLineageWorkbenchChatDetail = Object.freeze({ createChatDetail, dependencies: DEPENDENCIES });
})();
