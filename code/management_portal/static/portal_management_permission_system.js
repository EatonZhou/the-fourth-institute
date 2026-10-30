(function () {
  "use strict";

  if (window.PortalManagementPermissionSystem) return;

  // The permission system: the scheme in use and activating another one
  // after confirming its effect, and turning the system on or off, which
  // the tabs, the users view and the schemes view follow.

  const DEPENDENCIES = Object.freeze([
    "$", "acceptExecution", "entries", "executionSection", "executionStatus", "maySwitch", "me", "panelState",
    "permissionStatus", "permissionSwitches", "root", "showSetting", "switchMessages", "tabs",
  ]);

  function createPermissionSystem(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementPermissionSystem is missing: ${missing.join(", ")}`);
    const {
      $, acceptExecution, entries, executionSection, executionStatus, maySwitch, me, panelState, permissionStatus,
      permissionSwitches, root, showSetting, switchMessages, tabs,
    } = dependencies;

    // The scheme in use; activating another one brings its saved instance up to date first (ActivatePermissionScheme).
    const schemeSelect = executionSection.querySelector("[data-permission-scheme]");
    let activeScheme = "";
    // The settings read and the scheme list read answer in either order; their failures share the status line.
    const readNotes = {settings: "", schemes: ""};
    const showReadNote = (key, text) => {
      readNotes[key] = text;
      executionStatus.textContent = [readNotes.settings, readNotes.schemes].filter(Boolean).join(" ");
    };
    // Portal 管理员 always list the schemes (8fd7ed6f); anyone else a scheme refuses sees the refusal as it is.
    const readSchemes = () => {
      void window.PortalInstructionClient.execute("ListPermissionSchemes", {}).then(result => {
        if (panelState.destroyed) return;
        const schemes = Array.isArray(result.schemes) ? result.schemes : [];
        activeScheme = schemes.find(item => item.active)?.scheme_id || "";
        schemeSelect.replaceChildren(...schemes.map(item => {
          const option = document.createElement("option");
          option.value = item.scheme_id;
          option.textContent = item.title || item.scheme_id;
          option.selected = item.active === true;
          return option;
        }));
        schemeSelect.disabled = !schemes.length;
        readNotes.schemes = "";
      }).catch(error => { if (!panelState.destroyed) showReadNote("schemes", error.message); });
    };
    // The 授权差 of an activation (b820 §5.12), as rows of the confirmation: the scheme page's wording, with
    // the A levels titled from the scheme's rules when they can be read. It only informs.
    const authDiffRows = async (schemeId, report) => {
      if (!report) return [];
      try {
        await window.PortalUIPrimitives.loadAsset("/static/portal_permission_schemes.js", {ready: () => window.PortalPermissionSchemes});
        const rules = report.groups?.items?.length
          ? (await window.PortalInstructionClient.execute("GetPermissionScheme", {scheme_id: schemeId}).catch(() => null))?.rules : null;
        const parts = window.PortalPermissionSchemes.authDiffParts(report, rules);
        const t = window.PortalI18n.t;
        return [
          {label: t("授权差"), value: parts.head}, {label: t("未算完"), value: parts.incomplete},
          ...parts.groups.map(item => ({label: item.kindLabel, value: item.text})), {label: t("其余"), value: parts.more},
          ...parts.remapped.map(item => ({label: t("归类改变"), value: `${item.instruction}：${item.lines.join(t("；"))}`})), {label: t("其余"), value: parts.remappedMore},
        ];
      } catch (_error) { return []; }
    };
    schemeSelect.addEventListener("change", async () => {
      const schemeId = schemeSelect.value;
      if (!schemeId || schemeId === activeScheme) return;
      panelState.pending++;
      schemeSelect.disabled = true;
      try {
        const impact = await window.PortalInstructionClient.execute("ActivatePermissionScheme", {scheme_id: schemeId, dry_run: true, diff_limit: 8});
        if (panelState.destroyed) return;
        const authRows = await authDiffRows(schemeId, impact.auth_diff);
        if (panelState.destroyed) return;
        // While the system is off, activating only records the scheme; its instance is brought up to date when it is turned on.
        const t = window.PortalI18n.t;
        const work = impact.regenerate
          ? t("这份方案还没有按当前规则生成的实例，将按 Portal 现有对象生成：{p0} 个节点、{p1} 条表项。", {p0: impact.added_nodes || 0, p1: impact.added_entries || 0})
          : t("先把这份方案的实例补齐到 Portal 现有对象：新增 {p0} 条、删除 {p1} 条表项，移动 {p2} 个节点；实例中的修改原样保留。",
            {p0: impact.added_entries || 0, p1: impact.removed_entries || 0, p2: impact.moved_nodes || 0});
        const approved = await window.PortalUI.confirm(panelState.permissionsOff ? {
          title: t("开启权限系统时使用 {p0}？", {p0: impact.title || schemeId}),
          message: [t("权限系统仍保持关闭，现在不影响任何判定；在“执行设置”中开启权限系统时，Portal 按这份方案判定。"), work].join(" "),
          metadata: authRows, confirmLabel: t("设为开启时使用"),
        } : {
          title: t("启用权限方案「{p0}」？", {p0: impact.title || schemeId}),
          message: [work, t("之后所有判定按这份方案进行。")].join(" "),
          metadata: authRows, confirmLabel: t("启用"), tone: "danger",
        });
        if (panelState.destroyed) return;
        if (!approved) { schemeSelect.value = activeScheme; return; }
        if (!panelState.permissionsOff) executionStatus.textContent = t("正在补齐权限实例…");
        await window.PortalInstructionClient.execute("ActivatePermissionScheme", {scheme_id: schemeId},
          {idempotencyKey: `permission-scheme:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (panelState.destroyed) return;
        activeScheme = schemeId;
        executionStatus.textContent = t(panelState.permissionsOff ? "开启权限系统时将使用这份方案。" : "权限方案已切换。");
      } catch (error) {
        if (!panelState.destroyed) { schemeSelect.value = activeScheme; executionStatus.textContent = error.message; }
      } finally {
        panelState.pending--;
        if (!panelState.destroyed) schemeSelect.disabled = false;
      }
    });

    const syncPermissionSystem = enabled => {
      if (panelState.destroyed || typeof enabled !== "boolean") return;
      panelState.permissionRevision++;
      panelState.permissionsOff = !enabled;
      panelState.savedExecution.permission_system_enabled = enabled;
      permissionSwitches.forEach(input => { showSetting(input); input.disabled = !maySwitch || panelState.permissionSaving; });
      tabs.querySelector('[data-management-tab="entries"]').hidden = !enabled;
      $("view-permissions").hidden = !enabled;
      root.querySelector(".managementCreationPermissions h3").hidden = !enabled;
      root.querySelector("[data-management-users] header p").firstChild.textContent = `${window.PortalI18n.t(enabled
        ? "管理账户资料与业务关联；各类资源的实际权限在“权限表项”分栏中查看。"
        : "管理账户资料与业务关联；权限系统关闭时不限制访问。")} `;
      entries.get("schemes").instance?.setSystemEnabled?.(enabled);
    };
    const showPermissionMessage = message => {
      executionStatus.textContent = message;
      permissionStatus.textContent = message;
      permissionStatus.hidden = !message;
    };
    const savePermissionSystem = async value => {
      if (!maySwitch || panelState.permissionSaving || panelState.destroyed) { permissionSwitches.forEach(showSetting); return; }
      panelState.permissionSaving = true;
      panelState.pending++;
      permissionSwitches.forEach(input => { input.disabled = true; });
      try {
        const approved = await window.PortalUI.confirm(value
          ? {title: window.PortalI18n.t("开启权限系统？"), message: window.PortalI18n.t("先把启用方案的实例补齐到 Portal 现有对象，再对所有调用恢复检查。对象较多时需要等待一会儿。"), confirmLabel: window.PortalI18n.t("开启")}
          : {title: window.PortalI18n.t("关闭权限系统？"), message: window.PortalI18n.t("所有已登录的 User 与 Resident 将可以查看、使用、修改和删除所有对象。权限方案的实例和临时凭证保留，重新开启时补齐后继续生效。"), confirmLabel: window.PortalI18n.t("关闭"), tone: "danger"});
        if (!approved || panelState.destroyed) return;
        showPermissionMessage(window.PortalI18n.t(value ? "正在重建权限实例…" : "正在关闭权限系统…"));
        const result = await window.PortalInstructionClient.execute("UpdateExecutionSettings", {permission_system_enabled: value},
          {idempotencyKey: `execution-settings:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (panelState.destroyed) return;
        acceptExecution(result.settings, permissionSwitches);
        showPermissionMessage(switchMessages.permission_system_enabled(panelState.savedExecution.permission_system_enabled));
        if (!panelState.savedExecution.permission_system_enabled) readSchemes();
      } catch (error) {
        if (!panelState.destroyed) showPermissionMessage(error.message);
      } finally {
        panelState.pending--;
        panelState.permissionSaving = false;
        if (!panelState.destroyed) permissionSwitches.forEach(input => { showSetting(input); input.disabled = !maySwitch; });
      }
    };
    if (typeof me.access?.permission_system === "string") syncPermissionSystem(!panelState.permissionsOff);

    return {
      readSchemes, savePermissionSystem, showReadNote, syncPermissionSystem,
    };
  }

  window.PortalManagementPermissionSystem = Object.freeze({ createPermissionSystem, dependencies: DEPENDENCIES });
})();
