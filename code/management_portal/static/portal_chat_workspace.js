(function () {
  const escapeHtml = value => window.PortalUIPrimitives.escapeHtml(value);

  // Parts of the chat workspace live in module scripts that every page loads
  // before this one. chatWorkspaceModule(name) returns what a script registers:
  //   chat_workspace_message_content.js  handoff cards, forward references, runtime
  //     status, response sources, attachment references, Markdown and previews
  //   chat_workspace_composer.js  the message input, its tokens, caret, paste and
  //     drop, attachment tray, slash and @mention menu markup, sent content parts
  //   chat_workspace_composer_menus.js  one Workspace's slash command and @mention
  //     menus: opening them from what is typed, keys, choosing, the mention search
  //   chat_workspace_refs.js  the Skills, Agents and resources picked for a
  //     message, their popover, count, summary and payload
  //   chat_workspace_inputs.js  queued inputs, the Arrow-key input history and
  //     the drafts saved per conversation
  //   chat_workspace_messages.js  one message: role, identity, author and avatar,
  //     its refs and attachments, action menu, quote and copy text, folding, keys
  //   chat_workspace_message_list.js  grouping, the list HTML, inheritance cards,
  //     the live runtime stream, quoting a selection, reconciling the list
  //   chat_workspace_message_viewport.js  one Workspace's message viewport: reader
  //     scrolls, following the tail, the reading place across Workbench layouts
  //   chat_workspace_message_actions.js  one Workspace's message actions: the
  //     right-click menu, copy, links, host actions, handoff asks, folding runs
  //   chat_workspace_message_folds.js  one Workspace's long-body folds: measuring
  //     bodies, which messages start expanded, the reader's toggle
  //   chat_workspace_composer_attachments.js  one Workspace's composer attachments:
  //     adding, uploading, retrying, removing and replacing edited images, previews
  //   chat_workspace_composer_drafts.js  one Workspace's drafts: when the message
  //     being written is saved per conversation, and a failed send kept as a draft
  //   chat_workspace_composer_undo.js  one Workspace's composer undo: Ctrl/⌘+Z
  //     takes back its own pastes and inserted attachments
  //   chat_workspace_runtime_settings.js  the Runtime Settings dialog, notices after
  //     saving, the session restart dialog and its progress
  //   chat_workspace_harness_panel.js  the Agent Harness panel and menu, prompt
  //     injection presets and preview
  //   chat_workspace_subagents.js  the subagents a Resident's model starts: run
  //     cards, live cards and their clocks, badge title, the detail dialog
  //   chat_workspace_conversation.js  protocol messages as Chat messages, infos
  //     folded into their request, Family request progress, the snapshot contract
  //   chat_workspace_question_cards.js  question groups and their saved answers,
  //     native approval and MCP cards, a turn's plan and changes card
  //   chat_workspace_resident_messages.js  a Resident message's body by kind, its
  //     footers (delivery, infos, Mailbox, Family, recipients, tokens), progress
  //   chat_workspace_read_observer.js  the read receipt sent while the viewer sees
  //     the conversation
  //   chat_workspace_family_status.js  the Family status view: members' work,
  //     accounts and quota, session moves, member runtime controls
  //   chat_workspace_forward_dialog.js  where chosen messages are forwarded: a
  //     Resident in a Lineage or project, by resident_id, or a Chatroom
  //   chat_workspace_search.js  searching a conversation, its query and results
  //   chat_workspace_resident_workspace.js  one Resident's Chat as a Workspace:
  //     header chrome and ids, session actions, selecting messages to forward
  // Most parts are created once below. A part marked "one Workspace's" is
  // created inside createWorkspace, so each Workspace has its own.
  function chatWorkspaceModule(name) {
    const module = window[name];
    if (!module) throw new Error(`${name} is not loaded.`);
    return module;
  }

  const resourceChips = window.PortalResourceChips;
  if (!resourceChips) throw new Error("PortalResourceChips must load before PortalChatWorkspace");
  const DEFAULT_RESIDENT_HISTORY_PAGE_SIZE = 50;
  const RESIDENT_INITIAL_LOAD_TIMEOUT_MS = 90_000;
  const RESIDENT_CHAT_WORKSPACE_SCHEMA = "portal.chat_workspace.v3";

  const {
    residentHandoffCards, residentHandoffParts, renderResidentHandoffs, parseForwardReference,
    requireImageEditor, runtimeStatusLabel, RUNTIME_PHASE_STATUS, PRE_PROVIDER_RUNTIME_PHASES,
    runtimeActivityPhase, runtimePhaseStatusHtml, runtimeDetailsHtml, responseSourcesHtml,
    requestWorkspacePreview, bindResponseSourcePaths, messageAttachmentRefHtml, resourceSupportsTextPreview,
    loadResourcePreview, bindResourceChips, linkifyResourceAddresses, renderMarkdownWithAttachmentRefs,
    bindAttachmentRefs, openImagePreview, closeImagePreview, bindLinkPreview, bindImagePreview,
    attachmentPreviewUrl, attachmentKind, mediaPartPath,
  } = chatWorkspaceModule("PortalChatWorkspaceMessageContent").createMessageContent({
    escapeHtml, resourceChips,
  });

  const {
    subagentTypeLabel, subagentRunTitle, subagentRunCard, subagentLiveHtml, patchSubagentLive,
    subagentLiveById, rememberSubagentPlaces, revealFinishedSubagents, badgeTitleForSubagent,
    subagentFullResult, openSubagentDetail, subagentSlug, subagentResultMarkdown, SUBAGENT_STOP_REASONS,
  } = chatWorkspaceModule("PortalChatWorkspaceSubagents").createSubagents({
    bindLinkPreview, bindResourceChips, bindResponseSourcePaths, escapeHtml, linkifyResourceAddresses,
    requestWorkspacePreview, runtimeStatusLabel,
  });

  const {
    COMPOSER_DRAFT_ATTACHMENT_RE, normalizeComposerAttachment, normalizeComposerAttachments,
    createConversationSelectionReference, inputMediaPayload, mediaOnlyCaption, composerPayloadContentParts,
    composerAttachmentPlaceholder, composerAttachmentTokenOrder,
    insertComposerAttachmentRef, removeComposerAttachmentRef, moveComposerAttachmentRef,
    refreshComposerAttachmentRefs, syncComposerAttachmentRefs, serializeComposerInput,
    serializeComposerContentParts, renderComposerAttachmentTray, normalizeSlashCommands, slashCommandMatches,
    slashCommandMenuHtml, composerMentionToken, mentionMenuHtml, bindComposerAttachmentActions, inputText,
    setInputText, setComposerDraftInput, composerSelectionSnapshot, restoreComposerSelection,
    inputCaretLineState, isComposerImeActive, insertPlainText, handleAttachmentPaste, handleAttachmentDrop,
    handleAttachmentDragOver, bindWorkspaceComposer,
  } = chatWorkspaceModule("PortalChatWorkspaceComposer").createComposer({
    attachmentKind, attachmentPreviewUrl, bindImagePreview, bindResourceChips, escapeHtml,
    messageAvatarPlaceholderHtml,
  });

  const {
    turnArtifactDisplay, residentProtocolMessageToWorkspace, FAMILY_REQUEST_ACTIONS, noticeQueueWithdrawn,
    noticeQueueCleared, projectResidentConversation, loadResidentConversation, nativeInteractionNames,
  } = chatWorkspaceModule("PortalChatWorkspaceConversation").createConversation({
    DEFAULT_RESIDENT_HISTORY_PAGE_SIZE, mediaOnlyCaption, mediaPartPath, normalizeResidentTarget,
    subagentRunTitle,
  });

  const {
    ResidentQuestionCards,
  } = chatWorkspaceModule("PortalChatWorkspaceQuestionCards").createQuestionCards({
    escapeHtml, nativeInteractionNames, requestWorkspacePreview, residentSessionScope,
  });

  const {
    normalizeRefs, refCount, refsPayload, selectedRefLabels, refsControlHtml, bindRefsControl,
    renderRefsSummary,
  } = chatWorkspaceModule("PortalChatWorkspaceRefs").createRefs({
    escapeHtml,
  });

  const {
    workspaceQueueActionId, normalizeWorkspaceQueue, workspaceQueueHtml, bindWorkspaceQueue,
    createInputHistory, persistedDraftAttachments, normalizeWorkspaceDraft, workspaceDraftHasContent,
    createDraftStore,
  } = chatWorkspaceModule("PortalChatWorkspaceInputs").createInputs({
    attachmentKind, COMPOSER_DRAFT_ATTACHMENT_RE, escapeHtml, inputCaretLineState, isComposerImeActive,
    normalizeComposerAttachments, refsPayload, renderMarkdownWithAttachmentRefs,
    residentProtocolMessageToWorkspace,
  });

  const {
    messageRole, newMessageIdentity, messageIdentity, messageAuthoredByViewer, messageSystemPresentation,
    messageTitle, messageAuthorHeaderHtml, bindMessageAvatars, messageContextRefsHtml, messageAttachmentsHtml,
    messageSubmenuHtml, closeMessageSubmenus, bindMessageSubmenus, commonMessageActionsHtml,
    messageQuoteExcerpt, messageQuotable, messageQuoteHtml, composerQuoteHtml, messageCopyText,
    MESSAGE_FOLD_MIN_LINES, messageFoldChoices, messageFoldDefaults, messageFoldBodyHtml,
    applyMessageFold, normalizeMessageStream, messageRenderKey, messageInstanceKey, messageNodeKey,
    messageInstanceSelector, messageAfterKey, messageSenderIdentity,
  } = chatWorkspaceModule("PortalChatWorkspaceMessages").createMessages({
    escapeHtml, messageAttachmentRefHtml, messageAvatarPlaceholderHtml, outwardToResidents, resourceChips,
    runtimeActivityPhase, selectedRefLabels,
  });

  const {
    consecutiveAgentGroups, familyWorkGroups, messageListHtml, inheritanceCardHtml, runtimeStreamHtml,
    createMessageSelectionController, preserveRuntimeDisclosureState, reconcileMessageList,
    messageViewportAtTail,
  } = chatWorkspaceModule("PortalChatWorkspaceMessageList").createMessageList({
    commonMessageActionsHtml, escapeHtml, footerTime, messageAfterKey, messageAttachmentsHtml,
    messageAuthoredByViewer, messageAuthorHeaderHtml, messageContextRefsHtml, messageFoldBodyHtml,
    messageFoldChoices, messageFoldDefaults, messageInstanceKey, messageNodeKey, messageQuoteHtml,
    messageRenderKey, messageRole, messageSenderIdentity, messageSystemPresentation, messageTitle,
    normalizeMessageStream, PRE_PROVIDER_RUNTIME_PHASES, renderMarkdownWithAttachmentRefs,
    renderResidentHandoffs, residentHandoffParts, responseSourcesHtml, runtimeDetailsHtml,
    runtimePhaseStatusHtml, subagentLiveHtml,
  });

  const {
    residentRuntimeSettingRows, residentRuntimeStatusText, residentRuntimeMetaHtml, notifyRuntimeSettingsSaved,
    showResidentBackendNotices, createRuntimeSettingsDialog, sessionRestartActive,
    createSessionRestartProgress, createSessionRestartDialog,
  } = chatWorkspaceModule("PortalChatWorkspaceRuntimeSettings").createRuntimeSettings({
    escapeHtml,
  });

  const {
    residentAgentHarnessSettings, mountResidentHarnessPanel, residentHarnessMenuHtml,
  } = chatWorkspaceModule("PortalChatWorkspaceHarnessPanel").createHarnessPanel({
    escapeHtml,
  });

  const {
    residentMessageBody, familyExecution, familyReassignMenuItems, residentMessageFooter, residentMessageAfter,
    requestProgressHtml, formatTokenCount,
  } = chatWorkspaceModule("PortalChatWorkspaceResidentMessages").createResidentMessages({
    escapeHtml, footerTime, messageQuotable, messageRole, messageSubmenuHtml, nativeInteractionNames,
    outwardToResidents, parseForwardReference, PRE_PROVIDER_RUNTIME_PHASES, resourceChips,
    RUNTIME_PHASE_STATUS, runtimeDetailsHtml, subagentRunCard, turnArtifactDisplay,
  });

  const {
    ResidentReadObserver,
  } = chatWorkspaceModule("PortalChatWorkspaceReadObserver").createReadObserver();

  const {
    createFamilyStatusView, mountFamilyStatus, openFamilyStatus,
  } = chatWorkspaceModule("PortalChatWorkspaceFamilyStatus").createFamilyStatus({
    escapeHtml, footerTime, sessionRestartActive,
  });

  const {
    openResidentForwardDialog,
  } = chatWorkspaceModule("PortalChatWorkspaceForwardDialog").createForwardDialog({
    escapeHtml,
  });

  const {
    createConversationSearch,
  } = chatWorkspaceModule("PortalChatWorkspaceSearch").createSearch({
    escapeHtml, messageRenderKey, messageRole, messageTitle,
  });

  const {
    residentSessionActions, ResidentChatWorkspace,
  } = chatWorkspaceModule("PortalChatWorkspaceResidentWorkspace").createResidentWorkspace({
    createWorkspace, escapeHtml, messageIdentity, messageRole, messageTitle, normalizeRefs,
    normalizeResidentTarget, residentProtocolMessageToWorkspace, residentSessionScope, workspaceInstanceId,
  });

  function iconSvg(name) {
    if (name === "refresh") {
      return window.PortalUIPrimitives?.iconHtml?.("refresh") || "";
    }
    if (name === "collapse") {
      return window.PortalUIPrimitives?.iconHtml?.("chevron-right") || "";
    }
    return "";
  }

  function messageHistoryMediaAttachments(item, options = {}) {
    const messageId = messageIdentity(item);
    const contentParts = Array.isArray(item?.content_parts) ? item.content_parts : [];
    const media = Array.isArray(item?.input_media) && item.input_media.length
      ? item.input_media
      : contentParts.filter(part => part?.type === "image" || part?.type === "file");
    return media.map((part, index) => {
      const source = mediaPartPath(part);
      if (!source) return null;
      const type = String(part?.type || part?.media_type || "image").toLowerCase();
      const kind = type === "file" ? "file" : "image";
      const name = String(part?.name || part?.filename || source.split("/").filter(Boolean).pop() || kind);
      return normalizeComposerAttachment({
        id: String(part?.file_id || part?.id || `${messageId}:media:${index}`),
        file_id: String(part?.file_id || ""),
        upload_id: String(part?.upload_id || ""),
        kind,
        name,
        path: source,
        preview_url: String(part?.preview_url || (kind === "image" ? options.previewUrlForPath?.(source) || "" : "")),
        content_type: String(part?.mime_type || part?.content_type || ""),
        size_bytes: Number(part?.size_bytes || part?.size || 0),
      });
    }).filter(Boolean);
  }

  function messageHistoryDraft(item, options = {}) {
    const attachments = messageHistoryMediaAttachments(item, options);
    let text = String(item?.content || "");
    for (const attachment of attachments) {
      const kind = attachmentKind(attachment);
      const placeholder = composerAttachmentPlaceholder(attachment);
      const references = [
        `[${kind}: ${attachment.path}]`,
        `[${kind}:${attachment.path}]`,
        `[attached ${kind}: ${attachment.name}]`,
      ];
      let replaced = false;
      for (const reference of references) {
        if (!reference || !text.includes(reference)) continue;
        text = text.split(reference).join(placeholder);
        replaced = true;
      }
      if (!replaced) text = `${text}${text.trim() ? "\n" : ""}${placeholder}`;
    }
    return { text: text.trim(), attachments };
  }

  // One input as the history recalls it: its text with the attachments in place.
  function messageInputHistoryItem(item, options = {}) {
    const draft = messageHistoryDraft(item, options);
    return { id: messageIdentity(item), text: draft.text, meta: { attachments: draft.attachments } };
  }

  function messageAvatarPlaceholderHtml(id, label) {
    return window.PortalI18n.html`<span class="portalUserAvatar messageAuthorAvatar" data-message-avatar-principal="${escapeHtml(id)}" data-message-avatar-label="${escapeHtml(label)}" aria-hidden="true">${escapeHtml(Array.from(label || id)[0]?.toUpperCase() || "?")}</span>`;
  }

  function workspaceShellHtml(options = {}) {
    const title = options.title || window.PortalI18n.t("Chat Workspace");
    const subtitle = options.subtitle || "";
    const refreshId = options.refreshId || "";
    const messageListId = options.messageListId || "";
    const composerId = options.composerId || "";
    const composerSplitterId = options.composerSplitterId || "";
    const titleId = options.titleId || "";
    const statusId = options.statusId || "";
    const bodyId = options.bodyId || "";
    const loadingId = options.loadingId || "";
    return window.PortalI18n.html`<div class="paneHead chatHead portalWorkspaceHead">
      <div class="paneHeadText chatHeadText">
        <div class="paneTitle" ${titleId ? `id="${escapeHtml(titleId)}"` : ""}>${escapeHtml(title)}</div>
        <div class="chatSubline"><div class="paneNote" ${statusId ? `id="${escapeHtml(statusId)}"` : ""} data-workspace-status>${escapeHtml(subtitle)}</div></div>
      </div>
      <div class="paneHeadActions actionGroup">
        <button class="portalButton is-icon is-quiet iconBtn" type="button" data-workspace-history-search aria-label="查找当前对话" title="浏览当前对话片段，输入关键词查找并跳转" aria-expanded="false">${window.PortalUIPrimitives.iconHtml("search")}</button>
        ${refreshId ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet iconBtn" id="${escapeHtml(refreshId)}" type="button" aria-label="刷新 Chat Workspace" title="刷新">${iconSvg("refresh")}</button>` : ""}
        ${options.extraActionsHtml || ""}
      </div>
    </div>
    <div class="chatWorkspaceBody portalWorkspaceBody" ${bodyId ? `id="${escapeHtml(bodyId)}"` : ""}>
      ${options.bodyPrefixHtml || ""}
      <div class="paneBody" id="${escapeHtml(messageListId)}"></div>
      ${options.bodySuffixHtml || ""}
    </div>
    ${options.afterBodyHtml || ""}
    ${composerSplitterId ? window.PortalI18n.html`<div class="composerSplitter" id="${escapeHtml(composerSplitterId)}" role="separator" aria-orientation="horizontal" aria-label="调整发送框高度" title="拖动调整发送框高度"></div>` : ""}
    <div class="composer" id="${escapeHtml(composerId)}"></div>
    <div class="portalWorkspaceLoading" ${loadingId ? `id="${escapeHtml(loadingId)}"` : ""} data-workspace-loading hidden></div>`;
  }

  function normalizeWorkspaceSubtitleParts(parts = []) {
    return (Array.isArray(parts) ? parts : [])
      .map(part => {
        if (typeof part === "string") return { text: String(part).trim() };
        if (!part || typeof part !== "object") return null;
        const rawText = String(part.text || "").trim();
        const accountPrefix = part.identity === "account" && rawText.startsWith("账号 ");
        const text = accountPrefix ? window.PortalI18n.text`账号 ${rawText.slice(3)}`
          : part.identity === "family" ? window.PortalI18n.message(rawText) : rawText;
        if (!text) return null;
        return {
          text,
          copy_value: String(part.copy_value || part.copyValue || "").trim(),
          copy_label: window.PortalI18n.t(String(part.copy_label || part.copyLabel || "").trim()),
          identity: String(part.identity || "").trim(),
        };
      })
      .filter(Boolean);
  }

  // The composer's accessible name states how to send: plain Enter keeps a new line.
  const COMPOSER_INPUT_LABEL = window.PortalI18n.t("消息输入（Ctrl/⌘ + Enter 发送）");

  function workspaceComposerHtml(options = {}) {
    const scope = String(options.refsScope || "default");
    const inputId = options.inputId || "";
    const fileInputId = options.fileInputId || "";
    const trayId = options.trayId || "";
    const attachId = options.attachId || "";
    const sendId = options.sendId || "";
    const stopId = options.stopId || "";
    const autoId = options.autoId || "";
    const sendLabel = String(options.send_label || "").trim();
    const placeholder = window.PortalI18n.t(options.placeholder || "Ctrl/⌘ + Enter 发送");
    // The input's placeholder already states how to send; a note repeating it is left out.
    const meta = window.PortalI18n.t(String(options.meta || "").trim());
    const metaHtml = options.metaHtml || (meta && meta !== String(placeholder).trim()
      ? window.PortalI18n.html`<span class="paneNote composerMeta">${escapeHtml(meta)}</span>` : "");
    const sendTitle = sendLabel || window.PortalI18n.t("发送消息");
    const sendClass = sendLabel ? "portalButton is-compact is-primary" : "portalButton is-compact is-primary sendIconBtn";
    const sendContent = sendLabel
      ? escapeHtml(sendLabel)
      : window.PortalI18n.html`${window.PortalUIPrimitives.iconHtml("arrow-up")}<span class="srOnly">发送</span>`;
    return window.PortalI18n.html`<div class="workspaceComposerExtension" data-workspace-composer-extension="before">${options.beforeComposerHtml || ""}</div><div class="workspaceQueueHost" data-workspace-queue-host>${options.queueHtml || ""}</div><div class="composerImageTray" id="${escapeHtml(trayId)}"></div><div class="composerQuoteHost" data-composer-quote-host hidden></div>
      <div class="composerInputWrap">
        <div class="composerInput composerRichInput" id="${escapeHtml(inputId)}" contenteditable="${options.editable === false ? "false" : "true"}" role="textbox" aria-multiline="true" aria-label="${escapeHtml(COMPOSER_INPUT_LABEL)}" data-placeholder="${escapeHtml(placeholder)}"></div>
        <div class="composerSlashCommandHost" data-composer-slash-command-host></div>
        <div class="composerSlashCommandHost composerMentionHost" data-composer-mention-host hidden></div>
      </div>
      <div class="composerActions">
        <div class="workspaceComposerMetaHost" data-workspace-composer-meta>${metaHtml}</div>
        <div class="actionGroup" data-workspace-composer-controls>
          ${stopId ? window.PortalI18n.html`<button class="portalButton is-compact is-danger" id="${escapeHtml(stopId)}" type="button" aria-label="停止当前任务" title="停止当前任务" ${options.stopDisabled === false ? "" : "disabled"}>Stop</button>` : ""}
          ${fileInputId ? window.PortalI18n.html`<input id="${escapeHtml(fileInputId)}" type="file" multiple hidden />` : ""}
          ${attachId ? window.PortalI18n.html`<button class="portalButton is-compact" id="${escapeHtml(attachId)}" type="button" title="添加附件" aria-label="添加附件" data-portal-icon="plus"></button>` : ""}
          ${options.refs === false ? "" : refsControlHtml({ scope, selection: options.refsSelection || {}, contextOptions: options.contextOptions || {}, collapsed: options.refsCollapsed || {} })}
          ${sendId ? window.PortalI18n.html`<button class="${sendClass}" id="${escapeHtml(sendId)}" type="button" aria-label="${escapeHtml(sendTitle)}" aria-keyshortcuts="Control+Enter Meta+Enter" title="${escapeHtml(sendTitle)}" ${options.sendDisabled ? "disabled" : ""}>${sendContent}</button>` : ""}
          ${autoId ? window.PortalI18n.html`<button class="portalButton is-compact" id="${escapeHtml(autoId)}" type="button" aria-label="进入自动继续模式" title="进入自动继续模式" ${options.autoDisabled === false ? "" : "disabled"}>Auto</button>` : ""}
        </div>
      </div>
      ${options.refs === false ? "" : window.PortalI18n.html`<div class="refsSummary" data-refs-summary="${escapeHtml(scope)}"></div>`}
      <div class="workspaceComposerExtension" data-workspace-composer-extension="after">${options.afterComposerHtml || ""}</div>`;
  }

  function workspaceComposerCapabilities(composer = {}) {
    return new Set((Array.isArray(composer.capabilities) ? composer.capabilities : [])
      .map(item => String(item || "").trim().toLowerCase())
      .filter(Boolean));
  }

  const { defaultWorkspaceUpload } = chatWorkspaceModule("PortalChatWorkspaceComposerAttachments");

  let workspaceInstanceSequence = 0;

  function workspaceInstanceId(options = {}, prefix = "workspace") {
    const explicit = String(
      options.id
      || [options.workbenchId, options.panelId].filter(Boolean).join("-")
      || "",
    ).trim();
    if (explicit) return explicit;
    workspaceInstanceSequence += 1;
    return `${prefix}_${workspaceInstanceSequence}`;
  }

  function createWorkspace(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new Error("PortalChatWorkspace.createWorkspace root required");
    root.__portalChatWorkspaceController?.destroy?.();
    const id = workspaceInstanceId(options);
    const refsScope = String(options.refsScope || id);
    const composerResizeEnabled = options.composerResize !== false;
    const composerHeightStorageKey = String(options.composerHeightKey || `portal.workspaceComposerHeight.${id}`);
    const defaultComposerInputHeight = Math.max(56, Number(options.composerInputHeight || 86));
    const readStoredComposerInputHeight = () => {
      const value = Number(window.PortalState.storage.readText(composerHeightStorageKey, ""));
      return Number.isFinite(value) && value > 0 ? value : defaultComposerInputHeight;
    };
    const writeStoredComposerInputHeight = (value) => {
      if (!composerHeightStorageKey) return;
      window.PortalState.storage.writeText(composerHeightStorageKey, Math.round(value));
    };
    root.classList.add("portalChatWorkspaceRoot");
    // The shared Workspace owns its catalog lifecycle, independently of host loading.
    const refsCatalog = {
      status: options.contextOptions ? "ready" : "idle",
      value: {},
      request: null,
      disposed: false,
    };
    const contextOptions = () => typeof options.contextOptions === "function"
      ? (options.contextOptions() || {})
      : (options.contextOptions || refsCatalog.value);
    const loadRefsCatalog = () => {
      if (refsCatalog.disposed || refsCatalog.status === "ready" || refsCatalog.request) return;
      refsCatalog.status = "loading";
      controller.refreshRefs();
      refsCatalog.request = Promise.resolve()
        .then(() => window.PortalAPI.get("/api/agent-context-options", { cache: "no-store" }))
        .then(payload => {
          if (refsCatalog.disposed) return;
          if (!Array.isArray(payload?.skills) || !Array.isArray(payload?.agents)) {
            throw new Error("Invalid context options catalog");
          }
          refsCatalog.value = { skills: payload.skills, agents: payload.agents };
          refsCatalog.status = "ready";
        })
        .catch(() => {
          if (!refsCatalog.disposed) refsCatalog.status = "error";
        })
        .finally(() => {
          refsCatalog.request = null;
          if (!refsCatalog.disposed) controller.refreshRefs();
        });
    };
    const state = {
      attachments: [],
      refs: normalizeRefs(options.refsSelection || {}),
      refsCollapsed: options.refsCollapsed || {},
      messages: [],
      runtimeDetails: [],
      runtimeLive: false,
      runtimePhase: "",
      loading: false,
      loadingMessage: "",
      busy: false,
      contract: null,
      composer: {},
      composerSignature: "",
      composerQueueSignature: "",
      composerInputHeight: readStoredComposerInputHeight(),
      messageItemSignatures: [],
      messageRenderContext: { signature: "", renderers: [] },
      messageRenderOptions: {},
      collapsedAgentGroups: new Set(),
      collapsedWorkGroups: new Set(),
      expandedAgentGroups: new Set(),
      messageFoldDefaults: new Set(),
      messageFoldSizes: new Map(),
      recentFileSignatures: new Map(),
      composerInputFocused: false,
      composerImeActive: false,
      slashCommandItems: [],
      slashCommandActiveIndex: 0,
      mentionItems: [],
      mentionActiveIndex: 0,
      mentionQuery: null,
      mentionDismissed: null,
      activeDraftScope: "",
      recoveredSubmission: null,
      restoringDraft: false,
      submissionSequence: 0,
      updateRevision: "",
      messageByKey: new Map(),
      messageScrollScope: null,
      messageTailFollowIntent: true,
      subtitle: String(options.subtitle || ""),
      subtitleParts: normalizeWorkspaceSubtitleParts(options.subtitleParts),
    };
    const dom = {
      messageListId: `${id}Messages`,
      composerId: `${id}Composer`,
      inputId: `${id}Input`,
      trayId: `${id}AttachmentTray`,
      fileInputId: `${id}FileInput`,
      attachId: `${id}Attach`,
      sendId: `${id}Send`,
      stopId: `${id}Stop`,
      autoId: `${id}Auto`,
      refreshId: `${id}Refresh`,
      composerSplitterId: `${id}ComposerSplitter`,
      titleId: `${id}Title`,
      statusId: `${id}Status`,
      bodyId: `${id}Body`,
      loadingId: `${id}Loading`,
    };
    root.innerHTML = workspaceShellHtml({
      title: options.title || window.PortalI18n.t("Chat Workspace"),
      subtitle: options.subtitle || "",
      refreshId: options.onRefresh && !options.onMenu ? dom.refreshId : "",
      messageListId: dom.messageListId,
      composerId: dom.composerId,
      composerSplitterId: composerResizeEnabled ? dom.composerSplitterId : "",
      titleId: dom.titleId,
      statusId: dom.statusId,
      bodyId: dom.bodyId,
      loadingId: dom.loadingId,
      extraActionsHtml: options.extraActionsHtml || "",
      bodyPrefixHtml: options.bodyPrefixHtml || "",
      bodySuffixHtml: options.bodySuffixHtml || "",
      afterBodyHtml: options.afterBodyHtml || "",
    });
    // A tabpanel belongs to its host tab; preserve that accessible name.
    const hostLabelledBy = (root.getAttribute("aria-labelledby") || "").trim().split(/\s+/)
      .some(labelId => root.ownerDocument.getElementById(labelId));
    const hostNamesTabPanel = root.getAttribute("role") === "tabpanel"
      && (hostLabelledBy || root.getAttribute("aria-label")?.trim());
    if (!hostNamesTabPanel) {
      root.setAttribute("aria-labelledby", dom.titleId);
      root.removeAttribute("aria-label");
    }
    const composer = root.querySelector(`#${CSS.escape(dom.composerId)}`);
    let refsControlBinding = null;
    let conversationSearch = null;
    let runtimeSettingsDialog = null;
    let sessionRestartDialog = null;
    let runtimeCatalogEntry = null;
    let residentRuntime = null, runtimeResidentId = "", runtimeTargetPending = false;
    const releaseResidentRuntime = () => {
      residentRuntime?.destroy(); residentRuntime = null; runtimeResidentId = ""; runtimeCatalogEntry = null;
    };
    const residentRuntimeController = () => {
      if (runtimeTargetPending) return null;
      const residentId = contractResidentId();
      if (runtimeResidentId !== residentId) releaseResidentRuntime();
      if (!residentId) return null;
      if (!residentRuntime) {
        runtimeResidentId = residentId;
        residentRuntime = window.PortalResidentRuntimeController.bind(residentId, {
          onState: view => {
            if (contractResidentId() !== residentId) return;
            if (view.detail) state.composer.runtime_settings = view.settings;
            if (view.catalog && runtimeCatalogEntry) runtimeCatalogEntry.catalog = view.catalog;
            const button = composer.querySelector("[data-workspace-runtime-settings-open]");
            if (button) {
              button.disabled = view.loading || view.busy || sessionRestartActive(view.restart) || (!view.canEdit && !view.canRestart);
              const label = button.querySelector(".composerRuntimeLabel");
              if (label && view.detail) label.textContent = residentRuntimeStatusText(view.settings, view.catalog || {});
            }
          },
          onChanged: result => {
            if (contractResidentId() !== residentId) return;
            options.onRuntimeSettingsSaved?.(result, controller);
            void options.onRefresh?.(controller);
          },
        });
      }
      return residentRuntime;
    };
    // The Resident whose Chat this is; empty in other Chats (a Chatroom, an article).
    const contractResidentId = () => {
      const target = state.contract?.target || {};
      return String(target.resident_id || (target.kind === "resident" ? target.id : "") || "");
    };
    const runtimeCatalogUrl = (cfg = state.composer) => {
      const residentId = contractResidentId()
        || state.contract?.actions?.runtime_settings?.payload?.resident_id || "";
      // A Resident's own Provider wins on the server; the composer names it otherwise.
      const query = new URLSearchParams(cfg.runtime_backend ? { backend: cfg.runtime_backend } : {});
      if (residentId) query.set("resident_id", residentId);
      if (cfg.runtime_account_id) query.set("account_id", cfg.runtime_account_id);
      return `/api/agent-workbench/agent-runtime-catalog?${query}`;
    };
    const loadWorkspaceRuntimeCatalog = (cfg = state.composer, retry = false) => {
      if (runtimeTargetPending) return { catalog: null, promise: Promise.resolve({}) };
      const key = runtimeCatalogUrl(cfg);
      if (runtimeCatalogEntry?.key === key && !(retry && runtimeCatalogEntry.error)) return runtimeCatalogEntry;
      const entry = { key, catalog: cfg.runtime_catalog || null, error: null, promise: null };
      runtimeCatalogEntry = entry;
      entry.promise = (contractResidentId() ? residentRuntimeController().load().then(view => view.catalog || {})
        : entry.catalog ? Promise.resolve(entry.catalog) : window.PortalAPI.get(key))
        .then(catalog => {
          entry.catalog = catalog;
          // A late response from the previous Resident must not change this composer.
          if (runtimeCatalogEntry !== entry || runtimeCatalogUrl() !== key) return catalog;
          const button = composer.querySelector("[data-workspace-runtime-settings-open]");
          const label = button?.querySelector(".composerRuntimeLabel");
          if (label) {
            const text = residentRuntimeStatusText(state.composer.runtime_settings, catalog);
            label.textContent = text;
            button.title = window.PortalI18n.text`切换模型与运行设置：${text}`;
          }
          return catalog;
        }).catch(error => { entry.error = error; throw error; });
      // The footer can still show saved settings while a catalog is unavailable.
      entry.promise.catch(() => {});
      return entry;
    };
    const composerShellOptions = (composerOptions = {}) => {
      const cfg = {
        ...options,
        ...state.composer,
        ...composerOptions,
      };
      const canAttach = cfg.can_attach !== false;
      const refsEnabled = cfg.refs_enabled !== false;
      const editable = cfg.can_edit !== false;
      const dynamicCanStop = !state.contract && typeof options.canStop === "function"
        ? options.canStop() === true
        : false;
      const showStop = cfg.can_stop === true || dynamicCanStop;
      const capabilities = workspaceComposerCapabilities(cfg);
      const runtimeSettingsEnabled = (capabilities.has("runtime_settings")
        && cfg.runtime_settings && typeof cfg.runtime_settings === "object")
        || (contractResidentId() && state.contract?.actions?.session_restart);
      const runtimeMetaHtml = cfg.metaHtml || (
        runtimeSettingsEnabled
          ? residentRuntimeMetaHtml(cfg.runtime_settings || {}, {
              catalog: loadWorkspaceRuntimeCatalog(cfg).catalog || {},
              openAttribute: "data-workspace-runtime-settings-open",
            })
          : ""
      );
      return {
        inputId: dom.inputId,
        trayId: dom.trayId,
        fileInputId: canAttach ? dom.fileInputId : "",
        attachId: canAttach ? dom.attachId : "",
        sendId: dom.sendId,
        stopId: showStop ? dom.stopId : "",
        autoId: cfg.can_auto === false ? "" : (cfg.onAuto || options.onAuto ? dom.autoId : ""),
        refs: refsEnabled,
        editable,
        refsScope,
        placeholder: cfg.placeholder || window.PortalI18n.t("Ctrl/⌘ + Enter 发送"),
        meta: cfg.meta || "",
        metaHtml: runtimeMetaHtml,
        beforeComposerHtml: cfg.beforeComposerHtml || "",
        queueHtml: workspaceQueueHtml(cfg.queue, {
          formatTime: options.formatTime,
          previewUrlForPath: options.previewUrlForPath,
          renderMarkdown: options.renderMarkdown,
          mailbox: typeof options.onQueueMailbox === "function",
          mergeAll: typeof options.onQueueMergeAll === "function",
        }),
        afterComposerHtml: cfg.afterComposerHtml || "",
        send_label: cfg.send_label || "",
        contextOptions: contextOptions(),
        refsSelection: state.refs,
        refsCollapsed: state.refsCollapsed,
        stopDisabled: true,
        autoDisabled: !(cfg.onAuto || options.onAuto),
      };
    };
    const renderComposerShell = (composerOptions = {}) => {
      refsControlBinding?.destroy();
      refsControlBinding = null;
      composer.innerHTML = workspaceComposerHtml(composerShellOptions(composerOptions));
    };
    const els = {
      title: root.querySelector(".portalWorkspaceHead .paneTitle"),
      body: root.querySelector(".portalWorkspaceBody"),
      messages: root.querySelector(`#${CSS.escape(dom.messageListId)}`),
      composer,
      input: root.querySelector(`#${CSS.escape(dom.inputId)}`),
      tray: root.querySelector(`#${CSS.escape(dom.trayId)}`),
      fileInput: root.querySelector(`#${CSS.escape(dom.fileInputId)}`),
      attach: root.querySelector(`#${CSS.escape(dom.attachId)}`),
      send: root.querySelector(`#${CSS.escape(dom.sendId)}`),
      stop: root.querySelector(`#${CSS.escape(dom.stopId)}`),
      auto: root.querySelector(`#${CSS.escape(dom.autoId)}`),
      refresh: root.querySelector(`#${CSS.escape(dom.refreshId)}`),
      composerSplitter: root.querySelector(`#${CSS.escape(dom.composerSplitterId)}`),
      slashCommands: root.querySelector("[data-composer-slash-command-host]"),
      mentions: root.querySelector("[data-composer-mention-host]"),
      status: root.querySelector("[data-workspace-status]"),
      loading: root.querySelector("[data-workspace-loading]"),
    };
    const restartService = window.PortalResidentService;
    const restartProgress = createSessionRestartProgress({
      compact: true,
      owner: options.draftOwner,
      onOpen: () => openWorkspaceSessionRestart().catch(error => window.PortalUI.toast(error.message || String(error), "error")),
      onDismiss: () => els.refresh?.focus(),
    });
    els.status?.closest(".chatHeadText")?.append(restartProgress.element);
    let restartUnobserve = null;
    let restartResidentId = "";
    let lastRestartView = null;
    function syncSessionRestartObservation() {
      const residentId = sessionRestartAvailable() ? String(controller.target?.()?.resident_id || "") : "";
      if (residentId === restartResidentId || !restartService) return;
      restartUnobserve?.();
      restartResidentId = residentId;
      lastRestartView = null;
      restartProgress.update({});
      if (!residentId) return;
      restartUnobserve = restartService.observeSessionRestart(residentId, view => {
        const previous = lastRestartView?.restart;
        lastRestartView = view;
        restartProgress.update(view);
          if (["accepted", "cancelling"].includes(previous?.status) && previous.restart_id === view.restart?.restart_id
          && ["completed", "failed", "cancelled"].includes(view.restart.status)) {
          options.onSessionRestarted?.(view.restart, controller);
          Promise.resolve(options.onRefresh?.(controller)).catch(error => options.onError?.(error));
        }
      });
    }
    const renderSubtitle = (transientStatus = "") => {
      if (els.refresh) {
        const label = sessionRestartAvailable() ? window.PortalI18n.t("Session 操作") : window.PortalI18n.t("刷新 Chat Workspace");
        els.refresh.setAttribute("aria-label", label);
        els.refresh.setAttribute("data-portal-tooltip", label);
        els.refresh.title = label;
      }
      if (!els.status) return;
      if (!transientStatus && !state.loading) {
        if (state.composer.can_send === false) {
          transientStatus = state.composer.disabled_reason || window.PortalI18n.t("当前对话暂不可发送");
        } else {
          transientStatus = String(state.composer.send_hint || "").trim();
        }
      }
      if (state.composer.execution_gate) {
        const gate = window.PortalResidentProtocol.executionGateView(state.composer);
        transientStatus = [gate.text, transientStatus].filter(Boolean).join(" · ");
      }
      if (!state.subtitleParts.length) {
        els.status.classList.remove("hasSessionIdentity");
        els.status.textContent = transientStatus || state.subtitle;
        return;
      }
      els.status.classList.toggle(
        "hasSessionIdentity",
        state.subtitleParts.length > 0,
      );
      const fragment = document.createDocumentFragment();
      const familyButton = els.status.querySelector("[data-family-status-open]");
      state.subtitleParts.forEach((part, index) => {
        if (index) {
          const separator = document.createElement("span");
          separator.className = "chatHeaderSeparator";
          separator.textContent = "·";
          separator.dataset.nextIdentity = part.identity || "text";
          fragment.append(separator);
        }
        const opensFamily = part.identity === "family" && typeof options.onFamilyStatus === "function";
        const span = opensFamily ? familyButton || document.createElement("button") : document.createElement("span");
        if (opensFamily && !familyButton) {
          span.type = "button";
          span.dataset.familyStatusOpen = "1";
          span.title = window.PortalI18n.t("查看 Family 成员状态");
          span.setAttribute("aria-haspopup", "dialog");
          span.setAttribute("aria-expanded", "false");
          span.addEventListener("click", () => options.onFamilyStatus(span));
        }
        span.textContent = part.text;
        if (part.identity) span.dataset.workspaceIdentity = part.identity;
        if (part.copy_value) {
          span.className = "chatHeaderCopyId";
          span.dataset.workspaceCopyValue = part.copy_value;
          span.dataset.workspaceCopyLabel = part.copy_label || window.PortalI18n.t("值");
          span.title = window.PortalI18n.text`${span.dataset.workspaceCopyLabel}：${part.copy_value} · 双击复制`;
          if (part.identity === "resident") span.dataset.workspaceResidentId = part.copy_value;
          if (part.identity === "session") span.dataset.workspaceSessionId = part.copy_value;
          if (part.identity === "account") span.dataset.workspaceAccountId = part.copy_value;
        }
        fragment.append(span);
      });
      if (transientStatus) {
        const separator = document.createElement("span");
        separator.className = "chatHeaderSeparator";
        separator.textContent = "·";
        fragment.append(separator);
        const status = document.createElement("span");
        status.setAttribute("role", "status");
        status.textContent = transientStatus;
        fragment.append(status);
      }
      els.status.replaceChildren(fragment);
    };
    const handleSubtitleCopy = event => {
      const target = event.target instanceof Element
        ? event.target.closest("[data-workspace-copy-value]")
        : null;
      if (!target || !els.status?.contains(target)) return;
      const value = String(target.dataset.workspaceCopyValue || "").trim();
      if (!value) return;
      event.preventDefault();
      event.stopPropagation();
      window.PortalUI?.copyText?.(
        value,
        String(target.dataset.workspaceCopyLabel || window.PortalI18n.t("值")).trim() || window.PortalI18n.t("值"),
      );
    };
    els.status?.addEventListener("dblclick", handleSubtitleCopy);

    const {
      messageViewportMeasurable, syncMessageScrollMode, resetMessageScrollGesture, followTailAfterResize,
      destroyMessageViewport,
    } = chatWorkspaceModule("PortalChatWorkspaceMessageViewport").createMessageViewport({
      els, messageInstanceSelector, messageNodeKey, messageViewportAtTail, root, state,
    });

    const {
      measureMessageFolds, updateMessageFoldDefaults, showMessageFoldDefaults, toggleMessageFold,
      destroyMessageFolds,
    } = chatWorkspaceModule("PortalChatWorkspaceMessageFolds").createMessageFolds({
      MESSAGE_FOLD_MIN_LINES, applyMessageFold, els, messageFoldChoices, messageFoldDefaults,
      messageRenderKey, messageViewportAtTail, messageViewportMeasurable, state, syncMessageScrollMode,
    });
    const markComposerInputActivity = (activity = {}) => {
      state.composerInputFocused = !!activity.focused;
      state.composerImeActive = !!activity.composing;
      updateControls();
    };

    const {
      refreshSlashCommandMenu, closeSlashCommandMenu, chooseSlashCommand, handleSlashCommandKey,
      scheduleMentionRender, closeMentionMenu, destroyMentionMenu, refreshMentionMenu, chooseMention,
      handleMentionKey,
    } = chatWorkspaceModule("PortalChatWorkspaceComposerMenus").createComposerMenus({
      bindMessageAvatars, composerMentionToken, currentController: () => controller, els, insertPlainText,
      isComposerImeActive, mentionMenuHtml, normalizeSlashCommands, options, setComposerDraftInput,
      slashCommandMatches, slashCommandMenuHtml, state,
      // Defined later in the Workspace.
      attachmentPreviewOptions: (...args) => attachmentPreviewOptions(...args),
      saveActiveDraft: (...args) => saveActiveDraft(...args),
      updateControls: (...args) => updateControls(...args),
    });

    const configuredHistoryScope = (composerState = state.composer) => {
      const optionScope = typeof options.historyScope === "function"
        ? options.historyScope()
        : options.historyScope;
      return String(optionScope || composerState?.history_scope || id);
    };
    const historyScope = () => configuredHistoryScope();

    const {
      captureWorkspaceDraft, saveActiveDraft, markDraftSaved, openDraftScope, clearActiveDraft, clearDraft,
      recoverSubmission, keepSubmissionDraft, forgetConfirmedSubmission, destroyComposerDrafts,
    } = chatWorkspaceModule("PortalChatWorkspaceComposerDrafts").createComposerDrafts({
      composerSelectionSnapshot, createDraftStore, els, id, normalizeWorkspaceDraft, options,
      persistedDraftAttachments, refsPayload, serializeComposerInput, state, workspaceDraftHasContent,
    });

    const {
      attachmentPreviewOptions, renderAttachments, renderAttachmentTrayOnly, withoutAttachmentReconcile,
      setAttachments, updateAttachment, addFiles, addReference, uploadComposerAttachment, removeComposerAttachment,
      reconcileAttachmentsFromInput, discardComposerAttachments, releaseAttachmentPreviews, reportAttachmentError,
      destroyComposerAttachments,
    } = chatWorkspaceModule("PortalChatWorkspaceComposerAttachments").createComposerAttachments({
      composerAttachmentTokenOrder, composerSelectionSnapshot,
      createConversationSelectionReference, currentController: () => controller, els, historyScope,
      insertComposerAttachmentRef, mediaPartPath, normalizeComposerAttachment, normalizeComposerAttachments, options,
      removeComposerAttachmentRef, renderComposerAttachmentTray, requireImageEditor, restoreComposerSelection,
      saveActiveDraft, state, syncComposerAttachmentRefs,
      // Made below: the undo takes back these attachments, so it comes after them.
      markComposerUndoAfterSnapshot: (...args) => markComposerUndoAfterSnapshot(...args),
      pushComposerUndoSnapshot: (...args) => pushComposerUndoSnapshot(...args),
      // Defined later in the Workspace.
      updateControls: (...args) => updateControls(...args),
    });

    const {
      composerSnapshot, composerMutationSignature, pushComposerUndoSnapshot, dropComposerUndoSnapshot,
      markComposerUndoAfterSnapshot, forgetComposerUndo, restoreComposerSnapshot, undoComposerMutation,
    } = chatWorkspaceModule("PortalChatWorkspaceComposerUndo").createComposerUndo({
      composerSelectionSnapshot, currentController: () => controller, discardComposerAttachments, els,
      normalizeComposerAttachments, options, refsPayload, renderAttachmentTrayOnly, renderAttachments,
      restoreComposerSelection, serializeComposerInput, state, withoutAttachmentReconcile,
    });
    // One Fork card per conversation: whether its compaction is open, and what was read.
    let inheritanceView = null;
    const inheritanceViewFor = inheritance => {
      if (!inheritance || typeof inheritance !== "object") return null;
      const key = `${historyScope()}\n${inheritance.source_resident_id || ""}\n${inheritance.forked_from_at || ""}`;
      if (inheritanceView?.key !== key) inheritanceView = { key, expanded: false, status: "idle", compaction: null, error: "" };
      return inheritanceView;
    };
    const renderInheritanceCard = () => {
      const node = els.messages?.querySelector('.messageList > [data-message-key="__inheritance__"]');
      if (!node) return;
      const renderOptions = { ...options, ...state.messageRenderOptions };
      const template = document.createElement("template");
      template.innerHTML = inheritanceCardHtml(renderOptions.inheritance, inheritanceViewFor(renderOptions.inheritance), renderOptions).trim();
      if (template.content.firstElementChild) node.replaceWith(template.content.firstElementChild);
    };
    const toggleInheritedSummary = async () => {
      const view = inheritanceViewFor(state.messageRenderOptions?.inheritance);
      if (!view) return;
      view.expanded = !view.expanded;
      const load = view.expanded && !["loading", "ready"].includes(view.status) && options.onLoadInheritedContext;
      if (load) Object.assign(view, { status: "loading", error: "" });
      renderInheritanceCard();
      if (!load) return;
      try {
        const inheritance = await options.onLoadInheritedContext(controller);
        const compaction = inheritance?.opening_compaction;
        Object.assign(view, compaction && typeof compaction === "object"
          ? { status: "ready", compaction }
          : { status: "error", error: window.PortalI18n.t("这次 Fork 已没有可读取的压缩。") });
      } catch (error) {
        Object.assign(view, { status: "error", error: error?.message || String(error) });
      }
      if (inheritanceView === view) renderInheritanceCard();
    };
    const insertEditedMessageImage = async ({ file }) => {
      if (!file) return;
      await addFiles([file]);
      els.input?.focus?.();
    };
    const openMessageImageEditor = async (src, title = "") => {
      const url = String(src || "").trim();
      if (!url) return;
      try {
        await requireImageEditor();
        openImagePreview(url, title || "image", {
          editable: true,
          name: title || "annotated-image",
          onSave: insertEditedMessageImage,
        });
      } catch (error) {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
      }
    };
    // Draw the list again from the current messages and live runtime.
    const redrawMessages = () => controller.setMessages(state.messages, {
      ...state.messageRenderOptions,
      runtimeDetails: state.runtimeDetails,
      runtimeLive: state.runtimeLive,
      runtimePhase: state.runtimePhase,
      force: true,
    });
    const { destroyMessageActions } = chatWorkspaceModule("PortalChatWorkspaceMessageActions").createMessageActions({
      bindMessageSubmenus, closeMessageSubmenus, consecutiveAgentGroups, currentController: () => controller, els,
      messageCopyText, messageRenderKey, messageRole, options, redrawMessages, residentHandoffCards,
      state, toggleMessageFold,
    });
    // A message's attachment opens a file in the Workspace preview and an image
    // in the image editor; an image without a preview URL has its path copied.
    const openMessageAttachment = (renderOptions, kind, path, node) => {
      const attachmentPath = String(path || "").trim();
      if (String(kind || "") === "file" && window.PortalFileLinks?.kindForPath(path) !== "image") {
        const descriptor = window.PortalFileLinks?.describe(attachmentPath) || { path: attachmentPath };
        const request = { ...descriptor, href: descriptor.path, title: node?.textContent?.trim() || attachmentPath, node, source: "chat", target: els.messages };
        const open = renderOptions.onLinkPreview || options.onLinkPreview || requestWorkspacePreview;
        Promise.resolve().then(() => open(request)).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"));
        return;
      }
      const previewUrlForPath = renderOptions.previewUrlForPath || options.previewUrlForPath;
      const previewUrl = previewUrlForPath ? previewUrlForPath(attachmentPath) : "";
      if (previewUrl) openMessageImageEditor(previewUrl, attachmentPath);
      else void window.PortalUI.copyText(attachmentPath, window.PortalI18n.t("附件路径"));
    };
    // Every render of the message list ends here. What was drawn gets its
    // buttons and previews bound and its folds measured, then the host is told.
    // `incremental` tells the host that only `roots` changed, as when one message
    // is appended; otherwise it treats the whole list as drawn again.
    const afterMessagesDrawn = (roots, { incremental = false } = {}) => {
      const renderOptions = state.messageRenderOptions;
      for (const root of roots) {
        bindAttachmentRefs(root, {
          previewUrlForPath: renderOptions.previewUrlForPath || options.previewUrlForPath,
          onOpen: (kind, path, node) => openMessageAttachment(renderOptions, kind, path, node),
          onImagePreview: context => openMessageImageEditor(context?.src, context?.title),
        });
        bindResponseSourcePaths(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
        bindResourceChips(root, { onResourcePreview: renderOptions.onResourcePreview || options.onResourcePreview });
        bindMessageAvatars(root);
        if (options.enableLinkPreview || renderOptions.enableLinkPreview) {
          bindLinkPreview(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
        }
      }
      measureMessageFolds(roots);
      revealFinishedSubagents(els.messages, options.onSubagentFinished);
      options.onMessagesRendered?.(els.messages, controller, incremental, roots);
    };
    const messageRuntimeSignature = () => JSON.stringify({
      details: state.runtimeDetails,
      live: state.runtimeLive,
      phase: state.runtimePhase,
    });
    // Besides the messages, a render of the list depends on this context: its
    // options except the live runtime (setRuntime patches that in place), the
    // Chat's Resident, the host's render state, the groups the reader collapsed
    // or expanded, and the host's render functions, compared by identity. Rows
    // drawn in another context cannot be kept.
    const messageRenderContext = renderOptions => ({
      signature: JSON.stringify({
        ...renderOptions,
        chatResidentId: contractResidentId(),
        messageRenderState: options.messageRenderState?.(),
        runtimeDetails: undefined,
        runtimeLive: undefined,
        runtimePhase: undefined,
        force: undefined,
        collapsedAgentGroups: [...state.collapsedAgentGroups].sort(),
        collapsedWorkGroups: [...state.collapsedWorkGroups].sort(),
        expandedAgentGroups: [...state.expandedAgentGroups].sort(),
      }),
      renderers: [
        renderOptions.renderMarkdown || options.renderMarkdown,
        renderOptions.formatTime || options.formatTime,
        renderOptions.renderMessagePrefix || options.renderMessagePrefix,
        renderOptions.renderMessageBody || options.renderMessageBody,
        renderOptions.renderMessageActions || options.renderMessageActions,
        renderOptions.renderMessageMenuItems || options.renderMessageMenuItems,
        renderOptions.showMessageActions || options.showMessageActions,
        renderOptions.messageCopyValue || options.messageCopyValue,
        renderOptions.renderMessageFooter || options.renderMessageFooter,
        renderOptions.renderAfterMessage || options.renderAfterMessage,
        renderOptions.messageClass || options.messageClass,
        renderOptions.previewUrlForPath || options.previewUrlForPath,
      ],
    });
    const sameMessageRenderContext = (a, b) => a.signature === b.signature
      && a.renderers.length === b.renderers.length
      && a.renderers.every((renderer, index) => renderer === b.renderers[index]);
    // Rows the list draws besides messages: above them the history loader and
    // the inheritance card, after them the live runtime and the scroll anchor.
    const MESSAGE_LIST_HEAD_KEYS = ["__history__", "__inheritance__"];
    const RUNTIME_ROW_KEYS = ["__runtime_phase__", "__subagents__", "__runtime__"];
    const MESSAGE_LIST_TAIL_KEYS = [...RUNTIME_ROW_KEYS, "__scroll_anchor__"];
    // A message's rows: its own and the block shown after it.
    const messageRowKeys = items => items.flatMap(item => [messageInstanceKey(item), messageAfterKey(messageInstanceKey(item))]);
    // A partial render draws state.messages[start, end) and replaces the rows
    // named by replacedKeys; the rows around them stay. Older messages loaded
    // above the shown ones are drawn with the shown rows they change.
    const prependedMessageRange = (previousMessages, foldChanges) => {
      const prependedCount = state.messages.length - previousMessages.length;
      let end = prependedCount;
      // Loading the first history page introduces a boundary on the existing facade message.
      if (state.messages[prependedCount - 1]?.family_history && !state.messages[prependedCount]?.family_history) {
        end = Math.min(state.messages.length, prependedCount + 1);
      }
      // Older pages may move a work's header past intervening requests.
      const nextWorkGroups = familyWorkGroups(state.messages);
      for (const [id, work] of familyWorkGroups(previousMessages)) {
        if (nextWorkGroups.get(id)?.headKey === work.headKey) continue;
        const oldHeadIndex = state.messages.findIndex(item => messageRenderKey(item) === work.headKey);
        end = Math.max(end, oldHeadIndex + 1);
      }
      for (const key of foldChanges) {
        const index = state.messages.findIndex(item => messageRenderKey(item) === key);
        end = Math.max(end, index + 1);
      }
      // A prepend may join the first retained reply group or change the
      // compact headers of its first human burst. Stop at a role boundary.
      while (end < state.messages.length && messageRole(state.messages[end]) === messageRole(state.messages[end - 1])) {
        end += 1;
      }
      return {
        start: 0,
        end,
        replacedKeys: [...messageRowKeys(previousMessages.slice(0, end - prependedCount)), ...MESSAGE_LIST_HEAD_KEYS],
      };
    };
    // Otherwise the list is drawn from the first message that changed; null
    // when that is the first message, and the whole list is drawn.
    const changedMessageRange = (previousMessages, previousSignatures, signatures, foldChanges) => {
      const sharedLength = Math.min(previousSignatures.length, signatures.length);
      let start = 0;
      while (start < sharedLength && previousSignatures[start] === signatures[start]) start += 1;
      for (const key of foldChanges) {
        const index = state.messages.findIndex(item => messageRenderKey(item) === key);
        if (index >= 0) start = Math.min(start, index);
      }
      // Editing or removing a group's tail changes its preceding members'
      // disclosure state too. Preserve only the prefix outside that group.
      if (
        messageRole(state.messages[start]) === "agent"
        || messageRole(previousMessages[start]) === "agent"
        || start === state.messages.length
      ) {
        while (start > 0 && messageRole(state.messages[start - 1]) === "agent") start -= 1;
      }
      if (!start) return null;
      return {
        start,
        end: state.messages.length,
        replacedKeys: [...messageRowKeys(previousMessages.slice(start)), ...MESSAGE_LIST_TAIL_KEYS],
      };
    };
    const historyPreviewUrl = () => state.messageRenderOptions?.previewUrlForPath || options.previewUrlForPath;
    const history = createInputHistory({
      // The host reads the person's inputs from the server (ListInputHistory with
      // resident_id or chatroom_id); a Chat without that has no input history.
      loadPage: options.onLoadInputHistory ? async (scope, pageToken) => {
        const page = await options.onLoadInputHistory(scope, pageToken);
        if (!page) return null;
        return {
          items: (Array.isArray(page.messages) ? page.messages : [])
            .map(item => messageInputHistoryItem(item, { previewUrlForPath: historyPreviewUrl() })),
          next_page_token: page.next_page_token,
        };
      } : null,
      onError: error => options.onError?.(error),
      serializeDraft: input => serializeComposerInput(input, state.attachments, { mode: "draft" }),
      serializeDraftMeta: () => ({
        attachments: state.attachments.map(item => ({ ...item })),
      }),
      restoreDraft: (input, value, meta = null) => {
        state.attachments = normalizeComposerAttachments(meta && Array.isArray(meta.attachments) ? meta.attachments : []);
        setComposerDraftInput(input, value, state.attachments, attachmentPreviewOptions());
        renderAttachmentTrayOnly({ notify: true });
      },
    });
    // Keep the input history with the conversation shown: this person's inputs
    // still queued for a turn come first, and when the Chat
    // shows a new input of theirs, from here or another browser, the history
    // reads the server's first page again.
    const ownInputMarks = new Map();
    const syncHistory = (scope = historyScope()) => {
      const scopeKey = String(scope || "");
      const viewer = String(window.PortalIdentity?.username?.() || "").trim().toLowerCase();
      const queued = normalizeWorkspaceQueue(state.composer?.queue).items
        .filter(item => workspaceQueueActionId(item) && Array.isArray(item.content_parts) && item.content_parts.length
          && (!item.author_id || String(item.author_id).trim().toLowerCase() === viewer))
        .map(item => messageInputHistoryItem(residentProtocolMessageToWorkspace({ ...item, role: "user" }), {
          previewUrlForPath: historyPreviewUrl(),
        }));
      history.setQueuedItems(scopeKey, queued.reverse());
      const ownInputs = (state.messages || []).filter(item => !item.local_delivery && messageAuthoredByViewer(item));
      const latest = ownInputs.length ? messageIdentity(ownInputs[ownInputs.length - 1]) : "";
      const previous = ownInputMarks.get(scopeKey);
      ownInputMarks.set(scopeKey, latest);
      if (previous !== undefined && previous !== latest) history.refresh(scopeKey);
      else history.prepare(scopeKey);
    };
    const canSubmit = () => {
      const uploading = state.attachments.some(item => item.uploading);
      const failed = state.attachments.some(item => item.error);
      const text = serializeComposerInput(els.input, state.attachments);
      const canSendByContract = state.composer.can_send !== false;
      const textRequiredByContract = state.composer.requires_text === true;
      const localSendBlocked = options.canSend && !options.canSend({
        text,
        attachments: state.attachments.slice(),
        refs: normalizeRefs(state.refs),
      });
      return !state.loading
        && !state.busy
        && !isComposerImeActive(els.input)
        && !uploading
        && !failed
        && canSendByContract
        && !(textRequiredByContract && !String(text || "").trim())
        && !localSendBlocked;
    };
    const setWorkspaceLoading = (message = "正在加载…") => {
      conversationSearch?.close();
      root.querySelector("[data-workspace-history-search]").disabled = true;
      saveActiveDraft({ immediate: true });
      state.loading = true;
      state.loadingMessage = String(message || "正在加载…");
      root.classList.add("is-workspace-loading");
      root.setAttribute("aria-busy", "true");
      for (const node of [els.body, els.composer, els.composerSplitter]) {
        if (!node) continue;
        node.inert = true;
        node.setAttribute("aria-hidden", "true");
      }
      if (els.loading) {
        els.loading.innerHTML = window.PortalUIPrimitives.asyncStateHtml({
          state: "loading",
          title: "",
          message: state.loadingMessage,
          icon: "loading",
          className: "portalWorkspaceLoadingState",
        });
        els.loading.hidden = false;
        window.PortalUIPrimitives?.mountIcons?.(els.loading);
      }
      updateControls();
    };
    const clearWorkspaceLoading = () => {
      root.querySelector("[data-workspace-history-search]").disabled = false;
      state.loading = false;
      state.loadingMessage = "";
      root.classList.remove("is-workspace-loading");
      root.removeAttribute("aria-busy");
      for (const node of [els.body, els.composer, els.composerSplitter]) {
        if (!node) continue;
        node.inert = false;
        node.removeAttribute("aria-hidden");
      }
      if (els.loading) {
        els.loading.hidden = true;
        els.loading.replaceChildren();
      }
      updateControls();
    };
    // The message the composition quotes, per conversation; not saved with the draft.
    const composerQuotes = new Map();
    const composerQuoteScope = () => historyScope() || state.activeDraftScope || "";
    const composerQuote = () => composerQuotes.get(composerQuoteScope()) || null;
    const renderComposerQuote = () => {
      const host = els.composer?.querySelector("[data-composer-quote-host]");
      if (!host) return;
      const quote = composerQuote();
      const messageId = quote ? quote.message_id : "";
      if ((host.dataset.composerQuote || "") === messageId) return;
      host.dataset.composerQuote = messageId;
      host.innerHTML = quote ? composerQuoteHtml(quote, options) : "";
      if (quote) bindResourceChips(host, options);
      host.hidden = !quote;
      if (quote) window.PortalUIPrimitives?.mountIcons?.(host);
    };
    let stopPending = false;
    const updateControls = () => {
      const uploading = state.attachments.some(item => item.uploading);
      const failed = state.attachments.some(item => item.error);
      const canStopByContract = !!state.composer.can_stop;
      if (els.send) {
        els.send.disabled = !canSubmit();
        els.send.title = state.composer.can_send === false ? state.composer.disabled_reason || window.PortalI18n.t("当前对话暂不可发送") : window.PortalI18n.t("发送");
      }
      if (els.stop) {
        els.stop.disabled = stopPending || (state.contract ? !canStopByContract : !(canStopByContract || (options.canStop && options.canStop())));
        els.stop.textContent = stopPending ? window.PortalI18n.t("正在停止…") : window.PortalI18n.t("Stop");
        els.stop.title = stopPending ? window.PortalI18n.t("已请求停止，等待当前任务确认") : window.PortalI18n.t("停止当前任务");
        els.stop.setAttribute("aria-label", els.stop.title);
      }
      if (els.status) {
        const transientStatus = state.busy
          ? (options.busyText || "Sending...")
          : uploading
            ? "附件上传中..."
            : failed
              ? "有附件上传失败"
              : "";
        renderSubtitle(transientStatus);
      }
      renderComposerQuote();
    };
    const composerInputHeightLimits = () => {
      const rootHeight = root.getBoundingClientRect().height || window.innerHeight || 800;
      const min = Math.max(40, Number(options.composerMinHeight || 56));
      const configuredMax = Math.max(min, Number(options.composerMaxHeight || 360));
      const max = Math.max(min, Math.min(configuredMax, Math.round(rootHeight * 0.46)));
      return { min, max };
    };
    const applyComposerInputLayout = ({ persist = false } = {}) => {
      if (!composerResizeEnabled) return;
      const limits = composerInputHeightLimits();
      state.composerInputHeight = Math.max(limits.min, Math.min(limits.max, Number(state.composerInputHeight || defaultComposerInputHeight)));
      root.style.setProperty("--portal-composer-input-h", `${Math.round(state.composerInputHeight)}px`);
      if (persist) {
        writeStoredComposerInputHeight(state.composerInputHeight);
        saveActiveDraft();
      }
    };
    const handleWindowResize = () => {
      applyComposerInputLayout();
      followTailAfterResize();
    };
    const bindComposerSplitter = () => {
      const handle = els.composerSplitter;
      if (!composerResizeEnabled || !handle) return;
      if (!window.PortalWorkbench.layout) {
        throw new Error("PortalWorkbench.layout must be loaded before resizable Chat Workspaces.");
      }
      window.PortalWorkbench.layout.bindSplitter({
        handle,
        axis: "y",
        direction: -1,
        bodyClass: "resizingComposer",
        boundKey: "portalComposerSplitterBound",
        min: () => composerInputHeightLimits().min,
        max: () => composerInputHeightLimits().max,
        readSize: () => state.composerInputHeight,
        applySize: height => {
          state.composerInputHeight = height;
          applyComposerInputLayout();
        },
        onDone: () => applyComposerInputLayout({ persist: true }),
      });
      window.addEventListener("resize", handleWindowResize);
      applyComposerInputLayout();
    };
    // Show the draft of a conversation in the composer.
    const restoreDraftScope = (scope, { force = false, focus = document.activeElement === els.input } = {}) => {
      const nextScope = String(scope || "").trim();
      if (!nextScope || !els.input) return false;
      if (!force && state.activeDraftScope === nextScope) return false;
      closeMentionMenu();
      const draft = openDraftScope(nextScope);
      state.restoringDraft = true;
      try {
        withoutAttachmentReconcile(() => {
          state.attachments = normalizeComposerAttachments(draft.attachments);
          state.refs = normalizeRefs(draft.refs);
          if (draft.input_height > 0) state.composerInputHeight = draft.input_height;
          setComposerDraftInput(els.input, draft.text, state.attachments, {
            ...attachmentPreviewOptions(),
            focus,
          });
          renderAttachmentTrayOnly({ notify: false });
          refreshSlashCommandMenu();
          controller.refreshRefs();
          applyComposerInputLayout();
          if (focus && draft.selection) restoreComposerSelection(els.input, draft.selection);
          updateControls();
        });
      } finally {
        state.restoringDraft = false;
      }
      markDraftSaved();
      return true;
    };
    const clearComposerInput = ({ notify = true, focus = true } = {}) => {
      const restoring = state.restoringDraft;
      state.restoringDraft = true;
      state.recoveredSubmission = null;
      try {
        setInputText(els.input, "", { focus });
        releaseAttachmentPreviews(state.attachments);
        state.attachments = [];
        forgetComposerUndo();
        renderAttachments({ notify });
        closeSlashCommandMenu();
        closeMentionMenu();
      } finally {
        state.restoringDraft = restoring;
      }
    };
    const patchComposerElement = (current, desired) => {
      if (!current || !desired) return;
      for (const attribute of [...current.attributes]) {
        if (attribute.name.endsWith("-bound") || attribute.name === "data-portal-icon-mounted") continue;
        if (!desired.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
      }
      for (const attribute of [...desired.attributes]) {
        current.setAttribute(attribute.name, attribute.value);
      }
      if (current.innerHTML !== desired.innerHTML) current.replaceChildren(...desired.cloneNode(true).childNodes);
    };
    const composerControlKey = node => {
      if (!node) return "";
      if (node.classList?.contains("refsWrap")) return "__refs__";
      return String(node.id || node.dataset?.workspaceComposerControl || "");
    };
    const patchComposerShell = (composerOptions = {}) => {
      const template = document.createElement("template");
      template.innerHTML = window.PortalI18n.html`<div>${workspaceComposerHtml(composerShellOptions(composerOptions))}</div>`;
      const desired = template.content.firstElementChild;
      if (!desired || !els.input) return false;

      const desiredInput = desired.querySelector(`#${CSS.escape(dom.inputId)}`);
      if (desiredInput) {
        els.input.setAttribute("data-placeholder", desiredInput.dataset.placeholder || "");
        els.input.setAttribute("contenteditable", desiredInput.getAttribute("contenteditable") || "true");
        els.input.setAttribute("role", desiredInput.getAttribute("role") || "textbox");
        els.input.setAttribute("aria-multiline", desiredInput.getAttribute("aria-multiline") || "true");
      }

      for (const position of ["before", "after"]) {
        const selector = `[data-workspace-composer-extension="${position}"]`;
        const current = composer.querySelector(selector);
        const next = desired.querySelector(selector);
        if (current && next && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
      }
      for (const selector of ["[data-workspace-queue-host]", "[data-workspace-composer-meta]"]) {
        const current = composer.querySelector(selector);
        const next = desired.querySelector(selector);
        if (current && next && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
      }

      const currentControls = composer.querySelector("[data-workspace-composer-controls]");
      const desiredControls = desired.querySelector("[data-workspace-composer-controls]");
      if (currentControls && desiredControls) {
        const currentByKey = new Map(
          [...currentControls.children].map(node => [composerControlKey(node), node]).filter(([key]) => key),
        );
        const retained = new Set();
        [...desiredControls.children].forEach((next, index) => {
          const key = composerControlKey(next);
          let current = currentByKey.get(key) || null;
          if (current && key === "__refs__") {
            retained.add(current);
          } else if (current) {
            patchComposerElement(current, next);
            retained.add(current);
          } else {
            current = next.cloneNode(true);
            retained.add(current);
          }
          const position = currentControls.children[index] || null;
          if (position !== current) currentControls.insertBefore(current, position);
        });
        [...currentControls.children].forEach(node => {
          if (retained.has(node)) return;
          if (node.classList.contains("refsWrap")) {
            refsControlBinding?.destroy();
            refsControlBinding = null;
          }
          node.remove();
        });
      }

      const currentSummary = composer.querySelector(`[data-refs-summary="${CSS.escape(refsScope)}"]`);
      const desiredSummary = desired.querySelector(`[data-refs-summary="${CSS.escape(refsScope)}"]`);
      if (!desiredSummary && currentSummary) currentSummary.remove();
      else if (desiredSummary && !currentSummary) {
        const after = composer.querySelector('[data-workspace-composer-extension="after"]');
        composer.insertBefore(desiredSummary.cloneNode(true), after || null);
      }
      window.PortalUIPrimitives?.mountIcons?.(composer);
      return true;
    };
    const renderComposerNow = (normalizedComposer, signature) => {
      state.composer = normalizedComposer;
      state.composerSignature = signature;
      const existingInput = els.input;
      const composerOptions = {
        placeholder: state.composer.placeholder,
        meta: state.composer.meta,
        metaHtml: state.composer.metaHtml || options.metaHtml || "",
        beforeComposerHtml: state.composer.beforeComposerHtml ?? options.beforeComposerHtml ?? "",
        afterComposerHtml: state.composer.afterComposerHtml || options.afterComposerHtml || "",
        send_label: state.composer.send_label,
        queue: state.composer.queue || null,
        can_attach: state.composer.can_attach,
        refs_enabled: state.composer.refs_enabled,
        can_edit: state.composer.can_edit,
        can_stop: state.composer.can_stop,
        requires_text: state.composer.requires_text,
        capabilities: state.composer.capabilities,
      };
      if (existingInput) {
        if (!patchComposerShell(composerOptions)) {
          throw new Error("PortalChatWorkspace failed to patch the mounted composer");
        }
      } else {
        renderComposerShell(composerOptions);
      }
      state.composerQueueSignature = JSON.stringify(state.composer.queue || null);
      Object.assign(els, {
        composer,
        input: root.querySelector(`#${CSS.escape(dom.inputId)}`),
        tray: root.querySelector(`#${CSS.escape(dom.trayId)}`),
        fileInput: root.querySelector(`#${CSS.escape(dom.fileInputId)}`),
        attach: root.querySelector(`#${CSS.escape(dom.attachId)}`),
        send: root.querySelector(`#${CSS.escape(dom.sendId)}`),
        stop: root.querySelector(`#${CSS.escape(dom.stopId)}`),
        auto: root.querySelector(`#${CSS.escape(dom.autoId)}`),
        slashCommands: root.querySelector("[data-composer-slash-command-host]"),
        mentions: root.querySelector("[data-composer-mention-host]"),
      });
      if (existingInput && els.input !== existingInput) {
        throw new Error("PortalChatWorkspace contract update replaced the composer input");
      }
      if (!existingInput) {
        state.composerInputFocused = false;
        state.composerImeActive = false;
      }
      bindComposer();
      bindAttachmentRefs(els.composer, {
        previewUrlForPath: options.previewUrlForPath,
      });
      applyComposerInputLayout();
      renderAttachments();
      refreshSlashCommandMenu();
      controller.refreshRefs();
      refsControlBinding?.refresh?.();
      options.onComposerRendered?.(controller);
      updateControls();
    };
    const controller = {
      root,
      els,
      state,
      setLoading(message) {
        setWorkspaceLoading(message);
      },
      clearLoading() {
        clearWorkspaceLoading();
      },
      isLoading: () => state.loading,
      addFiles,
      addReference,
      retryAttachment(id) {
        return uploadComposerAttachment(id);
      },
      attachments: () => state.attachments.slice(),
      messages: () => state.messages.slice(),
      setAttachments,
      setTitle(title) {
        const node = root.querySelector(".portalWorkspaceHead .paneTitle");
        if (node) node.textContent = String(title || window.PortalI18n.t("Chat Workspace"));
      },
      setSubtitle(subtitle, parts = []) {
        state.subtitle = String(subtitle || "");
        state.subtitleParts = normalizeWorkspaceSubtitleParts(parts);
        renderSubtitle();
      },
      setStopPending(value) { stopPending = Boolean(value); updateControls(); },
      setComposer(nextComposer = {}) {
        if (!state.contract) {
          throw new Error("PortalChatWorkspace composer requires a Chat Workspace contract");
        }
        const rawNext = nextComposer || {};
        const { force_render: _forceRenderSnake, forceRender: _forceRenderCamel, ...next } = { ...rawNext };
        const defaultSendLabel = String(options.send_label || "").trim();
        const normalizedComposer = {
          ...next,
          can_attach: next.can_attach !== false,
          refs_enabled: next.refs_enabled !== false,
          can_edit: next.can_edit !== false,
          can_stop: !!next.can_stop || !!next.canStop,
          can_auto: next.can_auto !== false && next.canAuto !== false,
          requires_text: next.requires_text === true,
          send_label: String(next.send_label ?? defaultSendLabel).trim(),
          capabilities: Array.isArray(next.capabilities) ? next.capabilities.map(item => String(item || "").trim()).filter(Boolean) : [],
          slash_commands: normalizeSlashCommands(next.slash_commands || options.slash_commands),
        };
        const nextDraftScope = configuredHistoryScope(normalizedComposer).trim() || id;
        const draftScopeChanged = state.activeDraftScope !== nextDraftScope;
        if (draftScopeChanged && state.activeDraftScope) saveActiveDraft({ immediate: true });
        const signature = JSON.stringify({
          placeholder: normalizedComposer.placeholder || "",
          meta: normalizedComposer.meta || "",
          can_attach: normalizedComposer.can_attach,
          refs_enabled: normalizedComposer.refs_enabled,
          can_edit: normalizedComposer.can_edit,
          can_stop: normalizedComposer.can_stop,
          can_auto: normalizedComposer.can_auto,
          requires_text: normalizedComposer.requires_text,
          capabilities: normalizedComposer.capabilities,
          runtime_settings: normalizedComposer.runtime_settings || null,
          runtime_backend: normalizedComposer.runtime_backend || "",
          runtime_catalog_key: runtimeCatalogUrl(normalizedComposer),
          metaHtml: normalizedComposer.metaHtml || options.metaHtml || "",
          beforeComposerHtml: normalizedComposer.beforeComposerHtml ?? options.beforeComposerHtml ?? "",
          afterComposerHtml: normalizedComposer.afterComposerHtml || options.afterComposerHtml || "",
          send_label: normalizedComposer.send_label,
          slash_commands: normalizedComposer.slash_commands,
          queue: JSON.stringify(normalizedComposer.queue || null),
        });
        state.composer = normalizedComposer;
        renderSubtitle();
        syncHistory();
        if (signature === state.composerSignature) {
          if (draftScopeChanged) restoreDraftScope(nextDraftScope, { force: true });
          updateControls();
          return;
        }
        renderComposerNow(normalizedComposer, signature);
        if (draftScopeChanged) restoreDraftScope(nextDraftScope, { force: true });
        updateControls();
      },
      updateAttachment,
      refs: () => normalizeRefs(state.refs),
      setRefs(refs) {
        state.refs = normalizeRefs(refs);
        renderRefsSummary(els.composer, { scope: refsScope, selection: state.refs, contextOptions: contextOptions() });
        saveActiveDraft();
      },
      payload() {
        const nextScope = historyScope();
        if (nextScope && state.activeDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        reconcileAttachmentsFromInput();
        return {
          text: serializeComposerInput(els.input, state.attachments, { mode: "final" }),
          plainText: serializeComposerInput(els.input, state.attachments, { mode: "plain" }),
          content_parts: serializeComposerContentParts(els.input, state.attachments),
          attachments: state.attachments.filter(item => !item.uploading && !item.error),
          refs: normalizeRefs(state.refs),
        };
      },
      refreshRefs() {
        const bindingOptions = {
          scope: refsScope,
          contextOptions: contextOptions(),
          catalogStatus: refsCatalog.status,
          onLoad: loadRefsCatalog,
          getSelection: () => state.refs,
          setSelection: refs => { state.refs = normalizeRefs(refs); },
          getCollapsed: () => state.refsCollapsed,
          setCollapsed: collapsed => { state.refsCollapsed = collapsed || {}; },
          onChange: refs => {
            saveActiveDraft();
            options.onRefsChange?.(refs, controller);
          },
        };
        refsControlBinding = refsControlBinding || bindRefsControl(els.composer, bindingOptions);
        refsControlBinding?.update(bindingOptions).refresh();
      },
      clear(clearOptions = {}) {
        const preserveDraft = clearOptions.clearDraft === false;
        if (preserveDraft) saveActiveDraft({ immediate: true });
        clearComposerInput({ notify: !preserveDraft });
        if (!preserveDraft) clearActiveDraft();
        else markDraftSaved();
      },
      inputText() {
        return serializeComposerInput(els.input, state.attachments, { mode: "plain" });
      },
      draftText() {
        reconcileAttachmentsFromInput();
        return serializeComposerInput(els.input, state.attachments, { mode: "draft" });
      },
      setInputText(value) {
        state.recoveredSubmission = null;
        setInputText(els.input, value || "");
        refreshSlashCommandMenu();
        updateControls();
      },
      setDraftText(value, attachments = null) {
        state.recoveredSubmission = null;
        withoutAttachmentReconcile(() => {
          if (attachments !== null) state.attachments = normalizeComposerAttachments(attachments);
          setComposerDraftInput(els.input, value || "", state.attachments, attachmentPreviewOptions());
          renderAttachments({ notify: true });
          refreshSlashCommandMenu();
        });
        updateControls();
        saveActiveDraft();
      },
      draftScope() {
        return state.activeDraftScope || historyScope();
      },
      saveDraft(saveOptions = {}) {
        saveActiveDraft({ immediate: saveOptions.immediate !== false });
      },
      restoreDraft(scope = historyScope(), restoreOptions = {}) {
        return restoreDraftScope(scope, { force: restoreOptions.force !== false });
      },
      clearDraft(scope = state.activeDraftScope || historyScope(), expected = null) {
        return clearDraft(scope, expected);
      },
      confirmSubmittedMessages(messageIds, scope = state.activeDraftScope || historyScope()) {
        if (!forgetConfirmedSubmission(messageIds, scope)) return;
        // Clear only this unedited recovery, leaving a newer shared draft intact.
        clearComposerInput({ focus: false });
        state.refs = normalizeRefs({});
        controller.refreshRefs();
        markDraftSaved();
      },
      quote: () => composerQuote(),
      setQuote(quote) {
        const scope = composerQuoteScope();
        const messageId = String(quote?.message_id || "").trim();
        const text = String(quote?.text || "").trim();
        if (!scope || !messageId || !text) return false;
        composerQuotes.set(scope, { message_id: messageId, author_id: String(quote.author_id || ""), text });
        renderComposerQuote();
        els.input?.focus?.();
        return true;
      },
      // Drops a conversation's quote; given a message ID, only while it still quotes that message.
      clearQuote({ scope = composerQuoteScope(), messageId = "" } = {}) {
        const current = composerQuotes.get(scope);
        if (!current || (messageId && current.message_id !== messageId)) return false;
        composerQuotes.delete(scope);
        renderComposerQuote();
        return true;
      },
      // Shows a message of this conversation, loading earlier history until it is there.
      async revealMessage(messageId) {
        const key = String(messageId || "").trim();
        if (!key || !els.messages) return false;
        const scope = historyScope();
        const find = () => els.messages.querySelector(`.messageList > [data-message-key="${CSS.escape(key)}"]`);
        let row = find();
        while (!row) {
          if (!options.onLoadOlder || !state.messageRenderOptions?.history?.can_load_older) break;
          const before = `${state.messages.length}:${messageRenderKey(state.messages[0] || {})}`;
          await options.onLoadOlder(controller);
          await new Promise(resolve => window.requestAnimationFrame(resolve));
          if (refsCatalog.disposed || historyScope() !== scope) return false;
          row = find();
          if (!row && before === `${state.messages.length}:${messageRenderKey(state.messages[0] || {})}`) break;
        }
        if (row?.dataset.workGroupId && state.collapsedWorkGroups.has(row.dataset.workGroupId)) {
          els.messages.querySelector(`[data-portal-work-group-toggle="${CSS.escape(row.dataset.workGroupId)}"]`)?.click();
          row = find();
        }
        if (row?.hidden && row.dataset.agentGroupId) {
          els.messages.querySelector(`[data-portal-agent-group-toggle="${CSS.escape(row.dataset.agentGroupId)}"]`)?.click();
          row = find();
        }
        if (!row || row.hidden) {
          window.PortalUI?.toast?.("这条消息已不在当前对话中。", "warning");
          return false;
        }
        const fold = row.querySelector('[data-message-fold-toggle][aria-expanded="false"]');
        if (fold) toggleMessageFold(fold);
        const box = row.getBoundingClientRect();
        const view = els.messages.getBoundingClientRect();
        els.messages.scrollTop += box.top - view.top - Math.max(12, (view.height - box.height) / 2);
        state.messageTailFollowIntent = messageViewportAtTail(els.messages);
        syncMessageScrollMode();
        row.classList.remove("messageQuoteTarget");
        void row.offsetWidth;
        row.classList.add("messageQuoteTarget");
        window.setTimeout(() => row.classList.remove("messageQuoteTarget"), 1600);
        return true;
      },
      async submit(send, submitOptions = {}) {
        if (typeof send !== "function") throw new Error("workspace.submit requires a send function");
        const nextScope = historyScope();
        if (nextScope && state.activeDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        const scope = state.activeDraftScope || historyScope();
        const sequence = ++state.submissionSequence;
        const snapshot = composerSnapshot();
        const draftSnapshot = captureWorkspaceDraft();
        const payload = submitOptions.payload && typeof submitOptions.payload === "object"
          ? submitOptions.payload
          : controller.payload();
        const refsSnapshot = refsPayload(payload.refs || state.refs);
        const historyText = String(submitOptions.historyText ?? payload.plainText ?? payload.text ?? "").trim();
        const historyDraftText = String(draftSnapshot.text || historyText).trim();
        const context = {
          scope,
          sequence,
          payload,
          refs: refsSnapshot,
          snapshot,
          workspace: controller,
          messageIdentity: newMessageIdentity(),
        };
        const optimistic = submitOptions.optimistic === true;
        if (submitOptions.clear !== false) controller.clear();
        if (optimistic && submitOptions.clear !== false) {
          state.refs = normalizeRefs({});
          controller.refreshRefs();
          controller.clearQuote({ scope });
          clearActiveDraft();
        }
        const submittedSignature = composerMutationSignature();
        if (!optimistic && submitOptions.busy !== false) controller.setBusy(true);
        if (optimistic && submitOptions.focus !== false) els.input?.focus?.();
        let committed = false;
        const accept = () => {
          const clearedSignature = composerMutationSignature();
          if (!optimistic && submitOptions.clearRefsOnSuccess && state.activeDraftScope === scope) {
            const refsUnchanged = JSON.stringify(refsPayload(state.refs)) === JSON.stringify(refsSnapshot);
            if (refsUnchanged) {
              state.refs = normalizeRefs({});
              controller.refreshRefs();
            }
            if (composerMutationSignature() === clearedSignature && refsUnchanged) clearActiveDraft();
            else saveActiveDraft({ immediate: true });
          }
        };
        try {
          const result = await send(payload, context);
          committed = true;
          context.result = result;
          accept();
          if (submitOptions.rememberHistory !== false && historyDraftText) {
            history.remember(scope, {
              // A conversation that names the message itself (a Chatroom) gives its ID back.
              id: submitOptions.sentMessageId?.(result) || context.messageIdentity,
              text: historyDraftText,
              meta: { attachments: draftSnapshot.attachments },
            });
          }
          await submitOptions.onSuccess?.(result, context);
          return result;
        } catch (error) {
          context.error = error;
          const sameScope = state.activeDraftScope === scope;
          const composerUntouched = composerMutationSignature() === submittedSignature;
          if (!committed && !optimistic && submitOptions.clear !== false && sameScope && composerUntouched && state.submissionSequence === sequence) {
            restoreComposerSnapshot(snapshot);
            state.refs = normalizeRefs(refsSnapshot);
            controller.refreshRefs();
            recoverSubmission(context.messageIdentity);
          } else if (!committed && !optimistic && submitOptions.clear !== false && !sameScope) {
            keepSubmissionDraft(scope, draftSnapshot, context.messageIdentity);
          }
          if (!committed) await submitOptions.onRollback?.(error, context);
          else await submitOptions.onSuccessError?.(error, context);
          throw error;
        } finally {
          if (!optimistic && submitOptions.busy !== false && state.submissionSequence === sequence) controller.setBusy(false);
          if (!optimistic && submitOptions.focus !== false && state.activeDraftScope === scope) els.input?.focus?.();
        }
      },
      setBusy(value) {
        state.busy = !!value;
        updateControls();
      },
      updateControls,
      setStatus(text) {
        state.subtitle = String(text || "");
        state.subtitleParts = [];
        renderSubtitle();
      },
      followLatest() {
        state.messageTailFollowIntent = true;
        syncMessageScrollMode();
        if (messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      },
      appendMessage(message) {
        const previous = state.messages;
        const list = els.messages?.firstElementChild;
        if (!message?.id || state.messageByKey.has(String(message.id))
          || !previous.length || !list?.classList.contains("messageList")
          || state.messageItemSignatures.length !== previous.length
          || state.messageScrollScope !== String(historyScope() || "")
          || (state.runtimeLive && messageRole(message) !== "agent")) return false;
        let start = previous.length - 1;
        while (start > 0 && messageRole(previous[start - 1]) === messageRole(previous[start])) start -= 1;
        const first = list.querySelector(messageInstanceSelector(messageInstanceKey(previous[start])));
        if (!first) return false;
        const prefixNode = first.previousElementSibling;
        const wasFollowing = state.messageTailFollowIntent;
        state.messages = [...previous, message];
        state.contract = { ...state.contract, messages: state.messages };
        syncHistory();
        state.messageItemSignatures.push(JSON.stringify(message));
        state.messageByKey.set(messageIdentity(message), message);
        // The same fold rules as a whole render; the rows kept above the redrawn
        // ones change in place.
        showMessageFoldDefaults(updateMessageFoldDefaults());
        conversationSearch?.update(true);
        const renderOptions = state.messageRenderOptions;
        const html = messageListHtml(state.messages.slice(start), {
          ...options, ...renderOptions,
          chatResidentId: contractResidentId(),
          inheritance: null,
          history: {},
          runtimeDetails: state.runtimeDetails,
          runtimeLive: state.runtimeLive,
          runtimePhase: state.runtimePhase,
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: state.collapsedAgentGroups,
          collapsedWorkGroups: state.collapsedWorkGroups,
          expandedAgentGroups: state.expandedAgentGroups,
          messageFoldDefaults: state.messageFoldDefaults,
          messageFoldSizes: state.messageFoldSizes,
          messageIndexOffset: start,
          allMessages: state.messages,
        });
        const changed = reconcileMessageList(els.messages, html, {
          currentStartNode: first,
          prefixNode,
          replacedKeys: [...messageRowKeys(previous.slice(start)), ...MESSAGE_LIST_TAIL_KEYS],
        })?.changedRoots || [];
        afterMessagesDrawn(changed, { incremental: true });
        syncMessageScrollMode();
        if (wasFollowing && messageViewportMeasurable()) els.messages.scrollTop = els.messages.scrollHeight;
        return true;
      },
      setMessages(messages, renderOptions = {}) {
        const previousMessages = state.messages;
        const previousSignatures = state.messageItemSignatures;
        const previousContext = state.messageRenderContext;
        const previousRuntime = messageRuntimeSignature();
        const previousScrollHeight = Number(els.messages?.scrollHeight || 0);
        const previousScrollTop = Number(els.messages?.scrollTop || 0);
        const previousDraftScope = String(state.activeDraftScope || "");
        const nextScope = String(historyScope() || "");
        const scopeChanged = state.messageScrollScope === null || state.messageScrollScope !== nextScope;
        state.messageScrollScope = nextScope;
        if (nextScope && previousDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        state.messages = Array.isArray(messages) ? messages : [];
        state.runtimeDetails = Array.isArray(renderOptions.runtimeDetails) ? renderOptions.runtimeDetails : [];
        state.runtimeLive = !!renderOptions.runtimeLive;
        state.runtimePhase = String(renderOptions.runtimePhase || "");
        const runtimeChanged = messageRuntimeSignature() !== previousRuntime;
        state.messageRenderOptions = { ...renderOptions };
        syncHistory(nextScope);
        const foldChanges = updateMessageFoldDefaults();
        const signatures = state.messages.map(item => JSON.stringify(item));
        const context = messageRenderContext(renderOptions);
        const messagesUnchanged = previousSignatures.length === signatures.length
          && previousSignatures.every((signature, index) => signature === signatures[index]);
        const contextUnchanged = sameMessageRenderContext(previousContext, context);
        conversationSearch?.update(!messagesUnchanged);
        if (messagesUnchanged && contextUnchanged && !renderOptions.force && !foldChanges.length) {
          // Nothing to draw but perhaps the live runtime, which setRuntime patches.
          if (runtimeChanged) controller.setRuntime(state.runtimeDetails, state.runtimeLive, renderOptions);
          return;
        }
        // The messages shown before are all still there, below older ones loaded above.
        const prependedCount = state.messages.length - previousMessages.length;
        const prependOnly = !renderOptions.force && !scopeChanged && prependedCount > 0
          && previousSignatures.length === previousMessages.length
          && previousSignatures.every((signature, index) => signature === signatures[index + prependedCount]);
        // Rows already shown may stay when they were drawn in the same context.
        const rowsReusable = !renderOptions.force && !scopeChanged
          && previousMessages.length > 0 && state.messages.length > 0 && contextUnchanged
          && els.messages.firstElementChild?.classList.contains("messageList")
          && previousSignatures.length === previousMessages.length;
        state.messageItemSignatures = signatures;
        state.messageRenderContext = context;
        const renderedStream = normalizeMessageStream(state.messages, { ...options, ...renderOptions });
        state.messageByKey = new Map(renderedStream.messages.map((item) => [
          messageIdentity(item),
          item,
        ]));
        const followTail = scopeChanged || state.messageTailFollowIntent;
        state.messageTailFollowIntent = followTail;
        if (scopeChanged) resetMessageScrollGesture();
        syncMessageScrollMode();
        // Drawing only the older messages leaves the tail as it was, so a
        // changed live runtime draws from the first changed message instead.
        const range = !rowsReusable ? null
          : prependOnly && !runtimeChanged ? prependedMessageRange(previousMessages, foldChanges)
          : changedMessageRange(previousMessages, previousSignatures, signatures, foldChanges);
        rememberSubagentPlaces(els.messages);
        const messageHtml = messageListHtml(state.messages, {
          ...options,
          ...renderOptions,
          chatResidentId: contractResidentId(),
          inheritanceView: inheritanceViewFor(renderOptions.inheritance),
          runtimeDetails: state.runtimeDetails,
          runtimeLive: state.runtimeLive,
          runtimePhase: state.runtimePhase,
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: state.collapsedAgentGroups,
          collapsedWorkGroups: state.collapsedWorkGroups,
          expandedAgentGroups: state.expandedAgentGroups,
          messageFoldDefaults: state.messageFoldDefaults,
          messageFoldSizes: state.messageFoldSizes,
          renderStartIndex: range ? range.start : 0,
          renderEndIndex: range ? range.end : state.messages.length,
        });
        const reconciled = reconcileMessageList(els.messages, messageHtml, range ? {
          prefixKey: range.start > 0 ? messageInstanceKey(state.messages[range.start - 1]) : "",
          replacedKeys: range.replacedKeys,
        } : {});
        // Only new/replaced message subtrees need binding and measurement. The
        // host still hears of a whole render, so it redraws what it keeps for
        // the whole conversation, such as a Resident Chat's question cards.
        afterMessagesDrawn(reconciled ? reconciled.changedRoots : [els.messages]);
        if (followTail && messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        } else if (prependOnly && messageViewportMeasurable()) {
          els.messages.scrollTop = previousScrollTop + Math.max(
            0,
            els.messages.scrollHeight - previousScrollHeight,
          );
        }
      },
      setRuntime(details, live, renderOptions = {}) {
        state.runtimeDetails = Array.isArray(details) ? details : [];
        state.runtimeLive = !!live;
        state.runtimePhase = String(renderOptions.runtimePhase ?? state.runtimePhase ?? "");
        state.messageRenderOptions = { ...state.messageRenderOptions, ...renderOptions };
        const list = els.messages?.firstElementChild;
        // An empty Chat has no list to patch. setMessages would find the
        // runtime it was just given unchanged, so it is told to draw.
        if (!list?.classList.contains("messageList")) {
          redrawMessages();
          return;
        }
        const template = document.createElement("template");
        template.innerHTML = runtimeStreamHtml(
          state.runtimeDetails,
          state.runtimeLive,
          { ...options, ...state.messageRenderOptions },
        );
        const nextByKey = new Map(
          [...template.content.children].map(node => [String(node.dataset.messageKey || ""), node]),
        );
        const scrollAnchor = list.querySelector("[data-message-scroll-anchor]");
        rememberSubagentPlaces(list);
        const runtimeKeys = RUNTIME_ROW_KEYS;
        runtimeKeys.forEach((key, index) => {
          const current = list.querySelector(`[data-message-key="${key}"]`);
          const next = nextByKey.get(key);
          if (!next) {
            current?.remove();
            return;
          }
          if (current?.outerHTML === next.outerHTML) return;
          if (current && key === "__subagents__") {
            patchSubagentLive(current, next);
          } else if (current) {
            preserveRuntimeDisclosureState(current, next);
            current.replaceWith(next);
          } else {
            const later = runtimeKeys.slice(index + 1)
              .map(laterKey => list.querySelector(`[data-message-key="${laterKey}"]`))
              .find(Boolean);
            list.insertBefore(next, later || scrollAnchor);
          }
        });
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        // A turn that starts or ends changes which messages start expanded.
        const foldDefaults = messageFoldDefaults(state.messages, { runtimeLive: state.runtimeLive });
        if (foldDefaults.size !== state.messageFoldDefaults.size
          || [...foldDefaults].some(key => !state.messageFoldDefaults.has(key))) {
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
          });
        }
      },
      applyUpdate(update, renderOptions = {}) {
        if (!update || typeof update !== "object") return false;
        const workspaceKey = String(update.workspace_key || "");
        const contractKey = String(state.contract?.workspace_key || "");
        if (workspaceKey && contractKey && workspaceKey !== contractKey) return false;
        const revision = String(update.revision || update.content_signature || "");
        if (revision && revision === state.updateRevision && update.force !== true) return false;
        if (update.contract && typeof update.contract === "object") {
          controller.applyContract(update.contract, renderOptions);
        } else {
          if (update.composer && typeof update.composer === "object") controller.setComposer({ ...state.composer, ...update.composer });
          const runtime = update.runtime && typeof update.runtime === "object" ? update.runtime : {};
          const incoming = Array.isArray(update.messages) ? update.messages : [];
          if (Array.isArray(update.messages) || update.reset === true || update.mode === "reset") {
            const messages = update.reset === true || update.mode === "reset"
              ? incoming
              : [...state.messages, ...incoming];
            controller.setMessages(messages, {
              ...renderOptions,
              runtimeDetails: Array.isArray(runtime.details) ? runtime.details : state.runtimeDetails,
              runtimeLive: runtime.live === undefined ? state.runtimeLive : !!runtime.live,
              runtimePhase: runtime.phase === undefined ? state.runtimePhase : String(runtime.phase || ""),
            });
          } else if (update.runtime && typeof update.runtime === "object") {
            controller.setRuntime(
              Array.isArray(runtime.details) ? runtime.details : state.runtimeDetails,
              runtime.live === undefined ? state.runtimeLive : !!runtime.live,
              {
                ...renderOptions,
                runtimePhase: runtime.phase === undefined ? state.runtimePhase : String(runtime.phase || ""),
              },
            );
          }
        }
        state.updateRevision = revision;
        return true;
      },
      applyContract(contract, renderOptions = {}) {
        if (!contract || typeof contract !== "object") return;
        state.contract = contract;
        runtimeTargetPending = false;
        if (runtimeResidentId && runtimeResidentId !== contractResidentId()) releaseResidentRuntime();
        const runtime = contract.runtime && typeof contract.runtime === "object" ? contract.runtime : {};
        const rendering = contract.rendering && typeof contract.rendering === "object" ? contract.rendering : {};
        const composerCfg = contract.composer && typeof contract.composer === "object" ? { ...contract.composer } : {};
        if (!Array.isArray(composerCfg.capabilities) && Array.isArray(contract.capabilities)) {
          composerCfg.capabilities = contract.capabilities;
        }
        const header = contract.header && typeof contract.header === "object" ? contract.header : {};
        controller.setTitle(contract.title || options.title || "Chat Workspace");
        controller.setSubtitle(
          contract.subtitle || composerCfg.meta || options.subtitle || "",
          header.subtitle_parts || [],
        );
        controller.setComposer(composerCfg);
        controller.setMessages(Array.isArray(contract.messages) ? contract.messages : [], {
          ...renderOptions,
          runtimeDetails: Array.isArray(runtime.details) ? runtime.details : [],
          runtimeLive: !!runtime.live,
          runtimePhase: String(runtime.phase || ""),
          emptyText: window.PortalI18n.t(rendering.empty_text || renderOptions.emptyText || options.emptyText || "暂无对话。"),
          userName: rendering.user_name || renderOptions.userName || options.userName,
          assistantName: rendering.assistant_name || renderOptions.assistantName || options.assistantName || "Agent",
          history: contract.history && typeof contract.history === "object" ? contract.history : {},
          inheritance: contract.inheritance && typeof contract.inheritance === "object" ? contract.inheritance : null,
          autoCollapseAgentGroups: rendering.auto_collapse_agent_groups === undefined
            ? renderOptions.autoCollapseAgentGroups
            : !!rendering.auto_collapse_agent_groups,
        });
        clearWorkspaceLoading();
        updateControls();
        options.onContractApplied?.(contract, controller);
        syncSessionRestartObservation();
      },
      releaseResidentRuntime() {
        releaseResidentRuntime(); runtimeTargetPending = true;
        const button = composer.querySelector("[data-workspace-runtime-settings-open]");
        if (button) button.disabled = true;
      },
      destroy() {
        releaseResidentRuntime();
        conversationSearch?.destroy();
        refsCatalog.disposed = true;
        destroyMentionMenu();
        restartUnobserve?.();
        restartProgress.destroy();
        destroyComposerDrafts();
        destroyComposerAttachments();
        refsControlBinding?.destroy();
        refsControlBinding = null;
        destroyMessageViewport();
        destroyMessageActions();
        destroyMessageFolds();
        window.removeEventListener("resize", handleWindowResize);
        if (root.__portalChatWorkspaceController === controller) {
          delete root.__portalChatWorkspaceController;
        }
        runtimeSettingsDialog?.destroy?.();
        runtimeSettingsDialog = null;
        sessionRestartDialog?.destroy?.();
        sessionRestartDialog = null;
      },
    };
    const saveWorkspaceRuntimeSettings = async runtimeSettings => {
      const action = state.contract?.actions?.runtime_settings;
      let result;
      if (typeof options.onRuntimeSettingsSave === "function") {
        result = await options.onRuntimeSettingsSave(runtimeSettings, controller, action);
      } else {
        result = await requestWorkspaceAction(state.contract, "runtime_settings", {
          changes: runtimeSettings,
        });
      }
      const contract = result?.chat_workspace || result?.contract;
      if (contract && typeof contract === "object") {
        controller.applyContract(contract, state.messageRenderOptions || {});
      } else {
        controller.setComposer({
          ...state.composer,
          runtime_settings: { ...state.composer.runtime_settings, ...runtimeSettings },
        });
      }
      notifyRuntimeSettingsSaved(result);
      options.onRuntimeSettingsSaved?.(result, controller);
      return result;
    };
    const openWorkspaceRuntimeSettings = async () => {
      if (runtimeTargetPending) return;
      const resident = residentRuntimeController();
      if (resident) {
        try { await resident.openSettings(() => composer.querySelector("[data-workspace-runtime-settings-open]")); }
        catch (error) { window.PortalUI.toast(error.message, "error"); }
        return;
      }
      const workspaceCatalogKey = runtimeCatalogUrl();
      runtimeSettingsDialog ||= createRuntimeSettingsDialog({ id: `${id}RuntimeSettings` });
      const suppliedOptions = typeof options.runtimeSettingsDialogOptions === "function"
        ? await options.runtimeSettingsDialogOptions(controller)
        : (options.runtimeSettingsDialogOptions || {});
      const dialogOptions = {
        backend: state.composer.runtime_backend, ...suppliedOptions,
        residentId: contractResidentId() || state.contract?.actions?.runtime_settings?.payload?.resident_id,
        accountId: state.composer.runtime_account_id,
      };
      if (!dialogOptions.catalog) {
        try {
          dialogOptions.catalog = await loadWorkspaceRuntimeCatalog({ ...state.composer, runtime_backend: dialogOptions.backend }, true).promise;
        } catch (error) {
          window.PortalUI?.toast?.(`无法读取当前 Provider 能力，未打开设置：${error.message}`, "error");
          return;
        }
      }
      if (workspaceCatalogKey !== runtimeCatalogUrl()) return;
      runtimeSettingsDialog.open(
        state.composer.runtime_settings || {},
        saveWorkspaceRuntimeSettings,
        dialogOptions,
      );
    };
    function sessionRestartAvailable() {
      if (runtimeTargetPending) return false;
      return (typeof options.onSessionRestartOptions === "function" && !!controller.target?.()?.resident_id)
        || !!state.contract?.actions?.session_restart?.instruction
        || !!state.contract?.actions?.session_restart?.url;
    }
    const openWorkspaceSessionRestart = async () => {
      if (runtimeTargetPending) return;
      const resident = residentRuntimeController();
      if (resident) {
        try { await resident.openRestart(() => composer.querySelector("[data-workspace-runtime-settings-open]")); }
        catch (error) { window.PortalUI.toast(error.message, "error"); }
        return;
      }
      const action = state.contract?.actions?.session_restart;
      const config = typeof options.onSessionRestartOptions === "function"
        ? await options.onSessionRestartOptions(controller, action)
        : await window.PortalAPI.get(String(action?.options_url || ""));
      sessionRestartDialog ||= createSessionRestartDialog({ id: `${id}SessionRestart` });
      sessionRestartDialog.open(
        config,
        async values => {
          const result = typeof options.onSessionRestart === "function"
              ? await options.onSessionRestart(values, controller, action)
              : await requestWorkspaceAction(state.contract, "session_restart", values);
          if (!["accepted", "completed"].includes(result?.status)) throw new Error("Session 切换结果尚未确认，请刷新查看状态");
          restartService?.rememberSessionRestart(config.resident_id, result);
          window.PortalUI.toast(result.status === "accepted" ? window.PortalI18n.t("迁移请求已受理，可在聊天顶部持续查看进度。") : result.configuration_only
            ? window.PortalI18n.t("Provider / 账号设置已保存；尚未创建 Session。")
            : window.PortalI18n.t("Session 已切换，旧 session 已保留。"), "success");
          if (result.status === "completed") {
            options.onSessionRestarted?.(result, controller);
            await options.onRefresh?.(controller);
          }
          return result;
        },
        () => options.onRefresh?.(controller),
        values => {
          if (typeof options.onSessionRestartCancel !== "function") {
            throw new Error("当前 Workspace 未提供 Session 迁移取消入口");
          }
          return options.onSessionRestartCancel(values, controller, action);
        },
      );
    };
    const bindComposer = () => {
      bindWorkspaceComposer({
        input: els.input,
        sendButton: els.send,
        stopButton: els.stop,
        attachButton: els.attach,
        fileInput: els.fileInput,
        historyScope,
        history,
        canSend: canSubmit,
        onSend: () => {
          const payload = controller.payload();
          options.onSend?.(payload, controller);
        },
        onStop: () => options.onStop?.(controller),
        onFiles: files => addFiles(files).catch(reportAttachmentError),
        onMoveAttachment: (token, event) => {
          const moved = moveComposerAttachmentRef(els.input, token, event);
          if (moved) {
            reconcileAttachmentsFromInput({ notify: true });
            options.onInput?.(controller.payload(), controller);
          }
          return moved;
        },
        onBeforeManualTextPaste: () => pushComposerUndoSnapshot(),
        onCancelManualTextPaste: frame => dropComposerUndoSnapshot(frame),
        onAfterManualTextPaste: frame => markComposerUndoAfterSnapshot(frame),
        onUndo: () => undoComposerMutation(),
        onBeforeKeydown: event => handleSlashCommandKey(event) || handleMentionKey(event),
        onInputActivityChange: markComposerInputActivity,
        onInput: (_event, meta = {}) => {
          state.recoveredSubmission = null;
          const recalled = meta?.source === "history";
          if (!recalled) history.reset(historyScope());
          reconcileAttachmentsFromInput({ notify: true });
          if (recalled) {
            // A recalled input is not being typed: its "/" or "@" opens no
            // menu, so the next Arrow key goes on through the history.
            closeSlashCommandMenu();
            closeMentionMenu();
          } else {
            refreshSlashCommandMenu();
            refreshMentionMenu();
          }
          updateControls();
          saveActiveDraft();
          options.onInput?.(controller.payload(), controller);
        },
      });
      bindComposerAttachmentActions(els.composer, {
        onRetry: id => uploadComposerAttachment(id).catch(reportAttachmentError),
        onRemove: removeComposerAttachment,
      });
      controller.refreshRefs();
      bindWorkspaceQueue(els.composer, {
        onInject: id => options.onQueueInject?.(id, controller),
        onDelete: id => options.onQueueDelete?.(id, controller),
        onClear: () => options.onQueueClear?.(controller),
        onMergeAll: () => options.onQueueMergeAll?.(controller),
        onMailbox: () => options.onQueueMailbox?.(controller),
      });
      if (els.mentions && els.mentions.dataset.workspaceMentionsBound !== "1") {
        els.mentions.dataset.workspaceMentionsBound = "1";
        // Choosing keeps the caret in the input.
        els.mentions.addEventListener("mousedown", event => {
          if (event.target.closest("[data-portal-mention-index]")) event.preventDefault();
        });
        els.mentions.addEventListener("click", event => {
          const button = event.target.closest("[data-portal-mention-index]");
          if (button) chooseMention(Number(button.dataset.portalMentionIndex));
        });
      }
      if (els.input && typeof options.mentionCandidates === "function" && els.input.dataset.workspaceMentionBlurBound !== "1") {
        els.input.dataset.workspaceMentionBlurBound = "1";
        // Copying a labelled mention must preserve its target when pasted elsewhere.
        const copyMentions = event => {
          const selection = window.getSelection();
          if (!event.clipboardData || !selection?.rangeCount || selection.isCollapsed) return;
          const range = selection.getRangeAt(0);
          if (!els.input.contains(range.commonAncestorContainer)) return;
          const fragment = document.createElement("div");
          fragment.append(range.cloneContents());
          if (!fragment.querySelector("[data-mention-value]")) return;
          event.preventDefault();
          event.clipboardData.setData("text/plain", serializeComposerInput(fragment, state.attachments, { mode: "draft" }));
          if (event.type === "cut") {
            range.deleteContents();
            els.input.dispatchEvent(new Event("input", { bubbles: true }));
          }
        };
        els.input.addEventListener("copy", copyMentions);
        els.input.addEventListener("cut", copyMentions);
        els.input.addEventListener("blur", () => closeMentionMenu());
        els.input.addEventListener("compositionend", () => {
          refreshMentionMenu();
          scheduleMentionRender();
        });
      }
      if (els.slashCommands && els.slashCommands.dataset.workspaceSlashCommandsBound !== "1") {
        els.slashCommands.dataset.workspaceSlashCommandsBound = "1";
        els.slashCommands.addEventListener("mousedown", event => {
          if (event.target.closest("[data-portal-slash-command]")) event.preventDefault();
        });
        els.slashCommands.addEventListener("click", event => {
          const button = event.target.closest("[data-portal-slash-command]");
          if (!button) return;
          chooseSlashCommand(button.dataset.portalSlashCommand || "");
        });
      }
      const runtimeSettingsButton = els.composer.querySelector("[data-workspace-runtime-settings-open]");
      if (runtimeSettingsButton && runtimeSettingsButton.dataset.workspaceRuntimeSettingsBound !== "1") {
        runtimeSettingsButton.dataset.workspaceRuntimeSettingsBound = "1";
        runtimeSettingsButton.addEventListener("click", openWorkspaceRuntimeSettings);
        const view = residentRuntimeController()?.state();
        if (runtimeTargetPending) runtimeSettingsButton.disabled = true;
        else if (view) runtimeSettingsButton.disabled = view.loading || view.busy || sessionRestartActive(view.restart) || (!view.canEdit && !view.canRestart);
      }
      if (els.auto && els.auto.dataset.workspaceAutoBound !== "1") {
        els.auto.dataset.workspaceAutoBound = "1";
        els.auto.addEventListener("click", () => options.onAuto?.(controller));
      }
    };
    bindComposerSplitter();
    applyComposerInputLayout();
    conversationSearch = createConversationSearch({
      root, scope: historyScope, messages: () => state.messages,
      rendering: () => ({ ...options, ...state.messageRenderOptions }),
      search: options.onSearchMessages,
      canLoadOlder: () => !!options.onLoadOlder && !!state.messageRenderOptions?.history?.can_load_older,
      loadOlder: () => options.onLoadOlder?.(controller),
      reveal: key => controller.revealMessage(key),
    });
    if (els.refresh) els.refresh.addEventListener("click", event => {
      event.preventDefault();
      if (sessionRestartAvailable()) {
        openWorkspaceSessionRestart().catch(error => (
          options.onError
            ? options.onError(error)
            : window.PortalUI.toast(error.message || String(error), "error")
        ));
        return;
      }
      options.onRefresh?.(controller);
    });
    // The Fork card shows its opening compaction on request.
    els.messages?.addEventListener("click", event => {
      if (!event.target.closest("[data-chat-inheritance-summary]")) return;
      event.preventDefault();
      toggleInheritedSummary().catch(error => options.onError?.(error));
    });
    // A quote block goes to the quoted message; the quote bar's cancel drops the quote.
    els.messages?.addEventListener("click", event => {
      const quote = event.target.closest("[data-message-quote]");
      if (!quote) return;
      event.preventDefault();
      controller.revealMessage(quote.dataset.messageQuote).catch(error => options.onError?.(error));
    });
    root.addEventListener("click", event => {
      if (!event.target.closest("[data-composer-quote-cancel]")) return;
      event.preventDefault();
      controller.clearQuote();
      els.input?.focus?.();
    });
    els.messages?.addEventListener("click", event => {
      const button = event.target.closest("[data-workspace-load-older]");
      if (!button || !options.onLoadOlder || button.disabled) return;
      button.disabled = true;
      button.textContent = window.PortalI18n.t("正在加载…");
      Promise.resolve(options.onLoadOlder(controller)).catch(error => {
        if (options.onError) options.onError(error);
        else window.PortalUI?.toast?.(error?.message || String(error), "error");
      }).finally(() => {
        if (!button.isConnected) return;
        button.disabled = false;
        button.textContent = window.PortalI18n.t("加载更早消息");
      });
    });
    updateControls();
    root.__portalChatWorkspaceController = controller;
    if (options.contract && typeof options.contract === "object") {
      controller.applyContract(options.contract, options.renderOptions || {});
    }
    return controller;
  }

  function normalizeResidentTarget(value = {}) {
    const target = value && typeof value === "object" ? value : {};
    const residentId = String(target.resident_id || "").trim();
    return Object.freeze({
      resident_id: residentId,
      lineage_id: String(target.lineage_id || "").trim(),
      lineage_resident_id: String(target.lineage_resident_id || "").trim(),
      title: String(target.title || target.name || "").trim(),
      backend: String(target.backend || "").trim(),
    });
  }

  function residentSessionScope(value = {}) {
    const target = normalizeResidentTarget(value);
    return target.resident_id ? `resident-session:${target.resident_id}` : "resident-session:none";
  }

  function placeCaretAtEnd(input) {
    if (!input) return;
    input.focus?.();
    if ("value" in input) {
      const end = input.value.length;
      try {
        input.selectionStart = end;
        input.selectionEnd = end;
      } catch {}
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  // A time inside a message reads like the message's own: local and compact.
  function footerTime(value) {
    return window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || "");
  }

  // The record of a message a Family sent another Resident in its name: in the
  // facade's Conversation it stays one line and opens on demand.
  function outwardToResidents(item) {
    const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
    return messageRole(item) !== "human" && recipients.length > 0 && recipients.every(recipient => recipient.kind === "resident");
  }

  class ResidentChatPanel {
    constructor(options = {}) {
      const hostOptions = Object.fromEntries([
        "root", "id", "panelId", "target", "autoLoad", "title", "emptyText", "placeholder",
        "draftOwner", "historyLimit", "residentFeatures", "pendingHistoryScope",
        "resolveResident", "resolveBeforeSend", "initialContext", "submissionContext", "onDetail", "onAcknowledgement", "emit",
        "onSent", "onRollback", "onError", "onRuntimeSettingsSaved", "onForkCreated", "conversationView", "onWatchboardAction", "onInteractionChange",
      ].filter(key => Object.prototype.hasOwnProperty.call(options, key)).map(key => [key, options[key]]));
      this.options = hostOptions;
      this.initialContext = null;
      this.selectionController = null;
      this.selectionRoot = null;
      this.actionInFlight = false;
      this.preparingResident = false;
      this.targetRevision = 0;
      this.detail = null;
      this.residentProjection = null;
      this.protocol = null;
      this.pendingProtocol = null;
      this.pendingSnapshot = null;
      this.unsubscribeProjection = null;
      this.pendingTurnSubmissions = new Map();
      this.withdrawnEdits = new Set();
      this.stopRequest = null;
      this.conversationResetInFlight = false;
      this.conversationResetSuppressSnapshots = false;
      this.conversationResetScope = "";
      this.surfaceRevision = 0;
      this.surfaceTarget = null;
      this.workspace = new ResidentChatWorkspace({
        ...hostOptions,
        onFamilyStatus: button => this.openFamilyStatus(button),
        onMenu: () => {
          const residentId = this.target().resident_id;
          if (!residentId) return;
          return window.PortalNav.openResidentMenu({
            residentId,
            onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
          });
        },
        target: normalizeResidentTarget(options.target || options),
        onSend: (payload, _workspace, submitOptions) => this.send(payload, submitOptions).catch(error => this.reportError(error)),
        onStop: () => this.stop().catch(error => this.reportError(error)),
        onRefresh: () => this.reload().catch(error => this.reportError(error)),
        onLoadOlder: () => this.loadOlder(),
        onLoadInputHistory: (scope, pageToken) => this.loadInputHistory(scope, pageToken),
        onLoadInheritedContext: () => window.PortalResidentService.inheritedContext(this.target().resident_id),
        onQueueInject: id => this.queueAction("injectInput", id),
        onQueueDelete: id => this.queueAction("cancelInput", id, { withdraw: true }),
        onQueueClear: () => this.clearQueue(),
        onQueueMergeAll: () => this.mergeQueue(),
        onQueueMailbox: () => {
          const residentId = this.target().resident_id;
          if (!residentId || !window.PortalNav?.openResidentMenu) return undefined;
          return window.PortalNav.openResidentMenu({
            residentId,
            tab: "mailbox",
            onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
          });
        },
        onCompact: () => this.runAction(() => this.send({ text: "/compact", refs: [], attachments: [] }, { clear: false, focus: false, rememberHistory: false })),
        onResetContext: () => this.resetContext(),
        onForward: items => openResidentForwardDialog(items, {
          preferredLineageId: this.target().lineage_id,
          loadTargets: query => window.PortalResidentService.forwardTargets(query),
          loadChatrooms: () => window.PortalResidentService.forwardChatrooms(),
          submit: body => window.PortalResidentService.forward({ ...body, source_resident_id: this.target().resident_id }),
          onSuccess: () => this.workspace.clearForwardSelection(),
        }),
        attachmentUploadContext: controller => ({ resident_id: controller.target().resident_id }),
        previewUrlForPath: path => window.PortalFileLinks.imagePreviewUrl(path),
        renderMarkdown: text => window.MD?.render?.(text) || escapeHtml(text).replace(/\n/g, window.PortalI18n.markup("<br>")),
        renderMessageBody: (item, context) => this.conversationView()?.renderMessageBody?.(item, context)
          ?? residentMessageBody(item, context),
        renderMessageFooter: residentMessageFooter,
        renderAfterMessage: residentMessageAfter,
        formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
        enableLinkPreview: true,
        enableWithdrawnEdit: true,
        showMessageActions: item => !item.local_delivery && this.conversationView()?.messageActions !== false,
        renderMessageMenuItems: item => (messageQuotable(item) && this.target().resident_id
          ? window.PortalI18n.markup('<button type="button" data-portal-message-action="quote">引用回复</button>')
          : "") + (messageRole(item) === "human" && this.target().resident_id
          && this.detail?.backend_capabilities?.portal_fork === true && this.detail?.allows?.ForkResident === true
          ? window.PortalI18n.markup('<button type="button" data-portal-message-action="fork-current">在当前 Lineage 中创建分支</button><button type="button" data-portal-message-action="fork-other">在其他 Lineage 中创建分支…</button>')
          : "") + familyReassignMenuItems(item, this.familyStatusFamily),
        onMessageAction: ({ action, key, item, button }) => (action === "submission-retry"
          ? window.PortalResidentProtocol.retrySubmission(this.target().resident_id, key).catch(error => this.reportError(error))
          : action === "submission-dismiss"
          ? window.PortalResidentProtocol.dismissSubmission(this.target().resident_id, key)
          : action === "submission-check"
          ? window.PortalResidentProtocol.checkSubmissions(this.target().resident_id)
          : action === "family-reassign"
          ? this.reassignFamilyRequest(item, String(button?.dataset.familyMember || ""))
          : action === "family-request-correct"
          ? this.correctFamilyRoute(item)
          : action === "family-recovery-choose"
          ? this.chooseFamilyRecovery(item, String(button?.dataset.recoveryRequest || ""))
          : action === "family-recovery-view"
          ? this.workspace.revealMessage(item?.family_status?.original_message_id)
          : FAMILY_REQUEST_ACTIONS[action]
          ? this.familyRequestAction(action, item)
          : this.messageAction(action, key)),
        onMessagesRendered: (root, _controller, _workspace, incremental = false) => {
          this.bindMessageSelection(root);
          if (!incremental) this.questions?.render(this.snapshot() || {});
          this.readObserver?.schedule();
          this.conversationView()?.onMessagesRendered?.(root, this);
        },
        onExtensionOpen: () => this.renderExtensions(),
        onRuntimeSettingsSaved: (result, controller) => {
          window.PortalResidentProjection?.invalidate?.(
            result.resident_id || this.workspace?.target?.().resident_id,
          );
          options.onRuntimeSettingsSaved?.(result, controller, this);
        },
        onSubagentFinished: run => this.noticeSubagentFinished(run),
      }).mount();
      this.workspace.root?.addEventListener("click", event => this.onSubagentClick(event));
      this.questions = new ResidentQuestionCards(this);
      this.readObserver = new ResidentReadObserver(this);
      if (options.autoLoad !== false && this.workspace.target().resident_id) {
        // Initial load failures already have a local, non-editable Panel state.
        // Restoring Editors must not turn that same failure into global toasts.
        this.setResident(this.workspace.target(), { load: true }).catch(() => {});
      }
    }

    reportError(error) {
      if (error?.localSubmission) return;
      if (this.options.onError) this.options.onError(error, this);
      else window.PortalUI?.toast?.(error?.message || String(error), "error");
    }

    reportBackgroundSyncError(error) {
      if (this.rejectUnavailableTarget(error)) return;
      if (window.PortalResidentProtocol.isTransientSyncError(error)) return;
      this.reportError(error);
    }

    bindMessageSelection(root) {
      if (this.selectionRoot === root) return;
      this.selectionController?.destroy();
      this.selectionRoot = root;
      this.selectionController = createMessageSelectionController({
        root,
        onAdd: selection => {
          if (!this.workspace.addReference(selection)) throw new Error("无法添加对话选段");
          this.workspace.element("input")?.focus();
        },
        onError: error => this.reportError(error),
      });
    }

    async runAction(callback) {
      if (this.actionInFlight) return null;
      this.actionInFlight = true;
      this.syncActions();
      try {
        return await callback();
      } catch (error) {
        this.reportError(error);
        return null;
      } finally {
        this.actionInFlight = false;
        this.syncActions();
      }
    }

    syncActions() {
      this.workspace.root?.querySelectorAll(".familyRequestAction").forEach(button => {
        button.disabled = this.actionInFlight || button.dataset.actionUnavailable === "true";
      });
      const board = this.detail?.watchboard_capabilities;
      const boardButton = this.workspace.element("extensionMenu")?.querySelector("[data-watchboard-action]");
      if (boardButton) {
        boardButton.disabled = this.actionInFlight || !(board?.installed ? board.can_remove : board?.can_install);
      }
      const composer = this.detail?.chat_workspace?.composer || {};
      if (composer.queue) {
        const queue = normalizeWorkspaceQueue(composer.queue);
        const actionInFlight = this.actionInFlight || queue.actionInFlight;
        // The contract already applied the queue. Only a local action changing
        // its busy state needs another composer update during streaming.
        if (actionInFlight !== normalizeWorkspaceQueue(this.workspace.state.composer.queue).actionInFlight) {
          this.workspace.setComposer({
            ...this.workspace.state.composer,
            queue: { ...queue, action_in_flight: actionInFlight },
          });
        }
      }
      const workState = this.snapshot()?.state?.work_state;
      const active = !!this.target().resident_id;
      this.workspace.root?.toggleAttribute("data-subagent-stop", this.canStopSubagents());
      this.workspace.root?.toggleAttribute("data-subagent-steer", this.canSteerSubagents());
      this.workspace.setResidentActionVisibility(residentSessionActions({
        active,
        runtimeCapabilities: this.detail?.backend_capabilities?.runtime_capabilities,
        compactBusy: this.actionInFlight || composer.can_send === false,
        resetContext: active,
        resetContextDisabled: this.actionInFlight || ["running", "queued", "stopping"].includes(workState),
      }));
    }

    renderExtensions() {
      const menu = this.workspace.element("extensionMenu");
      if (!menu) return;
      if (this.detail?.binding_state === "unbound") {
        menu.innerHTML = window.PortalI18n.markup('<div class="paneNote" role="status">发送第一条消息后可配置会话扩展。</div>');
        return;
      }
      menu.replaceChildren();
      const board = this.detail?.watchboard_capabilities;
      if (this.options.residentFeatures?.watchboard && board && this.options.onWatchboardAction) {
        const action = board.installed ? "remove" : "add";
        const allowed = action === "remove" ? board.can_remove : board.can_install;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton";
        button.dataset.watchboardAction = action;
        button.textContent = board.installed ? window.PortalI18n.t("移除 Watchboard") : window.PortalI18n.t("安装 Watchboard");
        button.disabled = this.actionInFlight || !allowed;
        button.addEventListener("click", () => this.runAction(() =>
          this.options.onWatchboardAction(action, this.target().resident_id)));
        menu.append(button);
      }
      // Harness settings live on the Resident Menu's Harness page.
      const residentId = this.target().resident_id;
      if (!residentId || !window.PortalNav?.openResidentMenu) return;
      const harness = document.createElement("button");
      harness.type = "button";
      harness.className = "chatSessionSwitch chatSessionNav";
      harness.dataset.harnessMenuOpen = "";
      harness.innerHTML = window.PortalI18n.markup('<span class="chatSessionSwitchText"><strong>Harness</strong><small>Prompt 注入、sub agent、失败续跑与智能选择 Harness 在 Menu 的 Harness 页设置</small></span><span class="chatSessionNavArrow" aria-hidden="true">›</span>');
      harness.addEventListener("click", () => {
        this.workspace.closeExtensionMenu?.();
        return window.PortalNav.openResidentMenu({
          residentId,
          tab: "harness",
          onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
        });
      });
      menu.append(harness);
    }

    queueAction(command, messageId, { withdraw = false } = {}) {
      const target = this.target();
      return this.runAction(async () => {
        const result = await window.PortalResidentProtocol[command]({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
          message_id: messageId,
        });
        if (withdraw) noticeQueueWithdrawn(result);
        return result;
      });
    }

    mergeQueue() {
      const target = this.target();
      const revision = this.targetRevision;
      const isCurrent = () => revision === this.targetRevision
        && residentSessionScope(this.target()) === residentSessionScope(target);
      if (!target.resident_id) return null;
      return this.runAction(async () => {
        try {
          const view = await window.PortalResidentProtocol.loadMailbox(target.resident_id);
          if (!isCurrent()) return null;
          if (!view?.can_organize) throw new Error(window.PortalI18n.t("没有整理此 Mailbox 的权限"));
          // Exactly the Mailbox tab's Select all: pending requests and groups,
          // in queue order. Never merge only the Chat preview or expand groups here.
          const ids = (Array.isArray(view.pending) ? view.pending : [])
            .filter(item => item.kind !== "info").map(item => item.message_id);
          if (ids.length < 2) {
            window.PortalUI?.toast?.(window.PortalI18n.t("至少两项待处理请求或群体消息才能合并"));
            await this.protocol?.load({ force: true });
            return null;
          }
          const result = await window.PortalResidentProtocol.mergeMailboxItems({
            resident_id: target.resident_id,
            message_ids: ids,
          });
          if (!isCurrent()) return result;
          window.PortalUI?.toast?.(window.PortalI18n.t("已合并全部待处理请求和群体消息"), "success");
          await this.protocol?.load({ force: true });
          return result;
        } catch (error) {
          if (isCurrent()) throw error;
          return null;
        }
      });
    }

    clearQueue() {
      const target = this.target();
      const revision = this.targetRevision;
      const isCurrent = () => revision === this.targetRevision
        && residentSessionScope(this.target()) === residentSessionScope(target);
      return this.runAction(async () => {
        const confirmed = await window.PortalUI.confirm({
          title: window.PortalI18n.t("清除全部排队消息？"),
          message: window.PortalI18n.t("将清除当前 Resident 所有可撤回的排队消息。正在执行的消息不受影响，接单状态保持不变。"),
          confirmText: window.PortalI18n.t("全部清除"),
          danger: true,
        });
        if (!confirmed || !isCurrent()) return null;
        const result = await window.PortalResidentProtocol.clearMailbox({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
        });
        if (!isCurrent()) return result;
        noticeQueueCleared(result);
        return result;
      });
    }

    async resetContext() {
      const target = this.target();
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("清空 Resident 上下文？"),
        message: window.PortalI18n.t("这会清空此前对话和 Provider 上下文，并保留私有归档。"),
        confirmText: window.PortalI18n.t("清空上下文"),
        danger: true,
      });
      if (!confirmed || residentSessionScope(this.target()) !== residentSessionScope(target)) return null;
      return this.runAction(() => this.resetConversation(() => window.PortalResidentService.resetContext(target)));
    }

    // Cancellation resolves through the facade. Injection uses the exact member
    // copy in the relay projection; the facade itself has no active turn.
    familyRequestAction(action, item) {
      if (action === "family-request-inject") return this.injectFamilyRequest(item);
      const messageId = String(item?.family_status?.request_message_id || "");
      const target = this.target();
      if (!messageId || !target.resident_id) return null;
      return this.runAction(async () => {
        const result = await window.PortalResidentProtocol[FAMILY_REQUEST_ACTIONS[action]]({
          lineage_id: target.lineage_id,
          resident_id: target.resident_id,
          message_id: messageId,
        });
        if (action === "family-request-withdraw") noticeQueueWithdrawn(result);
        return result;
      });
    }

    injectFamilyRequest(item) {
      const status = item?.family_status || {};
      const residentId = String(status.resident_id || "");
      const messageId = String(status.message_id || "");
      const requestId = String(status.request_message_id || "");
      const revision = this.targetRevision;
      if (status.state !== "queued" || !residentId || !messageId || !requestId) return null;
      const isCurrent = () => revision === this.targetRevision && this.workspace.state.messages.some(message => {
        const current = message.family_status;
        return current?.request_message_id === requestId && current.state === "queued"
          && current.resident_id === residentId && current.message_id === messageId;
      });
      return this.runAction(async () => {
        // Read the actual member's current capability, not the facade's Provider.
        const result = await window.PortalResidentService.loadStates([residentId], { includeViewerState: false });
        if (!isCurrent()) return null;
        const member = result.states?.find(entry => entry.resident_id === residentId);
        if (!member) throw new Error(window.PortalI18n.t("暂时无法读取承接成员状态，请稍后重试"));
        if (typeof member.controls?.active_turn_input !== "boolean") {
          throw new Error(window.PortalI18n.t("暂时无法确认承接成员的注入能力，请稍后重试"));
        }
        if (member.controls?.active_turn_input !== true) {
          throw new Error(window.PortalI18n.t("承接成员不支持运行中接收消息；消息保留在队列中"));
        }
        // A state snapshot can lag completion, stopping or queue promotion.
        // The service decides current-turn delivery versus next-turn admission.
        const receipt = await window.PortalResidentProtocol.injectInput({ resident_id: residentId, message_id: messageId });
        if (revision !== this.targetRevision) return receipt;
        const delivered = receipt.injected === true || receipt.status === "delivered";
        const nextTurn = receipt.scheduled_next_turn === true || receipt.status === "next_turn";
        window.PortalUI?.toast?.(window.PortalI18n.t(delivered ? "已交给承接成员当前的工作"
          : nextTurn ? "消息已排到下一轮；暂停接单时会等待恢复" : "已提交注入请求，等待承接成员当前的工作接收"), "success");
        await this.protocol?.load({ force: true });
        return receipt;
      });
    }

    chooseFamilyRecovery(item, messageId) {
      const candidate = (item?.family_status?.candidates || []).find(entry => entry.message_id === messageId);
      if (!candidate) return false;
      this.workspace.setQuote({ message_id: candidate.message_id, author_id: candidate.author_id, text: candidate.text || candidate.message_id });
      if (!this.workspace.inputText().trim()) this.workspace.setInputText("continue");
      return true;
    }

    async reassignFamilyRequest(item, memberId) {
      const target = this.target();
      const member = (this.familyStatusFamily?.members || []).find(entry => entry?.resident_id === memberId);
      const messageId = String(item?.message_id || "");
      if (!member || !messageId || !target.resident_id) return null;
      const name = member.title || memberId;
      const reason = await window.PortalUI.prompt({
        title: window.PortalI18n.text`改派给 ${name}`,
        message: window.PortalI18n.t("这条请求会交给所选成员重新处理。原成员还没开始时会撤回原副本；正在处理时请先停止本轮。理由会记入改派纠正，供以后选人参考。"),
        label: window.PortalI18n.t("理由（可留空）"),
        placeholder: window.PortalI18n.t("例如：这是 steward 设计，应由 family 设计负责"),
        confirmLabel: "改派",
      });
      if (reason === null) return null;
      return this.submitFamilyReassign(messageId, member, reason);
    }

    // 纠正 Router on a queued request's progress: the user picks the member that
    // should take it and writes the correction, which the reassignment records for the Router.
    async correctFamilyRoute(item) {
      const status = item?.family_status || {};
      const messageId = String(status.request_message_id || "");
      const current = String(status.resident_id || "");
      if (!messageId || !this.target().resident_id) return null;
      const members = (this.familyStatusFamily?.members || []).filter(member => member?.resident_id && member.resident_id !== current);
      if (!members.length) {
        window.PortalUI?.toast?.(window.PortalI18n.t("这个 Family 没有其他成员可以改派"), "error");
        return null;
      }
      const answer = await window.PortalUI.prompt({
        title: window.PortalI18n.t("纠正 Router"),
        message: window.PortalI18n.text`这条请求现在交给了 ${String(status.title || current)}。选择应该处理它的成员并写下纠正内容：请求改派给所选成员，原成员的排队副本撤回；纠正内容记入 Family 的路由纠正并告诉 steward，Router 以后选人时参考。`,
        choiceLabel: window.PortalI18n.t("改派给"),
        choices: members.map(member => ({ value: member.resident_id, label: member.title || member.resident_id })),
        label: window.PortalI18n.t("纠正内容（可留空）"),
        placeholder: window.PortalI18n.t("例如：登录页样式问题归 family 设计负责"),
        multiline: true,
        confirmLabel: window.PortalI18n.t("改派"),
      });
      if (answer === null) return null;
      const member = members.find(entry => entry.resident_id === answer.choice);
      return member ? this.submitFamilyReassign(messageId, member, answer.value) : null;
    }

    submitFamilyReassign(messageId, member, reason) {
      const target = this.target();
      const name = member.title || member.resident_id;
      return this.runAction(async () => {
        const result = await window.PortalResidentService.reassignFamilyRequest({
          resident_id: target.resident_id, message_id: messageId, member_id: member.resident_id, reason,
        });
        window.PortalUI?.toast?.(`已改派给 ${name}`, "success");
        await this.reload();
        return result;
      });
    }

    // Subagent cards (decision_b1ab45c4b3ec): a running card opens its run,
    // stops it or starts a correction; a result card opens its run, quotes,
    // copies or saves its result.
    canStopSubagents() {
      const harness = this.detail?.backend_capabilities?.runtime_capabilities?.harness_capabilities || [];
      return harness.includes("subagent_stop") && this.snapshot()?.controls?.can_stop === true;
    }

    canSteerSubagents() {
      return this.snapshot()?.controls?.can_send === true;
    }

    onSubagentClick(event) {
      const target = event.target instanceof Element ? event.target : null;
      const button = target?.closest("[data-subagent-action]");
      const liveCard = target?.closest("[data-subagent-live]");
      if (!button && !(liveCard && !target.closest("a, button, summary, input, textarea"))) return;
      if (button?.disabled) return;
      const resultCard = target?.closest("[data-subagent-run]");
      const run = liveCard
        ? subagentLiveById.get(liveCard.dataset.subagentLive)
        : (this.workspace.state?.messages || []).find(item => String(item?.id || "") === resultCard?.dataset.subagentRun)
          ?.content_parts?.find(part => part?.type === "subagent_run");
      if (!run) return;
      event.preventDefault();
      event.stopPropagation();
      return this.subagentAction(button?.dataset.subagentAction || "detail", run, { live: !!liveCard });
    }

    subagentAction(action, run, { live = false } = {}) {
      if (action === "detail") return this.openSubagentRun(run, live);
      if (action === "stop") return this.stopSubagent(run);
      if (action === "steer") return this.steerSubagent(run);
      if (action === "quote") return this.quoteSubagentResult(run);
      if (action === "copy") return this.copySubagentResult(run);
      if (action === "save") return this.saveSubagentResult(run);
      return null;
    }

    openSubagentRun(run, live) {
      return openSubagentDetail({
        residentId: this.target().resident_id,
        run,
        live,
        actions: isLive => isLive
          ? [
            { action: "steer", label: window.PortalI18n.t("纠偏"), title: window.PortalI18n.t("在输入框写给父 Resident 的纠偏，发送后立即进入当前这一轮"), hidden: !this.canSteerSubagents() },
            { action: "stop", label: window.PortalI18n.t("停止这个子 agent"), danger: true, hidden: !this.canStopSubagents() },
          ]
          : [
            { action: "quote", label: window.PortalI18n.t("引用到输入框") },
            { action: "copy", label: window.PortalI18n.t("复制结果") },
            { action: "save", label: window.PortalI18n.t("保存为文件") },
          ],
        onAction: (name, context) => {
          if (name === "steer" || name === "quote") context.close();
          return this.subagentAction(name, context.run, { live: context.live });
        },
      });
    }

    subagentName(run) {
      return String(run?.description || "").trim() || subagentTypeLabel(run?.agent_type) || window.PortalI18n.t("子 agent");
    }

    async stopSubagent(run) {
      const target = this.target();
      if (!target.resident_id || !run?.tool_use_id || !run.execution_id) return null;
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("停止这个子 agent？"),
        message: window.PortalI18n.text`只停止子 agent「${this.subagentName(run)}」。父 Resident 的这一轮继续，它会得知这个子 agent 已停止。`,
        confirmLabel: window.PortalI18n.t("停止"),
        tone: "danger",
      });
      if (!confirmed || residentSessionScope(this.target()) !== residentSessionScope(target)) return null;
      return this.runAction(async () => {
        const result = await window.PortalInstructionClient.execute("CancelSubagent", {
          resident_id: target.resident_id, execution_id: run.execution_id, tool_use_id: run.tool_use_id,
        }, { idempotencyKey: `subagent-stop-${run.tool_use_id}-${Date.now().toString(36)}` });
        if (result?.status === "failed") {
          const reason = SUBAGENT_STOP_REASONS[result.error];
          throw new Error(reason ? window.PortalI18n.t(reason) : window.PortalI18n.text`没有停下：${result.error || "Provider 没有说明原因"}`);
        }
        window.PortalUI?.toast?.(window.PortalI18n.t("已请求停止；子 agent 停下后卡片显示已停止"), "success");
        return result;
      });
    }

    // Text the reader goes on writing: after the draft, the caret at its end.
    appendToComposer(text) {
      const draft = this.workspace.draftText();
      this.workspace.setDraftText(draft.trim() ? `${draft.replace(/\s+$/, "")}\n\n${text}` : text, this.workspace.attachments());
      const input = this.workspace.element("input");
      placeCaretAtEnd(input);
      input?.dispatchEvent(new Event("input", { bubbles: true }));
      this.workspace.updateControls();
    }

    steerSubagent(run) {
      if (!this.canSteerSubagents()) {
        window.PortalUI?.toast?.(window.PortalI18n.t("当前对话不能发送消息"), "warning");
        return null;
      }
      const prefix = window.PortalI18n.text`关于子 agent「${this.subagentName(run)}」：`;
      this.subagentSteer = { scope: residentSessionScope(this.target()), prefix };
      this.appendToComposer(prefix);
      return prefix;
    }

    // Send now: a correction about a running subagent leaves the queue for the
    // current turn when the Provider takes input mid-turn; otherwise it waits
    // its turn like any message.
    sendSubagentSteerNow(target, messageId) {
      const snapshot = this.protocol?.snapshot?.() || {};
      if (!messageId || snapshot.controls?.active_turn_input !== true || !snapshot.state?.active_message_id) return;
      window.PortalResidentProtocol.injectInput({
        resident_id: target.resident_id, lineage_id: target.lineage_id, message_id: messageId,
      }).catch(() => window.PortalUI?.toast?.(window.PortalI18n.t("纠偏没能进入当前这一轮，已排队在这一轮之后处理"), "warning"));
    }

    quoteSubagentResult(run) {
      const result = String(run?.result || "").trim();
      const excerpt = result.length > 600 ? `${result.slice(0, 600).trimEnd()}…` : result;
      const lines = [window.PortalI18n.text`子 agent「${this.subagentName(run)}」的结果：`, ...(excerpt ? excerpt.split("\n") : [window.PortalI18n.t("子 agent 没有返回文字结果")])];
      this.appendToComposer(`${lines.map(line => `> ${line}`.trimEnd()).join("\n")}\n\n`);
      return true;
    }

    async copySubagentResult(run) {
      const text = await subagentFullResult(this.target().resident_id, run);
      return window.PortalUI.copyText(text, window.PortalI18n.t("子 agent 结果"));
    }

    saveSubagentResult(run) {
      const residentId = this.target().resident_id;
      if (!residentId) return null;
      return this.runAction(async () => {
        const workspace = await window.PortalResidentService.workspaces(residentId);
        const workspaceId = String(workspace?.workspace_id || "");
        if (!workspaceId) throw new Error(window.PortalI18n.t("找不到这个 Resident 自己的 Workspace"));
        const moment = new Date(Date.parse(String(run.finished_at || "")) || Date.now());
        const stamp = moment.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
        const path = `subagent-results/${stamp}-${subagentSlug(run.description || run.agent_type)}.md`;
        const result = await subagentFullResult(residentId, run);
        await window.PortalResidentService.saveFile({ address: `${workspaceId}/file/${path}`, content: subagentResultMarkdown(run, result) });
        window.PortalUI?.toast?.(window.PortalI18n.text`已保存到 Workspace：${path}`, "success");
        return path;
      });
    }

    // A run this Chat showed running ended: a reader looking elsewhere hears it.
    noticeSubagentFinished(run) {
      const status = run?.status === "failed" ? window.PortalI18n.t("失败") : run?.status === "stopped" || run?.status === "cancelled" ? window.PortalI18n.t("已停止") : window.PortalI18n.t("已完成");
      if (document.hidden) {
        badgeTitleForSubagent();
        return;
      }
      const root = this.workspace.root;
      const rect = root?.getBoundingClientRect?.();
      const visible = !!rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
      if (!visible) window.PortalUI?.toast?.(window.PortalI18n.text`子 agent「${this.subagentName(run)}」${status}`, run?.status === "failed" ? "warning" : "success");
    }

    // Quote replies (decision_f560803947c6): the quote bar shows the quoted message until it is sent.
    quoteMessage(key) {
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      if (!item || !messageQuotable(item)) return false;
      const author = String(item.origin?.sender?.id || item.author_id
        || (messageRole(item) === "agent" ? this.target().resident_id : "") || "");
      return this.workspace.setQuote({ message_id: String(item.message_id), author_id: author, text: messageQuoteExcerpt(item) });
    }

    async reeditWithdrawnMessage(key) {
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      const target = this.target();
      const revision = this.targetRevision;
      if (!item?.withdrawn || !messageAuthoredByViewer(item) || !target.resident_id
        || this.withdrawnEdits.has(key)) return;
      this.withdrawnEdits.add(key);
      try {
        // The conversation keeps only the annotation; fetch the original only on demand.
        const { message } = await window.PortalInstructionClient.execute("GetMessage", {
          resident_id: target.resident_id, message_id: item.message_id,
        });
        if (revision !== this.targetRevision || residentSessionScope(target) !== residentSessionScope(this.target())) return;
        if (message?.message_id !== item.message_id || !messageAuthoredByViewer(message)) return;
        const restored = messageHistoryDraft(residentProtocolMessageToWorkspace(message), {
          previewUrlForPath: path => window.PortalFileLinks.imagePreviewUrl(path),
        });
        // Keep anything typed while the read was in flight, including its attachments and quote.
        const draft = this.workspace.draftText();
        const attachments = [...new Map([...this.workspace.attachments(), ...restored.attachments]
          .map(attachment => [attachment.id, attachment])).values()];
        this.workspace.setDraftText(draft ? `${draft}\n\n${restored.text}` : restored.text, attachments);
        if (message.quote && !this.workspace.quote()) this.workspace.setQuote(message.quote);
        const input = this.workspace.element("input");
        placeCaretAtEnd(input);
        input?.dispatchEvent(new Event("input", { bubbles: true }));
      } finally {
        this.withdrawnEdits.delete(key);
      }
    }

    messageAction(action, key) {
      if (action === "withdrawn-edit") return this.reeditWithdrawnMessage(key);
      if (action === "quote") return this.quoteMessage(key);
      if (!["fork-current", "fork-other"].includes(action)) return;
      const source = { ...this.target() };
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      if (!item || messageRole(item) !== "human") return;
      const fork = async target => {
        const lineageId = target.lineage_id;
        if (!lineageId) throw new Error("Resident 缺少所属 Lineage");
        const societyService = window.PortalSocietyService.createClient();
        const graph = await societyService.loadLineage(lineageId);
        const nodes = graph?.graph?.nodes || graph?.nodes || [];
        const parent = nodes.find(node => node.id === target.resident_id);
        if (!parent) throw new Error("目标 Lineage 中找不到父 Resident");
        const result = await societyService.branchNode({
          lineage_id: lineageId,
          source_resident_id: parent.id,
          context_source_resident_id: source.resident_id,
          fork_mode: "message",
          message: { message_id: item.id },
        });
        const residentId = String(result?.resident_id || "");
        if (this.options.onForkCreated) {
          await this.options.onForkCreated({ lineageId, residentId, lineage: result?.lineage, result }, this);
        } else if (!residentId || result?.lineage?.lineage_id !== lineageId) {
          window.PortalUI?.toast?.("分支已创建，但返回结果不完整；请刷新 Lineage 后定位新 Resident。", "warning");
        } else {
          window.PortalUI?.toast?.("分支已创建。", "success");
        }
        return result;
      };
      if (action === "fork-current") return this.runAction(() => fork(source));
      return openResidentForwardDialog([{ content: item.content || "对话分支" }], {
        title: window.PortalI18n.t("在其他 Lineage 中创建分支"),
        submitLabel: "创建分支",
        summary: "选择新分支所属的 Lineage 和父 Resident。",
        selectTargetOnly: true,
        successMessage: "已创建分支。",
        preferredLineageId: source.lineage_id,
        loadTargets: query => window.PortalResidentService.forwardTargets(query),
        submit: body => fork({ lineage_id: body.target_lineage_id, resident_id: body.target_resident_id }),
      });
    }

    rejectUnavailableTarget(error) {
      const status = Number(error?.status || 0);
      const deleted = error?.code === "resident.deleted";
      if (!deleted && status !== 404 && status !== 403) return false;
      if (deleted || status === 404) this.options.emit?.("binding-unavailable");
      this.questions.reset();
      const target = this.workspace.target();
      window.PortalConversationStore?.discardResident?.(target.resident_id);
      const currentDetail = this.detail?.resident?.resident_id === target.resident_id ? this.detail : null;
      const title = currentDetail?.chat_workspace?.title || target.title || target.resident_id || window.PortalI18n.t("Resident 不可用");
      // Detach this target before rendering its error contract. Late protocol
      // events and metadata responses must not restore the previous capabilities.
      this.targetRevision += 1;
      this.unavailableTargetRevision = this.targetRevision;
      this.unsubscribeProjection?.();
      this.unsubscribeProjection = null;
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      this.protocol?.destroy?.();
      this.protocol = null;
      this.pendingSnapshot = null;
      this.detail = null;
      this.residentProjection = null;
      this.showError(
        deleted || status === 404 ? window.PortalI18n.t("该 Resident 已删除或不存在。") : window.PortalI18n.t("没有查看这个 Resident 的权限。"),
        {
          title,
          subtitle: window.PortalI18n.t("Resident 不可用"),
          placeholder: window.PortalI18n.t("Resident 不可用"),
          meta: window.PortalI18n.t(deleted ? "请打开其他 Resident。" : "请打开其他 Resident，或刷新后重试。"),
          historyScope: residentSessionScope(target),
        },
      );
      return true;
    }

    instanceId() {
      return String(this.options.panelId || this.workspace?.id || "");
    }

    isReadyFor(target = {}) {
      return Boolean(
        this.protocol
        && residentSessionScope(this.workspace.target()) === residentSessionScope(target),
      );
    }

    async requestDetail(target, options = {}) {
      if (options.detail && typeof options.detail === "object") {
        if (this.contractFromDetail(options.detail, target)) return options.detail;
        return window.PortalResidentProjection.load(target.resident_id, {
          force: options.force === true,
        });
      }
      return window.PortalResidentProjection.load(target.resident_id, {
        force: options.force === true,
      });
    }

    contractFromDetail(detail, target = this.workspace.target()) {
      const contract = detail?.chat_workspace;
      if (!contract || typeof contract !== "object") return null;
      const contractTarget = contract.target && typeof contract.target === "object"
        ? contract.target
        : {};
      if (contractTarget.kind !== "resident") return null;
      if (
        String(contractTarget.kind || "") === "resident"
        && String(contractTarget.id || "") !== String(target?.resident_id || "")
      ) {
        throw new Error("Resident projection returned a mismatched Chat target");
      }
      if (Array.isArray(contract.messages)) return contract;
      return { ...contract, messages: [] };
    }

    conversationView() {
      if (!this.workspace) return null;
      return this.options.conversationView?.(this.target(), this) || null;
    }

    projectContract(contract, detail, snapshot = null, update = null) {
      // A capability may present its actor-scoped requests in the same Resident
      // Editor. Identity, permissions, session controls and menus remain native.
      const projected = this.conversationView()?.project?.(contract, detail, snapshot, update) || contract;
      const composer = projected.composer || {};
      // Reconcile this Editor's accepted input before rendering. Applying an
      // idle server composer first would remove and recreate an actionable Stop,
      // losing its focus/pointer target on every unrelated stream update.
      return { ...projected, composer: { ...composer,
        can_stop: this.pendingTurnStopCapability(composer, snapshot),
      } };
    }

    async setResident(residentId, options = {}) {
      const target = normalizeResidentTarget(
        residentId && typeof residentId === "object"
          ? residentId
          : { ...options, resident_id: residentId },
      );
      const scope = target.resident_id && options.load !== false ? residentSessionScope(target) : "";
      const installed = Boolean(this.protocol) && residentSessionScope(this.workspace.target()) === scope;
      // A repeated request for a Resident whose first load is still running joins
      // that load. Restarting it on every page refresh never finishes when one load
      // takes longer than the refresh interval, and the Chat stays "loading".
      if (scope && !installed && options.force !== true && this.initialLoad?.scope === scope) {
        return this.initialLoad.promise;
      }
      const promise = this.loadResidentTarget(residentId, options);
      if (scope && !installed) {
        const entry = { scope, promise };
        this.initialLoad = entry;
        const settle = () => { if (this.initialLoad === entry) this.initialLoad = null; };
        promise.then(settle, settle);
      } else {
        this.initialLoad = null;
      }
      return promise;
    }

    async loadResidentTarget(residentId, options = {}) {
      const target = normalizeResidentTarget(
        residentId && typeof residentId === "object"
          ? residentId
          : { ...options, resident_id: residentId },
      );
      const current = this.workspace.target();
      const sameTarget = residentSessionScope(current) === residentSessionScope(target);
      this.workspace.setHistoryTarget(target);
      if (sameTarget && this.protocol && options.force !== true) {
        if (this.pendingProtocol) {
          this.targetRevision += 1;
          const pendingProtocol = this.pendingProtocol;
          this.pendingProtocol = null;
          pendingProtocol.destroy?.();
          this.workspace.clearLoading?.();
        }
        if (options.detail && typeof options.detail === "object") {
          this.applyDetail(options.detail);
        }
        return target;
      }
      const revision = ++this.targetRevision;
      const releaseInstalledTarget = () => {
        if (!sameTarget) this.updateFamilyStatus(null);
        this.readObserver.observation = null;
        this.selectionController?.clear();
        this.workspace.extensionPopover?.close?.();
        if (!sameTarget) this.initialContext = null;
        this.questions.reset();
        this.unsubscribeProjection?.();
        this.unsubscribeProjection = null;
      };
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      if (!target.resident_id || options.load === false) {
        releaseInstalledTarget();
        this.protocol?.destroy?.();
        this.protocol = null;
        this.pendingSnapshot = null;
        this.detail = null;
        this.residentProjection = null;
        this.workspace.setTarget(target);
        this.showEmpty();
        return target;
      }
      const previousProtocol = this.protocol;
      if (!sameTarget || !previousProtocol) this.workspace.setLoading("正在加载 Resident 和对话状态；加载完成后恢复本地草稿…");
      if (!sameTarget && previousProtocol) {
        // The old conversation is no longer the navigation target. Keeping its
        // subscription until the new snapshot finishes lets a stream reset
        // start another GetConversation for a Resident that is already gone.
        previousProtocol.destroy?.();
        this.protocol = null;
        this.unsubscribeProjection?.();
        this.unsubscribeProjection = null;
      }
      if (!window.PortalResidentProtocol?.acquire) {
        const error = new Error("PortalResidentProtocol is unavailable");
        releaseInstalledTarget();
        this.workspace.setTarget(target);
        this.protocol = null;
        this.detail = null;
        this.residentProjection = null;
        this.showError(error, {
          title: target.title || this.options.title,
          historyScope: residentSessionScope(target),
        });
        previousProtocol?.destroy?.();
        throw error;
      }
      let stagedSnapshot = null;
      let stagedProtocol = null;
      let loadTimer = 0;
      try {
        stagedProtocol = window.PortalResidentProtocol.acquire(
          target.resident_id,
          update => {
            const installed = this.protocol === stagedProtocol;
            const pending = revision === this.targetRevision && this.pendingProtocol === stagedProtocol;
            if (!installed && !pending) return;
            if (installed && this.pendingProtocol && this.pendingProtocol !== stagedProtocol) return;
            if (update.type === "error") {
              // While staged, load() owns the error through its rejected promise.
              // Only an installed protocol owns asynchronous error notifications.
              if (installed) this.reportBackgroundSyncError(update.error);
            } else if (installed && update.snapshot) {
              this.applySnapshot(update.snapshot, update);
            } else if (pending && update.snapshot) {
              stagedSnapshot = update.snapshot;
            }
          },
          { load: false, consumer: "chat", observationRoot: this.workspace.root },
        );
        this.pendingProtocol = stagedProtocol;
        const detailRequest = this.requestDetail(target, options);
        const [detail, loadedSnapshot] = await Promise.race([
          Promise.all([
            detailRequest,
            stagedProtocol.load({
              force: options.force === true,
              historyLimit: Number(
                this.options.historyLimit || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE,
              ),
            }),
          ]),
          new Promise((_resolve, reject) => {
            loadTimer = window.setTimeout(() => {
              reject(new Error("Resident 和对话加载超时，请重试。"));
            }, RESIDENT_INITIAL_LOAD_TIMEOUT_MS);
          }),
        ]);
        if (revision !== this.targetRevision) {
          stagedProtocol.destroy?.();
          return target;
        }
        releaseInstalledTarget();
        this.workspace.setTarget({
          ...target,
          lineage_id: String(detail?.resident?.lineage_id || detail?.chat_workspace?.target?.lineage_id || loadedSnapshot?.resident?.lineage_id || target.lineage_id || ""),
        });
        this.protocol = stagedProtocol;
        this.pendingProtocol = null;
        this.pendingSnapshot = stagedProtocol.snapshot?.() || stagedSnapshot || loadedSnapshot || null;
        this.applyDetail(detail && typeof detail === "object" ? detail : {});
        this.options.emit?.("binding-ready");
        this.unsubscribeProjection = window.PortalResidentProjection?.subscribe?.(
          target.resident_id,
          update => {
            if (this.protocol !== stagedProtocol) return;
            if (this.pendingProtocol && this.pendingProtocol !== stagedProtocol) return;
            if (update.error) this.reportBackgroundSyncError(update.error);
            else this.applyDetail(update.detail);
          },
        );
        previousProtocol?.destroy?.();
        return target;
      } catch (error) {
        if (this.pendingProtocol === stagedProtocol) this.pendingProtocol = null;
        stagedProtocol?.destroy?.();
        if (revision === this.targetRevision) {
          releaseInstalledTarget();
          this.workspace.setTarget(target);
          this.protocol = null;
          this.pendingSnapshot = null;
          this.detail = null;
          this.residentProjection = null;
          if (!this.rejectUnavailableTarget(error)) {
            this.showError(error, {
              title: target.title || this.options.title,
              historyScope: residentSessionScope(target),
            });
          }
          previousProtocol?.destroy?.();
        }
        throw error;
      } finally {
        window.clearTimeout(loadTimer);
      }
    }

    openResource(resource = {}) {
      return this.setResident(resource, { ...resource, load: true });
    }

    setContext(context = {}) {
      const candidate = context.conversationTarget
        || context.resident
        || (context.residentId ? { resident_id: context.residentId } : null);
      if (!candidate) return this.workspace.target();
      const target = normalizeResidentTarget(candidate);
      const current = this.workspace.target();
      const targetPanelId = String(candidate.panelId || candidate.panel_id || "").trim();
      if (current.resident_id && targetPanelId !== this.instanceId()) return current;
      if (!target.resident_id || residentSessionScope(target) === this.workspace.sessionScope()) {
        return current;
      }
      return this.setResident(target, { load: true });
    }

    async openSurface(surfaceTarget = {}, options = {}) {
      if (typeof this.options.resolveResident !== "function") {
        throw new Error("ResidentChatPanel surface binding requires resolveResident");
      }
      const revision = ++this.surfaceRevision;
      const requested = Object.freeze({ ...(surfaceTarget || {}) });
      if (
        options.force !== true
        && this.surfaceTarget
        && JSON.stringify(this.surfaceTarget) === JSON.stringify(requested)
        && this.workspace.target().resident_id
      ) {
        return this.detail;
      }
      this.surfaceTarget = null;
      void this.setResident("", { load: false });
      const binding = { revision, targetRevision: this.targetRevision };
      this.pendingSurfaceBinding = binding;
      this.workspace.setLoading("正在解析当前页面的 Resident 和对话…");
      const isCurrent = () => revision === this.surfaceRevision && binding.targetRevision === this.targetRevision;
      try {
        const detail = await this.options.resolveResident(requested, this, options);
        if (!isCurrent()) return null;
        const residentId = String(
          detail?.resident_id
          || detail?.resident?.resident_id
          || (detail?.chat_workspace?.target?.kind === "resident" ? detail.chat_workspace.target.id : "")
          || "",
        ).trim();
        if (!residentId) {
          if (detail?.binding_state !== "unbound" || !this.options.resolveBeforeSend) {
            throw new Error("Resident Chat surface did not resolve resident_id");
          }
          this.surfaceTarget = requested;
          this.detail = detail;
          this.initialContext = this.options.initialContext?.(requested, detail) || null;
          this.showState(detail.chat_workspace);
          this.workspace.clearLoading();
          this.options.onDetail?.(detail, this);
          return detail;
        }
        this.surfaceTarget = requested;
        const loading = this.setResident({
          resident_id: residentId,
          lineage_id: String(detail?.lineage_id || detail?.resident?.lineage_id || ""),
        }, {
          ...options,
          load: true,
        });
        binding.targetRevision = this.targetRevision;
        await loading;
        if (!isCurrent()) return null;
        this.initialContext = this.options.initialContext?.(requested, detail) || null;
        return this.detail;
      } catch (error) {
        if (!isCurrent()) return null;
        this.surfaceTarget = null;
        this.workspace.clearLoading();
        this.showError(error);
        throw error;
      } finally {
        if (this.pendingSurfaceBinding === binding) this.pendingSurfaceBinding = null;
      }
    }

    closeSurface() {
      this.surfaceRevision += 1;
      this.surfaceTarget = null;
      return this.setResident("", { load: false });
    }

    updateFamilyStatus(detail) {
      const family = detail?.family_facade;
      const roster = value => JSON.stringify([value?.family_id, value?.title,
        (value?.nodes || value?.members || []).map(node => [node.resident_id, node.title, node.role])]);
      const changed = roster(this.familyStatusFamily) !== roster(family);
      const residentId = String(detail?.resident?.resident_id || detail?.resident_id || "");
      if (!family?.family_id || !residentId || this.familyStatusResidentId !== residentId) {
        this.familyStatusView?.destroy(); this.familyStatusView = null;
        this.familyStatusButton = null;
      }
      this.familyStatusFamily = family?.family_id ? family : null;
      this.familyStatusResidentId = this.familyStatusFamily ? residentId : "";
      if (changed) this.familyStatusView?.refresh();
    }

    openFamilyStatus(button) {
      if (!this.familyStatusResidentId) return;
      if (this.familyStatusButton !== button) { this.familyStatusView?.destroy(); this.familyStatusView = null; }
      this.familyStatusButton = button;
      this.familyStatusView ||= createFamilyStatusView(this.familyStatusResidentId, button,
        summary => { button.title = window.PortalI18n.text`查看 Family 成员状态 · ${summary}`; }, () => this.familyStatusFamily);
      if (this.familyStatusView.isOpen()) this.familyStatusView.close();
      else this.familyStatusView.open();
    }

    applySnapshot(snapshot, update = null) {
      if (
        this.conversationResetSuppressSnapshots
        && this.conversationResetScope === this.workspace.sessionScope()
      ) {
        return this.detail;
      }
      if (!this.residentProjection) {
        this.pendingSnapshot = snapshot;
        return null;
      }
      this.pendingSnapshot = null;
      if (this.appendSnapshotMessage(snapshot, update)) return this.detail;
      const detail = this.residentProjection;
      const contract = this.contractFromDetail(detail);
      if (!contract) throw new Error("Resident projection is missing Chat Workspace contract");
      const projected = projectResidentConversation({ ...detail, chat_workspace: contract }, snapshot);
      const chatContract = this.projectContract(projected.chat_workspace, projected, snapshot, update);
      this.detail = {
        ...projected,
        chat_workspace: chatContract,
        snapshot,
        state: projected.resident_state,
      };
      this.updateFamilyStatus(this.detail);
      this.workspace.applyContract(chatContract);
      this.questions.render(snapshot);
      this.confirmSubmittedDraft(snapshot);
      this.refreshPendingTurnStopCapability();
      this.readObserver.update(snapshot);
      this.options.onDetail?.(this.detail, this, update);
      this.syncActions();
      return this.detail;
    }

    appendSnapshotMessage(snapshot, update) {
      if (update?.type !== "event" || update.event?.event_type !== "conversation.message.appended"
        || snapshot.local_submissions?.length || this.workspace.state.messages.some(item => item.local_delivery)
        || snapshot.family_facade || this.conversationView()?.project || !this.detail?.chat_workspace) return false;
      const messages = snapshot.messages;
      const prior = this.detail.messages;
      const incoming = update.event?.payload?.message;
      if (!Array.isArray(messages) || !Array.isArray(prior) || !incoming?.message_id
        || incoming.released_with || messages.length !== prior.length + 1
        || String(messages[messages.length - 1]?.message_id || "") !== String(incoming.message_id)
        || (prior.length && String(messages[messages.length - 2]?.message_id || "")
          !== String(prior[prior.length - 1]?.message_id || ""))) return false;
      const projected = residentProtocolMessageToWorkspace(incoming);
      if (!this.workspace.appendMessage(projected)) return false;
      this.detail = {
        ...this.detail,
        messages: this.workspace.state.messages,
        chat_workspace: this.workspace.contract(),
        snapshot,
      };
      const parts = Array.isArray(incoming.content_parts) ? incoming.content_parts : [];
      if (parts.some(part => part?.type === "async_questions" || nativeInteractionNames.has(part?.tool_name))) {
        this.questions.render(snapshot);
      }
      this.workspace.confirmSubmittedMessages([incoming.message_id]);
      this.refreshPendingTurnStopCapability();
      this.readObserver.update(snapshot);
      this.options.onDetail?.(this.detail, this, update);
      this.syncActions();
      return true;
    }

    confirmSubmittedDraft(snapshot, scope) {
      if (!snapshot) return;
      this.workspace.confirmSubmittedMessages([
        ...(snapshot.messages || []).map(message => message.message_id),
        ...(snapshot.input_states || []).map(input => input.message_id),
      ], scope);
    }

    applyDetail(detail, expectedScope = this.workspace.sessionScope()) {
      if (expectedScope !== this.workspace.sessionScope()) return null;
      this.residentProjection = detail;
      this.updateFamilyStatus(detail);
      this.detail = detail;
      const snapshot = this.protocol?.snapshot?.() || this.pendingSnapshot;
      if (snapshot) return this.applySnapshot(snapshot);
      const contract = this.contractFromDetail(detail);
      if (contract) {
        const chatContract = this.projectContract(contract, detail, null, null);
        this.detail = { ...detail, chat_workspace: chatContract };
        this.workspace.applyContract(chatContract);
        this.refreshPendingTurnStopCapability();
      }
      this.options.onDetail?.(this.detail, this, null);
      this.syncActions();
      return this.detail;
    }

    async reload(options = {}) {
      if (!this.workspace.target().resident_id) return null;
      return this.setResident(this.workspace.target(), {
        load: true,
        force: options.force !== false,
      });
    }

    // What this person typed in the conversation, newest first, a page at a
    // time for the input history: the server lists it.
    async loadInputHistory(scope, pageToken = "") {
      const residentId = this.workspace.target().resident_id;
      const revision = this.targetRevision;
      if (!residentId || scope !== this.workspace.inputHistoryScope()) return null;
      // Rendering the unavailable state can refresh input history as the old
      // messages disappear. Keep its identity without reading that binding again;
      // an explicit reload or target switch creates a new revision.
      if (revision === this.unavailableTargetRevision) return null;
      const isCurrent = () => revision === this.targetRevision
        && residentId === this.workspace.target().resident_id
        && scope === this.workspace.inputHistoryScope();
      let page;
      try {
        page = await window.PortalResidentService.inputHistory(residentId, { pageToken });
      } catch (error) {
        if (!isCurrent()) return null;
        if ((error?.code === "resident.deleted" || Number(error?.status) === 404)
          && this.rejectUnavailableTarget(error)) return null;
        throw error;
      }
      if (!isCurrent()) return null;
      return {
        messages: (Array.isArray(page?.messages) ? page.messages : []).map(residentProtocolMessageToWorkspace),
        next_page_token: String(page?.next_page_token || ""),
      };
    }

    async loadOlder() {
      if (!this.protocol) return null;
      return this.protocol.loadOlder({
        limit: Number(
          this.options.historyLimit
          || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE,
        ),
      });
    }

    async resetConversation(operation) {
      if (this.conversationResetInFlight) return null;
      if (typeof operation !== "function") {
        throw new Error("Resident Chat reset requires an operation");
      }
      this.questions.reset();
      const targetScope = this.workspace.sessionScope();
      const contract = this.contractFromDetail(this.detail || {});
      this.conversationResetInFlight = true;
      this.conversationResetSuppressSnapshots = true;
      this.conversationResetScope = targetScope;
      this.pendingSnapshot = null;
      if (contract) {
        const resetContract = {
          ...contract,
          messages: [],
          runtime: { live: false, details: [] },
          composer: {
            ...(contract.composer || {}),
            can_send: false,
            can_stop: false,
          },
        };
        this.detail = { ...(this.detail || {}), chat_workspace: resetContract };
        this.workspace.applyContract(resetContract);
        this.options.onDetail?.(this.detail, this, { type: "conversation.reset.pending" });
      }
      try {
        const result = await operation(this);
        window.PortalResidentProjection?.invalidate?.(
          this.workspace.target().resident_id,
        );
        if (targetScope === this.workspace.sessionScope()) {
          this.conversationResetSuppressSnapshots = false;
          await this.reload({ force: true });
        }
        return result;
      } catch (error) {
        if (targetScope === this.workspace.sessionScope()) {
          this.conversationResetSuppressSnapshots = false;
          try {
            await this.reload({ force: true });
          } catch (_reloadError) {
            // Preserve the original reset failure; normal protocol recovery remains active.
          }
        }
        throw error;
      } finally {
        this.conversationResetSuppressSnapshots = false;
        this.conversationResetInFlight = false;
        this.conversationResetScope = "";
      }
    }

    refreshView() {
      const projection = this.residentProjection;
      if (!projection) return null;
      const snapshot = this.protocol?.snapshot?.() || this.pendingSnapshot;
      const contract = this.contractFromDetail(projection);
      if (!contract) return this.detail;
      const detail = snapshot
        ? projectResidentConversation({ ...projection, chat_workspace: contract }, snapshot)
        : projection;
      const chatContract = this.projectContract(
        detail.chat_workspace || contract,
        detail,
        snapshot,
        { type: "view.refreshed" },
      );
      this.detail = { ...detail, chat_workspace: chatContract };
      this.workspace.applyContract(chatContract);
      this.readObserver.update(snapshot);
      return this.detail;
    }

    showState(contract = {}) {
      const next = contract && typeof contract === "object" ? contract : {};
      if (next.schema !== RESIDENT_CHAT_WORKSPACE_SCHEMA) {
        throw new Error(`Resident Chat Workspace requires schema ${RESIDENT_CHAT_WORKSPACE_SCHEMA}`);
      }
      const normalized = {
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: String(next.title || this.options.title || window.PortalI18n.t("Chat Workspace")),
        subtitle: String(next.subtitle || ""),
        header: next.header && typeof next.header === "object" ? next.header : {},
        messages: Array.isArray(next.messages) ? next.messages : [],
        runtime: next.runtime && typeof next.runtime === "object"
          ? next.runtime
          : { live: false, details: [] },
        rendering: next.rendering && typeof next.rendering === "object"
          ? next.rendering
          : {},
        composer: next.composer && typeof next.composer === "object"
          ? next.composer
          : { can_send: false, can_stop: false },
        actions: next.actions && typeof next.actions === "object" ? next.actions : {},
      };
      this.detail = { ...(this.detail || {}), chat_workspace: normalized };
      this.workspace.applyContract(normalized);
      return this.detail;
    }

    showEmpty(options = {}) {
      return this.showState({
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: options.title || this.options.title || window.PortalI18n.t("Chat Workspace"),
        subtitle: options.subtitle || "",
        rendering: {
          empty_text: options.emptyText || "暂无对话。",
          user_name: options.userName,
          assistant_name: options.assistantName,
        },
        composer: {
          placeholder: options.placeholder || window.PortalI18n.t("选择会话后开始对话"),
          meta: options.meta || "当前没有可用会话",
          can_send: false,
          can_edit: false,
          can_stop: false,
          can_attach: false,
          refs_enabled: false,
          can_auto: false,
          history_scope: options.historyScope || "",
        },
      });
    }

    showError(error, options = {}) {
      const message = String(error?.message || error || window.PortalI18n.t("Resident Chat 加载失败"));
      return this.showState({
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: options.title || this.options.title || window.PortalI18n.t("Chat Workspace"),
        subtitle: options.subtitle || window.PortalI18n.t("Resident Chat 加载失败"),
        messages: [{
          id: newMessageIdentity(),
          role: "portal",
          name: "Portal",
          content: message,
        }],
        rendering: {
          user_name: options.userName,
          assistant_name: options.assistantName,
        },
        composer: {
          placeholder: options.placeholder || window.PortalI18n.t("加载失败"),
          meta: options.meta || "等待服务恢复…",
          can_send: false,
          can_edit: false,
          can_stop: false,
          can_attach: false,
          refs_enabled: false,
          can_auto: false,
          history_scope: options.historyScope || "",
        },
      });
    }

    pendingTurnForTarget(target = this.workspace.target()) {
      return [...this.pendingTurnSubmissions.values()].find(pending => pending.scope === residentSessionScope(target)) || null;
    }

    pendingTurnStopCapability(composer, snapshot) {
      const scope = residentSessionScope(this.workspace.target());
      for (const [id, pending] of this.pendingTurnSubmissions) {
        if (!pending.acknowledged || pending.scope !== scope) continue;
        // DeliverMessage can commit before its input state exists. Keep this
        // Editor's exact-message cancellation handle until an authoritative
        // active control, visible queue item or terminal state takes over.
        // A receipt or an unrelated event cursor alone is not that handoff.
        const terminal = (snapshot?.input_states || []).some(input => input.message_id === id
          && ["completed", "failed", "cancelled"].includes(input.state));
        const active = snapshot?.state?.active_message_id === id && snapshot?.controls?.can_stop === true;
        const queued = (snapshot?.queue?.items || []).some(input => input.message_id === id);
        if (terminal || active || queued) this.pendingTurnSubmissions.delete(id);
      }
      const stop = this.stopRequest;
      if (stop && (stop.scope !== residentSessionScope(this.workspace.target())
        || (snapshot?.input_states || []).some(input => input.message_id === stop.messageId
          && ["completed", "failed", "cancelled"].includes(input.state))
        || (Number(snapshot?.cursor || 0) > stop.cursor
          && snapshot?.state?.active_message_id !== stop.messageId
          && this.pendingTurnForTarget()?.messageId !== stop.messageId))) {
        this.stopRequest = null;
      }
      const stopping = Boolean(this.stopRequest);
      this.workspace.setStopPending(stopping);
      const protocolCanStop = snapshot
        ? snapshot.controls?.can_stop === true
        : Boolean(composer.can_stop);
      return stopping || protocolCanStop || Boolean(this.pendingTurnForTarget());
    }

    refreshPendingTurnStopCapability() {
      const composer = this.workspace?.state?.composer;
      if (!this.workspace?.state?.contract || !composer) return;
      const canStop = this.pendingTurnStopCapability(composer, this.protocol?.snapshot?.() || this.pendingSnapshot);
      if (Boolean(composer.can_stop) === canStop) {
        this.workspace.updateControls();
        return;
      }
      this.workspace.setComposer({ ...composer, can_stop: canStop });
    }

    async send(payload = this.workspace.payload(), submitOptions = {}) {
      if (this.pendingSurfaceBinding?.targetRevision === this.targetRevision) return null;
      // An attachment-only message still names what it sends, so it has a text part.
      const attachmentText = composerPayloadContentParts(payload).filter(part => part.type === "text")
        .map(part => String(part.text || "")).join("\n").trim();
      if (!String(payload?.text || payload?.plainText || "").trim() && attachmentText) {
        payload = { ...payload, text: attachmentText, plainText: attachmentText };
      }
      if (!String(payload?.text || payload?.plainText || "").trim()) return null;
      const view = this.conversationView();
      if (view?.send && !String(payload.text || payload.plainText || "").startsWith("/")) return view.send(payload, submitOptions, this);
      if (!this.target().resident_id && this.options.resolveBeforeSend) {
        if (this.preparingResident) return null;
        const revision = this.targetRevision;
        let preparationRevision = revision;
        const initialDraft = this.workspace.draftText();
        this.preparingResident = true;
        this.workspace.setBusy(true);
        this.workspace.setStatus("正在准备 Resident…");
        try {
          const surface = this.surfaceTarget;
          const identity = await this.options.resolveBeforeSend(surface, payload);
          if (revision !== this.targetRevision) return null;
          const draft = this.workspace.draftText();
          const attachments = this.workspace.attachments();
          const refs = this.workspace.refs();
          const pendingDraftScope = this.workspace.draftScope();
          if (draft !== initialDraft) submitOptions = { ...submitOptions, clear: false };
          const binding = this.setResident(identity);
          preparationRevision = this.targetRevision;
          try {
            await binding;
          } finally {
            if (preparationRevision === this.targetRevision) {
              this.workspace.setDraftText(draft, attachments);
              this.workspace.setRefs(refs);
            }
          }
          if (preparationRevision !== this.targetRevision) return null;
          // Move the pre-Resident composition to its conversation. Leaving a
          // copy under the host/card scope would revive it after a later send.
          this.workspace.saveDraft({ immediate: true });
          if (pendingDraftScope !== this.workspace.draftScope()) {
            this.workspace.clearDraft(pendingDraftScope, { text: draft, attachments, refs });
          }
          this.initialContext = this.options.initialContext?.(surface || identity, surface ? identity : this.detail) || null;
        } catch (error) {
          if (preparationRevision === this.targetRevision) {
            this.workspace.setStatus(`Resident 准备失败，结果未确认：${error?.message || "准备请求未完成"}`);
          }
          // Preserve the original receipt for the host's error/recovery path;
          // preparation failure is not a confirmed message or a retry request.
          throw error;
        } finally {
          this.preparingResident = false;
          if (preparationRevision === this.targetRevision) this.workspace.setBusy(false);
        }
      }
      const target = this.workspace.target();
      const content = String(payload?.text || payload?.plainText || "").trim();
      if (!target.resident_id || !content) return null;
      const submittedTarget = normalizeResidentTarget({
        ...target,
        title: this.detail?.resident?.title || target.title,
      });
      const pending = {
        scope: residentSessionScope(target),
        messageId: "",
        promise: null,
        stopPromise: null,
      };
      // A quote names only the quoted message; the platform fills in its author and excerpt (decision_c524c775e0be).
      const quote = content.startsWith("/") || submitOptions.quote === false ? null : this.workspace.quote();
      // A correction written from a running subagent's card goes into the current turn once sent.
      const steer = this.subagentSteer?.scope === residentSessionScope(target) && content.includes(this.subagentSteer.prefix)
        ? this.subagentSteer
        : null;
      if (steer) this.subagentSteer = null;
      const submission = this.workspace.submit(async (submitted, context) => {
        pending.messageId = context.messageIdentity;
        this.pendingTurnSubmissions.set(context.messageIdentity, pending);
        this.refreshPendingTurnStopCapability();
        const contentParts = composerPayloadContentParts(submitted);
        if (refCount(submitted.refs)) {
          contentParts.push({ type: "context_reference", context: refsPayload(submitted.refs) });
        }
        const initialContext = this.initialContext;
        const submittedDetail = this.detail;
        return window.PortalResidentProtocol.sendInput({
          lineage_id: target.lineage_id,
          resident_id: target.resident_id,
          message: {
            message_id: context.messageIdentity,
            role: "user",
            content_parts: contentParts,
            ...(quote ? { quote: { message_id: quote.message_id } } : {}),
          },
        }, {
          preview: quote ? { quote } : {},
          prepare: async (body, snapshot) => {
            if (submitOptions.includeContext === false) return;
            const currentContext = await this.options.submissionContext?.(submittedTarget, submittedDetail);
            const firstMessage = !(snapshot?.messages || []).some(item => messageRole(item) === "human");
            if (currentContext || (initialContext && firstMessage)) {
              body.message.content_parts.push({ type: "context_reference", context: currentContext || initialContext });
            }
          },
        });
      }, {
        payload,
        historyText: String(payload?.plainText || content).trim(),
        rememberHistory: submitOptions.rememberHistory,
        clear: submitOptions.clear,
        optimistic: true,
        focus: submitOptions.focus,
        clearRefsOnSuccess: submitOptions.clearRefsOnSuccess !== false,
        onSuccess: (result, context) => {
          pending.acknowledged = true;
          if (context.scope === this.workspace.sessionScope()) this.initialContext = null;
          if (steer) this.sendSubagentSteerNow(target, context.messageIdentity);
          // Host callbacks describe the submitted conversation even if this
          // Editor was retargeted while waiting for its acknowledgement.
          const sentContext = { ...context, target: submittedTarget };
          this.options.onAcknowledgement?.(result, sentContext, this);
          this.options.onSent?.(result, this, sentContext);
        },
        onRollback: (error, context) => {
          const snapshot = this.snapshot();
          if (snapshot?.resident_id === target.resident_id) this.confirmSubmittedDraft(snapshot, context.scope);
          return this.options.onRollback?.(error, context, this);
        },
      });
      pending.promise = submission;
      try {
        return await submission;
      } finally {
        if (!pending.acknowledged) this.pendingTurnSubmissions.delete(pending.messageId);
        this.refreshPendingTurnStopCapability();
      }
    }

    async stop() {
      if (this.familyStatusFamily) return this.stopFamily();
      const target = this.workspace.target();
      const snapshot = this.protocol?.snapshot?.() || {};
      const pending = this.pendingTurnForTarget(target);
      const messageId = String(pending?.messageId || snapshot?.state?.active_message_id || "").trim();
      if (!target.resident_id || !messageId) return null;
      const scope = residentSessionScope(target);
      if (this.stopRequest?.scope === scope && this.stopRequest.messageId === messageId) {
        return this.stopRequest.promise;
      }
      // This is this Editor's pending action, not a persisted message or an
      // optimistic change to the Resident's authoritative work/input state.
      const request = { scope, messageId, cursor: Number(snapshot.cursor || 0), promise: null };
      this.stopRequest = request;
      this.refreshPendingTurnStopCapability();
      request.promise = this.requestStop(target, pending, messageId).then(result => {
        if ((!result || ["completed", "failed", "cancelled"].includes(result.input_state?.state)) && this.stopRequest === request) {
          this.stopRequest = null;
          this.refreshPendingTurnStopCapability();
        }
        return result;
      }).catch(error => {
        if (this.stopRequest === request) {
          this.stopRequest = null;
          this.refreshPendingTurnStopCapability();
        }
        throw error;
      });
      return request.promise;
    }

    // A facade's Stop stops the whole Family's execution, so it asks first:
    // each running request is stopped at its member, each one being routed is withdrawn.
    async stopFamily() {
      const target = this.target();
      const { active, waiting } = familyExecution(this.protocol?.snapshot?.() || {});
      if (!target.resident_id || !active.length) return null;
      const counts = new Map();
      active.forEach(request => {
        const label = request.state === "routing" ? window.PortalI18n.t("正在选择成员") : request.member;
        counts.set(label, (counts.get(label) || 0) + 1);
      });
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("停止整个 Family 的执行？"),
        message: window.PortalI18n.text`会停止这个 Family 正在执行的全部 ${active.length} 条请求：成员正在处理的停止本轮，正在选择成员的撤回。`,
        description: waiting ? window.PortalI18n.text`排队中的 ${waiting} 条请求不受影响，会照常开始；需要时可以逐条撤回。` : "",
        metadata: [...counts].map(([label, count]) => ({ label, value: `${count} 条` })),
        confirmLabel: window.PortalI18n.t("全部停止"),
        tone: "danger",
      });
      if (!confirmed) return null;
      return this.runAction(async () => {
        const results = await Promise.allSettled(active.map(request => window.PortalResidentProtocol.cancelInput(
          { lineage_id: target.lineage_id, resident_id: target.resident_id, message_id: request.message_id })));
        const failed = results.filter(result => result.status === "rejected");
        if (failed.length === results.length) throw failed[0].reason;
        if (failed.length) {
          window.PortalUI?.toast?.(`已停止 ${results.length - failed.length} 条，另有 ${failed.length} 条没有停止：${failed[0].reason?.message || failed[0].reason}`, "warning");
        }
        return results;
      });
    }

    async requestStop(target, pending, messageId) {
      if (pending?.messageId) {
        if (pending.stopPromise) return pending.stopPromise;
        pending.stopPromise = (async () => {
          try {
            await pending.promise;
          } catch (_error) {
            return null;
          }
          if (pending.scope !== residentSessionScope(this.workspace.target())) return null;
          return window.PortalResidentProtocol.cancelInput({
            lineage_id: target.lineage_id,
            resident_id: target.resident_id,
            message_id: pending.messageId,
          });
        })().catch(error => {
          pending.stopPromise = null;
          throw error;
        });
        return pending.stopPromise;
      }
      return window.PortalResidentProtocol.cancelInput({
        lineage_id: target.lineage_id,
        resident_id: target.resident_id,
        message_id: messageId,
      });
    }

    // Hover hosts change presentation without resizing the mounted workspace.
    setPresented(presented) {
      this.presented = Boolean(presented);
      this.readObserver.schedule();
    }
    target() { return this.workspace.target(); }
    surface() { return this.surfaceTarget; }
    currentDetail() { return this.detail; }
    sessionScope() { return this.workspace.sessionScope(); }
    snapshot() { return this.protocol?.snapshot?.() || this.pendingSnapshot; }
    restore(saved = {}) {
      const target = normalizeResidentTarget(saved.target || {});
      if (!target.resident_id) return;
      // Restore only the Editor binding. Conversation data and permissions must
      // come from a new server read, never from the saved protocol snapshot.
      this.workspace.setTarget(target);
      this.setResident(target, { load: true }).catch(() => {});
    }
    isConversationResetPending() {
      return Boolean(
        this.conversationResetInFlight
        && this.conversationResetScope === this.workspace.sessionScope()
      );
    }
    attachments() { return this.workspace.attachments(); }
    setBusy(value) { return this.workspace.setBusy(value); }
    setStatus(value) { return this.workspace.setStatus(value); }
    updateControls() { return this.workspace.updateControls(); }
    destroy() {
      this.readObserver.destroy();
      this.updateFamilyStatus(null);
      this.selectionController?.destroy();
      this.surfaceRevision += 1;
      this.targetRevision += 1;
      this.unsubscribeProjection?.();
      this.unsubscribeProjection = null;
      this.pendingTurnSubmissions.clear();
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      this.protocol?.destroy?.();
      this.protocol = null;
      this.workspace.destroy();
      this.questions.destroy();
    }
  }

  function createResidentPanel(options = {}) {
    return new ResidentChatPanel(options);
  }

  function requestWorkspaceAction(contract, actionName, payload = {}) {
    const action = contract?.actions?.[String(actionName || "")];
    const instruction = String(action?.instruction || "").trim();
    if (instruction) {
      const args = {
        ...(action.arguments && typeof action.arguments === "object" ? action.arguments : {}),
        ...(payload && typeof payload === "object" ? payload : {}),
      };
      // Session restarts keep their own pending-receipt bookkeeping.
      if (instruction === "RestartSession") {
        return window.PortalResidentService.restartSession({ ...(payload || {}), resident_id: args.resident_id });
      }
      return window.PortalInstructionClient.execute(instruction, args, {
        idempotencyKey: `workspace-action:${globalThis.PortalUIPrimitives.randomUUID()}`,
      });
    }
    const url = String(action?.url || "").trim();
    const method = String(action?.method || "POST").trim().toUpperCase();
    if (!url) throw new Error(`当前 Chat Workspace 没有提供 ${actionName} 能力`);
    if (method !== "POST") throw new Error(`Chat Workspace 暂不支持 ${method} action`);
    return window.PortalAPI.post(url, {
      ...(action.payload && typeof action.payload === "object" ? action.payload : {}),
      ...(payload && typeof payload === "object" ? payload : {}),
    });
  }

  window.PortalChatWorkspace = {
    residentHandoffParts,
    messageRole,
    messageQuoteExcerpt,
    residentMessageFooter,
    noticeQueueCleared,
    formatTokenCount,
    residentMessageBody,
    requestProgressHtml,
    messageAttachmentRefHtml,
    resourceSupportsTextPreview,
    loadResourcePreview,
    bindResourceChips,
    renderMarkdownWithAttachmentRefs,
    normalizeMessageStream,
    newMessageIdentity,
    messageIdentity,
    openImagePreview,
    closeImagePreview,
    bindImagePreview,
    attachmentKind,
    normalizeComposerAttachments,
    inputMediaPayload,
    composerPayloadContentParts,
    persistedDraftAttachments,
    composerAttachmentPlaceholder,
    insertComposerAttachmentRef,
    removeComposerAttachmentRef,
    refreshComposerAttachmentRefs,
    serializeComposerInput,
    setComposerDraftInput,
    renderComposerAttachmentTray,
    bindComposerAttachmentActions,
    inputText,
    setInputText,
    createDraftStore,
    insertPlainText,
    handleAttachmentPaste,
    handleAttachmentDrop,
    handleAttachmentDragOver,
    bindWorkspaceComposer,
    normalizeRefs,
    refCount,
    refsPayload,
    messageListHtml,
    createMessageSelectionController,
    defaultWorkspaceUpload,
    residentRuntimeStatusText,
    residentRuntimeSettingRows,
    showResidentBackendNotices,
    createRuntimeSettingsDialog,
    notifyRuntimeSettingsSaved,
    createSessionRestartDialog,
    openFamilyStatus,
    mountFamilyStatus,
    residentAgentHarnessSettings,
    residentHarnessMenuHtml,
    mountResidentHarnessPanel,
    residentSessionScope,
    openResidentForwardDialog,
    createWorkspace,
    residentProtocolMessageToWorkspace,
    projectResidentConversation,
    loadResidentConversation,
  };
  const panelPackage = {
    type: "chat",
    version: 1,
    component: "PortalChatWorkspace.ResidentChatPanel",
    presentation: { title: window.PortalI18n.t("Chat Workspace"), icon: "chat" },
    sizing: { minWidth: 280, minHeight: 220 },
    instances: "multiple",
    capabilities: ["conversation", "runtime-settings", "references", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["conversationTarget", "resident", "residentId", "runtime"] },
    commands: [{
      id: "send",
      title: window.PortalI18n.t("发送 Chat 消息"),
      capability: "conversation",
      execute: ({ panel, args }) => panel.send(args?.payload),
    }, {
      id: "stop",
      title: window.PortalI18n.t("停止 Chat 任务"),
      capability: "conversation",
      execute: ({ panel }) => panel.stop(),
    }, {
      id: "refresh",
      title: window.PortalI18n.t("刷新 Chat Workspace"),
      capability: "conversation",
      execute: ({ panel }) => panel.reload(),
    }],
    persistence: {
      schemaVersion: 2,
      serialize: (_snapshot, { runtime }) => ({ target: runtime.target() }),
    },
    create: createResidentPanel,
  };
  window.PortalChatWorkspace.panelPackage = Object.freeze(panelPackage);
  if (window.PortalWorkbench) {
    if (typeof window.PortalWorkbench.registerPanelPackage !== "function") {
      throw new Error("PortalChatWorkspace requires the PortalWorkbench Panel Package registry.");
    }
    window.PortalWorkbench.registerPanelPackage(panelPackage);
  }
  requireImageEditor().catch(error => window.PortalUI.toast(error.message, "error"));
}());
