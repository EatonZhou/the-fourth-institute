(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceBrowserMarkup) return;

  // The markup of a workspace browser: its ids and action ids, the file
  // tree section when the browser has one, and the document surface with
  // the Preview toolbar's save, history, wrap, open, download, refresh and
  // mode actions.

  const DEPENDENCIES = Object.freeze([
    "defaultPreviewPanelIds", "defaultWorkspacePreviewIds", "escapeHtml", "mountPreviewDocumentSurface", "options",
    "root", "surfaceElement", "workspacePreviewActionId",
  ]);

  function createBrowserMarkup(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceBrowserMarkup is missing: ${missing.join(", ")}`);
    const {
      defaultPreviewPanelIds, defaultWorkspacePreviewIds, escapeHtml, mountPreviewDocumentSurface, options, root,
      surfaceElement, workspacePreviewActionId,
    } = dependencies;

    const ids = Object.freeze({
      ...defaultPreviewPanelIds,
      ...defaultWorkspacePreviewIds,
      ...(options.ids || {}),
    });
    const actionId = key => workspacePreviewActionId(options, key);
    const openControls = String(options.openControls || "").trim();
    const explorerEnabled = options.explorer !== false;
    root.className = `portalWorkspacePreviewBrowser${explorerEnabled ? "" : " is-document-only"}`;
    root.innerHTML = window.PortalI18n.html`
      ${explorerEnabled ? window.PortalI18n.html`<section class="portalWorkspacePreviewExplorerPanel" id="${escapeHtml(ids.explorerPanel)}">
        <div class="portalWorkspacePreviewExplorerToolbar">
          <span id="${escapeHtml(ids.explorerTitle)}">${escapeHtml(options.explorerTitle || window.PortalI18n.t("工作区与文件夹"))}</span>
          <div class="portalWorkspacePreviewExplorerActions">
            <span class="portalWorkspacePreviewExplorerCount" id="${escapeHtml(ids.explorerCount)}">0</span>
            <button class="portalButton is-icon is-quiet iconBtn portalWorkspacePreviewSectionCollapse" data-portal-icon="chevron-down" id="${escapeHtml(ids.explorerExpand)}" type="button" aria-label="向下展开文件树" title="向下展开文件树" aria-pressed="true" aria-controls="${escapeHtml(ids.explorerPanel)}" hidden></button>
            <button class="portalButton is-icon is-quiet iconBtn portalWorkspacePreviewSectionCollapse" data-portal-icon="chevron-up" id="${escapeHtml(ids.explorerCollapse)}" type="button" aria-label="向上折叠文件树" title="向上折叠文件树" aria-pressed="false" aria-controls="${escapeHtml(ids.explorerPanel)}"></button>
          </div>
        </div>
        <div class="portalResourceIdentityId portalWorkspacePreviewIdentity" id="${escapeHtml(ids.explorerIdentity)}">—</div>
        <div class="portalWorkspacePreviewExplorerTree" id="${escapeHtml(ids.explorer)}"></div>
      </section>
      <div class="portalWorkspacePreviewSplitter" id="${escapeHtml(ids.splitter)}" role="separator" aria-orientation="horizontal" aria-label="调整工作区文件树与 Preview 高度" title="拖动调整工作区文件树与 Preview 高度" tabindex="0"></div>` : ""}
      <div class="portalPreviewPanel" id="${escapeHtml(ids.previewPanel)}"></div>`;
    const component = mountPreviewDocumentSurface({
      ...options,
      root: surfaceElement(root, ids.previewPanel),
      ids,
      className: "portalWorkspacePreviewDocument",
      previewClass: "",
      title: options.title || window.PortalI18n.t("文件 Preview"),
      actions: [
        ...(Array.isArray(options.toolbarActions) ? options.toolbarActions : []),
        { id: actionId("save"), icon: "save", label: window.PortalI18n.t("保存当前文件"), priority: "critical", disabled: true },
        { type: "divider", priority: "normal" },
        { id: actionId("undo"), icon: "undo", label: window.PortalI18n.t("撤销"), priority: "critical", disabled: true },
        { id: actionId("redo"), icon: "redo", label: window.PortalI18n.t("重做"), priority: "critical", disabled: true },
        { id: actionId("wrap"), icon: "word-wrap", label: window.PortalI18n.t("自动换行"), title: window.PortalI18n.t("关闭自动换行"), priority: "critical", pressed: true, disabled: true, className: "portalPreviewWordWrapButton" },
        { id: actionId("open"), icon: "file-open-new", label: window.PortalI18n.t("在大视图打开当前文件"), controls: openControls || undefined, priority: "critical", disabled: true },
        { type: "link", id: actionId("download"), icon: "download", label: window.PortalI18n.t("下载当前文件"), priority: "normal", hidden: true, className: "portalPreviewDownloadButton" },
        { id: actionId("refresh"), icon: "refresh", label: window.PortalI18n.t("刷新当前文件"), priority: "critical", disabled: true },
        { type: "divider", priority: "normal" },
        { type: "mode", id: actionId("code"), text: window.PortalI18n.t("Edit"), label: window.PortalI18n.t("编辑当前文件的源码"), priority: "critical", disabled: true },
        { type: "mode", id: actionId("visual"), text: "Visual", label: window.PortalI18n.t("以只读视觉模式查看当前文件"), priority: "critical", active: true, disabled: true },
      ],
      headActions: explorerEnabled ? [
        { id: ids.previewCollapse, icon: "chevron-down", label: window.PortalI18n.t("向下折叠文件 Preview"), controls: `${ids.tabsShell} ${ids.toolbar} ${ids.preview}`, pressed: false, className: "portalWorkspacePreviewSectionCollapse" },
      ] : [],
      emptyHtml: options.emptyHtml || window.PortalI18n.markup('<div class="previewWelcome"><span data-portal-icon="file-text" aria-hidden="true"></span><span>选择成果文件后显示内容。</span></div>'),
    });

    return {
      actionId, component, explorerEnabled, ids,
    };
  }

  window.PortalWorkspaceSurfaceBrowserMarkup = Object.freeze({ createBrowserMarkup, dependencies: DEPENDENCIES });
})();
