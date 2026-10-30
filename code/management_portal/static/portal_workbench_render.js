(function (global) {
  "use strict";

  if (global.PortalWorkbenchRender) return;

  // Rendering: drawing or refreshing the layout and mounting its Panels,
  // committing the result or rolling back to the last committed layout,
  // the empty state, the mobile switcher, and announcing layout changes.

  const DEPENDENCIES = Object.freeze([
    "canvas", "clone", "customizeButton", "customizeLabel", "descriptors", "executeCommand", "findNode", "findPanel",
    "id", "lastOpenTargetByType", "mobileSwitcher", "mountPanelElement", "options", "panelFrameIn", "persist",
    "placements", "reconcilePanelMounts", "reconcilePanelVisibility", "refreshNode", "renderHiddenRail", "renderMenu",
    "renderNode", "restorePanelMounts", "root", "shellState", "stage", "syncLayoutLock", "toolbar", "topologyOf",
  ]);

  function createRender(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchRender is missing: ${missing.join(", ")}`);
    const {
      canvas, clone, customizeButton, customizeLabel, descriptors, executeCommand, findNode, findPanel, id,
      lastOpenTargetByType, mobileSwitcher, mountPanelElement, options, panelFrameIn, persist, placements,
      reconcilePanelMounts, reconcilePanelVisibility, refreshNode, renderHiddenRail, renderMenu, renderNode,
      restorePanelMounts, root, shellState, stage, syncLayoutLock, toolbar, topologyOf,
    } = dependencies;

    let renderedModelReference = null;
    let renderedTopology = "";
    let renderedMobileMode = false;
    let renderedMobilePanelId = "";
    let committedWorkbenchState = null;

    function commitWorkbenchState() {
      committedWorkbenchState = {
        model: clone(shellState.model),
        hidden: [...shellState.hidden],
        collapsed: [...shellState.collapsed],
        focusedStackId: shellState.focusedStackId,
        activeOpenTargetPanelId: shellState.activeOpenTargetPanelId,
        mobileActivePanelId: shellState.mobileActivePanelId,
        activeWorkspaceName: shellState.activeWorkspaceName,
        placements: clone(placements),
        lastOpenTargets: [...lastOpenTargetByType.entries()],
      };
    }

    function restoreCommittedWorkbenchState() {
      if (!committedWorkbenchState) return false;
      shellState.model = clone(committedWorkbenchState.model);
      shellState.hidden = new Set(committedWorkbenchState.hidden);
      shellState.collapsed = new Set(committedWorkbenchState.collapsed);
      shellState.focusedStackId = committedWorkbenchState.focusedStackId;
      shellState.activeOpenTargetPanelId = committedWorkbenchState.activeOpenTargetPanelId;
      shellState.mobileActivePanelId = committedWorkbenchState.mobileActivePanelId;
      shellState.activeWorkspaceName = committedWorkbenchState.activeWorkspaceName;
      Object.keys(placements).forEach(panelId => delete placements[panelId]);
      Object.assign(placements, clone(committedWorkbenchState.placements));
      lastOpenTargetByType.clear();
      committedWorkbenchState.lastOpenTargets.forEach(([type, panelId]) => {
        lastOpenTargetByType.set(type, panelId);
      });
      return true;
    }

    function announceChange(reason) {
      const detail = { id, reason, layout: clone(shellState.model), hidden: [...shellState.hidden], collapsed: [...shellState.collapsed] };
      root.dispatchEvent(new CustomEvent("portal:workbench-change", { detail }));
      options.onChange?.(detail);
      requestAnimationFrame(() => {
        descriptors.forEach(descriptor => {
          descriptor.element.dispatchEvent(new CustomEvent("portal:workbench-resize", { bubbles: false, detail }));
        });
      });
    }

    function announceBeforeLayout(reason) {
      const detail = { id, reason, layout: clone(shellState.model), hidden: [...shellState.hidden], collapsed: [...shellState.collapsed] };
      root.dispatchEvent(new CustomEvent("portal:workbench-before-layout", { detail }));
      descriptors.forEach(descriptor => {
        descriptor.element.dispatchEvent(new CustomEvent("portal:workbench-before-layout", {
          bubbles: false,
          detail,
        }));
      });
    }

    function render(reason = "render") {
      if (shellState.destroyed) return false;
      shellState.pendingPanelMounts = [];
      const originalParents = new Map(
        [...descriptors.values()].map(descriptor => [descriptor.id, descriptor.element.parentElement]),
      );
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      if (mobileMode && !renderedMobileMode) {
        const focusedStack = findNode(shellState.model, shellState.focusedStackId)?.node;
        if (focusedStack?.type === "stack" && descriptors.has(focusedStack.activePanelId)) {
          shellState.mobileActivePanelId = focusedStack.activePanelId;
        }
      }
      const mobilePanels = [...descriptors.values()].filter(descriptor => !shellState.hidden.has(descriptor.id));
      if (mobileMode && !mobilePanels.some(descriptor => descriptor.id === shellState.mobileActivePanelId)) {
        shellState.mobileActivePanelId = mobilePanels[0]?.id || "";
      } else if (!descriptors.has(shellState.mobileActivePanelId)) {
        shellState.mobileActivePanelId = mobilePanels[0]?.id || "";
      }
      const mobilePlacement = mobileMode ? findPanel(shellState.model, shellState.mobileActivePanelId) : null;
      const renderedModel = mobileMode && shellState.mobileActivePanelId && !mobilePlacement
        ? {
          type: "stack",
          id: `${id}-mobile-projection`,
          activePanelId: shellState.mobileActivePanelId,
          children: [{ type: "panel", id: `${id}-mobile-panel`, panelId: shellState.mobileActivePanelId }],
        }
        : shellState.model;
      announceBeforeLayout(reason);
      const topology = JSON.stringify(topologyOf(renderedModel));
      const mobilePanelId = mobileMode ? shellState.mobileActivePanelId : "";
      const refreshed = !!renderedModel
        && renderedModelReference === shellState.model
        && renderedTopology === topology
        && renderedMobileMode === mobileMode
        && renderedMobilePanelId === mobilePanelId
        && refreshNode(canvas.firstElementChild, renderedModel);
      const previousRoots = [...canvas.children];
      const layoutRoot = refreshed ? canvas.firstElementChild : renderedModel ? renderNode(renderedModel) : renderEmptyState();
      if (!refreshed) canvas.append(layoutRoot);
      const mounts = shellState.pendingPanelMounts;
      shellState.pendingPanelMounts = [];
      let mountComplete = true;
      mounts.forEach(([parent, element]) => {
        if (parent.isConnected && !mountPanelElement(parent, element)) mountComplete = false;
      });
      mountComplete = reconcilePanelMounts(layoutRoot) && mountComplete;
      if (!mountComplete) {
        const restored = restorePanelMounts(originalParents);
        if (restored && layoutRoot && !previousRoots.includes(layoutRoot)) layoutRoot.remove();
        restoreCommittedWorkbenchState();
        renderedModelReference = shellState.model;
        root.dispatchEvent(new CustomEvent("portal:workbench-layout-error", {
          detail: Object.freeze({
            workbenchId: id,
            reason,
            code: restored ? "panel-move-unavailable" : "panel-move-rollback-failed",
          }),
        }));
        console.error(
          `PortalWorkbench ${id} preserved the previous layout because a Panel could not be moved atomically.`,
        );
        return false;
      }
      descriptors.forEach(descriptor => {
        const frame = panelFrameIn(layoutRoot, descriptor.id);
        descriptor.element.hidden = !frame || frame.hidden;
      });
      previousRoots.forEach(previous => { if (previous !== layoutRoot) previous.remove(); });
      renderedModelReference = shellState.model;
      renderedTopology = topology;
      renderedMobileMode = mobileMode;
      renderedMobilePanelId = mobilePanelId;
      commitWorkbenchState();
      customizeButton.title = `${customizeLabel} · ${shellState.activeWorkspaceName}`;
      renderHiddenRail();
      renderMobileSwitcher(mobileMode, mobilePanels);
      renderMenu();
      global.PortalUIPrimitives?.mountIcons?.(stage);
      global.PortalUIPrimitives?.mountIcons?.(toolbar);
      syncLayoutLock();
      reconcilePanelVisibility();
      if (!(mobileMode && ["focus-panel", "responsive-mode"].includes(reason))) persist();
      announceChange(reason);
      return true;
    }

    function renderEmptyState() {
      const empty = document.createElement("section");
      empty.className = "portalWorkbenchEmpty";
      empty.setAttribute("aria-label", window.PortalI18n.t("没有打开的 Panel"));
      const title = document.createElement("strong");
      title.textContent = window.PortalI18n.t("暂无打开的 Panel");
      const hint = document.createElement("span");
      hint.textContent = window.PortalI18n.t("选择一个 Panel 继续工作。");
      const actions = document.createElement("div");
      actions.className = "portalWorkbenchEmptyActions";
      [...shellState.hidden].forEach(panelId => {
        const descriptor = descriptors.get(panelId);
        if (!descriptor) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton is-compact";
        button.dataset.portalIcon = descriptor.icon;
        button.dataset.workbenchEmptyRestore = descriptor.id;
        button.textContent = window.PortalI18n.text`打开 ${descriptor.title}`;
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.toggle-visibility`, event, "empty-state"));
        actions.append(button);
      });
      empty.append(title, hint, actions);
      return empty;
    }

    function renderMobileSwitcher(mobileMode, mobilePanels) {
      mobileSwitcher.replaceChildren();
      mobilePanels.forEach(descriptor => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `portalWorkbenchMobilePanel${descriptor.id === shellState.mobileActivePanelId ? " is-active" : ""}`;
        button.dataset.portalIcon = descriptor.icon;
        button.dataset.portalWorkbenchMobilePanel = descriptor.id;
        button.setAttribute("aria-label", descriptor.title);
        button.setAttribute("aria-pressed", String(descriptor.id === shellState.mobileActivePanelId));
        button.title = descriptor.title;
        const label = document.createElement("span");
        label.textContent = descriptor.title;
        button.append(label);
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.focus`, event, "mobile-switcher"));
        mobileSwitcher.append(button);
      });
      mobileSwitcher.hidden = !mobileMode || !mobilePanels.length;
    }

    return {
      announceBeforeLayout, announceChange, render,
    };
  }

  global.PortalWorkbenchRender = Object.freeze({ createRender, dependencies: DEPENDENCIES });
})(window);
