(() => {
  "use strict";

  if (window.PortalResidentMenuRuntime) return;

  // The Settings tab's runtime: the Session ID, and the runtime setting
  // rows that open the Runtime Settings dialog (a Family facade shows its
  // Family settings instead).

  const DEPENDENCIES = Object.freeze([
    "$id", "esc", "menuState", "nodeMenuNotice", "renderFamilySettings",
  ]);

  function createRuntime(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuRuntime is missing: ${missing.join(", ")}`);
    const {
      $id, esc, menuState, nodeMenuNotice, renderFamilySettings,
    } = dependencies;

    function renderNodeMenuSession(context, {detail, error}) {
      if (menuState.nodeMenuContext !== context) return;
      if (!error) {
        const backend = String(detail?.resident?.backend || "").trim();
        if (backend && backend !== context.node.backend) {
          context.node = {...context.node, backend};
          void loadNodeMenuRuntime();
        }
        context.sessionId = String(detail?.provider_session?.id || "").trim();
        $id("nodeMenuSessionId").textContent = context.sessionId || window.PortalI18n.t("尚未创建");
        $id("nodeMenuCopySession").disabled = !context.sessionId;
      } else {
        context.sessionId = "";
        $id("nodeMenuSessionId").textContent = window.PortalI18n.text`读取失败：${error.message}`;
        $id("nodeMenuCopySession").disabled = true;
      }
    }

    async function loadNodeMenuSession({force = false} = {}) {
      const context = menuState.nodeMenuContext;
      if (!context) return;
      try {
        const detail = await window.PortalResidentProjection.load(context.residentId, {force});
        renderNodeMenuSession(context, {detail});
      } catch (error) {
        renderNodeMenuSession(context, {error});
      }
    }

    async function loadNodeMenuRuntime() {
      const context = menuState.nodeMenuContext;
      if (!context) return;
      if (context.family) { renderFamilySettings(context); return; }
      if (!context.runtimeController) {
        context.runtimeController = window.PortalResidentRuntimeController.bind(context.residentId, {
          onState: view => {
            if (menuState.nodeMenuContext !== context) return;
            context.runtimeBusy = view.loading;
            context.saving = view.busy;
            const rows = [["Backend", view.detail?.resident?.backend || context.node.backend || "—"],
              ...window.PortalChatWorkspace.residentRuntimeSettingRows(view.settings, view.catalog || {})];
            const disabled = view.loading || view.busy || ["accepted", "submitting", "confirming", "cancelling"].includes(view.restart?.status)
              || (!view.canEdit && !view.canRestart);
            const root = $id("nodeMenuRuntime");
            const focus = root.contains(document.activeElement) ? document.activeElement?.dataset.menuRuntime : null;
            root.innerHTML = rows.map(([label, value]) => window.PortalI18n.html`<button class="residentMenuSetting" type="button" data-menu-runtime="${label}" aria-haspopup="dialog" ${disabled ? "disabled" : ""}><span>${label}</span><strong>${esc(value)}</strong><span aria-hidden="true">›</span></button>`).join("");
            if (focus) root.querySelector(`[data-menu-runtime="${CSS.escape(focus)}"]`)?.focus();
          },
          onChanged: () => { if (menuState.nodeMenuContext === context) void loadNodeMenuSession({force: true}); },
        });
        $id("nodeMenuRuntime").onclick = async event => {
          const button = event.target.closest("[data-menu-runtime]");
          if (!button || button.disabled || menuState.nodeMenuContext !== context) return;
          const field = button.dataset.menuRuntime;
          try {
            await context.runtimeController.openSettings(() => $id("nodeMenuRuntime").querySelector(`[data-menu-runtime="${CSS.escape(field)}"]`));
          } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
        };
      }
      try { await context.runtimeController.load(); }
      catch (error) { if (menuState.nodeMenuContext === context) $id("nodeMenuRuntime").textContent = window.PortalI18n.text`运行设置读取失败：${error.message}`; }
    }

    return {
      loadNodeMenuRuntime, renderNodeMenuSession,
    };
  }

  window.PortalResidentMenuRuntime = Object.freeze({ createRuntime, dependencies: DEPENDENCIES });
})();
