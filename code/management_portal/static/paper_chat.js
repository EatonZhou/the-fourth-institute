(() => {
  "use strict";

  if (window.PortalPaperChat) return;

  // The Paper Chat: its panel in the Workbench, the conversation of the
  // current Paper or collection, the context shown and sent with messages,
  // selections added to the draft, sending and stopping.
  const DEPENDENCIES = Object.freeze([
    "$", "alertError", "applyPaneCollapse", "collectionChatScope", "collectionItems", "collectionPaperIds",
    "collectionVisibleCount", "currentPaperWorkbench", "esc", "isCollectionMode", "md", "paperApi", "state",
  ]);

  function createChat(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperChat is missing: ${missing.join(", ")}`);
    const {
      $, alertError, applyPaneCollapse, collectionChatScope, collectionItems, collectionPaperIds,
      collectionVisibleCount, currentPaperWorkbench, esc, isCollectionMode, md, paperApi, state,
    } = dependencies;

    function chatMarkdown(text) {
      return window.PortalChatWorkspace.renderMarkdownWithAttachmentRefs(text, {
        renderMarkdown: md,
        previewUrlForPath: window.PortalFileLinks.imagePreviewUrl,
      });
    }

    let paperChatWorkspace = null;
    let paperChatPanel = null;

    function paperChatDescriptor() {
      return {
        id: "chat",
        type: "chat",
        presentation: "chat",
        title: window.PortalI18n.t("Research Chat"),
        onShow: ({ panel }) => bindPaperChatPanel(panel?.runtime),
        options: {
          id: "paperResearchChat",
          autoLoad: false,

          draftOwner: () => state.username || "anonymous",
          title: window.PortalI18n.t("Research Chat"),

          placeholder: window.PortalI18n.t("Ask about architecture, related work, review concerns..."),
          meta: "Ctrl/⌘ + Enter 发送",
          resolveResident: target => paperApi.request(`/chat-workspace/api/session?surface=paper&workspace_key=${encodeURIComponent(target.workspace_key || "")}&paper_id=${encodeURIComponent(target.paper_id || "")}`),
          resolveBeforeSend: (target, payload) => paperApi.request("/chat-workspace/api/prepare", {
            method: "POST", body: JSON.stringify({ surface: "paper", workspace_key: target.workspace_key,
              message: payload.text || payload.plainText, context: { ...activeChatContext(), paper_id: target.paper_id } }),
          }),
          initialContext: (_target, detail) => ({ context_text: `${detail.initial_context?.context_text || ""}\n${JSON.stringify(activeChatContext())}` }),

          onDetail: detail => {
            state.chatMetadata = detail;
            state.chatSession = detail?.session || null;
            state.chatSessions = state.chatSession ? [state.chatSession] : [];
            state.chat = Array.isArray(detail?.messages) ? detail.messages : [];
            state.chatHistoryLoadedFor = String(paperChatPanel?.surface?.()?.workspace_key || "");
            renderAssistantContext();
            updateChatComposerState();
          },
          onError: alertError,
        },
      };
    }

    function bindPaperChatPanel(panel) {
      if (!panel || paperChatPanel === panel) return;
      paperChatPanel = panel;
      paperChatWorkspace = panel.workspace;
    }

    async function loadMe() {
      const currentUser = await window.PortalUI.loadCurrentUser({ fallbackUsername: "" });
      if (!currentUser.ok) return;
      state.username = currentUser.data?.username || "";
      paperChatWorkspace?.restoreDraft?.();
    }

    function activeChatPaperId() {
      return isCollectionMode() ? "" : String(state.selectedId || "");
    }

    function activeChatWorkspaceId() {
      if (state.requestedChatWorkspaceId) return state.requestedChatWorkspaceId;
      return isCollectionMode() ? collectionChatScope().key : chatSessionKey(state.selectedId || "");
    }

    function activeChatContext() {
      if (state.requestedChatWorkspaceId) {
        return {
          paper_id: "",
          collection_mode: "history",
          collection_label: "Saved collection chat",
          collection_summary: "Previously saved Paper collection conversation",
          collection_query: "",
          collection_filters: {},
          semantic_search_id: "",
          collector_job_id: "",
          paper_ids: [],
          collection_items: [],
        };
      }
      if (!isCollectionMode()) return { paper_id: activeChatPaperId() };
      const scope = collectionChatScope();
      return {
        paper_id: "",
        collection_mode: scope.payload.mode,
        collection_label: scope.label,
        collection_summary: scope.summaryText,
        collection_query: scope.payload.query,
        collection_filters: scope.payload.filters,
        semantic_search_id: scope.payload.semantic_search_id,
        collector_job_id: scope.payload.collector_job_id,
        paper_ids: collectionPaperIds(),
        collection_items: collectionItems(),
      };
    }

    function chatSessionKey(paperId = state.selectedId) {
      const itemId = String(paperId || "").trim();
      return itemId ? `paper:${itemId}` : "library";
    }

    async function loadChatHistory({ paperId = activeChatPaperId(), workspaceId = activeChatWorkspaceId(), render = true } = {}) {
      if (paperId && paperId === state.deletingPaperId) return null;
      const key = workspaceId || chatSessionKey(paperId);
      state.chatHistoryLoading = true;
      try {
        return await paperChatPanel?.openSurface?.({
          surface: "paper",
          workspace_key: key,
          paper_id: String(paperId || ""),
        }, { force: true, render });
      } finally {
        if (key === activeChatWorkspaceId()) state.chatHistoryLoading = false;
      }
    }

    function renderAssistantContext() {
      if (!$("assistantContext")) return;
      const session = state.chatSession || {};
      const count = Number(session.count || state.chat.length || 0);
      const sessionLine = count
        ? `${count} saved turn${count === 1 ? "" : "s"} in this session.`
        : "No saved turns in this session yet.";
      if (isCollectionMode()) {
        const scope = collectionChatScope();
        const visible = collectionVisibleCount();
        $("assistantContext").innerHTML = window.PortalI18n.html`${esc(scope.label)} · ${esc(visible)} visible paper${Number(visible) === 1 ? "" : "s"}.<br>${esc(sessionLine)}`;
        return;
      }
      const paper = state.detail?.paper;
      $("assistantContext").innerHTML = paper
        ? window.PortalI18n.html`Selected paper: <strong>${esc(paper.title)}</strong><br>${esc(paper.venue || "")} ${esc(paper.year || "")}<br>${esc(sessionLine)}`
        : window.PortalI18n.html`Library chat across all papers.<br>${esc(sessionLine)}`;
    }

    function renderChat() {
      paperChatPanel?.updateControls?.();
    }

    function hasPendingChat() {
      return window.PortalResidentProtocol.stateView(paperChatPanel?.snapshot?.()?.state).working;
    }

    function paperChatHasUploadingAttachments() {
      return (paperChatPanel?.attachments?.() || []).some(item => item.uploading);
    }

    function updateChatComposerState() {
      paperChatPanel?.updateControls?.();
    }

    function addSelectionToChatDraft(text) {
      const selected = String(text || "").trim();
      if (!selected) return false;
      if (!currentPaperWorkbench()?.isPanelVisible("chat")) currentPaperWorkbench()?.showPanel("chat");
      applyPaneCollapse();
      const workspace = paperChatWorkspace || paperChatPanel?.workspace;
      if (!workspace?.setDraftText) return false;
      const currentDraft = String(workspace.draftText?.() || "").trimEnd();
      workspace.setDraftText(currentDraft ? `${currentDraft}\n\n${selected}` : selected);
      paperChatPanel?.focusComposer?.();
      return true;
    }

    async function submitChat(payload = null) {
      const data = payload || paperChatWorkspace?.payload?.() || {};
      const message = String(data.text || "").trim();
      if (!message || state.chatSendInFlight) return;
      await submitChatMessage(message, { fromComposer: true });
    }

    async function submitChatMessage(message, options = {}) {
      const text = String(message || "").trim();
      if (!text || state.chatSendInFlight) return;
      state.chatSendInFlight = true;
      updateChatComposerState();
      try {
        await paperChatPanel?.send?.({
          text,
          plainText: text,
          attachments: [],
          refs: window.PortalChatWorkspace.normalizeRefs({}),
        }, { clear: options.fromComposer === true });
      } catch (err) {
        alertError(err);
      } finally {
        state.chatSendInFlight = false;
        updateChatComposerState();
      }
    }

    async function stopPaperChat() {
      if (!hasPendingChat() || state.chatSendInFlight) return;
      state.chatSendInFlight = true;
      updateChatComposerState();
      try {
        await paperChatPanel?.stop?.();
      } finally {
        state.chatSendInFlight = false;
        updateChatComposerState();
      }
    }

    return Object.freeze({
      activeChatPaperId, activeChatWorkspaceId, addSelectionToChatDraft, loadChatHistory, loadMe,
      paperChatDescriptor, renderAssistantContext, renderChat, submitChatMessage, updateChatComposerState,
      currentPaperChatPanel: () => paperChatPanel, currentPaperChatWorkspace: () => paperChatWorkspace,
    });
  }

  window.PortalPaperChat = Object.freeze({ createChat, dependencies: DEPENDENCIES });
})();
