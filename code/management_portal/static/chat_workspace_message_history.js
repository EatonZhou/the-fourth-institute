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
    "messageRenderKey", "messageViewportAtTail", "options", "state", "syncMessageScrollMode", "toggleMessageFold",
  ]);

  function createMessageHistory(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageHistory is missing: ${missing.join(", ")}`);
    const {
      bindResourceChips, composerQuoteHtml, currentController, els, historyScope, inheritanceCardHtml,
      messageRenderKey, messageViewportAtTail, options, state, syncMessageScrollMode, toggleMessageFold,
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

    // The message the composition quotes, per conversation; not saved with the draft.
    const composerQuotes = new Map();
    const composerQuoteScope = () => historyScope() || state.activeDraftScope || "";
    const composerQuote = () => composerQuotes.get(composerQuoteScope()) || null;
    // The quote bar shows the quote of the conversation shown.
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
      destroyed = true;
      els.messages?.removeEventListener("click", handleMessagesClick);
      els.composer?.removeEventListener("click", handleComposerClick);
    };

    return {
      revealMessage, inheritanceViewFor, composerQuote, renderComposerQuote, setQuote, clearQuote, destroyMessageHistory,
    };
  }

  window.PortalChatWorkspaceMessageHistory = Object.freeze({ createMessageHistory, dependencies: DEPENDENCIES });
})();
