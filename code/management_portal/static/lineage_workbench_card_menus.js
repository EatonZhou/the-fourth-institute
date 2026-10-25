(() => {
  "use strict";

  if (window.PortalLineageWorkbenchCardMenus) return;

  // The menus of the cards in the side views: what a Resident, Lineage or member
  // card offers the user under their permissions, the controls bound on each
  // card, the editors a card can open, favourites, and deleting a Resident or a
  // Lineage from the workbench.
  const DEPENDENCIES = Object.freeze([
    "$id", "activeChatTargetId", "beginChatWorkspaceNavigation", "clearDeletedLineageLocation",
    "clearDeletedResidentLocation", "commitChatTarget", "currentAgentChatPanel",
    "currentChatTarget", "currentLineageWorkbench", "derivedResidentForSidebar",
    "dismissRecentAgentSession", "esc", "firstAvailableResidentChatTarget",
    "handleResidentCardClick", "handleResidentMemberCardAction", "isFavoriteAgent",
    "isLineageLeaderResident", "isPersonalAmaResident", "lineageContainsPersonalAmaLeader",
    "lineageIdForResident", "lineageLeaderResidentId", "lineageSidebarResidentsForItem",
    "loadDetail", "loadFolder", "menuTargetById", "openLineageAttachChoiceModal",
    "openResidentEditModal", "pendingResidentDeletions", "recentAgentSessionKey",
    "refreshResidentSidebarIfDue", "registeredLineageIdForResident", "renderAgentList",
    "renderFavoriteList", "resetChatRenderState", "residentChatTarget", "residentExactId",
    "residentService", "retireDeletedResidentSurfaces", "selectDerivedResident",
    "selectedResidentId", "selectResident", "societyService", "state", "syncChatTargetLocation",
    "syncLineageHatgPanel", "syncSelectedResidentSurface", "unavailableResidentBindings",
    "writeLastSelection", "writeLineageUiState",
  ]);

  function residentMemberMenuKey(residentId, derivedResidentId) {
    return `${residentId || ""}:${derivedResidentId || ""}`;
  }

  function editorOpenMenuActionsHtml({ attributes = () => "" } = {}) {
    return window.PortalEditorOpenCommands.actionsHtml({
      className: "agentCardAction",
      attributes: command => attributes(command.id),
    });
  }

  function agentMenuButton(instanceId) {
    for (const btn of document.querySelectorAll(".sideView.active [data-agent-menu]")) {
      if (btn.dataset.agentMenu === instanceId) return btn;
    }
    for (const btn of document.querySelectorAll("[data-agent-menu]")) {
      if (btn.dataset.agentMenu === instanceId) return btn;
    }
    return null;
  }

  function residentMemberMenuButton(menuId) {
    for (const btn of document.querySelectorAll(".sideView.active [data-resident-member-menu]")) {
      if (btn.dataset.residentMemberMenu === menuId) return btn;
    }
    for (const btn of document.querySelectorAll("[data-resident-member-menu]")) {
      if (btn.dataset.residentMemberMenu === menuId) return btn;
    }
    return null;
  }

  function panelIdsInLayout(node, ids = new Set()) {
    if (!node) return ids;
    if (node.type === "stack") (node.children || []).forEach(child => child?.panelId && ids.add(String(child.panelId)));
    else (node.children || []).forEach(child => panelIdsInLayout(child, ids));
    return ids;
  }

  function favoriteSelectionForObject(item) {
    const value = String(item || "");
    const prefix = ["lineage", "resident"].find(name => value.startsWith(`${name}_`));
    return value ? `${prefix || "project"}:${value}` : "";
  }

  function createCardMenus(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchCardMenus is missing: ${missing.join(", ")}`);
    const {
      $id, activeChatTargetId, beginChatWorkspaceNavigation, clearDeletedLineageLocation,
      clearDeletedResidentLocation, commitChatTarget, currentAgentChatPanel, currentChatTarget,
      currentLineageWorkbench, derivedResidentForSidebar, dismissRecentAgentSession, esc,
      firstAvailableResidentChatTarget, handleResidentCardClick, handleResidentMemberCardAction,
      isFavoriteAgent, isLineageLeaderResident, isPersonalAmaResident,
      lineageContainsPersonalAmaLeader, lineageIdForResident, lineageLeaderResidentId,
      lineageSidebarResidentsForItem, loadDetail, loadFolder, menuTargetById,
      openLineageAttachChoiceModal, openResidentEditModal, pendingResidentDeletions,
      recentAgentSessionKey, refreshResidentSidebarIfDue, registeredLineageIdForResident,
      renderAgentList, renderFavoriteList, resetChatRenderState, residentChatTarget,
      residentExactId, residentService, retireDeletedResidentSurfaces, selectDerivedResident,
      selectedResidentId, selectResident, societyService, state, syncChatTargetLocation,
      syncLineageHatgPanel, syncSelectedResidentSurface, unavailableResidentBindings,
      writeLastSelection, writeLineageUiState,
    } = dependencies;

    let agentFloatingMenuOverlay = null;

    let lineageEditorOpenCommands = null;

    const RESIDENT_CARD_MENU_ACTIONS = [
      { id: "backpack", label: window.PortalI18n.t("Menu"), permission: "canRead", title: window.PortalI18n.t("查看此 Resident 的 Menu") },
      { id: "edit", label: window.PortalI18n.t("编辑"), permission: "canEdit", title: window.PortalI18n.t("编辑此 Resident") },
      { id: "delete", label: window.PortalI18n.t("删除"), permission: "canDelete", title: window.PortalI18n.t("删除此 Resident"), danger: true },
    ];

    function residentCardMenuActionsHtml({
      canEdit = false,
      canDelete = false,
      actionName = action => action,
      attributes = () => "",
    } = {}) {
      const permissions = { canRead: true, canEdit, canDelete };
      return RESIDENT_CARD_MENU_ACTIONS
        .filter(action => permissions[action.permission])
        .map(action => window.PortalI18n.html`<button class="agentCardAction ${action.danger ? "danger" : ""}" type="button" ${attributes(action.id)} data-resident-menu-action="${esc(actionName(action.id))}" title="${esc(action.title)}">${esc(action.label)}</button>`)
        .join("");
    }

    function renderResidentMemberMenu(residentId, derivedResident) {
      const derivedResidentId = residentExactId(derivedResident);
      const openActions = editorOpenMenuActionsHtml({
        attributes: action => `data-resident-member-action="${esc(action)}" data-resident-id="${esc(residentId)}" data-member-resident-id="${esc(derivedResidentId)}"`,
      });
      const manageActions = residentCardMenuActionsHtml({
        canEdit: lineageAllows(derivedResident?.lineage_id).UpdateResident === true,
        canDelete: residentAllows(derivedResident).DeleteResident === true,
        actionName: action => action,
        attributes: action => `data-resident-member-action="${esc(action)}" data-resident-id="${esc(residentId)}" data-member-resident-id="${esc(derivedResidentId)}"`,
      });
      return `${openActions}${manageActions}`;
    }

    function canDeleteResident(item) {
      if (isPersonalAmaResident(item)) return false;
      return residentAllows(item).DeleteResident === true;
    }

    // Each menu button asks for the one instruction it runs: Lineage actions and editing a
    // Resident from the Lineage listing, deleting a Resident from its session projection.
    function lineageAllows(lineageId) {
      return (state.lineages || []).find(item => item?.lineage_id === lineageId)?.allows || {};
    }

    const residentAllowsCache = new Map();

    function residentAllows(item) {
      const residentId = residentExactId(item);
      if (!residentId) return {};
      const cached = residentAllowsCache.get(residentId);
      if (cached) return cached.allows || {};
      const entry = { allows: null };
      residentAllowsCache.set(residentId, entry);
      window.PortalResidentService.projection({ residentId, lineageId: String(item?.lineage_id || "") })
        .then(projection => { entry.allows = projection?.allows || {}; })
        .catch(() => { residentAllowsCache.delete(residentId); })
        .finally(() => { if (state.agentMenuId || state.residentMemberMenuId) renderAgentFloatingMenu(); });
      return {};
    }

    function bindAgentCardControls(root) {
      root.querySelectorAll("[data-project-lineage-create]").forEach(button => button.addEventListener("click", async event => {
        event.stopPropagation();
        button.disabled = true;
        try {
          if (!currentLineageWorkbench()?.isPanelVisible("hatg")) currentLineageWorkbench()?.showPanel("hatg", { zone: "right" });
          const panel = currentLineageWorkbench()?.getPanel("hatg");
          if (!panel?.createLineage) throw new Error("Lineage 创建面板尚未就绪，请刷新重试。");
          await panel.createLineage({ projectId: button.dataset.projectLineageCreate });
        } catch (error) {
          window.PortalUI.toast(window.PortalI18n.text`打开 Lineage 创建表单失败：${error.message || error}`, "error");
        } finally {
          button.disabled = false;
        }
      }));
      root.querySelectorAll("[data-principal-open]").forEach(button => button.addEventListener("click", async event => {
        event.stopPropagation();
        let id = button.dataset.principalOpen;
        const lineageId = button.dataset.lineageOpen || "";
        if (!id && lineageId) {
          try {
            if (!currentLineageWorkbench()?.isPanelVisible("hatg")) currentLineageWorkbench()?.showPanel("hatg", { zone: "right" });
            await currentLineageWorkbench().openResource({
              type: "hatg",
              mode: "reuse",
              resource: { kind: "hatg", lineageId },
            });
          } catch (error) {
            window.PortalUI.toast(window.PortalI18n.text`打开 Lineage 失败：${error.message || error}`, "error");
          }
          return;
        }
        if (id) {
          if (!currentLineageWorkbench()?.isPanelVisible("hatg")) currentLineageWorkbench()?.showPanel("hatg", { zone: "right" });
          if (!currentLineageWorkbench()?.isPanelVisible("chat")) currentLineageWorkbench()?.showPanel("chat");
          selectResident(id, { lineageId, forceView: true, revealInGraph: true }).catch(error => window.PortalUI.toast(error.message, "error"));
        }
      }));
      root.querySelectorAll("[data-resident-select]").forEach(btn => {
        let residentSelectTimer = 0;
        btn.addEventListener("click", event => {
          if (event.detail > 1) return;
          const residentId = btn.dataset.residentSelect || "";
          residentSelectTimer = window.setTimeout(() => handleResidentCardClick(residentId), 300);
        });
        btn.addEventListener("dblclick", event => {
          event.preventDefault();
          event.stopPropagation();
          window.clearTimeout(residentSelectTimer);
          const residentId = String(btn.dataset.residentSelect || "").trim();
          const recommendation = String(state.recommendationResident?.resident_id || "") === residentId
            ? state.recommendationResident
            : null;
          const target = menuTargetById(`resident:${residentId}`)
            || (recommendation ? { type: "resident", item: recommendation } : null);
          if (!target) return;
          void openNavigationEditor(target, "new-tab")
            .catch(error => window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${error.message || error}`, "error"));
        });
      });
      root.querySelectorAll("[data-resident-member-select]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          selectDerivedResident(btn.dataset.residentMemberSelect || "", btn.dataset.memberResidentId || "");
        });
      });
      root.querySelectorAll("[data-resident-member-menu]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          state.agentMenuId = "";
          state.residentMemberMenuId = state.residentMemberMenuId === btn.dataset.residentMemberMenu ? "" : (btn.dataset.residentMemberMenu || "");
          renderAgentList();
          renderFavoriteList();
        });
      });
      root.querySelectorAll("[data-resident-member-action]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          handleResidentMemberCardAction(btn.dataset.residentMemberAction || "", btn.dataset.residentId || "", btn.dataset.memberResidentId || "");
        });
      });
      root.querySelectorAll("[data-agent-menu]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.stopPropagation();
          state.residentMemberMenuId = "";
          state.agentMenuId = state.agentMenuId === btn.dataset.agentMenu ? "" : (btn.dataset.agentMenu || "");
          renderAgentList();
          renderFavoriteList();
        });
      });
      root.querySelectorAll("[data-agent-favorite]").forEach(btn => {
        btn.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          const selectionId = btn.dataset.agentFavorite || "";
          setAgentFavorite(selectionId, !isFavoriteAgent(selectionId));
        });
      });
    }

    function residentMemberMenuTargetById(menuId) {
      const text = String(menuId || "");
      const splitAt = text.indexOf(":");
      if (splitAt < 0) return null;
      const residentId = text.slice(0, splitAt);
      const derivedResidentId = text.slice(splitAt + 1);
      if (!residentId || !derivedResidentId) return null;
      const resident = state.residents.find(item => String(item?.resident_id || "") === residentId) || null;
      const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, resident);
      return derivedResident ? { residentId, derivedResident } : null;
    }

    function menuActionsHtml(target) {
      const creatorHtml = ["resident", "lineage"].includes(target?.type)
        ? window.PortalI18n.html`<div class="agentMeta" data-principal-created-by>${esc(window.PortalI18n.text`创建者：${String(target.item?.created_by || "-")}`)}</div>`
        : "";
      const openActions = editorOpenMenuActionsHtml({
        attributes: action => `data-agent-floating-action="${esc(action)}"`,
      });
      if (target?.type === "project") {
        return `${target.item.lineage_id ? openActions : ""}${window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="menu-scope" title="打开 Project Menu">Menu</button>`}`;
      }
      if (target?.type === "lineage") {
        const allows = lineageAllows(target.item?.lineage_id);
        const manageActions = window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="menu-scope" title="打开 Lineage Menu">Menu</button>
              ${allows.CreateResident === true ? window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="attach-resident" title="向此 Lineage 添加正式 Resident">Attach</button>` : ""}
              ${lineageContainsPersonalAmaLeader(target.item) || allows.DeleteLineage !== true ? "" : window.PortalI18n.html`<button class="agentCardAction danger" type="button" data-agent-floating-action="delete-lineage" title="删除整条 Lineage">删除</button>`}`;
        return `${creatorHtml}${openActions}${manageActions}`;
      }
      if (target?.type === "resident") {
        const canDelete = canDeleteResident(target.item);
        return `${creatorHtml}${openActions}${residentCardMenuActionsHtml({
            canEdit: lineageAllows(target.item?.lineage_id).UpdateResident === true,
            canDelete,
            actionName: action => `${action}-resident`,
            attributes: action => `data-agent-floating-action="${esc(`${action}-resident`)}"`,
          })}`;
      }
      return "";
    }

    function navigationEditorIntent(target) {
      if (target?.type === "project") return navigationEditorIntent({ type: "lineage", item: target.item });
      const item = target?.item || {};
      if (target?.type === "lineage") {
        const lineageId = lineageIdForResident(item);
        const candidateResident = lineageLeaderResidentId(item) || String(item?.resident_id || "").trim();
        const residentId = /^resident_[0-9a-f]{12}$/.test(candidateResident) ? candidateResident : "";
        return {
          type: "hatg",
          resource: {
            kind: "hatg",
            lineageId,
            residentId,
            title: String(item?.lineage?.title || item?.lineage_title || item?.title || lineageId || "HATG"),
          },
        };
      }
      if (target?.type === "resident") {
        const residentId = residentExactId(item);
        return {
          type: "chat",
          resource: {
            kind: "chat",
            resident_id: residentId,
            lineage_id: String(item?.lineage_id || item?.lineage?.lineage_id || "").trim(),
            title: String(item?.title || item?.name || item?.label || residentId || window.PortalI18n.t("Chat Workspace")),
          },
        };
      }
      return null;
    }

    function openNavigationEditor(target, mode) {
      const intent = navigationEditorIntent(target);
      if (!intent || !currentLineageWorkbench()) return Promise.resolve(null);
      return Promise.resolve(currentLineageWorkbench().openResource({
        ...intent,
        mode,
        ...(mode === "side" ? { side: "right" } : {}),
      }));
    }

    function editorOpenCommandMenu() {
      if (lineageEditorOpenCommands) return lineageEditorOpenCommands;
      lineageEditorOpenCommands = window.PortalEditorOpenCommands.create({
        owner: $id("lineageWorkbenchRoot"),
        label: window.PortalI18n.t("打开编辑器"),
        onCommand: (command, context) => context?.open?.(command),
        additionalCommands: context => [
          ...(context?.reuseLabel && typeof context?.reuse === "function" ? [{
            id: "open-reuse", label: context.reuseLabel,
            run: () => context.reuse(),
          }] : []),
          ...(context?.openFamilyStatus ? [{ id: "family-status", label: window.PortalI18n.t("Family 状态"), run: () => context.openFamilyStatus() }] : []),
          ...(context?.principalId ? [{
            id: "backpack", label: window.PortalI18n.t("Menu"),
            run: () => context.openDetails ? context.openDetails() : window.PortalNav.openPrincipalBackpack({ principalId: context.principalId }),
          }] : []),
        ],
      });
      return lineageEditorOpenCommands;
    }

    function showEditorOpenMenu({ event, trigger, open, principalId = "", openDetails, openFamilyStatus, reuseLabel = "", reuse }) {
      if (typeof open !== "function") return;
      editorOpenCommandMenu().open({
        event,
        trigger,
        context: { open, principalId, openDetails, openFamilyStatus, reuseLabel, reuse },
      });
    }

    function showResidentEditorOpenMenu(detail = {}) {
      const residentId = String(detail.residentId || "").trim();
      const lineageId = String(detail.lineageId || "").trim();
      if (!residentId) return;
      const resource = {
        kind: "chat",
        resident_id: residentId,
        lineage_id: lineageId,
        title: String(detail.title || residentId),
      };
      showEditorOpenMenu({
        event: detail.event,
        trigger: detail.trigger,
        principalId: residentId,
        openDetails: detail.openDetails,
        openFamilyStatus: detail.isFamilyFacade ? () => window.PortalChatWorkspace.openFamilyStatus(residentId, { anchor: detail.trigger }) : null,
        reuseLabel: "打开对话",
        reuse: () => {
          // The Chat the user has open for this Resident wins over one they closed earlier.
          const shown = panelIdsInLayout(currentLineageWorkbench().getLayout?.());
          const chats = (currentLineageWorkbench().getPanelsByType("chat") || []).filter(panel => (
            String(panel?.target?.()?.resident_id || "") === residentId
          ));
          const existing = chats.find(panel => shown.has(String(panel?.instanceId?.() || ""))) || chats[0];
          if (existing?.instanceId?.()) {
            currentLineageWorkbench().focusPanel(existing.instanceId());
            return existing;
          }
          return currentLineageWorkbench().openResource({ type: "chat", resource, mode: "reuse" });
        },
        open: command => currentLineageWorkbench().openResource({
          type: "chat",
          resource,
          mode: command.mode,
          ...(command.mode === "side" ? { side: command.side || "right" } : {}),
        }),
      });
    }

    function renderAgentFloatingMenu() {
      const menu = $id("agentFloatingMenu");
      if (!agentFloatingMenuOverlay) {
        agentFloatingMenuOverlay = window.PortalOverlay.bindDynamicPopover({
          panel: menu,
          placement: "top-end",
          gap: 6,
          margin: 8,
          portal: true,
          closeOnOutside: true,
          closeOnEscape: true,
          resolveTriggerAfterClose: previousTrigger => (
            previousTrigger.dataset.agentMenu
              ? agentMenuButton(previousTrigger.dataset.agentMenu)
              : residentMemberMenuButton(previousTrigger.dataset.residentMemberMenu || "")
          ),
          onOpen: () => menu.setAttribute("aria-hidden", "false"),
          onClose: () => {
            menu.setAttribute("aria-hidden", "true");
            const hadOpenMenu = !!(state.agentMenuId || state.residentMemberMenuId);
            state.agentMenuId = "";
            state.residentMemberMenuId = "";
            if (!hadOpenMenu) return;
            renderAgentFloatingMenu();
            renderAgentList();
            renderFavoriteList();
          },
        });
      }
      let button = null;
      let html = "";
      if (state.residentMemberMenuId) {
        const target = residentMemberMenuTargetById(state.residentMemberMenuId);
        button = residentMemberMenuButton(state.residentMemberMenuId);
        if (target) html = renderResidentMemberMenu(target.residentId, target.derivedResident);
      } else if (state.agentMenuId) {
        const target = menuTargetById(state.agentMenuId);
        button = agentMenuButton(state.agentMenuId);
        if (target) html = menuActionsHtml(target);
      }
      const contentHtml = String(html || "").trim();
      if (!button || !contentHtml) {
        agentFloatingMenuOverlay.close();
        menu.setAttribute("aria-hidden", "true");
        menu.innerHTML = "";
        return;
      }
      menu.innerHTML = contentHtml;
      agentFloatingMenuOverlay.open(button);
    }

    async function deleteResidentAgent(item) {
      const residentId = String(item?.resident_id || "").trim();
      if (!residentId) return;
      const title = item.title || residentId;
      const workspace = item.workspace_path || "";
      const ok = await window.PortalUI.confirm({
        title: window.PortalI18n.t("删除 Resident Agent？"),
        message: window.PortalI18n.text`确定删除 Resident Agent「${title}」吗？`,
        description: window.PortalI18n.t("Resident 将标记为已删除并移除对应 HATG 节点、自有工作区与模型会话；对话与运行轨迹保留。\n")
          + window.PortalI18n.t("不会删除整条 Lineage，也不会删除 TODO task。")
          + (isLineageLeaderResident(item) ? window.PortalI18n.t("\n它是所在 Lineage 的 Leader；删除后系统从其余 Resident 中随机选出新的 Leader。") : ""),
        metadata: [{ label: window.PortalI18n.t("工作区"), value: workspace || "-" }],
        confirmLabel: window.PortalI18n.t("删除"),
        tone: "danger",
      });
      if (!ok) return;
      await deleteResidentFromWorkbench({
        residentId,
        lineageId: registeredLineageIdForResident(item),
      });
    }

    async function deleteResidentFromWorkbench({
      residentId,
      lineageId = "",
      fallbackTarget = null,
    } = {}) {
      const exactResidentId = String(residentId || "").trim();
      if (!exactResidentId || pendingResidentDeletions.has(exactResidentId)) return null;
      pendingResidentDeletions.add(exactResidentId);
      let deletion = null;
      try {
        state.agentMenuId = "";
        state.residentMemberMenuId = "";
        agentFloatingMenuOverlay?.close?.();
        const result = await residentService.remove({
          lineage_id: String(lineageId || "").trim(),
          resident_id: exactResidentId,
        });
        const deletedIds = new Set([exactResidentId]);
        const deletedResidentId = String(result?.resident_id || "").trim();
        if (deletedResidentId) deletedIds.add(deletedResidentId);
        deletion = { result, deletedIds };
        await retireDeletedResidentSurfaces([...deletedIds]);

        state.residents = state.residents.filter(item => (
          !deletedIds.has(String(item?.resident_id || "").trim())
        ));
        for (const id of deletedIds) {
          dismissRecentAgentSession(recentAgentSessionKey({ resident_id: id }));
          window.PortalConversationStore?.discardResident?.(id);
        }
        clearDeletedResidentLocation(deletedIds);

        if (deletedIds.has(selectedResidentId())) {
          // Only a caller-named Resident (a derived Resident's parent) takes
          // over the Chat. Otherwise the Chat says the Resident was deleted
          // instead of opening the Lineage's Leader or another Resident.
          const nextTarget = fallbackTarget && !deletedIds.has(String(fallbackTarget.id || ""))
            ? fallbackTarget
            : null;
          if (nextTarget) {
            commitChatTarget(nextTarget);
            beginChatWorkspaceNavigation();
            writeLastSelection(nextTarget);
            syncChatTargetLocation(nextTarget);
          } else {
            state.chatNavigationError = window.PortalI18n.t("该 Resident 已删除。");
            writeLineageUiState({ lastChatTarget: null });
          }
          state.detail = null;
          state.folder = null;
          state.status = null;
          state.selectedFile = "";
          resetChatRenderState();
        }
        renderAgentList();
        renderFavoriteList();
        syncSelectedResidentSurface();
        await refreshResidentSidebarIfDue(true);
        await syncLineageHatgPanel();
        if (activeChatTargetId() && !unavailableResidentBindings.has(selectedResidentId())) {
          await Promise.allSettled([loadDetail(true), loadFolder()]);
        }
        return deletion;
      } catch (err) {
        window.PortalUI.toast(
          window.PortalUI.errorMessage(window.PortalI18n.t(deletion ? "刷新删除结果" : "删除 Resident"), err),
          "error",
        );
        return deletion;
      } finally {
        pendingResidentDeletions.delete(exactResidentId);
      }
    }

    async function deleteLineageFromConsole(item) {
      const lineageId = lineageIdForResident(item);
      if (!lineageId) return false;
      if (lineageContainsPersonalAmaLeader(item)) {
        window.PortalUI.toast(window.PortalI18n.t("Personal AMA 的 Leader Resident 不能删除。"), "warning");
        return false;
      }
      if (state.lineageDeleteSaving === lineageId) return false;
      state.lineageDeleteSaving = lineageId;
      let hatgPanel = null;
      let suspendedHatgTarget = null;
      let suspendedChatTarget = null;
      try {
        const lineage = await societyService.loadLineage(lineageId);
        if (!Array.isArray(lineage?.nodes)) throw new Error("无法确认 Lineage 的完整成员，未执行删除。");
        const members = [...new Map(lineage.nodes
          .filter(node => String(node?.id || "").trim())
          .map(node => [String(node.id).trim(), node])).values()];
        const title = String(
          lineage.title
          || item?.lineage?.title
          || item?.lineage_title
          || item?.title
          || lineageId
        ).trim();
        const ok = await window.PortalUI.confirm({
          title: window.PortalI18n.t("删除 Lineage？"),
          message: window.PortalI18n.text`确定删除 Lineage「${title}」吗？`,
          description: window.PortalI18n.t("Portal 会删除整条 Lineage，将其 Resident 标记为已删除并移除模型会话与工作区；对话与运行轨迹保留。\n")
            + window.PortalI18n.t("如果任一成员仍在运行，后端会拒绝删除。不会删除 TODO task。"),
          metadata: [
            { label: "Lineage", value: lineageId },
            { label: window.PortalI18n.t("Resident 数量"), value: String(members.length) },
          ],
          confirmLabel: window.PortalI18n.t("删除"),
          tone: "danger",
        });
        if (!ok) return false;

        const selectedResident = selectedResidentId();
        const memberIds = new Set(
          members
            .map(member => String(member?.id || "").trim())
            .filter(Boolean)
        );
        const activeTarget = currentChatTarget();
        const panelTarget = currentAgentChatPanel()?.target?.() || {};
        const activeTargetBelongsToLineage = Boolean(
          activeTarget
          && (
            String(activeTarget.lineage_id || "") === lineageId
            || memberIds.has(String(activeTarget.id || ""))
          )
        );
        const panelTargetBelongsToLineage = Boolean(
          panelTarget.resident_id
          && (
            String(panelTarget.lineage_id || "") === lineageId
            || memberIds.has(String(panelTarget.resident_id || ""))
          )
        );
        if (activeTargetBelongsToLineage) {
          suspendedChatTarget = activeTarget;
          memberIds.add(String(activeTarget.id || ""));
          commitChatTarget(null);
          beginChatWorkspaceNavigation();
        } else if (panelTargetBelongsToLineage) {
          suspendedChatTarget = residentChatTarget(
            panelTarget.resident_id,
            panelTarget.lineage_id,
            panelTarget,
          );
        }
        if (panelTargetBelongsToLineage) {
          memberIds.add(String(panelTarget.resident_id || ""));
          await Promise.resolve(currentAgentChatPanel()?.closeSurface?.());
        }
        hatgPanel = currentLineageWorkbench().getPanel("hatg");
        suspendedHatgTarget = hatgPanel?.suspendLineage?.(lineageId) || null;
        const result = await societyService.deleteLineage({
          lineage_id: lineageId,
        });
        for (const residentId of result?.deleted_resident_ids || result?.resident_ids || []) {
          const id = String(residentId || "").trim();
          if (id) memberIds.add(id);
        }
        await retireDeletedResidentSurfaces([...memberIds]);

        state.agentMenuId = "";
        state.lineages = state.lineages.filter(item => item.lineage_id !== lineageId);
        state.residents = state.residents.filter(resident => (
          lineageIdForResident(resident) !== lineageId
          && !memberIds.has(String(resident?.resident_id || "").trim())
        ));
        window.PortalLineageCatalogs.invalidate("residents");
        await refreshResidentSidebarIfDue(true).catch(err => {
          window.PortalUI.toast(err.message || String(err), "error");
        });
        clearDeletedLineageLocation(lineageId, memberIds);
        if (suspendedChatTarget || memberIds.has(selectedResident) || state.chatTarget?.lineage_id === lineageId) {
          commitChatTarget(firstAvailableResidentChatTarget({
            excludedLineageIds: new Set([lineageId]),
            excludedResidentIds: memberIds,
          }));
          beginChatWorkspaceNavigation();
          writeLastSelection();
          if (state.chatTarget) syncChatTargetLocation(state.chatTarget);
          state.detail = null;
          state.folder = null;
          state.status = null;
          state.selectedFile = "";
        }
        renderAgentList();
        renderFavoriteList();
        syncSelectedResidentSurface();
        hatgPanel?.applyLineageDeletion?.(lineageId);
        await syncLineageHatgPanel();
        if (activeChatTargetId()) {
          await Promise.allSettled([loadDetail(true), loadFolder()]);
        }
        window.PortalUI.toast(window.PortalI18n.t("已删除。"), "warning", 6000);
        return true;
      } catch (err) {
        if (suspendedHatgTarget) {
          await Promise.resolve(
            hatgPanel?.resumeLineage?.(suspendedHatgTarget),
          ).catch(() => {});
        }
        if (suspendedChatTarget) {
          commitChatTarget(suspendedChatTarget);
          beginChatWorkspaceNavigation();
          writeLastSelection();
          syncChatTargetLocation(suspendedChatTarget);
          syncSelectedResidentSurface();
          await currentAgentChatPanel()?.setResident?.({
            resident_id: suspendedChatTarget.id,
            lineage_id: suspendedChatTarget.lineage_id,
            title: suspendedChatTarget.title,
            backend: suspendedChatTarget.backend,
          }, {
            load: true,
            force: true,
          }).catch(() => {});
        }
        window.PortalUI.toast(
          window.PortalUI.errorMessage(window.PortalI18n.t("删除 Lineage"), err),
          "error",
        );
        return false;
      } finally {
        if (state.lineageDeleteSaving === lineageId) {
          state.lineageDeleteSaving = "";
        }
      }
    }

    async function setAgentFavorite(selectionId, favorite) {
      const id = String(selectionId || "").trim();
      if (!id) return;
      try {
        const objectId = id.includes(":") ? id.slice(id.indexOf(":") + 1) : id;
        const result = await window.PortalInstructionClient.execute("UpdateUserNavigation", {
          user_id: await window.PortalIdentity.userId(),
          object_id: objectId,
          favorite: !!favorite,
        });
        state.favoriteLoadError = "";
        state.favoriteAgentIds = new Set((Array.isArray(result?.items) ? result.items : []).map(favoriteSelectionForObject).filter(Boolean));
        state.agentMenuId = "";
        renderAgentList();
        renderFavoriteList();
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`${favorite ? window.PortalI18n.t("添加收藏") : window.PortalI18n.t("取消收藏")}失败：${err.message || err}`, "error");
      }
    }

    async function handleAgentCardAction(action, instanceId) {
      const target = menuTargetById(instanceId);
      if (!target) {
        state.agentMenuId = "";
        renderAgentList();
        renderFavoriteList();
        return;
      }
      state.agentMenuId = "";
      const item = target.item;
      if (action === "backpack-resident" && target.type === "resident") {
        renderAgentList(); renderFavoriteList();
        agentMenuButton(instanceId)?.focus();
        await window.PortalNav.openPrincipalBackpack({ principalId: residentExactId(item) });
        return;
      }
      if (action === "open-new-tab" || action === "open-side") {
        try {
          await openNavigationEditor(target, action === "open-side" ? "side" : "new-tab");
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${err.message || err}`, "error");
        }
        renderAgentList();
        renderFavoriteList();
        return;
      }
      if (action === "menu-scope" && ["project", "lineage"].includes(target.type)) {
        renderAgentList();
        renderFavoriteList();
        agentMenuButton(instanceId)?.focus();
        const id = target.type === "project" ? item.project_id : lineageIdForResident(item);
        const lineage = target.type === "lineage"
          ? state.lineages.find(entry => entry.lineage_id === id)
          : null;
        const members = new Map(lineageSidebarResidentsForItem(item).map(entry => [entry.resident_id, entry]));
        const record = lineage ? {
          lineage_id: id,
          project_id: lineage.project_id,
          title: lineage.title,
          description: lineage.description,
          leader_resident_id: lineage.leader_resident_id,
          nodes: (lineage.lineage_node_ids || []).map(residentId => {
            const member = members.get(residentId) || {};
            return {id: residentId, title: member.title || residentId, backend: member.backend, status: member.status};
          }),
        } : null;
        await window.PortalNav.openScopeMenu({
          kind: target.type, id, ...(record ? {record} : {}),
          onChanged: detail => ["settings", "family"].includes(detail.type) ? refreshResidentSidebarIfDue(true) : undefined,
          ...(target.type === "lineage" ? {
            onOpenAdvanced: () => currentLineageWorkbench()?.getPanel("hatg")?.editLineage?.({
              lineageId: id,
              residentId: lineageLeaderResidentId(item) || item?.resident_id || "",
            }),
          } : {}),
        });
        return;
      }
      if (action === "delete-lineage") {
        if (target.type === "lineage") await deleteLineageFromConsole(item);
        return;
      }
      if (action === "delete-resident") {
        if (target.type === "resident" && canDeleteResident(item)) await deleteResidentAgent(item);
        return;
      }
      if (action === "edit-resident") {
        if (target.type === "resident") openResidentEditModal(item);
        renderAgentList();
        renderFavoriteList();
        return;
      }
      if (action === "attach-resident") {
        if (target.type === "lineage") openLineageAttachChoiceModal(item);
        renderAgentList();
        renderFavoriteList();
        return;
      }
      renderAgentList();
      renderFavoriteList();
    }

    return Object.freeze({
      residentMemberMenuKey, bindAgentCardControls, residentMemberMenuButton,
      openNavigationEditor, showResidentEditorOpenMenu, renderAgentFloatingMenu,
      deleteResidentFromWorkbench, deleteLineageFromConsole, favoriteSelectionForObject,
      handleAgentCardAction, currentAgentFloatingMenuOverlay: () => agentFloatingMenuOverlay,
      currentLineageEditorOpenCommands: () => lineageEditorOpenCommands,
    });
  }

  window.PortalLineageWorkbenchCardMenus = Object.freeze({
    createCardMenus, dependencies: DEPENDENCIES, residentMemberMenuKey, residentMemberMenuButton,
    favoriteSelectionForObject,
  });
})();
