(() => {
  "use strict";
  if (window.PortalRepoDevelopment) return;
  const sessions = new Map();
  const t = text => window.PortalI18n.t(text);

  function createSession({ repoId, overview, api, onError, renderMarkdown, onOpenChanges }) {
    window.PortalRepositoryPanels.register();
    window.PortalSourceEditorPanel.register();
    const root = document.createElement("div");
    root.className = "repoDevelopment portalWorkbenchPageLayout";
    const tools = document.createElement("nav");
    tools.className = "repoDevelopmentTools";
    tools.setAttribute("aria-label", t("开发工作台工具"));
    const host = document.createElement("div");
    host.className = "repoDevelopmentWorkbench";
    root.append(tools, host);
    const base = `/api/repos/${encodeURIComponent(repoId)}/workspace`;
    let workbench, rail, selectedTool = "files", previewDocument = "", refreshing = null, visible = false, disposed = false;
    let chatReady, automaticReferenceId = "", selectionRevision = 0;
    const reportError = error => onError?.(error);
    const panel = id => workbench?.getPanel(id);
    const focus = id => { selectedTool = id; workbench?.focusPanel(id); rail?.setActiveView(id); };
    const readFile = path => api.get(`${base}/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    async function openFile(path) {
      focus("source");
      await panel("source").openFile(path);
    }
    async function refresh() {
      if (disposed || !visible) return;
      if (refreshing) return refreshing;
      refreshing = Promise.all(["source", "files", "git"].map(id => panel(id)?.refresh?.()))
        .catch(reportError).finally(() => { refreshing = null; });
      return refreshing;
    }
    function ensureChat() {
      if (!chatReady) chatReady = panel("chat").openSurface({ kb_id: repoId }).catch(error => {
        chatReady = null;
        throw error;
      });
      return chatReady;
    }
    async function addReference(reference, { automatic = false } = {}) {
      const revision = automatic ? ++selectionRevision : selectionRevision;
      if (!automatic) focus("chat");
      const chat = panel("chat");
      await ensureChat();
      if (disposed || (automatic && revision !== selectionRevision)) return;
      const selection = reference.selection;
      const context = { repo_id: repoId, root: overview.checkout_path,
        branch: overview.current_branch || "", file: reference };
      const text = JSON.stringify(context, null, 2);
      const automaticItem = chat.workspace.attachments().find(item => item.id === automaticReferenceId);
      const replaceId = automatic || automaticItem?.reference_text === text ? automaticReferenceId : "";
      const item = chat.workspace.addReference({
        title: selection ? `${reference.path} L${selection.start_line}–${selection.end_line}` : reference.path,
        path: `${repoId}/file/${reference.path}`, text,
        ...(selection ? { line_start: selection.start_line, line_end: selection.end_line } : {}),
      }, { focus: !automatic, replaceId });
      if (automatic) automaticReferenceId = item?.id || "";
      else {
        if (replaceId) automaticReferenceId = "";
        chat.workspace.focusComposer();
      }
    }
    function preview(document) {
      previewDocument = document.path;
      focus("preview");
      return panel("preview").setDocument(document);
    }
    const shared = { repoId, api, onOpenFile: openFile, onError: reportError, renderMarkdown,
      onOpenChanges, beforeCompile: () => panel("source").saveAll(),
      loadLatex: (path, compiler) => api.get(`${base}/latex?path=${encodeURIComponent(path)}${compiler ? `&compiler=${encodeURIComponent(compiler)}` : ""}`),
      async compileLatex(path, compiler) {
        try { return await api.post(`${base}/latex`, { path, ...(compiler ? { compiler } : {}) }); }
        catch (error) {
          if (error.status === 422 && error.data?.report) return error.data.report;
          throw error;
        }
      },
      async onDiff(path, diff) {
        focus("source");
        await panel("source").showGitDiff(path, diff);
      },
    };
    const chatOptions = {
      autoLoad: false, refsScope: `repo-development:${repoId}`,
      title: t("Chat Workspace"), draftOwner: () => window.PortalIdentity?.username?.() || "anonymous",
      resourceProjectContext: true,
      resolveResident: target => api.get(`/knowledge/api/item-chat?${new URLSearchParams({ kb_id: target.kb_id, ...(target.project_id ? { project_id: target.project_id } : {}) })}`),
      resolveBeforeSend: (target, payload) => api.post("/knowledge/api/item-chat/prepare", {
        kb_id: target.kb_id, ...(target.project_id ? { project_id: target.project_id } : {}), purpose: "send", message: payload.text || payload.plainText,
      }),
      resolveBeforeSettings: target => api.post("/knowledge/api/item-chat/prepare", { kb_id: target.kb_id, ...(target.project_id ? { project_id: target.project_id } : {}), purpose: "settings" }),
      initialContext: (_target, detail) => ({ context_text: `${detail.initial_context?.context_text || ""}\nRepo: ${repoId}\n工程目录：${overview.checkout_path || ""}` }),
      emptyText: t("鼠标选中代码后自动加入聊天；也可将当前文件加入聊天，讨论或修改当前仓库。"), enableLinkPreview: true, onError: reportError,
    };
    const descriptors = [
      { id: "files", type: "repository-files", collapseToRail: true, title: t("文件树"), options: shared },
      ...(overview.capabilities?.search_files ? [{ id: "search", type: "repository-search", collapseToRail: true, title: t("搜索"), options: shared }] : []),
      ...(overview.capabilities?.git ? [{ id: "git", type: "repository-git", collapseToRail: true, title: t("源代码管理"), options: shared }] : []),
      { id: "source", type: "source-editor", title: t("代码"), options: {
        readFile, scopeKey: repoId,
        async saveFile(path, content, expected_hash) {
          const result = await api.post(`${base}/file`, { path, content, expected_hash });
          void panel("git")?.refresh?.();
          return result;
        },
        onSelection: reference => addReference(reference).catch(reportError),
        onSelectionCommitted: reference => addReference(reference, { automatic: true }).catch(reportError),
        onFileReference: reference => addReference(reference).catch(reportError),
        onChange(document) {
          if (document?.path === previewDocument) {
            Promise.resolve(panel("preview")?.setDocument(document)).catch(reportError);
          }
        },
        onPreview: document => Promise.resolve(preview(document)).catch(reportError), onError: reportError,
      } },
      { id: "preview", type: "repository-preview", title: t("预览"), options: shared },
      { id: "chat", type: "chat", title: t("聊天"), options: chatOptions },
    ];
    const { split, stack } = window.PortalWorkbench.model;
    const navigationIds = descriptors.filter(d => ["files", "search", "git"].includes(d.id)).map(d => d.id);
    workbench = window.PortalWorkbench.create({
      id: `repo-development-${repoId}`, root: host, label: t("Repo 开发工作台"), initialEditors: descriptors,
      defaultLayout: split("horizontal", [stack(navigationIds, "files"), stack(["source", "preview"], "source"), stack(["chat"])], [1, 2.8, 1.5]),
      fitViewport: { bottom: 18, minHeight: 360 },
    });
    // Navigation is still owned by the Workbench layout: collapse its stacks
    // to the activity rail, keeping their active tabs, weights and runtimes.
    // A user-created stack containing an editor or chat is not a sidebar.
    function navigationStacks(node = workbench.getLayout()) {
      if (!node) return [];
      if (node.type === "stack") {
        return node.children.every(child => navigationIds.includes(child.panelId)) ? [node] : [];
      }
      return (node.children || []).flatMap(child => navigationStacks(child));
    }
    function toggleSidebar() {
      const stacks = navigationStacks();
      if (!stacks.length) {
        const hidden = navigationIds.find(id => !workbench.isPanelVisible(id));
        if (hidden) focus(hidden);
        return;
      }
      const expanded = stacks.filter(stack => !workbench.isPanelCollapsed(stack.activePanelId));
      if (expanded.length) {
        const focused = document.activeElement;
        expanded.forEach(stack => workbench.collapsePanel(stack.activePanelId));
        rail.setActiveView("");
        // Do not leave keyboard focus inside the collapsed navigation panel.
        if (focused && !focused.getClientRects().length) tools.querySelector('[data-activity-view="files"]')?.focus();
      } else {
        stacks.forEach(stack => workbench.expandPanel(stack.activePanelId));
        selectedTool = stacks[0].activePanelId;
        rail.setActiveView(selectedTool);
      }
    }
    const removeSidebarCommand = window.PortalCommandRegistry.root.register({
      id: `repo-development.${repoId}.toggle-sidebar`, title: t("显示或隐藏左侧栏"), category: "Repo",
      keybindings: ["Ctrl+B", "Meta+B"], allowInInput: true,
      enabled: () => {
        if (disposed || !root.isConnected || root.closest("[hidden]") || workbench.isLayoutLocked()) return false;
        const active = document.activeElement;
        if (active?.closest('[role="dialog"], dialog, .portalOverlay')) return false;
        const editable = active?.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]');
        // Monaco uses a textarea, but B is a layout shortcut in the editor.
        // Chat and other text inputs retain their own formatting shortcuts.
        return !editable || Boolean(root.contains(active) && active.closest(".monaco-editor"));
      },
      execute: ({ event }) => { if (!event?.repeat) toggleSidebar(); },
    });
    const icons = { files: "file-text", search: "search", git: "branch", source: "code", preview: "eye", chat: "chat-room" };
    rail = window.PortalWorkbench.createActivityRail({
      id: `repo-development-${repoId}`, root: tools, label: t("开发工作台工具"), activeView: selectedTool,
      views: [...descriptors.map(descriptor => ({ id: descriptor.id, label: descriptor.title, icon: icons[descriptor.id],
        title: navigationIds.includes(descriptor.id) ? `${descriptor.title} · Ctrl+B / ⌘B` : descriptor.title })),
        { id: "refresh", label: t("刷新文件状态"), icon: "refresh" }],
      onViewSelect(id, _rail, { repeated } = {}) {
        if (id === "refresh") { rail.setActiveView(selectedTool); void refresh(); return; }
        if (repeated && navigationStacks().some(stack => stack.activePanelId === id && !workbench.isPanelCollapsed(id))) {
          toggleSidebar();
          return;
        }
        selectedTool = id;
        focus(id);
        if (id === "preview") {
          const document = panel("source").activeDocument();
          if (document) Promise.resolve(preview(document)).catch(reportError);
        }
      },
    });
    void ensureChat().catch(reportError);
    const visibility = () => {
      const next = root.isConnected && !root.closest("[hidden]");
      if (next === visible) return;
      visible = next; workbench.setVisible(visible);
      if (visible) void refresh();
    };
    const observer = new MutationObserver(visibility);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 8000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return {
      root, workbench, openFile, refresh, hasDirty: () => panel("source")?.hasDirty?.() || false,
      destroy() { disposed = true; removeSidebarCommand(); observer.disconnect(); clearInterval(timer); window.removeEventListener("focus", onFocus); rail.destroy(); workbench.destroy(); },
    };
  }

  function mount(options) {
    let session = sessions.get(options.repoId);
    if (!session) { session = createSession(options); sessions.set(options.repoId, session); }
    options.root.append(session.root);
    if (options.initialFile) void session.openFile(options.initialFile).catch(options.onError);
    return session;
  }
  window.addEventListener("beforeunload", event => {
    if ([...sessions.values()].some(session => session.hasDirty())) { event.preventDefault(); event.returnValue = ""; }
  });
  window.PortalRepoDevelopment = Object.freeze({ mount });
})();
