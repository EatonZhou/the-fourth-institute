// Knowledge board: the Resident chats of the detail dialog and the archive workspace.

function resetDetailChat() {
  detailChatPanel?.closeSurface?.();
  detailChatWorkspace?.saveDraft?.({ immediate: true });
  state.detailChat = {
    fileId: "",
    loading: false,
    attachments: [],
  };
  renderDetailChat();
}

function detailChatPayload() {
  return detailChatPanel?.currentDetail?.() || null;
}

function detailChatResidentState() {
  return detailChatPanel?.snapshot?.()?.state || {};
}

function detailChatIsActive() {
  return ["queued", "running", "stopping"].includes(
    String(detailChatResidentState().work_state || "")
  );
}

function detailChatStatusText() {
  if (state.detailChat.loading) return window.PortalI18n.t("加载中");
  const residentState = detailChatResidentState();
  if (detailChatIsActive()) {
    const label = residentState.work_state === "queued" ? window.PortalI18n.t("等待 Codex 启动") : residentState.work_state === "stopping" ? window.PortalI18n.t("Codex 正在停止") : window.PortalI18n.t("Codex 正在处理");
    return `${label}${Number(residentState.queued_count || 0) ? window.PortalI18n.text`，队列 ${residentState.queued_count}` : ""}`;
  }
  if ((detailChatPanel?.snapshot?.()?.messages || []).length) return window.PortalI18n.t("等待继续");
  return window.PortalI18n.t("未开始");
}

function detailChatQueueText() {
  if (detailHasLoadingAttachments()) return "正在读取文件，完成后可发送";
  const residentState = detailChatResidentState();
  if (detailChatIsActive()) {
    const count = Number(residentState.queued_count || 0);
    const workState = String(residentState.work_state || "");
    const current = workState === "running"
      ? "当前轮运行中"
      : workState === "stopping"
        ? "当前轮正在停止"
        : "当前轮等待启动";
    if (count) return `${current}，队列中还有 ${count} 条`;
    return `${current}，继续发送会进入队列`;
  }
  if ((detailChatPanel?.snapshot?.()?.messages || []).length) return "Ctrl/⌘+Enter 发送";
  return "发送第一条消息后创建 Codex session";
}


function detailHasLoadingAttachments() {
  return (detailChatWorkspace?.attachments?.() || state.detailChat.attachments || []).some(item => !!item.loading || !!item.uploading);
}

function renderDetailChat() {
  if (!detailChatWorkspace) return;
  const statusLine = `${detailChatStatusText()} · ${detailChatQueueText()}`;
  detailChatPanel?.updateControls?.();

}

async function loadDetailChat(fileId, options = {}) {
  const cleanId = String(fileId || "").trim();
  if (!cleanId) return;
  state.detailChat.fileId = cleanId;
  if (!options.silent) {
    state.detailChat.loading = true;
    renderDetailChat();
  }
  try {
    await detailChatPanel.openSurface({ kb_id: cleanId }, {
      force: options.force === true,
    });
    if (state.detailFileId !== cleanId) return;
  } finally {
    state.detailChat.loading = false;
    renderDetailChat();
  }
}

function initChatWorkspaces() {
  detailChatPanel = window.PortalWorkbench.createPanel("chat", {
    root: el.detailChatWorkspace,
    id: "knowledgeDetailChat",

    draftOwner: () => portalUsername || "anonymous",
    title: window.PortalI18n.t("Chat Workspace"),

    placeholder: window.PortalI18n.t("讨论这个知识库条目，Ctrl/⌘ + Enter 发送"),
    refsScope: "detail",
    resolveResident: target => knowledgeApi.request(
      `/knowledge/api/item-chat?kb_id=${encodeURIComponent(String(target?.kb_id || ""))}`,
      { cache: "no-store" },
    ),
    resolveBeforeSend: (target, payload) => knowledgeApi.request("/knowledge/api/item-chat/prepare", {
      method: "POST", body: JSON.stringify({ kb_id: target.kb_id, message: payload.text || payload.plainText }),
    }),
    initialContext: (_target, detail) => detail.initial_context,

    onDetail: renderDetailChat,

    onError: alertError,
  });
  detailChatWorkspace = detailChatPanel.workspace;

  archiveChatPanel = window.PortalWorkbench.createPanel("chat", {
    root: el.archiveChatWorkspace,
    id: "knowledgeArchiveChat",

    draftOwner: () => portalUsername || "anonymous",
    title: window.PortalI18n.t("Chat Workspace"),

    placeholder: window.PortalI18n.t("向本次归档 session 追加要求，Ctrl/⌘ + Enter 发送"),
    onDetail: renderArchiveWorkspace,
    onSent: () => scheduleArchiveRefresh(),
    onError: alertError,
  });
  archiveChatWorkspace = archiveChatPanel.workspace;
  archiveChatRefresh = window.PortalRefreshCoordinator.create({
    key: () => state.archive.currentId || "",
    interval: 30000,
    hiddenInterval: 60000,
    shouldRefresh: () => state.archive.open && !!state.archive.currentId,
    fetch: () => knowledgeApi.request(`/knowledge/api/archive/job?id=${encodeURIComponent(state.archive.currentId)}`, { cache: "no-store" }),
    subscribe: window.PortalRefreshCoordinator.portalSubscription({
      domains: ["projection"],
      matches: detail => {
        if (detail?.snapshot_required) return true;
        const ids = detail?.projections?.["knowledge-archive"];
        return Array.isArray(ids) && (
          ids.includes(state.archive.currentId) || ids.includes("snapshot")
        );
      },
      debounceMs: 120,
    }),
    apply: payload => {
      state.archive.currentId = payload.job?.archive_id || state.archive.currentId;
      setArchiveCurrent(payload);
      renderArchiveJobs();
      renderArchiveWorkspace();
    },
    onError: alertError,
  });
}
