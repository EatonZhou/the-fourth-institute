(function (global) {
  "use strict";

  if (global.PortalWorkbenchPanelRuntimes) return;

  // Panel runtimes: passing context changes to the Panels that listen to
  // them, showing and hiding runtimes as the layout shows and hides their
  // Panels, and the commands of each Panel.

  const DEPENDENCIES = Object.freeze([
    "callPanelLifecycle", "canFocusFixedPanel", "commandScope", "contextStore", "descriptors", "isLayoutLocked",
    "root", "runtimeContext", "shellState",
  ]);

  function createPanelRuntimes(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchPanelRuntimes is missing: ${missing.join(", ")}`);
    const {
      callPanelLifecycle, canFocusFixedPanel, commandScope, contextStore, descriptors, isLayoutLocked, root,
      runtimeContext, shellState,
    } = dependencies;

    const panelCommandDisposers = new Map();

    const unsubscribeWorkbenchContext = contextStore.subscribe("*", change => {
      descriptors.forEach(descriptor => {
        const channels = descriptor.panelPackage.context.channels;
        if (!channels.includes("*") && !change.changedChannels.some(channel => channels.includes(channel))) return;
        callPanelLifecycle(descriptor, "setContext", change.values, runtimeContext, change);
      });
      root.dispatchEvent(new CustomEvent("portal:workbench-selection-change", { detail: change }));
    });

    function activePanelIds(node, result = new Set()) {
      if (!node) return result;
      if (node.type === "panel") {
        if (!shellState.collapsed.has(node.panelId) && !shellState.hidden.has(node.panelId)) result.add(node.panelId);
        return result;
      }
      if (node.type === "stack") {
        if (!shellState.collapsed.has(node.activePanelId) && !shellState.hidden.has(node.activePanelId)) result.add(node.activePanelId);
        return result;
      }
      (node.children || []).forEach(child => activePanelIds(child, result));
      return result;
    }

    function reconcilePanelVisibility() {
      const mobileProjected = global.PortalResponsive?.isMobile?.()
        && descriptors.has(shellState.mobileActivePanelId)
        ? shellState.mobileActivePanelId
        : "";
      const next = !shellState.hostVisible ? new Set() : mobileProjected ? new Set([mobileProjected]) : activePanelIds(shellState.model);
      const previous = shellState.activeRuntimePanelIds;
      // Commit visibility before lifecycle callbacks. A callback may synchronously
      // focus, hide, or show a panel and trigger another render; the nested
      // reconciliation must observe the state that caused this callback.
      shellState.activeRuntimePanelIds = next;
      previous.forEach(panelId => {
        if (next.has(panelId)) return;
        const descriptor = descriptors.get(panelId);
        callPanelLifecycle(descriptor, "hide", runtimeContext);
        descriptor?.onHide?.({ workbench: shellState.controller, panel: descriptor });
      });
      next.forEach(panelId => {
        if (previous.has(panelId)) return;
        const descriptor = descriptors.get(panelId);
        callPanelLifecycle(descriptor, "show", runtimeContext);
        descriptor?.onShow?.({ workbench: shellState.controller, panel: descriptor });
      });
    }

    function unregisterPanelCommands(panelId) {
      (panelCommandDisposers.get(String(panelId || "")) || []).forEach(dispose => dispose());
      panelCommandDisposers.delete(String(panelId || ""));
    }

    function registerPanelCommands(descriptor) {
      unregisterPanelCommands(descriptor.id);
      const prefix = `panel.${descriptor.id}`;
      const disposers = commandScope.registerMany([
        {
          id: `${prefix}.toggle-visibility`,
          title: () => `${shellState.hidden.has(descriptor.id) ? window.PortalI18n.t("显示") : window.PortalI18n.t("隐藏")} ${descriptor.title}`,
          category: "Panel",
          icon: descriptor.icon,
          enabled: () => !isLayoutLocked() && descriptor.canHide,
          execute: () => shellState.controller.togglePanel(descriptor.id),
        },
        {
          id: `${prefix}.toggle-collapsed`,
          title: window.PortalI18n.text`折叠或展开 ${descriptor.title}`,
          category: "Panel",
          icon: "chevron-down",
          enabled: () => !isLayoutLocked() && descriptor.canCollapse && !shellState.hidden.has(descriptor.id),
          execute: () => shellState.controller.toggleCollapsed(descriptor.id),
        },
        {
          id: `${prefix}.focus`,
          title: window.PortalI18n.text`聚焦 ${descriptor.title}`,
          category: "Panel",
          icon: descriptor.icon,
          enabled: () => !shellState.hidden.has(descriptor.id) && (!isLayoutLocked() || canFocusFixedPanel(descriptor.id)),
          execute: () => shellState.controller.focusPanel(descriptor.id),
        },
      ]);
      if (descriptor.closable) {
        disposers.push(commandScope.register({
          id: `${prefix}.close`,
          title: window.PortalI18n.text`关闭 ${descriptor.title}`,
          category: "Panel",
          icon: "close",
          enabled: () => !isLayoutLocked(),
          execute: () => shellState.controller.closePanel(descriptor.id),
        }));
      }
      descriptor.panelPackage.commands.forEach(contribution => {
        disposers.push(commandScope.register({
          ...contribution,
          id: `${prefix}.${String(contribution.id || "").trim()}`,
          execute: invocation => contribution.execute({
            ...invocation,
            context: descriptor.contextScope.values(invocation.context),
            panel: descriptor.runtime,
            panelId: descriptor.id,
            workbench: shellState.controller,
          }),
        }));
      });
      panelCommandDisposers.set(descriptor.id, disposers);
    }

    function destroyPanelRuntimes() {
      shellState.activeRuntimePanelIds.forEach(panelId => {
        callPanelLifecycle(descriptors.get(panelId), "hide", runtimeContext);
      });
      shellState.activeRuntimePanelIds.clear();
      descriptors.forEach(descriptor => {
        callPanelLifecycle(descriptor, "disconnect", runtimeContext);
        callPanelLifecycle(descriptor, "destroy", runtimeContext);
        descriptor.contextScope.dispose();
      });
      unsubscribeWorkbenchContext();
    }

    return {
      destroyPanelRuntimes, reconcilePanelVisibility, registerPanelCommands, unregisterPanelCommands,
    };
  }

  global.PortalWorkbenchPanelRuntimes = Object.freeze({ createPanelRuntimes, dependencies: DEPENDENCIES });
})(window);
