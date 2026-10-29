(() => {
  if (window.PortalChatWorkspaceMessageHistory) return;

  // One Workspace's way back through its conversation. The load-earlier button
  // asks the host for the page before; the Fork card reads the compaction the
  // Fork opened with, once, when it is first opened; a message is revealed by
  // loading earlier pages until it is there. The message being written can
  // quote one message per conversation, shown in the quote bar, and a sent
  // message's quote block goes to the message it quotes. A failed click goes to
  // the host's onError, or else to a toast.
  const DEPENDENCIES = Object.freeze([
    "bindResourceChips", "composerQuoteHtml", "currentController", "els", "historyScope", "inheritanceCardHtml",
    "messageRenderKey", "messageQuoteExcerpt", "messageViewportAtTail", "options", "state", "syncMessageScrollMode", "toggleMessageFold",
  ]);

  function createMessageHistory(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageHistory is missing: ${missing.join(", ")}`);
    const {
      bindResourceChips, composerQuoteHtml, currentController, els, historyScope, inheritanceCardHtml,
      messageRenderKey, messageQuoteExcerpt, messageViewportAtTail, options, state, syncMessageScrollMode, toggleMessageFold,
    } = dependencies;

    let destroyed = false;
    const reportError = error => {
      if (options.onError) options.onError(error);
      else window.PortalUI?.toast?.(error?.message || String(error), "error");
    };

    // Shows a message of this conversation, loading earlier history until it is there.
    const revealMessage = async messageId => {
      const key = String(messageId || "").trim();
      if (!key || !els.messages) return false;
      const scope = historyScope();
      const find = () => els.messages.querySelector(`.messageList > [data-message-key="${CSS.escape(key)}"]`);
      let row = find();
      while (!row) {
        if (!options.onLoadOlder || !state.messageRenderOptions?.history?.can_load_older) break;
        const before = `${state.messages.length}:${messageRenderKey(state.messages[0] || {})}`;
        await options.onLoadOlder(currentController());
        await new Promise(resolve => window.requestAnimationFrame(resolve));
        if (destroyed || historyScope() !== scope) return false;
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
    };

    const loadOlder = button => {
      if (!options.onLoadOlder || button.disabled) return;
      button.disabled = true;
      button.textContent = window.PortalI18n.t("正在加载…");
      Promise.resolve(options.onLoadOlder(currentController())).catch(reportError).finally(() => {
        button.disabled = false;
        button.textContent = window.PortalI18n.t("加载更早消息");
      });
    };

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
        const inheritance = await options.onLoadInheritedContext(currentController());
        const compaction = inheritance?.opening_compaction;
        Object.assign(view, compaction && typeof compaction === "object"
          ? { status: "ready", compaction }
          : { status: "error", error: window.PortalI18n.t("这次 Fork 已没有可读取的压缩。") });
      } catch (error) {
        Object.assign(view, { status: "error", error: error?.message || String(error) });
      }
      // An answer for another conversation's card leaves the card shown, and its focus, alone.
      if (inheritanceView === view) renderInheritanceCard();
    };

    // Personal navigation bookmarks belong to the viewer and this conversation,
    // independently of the server's messages and of the composition's quote.
    const pinHost = document.createElement("div");
    pinHost.className = "workspacePinnedMessages";
    pinHost.hidden = true;
    pinHost.title = window.PortalI18n.t("仅你可见，保存在当前浏览器");
    pinHost.setAttribute("aria-label", window.PortalI18n.t("置顶对话"));
    if (options.pinMessages) els.head?.after(pinHost);
    const pinKey = () => JSON.stringify(["portal.chat.pins.v1", window.PortalIdentity?.username?.() || "", historyScope()]);
    const readPins = () => {
      try {
        const pins = JSON.parse(window.localStorage.getItem(pinKey()) || "[]");
        return Array.isArray(pins) ? pins.filter(pin => pin && typeof pin.message_id === "string" && typeof pin.text === "string") : [];
      } catch (_) { return []; }
    };
    let pinSignature = "";
    const renderPinnedMessages = () => {
      if (!options.pinMessages) return;
      const pins = readPins();
      const signature = pinKey() + JSON.stringify(pins);
      if (signature === pinSignature) return;
      pinSignature = signature;
      const esc = window.PortalUIPrimitives.escapeHtml;
      pinHost.hidden = !pins.length;
      pinHost.innerHTML = pins.map(pin => window.PortalI18n.html`<div class="workspacePinnedMessage" data-pinned-id="${esc(pin.message_id)}">
        <button type="button" class="workspacePinnedJump" data-pinned-action="jump" title="跳到置顶消息"><span>置顶</span><span>${esc(pin.text)}</span></button>
        <button type="button" class="portalButton is-quiet is-compact" data-pinned-action="quote">引用回复</button>
        <button type="button" class="portalButton is-quiet is-compact" data-pinned-action="remove" aria-label="取消置顶" title="取消置顶">×</button>
      </div>`).join("");
    };
    const writePins = pins => {
      try {
        window.localStorage.setItem(pinKey(), JSON.stringify(pins));
        renderPinnedMessages();
        window.dispatchEvent(new CustomEvent("portal:chat-pins-changed"));
      } catch (error) { reportError(error); }
    };
    const toggleMessagePin = key => {
      if (!options.pinMessages || !historyScope()) return;
      const item = state.messageByKey.get(String(key));
      if (!item || item.pending || item.local_delivery) return;
      const id = String(item.message_id || item.id || "");
      const text = messageQuoteExcerpt(item);
      if (!id || !text) return;
      const pins = readPins();
      writePins(pins.some(pin => pin.message_id === id) ? pins.filter(pin => pin.message_id !== id)
        : [...pins, { message_id: id, author_id: String(item.author_id || item.author || ""), text }]);
    };
    const isMessagePinned = key => {
      const item = state.messageByKey.get(String(key));
      return !!item && readPins().some(pin => pin.message_id === String(item.message_id || item.id || ""));
    };
    const onPinClick = event => {
      const button = event.target.closest("[data-pinned-action]");
      const id = button?.closest("[data-pinned-id]")?.dataset.pinnedId;
      const pin = readPins().find(pin => pin.message_id === id);
      if (!pin) return;
      if (button.dataset.pinnedAction === "jump") revealMessage(id).catch(reportError);
      if (button.dataset.pinnedAction === "quote") setQuote(pin);
      if (button.dataset.pinnedAction === "remove") writePins(readPins().filter(pin => pin.message_id !== id));
    };
    pinHost.addEventListener("click", onPinClick);
    window.addEventListener("storage", renderPinnedMessages);
    window.addEventListener("portal:chat-pins-changed", renderPinnedMessages);
    window.addEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", renderPinnedMessages);

    // The message the composition quotes, per conversation; not saved with the draft.
    const composerQuotes = new Map();
    const composerQuoteScope = () => historyScope() || state.activeDraftScope || "";
    const composerQuote = () => composerQuotes.get(composerQuoteScope()) || null;
    // The quote bar shows the quote of the conversation shown.
    const renderComposerQuote = () => {
      renderPinnedMessages();
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
    const setQuote = quote => {
      const scope = composerQuoteScope();
      const messageId = String(quote?.message_id || "").trim();
      const text = String(quote?.text || "").trim();
      if (!scope || !messageId || !text) return false;
      composerQuotes.set(scope, { message_id: messageId, author_id: String(quote.author_id || ""), text });
      renderComposerQuote();
      els.input?.focus?.();
      return true;
    };
    // Drops a conversation's quote; given a message ID, only while it still quotes that message.
    const clearQuote = ({ scope = composerQuoteScope(), messageId = "" } = {}) => {
      const current = composerQuotes.get(scope);
      if (!current || (messageId && current.message_id !== messageId)) return false;
      composerQuotes.delete(scope);
      renderComposerQuote();
      return true;
    };

    const handleMessagesClick = event => {
      if (event.target.closest("[data-chat-inheritance-summary]")) {
        event.preventDefault();
        toggleInheritedSummary().catch(reportError);
        return;
      }
      const quote = event.target.closest("[data-message-quote]");
      if (quote) {
        event.preventDefault();
        revealMessage(quote.dataset.messageQuote).catch(reportError);
        return;
      }
      const older = event.target.closest("[data-workspace-load-older]");
      if (older) loadOlder(older);
    };
    const handleComposerClick = event => {
      if (!event.target.closest("[data-composer-quote-cancel]")) return;
      event.preventDefault();
      clearQuote();
      els.input?.focus?.();
    };
    els.messages?.addEventListener("click", handleMessagesClick);
    els.composer?.addEventListener("click", handleComposerClick);
    // A destroyed Workspace answers none of these clicks, and a reveal under way stops.
    const destroyMessageHistory = () => {
      pinHost.remove();
      window.removeEventListener("storage", renderPinnedMessages);
      window.removeEventListener("portal:chat-pins-changed", renderPinnedMessages);
      window.removeEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", renderPinnedMessages);
      destroyed = true;
      els.messages?.removeEventListener("click", handleMessagesClick);
      els.composer?.removeEventListener("click", handleComposerClick);
    };

    return {
      revealMessage, inheritanceViewFor, composerQuote, renderComposerQuote, setQuote, clearQuote, toggleMessagePin, isMessagePinned, renderPinnedMessages, destroyMessageHistory,
    };
  }

  window.PortalChatWorkspaceMessageHistory = Object.freeze({ createMessageHistory, dependencies: DEPENDENCIES });
})();
