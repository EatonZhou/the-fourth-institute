(() => {
  "use strict";

  if (window.PortalArticleWorkspaceChat) return;

  // The Article's Chat: its Workspace in the side view, the context of the
  // open document and selection, and references added to the message.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "articleProjects", "articleRoot", "documentIsDirty", "editorPosition",
    "editorSelectionContext", "languageForPath", "selectedArticle", "setSideView", "showToast", "state",
  ]);

  function createChat(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceChat is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleProjects, articleRoot, documentIsDirty, editorPosition,
      editorSelectionContext, languageForPath, selectedArticle, setSideView, showToast, state,
    } = dependencies;

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
        pdf: state.pdfSyncLocation || {},
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

    function addArticleChatReference() {
      const context = articleChatContext();
      const selection = context.file?.selection || {};
      const startLine = selection.start_line || context.file?.cursor?.line || 1;
      const endLine = selection.end_line || startLine;
      return state.chatController?.addReference({
        title: `${state.selectedId} L${startLine}–${endLine}`,
        path: state.selectedId,
        text: JSON.stringify(context, null, 2),
        line_start: startLine,
        line_end: endLine,
      });
    }

    async function bringCurrentContextToChat() {
      if (!state.selectedId) return;
      const articleId = state.selectedId;
      const selection = editorSelectionContext();
      const controller = await setSideView("chat");
      if (!controller || articleId !== state.selectedId) return;

      const prompt = selection?.text
        ? "请结合已附带的稿件选区，帮我审阅并给出可以直接落到 LaTeX 源码的修改建议。"
        : "请结合已附带的当前稿件位置和工程上下文，继续协助我完善这篇 Article。";
      const existing = String(controller?.draftText?.() || "").trim();
      if (!existing) controller?.setDraftText?.(prompt);
      addArticleChatReference();

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
        resolveResident: target => window.PortalAPI.get(`/knowledge/api/item-chat?kb_id=${encodeURIComponent(target.kb_id || "")}`),
        resolveBeforeSend: (target, payload) => window.PortalAPI.post("/knowledge/api/item-chat/prepare", {
          kb_id: target.kb_id, message: payload.text || payload.plainText,
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
      addArticleChatReference, bringCurrentContextToChat, loadArticleChat,
    });
  }

  window.PortalArticleWorkspaceChat = Object.freeze({ createChat, dependencies: DEPENDENCIES });
})();
