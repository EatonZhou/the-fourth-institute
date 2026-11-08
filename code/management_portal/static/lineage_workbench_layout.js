(() => {
  "use strict";

  if (window.PortalLineageWorkbenchLayout) return;

  // The workbench layout: the Watchboard's width and splitter, and showing or
  // hiding the navigation and Preview panels.
  const DEPENDENCIES = Object.freeze([
    "$id", "chatElement", "currentActivityRail", "currentLineageLayoutStore", "currentLineageWorkbench",
    "readLineageUiState", "setSideView", "state", "writeLineageUiState",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchLayout is missing: ${missing.join(", ")}`);
    const {
      $id, chatElement, currentActivityRail, currentLineageLayoutStore, currentLineageWorkbench,
      readLineageUiState, setSideView, state, writeLineageUiState,
    } = dependencies;

    function saveLayoutState() {
      currentLineageLayoutStore().write({
        watchboard: Math.round(state.layout.watchboard),
      });
    }

    function watchboardWidthLimits() {
      const body = chatElement("body");
      const width = body?.getBoundingClientRect().width || 900;
      return {
        min: 280,
        max: Math.max(320, Math.min(1280, Math.round(width * 0.60))),
      };
    }

    function applyWatchboardLayout({ persist = false } = {}) {
      const shell = $id("consoleShell");
      if (!shell) return;
      const limits = watchboardWidthLimits();
      state.layout.watchboard = window.PortalUI.clampNumber(state.layout.watchboard, limits.min, limits.max);
      shell.style.setProperty("--watchboard-dashboard-w", `${Math.round(state.layout.watchboard)}px`);
      if (persist) saveLayoutState();
    }

    // The activity rail opens and closes the navigation panel: its current
    // view is marked only while the panel is docked open or slid out.
    function syncNavigationPanelControl() {
      const workbench = currentLineageWorkbench();
      const open = workbench?.isPanelExpanded("navigation") === true || workbench?.panelFlyout?.() === "navigation";
      currentActivityRail()?.setActiveView?.(open ? state.sideView || "agents" : "");
    }

    // Pinned, the navigation panel is docked and a rail click opens or
    // closes it. Unpinned (the default on devices that can hover), the panel
    // stays collapsed to the rail and slides out over the page while the
    // mouse is on a rail view or on the panel, like the Mailbox. Devices
    // without hover always behave as pinned.
    const FLYOUT_OPEN_DELAY = 120;
    const FLYOUT_CLOSE_DELAY = 280;
    let flyoutOpenTimer = 0;
    let flyoutCloseTimer = 0;
    let flyoutTracking = false;

    function canHover() {
      return window.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches === true
        && window.PortalResponsive?.isMobile?.() !== true;
    }

    function navigationPinned() {
      return !canHover() || readLineageUiState().navigationPinned === true;
    }

    function navigationFlyoutOpen() {
      return currentLineageWorkbench()?.panelFlyout?.() === "navigation";
    }

    function navigationStack() {
      return [...document.querySelectorAll(".portalWorkbenchStack")]
        .find(stack => stack.querySelector('[data-portal-workbench-panel="navigation"]')) || null;
    }

    // The panel stays open while the user is working in it: typing a search,
    // or with a row menu open.
    function flyoutHeld() {
      const stack = navigationStack();
      const focused = document.activeElement;
      if (stack && focused && stack.contains(focused) && focused.matches("input, textarea")) return true;
      return Boolean(state.agentMenuId);
    }

    function pointerOnFlyout(target) {
      return target instanceof Element && Boolean(
        target.closest(".portalActivityRail") || navigationStack()?.contains(target),
      );
    }

    function trackFlyoutPointer(event) {
      if (!navigationFlyoutOpen()) return;
      if (pointerOnFlyout(event.target)) {
        window.clearTimeout(flyoutCloseTimer);
        flyoutCloseTimer = 0;
      } else if (!flyoutCloseTimer) {
        flyoutCloseTimer = window.setTimeout(() => {
          flyoutCloseTimer = 0;
          if (!flyoutHeld()) closeNavigationFlyout();
        }, FLYOUT_CLOSE_DELAY);
      }
    }

    function dismissFlyoutOutside(event) {
      if (!navigationFlyoutOpen() || pointerOnFlyout(event.target) || state.agentMenuId) return;
      if (event.target instanceof Element && event.target.closest(".portalOverlay, [role='dialog'], [role='menu']")) return;
      closeNavigationFlyout();
    }

    function dismissFlyoutOnEscape(event) {
      if (event.key !== "Escape" || !navigationFlyoutOpen() || event.defaultPrevented) return;
      closeNavigationFlyout();
    }

    function startFlyoutTracking() {
      if (flyoutTracking) return;
      flyoutTracking = true;
      document.addEventListener("pointermove", trackFlyoutPointer, { passive: true });
      document.addEventListener("pointerdown", dismissFlyoutOutside, true);
      document.addEventListener("keydown", dismissFlyoutOnEscape);
    }

    function stopFlyoutTracking() {
      if (!flyoutTracking) return;
      flyoutTracking = false;
      document.removeEventListener("pointermove", trackFlyoutPointer);
      document.removeEventListener("pointerdown", dismissFlyoutOutside, true);
      document.removeEventListener("keydown", dismissFlyoutOnEscape);
    }

    // The slid-out panel floats over the page; the layout keeps it collapsed.
    function collapseNavigationToRail() {
      const workbench = currentLineageWorkbench();
      if (!workbench) return false;
      if (!workbench.isPanelVisible("navigation")) workbench.showPanel("navigation");
      if (workbench.isPanelExpanded("navigation")) workbench.collapsePanel("navigation");
      return true;
    }

    function openNavigationFlyout(view = "") {
      window.clearTimeout(flyoutOpenTimer);
      window.clearTimeout(flyoutCloseTimer);
      flyoutOpenTimer = 0;
      flyoutCloseTimer = 0;
      if (view && view !== state.sideView) setSideView(view, { userInitiated: true });
      if (navigationPinned()) {
        setNavigationPanelVisible(true);
        return;
      }
      if (!collapseNavigationToRail()) return;
      currentLineageWorkbench().setPanelFlyout("navigation", true);
      startFlyoutTracking();
      syncNavigationPanelControl();
    }

    function closeNavigationFlyout() {
      window.clearTimeout(flyoutOpenTimer);
      window.clearTimeout(flyoutCloseTimer);
      flyoutOpenTimer = 0;
      flyoutCloseTimer = 0;
      stopFlyoutTracking();
      if (!navigationFlyoutOpen()) return;
      currentLineageWorkbench().setPanelFlyout("navigation", false);
      syncNavigationPanelControl();
    }

    // Rail hover: slide out after a short pause, or switch views at once
    // while the panel is already out.
    function hoverNavigationView(view) {
      if (navigationPinned()) return;
      window.clearTimeout(flyoutOpenTimer);
      if (navigationFlyoutOpen()) {
        openNavigationFlyout(view);
        return;
      }
      flyoutOpenTimer = window.setTimeout(() => {
        flyoutOpenTimer = 0;
        openNavigationFlyout(view);
      }, FLYOUT_OPEN_DELAY);
    }

    // Passing over a view on the way elsewhere does not slide it out.
    function leaveNavigationView() {
      window.clearTimeout(flyoutOpenTimer);
      flyoutOpenTimer = 0;
    }

    // Rail click: pinned, it opens the view or closes the docked panel;
    // unpinned, it slides the view out or back.
    function selectNavigationView(view, { repeated = false } = {}) {
      if (window.PortalResponsive?.isMobile?.() === true) {
        setSideView(view, { userInitiated: true });
        return;
      }
      if (!navigationPinned()) {
        if (navigationFlyoutOpen() && view === state.sideView) closeNavigationFlyout();
        else openNavigationFlyout(view);
        return;
      }
      if (repeated && currentLineageWorkbench()?.isPanelExpanded("navigation")) setNavigationPanelVisible(false);
      else {
        setSideView(view, { userInitiated: true });
        setNavigationPanelVisible(true);
      }
    }

    function setNavigationPinned(pinned) {
      writeLineageUiState({ navigationPinned: pinned === true });
      closeNavigationFlyout();
      if (pinned) setNavigationPanelVisible(true);
      else {
        collapseNavigationToRail();
        syncNavigationPanelControl();
      }
    }

    // Shows the navigation the way the pin says: docked, or slid out.
    function revealNavigation(view = "") {
      if (navigationPinned()) {
        if (view) setSideView(view, { userInitiated: true });
        setNavigationPanelVisible(true);
      } else openNavigationFlyout(view);
    }

    // On load an unpinned panel starts collapsed to the rail.
    function applyNavigationPin() {
      if (!navigationPinned()) collapseNavigationToRail();
      syncNavigationPanelControl();
    }

    function setNavigationPanelVisible(visible) {
      if (!currentLineageWorkbench()) return;
      if (visible) {
        if (currentLineageWorkbench().isPanelVisible("navigation")) currentLineageWorkbench().expandPanel("navigation");
        else currentLineageWorkbench().showPanel("navigation");
      } else currentLineageWorkbench().collapsePanel("navigation");
      syncNavigationPanelControl();
    }

    function syncPreviewPanelControl() {
      const collapsed = currentLineageWorkbench()?.isPanelCollapsed("preview") === true;
      const collapseBtn = $id("previewCollapseBtn");
      window.PortalWorkbench.layout.syncCollapseButton(collapseBtn, collapsed, {
        expand: "展开 Preview",
        collapse: "折叠 Preview",
      });
    }

    function setPreviewPanelVisible(visible) {
      if (!currentLineageWorkbench()) return;
      if (visible) {
        if (currentLineageWorkbench().isPanelVisible("preview")) currentLineageWorkbench().expandPanel("preview");
        else currentLineageWorkbench().showPanel("preview");
      } else currentLineageWorkbench().collapsePanel("preview");
      syncPreviewPanelControl();
    }

    function ensurePreviewVisible() {
      if (!currentLineageWorkbench()?.isPanelExpanded("preview")) setPreviewPanelVisible(true);
    }

    function bindWatchboardSplitter() {
      const handle = chatElement("watchboardSplitter");
      if (!handle) return;
      if (handle.dataset.watchboardSplitterBound === "1") return;
      window.PortalWorkbench.layout.bindSplitter({
        handle,
        boundKey: "watchboardSplitterBound",
        disabled: () => window.matchMedia("(max-width: 1180px)").matches,
        direction: -1,
        min: () => watchboardWidthLimits().min,
        max: () => watchboardWidthLimits().max,
        readSize: () => state.layout.watchboard,
        applySize: width => {
          state.layout.watchboard = width;
          applyWatchboardLayout();
        },
        onDone: () => applyWatchboardLayout({ persist: true }),
      });
    }

    return Object.freeze({
      applyWatchboardLayout, syncNavigationPanelControl, setNavigationPanelVisible,
      navigationPinned, setNavigationPinned, hoverNavigationView, leaveNavigationView, selectNavigationView, revealNavigation,
      applyNavigationPin, closeNavigationFlyout,
      syncPreviewPanelControl, setPreviewPanelVisible, ensurePreviewVisible, bindWatchboardSplitter,
    });
  }

  window.PortalLineageWorkbenchLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
