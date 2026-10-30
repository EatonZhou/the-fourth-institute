(() => {
  "use strict";

  if (window.PortalResidentMenuResident) return;

  // The Menu of one Resident: opening it (its menu context, name, identity
  // readouts, intake switch, Move button and removal), leaving and
  // disposing it, the status line, saving a new name, switching intake and
  // deleting the Resident.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "dialog", "disposeFamilySettings", "disposeNodeDetails", "disposeNodeHarness",
    "disposeNodeMailbox", "leaveNodeDetails", "loadNodeMenuIdentity", "loadNodeMenuRuntime", "menuState",
    "nodeIntakeState", "nodeResidentId", "renderNodeMenuSession", "renderNodeSettingsTab", "switchNodeSettingsTab",
    "toast", "transferBlocker",
  ]);

  function createResident(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuResident is missing: ${missing.join(", ")}`);
    const {
      $id, changed, dialog, disposeFamilySettings, disposeNodeDetails, disposeNodeHarness, disposeNodeMailbox,
      leaveNodeDetails, loadNodeMenuIdentity, loadNodeMenuRuntime, menuState, nodeIntakeState, nodeResidentId,
      renderNodeMenuSession, renderNodeSettingsTab, switchNodeSettingsTab, toast, transferBlocker,
    } = dependencies;

    function renderNodeSettingsIntake(node) {
      const panel = $id("nodeSettingsIntake");
      if (!panel) return;
      const available = !!nodeResidentId(node);
      panel.hidden = !available;
      if (!available) return;
      const intakeState = nodeIntakeState(node);
      const busy = !!menuState.nodeMenuContext?.intakeBusy;
      panel.dataset.state = intakeState;
      $id("nodeSettingsIntakeState").textContent = busy ? window.PortalI18n.t("切换中…") : intakeState.toUpperCase();
      $id("nodeSettingsIntakeHint").textContent = intakeState === "closed"
        ? window.PortalI18n.t("当前工作不会中断；新消息只进入 queue，切回 OPEN 后恢复执行。")
        : window.PortalI18n.t("空闲时会立即执行新消息；设为 CLOSED 后，新消息只进入 queue。");
      const button = $id("nodeSettingsIntakeToggle");
      button.disabled = busy;
      button.setAttribute("role", "switch");
      button.setAttribute("aria-checked", String(intakeState === "open"));
      button.title = $id("nodeSettingsIntakeHint").textContent;

    }

    function nodeSettingsNode() {
      return menuState.nodeMenuContext?.node || null;
    }

    async function openNodeSettingsModal(requestedNode, tab = "display") {
      if (menuState.nodeTransferInFlight) {
        toast(window.PortalI18n.t("迁移正在处理，请等待本次操作结束。"), "info");
        return;
      }
      const node = requestedNode;
      if (!node) return;
      disposeNodeDetails();
      disposeNodeMailbox();
      disposeNodeHarness();
      renderNodeSettingsTab("display");
      $id("nodeSettingsNameLabel").textContent = window.PortalI18n.t("Resident 名称");
      $id("nodeSettingsName").value = node.title || "";
      disposeNodeMenu();
      menuState.nodeMenuContext = { residentId: nodeResidentId(node), lineageId: menuState.lineage.lineage_id, node, pinned: false, pinReady: false, busy: false };
      menuState.nodeMenuContext.family = (menuState.lineage.function_blocks || []).find(block => block.family?.facade === nodeResidentId(node)) || null;
      $id("nodeSettingsForm").hidden = Boolean(menuState.nodeMenuContext.family);
      $id("nodeMenuFamilySettings").hidden = !menuState.nodeMenuContext.family;
      // A Family facade has no runtime form; its avatar row sits above the Family settings instead.
      if (menuState.nodeMenuContext.family) $id("nodeMenuFamilySettings").before($id("nodeMenuAvatar"));
      else $id("nodeSettingsForm").querySelector(".nodeSettingsFooter").before($id("nodeMenuAvatar"));
      $id("nodeMenuName").textContent = node.title || nodeResidentId(node);
      $id("nodeMenuRenameFields").hidden = true;
      $id("nodeMenuStatus").hidden = true;
      $id("nodeMenuPortrait").textContent = (node.title || "R").slice(0, 1);
      $id("nodeJournalRows").replaceChildren();
      $id("nodeJournalRetry").hidden = true;
      $id("nodeMenuRuntime").textContent = window.PortalI18n.t("正在读取运行设置…");
      $id("nodeMenuSessionId").textContent = window.PortalI18n.t("读取中…");
      $id("nodeMenuCopySession").disabled = true;
      const sessionContext = menuState.nodeMenuContext;
      // A session may have been replaced while this menu was closed. Read the
      // binding on each explicit open instead of reusing the last displayed ID.
      if (!sessionContext.family) {
        window.PortalResidentProjection.invalidate(sessionContext.residentId);
        sessionContext.unobserveSession = window.PortalResidentProjection.subscribe(sessionContext.residentId,
          update => renderNodeMenuSession(sessionContext, update));
      }
      void loadNodeMenuIdentity(menuState.nodeMenuContext);
      $id("nodeSettingsTitle").textContent = window.PortalI18n.t("Menu");
      renderNodeSettingsIdentity(node);
      renderNodeSettingsIntake(node);

      const removeButton = $id("removeNodeBtn");
      const transferBlocked = transferBlocker(menuState.lineage, nodeResidentId(node));
      menuState.nodeTransferContext = null;
      $id("nodeTransferFields").hidden = true;
      $id("transferNodeBtn").disabled = Boolean(transferBlocked);
      $id("transferNodeBtn").title = transferBlocked || window.PortalI18n.t("迁移 Resident 身份、成员关系及自有 Workspace");
      $id("transferNodeBtn").setAttribute("aria-expanded", "false");
      removeButton.disabled = false;
      removeButton.textContent = window.PortalI18n.t("删除节点");
      removeButton.title = window.PortalI18n.t("Resident 将标记为已删除，并移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。");
      dialog.open();
      if (tab !== "display") await switchNodeSettingsTab(tab);
      else await loadNodeMenuRuntime();
    }

    function disposeNodeMenu() {
      disposeFamilySettings();
      menuState.nodeMenuContext?.unobserveSession?.();
      menuState.nodeMenuContext?.unobservePin?.();
      menuState.nodeMenuContext?.avatarEditor?.destroy();
      menuState.nodeMenuContext?.runtimeController?.destroy();
      $id("nodeMenuAvatar").replaceChildren();
      menuState.nodeMenuContext = null;

    }

    function retireResidents(deleted) {
      if (deleted.has(menuState.nodeMenuContext?.residentId)) dialog.close({force: true});
    }

    async function leaveNodeMenu() {
      if (!await leaveNodeDetails()) return false;
      const context = menuState.nodeMenuContext;
      const node = nodeSettingsNode();
      if (node && !$id("nodeMenuRenameFields").hidden && $id("nodeSettingsName").value.trim() !== node.title) {
        const approved = await window.PortalUI.confirm({title: window.PortalI18n.t("放弃未保存的名称？"), message: window.PortalI18n.t("关闭 Menu 会丢失尚未保存的名称。"), confirmLabel: window.PortalI18n.t("放弃修改")});
        if (!approved || context !== menuState.nodeMenuContext) return false;
      }
      return !menuState.nodeMenuContext?.busy && !menuState.nodeMenuContext?.saving && !menuState.nodeMenuContext?.avatarEditor?.isBusy();
    }

    function nodeMenuNotice(message, retry = null) {
      $id("nodeMenuStatus").textContent = message;
      $id("nodeMenuStatus").hidden = !message;
      if (retry) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton is-compact";
        button.textContent = window.PortalI18n.t("重试");
        button.addEventListener("click", retry);
        $id("nodeMenuStatus").append(button);
      }
    }

    function renderNodeSettingsIdentity(node) {
      if (!node) return;
      $id("nodeLineageIdentityReadout").textContent = [
        menuState.lineage?.title,
        menuState.lineage?.lineage_id,
      ].filter(Boolean).join(" · ");
      $id("nodeResidentIdentityReadout").textContent = nodeResidentId(node);
    }

    async function submitNodeSettings(event) {
      event.preventDefault();
      if (menuState.nodeTransferInFlight || menuState.nodeMenuContext?.saving) return;
      const node = nodeSettingsNode();
      if (!node) return;
      const nextTitle = $id("nodeSettingsName").value.trim() || node.title;
      const titleChanged = nextTitle !== node.title;
      const context = menuState.nodeMenuContext;
      if (context) context.saving = true;
      try {
        if (titleChanged) {
          const response = await window.PortalResidentService.update({
            resident_id: nodeResidentId(node),
            title: nextTitle,
          });
          context.node = {...context.node, title: nextTitle};
          await changed({type: "rename", residentId: context.residentId, lineageId: context.lineageId, result: response});
        }
        if (menuState.nodeMenuContext !== context) return;
        $id("nodeMenuName").textContent = nextTitle;
        $id("nodeSettingsName").value = nextTitle;
        $id("nodeMenuRenameFields").hidden = true;
        nodeMenuNotice("");
        toast(titleChanged ? window.PortalI18n.t("Resident 名称已保存。") : window.PortalI18n.t("名称未改变。"));
      } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`名称保存失败：${error.message}`); }
      finally { if (context) context.saving = false; }
    }

    async function updateIntake(node) {
      const context = menuState.nodeMenuContext;
      if (!context || context.intakeBusy) return;
      context.intakeBusy = true;
      renderNodeSettingsIntake(node);
      try {
        const result = await window.PortalResidentService.setIntake({resident_id: context.residentId, open: nodeIntakeState(node) === "closed"});
        if (menuState.nodeMenuContext !== context) return;
        context.node = {...context.node, intake_state: result.current};
        await changed({type: "intake", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { if (menuState.nodeMenuContext === context) { context.intakeBusy = false; renderNodeSettingsIntake(context.node); } }
    }

    async function removeResident(node) {
      const context = menuState.nodeMenuContext;
      if (!context || context.busy || context.saving) return;
      if (menuState.options.onDelete) {
        context.busy = true;
        try { if (await menuState.options.onDelete(node) && menuState.nodeMenuContext === context) dialog.close({force: true}); }
        finally { context.busy = false; }
        return;
      }
      if (!await window.PortalUI.confirm({title: window.PortalI18n.t("删除 Resident？"), message: window.PortalI18n.text`删除「${node.title}」将移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。`, confirmLabel: window.PortalI18n.t("删除"), tone: "danger"}) || menuState.nodeMenuContext !== context) return;
      context.busy = true;
      try {
        const result = await window.PortalResidentService.remove({resident_id: context.residentId});
        // Deletion is committed; a failed owner refresh cannot keep its controls active.
        if (menuState.nodeMenuContext === context) dialog.close({force: true});
        await changed({type: "delete", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { context.busy = false; }
    }

    return {
      disposeNodeMenu, leaveNodeMenu, nodeMenuNotice, nodeSettingsNode, openNodeSettingsModal, removeResident,
      retireResidents, submitNodeSettings, updateIntake,
    };
  }

  window.PortalResidentMenuResident = Object.freeze({ createResident, dependencies: DEPENDENCIES });
})();
