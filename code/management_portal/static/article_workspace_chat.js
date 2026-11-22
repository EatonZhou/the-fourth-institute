(() => {
  "use strict";

  if (window.PortalArticleWorkspaceChat) return;

  // The Article's Chat: its Workspace in the side view, the context of the
  // open document and selection, and references added to the message.
  const DEPENDENCIES = Object.freeze([
    "outputView",
    "$", "activeDocument", "articleProjects", "articleRoot", "documentIsDirty", "editorPosition",
    "editorSelectionContext", "editorValue", "languageForPath", "selectedArticle", "setSideView", "showToast", "state",
  ]);

  function createChat(dependencies) {
    const outputView = dependencies.outputView;
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceChat is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleProjects, articleRoot, documentIsDirty, editorPosition,
      editorSelectionContext, editorValue, languageForPath, selectedArticle, setSideView, showToast, state,
    } = dependencies;
    const automaticReferences = new Map();
    let selectionRevision = 0;

    function articleChatContext() {
      const article = selectedArticle();
      const documentState = activeDocument();
      const cursor = editorPosition();
      const selection = editorSelectionContext();
      return {
        article_id: state.selectedId,
        title: article?.title || "",
        root: articleRoot(article)?.path || article?.canonical_root || "",
        project_ids: articleProjects(article),
        file: {
          path: state.openFile,
          revision: documentState?.revision || "",
          dirty: documentIsDirty(documentState),
          language: languageForPath(state.openFile),
          cursor,
          selection: selection || {
            start_line: cursor.line,
            start_column: cursor.column,
            end_line: cursor.line,
            end_column: cursor.column,
            text: "",
            truncated: false,
          },
        },
        diagnostic: state.activeDiagnostic || {},
        pdf: outputView().getSyncLocation() || {},
        build: state.build ? {
          status: state.build.status || "",
          compiler: state.build.compiler || "",
          compiled_at: state.build.compiled_at || "",
          counts: {
            error: Number(state.build.counts?.error || 0),
            warning: Number(state.build.counts?.warning || 0),
            info: Number(state.build.counts?.info || 0),
          },
        } : {},
      };
    }

    function addArticleChatReference(context = articleChatContext(), options) {
      const selection = context.file?.selection;
      const path = context.file?.path ? `${context.article_id}/file/${context.file.path}` : context.article_id;
      return state.chatController?.addReference({
        title: context.file?.path || context.article_id,
        path,
        text: JSON.stringify(context, null, 2),
        ...(selection ? { line_start: selection.start_line, line_end: selection.end_line } : {}),
      }, options);
    }

    async function bringCurrentFileToChat() {
      if (!state.selectedId || !state.openFile) return;
      const articleId = state.selectedId;
      const context = articleChatContext();
      context.file.content = editorValue();
      context.file.selection = null;
      const controller = await setSideView("chat");
      if (!controller || articleId !== state.selectedId) return;
      addArticleChatReference(context);
      controller.focusComposer();
    }

    async function bringSelectionToChat() {
      if (!state.selectedId || !state.openFile) return;
      const selection = editorSelectionContext(Infinity);
      if (!selection?.text) return;
      const revision = ++selectionRevision;
      const context = articleChatContext();
      context.file.selection = selection;
      const controller = await loadArticleChat();
      if (!controller || context.article_id !== state.selectedId || revision !== selectionRevision) return;
      const item = addArticleChatReference(context, {
        focus: false, replaceId: automaticReferences.get(context.article_id) || "",
      });
      automaticReferences.set(context.article_id, item?.id || "");
    }

    function initChatWorkspace() {
      if (state.chatController) return state.chatController;
      const root = $("articleChatWorkspace");
      state.chatPanel = window.PortalWorkbench.createPanel("chat", {
        root,
        id: "articleWorkspaceChat",
        autoLoad: false,
        refsScope: "articleWorkspaceChat",
        title: window.PortalI18n.t("Chat Workspace"),
        draftOwner: () => $("portalUsername")?.textContent || "anonymous",
        resourceProjectContext: true,
        resolveResident: target => window.PortalAPI.get(`/knowledge/api/item-chat?${new URLSearchParams({ kb_id: target.kb_id || "", ...(target.project_id ? { project_id: target.project_id } : {}) })}`),
        resolveBeforeSend: (target, payload) => window.PortalAPI.post("/knowledge/api/item-chat/prepare", {
          kb_id: target.kb_id, ...(target.project_id ? { project_id: target.project_id } : {}), purpose: "send", message: payload.text || payload.plainText,
        }),
        resolveBeforeSettings: target => window.PortalAPI.post("/knowledge/api/item-chat/prepare", {
          kb_id: target.kb_id, ...(target.project_id ? { project_id: target.project_id } : {}), purpose: "settings",
        }),
        initialContext: (target, detail) => ({
          context_text: `${detail.initial_context?.context_text || ""}\nArticle: ${target.kb_id}\n工程目录：${articleRoot(selectedArticle())?.path || selectedArticle()?.canonical_root || ""}`,
        }),
        enableLinkPreview: true,
        emptyText: window.PortalI18n.t("可以围绕当前 Article 的结构、论证、LaTeX 修改或编译问题和 Agent 讨论。"),
        onError: error => showToast(error.message || error, "error"),
      });
      state.chatController = state.chatPanel?.workspace || null;

      return state.chatController;
    }

    async function loadArticleChat({ force = false } = {}) {
      if (!state.selectedId) {
        initChatWorkspace();
        state.chatPanel?.showEmpty?.({
          emptyText: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
          placeholder: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
        });
        return null;
      }
      const id = state.selectedId;
      if (!force && state.chatLoadRequest?.id === id) return state.chatLoadRequest.promise;
      initChatWorkspace();
      const panel = state.chatPanel;
      const request = { id, promise: null };
      state.chatLoadRequest = request;
      request.promise = Promise.resolve(panel?.openSurface?.({ kb_id: id }, { force }))
        .then(detail => detail && id === state.selectedId ? state.chatController : null)
        .finally(() => {
          if (state.chatLoadRequest === request) state.chatLoadRequest = null;
        });
      return request.promise;
    }

    return Object.freeze({
      addArticleChatReference, bringCurrentFileToChat, bringSelectionToChat, loadArticleChat,
    });
  }

  window.PortalArticleWorkspaceChat = Object.freeze({ createChat, dependencies: DEPENDENCIES });
})();
