(() => {
  if (window.PortalChatWorkspaceMessageDrawing) return;

  // One Workspace's drawing of its message list. Three entries draw it:
  // setMessages with the whole conversation, appendMessage with one new message
  // at the end, and setRuntime with the live runtime rows after the messages.
  // setMessages keeps the rows that cannot have changed: it compares each
  // message's signature and the render context (the options, the Chat's
  // Resident, the host's render state, the groups the reader collapsed or
  // expanded, the host's render functions) and redraws from the first changed
  // message, or only the older messages loaded above. Every draw ends in
  // afterMessagesDrawn, which binds what was drawn, measures its folds and tells
  // the host. redrawMessages draws the whole list again from what is shown.
  const DEPENDENCIES = Object.freeze([
    "addFiles", "bindAttachmentRefs", "bindLinkPreview", "bindMessageAvatars", "bindResourceChips",
    "bindResponseSourcePaths", "contractResidentId", "currentController", "currentConversationSearch", "els",
    "familyWorkGroups", "historyScope", "inheritanceViewFor", "measureMessageFolds", "messageAfterKey",
    "messageFoldDefaults", "messageIdentity", "messageInstanceKey", "messageInstanceSelector", "messageListHtml",
    "messageRenderKey", "messageRole", "messageViewportMeasurable", "normalizeMessageStream", "openImagePreview",
    "options", "patchSubagentLive", "preserveRuntimeDisclosureState", "reconcileMessageList",
    "rememberSubagentPlaces", "requestWorkspacePreview", "requireImageEditor", "resetMessageScrollGesture",
    "restoreDraftScope", "revealFinishedSubagents", "runtimeStreamHtml", "showMessageFoldDefaults", "state",
    "syncHistory", "syncMessageScrollMode", "updateMessageFoldDefaults",
  ]);

  function createMessageDrawing(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageDrawing is missing: ${missing.join(", ")}`);
    const {
      addFiles, bindAttachmentRefs, bindLinkPreview, bindMessageAvatars, bindResourceChips,
      bindResponseSourcePaths, contractResidentId, currentController, currentConversationSearch, els,
      familyWorkGroups, historyScope, inheritanceViewFor, measureMessageFolds, messageAfterKey,
      messageFoldDefaults, messageIdentity, messageInstanceKey, messageInstanceSelector, messageListHtml,
      messageRenderKey, messageRole, messageViewportMeasurable, normalizeMessageStream, openImagePreview,
      options, patchSubagentLive, preserveRuntimeDisclosureState, reconcileMessageList,
      rememberSubagentPlaces, requestWorkspacePreview, requireImageEditor, resetMessageScrollGesture,
      restoreDraftScope, revealFinishedSubagents, runtimeStreamHtml, showMessageFoldDefaults, state,
      syncHistory, syncMessageScrollMode, updateMessageFoldDefaults,
    } = dependencies;

    // An image from a message opens in the image editor; what the reader saves
    // there joins the composer as an attachment.
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
      options.onMessagesRendered?.(els.messages, currentController(), incremental, roots);
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
    const RUNTIME_ROW_KEYS = ["__runtime_phase__", "__plans__", "__subagents__", "__runtime__"];
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

    // One new message at the end is drawn with the reply group or human burst
    // it joins; false when the list must be drawn by setMessages instead.
    const appendMessage = message => {
      const previous = state.messages;
      const list = els.messages?.firstElementChild;
      if (!message?.id || state.messageByKey.has(String(message.id))
        || !previous.length || !list?.classList.contains("messageList")
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
      currentConversationSearch()?.update(true);
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
      if (wasFollowing && messageViewportMeasurable()) els.messages.scrollTop = els.messages.scrollHeight;
      return true;
    };

    const setMessages = (messages, renderOptions = {}) => {
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
      // Each message keeps its signature, which the next draw compares.
      state.messages = Array.isArray(messages) ? messages : [];
      const signatures = state.messages.map(item => JSON.stringify(item));
      state.messageItemSignatures = signatures;
      state.runtimeDetails = Array.isArray(renderOptions.runtimeDetails) ? renderOptions.runtimeDetails : [];
      state.runtimeLive = !!renderOptions.runtimeLive;
      state.runtimePhase = String(renderOptions.runtimePhase || "");
      const runtimeChanged = messageRuntimeSignature() !== previousRuntime;
      state.messageRenderOptions = { ...renderOptions };
      syncHistory(nextScope);
      const foldChanges = updateMessageFoldDefaults();
      const context = messageRenderContext(renderOptions);
      const messagesUnchanged = previousSignatures.length === signatures.length
        && previousSignatures.every((signature, index) => signature === signatures[index]);
      const contextUnchanged = sameMessageRenderContext(previousContext, context);
      currentConversationSearch()?.update(!messagesUnchanged);
      if (messagesUnchanged && contextUnchanged && !renderOptions.force && !foldChanges.length) {
        // Nothing to draw but perhaps the live runtime, which setRuntime patches.
        if (runtimeChanged) setRuntime(state.runtimeDetails, state.runtimeLive, renderOptions);
        return;
      }
      // The messages shown before are all still there, below older ones loaded above.
      const prependedCount = state.messages.length - previousMessages.length;
      const prependOnly = prependedCount > 0
        && previousSignatures.every((signature, index) => signature === signatures[index + prependedCount]);
      // Rows already shown may stay when they were drawn in the same context.
      const rowsReusable = !renderOptions.force && !scopeChanged
        && previousMessages.length > 0 && state.messages.length > 0 && contextUnchanged
        && els.messages.firstElementChild?.classList.contains("messageList");
      state.messageRenderContext = context;
      const renderedStream = normalizeMessageStream(state.messages, { ...options, ...renderOptions });
      state.messageByKey = new Map(renderedStream.messages.map((item) => [
        messageIdentity(item),
        item,
      ]));
      if (scopeChanged) {
        // Another conversation opens at its newest message.
        state.messageTailFollowIntent = true;
        resetMessageScrollGesture();
        syncMessageScrollMode();
      }
      const followTail = state.messageTailFollowIntent;
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
    };

    // Draw the list again from the current messages and live runtime.
    const redrawMessages = () => setMessages(state.messages, {
      ...state.messageRenderOptions,
      runtimeDetails: state.runtimeDetails,
      runtimeLive: state.runtimeLive,
      runtimePhase: state.runtimePhase,
      force: true,
    });

    // The live runtime rows after the messages change in place; the messages
    // are drawn again only when a turn that starts or ends changes their folds.
    const setRuntime = (details, live, renderOptions = {}) => {
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
        { ...options, ...state.messageRenderOptions, messages: state.messages },
      );
      const nextByKey = new Map(
        [...template.content.children].map(node => [String(node.dataset.messageKey || ""), node]),
      );
      const scrollAnchor = list.querySelector("[data-message-scroll-anchor]");
      rememberSubagentPlaces(list);
      RUNTIME_ROW_KEYS.forEach((key, index) => {
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
          const later = RUNTIME_ROW_KEYS.slice(index + 1)
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
        setMessages(state.messages, {
          ...state.messageRenderOptions,
          runtimeDetails: state.runtimeDetails,
          runtimeLive: state.runtimeLive,
          runtimePhase: state.runtimePhase,
        });
      }
    };

    return { appendMessage, setMessages, setRuntime, redrawMessages };
  }

  window.PortalChatWorkspaceMessageDrawing = Object.freeze({ createMessageDrawing, dependencies: DEPENDENCIES });
})();
