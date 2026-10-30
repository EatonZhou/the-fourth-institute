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

  // The Preview panel, the workspace browser, document sessions, document tabs
  // and selection controllers below create their parts from module scripts
  // that load before they run (the "portal-workspace-surface" script group);
  // surfacePart(name) returns what such a script registers.
  function surfacePart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

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
  // mountPreviewWorkspaceSurface builds the workspace browser of a Preview
  // panel. Its parts live in module scripts that load before it runs:
  //   portal_workspace_surface_browser_markup.js  the browser's ids, markup
  //     and document surface
  //   portal_workspace_surface_browser_explorer.js  the file tree section:
  //     folding, height, splitter and resizes
  // A browser without a file tree returns before the second part is created.
  function mountPreviewWorkspaceSurface(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new Error("Preview workspace surface root required");
    const {
      actionId, component, explorerEnabled, ids,
    } = surfacePart("PortalWorkspaceSurfaceBrowserMarkup").createBrowserMarkup({
      defaultPreviewPanelIds, defaultWorkspacePreviewIds, escapeHtml, mountPreviewDocumentSurface, options, root,
      surfaceElement, workspacePreviewActionId,
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
    const {
      applyLayout, handleResize, setSectionCollapsed, toggleSection, unbindSplitter,
    } = surfacePart("PortalWorkspaceSurfaceBrowserExplorer").createBrowserExplorer({
      ids, options, root, surfaceElement,
    });
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
  // mountWorkbenchPreviewPanel builds a Preview panel. Its parts live in module
  // scripts that load before it runs:
  //   portal_workspace_surface_panel_chrome.js  the panel's ids, markup and
  //     workspace browser
  //   portal_workspace_surface_panel_ownership.js  the sessions, tab sets,
  //     selections and Preview hosts the panel owns, and persistence changes
  //   portal_workspace_surface_panel_navigation.js  persisted locators, the
  //     history of shown resources and the back button
  //   portal_workspace_surface_panel_document.js  rendering, opening, clearing
  //     and going back
  //   portal_workspace_surface_panel_controller.js  the panel's controller and
  //     its toolbar buttons
  // It holds the state the parts share and creates the parts in that order; a
  // part that calls a function of a part created after it gets an arrow
  // forwarding the call.
  function mountWorkbenchPreviewPanel(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new TypeError("PortalWorkspaceSurface.mountWorkbenchPreviewPanel root required");
    // The state the parts share: the restored tab snapshots, whether the panel
    // is destroyed, the binding and document shown, the request revision, the
    // navigation history, whether going back is busy, the navigation adapter
    // and a binding waiting for its adapter.
    const previewPanelState = {
      restoredTabSnapshots: [], destroyed: false, binding: null, documentValue: null, requestRevision: 0,
      navigationHistory: [], navigationBusy: false, navigationAdapter: null, pendingBindingRestore: null,
    };
    const { ids, surface } = surfacePart("PortalWorkspaceSurfacePanelChrome").createPanelChrome({
      defaultPreviewPanelIds, defaultWorkspacePreviewIds, escapeHtml, mountPreviewWorkspaceSurface, options, root,
      workspacePreviewActionKeys,
    });
    const {
      createOwnedDocumentSession, createOwnedSelectionController, createOwnedTabsController, documentSessions,
      notifyPersistence, previewHostDisposers, registerOwnedPreviewHost, selectionControllers, tabControllers,
    } = surfacePart("PortalWorkspaceSurfacePanelOwnership").createPanelOwnership({
      clearPreview: (...args) => clearPreview(...args), createDocumentSession, createDocumentTabsController,
      createPreviewSelectionController, options, previewPanelState,
    });
    const {
      hasPreviewTarget, navigationLocator, needsNavigationAdapter, normalizeNavigationHistory,
      rememberPreviousResource, restoreNavigationResource, sanitizeNavigationLocator, updateBackAction,
    } = surfacePart("PortalWorkspaceSurfacePanelNavigation").createPanelNavigation({
      ids, options, previewPanelState, root,
    });
    const {
      clearPreview, goBack, openResource, previewRoot, renderDocument,
    } = surfacePart("PortalWorkspaceSurfacePanelDocument").createPanelDocument({
      hasPreviewTarget, ids, notifyPersistence, options, previewPanelState, rememberPreviousResource,
      restoreNavigationResource, root, surface, updateBackAction,
    });
    const { bindToolbar, controller } = surfacePart("PortalWorkspaceSurfacePanelController").createPanelController({
      clearPreview, createDocumentEditorTools, createOwnedDocumentSession, createOwnedSelectionController,
      createOwnedTabsController, documentSessions, goBack, hasPreviewTarget, ids, navigationLocator,
      needsNavigationAdapter, normalizeNavigationHistory, openResource, options, previewHostDisposers,
      previewPanelState, previewRoot, registerOwnedPreviewHost, restoreNavigationResource, root,
      sanitizeNavigationLocator, selectionControllers, surface, tabControllers, updateBackAction,
    });
    root.__portalWorkbenchPreviewPanel = controller;
    bindToolbar();
    updateBackAction();
    if (previewPanelState.documentValue) void renderDocument(previewPanelState.documentValue).catch(error => surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`));
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

  // createDocumentSession creates a document session. Its parts live in module
  // scripts that load before it runs:
  //   portal_workspace_surface_session_transitions.js  snapshots, transitions,
  //     the discard guard, invalidation, listeners and destroying
  //   portal_workspace_surface_session_loading.js  presenting, showing and
  //     opening documents
  //   portal_workspace_surface_session_editing.js  editing, saving and the
  //     allowed actions
  function createDocumentSession(options = {}) {
    // The state the parts share: the request, save and open counters, whether
    // the session is destroyed, the controller aborting the current read and
    // the session's state.
    const documentSessionState = {
      requestId: 0, saveId: 0, openSequence: 0, destroyed: false, abortController: null, state: null,
    };

    const {
      destroy, emit, guardDiscard, invalidate, setReadError, snapshot, subscribe, transition,
    } = surfacePart("PortalWorkspaceSurfaceSessionTransitions").createSessionTransitions({
      documentSessionState, options,
    });
    const { open, present, show } = surfacePart("PortalWorkspaceSurfaceSessionLoading").createSessionLoading({
      documentSessionState, guardDiscard, invalidate, options, snapshot, transition,
    });
    const {
      actionState, beginEdit, cancelEdit, save, updateDraft,
    } = surfacePart("PortalWorkspaceSurfaceSessionEditing").createSessionEditing({
      documentSessionState, guardDiscard, options, snapshot, transition,
    });

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

  // createDocumentTabsController creates a set of document tabs. Its parts live
  // in module scripts that load before it runs:
  //   portal_workspace_surface_tabs_documents.js  the documents, the active
  //     one, snapshots, change notices and errors
  //   portal_workspace_surface_tabs_view.js  drawing the tabs and the pin
  //     button
  //   portal_workspace_surface_tabs_opening.js  activating, opening, closing
  //     and pinning
  //   portal_workspace_surface_tabs_restore.js  restoring persisted tabs and
  //     destroying the set
  // A part that calls a function of a part created after it gets an arrow
  // forwarding the call.
  function createDocumentTabsController(options = {}) {
    // The state the parts share: the navigation counter, the tab order, the
    // active key and the counters of restores started and of the one running.
    const documentTabsState = {
      navigationSequence: 0, tabs: [], activeKey: "", restoreSequence: 0, restoringSequence: 0,
    };

    const {
      active, documents, get, isActive, notify, orderedDocuments, pendingSessions, persistenceSnapshot, reportError,
      restoreToken, snapshot,
    } = surfacePart("PortalWorkspaceSurfaceTabsDocuments").createTabsDocuments({ documentTabsState, options });
    const { render } = surfacePart("PortalWorkspaceSurfaceTabsView").createTabsView({
      activate: (...args) => activate(...args), active, close: (...args) => close(...args), documentTabsState,
      options, orderedDocuments, renderDocumentTabs, reportError, resolveRoot, snapshot,
    });
    const { activate, close, open, togglePin } = surfacePart("PortalWorkspaceSurfaceTabsOpening").createTabsOpening({
      active, createDocumentSession, documentTabsState, documents, notify, options, pendingSessions, render,
      reportError, restoreToken,
    });
    const { destroy, restorePersistence } = surfacePart("PortalWorkspaceSurfaceTabsRestore").createTabsRestore({
      activate, documentTabsState, documents, notify, open, options, orderedDocuments, pendingSessions, render,
      restoreToken, snapshot,
    });

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
      isRestoring: () => documentTabsState.restoringSequence > 0 && documentTabsState.restoringSequence === documentTabsState.restoreSequence,
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

  // createPreviewSelectionController follows selections in a Preview and offers
  // to send them to a conversation. Its parts live in module scripts that load
  // before it runs:
  //   portal_workspace_surface_selection_info.js  what is selected, and where
  //   portal_workspace_surface_selection_toolbar.js  the toolbar and showing
  //     it at the selection
  //   portal_workspace_surface_selection_chat.js  sending the selection to a
  //     conversation
  //   portal_workspace_surface_selection_events.js  the events it follows
  function createPreviewSelectionController(options = {}) {
    const configuredRoots = Array.isArray(options.roots) ? options.roots : [options.root];
    const roots = configuredRoots.map(resolveRoot).filter(Boolean);
    if (!roots.length) return null;
    // The state the parts share: the selection shown, the pending show,
    // whether the controller is destroyed and the last pointer position.
    const previewSelectionState = {
      current: null, timer: 0, destroyed: false, lastPointer: null,
    };

    const { editor, selectionInfo } = surfacePart("PortalWorkspaceSurfaceSelectionInfo").createSelectionInfo({
      options, previewSelectionLineRangeFromDom, previewSelectionLineRangeFromEditor, previewSelectionOption,
      previewSelectionState, resolveRoot, roots,
    });
    const {
      hide, ownsToolbar, scheduleShow, show, toolbar,
    } = surfacePart("PortalWorkspaceSurfaceSelectionToolbar").createSelectionToolbar({
      options, previewSelectionState, resolveRoot, selectionInfo,
    });
    const { addToChat } = surfacePart("PortalWorkspaceSurfaceSelectionChat").createSelectionChat({
      createPreviewReferenceAttachment, editor, hide, options, previewSelectionOption, previewSelectionState,
      selectionInfo,
    });
    const {
      handleKeyUp, handleMouseDown, handleMouseUp, handleOutsideMouseDown, handleSelect, handleToolbarClick,
      handleToolbarMouseDown,
    } = surfacePart("PortalWorkspaceSurfaceSelectionEvents").createSelectionEvents({
      addToChat, hide, roots, scheduleShow, toolbar,
    });

    return Object.freeze({
      addToChat,
      attachment: () => previewSelectionState.current ? createPreviewReferenceAttachment(previewSelectionState.current) : null,
      current: () => previewSelectionState.current ? Object.freeze({ ...previewSelectionState.current }) : null,
      destroy() {
        previewSelectionState.destroyed = true;
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
