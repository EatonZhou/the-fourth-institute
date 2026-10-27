(() => {
  if (window.PortalChatWorkspaceMessageList) return;

  // The message list: grouping consecutive replies and Family work, the list HTML
  // with inheritance cards and the live runtime stream, quoting a text selection,
  // and reconciling the rendered list with new messages without losing state.
  const DEPENDENCIES = Object.freeze([
    "commonMessageActionsHtml", "escapeHtml", "footerTime", "messageAfterKey",
    "messageAttachmentsHtml", "messageAuthoredByViewer", "messageAuthorHeaderHtml",
    "messageContextRefsHtml", "messageFoldBodyHtml", "messageFoldChoices", "messageFoldDefaults",
    "messageInstanceKey", "messageNodeKey", "messageQuoteHtml", "messageRenderKey",
    "messageRole", "messageSenderIdentity", "messageSystemPresentation", "messageTitle",
    "normalizeMessageStream", "PRE_PROVIDER_RUNTIME_PHASES", "renderMarkdownWithAttachmentRefs",
    "renderResidentHandoffs", "residentHandoffParts", "responseSourcesHtml",
    "runtimeDetailsHtml", "runtimePhaseStatusHtml", "subagentLiveHtml", "nativePlanLiveHtml",
  ]);

  function messageSelectionRanges() {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return [];
    return Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index));
  }

  function selectionIntersectsNode(node, ranges = messageSelectionRanges()) {
    if (!node) return false;
    for (const range of ranges) {
      try {
        if (range.intersectsNode(node)) return true;
      } catch {
        // Ignore a range that became detached while the message list was updating.
      }
    }
    return false;
  }

  function selectedMessageText(range, content) {
    const segment = range.cloneRange();
    if (!content.contains(range.startContainer)) segment.setStart(content, 0);
    if (!content.contains(range.endContainer)) segment.setEnd(content, content.childNodes.length);
    return String(segment.toString() || "").trim();
  }

  function messageContentAtBoundary(root, node) {
    const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    const content = element?.closest?.(".messageItem .messageContent");
    return content && root.contains(content) ? content : null;
  }

  function messageSelectionCandidate(root, selection = window.getSelection?.()) {
    if (!root || !selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
    const range = selection.getRangeAt(0);
    const startContent = messageContentAtBoundary(root, range.startContainer);
    const endContent = messageContentAtBoundary(root, range.endContainer);
    const contents = startContent && startContent === endContent
      ? [startContent]
      : [...root.querySelectorAll(".messageItem .messageContent")].filter(content => {
          try {
            return range.intersectsNode(content);
          } catch {
            return false;
          }
        });
    if (!contents.length) return null;
    const segments = contents.map(content => {
      const article = content.closest(".messageItem");
      const text = selectedMessageText(range, content);
      if (!article || !text) return null;
      const fallbackTitle = article.classList.contains("agent")
        ? "Agent"
        : article.classList.contains("portal")
          ? "Portal"
          : article.classList.contains("human") || article.classList.contains("user")
          ? window.PortalI18n.t("用户")
            : window.PortalI18n.t("消息");
      return {
        message_id: String(article.dataset.messageKey || "").trim(),
        title: String(article.querySelector(".messageTitle")?.textContent || fallbackTitle).trim(),
        text,
      };
    }).filter(Boolean);
    if (!segments.length) return null;
    const text = segments.map(segment => `${segment.title}: ${segment.text}`).join("\n\n");
    const rects = range.getClientRects();
    const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
    // A selection running into a folded body's hidden part points at the body's visible end.
    const visibleBottom = endContent?.dataset.messageFold === "collapsed" && endContent.dataset.messageFoldSize === "long"
      ? endContent.getBoundingClientRect().bottom
      : Number.POSITIVE_INFINITY;
    return {
      text,
      title: window.PortalI18n.t("对话选段"),
      path: "Chat Workspace",
      source_message_ids: segments.map(segment => segment.message_id).filter(Boolean),
      rect: {
        left: Number(rect?.left || 0),
        right: Number(rect?.right || 0),
        top: Math.min(Number(rect?.top || 0), visibleBottom),
        bottom: Math.min(Number(rect?.bottom || 0), visibleBottom),
      },
    };
  }

  function preserveRuntimeDisclosureState(current, next) {
    const currentDetails = current?.querySelector?.("details.messageRuntimeDetails");
    const nextDetails = next?.querySelector?.("details.messageRuntimeDetails");
    if (currentDetails && nextDetails) nextDetails.open = currentDetails.open;
    const currentDisclosures = new Map(
      [...(current?.querySelectorAll?.("details[data-message-disclosure]") || [])]
        .map(details => [String(details.dataset.messageDisclosure || ""), details])
        .filter(([key]) => key)
    );
    for (const nextDisclosure of next?.querySelectorAll?.("details[data-message-disclosure]") || []) {
      const previous = currentDisclosures.get(String(nextDisclosure.dataset.messageDisclosure || ""));
      if (previous) nextDisclosure.open = previous.open;
    }
  }

  function preserveDeferredImageState(current, next) {
    const currentImages = [...(current?.querySelectorAll?.("img[data-portal-deferred-image]") || [])];
    const nextImages = [...(next?.querySelectorAll?.("img[data-portal-deferred-image]") || [])];
    nextImages.forEach((nextImage, index) => {
      const currentImage = currentImages[index];
      if (
        !currentImage?.hasAttribute("src")
        || currentImage.dataset.portalDeferredImage !== nextImage.dataset.portalDeferredImage
      ) return;
      nextImage.setAttribute("src", currentImage.getAttribute("src") || "");
    });
  }

  // Question cards own their mounted DOM, including unconfirmed input and IME
  // state. Refresh the message around those mounts without detaching them.
  // A removed host is still removed; card status/permissions remain its owner's
  // responsibility in ResidentQuestionCards.render after message reconciliation.
  function reconcileQuestionMounts(current, next) {
    const hosts = node => node.nodeType !== 1 ? [] : node.hasAttribute("data-resident-question")
      ? [node] : [...node.querySelectorAll("[data-resident-question]")];
    if (current.hasAttribute("data-resident-question")
      && current.dataset.residentQuestion === next.dataset.residentQuestion) return;
    for (const attribute of [...current.attributes]) {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of [...next.attributes]) current.setAttribute(attribute.name, attribute.value);
    if (!current.querySelector("[data-resident-question]") || !next.querySelector("[data-resident-question]")) {
      current.replaceChildren(...next.childNodes);
      return;
    }
    const branches = new Map();
    for (const child of current.children) {
      for (const host of hosts(child)) branches.set(host.dataset.residentQuestion, child);
    }
    const retained = new Set();
    const children = [...next.childNodes].map(child => {
      const branch = hosts(child).map(host => branches.get(host.dataset.residentQuestion))
        .find(candidate => candidate && candidate.tagName === child.tagName && !retained.has(candidate));
      if (!branch) return child;
      retained.add(branch);
      reconcileQuestionMounts(branch, child);
      return branch;
    });
    // Remove obsolete siblings before positioning retained branches; inserting
    // a retained branch before a soon-to-be-removed sibling would blur inputs.
    for (const child of [...current.childNodes]) if (!retained.has(child)) child.remove();
    let cursor = null;
    for (const child of children) {
      const position = cursor ? cursor.nextSibling : current.firstChild;
      if (child !== position) current.insertBefore(child, position);
      cursor = child;
    }
  }

  function messageViewportAtTail(root, threshold = 12) {
    if (!root) return true;
    return root.scrollHeight - root.scrollTop - root.clientHeight <= threshold;
  }

  function createMessageList(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageList is missing: ${missing.join(", ")}`);
    const {
      commonMessageActionsHtml, escapeHtml, footerTime, messageAfterKey, messageAttachmentsHtml,
      messageAuthoredByViewer, messageAuthorHeaderHtml, messageContextRefsHtml,
      messageFoldBodyHtml, messageFoldChoices, messageFoldDefaults, messageInstanceKey,
      messageNodeKey, messageQuoteHtml, messageRenderKey, messageRole, messageSenderIdentity,
      messageSystemPresentation, messageTitle, normalizeMessageStream,
      PRE_PROVIDER_RUNTIME_PHASES, renderMarkdownWithAttachmentRefs, renderResidentHandoffs,
      residentHandoffParts, responseSourcesHtml, runtimeDetailsHtml, runtimePhaseStatusHtml,
      subagentLiveHtml, nativePlanLiveHtml,
    } = dependencies;

    // Human chat bursts periodically repeat their header. Agent output uses its
    // consecutive reply group instead, regardless of elapsed time. Compare stable
    // identities, not display names. Recompute from the full ordered list so
    // prepend, deletion and suffix rendering share the same group boundaries.
    function compactHumanHeaders(items) {
      const compact = new Set();
      let start = NaN, previousTime = NaN, previousSender = "", previousRole = "", previousHistory = "";
      items.forEach((item, index) => {
        const role = messageRole(item), sender = messageSenderIdentity(item);
        const time = Date.parse(item?.created_at || "");
        const delayed = Date.parse(item?.activated_at || "") - time >= 1000;
        const eligible = role === "human" && sender && Number.isFinite(time)
          && !delayed && !messageSystemPresentation(item) && !item?.recipients?.length;
        const history = String(item?.family_history?.resident_id || "");
        const continues = eligible && history === previousHistory && sender === previousSender && role === previousRole
          && time >= previousTime && time - start <= 5 * 60 * 1000
          && new Date(time).toDateString() === new Date(start).toDateString();
        if (continues) compact.add(index);
        else start = eligible ? time : NaN;
        previousTime = time;
        previousSender = eligible ? sender : "";
        previousRole = role;
        previousHistory = history;
      });
      return compact;
    }

    // A Family facade answers requests routed together separately: replies to
    // different requests are separate runs (decision_44cf587c1721).
    function sameAgentRun(previous, next) {
      if (String(previous?.family_history?.resident_id || "") !== String(next?.family_history?.resident_id || "")) return false;
      const previousSender = messageSenderIdentity(previous), nextSender = messageSenderIdentity(next);
      if ((previousSender || nextSender) && previousSender !== nextSender) return false;
      // A message sent to someone stands on its own, apart from any reply (decision_c524c775e0be).
      if (previous?.recipients?.length || next?.recipients?.length) return false;
      return String(previous?.family_reply?.request_message_id || "")
        === String(next?.family_reply?.request_message_id || "");
    }

    function consecutiveAgentGroups(items, options = {}) {
      const groups = new Map();
      const collapsed = options.collapsedAgentGroups instanceof Set
        ? options.collapsedAgentGroups
        : new Set(options.collapsedAgentGroups || []);
      const expanded = options.expandedAgentGroups instanceof Set
        ? options.expandedAgentGroups
        : new Set(options.expandedAgentGroups || []);
      const autoCollapse = options.autoCollapseAgentGroups === true;
      let index = 0;
      while (index < items.length) {
        if (messageRole(items[index]) !== "agent") {
          index += 1;
          continue;
        }
        const start = index;
        while (
          index + 1 < items.length
          && messageRole(items[index + 1]) === "agent"
          && sameAgentRun(items[index], items[index + 1])
        ) index += 1;
        const end = index;
        if (end > start) {
          const id = `agent:${messageRenderKey(items[start])}`;
          const group = {
            id,
            start,
            end,
            intermediateCount: end - start,
            collapsed: autoCollapse ? !expanded.has(id) : collapsed.has(id),
          };
          for (let member = start; member <= end; member += 1) groups.set(member, group);
        }
        index += 1;
      }
      return groups;
    }

    // A facade work item keeps its identity across interleaved replies, paging
    // and live updates. It is not the consecutive intermediate-output group.
    function familyWorkGroups(items, collapsed = new Set()) {
      const groups = new Map();
      for (const item of items) {
        const id = String(item?.family_reply?.request_message_id || "");
        if (!id || messageRole(item) !== "agent" || groups.has(id)) continue;
        groups.set(id, { id, headKey: messageRenderKey(item), collapsed: collapsed.has(id) });
      }
      return groups;
    }

    function messageListHtml(messages, options = {}) {
      const stream = normalizeMessageStream(messages, options);
      const items = stream.messages;
      const runtimeDetails = stream.runtimeDetails;
      const runtimeLive = stream.runtimeLive;
      const runtimePhase = stream.runtimePhase;
      const history = options.history && typeof options.history === "object" ? options.history : {};
      const inheritanceHtml = !history.can_load_older && !(Number(options.renderStartIndex) > 0)
        ? inheritanceCardHtml(options.inheritance, options.inheritanceView, options)
        : "";
      if (!items.length && !runtimeDetails.length && !runtimeLive && !runtimePhase) {
        if (inheritanceHtml) return window.PortalI18n.html`<div class="messageList">${inheritanceHtml}</div>`;
        if (!window.PortalUIPrimitives?.asyncStateHtml) {
          throw new Error(window.PortalI18n.t("PortalUIPrimitives must be loaded before PortalChatWorkspace."));
        }
        return window.PortalUIPrimitives.asyncStateHtml({
          state: "empty",
          title: "",
          message: options.emptyText || window.PortalI18n.t("暂无对话。"),
          icon: "chat",
          className: "portalWorkspaceEmpty",
        });
      }
      const agentGroups = consecutiveAgentGroups(items, options);
      const collapsedWorkGroups = options.collapsedWorkGroups instanceof Set
        ? options.collapsedWorkGroups : new Set(options.collapsedWorkGroups || []);
      const workGroups = familyWorkGroups(options.allMessages || items, collapsedWorkGroups);
      const compactHeaders = compactHumanHeaders(items);
      const foldDefaults = options.messageFoldDefaults instanceof Set
        ? options.messageFoldDefaults
        : messageFoldDefaults(items, { runtimeLive });
      const foldSizes = options.messageFoldSizes instanceof Map ? options.messageFoldSizes : new Map();
      const renderStartIndex = Math.max(0, Math.min(
        items.length,
        Number.isFinite(Number(options.renderStartIndex)) ? Number(options.renderStartIndex) : 0,
      ));
      const renderEndIndex = Math.max(renderStartIndex, Math.min(
        items.length,
        Number.isFinite(Number(options.renderEndIndex)) ? Number(options.renderEndIndex) : items.length,
      ));
      const historyHtml = renderStartIndex === 0 && history.can_load_older
        ? window.PortalI18n.html`<div class="workspaceHistoryLoader" data-message-key="__history__"><button class="portalButton is-compact" type="button" data-workspace-load-older ${history.loading ? "disabled" : ""}>${escapeHtml(history.loading ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("加载更早消息"))}</button></div>`
        : "";
      const html = items.slice(renderStartIndex, renderEndIndex).map((item, localIndex) => {
        const idx = renderStartIndex + localIndex;
        const callbackIndex = idx + (Number(options.messageIndexOffset) || 0);
        const callbackMessages = options.allMessages || items;
        const role = messageRole(item);
        const key = messageRenderKey(item);
        const historical = item.family_history;
        const historyBoundary = !historical && callbackMessages[callbackIndex - 1]?.family_history;
        const historyPrefix = historical
          ? `<div class="personalAssistantHistorySource" data-family-history="${escapeHtml(historical.resident_id)}">${escapeHtml(window.PortalI18n.text`${historical.title || "AMA"} 的历史`)}</div>`
          : historyBoundary ? `<div class="personalAssistantHistoryBoundary" data-family-history-boundary role="separator">${window.PortalI18n.t("个人助手从此处接续对话")}${historyBoundary.before_at ? ` · ${escapeHtml(footerTime(historyBoundary.before_at))}` : ""}</div>` : "";
        if (role === "human" && item?.withdrawn) {
          const edit = options.enableWithdrawnEdit && messageAuthoredByViewer(item)
            ? window.PortalI18n.markup('<button type="button" class="messageWithdrawalEdit" data-portal-message-action="withdrawn-edit">重新编辑</button>') : "";
          return window.PortalI18n.html`<article class="messageItem messageWithdrawal" data-message-key="${escapeHtml(key)}">${historyPrefix}<small>用户已撤回一条信息</small>${edit}</article>`;
        }
        const work = role === "agent" ? workGroups.get(String(item?.family_reply?.request_message_id || "")) : null;
        const isWorkHead = !!work && work.headKey === key;
        const system = messageSystemPresentation(item);
        const previousRole = idx > 0 ? messageRole(items[idx - 1]) : "";
        const continued = work ? !isWorkHead : role === "agent" && previousRole === "agent" && sameAgentRun(items[idx - 1], item);
        const group = work ? null : agentGroups.get(idx) || null;
        const isGroupEnd = !!group && group.end === idx;
        const hiddenByGroup = (!!work?.collapsed && !isWorkHead) || (!!group?.collapsed && !isGroupEnd);
        const compactHeader = (continued || compactHeaders.has(idx)) && !(group?.collapsed && isGroupEnd);
        const content = String(item?.content || "");
        const created = item?.created_at || "";
        const renderMarkdown = options.renderMarkdown || ((value) => (
          window.MD?.render?.(value) || escapeHtml(value).replace(/\n/g, "<br>")
        ));
        const renderContent = value => renderResidentHandoffs(value, text => renderMarkdownWithAttachmentRefs(
          text, { ...options, renderMarkdown }
        ));
        const defaultBodyHtml = renderContent(content);
        const customBodyHtml = options.renderMessageBody
          ? options.renderMessageBody(item, {
            content,
            bodyHtml: defaultBodyHtml,
            role,
            index: callbackIndex,
            messages: callbackMessages,
            renderContent,
          })
          : null;
        const bodyHtml = customBodyHtml == null ? defaultBodyHtml : String(customBodyHtml);
        // Only a plain body folds; cards, questions and forwarded references keep their own display.
        const bodyBlockHtml = options.foldLongMessages !== false && !residentHandoffParts(content).some(part => part.cards)
          && content.trim() && bodyHtml && bodyHtml === defaultBodyHtml
          ? messageFoldBodyHtml(key, bodyHtml, messageFoldChoices.has(key)
            ? messageFoldChoices.get(key) === "expanded"
            : foldDefaults.has(key), foldSizes.get(key))
          : window.PortalI18n.html`<div class="messageContent markdownBody">${bodyHtml || window.PortalI18n.markup("<span class=\"paneNote\">无正文。</span>")}</div>`;
        const prefixHtml = historyPrefix + (options.renderMessagePrefix ? String(options.renderMessagePrefix(item, callbackIndex, callbackMessages) || "") : "");
        const showDefaultActions = options.defaultMessageActions !== false
          && (
            typeof options.showMessageActions !== "function"
            || options.showMessageActions(item, key, callbackIndex, callbackMessages) !== false
          );
        const actionsHtml = options.renderMessageActions
          ? String(options.renderMessageActions(item, callbackIndex, callbackMessages) || "")
          : (showDefaultActions ? commonMessageActionsHtml(item, key, options, role === "agent" && !continued) : "");
        const footerHtml = options.renderMessageFooter ? String(options.renderMessageFooter(item, callbackIndex, callbackMessages) || "") : "";
        const formatTime = options.formatTime || (value => String(value || ""));
        const activated = String(item?.activated_at || "");
        const queuedDelay = Date.parse(activated) - Date.parse(created);
        // Input order is when the Resident receives it, even if it waited in the queue.
        const delayedInput = role === "human" && Number.isFinite(queuedDelay) && queuedDelay >= 1000;
        const formattedCreated = delayedInput
          ? window.PortalI18n.text`${formatTime(activated)} 接单 · ${formatTime(created)} 发出`
          : formatTime(created);
        const workToggleHtml = isWorkHead
          ? window.PortalI18n.html`<button class="messageGroupToggle messageWorkToggle" data-portal-icon="${work.collapsed ? "chevron-down" : "chevron-up"}" type="button" data-portal-work-group-toggle="${escapeHtml(work.id)}" aria-expanded="${work.collapsed ? "false" : "true"}" aria-label="${window.PortalI18n.t(work.collapsed ? "展开这项工作的全部输出" : "折叠这项工作的全部输出")}" title="${window.PortalI18n.t(work.collapsed ? "展开这项工作的全部输出" : "折叠这项工作的全部输出")}"></button>` : "";
        const headRight = window.PortalI18n.html`<span class="messageHeadRight"><span>${escapeHtml(formattedCreated)}</span>${workToggleHtml}${actionsHtml}</span>`;
        const groupToggleHtml = isGroupEnd
            ? window.PortalI18n.html`<button class="messageGroupToggle" data-portal-icon="${group.collapsed ? "chevron-down" : "chevron-up"}" type="button" data-portal-agent-group-toggle="${escapeHtml(group.id)}" aria-expanded="${group.collapsed ? "false" : "true"}" aria-label="${group.collapsed ? window.PortalI18n.text`展开 ${group.intermediateCount} 条中间输出` : window.PortalI18n.text`折叠 ${group.intermediateCount} 条中间输出`}" title="${group.collapsed ? window.PortalI18n.text`展开 ${group.intermediateCount} 条中间输出` : window.PortalI18n.text`折叠 ${group.intermediateCount} 条中间输出`}"></button>`
          : "";
        const showHeader = !compactHeader;
        const continuationHtml = showHeader
          ? window.PortalI18n.html`<div class="messageHead">${messageAuthorHeaderHtml(item, messageTitle(item, callbackIndex, options), system, options)}${headRight}${continued ? groupToggleHtml : ""}</div>`
          : continued
          ? window.PortalI18n.html`<div class="messageDividerRow"><div class="messageDivider" role="separator" aria-label="${escapeHtml(options.assistantName || "Agent")} 继续回复"></div>${groupToggleHtml}${actionsHtml}</div>`
          : actionsHtml;
        const messageBodyHtml = `${continued ? "" : messageQuoteHtml(item?.quote, options)}${bodyBlockHtml}
        ${responseSourcesHtml(item?.response_sources)}
        ${messageContextRefsHtml(item?.context_refs || {}, options)}
        ${messageAttachmentsHtml(item?.context_refs?.attachments || item?.attachments || [], options)}`;
        // Delivered Family requests retain their body after cancellation. Keep
        // the member's terminal state visible even when the sender header folds.
        const cancellationHtml = role === "human" && item?.relay?.state === "cancelled"
          ? window.PortalI18n.html`<small class="messageCancelledBadge">已撤销</small>` : "";
        // Local human submissions are pending too; only Agent output gets the runtime accent.
        const runtimeClass = role === "agent" && item?.pending ? "liveRuntime" : "";
        return window.PortalI18n.html`<article class="messageItem ${escapeHtml(role)}${system ? " chatSystemCard" : ""} ${continued ? "continued" : ""}${compactHeader ? " senderContinued" : ""}${work?.collapsed ? " is-work-collapsed" : ""} ${runtimeClass} ${options.messageClass ? escapeHtml(options.messageClass(item, callbackIndex, callbackMessages) || "") : ""}" data-message-key="${escapeHtml(key)}" data-message-instance="${escapeHtml(messageInstanceKey(item))}"${work ? ` data-work-group-id="${escapeHtml(work.id)}"${isWorkHead ? ' data-work-group-head="true"' : ""}` : ""}${compactHeader ? ` aria-label="${escapeHtml(`${messageTitle(item, callbackIndex, options)} · ${formattedCreated}`)}" title="${escapeHtml(formattedCreated)}"` : ""}${system ? ` data-system-source="${system.source}"` : ""}${group ? ` data-agent-group-id="${escapeHtml(group.id)}"` : ""}${hiddenByGroup ? " hidden" : ""}>
        ${prefixHtml}
        ${continuationHtml}
        ${role === "human" && !system ? window.PortalI18n.html`<div class="messageBubble">${cancellationHtml}${messageBodyHtml}</div>` : messageBodyHtml}
        ${footerHtml}
      </article>${options.renderAfterMessage
        ? String(options.renderAfterMessage(item, callbackIndex, callbackMessages, { key: messageAfterKey(messageInstanceKey(item)), anchorKey: key,
          workCollapsed: collapsedWorkGroups.has(String(item?.family_status?.request_message_id || work?.id || "")) }) || "")
        : ""}`;
      }).join("");
      const tailHtml = renderEndIndex === items.length
        ? window.PortalI18n.html`${runtimeStreamHtml(runtimeDetails, runtimeLive, { ...options, messages: items })}<div class="messageScrollAnchor" data-message-key="__scroll_anchor__" data-message-scroll-anchor aria-hidden="true"></div>`
        : "";
      return window.PortalI18n.html`<div class="messageList">${historyHtml}${renderStartIndex === 0 ? inheritanceHtml : ""}${html}${tailHtml}</div>`;
    }

    const FORK_MODE_LABELS = Object.freeze({
      full: "完整上下文",
      compacted: "压缩继承",
      "recent-5": "最近 5 轮",
      "recent-10": "最近 10 轮",
      message: window.PortalI18n.t("从这里 Fork"),
    });

    // Where a Fork's inherited context came from, above the first inherited
    // message (decision_5bf5bfbd52cb). Its trace starts at the source's latest
    // compaction, so the source's earlier conversation is not in this Chat.
    function inheritanceCardHtml(inheritance, view = {}, options = {}) {
      if (!inheritance || typeof inheritance !== "object" || !inheritance.source_resident_id) return "";
      const formatTime = value => (value ? String(options.formatTime ? options.formatTime(value) : value) : "");
      const anchorPrefix = inheritance.fork_mode === "message" ? "context_source" : "source";
      const anchorMessage = inheritance.fork_mode === "message" ? inheritance.source_message_id : inheritance.last_message_id;
      const anchorHref = inheritance[`${anchorPrefix}_visible`] && anchorMessage
        ? `/lineage?${new URLSearchParams({
        ...(inheritance[`${anchorPrefix}_lineage_id`] ? { lineage: inheritance[`${anchorPrefix}_lineage_id`] } : {}),
        resident: inheritance[`${anchorPrefix}_resident_id`],
        message: anchorMessage,
        open_chat: "1",
      }).toString()}`
        : "";
      // The source's name opens its Chat at the Fork point when the caller can see it.
      const sourceHtml = (prefix) => {
        const id = String(inheritance[`${prefix}_resident_id`] || "");
        const label = inheritance[`${prefix}_visible`] && inheritance[`${prefix}_title`] ? inheritance[`${prefix}_title`] : id;
        if (!anchorHref || id !== String(inheritance[`${anchorPrefix}_resident_id`] || "")) {
          return `<span class="chatInheritanceSource" title="${escapeHtml(id)}">${escapeHtml(label)}</span>`;
        }
        const title = `${label === id ? "" : `${id} · `}${window.PortalI18n.t("在来源对话中查看 Fork 位置")}`;
        return `<a class="chatInheritanceSource" href="${escapeHtml(anchorHref)}" title="${escapeHtml(title)}">${escapeHtml(label)}</a>`;
      };
      const mode = FORK_MODE_LABELS[inheritance.fork_mode] ? window.PortalI18n.t(FORK_MODE_LABELS[inheritance.fork_mode]) : "";
      const meta = [mode, formatTime(inheritance.forked_from_at)].filter(Boolean).join(" · ");
      const contextSourceHtml = inheritance.context_source_resident_id
        && inheritance.context_source_resident_id !== inheritance.source_resident_id
        ? window.PortalI18n.html`<p class="chatInheritanceText">上下文来自 ${sourceHtml("context_source")}</p>`
        : "";
      const compaction = inheritance.opening_compaction && typeof inheritance.opening_compaction === "object"
        ? inheritance.opening_compaction
        : null;
      const count = Number(inheritance.message_count || 0);
      const compactedAt = formatTime(compaction?.created_at);
      let description = "";
      if (inheritance.context_cleared) {
        description = window.PortalI18n.t("继承的上下文已在之后清除，下方只显示清除之后的对话。");
      } else if (compaction && count) {
        description = window.PortalI18n.text`继承的上下文以来源 ${compactedAt} 的一次压缩开头，之后的 ${count} 条消息显示在下方。更早的对话只以压缩摘要的形式进入上下文。`;
      } else if (compaction) {
        description = window.PortalI18n.text`继承的上下文是来源 ${compactedAt} 的一次压缩。更早的对话只以压缩摘要的形式进入上下文。`;
      } else if (count) {
        description = window.PortalI18n.text`继承了来源的 ${count} 条消息，显示在下方。`;
      } else if (inheritance.recorded) {
        description = window.PortalI18n.t("继承的上下文中没有对话消息。");
      }
      const expanded = !!view?.expanded && !!compaction && !inheritance.context_cleared;
      const summaryButton = compaction && !inheritance.context_cleared
        ? `<button type="button" class="portalButton is-compact" data-chat-inheritance-summary aria-expanded="${expanded ? "true" : "false"}">${escapeHtml(expanded ? window.PortalI18n.t("收起压缩摘要") : window.PortalI18n.t("查看压缩摘要"))}</button>`
        : "";
      return window.PortalI18n.html`<section class="chatInheritanceCard" data-message-key="__inheritance__" aria-label="Fork">
      <div class="chatInheritanceHead"><strong>从 ${sourceHtml("source")} Fork 而来</strong>${meta ? `<span class="chatInheritanceMeta">${escapeHtml(meta)}</span>` : ""}</div>
      ${contextSourceHtml}
      ${description ? `<p class="chatInheritanceText">${escapeHtml(description)}</p>` : ""}
      ${summaryButton ? `<div class="chatInheritanceActions">${summaryButton}</div>` : ""}
      ${expanded ? inheritanceSummaryHtml(view, options) : ""}
    </section>`;
    }

    function inheritanceSummaryHtml(view = {}, options = {}) {
      if (view.status === "loading") {
        return `<div class="chatInheritanceSummary" aria-busy="true">${escapeHtml(window.PortalI18n.t("正在读取压缩摘要…"))}</div>`;
      }
      if (view.status === "error") {
        return `<div class="chatInheritanceSummary is-error" role="alert">${escapeHtml(window.PortalI18n.text`读取压缩摘要失败：${view.error || ""}`)}</div>`;
      }
      const compaction = view.compaction && typeof view.compaction === "object" ? view.compaction : {};
      const items = Array.isArray(compaction.items) ? compaction.items : [];
      const render = value => (options.renderMarkdown ? options.renderMarkdown(value) : escapeHtml(value).replace(/\n/g, "<br>"));
      const labeled = items.length > 1;
      const blocks = items.map(item => {
        const label = item.role === "agent" ? "Agent" : window.PortalI18n.t("用户");
        const images = Number(item.image_count || 0)
          ? `<p class="chatInheritanceNote">${escapeHtml(window.PortalI18n.text`另有 ${Number(item.image_count)} 张图片未显示`)}</p>`
          : "";
        return `<div class="chatInheritanceSummaryItem">${labeled ? `<div class="chatInheritanceSummaryRole">${escapeHtml(label)}</div>` : ""}<div class="messageContent">${render(String(item.text || ""))}</div>${images}</div>`;
      }).join("");
      const encrypted = compaction.encrypted
        ? `<p class="chatInheritanceNote">${escapeHtml(items.length
        ? window.PortalI18n.t("这次压缩还有一段由 Provider 加密保存的摘要，无法显示。")
        : window.PortalI18n.t("这次压缩的摘要由 Provider 加密保存，无法显示。"))}</p>`
        : "";
      const empty = !blocks && !encrypted
        ? `<p class="chatInheritanceNote">${escapeHtml(window.PortalI18n.t("这次压缩没有可显示的内容。"))}</p>`
        : "";
      return `<div class="chatInheritanceSummary">${blocks}${encrypted}${empty}</div>`;
    }

    function runtimeStreamHtml(details, live, options = {}) {
      const stream = normalizeMessageStream([], {
        runtimeDetails: details,
        runtimeLive: live,
        runtimePhase: options.runtimePhase,
      });
      const showRuntimeActivity = !PRE_PROVIDER_RUNTIME_PHASES.has(stream.runtimePhase)
        && (stream.runtimeLive || stream.runtimeDetails.length);
      const runtimeHtml = showRuntimeActivity
        ? window.PortalI18n.html`<article class="messageItem agent liveRuntime" data-message-key="__runtime__">
          <div class="messageHead"><span class="messageTitle">${escapeHtml(options.assistantName || "Agent")}</span><span>${stream.runtimeLive ? window.PortalI18n.t("运行中") : window.PortalI18n.t("活动")}</span></div>
          ${runtimeDetailsHtml(stream.runtimeDetails, {
            live: stream.runtimeLive,
            formatTime: options.formatTime,
            assistantName: options.assistantName,
            emptyLiveHtml: options.emptyLiveHtml || window.PortalI18n.html`<div class="paneNote">${escapeHtml(options.assistantName || "Agent")} 已开始运行，等待活动事件。</div>`,
          })}
        </article>`
        : "";
      return `${runtimePhaseStatusHtml(stream.runtimePhase)}${stream.runtimeLive ? nativePlanLiveHtml(stream.runtimeDetails, options.messages) + subagentLiveHtml(stream.runtimeDetails) : ""}${runtimeHtml}`;
    }

    function createMessageSelectionController(options = {}) {
      const root = options.root;
      if (!root) return null;
      const toolbar = document.createElement("div");
      toolbar.className = "chatSelectionContextAction";
      toolbar.hidden = true;
      toolbar.innerHTML = window.PortalI18n.html`<button type="button" data-portal-icon="plus" aria-label="${escapeHtml(options.label || window.PortalI18n.t("添加到上下文"))}"><span>${escapeHtml(options.label || window.PortalI18n.t("添加到上下文"))}</span></button>`;
      document.body.appendChild(toolbar);
      let candidate = null;
      let selectionFrame = 0;

      const clear = ({ preserveSelection = false } = {}) => {
        candidate = null;
        toolbar.hidden = true;
        if (!preserveSelection) window.getSelection?.()?.removeAllRanges?.();
      };
      const position = rect => {
        toolbar.hidden = false;
        const box = toolbar.getBoundingClientRect();
        const gap = 8;
        const left = Math.min(
          Math.max(gap, Number(rect.right || rect.left || gap) - box.width),
          Math.max(gap, window.innerWidth - box.width - gap),
        );
        const preferredTop = Number(rect.bottom || rect.top || gap) + gap;
        const top = preferredTop + box.height <= window.innerHeight - gap
          ? preferredTop
          : Math.max(gap, Number(rect.top || gap) - box.height - gap);
        toolbar.style.left = `${Math.round(left)}px`;
        toolbar.style.top = `${Math.round(top)}px`;
      };
      const refresh = () => {
        window.cancelAnimationFrame(selectionFrame);
        selectionFrame = window.requestAnimationFrame(() => {
          const next = messageSelectionCandidate(root);
          if (!next) {
            clear({ preserveSelection: true });
            return;
          }
          candidate = next;
          position(next.rect);
        });
      };
      const handleSelectionChange = () => {
        const selection = window.getSelection?.();
        if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
          if (!toolbar.hidden) clear({ preserveSelection: true });
          return;
        }
        const range = selection.getRangeAt(0);
        if (!root.contains(range.startContainer) && !root.contains(range.endContainer)) {
          if (!toolbar.hidden) clear({ preserveSelection: true });
          return;
        }
        refresh();
      };
      const handleToolbarPointerDown = event => event.preventDefault();
      const handleToolbarClick = () => {
        if (!candidate) return;
        const selected = candidate;
        Promise.resolve()
          .then(() => options.onAdd?.(selected))
          .then(() => clear())
          .catch(error => options.onError?.(error));
      };
      const handleKeyDown = event => {
        // Escape dismisses a shown action and its message selection; elsewhere,
        // such as the composer caret, the selection is not this controller's.
        if (event.key === "Escape" && !toolbar.hidden) clear();
      };
      const handleViewportChange = () => clear({ preserveSelection: true });
      root.addEventListener("mouseup", refresh);
      root.addEventListener("keyup", refresh);
      root.addEventListener("scroll", handleViewportChange, { passive: true });
      toolbar.addEventListener("pointerdown", handleToolbarPointerDown);
      toolbar.addEventListener("click", handleToolbarClick);
      document.addEventListener("selectionchange", handleSelectionChange);
      document.addEventListener("keydown", handleKeyDown);
      window.addEventListener("resize", handleViewportChange);

      return {
        clear,
        refresh,
        destroy() {
          window.cancelAnimationFrame(selectionFrame);
          root.removeEventListener("mouseup", refresh);
          root.removeEventListener("keyup", refresh);
          root.removeEventListener("scroll", handleViewportChange);
          toolbar.removeEventListener("pointerdown", handleToolbarPointerDown);
          toolbar.removeEventListener("click", handleToolbarClick);
          document.removeEventListener("selectionchange", handleSelectionChange);
          document.removeEventListener("keydown", handleKeyDown);
          window.removeEventListener("resize", handleViewportChange);
          toolbar.remove();
        },
      };
    }

    function reconcileMessageList(container, html, options = {}) {
      if (!container) return false;
      const selectionRanges = messageSelectionRanges();
      const template = document.createElement("template");
      template.innerHTML = String(html || "").trim();
      const nextRoot = template.content.firstElementChild;
      const currentRoot = container.firstElementChild;
      if (!nextRoot?.classList.contains("messageList") || !currentRoot?.classList.contains("messageList")) {
        container.replaceChildren(...template.content.childNodes);
        return { changedRoots: [container] };
      }
      const currentByKey = new Map();
      for (let node = options.currentStartNode || currentRoot.firstElementChild;
        node; node = node.nextElementSibling) {
        const key = messageNodeKey(node);
        if (key) currentByKey.set(key, node);
      }
      const retained = new Set();
      const changedRoots = [];
      // The prefix ends after the block shown after its last message, if any.
      let cursor = options.prefixNode || (options.prefixKey
        ? currentByKey.get(messageAfterKey(String(options.prefixKey))) || currentByKey.get(String(options.prefixKey)) || null
        : null);
      for (const nextNode of [...nextRoot.children]) {
        const key = messageNodeKey(nextNode);
        if (!key) continue;
        const current = currentByKey.get(key) || null;
        const preserveSelectedNode = current && selectionIntersectsNode(current, selectionRanges);
        const preserveOpenMenu = current?.classList.contains("menuOpen");
        if (current && !preserveSelectedNode && !preserveOpenMenu) {
          preserveRuntimeDisclosureState(current, nextNode);
          preserveDeferredImageState(current, nextNode);
        }
        let node = nextNode;
        if (current) {
          node = current;
          if (!preserveSelectedNode && !preserveOpenMenu && current.outerHTML !== nextNode.outerHTML) {
            reconcileQuestionMounts(current, nextNode);
            changedRoots.push(current);
          }
        } else {
          changedRoots.push(node);
        }
        const position = cursor ? cursor.nextSibling : currentRoot.firstChild;
        if (position !== node) currentRoot.insertBefore(node, position);
        cursor = node;
        retained.add(node);
      }
      for (const key of options.replacedKeys || currentByKey.keys()) {
        const node = currentByKey.get(String(key));
        if (node && !retained.has(node) && !selectionIntersectsNode(node, selectionRanges)) node.remove();
      }
      return { changedRoots };
    }

    return Object.freeze({
      consecutiveAgentGroups, familyWorkGroups, messageListHtml, inheritanceCardHtml,
      runtimeStreamHtml, createMessageSelectionController, preserveRuntimeDisclosureState,
      reconcileMessageList, messageViewportAtTail,
    });
  }

  window.PortalChatWorkspaceMessageList = Object.freeze({
    createMessageList, dependencies: DEPENDENCIES, preserveRuntimeDisclosureState,
    messageViewportAtTail,
  });
})();
