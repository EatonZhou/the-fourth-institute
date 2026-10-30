(function () {
  "use strict";

  if (window.PortalManagementLayout) return;

  // The panel's layout: the tabs, the stage with one pane per view, the
  // sections each pane holds, the permission system row on the schemes
  // pane, and the tabs the caller may open.

  const DEPENDENCIES = Object.freeze([
    "$", "allows", "me", "panelState", "portalAdmin", "root",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementLayout is missing: ${missing.join(", ")}`);
    const {
      $, allows, me, panelState, portalAdmin, root,
    } = dependencies;

    const entries = new Map();

    const layout = document.createElement("div");
    layout.className = "managementLayout";
    const tabs = root.querySelector(".managementTabs");
    tabs.setAttribute("aria-orientation", "vertical");
    const stage = root.querySelector("[data-management-permission-host]");
    stage.hidden = false;
    stage.removeAttribute("role");
    stage.className = "managementStage";
    for (const view of ["users", "backend", "execution", "resource-types", "schemes", "entries", "paths", "network", "instances"]) {
      const pane = document.createElement("section");
      pane.className = "managementView"; pane.dataset.managementView = view;
      pane.setAttribute("role", "tabpanel"); pane.hidden = view !== "users";
      const container = document.createElement("div");
      container.dataset.managementComponent = view;
      pane.append(container); stage.append(pane);
      entries.set(view, {pane, container, instance: null, promise: null, target: "", invalidation: null, syncing: false});
    }
    entries.get("users").pane.append(root.querySelector("[data-management-users]"));
    const executionSection = root.querySelector("[data-management-execution]");
    // Both views use the same setting, authorization, confirmation and save path.
    const permissionRow = executionSection.querySelector('[data-execution-setting="permission_system_enabled"]').closest("label").cloneNode(true);
    permissionRow.querySelector("small").textContent = window.PortalI18n.t("关闭时方案不参与权限判定；开启时使用下方选定方案。");
    const permissionStatus = document.createElement("p");
    permissionStatus.setAttribute("role", "status");
    permissionStatus.dataset.permissionSettingsStatus = "";
    permissionStatus.hidden = true;
    entries.get("schemes").pane.prepend(permissionRow, permissionStatus);
    entries.get("execution").pane.append(executionSection);
    entries.get("paths").pane.append(root.querySelector("[data-management-paths]"));
    // With the permission system off there are no permission entries to manage;
    // the scheme page stays and explains that schemes take effect once it is on.
    const permissionViews = new Set(["entries"]);
    panelState.permissionsOff = me.access?.permission_system === "open";
    if (panelState.permissionsOff) {
      tabs.querySelectorAll("[data-management-tab]").forEach(tab => { tab.hidden = permissionViews.has(tab.dataset.managementTab); });
      $("view-permissions").hidden = true;
      root.querySelector(".managementCreationPermissions h3").hidden = true;
      root.querySelector("[data-management-users] header p").firstChild.textContent = `${window.PortalI18n.t("管理账户资料与业务关联；权限系统关闭时不限制访问。")} `;
    }
    layout.append(tabs, stage); root.prepend(layout);
    tabs.querySelector('[data-management-tab="users"]').hidden = allows.CreateUser !== true;
    tabs.querySelector('[data-management-tab="resource-types"]').hidden = allows.ListResourceTypes !== true && !panelState.permissionsOff;
    tabs.querySelector('[data-management-tab="backend"]').hidden = allows.ListProviders !== true && allows.ListProviderAccounts !== true && allows.UpdateProviderAccount !== true && allows.RegisterProviderAccount !== true && !panelState.permissionsOff;
    tabs.querySelector('[data-management-tab="paths"]').hidden = me.access?.user_category !== "portal_admin";
    tabs.querySelector('[data-management-tab="network"]').hidden = !portalAdmin;
    tabs.querySelector('[data-management-tab="instances"]').hidden = !portalAdmin;

    return {
      entries, executionSection, permissionStatus, permissionViews, tabs,
    };
  }

  window.PortalManagementLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
