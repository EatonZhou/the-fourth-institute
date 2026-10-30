(() => {
  "use strict";

  if (window.PortalLineageWorkbenchLayout) return;

  // The workbench layout: the Watchboard's width and splitter, and showing or
  // hiding the navigation and Preview panels.
  const DEPENDENCIES = Object.freeze([
    "$id", "chatElement", "currentLineageLayoutStore", "currentLineageWorkbench", "state",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchLayout is missing: ${missing.join(", ")}`);
    const {
      $id, chatElement, currentLineageLayoutStore, currentLineageWorkbench, state,
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

    function syncNavigationPanelControl() {
      const collapsed = currentLineageWorkbench()?.isPanelCollapsed("navigation") === true;
      document.querySelectorAll("[data-side-collapse-toggle]").forEach(btn => {
        window.PortalWorkbench.layout.syncCollapseButton(btn, collapsed, {
          expand: "展开左侧栏",
          collapse: "折叠左侧栏",
        });
      });
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
      syncPreviewPanelControl, setPreviewPanelVisible, ensurePreviewVisible, bindWatchboardSplitter,
    });
  }

  window.PortalLineageWorkbenchLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
