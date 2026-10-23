(() => {
  "use strict";

  if (window.PortalWorkspaceSurface) return;

  function resolveRoot(root) {
    if (typeof root === "string") return document.querySelector(root);
    return root || null;
  }

  // Workbench panels are built before their roots are attached to the document.
  // Keep lookups inside the owning surface, including when multiple panels exist.
  function surfaceElement(root, id) {
    return id ? root.querySelector(`#${CSS.escape(String(id))}`) : null;
  }

  const escapeHtml = window.PortalUIPrimitives.escapeHtml;

  function renderExplorer(options = {}) {
    return window.PortalFileTree?.render?.(options);
  }

  const defaultPreviewPanelIds = Object.freeze({
    sectionHead: "portalPreviewSectionHead",
    tabsShell: "portalPreviewTabsShell",
    tabs: "portalPreviewTabs",
    pin: "portalPreviewPinBtn",
    toolbar: "portalPreviewToolbar",
    toolbarState: "portalPreviewToolbarState",
    preview: "portalPreviewBox",
    selectionToolbar: "portalPreviewSelectionToolbar",
  });

  function previewToolbarActionHtml(action = {}) {
    const type = String(action.type || "button");
    if (type === "divider") {
      return window.PortalI18n.html`<span class="portalDocumentToolbarDivider" data-portal-toolbar-item data-portal-toolbar-priority="${escapeHtml(action.priority || "normal")}"></span>`;
    }
    if (type === "form") {
      return window.PortalI18n.html`<form class="previewOpenForm" id="${escapeHtml(action.id)}" data-portal-toolbar-item data-portal-toolbar-priority="${escapeHtml(action.priority || "low")}"><input class="previewOpenInput" id="${escapeHtml(action.inputId)}" type="text" autocomplete="off" spellcheck="false" placeholder="${escapeHtml(action.placeholder || window.PortalI18n.t("path or text Resource ID"))}" aria-label="${escapeHtml(action.label || action.placeholder || window.PortalI18n.t("打开文件"))}"></form>`;
    }
    const tag = type === "link" ? "a" : "button";
    const mode = type === "mode";
    const classes = mode
      ? `portalDocumentMode${action.active ? " active" : ""}`
      : `portalButton is-icon is-quiet iconBtn${action.className ? ` ${escapeHtml(action.className)}` : ""}`;
    const attributes = [
      `class="${classes}"`,
      `id="${escapeHtml(action.id)}"`,
      `data-portal-toolbar-item`,
      `data-portal-toolbar-priority="${escapeHtml(action.priority || "normal")}"`,
      `aria-label="${escapeHtml(action.label || action.title || action.id)}"`,
      `title="${escapeHtml(action.title || action.label || "")}"`,
    ];
    if (tag === "button") attributes.push(`type="button"`);
    if (action.icon) attributes.push(`data-portal-icon="${escapeHtml(action.icon)}"`);
    if (mode || action.pressed !== undefined) attributes.push(`aria-pressed="${action.active || action.pressed ? "true" : "false"}"`);
    if (action.controls) attributes.push(`aria-controls="${escapeHtml(action.controls)}"`);
    if (action.disabled === true) attributes.push("disabled");
    if (action.hidden) attributes.push("hidden");
    return `<${tag} ${attributes.join(" ")}>${mode ? escapeHtml(action.text || action.label || "") : ""}</${tag}>`;
  }

  function previewTabActionHtml(action = {}) {
    const attributes = [
      `class="portalDocumentTabsAction${action.className ? ` ${escapeHtml(action.className)}` : ""}"`,
      `id="${escapeHtml(action.id)}"`,
      `type="button"`,
      `aria-label="${escapeHtml(action.label || action.title || action.id)}"`,
      `title="${escapeHtml(action.title || action.label || "")}"`,
    ];
    if (action.icon) attributes.push(`data-portal-icon="${escapeHtml(action.icon)}"`);
    if (action.pressed !== undefined) attributes.push(`aria-pressed="${action.pressed ? "true" : "false"}"`);
    if (action.disabled === true) attributes.push("disabled");
    if (action.hidden === true) attributes.push("hidden");
    return window.PortalI18n.html`<button ${attributes.join(" ")}></button>`;
  }

  function mountPreviewDocumentSurface(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new Error("Preview document surface root required");
    const ids = Object.freeze({ ...defaultPreviewPanelIds, ...(options.ids || {}) });
    const actions = Array.isArray(options.actions) ? options.actions : [];
    const headActions = Array.isArray(options.headActions) ? options.headActions : [];
    const tabActions = Array.isArray(options.tabActions) ? options.tabActions : [];
    const initialState = String(options.emptyState || "Select a file from File tree");
    root.className = [
      "portalPreviewPanel",
      "portalPreviewPanelComponent",
      options.className || "",
    ].filter(Boolean).join(" ");
    root.innerHTML = window.PortalI18n.html`
      <div class="portalPreviewPanelSectionHead" id="${escapeHtml(ids.sectionHead)}">
        <div class="portalPreviewPanelSectionTitle">
          ${headActions.filter(action => action.placement === "before").map(previewToolbarActionHtml).join("")}
          <span>${escapeHtml(options.title || window.PortalI18n.t("文件 Preview"))}</span>
        </div>
        <div class="portalPreviewPanelSectionActions">${headActions.filter(action => action.placement !== "before").map(previewToolbarActionHtml).join("")}</div>
      </div>
      <div class="portalDocumentTabsShell" id="${escapeHtml(ids.tabsShell)}" hidden>
        <div class="portalDocumentTabs" id="${escapeHtml(ids.tabs)}" role="tablist" aria-label="Preview 中打开的文件"></div>
        ${tabActions.map(previewTabActionHtml).join("")}
        <button class="portalDocumentTabsAction" data-portal-icon="pin" id="${escapeHtml(ids.pin)}" type="button" aria-label="锁定当前文件" title="锁定当前文件" aria-pressed="false" disabled></button>
      </div>
      <div class="portalDocumentToolbar" id="${escapeHtml(ids.toolbar)}">
        <div class="portalDocumentToolbarTools" data-portal-toolbar data-portal-toolbar-overflow="wrap">${actions.map(previewToolbarActionHtml).join("")}</div>
        <div class="portalDocumentToolbarState portalPreviewDocumentPath" id="${escapeHtml(ids.toolbarState)}" title="${escapeHtml(initialState)}">${escapeHtml(initialState)}</div>
      </div>
      <div data-preview-read-error hidden></div>
      <div class="previewBox${options.previewClass ? ` ${escapeHtml(options.previewClass)}` : ""}" id="${escapeHtml(ids.preview)}">${options.emptyHtml || escapeHtml(options.emptyText || window.PortalI18n.t("选择文件后显示内容。"))}</div>
      <div class="portalPreviewStatus" hidden><span data-preview-stat="words" title="中文逐字、英文按词；不计标点和空白"></span></div>
      <button class="portalPreviewSelectionToolbar" id="${escapeHtml(ids.selectionToolbar)}" type="button" hidden>${escapeHtml(options.selectionLabel || window.PortalI18n.t("发送到对话"))}</button>`;
    const statistics = root.querySelector(".portalPreviewStatus");
    const toolbarState = surfaceElement(root, ids.toolbarState);
    const setToolbarValue = (value, { path = false } = {}) => {
      if (!toolbarState) return "";
      const text = String(value || "");
      toolbarState.textContent = text;
      if (path && text) {
        toolbarState.dataset.portalPreviewPath = text;
        toolbarState.title = window.PortalI18n.text`${text}\n双击复制文件路径`;
        toolbarState.setAttribute("aria-label", window.PortalI18n.text`当前文件路径：${text}。双击复制`);
      } else {
        delete toolbarState.dataset.portalPreviewPath;
        toolbarState.title = text;
        toolbarState.removeAttribute("aria-label");
      }
      return text;
    };
    toolbarState?.addEventListener("dblclick", event => {
      const path = String(toolbarState.dataset.portalPreviewPath || "").trim();
      if (!path) return;
      event.preventDefault();
      event.stopPropagation();
      const copyText = options.copyText || window.PortalUI?.copyText;
      if (typeof copyText === "function") void Promise.resolve(copyText(path, window.PortalI18n.t("文件路径")));
    });
    const component = Object.freeze({
      root,
      ids,
      element: key => surfaceElement(root, ids[key]) || null,
      setState(value) {
        statistics.hidden = true;
        return setToolbarValue(value);
      },
      setDocumentStatistics(document) {
        const textDocument = document && window.PortalPreviewContract.isTextDocument(document);
        statistics.hidden = !textDocument;
        statistics.querySelector("[data-preview-stat=words]").textContent = textDocument
          ? window.PortalI18n.text`字数 ${window.PortalDocumentStatistics.wordCount(document.content).toLocaleString()}`
          : "";
      },
      setPath(value) {
        return setToolbarValue(value, { path: true });
      },
      setReadError(value) {
        const notice = root.querySelector("[data-preview-read-error]");
        const message = String(value || "");
        if (notice.dataset.error === message) return;
        notice.dataset.error = message;
        notice.hidden = !message;
        if (!message) notice.replaceChildren();
        else window.PortalUIPrimitives.renderAsyncState(notice, {
          state: "error",
          title: window.PortalI18n.t("刷新失败，当前显示内容尚未更新。"),
          message,
          density: "compact",
        });
      },
      setDownload(url, filename = "") {
        const link = root.querySelector(".portalPreviewDownloadButton");
        return window.PortalPreview.configureDownloadLink(link, { url, filename });
      },
      destroy() { root.replaceChildren(); },
    });
    root.__portalPreviewPanelComponent = component;
    return component;
  }

  const workspacePreviewActionKeys = Object.freeze([
    "save", "undo", "redo", "wrap", "open",
    "download", "refresh", "code", "visual",
  ]);

  function workspacePreviewActionId(options, key) {
    const configured = options.actionIds && typeof options.actionIds === "object"
      ? options.actionIds[key]
      : "";
    return String(configured || `portalPreview${key[0].toUpperCase()}${key.slice(1)}Btn`);
  }

  const defaultWorkspacePreviewIds = Object.freeze({
    previewPanel: "portalWorkspacePreviewDocument",
    explorerPanel: "portalWorkspacePreviewExplorerPanel",
    explorerTitle: "portalWorkspacePreviewExplorerTitle",
    explorerCount: "portalWorkspacePreviewExplorerCount",
    explorerCollapse: "portalWorkspacePreviewExplorerCollapse",
    explorerExpand: "portalWorkspacePreviewExplorerExpand",
    explorerIdentity: "portalWorkspacePreviewExplorerIdentity",
    explorer: "portalWorkspacePreviewExplorer",
    splitter: "portalWorkspacePreviewSplitter",
    previewCollapse: "portalWorkspacePreviewDocumentCollapse",
  });

  /** Private full-featured surface assembled only by mountWorkbenchPreviewPanel. */
  function mountPreviewWorkspaceSurface(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new Error("Preview workspace surface root required");
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
    if (!explorerEnabled) {
      return Object.freeze({
        ...component,
        root,
        previewRoot: component.root,
        actionKeys: workspacePreviewActionKeys,
        action: key => surfaceElement(root, actionId(key)) || null,
      });
    }
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
    applyLayout();
    return Object.freeze({
      ...component,
      root,
      previewRoot: component.root,
      explorer: surfaceElement(root, ids.explorer),
      actionKeys: workspacePreviewActionKeys,
      action: key => surfaceElement(root, actionId(key)) || null,
      applyLayout,
      setSectionCollapsed,
      toggleSection,
      setExplorerMeta(meta = {}) {
        const count = surfaceElement(root, ids.explorerCount);
        const identity = surfaceElement(root, ids.explorerIdentity);
        if (count && meta.count !== undefined) count.textContent = String(meta.count ?? 0);
        if (identity && meta.identity !== undefined) {
          const value = String(meta.identity || "").trim();
          identity.textContent = value || String(meta.placeholder || "—");
          if (value) {
            identity.dataset.portalResourceId = value;
            identity.dataset.portalResourceCopyLabel = String(meta.copyLabel || "workspace_id");
            identity.title = window.PortalI18n.text`双击复制 ${identity.dataset.portalResourceCopyLabel}`;
          } else {
            delete identity.dataset.portalResourceId;
            delete identity.dataset.portalResourceCopyLabel;
            identity.removeAttribute("title");
          }
        }
      },
      destroy() {
        unbindSplitter();
        window.removeEventListener("resize", handleResize);
        root.replaceChildren();
      },
    });
  }

  /**
   * Lifecycle adapter for page-owned domain content. Workbench still creates
   * every editor through a factory; this adapter only transfers an already
   * authored domain view into that lifecycle and restores it on destroy.
   */
  function mountAuthoredPanel(options = {}) {
    const mount = resolveRoot(options.root);
    const source = resolveRoot(options.source);
    if (!mount || !source) throw new TypeError("PortalWorkspaceSurface.mountAuthoredPanel requires root and source");
    const originalParent = source.parentNode;
    const originalNextSibling = source.nextSibling;
    const runtime = Object.freeze({
      root: source,
      element: source,
      snapshot: () => ({}),
      restore() {},
      show() { source.hidden = false; },
      hide() {},
      destroy() {
        source.hidden = false;
        if (!originalParent) return;
        if (originalNextSibling?.parentNode === originalParent) originalParent.insertBefore(source, originalNextSibling);
        else originalParent.append(source);
      },
    });
    return runtime;
  }

  /** Preview panel runtime. Terminal is an independent editor instance. */
  function mountWorkbenchPreviewPanel(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new TypeError("PortalWorkspaceSurface.mountWorkbenchPreviewPanel root required");
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
    const documentSessions = new Set();
    const tabControllers = new Set();
    const selectionControllers = new Set();
    const previewHostDisposers = new Set();
    let restoredTabSnapshots = [];
    let destroyed = false;
    let persistenceScheduled = false;
    const notifyPersistence = () => {
      if (destroyed || persistenceScheduled) return;
      persistenceScheduled = true;
      // Tab callbacks run before activation renders the document. Snapshot
      // after those synchronous callbacks, without waiting for a layout edit.
      Promise.resolve().then(() => {
        persistenceScheduled = false;
        if (!destroyed && ![...tabControllers].some(tabs => tabs.isRestoring())) {
          options.emit?.("persistence-change");
        }
      });
    };
    const ownDocumentSession = session => {
      if (documentSessions.has(session)) return session;
      documentSessions.add(session);
      session.subscribe((snapshot, reason) => {
        if (reason === "destroy") documentSessions.delete(session);
      });
      return session;
    };
    const createOwnedDocumentSession = config => ownDocumentSession(createDocumentSession(config));
    const createOwnedTabsController = config => {
      const createDocument = config?.createDocument;
      const restoreIndex = tabControllers.size;
      const tabs = createDocumentTabsController({
        ...(config || {}),
        onChange: (snapshot, reason, meta) => {
          // Closing the last owned document also retires its navigation
          // binding, before persistence observers take their next snapshot.
          if (!snapshot.documents.length
            && [...tabControllers].every(controller => !controller.snapshot().documents.length)) {
            clearPreview();
          }
          config?.onChange?.(snapshot, reason, meta);
          if (reason !== "open-create" && reason !== "destroy"
            && !meta?.request?.__portalPreviewRestoreToken
            && !meta?.result?.error && !meta?.result?.stale
            && meta?.result?.accepted !== false) notifyPersistence();
        },
        createDocument: request => {
          const documentState = createDocument?.(request) || {};
          if (documentState.session) ownDocumentSession(documentState.session);
          return documentState;
        },
      });
      tabControllers.add(tabs);
      const restored = restoredTabSnapshots[restoreIndex];
      if (Array.isArray(restored?.documents) && restored.documents.length) {
        void tabs.restorePersistence(restored).catch(error => {
          config?.onError?.(error);
          if (typeof config?.onError !== "function") console.error(window.PortalI18n.t("Preview tab restore failed"), error);
        });
      }
      return tabs;
    };
    const createOwnedSelectionController = config => {
      const selection = createPreviewSelectionController(config);
      selectionControllers.add(selection);
      return selection;
    };
    const registerOwnedPreviewHost = (name, handler, config) => {
      const dispose = window.PortalPreview.registerHost(name, handler, config);
      previewHostDisposers.add(dispose);
      return () => { previewHostDisposers.delete(dispose); dispose(); };
    };
    const previewRoot = surface.element("preview");
    let binding = { ...(options.resource || {}) };
    let documentValue = options.document || options.payload || binding.document || null;
    let previewRender = null;
    let requestRevision = 0;
    const configuredNavigationHistoryLimit = Number(options.navigationHistoryLimit ?? 32);
    const navigationHistoryLimit = Number.isInteger(configuredNavigationHistoryLimit)
      && configuredNavigationHistoryLimit > 0
      ? Math.min(configuredNavigationHistoryLimit, 256)
      : 32;
    let navigationHistory = [];
    let navigationBusy = false;
    let navigationAdapter = null;
    let pendingBindingRestore = null;
    const hasPreviewTarget = resource => {
      if (!resource || typeof resource !== "object") return false;
      if (resource.document || resource.payload) return true;
      if (resource.request && resource.request !== resource) return hasPreviewTarget(resource.request);
      return Boolean(
        String(resource.resourceAddress || resource.href || resource.path || resource.identity || "").trim(),
      );
    };
    const persistenceBinding = resource => {
      if (!resource || typeof resource !== "object" || Array.isArray(resource)) return {};
      const candidates = [resource, resource.request, resource.document, resource.payload]
        .filter(candidate => candidate && typeof candidate === "object" && !Array.isArray(candidate));
      const locator = {};
      ["resourceAddress", "href", "path", "identity", "locatorKind", "source", "title", "label"].forEach(field => {
        const value = candidates
          .map(candidate => String(candidate[field] ?? "").trim())
          .find(Boolean);
        if (!value || value.length > 8192) return;
        if (field === "href" && /^(?:data|blob):/i.test(value)) return;
        locator[field] = value;
      });
      ["line", "lineStart", "lineEnd", "column", "columnStart", "columnEnd"].forEach(field => {
        const value = candidates.map(candidate => candidate[field]).find(candidate => Number.isInteger(candidate));
        if (value !== undefined) locator[field] = value;
      });
      return locator;
    };
    const sanitizeNavigationLocator = value => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return {};
      const excluded = new Set([
        "content", "text", "body", "draft", "data", "document", "payload",
        "request", "load", "save", "session", "previewUrl",
      ]);
      const locator = {};
      Object.entries(value).forEach(([field, raw]) => {
        if (!field || excluded.has(field) || typeof raw === "function") return;
        if (typeof raw === "string") {
          const normalized = raw.trim();
          if (!normalized || normalized.length > 8192) return;
          if (/^(?:data|blob):/i.test(normalized)) return;
          locator[field] = normalized;
          return;
        }
        if (typeof raw === "boolean" || (typeof raw === "number" && Number.isFinite(raw))) {
          locator[field] = raw;
        }
      });
      return locator;
    };
    const navigationLocator = resource => {
      if (navigationAdapter?.serialize) {
        const adapted = sanitizeNavigationLocator(navigationAdapter.serialize(resource));
        if (hasPreviewTarget(adapted)) {
          if (navigationAdapter.id) adapted.navigationAdapter = navigationAdapter.id;
          return adapted;
        }
      }
      return persistenceBinding(resource);
    };
    const restoreNavigationResource = async locator => {
      if (navigationAdapter?.restore) {
        const restored = await navigationAdapter.restore({ ...(locator || {}) });
        if (restored) return restored;
        if (
          String(locator?.navigationAdapter || "") === navigationAdapter.id
          || String(locator?.sourceType || "")
        ) {
          throw new Error("Preview 历史目标已失效");
        }
      }
      return locator;
    };
    const needsNavigationAdapter = locator => {
      if (locator?.navigationAdapter || locator?.sourceType) return true;
      const sourceId = String(locator?.source || "").trim();
      return Boolean(sourceId && !window.PortalPreview.source?.(sourceId));
    };
    const locatorIdentity = locator => JSON.stringify([
      String(locator?.navigationAdapter || ""),
      String(locator?.sourceType || locator?.source || ""),
      String(locator?.resourceAddress || locator?.identity || locator?.href || locator?.path || ""),
    ]);
    const normalizeNavigationHistory = values => {
      const normalized = [];
      (Array.isArray(values) ? values : []).forEach(value => {
        const locator = sanitizeNavigationLocator(value);
        if (!hasPreviewTarget(locator)) return;
        if (locatorIdentity(normalized.at(-1)) === locatorIdentity(locator)) return;
        normalized.push(locator);
      });
      return normalized.slice(-navigationHistoryLimit);
    };
    const updateBackAction = () => {
      const previous = navigationHistory.at(-1) || null;
      const label = String(previous?.title || previous?.label || previous?.path || "").trim();
      [ids.back].forEach(id => {
        const button = root.querySelector(`#${CSS.escape(id)}`);
        if (!button) return;
        button.disabled = navigationBusy || !previous;
        button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        button.title = label ? window.PortalI18n.text`返回：${label}` : window.PortalI18n.t("返回上一个预览文件");
      });
    };
    const rememberPreviousResource = (previousResource, nextResource) => {
      const previous = navigationLocator(previousResource);
      const next = navigationLocator(nextResource);
      if (!hasPreviewTarget(previous) || locatorIdentity(previous) === locatorIdentity(next)) return;
      if (locatorIdentity(navigationHistory.at(-1)) !== locatorIdentity(previous)) {
        navigationHistory.push(previous);
        navigationHistory = navigationHistory.slice(-navigationHistoryLimit);
      }
      updateBackAction();
    };
    const updateDocumentHeader = document => {
      const title = root.querySelector(`#${CSS.escape(ids.title)}`);
      if (title) title.textContent = String(document?.title || document?.name || options.heading || window.PortalI18n.t("Preview"));
      surface.setPath(String(document?.path || document?.resourceAddress || ""));
    };
    const initialPreviewLine = resource => {
      const candidates = [resource, resource?.request, resource?.document, resource?.payload]
        .filter(candidate => candidate && typeof candidate === "object");
      const explicit = candidates
        .map(candidate => Number(candidate.lineStart ?? candidate.line))
        .find(value => Number.isInteger(value) && value > 0);
      if (explicit) return explicit;
      const locator = candidates.map(candidate => String(candidate.locator || "").trim()).find(Boolean) || "";
      const parsed = Number(locator.match(/(?:#L|:)(\d+)/i)?.[1] || 0);
      return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
    };
    const focusInitialPreviewLine = resource => {
      const lineNumber = initialPreviewLine(resource);
      if (!lineNumber) return false;
      const target = previewRoot.querySelector(`[data-preview-line="${lineNumber}"]`);
      if (!target) return false;
      previewRoot.querySelector('[data-preview-line][aria-current="location"]')?.removeAttribute("aria-current");
      target.setAttribute("aria-current", "location");
      (target.querySelector(".previewLineContent") || target).scrollIntoView({ block: "center", inline: "nearest" });
      return true;
    };
    const renderDocument = async document => {
      documentValue = window.PortalPreview.normalizeDocument(document || {});
      updateDocumentHeader(documentValue);
      surface.setDownload(
        documentValue.downloadUrl,
        String(
          documentValue.downloadName
          || documentValue.filename
          || documentValue.name
          || documentValue.title
          || "",
        ).split("/").at(-1),
      );
      previewRender = window.PortalPreview.render({
        root: previewRoot,
        document: documentValue,
        onPreviewRequest: options.onPreviewRequest,
        context: options.renderContext,
      });
      surface.setDocumentStatistics(documentValue);
      await previewRender.promise;
      surface.setPath(String(documentValue.path || documentValue.resourceAddress || ""));
      return documentValue;
    };
    const clearPreview = (config = {}) => {
      requestRevision += 1;
      binding = {};
      pendingBindingRestore = null;
      documentValue = null;
      surface.setDocumentStatistics(null);
      if (config.preserveHistory !== true) navigationHistory = [];
      window.PortalPreview.dispose(previewRoot);
      previewRender = null;
      updateDocumentHeader(null);
      surface.setDownload("");
      surface.setPath("");
      previewRoot.textContent = String(config.emptyText || options.emptyText || window.PortalI18n.t("选择文件后显示内容。"));
      updateBackAction();
      notifyPersistence();
      return null;
    };
    const openResource = async (resource, navigation = {}) => {
      if (!hasPreviewTarget(resource)) return clearPreview();
      const previousBinding = binding;
      const revision = ++requestRevision;
      surface.setState(window.PortalI18n.t("加载中…"));
      try {
        const embedded = resource?.document || resource?.payload;
        const request = resource?.request || resource;
        const document = embedded || await (typeof request?.load === "function"
          ? request.load()
          : options.load
            ? options.load(resource)
            : window.PortalPreview.load(request));
        if (revision !== requestRevision) return null;
        const rendered = await renderDocument(document);
        if (revision !== requestRevision) return null;
        binding = { ...(resource || {}) };
        focusInitialPreviewLine(resource);
        if (navigation.recordHistory !== false) rememberPreviousResource(previousBinding, resource);
        updateBackAction();
        notifyPersistence();
        return rendered;
      } catch (error) {
        if (revision !== requestRevision) return null;
        binding = previousBinding;
        surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
        window.PortalUIPrimitives?.renderAsyncState?.(previewRoot, {
          state: "error",
          title: window.PortalI18n.t("Preview 加载失败"),
          message: error?.message || String(error),
          density: "panel",
        });
        throw error;
      }
    };
    const goBack = async () => {
      if (navigationBusy || navigationHistory.length === 0) return null;
      const target = navigationHistory.at(-1);
      navigationBusy = true;
      updateBackAction();
      try {
        const resource = await restoreNavigationResource(target);
        const rendered = await openResource(resource, { recordHistory: false });
        if (rendered) {
          navigationHistory.pop();
          notifyPersistence();
        }
        return rendered;
      } finally {
        navigationBusy = false;
        updateBackAction();
      }
    };
    const controller = Object.freeze({
      root,
      ids,
      surface,
      createDocumentSession: createOwnedDocumentSession,
      createDocumentTabsController: createOwnedTabsController,
      createDocumentEditorTools,
      createPreviewSelectionController: createOwnedSelectionController,
      registerPreviewHost: registerOwnedPreviewHost,
      setDocument(document) { return openResource({ document }); },
      openResource,
      goBack,
      async canClose() {
        const sessions = new Set([...documentSessions, ...[...tabControllers].flatMap(tabs => (
          tabs.snapshot().documents.map(document => document.session).filter(Boolean)
        ))]);
        for (const session of sessions) {
          if (session.snapshot().dirty && !await session.guardDiscard()) return false;
        }
        return true;
      },
      setNavigationAdapter(adapter = null) {
        if (adapter == null) {
          navigationAdapter = null;
          return null;
        }
        const id = String(adapter.id || "").trim();
        if (!id || typeof adapter.serialize !== "function" || typeof adapter.restore !== "function") {
          throw new TypeError("Preview navigation adapter requires id, serialize, and restore");
        }
        navigationAdapter = Object.freeze({ id, serialize: adapter.serialize, restore: adapter.restore });
        navigationHistory = normalizeNavigationHistory(navigationHistory);
        const pending = pendingBindingRestore;
        pendingBindingRestore = null;
        if (pending) {
          void restoreNavigationResource(pending)
            .then(resource => openResource(resource, { recordHistory: false }))
            .catch(error => {
              pendingBindingRestore = pending;
              surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
            });
        }
        updateBackAction();
        return navigationAdapter;
      },
      setContext(context = {}) {
        const target = context.documentTarget;
        if (!target) return documentValue;
        const targetPanelId = String(target.panelId || target.panel_id || "").trim();
        if (hasPreviewTarget(binding) && targetPanelId !== String(options.panelId || "")) return documentValue;
        return openResource(target);
      },
      clear: clearPreview,
      reload() {
        // A document-session adapter owns both edit protection and rendering.
        if (options.reload) return options.reload(documentValue);
        return openResource(binding, { recordHistory: false });
      },
      snapshot: () => ({
        binding: navigationLocator(binding),
        navigationHistory: navigationHistory.map(locator => ({ ...locator })),
        dirty: [...documentSessions].some(session => session.snapshot().dirty)
          || [...tabControllers].some(tabs => tabs.snapshot().documents.some(document => document.session?.snapshot().dirty)),
        documentTabs: tabControllers.size
          ? [...tabControllers].map(item => item.persistenceSnapshot())
          : restoredTabSnapshots,
      }),
      restore(snapshot = {}) {
        navigationHistory = normalizeNavigationHistory(snapshot.navigationHistory);
        updateBackAction();
        restoredTabSnapshots = Array.isArray(snapshot.documentTabs)
          ? snapshot.documentTabs.filter(item => item && typeof item === "object")
          : [];
        [...tabControllers].forEach((tabs, index) => {
          const restored = restoredTabSnapshots[index];
          if (!restored) return;
          void tabs.restorePersistence(restored).catch(error => console.error(window.PortalI18n.t("Preview tab restore failed"), error));
        });
        const restoredBinding = sanitizeNavigationLocator(snapshot.binding);
        if (!hasPreviewTarget(restoredBinding)) return clearPreview();
        binding = { ...restoredBinding };
        if (!navigationAdapter && needsNavigationAdapter(restoredBinding)) {
          pendingBindingRestore = restoredBinding;
          return;
        }
        if (restoredBinding.navigationAdapter !== undefined
          && restoredBinding.navigationAdapter !== navigationAdapter?.id) {
          pendingBindingRestore = restoredBinding;
          return;
        }
        void restoreNavigationResource(restoredBinding)
          .then(resource => openResource(resource, { recordHistory: false }))
          .catch(error => {
            pendingBindingRestore = restoredBinding;
            surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
          });
      },
      show() {},
      hide() {},
      async destroy() {
        destroyed = true;
        requestRevision += 1;
        window.PortalPreview.dispose(previewRoot);
        previewHostDisposers.forEach(dispose => dispose());
        previewHostDisposers.clear();
        tabControllers.forEach(item => item.destroy?.());
        tabControllers.clear();
        selectionControllers.forEach(item => item.destroy?.());
        selectionControllers.clear();
        documentSessions.forEach(item => item.destroy?.());
        documentSessions.clear();
        surface.destroy();
        root.replaceChildren();
      },
    });
    root.__portalWorkbenchPreviewPanel = controller;
    [ids.back].forEach(id => {
      root.querySelector(`#${CSS.escape(id)}`)?.addEventListener("click", event => {
        const operation = options.commands?.execute
          ? options.commands.execute("back", { event, source: "preview-toolbar" })
          : controller.goBack();
        Promise.resolve(operation).catch(error => {
          surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
        });
      });
    });
    surface.action("refresh")?.addEventListener("click", event => {
      const operation = options.commands?.execute
        ? options.commands.execute("reload", { event, source: "preview-toolbar" })
        : controller.reload();
      Promise.resolve(operation).catch(error => {
        surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
      });
    });
    const refresh = surface.action("refresh");
    if (refresh) refresh.disabled = false;
    updateBackAction();
    if (documentValue) void renderDocument(documentValue).catch(error => surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`));
    else if (hasPreviewTarget(options.resource)) void openResource(options.resource, { recordHistory: false }).catch(error => surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`));
    else clearPreview();
    return controller;
  }

  /** Independent terminal editor. Each mount owns exactly one shell runtime. */
  function mountWorkbenchTerminalPanel(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new TypeError("PortalWorkspaceSurface.mountWorkbenchTerminalPanel root required");
    root.className = "pane portalWorkbenchTerminalPanel";
    root.setAttribute("aria-label", options.label || window.PortalI18n.t("Terminal"));
    root.innerHTML = window.PortalI18n.html`<div class="paneHead resultPaneHead">
      <div class="paneHeadText previewHeadText"><div class="paneTitle previewTitle" data-terminal-title>${escapeHtml(options.heading || window.PortalI18n.t("Terminal"))}</div><div class="paneNote" data-terminal-context></div></div>
    </div>
    <div class="terminalBox portalWorkbenchTerminalBody"><div class="terminalXterm" data-terminal-mount></div><div class="terminalToolbar"><button class="terminalBtn" type="button" data-terminal-interrupt>Ctrl-C</button><button class="terminalBtn" type="button" data-terminal-restart title="关闭当前 shell 并新建一个">New shell</button></div></div>`;
    let target = { ...(options.target || {}) };
    let controller = null;
    let visible = false;
    const context = () => {
      const resolved = options.resolveContext?.(target) || target || {};
      return {
        key: String(resolved.key || resolved.cwd || "portal-host"),
        cwd: String(resolved.cwd || ""),
        label: String(resolved.label || resolved.cwd || window.PortalI18n.t("Portal host")),
      };
    };
    const renderContext = () => {
      const current = context();
      const note = root.querySelector("[data-terminal-context]");
      if (note) note.textContent = current.label;
      return current;
    };
    const ensureController = () => {
      if (controller) return controller;
      controller = window.PortalTerminal.create({
        mount: root.querySelector("[data-terminal-mount]"),
        apiGet: options.apiGet,
        apiPost: options.apiPost,
        startUrl: options.startUrl || "/api/agent-workbench/local-terminal/start",
        readUrl: options.readUrl || "/api/agent-workbench/local-terminal/read",
        inputUrl: options.inputUrl || "/api/agent-workbench/local-terminal/input",
        controlUrl: options.controlUrl || "/api/agent-workbench/local-terminal/control",
        startPayload: () => ({ cwd: context().cwd, ...(options.startPayload?.(target) || {}) }),
        isActive: () => visible,
        TerminalClass: options.TerminalClass || window.Terminal,
        FitAddonClass: options.FitAddonClass || window.FitAddon?.FitAddon,
        messages: { starting: window.PortalI18n.t("正在启动本地终端…"), ...(options.messages || {}) },
      });
      return controller;
    };
    const executeTerminalCommand = (commandId, event) => options.commands?.execute
      ? options.commands.execute(commandId, { event, source: "terminal-toolbar" })
      : ensureController().control(commandId);
    root.querySelector("[data-terminal-interrupt]")?.addEventListener("click", event => {
      void executeTerminalCommand("interrupt", event);
    });
    root.querySelector("[data-terminal-restart]")?.addEventListener("click", event => {
      void executeTerminalCommand("restart", event);
    });
    renderContext();
    const runtime = Object.freeze({
      root,
      async setTarget(next = {}) {
        const previous = context();
        target = { ...next };
        const current = renderContext();
        if (controller && (previous.key !== current.key || previous.cwd !== current.cwd)) {
          await controller.reset({ restart: visible });
        }
      },
      openResource(resource) { return runtime.setTarget(resource); },
      setContext(nextContext = {}) {
        return nextContext.workspace ? runtime.setTarget(nextContext.workspace) : context();
      },
      show() {
        visible = true;
        const terminal = ensureController();
        requestAnimationFrame(() => {
          terminal.fit();
          terminal.start().then(() => { if (visible) terminal.terminal?.focus(); });
        });
      },
      hide() { visible = false; controller?.pause?.(); },
      fit() { controller?.fit?.(); },
      control(action) { return ensureController().control(action); },
      snapshot: () => ({ target }),
      restore(snapshot = {}) { if (snapshot.target) runtime.setTarget(snapshot.target); },
      async destroy() { visible = false; await controller?.destroy?.(); root.replaceChildren(); },
    });
    return runtime;
  }

  function renderDocumentTabs(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) return 0;
    const items = Array.isArray(options.items) ? options.items : [];
    root.classList.add("portalDocumentTabs");
    if (!items.length) {
      root.innerHTML = window.PortalI18n.html`<div class="portalDocumentTabsEmpty">${escapeHtml(options.emptyText || window.PortalI18n.t("Open a file"))}</div>`;
      return 0;
    }
    const byKey = new Map();
    root.innerHTML = items.map((item, index) => {
      const key = String(item?.key ?? index);
      const label = String(item?.label || key.split("/").at(-1) || key);
      const title = String(item?.title || key);
      const active = item?.active === true;
      const dirty = item?.dirty === true;
      const pinned = item?.pinned === true;
      byKey.set(key, item);
      return window.PortalI18n.html`<div class="portalDocumentTab${active ? " is-active" : ""}${dirty ? " is-dirty" : ""}${pinned ? " is-pinned" : ""}" role="tab" aria-selected="${active ? "true" : "false"}" title="${escapeHtml(title)}">
        <button class="portalDocumentTabSelect" data-portal-icon="file" type="button" data-portal-document-tab="${escapeHtml(key)}">
          <span class="portalDocumentTabLabel">${escapeHtml(label)}</span>
          ${pinned ? window.PortalI18n.t('<span class="portalDocumentTabPin" data-portal-icon="pin" aria-label="已锁定" title="已锁定"></span>') : ""}
          <i class="portalDocumentTabDirty" aria-hidden="true"></i>
        </button>
        ${item?.closable === false ? "" : window.PortalI18n.html`<button class="portalDocumentTabClose" data-portal-icon="close" type="button" data-portal-document-close="${escapeHtml(key)}" aria-label="关闭 ${escapeHtml(label)}" title="关闭"></button>`}
      </div>`;
    }).join("");
    window.PortalUIPrimitives?.mountIcons?.(root);
    root.querySelectorAll("[data-portal-document-tab]").forEach(button => {
      button.addEventListener("click", event => {
        event.stopPropagation();
        const key = button.dataset.portalDocumentTab || "";
        options.onSelect?.(key, byKey.get(key), event);
      });
    });
    root.querySelectorAll("[data-portal-document-close]").forEach(button => {
      button.addEventListener("click", event => {
        event.stopPropagation();
        const key = button.dataset.portalDocumentClose || "";
        options.onClose?.(key, byKey.get(key), event);
      });
    });
    window.requestAnimationFrame(() => {
      root.querySelector(".portalDocumentTab.is-active")?.scrollIntoView?.({
        block: "nearest",
        inline: "nearest",
      });
    });
    return items.length;
  }

  function createDocumentSession(options = {}) {
    let requestId = 0;
    let saveId = 0;
    let openSequence = 0;
    let destroyed = false;
    let abortController = null;
    const listeners = new Set();
    let state = {
      status: "empty",
      payload: null,
      requestId,
      requestKey: "",
      editing: false,
      draft: "",
      dirty: false,
      saving: false,
      error: "",
      readError: "",
    };

    const snapshot = () => Object.freeze({ ...state });
    const emit = (reason, meta = {}) => {
      const current = snapshot();
      options.onStateChange?.(current, reason, meta);
      listeners.forEach(listener => listener(current, reason, meta));
      return current;
    };
    const transition = (patch, reason, meta = {}) => {
      state = { ...state, ...patch };
      return emit(reason, meta);
    };
    const confirmDiscard = context => {
      if (!state.dirty) return true;
      const handler = context?.confirmDiscard || options.confirmDiscard;
      if (typeof handler === "function") {
        const decision = handler(snapshot(), context);
        return decision && typeof decision.then === "function"
          ? Promise.resolve(decision).then(value => value !== false)
          : decision !== false;
      }
      return window.PortalUI.confirm({
        title: window.PortalI18n.t("放弃未保存的修改？"),
        message: options.discardMessage || window.PortalI18n.t("关闭后将丢失尚未保存的文件修改。"),
        confirmLabel: window.PortalI18n.t("放弃修改"),
        tone: "danger",
      });
    };
    const guardDiscard = (context = {}) => {
      if (destroyed) return false;
      const guarded = snapshot();
      const stillCurrent = approved => approved !== false
        && !destroyed
        && requestId === guarded.requestId
        && state.payload === guarded.payload
        && state.draft === guarded.draft
        && state.editing === guarded.editing
        && state.saving === guarded.saving;
      const decision = confirmDiscard(context);
      return decision && typeof decision.then === "function"
        ? Promise.resolve(decision).then(stillCurrent)
        : stillCurrent(decision);
    };
    const invalidate = (reason = "invalidate") => {
      if (destroyed) return requestId;
      openSequence += 1;
      requestId += 1;
      saveId += 1;
      abortController?.abort?.();
      abortController = null;
      state = { ...state, requestId };
      emit(reason);
      return requestId;
    };
    const present = (payload, meta = {}) => {
      if (destroyed) return null;
      if (meta.invalidate !== false) invalidate("present-invalidate");
      const continuousEdit = options.continuousEdit === true && !!payload;
      const current = transition({
        status: continuousEdit ? "editing" : (payload ? "ready" : "empty"),
        payload: payload || null,
        requestId,
        requestKey: String(meta.requestKey || ""),
        editing: continuousEdit,
        draft: continuousEdit ? String(payload?.content || "") : "",
        dirty: false,
        saving: false,
        error: "",
        readError: "",
      }, meta.reason || "present", meta);
      options.onPresent?.(payload || null, current, meta);
      meta.onPresent?.(payload || null, current, meta);
      return payload || null;
    };
    const show = (patch = {}, meta = {}) => {
      if (destroyed) return snapshot();
      if (meta.invalidate !== false) invalidate("show-invalidate");
      return transition({
        status: String(patch.status || "empty"),
        payload: patch.payload || null,
        requestId,
        requestKey: String(patch.requestKey || ""),
        editing: false,
        draft: "",
        dirty: false,
        saving: false,
        error: String(patch.error || ""),
        readError: "",
      }, meta.reason || "show", meta);
    };
    const open = async (request = {}) => {
      if (destroyed) return { accepted: false, stale: true };
      const sequence = ++openSequence;
      const discardDecision = guardDiscard(request);
      const discardApproved = discardDecision && typeof discardDecision.then === "function"
        ? await discardDecision
        : discardDecision;
      if (sequence !== openSequence) return { accepted: true, stale: true };
      if (!discardApproved) return { accepted: false, stale: false };
      const id = invalidate("open-invalidate");
      abortController = typeof AbortController === "function" ? new AbortController() : null;
      const requestKey = String(request.key || "");
      const loadingPayload = request.loadingPayload || null;
      const current = transition({
        status: "loading",
        payload: loadingPayload,
        requestId: id,
        requestKey,
        editing: false,
        draft: "",
        dirty: false,
        saving: false,
        error: "",
        readError: "",
      }, "loading", { request, requestId: id });
      options.onLoading?.(current, request);
      request.onLoading?.(current, request);
      try {
        const payload = await request.load?.({
          signal: abortController?.signal,
          requestId: id,
          requestKey,
        });
        if (id !== requestId) return { accepted: true, stale: true };
        abortController = null;
        if (payload == null && request.allowEmpty !== true) {
          const empty = transition({
            status: "empty",
            payload: null,
            saving: false,
            error: "",
          }, "empty", { request, requestId: id });
          options.onEmpty?.(empty, request);
          request.onEmpty?.(empty, request);
          return { accepted: true, stale: false, payload: null };
        }
        present(payload, {
          invalidate: false,
          reason: "loaded",
          request,
          requestId: id,
          requestKey,
          onPresent: request.onPresent,
        });
        return { accepted: true, stale: false, payload };
      } catch (error) {
        if (id !== requestId || error?.name === "AbortError") {
          return { accepted: true, stale: true, error };
        }
        abortController = null;
        const failed = transition({
          status: "error",
          payload: request.errorPayload || loadingPayload,
          saving: false,
          error: String(error?.message || error || "Preview failed"),
        }, "error", { request, requestId: id, error });
        options.onError?.(error, failed, request);
        request.onError?.(error, failed, request);
        return { accepted: true, stale: false, error };
      }
    };
    const beginEdit = meta => {
      if (destroyed || !state.payload || state.saving) return false;
      if (typeof options.canEdit === "function" && !options.canEdit(state.payload, snapshot())) return false;
      transition({
        status: "editing",
        editing: true,
        draft: String(state.payload.content || ""),
        dirty: false,
        error: "",
      }, "edit-start", meta);
      options.onEditStart?.(snapshot(), meta);
      return true;
    };
    const updateDraft = (draft, meta) => {
      if (destroyed || !state.editing || state.saving) return false;
      transition({
        draft: String(draft ?? ""),
        dirty: String(draft ?? "") !== String(state.payload?.content || ""),
      }, "draft-change", meta);
      return true;
    };
    const cancelEdit = async (meta = {}) => {
      if (destroyed || !state.editing || state.saving) return false;
      const discardDecision = guardDiscard(meta);
      const discardApproved = discardDecision && typeof discardDecision.then === "function"
        ? await discardDecision
        : discardDecision;
      if (!discardApproved) return false;
      const payload = state.payload;
      const continuousEdit = options.continuousEdit === true && !!payload;
      const current = transition({
        status: continuousEdit ? "editing" : (payload ? "ready" : "empty"),
        editing: continuousEdit,
        draft: continuousEdit ? String(payload?.content || "") : "",
        dirty: false,
        saving: false,
        error: "",
      }, "edit-cancel", meta);
      options.onPresent?.(payload, current, { ...meta, reason: "edit-cancel" });
      meta.onPresent?.(payload, current, { ...meta, reason: "edit-cancel" });
      return true;
    };
    const save = async (request = {}) => {
      if (destroyed || !actionState().canSave || typeof request.save !== "function") {
        return { accepted: false };
      }
      const id = ++saveId;
      const payload = state.payload;
      const draft = state.draft;
      transition({ status: "saving", saving: true, error: "" }, "saving", { request, saveId: id });
      options.onSaving?.(snapshot(), request);
      request.onSaving?.(snapshot(), request);
      try {
        const updated = await request.save({ payload, draft, saveId: id });
        if (id !== saveId) return { accepted: true, stale: true };
        const savedPayload = updated
          ? {
            ...payload,
            ...updated,
            content: Object.hasOwn(updated, "content") ? updated.content : draft,
          }
          : { ...payload, content: draft };
        transition({
          status: "editing",
          payload: savedPayload,
          editing: true,
          draft: String(savedPayload.content ?? ""),
          dirty: false,
          saving: false,
          error: "",
        }, "saved", { request, saveId: id });
        options.onSaved?.(savedPayload, snapshot(), request);
        request.onSaved?.(savedPayload, snapshot(), request);
        return { accepted: true, stale: false, payload: savedPayload };
      } catch (error) {
        if (id !== saveId) return { accepted: true, stale: true, error };
        const failed = transition({
          status: "editing",
          saving: false,
          editing: true,
          error: String(error?.message || error || "Save failed"),
        }, "save-error", { request, saveId: id, error });
        options.onSaveError?.(error, failed, request);
        request.onError?.(error, failed, request);
        return { accepted: true, stale: false, error };
      }
    };
    const actionState = () => {
      const current = snapshot();
      const editable = !destroyed && !!current.payload
        && (typeof options.canEdit !== "function" || !!options.canEdit(current.payload, current));
      return Object.freeze({
        status: current.status,
        hasPayload: !!current.payload,
        loading: current.status === "loading",
        editing: current.editing,
        dirty: current.dirty,
        saving: current.saving,
        error: current.error,
        editable,
        canEdit: editable && !current.saving && current.status !== "loading",
        canSave: editable && current.editing && current.dirty && !current.saving,
        canOpen: !!current.payload && !current.editing && !current.saving && current.status === "ready",
      });
    };
    const subscribe = listener => {
      if (destroyed || typeof listener !== "function") return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    };
    const setReadError = error => {
      if (destroyed) return snapshot();
      return transition({ readError: String(error?.message || error || "") }, "read-error");
    };
    const destroy = () => {
      if (destroyed) return;
      invalidate("destroy");
      destroyed = true;
      listeners.clear();
    };

    emit("init");
    return Object.freeze({
      actionState,
      beginEdit,
      cancelEdit,
      destroy,
      guardDiscard,
      invalidate,
      open,
      present,
      save,
      setReadError,
      show,
      snapshot,
      subscribe,
      updateDraft,
    });
  }

  function createDocumentTabsController(options = {}) {
    const documents = new Map();
    const pendingSessions = new Set();
    let navigationSequence = 0;
    let tabs = [];
    let activeKey = "";
    let restoreSequence = 0;
    let restoringSequence = 0;
    const restoreToken = Symbol("portal-preview-tab-restore");

    const orderedDocuments = () => tabs
      .map(key => documents.get(key))
      .filter(Boolean);
    const active = () => activeKey ? documents.get(activeKey) || null : null;
    const snapshot = () => Object.freeze({
      activeKey,
      active: active(),
      tabs: Object.freeze([...tabs]),
      documents: Object.freeze(orderedDocuments()),
    });
    const persistenceSnapshot = () => Object.freeze({
      schemaVersion: 1,
      activeKey,
      documents: Object.freeze(orderedDocuments().map(documentState => {
        const value = options.serializeDocument?.(
          documentState,
          documentState.session?.snapshot?.() || null,
        );
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        return Object.freeze({
          key: documentState.key,
          pinned: documentState.pinned === true,
          value,
        });
      }).filter(Boolean)),
    });
    const notify = (reason, meta = {}) => {
      const current = snapshot();
      options.onChange?.(current, reason, meta);
      return current;
    };
    const reportError = error => {
      if (typeof options.onError === "function") options.onError(error);
      else window.setTimeout(() => { throw error; }, 0);
    };
    const render = () => {
      const shell = resolveRoot(options.shell);
      const root = resolveRoot(options.root);
      const pinButton = resolveRoot(options.pinButton);
      const ordered = orderedDocuments();
      if (shell) shell.hidden = ordered.length === 0 && options.showWhenEmpty !== true;
      if (root) {
        renderDocumentTabs({
          root,
          emptyText: options.emptyText,
          items: ordered.map(documentState => {
            const sessionState = documentState.session?.snapshot?.() || {};
            return {
              key: documentState.key,
              label: options.label?.(documentState, sessionState)
                || documentState.label
                || String(documentState.key || "").split("/").at(-1)
                || window.PortalI18n.t("Preview"),
              title: options.title?.(documentState, sessionState)
                || documentState.title
                || documentState.path
                || documentState.key,
              active: documentState.key === activeKey,
              dirty: sessionState.dirty === true,
              pinned: documentState.pinned === true,
              closable: documentState.closable !== false,
            };
          }),
          onSelect: key => {
            try {
              const result = activate(key, { reason: "tab-select" });
              if (result && typeof result.catch === "function") result.catch(reportError);
            } catch (error) {
              reportError(error);
            }
          },
          onClose: key => {
            close(key, { reason: "tab-close" }).catch(reportError);
          },
        });
      }
      const current = active();
      if (pinButton) {
        const canPin = !!current && options.canPin?.(current) !== false;
        pinButton.disabled = !canPin;
        pinButton.setAttribute("aria-pressed", current?.pinned ? "true" : "false");
        pinButton.setAttribute(
          "aria-label",
          current?.pinned ? window.PortalI18n.t("解除当前文件锁定") : window.PortalI18n.t("锁定当前文件"),
        );
        pinButton.title = current?.pinned
          ? window.PortalI18n.t("解除锁定；下次打开文件时可替换此标签")
          : window.PortalI18n.t("锁定当前文件；之后打开的文件会新增为标签");
      }
      options.onRender?.(snapshot());
      return ordered.length;
    };
    const activate = (key, meta = {}) => {
      const documentState = documents.get(String(key || ""));
      if (!documentState) return false;
      navigationSequence += 1;
      activeKey = documentState.key;
      notify(meta.reason || "activate", { ...meta, document: documentState });
      options.onActivate?.(
        documentState,
        documentState.session?.snapshot?.() || null,
        meta,
      );
      render();
      return true;
    };
    const open = async (request = {}) => {
      const navigation = ++navigationSequence;
      const restoring = request.__portalPreviewRestoreToken === restoreToken;
      if (!restoring) restoreSequence += 1;
      const key = String(request.key || "").trim();
      if (!key) return { accepted: false, stale: false };
      const existing = documents.get(key);
      if (existing) {
        if (request.mode === "new-tab") existing.pinned = true;
        activate(key, { reason: "open-existing", request });
        if (request.reload === true) {
          const result = await existing.session?.open?.(request);
          render();
          return result;
        }
        return {
          accepted: true,
          stale: false,
          existing: true,
          payload: existing.session?.snapshot?.().payload || null,
        };
      }

      const previous = active();
      const inheritedPin = previous?.pinned === true;
      const newTab = request.mode === "new-tab";
      const replacing = previous && !previous.pinned && !restoring && !newTab;
      let insertIndex = tabs.length;
      if (replacing) {
        const discardApproved = await previous.session?.guardDiscard?.(request);
        if (discardApproved === false) return { accepted: false, stale: false };
        if (navigation !== navigationSequence) return { accepted: false, stale: true };
        insertIndex = Math.max(0, tabs.indexOf(previous.key));
      }

      const created = options.createDocument?.(request, {
        active: previous,
        insertIndex,
      }) || {};
      const documentState = {
        ...created,
        key,
        label: String(
          created.label
          || request.label
          || request.title
          || request.path
          || key,
        ).split("/").at(-1) || window.PortalI18n.t("Preview"),
        title: String(created.title || request.title || request.path || key),
        path: String(created.path || request.path || ""),
        pinned: created.pinned === undefined
          ? (restoring ? request.pinned === true : Boolean(newTab || request.pinned || inheritedPin))
          : created.pinned === true,
        session: null,
      };
      documentState.session = options.createSession?.(documentState, request)
        || createDocumentSession(options.sessionOptions || {});
      if (previous) {
        // Read into a detached session. Persistence and the visible document
        // retain the old identity until the new resource is actually readable.
        const before = previous.session?.snapshot?.() || {};
        pendingSessions.add(documentState.session);
        let result;
        try {
          result = await documentState.session.open({ ...request,
            onLoading: undefined, onPresent: undefined, onEmpty: undefined, onError: undefined,
          });
        } catch (error) {
          result = { accepted: true, stale: false, error };
        } finally {
          pendingSessions.delete(documentState.session);
        }
        const after = previous.session?.snapshot?.() || {};
        const superseded = navigation !== navigationSequence || active() !== previous
          || ['draft', 'editing', 'dirty', 'saving'].some(field => before[field] !== after[field]);
        if (superseded || result?.error || result?.stale || result?.accepted === false || result?.payload == null) {
          documentState.session.destroy?.();
          if (!superseded && result?.error && !result.stale) reportError(result.error);
          return { ...result, stale: superseded || result?.stale === true };
        }
        if (replacing) {
          previous.session?.destroy?.();
          documents.delete(previous.key);
          tabs = tabs.filter(item => item !== previous.key);
        }
        if (newTab) previous.pinned = true;
        documents.set(key, documentState);
        tabs.splice(insertIndex, 0, key);
        activate(key, { reason: "open-complete", request, result });
        request.onPresent?.(result.payload, documentState.session.snapshot?.(), { request });
        return result;
      }
      documents.set(key, documentState);
      tabs.splice(insertIndex, 0, key);
      activeKey = key;
      notify("open-create", { document: documentState, request });
      options.onActivate?.(
        documentState,
        documentState.session?.snapshot?.() || null,
        { reason: "open-create", request },
      );
      render();
      const result = await documentState.session.open(request);
      notify("open-complete", { document: documentState, request, result });
      render();
      return result;
    };
    const close = async (key, meta = {}) => {
      const documentState = documents.get(String(key || ""));
      if (!documentState) return false;
      const discardApproved = await documentState.session?.guardDiscard?.(meta);
      if (discardApproved === false) return false;
      navigationSequence += 1;
      const index = tabs.indexOf(documentState.key);
      const wasActive = documentState.key === activeKey;
      documentState.session?.destroy?.();
      documents.delete(documentState.key);
      tabs = tabs.filter(item => item !== documentState.key);
      if (wasActive) {
        activeKey = "";
        const next = tabs[index] || tabs[index - 1] || "";
        if (next) {
          activate(next, { ...meta, reason: meta.reason || "close-activate-next" });
        } else {
          notify(meta.reason || "close-empty", { ...meta, document: documentState });
          options.onEmpty?.(meta);
        }
      } else {
        notify(meta.reason || "close", { ...meta, document: documentState });
      }
      render();
      return true;
    };
    const togglePin = () => {
      const documentState = active();
      if (!documentState || options.canPin?.(documentState) === false) return false;
      documentState.pinned = !documentState.pinned;
      notify("pin-change", { document: documentState, pinned: documentState.pinned });
      options.onPinChange?.(documentState, documentState.pinned);
      render();
      return documentState.pinned;
    };
    const isActive = key => String(key || "") === activeKey;
    const get = key => documents.get(String(key || "")) || null;
    const reportRestoreError = (error, record) => {
      if (typeof options.onError === "function") {
        options.onError(error, { reason: "restore", record });
        return;
      }
      console.error(window.PortalI18n.t("Preview tab restore failed"), error);
    };
    const restorePersistence = async (persisted = {}) => {
      const records = persisted.schemaVersion === 1
        && Array.isArray(persisted.documents)
        ? persisted.documents
        : [];
      const sequence = ++restoreSequence;
      restoringSequence = sequence;
      orderedDocuments().forEach(documentState => documentState.session?.destroy?.());
      documents.clear();
      tabs = [];
      activeKey = "";
      for (const record of records) {
        if (sequence !== restoreSequence) return snapshot();
        let key = "";
        try {
          const restoredRequest = await options.restoreDocument?.(record.value, record);
          if (sequence !== restoreSequence) return snapshot();
          key = String(restoredRequest?.key || record.key || "").trim();
          if (!key || !restoredRequest || typeof restoredRequest !== "object") continue;
          const result = await open({
            ...restoredRequest,
            key,
            pinned: record.pinned === true,
            __portalPreviewRestoreToken: restoreToken,
          });
          if (result?.error) {
            throw result.error instanceof Error
              ? result.error
              : new Error(String(result.error || "Preview tab restore failed"));
          }
        } catch (error) {
          const partial = key ? documents.get(key) : null;
          partial?.session?.destroy?.();
          if (key) {
            documents.delete(key);
            tabs = tabs.filter(item => item !== key);
            if (activeKey === key) activeKey = "";
          }
          reportRestoreError(error, record);
        }
        if (sequence !== restoreSequence) return snapshot();
      }
      const restoredActiveKey = String(persisted.activeKey || "");
      restoringSequence = 0;
      if (documents.has(restoredActiveKey)) {
        activate(restoredActiveKey, { reason: "restore" });
      } else if (tabs.length) {
        activate(tabs.at(-1), { reason: "restore-fallback" });
      } else {
        notify("restore-empty");
        options.onEmpty?.({ reason: "restore-empty" });
      }
      render();
      return snapshot();
    };
    const destroy = () => {
      navigationSequence += 1;
      for (const session of pendingSessions) session.destroy?.();
      pendingSessions.clear();
      restoreSequence += 1;
      orderedDocuments().forEach(documentState => documentState.session?.destroy?.());
      documents.clear();
      tabs = [];
      activeKey = "";
      notify("destroy");
      render();
    };

    render();
    return Object.freeze({
      activate,
      active,
      close,
      destroy,
      get,
      isActive,
      open,
      persistenceSnapshot,
      isRestoring: () => restoringSequence > 0 && restoringSequence === restoreSequence,
      render,
      restorePersistence,
      snapshot,
      togglePin,
    });
  }

  function previewSelectionOption(value, ...args) {
    return typeof value === "function" ? value(...args) : value;
  }

  function normalizePreviewSelectionSearchText(value) {
    return String(value || "")
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map(line => line
        .replace(/^\s{0,3}>\s?/, "")
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s*[-*+]\s+/, "")
        .replace(/^\s*\d+[.)]\s+/, "")
        .replace(/`/g, "")
        .replace(/\*\*/g, "")
        .replace(/~~/g, "")
        .trim())
      .filter(Boolean)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function previewSelectionLineRangeFromSource(sourceValue, selectedValue) {
    const source = String(sourceValue || "").replace(/\r\n?/g, "\n");
    const selectedText = String(selectedValue || "").replace(/\r\n?/g, "\n");
    const needle = normalizePreviewSelectionSearchText(selectedText);
    if (!source || !needle) return { start: 0, end: 0 };
    const lines = source.split("\n");
    const selectedLines = selectedText
      .split("\n")
      .map(normalizePreviewSelectionSearchText)
      .filter(Boolean);
    if (selectedLines.length > 1) {
      for (let start = 0; start < lines.length; start += 1) {
        if (!normalizePreviewSelectionSearchText(lines[start]).includes(selectedLines[0])) continue;
        let selectedIndex = 1;
        let end = start;
        for (let index = start + 1; index < lines.length && selectedIndex < selectedLines.length; index += 1) {
          if (normalizePreviewSelectionSearchText(lines[index]).includes(selectedLines[selectedIndex])) {
            selectedIndex += 1;
            end = index;
          }
        }
        if (selectedIndex >= selectedLines.length) {
          return { start: start + 1, end: end + 1 };
        }
      }
    }
    for (let index = 0; index < lines.length; index += 1) {
      if (normalizePreviewSelectionSearchText(lines[index]).includes(needle)) {
        return { start: index + 1, end: index + 1 };
      }
    }
    const maxSpan = Math.min(40, lines.length);
    for (let start = 0; start < lines.length; start += 1) {
      let joined = "";
      for (let end = start; end < Math.min(lines.length, start + maxSpan); end += 1) {
        joined = `${joined}\n${lines[end]}`;
        if (normalizePreviewSelectionSearchText(joined).includes(needle)) {
          return { start: start + 1, end: end + 1 };
        }
      }
    }
    return { start: 0, end: 0 };
  }

  function previewSelectionLineRangeFromDom(range, roots, source, selectedText) {
    const touched = [];
    roots.forEach(root => {
      root.querySelectorAll?.("[data-preview-line]").forEach(line => {
        try {
          if (range.intersectsNode(line)) touched.push(Number(line.dataset.previewLine || 0));
        } catch (_error) {}
      });
    });
    const lines = touched.filter(Number);
    if (lines.length) return { start: Math.min(...lines), end: Math.max(...lines) };
    return previewSelectionLineRangeFromSource(source, selectedText);
  }

  function previewSelectionLineRangeFromEditor(editor, start, end) {
    const value = String(editor?.value || "");
    const safeStart = Math.max(0, Math.min(value.length, Number(start || 0)));
    const safeEnd = Math.max(safeStart, Math.min(value.length, Number(end || 0)));
    const endOffset = safeEnd > safeStart && value[safeEnd - 1] === "\n"
      ? safeEnd - 1
      : safeEnd;
    return {
      start: value.slice(0, safeStart).split("\n").length,
      end: value.slice(0, endOffset).split("\n").length,
    };
  }

  function previewSelectionLinePart(info = {}) {
    const start = Number(info.lineStart || info.line_start || 0);
    const end = Number(info.lineEnd || info.line_end || start || 0);
    return start ? `#L${start}${end && end !== start ? `-L${end}` : ""}` : "";
  }

  function previewReferenceAttachmentId(info = {}) {
    const path = String(info.path || "").trim() || String(info.title || "Preview");
    const source = `${path}\n${previewSelectionLinePart(info)}\n${String(info.text || "")}`;
    let hash = 0;
    for (let index = 0; index < source.length; index += 1) {
      hash = ((hash << 5) - hash + source.charCodeAt(index)) | 0;
    }
    return `preview-ref-${Math.abs(hash).toString(36)}`;
  }

  function createPreviewReferenceAttachment(info = {}) {
    const path = String(info.path || "").trim() || String(info.title || "Preview");
    const title = String(info.title || path || window.PortalI18n.t("Preview"));
    const text = String(info.text || "").trim();
    const lineStart = Number(info.lineStart || info.line_start || 0);
    const lineEnd = Number(info.lineEnd || info.line_end || lineStart || 0);
    const base = path.split("/").filter(Boolean).pop() || title || "Preview";
    return {
      id: previewReferenceAttachmentId({ ...info, path, text, lineStart, lineEnd }),
      kind: "file",
      name: window.PortalI18n.text`引用 ${base}${previewSelectionLinePart({ lineStart, lineEnd })}`,
      path,
      content_type: "text/x-portal-preview-reference",
      reference_text: text,
      preview_selection: {
        path,
        title,
        line_start: lineStart,
        line_end: lineEnd,
        text,
      },
    };
  }

  function createPreviewSelectionController(options = {}) {
    const configuredRoots = Array.isArray(options.roots) ? options.roots : [options.root];
    const roots = configuredRoots.map(resolveRoot).filter(Boolean);
    if (!roots.length) return null;
    let toolbar = resolveRoot(options.toolbar);
    let ownsToolbar = false;
    if (!toolbar) {
      toolbar = document.createElement("button");
      toolbar.type = "button";
      toolbar.hidden = true;
      document.body.appendChild(toolbar);
      ownsToolbar = true;
    }
    toolbar.classList.add("portalPreviewSelectionToolbar");
    toolbar.textContent = String(options.label || toolbar.textContent || window.PortalI18n.t("发送到对话"));
    toolbar.dataset.portalOverlayLayer = "popover";
    toolbar.setAttribute("aria-label", String(options.ariaLabel || window.PortalI18n.t("把选中的文件内容发送到对话")));

    let current = null;
    let timer = 0;
    let destroyed = false;
    let lastPointer = null;

    const payload = () => previewSelectionOption(options.payload) || {};
    const editor = () => {
      const candidate = resolveRoot(previewSelectionOption(options.editor));
      return candidate && roots.some(root => root.contains(candidate)) ? candidate : null;
    };
    const source = () => {
      const explicit = previewSelectionOption(options.content);
      if (explicit !== undefined && explicit !== null) return String(explicit);
      const activeEditor = editor();
      if (activeEditor && "value" in activeEditor) return String(activeEditor.value || "");
      return String(payload()?.content || "");
    };
    const location = () => {
      const currentPayload = payload();
      return {
        path: String(
          previewSelectionOption(options.path, currentPayload)
          || currentPayload.path
          || currentPayload.rel_path
          || "",
        ).trim(),
        title: String(
          previewSelectionOption(options.title, currentPayload)
          || currentPayload.title
          || currentPayload.name
          || window.PortalI18n.t("Preview"),
        ).trim(),
      };
    };
    const disabled = () => previewSelectionOption(options.disabled, payload()) === true;
    const rootContaining = node => roots.find(root => root === node || root.contains(node)) || null;
    const pointerRect = (event, fallback) => {
      const x = Number(event?.clientX);
      const y = Number(event?.clientY);
      if (Number.isFinite(x) && Number.isFinite(y) && (x || y)) {
        return { left: x, right: x, top: y, bottom: y, width: 0, height: 0 };
      }
      const rect = fallback?.getBoundingClientRect?.();
      if (!rect) return null;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + Math.min(rect.height / 2, 56);
      return {
        left: centerX,
        right: centerX,
        top: centerY,
        bottom: centerY,
        width: 0,
        height: 0,
      };
    };
    const editorSelection = event => {
      const activeEditor = editor();
      if (!activeEditor || !("selectionStart" in activeEditor)) return null;
      const start = Number(activeEditor.selectionStart || 0);
      const end = Number(activeEditor.selectionEnd || 0);
      if (end <= start) return null;
      const text = String(activeEditor.value || "").slice(start, end).trim();
      if (!text) return null;
      const lineRange = previewSelectionLineRangeFromEditor(activeEditor, start, end);
      return {
        ...location(),
        text,
        lineStart: lineRange.start,
        lineEnd: lineRange.end,
        rect: pointerRect(event || lastPointer, activeEditor),
        source: "editor",
      };
    };
    const providedSelection = event => {
      const candidate = previewSelectionOption(options.selection, event);
      if (!candidate || typeof candidate !== "object") return null;
      const text = String(candidate.text || "").trim();
      if (!text) return null;
      return {
        ...location(),
        ...candidate,
        text,
        lineStart: Number(candidate.lineStart || candidate.line_start || 0),
        lineEnd: Number(candidate.lineEnd || candidate.line_end || candidate.lineStart || candidate.line_start || 0),
        rect: candidate.rect || pointerRect(event || lastPointer, roots[0]),
        source: String(candidate.source || "editor"),
      };
    };
    const domSelection = () => {
      const selection = window.getSelection?.();
      if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
      const selectedText = String(selection.toString() || "").trim();
      if (!selectedText) return null;
      const range = selection.getRangeAt(0);
      if (!rootContaining(range.commonAncestorContainer)) return null;
      const lineRange = previewSelectionLineRangeFromDom(range, roots, source(), selectedText);
      const rect = range.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) return null;
      return {
        ...location(),
        text: selectedText,
        lineStart: lineRange.start,
        lineEnd: lineRange.end,
        rect,
        source: "visual",
      };
    };
    const selectionInfo = (event = null) => {
      if (destroyed || disabled()) return null;
      const supplied = providedSelection(event);
      if (supplied) return supplied;
      const eventTarget = event?.target;
      const activeEditor = editor();
      if (
        activeEditor
        && (
          eventTarget === activeEditor
          || document.activeElement === activeEditor
          || activeEditor.contains?.(eventTarget)
        )
      ) {
        return editorSelection(event);
      }
      return domSelection() || editorSelection(event);
    };
    const hide = () => {
      window.clearTimeout(timer);
      timer = 0;
      current = null;
      toolbar.hidden = true;
      return true;
    };
    const position = info => {
      const rect = info?.rect;
      if (!rect) return hide();
      toolbar.hidden = false;
      const width = toolbar.offsetWidth || 92;
      const height = toolbar.offsetHeight || 30;
      const above = rect.top - height - 8;
      const top = above >= 8
        ? above
        : Math.min(window.innerHeight - height - 8, rect.bottom + 8);
      const center = rect.left + (Number(rect.width || 0) / 2);
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, center - width / 2));
      toolbar.style.top = `${Math.max(8, top)}px`;
      toolbar.style.left = `${left}px`;
      return true;
    };
    const show = event => {
      const info = selectionInfo(event);
      if (!info) return hide();
      current = info;
      position(info);
      options.onShow?.(Object.freeze({ ...info }));
      return info;
    };
    const scheduleShow = event => {
      if (event && Number.isFinite(Number(event.clientX)) && Number.isFinite(Number(event.clientY))) {
        lastPointer = {
          clientX: Number(event.clientX),
          clientY: Number(event.clientY),
          target: event.target,
        };
      }
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = 0;
        show(event || lastPointer);
      }, 0);
    };
    const workspace = () => previewSelectionOption(options.chatWorkspace) || null;
    const clearSelection = info => {
      if (info?.source === "editor") {
        const activeEditor = editor();
        if (activeEditor && "selectionEnd" in activeEditor) {
          const end = Number(activeEditor.selectionEnd || 0);
          activeEditor.setSelectionRange?.(end, end);
        } else if (typeof options.clearSelection === "function") {
          options.clearSelection(info);
        }
      } else {
        window.getSelection?.()?.removeAllRanges?.();
      }
    };
    const addToChat = () => {
      const info = current || selectionInfo();
      const targetWorkspace = workspace();
      if (!info || !targetWorkspace?.setAttachments) return false;
      const attachment = createPreviewReferenceAttachment(info);
      const previous = targetWorkspace.attachments?.() || [];
      targetWorkspace.setAttachments([
        ...previous.filter(item => String(item?.id || "") !== attachment.id),
        attachment,
      ]);
      targetWorkspace.saveDraft?.();
      clearSelection(info);
      hide();
      options.onAdd?.(Object.freeze({ ...info }), attachment, targetWorkspace);
      window.requestAnimationFrame(() => targetWorkspace?.element?.("input")?.focus?.());
      return attachment;
    };
    const handleMouseDown = event => {
      if (event.target === toolbar || toolbar.contains(event.target)) return;
      hide();
    };
    const handleMouseUp = event => scheduleShow(event);
    const handleKeyUp = event => {
      if (event.shiftKey || ["Shift", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        scheduleShow(event);
      }
    };
    const handleSelect = event => scheduleShow(event);
    const handleOutsideMouseDown = event => {
      if (toolbar.contains(event.target) || roots.some(root => root.contains(event.target))) return;
      hide();
    };
    const handleToolbarMouseDown = event => event.preventDefault();
    const handleToolbarClick = event => {
      event.preventDefault();
      addToChat();
    };

    roots.forEach(root => {
      root.addEventListener("mousedown", handleMouseDown);
      root.addEventListener("mouseup", handleMouseUp);
      root.addEventListener("keyup", handleKeyUp);
      root.addEventListener("select", handleSelect, true);
    });
    document.addEventListener("mousedown", handleOutsideMouseDown);
    toolbar.addEventListener("mousedown", handleToolbarMouseDown);
    toolbar.addEventListener("click", handleToolbarClick);

    return Object.freeze({
      addToChat,
      attachment: () => current ? createPreviewReferenceAttachment(current) : null,
      current: () => current ? Object.freeze({ ...current }) : null,
      destroy() {
        destroyed = true;
        hide();
        roots.forEach(root => {
          root.removeEventListener("mousedown", handleMouseDown);
          root.removeEventListener("mouseup", handleMouseUp);
          root.removeEventListener("keyup", handleKeyUp);
          root.removeEventListener("select", handleSelect, true);
        });
        document.removeEventListener("mousedown", handleOutsideMouseDown);
        toolbar.removeEventListener("mousedown", handleToolbarMouseDown);
        toolbar.removeEventListener("click", handleToolbarClick);
        if (ownsToolbar) toolbar.remove();
      },
      hide,
      selectionInfo,
      show,
      toolbar,
    });
  }

  function createDocumentEditorTools(options = {}) {
    const histories = new WeakMap();
    const storageKey = String(options.storageKey || "").trim();
    let wordWrap = options.defaultWordWrap !== false;
    if (storageKey) {
      const stored = window.PortalState.storage.readText(storageKey, "");
      if (stored === "0") wordWrap = false;
      if (stored === "1") wordWrap = true;
    }

    const reset = (documentState, value = "") => {
      if (!documentState || typeof documentState !== "object") return null;
      const history = {
        current: String(value ?? ""),
        undo: [],
        redo: [],
        lastInputAt: 0,
        lastInputGroup: "",
      };
      histories.set(documentState, history);
      return history;
    };
    const history = (documentState, value = "") => {
      if (!documentState || typeof documentState !== "object") return null;
      return histories.get(documentState) || reset(documentState, value);
    };
    const inputGroup = inputType => {
      const type = String(inputType || "");
      if (["insertText", "insertLineBreak", "insertParagraph"].includes(type)) return "insert";
      if (["deleteContentBackward", "deleteContentForward"].includes(type)) return "delete";
      return type || "replace";
    };
    const record = (documentState, value, inputType = "") => {
      const current = history(documentState, value);
      if (!current) return null;
      const next = String(value ?? "");
      if (next === current.current) return current;
      const now = performance.now();
      const group = inputGroup(inputType);
      const canGroup = (
        current.undo.length > 0
        && current.lastInputGroup === group
        && ["insert", "delete"].includes(group)
        && now - current.lastInputAt < Number(options.groupWindowMs || 700)
      );
      if (!canGroup) {
        current.undo.push(current.current);
        const limit = Math.max(1, Number(options.historyLimit || 120));
        if (current.undo.length > limit) current.undo.splice(0, current.undo.length - limit);
      }
      current.current = next;
      current.redo = [];
      current.lastInputAt = now;
      current.lastInputGroup = group;
      return current;
    };
    const run = (documentState, command) => {
      const current = histories.get(documentState);
      if (!current || !["undo", "redo"].includes(command)) return null;
      const source = command === "undo" ? current.undo : current.redo;
      const target = command === "undo" ? current.redo : current.undo;
      if (!source.length) return null;
      target.push(current.current);
      const next = String(source.pop() ?? "");
      current.current = next;
      current.lastInputAt = 0;
      current.lastInputGroup = "";
      return next;
    };
    const setWordWrap = enabled => {
      wordWrap = enabled !== false;
      if (storageKey) {
        window.PortalState.storage.writeText(storageKey, wordWrap ? "1" : "0");
      }
      options.onWordWrapChange?.(wordWrap);
      return wordWrap;
    };
    const toggleWordWrap = () => setWordWrap(!wordWrap);
    const applyWordWrap = (config = {}) => {
      const className = String(config.noWrapClass || "noWrap");
      const roots = Array.isArray(config.roots) ? config.roots : [config.root];
      roots.map(resolveRoot).filter(Boolean).forEach(root => {
        root.classList.toggle(className, !wordWrap);
      });
      const button = resolveRoot(config.button);
      if (button) {
        button.setAttribute("aria-pressed", wordWrap ? "true" : "false");
        button.title = wordWrap ? window.PortalI18n.t("关闭自动换行") : window.PortalI18n.t("自动换行");
      }
      const editor = resolveRoot(config.editor);
      if (editor) {
        editor.wrap = wordWrap ? "soft" : "off";
        if (wordWrap) editor.scrollLeft = 0;
      }
      return wordWrap;
    };

    return Object.freeze({
      applyWordWrap,
      history,
      record,
      reset,
      run,
      setWordWrap,
      toggleWordWrap,
      wordWrap: () => wordWrap,
    });
  }

  window.PortalDocumentSession = Object.freeze({
    create: createDocumentSession,
  });
  const authoredPanelPackage = {
    type: "authored",
    version: 1,
    component: "PortalWorkspaceSurface.AuthoredPanel",
    presentation: { title: "Editor", icon: "code" },
    sizing: { minWidth: 360, minHeight: 180 },
    instances: "multiple",
    capabilities: ["open-resource", "edit", "dirty-state", "save", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["documentTarget", "commandContext"] },
    persistence: { schemaVersion: 1 },
    create: mountAuthoredPanel,
  };
  const previewPanelPackage = {
    type: "preview",
    version: 1,
    component: "PortalWorkspaceSurface.PreviewPanel",
    presentation: { title: window.PortalI18n.t("Preview"), icon: "window" },
    sizing: { minWidth: 280, minHeight: 180 },
    instances: "multiple",
    capabilities: ["open-resource", "preview", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["documentTarget", "commandContext"] },
    commands: [{
      id: "back",
      title: window.PortalI18n.t("返回上一个预览文件"),
      capability: "preview",
      execute: ({ panel }) => panel.goBack(),
    }, {
      id: "reload",
      title: window.PortalI18n.t("重新加载 Preview"),
      capability: "preview",
      execute: ({ panel }) => panel.reload(),
    }],
    persistence: { schemaVersion: 1 },
    create: mountWorkbenchPreviewPanel,
  };
  const terminalPanelPackage = {
    type: "terminal",
    version: 1,
    component: "PortalWorkspaceSurface.TerminalPanel",
    presentation: { title: window.PortalI18n.t("Terminal"), icon: "terminal" },
    sizing: { minWidth: 280, minHeight: 160 },
    instances: "multiple",
    capabilities: ["terminal", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["workspace", "commandContext"] },
    commands: [{
      id: "interrupt",
      title: window.PortalI18n.t("中断 Terminal 任务"),
      capability: "terminal",
      execute: ({ panel }) => panel.control("interrupt"),
    }, {
      id: "restart",
      title: window.PortalI18n.t("新建 Terminal Shell"),
      capability: "terminal",
      execute: ({ panel }) => panel.control("restart"),
    }],
    persistence: { schemaVersion: 1 },
    create: mountWorkbenchTerminalPanel,
  };
  const panelPackages = Object.freeze({
    authored: Object.freeze(authoredPanelPackage),
    preview: Object.freeze(previewPanelPackage),
    terminal: Object.freeze(terminalPanelPackage),
  });
  window.PortalWorkspaceSurface = Object.freeze({
    createDocumentEditorTools,
    createDocumentTabsController,
    renderDocumentTabs,
    renderExplorer,
    panelPackages,
  });
  if (window.PortalWorkbench) {
    if (typeof window.PortalWorkbench.registerPanelPackage !== "function") {
      throw new Error("PortalWorkspaceSurface requires the PortalWorkbench Panel Package registry.");
    }
    Object.values(panelPackages)
      .forEach(panelPackage => window.PortalWorkbench.registerPanelPackage(panelPackage));
  }
})();
