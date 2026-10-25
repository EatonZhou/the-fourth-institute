(function () {
  const escapeHtml = value => window.PortalUIPrimitives.escapeHtml(value);

  // Parts of the chat workspace live in module scripts that every page loads
  // before this one. chatWorkspaceModule(name) returns what a script registers:
  //   chat_workspace_message_content.js  handoff cards, forward references, runtime
  //     status, response sources, attachment references, Markdown and previews
  //   chat_workspace_composer.js  the message input, its tokens, caret, paste and
  //     drop, attachment tray, slash and @mention menus, sent content parts
  //   chat_workspace_refs.js  the Skills, Agents and resources picked for a
  //     message, their popover, count, summary and payload
  //   chat_workspace_inputs.js  queued inputs, the Arrow-key input history and
  //     the drafts saved per conversation
  //   chat_workspace_messages.js  one message: role, identity, author and avatar,
  //     its refs and attachments, action menu, quote and copy text, folding, keys
  //   chat_workspace_message_list.js  grouping, the list HTML, inheritance cards,
  //     the live runtime stream, quoting a selection, reconciling the list
  //   chat_workspace_runtime_settings.js  the Runtime Settings dialog, notices after
  //     saving, the session restart dialog and its progress
  //   chat_workspace_harness_panel.js  the Agent Harness panel and menu, prompt
  //     injection presets and preview
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
    COMPOSER_DRAFT_ATTACHMENT_RE, normalizeComposerAttachment, normalizeComposerAttachments,
    createConversationSelectionReference, inputMediaPayload, mediaOnlyCaption, composerPayloadContentParts,
    composerPreviewReference, composerAttachmentPlaceholder, composerAttachmentTokenOrder,
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
    MESSAGE_FOLD_MIN_LINES, messageFoldChoices, messageFoldReply, messageFoldDefaults, messageFoldBodyHtml,
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

  // Chat attachments are uploads (CreateUpload + its data channel); the
  // message then refers to the upload ID (decision_ad3e21094f14).
  async function defaultWorkspaceUpload(file, options = {}) {
    const kind = String(file?.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
    // The receiving Resident, or the Chatroom a message is posted to.
    const target = options.upload_target || options.resident_id || "";
    const uploaded = await window.PortalInstructionClient.upload(file, {
      purpose: "conversation_attachment",
      target,
      fileName: file.name || (kind === "image" ? "pasted-image.png" : "attachment"),
      mediaType: file.type || (kind === "image" ? "image/png" : "application/octet-stream"),
      onProgress: progress => options.workspace?.updateAttachment?.(options.item?.id, { progress }),
    });
    options.workspace?.updateAttachment?.(options.item?.id, { progress: 100 });
    return {
      upload_id: uploaded.upload_id,
      filename: uploaded.file_name,
      content_type: uploaded.media_type,
      size: uploaded.size,
      kind,
    };
  }

  function defaultWorkspaceDeleteAttachment(item = {}) {
    const uploadId = String(item.upload_id || "").trim();
    if (!uploadId) return Promise.resolve({ deleted: false });
    return window.PortalInstructionClient.execute("DeleteUpload", { upload_id: uploadId }, {
      idempotencyKey: `upload-delete:${uploadId}`,
    });
  }

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

  // The search owns only its query/results. Conversation messages and navigation
  // remain owned by the Workspace and its existing history loader.
  function createConversationSearch({ root, scope, messages, rendering, search, loadOlder, canLoadOlder, reveal }) {
    const trigger = root.querySelector("[data-workspace-history-search]");
    const panel = document.createElement("section");
    panel.className = "portalConversationSearch";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", window.PortalI18n.t("查找当前对话"));
    panel.innerHTML = window.PortalI18n.html`<div data-portal-search-field data-portal-search-density="compact"><label>查找当前对话</label><input data-portal-search-input type="search" aria-label="搜索当前对话" placeholder="搜索当前对话…" autocomplete="off" /></div>
      <div class="paneNote conversationSearchStatus" role="status"></div>
      <div class="conversationSearchResults" aria-label="对话片段"></div>
      <button class="portalButton is-quiet is-compact conversationSearchMore" type="button" hidden>加载更多</button>`;
    root.appendChild(panel);
    const input = panel.querySelector("input");
    const status = panel.querySelector('[role="status"]');
    const list = panel.querySelector(".conversationSearchResults");
    const more = panel.querySelector(".conversationSearchMore");
    let revision = 0, timer = 0, currentScope = scope(), query = "", rows = [], nextPage = "", busy = false, error = "";
    let disposed = false;
    const binding = window.PortalOverlay.bindPopover({
      trigger, panel, portal: true, placement: "bottom-end", gap: 6,
      onOpen: () => { refresh(); input.focus({ preventScroll: true }); },
      onClose: () => { revision += 1; clearTimeout(timer); busy = false; },
    });
    function snippet(text) {
      const value = String(text || "").replace(/\s+/g, " ").trim();
      const needle = query.toLocaleLowerCase();
      const index = needle ? value.toLocaleLowerCase().indexOf(needle) : 0;
      const start = Math.max(0, index - 45);
      const excerpt = value.slice(start, start + Math.max(210, query.length + 70));
      if (!needle) return escapeHtml(excerpt);
      let html = start ? "…" : "", offset = 0;
      const lower = excerpt.toLocaleLowerCase();
      for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, offset)) {
        html += escapeHtml(excerpt.slice(offset, at)) + window.PortalI18n.html`<mark>${escapeHtml(excerpt.slice(at, at + query.length))}</mark>`;
        offset = at + query.length;
      }
      return html + escapeHtml(excerpt.slice(offset));
    }
    function render() {
      const opts = rendering();
      const scrollTop = list.scrollTop;
      list.innerHTML = rows.map((item, index) => window.PortalI18n.html`<button type="button" class="conversationSearchResult" data-search-message="${escapeHtml(messageRenderKey(item))}" data-portal-tooltip="${escapeHtml(String(item.content || "").trim())}">
        <span class="conversationSearchMeta"><span>${escapeHtml(messageTitle(item, index, opts))}</span><time>${escapeHtml(opts.formatTime?.(item.created_at) || item.created_at || "")}</time></span>
        <span class="conversationSearchExcerpt">${snippet(item.content)}</span></button>`).join("");
      list.scrollTop = scrollTop;
      status.textContent = error || (busy ? window.PortalI18n.t("正在查找…") : rows.length ? `${query ? window.PortalI18n.t("匹配") : window.PortalI18n.t("对话片段")} · ${rows.length}` : query ? window.PortalI18n.t("没有匹配的片段") : window.PortalI18n.t("暂无对话片段"));
      more.hidden = !(error || (query && search ? nextPage : canLoadOlder()));
      more.disabled = busy;
      more.textContent = error ? window.PortalI18n.t("重试") : query ? window.PortalI18n.t("加载更多结果") : window.PortalI18n.t("加载更早片段");
    }
    async function refresh(append = false, preserveScroll = false) {
      const request = ++revision;
      clearTimeout(timer);
      error = "";
      if (!append) { nextPage = ""; rows = []; if (!preserveScroll) list.scrollTop = 0; }
      if (!query || !search) {
        const opts = rendering();
        rows = messages().filter((item, index, items) => String(item.content || "").trim()
          && (query ? String(item.content).toLocaleLowerCase().includes(query.toLocaleLowerCase())
            : messageRole(item) === "human"
              && !String(opts.messageClass?.(item, index, items) || "").split(/\s+/).includes("messageIncoming"))).slice().reverse();
        busy = false; render(); return;
      }
      busy = true; render();
      try {
        const result = await search(query, { pageToken: append ? nextPage : "", limit: 50 });
        if (disposed || request !== revision || currentScope !== scope()) return;
        const seen = new Set(rows.map(messageRenderKey));
        rows = [...rows, ...(result.messages || []).filter(item => !seen.has(messageRenderKey(item)))];
        nextPage = result.next_page_token || "";
      } catch (err) {
        if (disposed || request !== revision) return;
        error = `查找失败：${err.message || err}`;
      } finally {
        if (!disposed && request === revision) { busy = false; render(); binding.reposition(); }
      }
    }
    trigger.addEventListener("click", () => binding.toggle());
    input.addEventListener("input", () => {
      revision += 1;
      clearTimeout(timer);
      query = input.value.trim(); rows = []; nextPage = ""; error = "";
      busy = !!query && !!search; render();
      timer = window.setTimeout(() => refresh(), 220);
    });
    panel.addEventListener("keydown", event => {
      const buttons = [...list.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
      } else if (event.key === "Enter" && event.target === input && !busy) {
        event.preventDefault(); buttons[0]?.click();
      }
    });
    list.addEventListener("click", async event => {
      const button = event.target.closest("[data-search-message]");
      if (!button) return;
      binding.close();
      try { await reveal(button.dataset.searchMessage); }
      catch (err) { window.PortalUI.toast(window.PortalI18n.text`定位失败：${err.message || err}`, "error"); }
    });
    more.addEventListener("click", async () => {
      if (busy) return;
      if (query && search) { void refresh(!error); return; }
      const request = ++revision;
      busy = true; error = ""; render();
      try { await loadOlder(); }
      catch (err) { if (request === revision) error = `加载失败：${err.message || err}`; }
      if (disposed || request !== revision || currentScope !== scope()) return;
      busy = false;
      if (error) render(); else refresh();
    });
    return {
      update(messagesChanged) {
        if (currentScope !== scope()) {
          binding.close(); revision += 1; clearTimeout(timer);
          currentScope = scope(); input.value = query = ""; rows = []; nextPage = "";
          busy = false; error = ""; render();
        }
        if (messagesChanged && binding.isOpen() && !query && !busy) refresh(false, true);
      },
      close() { binding.close(); },
      destroy() { disposed = true; revision += 1; clearTimeout(timer); binding.destroy(); panel.remove(); },
    };
  }

  function createWorkspace(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new Error("PortalChatWorkspace.createWorkspace root required");
    root.__portalChatWorkspaceController?.destroy?.();
    const id = workspaceInstanceId(options);
    const refsScope = String(options.refsScope || id);
    const ownedAttachmentPreviews = new Set();
    const releaseAttachmentPreview = value => {
      if (!ownedAttachmentPreviews.delete(value)) return;
      URL.revokeObjectURL(value);
    };
    const releaseAttachmentPreviews = items => {
      for (const item of items || []) releaseAttachmentPreview(item?.preview_url);
    };
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
      messageRenderSignature: "",
      messageItemSignatures: [],
      messageRenderContextSignature: "",
      messageRendererRefs: [],
      messageRenderOptions: {},
      collapsedAgentGroups: new Set(),
      collapsedWorkGroups: new Set(),
      expandedAgentGroups: new Set(),
      messageFoldDefaults: new Set(),
      messageFoldSizes: new Map(),
      latestFoldReplyKey: "",
      latestFoldReplyAt: -Infinity,
      undoStack: [],
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
    let messageScrollGesturePending = false;
    let messageScrollPointerActive = false;
    let messageScrollTouchActive = false;
    const messageViewportMeasurable = () => !!(
      els.messages?.isConnected
      && els.messages.clientHeight > 0
      && els.messages.getClientRects().length
    );
    const syncMessageScrollMode = () => {
      els.messages?.classList.toggle("is-following-tail", state.messageTailFollowIntent);
      els.messages?.classList.toggle("is-reading-history", !state.messageTailFollowIntent);
    };
    syncMessageScrollMode();
    const expectMessageScrollFromGesture = () => {
      messageScrollGesturePending = true;
    };
    const endMessageScrollPointerGesture = () => {
      if (!messageScrollPointerActive) return;
      messageScrollPointerActive = false;
    };
    const handleMessageScrollPointerDown = event => {
      if (event.target !== els.messages) return;
      messageScrollPointerActive = true;
      expectMessageScrollFromGesture();
    };
    const beginMessageScrollTouchGesture = () => {
      messageScrollTouchActive = true;
      expectMessageScrollFromGesture();
    };
    const endMessageScrollTouchGesture = () => {
      messageScrollTouchActive = false;
    };
    const handleMessageScrollKeyDown = event => {
      if (!["ArrowUp", "ArrowDown", "PageUp", "PageDown", window.PortalI18n.t("Home"), "End", " "].includes(event.key)) return;
      expectMessageScrollFromGesture();
    };
    const handleMessageViewportScroll = () => {
      if (!messageViewportMeasurable()) return;
      if (!messageScrollGesturePending && !messageScrollPointerActive && !messageScrollTouchActive) return;
      // A user scroll supersedes the viewport captured before a panel layout.
      // Otherwise its delayed restoration can snap a new reading position back.
      if (pendingWorkbenchViewport) {
        els.messages.style.overflowAnchor = pendingWorkbenchViewport.overflowAnchorStyle;
        pendingWorkbenchViewport = null;
        window.cancelAnimationFrame(workbenchViewportRestoreFrame);
        window.cancelAnimationFrame(workbenchViewportSettleFrame);
        workbenchViewportRestoreFrame = 0;
        workbenchViewportSettleFrame = 0;
      }
      state.messageTailFollowIntent = messageViewportAtTail(els.messages);
      workbenchAnchorKey = "";
      if (!messageScrollPointerActive && !messageScrollTouchActive) messageScrollGesturePending = false;
      syncMessageScrollMode();
    };
    let workbenchAnchorKey = "";
    let pendingWorkbenchViewport = null;
    let workbenchViewportRestoreFrame = 0;
    let workbenchViewportSettleFrame = 0;
    let windowResizeFollowFrame = 0;
    const captureWorkbenchViewport = () => {
      if (!messageViewportMeasurable()) return null;
      const viewportBox = els.messages.getBoundingClientRect();
      const rows = [...els.messages.querySelectorAll("[data-message-key]")]
        .filter(row => row.dataset.messageKey !== "__scroll_anchor__");
      // A partially clipped message can become shorter than its clipped region
      // after text reflow, so it cannot remain visible while retaining its top
      // offset. Anchor the first row whose leading edge is visible; fall back to
      // the intersecting row only when the viewport contains no such row. Until
      // the user scrolls, later layout changes keep the same row, so opening and
      // closing a sibling Panel brings the reader back to the same place.
      const leadingEdgeVisible = row => {
        const box = row.getBoundingClientRect();
        return box.top >= viewportBox.top + 1 && box.top < viewportBox.bottom;
      };
      const anchor = rows.find(row => workbenchAnchorKey && messageNodeKey(row) === workbenchAnchorKey && leadingEdgeVisible(row))
        || rows.find(leadingEdgeVisible) || rows.find(row => {
        const box = row.getBoundingClientRect();
        return box.bottom >= viewportBox.top + 1 && box.top < viewportBox.bottom;
      });
      const anchorBox = anchor?.getBoundingClientRect();
      workbenchAnchorKey = messageNodeKey(anchor);
      const anchorProgress = anchorBox?.top < viewportBox.top && anchorBox.bottom > viewportBox.bottom
        ? (viewportBox.top - anchorBox.top) / anchorBox.height
        : null;
      return {
        followTail: state.messageTailFollowIntent,
        scrollTop: els.messages.scrollTop,
        anchor,
        anchorKey: messageNodeKey(anchor),
        anchorOffset: anchorBox ? anchorBox.top - viewportBox.top : 0,
        anchorProgress,
        overflowAnchorStyle: els.messages.style.overflowAnchor,
      };
    };
    const handleBeforeWorkbenchLayout = () => {
      // Several synchronous Workbench mutations may precede one painted layout.
      // Keep the first stable viewport instead of recapturing an already-clamped
      // one, unless a user action or history-scope change has since changed the
      // authoritative tail-following intent.
      const captured = captureWorkbenchViewport();
      if (!pendingWorkbenchViewport) {
        pendingWorkbenchViewport = captured || {
          followTail: state.messageTailFollowIntent,
          scrollTop: 0,
          anchor: null,
          anchorKey: "",
          anchorOffset: 0,
          overflowAnchorStyle: els.messages.style.overflowAnchor,
        };
        els.messages.style.overflowAnchor = "none";
        return;
      }
      if (pendingWorkbenchViewport.followTail !== state.messageTailFollowIntent) {
        pendingWorkbenchViewport = captured || {
          followTail: state.messageTailFollowIntent,
          scrollTop: pendingWorkbenchViewport.scrollTop,
          anchor: null,
          anchorKey: "",
          anchorOffset: 0,
          overflowAnchorStyle: pendingWorkbenchViewport.overflowAnchorStyle,
        };
      }
    };
    const restoreWorkbenchViewport = snapshot => {
      if (!snapshot || !messageViewportMeasurable()) return;
      if (snapshot.followTail) {
        els.messages.scrollTop = els.messages.scrollHeight;
        return;
      }
      const keyedAnchor = snapshot.anchorKey
        ? els.messages.querySelector(messageInstanceSelector(snapshot.anchorKey))
        : null;
      const anchor = snapshot.anchor?.isConnected && els.messages.contains(snapshot.anchor)
        ? snapshot.anchor
        : keyedAnchor;
      if (anchor) {
        const viewportTop = els.messages.getBoundingClientRect().top;
        const anchorBox = anchor.getBoundingClientRect();
        const nextOffset = anchorBox.top - viewportTop;
        const targetOffset = snapshot.anchorProgress === null || snapshot.anchorProgress === undefined
          ? snapshot.anchorOffset
          : -snapshot.anchorProgress * anchorBox.height;
        els.messages.scrollTop += nextOffset - targetOffset;
        return;
      }
      els.messages.scrollTop = snapshot.scrollTop;
    };
    const handleWorkbenchResize = () => {
      if (!pendingWorkbenchViewport) return;
      window.cancelAnimationFrame(workbenchViewportRestoreFrame);
      window.cancelAnimationFrame(workbenchViewportSettleFrame);
      workbenchViewportRestoreFrame = window.requestAnimationFrame(() => {
        workbenchViewportRestoreFrame = 0;
        if (!pendingWorkbenchViewport || !messageViewportMeasurable()) return;
        const snapshot = pendingWorkbenchViewport;
        restoreWorkbenchViewport(snapshot);
        workbenchViewportSettleFrame = window.requestAnimationFrame(() => {
          workbenchViewportSettleFrame = 0;
          if (pendingWorkbenchViewport !== snapshot) return;
          restoreWorkbenchViewport(snapshot);
          pendingWorkbenchViewport = null;
          els.messages.style.overflowAnchor = snapshot.overflowAnchorStyle;
        });
      });
    };
    els.messages?.addEventListener("scroll", handleMessageViewportScroll, { passive: true });
    els.messages?.addEventListener("wheel", expectMessageScrollFromGesture, { passive: true });
    els.messages?.addEventListener("touchstart", beginMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchmove", beginMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchend", endMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchcancel", endMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("pointerdown", handleMessageScrollPointerDown, { passive: true });
    els.messages?.addEventListener("keydown", handleMessageScrollKeyDown);
    window.addEventListener("pointerup", endMessageScrollPointerGesture, { passive: true });
    window.addEventListener("pointercancel", endMessageScrollPointerGesture, { passive: true });
    root.addEventListener("portal:workbench-before-layout", handleBeforeWorkbenchLayout);
    root.addEventListener("portal:workbench-resize", handleWorkbenchResize);
    // A body folds by its rendered height: measured after each render, when the list
    // changes width or becomes visible, and when an image in it loads.
    const measureMessageFolds = (roots = [els.messages], { reading = false } = {}) => {
      const bodies = roots.flatMap(node => node ? [
        ...(node.matches?.(".messageContent[data-message-fold]") ? [node] : []),
        ...(node.querySelectorAll?.(".messageContent[data-message-fold]") || []),
      ] : []);
      const viewport = reading ? els.messages.getBoundingClientRect() : null;
      // Read every height before writing any, so the list lays out once.
      const measured = bodies.map(body => {
        if (!body.getClientRects().length) return null;
        const lineHeight = Number.parseFloat(getComputedStyle(body).lineHeight) || 21;
        const box = viewport ? body.getBoundingClientRect() : null;
        return {
          size: body.scrollHeight > lineHeight * MESSAGE_FOLD_MIN_LINES + 1 ? "long" : "short",
          inView: !!box && box.bottom > viewport.top && box.top < viewport.bottom,
        };
      });
      bodies.forEach((body, index) => {
        const key = body.closest("[data-message-key]")?.dataset.messageKey || "";
        const result = measured[index];
        if (!result || !key) return;
        // A body that grows long while the reader looks at it stays open.
        if (result.inView && result.size === "long" && body.dataset.messageFoldSize !== "long"
          && body.dataset.messageFold === "collapsed" && !messageFoldChoices.has(key)) {
          messageFoldChoices.set(key, "expanded");
          applyMessageFold(body.nextElementSibling, true);
        }
        state.messageFoldSizes.set(key, result.size);
        if (body.dataset.messageFoldSize !== result.size) body.dataset.messageFoldSize = result.size;
      });
    };
    const firstVisibleMessageRow = () => {
      const top = els.messages.getBoundingClientRect().top + 1;
      const rows = [...els.messages.querySelectorAll(".messageList > [data-message-key]")];
      return rows.find(row => row.getBoundingClientRect().top >= top)
        || rows.find(row => row.getBoundingClientRect().bottom >= top) || null;
    };
    let messageFoldWidth = 0;
    const messageFoldObserver = els.messages && typeof ResizeObserver === "function"
      ? new ResizeObserver(() => {
        const width = els.messages.clientWidth;
        if (width === messageFoldWidth) return;
        messageFoldWidth = width;
        if (!width) return;
        // While the user reads history, the first message starting in view keeps its place.
        const reading = !state.messageTailFollowIntent && messageViewportMeasurable();
        const anchor = reading ? firstVisibleMessageRow() : null;
        const before = anchor?.getBoundingClientRect().top;
        measureMessageFolds([els.messages], { reading });
        if (anchor) els.messages.scrollTop += anchor.getBoundingClientRect().top - before;
      })
      : null;
    messageFoldObserver?.observe(els.messages);
    const measureLoadedMessageImage = event => {
      const body = event.target?.tagName === "IMG" ? event.target.closest(".messageContent[data-message-fold]") : null;
      if (body) measureMessageFolds([body]);
    };
    els.messages?.addEventListener("load", measureLoadedMessageImage, true);
    // A newer reply or the end of a turn changes which messages start expanded. A message
    // the user is reading stays open, so its text is not pulled away.
    const updateMessageFoldDefaults = () => {
      const previous = state.messageFoldDefaults;
      const next = messageFoldDefaults(state.messages, {
        runtimeLive: state.runtimeLive,
        onLatestReply: (item, at) => {
          state.latestFoldReplyKey = item ? messageRenderKey(item) : "";
          state.latestFoldReplyAt = at;
        },
      });
      state.messageFoldDefaults = next;
      const shown = new Set(state.messages.map(messageRenderKey));
      const changed = [...previous].filter(key => !next.has(key))
        .concat([...next].filter(key => !previous.has(key)))
        .filter(key => shown.has(key) && !messageFoldChoices.has(key));
      if (changed.length && !state.messageTailFollowIntent && messageViewportMeasurable()) {
        const viewport = els.messages.getBoundingClientRect();
        for (const key of changed) {
          if (next.has(key)) continue;
          const box = els.messages.querySelector(`[data-message-key="${CSS.escape(key)}"]`)?.getBoundingClientRect();
          if (box && box.bottom > viewport.top && box.top < viewport.bottom) messageFoldChoices.set(key, "expanded");
        }
      }
      return changed.filter(key => !messageFoldChoices.has(key));
    };
    const toggleMessageFold = button => {
      const key = String(button.dataset.messageFoldToggle || "");
      const body = button.previousElementSibling;
      if (!key || !body?.dataset.messageFold) return;
      const expanded = body.dataset.messageFold !== "expanded";
      messageFoldChoices.set(key, expanded ? "expanded" : "collapsed");
      // Opening keeps the text in place to read on; closing keeps the button under the pointer.
      const anchor = expanded ? body : button;
      const before = anchor.getBoundingClientRect().top;
      document.querySelectorAll(`[data-message-fold-toggle="${CSS.escape(key)}"]`)
        .forEach(other => applyMessageFold(other, expanded));
      if (!messageViewportMeasurable()) return;
      els.messages.scrollTop += anchor.getBoundingClientRect().top - before;
      state.messageTailFollowIntent = messageViewportAtTail(els.messages);
      syncMessageScrollMode();
    };
    const markComposerInputActivity = (activity = {}) => {
      state.composerInputFocused = !!activity.focused;
      state.composerImeActive = !!activity.composing;
      updateControls();
    };
    const slashCommandConfig = () => {
      const configured = state.composer.slash_commands
        || options.slash_commands
;
      return normalizeSlashCommands(configured);
    };
    const setSlashCommandMenu = (items, activeIndex = 0) => {
      state.slashCommandItems = Array.isArray(items) ? items : [];
      state.slashCommandActiveIndex = state.slashCommandItems.length
        ? Math.max(0, Math.min(Number(activeIndex || 0), state.slashCommandItems.length - 1))
        : 0;
      if (!els.slashCommands) return;
      const previousMenu = els.slashCommands.querySelector(".composerSlashCommandMenu");
      const previousScrollTop = previousMenu ? previousMenu.scrollTop : 0;
      els.slashCommands.innerHTML = slashCommandMenuHtml(state.slashCommandItems, state.slashCommandActiveIndex);
      els.slashCommands.hidden = !state.slashCommandItems.length;
      const menu = els.slashCommands.querySelector(".composerSlashCommandMenu");
      const active = menu?.querySelector(".composerSlashCommandItem.active");
      if (menu && active) {
        const padding = 6;
        menu.scrollTop = Math.max(0, Math.min(previousScrollTop, menu.scrollHeight - menu.clientHeight));
        const itemTop = active.offsetTop;
        const itemBottom = itemTop + active.offsetHeight;
        const viewTop = menu.scrollTop;
        const viewBottom = viewTop + menu.clientHeight;
        if (itemTop < viewTop) {
          menu.scrollTop = Math.max(0, itemTop - padding);
        } else if (itemBottom > viewBottom) {
          menu.scrollTop = itemBottom - menu.clientHeight + padding;
        }
      }
    };
    const refreshSlashCommandMenu = () => {
      if (!els.input) return;
      const items = slashCommandMatches(els.input, state.attachments, slashCommandConfig());
      const active = state.slashCommandItems[state.slashCommandActiveIndex]?.command;
      const nextIndex = active ? Math.max(0, items.findIndex(item => item.command === active)) : 0;
      setSlashCommandMenu(items, nextIndex);
    };
    const closeSlashCommandMenu = () => setSlashCommandMenu([]);
    const chooseSlashCommand = (command) => {
      if (!els.input || !command) return false;
      setComposerDraftInput(els.input, `${command} `, state.attachments, {
        ...attachmentPreviewOptions(),
        focus: true,
      });
      closeSlashCommandMenu();
      updateControls();
      saveActiveDraft();
      options.onInput?.(controller.payload(), controller);
      return true;
    };
    const handleSlashCommandKey = (event) => {
      if (!state.slashCommandItems.length) return false;
      const key = String(event.key || "");
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        const delta = key === "ArrowDown" ? 1 : -1;
        const next = (state.slashCommandActiveIndex + delta + state.slashCommandItems.length) % state.slashCommandItems.length;
        setSlashCommandMenu(state.slashCommandItems, next);
        return true;
      }
      if (key === "Enter" || key === "Tab") {
        if (event.ctrlKey || event.metaKey) return false;
        event.preventDefault();
        return chooseSlashCommand(state.slashCommandItems[state.slashCommandActiveIndex]?.command || "");
      }
      if (key === "Escape") {
        event.preventDefault();
        closeSlashCommandMenu();
        return true;
      }
      return false;
    };
    let mentionTimer = 0;
    let mentionRequest = 0;
    let mentionAbort = null;
    let mentionStatus = "";
    let mentionRenderFrame = 0;
    let mentionMarkup = "";
    const renderMentionMenu = () => {
      mentionRenderFrame = 0;
      if (!els.mentions || isComposerImeActive(els.input)) return;
      // Coalesce loading and result updates outside the input handler. Changing
      // the selection only updates two rows, preserving the list and its scroll.
      const html = mentionMenuHtml(state.mentionItems, -1, options.mentionTitle, mentionStatus);
      if (html !== mentionMarkup || (html && !els.mentions.firstElementChild)) {
        const scrollTop = els.mentions.firstElementChild?.scrollTop || 0;
        els.mentions.innerHTML = html;
        mentionMarkup = html;
        if (els.mentions.firstElementChild) els.mentions.firstElementChild.scrollTop = scrollTop;
        bindMessageAvatars(els.mentions);
      }
      els.mentions.hidden = !state.mentionItems.length && !mentionStatus;
      const previous = els.mentions.querySelector(".composerMentionItem.active");
      const active = els.mentions.querySelector(`[data-portal-mention-index="${state.mentionActiveIndex}"]:not(:disabled)`);
      if (previous !== active) {
        previous?.classList.remove("active");
        previous?.setAttribute("aria-selected", "false");
        active?.classList.add("active");
        active?.setAttribute("aria-selected", "true");
      }
      // Only scroll the candidate list, never the surrounding workspace or page.
      const menu = els.mentions.firstElementChild;
      if (active && menu) {
        const top = active.offsetTop;
        if (top < menu.scrollTop) menu.scrollTop = top;
        else if (top + active.offsetHeight > menu.scrollTop + menu.clientHeight) {
          menu.scrollTop = top + active.offsetHeight - menu.clientHeight;
        }
      }
    };
    const scheduleMentionRender = () => {
      if (!mentionRenderFrame) mentionRenderFrame = window.requestAnimationFrame(renderMentionMenu);
    };
    const setMentionMenu = (items, activeIndex = 0, status = mentionStatus) => {
      mentionStatus = status;
      state.mentionItems = Array.isArray(items) ? items : [];
      const enabled = index => Boolean(state.mentionItems[index]) && !state.mentionItems[index].disabled;
      state.mentionActiveIndex = enabled(activeIndex) ? activeIndex : Math.max(0, state.mentionItems.findIndex(item => !item.disabled));
      scheduleMentionRender();
    };
    const closeMentionMenu = () => {
      window.clearTimeout(mentionTimer);
      mentionRequest += 1;
      mentionAbort?.abort();
      mentionAbort = null;
      state.mentionQuery = null;
      setMentionMenu([], 0, "");
      if (els.mentions) els.mentions.hidden = true;
    };
    const refreshMentionMenu = () => {
      if (typeof options.mentionCandidates !== "function" || !els.input) return;
      if (isComposerImeActive(els.input)) return;
      const token = state.slashCommandItems.length ? null : composerMentionToken(els.input);
      const dismissed = state.mentionDismissed;
      if (!token) {
        state.mentionDismissed = null;
        closeMentionMenu();
        return;
      }
      if (dismissed && dismissed.node === token.node && dismissed.start === token.start) {
        closeMentionMenu();
        return;
      }
      state.mentionDismissed = null;
      if (token.query === state.mentionQuery) return;
      state.mentionQuery = token.query;
      window.clearTimeout(mentionTimer);
      const request = ++mentionRequest;
      mentionAbort?.abort();
      const abort = new AbortController();
      mentionAbort = abort;
      setMentionMenu([], 0, "正在查找 Resident…");
      const update = (items, status) => {
        if (request !== mentionRequest || abort.signal.aborted) return;
        const selected = state.mentionItems[state.mentionActiveIndex]?.value;
        setMentionMenu(items, items.findIndex(item => item.value === selected), status);
      };
      // @ itself responds immediately; coalesce keyword edits even while
      // the first search is still loading. Old results are never selectable.
      mentionTimer = window.setTimeout(() => {
        Promise.resolve().then(() => options.mentionCandidates(token.query, controller, {
          signal: abort.signal,
          onUpdate: items => update(items, "正在查找 Resident…"),
        }))
          .then(items => update(items, items.length ? "" : "没有匹配的 Resident"))
          .catch(error => {
            if (request !== mentionRequest || abort.signal.aborted) return;
            setMentionMenu(state.mentionItems, state.mentionActiveIndex,
              state.mentionItems.length ? "部分候选加载失败，请重新输入重试" : "候选加载失败，请重新输入重试");
            options.onError ? options.onError(error) : window.PortalUI?.toast?.(error?.message || String(error), "error");
          });
      }, token.query ? 150 : 0);
    };
    const chooseMention = index => {
      const item = state.mentionItems[index];
      if (!item || item.disabled || !els.input) return false;
      const token = composerMentionToken(els.input);
      if (token) {
        const range = document.createRange();
        range.setStart(token.node, token.start);
        range.setEnd(token.node, token.end);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      closeMentionMenu();
      if (els.input.isContentEditable) {
        const selection = window.getSelection();
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        if (!range || !els.input.contains(range.commonAncestorContainer)) return false;
        const node = document.createElement("span");
        node.className = "portalMention";
        node.contentEditable = "false";
        node.dataset.mentionValue = item.value;
        node.textContent = `@${item.title || item.label}`;
        node.title = item.value;
        range.deleteContents();
        range.insertNode(node);
        const space = document.createTextNode(" ");
        node.after(space);
        range.setStartAfter(space);
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
        els.input.dispatchEvent(new Event("input", { bubbles: true }));
      } else insertPlainText(els.input, `${item.value} `);
      return true;
    };
    const handleMentionKey = (event) => {
      if (state.mentionQuery === null || event.isComposing) return false;
      const key = String(event.key || "");
      if ((key === "ArrowDown" || key === "ArrowUp") && state.mentionItems.length) {
        event.preventDefault();
        const delta = key === "ArrowDown" ? 1 : -1;
        const count = state.mentionItems.length;
        let next = state.mentionActiveIndex;
        for (let step = 0; step < count; step += 1) {
          next = (next + delta + count) % count;
          if (!state.mentionItems[next].disabled) break;
        }
        setMentionMenu(state.mentionItems, next);
        return true;
      }
      if (key === "Enter" || key === "Tab") {
        if (event.ctrlKey || event.metaKey || !state.mentionItems.some(item => !item.disabled)) return false;
        event.preventDefault();
        return chooseMention(state.mentionActiveIndex);
      }
      if (key === "Escape") {
        event.preventDefault();
        const token = composerMentionToken(els.input);
        state.mentionDismissed = token ? { node: token.node, start: token.start } : null;
        closeMentionMenu();
        return true;
      }
      return false;
    };
    const configuredHistoryScope = (composerState = state.composer) => {
      const optionScope = typeof options.historyScope === "function"
        ? options.historyScope()
        : options.historyScope;
      return String(optionScope || composerState?.history_scope || id);
    };
    const historyScope = () => configuredHistoryScope();
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
    const notifyAttachmentsChange = () => {
      saveActiveDraft();
      options.onAttachmentsChange?.(controller.payload(), controller);
    };
    const attachmentOriginal = item => {
      const raw = item && typeof item === "object" ? item : {};
      return raw.original && typeof raw.original === "object" ? raw.original : {
        upload_id: String(raw.upload_id || ""),
        file_id: String(raw.file_id || ""),
        kind: String(raw.kind || "image"),
        name: String(raw.name || ""),
        path: String(raw.path || ""),
        preview_url: String(raw.preview_url || raw.data_url || ""),
        content_type: String(raw.content_type || raw.mime || ""),
        size: Number(raw.size || raw.size_bytes || 0),
      };
    };
    const replaceEditedAttachment = async (context, file, previewUrl) => {
      const attachmentId = String(context?.attachmentId || "").trim();
      const current = state.attachments.find(item => String(item?.id || "") === attachmentId);
      if (!attachmentId || !current) throw new Error("未找到需要替换的图片附件");
      if (current.uploading) throw new Error("图片还在上传，请稍后编辑。");
      const previous = { ...current };
      const scope = historyScope();
      if (String(previewUrl || "").startsWith("blob:")) ownedAttachmentPreviews.add(previewUrl);
      controller.updateAttachment(attachmentId, {
        upload_id: "",
        file_id: "",
        original: attachmentOriginal(current),
        name: file.name,
        path: "",
        url: "",
        preview_url: previewUrl,
        data_url: "",
        mime: "image/png",
        content_type: "image/png",
        size: file.size,
        size_bytes: file.size,
        uploading: true,
        progress: 0,
        error: "",
      });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(file, {
          ...attachmentUploadContext(),
          item: current,
          workspace: controller,
          editedAttachmentId: attachmentId,
        });
        if (historyScope() !== scope || !state.attachments.some(item => String(item.id || "") === attachmentId)) {
          await deleteUploadedAttachment({ ...result, kind: "image" });
          releaseAttachmentPreview(previewUrl);
          return;
        }
        const uploadedPreview = result?.preview_url || result?.data_url || mediaPartPath({ upload_id: result?.upload_id }) || previewUrl;
        if (uploadedPreview !== previous.preview_url) releaseAttachmentPreview(previous.preview_url);
        if (uploadedPreview !== previewUrl) releaseAttachmentPreview(previewUrl);
        controller.updateAttachment(attachmentId, {
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          original: attachmentOriginal(current),
          name: result?.filename || file.name,
          path: result?.path || "",
          url: result?.url || "",
          preview_url: uploadedPreview,
          data_url: result?.data_url || "",
          mime: result?.mime || result?.content_type || "image/png",
          content_type: result?.content_type || "image/png",
          size: Number(result?.size || file.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || file.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        });
      } catch (error) {
        releaseAttachmentPreview(previewUrl);
        if (historyScope() === scope) controller.updateAttachment(attachmentId, previous);
        throw error;
      }
    };
    const openComposerAttachmentEditor = async context => {
      try {
        await requireImageEditor();
        const attachment = context?.attachment || {};
        context.open?.({
          editable: true,
          name: attachment.name || context.title || "annotated-image",
          meta: { attachmentId: context.attachmentId || "" },
          onSave: ({ file, previewUrl }) => replaceEditedAttachment(context, file, previewUrl),
        });
      } catch (error) {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
      }
    };
    const insertEditedMessageImage = async ({ file }) => {
      if (!file) return;
      await controller.addFiles([file]);
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
    const attachmentPreviewOptions = () => ({
      renderMentions: options.renderMentions,
      attachmentById: id => state.attachments.find(item => String(item?.id || "") === String(id || "")) || null,
      onImagePreview: context => openComposerAttachmentEditor({ ...context, workspace: controller }),
    });
    const bindDefaultMessageActions = () => {
      if (!els.messages || els.messages.dataset.portalMessageActionsBound === "1") return;
      els.messages.dataset.portalMessageActionsBound = "1";
      const messageMenuPopovers = new WeakMap();
      let openMessageMenu = null;
      let handleMessagesClick = null;
      const messageMenuPopover = menu => {
        if (!menu) return null;
        const existing = messageMenuPopovers.get(menu);
        if (existing) return existing;
        const messageItem = menu.closest(".messageItem");
        if (!messageItem || !window.PortalOverlay) return null;
        menu.addEventListener("click", event => {
          if (menu.parentNode !== document.body) return;
          event.stopPropagation();
          handleMessagesClick?.(event);
        });
        const popover = window.PortalOverlay.bindPopover({
          trigger: messageItem,
          panel: menu,
          placement: "bottom-end",
          closeOnEscape: true,
          closeOnOutside: true,
          closeOnTrigger: true,
          portal: true,
          onOpen: () => {
            openMessageMenu = menu;
            messageItem?.classList.add("menuOpen");
            options.onInteractionChange?.(true, menu);
          },
          onClose: () => {
            if (openMessageMenu === menu) openMessageMenu = null;
            messageItem?.classList.remove("menuOpen");
            options.onInteractionChange?.(false, menu);
          },
        });
        messageMenuPopovers.set(menu, popover);
        return popover;
      };
      const setMessageMenuOpen = (menu, open, context) => {
        if (!menu) return;
        bindMessageSubmenus(menu);
        closeMessageSubmenus(menu);
        const popover = messageMenuPopover(menu);
        if (popover) {
          if (open) popover.open(context);
          else popover.close();
          return;
        }
        menu.hidden = !open;
      };
      const closeMessageMenus = (except = null) => {
        if (openMessageMenu && openMessageMenu !== except) setMessageMenuOpen(openMessageMenu, false);
        els.messages.querySelectorAll("[data-portal-message-menu]").forEach(menu => {
          if (menu !== except) setMessageMenuOpen(menu, false);
        });
      };
      handleMessagesClick = event => {
        const workToggle = event.target.closest("[data-portal-work-group-toggle]");
        if (workToggle) {
          event.preventDefault();
          event.stopPropagation();
          const groupId = workToggle.dataset.portalWorkGroupToggle;
          if (state.collapsedWorkGroups.has(groupId)) state.collapsedWorkGroups.delete(groupId);
          else state.collapsedWorkGroups.add(groupId);
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions, runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive, runtimePhase: state.runtimePhase, force: true,
          });
          els.messages.querySelector(`[data-portal-work-group-toggle="${CSS.escape(groupId)}"]`)?.focus({ preventScroll: true });
          return;
        }
        const foldToggle = event.target.closest("[data-message-fold-toggle]");
        if (foldToggle) {
          event.preventDefault();
          event.stopPropagation();
          toggleMessageFold(foldToggle);
          return;
        }
        const groupToggle = event.target.closest("[data-portal-agent-group-toggle]");
        if (groupToggle) {
          event.preventDefault();
          event.stopPropagation();
          const groupId = String(groupToggle.dataset.portalAgentGroupToggle || "").trim();
          if (!groupId) return;
          if (state.messageRenderOptions?.autoCollapseAgentGroups === true) {
            if (state.expandedAgentGroups.has(groupId)) state.expandedAgentGroups.delete(groupId);
            else state.expandedAgentGroups.add(groupId);
          } else if (state.collapsedAgentGroups.has(groupId)) state.collapsedAgentGroups.delete(groupId);
          else state.collapsedAgentGroups.add(groupId);
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
            force: true,
          });
          return;
        }
        const openLinkButton = event.target.closest("[data-portal-message-open-link]");
        if (openLinkButton) {
          event.preventDefault();
          setMessageMenuOpen(openLinkButton.closest("[data-portal-message-menu]"), false);
          window.open(openLinkButton.dataset.portalMessageOpenLink, "_blank", window.PortalI18n.t("noopener,noreferrer"));
          return;
        }
        const actionButton = event.target.closest("[data-portal-message-action]");
        if (actionButton) {
          const menu = actionButton.closest("[data-portal-message-menu]");
          const article = actionButton.closest("[data-message-key]");
          const key = String(menu?.dataset.portalMessageKey || article?.dataset.messageAfterOf || article?.dataset.messageKey || "");
          const item = state.messageByKey.get(key);
          const action = String(actionButton.dataset.portalMessageAction || "");
          setMessageMenuOpen(menu, false);
          if (action === "resident-handoff-ask") {
            const card = residentHandoffCards(actionButton.dataset.handoffCard)[0];
            if (!card?.ask_message || actionButton.disabled || !options.onSend || state.composer.can_send === false) return;
            actionButton.disabled = true;
            // This explicit action owns its payload; an unfinished draft remains in the composer.
            Promise.resolve(options.onSend({ text: `替我问一下 ${card.title}（${card.resident_id}）：${card.ask_message}`, refs: [], attachments: [] }, controller,
              { clear: false, focus: false, quote: false, includeContext: false, clearRefsOnSuccess: false }))
              .catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"))
              .finally(() => { actionButton.disabled = false; });
            return;
          }
          const handler = state.messageRenderOptions?.onMessageAction || options.onMessageAction;
          if (handler && item && action) {
            Promise.resolve(handler({ action, key, item, button: actionButton, workspace: controller }))
              .catch(error => options.onError?.(error));
          }
          return;
        }
        const copyButton = event.target.closest("[data-portal-message-copy]");
        if (!copyButton) return;
        const menu = copyButton.closest("[data-portal-message-menu]");
        const key = String(menu?.dataset.portalMessageKey || "");
        const item = state.messageByKey.get(key);
        if (!item) return;
        let items = [item];
        if (copyButton.dataset.portalMessageCopyScope === "replies") {
          if (messageRole(item) !== "agent") return;
          const index = state.messages.findIndex(message => messageRenderKey(message) === key);
          const group = consecutiveAgentGroups(state.messages).get(index);
          if (group) items = state.messages.slice(group.start, group.end + 1);
        }
        const format = copyButton.dataset.portalMessageCopy === "markdown" ? "markdown" : "text";
        const valueResolver = state.messageRenderOptions?.messageCopyValue || options.messageCopyValue;
        Promise.all(items.map(item => {
          const key = messageRenderKey(item);
          const article = els.messages.querySelector(`[data-message-key="${CSS.escape(key)}"]`);
          const raw = String(item?.content || "");
          return valueResolver
            ? valueResolver({ format, item, key, article, workspace: controller })
            : (format === "markdown" ? raw : messageCopyText(article, raw));
        }))
          .then(values => window.PortalUI.copyText(values.map(value => String(value ?? "")).join("\n\n"), window.PortalI18n.t("消息")))
          .catch(error => options.onError?.(error));
        setMessageMenuOpen(menu, false);
      };
      els.messages.addEventListener("click", handleMessagesClick);
      els.messages.addEventListener("contextmenu", event => {
        if (event.target.closest("input, textarea, select") || event.target.isContentEditable) return;
        // The reading pane owns its context menu, including blank space and
        // status blocks. Only an actual message may supply message actions.
        event.preventDefault();
        event.stopPropagation();
        let article = event.target.closest(".messageItem[data-message-key]");
        const list = event.target;
        if (!article && list.matches(".messageList") && list.parentElement === els.messages) {
          // The flex gap after a message belongs to that message, up to the
          // next visible message. Do not claim side gutters, history controls,
          // runtime cards, or the empty space before/after the conversation.
          let previous = null;
          for (const row of list.children) {
            if (!row.getClientRects().length) continue;
            const bounds = row.getBoundingClientRect();
            if (bounds.top > event.clientY) {
              if (previous?.matches(".messageItem[data-message-key]")
                && row.matches(".messageItem[data-message-key]")
                && state.messageByKey.has(row.dataset.messageKey)) {
                const before = previous.getBoundingClientRect();
                if (event.clientY >= before.bottom && event.clientX >= before.left && event.clientX <= before.right) article = previous;
              }
              break;
            }
            previous = row;
          }
        }
        const key = article && els.messages.contains(article) ? article.dataset.messageKey : "";
        const menu = key && state.messageByKey.has(key)
          ? article.querySelector("[data-portal-message-menu]")
            || (openMessageMenu?.dataset.portalMessageKey === key ? openMessageMenu : null)
          : null;
        if (!menu) {
          closeMessageMenus();
          return;
        }
        menu.querySelector("[data-portal-message-open-link]")?.remove();
        const link = event.target.closest("a[href]");
        if (link && article.contains(link) && ["http:", "https:"].includes(link.protocol)) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.portalMessageOpenLink = link.href;
          button.textContent = window.PortalI18n.t("打开链接");
          button.title = window.PortalI18n.t("在新标签页打开这个网页链接");
          menu.insertBefore(button, menu.querySelector("[data-portal-message-copy]"));
        }
        closeMessageMenus(menu);
        setMessageMenuOpen(menu, true, { point: { x: event.clientX, y: event.clientY } });
      });
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
    const draftStore = options.draftStore || createDraftStore({
      namespace: String(options.draftNamespace || id),
      owner: options.draftOwner || "anonymous",
      delay: options.draftSaveDelay || 180,
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
    let draftPersistenceSuspension = null;
    let draftBaseline = null;
    const renderAttachments = (renderOptions = {}) => {
      const selection = document.activeElement === els.input
        ? composerSelectionSnapshot(els.input)
        : null;
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      syncComposerAttachmentRefs(els.input, state.attachments, attachmentPreviewOptions());
      if (selection) restoreComposerSelection(els.input, selection);
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
    };
    const renderAttachmentTrayOnly = (renderOptions = {}) => {
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
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
      window.cancelAnimationFrame(windowResizeFollowFrame);
      if (!state.messageTailFollowIntent) return;
      windowResizeFollowFrame = window.requestAnimationFrame(() => {
        windowResizeFollowFrame = 0;
        if (state.messageTailFollowIntent && messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      });
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
    let suppressAttachmentReconcile = false;
    const fileSignature = (file) => {
      const type = String(file?.type || "").toLowerCase();
      const name = String(file?.name || "").trim();
      const size = Number(file?.size || 0);
      const kind = type.startsWith("image/") ? "image" : "file";
      return kind === "image"
        ? `image:${type}:${size}:${name}`
        : `file:${name}:${type}:${size}:${file?.lastModified || 0}`;
    };
    const attachmentUploadContext = () => {
      const configured = typeof options.attachmentUploadContext === "function"
        ? options.attachmentUploadContext(controller)
        : options.attachmentUploadContext;
      const contractContext = state.composer.attachment_upload_context;
      return {
        ...(configured && typeof configured === "object" ? configured : {}),
        ...(contractContext && typeof contractContext === "object" ? contractContext : {}),
      };
    };
    const composerSnapshot = () => ({
      inputValue: els.input && "value" in els.input ? String(els.input.value || "") : "",
      inputHtml: els.input && !("value" in els.input) ? String(els.input.innerHTML || "") : "",
      plainText: serializeComposerInput(els.input, state.attachments, { mode: "plain" }),
      selection: composerSelectionSnapshot(els.input),
      attachments: state.attachments.map(item => ({ ...item })),
    });
    const composerMutationSignature = () => JSON.stringify({
      draftText: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
      attachmentIds: state.attachments.map(item => String(item?.id || "")),
      refs: refsPayload(state.refs),
    });
    const pushComposerUndoSnapshot = () => {
      if (!els.input) return null;
      const snapshot = composerSnapshot();
      const previous = state.undoStack[state.undoStack.length - 1];
      const signature = JSON.stringify({
        inputValue: snapshot.inputValue,
        inputHtml: snapshot.inputHtml,
        attachments: snapshot.attachments.map(item => [item.id, item.path, item.preview_url, item.uploading, item.error]),
      });
      if (previous?.signature === signature) return previous;
      const frame = { ...snapshot, signature, afterSignature: "" };
      state.undoStack.push(frame);
      state.undoStack = state.undoStack.slice(-20);
      return frame;
    };
    const dropComposerUndoSnapshot = (frame) => {
      if (!frame) return;
      if (state.undoStack[state.undoStack.length - 1] === frame) {
        state.undoStack.pop();
        return;
      }
      state.undoStack = state.undoStack.filter(item => item !== frame);
    };
    const markComposerUndoAfterSnapshot = (frame) => {
      if (!frame || !els.input) return;
      frame.afterSignature = composerMutationSignature();
    };
    const restoreComposerSnapshot = (snapshot) => {
      if (!snapshot || !els.input) return false;
      suppressAttachmentReconcile = true;
      try {
        state.attachments = normalizeComposerAttachments(snapshot.attachments || []);
        if ("value" in els.input) els.input.value = String(snapshot.inputValue || "");
        else els.input.innerHTML = String(snapshot.inputHtml || "");
        restoreComposerSelection(els.input, snapshot.selection);
        renderAttachmentTrayOnly({ notify: true });
        bindImagePreview(els.input, attachmentPreviewOptions());
        updateControls();
        options.onInput?.(controller.payload(), controller);
        return true;
      } finally {
        suppressAttachmentReconcile = false;
      }
    };
    const captureWorkspaceDraft = () => {
      const draft = {
        text: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
        attachments: persistedDraftAttachments(state.attachments),
        refs: refsPayload(state.refs),
        selection: composerSelectionSnapshot(els.input),
        input_height: state.composerInputHeight,
      };
      if (state.recoveredSubmission?.content !== draftContentSignature(draft)) {
        state.recoveredSubmission = null;
      }
      return { ...draft, submitted_message_id: state.recoveredSubmission?.messageId || "" };
    };
    const draftContentSignature = draft => JSON.stringify({
      text: draft.text,
      attachments: draft.attachments,
      refs: draft.refs,
    });
    const draftSignatures = draft => ({
      content: draftContentSignature(draft),
      submission: draft.submitted_message_id || "",
      view: JSON.stringify({ selection: draft.selection, input_height: draft.input_height }),
    });
    const saveActiveDraft = ({ immediate = false } = {}) => {
      if (state.restoringDraft || !state.activeDraftScope) return;
      const draft = captureWorkspaceDraft();
      const signature = draftSignatures(draft);
      // Loading, hiding and destroying an untouched composition are not edits.
      // Selection/layout changes may only update the draft they belong to.
      if (
        signature.content !== draftBaseline?.content
        || signature.submission !== draftBaseline?.submission
        || (signature.view !== draftBaseline?.view
          && signature.content === draftContentSignature(draftStore.read(state.activeDraftScope)))
      ) {
        draftStore.schedule(state.activeDraftScope, draft);
      }
      draftBaseline = signature;
      if (immediate) draftStore.flush(state.activeDraftScope);
    };
    const saveDraftBeforePageLeave = () => saveActiveDraft({ immediate: true });
    const saveDraftWhenHidden = () => {
      if (document.visibilityState === "hidden") saveDraftBeforePageLeave();
    };
    const restoreDraftScope = (scope, { force = false, focus = document.activeElement === els.input } = {}) => {
      const nextScope = String(scope || "").trim();
      if (!nextScope || !els.input) return false;
      if (!force && state.activeDraftScope === nextScope) return false;
      closeMentionMenu();
      if (state.activeDraftScope && state.activeDraftScope !== nextScope) saveActiveDraft({ immediate: true });
      state.activeDraftScope = nextScope;
      const draft = draftStore.read(nextScope);
      state.recoveredSubmission = draft.submitted_message_id
        ? { messageId: draft.submitted_message_id, content: draftContentSignature(draft) }
        : null;
      state.restoringDraft = true;
      suppressAttachmentReconcile = true;
      try {
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
      } finally {
        suppressAttachmentReconcile = false;
        state.restoringDraft = false;
      }
      draftBaseline = draftSignatures(captureWorkspaceDraft());
      return true;
    };
    const clearActiveDraft = () => {
      if (state.activeDraftScope) draftStore.clear(state.activeDraftScope);
      draftBaseline = draftSignatures(captureWorkspaceDraft());
    };
    const clearComposerInput = ({ notify = true, focus = true } = {}) => {
      const restoring = state.restoringDraft;
      state.restoringDraft = true;
      state.recoveredSubmission = null;
      try {
        setInputText(els.input, "", { focus });
        releaseAttachmentPreviews(state.attachments);
        state.attachments = [];
        state.undoStack = [];
        renderAttachments({ notify });
        closeSlashCommandMenu();
        closeMentionMenu();
      } finally {
        state.restoringDraft = restoring;
      }
    };
    const deleteUploadedAttachment = item => {
      if (!String(item?.upload_id || "").trim()) return Promise.resolve({ deleted: false });
      const remove = options.deleteAttachment || defaultWorkspaceDeleteAttachment;
      return Promise.resolve(remove(item, attachmentUploadContext())).catch(error => {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
        return { deleted: false, error };
      });
    };
    const discardComposerAttachments = items => {
      const removed = Array.isArray(items) ? items.filter(Boolean) : [];
      releaseAttachmentPreviews(removed);
      removed.forEach(item => { deleteUploadedAttachment(item); });
    };
    const undoComposerAttachmentInsert = (snapshot) => {
      const ids = new Set((snapshot?.attachmentIds || []).map(id => String(id || "")).filter(Boolean));
      if (!ids.size || !els.input) return false;
      let removed = false;
      for (const id of ids) {
        els.input.querySelectorAll(`[data-portal-composer-attachment-token="${CSS.escape(id)}"]`).forEach(node => {
          const next = node.nextSibling;
          const previous = node.previousSibling;
          if (next?.nodeType === Node.TEXT_NODE && !String(next.textContent || "").trim()) next.remove();
          else if (previous?.nodeType === Node.TEXT_NODE && !String(previous.textContent || "").trim()) previous.remove();
          node.remove();
          removed = true;
        });
      }
      if (!/\s$/.test(String(snapshot?.plainText || ""))) {
        let node = els.input.lastChild;
        while (node?.nodeType === Node.TEXT_NODE && !String(node.textContent || "").trim()) {
          const previous = node.previousSibling;
          node.remove();
          node = previous;
        }
        if (node?.nodeType === Node.TEXT_NODE) {
          node.textContent = String(node.textContent || "").replace(/\s+$/g, "");
        }
      }
      const removedItems = state.attachments.filter(item => ids.has(String(item?.id || "")));
      discardComposerAttachments(removedItems);
      state.attachments = state.attachments.filter(item => !ids.has(String(item?.id || "")));
      renderAttachmentTrayOnly({ notify: true });
      bindImagePreview(els.input, attachmentPreviewOptions());
      updateControls();
      if (removed) els.input.dispatchEvent?.(new Event("input", { bubbles: true }));
      return true;
    };
    const undoComposerMutation = () => {
      const snapshot = state.undoStack[state.undoStack.length - 1];
      if (!snapshot) return false;
      if (snapshot.afterSignature && composerMutationSignature() !== snapshot.afterSignature) return false;
      state.undoStack.pop();
      if (snapshot.kind === "attachment-insert") return undoComposerAttachmentInsert(snapshot);
      return restoreComposerSnapshot(snapshot);
    };
    const reconcileAttachmentsFromInput = (reconcileOptions = {}) => {
      if (suppressAttachmentReconcile || !els.input || "value" in els.input || !state.attachments.length) return false;
      const order = composerAttachmentTokenOrder(els.input);
      const present = new Set(order);
      const byId = new Map(state.attachments.map(item => [String(item?.id || ""), item]));
      const next = order.map(id => byId.get(id)).filter(Boolean);
      const currentIds = state.attachments.map(item => String(item?.id || ""));
      const nextIds = next.map(item => String(item?.id || ""));
      if (next.length === state.attachments.length && nextIds.every((id, idx) => id === currentIds[idx])) return false;
      discardComposerAttachments(state.attachments.filter(item => !present.has(String(item?.id || ""))));
      state.attachments = normalizeComposerAttachments(next);
      renderAttachmentTrayOnly({ notify: reconcileOptions.notify });
      return true;
    };
    const uploadComposerAttachment = async attachmentId => {
      const target = state.attachments.find(item => String(item.id || "") === String(attachmentId || ""));
      if (!target?.file) throw new Error("原始附件已不可用，请重新选择文件。");
      state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
        ...item,
        uploading: true,
        progress: 0,
        error: "",
      } : item));
      renderAttachments({ notify: true });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(target.file, {
          ...attachmentUploadContext(),
          item: target,
          workspace: controller,
        });
        if (!state.attachments.some(item => String(item?.id || "") === String(target.id || ""))) {
          await deleteUploadedAttachment({ ...target, ...result, kind: target.kind });
          return;
        }
        if (result?.preview_url && result.preview_url !== target.preview_url) {
          releaseAttachmentPreview(target.preview_url);
        }
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          name: result?.filename || item.name,
          path: result?.path || item.path || "",
          preview_url: result?.preview_url || item.preview_url || "",
          data_url: result?.data_url || item.data_url || "",
          mime: result?.mime || result?.content_type || item.mime || item.content_type || "",
          content_type: result?.content_type || item.content_type || "",
          size: Number(result?.size || item.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || item.size_bytes || item.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        } : item));
      } catch (error) {
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          uploading: false,
          error: error.message || String(error || "上传失败"),
        } : item));
        throw error;
      } finally {
        renderAttachments({ notify: true });
      }
    };
    const addFiles = async (files) => {
      const now = Date.now();
      const seen = new Set();
      const list = Array.from(files || []).filter(file => {
        if (!file) return false;
        const signature = fileSignature(file);
        if (seen.has(signature)) return false;
        seen.add(signature);
        const previous = Number(state.recentFileSignatures.get(signature) || 0);
        if (previous && now - previous < 900) return false;
        state.recentFileSignatures.set(signature, now);
        return true;
      });
      state.recentFileSignatures.forEach((timestamp, signature) => {
        if (now - Number(timestamp || 0) > 3000) state.recentFileSignatures.delete(signature);
      });
      if (!list.length) return;
      const maxFileSize = Number(options.maxFileSize || 512 * 1024 * 1024);
      const tooLarge = list.find(file => Number(file.size || 0) > maxFileSize);
      if (tooLarge) throw new Error(`文件过大：${tooLarge.name || "attachment"}，上限 ${Math.round(maxFileSize / 1024 / 1024)} MB`);
      const undoFrame = pushComposerUndoSnapshot();
      const drafts = list.map(file => {
        const kind = String(file.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
        const previewUrl = kind === "image" ? URL.createObjectURL(file) : "";
        if (previewUrl) ownedAttachmentPreviews.add(previewUrl);
        return {
          id: `uploading:${Date.now()}:${Math.random().toString(16).slice(2)}`,
          kind,
          name: file.name || (kind === "image" ? window.PortalI18n.t("pasted image") : "attachment"),
          content_type: file.type || "",
          size: Number(file.size || 0),
          preview_url: previewUrl,
          path: "",
          uploading: true,
          progress: 0,
          error: "",
          file,
        };
      });
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = drafts.map(item => item.id);
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, ...drafts]);
      suppressAttachmentReconcile = true;
      try {
        drafts.forEach(item => insertComposerAttachmentRef(els.input, item, { forceDom: true }));
      } finally {
        suppressAttachmentReconcile = false;
      }
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      for (const draft of drafts) {
        try {
          await uploadComposerAttachment(draft.id);
        } catch {}
      }
    };
    const addReference = (reference) => {
      const item = reference?.content_type === "text/x-portal-preview-reference"
        ? normalizeComposerAttachment(reference)
        : createConversationSelectionReference(reference);
      if (!item || !composerPreviewReference(item)) return null;
      const undoFrame = pushComposerUndoSnapshot();
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = [item.id];
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, item]);
      suppressAttachmentReconcile = true;
      try {
        insertComposerAttachmentRef(els.input, item, { forceDom: true });
      } finally {
        suppressAttachmentReconcile = false;
      }
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      saveActiveDraft();
      return item;
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
      setAttachments(items) {
        state.attachments = normalizeComposerAttachments(items);
        renderAttachments({ notify: true });
      },
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
      updateAttachment(id, patch = {}) {
        const target = String(id || "");
        if (!target) return;
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => String(item?.id || "") === target ? { ...item, ...patch } : item));
        renderAttachments({ notify: true });
      },
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
        else draftBaseline = draftSignatures(captureWorkspaceDraft());
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
        suppressAttachmentReconcile = true;
        try {
          if (attachments !== null) state.attachments = normalizeComposerAttachments(attachments);
          setComposerDraftInput(els.input, value || "", state.attachments, attachmentPreviewOptions());
          renderAttachments({ notify: true });
          refreshSlashCommandMenu();
        } finally {
          suppressAttachmentReconcile = false;
        }
        updateControls();
        saveActiveDraft();
      },
      draftScope() {
        return state.activeDraftScope || historyScope();
      },
      suspendDraftPersistence() {
        if (
          typeof draftStore.suspend !== "function"
          || typeof draftStore.resume !== "function"
        ) {
          throw new Error("Chat Workspace draft store cannot enter ephemeral mode");
        }
        if (draftPersistenceSuspension) {
          draftPersistenceSuspension.depth += 1;
          draftStore.suspend();
          return false;
        }
        const activeDraftScope = state.activeDraftScope || historyScope();
        const inputSnapshot = composerSnapshot();
        const draftSnapshot = captureWorkspaceDraft();
        draftPersistenceSuspension = {
          depth: 1,
          snapshot: {
            activeDraftScope,
            recoveredSubmission: state.recoveredSubmission,
            draftBaseline,
            composer: {
              ...state.composer,
              capabilities: Array.isArray(state.composer.capabilities)
                ? [...state.composer.capabilities]
                : [],
            },
            composerImeActive: state.composerImeActive,
            composerInputFocused: state.composerInputFocused,
            composerInputHeight: state.composerInputHeight,
            composerQueueSignature: state.composerQueueSignature,
            composerSignature: state.composerSignature,
            inputFocused: document.activeElement === els.input,
            inputSnapshot,
            refs: normalizeRefs(state.refs),
            refsCollapsed: { ...(state.refsCollapsed || {}) },
            undoStack: state.undoStack.slice(),
          },
        };
        draftStore.suspend([[activeDraftScope, draftSnapshot]]);
        return true;
      },
      resumeDraftPersistence() {
        const suspension = draftPersistenceSuspension;
        if (!suspension) return false;
        if (suspension.depth > 1) {
          suspension.depth -= 1;
          draftStore.resume();
          return false;
        }
        const snapshot = suspension.snapshot;
        try {
          const restoredComposer = {
            ...snapshot.composer,
            capabilities: Array.isArray(snapshot.composer?.capabilities)
              ? [...snapshot.composer.capabilities]
              : [],
          };
          renderComposerNow(restoredComposer, snapshot.composerSignature);
          state.activeDraftScope = snapshot.activeDraftScope;
          draftBaseline = snapshot.draftBaseline;
          state.recoveredSubmission = snapshot.recoveredSubmission;
          state.attachments = normalizeComposerAttachments(
            snapshot.inputSnapshot.attachments || [],
          );
          state.refs = normalizeRefs(snapshot.refs);
          state.refsCollapsed = { ...(snapshot.refsCollapsed || {}) };
          state.composerInputHeight = snapshot.composerInputHeight;
          state.composerQueueSignature = snapshot.composerQueueSignature;
          state.undoStack = snapshot.undoStack.slice();
          state.composerInputFocused = snapshot.composerInputFocused;
          state.composerImeActive = snapshot.composerImeActive;
          suppressAttachmentReconcile = true;
          try {
            if (els.input && "value" in els.input) {
              els.input.value = String(snapshot.inputSnapshot.inputValue || "");
            } else if (els.input) {
              els.input.innerHTML = String(snapshot.inputSnapshot.inputHtml || "");
            }
            renderAttachmentTrayOnly({ notify: false });
            controller.refreshRefs();
            applyComposerInputLayout();
            updateControls();
            if (snapshot.inputFocused) {
              els.input?.focus?.({ preventScroll: true });
              restoreComposerSelection(els.input, snapshot.inputSnapshot.selection);
            }
          } finally {
            suppressAttachmentReconcile = false;
          }
        } finally {
          draftPersistenceSuspension = null;
          draftStore.resume();
        }
        return true;
      },
      draftPersistenceSuspended() {
        return !!draftPersistenceSuspension;
      },
      saveDraft(saveOptions = {}) {
        saveActiveDraft({ immediate: saveOptions.immediate !== false });
      },
      restoreDraft(scope = historyScope(), restoreOptions = {}) {
        return restoreDraftScope(scope, { force: restoreOptions.force !== false });
      },
      clearDraft(scope = state.activeDraftScope || historyScope(), expected = null) {
        if (expected && draftContentSignature(draftStore.read(scope)) !== draftContentSignature(normalizeWorkspaceDraft(expected))) return false;
        draftStore.clear(scope);
        if (scope === state.activeDraftScope) draftBaseline = draftSignatures(captureWorkspaceDraft());
        return true;
      },
      confirmSubmittedMessages(messageIds, scope = state.activeDraftScope || historyScope()) {
        const accepted = new Set((messageIds || []).filter(Boolean));
        if (!accepted.size) return;
        const storedDraft = draftStore.read(scope);
        if (accepted.has(storedDraft.submitted_message_id)) draftStore.clear(scope);
        if (state.activeDraftScope !== scope) return;
        const draft = captureWorkspaceDraft();
        if (!accepted.has(draft.submitted_message_id)) return;
        // Clear only this unedited recovery, leaving a newer shared draft intact.
        clearComposerInput({ focus: false });
        state.refs = normalizeRefs({});
        controller.refreshRefs();
        draftBaseline = draftSignatures(captureWorkspaceDraft());
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
            state.recoveredSubmission = {
              messageId: context.messageIdentity,
              content: draftContentSignature(captureWorkspaceDraft()),
            };
            saveActiveDraft({ immediate: true });
          } else if (!committed && !optimistic && submitOptions.clear !== false && !sameScope) {
            if (!workspaceDraftHasContent(draftStore.read(scope))) {
              draftStore.write(scope, { ...draftSnapshot, submitted_message_id: context.messageIdentity });
            }
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
        const role = messageRole(message);
        let start = previous.length - 1;
        while (start > 0 && messageRole(previous[start - 1]) === messageRole(previous[start])) start -= 1;
        const first = list.querySelector(messageInstanceSelector(messageInstanceKey(previous[start])));
        if (!first) return false;
        const prefixNode = first.previousElementSibling;
        const wasFollowing = state.messageTailFollowIntent;
        const oldLatest = state.latestFoldReplyKey;
        state.messages = [...previous, message];
        state.contract = { ...state.contract, messages: state.messages };
        syncHistory();
        state.messageItemSignatures.push(JSON.stringify(message));
        state.messageByKey.set(messageIdentity(message), message);
        const key = messageRenderKey(message);
        if (message.pending || message.live_output || (state.runtimeLive && role === "agent")) {
          state.messageFoldDefaults.add(key);
        }
        if (messageFoldReply(message)) {
          const at = Date.parse(String(message.created_at || ""));
          if (!(at < state.latestFoldReplyAt)) {
            state.latestFoldReplyKey = key;
            if (Number.isFinite(at)) state.latestFoldReplyAt = at;
            state.messageFoldDefaults.add(key);
            if (oldLatest && oldLatest !== key && !state.runtimeLive) {
              const oldMessage = state.messageByKey.get(oldLatest);
              if (!oldMessage?.pending && !oldMessage?.live_output) {
                state.messageFoldDefaults.delete(oldLatest);
                if (!messageFoldChoices.has(oldLatest)) {
                  const oldNode = list.querySelector(`[data-message-key="${CSS.escape(oldLatest)}"]`);
                  applyMessageFold(oldNode?.querySelector("[data-message-fold-toggle]"), false);
                }
              }
            }
          }
        }
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
        const replacedKeys = previous.slice(start).flatMap(item => [messageInstanceKey(item), messageAfterKey(messageInstanceKey(item))]);
        replacedKeys.push("__runtime_phase__", "__subagents__", "__runtime__", "__scroll_anchor__");
        const changed = reconcileMessageList(els.messages, html, {
          currentStartNode: first,
          prefixNode,
          replacedKeys,
        })?.changedRoots || [];
        bindDefaultMessageActions();
        for (const root of changed) {
          bindAttachmentRefs(root, {
            previewUrlForPath: renderOptions.previewUrlForPath || options.previewUrlForPath,
            onOpen: (kind, path, node) => {
              const attachmentKind = String(kind || "") === "file" && window.PortalFileLinks?.kindForPath(path) !== "image" ? "file" : "image";
              const attachmentPath = String(path || "").trim();
              if (attachmentKind === "file") {
                const descriptor = window.PortalFileLinks?.describe(attachmentPath) || { path: attachmentPath };
                const request = { ...descriptor, href: descriptor.path, title: node?.textContent?.trim() || attachmentPath, node, source: "chat", target: els.messages };
                const open = renderOptions.onLinkPreview || options.onLinkPreview || requestWorkspacePreview;
                Promise.resolve().then(() => open(request)).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"));
                return;
              }
              const previewUrl = attachmentKind === "image" && (renderOptions.previewUrlForPath || options.previewUrlForPath)
                ? (renderOptions.previewUrlForPath || options.previewUrlForPath)(attachmentPath) : "";
              if (previewUrl) openMessageImageEditor(previewUrl, attachmentPath);
              else void window.PortalUI.copyText(attachmentPath, window.PortalI18n.t("附件路径"));
            },
            onImagePreview: context => openMessageImageEditor(context?.src, context?.title),
          });
          bindResponseSourcePaths(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          bindResourceChips(root, { onResourcePreview: renderOptions.onResourcePreview || options.onResourcePreview });
          bindMessageAvatars(root);
          if (options.enableLinkPreview || renderOptions.enableLinkPreview) {
            bindLinkPreview(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          }
        }
        measureMessageFolds(changed);
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        options.onMessagesRendered?.(els.messages, controller, true, changed);
        syncMessageScrollMode();
        if (wasFollowing && messageViewportMeasurable()) els.messages.scrollTop = els.messages.scrollHeight;
        return true;
      },
      setMessages(messages, renderOptions = {}) {
        const previousMessages = state.messages;
        const previousRuntimeSignature = JSON.stringify({
          details: state.runtimeDetails,
          live: state.runtimeLive,
          phase: state.runtimePhase,
        });
        const previousScrollHeight = Number(els.messages?.scrollHeight || 0);
        const previousScrollTop = Number(els.messages?.scrollTop || 0);
        const previousItemSignatures = state.messageItemSignatures;
        const previousRenderContextSignature = state.messageRenderContextSignature;
        const previousRendererRefs = state.messageRendererRefs;
        const previousDraftScope = String(state.activeDraftScope || "");
        const nextScope = String(historyScope() || "");
        const scopeChanged = state.messageScrollScope === null || state.messageScrollScope !== nextScope;
        state.messageScrollScope = nextScope;
        if (nextScope && previousDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        state.messages = Array.isArray(messages) ? messages : [];
        state.runtimeDetails = Array.isArray(renderOptions.runtimeDetails) ? renderOptions.runtimeDetails : [];
        state.runtimeLive = !!renderOptions.runtimeLive;
        state.runtimePhase = String(renderOptions.runtimePhase || "");
        const nextRuntimeSignature = JSON.stringify({
          details: state.runtimeDetails,
          live: state.runtimeLive,
          phase: state.runtimePhase,
        });
        state.messageRenderOptions = { ...renderOptions };
        syncHistory(nextScope);
        const foldChanges = updateMessageFoldDefaults();
        const nextItemSignatures = state.messages.map(item => JSON.stringify(item));
        const nextRendererRefs = [
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
        ];
        const nextRenderContextSignature = JSON.stringify({
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
        });
        const nextRenderSignature = JSON.stringify({
          emptyText: renderOptions.emptyText || options.emptyText || "",
          userName: renderOptions.userName || options.userName || "",
          assistantName: renderOptions.assistantName || options.assistantName || "",
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: [...state.collapsedAgentGroups].sort(),
          collapsedWorkGroups: [...state.collapsedWorkGroups].sort(),
          expandedAgentGroups: [...state.expandedAgentGroups].sort(),
        });
        const messageItemsUnchanged = previousItemSignatures.length === nextItemSignatures.length
          && previousItemSignatures.every(
            (signature, index) => signature === nextItemSignatures[index],
          );
        const rendererRefsUnchanged = previousRendererRefs.length === nextRendererRefs.length
          && nextRendererRefs.every((ref, index) => ref === previousRendererRefs[index]);
        conversationSearch?.update(!messageItemsUnchanged);
        if (
          nextRenderSignature === state.messageRenderSignature
          && messageItemsUnchanged
          && previousRenderContextSignature === nextRenderContextSignature
          && rendererRefsUnchanged
          && !renderOptions.force
          && !foldChanges.length
        ) {
          if (previousRuntimeSignature !== nextRuntimeSignature) {
            controller.setRuntime(state.runtimeDetails, state.runtimeLive, renderOptions);
          }
          return;
        }
        state.messageRenderSignature = nextRenderSignature;
        const canReconcileSuffix = !renderOptions.force
          && !scopeChanged
          && previousMessages.length > 0
          && state.messages.length > 0
          && previousRenderContextSignature === nextRenderContextSignature
          && rendererRefsUnchanged
          && els.messages.firstElementChild?.classList.contains("messageList")
          && previousItemSignatures.length === previousMessages.length;
        const prependedCount = state.messages.length - previousMessages.length;
        const prependOnly = !renderOptions.force
          && !scopeChanged
          && prependedCount > 0
          && previousItemSignatures.length === previousMessages.length
          && previousItemSignatures.every(
            (signature, index) => signature === nextItemSignatures[index + prependedCount],
          );
        state.messageItemSignatures = nextItemSignatures;
        state.messageRenderContextSignature = nextRenderContextSignature;
        state.messageRendererRefs = nextRendererRefs;
        const renderedStream = normalizeMessageStream(state.messages, { ...options, ...renderOptions });
        state.messageByKey = new Map(renderedStream.messages.map((item) => [
          messageIdentity(item),
          item,
        ]));
        const followTail = scopeChanged || state.messageTailFollowIntent;
        state.messageTailFollowIntent = followTail;
        if (scopeChanged) {
          messageScrollGesturePending = false;
          messageScrollPointerActive = false;
          messageScrollTouchActive = false;
        }
        syncMessageScrollMode();
        let renderStartIndex = 0;
        let renderEndIndex = state.messages.length;
        const canReconcilePrepend = canReconcileSuffix && prependOnly
          && previousRuntimeSignature === nextRuntimeSignature;
        if (canReconcilePrepend) {
          renderEndIndex = prependedCount;
          // Loading the first history page introduces a boundary on the existing facade message.
          if (state.messages[prependedCount - 1]?.family_history && !state.messages[prependedCount]?.family_history) {
            renderEndIndex = Math.min(state.messages.length, prependedCount + 1);
          }
          // Older pages may move a work's header past intervening requests.
          const nextWorkGroups = familyWorkGroups(state.messages);
          for (const [id, work] of familyWorkGroups(previousMessages)) {
            if (nextWorkGroups.get(id)?.headKey === work.headKey) continue;
            const oldHeadIndex = state.messages.findIndex(item => messageRenderKey(item) === work.headKey);
            renderEndIndex = Math.max(renderEndIndex, oldHeadIndex + 1);
          }
          for (const key of foldChanges) {
            const index = state.messages.findIndex(item => messageRenderKey(item) === key);
            renderEndIndex = Math.max(renderEndIndex, index + 1);
          }
          // A prepend may join the first retained reply group or change the
          // compact headers of its first human burst. Stop at a role boundary.
          while (renderEndIndex < state.messages.length
            && messageRole(state.messages[renderEndIndex]) === messageRole(state.messages[renderEndIndex - 1])) {
            renderEndIndex += 1;
          }
        } else if (canReconcileSuffix) {
          const sharedLength = Math.min(previousItemSignatures.length, nextItemSignatures.length);
          while (renderStartIndex < sharedLength
            && previousItemSignatures[renderStartIndex] === nextItemSignatures[renderStartIndex]) {
            renderStartIndex += 1;
          }
          for (const key of foldChanges) {
            const index = state.messages.findIndex(item => messageRenderKey(item) === key);
            if (index >= 0) renderStartIndex = Math.min(renderStartIndex, index);
          }
          // Editing or removing a group's tail changes its preceding members'
          // disclosure state too. Preserve only the prefix outside that group.
          if (
            messageRole(state.messages[renderStartIndex]) === "agent"
            || messageRole(previousMessages[renderStartIndex]) === "agent"
            || renderStartIndex === state.messages.length
          ) {
            while (renderStartIndex > 0 && messageRole(state.messages[renderStartIndex - 1]) === "agent") {
              renderStartIndex -= 1;
            }
          }
        }
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
          renderStartIndex,
          renderEndIndex,
        });
        const partialRange = canReconcilePrepend || (canReconcileSuffix && renderStartIndex > 0);
        const reconciledRange = reconcileMessageList(els.messages, messageHtml, partialRange ? {
          prefixKey: renderStartIndex > 0 ? messageInstanceKey(state.messages[renderStartIndex - 1]) : "",
          replacedKeys: [
            ...previousMessages.slice(renderStartIndex, canReconcilePrepend ? renderEndIndex - prependedCount : undefined)
              .flatMap(item => [messageInstanceKey(item), messageAfterKey(messageInstanceKey(item))]),
            ...(canReconcilePrepend ? ["__history__", "__inheritance__"] : ["__runtime_phase__", "__subagents__", "__runtime__", "__scroll_anchor__"]),
          ],
        } : {});
        bindDefaultMessageActions();
        const previewUrlForPath = renderOptions.previewUrlForPath || options.previewUrlForPath;
        // Only new/replaced message subtrees need binding and measurement.
        const bindingRoots = reconciledRange ? reconciledRange.changedRoots : [els.messages];
        for (const bindingRoot of bindingRoots) {
          bindAttachmentRefs(bindingRoot, {
            previewUrlForPath,
            onOpen: (kind, path, node) => {
              const attachmentKind = String(kind || "") === "file" && window.PortalFileLinks?.kindForPath(path) !== "image" ? "file" : "image";
              const attachmentPath = String(path || "").trim();
              if (attachmentKind === "file") {
                const descriptor = window.PortalFileLinks?.describe(attachmentPath) || { path: attachmentPath };
                const request = { ...descriptor, href: descriptor.path, title: node?.textContent?.trim() || attachmentPath, node, source: "chat", target: els.messages };
                const open = renderOptions.onLinkPreview || options.onLinkPreview || requestWorkspacePreview;
                Promise.resolve().then(() => open(request)).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"));
                return;
              }
              const previewUrl = attachmentKind === "image" && previewUrlForPath
                ? previewUrlForPath(attachmentPath)
                : "";
              if (previewUrl) {
                openMessageImageEditor(previewUrl, attachmentPath);
                return;
              }
              void window.PortalUI.copyText(attachmentPath, window.PortalI18n.t("附件路径"));
            },
            onImagePreview: context => openMessageImageEditor(context?.src, context?.title),
          });
          bindResponseSourcePaths(bindingRoot, {
            onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview,
          });
          bindResourceChips(bindingRoot, {
            onResourcePreview: renderOptions.onResourcePreview || options.onResourcePreview,
          });
          bindMessageAvatars(bindingRoot);
          if (options.enableLinkPreview || renderOptions.enableLinkPreview) {
            bindLinkPreview(bindingRoot, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          }
        }
        measureMessageFolds(bindingRoots);
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        options.onMessagesRendered?.(els.messages, controller);
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
        if (!list?.classList.contains("messageList")) {
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
          });
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
        const runtimeKeys = ["__runtime_phase__", "__subagents__", "__runtime__"];
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
        closeMentionMenu();
        window.cancelAnimationFrame(mentionRenderFrame);
        mentionRenderFrame = 0;
        restartUnobserve?.();
        restartProgress.destroy();
        saveActiveDraft({ immediate: true });
        ownedAttachmentPreviews.forEach(releaseAttachmentPreview);
        window.removeEventListener("pagehide", saveDraftBeforePageLeave);
        document.removeEventListener("visibilitychange", saveDraftWhenHidden);
        refsControlBinding?.destroy();
        refsControlBinding = null;
        els.messages?.removeEventListener("scroll", handleMessageViewportScroll);
        els.messages?.removeEventListener("wheel", expectMessageScrollFromGesture);
        els.messages?.removeEventListener("touchstart", beginMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchmove", beginMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchend", endMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchcancel", endMessageScrollTouchGesture);
        els.messages?.removeEventListener("pointerdown", handleMessageScrollPointerDown);
        els.messages?.removeEventListener("keydown", handleMessageScrollKeyDown);
        els.messages?.removeEventListener("load", measureLoadedMessageImage, true);
        messageFoldObserver?.disconnect();
        window.removeEventListener("pointerup", endMessageScrollPointerGesture);
        window.removeEventListener("pointercancel", endMessageScrollPointerGesture);
        root.removeEventListener("portal:workbench-before-layout", handleBeforeWorkbenchLayout);
        root.removeEventListener("portal:workbench-resize", handleWorkbenchResize);
        window.cancelAnimationFrame(workbenchViewportRestoreFrame);
        window.cancelAnimationFrame(workbenchViewportSettleFrame);
        window.cancelAnimationFrame(windowResizeFollowFrame);
        if (pendingWorkbenchViewport) {
          els.messages.style.overflowAnchor = pendingWorkbenchViewport.overflowAnchorStyle;
        }
        pendingWorkbenchViewport = null;
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
        onFiles: files => addFiles(files).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || error, "error")),
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
        onRetry: id => uploadComposerAttachment(id).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || error, "error")),
        onRemove: id => {
          discardComposerAttachments(state.attachments.filter(item => item.id === id));
          state.attachments = state.attachments.filter(item => item.id !== id);
          removeComposerAttachmentRef(els.input, id);
          renderAttachments({ notify: true });
          options.onInput?.(controller.payload(), controller);
        },
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
    window.addEventListener("pagehide", saveDraftBeforePageLeave);
    document.addEventListener("visibilitychange", saveDraftWhenHidden);
    return controller;
  }

  class BaseChatWorkspace {
    constructor(options = {}) {
      this.options = options || {};
      this.controller = null;
    }

    extensionOptions() {
      return {};
    }

    mount() {
      const extension = this.extensionOptions() || {};
      this.controller = createWorkspace({
        ...this.options,
        ...extension,
      });
      return this;
    }

    get root() { return this.controller?.root || null; }
    get els() { return this.controller?.els || {}; }
    get state() { return this.controller?.state || {}; }
    setLoading(message) { return this.controller?.setLoading?.(message); }
    clearLoading() { return this.controller?.clearLoading?.(); }
    isLoading() { return this.controller?.isLoading?.() === true; }
    element(name) { return this.els?.[String(name || "")] || null; }
    addFiles(files) { return this.controller?.addFiles?.(files); }
    addReference(reference) { return this.controller?.addReference?.(reference); }
    retryAttachment(id) { return this.controller?.retryAttachment?.(id); }
    attachments() { return this.controller?.attachments?.() || []; }
    messages() { return this.controller?.messages?.() || []; }
    setAttachments(items) { return this.controller?.setAttachments?.(items); }
    updateAttachment(id, patch) { return this.controller?.updateAttachment?.(id, patch); }
    refs() { return this.controller?.refs?.() || normalizeRefs({}); }
    setRefs(refs) { return this.controller?.setRefs?.(refs); }
    payload() { return this.controller?.payload?.() || { text: "", plainText: "", attachments: [], refs: normalizeRefs({}) }; }
    refreshRefs() { return this.controller?.refreshRefs?.(); }
    quote() { return this.controller?.quote?.() || null; }
    setQuote(quote) { return this.controller?.setQuote?.(quote) || false; }
    clearQuote(options) { return this.controller?.clearQuote?.(options) || false; }
    revealMessage(messageId) { return this.controller?.revealMessage?.(messageId) || Promise.resolve(false); }
    clear(options) { return this.controller?.clear?.(options); }
    inputText() { return this.controller?.inputText?.() || ""; }
    draftText() { return this.controller?.draftText?.() || ""; }
    setInputText(value) { return this.controller?.setInputText?.(value); }
    setDraftText(value, attachments) { return this.controller?.setDraftText?.(value, attachments); }
    draftScope() { return this.controller?.draftScope?.() || ""; }
    suspendDraftPersistence() { return this.controller?.suspendDraftPersistence?.() || false; }
    resumeDraftPersistence() { return this.controller?.resumeDraftPersistence?.() || false; }
    draftPersistenceSuspended() { return this.controller?.draftPersistenceSuspended?.() || false; }
    saveDraft(options) { return this.controller?.saveDraft?.(options); }
    restoreDraft(scope, options) { return this.controller?.restoreDraft?.(scope, options); }
    clearDraft(scope, expected) { return this.controller?.clearDraft?.(scope, expected); }
    confirmSubmittedMessages(messageIds, scope) { return this.controller?.confirmSubmittedMessages?.(messageIds, scope); }
    submit(send, options) { return this.controller?.submit?.(send, options); }
    setBusy(value) { return this.controller?.setBusy?.(value); }
    updateControls() { return this.controller?.updateControls?.(); }
    setStatus(text) { return this.controller?.setStatus?.(text); }
    followLatest() { return this.controller?.followLatest?.(); }
    setTitle(title) { return this.controller?.setTitle?.(title); }
    setSubtitle(subtitle, parts) { return this.controller?.setSubtitle?.(subtitle, parts); }
    setComposer(composer) { return this.controller?.setComposer?.(composer); }
    setStopPending(value) { return this.controller?.setStopPending?.(value); }
    setMessages(messages, renderOptions) { return this.controller?.setMessages?.(messages, renderOptions); }
    appendMessage(message) { return this.controller?.appendMessage?.(message) === true; }
    setRuntime(details, live, renderOptions) { return this.controller?.setRuntime?.(details, live, renderOptions); }
    contract() { return this.state.contract || null; }
    applyUpdate(update, renderOptions) { return this.controller?.applyUpdate?.(update, renderOptions); }
    applyContract(contract, renderOptions) { return this.controller?.applyContract?.(contract, renderOptions); }
    destroy() {
      this.controller?.destroy?.();
      this.controller = null;
    }
  }

  const residentChromeIdSuffixes = Object.freeze({
    menu: "Menu",
    extensionTools: "ExtensionTools",
    extensionButton: "ExtensionButton",
    extensionMenu: "ExtensionMenu",
    forwardCancel: "ForwardCancel",
    compact: "Compact",
    resetContext: "ResetContext",
    watchboardSplitter: "WatchboardSplitter",
    watchboard: "Watchboard",
    forwardDock: "ForwardDock",
    forwardCount: "ForwardCount",
    forward: "Forward",
  });

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

  function residentChromeIds(options = {}) {
    const workspaceId = String(options.id || "residentChat").trim() || "residentChat";
    return Object.freeze(Object.fromEntries(
      Object.entries(residentChromeIdSuffixes).map(([name, suffix]) => [name, `${workspaceId}${suffix}`]),
    ));
  }

  function residentSessionActions(options = {}) {
    const active = options.active !== false;
    const compact = active && (options.runtimeCapabilities?.operations || []).includes("compact");
    return {
      extensions: active,
      forward: active,
      compact,
      compactDisabled: compact && !!options.compactBusy,
      resetContext: !!options.resetContext,
      resetContextDisabled: !!options.resetContextDisabled,
    };
  }

  function residentChatChrome(options = {}) {
    const chromeIds = residentChromeIds(options);
    const features = options.residentFeatures && typeof options.residentFeatures === "object"
      ? options.residentFeatures
      : {};
    const optionalVisible = name => features[name] === true ? "" : " hidden";
    return {
      extraActionsHtml: window.PortalI18n.html`
        <div class="chatExtensionTools" id="${chromeIds.extensionTools}">
          <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.extensionButton}" type="button" aria-label="Chat Workspace 扩展" title="扩展" data-portal-icon="plus"></button>
          <div class="filterPopover chatExtensionMenu" id="${chromeIds.extensionMenu}" role="dialog" aria-label="Chat Workspace 扩展" aria-hidden="true"></div>
        </div>
        <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.compact}" type="button" aria-label="压缩当前会话上下文" title="压缩当前会话上下文" hidden data-portal-icon="compact"></button>
        <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.resetContext}" type="button" aria-label="清空 Resident 上下文" title="清空此前对话和上下文" hidden data-portal-icon="eraser"></button>
        ${options.extraActionsHtml || ""}
        ${options.onMenu ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.menu}" type="button" aria-label="Resident Menu" title="打开当前 Resident 的设置、Package、Mailbox 与工作日志" data-portal-icon="settings"></button>` : ""}`,
      bodySuffixHtml: window.PortalI18n.html`
        <div class="chatExtensionSplitter watchboardSplitter" id="${chromeIds.watchboardSplitter}" role="separator" aria-orientation="vertical" aria-label="调整 Watchboard 宽度" title="拖动调整 Watchboard 宽度"${optionalVisible("watchboard")}></div>
        <aside class="chatExtensionHost watchboard" id="${chromeIds.watchboard}" aria-label="Resident Watchboard"${optionalVisible("watchboard")}></aside>
        ${options.bodySuffixHtml || ""}`,
      afterBodyHtml: window.PortalI18n.html`
        <div class="forwardDock" id="${chromeIds.forwardDock}" role="group" aria-label="转发所选消息">
          <span class="forwardCount" id="${chromeIds.forwardCount}">已选择 0 条</span>
          <button class="portalButton is-quiet is-compact" id="${chromeIds.forwardCancel}" type="button" title="退出转发选择并清空所选消息">取消</button>
          <button class="portalButton is-primary is-compact" id="${chromeIds.forward}" type="button" title="选择 Resident 或聊天室并转发所选消息">转发</button>
        </div>
        ${options.afterBodyHtml || ""}`,
    };
  }

  let residentForwardDialog = null;

  function ensureResidentForwardDialog() {
    if (residentForwardDialog?.overlay?.isConnected) return residentForwardDialog;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay residentForwardOverlay";
    overlay.dataset.portalDetailOverlay = "";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface residentForwardSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="residentForwardTitle">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="residentModalTitle" id="residentForwardTitle">转发</div>
        <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭转发窗口" title="关闭"></button>
      </div>
      <form class="portalDetailForm residentForwardForm" data-resident-forward-form>
        <div class="residentForwardField" data-forward-destination-field hidden>
          <label for="residentForwardDestination">转发到</label>
          <select class="portalControl" id="residentForwardDestination" data-forward-destination>
            <option value="resident">Resident</option><option value="chatroom">聊天室</option>
          </select>
        </div>
        <div class="residentForwardField" data-forward-chatroom-field hidden>
          <label for="residentForwardChatroom">聊天室</label>
          <select class="portalControl" id="residentForwardChatroom" data-forward-chatroom></select>
          <small data-forward-chatroom-hint></small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardScope">问答 / 项目</label>
          <select class="portalControl" id="residentForwardScope" data-resident-forward-scope></select>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardLineage">Lineage</label>
          <select class="portalControl" id="residentForwardLineage" data-resident-forward-lineage></select>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardTarget">Resident</label>
          <select class="portalControl" id="residentForwardTarget" data-resident-forward-target></select>
          <small data-resident-forward-target-hint>先选择范围和 Lineage，再选择具体 Resident。</small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardDirectId">按 Resident ID 定位</label>
          <div class="residentForwardDirectRow">
            <input class="portalControl" id="residentForwardDirectId" data-resident-forward-direct-id autocomplete="off" spellcheck="false" placeholder="resident_…">
            <button class="portalButton" type="button" data-resident-forward-direct-locate>定位</button>
          </div>
          <small data-resident-forward-direct-hint>也可以跳过上方选择，直接输入你有权访问的 resident_id。</small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardMessage">附加消息</label>
          <textarea class="portalControl" id="residentForwardMessage" data-resident-forward-message placeholder="随转发附加的信息"></textarea>
        </div>
        <div class="residentForwardSummary" data-resident-forward-summary></div>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" type="button" data-resident-forward-cancel>取消</button>
          <button class="portalButton is-primary" type="submit" data-resident-forward-submit>发送</button>
        </div>
      </form>
    </div>`;
    document.body.appendChild(overlay);
    window.PortalDetailSurface?.mountAll?.(overlay);
    const dialog = window.PortalUI.createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => dialog.close(),
    });
    overlay.querySelector("[data-portal-detail-close]")?.addEventListener("click", () => dialog.close());
    overlay.querySelector("[data-resident-forward-cancel]")?.addEventListener("click", () => dialog.close());
    residentForwardDialog = { overlay, dialog };
    return residentForwardDialog;
  }

  async function openResidentForwardDialog(items = [], options = {}) {
    const messages = Array.isArray(items) ? items.filter(item => String(item?.content || "").trim()) : [];
    if (!messages.length) {
      window.PortalUI?.toast?.("请先选择至少一条消息。", "warning");
      return false;
    }
    const runtime = ensureResidentForwardDialog();
    const overlay = runtime.overlay;
    const form = overlay.querySelector("[data-resident-forward-form]");
    const scope = overlay.querySelector("[data-resident-forward-scope]");
    const lineage = overlay.querySelector("[data-resident-forward-lineage]");
    const target = overlay.querySelector("[data-resident-forward-target]");
    const targetHint = overlay.querySelector("[data-resident-forward-target-hint]");
    const directId = overlay.querySelector("[data-resident-forward-direct-id]");
    const directLocate = overlay.querySelector("[data-resident-forward-direct-locate]");
    const directHint = overlay.querySelector("[data-resident-forward-direct-hint]");
    const message = overlay.querySelector("[data-resident-forward-message]");
    const summary = overlay.querySelector("[data-resident-forward-summary]");
    const submit = overlay.querySelector("[data-resident-forward-submit]");
    const destination = overlay.querySelector("[data-forward-destination]");
    const chatroom = overlay.querySelector("[data-forward-chatroom]");
    const chatroomHint = overlay.querySelector("[data-forward-chatroom-hint]");
    const loadVersion = Number(form.__residentForwardLoadVersion || 0) + 1;
    form.__residentForwardLoadVersion = loadVersion;
    const isCurrentLoad = () => Number(form.__residentForwardLoadVersion || 0) === loadVersion;
    let currentTargets = [];
    let directTarget = null;
    let chatrooms = [];
    let chatroomLoadVersion = 0;
    let sending = false;
    const isChatroom = () => destination.value === "chatroom";

    const syncSubmit = () => {
      submit.disabled = sending || (isChatroom() ? chatroom.disabled || !chatroom.value
        : !String(directTarget?.resident_id || target.value || "").trim());
    };

    destination.value = "resident";
    overlay.querySelector("[data-forward-destination-field]").hidden = !options.loadChatrooms;
    overlay.querySelector("[data-forward-chatroom-field]").hidden = true;
    for (const field of [scope, lineage, target, directId]) field.closest(".residentForwardField").hidden = false;
    chatroom.onchange = syncSubmit;
    destination.onchange = async () => {
      for (const field of [scope, lineage, target, directId]) field.closest(".residentForwardField").hidden = isChatroom();
      overlay.querySelector("[data-forward-chatroom-field]").hidden = !isChatroom();
      if (!isChatroom()) { syncSubmit(); return; }
      const version = ++chatroomLoadVersion;
      chatroom.disabled = true;
      chatroom.innerHTML = window.PortalI18n.markup('<option value="">正在读取聊天室…</option>');
      chatroomHint.textContent = "";
      syncSubmit();
      try {
        const payload = await options.loadChatrooms();
        if (!isCurrentLoad() || version !== chatroomLoadVersion) return;
        chatrooms = (payload?.chatrooms || []).filter(room => room.permissions?.can_post);
        chatroom.innerHTML = chatrooms.length
          ? chatrooms.map(room => window.PortalI18n.html`<option value="${escapeHtml(room.chatroom_id)}">${escapeHtml(room.title || room.members?.join("、") || room.chatroom_id)}</option>`).join("")
          : window.PortalI18n.markup('<option value="">暂无可发送的聊天室</option>');
        chatroom.disabled = !chatrooms.length;
        chatroomHint.textContent = window.PortalI18n.t("以你的身份发送到聊天室；原消息中的 @ 作为引用保留。");
      } catch (error) {
        if (!isCurrentLoad() || version !== chatroomLoadVersion) return;
        chatroom.innerHTML = window.PortalI18n.markup('<option value="">聊天室加载失败</option>');
        chatroomHint.textContent = error?.message || String(error);
      }
      syncSubmit();
    };

    const clearDirectTarget = (clearInput = true) => {
      directTarget = null;
      if (clearInput) directId.value = "";
      directHint.textContent = window.PortalI18n.t("也可以跳过上方选择，直接输入你有权访问的 resident_id。");
      syncSubmit();
    };

    const renderTargets = (targets, selectedId = "") => {
      currentTargets = Array.isArray(targets) ? targets : [];
      if (!currentTargets.length) {
        target.innerHTML = window.PortalI18n.markup('<option value="">该 Lineage 暂无可发送的 Resident</option>');
        target.disabled = true;
        syncSubmit();
        targetHint.textContent = window.PortalI18n.t("该 Lineage 中没有当前用户可发送的 Resident。");
        return;
      }
      target.innerHTML = currentTargets.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.resident_id || "")}">${escapeHtml(item.title || item.resident_id || "Resident")}</option>`).join("");
      if (selectedId && currentTargets.some(item => String(item?.resident_id || "") === selectedId)) {
        target.value = selectedId;
      }
      target.disabled = false;
      syncSubmit();
      targetHint.textContent = window.PortalI18n.text`${currentTargets.length} 个可发送的 Resident`;
    };

    const loadLineageTargets = async (lineageId, selectedId = "") => {
      const cleanLineageId = String(lineageId || "").trim();
      target.disabled = true;
      target.innerHTML = window.PortalI18n.markup('<option value="">正在读取 Resident…</option>');
      syncSubmit();
      targetHint.textContent = cleanLineageId ? window.PortalI18n.t("正在读取这个 Lineage 的联系人…") : window.PortalI18n.t("请先选择 Lineage。");
      if (!cleanLineageId) return [];
      const payload = await options.loadTargets?.({ lineageId: cleanLineageId });
      if (!isCurrentLoad()) return [];
      const targets = Array.isArray(payload?.targets) ? payload.targets : [];
      renderTargets(targets, selectedId);
      return targets;
    };

    const loadScopeLineages = async (scopeId, selectedLineageId = "") => {
      const cleanScopeId = String(scopeId || "").trim();
      lineage.disabled = true;
      lineage.innerHTML = window.PortalI18n.markup('<option value="">正在读取 Lineage…</option>');
      target.disabled = true;
      target.innerHTML = window.PortalI18n.markup('<option value="">请先选择 Lineage</option>');
      currentTargets = [];
      syncSubmit();
      if (!cleanScopeId) return [];
      const payload = await options.loadTargets?.({ scopeId: cleanScopeId });
      if (!isCurrentLoad()) return [];
      const lineages = Array.isArray(payload?.lineages) ? payload.lineages : [];
      if (!lineages.length) {
        lineage.innerHTML = window.PortalI18n.markup('<option value="">该范围暂无可发送的 Lineage</option>');
        targetHint.textContent = window.PortalI18n.t("该范围中没有当前用户可发送的 Resident。");
        return [];
      }
      lineage.innerHTML = lineages.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.title || item.id || "Lineage")}</option>`).join("");
      if (selectedLineageId && lineages.some(item => String(item?.id || "") === selectedLineageId)) {
        lineage.value = selectedLineageId;
      }
      lineage.disabled = false;
      await loadLineageTargets(lineage.value);
      return lineages;
    };

    overlay.querySelector("#residentForwardTitle").textContent = options.title || window.PortalI18n.t("转发");
    submit.textContent = options.submitLabel || window.PortalI18n.t("发送");
    summary.textContent = options.summary || window.PortalI18n.text`将转发 ${messages.length} 条消息。`;
    message.closest(".residentForwardField").hidden = options.selectTargetOnly === true;
    message.value = "";
    directId.value = "";
    directHint.textContent = window.PortalI18n.t("也可以跳过上方选择，直接输入你有权访问的 resident_id。");
    scope.disabled = true;
    scope.innerHTML = window.PortalI18n.markup('<option value="">正在读取范围…</option>');
    lineage.disabled = true;
    lineage.innerHTML = window.PortalI18n.markup('<option value="">请先选择范围</option>');
    target.disabled = true;
    target.innerHTML = window.PortalI18n.markup('<option value="">请先选择 Lineage</option>');
    submit.disabled = true;
    runtime.dialog.open({ focusTarget: scope });

    const previousScopeChange = form.__residentForwardScopeChange;
    if (previousScopeChange) scope.removeEventListener("change", previousScopeChange);
    const handleScopeChange = () => {
      clearDirectTarget();
      loadScopeLineages(scope.value).catch(error => {
        if (!isCurrentLoad()) return;
        lineage.innerHTML = window.PortalI18n.markup('<option value="">Lineage 加载失败</option>');
        lineage.disabled = true;
        renderTargets([]);
        targetHint.textContent = window.PortalI18n.text`Lineage 加载失败：${error?.message || error}`;
      });
    };
    form.__residentForwardScopeChange = handleScopeChange;
    scope.addEventListener("change", handleScopeChange);

    const previousLineageChange = form.__residentForwardLineageChange;
    if (previousLineageChange) lineage.removeEventListener("change", previousLineageChange);
    const handleLineageChange = () => {
      clearDirectTarget();
      loadLineageTargets(lineage.value).catch(error => {
        if (!isCurrentLoad()) return;
        renderTargets([]);
        targetHint.textContent = window.PortalI18n.text`Resident 加载失败：${error?.message || error}`;
      });
    };
    form.__residentForwardLineageChange = handleLineageChange;
    lineage.addEventListener("change", handleLineageChange);

    const previousTargetChange = form.__residentForwardTargetChange;
    if (previousTargetChange) target.removeEventListener("change", previousTargetChange);
    const handleTargetChange = () => clearDirectTarget();
    form.__residentForwardTargetChange = handleTargetChange;
    target.addEventListener("change", handleTargetChange);

    const previousDirectLocate = form.__residentForwardDirectLocate;
    if (previousDirectLocate) directLocate.removeEventListener("click", previousDirectLocate);
    const handleDirectLocate = async () => {
      const residentId = String(directId.value || "").trim();
      if (!residentId) {
        directHint.textContent = window.PortalI18n.t("请输入 resident_id。");
        directId.focus();
        return;
      }
      window.PortalUI.setButtonBusy(directLocate, true, "定位中…");
      directHint.textContent = window.PortalI18n.t("正在校验权限并定位 Resident…");
      try {
        const payload = await options.loadTargets?.({ residentId });
        if (!isCurrentLoad()) return;
        const resolved = payload?.target && typeof payload.target === "object" ? payload.target : null;
        if (!resolved?.resident_id) throw new Error("未找到可发送的 Resident");
        directTarget = resolved;
        directHint.textContent = window.PortalI18n.text`已定位并将发送至：${resolved.title || resolved.resident_id}`;
        syncSubmit();
      } catch (error) {
        directTarget = null;
        syncSubmit();
        if (isCurrentLoad()) directHint.textContent = window.PortalI18n.text`定位失败：${error?.message || error}`;
      } finally {
        window.PortalUI.setButtonBusy(directLocate, false);
      }
    };
    form.__residentForwardDirectLocate = handleDirectLocate;
    directLocate.addEventListener("click", handleDirectLocate);

    const previousDirectKeydown = form.__residentForwardDirectKeydown;
    if (previousDirectKeydown) directId.removeEventListener("keydown", previousDirectKeydown);
    const handleDirectKeydown = event => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      directLocate.click();
    };
    form.__residentForwardDirectKeydown = handleDirectKeydown;
    directId.addEventListener("keydown", handleDirectKeydown);

    const previousDirectInput = form.__residentForwardDirectInput;
    if (previousDirectInput) directId.removeEventListener("input", previousDirectInput);
    const handleDirectInput = () => {
      if (String(directTarget?.resident_id || "") === String(directId.value || "").trim()) return;
      directTarget = null;
      directHint.textContent = window.PortalI18n.t("点击“定位”校验这个 resident_id；不定位则仍使用上方选择。");
      syncSubmit();
    };
    form.__residentForwardDirectInput = handleDirectInput;
    directId.addEventListener("input", handleDirectInput);

    const previousSubmit = form.__residentForwardSubmit;
    if (previousSubmit) form.removeEventListener("submit", previousSubmit);
    const handleSubmit = async event => {
      event.preventDefault();
      const targetResidentId = isChatroom() ? "" : String(directTarget?.resident_id || target.value || "").trim();
      const targetChatroomId = isChatroom() ? chatroom.value : "";
      if ((!targetResidentId && !targetChatroomId) || sending || submit.disabled) return;
      sending = true;
      window.PortalUI.setButtonBusy(submit, true, "发送中…");
      try {
        const result = await options.submit?.({
          ...(targetChatroomId ? { target_chatroom_id: targetChatroomId } : {
            target_resident_id: targetResidentId,
            target_lineage_id: String(directTarget?.lineage_id || lineage.value || ""),
          }),
          messages,
          message: String(message.value || ""),
        });
        const forwardedTarget = targetChatroomId ? chatrooms.find(room => room.chatroom_id === targetChatroomId)
          : directTarget || currentTargets.find(item => String(item?.resident_id || "") === targetResidentId);
        if (targetResidentId) window.PortalConversationStore?.remember?.({
          key: `resident:${targetResidentId}`, type: "resident", resident_id: targetResidentId,
          lineage_id: String(forwardedTarget?.lineage_id || lineage.value || ""),
          title: String(forwardedTarget?.title || targetResidentId), t: Date.now(),
        });
        runtime.dialog.close({ accepted: true });
        options.onSuccess?.(result, forwardedTarget || null);
        window.PortalUI.toast(options.successMessage || window.PortalI18n.t("已转发。"), "success");
      } catch (error) {
        window.PortalUI.toast(error?.message || String(error), "error");
      } finally {
        sending = false;
        if (isCurrentLoad()) {
          window.PortalUI.setButtonBusy(submit, false);
          syncSubmit();
        }
      }
    };
    form.__residentForwardSubmit = handleSubmit;
    form.addEventListener("submit", handleSubmit);
    try {
      const preferredLineageId = String(options.preferredLineageId || "").trim();
      const payload = await options.loadTargets?.({ preferredLineageId });
      if (!isCurrentLoad()) return false;
      const scopes = Array.isArray(payload?.scopes) ? payload.scopes : [];
      if (!scopes.length) {
        scope.innerHTML = window.PortalI18n.markup('<option value="">暂无可发送的 Resident</option>');
        scope.disabled = true;
        renderTargets([]);
      } else {
        scope.innerHTML = scopes.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.title || item.id || window.PortalI18n.t("项目"))}</option>`).join("");
        const preferredScopeId = String(payload?.preferred_scope_id || "").trim();
        if (preferredScopeId && scopes.some(item => String(item?.id || "") === preferredScopeId)) {
          scope.value = preferredScopeId;
        }
        scope.disabled = false;
        await loadScopeLineages(scope.value, preferredLineageId);
      }
    } catch (error) {
      if (!isCurrentLoad()) return false;
      scope.innerHTML = window.PortalI18n.markup('<option value="">联系人加载失败</option>');
      scope.disabled = true;
      renderTargets([]);
      targetHint.textContent = window.PortalI18n.text`联系人加载失败：${error?.message || error}`;
    }
    return true;
  }

  class ResidentChatWorkspace extends BaseChatWorkspace {
    constructor(options = {}) {
      const id = workspaceInstanceId(options, "residentChat");
      const instanceOptions = { ...options, id };
      super(instanceOptions);
      this.chromeIds = residentChromeIds(instanceOptions);
      this.residentTarget = normalizeResidentTarget(instanceOptions.target || instanceOptions.residentTarget || {});
      this.historyTarget = this.residentTarget;
      this.forwardEnabled = true;
      this.forwardSelecting = false;
      this.forwardSelected = new Set();
      this.extensionPopover = null;
    }

    element(name) {
      if (name === "extensionMenu" && this.extensionPopover) return this.extensionPopover.element;
      const chromeId = this.chromeIds?.[String(name || "")];
      if (chromeId) return this.root?.querySelector(`#${CSS.escape(chromeId)}`) || null;
      return super.element(name);
    }

    messageKey(item) {
      return messageIdentity(item);
    }

    forwardSelectionGroup(key) {
      const items = Array.isArray(this.state.messages) ? this.state.messages : [];
      const index = items.findIndex((item, itemIndex) => this.messageKey(item, itemIndex, items) === key);
      if (index < 0 || messageRole(items[index]) !== "human") return [key];
      let lastAgentKey = "";
      for (let cursor = index + 1; cursor < items.length; cursor += 1) {
        if (messageRole(items[cursor]) === "human") break;
        if (messageRole(items[cursor]) === "agent") lastAgentKey = this.messageKey(items[cursor], cursor, items);
      }
      return lastAgentKey ? [key, lastAgentKey] : [key];
    }

    refreshForwardControls() {
      const count = this.forwardSelected.size;
      const dock = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardDock)}`);
      const label = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardCount)}`);
      const submit = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forward)}`);
      dock?.classList.toggle("open", this.forwardSelecting);
      if (label) label.textContent = window.PortalI18n.text`已选择 ${count} 条`;
      if (submit) submit.disabled = count <= 0;
    }

    clearForwardSelection() {
      this.forwardSelecting = false;
      this.forwardSelected.clear();
      this.rerenderForwardSelection();
    }

    startForwardSelection(key) {
      if (!this.forwardEnabled || !this.state.messages?.some(item => this.messageKey(item) === key && !item.pending)) return;
      this.forwardSelecting = true;
      this.toggleForwardSelection(key, true);
    }

    forwardSelectionState() {
      return Object.freeze({
        active: this.forwardSelecting,
        count: this.forwardSelected.size,
      });
    }

    closeExtensionMenu() {
      this.extensionPopover?.close?.();
    }

    repositionExtensionMenu() {
      this.extensionPopover?.reposition?.();
    }

    focusComposer() {
      this.element("input")?.focus?.();
    }

    rerenderForwardSelection() {
      this.controller?.setMessages?.(this.state.messages || [], this.state.messageRenderOptions || {});
      this.refreshForwardControls();
    }

    toggleForwardSelection(key, checked) {
      const selected = new Set(this.forwardSelected);
      this.forwardSelectionGroup(key).forEach(groupKey => {
        if (checked) selected.add(groupKey);
        else selected.delete(groupKey);
      });
      this.forwardSelected = selected;
      this.rerenderForwardSelection();
    }

    bindForwardInputs(root) {
      root?.querySelectorAll?.("[data-resident-forward-select-key]").forEach(input => {
        if (input.dataset.residentForwardBound === "1") return;
        input.dataset.residentForwardBound = "1";
        input.addEventListener("change", () => {
          this.toggleForwardSelection(input.dataset.residentForwardSelectKey || "", input.checked);
        });
      });
    }

    selectedForwardMessages() {
      const items = Array.isArray(this.state.messages) ? this.state.messages : [];
      return items.map((item, index) => ({ item, index, key: this.messageKey(item, index, items) }))
        .filter(entry => this.forwardSelected.has(entry.key))
        .map(entry => ({
          // ForwardMessages reads each selected message by its IDs.
          resident_id: String(this.residentTarget?.resident_id || ""),
          message_id: String(entry.item?.message_id || entry.item?.id || ""),
          role: messageRole(entry.item),
          author: messageTitle(entry.item, entry.index, this.state.messageRenderOptions || {}),
          content: String(entry.item?.content || ""),
          created_at: entry.item?.created_at || "",
        }))
        .filter(item => item.content.trim() && item.message_id);
    }

    bindResidentChrome() {
      const extensionButton = this.root?.querySelector(`#${CSS.escape(this.chromeIds.extensionButton)}`);
      const extensionMenu = this.root?.querySelector(`#${CSS.escape(this.chromeIds.extensionMenu)}`);
      if (extensionButton && extensionMenu && window.PortalOverlay) {
        this.extensionPopover = window.PortalOverlay.bindPopover({
          trigger: extensionButton,
          panel: extensionMenu,
          boundary: this.root,
          placement: "bottom-end",
          ariaHaspopup: "dialog",
          portal: true,
          closeOnOutside: true,
          closeOnEscape: true,
          onOpen: context => {
            extensionMenu.classList.add("open");
            extensionMenu.setAttribute("aria-hidden", "false");
            extensionButton.classList.add("is-active");
            this.options.onExtensionOpen?.(extensionMenu, this, context);
            if (!extensionMenu.childElementCount) {
              const notice = document.createElement("p");
              notice.className = "paneNote";
              notice.textContent = window.PortalI18n.t("当前对话没有可用扩展。");
              extensionMenu.append(notice);
            }
          },
          onClose: context => {
            extensionMenu.classList.remove("open");
            extensionMenu.setAttribute("aria-hidden", "true");
            extensionButton.classList.remove("is-active");
            this.options.onExtensionClose?.(extensionMenu, this, context);
          },
        });
        extensionButton.addEventListener("click", event => this.extensionPopover?.toggle(event));
      }
      this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardCancel)}`)?.addEventListener("click", () => this.clearForwardSelection());
      this.root?.querySelector(`#${CSS.escape(this.chromeIds.forward)}`)?.addEventListener("click", () => {
        const items = this.selectedForwardMessages();
        if (!items.length) return window.PortalUI?.toast?.("请先选择至少一条消息。", "warning");
        this.options.onForward?.(items, this);
      });
      this.root?.querySelector(`#${CSS.escape(this.chromeIds.compact)}`)?.addEventListener("click", () => this.options.onCompact?.(this));
      this.root?.querySelector(`#${CSS.escape(this.chromeIds.resetContext)}`)?.addEventListener("click", () => this.options.onResetContext?.(this));
      this.root?.querySelector(`#${CSS.escape(this.chromeIds.menu)}`)?.addEventListener("click", () => this.options.onMenu?.(this));
      this.refreshForwardControls();
    }

    extensionOptions() {
      const options = this.options;
      return {
        ...residentChatChrome(options),
        draftNamespace: "resident-session",
        historyScope: () => this.inputHistoryScope(),
        composerInputHeight: Number(options.composerInputHeight || 72),
        composerMinHeight: Number(options.composerMinHeight || 66),
        composerMaxHeight: Number(options.composerMaxHeight || 360),
        // Whether Forward is offered does not render the messages again:
        // syncForwardAvailability shows or hides the menu items in place.
        messageRenderState: () => ({
          forwardSelecting: this.forwardSelecting,
          forwardSelected: [...this.forwardSelected].sort(),
        }),
        renderMessageMenuItems: (item, key) => String(options.renderMessageMenuItems?.(item, key) || "") + (
          !item.pending
            ? '<button type="button" data-portal-message-action="forward"' + (this.forwardEnabled ? "" : " hidden") + ' title="选择这条消息，并可继续多选后向 Resident 或聊天室转发">Forward</button>'
            : ""
        ),
        onMessageAction: context => {
          if (context.action === "forward") return this.startForwardSelection(context.key);
          return options.onMessageAction?.(context);
        },
        onSearchMessages: async (query, paging) => {
          const residentId = this.target().resident_id;
          if (!residentId) return { messages: [] };
          const result = await window.PortalResidentService.searchMessages([residentId], query, paging);
          return {
            messages: (result.results || []).filter(item => item.resident_id === residentId)
              .map(item => residentProtocolMessageToWorkspace(item.message)),
            next_page_token: result.next_page_token || "",
          };
        },
        messageClass: (item, index, items) => [
          options.messageClass?.(item, index, items) || "",
          this.forwardSelecting ? "forwardSelectable" : "",
          this.forwardSelected.has(this.messageKey(item, index, items)) ? "forwardSelected" : "",
        ].filter(Boolean).join(" "),
        renderMessagePrefix: (item, index, items) => {
          const original = String(options.renderMessagePrefix?.(item, index, items) || "");
          if (!this.forwardSelecting) return original;
          const key = this.messageKey(item, index, items);
          return window.PortalI18n.html`<label class="forwardCheck" title="选择转发"><input type="checkbox" data-resident-forward-select-key="${escapeHtml(key)}" ${this.forwardSelected.has(key) ? "checked" : ""} /></label>${original}`;
        },
        onMessagesRendered: (root, controller, incremental = false, changedRoots = []) => {
          options.onMessagesRendered?.(root, controller, this, incremental);
          for (const bindingRoot of incremental ? changedRoots : [root]) this.bindForwardInputs(bindingRoot);
          this.refreshForwardControls();
        },
      };
    }

    mount() {
      super.mount();
      this.controller.target = () => this.target();
      this.controller.sessionScope = () => this.sessionScope();
      this.bindResidentChrome();
      this.syncForwardAvailability();
      return this;
    }

    syncForwardAvailability() {
      this.root?.querySelectorAll('[data-portal-message-action="forward"]').forEach(button => {
        button.hidden = !this.forwardEnabled;
      });
    }

    setTarget(value = {}) {
      const next = normalizeResidentTarget(value);
      const previousScope = residentSessionScope(this.residentTarget);
      const nextScope = residentSessionScope(next);
      if (previousScope !== nextScope) {
        this.controller?.releaseResidentRuntime?.();
        this.saveDraft({ immediate: true });
        this.followLatest();
      }
      this.residentTarget = next;
      this.historyTarget = next;
      this.options.onTargetChange?.(next, this);
      return next;
    }

    setHistoryTarget(value = {}) {
      this.historyTarget = normalizeResidentTarget(value);
      return this.historyTarget;
    }

    target() {
      return this.residentTarget;
    }

    sessionScope() {
      return residentSessionScope(this.residentTarget);
    }

    inputHistoryScope() {
      if (this.historyTarget?.resident_id) return residentSessionScope(this.historyTarget);
      const pending = typeof this.options.pendingHistoryScope === "function"
        ? this.options.pendingHistoryScope()
        : this.options.pendingHistoryScope;
      return String(pending || residentSessionScope(this.historyTarget));
    }

    setResidentActionVisibility(actions = {}) {
      const set = (id, visible, disabled = false) => {
        const element = this.root?.querySelector(`#${CSS.escape(id)}`);
        if (!element) return;
        element.hidden = visible === false;
        element.disabled = !!disabled;
      };
      set(this.chromeIds.extensionTools, actions.extensions !== false);
      set(this.chromeIds.menu, actions.extensions !== false);
      const forwardEnabled = actions.forward !== false;
      if (this.forwardEnabled !== forwardEnabled) {
        this.forwardEnabled = forwardEnabled;
        this.syncForwardAvailability();
        if (!forwardEnabled && (this.forwardSelecting || this.forwardSelected.size)) this.clearForwardSelection();
      }
      set(this.chromeIds.compact, !!actions.compact, actions.compactDisabled);
      set(this.chromeIds.resetContext, !!actions.resetContext, actions.resetContextDisabled);
    }
  }

  function turnArtifactDisplay(part) {
    const hasPlan = !!(String(part.plan_text || "").trim() || String(part.explanation || "").trim() || part.plan?.length);
    const hasChanges = !!(String(part.diff || "").trim() || part.changes?.length);
    return {
      hasContent: hasPlan || hasChanges,
      title: hasPlan ? (hasChanges ? window.PortalI18n.t("本轮计划与修改") : window.PortalI18n.t("本轮计划")) : (hasChanges ? window.PortalI18n.t("本轮修改") : window.PortalI18n.t("本轮未记录到可展示的计划或文件差异")),
    };
  }

  // A subagent the Resident's model started: its own steps stay out of the
  // Resident's context; the Chat shows the run once the model has its result.
  // The built-in kinds of the Providers (Claude Code subagent_type, Codex
  // agent role, Kimi Code subagent name) by what they do; any other kind
  // shows as its Provider named it.
  const SUBAGENT_TYPE_LABELS = {
    "general-purpose": window.PortalI18n.t("通用"), default: window.PortalI18n.t("通用"),
    explore: window.PortalI18n.t("探索"), explorer: window.PortalI18n.t("探索"),
    plan: window.PortalI18n.t("规划"),
    coder: window.PortalI18n.t("编码"), worker: window.PortalI18n.t("执行"),
  };

  function subagentTypeLabel(type) {
    const raw = String(type || "").trim();
    return SUBAGENT_TYPE_LABELS[raw.toLowerCase()] || raw;
  }

  function subagentRunTitle(part) {
    const kind = subagentTypeLabel(part.agent_type);
    return window.PortalI18n.text`子 agent${kind ? ` · ${kind}` : ""}${part.description ? `：${part.description}` : ""}`;
  }

  function subagentRunDuration(part) {
    const seconds = Math.round((Date.parse(String(part.finished_at || "")) - Date.parse(String(part.started_at || ""))) / 1000);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    return seconds < 60 ? `用时 ${seconds} 秒` : `用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }

  // The tools a run used, most used first: "Bash ×3 · Read".
  function subagentRunTools(steps) {
    const counts = new Map();
    steps.filter(step => step.kind === "tool").forEach(step => {
      const name = String(step.tool_name || "tool");
      counts.set(name, (counts.get(name) || 0) + 1);
    });
    return [...counts].sort((a, b) => b[1] - a[1])
      .map(([name, count]) => window.PortalI18n.html`<code>${escapeHtml(name)}${count > 1 ? ` ×${count}` : ""}</code>`).join("");
  }

  // The folded card shows the result's opening words as plain text.
  function subagentRunPreview(result) {
    return String(result || "")
      .replace(/```[\s\S]*?(```|$)/g, " ")
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/^\s*(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/`|\*\*|__|~~/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 300);
  }

  function subagentRunCard(item, part, context) {
    const status = { completed: "已完成", running: "进行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" }[part.status] || String(part.status || "");
    const steps = Array.isArray(part.steps) ? part.steps : [];
    const total = Number(part.steps_total || steps.length);
    const stepHtml = steps.map(step => step.kind === "tool"
      ? window.PortalI18n.html`<li data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}><code>${escapeHtml(step.tool_name || "tool")}</code> ${escapeHtml(step.text || "")}</li>`
      : window.PortalI18n.html`<li data-step-kind="message">${escapeHtml(step.text || "")}</li>`).join("");
    const meta = [subagentRunDuration(part), total ? `${total} 步` : "", subagentTokens(part.usage)].filter(Boolean).join(" · ");
    const tools = subagentRunTools(steps);
    // Folded by default: one line of the result and the tools it used.
    const fold = escapeHtml(part.tool_use_id || item.id);
    const preview = part.result ? subagentRunPreview(part.result) : "子 agent 没有返回文字结果";
    const files = subagentFilesHtml(part.files);
    const prompt = String(part.prompt || "").trim();
    return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard subagentRunCard" data-subagent-run="${escapeHtml(item.id)}" data-subagent-tool-use="${escapeHtml(part.tool_use_id || "")}" data-status="${escapeHtml(part.status || "")}">
      <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle"><span class="subagentRunMark" aria-hidden="true"></span>${escapeHtml(subagentRunTitle(part))}</div><span class="messageSystemBadge">${escapeHtml(part.background ? window.PortalI18n.t("后台子 agent") : window.PortalI18n.t("子 agent"))}${status ? ` · ${escapeHtml(status)}` : ""}</span></div>${meta ? window.PortalI18n.html`<span class="subagentRunMeta">${escapeHtml(meta)}</span>` : ""}</header>
      <details class="subagentRunBody" data-message-disclosure="subagent-result:${fold}">
        <summary><span class="subagentRunFold" aria-hidden="true"></span><span class="subagentRunPreview">${escapeHtml(preview)}</span><span class="subagentRunLabel">结果</span>${tools ? window.PortalI18n.html`<span class="subagentRunTools">${tools}</span>` : ""}<span class="subagentRunHint"><span class="subagentRunExpand">展开</span><span class="subagentRunCollapse">收起</span></span></summary>
        <div class="subagentRunResult">${part.result ? context.renderContent(part.result) : window.PortalI18n.html`<small class="nativeTurnEmpty">子 agent 没有返回文字结果</small>`}
        ${part.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">结果较长，这里只显示开头；复制或保存时取完整结果</small>` : ""}</div>
        ${files ? window.PortalI18n.html`<div class="subagentRunSection"><div class="subagentRunSectionLabel">改动的文件</div>${files}</div>` : ""}
        ${prompt ? window.PortalI18n.html`<details class="subagentRunPromptFold" data-message-disclosure="subagent-prompt:${fold}"><summary>它收到的任务</summary><div class="subagentRunPrompt">${escapeHtml(prompt)}</div>${part.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">任务较长，这里只显示开头</small>` : ""}</details>` : ""}
        ${steps.length ? window.PortalI18n.html`<details class="subagentRunStepsFold" data-message-disclosure="subagent-steps:${fold}"><summary>查看子 agent 的步骤（${total} 步${total > steps.length ? window.PortalI18n.text`，显示最后 ${steps.length} 步` : ""}）</summary><ol class="subagentRunSteps">${stepHtml}</ol></details>` : ""}
        <div class="subagentRunActions">
          <button type="button" class="portalButton is-compact" data-subagent-action="detail" title="打开这个子 agent 的完整步骤：每次工具调用的输入和输出">全部步骤</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="quote" title="把结果摘录放进输入框，接着写要父 Resident 做什么">引用到输入框</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="copy">复制结果</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="save" title="把任务、结果和改动的文件保存为 Resident 自己 Workspace 中的 Markdown 文件">保存为文件</button>
        </div>
      </details>
      </section>`;
  }

  // While the model waits on a subagent, its live card shows among the turn's
  // activity: what it does now, its last steps and how long it has run.  The
  // finished card replaces it in the conversation once the model has the result.
  const SUBAGENT_LIVE_TRAIL = 4;
  let subagentClockTimer = 0;

  function subagentClockText(since) {
    const started = Date.parse(String(since || ""));
    if (!Number.isFinite(started)) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function startSubagentClocks() {
    if (!subagentClockTimer) subagentClockTimer = window.setInterval(tickSubagentClocks, 1000);
  }

  function tickSubagentClocks() {
    const clocks = document.querySelectorAll("[data-subagent-since]");
    if (!clocks.length) {
      window.clearInterval(subagentClockTimer);
      subagentClockTimer = 0;
      return;
    }
    clocks.forEach(clock => {
      const text = subagentClockText(clock.dataset.subagentSince);
      if (clock.textContent !== text) clock.textContent = text;
    });
  }

  function subagentLiveRuns(details) {
    return (Array.isArray(details) ? details : [])
      .filter(detail => detail?.kind === "subagent" && runtimeStatusLabel(detail.status) === "running"
        && detail.subagent && typeof detail.subagent === "object" && detail.subagent.tool_use_id)
      .map(detail => ({ ...detail.subagent, execution_id: String(detail.execution_id || "") }))
      .sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || "")));
  }

  // Each part carries the data it shows (data-sig): an update replaces only
  // the parts that changed, so a card keeps its running animations.
  function subagentLivePart(name, data, tag, className, html) {
    return `<${tag} class="${className}" data-subagent-part="${name}" data-sig="${escapeHtml(JSON.stringify(data))}">${html}</${tag}>`;
  }

  function subagentLiveStepText(step) {
    return step.kind === "tool"
      ? window.PortalI18n.html`<code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`
      : window.PortalI18n.html`<span class="subagentLiveSay">说</span><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`;
  }

  function subagentLiveCardHtml(run) {
    const steps = Array.isArray(run.steps) ? run.steps : [];
    const now = steps.length ? steps[steps.length - 1] : null;
    const trail = steps.slice(Math.max(0, steps.length - 1 - SUBAGENT_LIVE_TRAIL), Math.max(0, steps.length - 1));
    const total = Number(run.steps_total || steps.length);
    const started = Date.parse(String(run.started_at || ""));
    const age = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : 0;
    const head = subagentLivePart("head", [run.agent_type, run.description, run.background, run.started_at], "header", "subagentLiveHead", window.PortalI18n.html`
        <span class="subagentLiveSpinner" aria-hidden="true"></span>
        <div class="subagentLiveIdentity">
          <div class="subagentLiveType" title="${escapeHtml(run.agent_type || "")}">${escapeHtml(subagentTypeLabel(run.agent_type) || window.PortalI18n.t("子 agent"))}<span class="subagentLiveMode">${run.background ? window.PortalI18n.t("后台") : window.PortalI18n.t("前台")}</span></div>
          <div class="subagentLiveTask" title="${escapeHtml(run.description || "")}">${escapeHtml(run.description || "")}</div>
        </div>
        <span class="subagentLiveClock" data-subagent-since="${escapeHtml(run.started_at || "")}">${escapeHtml(subagentClockText(run.started_at))}</span>`);
    const nowHtml = now
      ? `${now.kind === "tool" && !now.running ? window.PortalI18n.html`<span class="subagentLiveTick" data-step-error="${now.error ? "1" : ""}" aria-hidden="true"></span>` : ""}${subagentLiveStepText(now)}`
      : window.PortalI18n.html`<span class="subagentLiveText">正在启动…</span>`;
    // A call still running shows how long it has taken so far.
    const stepClock = now?.kind === "tool" && now.running && now.started_at
      ? window.PortalI18n.html`<span class="subagentLiveStepClock" data-subagent-since="${escapeHtml(now.started_at)}" title="这一步已用时">${escapeHtml(subagentClockText(now.started_at))}</span>`
      : "";
    const nowPart = subagentLivePart("now", now, "div", "subagentLiveNow", window.PortalI18n.html`${nowHtml}${stepClock}<span class="subagentLiveDots" aria-hidden="true"><i></i><i></i><i></i></span>`);
    const trailPart = subagentLivePart("trail", trail, "ol", "subagentLiveTrail", trail.map(step => window.PortalI18n.html`<li data-step-kind="${escapeHtml(step.kind || "")}"><span class="subagentLiveTick" data-step-error="${step.error ? "1" : ""}" aria-hidden="true"></span>${subagentLiveStepText(step)}</li>`).join(""));
    const tokens = subagentTokens(run.usage);
    const fileCount = Array.isArray(run.files) ? run.files.length : 0;
    const foot = subagentLivePart("foot", [total, run.model, tokens, fileCount], "footer", "subagentLiveFoot",
      window.PortalI18n.html`<span>${total ? window.PortalI18n.text`第 ${total} 步` : window.PortalI18n.t("等待第一步")}${tokens ? ` · ${escapeHtml(tokens)}` : ""}${fileCount ? window.PortalI18n.text` · 改了 ${fileCount} 个文件` : ""}</span>${run.model ? window.PortalI18n.html`<span>${escapeHtml(run.model)}</span>` : ""}`);
    // Stop shows where the Provider stops one run and the reader may stop the
    // turn, steering where the reader may send (the Chat root says which).
    const actions = subagentLivePart("actions", 0, "div", "subagentLiveActions", window.PortalI18n.html`<button type="button" class="subagentLiveAction" data-subagent-action="detail" title="打开这个子 agent 的任务、完整步骤和用量">详情</button><button type="button" class="subagentLiveAction" data-subagent-action="steer" title="在输入框写给父 Resident 的纠偏，发送后立即进入当前这一轮">纠偏</button><button type="button" class="subagentLiveAction" data-subagent-action="stop" title="只停止这个子 agent，父 Resident 的这一轮继续">停止</button>`);
    return window.PortalI18n.html`<section class="subagentLiveCard" data-subagent-live="${escapeHtml(run.tool_use_id)}"${age < 2 ? " data-fresh" : ""} style="--subagent-age: ${age.toFixed(2)}" aria-label="${escapeHtml(window.PortalI18n.text`子 agent${subagentTypeLabel(run.agent_type) ? ` ${subagentTypeLabel(run.agent_type)}` : ""} 正在运行：${run.description || ""}`)}">${head}${nowPart}${trailPart}${foot}${actions}</section>`;
  }

  function subagentLiveHtml(details) {
    const runs = subagentLiveRuns(details);
    if (!runs.length) return "";
    runs.forEach(run => subagentLiveById.set(run.tool_use_id, run));
    startSubagentClocks();
    const head = runs.length > 1
      ? subagentLivePart("group", runs.length, "div", "subagentLiveGroupHead", window.PortalI18n.html`<span class="subagentLivePulse" aria-hidden="true"></span>${runs.length} 个子 agent 正在并行工作`)
      : "";
    return window.PortalI18n.html`<div class="subagentLiveGroup" data-message-key="__subagents__" data-count="${runs.length}" role="status" aria-live="polite">${head}<div class="subagentLiveGrid">${runs.map(subagentLiveCardHtml).join("")}</div></div>`;
  }

  function patchSubagentLive(current, next) {
    current.dataset.count = next.dataset.count;
    const currentHead = current.querySelector(":scope > .subagentLiveGroupHead");
    const nextHead = next.querySelector(":scope > .subagentLiveGroupHead");
    if (!nextHead) currentHead?.remove();
    else if (!currentHead) current.prepend(nextHead);
    else if (currentHead.dataset.sig !== nextHead.dataset.sig) currentHead.replaceWith(nextHead);
    const grid = current.querySelector(".subagentLiveGrid");
    const cards = [...next.querySelectorAll(".subagentLiveCard")];
    const keep = new Set(cards.map(card => card.dataset.subagentLive));
    [...grid.children].forEach(card => { if (!keep.has(card.dataset.subagentLive)) card.remove(); });
    cards.forEach((card, index) => {
      const existing = [...grid.children].find(node => node.dataset.subagentLive === card.dataset.subagentLive);
      if (!existing) {
        grid.insertBefore(card, grid.children[index] || null);
        return;
      }
      for (const part of card.querySelectorAll("[data-subagent-part]")) {
        const old = existing.querySelector(`[data-subagent-part="${part.dataset.subagentPart}"]`);
        if (old && old.dataset.sig !== part.dataset.sig) old.replaceWith(part);
      }
      if (grid.children[index] !== existing) grid.insertBefore(existing, grid.children[index] || null);
    });
  }

  // Running subagents by their card's tool_use_id, as the Chat last drew
  // them: the detail dialog follows a run here, and its Stop names the run's
  // Execution from here.
  const subagentLiveById = new Map();
  // Where each running card last stood: the run's result card arrives from
  // there when it ends (decision_b1ab45c4b3ec).
  const subagentLivePlaces = new Map();

  function subagentTokens(usage) {
    const total = Number(usage?.total_tokens || 0) || Number(usage?.input_tokens || 0) + Number(usage?.output_tokens || 0);
    if (!(total > 0)) return "";
    return total >= 1000 ? `${(total / 1000).toFixed(total >= 100000 ? 0 : 1)}k tokens` : `${total} tokens`;
  }

  function subagentFileName(path) {
    return String(path || "").split("/").filter(Boolean).pop() || String(path || "");
  }

  // The files a run changed; each opens in the Workspace preview.
  function subagentFilesHtml(files) {
    const list = (Array.isArray(files) ? files : []).filter(file => String(file?.path || "").trim());
    if (!list.length) return "";
    return window.PortalI18n.html`<div class="subagentFiles">${list.map(file => window.PortalI18n.html`<button type="button" class="subagentFileChip" data-portal-source-path="${escapeHtml(file.path)}" title="${escapeHtml(window.PortalI18n.text`${file.path}（${(file.tools || []).join("、")}）`)}"><span>${escapeHtml(subagentFileName(file.path))}</span>${Number(file.count) > 1 ? `<small>×${Number(file.count)}</small>` : ""}</button>`).join("")}</div>`;
  }

  function rememberSubagentPlaces(root) {
    root?.querySelectorAll?.(".subagentLiveCard[data-subagent-live]").forEach(card => {
      const rect = card.getBoundingClientRect();
      if (rect.width && rect.height) subagentLivePlaces.set(card.dataset.subagentLive, { rect, at: Date.now() });
    });
  }

  // A run this Chat showed running has ended once its result card is drawn
  // and its running card is gone: the result card moves in from where the
  // running card stood and lights up once.
  function revealFinishedSubagents(root, onFinished) {
    if (!root || !subagentLivePlaces.size) return;
    const latest = new Map();
    root.querySelectorAll(".subagentRunCard[data-subagent-tool-use]").forEach(card => latest.set(card.dataset.subagentToolUse, card));
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    latest.forEach((card, id) => {
      const place = subagentLivePlaces.get(id);
      if (!id || !place || root.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`)) return;
      subagentLivePlaces.delete(id);
      const run = subagentLiveById.get(id) || {};
      subagentLiveById.delete(id);
      const to = card.getBoundingClientRect();
      const dx = place.rect.left - to.left;
      const dy = place.rect.top - to.top;
      if (!reduced && typeof card.animate === "function" && Date.now() - place.at < 5000 && Math.abs(dy) < window.innerHeight) {
        card.animate([
          { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.35 },
          { transform: "none", opacity: 1 },
        ], { duration: 420, easing: "cubic-bezier(.2, .8, .2, 1)" });
      }
      card.setAttribute("data-just-finished", "");
      window.setTimeout(() => card.removeAttribute("data-just-finished"), 2600);
      onFinished?.({ ...run, status: card.dataset.status || run.status });
    });
  }

  // While the page is hidden, its title counts the subagents that ended.
  let subagentTitleCount = 0;
  let subagentTitleBase = "";
  let subagentTitleShown = "";

  function badgeTitleForSubagent() {
    if (!subagentTitleCount) {
      subagentTitleBase = document.title;
      const restore = () => {
        if (document.hidden) return;
        document.removeEventListener("visibilitychange", restore);
        if (document.title === subagentTitleShown) document.title = subagentTitleBase;
        subagentTitleCount = 0;
      };
      document.addEventListener("visibilitychange", restore);
    }
    subagentTitleCount += 1;
    subagentTitleShown = window.PortalI18n.text`✓ ${subagentTitleCount} 个子 agent 已结束 · ${subagentTitleBase}`;
    document.title = subagentTitleShown;
  }

  const SUBAGENT_STATUS_LABELS = { completed: "已完成", running: "运行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" };
  const SUBAGENT_RUNNING_STEPS = new Set(["running", "inProgress", "in_progress"]);
  const SUBAGENT_DETAIL_POLL_MS = 2500;
  const SUBAGENT_TRACE_PAGE = 500;

  function subagentStatusLabel(status) {
    const label = SUBAGENT_STATUS_LABELS[String(status || "")];
    return label ? window.PortalI18n.t(label) : String(status || "");
  }

  function subagentStepDuration(step) {
    const ms = Date.parse(String(step.finished_at || "")) - Date.parse(String(step.started_at || ""));
    if (!Number.isFinite(ms) || ms < 0) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function subagentStepInput(input) {
    if (input === null || input === undefined || input === "") return "";
    return typeof input === "string" ? input : JSON.stringify(input, null, 2);
  }

  function subagentStepSummary(step) {
    const input = step.input;
    const value = input && typeof input === "object"
      ? ["command", "file_path", "pattern", "path", "url", "query", "description", "prompt"]
        .map(key => input[key]).find(item => typeof item === "string" && item.trim()) || ""
      : String(input || step.text || "");
    return String(value).replace(/\s+/g, " ").trim().slice(0, 240);
  }

  function subagentMarkdown(text) {
    return window.MD?.render?.(String(text || "")) || escapeHtml(String(text || "")).replace(/\n/g, "<br>");
  }

  // One step of the timeline: a call opens to its whole input and output,
  // a failed one is red and open, what the subagent said reads in full.
  function subagentTimelineStepHtml(step) {
    const index = Number(step.index ?? 0);
    const kind = String(step.kind || "");
    const linkified = value => linkifyResourceAddresses(escapeHtml(String(value || "")));
    if (kind === "tool") {
      const running = SUBAGENT_RUNNING_STEPS.has(String(step.status || ""));
      const input = subagentStepInput(step.input);
      const when = running && step.started_at
        ? `<span class="subagentStepClock" data-subagent-since="${escapeHtml(step.started_at)}">${escapeHtml(subagentClockText(step.started_at))}</span>`
        : escapeHtml(subagentStepDuration(step));
      const files = subagentFilesHtml((step.files || []).map(path => ({ path, tools: [step.tool_name], count: 1 })));
      return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}${running ? " data-step-running" : ""}><details${step.error ? " open" : ""}>
        <summary><span class="subagentStepMark" aria-hidden="true"></span><code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentStepText">${escapeHtml(subagentStepSummary(step))}</span><span class="subagentStepWhen">${when}</span></summary>
        <div class="subagentStepBody">${files}
          ${input ? window.PortalI18n.html`<div class="subagentStepLabel">输入</div><pre class="subagentStepPre">${linkified(input)}</pre>` : ""}
          ${step.error_text ? window.PortalI18n.html`<div class="subagentStepLabel">错误</div><pre class="subagentStepPre" data-step-error="1">${linkified(step.error_text)}</pre>` : ""}
          ${step.output ? window.PortalI18n.html`<div class="subagentStepLabel">输出${step.output_truncated ? window.PortalI18n.t("（较长，只显示开头）") : ""}</div><pre class="subagentStepPre">${linkified(step.output)}</pre>` : running ? window.PortalI18n.html`<small class="nativeTurnEmpty">正在运行…</small>` : ""}
        </div></details></li>`;
    }
    const label = kind === "input" ? window.PortalI18n.t("任务") : kind === "thinking" ? window.PortalI18n.t("思考")
      : step.final ? window.PortalI18n.t("回答") : window.PortalI18n.t("说");
    const at = step.at ? window.PortalUI?.formatDateTime?.(step.at, { style: "compact" }) || "" : "";
    const body = kind === "message"
      ? `<div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(step.text))}</div>`
      : `<div class="subagentStepPlain">${linkified(step.text)}</div>`;
    return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="${escapeHtml(kind)}"${step.final ? " data-step-final" : ""}><details${kind === "message" ? " open" : ""}>
      <summary><span class="subagentStepMark" aria-hidden="true"></span><span class="subagentStepKind">${escapeHtml(label)}</span><span class="subagentStepText">${escapeHtml(String(step.text || "").replace(/\s+/g, " ").trim().slice(0, 200))}</span><span class="subagentStepWhen">${escapeHtml(at)}</span></summary>
      <div class="subagentStepBody">${body}${step.text_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}</div></details></li>`;
  }

  // The steps of a run's own trace from ``offset`` on, every page of them.
  async function readSubagentTrace(residentId, traceRef, offset = 0) {
    let from = offset;
    const steps = [];
    for (;;) {
      const page = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: traceRef, offset: from, limit: SUBAGENT_TRACE_PAGE,
      }))?.run || {};
      steps.push(...(Array.isArray(page.steps) ? page.steps : []));
      const total = Number(page.total || 0);
      if (total <= from + SUBAGENT_TRACE_PAGE) return { steps, total };
      from += SUBAGENT_TRACE_PAGE;
    }
  }

  // A run's whole result: the card keeps its opening, the run's own trace
  // keeps what it last said.
  async function subagentFullResult(residentId, run) {
    const result = String(run?.result || "");
    if (!run?.result_truncated) return result;
    try {
      const head = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: run.trace_ref || run.tool_use_id, offset: 0, limit: 1,
      }))?.run || {};
      const { steps } = await readSubagentTrace(residentId, run.trace_ref || run.tool_use_id, Math.max(0, Number(head.total || 0) - 40));
      const said = steps.filter(step => step.kind === "message");
      const last = said.find(step => step.final) || said.at(-1);
      if (last && String(last.text || "").length > result.length) return String(last.text);
    } catch {}
    return result;
  }

  let subagentDetailRuntime = null;

  function ensureSubagentDetailDialog() {
    if (subagentDetailRuntime?.overlay?.isConnected) return subagentDetailRuntime;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay subagentDetailOverlay";
    overlay.dataset.portalDetailOverlay = "";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface subagentDetailSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="subagentDetailTitle">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="subagentDetailHeading"><div class="subagentDetailTitle" id="subagentDetailTitle" data-subagent-detail-title></div><div class="subagentDetailMeta" data-subagent-detail-meta></div></div>
        <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭子 agent 详情" title="关闭"></button>
      </div>
      <div class="subagentDetailBody" data-subagent-detail-body>
        <section class="subagentDetailSection" data-subagent-detail-prompt></section>
        <section class="subagentDetailSection" data-subagent-detail-files></section>
        <section class="subagentDetailSection" data-subagent-detail-result></section>
        <section class="subagentDetailSection"><h3>步骤</h3><ol class="subagentTimeline" data-subagent-detail-steps></ol><p class="subagentDetailNote" data-subagent-detail-status role="status"></p></section>
      </div>
      <div class="portalDetailFooter subagentDetailFooter" data-subagent-detail-footer></div>
    </div>`;
    document.body.appendChild(overlay);
    window.PortalDetailSurface?.mountAll?.(overlay);
    const runtime = { overlay, session: null, dialog: null };
    runtime.dialog = window.PortalUI.createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => runtime.dialog.close(),
      onClose: () => {
        runtime.session?.stop();
        runtime.session = null;
      },
    });
    overlay.querySelector("[data-portal-detail-close]")?.addEventListener("click", () => runtime.dialog.close());
    overlay.addEventListener("click", event => {
      const button = event.target instanceof Element ? event.target.closest("[data-subagent-detail-action]") : null;
      if (!button || button.disabled) return;
      if (button.dataset.subagentDetailAction === "close") runtime.dialog.close();
      else runtime.session?.action(button.dataset.subagentDetailAction);
    });
    subagentDetailRuntime = runtime;
    return runtime;
  }

  // The whole run of one subagent: its task, the files it changed, its token
  // use and every step from its own trace (GetSubagentRun), followed while
  // it runs.  ``actions(live)`` are the footer buttons of the Chat that
  // opened it, ``onAction(name, context)`` runs them.
  function openSubagentDetail(options = {}) {
    const runtime = ensureSubagentDetailDialog();
    runtime.session?.stop();
    const { overlay } = runtime;
    const residentId = String(options.residentId || "");
    const id = String(options.run?.tool_use_id || "");
    const traceRef = String(options.run?.trace_ref || id);
    const el = name => overlay.querySelector(`[data-subagent-detail-${name}]`);
    const scroller = el("body");
    const list = el("steps");
    let run = { ...(options.run || {}) };
    let steps = [];
    let total = 0;
    let timer = 0;
    let stopped = false;
    let signature = "";
    const isLive = () => options.live === true && !!document.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`);
    const openPreview = request => {
      runtime.dialog.close();
      return requestWorkspacePreview(request);
    };
    const bindLinks = root => {
      bindResponseSourcePaths(root, { onLinkPreview: openPreview });
      bindResourceChips(root, { onResourcePreview: context => openPreview({ ...context, resourceAddress: context.address, path: context.address, title: context.node?.textContent?.trim() || context.address, source: "chat" }) });
      bindLinkPreview(root, { onLinkPreview: openPreview });
    };
    const renderRun = () => {
      const live = isLive();
      if (live) run = { ...run, ...(subagentLiveById.get(id) || {}) };
      const prompt = String(run.prompt || steps.find(step => step.kind === "input")?.text || "").trim();
      const next = JSON.stringify([live, run.status, run.description, run.agent_type, run.model, run.usage, run.files, run.result, prompt, run.finished_at]);
      if (next === signature) return;
      signature = next;
      el("title").textContent = subagentRunTitle(run);
      el("meta").innerHTML = [
        `<span data-subagent-detail-state="${live ? "running" : escapeHtml(run.status || "")}">${escapeHtml(live ? window.PortalI18n.t("运行中") : subagentStatusLabel(run.status))}</span>`,
        live && run.started_at
          ? `<span data-subagent-since="${escapeHtml(run.started_at)}">${escapeHtml(subagentClockText(run.started_at))}</span>`
          : subagentRunDuration(run) ? `<span>${escapeHtml(subagentRunDuration(run))}</span>` : "",
        subagentTokens(run.usage) ? `<span>${escapeHtml(subagentTokens(run.usage))}</span>` : "",
        run.usage?.tool_uses ? `<span>${escapeHtml(window.PortalI18n.text`${run.usage.tool_uses} 次工具调用`)}</span>` : "",
        run.model ? `<span>${escapeHtml(run.model)}</span>` : "",
        run.background ? `<span>${escapeHtml(window.PortalI18n.t("后台"))}</span>` : "",
      ].filter(Boolean).join("");
      el("prompt").innerHTML = window.PortalI18n.html`<h3>它收到的任务</h3>${prompt
        ? window.PortalI18n.html`<div class="subagentDetailPrompt">${linkifyResourceAddresses(escapeHtml(prompt))}</div>${run.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}`
        : window.PortalI18n.html`<small class="nativeTurnEmpty">这个 Provider 没有给出任务全文</small>`}`;
      const files = subagentFilesHtml(run.files);
      el("files").hidden = !files;
      el("files").innerHTML = files ? window.PortalI18n.html`<h3>改动的文件</h3>${files}` : "";
      const result = !live && String(run.result || "").trim();
      el("result").hidden = !result;
      el("result").innerHTML = result
        ? window.PortalI18n.html`<h3>结果</h3><div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(run.result))}</div>${run.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">卡片只保留开头；完整回答见步骤中的最后一条</small>` : ""}`
        : "";
      el("footer").innerHTML = (options.actions?.(live) || []).filter(action => !action.hidden)
        .map(action => `<button type="button" class="portalButton${action.danger ? " is-danger" : ""}" data-subagent-detail-action="${escapeHtml(action.action)}"${action.title ? ` title="${escapeHtml(action.title)}"` : ""}>${escapeHtml(action.label)}</button>`).join("")
        + window.PortalI18n.html`<button type="button" class="portalButton" data-subagent-detail-action="close">关闭</button>`;
      for (const name of ["prompt", "files", "result"]) bindLinks(el(name));
      if (live) startSubagentClocks();
    };
    const renderSteps = (from, fresh) => {
      const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
      const open = new Set([...list.querySelectorAll(":scope > li > details")]
        .map(details => [details.parentElement.dataset.stepIndex, details.open]).filter(([, isOpen]) => isOpen).map(([index]) => index));
      [...list.children].forEach(item => { if (Number(item.dataset.stepIndex) >= from) item.remove(); });
      const template = document.createElement("template");
      template.innerHTML = fresh.map(subagentTimelineStepHtml).join("");
      const added = [...template.content.children];
      added.forEach(item => {
        if (open.has(item.dataset.stepIndex)) item.querySelector(":scope > details")?.setAttribute("open", "");
        list.append(item);
        bindLinks(item);
      });
      if (added.some(item => item.hasAttribute("data-step-running"))) startSubagentClocks();
      // A reader who scrolled down to the latest step keeps following it.
      if (nearBottom && scroller.scrollTop > 0 && isLive()) scroller.scrollTop = scroller.scrollHeight;
    };
    const load = async () => {
      // A call still running reads again from there; the rest of the trace only grows.
      const running = steps.find(step => step.kind === "tool" && SUBAGENT_RUNNING_STEPS.has(String(step.status || "")));
      const from = running ? Number(running.index) : total;
      try {
        const page = await readSubagentTrace(residentId, traceRef, from);
        if (stopped) return;
        total = page.total;
        steps = [...steps.filter(step => Number(step.index) < from), ...page.steps];
        renderSteps(from, page.steps);
        el("status").textContent = steps.length ? "" : window.PortalI18n.t("还没有记录的步骤");
      } catch (error) {
        if (stopped) return;
        el("status").textContent = error?.code === "instruction.not_found"
          ? window.PortalI18n.t("这个子 agent 还没有记录的步骤")
          : window.PortalI18n.text`步骤读取失败：${error?.message || String(error)}`;
      }
    };
    const tick = async () => {
      if (stopped) return;
      const live = isLive();
      renderRun();
      await load();
      if (stopped) return;
      renderRun();
      // A run that ended gets its last steps once more, then stays still.
      if (live) timer = window.setTimeout(tick, SUBAGENT_DETAIL_POLL_MS);
    };
    runtime.session = {
      stop() {
        stopped = true;
        window.clearTimeout(timer);
      },
      action(name) {
        return options.onAction?.(name, { run, steps, live: isLive(), close: () => runtime.dialog.close() });
      },
    };
    list.replaceChildren();
    el("status").textContent = window.PortalI18n.t("正在读取步骤…");
    scroller.scrollTop = 0;
    runtime.dialog.open();
    tick();
    return runtime;
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

  function subagentSlug(text) {
    return String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "subagent";
  }

  function subagentResultMarkdown(run, result) {
    const lines = [`# ${subagentRunTitle(run)}`, ""];
    const facts = [
      [window.PortalI18n.t("状态"), subagentStatusLabel(run.status)],
      [window.PortalI18n.t("开始"), run.started_at],
      [window.PortalI18n.t("结束"), run.finished_at],
      [window.PortalI18n.t("模型"), run.model],
      [window.PortalI18n.t("用量"), subagentTokens(run.usage)],
      ["tool_use_id", run.tool_use_id],
    ].filter(([, value]) => String(value || "").trim());
    facts.forEach(([name, value]) => lines.push(`- ${name}：${value}`));
    if (String(run.prompt || "").trim()) lines.push("", `## ${window.PortalI18n.t("任务")}`, "", String(run.prompt).trim());
    lines.push("", `## ${window.PortalI18n.t("结果")}`, "", String(result || "").trim() || window.PortalI18n.t("子 agent 没有返回文字结果"));
    const files = Array.isArray(run.files) ? run.files : [];
    if (files.length) lines.push("", `## ${window.PortalI18n.t("改动的文件")}`, "", ...files.map(file => `- ${file.path}`));
    return `${lines.join("\n")}\n`;
  }

  const SUBAGENT_STOP_REASONS = {
    unknown_subagent: "Provider 还没有登记这个子 agent（可能刚启动或已经结束），稍后再试",
    subagent_stop_unsupported: "这个 Provider 不能单独停止子 agent",
    subagent_finished: "子 agent 已经结束，不用再停止",
    no_active_turn: "这一轮已经结束",
    no_session: "这一轮的会话已经结束",
    session_changed: "会话已经换成新的，这个子 agent 不在其中",
  };

  function residentProtocolMessageToWorkspace(message = {}) {
    const parts = Array.isArray(message.content_parts) ? message.content_parts : [];
    const media = parts.filter(part => part?.type === "image" || part?.type === "file");
    const text = parts.filter(part => part?.type === "text").map(part => String(part.text || "")).join("\n");
    const legacyMediaCaption = media.map(part => `[${part.type}: ${part.name || part.type}]`).join(" ");
    // Media-only delivery has a technical text part for Resident execution.
    // The thumbnail already labels the attachment in the conversation.
    const captionParts = media.length && [legacyMediaCaption, mediaOnlyCaption(media)].includes(text.trim())
      ? parts.filter(part => part.type !== "text")
      : parts;
    let previousType = "";
    const orderedContent = captionParts.map(part => {
      let value = "";
      if (part?.type === "text") {
        value = `${previousType === "text" ? "\n" : ""}${String(part.text || "")}`;
      } else if (part?.type === "image" || part?.type === "file") {
        const path = mediaPartPath(part);
        if (path && !text.includes(path)) value = `[${part.type}: ${path}]`;
      } else return "";
      previousType = part.type;
      return value;
    }).join("");
    // “审查这些修改” sends only the reviewed turn; any text beside it is Portal's wording for the agent.
    const content = (parts.some(part => part?.type === "turn_review") ? window.PortalI18n.t("请审查这一轮的修改") : "")
      || orderedContent
      || parts.filter(part => part?.type === "async_questions").flatMap(part => part.questions || []).map(question => String(question.title || "")).join("\n")
      || parts.filter(part => ["native_turn", "resource_changes"].includes(part.type)).map(part => turnArtifactDisplay(part).title).join("\n")
      || parts.filter(part => part?.type === "subagent_run").map(subagentRunTitle).join("\n")
      || parts.filter(part => nativeInteractionNames.has(part.tool_name)).map(part => part.type === "tool_call"
        ? part.interaction ? window.PortalI18n.t("原生交互请求") : (part.questions || []).map(question => question.question).join("\n")
        : ({ answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("提问已取消"), expired: window.PortalI18n.t("提问已失效") }[part.status] || window.PortalI18n.t("提问已结束"))).join("\n");
    return {
      ...message,
      ...(message.local_delivery ? { pending: true } : {}),
      id: String(message.message_id || ""),
      role: message.role === "agent" ? "assistant" : String(message.role || "system"),
      author: String(message.origin?.sender?.id || message.author_id || ""),
      content,
      input_media: media,
      token_usage: message.usage || {},
    };
  }

  // A Family facade shows each unfinished request's progress after
  // the request and the outputs written for it so far — routing, its place in
  // the member's queue, the member's live work — not one block below all
  // messages.  The block belongs to the request's last shown message, so a new
  // output moves it and re-renders both.
  const FAMILY_OPEN_REQUEST_STATES = new Set(["waiting_members", "delivering", "routing", "scope_review", "quota_review", "queued", "running", "recovery"]);
  const FAMILY_REQUEST_ACTIONS = Object.freeze({
    // CancelInput withdraws a waiting request and stops a running one.
    "family-request-withdraw": "cancelInput",
    "family-request-stop": "cancelInput",
    "family-request-inject": "injectInput",
  });
  // A withdrawn message never reaches the Resident. A Family facade, which
  // already displayed the request, keeps only a withdrawal annotation.
  // A message that started meanwhile is stopped instead (decision_2a3ff353165c).
  const QUEUE_WITHDRAWN_NOTICE = window.PortalI18n.t("已撤回，这条消息不会被处理");
  function noticeQueueWithdrawn(result) {
    if (result?.cancelled === true || String(result?.input_state?.state || "") === "cancelled") {
      window.PortalUI?.toast?.(QUEUE_WITHDRAWN_NOTICE, "success");
    }
  }

  function noticeQueueCleared(result) {
    const count = Number(result.cancelled);
    window.PortalUI?.toast?.(window.PortalI18n.text`已清除 ${count} 条排队消息，正在执行的消息不受影响。`, "success");
  }

  function withFamilyRequestStatus(messages) {
    const status = new Map();
    const live = new Set();
    messages.forEach((item, index) => {
      const relay = item?.relay && typeof item.relay === "object" ? item.relay : null;
      if (item?.role !== "user" || !relay || !FAMILY_OPEN_REQUEST_STATES.has(String(relay.state || ""))) return;
      let end = index;
      while (end + 1 < messages.length
        && String(messages[end + 1]?.family_reply?.request_message_id || "") === item.id) end += 1;
      status.set(end, { ...relay, request_message_id: item.id });
      // Outputs of a request its member is running stay open, like a running turn's.
      if (relay.state === "running") for (let output = index + 1; output <= end; output += 1) live.add(output);
    });
    if (!status.size) return messages;
    return messages.map((item, index) => (status.has(index) || live.has(index) ? {
      ...item,
      ...(live.has(index) ? { live_output: true } : {}),
      ...(status.has(index) ? { family_status: status.get(index) } : {}),
    } : item));
  }

  // Infos delivered with a request were read with it and show inside it (concept_43c793b4f3ab).
  // Every read folds them on the server, but an info enters the trace when its request
  // starts, so it arrives as an Event after that request: fold it here the same way.
  // An info waiting again (held_info) stays a message of its own.
  function foldDeliveredInfos(messages, { handedToSteward = false } = {}) {
    const present = new Set(messages.map(message => String(message?.message_id || "")));
    const infos = new Map();
    messages.forEach(message => {
      const request = String(message?.released_with || "");
      if (!request || message?.held_info === true || !present.has(request)) return;
      if (!infos.has(request)) infos.set(request, []);
      infos.get(request).push(message);
    });
    if (!infos.size) return messages;
    const folded = new Set([...infos.values()].flat());
    const view = info => Object.fromEntries(["message_id", "author_id", "origin", "created_at", "content_parts"]
      .filter(key => key in info).map(key => [key, info[key]]));
    return messages.filter(message => !folded.has(message)).map(message => {
      const attached = infos.get(String(message?.message_id || ""));
      if (!attached) return message;
      const shown = Array.isArray(message.attached_infos) ? message.attached_infos : [];
      const known = new Set(shown.map(info => String(info?.message_id || "")));
      const added = attached.filter(info => !known.has(String(info.message_id || ""))).map(view);
      return {
        ...message,
        attached_infos: [...shown, ...added],
        ...(handedToSteward ? { attached_infos_steward: true } : {}),
      };
    });
  }

  function projectResidentConversation(metadata = {}, snapshot = {}) {
    const base = metadata && typeof metadata === "object" ? metadata : {};
    const facade = !!(snapshot.family_facade && typeof snapshot.family_facade === "object");
    const familyMembers = Array.isArray(snapshot.family_facade?.members) ? snapshot.family_facade.members : null;
    const roleIssues = (snapshot.family_facade?.nodes || []).filter(node =>
      node.availability?.status && node.availability.status !== "available");
    const familyIssueLabel = [
      ...roleIssues.map(node => `${node.role === "router" ? "Router" : "Steward"}：${node.availability.reason}`),
      snapshot.family_facade?.alert_contact?.status === "missing" ? window.PortalI18n.t("未登记真人告警联系人") : "",
    ].filter(Boolean).join("；");
    const projected = foldDeliveredInfos(Array.isArray(snapshot.messages) ? snapshot.messages : [],
      { handedToSteward: facade }).map(residentProtocolMessageToWorkspace);
    const known = new Set(projected.map(message => message.id));
    for (const message of snapshot.local_submissions || []) {
      if (!known.has(message.message_id)) projected.push(residentProtocolMessageToWorkspace(message));
    }
    const messages = facade ? withFamilyRequestStatus(projected) : projected;
    const activities = !facade && Array.isArray(snapshot.activities) ? snapshot.activities : [];
    const state = window.PortalResidentProtocol.stateView(snapshot.state);
    const controls = snapshot.controls || {};
    // A facade's work shows under each request instead.
    const live = facade ? false : state.working;
    const phase = facade ? "" : (state.work_state === "stopping" ? "stopping" : state.work_phase);
    const queue = {
      ...(snapshot.queue && typeof snapshot.queue === "object"
        ? snapshot.queue
        : { resident_id: String(snapshot.resident?.resident_id || ""), items: [] }),
      active_turn_input_supported: controls.active_turn_input === true,
    };
    const runtimeSettings = snapshot.runtime_settings || {};
    const contract = base.chat_workspace && typeof base.chat_workspace === "object"
      ? {
        ...base.chat_workspace,
        messages,
        ...(facade && familyMembers ? { rendering: {
          ...(base.chat_workspace.rendering || {}),
          assistant_name: snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family",
          empty_text: window.PortalI18n.text`这是 Family「${snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family"}」的门面。发到这里的消息由 Family Router 交给最合适的成员处理，`
            + window.PortalI18n.t("成员的回复显示在这里。还没有经过门面的对话；直接发给成员的对话在各成员自己的对话里。")
            + (familyMembers.length ? window.PortalI18n.text`成员：${familyMembers.map(member => member.title || member.resident_id || "").join("、")}。` : ""),
        } } : {}),
        ...(facade && (familyMembers || state.family_activity || familyIssueLabel) ? { header: {
          ...(base.chat_workspace.header || {}),
          subtitle_parts: (base.chat_workspace.header?.subtitle_parts || []).map(part => {
            if (part.identity !== "family") return part;
            let text = familyMembers
              ? (familyMembers.length ? window.PortalI18n.text`Family 门面（${familyMembers.length} 位成员）` : window.PortalI18n.t("Family 门面"))
              : window.PortalI18n.message(part.text);
            if (state.family_activity) text += ` · ${{ running: window.PortalI18n.t("运行中"), queued: window.PortalI18n.t("排队中"), idle: window.PortalI18n.t("空闲") }[state.display_work_state]}${state.family_activity.running_count > 1 ? window.PortalI18n.text`（${state.family_activity.running_count} 个节点）` : ""}`;
            return { ...part, text };
          }).concat(familyIssueLabel ? [{ text: familyIssueLabel, identity: "family_availability" }] : []),
        } } : {}),
        runtime: { live, details: activities, phase },
        composer: {
          ...(base.chat_workspace.composer || {}),
          can_send: controls.can_send === true,
          disabled_reason: controls.disabled_reason || "",
          send_hint: controls.send_hint ? window.PortalI18n.t(controls.send_hint) : "",
          execution_gate: state.execution_gate,
          intake_state: state.intake_state,
          can_stop: controls.can_stop === true,
          queue,
          runtime_settings: runtimeSettings,
        },
        history: {
          can_load_older: Boolean(String(snapshot.next_page_token || "").trim()),
        },
        inheritance: snapshot.inheritance && typeof snapshot.inheritance === "object" ? snapshot.inheritance : null,
      }
      : null;
    return {
      ...base,
      ...(Object.prototype.hasOwnProperty.call(snapshot, "family_facade") ? { family_facade: snapshot.family_facade } : {}),
      messages,
      runtime_activity: { live, details: activities, phase },
      resident_state: state,
      queue,
      input_states: Array.isArray(snapshot.input_states) ? snapshot.input_states : [],
      runtime_settings: runtimeSettings,
      ...(contract ? { chat_workspace: contract } : {}),
    };
  }

  async function loadResidentConversation(target, options = {}) {
    const normalized = normalizeResidentTarget(target);
    if (!normalized.resident_id) throw new TypeError("resident_id required");
    const [projection, snapshot] = await Promise.all([
      window.PortalResidentProjection.load(normalized.resident_id, {
        force: options.force === true,
      }),
      window.PortalResidentProtocol.loadSnapshot(normalized.resident_id, {
        force: options.force === true,
        historyLimit: Math.max(
          1,
          Number(options.historyLimit || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE),
        ),
      }),
    ]);
    return projectResidentConversation(projection, snapshot);
  }

  const nativeInteractionNames = new Set(["request_user_input", "request_approval", "mcp_elicitation"]);

  class ResidentQuestionCards {
    constructor(panel) {
      this.panel = panel;
      // Native approval and MCP cards, by request.
      this.cards = new Map();
      this.turnEditors = new Map();
      this.nativeOutcomes = new Map();
      this.sending = new Set();
      // Each open question group's chosen answers and current question, by
      // group key; kept per actor and conversation until the group closes.
      this.progress = new Map();
      // The card of the one open question group being answered, shown where
      // it was asked; ``chosen`` names an earlier open group picked for it.
      this.active = { key: "", element: null, error: "" };
      this.chosen = "";
      // Groups closed here, until the conversation shows their closed slot.
      this.closed = new Set();
      this.scope = "";
    }

    reset() {
      // Reloading the same conversation keeps its message nodes and their open
      // diffs, so only editors whose card has left the page are released here.
      this.releaseTurnEditors();
      this.scope = "";
      for (const card of this.cards.values()) card.form.remove();
      this.cards.clear();
      this.nativeOutcomes.clear();
      this.progress.clear();
      this.chosen = "";
      this.closed.clear();
      this.closeActive();
    }

    progressKey(scope = this.scope) {
      return ["portal.residentQuestionProgress.v1", this.panel.options.draftOwner || "anonymous", scope]
        .map(part => encodeURIComponent(part)).join(":");
    }

    saveProgress() {
      if (!this.scope) return;
      if (this.progress.size) window.PortalState.storage.writeJson(this.progressKey(), Object.fromEntries(this.progress));
      else window.PortalState.storage.remove(this.progressKey());
    }

    // A question group is the questions one Agent message asks (its
    // ``async_questions`` part) or one native ``request_user_input`` request.
    // A message's group is answered by one answer message and stays open
    // until then, whatever later messages and groups come, unless a person
    // closes it (its answer slot is then cancelled as question_set_closed);
    // the server applies the same rule.  A member reply shown on a Family
    // facade carries the state the member's own Conversation gives its group.
    questionGroups(snapshot) {
      const messages = snapshot.messages || [];
      const queued = new Set((snapshot.queue?.items || []).map(item => item.message_id));
      const states = new Map((snapshot.input_states || []).map(input => [input.message_id, input.state]));
      const closed = new Set([...this.closed, ...(snapshot.input_states || [])
        .filter(input => input.error?.code === "question_set_closed").map(input => input.message_id)]);
      const answers = new Map();
      for (const message of messages) {
        if (message.role !== "user") continue;
        for (const part of message.content_parts || []) {
          if (part.type !== "async_question_answer") continue;
          const merged = answers.get(part.source_message_id) || {};
          for (const entry of part.answers || []) merged[entry.question_index] = entry.answer;
          answers.set(part.source_message_id, merged);
        }
      }
      const nativeResults = new Map(messages.flatMap(message => (message.content_parts || [])
        .filter(part => part.type === "tool_result" && part.tool_name === "request_user_input")
        .map(part => [part.request_id, part])));
      const groups = [];
      for (const message of messages) {
        if (message.role !== "agent") continue;
        for (const part of message.content_parts || []) {
          if (part.type === "async_questions") {
            const answered = answers.has(message.message_id)
              || (!closed.has(part.answer_message_id) && (states.has(part.answer_message_id) || queued.has(part.answer_message_id)));
            groups.push({
              kind: "async", key: part.answer_message_id, source_message_id: message.message_id,
              // A member's set shown on a facade is closed in the member's own conversation.
              closable: !message.family_reply,
              // Permissions are lent to the Resident this conversation belongs to, never through a facade.
              lendable: !message.family_reply,
              questions: (part.questions || []).map(question => ({ id: question.question_index, title: question.title, options: question.options || [],
                credentials: Array.isArray(question.credentials) ? question.credentials : [] })),
              state: answered ? "answered" : closed.has(part.answer_message_id) ? "closed" : part.state || "open",
              answers: answers.get(message.message_id) || {}, delivery: states.get(part.answer_message_id) || "",
            });
          } else if (part.type === "tool_call" && part.tool_name === "request_user_input" && !part.interaction) {
            const result = nativeResults.get(part.request_id)
              || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
            groups.push({
              kind: "native", key: part.request_id, source_message_id: message.message_id, native: part,
              questions: (part.questions || []).map(question => ({ id: question.id, title: question.question, options: (question.options || []).map(option => option.label) })),
              state: result.status === "pending" ? "open" : result.status,
              answers: Object.fromEntries(Object.entries(result.answers || {}).map(([id, answer]) => [id, (answer.answers || []).join("、")])),
              delivery: "",
            });
          }
        }
      }
      return groups;
    }

    render(snapshot) {
      const target = this.panel.workspace.target();
      const scope = residentSessionScope(target);
      if (this.scope !== scope) {
        this.reset();
        this.scope = scope;
        this.progress = new Map(Object.entries(window.PortalState.storage.readJson(this.progressKey(), {}) || {}));
      }
      const messages = new Map((snapshot.messages || []).map(message => [message.message_id, message]));
      this.bindTurnArtifacts(messages, target, snapshot.controls?.can_send === true);
      const hosts = new Map([...this.panel.workspace.els.messages.querySelectorAll("[data-resident-question]")]
        .map(element => [element.dataset.residentQuestion, element]));
      const canSend = snapshot.controls?.can_send === true;
      const groups = this.questionGroups(snapshot);
      // One open group at a time is answered where it was asked: the one
      // picked from its summary or already being edited, else the newest;
      // the others wait in summary.
      const answerable = canSend ? groups.filter(group => group.state === "open" && hosts.has(group.key)) : [];
      const open = answerable.find(group => group.key === this.chosen) || answerable.at(-1) || null;
      for (const group of groups) {
        const host = hosts.get(group.key);
        if (host && group !== open) this.renderSummary(host, group, canSend);
      }
      const progressCount = this.progress.size;
      for (const group of groups) {
        // Discard the choices of a group that has closed; a group off this
        // page keeps them until it is seen again.
        if (group.state !== "open") this.progress.delete(group.key);
      }
      if (this.progress.size !== progressCount) this.saveProgress();
      if (open) this.renderActive(open, hosts.get(open.key), target, scope);
      else this.closeActive();
      this.renderInteractions(messages, hosts, target, scope, canSend);
    }

    renderSummary(host, group, canSend) {
      const labels = {
        open: canSend ? window.PortalI18n.t("等待回答") : window.PortalI18n.t("只有拥有发送权限的用户可以回答。"),
        expired: group.kind === "native" ? window.PortalI18n.t("请求已失效") : window.PortalI18n.t("已失效：原对话已清空"),
        closed: window.PortalI18n.t("已关闭，不再回答"),
        cancelled: window.PortalI18n.t("请求已取消"),
        answered: ({
          queued: window.PortalI18n.t("回答已排队，将在可接收时送达"),
          failed: window.PortalI18n.t("回答已接收，但执行失败；请查看对话错误"),
          cancelled: window.PortalI18n.t("回答对应的执行已取消"),
        })[group.delivery] || window.PortalI18n.t("回答已送达"),
      };
      const status = labels[group.state] || window.PortalI18n.t("请求已结束");
      const pick = canSend && group.state === "open";
      const closing = this.sending.has(`${this.scope}:${group.key}`);
      const signature = JSON.stringify([group.state, status, group.answers, pick, closing]);
      if (host.dataset.questionSummary === signature) return;
      host.dataset.questionSummary = signature;
      host.innerHTML = window.PortalI18n.html`<div class="residentQuestionSummary" data-state="${escapeHtml(group.state)}">
        <div class="residentQuestionSummaryHead"><span class="residentQuestionTitle">${escapeHtml(window.PortalI18n.text`Agent 的问题 · ${group.questions.length}`)}</span><span class="residentQuestionStatus" role="status">${escapeHtml(status)}</span>${pick ? window.PortalI18n.html`<span class="residentQuestionSummaryActions"><button type="button" class="portalButton is-compact" data-question-pick ${closing ? "disabled" : ""}>回答</button>${group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close ${closing ? "disabled" : ""}>不再回答</button>` : ""}</span>` : ""}</div>
        <ol>${group.questions.map(question => {
          const answer = group.answers[question.id];
          return window.PortalI18n.html`<li><span>${escapeHtml(question.title)}</span>${answer ? window.PortalI18n.html`<strong>${escapeHtml(answer)}</strong>` : ""}</li>`;
        }).join("")}</ol>
      </div>`;
      host.querySelector("[data-question-pick]")?.addEventListener("click", () => {
        this.chosen = group.key;
        this.render(this.panel.snapshot() || {});
        this.active.element?.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
      });
      host.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
    }

    closeActive() {
      this.active.element?.remove();
      this.active = { key: "", element: null, error: "" };
    }

    renderActive(group, host, target, scope) {
      if (this.active.key !== group.key) {
        this.closeActive();
        this.active = { key: group.key, element: document.createElement("div"), error: "" };
        this.active.element.className = "residentQuestionCard residentQuestionStepper";
        this.active.element.setAttribute("role", "group");
        this.active.element.setAttribute("aria-label", window.PortalI18n.t("Agent 的问题"));
        this.active.element.addEventListener("keydown", event => this.activeKey(event));
        this.active.element.addEventListener("focusin", () => { this.chosen = group.key; });
      }
      this.active.group = group;
      this.active.target = target;
      this.active.scope = scope;
      if (this.active.element.parentElement !== host) {
        delete host.dataset.questionSummary;
        host.replaceChildren(this.active.element);
      }
      const sending = this.sending.has(`${scope}:${group.key}`);
      const signature = JSON.stringify([this.progressOf(group), sending, this.active.error]);
      if (this.active.signature !== signature) this.drawActive();
    }

    progressOf(group) {
      if (!this.progress.has(group.key)) this.progress.set(group.key, { answers: {}, step: 0 });
      return this.progress.get(group.key);
    }

    drawActive() {
      const { group, element, scope } = this.active;
      const progress = this.progressOf(group);
      const sending = this.sending.has(`${scope}:${group.key}`);
      this.active.signature = JSON.stringify([progress, sending, this.active.error]);
      const step = Math.min(progress.step, group.questions.length - 1);
      const question = group.questions[step];
      const chosen = progress.answers[question.id] || "";
      const custom = chosen && !question.options.includes(chosen) ? chosen : "";
      // A permission request: its fixed options are the server's own words, the
      // first approves, and only the exact permissions it lists can be lent.
      const lending = question.credentials?.length > 0;
      const credentials = lending ? window.PortalI18n.html`<div class="residentQuestionCredentials">
          <span>${escapeHtml(group.lendable ? window.PortalI18n.t("同意后随这条回答交给 Resident 的权限：") : window.PortalI18n.t("成员在 Family 内提出的权限请求不能经门面交出"))}</span>
          <ul>${question.credentials.map(item => window.PortalI18n.html`<li><code>${escapeHtml(item.a)}</code> · <code>${escapeHtml(item.resource_id)}</code></li>`).join("")}</ul>
          <span class="residentQuestionCredentialScope">${escapeHtml(window.PortalI18n.t("只在 Resident 处理这条回答期间有效，处理完即失效。"))}</span>
        </div>` : "";
      const hint = this.active.error || (sending ? window.PortalI18n.t("正在提交…")
        : group.kind === "native" ? (group.native.is_blocking ? window.PortalI18n.t("Agent 正在等待你的回答") : window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。"))
        : window.PortalI18n.t("这组问题在回答前一直保留"));
      element.classList.toggle("has-error", Boolean(this.active.error));
      element.setAttribute("aria-busy", String(sending));
      element.innerHTML = window.PortalI18n.html`<div class="residentQuestionStepHead">
          <span class="residentQuestionStepProgress">${escapeHtml(window.PortalI18n.text`问题 ${step + 1}/${group.questions.length}`)}</span>
          <span class="residentQuestionStatus" role="status">${escapeHtml(hint)}</span>
          ${step > 0 ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-back>上一题</button>` : ""}
          ${group.kind === "native" ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-cancel>取消请求</button>` : ""}
          ${group.kind === "async" && group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close>不再回答</button>` : ""}
        </div>
        <div class="residentQuestionTitle">${escapeHtml(question.title)}</div>
        ${credentials}
        <div class="residentQuestionChoices">${question.options.map((option, index) => window.PortalI18n.html`<button type="button" class="portalButton residentQuestionChoice" data-question-option="${index}" aria-pressed="${String(option === chosen)}">${index < 9 ? window.PortalI18n.html`<kbd>${index + 1}</kbd>` : ""}<span>${escapeHtml(lending ? window.PortalI18n.t(option) : option)}</span></button>`).join("")}</div>
        ${lending ? "" : window.PortalI18n.html`<div class="residentQuestionCustom"><input type="text" data-question-custom value="${escapeHtml(custom)}" placeholder="${escapeHtml(question.options.length ? window.PortalI18n.t("其他回答，按 Enter 确认") : window.PortalI18n.t("输入回答，按 Enter 确认"))}" aria-label="${escapeHtml(window.PortalI18n.text`回答：${question.title}`)}" /><button type="button" class="portalButton is-compact is-primary" data-question-confirm>${escapeHtml(step === group.questions.length - 1 ? window.PortalI18n.t("提交回答") : window.PortalI18n.t("下一题"))}</button></div>`}`;
      for (const control of element.querySelectorAll("button, input")) control.disabled = sending;
      if (lending && !group.lendable) element.querySelector('[data-question-option="0"]').disabled = true;
      element.querySelectorAll("[data-question-option]").forEach(button => button.addEventListener("click", () => {
        this.answer(question.options[Number(button.dataset.questionOption)]);
      }));
      const input = element.querySelector("[data-question-custom]");
      element.querySelector("[data-question-confirm]")?.addEventListener("click", () => this.answer(input.value.trim() || chosen));
      element.querySelector("[data-question-back]")?.addEventListener("click", () => {
        progress.step = step - 1;
        this.saveProgress();
        this.active.error = "";
        this.drawActive();
        element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
      });
      element.querySelector("[data-question-cancel]")?.addEventListener("click", () => {
        void this.sendNativeQuestions(group, "cancel");
      });
      element.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
    }

    activeKey(event) {
      // Keys typed in the card answer the question; the conversation's own shortcuts do not see them.
      event.stopPropagation();
      if (event.isComposing) return;
      const input = event.target.closest?.("[data-question-custom]");
      if (input) {
        if (event.key === "Enter" && input.value.trim()) {
          event.preventDefault();
          this.answer(input.value.trim());
        }
        return;
      }
      const option = /^[1-9]$/.test(event.key) ? this.active.element.querySelector(`[data-question-option="${Number(event.key) - 1}"]`) : null;
      if (option && !option.disabled && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        option.click();
      }
    }

    answer(value) {
      const { group } = this.active;
      if (!group || !value || this.sending.has(`${this.active.scope}:${group.key}`)) return;
      const progress = this.progressOf(group);
      const step = Math.min(progress.step, group.questions.length - 1);
      progress.answers[group.questions[step].id] = value;
      this.active.error = "";
      // Go on to the next open question; a revised earlier answer walks the
      // following questions again, and the last answer submits the group.
      const unanswered = group.questions.map((question, index) => index).filter(index => !progress.answers[group.questions[index].id]);
      const next = unanswered.find(index => index > step) ?? unanswered[0] ?? (step + 1 < group.questions.length ? step + 1 : -1);
      if (next < 0) {
        progress.step = step;
        this.saveProgress();
        void (group.kind === "native" ? this.sendNativeQuestions(group, "answer") : this.sendAnswers(group));
        return;
      }
      progress.step = next;
      this.saveProgress();
      this.drawActive();
      this.active.element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
    }

    async sendAnswers(group) {
      const { target, scope } = this.active;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      const progress = this.progressOf(group);
      const answers = group.questions.map(question => ({ question_index: question.id, answer: progress.answers[question.id] }));
      // The user lends, as themself, exactly what each approved request lists.
      const lent = new Map();
      if (group.lendable) {
        for (const question of group.questions) {
          if (!question.credentials?.length || progress.answers[question.id] !== question.options[0]) continue;
          for (const item of question.credentials) lent.set(`${item.a}\n${item.resource_id}`, { a: item.a, resource_id: item.resource_id });
        }
      }
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        const result = await window.PortalResidentProtocol.sendInput({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
          ...(lent.size ? { temporary_credentials: [...lent.values()] } : {}),
          message: {
            message_id: group.key,
            role: "user",
            content_parts: [
              { type: "text", text: answers.map(entry => entry.answer).join("\n") },
              { type: "async_question_answer", source_message_id: group.source_message_id, answers },
            ],
          },
        });
        if (this.scope === scope) {
          this.progress.delete(group.key);
          this.saveProgress();
          this.panel.options.onAcknowledgement?.(result, { messageIdentity: group.key }, this.panel);
        }
      } catch (error) {
        if (this.scope === scope && this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    // Closing a group leaves it unanswered and tells the Resident nothing; the
    // server records it on the group's answer slot (AnswerResidentRequest cancel).
    async closeGroup(group) {
      const target = this.panel.workspace.target();
      const scope = this.scope;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: group.source_message_id, action: "cancel" });
        if (this.scope === scope) this.closed.add(group.key);
      } catch (error) {
        if (this.scope === scope) window.PortalUI?.toast?.(error?.message || window.PortalI18n.t("关闭失败，请重试"), "error");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    // A native request takes its answer through the Execution's control channel;
    // ``status`` reads whether the Provider has taken it.
    async respondNative(target, scope, sourceMessageId, payload) {
      let result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, ...payload });
      for (let attempt = 0; result.status === "pending" && attempt < 60; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
        if (this.scope !== scope) return null;
        result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, action: "status" });
      }
      return result;
    }

    async sendNativeQuestions(group, action) {
      const { target, scope } = this.active;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      const progress = this.progressOf(group);
      const answers = action === "cancel" ? {} : Object.fromEntries(group.questions.map(question => [question.id, { answers: [progress.answers[question.id]] }]));
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        const result = await this.respondNative(target, scope, group.source_message_id, { action, answers });
        if (!result) return;
        if (result.status === "expired" || (result.status === "cancelled" && action !== "cancel")) {
          this.nativeOutcomes.set(group.key, { status: "expired", answers: {} });
        }
        if (!(result.status === "answered" || (result.status === "cancelled" && action === "cancel"))) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
        this.nativeOutcomes.set(group.key, { status: action === "cancel" ? "cancelled" : "answered", answers });
        this.progress.delete(group.key);
        this.saveProgress();
        this.panel.protocol?.load?.({ force: true });
      } catch (error) {
        if (this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    renderInteractions(messages, hosts, target, scope, canSend) {
      const questions = [];
      const nativeResults = new Map([...messages.values()].flatMap(message => (message.content_parts || [])
        .filter(part => part.type === "tool_result" && nativeInteractionNames.has(part.tool_name))
        .map(part => [part.request_id, part])));
      for (const message of messages.values()) {
        if (message.role !== "agent") continue;
        for (const part of message.content_parts || []) {
          if (part.type !== "tool_call" || !part.interaction || !nativeInteractionNames.has(part.tool_name)) continue;
          const result = nativeResults.get(part.request_id)
            || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
          const titles = { command: "运行命令审批", file_change: window.PortalI18n.t("文件修改审批"), permissions: window.PortalI18n.t("额外权限审批"), mcp: window.PortalI18n.t("工具需要补充信息") };
          questions.push({ title: titles[part.interaction.kind], native: part, key: part.request_id,
            source_message_id: message.message_id, accepted: result.status !== "pending", state: result.status });
        }
      }
      const visible = questions.filter(question => hosts.has(question.key));
      const visibleIds = new Set(visible.map(question => question.key));
      for (const [key, card] of this.cards) {
        if (visibleIds.has(key)) continue;
        card.form.remove();
        this.cards.delete(key);
      }
      for (const question of visible) {
        let card = this.cards.get(question.key);
        if (!card) {
          card = this.createInteractionCard(question, target, scope);
          this.cards.set(question.key, card);
        }
        const host = hosts.get(question.key);
        if (card.form.parentElement !== host) host.append(card.form);
        card.question = question;
        card.canSend = canSend;
        // A later authoritative terminal result supersedes a delivery/check
        // error from the original response; it must not invite another answer.
        if (question.accepted) card.error = "";
        const sending = this.sending.has(`${scope}:${question.key}`);
        card.fields.hidden = question.accepted;
        for (const field of card.form.querySelectorAll("input, select, textarea, button")) field.disabled = !canSend || sending || question.accepted;
        card.form.setAttribute("aria-busy", String(sending));
        card.form.classList.toggle("answered", question.accepted);
        card.status.textContent = card.error || ({ answered: window.PortalI18n.t("已送达原请求"), declined: window.PortalI18n.t("已拒绝"), cancelled: window.PortalI18n.t("已取消"), expired: window.PortalI18n.t("请求已失效，不能再提交") }[question.state])
          || (!canSend ? window.PortalI18n.t("只有拥有发送权限的用户可以回答。") : !question.native.is_blocking ? window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。") : question.native.interaction.kind === "mcp" ? window.PortalI18n.t("等待你的回答") : window.PortalI18n.t("等待你的审批"));
      }
    }

    createInteractionCard(question, target, scope) {
      const interaction = question.native.interaction;
      const request = interaction.request;
      const form = document.createElement("form");
      form.className = "residentQuestionCard";
      form.dataset.questionId = question.key;
      const title = document.createElement("div");
      title.className = "residentQuestionTitle";
      title.textContent = question.title;
      const description = document.createElement("p");
      description.textContent = interaction.kind === "mcp" ? `${request.serverName}：${request.message}` : request.reason || window.PortalI18n.t("请核对操作范围。允许仅针对本次操作；额外权限仅授予本轮。");
      const fields = document.createElement("div");
      const inputs = [];
      if (interaction.kind !== "mcp") {
        const details = document.createElement("pre");
        details.className = "nativeApprovalDetails";
        details.textContent = JSON.stringify(Object.fromEntries(Object.entries(request).filter(([key]) =>
          ["command", "cwd", "permissions", "grantRoot", "networkApprovalContext", "additionalPermissions", "reason"].includes(key))), null, 2);
        fields.append(details);
        if (interaction.kind === "file_change") {
          const scope = interaction.file_change;
          const changes = scope && scope.thread_id === request.threadId && scope.turn_id === request.turnId
            && scope.item_id === request.itemId && Array.isArray(scope.changes) ? scope.changes : [];
          if (!changes.length) {
            const unavailable = document.createElement("p");
            unavailable.className = "nativeApprovalScopeMissing";
            unavailable.textContent = window.PortalI18n.t("Provider 未提供可核对的文件改动详情。");
            fields.append(unavailable);
          }
          for (const change of changes) {
            const file = document.createElement("section");
            file.className = "nativeApprovalFile";
            const heading = document.createElement("div");
            heading.className = "nativeApprovalFilePath";
            const kind = change.kind?.type;
            const operation = { add: "新增", delete: "删除", update: "修改" }[kind];
            heading.textContent = `${operation ? window.PortalI18n.t(operation) + " · " : ""}${change.path}`;
            if (change.kind?.move_path) heading.textContent += ` → ${change.kind.move_path}`;
            const diff = document.createElement("pre");
            diff.className = "nativeApprovalDetails";
            diff.textContent = typeof change.diff === "string" ? change.diff : window.PortalI18n.t("Provider 未提供此文件的差异内容。");
            file.append(heading, diff);
            fields.append(file);
          }
        }
      } else if (request.mode === "url") {
        const link = document.createElement("a");
        link.textContent = window.PortalI18n.t("在新窗口完成工具请求");
        try {
          const url = new URL(request.url);
          if (url.protocol === "https:" && !url.username && !url.password) link.href = url.href;
        } catch (_) { link.removeAttribute("href"); }
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        fields.append(link);
      } else {
        for (const [name, schema] of Object.entries(request.requestedSchema?.properties || {})) {
          const label = document.createElement("label");
          label.className = "residentMcpField";
          const heading = document.createElement("span");
          heading.textContent = schema.title || name;
          const alternatives = schema.oneOf || schema.anyOf || (schema.enum || []).map(value => ({ const: value, title: String(value) }));
          const options = schema.type === "array" ? (schema.items?.anyOf || schema.items?.oneOf || (schema.items?.enum || []).map(value => ({ const: value, title: String(value) }))) : alternatives;
          const field = document.createElement(options.length ? "select" : "input");
          field.setAttribute("aria-label", schema.title || name);
          field.name = name;
          field.required = (request.requestedSchema.required || []).includes(name);
          if (options.length) {
            field.multiple = schema.type === "array";
            if (!field.multiple) field.append(new Option("请选择…", ""));
            for (const option of options) field.append(new Option(option.title || String(option.const), JSON.stringify(option.const)));
          } else {
            field.type = schema.type === "boolean" ? "checkbox" : ["number", "integer"].includes(schema.type) ? "number" : "text";
            if (field.type === "number") field.step = schema.type === "integer" ? "1" : "any";
            if (schema.minimum != null) field.min = schema.minimum;
            if (schema.maximum != null) field.max = schema.maximum;
            if (schema.minLength != null) field.minLength = schema.minLength;
            if (schema.maxLength != null) field.maxLength = schema.maxLength;
            if (schema.type === "boolean") field.required = false;
          }
          if (schema.default != null) {
            if (schema.type === "boolean") field.checked = schema.default;
            else if (!field.multiple) field.value = options.length ? JSON.stringify(schema.default) : String(schema.default);
          }
          label.append(heading, field);
          if (schema.description) { const hint = document.createElement("small"); hint.textContent = schema.description; label.append(hint); }
          fields.append(label);
          inputs.push({ name, schema, field, options });
        }
      }
      const footer = document.createElement("div");
      footer.className = "residentQuestionFooter";
      const status = document.createElement("div");
      status.className = "residentQuestionStatus";
      status.setAttribute("role", "status");
      footer.append(status);
      const card = { form, fields, status, question, canSend: false, error: "" };
      card.content = () => {
        if (interaction.kind !== "mcp" || request.mode === "url") return null;
        return Object.fromEntries(inputs.filter(({ field, schema }) => schema.type === "boolean" || field.value !== "").map(({ name, schema, field, options }) => [name,
          field.multiple ? [...field.selectedOptions].map(option => JSON.parse(option.value)) : options.length ? JSON.parse(field.value) :
          schema.type === "boolean" ? field.checked : ["number", "integer"].includes(schema.type) ? Number(field.value) : field.value]));
      };
      for (const [action, text] of [["decline", "拒绝"], ["cancel", "取消请求"], ["accept", interaction.kind === "mcp" ? "提交" : "允许本次"]]) {
        if (Array.isArray(request.availableDecisions) && !request.availableDecisions.includes(action)) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = `portalButton${action === "accept" ? " is-primary" : ""}`;
        button.textContent = text;
        button.addEventListener("click", () => {
          if (action === "accept" && !form.reportValidity()) return;
          void this.sendNativeInteraction(card, target, scope, action);
        });
        footer.append(button);
      }
      form.addEventListener("submit", event => event.preventDefault());
      form.append(title, description, fields, footer);
      return card;
    }

    async sendNativeInteraction(card, target, scope, action) {
      const key = `${scope}:${card.question.key}`;
      if (!card.canSend || card.question.accepted || this.sending.has(key)) return;
      this.sending.add(key);
      card.error = "";
      this.render(this.panel.snapshot() || {});
      try {
        const result = await this.respondNative(target, scope, card.question.source_message_id, {
          action, content: action === "accept" ? card.content() : null,
        });
        if (!result) return;
        if (!["answered", "declined", "cancelled", "expired"].includes(result.status)) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
        const outcome = result.status === "answered"
          ? (action === "cancel" ? "cancelled" : action === "decline" ? "declined" : "answered")
          : result.status;
        this.nativeOutcomes.set(card.question.key, { status: outcome, answers: {} });
        card.question.accepted = true;
        this.panel.protocol?.load?.({ force: true });
      } catch (error) {
        card.error = error?.message || "回答发送失败，请重试";
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    destroy() {
      this.reset();
      this.releaseTurnEditors(true);
    }

    releaseTurnEditors(all = false) {
      for (const [root, editor] of this.turnEditors) {
        if (all || !root.isConnected) { editor?.destroy?.(); this.turnEditors.delete(root); }
      }
    }

    bindTurnArtifacts(messages, target, canSend) {
      this.releaseTurnEditors();
      for (const host of this.panel.workspace.els.messages.querySelectorAll("[data-native-turn]")) {
        const source = messages.get(host.dataset.nativeTurn);
        const part = source?.content_parts?.find(candidate => ["native_turn", "resource_changes"].includes(candidate.type));
        if (!part) continue;
        const review = host.querySelector("[data-turn-review]");
        if (review) review.disabled = !canSend || Boolean(part.diff_truncated) || review.dataset.sending === "1";
        if (host.dataset.bound) continue;
        host.dataset.bound = "1";
        const details = host.querySelector("details");
        const root = host.querySelector("[data-turn-diff]");
        const fileSelect = host.querySelector("[data-turn-diff-file]");
        const preview = host.querySelector("[data-turn-file-preview]");
        let files = [];
        let activeEditor = null;
        const selectedFile = () => fileSelect?.value !== "" ? files[Number(fileSelect?.value)] : null;
        const selectedDiff = () => fileSelect?.value ? files[Number(fileSelect.value)]?.diff || part.diff : part.diff;
        const showDiff = () => {
          const value = selectedDiff();
          root.style.height = `${Math.min(380, Math.max(96, value.split("\n").length * 19 + 20))}px`;
          fileSelect.title = fileSelect.selectedOptions[0]?.textContent || window.PortalI18n.t("本轮全部修改");
          const file = selectedFile();
          preview.disabled = !file?.path || file.deleted;
          preview.title = file?.deleted ? window.PortalI18n.t("文件已删除，仅可查看 Diff") : file?.path ? window.PortalI18n.t("在 Preview 中打开当前文件") : window.PortalI18n.t("请先选择一个文件");
          if (activeEditor) activeEditor.setValue(value);
          else root.textContent = value;
        };
        fileSelect?.addEventListener("change", showDiff);
        preview?.addEventListener("click", async () => {
          const file = selectedFile();
          if (!file?.path || file.deleted || preview.disabled) return;
          preview.disabled = true;
          try {
            const open = this.panel.workspace.options.onLinkPreview || requestWorkspacePreview;
            const opened = await open({
              href: file.path, path: file.path, title: file.path, node: preview,
              resourceAddress: file.resourceAddress || "",
              source: "chat", target: this.panel.workspace.els.messages,
            });
            if (opened === false) throw new Error("当前页面没有可用的 Preview");
          } catch (error) { this.panel.reportError(error); }
          finally { if (preview.isConnected) showDiff(); }
        });
        details?.addEventListener("toggle", async () => {
          if (!details.open || this.turnEditors.has(root)) return;
          this.turnEditors.set(root, null);
          try {
            if (!window.PortalCodeEditor) {
              window.portalTurnEditorLoading ||= new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = "/static/portal_code_editor.js";
                script.charset = "utf-8";
                script.onload = resolve; script.onerror = reject; document.head.append(script);
              });
              await window.portalTurnEditorLoading;
            }
            if (!root.isConnected) return;
            files = window.PortalCodeEditor.splitDiffFiles(part.diff);
            if (part.type === "resource_changes") {
              // Resource diffs have one block per write, in changes order. Their
              // address@change_id labels identify snapshots, not openable files.
              files = files.map((file, index) => {
                const change = part.changes?.[index];
                return { ...file, label: change?.path || change?.address || file.label,
                  path: change?.address || "", resourceAddress: change?.address || "" };
              });
            }
            fileSelect.replaceChildren(...(files.length > 1 ? [new Option(`全部修改（${files.length} 个文件）`, "")] : []), ...files.map((file, index) => new Option(file.label, String(index))));
            fileSelect.parentElement.hidden = !files.length;
            showDiff();
            root.textContent = "";
            const editor = await window.PortalCodeEditor.mount({ root, value: selectedDiff(), language: "diff", readOnly: true, ariaLabel: window.PortalI18n.t("本轮修改 Diff") });
            if (!root.isConnected) { editor.destroy(); return; }
            activeEditor = editor;
            this.turnEditors.set(root, editor);
          } catch (_) { root.textContent = selectedDiff(); }
          finally { fileSelect.disabled = false; }
        });
        review?.addEventListener("click", async () => {
          if (review.disabled) return;
          review.dataset.sending = "1"; review.disabled = true;
          try {
            host.dataset.reviewMessage ||= `msg_${globalThis.PortalUIPrimitives.randomUUID().replaceAll("-", "")}`;
            await window.PortalResidentProtocol.sendInput({ resident_id: target.resident_id, message: {
              message_id: host.dataset.reviewMessage, role: "user", content_parts: [{ type: "turn_review", segment_id: part.segment_id }],
            } });
            review.textContent = window.PortalI18n.t("审查已排队");
          } catch (error) { review.dataset.sending = ""; review.disabled = !canSend; review.textContent = window.PortalI18n.t("重试审查"); review.title = error.message; }
        });
      }
    }
  }

  function residentMessageBody(item, context = {}) {
    const subagent = item.content_parts?.find(part => part.type === "subagent_run");
    if (subagent && messageRole(item) !== "human") return subagentRunCard(item, subagent, context);
    const turn = item.content_parts?.find(part => ["native_turn", "resource_changes"].includes(part.type));
    if (turn && messageRole(item) !== "human") {
      const display = turnArtifactDisplay(turn);
      if (!display.hasContent) return window.PortalI18n.html`<small class="nativeTurnEmpty">${escapeHtml(display.title)}</small>`;
      const labels = { pending: window.PortalI18n.t("待处理"), inProgress: window.PortalI18n.t("进行中"), completed: window.PortalI18n.t("已完成") };
      return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard" data-native-turn="${escapeHtml(item.id)}">
        <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle">${escapeHtml(display.title)}</div><span class="messageSystemBadge">执行记录</span></div>${turn.diff ? window.PortalI18n.html`<button type="button" class="portalButton" data-turn-review title="审查本轮全部修改">审查这些修改</button>` : ""}</header>
        ${turn.type === "resource_changes" ? window.PortalI18n.html`<ul class="resourceTurnChanges">${(turn.changes || []).map(change => {
          const version = snapshot => snapshot?.number ? `v${snapshot.number}` : "";
          const before = version(change.before_version) || String(change.before_sha256 || "").slice(0, 8);
          const after = version(change.after_version) || String(change.after_sha256 || "").slice(0, 8);
          return window.PortalI18n.html`<li>${escapeHtml(change.address)} <small>${escapeHtml(before)} → ${escapeHtml(after)}</small></li>`;
        }).join("")}</ul>` : ""}
        ${turn.plan_text ? context.renderContent(turn.plan_text) : ""}
        ${turn.explanation ? window.PortalI18n.html`<p>${escapeHtml(turn.explanation)}</p>` : ""}
        ${turn.plan?.length ? window.PortalI18n.html`<ol>${turn.plan.map(step => window.PortalI18n.html`<li data-step-status="${escapeHtml(step.status)}"><span>${escapeHtml(labels[step.status] || step.status)}</span> ${escapeHtml(step.step)}</li>`).join("")}</ol>` : ""}
        ${turn.diff ? window.PortalI18n.html`<details><summary>查看本轮修改${turn.diff_truncated ? window.PortalI18n.t("（已截断）") : ""}</summary><div class="nativeTurnDiffToolbar" hidden><select data-turn-diff-file aria-label="修改文件" disabled></select><button type="button" class="portalButton is-icon is-quiet" data-turn-file-preview aria-label="在 Preview 中打开当前文件" title="请先选择一个文件" disabled>${window.PortalUIPrimitives.iconHtml("external-link")}</button></div><div data-turn-diff>${escapeHtml(turn.diff)}</div></details>` : ""}
        </section>`;
    }
    if (messageRole(item) !== "human" && item.content_parts?.some(part => nativeInteractionNames.has(part.tool_name))) {
      return item.content_parts.map(part => {
        if (!nativeInteractionNames.has(part.tool_name)) return "";
        if (part.type === "tool_call" && part.interaction) return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
        if (part.type === "tool_call") return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
        const label = { answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("请求已取消"), expired: window.PortalI18n.t("请求已失效") }[part.status] || window.PortalI18n.t("请求已结束");
        const answers = Object.values(part.answers || {}).flatMap(answer => answer.answers || []).join("；");
        return window.PortalI18n.html`<div class="residentQuestionStatus">${escapeHtml(label)}${answers ? `：${escapeHtml(answers)}` : ""}</div>`;
      }).join("");
    }
    if (messageRole(item) !== "human" && item.content_parts?.some(part => part.type === "async_questions")) {
      return item.content_parts.map(part => {
        if (part.type === "text") return context.renderContent(part.text || "");
        if (part.type !== "async_questions") return "";
        return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.answer_message_id || "")}"></div>`;
      }).join("");
    }
    if (outwardToResidents(item)) return outwardRecordHtml(item, context.bodyHtml || "");
    if (item.expanded_members?.length) return mailboxGroupBody(item.expanded_members, context);
    const parsed = messageRole(item) === "human" ? parseForwardReference(context.content || "") : null;
    if (!parsed) return context.bodyHtml || "";
    const render = context.renderContent;
    const reference = parsed.entries?.length
      ? parsed.entries.map(entry => window.PortalI18n.html`<section class="forwardReferenceEntry"><div class="forwardReferenceSpeaker">${escapeHtml(entry.speaker)}</div><div class="forwardReferenceEntryContent markdownBody">${render(entry.content)}</div></section>`).join("")
      : render(parsed.reference);
    return window.PortalI18n.html`<div class="forwardReferenceMessage"><details class="forwardReferenceDetails" data-message-disclosure="forward-reference"><summary><span>转发参考</span><span class="forwardReferenceHint"><span class="forwardReferenceExpand">展开</span><span class="forwardReferenceCollapse">收起</span></span></summary><div class="forwardReferenceBody markdownBody">${reference}</div></details>${parsed.note ? window.PortalI18n.html`<div class="forwardReferenceNote">${render(parsed.note)}</div>` : ""}</div>`;
  }

  // A Family facade Conversation reads like any Resident's (decision_44cf587c1721):
  // requests keep their sender, and member replies are the facade's replies.
  // Replies are projected from the member's Conversation, never copied.
  function familyRelayRoute(value) {
    const names = items => (Array.isArray(items) ? items : [])
      .map(item => String(item?.title || item?.resident_id || "")).filter(Boolean);
    const via = names(value?.via), moved = names(value?.reassigned_from);
    return (via.length ? `（经 ${via.join(" → ")}）` : "") + (moved.length ? `（改派自 ${moved[moved.length - 1]}）` : "");
  }

  // What a facade's Stop stops: every request the Family is executing - run by a
  // member or still being routed - and, for the confirmation, what stays queued.
  function familyExecution(snapshot) {
    const requests = (Array.isArray(snapshot?.messages) ? snapshot.messages : [])
      .filter(message => message?.role === "user" && message.relay && typeof message.relay === "object" && message.message_id)
      .map(message => ({ message_id: String(message.message_id), state: String(message.relay.state || ""),
        stopping: message.relay.work_state === "stopping",
        member: String(message.relay.title || message.relay.resident_id || "成员") }));
    return {
      active: requests.filter(request => request.state === "routing" || (request.state === "running" && !request.stopping)),
      waiting: requests.filter(request => ["waiting_members", "scope_review", "quota_review", "delivering", "queued"].includes(request.state)).length,
    };
  }

  // A facade request given to a member can be reassigned to another (decision_44cf587c1721);
  // the members open from one 改派 item, beside it, like a bookmark folder's.
  function familyReassignMenuItems(item, family) {
    const current = String(item?.relay?.resident_id || "");
    if (messageRole(item) !== "human" || !current) return "";
    const members = (Array.isArray(family?.members) ? family.members : [])
      .filter(member => member?.resident_id && member.resident_id !== current)
      .map(member => window.PortalI18n.html`<button type="button" role="menuitem" data-portal-message-action="family-reassign" data-family-member="${escapeHtml(member.resident_id)}">${escapeHtml(member.title || member.resident_id)}</button>`)
      .join("");
    return members ? messageSubmenuHtml("family-reassign", window.PortalI18n.t("改派"), members) : "";
  }

  // Each reply follows the request it answers; the request's progress shows
  // where any Resident shows its work, below the messages.  The Conversation
  // answers one request at a time, so the member is named once, on the last
  // reply to a request, not on each intermediate output; while the request is
  // open its status block names the member.
  function familyReplyFooter(item, index = -1, items = []) {
    const reply = item && typeof item.family_reply === "object" ? item.family_reply : null;
    if (!reply || item.family_status) return "";
    const request = String(reply.request_message_id || "");
    const next = index >= 0 && Array.isArray(items) ? items[index + 1] : null;
    if (request && String(next?.family_reply?.request_message_id || "") === request) return "";
    const title = String(reply.title || reply.resident_id || window.PortalI18n.t("成员"));
    const source = messageRole(item) === "agent" ? `由 ${title} 回复` : `来自 ${title}`;
    const residentId = String(reply.resident_id || "");
    const messageId = String(reply.message_id || item.message_id || item.id || "");
    const label = escapeHtml(`${source}${familyRelayRoute(reply)}`);
    const href = `/lineage?${new URLSearchParams({ resident: residentId, message: messageId, open_chat: "1" })}`;
    const sourceLink = residentId && messageId
      ? window.PortalI18n.html`<a class="messageMetaLink" href="${escapeHtml(href)}" data-family-reply-source title="在成员对话中定位这条回复">${label}</a>`
      : label;
    return window.PortalI18n.html`<div class="familyRelay familyReply" data-family-reply-member="${escapeHtml(residentId)}"><div class="familyRelayStatus">${sourceLink}</div></div>`;
  }

  function heldInfoFooter(item) {
    const released = Boolean(item?.released_with);
    if (!released && item?.held_info !== true) return "";
    // A Family facade hands its infos to the steward at once (decision_44cf587c1721); a delivered
    // info whose request is not shown here was read with that request, not on its own.
    const status = item.held_info_steward
      ? "背景更新 · 已交给 steward"
      : released ? "背景更新 · 已作为背景随一条请求送达，没有单独打断工作" : "背景更新 · 随下一条任务送达";
    return window.PortalI18n.html`<div class="familyRelay heldInfo" data-held-info="${released ? "released" : "true"}"><div class="familyRelayStatus">${status}</div></div>`;
  }

  // A time inside a message reads like the message's own: local and compact.
  function footerTime(value) {
    return window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || "");
  }

  // Infos delivered with a request were read as one input with it: shown inside it, collapsed (concept_43c793b4f3ab).
  function attachedInfosFooter(item) {
    const infos = Array.isArray(item?.attached_infos) ? item.attached_infos : [];
    if (!infos.length) return "";
    const rows = infos.map(info => {
      const text = (Array.isArray(info?.content_parts) ? info.content_parts : [])
        .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
      const authorId = String(info?.origin?.sender?.id || info?.author_id || "").trim();
      const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
      const time = footerTime(info?.created_at);
      return window.PortalI18n.html`<div class="attachedInfo" data-attached-info="${escapeHtml(String(info?.message_id || ""))}"><div class="attachedInfoMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</div><div class="attachedInfoText">${escapeHtml(text.slice(0, 4000))}</div></div>`;
    }).join("");
    const note = item.attached_infos_steward ? " · 已交给 steward" : "";
    return window.PortalI18n.html`<details class="familyRelay heldInfo attachedInfos" data-message-disclosure="attached-infos"><summary class="familyRelayStatus">附带背景更新 ${infos.length} 条${note}</summary>${rows}</details>`;
  }

  function memberText(message) {
    return (Array.isArray(message?.content_parts) ? message.content_parts : [])
      .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
  }

  // A Mailbox group's body is its members (decision_b5d349190743), each with
  // its own sender, time and text; a long one opens from a one-line preview.
  function mailboxGroupBody(members, context) {
    const rows = members.map(member => {
      const id = escapeHtml(String(member?.message_id || ""));
      const text = memberText(member);
      const authorId = String(member?.origin?.sender?.id || member?.author_id || "").trim();
      const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
      const time = footerTime(member?.created_at);
      const meta = window.PortalI18n.html`<span class="mailboxMemberMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</span>`;
      const body = window.PortalI18n.html`<div class="mailboxMemberText markdownBody">${context.renderContent(text)}</div>`;
      if (text.length <= 160 && !text.includes("\n")) {
        return window.PortalI18n.html`<section class="mailboxMember" data-mailbox-member="${id}">${meta}${body}</section>`;
      }
      const preview = text.split("\n").find(line => line.trim()) || "";
      return window.PortalI18n.html`<details class="mailboxMember" data-mailbox-member="${id}" data-message-disclosure="mailbox-member:${id}"><summary>${meta}<span class="mailboxMemberPreview">${escapeHtml(preview.slice(0, 200))}</span></summary>${body}</details>`;
    }).join("");
    return window.PortalI18n.html`<div class="mailboxGroupBody" data-mailbox-group="true"><div class="mailboxGroupNote">合并了 ${members.length} 条消息，作为一次输入处理</div>${rows}</div>`;
  }

  // A Mailbox group shows its members in its body; a member points at its group.
  function mailboxFooter(item) {
    if (item?.grouped_into_message_id) {
      return window.PortalI18n.markup('<div class="familyRelay heldInfo" data-mailbox-member="true"><div class="familyRelayStatus">已并入 Mailbox 群体消息</div></div>');
    }
    return "";
  }

  // A message a Resident sends to a user, or a Family sends out as its facade, names
  // its recipients; a Family's message also names the member that sent it (decision_c524c775e0be).
  function recipientsStatus(item) {
    const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
    if (!recipients.length) return null;
    const label = id => (/^resident_[0-9a-f]{12}$/.test(id) ? resourceChips.resourceLabelHtml(id) : escapeHtml(id));
    const names = recipients.map(recipient => {
      const display = item?.recipient_labels?.[recipient.kind]?.[recipient.id];
      return typeof display === "string" && display ? escapeHtml(display) : label(String(recipient.id));
    }).join("、");
    const sender = String(item?.origin?.sender?.id || "");
    const initiator = String(item?.origin?.initiated_by?.id || "");
    const by = /^resident_[0-9a-f]{12}$/.test(initiator) && initiator !== sender ? ` · 由 ${label(initiator)} 发出` : "";
    return { ids: recipients.map(recipient => recipient.id).join(","), html: `发给 ${names}${by}` };
  }

  // The record of a message a Family sent another Resident in its name: in the
  // facade's Conversation it stays one line and opens on demand.
  function outwardToResidents(item) {
    const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
    return messageRole(item) !== "human" && recipients.length > 0 && recipients.every(recipient => recipient.kind === "resident");
  }

  function outwardRecordHtml(item, bodyHtml) {
    const status = recipientsStatus(item);
    return window.PortalI18n.html`<details class="familyRelay outwardMessage outwardRecord" data-message-disclosure="outward-record" data-message-recipients="${escapeHtml(status.ids)}"><summary class="familyRelayStatus">${status.html}</summary><div class="outwardRecordBody markdownBody">${bodyHtml}</div></details>`;
  }

  // A message sent to a user - a member's question, say - is answered from the message
  // itself; the reply quotes it and goes straight to the member that wrote it.
  function recipientsFooter(item) {
    const status = recipientsStatus(item);
    if (!status || outwardToResidents(item)) return "";
    const toUser = item.recipients.some(recipient => recipient?.kind === "user");
    const reply = toUser && messageQuotable(item)
      ? window.PortalI18n.markup('<button class="portalButton is-compact outwardReply" type="button" data-portal-message-action="quote" title="引用这条消息回复">回复</button>')
      : "";
    return window.PortalI18n.html`<div class="familyRelay outwardMessage${reply ? " canReply" : ""}" data-message-recipients="${escapeHtml(status.ids)}"><div class="familyRelayStatus">${status.html}</div>${reply}</div>`;
  }

  function residentMessageFooter(item, index, items) {
    const delivery = item.local_delivery;
    const failed = delivery?.state === "failed";
    const confirming = delivery?.state === "confirming";
    const localStatus = delivery && delivery.state !== "accepted"
      ? window.PortalI18n.html`<div class="messageDeliveryStatus${failed ? " is-failed" : ""}" data-message-delivery="${delivery.state}" role="status">
          <span>${escapeHtml(failed ? window.PortalI18n.text`发送失败：${delivery.error}`
            : confirming ? window.PortalI18n.t("正在确认发送状态") : window.PortalI18n.t("发送中…"))}</span>
          ${failed ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-retry">${window.PortalI18n.t("重试")}</button><button type="button" class="portalButton is-compact" data-portal-message-action="submission-dismiss">${window.PortalI18n.t("移除")}</button>`
            : confirming ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-check">${window.PortalI18n.t("检查发送状态")}</button>` : ""}
        </div>` : "";
    return localStatus + attachedInfosFooter(item) + mailboxFooter(item) + heldInfoFooter(item) + familyReplyFooter(item, index, items)
      + recipientsFooter(item) + residentTokenFooter(item);
  }

  // The progress of a Family facade request, after its last shown message:
  // which member has it, its place in that member's queue, the live activity.
  // The sender (or the owner) withdraws it while queued and stops it while it runs.
  function residentMessageAfter(item, index, items, context = {}) {
    const status = item && typeof item.family_status === "object" ? item.family_status : null;
    return status ? requestProgressHtml(status, context) : "";
  }

  // How far one Resident has got with one request: being handed over, its
  // place in the Resident's queue, or the Resident's live work on it
  // (decision_44cf587c1721).  A Chatroom shows the same block, without the
  // withdraw and stop actions, for each Resident a message called
  // (decision_a6bebf4ac283).
  function requestProgressHtml(status, context = {}, { actions: withActions = true } = {}) {
    const state = String(status?.state || "");
    const member = `${String(status.title || status.resident_id || "成员")}${familyRelayRoute(status)}`;
    const phase = String(status.work_phase || "");
    const phaseLabel = PRE_PROVIDER_RUNTIME_PHASES.has(phase) ? RUNTIME_PHASE_STATUS[phase]?.label || "" : "";
    const stopping = status.work_state === "stopping";
    const button = (action, label, unavailable = "", title = "") => window.PortalI18n.html`<button class="portalButton is-compact familyRequestAction" type="button" data-portal-message-action="${action}"${unavailable ? ' disabled data-action-unavailable="true"' : ""}${unavailable || title ? ` title="${escapeHtml(unavailable || title)}"` : ""}>${escapeHtml(label)}</button>`;
    let label = "";
    let actions = "";
    let body = "";
    if (state === "recovery") {
      const labels = {
        selecting: "正在查找原请求", selected: "正在恢复原请求", routing: "正在恢复原请求的转交",
        queued: "原请求已在排队，无需重复启动", running: "原请求正在运行，无需重复启动",
        completed: "原请求已经完成", failed: "恢复未完成", cancelled: "原请求已停止",
        missing: "原请求的执行记录不可用", unavailable: "无法恢复原请求",
        choose: "选择要恢复的原请求，引用后发送 continue。",
      };
      label = window.PortalI18n.t(labels[status.status] || "正在恢复原请求");
      if (status.resident_id) label += ` · ${member}`;
      if (status.error) body += `<div class="paneNote">${escapeHtml(typeof status.error === "string" ? status.error : status.error.message || JSON.stringify(status.error))}</div>`;
      if (status.original_message_id) actions = button("family-recovery-view", "查看原请求");
      body += (status.candidates || []).map(candidate => `<div class="paneNote"><button type="button" class="portalButton is-compact" data-portal-message-action="family-recovery-choose" data-recovery-request="${escapeHtml(candidate.message_id)}">${escapeHtml(candidate.text || candidate.message_id)}</button></div>`).join("");
    } else if (state === "routing") {
      label = RUNTIME_PHASE_STATUS.family_routing.label;
      // Stopped while being routed, the request is withdrawn before any member gets it.
      actions = button("family-request-withdraw", "停止");
    } else if (state === "waiting_members") {
      label = "等待 Family 恢复成员";
      actions = button("family-request-withdraw", "撤回");
    } else if (state === "scope_review") {
      // No member's duties cover it: the steward decides who takes it, maybe a new member.
      label = "不在现有成员职责内，steward 正在安排成员";
      actions = button("family-request-withdraw", "撤回");
    } else if (state === "quota_review") {
      label = "steward 正在评估成员额度与任务强度";
    } else if (state === "delivering") {
      label = `正在交给 ${member}`;
    } else if (state === "queued") {
      const ahead = Number(status.ahead);
      const place = status.ahead === undefined || !Number.isFinite(ahead) ? "" : ahead > 0 ? `（前面还有 ${ahead} 条）` : "（下一条处理）";
      label = phaseLabel ? `已交给 ${member} · ${phaseLabel}` : `已交给 ${member} · 排队中${place}`;
      // Disputing the Router's choice names the member that should take it, and why.
      actions = button("family-request-inject", "立即注入",
        status.resident_id && status.message_id ? "" : "等待确认承接成员的排队消息",
        "当前工作可接收时立即注入，否则排到下一轮；暂停接单时仍会等待")
        + button("family-request-correct", "纠正 Router") + button("family-request-withdraw", "撤回");
    } else if (state === "running") {
      label = stopping ? `${member} · ${RUNTIME_PHASE_STATUS.stopping.label}` : phaseLabel ? `${member} · ${phaseLabel}` : `${member} 运行中`;
      actions = stopping ? "" : button("family-request-stop", "停止");
      body = phaseLabel ? "" : runtimeDetailsHtml(Array.isArray(status.activities) ? status.activities : [], {
        live: !stopping,
        formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
        assistantName: String(status.title || "成员"),
        emptyLiveHtml: window.PortalI18n.html`<div class="paneNote">${escapeHtml(member)} 已开始处理，等待活动事件。</div>`,
      });
    } else return "";
    const keyAttrs = context.key ? ` data-message-key="${escapeHtml(context.key)}" data-message-after-of="${escapeHtml(context.anchorKey || "")}"` : "";
    const residentAttr = status.resident_id ? ` data-request-resident="${escapeHtml(String(status.resident_id))}"` : "";
    const labelHtml = status.resident_id
      ? `<a class="messageMetaLink" data-family-request-source href="/lineage?${escapeHtml(new URLSearchParams({ resident: String(status.resident_id), open_chat: "1" }).toString())}" title="${escapeHtml(window.PortalI18n.t("打开执行成员的对话"))}">${escapeHtml(label)}</a>`
      : escapeHtml(label);
    return window.PortalI18n.html`<article class="messageItem agent liveRuntime familyRequestStatus"${keyAttrs}${residentAttr} data-family-request="${escapeHtml(String(status.request_message_id || ""))}" data-family-request-state="${escapeHtml(stopping ? "stopping" : state)}">
      <div class="messageHead"><span class="messageTitle familyRequestLabel">${labelHtml}</span><span class="messageHeadRight">${withActions ? actions : ""}</span></div>
      ${body ? `<div class="familyRequestActivity"${context.workCollapsed ? " hidden" : ""}>${body}</div>` : ""}
    </article>`;
  }

  function formatTokenCount(value) {
    const count = Number(value || 0);
    if (!Number.isFinite(count) || count <= 0) return "0";
    if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
    if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
    return String(Math.round(count));
  }

  function residentTokenFooter(item) {
    return [
      item.token_usage,
    ].map(usage => {
      if (!usage) return "";
      const parts = [["total_tokens", ""], ["input_tokens", "in "], ["output_tokens", "out "], ["reasoning_output_tokens", "reason "]]
        .filter(([key]) => Number(usage[key]) > 0).map(([key, label]) => `${label}${formatTokenCount(usage[key])}`);
      return parts.length ? window.PortalI18n.html`<div class="messageTokenNote">${escapeHtml(`tokens ${parts.join(" · ")}`)}</div>` : "";
    }).join("");
  }

  class ResidentReadObserver {
    constructor(panel) {
      this.panel = panel;
      this.root = panel.workspace.els.messages;
      this.observation = null;
      this.frame = 0;
      this.pending = false;
      this.confirmAfterPending = false;
      this.retryAfter = 0;
      this.retryTimer = 0;
      this.confirmed = null;
      this.disposed = false;
      this.schedule = () => {
        if (this.disposed || this.frame) return;
        this.frame = window.requestAnimationFrame(() => {
          this.frame = 0;
          this.confirm();
        });
      };
      this.resume = () => {
        this.retryAfter = 0;
        this.schedule();
      };
      this.deferRetry = () => {
        this.retryAfter = Date.now() + 5000;
        if (!this.retryTimer) this.retryTimer = window.setTimeout(() => {
          this.retryTimer = 0;
          this.schedule();
        }, 5000);
      };
      this.unsubscribe = window.PortalConversationStore?.subscribe?.(() => {
        this.schedule();
      });
      this.resizeObserver = new ResizeObserver(this.schedule);
      this.resizeObserver.observe(this.root);
      document.addEventListener("visibilitychange", this.resume);
      window.addEventListener("focus", this.resume);
    }

    update(snapshot) {
      // A conversation view that projects the snapshot differently decides when it is presented.
      const presented = this.panel.conversationView()?.readReceipt?.(snapshot) !== false;
      const observation = snapshot && presented ? {
        residentId: this.panel.target().resident_id,
        cursor: Number(snapshot.cursor || 0),
        unread: snapshot.viewer_state?.unread === true,
      } : null;
      if (observation?.residentId !== this.observation?.residentId) this.confirmed = null;
      this.observation = observation;
      this.schedule();
    }

    confirm() {
      if (this.pending) {
        this.confirmAfterPending = true;
        return;
      }
      if (this.disposed || !this.observation || document.hidden || this.panel.presented === false
        || !this.root.isConnected || this.panel.pendingProtocol
        || this.panel.workspace.isLoading() || Date.now() < this.retryAfter
        || this.observation.residentId !== this.panel.target().resident_id) return;
      if (this.root.checkVisibility && !this.root.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return;
      const store = window.PortalConversationStore;
      if (!store?.observeResidentRead) return;
      const residentId = this.observation.residentId;
      const cursor = this.observation.cursor;
      const actor = String(window.PortalIdentity?.username?.() || "").trim();
      if (!actor) return;
      const item = store.entry?.(`resident:${residentId}`);
      const revision = Number(item?.read_revision || 0);
      if (this.confirmed?.residentId === residentId
        && this.confirmed.actor === actor
        && !item?.unread
        // HATG can present a new completion without loading its inbox entry.
        // Only reuse a receipt for the same unread presentation; an old or
        // absent index entry cannot suppress the next completion's receipt.
        && (!this.observation.unread || this.confirmed.cursor === cursor)
        && this.confirmed.revision >= revision) return;
      this.pending = true;
      Promise.resolve(store.observeResidentRead({
        residentId,
        actor,
      })).then(payload => {
        if (!payload) {
          this.deferRetry();
          return;
        }
        this.confirmed = {
          residentId,
          actor,
          cursor,
          revision: Math.max(revision, Number(payload?.item?.read_revision || 0)),
        };
        this.retryAfter = 0;
        if (this.retryTimer) window.clearTimeout(this.retryTimer);
        this.retryTimer = 0;
      }).catch(error => {
        this.deferRetry();
        console.warn("Resident read receipt failed", error);
      }).finally(() => {
        this.pending = false;
        if (this.confirmAfterPending) {
          this.confirmAfterPending = false;
          this.schedule();
        }
      });
    }

    destroy() {
      this.disposed = true;
      if (this.frame) window.cancelAnimationFrame(this.frame);
      if (this.retryTimer) window.clearTimeout(this.retryTimer);
      this.unsubscribe?.();
      this.resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", this.resume);
      window.removeEventListener("focus", this.resume);
      this.observation = null;
    }
  }

  // The same Family view is opened from Chat and from a node's menu.
  function createFamilyStatusView(residentId, anchor, onSummary = null, getFamily = null, container = null) {
    const root = document.createElement("section");
    root.className = `familyStatusPanel portalHatgOverlayComponent${container ? " is-inline" : ""}`;
    root.hidden = true;
    root.setAttribute("role", container ? "region" : "dialog");
    root.setAttribute("aria-label", window.PortalI18n.t("Family 状态"));
    (container || document.body).append(root);
    const rows = new Map();
    let accounts = [], family = {}, quotaError = "", disposed = false, loading = false, reloadRequested = false;

    const popoverOptions = { trigger: anchor, panel: root, portal: true,
      placement: "bottom-start", ariaHaspopup: "dialog", closeOnOutside: true, closeOnEscape: true,
      onClose: () => {
        rows.forEach(row => row.stops?.forEach(stop => stop?.()));
        rows.clear();
        if (!onSummary) cleanup();
      } };
    const popover = container ? null : window.PortalOverlay.bindPopover(popoverOptions);
    const isOpen = () => container ? root.isConnected && !root.closest("[hidden]") : popover.isOpen();
    const weekly = account => {
      const q = account?.quota_reserve || {};
      if (q.fresh !== true || (Number.isFinite(q.valid_until) && q.valid_until * 1000 <= Date.now())) return null;
      const ws = (q.windows || []).filter(w => w.window === "seven_day" || w.window_duration_mins === 10080);
      return ws.length && ws.every(w => typeof w.remaining_percent === "number" && Number.isFinite(w.remaining_percent) && w.remaining_percent >= 0 && w.remaining_percent <= 100)
        ? Math.min(...ws.map(w => w.remaining_percent)) : null;
    };
    const accountOf = row => {
      const d = row.detail || {}, bound = d.execution_account || {};
      return accounts.find(a => a.account_id === bound.account_id
        || (a.provider === (d.provider_session?.provider || bound.provider || d.resident?.backend)
          && a.backend_route_id === (bound.backend_route_id || d.provider_session?.account_id))) || {};
    };
    const rowState = row => window.PortalResidentProtocol.stateView(row.snapshot?.state);
    const working = row => rowState(row).working;
    const failed = row => row.snapshot?.input_states?.some(input => input.state === "failed");
    const migration = row => sessionRestartActive(row.restart) ? row.restart : null;
    const label = row => migration(row)?.phase === "waiting_current_execution" ? window.PortalI18n.t("等待本轮后迁移")
      : migration(row) ? window.PortalI18n.t("迁移中") : failed(row) && !working(row) ? window.PortalI18n.t("失败")
      : ({ running: window.PortalI18n.t("工作中"), queued: window.PortalI18n.t("等待执行"), stopping: window.PortalI18n.t("停止中"), idle: window.PortalI18n.t("空闲") }[row.snapshot ? rowState(row).display_work_state : ""] || window.PortalI18n.t("加载中"));
    const runtimeKey = row => JSON.stringify([row.detail?.resident?.backend, row.detail?.execution_account?.account_id,
      row.detail?.provider_session?.provider, row.detail?.provider_session?.account_id]);
    const canEditRuntime = row => row.runtime?.state().canEdit === true;
    const runtimeLabel = row => {
      const settings = row.snapshot?.runtime_settings || {};
      const catalog = row.catalogKey === runtimeKey(row) ? row.catalog : null;
      const fields = catalog?.runtime_capabilities?.settings
        || row.detail?.backend_capabilities?.runtime_capabilities?.settings || [];
      return [settings.reasoning_effort || window.PortalI18n.t("默认强度"),
        fields.includes("service_tier") ? catalog && !(catalog.service_tiers_by_model?.[settings.model || catalog.default_model] || []).length
          ? window.PortalI18n.t("Fast 不支持") : settings.service_tier === "priority" ? window.PortalI18n.t("Fast 开") : window.PortalI18n.t("Fast 关") : ""].filter(Boolean).join(" · ");
    };
    const render = () => {
      if (disposed) return;
      const all = [...rows.values()];
      const running = all.filter(working).length;
      const queued = all.reduce((n, row) => n + Number(row.snapshot?.state?.queued_count || 0), 0);
      const attention = all.filter(row => failed(row) || migration(row) || (weekly(accountOf(row)) !== null
        && (row.role === "steward" ? weekly(accountOf(row)) <= 10 : weekly(accountOf(row)) < 10))).length;
      onSummary?.(`运行 ${running} · 排队 ${queued} · 需关注 ${attention}`);
      const rowHtml = row => {
        const id = row.resident_id, d = row.detail || {}, snap = row.snapshot || {}, account = accountOf(row);
        const remain = weekly(account), q = account.quota_reserve || {}, backend = d.provider_session?.provider || d.resident?.backend || "—";
        const accountLabel = account.label || d.execution_account?.label || d.execution_account?.account_id || window.PortalI18n.t("自动");
        const queue = Number(snap.state?.queued_count || 0), closed = snap.state?.intake_state === "closed";
        const gate = window.PortalResidentProtocol.executionGateView(snap.state);
        const settings = d.runtime_settings || {}, error = row.error || snap.input_states?.find(input => input.state === "failed")?.error?.message || row.restart?.error || "";
        const task = (snap.messages || []).find(m => m.message_id === snap.state?.active_message_id) || (snap.messages || []).find(m => m.role === "user");
        const taskText = (task?.content_parts || []).filter(p => p.type === "text").map(p => p.text).join("\n");
        const week = (q.windows || []).find(w => w.window === "seven_day" || w.window_duration_mins === 10080);
        const reset = week?.resets_at ? footerTime(typeof week.resets_at === "number" ? new Date(week.resets_at * 1000).toISOString() : week.resets_at) : "未知";
        const low = remain !== null && (row.role === "steward" ? remain <= 10 : remain < 10);
        const note = low ? row.role === "steward" ? "额度不足 · 平台负责迁移" : row.role === "member" ? "低于 10% · 新请求须 steward 评估" : "低额度" : "";
        const href = `/lineage?${new URLSearchParams({ resident: id, lineage: family.lineage_id || "", open_chat: "1" })}`;
        const intakeNote = migration(row) ? "迁移暂关" : closed ? "关" : "开";
        const status = migration(row) ? "queued" : failed(row) && !working(row) ? "failed" : rowState(row).status;
        return window.PortalI18n.html`<tr data-family-status-row="${escapeHtml(id)}"><td data-label="节点"><a class="messageMetaLink familyStatusName" href="${escapeHtml(href)}" title="${escapeHtml(row.title || id)}">${escapeHtml(row.title || id)}</a></td><td data-label="工作状态"><button class="portalButton is-quiet familyStatusWork" data-family-status-detail="${escapeHtml(id)}" title="${escapeHtml(label(row))} · 展开工作详情" aria-expanded="${row.expanded ? "true" : "false"}"><span class="status-badge ${escapeHtml(status)}">${escapeHtml(label(row))}</span>${gate.text ? `<span class="status-badge" data-execution-gate="${escapeHtml(gate.state)}" title="${escapeHtml(gate.text)}">${escapeHtml(gate.label)}</span>` : ""}${queue ? window.PortalI18n.html`<span class="familyStatusQueue"> · 排队 ${queue}</span>` : ""}</button></td><td data-label="Backend／账号"><button type="button" class="portalButton is-quiet familyStatusAccount" data-family-status-settings="${escapeHtml(id)}" aria-haspopup="dialog" title="${escapeHtml(window.PortalI18n.text`${row.title || id} · 成员运行设置`)}" ${!canEditRuntime(row) && row.runtime?.state().canRestart !== true || row.settingsLoading || migration(row) ? "disabled" : ""}><span><span>${escapeHtml(`${backend} · ${accountLabel}`)}</span>${canEditRuntime(row) || d.runtime_settings ? window.PortalI18n.html`<span class="familyStatusRuntime"><span>${escapeHtml(row.settingsLoading ? window.PortalI18n.t("读取设置…") : runtimeLabel(row))}</span></span>` : ""}</span><span aria-hidden="true">▾</span></button></td><td data-label="周剩余" ${low ? 'class="familyStatusAttention"' : ""} title="${escapeHtml(note || (remain === null ? q.fresh === false ? window.PortalI18n.t("观测过期，待刷新") : window.PortalI18n.t("无有效周额度观测") : window.PortalI18n.text`周额度重置：${reset}`))}"><span class="familyStatusQuotaValue">${remain === null ? q.currency && q.fresh === true ? escapeHtml(`${q.remaining_balance} ${q.currency}`) : window.PortalI18n.t("未知") : `${remain.toFixed(0)}%`}${low ? window.PortalI18n.markup('<span class="familyStatusQuotaWarning" aria-label="低额度">!</span>') : ""}</span>${remain !== null ? window.PortalI18n.html`<span class="familyStatusQuotaTrack" aria-hidden="true"><span style="width:${remain}%"></span></span>` : ""}</td><td data-label="接单"><button class="portalButton is-compact familyStatusIntake" role="switch" aria-checked="${closed ? "false" : "true"}" aria-label="${escapeHtml(window.PortalI18n.text`${row.title || id}接单`)}" aria-description="${escapeHtml(window.PortalI18n.t("关闭接单只暂停后续任务，当前工作继续"))}" data-portal-tooltip-opt-out="1" data-family-status-intake="${escapeHtml(id)}" ${d.allows?.OpenIntake !== true || migration(row) ? "disabled" : ""}>${intakeNote}</button></td></tr>${row.expanded ? window.PortalI18n.html`<tr><td colspan="5" class="familyStatusDetails"><dl>${gate.text ? `<dt>${escapeHtml(window.PortalI18n.t("执行保护"))}</dt><dd>${escapeHtml(gate.text)}</dd>` : ""}<dt>职责</dt><dd>${escapeHtml(d.resident?.description || window.PortalI18n.t("未填写"))}</dd><dt>模型</dt><dd>${escapeHtml([settings.model, settings.reasoning_effort].filter(Boolean).join(" · ") || window.PortalI18n.t("默认"))}</dd><dt>当前任务</dt><dd>${escapeHtml(taskText.slice(0, 800) || window.PortalI18n.t("无当前任务"))}</dd><dt>开始处理</dt><dd>${escapeHtml(footerTime(task?.activated_at || task?.created_at) || "—")}</dd><dt>最近活动</dt><dd>${escapeHtml(footerTime(snap.messages?.at(-1)?.created_at) || "—")}</dd><dt>周额度重置</dt><dd>${escapeHtml(reset)}</dd>${note ? window.PortalI18n.html`<dt>额度提示</dt><dd>${escapeHtml(note)}</dd>` : ""}${error ? window.PortalI18n.html`<dt>错误</dt><dd>${escapeHtml(typeof error === "string" ? error : JSON.stringify(error))}</dd>` : ""}</dl><a class="messageMetaLink" href="/lineage?${escapeHtml(new URLSearchParams({ lineage: family.lineage_id || "", resident: id }).toString())}">在画布定位</a></td></tr>` : ""}`;
      };
      const table = values => window.PortalI18n.html`<table><thead><tr><th scope="col">节点</th><th scope="col">工作状态</th><th scope="col">Backend／账号</th><th scope="col">周剩余</th><th scope="col">接单</th></tr></thead><tbody>${values.map(rowHtml).join("")}</tbody></table>`;
      // Preserve keyboard focus and detail expansion during live refresh.
      const active = document.activeElement;
      const focusKey = active?.closest("[data-family-status-row]")?.dataset.familyStatusRow;
      const action = active?.dataset.familyStatusDetail ? "detail" : active?.dataset.familyStatusSettings ? "settings" : active?.dataset.familyStatusIntake ? "intake" : "";
      const scrollTop = root.querySelector(".familyStatusBody")?.scrollTop || 0;
      root.setAttribute("aria-label", window.PortalI18n.text`${family.title || "Family"} · 成员状态`);
      root.innerHTML = window.PortalI18n.html`<div class="familyStatusBody">${quotaError ? window.PortalI18n.html`<p class="paneNote">额度暂不可用：${escapeHtml(quotaError)}</p>` : ""}${rows.size ? table(all) : window.PortalI18n.html`<p>${loading ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("暂无成员状态")}</p>`}</div>${rows.size ? window.PortalI18n.html`<p class="familyStatusIntakeNote">关闭接单只暂停后续任务，当前工作继续</p>` : ""}`;
      root.querySelector(".familyStatusBody").scrollTop = scrollTop;
      if (focusKey && action) root.querySelector(`[data-family-status-${action}="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    };
    let quotaRequest = null;
    const loadQuota = () => {
      if (quotaRequest) return quotaRequest;
      // Home owns quota sampling and freshness. Reads here reuse its cached summary.
      quotaRequest = window.PortalAPI.get("/api/agent-status").then(summary => {
        if (disposed) return;
        accounts = summary.accounts || []; quotaError = ""; render();
      }).catch(error => {
        if (disposed) return;
        accounts = []; quotaError = error.message; render();
      }).finally(() => { quotaRequest = null; });
      return quotaRequest;
    };
    const load = async () => {
      loading = true; render();
      void loadQuota();
      try {
        const knownFamily = getFamily?.();
        family = knownFamily || (await window.PortalResidentService.projection({ residentId })).family_facade || {};
        if (disposed || !isOpen()) return;
        const nodes = family.nodes || (family.members || []).map(m => ({ ...m, role: "member" }));
        const liveIds = new Set(nodes.map(node => node.resident_id));
        for (const [id, row] of rows) if (!liveIds.has(id)) {
          row.stops?.forEach(stop => stop?.()); rows.delete(id);
        }
        for (const node of nodes) {
          let row = rows.get(node.resident_id);
          if (!row) {
            row = { ...node, stops: [] }; rows.set(node.resident_id, row);
            row.runtime = window.PortalResidentRuntimeController.bind(node.resident_id, {
              onState: view => {
                if (disposed || rows.get(node.resident_id) !== row) return;
                if (view.detail) {
                  row.detail = { ...view.detail, runtime_settings: view.settings };
                  row.snapshot = { ...row.snapshot, runtime_settings: view.settings };
                  row.catalog = view.catalog; row.catalogKey = runtimeKey(row);
                }
                row.settingsLoading = view.loading || view.busy;
                row.restart = view.restart; render();
              },
              onOpen: () => { popoverOptions.closeOnOutside = false; },
              onClose: () => {
                popoverOptions.closeOnOutside = true;
                if (!disposed) root.querySelector(`[data-family-status-settings="${CSS.escape(node.resident_id)}"]`)?.focus();
              },
            });
            row.stops.push(() => row.runtime.destroy());
          } else Object.assign(row, node);
        }
        render();
        await Promise.all(nodes.map(async node => {
          const row = rows.get(node.resident_id);
          try {
            row.error = "";
            if (!row.subscription) {
              const subscription = window.PortalResidentProtocol.acquire(node.resident_id, update => {
                if (!disposed && rows.get(node.resident_id) === row && update.snapshot) { row.snapshot = update.snapshot; render(); }
              }, { historyLimit: 1, consumer: "family_member", observationRoot: root });
              row.subscription = subscription;
              row.stops.push(() => subscription.destroy());
            }
            const [memberDetail, snapshot] = await Promise.all([
              row.runtime.load().then(view => ({ ...view.detail, runtime_settings: view.settings })),
              row.subscription?.load({ historyLimit: 1 }).then(snapshot => {
                if (!disposed && rows.get(node.resident_id) === row) { row.snapshot = snapshot; render(); }
                return snapshot;
              }),
            ]);
            if (disposed || rows.get(node.resident_id) !== row) return;
            row.detail = memberDetail;
            if (snapshot) row.snapshot = snapshot;
            const activeId = row.snapshot?.state?.active_message_id;
            if (activeId && !row.snapshot.messages?.some(m => m.message_id === activeId)) {
              const result = await window.PortalInstructionClient.execute("GetMessage", { resident_id: node.resident_id, message_id: activeId });
              row.snapshot = { ...row.snapshot, messages: [...(row.snapshot.messages || []), result.message] };
            }
          } catch (error) { row.error = error.message; }
          render();
        }));
        loading = false; render();
      } catch (error) { root.innerHTML = window.PortalI18n.html`<p>${escapeHtml(error.message)}</p>`; }
      finally {
        loading = false;
        if (reloadRequested) {
          reloadRequested = false;
          if (!disposed && isOpen()) void load();
        }
      }
    };
    root.addEventListener("click", async event => {
      try {
        const detail = event.target.closest("[data-family-status-detail]");
        if (detail) { const row = rows.get(detail.dataset.familyStatusDetail); row.expanded = !row.expanded; render(); return; }
        const intake = event.target.closest("[data-family-status-intake]");
        if (intake) {
          const row = rows.get(intake.dataset.familyStatusIntake); intake.disabled = true;
          await window.PortalResidentService.setIntake({ resident_id: row.resident_id, open: row.snapshot.state.intake_state === "closed" });
          await load(); return;
        }
        const settingsButton = event.target.closest("[data-family-status-settings]");
        if (settingsButton) {
          const row = rows.get(settingsButton.dataset.familyStatusSettings);
          if (!row || settingsButton.disabled) return;
          await row.runtime.openSettings(() => root.querySelector(`[data-family-status-settings="${CSS.escape(row.resident_id)}"]`));
          return;
        }
      } catch (error) { window.PortalUI?.toast?.(error.message, "error"); }
    });
    const freshnessTimer = window.setInterval(() => { if (isOpen()) { render(); void loadQuota(); } }, 15000);
    function cleanup() {
      if (disposed) return;
      window.clearInterval(freshnessTimer);
      disposed = true;
      rows.forEach(row => row.stops?.forEach(stop => stop?.()));
      popover?.destroy(); root.remove();
    }
    return { open() { if (container) root.hidden = false; else popover.open(); if (!loading) void load(); },
      refresh() {
        if (disposed || !isOpen()) return;
        if (loading) reloadRequested = true;
        else void load();
      },
      close() { rows.forEach(row => row.runtime?.close()); if (container) root.hidden = true; else popover.close(); }, isOpen, destroy: cleanup };
  }

  function mountFamilyStatus(residentId, container) {
    const view = createFamilyStatusView(residentId, null, null, null, container);
    view.open();
    return view;
  }

  async function openFamilyStatus(residentId, options = {}) {
    const anchor = options.anchor || document.querySelector(".portalWorkspaceHead .paneHeadActions") || document.body;
    const view = createFamilyStatusView(residentId, anchor);
    view.open();
    return view;
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
