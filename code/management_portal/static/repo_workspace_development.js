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
    async function addSelection(reference) {
      focus("chat");
      const chat = panel("chat");
      await chat.openSurface({ kb_id: repoId });
      if (disposed) return;
      const selection = reference.selection || {};
      const context = { repo_id: repoId, root: overview.checkout_path,
        branch: overview.current_branch || "", file: reference };
      chat.workspace.addReference({
        title: `${reference.path} L${selection.start_line || 1}–${selection.end_line || selection.start_line || 1}`,
        path: `${repoId}/file/${reference.path}`, text: JSON.stringify(context, null, 2),
        line_start: selection.start_line || 1, line_end: selection.end_line || selection.start_line || 1,
      });
      chat.workspace.focusComposer();
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
      resolveResident: target => api.get(`/knowledge/api/item-chat?kb_id=${encodeURIComponent(target.kb_id)}`),
      resolveBeforeSend: (target, payload) => api.post("/knowledge/api/item-chat/prepare", {
        kb_id: target.kb_id, message: payload.text || payload.plainText,
      }),
      resolveBeforeSettings: target => api.post("/knowledge/api/item-chat/prepare", { kb_id: target.kb_id, purpose: "settings" }),
      initialContext: (_target, detail) => ({ context_text: `${detail.initial_context?.context_text || ""}\nRepo: ${repoId}\n工程目录：${overview.checkout_path || ""}` }),
      emptyText: t("选中代码后使用“加入聊天”，讨论或修改当前仓库。"), enableLinkPreview: true, onError: reportError,
    };
    const descriptors = [
      { id: "files", type: "repository-files", title: t("文件树"), options: shared },
      ...(overview.capabilities?.search_files ? [{ id: "search", type: "repository-search", title: t("搜索"), options: shared }] : []),
      ...(overview.capabilities?.git ? [{ id: "git", type: "repository-git", title: t("源代码管理"), options: shared }] : []),
      { id: "source", type: "source-editor", title: t("代码"), options: {
        readFile, scopeKey: repoId,
        async saveFile(path, content, expected_hash) {
          const result = await api.post(`${base}/file`, { path, content, expected_hash });
          void panel("git")?.refresh?.();
          return result;
        },
        onSelection: reference => addSelection(reference).catch(reportError),
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
    const icons = { files: "file-text", search: "search", git: "branch", source: "code", preview: "eye", chat: "chat-room" };
    rail = window.PortalWorkbench.createActivityRail({
      id: `repo-development-${repoId}`, root: tools, label: t("开发工作台工具"), activeView: selectedTool,
      views: [...descriptors.map(descriptor => ({ id: descriptor.id, label: descriptor.title, icon: icons[descriptor.id] })),
        { id: "refresh", label: t("刷新文件状态"), icon: "refresh" }],
      onViewSelect(id) {
        if (id === "refresh") { rail.setActiveView(selectedTool); void refresh(); return; }
        selectedTool = id;
        focus(id);
        if (id === "preview") {
          const document = panel("source").activeDocument();
          if (document) Promise.resolve(preview(document)).catch(reportError);
        }
      },
    });
    void panel("chat").openSurface({ kb_id: repoId }).catch(reportError);
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
      destroy() { disposed = true; observer.disconnect(); clearInterval(timer); window.removeEventListener("focus", onFocus); rail.destroy(); workbench.destroy(); },
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
