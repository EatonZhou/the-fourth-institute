(function () {
  "use strict";

  if (window.top !== window || window.PortalAgentInbox) return;

  const POSITION_PAGE = "agent-inbox";
  const ASSISTANT_PAGE = "personal-assistant-trigger";
  const TRIGGER_HEIGHT = 44;
  const VIEWPORT_GAP = 8;
  const AVOID_SELECTOR = ".composer, [data-portal-inbox-avoid]";
  const state = {
    snapshot: { entries: [], unreadCount: 0, loaded: false, ready: false, loading: false, refreshing: false, syncing: false, clearing: false, error: "" },
    touchExpanded: false,
    hovered: false,
    focused: false,
    activePanel: "mailbox",
    assistant: null,
    longPress: null,
    suppressEntryClickUntil: 0,
    verticalRatio: null,
    dockSide: "right",
    dragging: false,
    suppressTriggerClickUntil: 0,
    suppressHoverUntil: 0,
    navigationKeys: new Set(),
    clickTimers: new Map(),
    renderedEntries: new Map(),
    pressedPointerId: null,
    pressedKey: null,
    listRenderTimer: 0,
    unsubscribe: null,
    relativeTimer: 0,
    focusTimer: 0,
    listenerController: null,
  };
  let root = null;
  let notificationDetail = null;
  let conversationMenu = null;
  let todoPanel = null;
  let unregisterAvoidedElements = null;
  let layoutObserver = null;
  let positionFrame = 0;
  let mailboxChat = null;
  let assistantChat = null;
  let assistantMount = null;
  let assistantRefresh = null;
  const assistantInteractions = new Set();
  const residentDiscovery = window.PortalResidentDiscoveryService?.createClient?.() || null;

  function store() {
    return window.PortalConversationStore || null;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, Number(value) || 0));
  }

  function readVerticalRatio() {
    const ratio = Number(window.PortalState?.read?.(POSITION_PAGE, "device", {})?.verticalRatio);
    return Number.isFinite(ratio) ? clamp(ratio, 0, 1) : null;
  }

  function persistPosition() {
    if (Number.isFinite(state.verticalRatio)) {
      window.PortalState?.write?.(POSITION_PAGE, "device", { verticalRatio: clamp(state.verticalRatio, 0, 1), dockSide: state.dockSide });
    }
  }

  function verticalBounds() {
    const height = root?.getBoundingClientRect().height || TRIGGER_HEIGHT * (state.assistant ? 3 : 2);
    const viewportHeight = Math.max(height + VIEWPORT_GAP * 2, window.innerHeight || 0);
    const header = document.querySelector(".portalSiteHeader, [data-portal-site-header]");
    const headerBottom = Math.max(0, Number(header?.getBoundingClientRect?.().bottom || 0));
    const minimum = Math.min(
      viewportHeight - height - VIEWPORT_GAP,
      Math.max(VIEWPORT_GAP, headerBottom ? headerBottom + VIEWPORT_GAP : VIEWPORT_GAP),
    );
    return { minimum, maximum: Math.max(minimum, viewportHeight - height - VIEWPORT_GAP), viewportHeight, height };
  }

  function defaultVerticalTop(bounds) {
    return clamp(bounds.viewportHeight - TRIGGER_HEIGHT - clamp(bounds.viewportHeight * 0.1, 72, 96), bounds.minimum, bounds.maximum);
  }

  function applyVerticalPosition() {
    if (!root) return;
    const bounds = verticalBounds();
    const range = Math.max(0, bounds.maximum - bounds.minimum);
    if (!Number.isFinite(state.verticalRatio)) {
      const initialTop = defaultVerticalTop(bounds);
      state.verticalRatio = range ? (initialTop - bounds.minimum) / range : 0;
    }
    const preferredTop = bounds.minimum + range * clamp(state.verticalRatio, 0, 1);
    const triggerBounds = root.getBoundingClientRect();
    const obstacles = Array.from(document.querySelectorAll(AVOID_SELECTOR)).filter(element => !root.contains(element)).map(element => element.getBoundingClientRect())
      .filter(rect => rect.width > 0 && rect.height > 0 && rect.right > triggerBounds.left && rect.left < triggerBounds.right
        && rect.bottom > 0 && rect.top < bounds.viewportHeight);
    const candidates = [preferredTop, bounds.minimum, bounds.maximum, ...obstacles.flatMap(rect => [
      rect.top - bounds.height - VIEWPORT_GAP, rect.bottom + VIEWPORT_GAP,
    ])].filter(top => top >= bounds.minimum && top <= bounds.maximum)
      .sort((left, right) => Math.abs(left - preferredTop) - Math.abs(right - preferredTop));
    const top = state.dragging ? preferredTop : candidates.find(candidate => obstacles.every(rect => (
      candidate + bounds.height <= rect.top - VIEWPORT_GAP || candidate >= rect.bottom + VIEWPORT_GAP
    ))) ?? preferredTop;
    const spaceAbove = Math.max(120, top + bounds.height - VIEWPORT_GAP);
    const spaceBelow = Math.max(120, bounds.viewportHeight - top - VIEWPORT_GAP);
    const below = spaceBelow >= spaceAbove;
    root.style.setProperty("--portal-agent-inbox-top", `${Math.round(top)}px`);
    root.style.setProperty("--portal-agent-inbox-panel-max-height", `${Math.floor(below ? spaceBelow : spaceAbove)}px`);
    root.dataset.panelPlacement = below ? "below" : "above";
    const style = document.documentElement.style;
    const left = state.dockSide === "left";
    const clearance = Math.ceil(triggerBounds.width + VIEWPORT_GAP);
    style.setProperty("--portal-floating-left", left ? clearance + "px" : "0px");
    style.setProperty("--portal-floating-right", left ? "0px" : clearance + "px");
    style.setProperty("--portal-floating-width", Math.ceil(triggerBounds.width) + "px");
    style.setProperty("--portal-floating-height", Math.ceil(bounds.height) + "px");
    style.setProperty("--portal-floating-tail", Math.ceil(bounds.viewportHeight - top + VIEWPORT_GAP) + "px");
  }

  // The last confirmed target, per user, lets a new page draw both triggers in
  // the same frame; resolving it takes three requests.
  function cachedAssistant() {
    const username = window.PortalIdentity?.username?.() || "";
    const target = username ? window.PortalState?.read?.(ASSISTANT_PAGE, username, {})?.target : null;
    return /^resident_[0-9a-f]{12}$/.test(target?.resident_id || "") ? target : null;
  }

  function rememberAssistant(target) {
    const username = window.PortalIdentity?.username?.() || "";
    if (username) window.PortalState?.replace?.(ASSISTANT_PAGE, username, target ? { target } : {});
  }

  function showAssistant(target) {
    if (target?.resident_id !== state.assistant?.resident_id) {
      assistantChat?.destroy();
      assistantChat = null;
      assistantMount = null;
      root.querySelector("[data-personal-assistant-chat]").replaceChildren();
    }
    state.assistant = target;
    if (!target && state.activePanel === "assistant") state.activePanel = "mailbox";
    const trigger = root.querySelector("[data-personal-assistant-trigger]");
    trigger.hidden = !target;
    if (target) {
      trigger.dataset.portalTooltip = `${target.title} · ${window.PortalI18n.t("按住拖动到左侧或右侧")}`;
      trigger.dataset.portalTooltipSource = "explicit";
      delete trigger.dataset.portalGeneratedTooltip;
    }
    root.dataset.hasAssistant = String(!!target);
  }

  async function refreshAssistant() {
    if (assistantRefresh) return assistantRefresh;
    const mountedRoot = root;
    const request = window.PortalNav.personalAssistantTarget().catch(() => null).then(target => {
      if (!root || root !== mountedRoot) return;
      rememberAssistant(target);
      showAssistant(target);
      applyVerticalPosition();
      updateChrome();
    }).finally(() => { if (assistantRefresh === request) assistantRefresh = null; });
    assistantRefresh = request;
    return request;
  }

  async function mountAssistantChat() {
    if (assistantChat || assistantMount || !state.assistant) return;
    const target = state.assistant;
    const mountedRoot = root;
    const container = root.querySelector("[data-personal-assistant-chat]");
    const current = () => root === mountedRoot && container.isConnected
      && state.assistant?.resident_id === target.resident_id;
    let candidate = null;
    container.textContent = window.PortalI18n.t("正在读取个人助手…");
    const attempt = (async () => {
      await window.PortalNav.loadChatWorkspace();
      if (!current()) return;
      container.replaceChildren();
      candidate = window.PortalChatWorkspace.panelPackage.create({
        root: container, id: "personalAssistantChat", target, title: target.title,
        onInteractionChange(open, source) {
          if (open) { assistantInteractions.add(source); updateChrome(); }
          else window.setTimeout(() => {
            assistantInteractions.delete(source);
            state.focused = keepsPanelFocused(document.activeElement);
            updateChrome();
          }, 0);
        },
      });
      candidate.setPresented(mountedRoot.querySelector("[data-personal-assistant-trigger]").getAttribute("aria-expanded") === "true");
      if (!current()) { candidate.destroy(); candidate = null; return; }
      assistantChat = candidate;
    })().catch(error => {
      candidate?.destroy();
      candidate = null;
      if (!current()) return;
      container.replaceChildren();
      const retry = document.createElement("button");
      retry.className = "portalButton";
      retry.textContent = window.PortalI18n.t("个人助手暂不可用，点击重试");
      retry.title = error.message || "";
      retry.addEventListener("click", () => mountAssistantChat(), { once: true });
      container.append(retry);
    }).finally(() => { if (assistantMount === attempt) assistantMount = null; });
    assistantMount = attempt;
  }

  function returnToMailbox({ focus = true } = {}) {
    const chat = mailboxChat;
    if (!chat) return;
    mailboxChat = null; // Invalidate in-flight asset and room reads first.
    chat.container.hidden = true;
    chat.view?.destroy();
    chat.container.replaceChildren();
    root?.removeAttribute("data-chatroom-open");
    if (root) {
      const list = root.querySelector("[data-agent-conversation-list]");
      list.scrollTop = chat.scrollTop;
      if (focus) {
        const item = [...list.querySelectorAll("[data-agent-conversation-key]")]
          .find(item => item.dataset.agentConversationKey === chat.key);
        (item || root.querySelector("[data-agent-conversation-trigger]")).focus({ preventScroll: true });
      }
    }
  }

  async function openMailboxChatroom(entry) {
    returnToMailbox({ focus: false });
    const container = root.querySelector("[data-mailbox-chatroom-view]");
    const chat = { container, key: entry.key, view: null,
      actor: window.PortalIdentity.username(),
      scrollTop: root.querySelector("[data-agent-conversation-list]").scrollTop };
    const current = () => mailboxChat === chat && container.isConnected
      && window.PortalIdentity.username() === chat.actor;
    mailboxChat = chat;
    root.dataset.chatroomOpen = "true";
    state.activePanel = "mailbox";
    state.touchExpanded = true;
    container.hidden = false;
    container.textContent = window.PortalI18n.t("正在读取聊天室。");
    updateChrome();
    root.querySelector("[data-mailbox-chatroom-back]").focus({ preventScroll: true });
    try {
      await window.PortalNav.loadChatWorkspace();
      await window.PortalUIPrimitives.loadAsset("/static/portal_components_conversation.css");
      await window.PortalUIPrimitives.loadAsset("/static/portal_chatroom_view.js", { ready: () => !!window.PortalChatroomView });
      if (!current()) return;
      container.replaceChildren();
      chat.view = window.PortalChatroomView.mount({
        root: container, id: "mailbox-chatroom", me: () => chat.actor,
        onMenu: button => openConversationMenu(button, undefined, chat.key),
      });
      const messageId = new URL(entry.url, window.location.href).searchParams.get("message") || "";
      await chat.view.setChatroom(entry.chatroom_id, { messageId });
    } catch (error) {
      if (!current()) return;
      chat.view?.destroy();
      chat.view = null;
      container.replaceChildren();
      const message = document.createElement("p");
      message.textContent = error?.message || window.PortalI18n.t("聊天室不可用");
      const retry = document.createElement("button");
      retry.className = "portalButton";
      retry.textContent = window.PortalI18n.t("重试");
      retry.addEventListener("click", () => openMailboxChatroom(entry), { once: true });
      container.append(message, retry);
    }
  }

  function watchAvoidanceLayout(signal) {
    const schedule = () => {
      if (positionFrame) return;
      positionFrame = window.requestAnimationFrame(() => {
        positionFrame = 0;
        applyVerticalPosition();
      });
    };
    layoutObserver = new ResizeObserver(schedule);
    unregisterAvoidedElements = window.PortalUIPrimitives.registerEnhancer({
      id: "inbox-avoidance", selector: AVOID_SELECTOR,
      enhance(element) { layoutObserver.observe(element); schedule(); },
      remove(element) { layoutObserver.unobserve(element); schedule(); },
    });
    // Scrolling an Inspector moves its actions without resizing them.
    document.addEventListener("scroll", schedule, { capture: true, passive: true, signal });
  }

  function updateChrome() {
    if (!root) return;
    const snapshot = state.snapshot;
    const expanded = !state.dragging && Boolean(state.touchExpanded || state.hovered || state.focused || conversationMenu?.controller.isOpen()
      || (state.activePanel === "assistant" && assistantInteractions.size));
    const assistantOpen = expanded && state.activePanel === "assistant" && !!state.assistant;
    const mailboxOpen = expanded && state.activePanel === "mailbox";
    const todoOpen = expanded && state.activePanel === "todo";
    const mobileOverlayOpen = expanded && usesTouchExpansion();
    if (mailboxChat && !mailboxOpen) returnToMailbox({ focus: false });
    store()?.setInboxOpen?.(mailboxOpen);
    todoPanel?.setView({ visible: mailboxOpen || todoOpen, active: todoOpen });
    root.dataset.expanded = expanded ? "true" : "false";
    root.dataset.activePanel = state.activePanel;
    root.dataset.mobileOverlay = mobileOverlayOpen ? "true" : "false";
    document.body?.classList.toggle("portalAgentInboxOverlayOpen", mobileOverlayOpen);
    root.querySelector("[data-agent-conversation-trigger]")?.setAttribute("aria-expanded", String(mailboxOpen));
    root.querySelector("[data-agent-conversation-trigger]")?.setAttribute("aria-label", window.PortalI18n.t(mailboxOpen ? "收起我的 Mailbox" : "展开我的 Mailbox"));
    root.querySelector("[data-personal-assistant-trigger]")?.setAttribute("aria-expanded", String(assistantOpen));
    root.querySelector("[data-personal-assistant-trigger]")?.setAttribute("aria-label", window.PortalI18n.t(assistantOpen ? "收起个人助手" : "展开个人助手"));
    root.querySelector("[data-agent-todo-trigger]").setAttribute("aria-expanded", String(todoOpen));
    root.querySelector("[data-agent-todo-trigger]").setAttribute("aria-label", window.PortalI18n.t(todoOpen ? "收起 To Do" : "展开 To Do"));
    root.querySelectorAll("[data-inbox-panel]").forEach(panel => {
      panel.inert = !expanded || panel.dataset.inboxPanel !== state.activePanel;
    });
    if (assistantChat && assistantChat.presented !== assistantOpen) assistantChat.setPresented(assistantOpen);
    const mobileClose = root.querySelector("[data-agent-inbox-mobile-close]");
    if (mobileClose) {
      mobileClose.hidden = !mobileOverlayOpen;
      mobileClose.setAttribute("aria-label", window.PortalI18n.t(assistantOpen ? "关闭个人助手并返回当前页面" : todoOpen ? "关闭 To Do 并返回当前页面" : "关闭 Mailbox 并返回当前页面"));
    }
    const assistantUnread = snapshot.entries.some(entry => entry.resident_id === state.assistant?.resident_id && (entry.attention_unread ?? entry.unread));
    root.querySelector("[data-personal-assistant-unread]").hidden = !assistantUnread;
    if (assistantOpen) void mountAssistantChat();
    root.querySelectorAll("[data-agent-conversation-count]").forEach(badge => {
      const count = snapshot.unreadCount;
      badge.textContent = count > 99 ? "99+" : String(count || "");
      badge.hidden = count < 1;
      badge.title = window.PortalI18n.t("Mailbox 未读数");
    });
    const todos = todoPanel?.snapshot() || {};
    root.querySelectorAll("[data-agent-todo-count]").forEach(badge => {
      const count = todos.pendingCount || 0;
      badge.textContent = count > 99 ? "99+" : String(count || "");
      badge.hidden = count < 1;
      badge.title = window.PortalI18n.t("未完成待办");
    });
    root.querySelector("[data-agent-todo-summary]").textContent = window.PortalI18n.text`${todos.count || 0} 条`;
    const summary = root.querySelector("[data-agent-conversation-action-summary]");
    if (summary) summary.textContent = window.PortalI18n.text`${snapshot.refreshing ? window.PortalI18n.t("正在更新 · ") : ""}${snapshot.paged ? window.PortalI18n.t("已加载 ") : ""}${snapshot.entries.length} 条`;
    const error = root.querySelector("[data-agent-conversation-error]");
    if (error) {
      error.hidden = !snapshot.error;
      error.textContent = !snapshot.error ? "" : snapshot.entries.length
        ? window.PortalI18n.t("Mailbox 刷新失败，当前显示上次加载的内容。请点击右上角刷新重试。")
        : window.PortalI18n.t("Mailbox 暂时无法加载，请点击右上角刷新重试。");
    }
    const loadMore = root.querySelector("[data-agent-conversation-load-more]");
    if (loadMore) {
      loadMore.hidden = !snapshot.nextPageToken;
      loadMore.disabled = snapshot.loadingMore;
      loadMore.textContent = snapshot.loadingMore ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("加载更早的 Mailbox 条目");
    }
    const clearUnread = root.querySelector("[data-agent-conversation-clear-unread]");
    const clearAll = root.querySelector("[data-agent-conversation-clear-all]");
    if (clearUnread) clearUnread.disabled = snapshot.clearing || (!snapshot.entries.some(entry => entry.unread) && !snapshot.nextPageToken);
    if (clearAll) clearAll.disabled = snapshot.clearing || (!snapshot.entries.some(entry => !entry.pinned) && !snapshot.nextPageToken);
  }

  function keepsPanelFocused(element) {
    return Boolean(root?.contains(element) && (element.matches(":focus-visible")
      || element.closest(".portalPersonalAssistantPanel")));
  }

  function usesTouchExpansion(event) {
    if (event?.pointerType === "touch") return true;
    if (window.PortalResponsive?.isMobile?.()) return true;
    return window.matchMedia?.("(hover: none), (pointer: coarse)")?.matches === true;
  }

  function bindTriggerDragging(trigger, signal) {
    let gesture = null;
    const finish = event => {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      const { moved, originalRatio } = gesture;
      const cancelled = event.type !== "pointerup";
      if (moved && !cancelled) {
        const rect = root.getBoundingClientRect();
        state.dockSide = rect.left + rect.width / 2 < window.innerWidth / 2 ? "left" : "right";
      } else if (cancelled) {
        state.verticalRatio = originalRatio;
      }
      gesture = null;
      state.dragging = false;
      root.dataset.dockSide = state.dockSide;
      root.style.removeProperty("left");
      root.style.removeProperty("right");
      root?.removeAttribute("data-dragging");
      if (trigger.hasPointerCapture?.(event.pointerId)) trigger.releasePointerCapture(event.pointerId);
      if (moved) {
        const until = performance.now() + 400;
        state.hovered = false;
        state.focused = false;
        state.touchExpanded = false;
        state.suppressTriggerClickUntil = until;
        state.suppressHoverUntil = until;
        if (document.activeElement === trigger) trigger.blur();
        if (!cancelled) persistPosition();
        applyVerticalPosition();
      } else {
        state.hovered = event.pointerType !== "touch" && Boolean(root?.matches(":hover"));
      }
      updateChrome();
    };
    trigger.addEventListener("pointerdown", event => {
      if (state.dragging || event.isPrimary === false || (event.pointerType === "mouse" && event.button !== 0)) return;
      const rect = root.getBoundingClientRect();
      gesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startLeft: rect.left,
        startTop: rect.top,
        originalRatio: state.verticalRatio,
        moved: false,
      };
      state.dragging = true;
      state.hovered = false;
      root.dataset.dragging = "true";
      trigger.setPointerCapture?.(event.pointerId);
      updateChrome();
    }, { signal });
    trigger.addEventListener("pointermove", event => {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      const deltaX = event.clientX - gesture.startX;
      const deltaY = event.clientY - gesture.startY;
      if (!gesture.moved && Math.hypot(deltaX, deltaY) < 4) return;
      gesture.moved = true;
      const bounds = verticalBounds();
      const top = clamp(gesture.startTop + deltaY, bounds.minimum, bounds.maximum);
      const maxLeft = Math.max(0, window.innerWidth - root.getBoundingClientRect().width);
      root.style.left = `${clamp(gesture.startLeft + deltaX, 0, maxLeft)}px`;
      root.style.right = "auto";
      state.verticalRatio = (top - bounds.minimum) / Math.max(1, bounds.maximum - bounds.minimum);
      applyVerticalPosition();
      event.preventDefault();
    }, { signal });
    trigger.addEventListener("pointerup", finish, { signal });
    trigger.addEventListener("pointercancel", finish, { signal });
    trigger.addEventListener("lostpointercapture", finish, { signal });
  }

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);

  function relativeTime(value) {
    const timestamp = new Date(String(value || "")).getTime();
    if (!Number.isFinite(timestamp)) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 45) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
    if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d`;
    // Inbox grouping intentionally omits the year and time to keep recent sections compact.
    return window.PortalUI.formatDateTime(timestamp, {
      locale: "zh-CN",
      format: { month: "numeric", day: "numeric" },
    });
  }

  function routedEntry(entry) {
    return { ...entry, ...(window.PortalNav?.agentEntryDefaults?.(entry) || {}) };
  }

  function conversationStatus(entry) {
    const status = String(entry?.status || "idle").trim().toLowerCase();
    return status;
  }

  function entryIcon(entry) {
    if (["failed", "interrupted"].includes(conversationStatus(entry))) return "alert-circle";
    const kind = String(entry?.kind || "");
    if (kind === "resident") return "chat";
    if (kind === "chatroom") return "users";
    if (isNotification(entry)) return "alert-triangle";
    return "chat";
  }

  // A notification is a message to its recipients, not a conversation: it has
  // no Chat whose presentation could confirm the read.
  function isNotification(entry) {
    return entry?.section === "notification";
  }

  function statusMarkup(entry) {
    const status = conversationStatus(entry);
    const family = !!entry?.resident_state?.family_activity;
    if (family && status === "idle") return window.PortalI18n.markup('<span class="portalAgentInboxStatus idle">空闲</span>');
    if (family && status === "queued") return window.PortalI18n.markup('<span class="portalAgentInboxStatus queued">排队中</span>');
    if (status === "running") {
      const resident = ["resident-main", "resident"].includes(entry?.kind);
      const mark = resident ? window.PortalI18n.markup('<span class="portalAgentInboxRunningMark" aria-hidden="true"></span>') : "";
      return window.PortalI18n.html`<span class="portalAgentInboxStatus status-badge running${resident ? " is-resident-running" : ""}">${mark}${window.PortalI18n.t("运行中")}</span>`;
    }
    if (status === "queued") return window.PortalI18n.markup('<span class="portalAgentInboxStatus queued">排队</span>');
    if (status === "failed") return window.PortalI18n.markup('<span class="portalAgentInboxStatus failed">失败</span>');
    if (status === "interrupted") return window.PortalI18n.markup('<span class="portalAgentInboxStatus interrupted">已中断</span>');
    if (status === "archived") return window.PortalI18n.markup('<span class="portalAgentInboxStatus archived">归档</span>');
    return "";
  }

  function entryMarkup(entry) {
    const routed = routedEntry(entry);
    const title = String(routed?.title || window.PortalI18n.t("Agent conversation"));
    const subtitle = String(routed?.subtitle || "");
    const source = String(routed?.surface_label || "Portal");
    const residentConversation = ["resident-main", "resident"].includes(routed?.kind);
    const updatedAt = String(routed?.updated_at || "");
    const status = conversationStatus(routed);
    const closed = String(routed?.intake_state || "").trim().toLowerCase() === "closed";
    const pinMarkup = routed.pinned ? window.PortalI18n.markup('<span class="portalAgentInboxItemPin" data-agent-inbox-pinned data-portal-icon="pin" title="已置顶" aria-label="已置顶"></span>') : "";
    const intakeMarkup = closed
      ? window.PortalI18n.markup('<span class="portalAgentInboxStatus closed" title="消息接收已关闭，仍可打开对话查看历史" aria-label="消息接收已关闭">closed</span>')
      : "";
    const issueClass = ["failed", "interrupted"].includes(status) ? ` terminalIssue ${status}` : "";
    const entryType = isNotification(routed) ? "notification" : "conversation";
    const unreadCount = Number(routed?.unread_count);
    const mentions = Number(routed?.unread_mention_count || 0);
    const attention = routed?.attention_unread ?? routed?.unread;
    // Counts belong to the source (currently Chatroom). A result-only unread
    // flag on a Resident or notification is not a count of messages.
    const unreadMarkup = !routed?.unread ? ""
      : Number.isSafeInteger(unreadCount) && unreadCount > 0
        ? window.PortalI18n.html`<span class="portalAgentInboxUnreadCount${routed.muted ? " is-muted" : ""}" aria-label="${unreadCount} 条未读消息" title="${unreadCount} 条未读消息">${unreadCount > 99 ? "99+" : unreadCount}</span>`
        : window.PortalI18n.html`<span class="portalAgentInboxUnreadDot${routed.muted ? " is-muted" : ""}" aria-label="未读"></span>`;
    return window.PortalI18n.html`<button class="portalAgentInboxItem${attention ? " unread" : ""}${issueClass}" type="button" data-agent-conversation-key="${esc(routed?.key || "")}" data-entry-type="${entryType}" data-status="${esc(status)}" title="${esc(title)}">
      <span class="portalAgentInboxItemIcon" data-portal-icon="${entryIcon(routed)}" aria-hidden="true"></span>
      <span class="portalAgentInboxItemBody"><span class="portalAgentInboxItemTitle"><strong>${esc(title)}</strong>${pinMarkup}</span><small><span>${esc(subtitle || source)}</span>${!residentConversation && subtitle && subtitle !== source ? window.PortalI18n.html`<span>${esc(source)}</span>` : ""}<time datetime="${esc(updatedAt)}" data-agent-conversation-time>${esc(relativeTime(updatedAt))}</time></small></span>
      <span class="portalAgentInboxItemState"><span class="portalAgentInboxItemMeta"><span class="portalAgentInboxItemType">${entryType === "notification" ? window.PortalI18n.t("通知") : window.PortalI18n.t("对话")}</span>${routed.muted ? window.PortalI18n.markup('<span class="chatroomMutedMark" data-portal-icon="bell-off" title="消息免打扰" aria-label="消息免打扰"></span>') : ""}${intakeMarkup}</span><span class="portalAgentInboxItemIndicators">${statusMarkup(routed)}${unreadMarkup}${mentions ? window.PortalI18n.html`<span class="chatroomMentionBadge" title="定位第一条未读 @ 消息">@我 ${mentions}</span>` : ""}</span></span>
    </button>`;
  }

  function renderList() {
    if (!root) return;
    updateChrome();
    // Keep the pressed card and its position stable until activation resolves.
    // Store notifications can arrive between pointer/key down and the click.
    if (state.pressedPointerId !== null || state.pressedKey !== null || state.clickTimers.size) return;
    const list = root.querySelector("[data-agent-conversation-list]");
    const snapshot = state.snapshot;
    const entries = snapshot.entries;
    if (entries.length) {
      const focused = list.contains(document.activeElement) ? document.activeElement : null;
      const template = document.createElement("template");
      const rendered = new Map();
      entries.forEach((entry, index) => {
        const markup = entryMarkup(entry);
        let record = state.renderedEntries.get(entry.key);
        if (!record || record.markup !== markup) {
          template.innerHTML = markup;
          const button = template.content.firstElementChild;
          if (record) {
            for (const { name, value } of button.attributes) record.button.setAttribute(name, value);
            record.button.replaceChildren(...button.childNodes);
          } else {
            record = { button };
          }
          record.markup = markup;
        }
        if (list.children[index] !== record.button) list.insertBefore(record.button, list.children[index] || null);
        rendered.set(entry.key, record);
      });
      for (const child of Array.from(list.children)) {
        if (!rendered.has(child.dataset.agentConversationKey)) child.remove();
      }
      state.renderedEntries = rendered;
      // Moving a focused button to its new sorted position can blur it.
      if (focused && list.contains(focused) && document.activeElement !== focused) focused.focus({ preventScroll: true });
    }
    else if (!snapshot.loaded && snapshot.loading) list.innerHTML = window.PortalI18n.markup('<div class="portalAgentInboxEmpty">正在读取 Mailbox…</div>');
    else if (snapshot.error) list.innerHTML = "";
    else if (!snapshot.ready) list.innerHTML = window.PortalI18n.markup('<div class="portalAgentInboxEmpty">正在建立 Mailbox 索引…</div>');
    else list.innerHTML = window.PortalI18n.markup('<div class="portalAgentInboxEmpty">Mailbox 中暂无消息</div>');
    if (!entries.length) state.renderedEntries.clear();
    window.PortalUIPrimitives?.mountIcons?.(list);
  }

  function releaseListInteraction(event) {
    if (state.pressedPointerId === null && state.pressedKey === null) return;
    if (event.type === "blur") {
      state.pressedPointerId = null;
      state.pressedKey = null;
    } else if (event.type === "keyup") {
      if (event.key !== state.pressedKey) return;
      state.pressedKey = null;
    } else {
      if (event.pointerId !== state.pressedPointerId) return;
      state.pressedPointerId = null;
    }
    window.clearTimeout(state.listRenderTimer);
    // The browser dispatches click after pointerup/keyup. Let its handler run before
    // reconciling the latest snapshot, including when the gesture was cancelled.
    state.listRenderTimer = window.setTimeout(() => {
      state.listRenderTimer = 0;
      renderList();
    }, 0);
  }

  function updateRelativeTimes() {
    root?.querySelectorAll("[data-agent-conversation-key]").forEach(node => {
      const entry = store()?.entry?.(node.dataset.agentConversationKey);
      const time = node.querySelector("[data-agent-conversation-time]");
      if (entry && time) time.textContent = relativeTime(entry.updated_at);
    });
  }

  function navigationResidentId(entry) {
    try {
      const target = new URL(String(entry?.url || ""), window.location.href);
      return String(target.searchParams.get("resident") || entry?.resident_id || "").trim();
    } catch (_error) {
      return String(entry?.resident_id || "").trim();
    }
  }

  async function verifyNavigationTarget(entry) {
    const residentId = navigationResidentId(entry);
    if (!residentId) return entry;
    if (!residentDiscovery) {
      window.PortalUI?.toast?.(window.PortalI18n.t("无法确认 Resident 状态，请稍后重试。"), "error");
      return null;
    }
    try {
      await residentDiscovery.resolveNavigationTarget(residentId);
      return entry;
    } catch (error) {
      if (Number(error?.status || 0) === 404) {
        store()?.discardResident?.(residentId);
        window.PortalUI?.toast?.(window.PortalI18n.t("该 Resident 已删除或不存在。"), "warning");
        return null;
      }
      if (Number(error?.status || 0) === 403) {
        store()?.discardResident?.(residentId);
        window.PortalUI?.toast?.(window.PortalI18n.t("没有查看这个 Resident 的权限。"), "warning");
        return null;
      }
      window.PortalUI?.toast?.("无法确认 Resident 状态，请稍后重试。", "error");
      return null;
    }
  }

  async function navigateToEntry(entry) {
    const target = routedEntry(entry);
    if (!target?.url) return;
    if (target.kind === "chatroom" && target.chatroom_id) return openMailboxChatroom(target);
    const navigationKey = String(target?.key || target.url).trim();
    if (state.navigationKeys.has(navigationKey)) return;
    state.navigationKeys.add(navigationKey);
    try {
      const verifiedTarget = await verifyNavigationTarget(target);
      if (!verifiedTarget) return;
      const request = { entry: verifiedTarget, handled: false };
      window.dispatchEvent(new CustomEvent("portal:agent-conversation-open", { detail: request }));
      if (request.handled) {
        state.touchExpanded = false;
        updateChrome();
        return;
      }
      window.location.assign(verifiedTarget.url);
    } finally {
      state.navigationKeys.delete(navigationKey);
    }
  }

  function ensureNotificationDetail() {
    if (notificationDetail) return notificationDetail;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.hidden = true;
    overlay.dataset.agentNotificationDetail = "";
    overlay.innerHTML = window.PortalI18n.html`<section class="portalDetailSurface portalAgentNotificationSurface" role="dialog" aria-modal="true" aria-labelledby="portalAgentNotificationTitle" tabindex="-1">
      <header class="portalDetailHeader"><div class="portalAgentNotificationHeading">
        <div class="portalAgentNotificationContext"><span class="portalAgentNotificationSeverity" data-agent-notification-severity></span><span class="portalAgentNotificationSource" data-agent-notification-origin></span></div>
        <h2 id="portalAgentNotificationTitle"></h2>
        <div class="portalAgentNotificationMeta" data-agent-notification-meta><span data-agent-notification-sender></span><time data-agent-notification-time></time></div>
      </div><button class="portalDetailClose" type="button" data-agent-notification-close data-portal-icon="close" aria-label="关闭通知详情"></button></header>
      <div class="portalDetailBody"><div class="portalAgentNotificationMessage" data-agent-notification-message></div></div>
      <footer class="portalAgentNotificationActions"><button class="portalButton" type="button" data-agent-notification-close>关闭</button><button class="portalButton is-primary" type="button" data-agent-notification-source></button></footer>
    </section>`;
    document.body.appendChild(overlay);
    const runtime = { overlay, entry: null, controller: null };
    runtime.controller = window.PortalUI.createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      initialFocus: overlay.querySelector('[data-agent-notification-close]'),
      onClose: () => {
        runtime.entry = null;
        overlay.querySelector("[data-agent-notification-message]").textContent = "";
      },
    });
    overlay.addEventListener("click", event => {
      if (event.target.closest("[data-agent-notification-close]")) runtime.controller.close();
      else if (event.target.closest("[data-agent-notification-source]")) {
        const entry = runtime.entry;
        runtime.controller.close();
        void navigateToEntry(entry);
      }
    }, { signal: state.listenerController.signal });
    window.PortalUIPrimitives?.mountIcons?.(overlay);
    notificationDetail = runtime;
    return runtime;
  }

  function activateEntry(entry) {
    if (!isNotification(entry)) return navigateToEntry(entry);
    if (entry.kind === "notice-board-mention") {
      void store()?.markRead?.(entry.key)?.catch?.(() => {});
      return navigateToEntry(entry);
    }
    const detail = entry.notification;
    if (!detail || typeof detail.message !== "string") {
      window.PortalUI?.toast?.("通知详情暂不可用，请刷新列表后重试。", "warning");
      return;
    }
    const runtime = ensureNotificationDetail();
    runtime.entry = routedEntry(entry);
    const overlay = runtime.overlay;
    overlay.querySelector("#portalAgentNotificationTitle").textContent = entry.title || window.PortalI18n.t("通知");
    const severities = { info: "信息", warning: "提醒", error: "错误", success: "完成" };
    const severity = Object.hasOwn(severities, detail.severity) ? detail.severity : "info";
    const badge = overlay.querySelector("[data-agent-notification-severity]");
    badge.dataset.severity = severity;
    badge.textContent = window.PortalI18n.t(severities[severity]);
    overlay.querySelector("[data-agent-notification-origin]").textContent = detail.lineage_title || (detail.lineage_id ? "Lineage" : "Portal");
    const sender = detail.created_by === "portal" ? window.PortalI18n.t("Portal 系统") : detail.created_by;
    const senderLabel = overlay.querySelector("[data-agent-notification-sender]");
    senderLabel.textContent = sender ? window.PortalI18n.text`发送者：${sender}` : "";
    senderLabel.hidden = !sender;
    const time = overlay.querySelector("[data-agent-notification-time]");
    time.dateTime = detail.created_at || entry.updated_at || "";
    time.textContent = window.PortalUI.formatDateTime(time.dateTime);
    overlay.querySelector("[data-agent-notification-message]").textContent = detail.message;
    const sourceButton = overlay.querySelector("[data-agent-notification-source]");
    sourceButton.textContent = detail.lineage_id ? window.PortalI18n.t("打开所属 Lineage") : window.PortalI18n.t("打开来源页面");
    sourceButton.hidden = !runtime.entry.url;
    if (runtime.controller.open()) {
      void store()?.markRead?.(entry.key)?.catch?.(() => {
        window.PortalUI?.toast?.("通知已显示，但已读状态保存失败，请稍后重试。", "warning");
      });
    }
  }

  function handleEntryClick(event, button) {
    if (performance.now() < state.suppressEntryClickUntil) return;
    const key = String(button?.dataset.agentConversationKey || "");
    const entry = store()?.entry?.(key);
    if (!entry) return;
    if (event.detail === 0) return void activateEntry(entry);
    const existing = state.clickTimers.get(key);
    if (existing) window.clearTimeout(existing);
    if (event.detail > 1) return state.clickTimers.delete(key);
    state.clickTimers.set(key, window.setTimeout(() => {
      state.clickTimers.delete(key);
      void activateEntry(store()?.entry?.(key));
      renderList();
    }, 240));
  }

  function handleEntryDoubleClick(event, button) {
    event.preventDefault();
    const key = String(button?.dataset.agentConversationKey || "");
    const timer = state.clickTimers.get(key);
    if (timer) window.clearTimeout(timer);
    state.clickTimers.delete(key);
    const entry = store()?.entry?.(key);
    if (entry) store()?.setReadState?.(entry, !entry.unread)?.catch?.(() => {
      window.PortalUI?.toast?.(window.PortalI18n.t("未读状态保存失败，请重试。"), "error");
    });
  }

  function clear(mode) {
    const request = store()?.clear?.(mode);
    if (!request) return;
    request.then(payload => {
      const count = Math.max(0, Number(payload?.cleared || 0));
      window.PortalUI?.toast?.(mode === "unread" ? `已清除 ${count} 条未读状态` : `已清除 ${count} 条消息`, "success");
    }).catch(error => window.PortalUI?.toast?.(error?.message || window.PortalI18n.t("清理 Mailbox 失败"), "error"));
  }

  function closeConversationMenu() {
    conversationMenu?.controller.destroy();
    conversationMenu?.panel.remove();
    conversationMenu = null;
  }

  function openConversationMenu(button, point, key = button.dataset.agentConversationKey) {
    const entry = state.snapshot.entries.find(item => item.key === key);
    if (!entry || isNotification(entry)) return;
    closeConversationMenu();
    const panel = document.createElement("div");
    panel.className = "portalAgentInboxPinMenu";
    panel.hidden = true;
    panel.setAttribute("role", "menu");
    panel.setAttribute("aria-label", window.PortalI18n.t("对话操作"));
    const action = document.createElement("button");
    action.type = "button";
    action.setAttribute("role", "menuitem");
    action.dataset.agentInboxPin = "";
    action.innerHTML = window.PortalI18n.html`<span data-portal-icon="pin" aria-hidden="true"></span><span>${esc(window.PortalI18n.t(entry.pinned ? "取消置顶" : "置顶"))}</span>`;
    const muteAction = document.createElement("button");
    muteAction.type = "button";
    muteAction.setAttribute("role", "menuitem");
    muteAction.dataset.agentInboxMute = "";
    muteAction.innerHTML = window.PortalI18n.html`<span data-portal-icon="bell-off" aria-hidden="true"></span><span>${esc(window.PortalI18n.t(entry.muted ? "关闭消息免打扰" : "消息免打扰"))}</span>`;
    muteAction.title = window.PortalI18n.t(entry.kind === "chatroom"
      ? "普通消息不计入未读提醒，@ 提及仍提醒；消息保留"
      : "不计入未读提醒，消息和未读状态仍保留");
    panel.append(action, muteAction);
    document.body.append(panel);
    const controller = window.PortalOverlay.bindPopover({
      trigger: button, panel, onOpen: () => action.focus({preventScroll: true}),
      onClose: () => updateChrome(),
    });
    conversationMenu = {panel, controller};
    action.addEventListener("click", () => {
      controller.close();
      void store().setPinned(entry.key, !entry.pinned).catch(error => {
        window.PortalUI.toast(error.message || window.PortalI18n.t("置顶失败"), "error");
      });
    }, {signal: state.listenerController.signal});
    muteAction.addEventListener("click", () => {
      controller.close();
      void store().setMuted(entry.key, !entry.muted).catch(error => {
        window.PortalUI.toast(error.message || window.PortalI18n.t("设置消息免打扰失败"), "error");
      });
    }, {signal: state.listenerController.signal});
    controller.open({point});
    updateChrome();
  }

  function cancelLongPress() {
    if (state.longPress) window.clearTimeout(state.longPress.timer);
    state.longPress = null;
  }

  function mount() {
    if (root || !document.body || document.querySelector("[data-portal-agent-inbox]") || !store()) return;
    state.listenerController = new AbortController();
    const { signal } = state.listenerController;
    root = document.createElement("aside");
    root.className = "portalAgentInbox";
    root.dataset.portalAgentInbox = "1";
    root.dataset.expanded = "false";
    root.setAttribute("aria-label", window.PortalI18n.t("快捷入口"));
    root.innerHTML = window.PortalI18n.html`<button class="portalAgentInboxTrigger" type="button" data-inbox-trigger="mailbox" data-agent-conversation-trigger aria-label="展开我的 Mailbox" aria-expanded="false" title="我的 Mailbox · 按住拖动到左侧或右侧" data-portal-tooltip="我的 Mailbox · 按住拖动到左侧或右侧" data-portal-icon="chat-room"><span class="portalAgentInboxTriggerCount" data-agent-conversation-count hidden></span></button>
      <section class="portalAgentInboxPanel" data-inbox-panel="mailbox" aria-label="我的 Mailbox" inert><header><strong>Mailbox</strong><span class="portalAgentInboxHeaderCount" data-agent-conversation-count hidden></span><span class="portalAgentInboxHeaderSummary" data-agent-conversation-action-summary>0 条</span><button class="portalAgentInboxHeaderAction" type="button" data-agent-conversation-clear-unread title="保留消息，只清除未读状态">清除未读</button><button class="portalAgentInboxHeaderAction" type="button" data-agent-conversation-clear-all title="清除当前列表；有新活动时会重新出现">清除全部</button><button type="button" data-agent-conversation-refresh aria-label="刷新 Mailbox" title="刷新" data-portal-icon="refresh"></button></header>
      <div class="portalAgentInboxError" data-agent-conversation-error role="status" hidden></div>
      <div class="portalAgentInboxList" data-agent-conversation-list><div class="portalAgentInboxEmpty">正在读取 Mailbox…</div></div><section class="portalMailboxChatroom" aria-label="Mailbox 聊天室"><header><button type="button" class="portalButton is-quiet" data-mailbox-chatroom-back aria-label="返回 Mailbox"><span data-portal-icon="arrow-left" aria-hidden="true"></span>Mailbox</button><button type="button" class="portalButton is-icon is-quiet" data-mailbox-chatroom-close data-portal-icon="close" aria-label="关闭 Mailbox"></button></header><div data-mailbox-chatroom-view hidden></div></section></section>
      <button class="portalAgentInboxTrigger" type="button" data-inbox-trigger="todo" data-agent-todo-trigger aria-label="展开 To Do" aria-expanded="false" title="To Do · 按住拖动到左侧或右侧" data-portal-tooltip="To Do · 按住拖动到左侧或右侧" data-portal-icon="check"><span class="portalAgentInboxTriggerCount" data-agent-todo-count hidden></span></button>
      <section class="portalAgentInboxPanel" data-inbox-panel="todo" aria-label="To Do" inert><header><strong>To Do</strong><span class="portalAgentInboxHeaderCount" data-agent-todo-count hidden></span><span class="portalAgentInboxHeaderSummary" data-agent-todo-summary>0 条</span><button type="button" data-agent-todo-refresh aria-label="刷新 To Do" title="刷新" data-portal-icon="refresh"></button></header><div class="portalMailboxTodos" data-mailbox-todos></div></section>
      <button class="portalAgentInboxTrigger portalPersonalAssistantTrigger" type="button" data-inbox-trigger="assistant" data-personal-assistant-trigger aria-label="展开个人助手" aria-expanded="false" data-portal-icon="octopus" hidden><span class="portalAgentInboxUnreadDot" data-personal-assistant-unread aria-label="未读" hidden></span></button>
      <section class="portalAgentInboxPanel portalPersonalAssistantPanel" data-inbox-panel="assistant" aria-label="个人助手" inert><div data-personal-assistant-chat></div></section>
      <button class="portalAgentInboxMobileClose" type="button" data-agent-inbox-mobile-close data-portal-icon="close" aria-label="关闭并返回当前页面" hidden><span>关闭</span></button>`;
    document.body.appendChild(root);
    todoPanel = window.PortalMailboxTodos.create(root.querySelector("[data-mailbox-todos]"), { onChange: () => updateChrome() });
    document.body.classList.add("portalAgentInboxMounted");
    window.PortalUIPrimitives?.mountIcons?.(root);
    state.verticalRatio = readVerticalRatio();
    const savedPosition = window.PortalState?.read?.(POSITION_PAGE, "device", {}) || {};
    state.dockSide = savedPosition.dockSide === "left" ? "left" : "right";
    root.dataset.dockSide = state.dockSide;
    // Legacy section preferences no longer redirect Mailbox to To Do.
    showAssistant(cachedAssistant());
    applyVerticalPosition();
    watchAvoidanceLayout(signal);
    root.querySelectorAll("[data-inbox-trigger]").forEach(trigger => bindTriggerDragging(trigger, signal));
    const assistantPanel = root.querySelector(".portalPersonalAssistantPanel");
    assistantPanel.addEventListener("transitionend", event => {
      if (event.target === assistantPanel && event.propertyName === "opacity") assistantChat?.setPresented(!assistantPanel.inert);
    }, { signal });
    void refreshAssistant();
    window.addEventListener("focus", refreshAssistant, { signal });
    window.addEventListener("portal:change", event => {
      if ((event.detail?.changed?.lineage || []).includes(state.assistant?.lineage_id)) void refreshAssistant();
    }, { signal });
    const loadMore = document.createElement("button");
    loadMore.type = "button";
    loadMore.className = "portalAgentInboxHeaderAction";
    loadMore.dataset.agentConversationLoadMore = "";
    loadMore.hidden = true;
    root.querySelector(".portalAgentInboxPanel").append(loadMore);
    state.unsubscribe = store().subscribe(snapshot => {
      state.snapshot = snapshot;
      renderList();
    });
    root.addEventListener("pointerdown", event => {
      if (event.isPrimary && event.button === 0 && event.target.closest("[data-agent-conversation-key]")) {
        state.pressedPointerId = event.pointerId;
        if (event.pointerType === "touch") {
          const button = event.target.closest("[data-agent-conversation-key]");
          cancelLongPress();
          const gesture = {x: event.clientX, y: event.clientY, timer: 0};
          gesture.timer = window.setTimeout(() => {
            state.suppressEntryClickUntil = performance.now() + 800;
            openConversationMenu(button, {x: gesture.x, y: gesture.y});
          }, 550);
          state.longPress = gesture;
        }
      }
    }, { signal });
    root.addEventListener("pointermove", event => {
      const gesture = state.longPress;
      if (gesture && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 8) cancelLongPress();
    }, {signal});
    window.addEventListener("pointerup", cancelLongPress, {signal});
    window.addEventListener("pointercancel", cancelLongPress, {signal});
    root.addEventListener("contextmenu", event => {
      const button = event.target.closest("[data-agent-conversation-key]");
      if (!button || isNotification(store()?.entry?.(button.dataset.agentConversationKey))) return;
      event.preventDefault();
      openConversationMenu(button, {x: event.clientX, y: event.clientY});
    }, {signal});
    root.addEventListener("keydown", event => {
      if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
        const button = event.target.closest("[data-agent-conversation-key]");
        if (button) { event.preventDefault(); openConversationMenu(button); }
        return;
      }
      if (event.key === "Escape") {
        state.touchExpanded = false;
        state.hovered = false;
        state.focused = false;
        if (root.contains(document.activeElement)) document.activeElement.blur();
        updateChrome();
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      state.focused = true;
      updateChrome();
      if (["Enter", " "].includes(event.key) && event.target.closest("[data-agent-conversation-key]")) {
        state.pressedKey = event.key;
      }
    }, { signal });
    window.addEventListener("pointerup", releaseListInteraction, { capture: true, signal });
    window.addEventListener("pointercancel", releaseListInteraction, { capture: true, signal });
    window.addEventListener("keyup", releaseListInteraction, { capture: true, signal });
    window.addEventListener("blur", releaseListInteraction, { signal });
    document.addEventListener("pointerdown", event => {
      // Pointer focus must not keep a hover panel open after the pointer leaves.
      state.focused = Boolean(event.target.closest(".portalPersonalAssistantPanel"));
      if (!root.contains(event.target) && !mailboxChat) state.touchExpanded = false;
      updateChrome();
    }, { capture: true, signal });
    root.addEventListener("pointerenter", event => {
      if (event.pointerType === "touch" || state.dragging || performance.now() < state.suppressHoverUntil) return;
      state.hovered = true;
      updateChrome();
    }, { signal });
    root.addEventListener("pointerover", event => {
      if (event.pointerType === "touch" || state.dragging || performance.now() < state.suppressHoverUntil) return;
      const trigger = event.target.closest(".portalAgentInboxTrigger");
      if (!trigger) return;
      state.activePanel = trigger.dataset.inboxTrigger;
      state.hovered = true;
      closeConversationMenu();
      updateChrome();
    }, { signal });
    root.addEventListener("pointerleave", () => { state.hovered = false; updateChrome(); }, { signal });
    root.addEventListener("focusin", event => {
      state.focused = keepsPanelFocused(event.target);
      if (state.focused) {
        const owner = event.target.closest("[data-inbox-trigger], [data-inbox-panel]");
        if (owner) state.activePanel = owner.dataset.inboxTrigger || owner.dataset.inboxPanel;
      }
      updateChrome();
    }, { signal });
    root.addEventListener("focusout", () => {
      window.clearTimeout(state.focusTimer);
      state.focusTimer = window.setTimeout(() => {
        state.focusTimer = 0;
        state.focused = keepsPanelFocused(document.activeElement);
        updateChrome();
      }, 0);
    }, { signal });
    root.addEventListener("click", event => {
      if (event.target.closest("[data-mailbox-chatroom-back]")) {
        returnToMailbox();
        return;
      }
      const mobileClose = event.target.closest("[data-agent-inbox-mobile-close], [data-mailbox-chatroom-close]");
      if (mobileClose) {
        state.touchExpanded = false;
        state.hovered = false;
        state.focused = false;
        closeConversationMenu();
        mobileClose.blur();
        updateChrome();
        return;
      }
      const trigger = event.target.closest(".portalAgentInboxTrigger");
      if (trigger) {
        if (performance.now() < state.suppressTriggerClickUntil) {
          state.suppressTriggerClickUntil = 0;
          event.preventDefault();
          return;
        }
        const panel = trigger.dataset.inboxTrigger;
        // WebKit can dispatch the synthetic click after a touch as a MouseEvent
        // without pointerType. Mobile/coarse capability therefore owns this
        // toggle instead of relying on the click event's optional field.
        if (usesTouchExpansion(event)) state.touchExpanded = state.activePanel !== panel || !state.touchExpanded;
        state.activePanel = panel;
        updateChrome();
        return;
      }
      if (event.target.closest("[data-agent-todo-refresh]")) return void todoPanel?.refresh();
      if (event.target.closest("[data-agent-conversation-refresh]")) {
        return void store().refresh({ force: true, foreground: true }).catch(() => {});
      }
      if (event.target.closest("[data-agent-conversation-load-more]")) {
        return void store().loadMore().catch(() => {});
      }
      if (event.target.closest("[data-agent-conversation-clear-unread]")) return clear("unread");
      if (event.target.closest("[data-agent-conversation-clear-all]")) return clear("all");
      const item = event.target.closest("[data-agent-conversation-key]");
      if (item) handleEntryClick(event, item);
    }, { signal });
    root.addEventListener("dblclick", event => {
      const item = event.target.closest("[data-agent-conversation-key]");
      if (item) handleEntryDoubleClick(event, item);
    }, { signal });
    window.addEventListener("portal:identity-changed", () => {
      returnToMailbox({ focus: false });
      state.touchExpanded = state.hovered = state.focused = false;
      updateChrome();
    }, { signal });
    window.addEventListener("resize", applyVerticalPosition, { passive: true, signal });
    state.relativeTimer = window.setInterval(updateRelativeTimes, 60_000);
  }

  function destroy() {
    // A remounted Inbox owns a new target read. The old read may still settle,
    // but cannot block the new one or clear its in-flight deduplication.
    assistantRefresh = null;
    returnToMailbox({ focus: false });
    assistantChat?.destroy();
    assistantChat = null;
    assistantMount = null;
    assistantInteractions.clear();
    state.assistant = null;
    state.activePanel = "mailbox";
    todoPanel?.destroy();
    todoPanel = null;
    store()?.setInboxOpen?.(false);
    closeConversationMenu();
    cancelLongPress();
    if (notificationDetail) {
      notificationDetail.controller.close({ force: true });
      notificationDetail.controller.destroy();
      notificationDetail.overlay.remove();
      notificationDetail = null;
    }
    unregisterAvoidedElements?.();
    layoutObserver?.disconnect();
    unregisterAvoidedElements = null;
    layoutObserver = null;
    window.cancelAnimationFrame(positionFrame);
    positionFrame = 0;
    document.removeEventListener("DOMContentLoaded", mount);
    state.listenerController?.abort();
    state.listenerController = null;
    state.unsubscribe?.();
    state.unsubscribe = null;
    window.clearInterval(state.relativeTimer);
    state.relativeTimer = 0;
    window.clearTimeout(state.focusTimer);
    state.focusTimer = 0;
    state.clickTimers.forEach(timer => window.clearTimeout(timer));
    state.clickTimers.clear();
    window.clearTimeout(state.listRenderTimer);
    state.listRenderTimer = 0;
    state.pressedPointerId = null;
    state.pressedKey = null;
    state.renderedEntries.clear();
    state.navigationKeys.clear();
    state.dragging = false;
    state.touchExpanded = false;
    state.hovered = false;
    state.focused = false;
    root?.remove();
    root = null;
    for (const key of ["left","right","width","height","tail"]) document.documentElement.style.removeProperty("--portal-floating-" + key);
    document.body?.classList.remove("portalAgentInboxMounted");
    document.body?.classList.remove("portalAgentInboxOverlayOpen");
  }

  window.PortalAgentInbox = Object.freeze({
    mount,
    destroy,
    refresh: options => store()?.refresh?.({ force: true, foreground: true, ...(options || {}) }),
    markRead: key => store()?.markRead?.(key),
    entries: () => store()?.entries?.() || [],
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
}());
