(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceBrowserExplorer) return;

  // The file tree section of a workspace browser: the stored layout,
  // folding the tree and the Preview, the tree's height between its limits,
  // the splitter between them and following window resizes.

  const DEPENDENCIES = Object.freeze([
    "ids", "options", "root", "surfaceElement",
  ]);

  function createBrowserExplorer(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceBrowserExplorer is missing: ${missing.join(", ")}`);
    const {
      ids, options, root, surfaceElement,
    } = dependencies;

    const layoutStore = options.layoutStore || null;
    const layoutKeys = Object.freeze({
      explorerCollapsed: "workspacePreviewExplorerCollapsed",
      previewCollapsed: "workspacePreviewDocumentCollapsed",
      explorerHeight: "workspacePreviewExplorerHeight",
      ...(options.layoutKeys || {}),
    });
    const explorerPanel = surfaceElement(root, ids.explorerPanel);
    const splitter = surfaceElement(root, ids.splitter);
    const minimumExplorerHeight = Math.max(80, Number(options.minimumExplorerHeight || 110));
    const minimumPreviewHeight = Math.max(80, Number(options.minimumPreviewHeight || 110));
    const splitterHeight = 8;
    const readLayout = () => layoutStore?.read?.() || {};
    const writeLayout = patch => layoutStore?.write?.(patch || {});
    const explorerHeight = () => {
      const configured = Number.parseFloat(root.style.getPropertyValue("--portal-workspace-preview-explorer-h") || "");
      if (Number.isFinite(configured) && configured > 0) return configured;
      const measured = explorerPanel?.getBoundingClientRect().height || 0;
      return measured || Math.max(minimumExplorerHeight, root.clientHeight * 0.4);
    };
    const maximumExplorerHeight = () => root.clientHeight
      ? Math.max(minimumExplorerHeight, root.clientHeight - splitterHeight - minimumPreviewHeight)
      : Number.POSITIVE_INFINITY;
    const applyExplorerHeight = height => {
      const numeric = Number(height);
      const requested = Number.isFinite(numeric) ? numeric : explorerHeight();
      const applied = Math.round(Math.max(minimumExplorerHeight, Math.min(maximumExplorerHeight(), requested)));
      root.style.setProperty("--portal-workspace-preview-explorer-h", `${applied}px`);
      return applied;
    };
    const syncFoldControls = () => {
      const explorerCollapsed = root.classList.contains("is-explorer-collapsed");
      const previewCollapsed = root.classList.contains("is-preview-collapsed");
      const explorerCollapse = surfaceElement(root, ids.explorerCollapse);
      window.PortalWorkbench?.layout?.syncCollapseButton?.(
        explorerCollapse,
        explorerCollapsed,
        { expand: "向下展开文件树", collapse: "向上折叠文件树" },
      );
      const explorerExpand = surfaceElement(root, ids.explorerExpand);
      window.PortalWorkbench?.layout?.syncCollapseButton?.(
        explorerExpand,
        explorerCollapsed,
        { expand: "向下展开文件树", collapse: "向上折叠文件树" },
      );
      if (explorerCollapse) explorerCollapse.hidden = explorerCollapsed;
      if (explorerExpand) explorerExpand.hidden = !explorerCollapsed;
      window.PortalWorkbench?.layout?.syncCollapseButton?.(
        surfaceElement(root, ids.previewCollapse),
        previewCollapsed,
        { expand: "向上展开文件 Preview", collapse: "向下折叠文件 Preview" },
      );
    };
    const setSectionCollapsed = (section, collapsed, persist = true) => {
      const key = section === "explorer" ? layoutKeys.explorerCollapsed : layoutKeys.previewCollapsed;
      root.classList.toggle(section === "explorer" ? "is-explorer-collapsed" : "is-preview-collapsed", collapsed === true);
      syncFoldControls();
      if (persist) writeLayout({ [key]: collapsed === true });
      if (!collapsed) window.requestAnimationFrame(() => applyExplorerHeight(readLayout()[layoutKeys.explorerHeight]));
      options.onLayoutChange?.({ section, collapsed: collapsed === true });
    };
    const toggleSection = section => {
      const className = section === "explorer" ? "is-explorer-collapsed" : "is-preview-collapsed";
      setSectionCollapsed(section, !root.classList.contains(className));
    };
    const applyLayout = (layout = readLayout()) => {
      const storedExplorerCollapsed = layout[layoutKeys.explorerCollapsed];
      setSectionCollapsed("explorer", storedExplorerCollapsed === undefined ? true : storedExplorerCollapsed === true, false);
      setSectionCollapsed("preview", layout[layoutKeys.previewCollapsed] === true, false);
      window.requestAnimationFrame(() => applyExplorerHeight(layout[layoutKeys.explorerHeight]));
    };
    surfaceElement(root, ids.explorerCollapse)?.addEventListener("click", () => toggleSection("explorer"));
    surfaceElement(root, ids.explorerExpand)?.addEventListener("click", () => toggleSection("explorer"));
    surfaceElement(root, ids.previewCollapse)?.addEventListener("click", () => toggleSection("preview"));
    const unbindSplitter = window.PortalWorkbench?.layout?.bindSplitter?.({
      root,
      handle: splitter,
      axis: "y",
      bodyClass: "resizingWorkspacePreview",
      min: minimumExplorerHeight,
      max: maximumExplorerHeight,
      readSize: explorerHeight,
      applySize: applyExplorerHeight,
      store: layoutStore,
      stateKey: layoutKeys.explorerHeight,
      disabled: () => root.classList.contains("is-explorer-collapsed") || root.classList.contains("is-preview-collapsed"),
    }) || (() => {});
    const handleResize = () => {
      if (root.clientHeight) applyExplorerHeight(readLayout()[layoutKeys.explorerHeight]);
    };
    window.addEventListener("resize", handleResize);

    return {
      applyLayout, handleResize, setSectionCollapsed, toggleSection, unbindSplitter,
    };
  }

  window.PortalWorkspaceSurfaceBrowserExplorer = Object.freeze({ createBrowserExplorer, dependencies: DEPENDENCIES });
})();
