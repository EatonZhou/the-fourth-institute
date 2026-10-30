(() => {
  "use strict";

  if (window.PortalWorkspaceSurfacePanelChrome) return;

  // The chrome of a Preview panel: the ids scoped to the panel, its markup
  // and the workspace browser it shows documents in, with the back button
  // among the tab actions.

  const DEPENDENCIES = Object.freeze([
    "defaultPreviewPanelIds", "defaultWorkspacePreviewIds", "escapeHtml", "mountPreviewWorkspaceSurface", "options",
    "root", "workspacePreviewActionKeys",
  ]);

  function createPanelChrome(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfacePanelChrome is missing: ${missing.join(", ")}`);
    const {
      defaultPreviewPanelIds, defaultWorkspacePreviewIds, escapeHtml, mountPreviewWorkspaceSurface, options, root,
      workspacePreviewActionKeys,
    } = dependencies;

    const scope = String(options.panelId || options.id || `preview-${Date.now().toString(36)}`)
      .replace(/[^a-zA-Z0-9_-]+/g, "-");
    const scopedId = name => `${scope}-${name}`;
    const generatedSurfaceIds = Object.fromEntries(
      Object.keys({ ...defaultPreviewPanelIds, ...defaultWorkspacePreviewIds })
        .map(key => [key, scopedId(key)]),
    );
    const generatedActionIds = Object.fromEntries(
      workspacePreviewActionKeys.map(key => [key, scopedId(`${key}-action`)]),
    );
    const ids = Object.freeze({
      title: scopedId("title"),
      path: scopedId("path"),
      back: scopedId("back"),
      collapse: scopedId("collapse"),
      browser: scopedId("browser"),
      ...(options.panelIds || {}),
    });
    root.className = "pane previewPane portalWorkspacePreviewPane portalWorkbenchPreviewPanel";
    root.setAttribute("aria-label", options.label || window.PortalI18n.t("Explorer Preview"));
    root.innerHTML = window.PortalI18n.html`
      <span id="${escapeHtml(ids.title)}" hidden>${escapeHtml(options.heading || window.PortalI18n.t("Node Workspaces"))}</span>
      <div class="portalWorkspacePreviewBrowser" id="${escapeHtml(ids.browser)}"></div>
      ${options.footerHtml || ""}`;
    const surface = mountPreviewWorkspaceSurface({
      ...options,
      ...(options.surface || {}),
      ids: {
        ...generatedSurfaceIds,
        ...(options.surface?.ids || {}),
        toolbarState: ids.path,
      },
      actionIds: { ...generatedActionIds, ...(options.surface?.actionIds || options.actionIds || {}) },
      tabActions: [
        ...(Array.isArray(options.surface?.tabActions) ? options.surface.tabActions : []),
        {
          id: ids.back,
          icon: "arrow-left",
          label: window.PortalI18n.t("返回上一个预览文件"),
          title: window.PortalI18n.t("返回上一个预览文件"),
          disabled: true,
          className: "portalPreviewHistoryBack",
        },
      ],
      root: root.querySelector(`#${CSS.escape(ids.browser)}`),
    });

    return {
      ids, surface,
    };
  }

  window.PortalWorkspaceSurfacePanelChrome = Object.freeze({ createPanelChrome, dependencies: DEPENDENCIES });
})();
