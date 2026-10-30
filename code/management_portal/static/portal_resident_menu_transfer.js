(() => {
  "use strict";

  if (window.PortalResidentMenuTransfer) return;

  // Moving the Resident to another Lineage: listing the Lineages that can
  // take it, confirming, and submitting the move with the dialog's
  // controls held.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "dialog", "memberFamily", "menuState", "nodeResidentId", "nodeSettingsNode", "societyService",
    "toast", "transferBlocker",
  ]);

  function createTransfer(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuTransfer is missing: ${missing.join(", ")}`);
    const {
      $id, changed, dialog, memberFamily, menuState, nodeResidentId, nodeSettingsNode, societyService, toast,
      transferBlocker,
    } = dependencies;

    async function openNodeTransfer() {
      const node = nodeSettingsNode();
      if (!node || transferBlocker(menuState.lineage, nodeResidentId(node))) return;
      const family = memberFamily(menuState.lineage, nodeResidentId(node));
      const context = { residentId: nodeResidentId(node), lineageId: menuState.lineage.lineage_id, title: node.title, family, targets: [], busy: false };
      menuState.nodeTransferContext = context;
      $id("nodeTransferFamily").hidden = !family;
      $id("nodeTransferFamily").textContent = family ? window.PortalI18n.text`它会离开 Family「${family.family.title || family.title || "Family"}」：Router 不再把请求交给它；已经转交给它的请求仍由它完成并经门面回复。` : "";
      $id("nodeTransferFields").hidden = false;
      $id("transferNodeBtn").setAttribute("aria-expanded", "true");
      const target = $id("nodeTransferTarget");
      target.disabled = true;
      target.replaceChildren(new Option("加载中…", ""));
      $id("nodeTransferConfirmBtn").disabled = true;
      $id("nodeTransferStatus").textContent = window.PortalI18n.t("正在读取你拥有的 Lineage…");
      try {
        const payload = await societyService.listLineages();
        if (menuState.nodeTransferContext !== context) return;
        context.targets = (payload.lineages || []).filter(item => item.lineage_id !== context.lineageId);
        target.replaceChildren(new Option("请选择目标 Lineage", ""), ...context.targets.map(item => new Option(`${item.title} · ${item.lineage_id}`, item.lineage_id)));
        target.disabled = !context.targets.length;
        $id("nodeTransferStatus").textContent = context.targets.length ? window.PortalI18n.t("请选择接收此 Resident 的 Lineage。") : window.PortalI18n.t("没有可接收的其他 Lineage。请先创建目标 Lineage 或由其 owner 授权。");
      } catch (error) {
        if (menuState.nodeTransferContext !== context) return;
        target.replaceChildren(new Option("加载失败", ""));
        $id("nodeTransferStatus").textContent = window.PortalI18n.text`加载失败：${error.message || error}。可再次点击 Move 重试。`;
      }
    }

    async function submitNodeTransfer() {
      const context = menuState.nodeTransferContext;
      const destination = context?.targets.find(item => item.lineage_id === $id("nodeTransferTarget").value);
      if (!destination || context.busy || menuState.nodeTransferInFlight) return;
      menuState.nodeTransferInFlight = true;
      context.busy = true;
      const button = $id("nodeTransferConfirmBtn");
      button.disabled = true;
      $id("transferNodeBtn").disabled = true;
      let accepted = false;
      try {
        const confirmed = await window.PortalUI.confirm({
          title: window.PortalI18n.t("转移到其他 Lineage？"),
          message: window.PortalI18n.text`将「${context.title}」转移到「${destination.title}」。Resident 身份与会话保留，自有 Workspace 与成员关系会迁移，访问权限随目标 Lineage 生效。未保存的表单修改不会提交。`
            + (context.family ? " " + $id("nodeTransferFamily").textContent : ""),
          confirmLabel: window.PortalI18n.t("确认转移"),
        });
        if (!confirmed || menuState.nodeTransferContext !== context || menuState.lineage?.lineage_id !== context.lineageId) return;
        $id("nodeSettingsModal").querySelectorAll("button, input, textarea, select").forEach(control => {
          control.dataset.transferDisabled = String(control.disabled);
          control.disabled = true;
        });
        if (menuState.nodeTransferContext !== context || menuState.lineage?.lineage_id !== context.lineageId) return;
        $id("nodeTransferStatus").textContent = window.PortalI18n.t("正在提交迁移，请勿重复操作…");

        const result = await window.PortalResidentService.update({
          resident_id: context.residentId,
          lineage_id: context.lineageId,
          target_lineage_id: destination.lineage_id,
        });
        accepted = true;
        window.PortalLineageCatalogs?.invalidate();
        if (menuState.nodeTransferContext === context) dialog.close({force: true});
        await changed({
          type: "move", residentId: context.residentId, lineageId: context.lineageId,
          targetLineageId: destination.lineage_id, result,
        });
        toast(result.workspace_pending ? window.PortalI18n.t("成员关系已转移；Workspace 正在后台迁移，请稍后在目标 Lineage 查看。") : window.PortalI18n.t("Resident 已转移到目标 Lineage。"), "success", 9000);
      } catch (error) {
        const message = accepted ? window.PortalI18n.text`迁移已受理，页面刷新失败，请刷新页面查看，勿重复迁移：${error.message || error}` : window.PortalI18n.text`转移失败：${error.message || error}`;
        if (menuState.nodeTransferContext === context) $id("nodeTransferStatus").textContent = message;
        toast(message, "error", 9000);
      } finally {
        menuState.nodeTransferInFlight = false;
        context.busy = false;
        $id("nodeSettingsModal").querySelectorAll("[data-transfer-disabled]").forEach(control => {
          control.disabled = control.dataset.transferDisabled === "true";
          delete control.dataset.transferDisabled;
        });
        if (menuState.nodeTransferContext === context) {
          button.disabled = accepted || !$id("nodeTransferTarget").value;
          $id("transferNodeBtn").disabled = accepted;
        }
      }
    }

    return {
      openNodeTransfer, submitNodeTransfer,
    };
  }

  window.PortalResidentMenuTransfer = Object.freeze({ createTransfer, dependencies: DEPENDENCIES });
})();
