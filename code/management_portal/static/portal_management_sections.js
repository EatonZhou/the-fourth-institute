(function () {
  "use strict";

  if (window.PortalManagementSections) return;

  // Which view shows: mounting each view's component once, refreshing it
  // after an invalidation, opening a section for a principal, and the tabs
  // that open them.

  const DEPENDENCIES = Object.freeze([
    "$", "accessUser", "entries", "me", "mountNetwork", "notice", "options", "panelState", "permissionViews",
    "portalAdmin", "readExecutionSettings", "readPaths", "renderEditor", "renderUserList", "root", "saveUserDraft",
    "state", "syncPermissionSystem",
  ]);

  function createSections(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementSections is missing: ${missing.join(", ")}`);
    const {
      $, accessUser, entries, me, mountNetwork, notice, options, panelState, permissionViews, portalAdmin,
      readExecutionSettings, readPaths, renderEditor, renderUserList, root, saveUserDraft, state,
      syncPermissionSystem,
    } = dependencies;

    let currentView = "users";

    let navigationVersion = 0;

    const disposePermission = () => {
      navigationVersion++;
      for (const entry of entries.values()) {
        entry.instance?.destroy?.(); entry.instance = null;
      }
    };
    const showView = () => {
      for (const [view, entry] of entries) entry.pane.hidden = view !== currentView;
      root.querySelectorAll("[data-management-tab]").forEach(tab => {
        const selected = tab.dataset.managementTab === currentView;
        tab.setAttribute("aria-selected", String(selected)); tab.classList.toggle("is-primary", selected);
      });
    };
    const ensureEntry = async (view, principalId) => {
      const entry = entries.get(view);
      if (!entry.promise) {
        entry.target = principalId;
        entry.container.textContent = window.PortalI18n.t("正在加载…");
        entry.promise = Promise.resolve().then(async () => {
          if (view === "network") return mountNetwork(entry.container);
          if (view === "instances") return (await import("/static/portal_management_instances.js")).mount(entry.container);
          if (view === "resource-types") return (await import("/static/portal_management_resource_types.js")).mount(entry.container, {me});
          if (view === "backend") return (await import("/static/portal_management_backend.js")).mount(entry.container, {me, accountId: options.accountId});
          return window.PortalNav.mountPermissionManagement({container: entry.container, principalId, view, onSystemChange: syncPermissionSystem});
        }).then(instance => {
          if (panelState.destroyed) { instance?.destroy?.(); return null; }
          entry.instance = instance;
          return instance;
        }).catch(error => {
          if (!panelState.destroyed) entry.container.textContent = error.message || window.PortalI18n.t("无法加载权限管理。");
          entry.promise = null;
          return null;
        });
      }
      return entry.promise;
    };
    const refreshEntry = async entry => {
      if (!entry.invalidation || !entry.instance?.refresh || entry.syncing || entry.instance.isBusy?.()) return;
      const invalidation = entry.invalidation;
      const selected = entry.instance.getPrincipalId?.() ?? entry.target;
      if (invalidation.principalId && invalidation.principalId !== selected) return;
      entry.syncing = true;
      try {
        const refreshed = await entry.instance.refresh(invalidation);
        if (refreshed !== false && entry.invalidation === invalidation) entry.invalidation = null;
      } catch (error) { notice(error.message || window.PortalI18n.t("部分内容待刷新，请稍后重试。"), "error"); }
      finally { entry.syncing = false; }
    };
    const invalidate = ({views = [], principalId, reason = "changed"} = {}) => {
      for (const view of views) {
        const entry = entries.get(view);
        if (entry && view !== "users") entry.invalidation = {principalId, reason};
      }
    };
    const openSection = async ({view = "users", principalId, accountId, createAccount = false} = {}) => {
      if (!entries.has(view)) throw new Error("Unknown Portal Management view.");
      if (["network", "instances"].includes(view) && !portalAdmin) return false;
      if (panelState.permissionsOff && permissionViews.has(view)) { view = "users"; principalId = undefined; }
      if (panelState.destroyed || panelState.confirming) return false;
      const current = entries.get(currentView);
      const targetPrincipal = principalId === undefined
        ? (view === "users" || currentView === "users" ? state.selectedUsername : (current.instance?.getPrincipalId?.() ?? panelState.currentPrincipalId))
        : String(principalId || "");
      const entry = entries.get(view);
      if (view === "users" && targetPrincipal !== state.selectedUsername) {
        if (panelState.pending > 0) { notice(window.PortalI18n.t("账户操作正在处理，请稍后切换用户。"), "error"); return false; }
        if (!accessUser(targetPrincipal)) { notice(window.PortalI18n.t("用户目录中没有该账户。"), "error"); return false; }
        saveUserDraft(); state.selectedUsername = targetPrincipal;
        renderUserList(); renderEditor();
      }
      const principalView = view === "entries" || view === "schemes";
      if (principalView && entry.instance && targetPrincipal !== (entry.instance.getPrincipalId?.() ?? entry.target)) {
        if (entry.syncing || entry.instance.isBusy?.()) { notice(window.PortalI18n.t("目标资料正在处理，请稍后切换。"), "error"); return false; }
        if (!entry.instance.setPrincipalId) { notice(window.PortalI18n.t("该视图暂不支持保留草案切换主体。"), "error"); return false; }
      }
      const version = ++navigationVersion;
      const previousView = currentView;
      currentView = view; panelState.currentPrincipalId = targetPrincipal; showView();
      if (view === "users") return true;
      if (view === "execution") { readExecutionSettings(); return true; }
      if (view === "paths") { readPaths(); return true; }
      const alreadyMounted = Boolean(entry.instance);
      const instance = await ensureEntry(view, targetPrincipal);
      if (!instance || panelState.destroyed || version !== navigationVersion) return false;
      if (view === "schemes" && alreadyMounted) await instance.refreshSystem?.();
      if (view === "backend") {
        instance.focusAccount?.(accountId);
        if (createAccount) instance.openAccountEditor?.();
      }
      if (principalView && targetPrincipal !== (instance.getPrincipalId?.() ?? entry.target)) {
        if (entry.syncing || instance.isBusy?.() || !instance.setPrincipalId) return false;
        entry.syncing = true;
        try {
          if (await instance.setPrincipalId(targetPrincipal) === false) {
            if (!panelState.destroyed && version === navigationVersion) { currentView = previousView; showView(); }
            return false;
          }
          entry.target = targetPrincipal;
        } catch (error) { notice(error.message || window.PortalI18n.t("无法切换目标主体。"), "error"); return false; }
        finally { entry.syncing = false; }
      }
      await refreshEntry(entry);
      return !panelState.destroyed && version === navigationVersion;
    };
    root.querySelectorAll("[data-management-tab]").forEach(tab => tab.addEventListener("click", () => openSection({view: tab.dataset.managementTab})));
    $("view-permissions").addEventListener("click", () => openSection({view: "entries", principalId: state.selectedUsername}));

    return {
      disposePermission, invalidate, openSection, showView,
    };
  }

  window.PortalManagementSections = Object.freeze({ createSections, dependencies: DEPENDENCIES });
})();
