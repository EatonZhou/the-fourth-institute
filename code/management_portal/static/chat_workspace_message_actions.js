(() => {
  if (window.PortalChatWorkspaceMessageActions) return;

  // What a reader can do to one Workspace's messages: the menu a right-click
  // opens (the gap after a message belongs to it), copying a message or its run
  // of replies, opening a link, host actions, asking another Resident from a
  // handoff card, and folding runs of replies and Family work. It listens on the
  // message list from its creation; destroyMessageActions stops that and closes
  // an open menu.
  const DEPENDENCIES = Object.freeze([
    "bindMessageSubmenus", "closeMessageSubmenus", "consecutiveAgentGroups", "currentController", "els",
    "messageCopyText", "messageRenderKey", "messageRole", "options", "redrawMessages", "residentHandoffCards",
    "state", "toggleMessageFold",
  ]);

  function createMessageActions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageActions is missing: ${missing.join(", ")}`);
    const {
      bindMessageSubmenus, closeMessageSubmenus, consecutiveAgentGroups, currentController, els,
      messageCopyText, messageRenderKey, messageRole, options, redrawMessages, residentHandoffCards,
      state, toggleMessageFold,
    } = dependencies;

    // One menu is open at a time. It opens out in the page, so its clicks are
    // passed on to the list's handler.
    const messageMenuPopovers = new WeakMap();
    let openMessageMenu = null;
    const messageMenuPopover = menu => {
      const existing = messageMenuPopovers.get(menu);
      if (existing) return existing;
      const messageItem = menu.closest(".messageItem");
      menu.addEventListener("click", event => {
        if (menu.parentNode !== document.body) return;
        event.stopPropagation();
        handleMessagesClick(event);
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
          messageItem.classList.add("menuOpen");
          options.onInteractionChange?.(true, menu);
        },
        onClose: () => {
          if (openMessageMenu === menu) openMessageMenu = null;
          closeMessageSubmenus(menu);
          messageItem.classList.remove("menuOpen");
          options.onInteractionChange?.(false, menu);
        },
      });
      messageMenuPopovers.set(menu, popover);
      return popover;
    };
    const openMessageMenuAt = (menu, point) => {
      const pin = options.pinMessages && menu.querySelector('[data-portal-message-action="pin"]');
      if (pin) pin.textContent = window.PortalI18n.t(currentController().isMessagePinned(menu.dataset.portalMessageKey) ? "取消置顶" : "置顶消息");
      syncRoundAction(menu);
      if (openMessageMenu !== menu) closeMessageMenu();
      bindMessageSubmenus(menu);
      closeMessageSubmenus(menu);
      messageMenuPopover(menu).open({ point });
    };
    const closeMessageMenu = () => {
      if (openMessageMenu) messageMenuPopovers.get(openMessageMenu).close();
    };
    const syncRoundAction = menu => {
      if (!menu) return;
      let action = menu.querySelector('[data-portal-message-action="round-follow"]');
      const controller = currentController(), key = menu.dataset.portalMessageKey;
      if (!controller.roundNavigationTarget?.(key)) { action?.remove(); return; }
      if (!action) {
        action = document.createElement('button'); action.type = 'button';
        action.className = 'messageMenuItem'; action.dataset.portalMessageAction = 'round-follow'; menu.append(action);
      }
      action.textContent = window.PortalI18n.t(controller.isRoundFollowed(key) ? '取消Flag' : 'Flag');
      action.setAttribute('aria-pressed', String(controller.isRoundFollowed(key)));
    };
    const syncOpenRoundAction = () => syncRoundAction(openMessageMenu);
    window.addEventListener('portal:chat-round-flags-changed', syncOpenRoundAction);
    window.addEventListener('storage', syncOpenRoundAction);

    const toggleWorkGroup = toggle => {
      const groupId = toggle.dataset.portalWorkGroupToggle;
      if (state.collapsedWorkGroups.has(groupId)) state.collapsedWorkGroups.delete(groupId);
      else state.collapsedWorkGroups.add(groupId);
      redrawMessages();
      els.messages.querySelector(`[data-portal-work-group-toggle="${CSS.escape(groupId)}"]`)?.focus({ preventScroll: true });
    };
    // A host that folds runs by default records the runs the reader unfolded.
    const toggleAgentGroup = toggle => {
      const groupId = String(toggle.dataset.portalAgentGroupToggle || "").trim();
      if (!groupId) return;
      const choices = state.messageRenderOptions?.autoCollapseAgentGroups === true
        ? state.expandedAgentGroups
        : state.collapsedAgentGroups;
      if (choices.has(groupId)) choices.delete(groupId);
      else choices.add(groupId);
      redrawMessages();
    };
    const openMessageLink = button => {
      closeMessageMenu();
      window.open(button.dataset.portalMessageOpenLink, "_blank", "noopener,noreferrer");
    };
    // This explicit action owns its payload; an unfinished draft remains in the composer.
    const askHandoffResident = (button, item) => {
      const card = residentHandoffCards(button.dataset.handoffCard)[0];
      if (!card?.ask_message || button.disabled || !options.onSend || state.composer.can_send === false) return;
      const source = String(item?.message_id || item?.id || "");
      const index = Number(button.dataset.handoffIndex);
      if (!source || !Number.isInteger(index)) return;
      const key = `${source}:${index}`;
      const deliveries = window.PortalChatWorkspaceMessageContent.handoffDeliveries;
      if (["sending", "accepted"].includes(deliveries.get(key))) return;
      const selection = { type: "resident_handoff_selection", source_message_id: source, card_index: index,
        resident_id: card.resident_id, title: card.title, reason: card.reason.trim() };
      const messageIdentity = button.dataset.handoffMessageId || undefined;
      const target = state.contract?.target || {};
      button.disabled = true;
      deliveries.set(key, "sending");
      redrawMessages();
      Promise.resolve().then(() => options.onSend({ text: "替我问一下", refs: [], attachments: [],
        content_parts: [{ type: "text", text: "替我问一下" }, selection] }, currentController(),
        { clear: false, focus: false, quote: { message_id: source }, messageIdentity,
          idempotencyKey: `resident-handoff:${source}:${index}`, includeContext: false, clearRefsOnSuccess: false, rememberHistory: false }))
        .then(result => {
          if (!result) throw new Error("推荐选择尚未发送，请重试");
          deliveries.set(key, "accepted");
        })
        .catch(error => {
          deliveries.set(key, "failed");
          if (target.kind === "resident" && messageIdentity) window.PortalResidentProtocol.dismissSubmission(target.id, messageIdentity);
          if (options.onError) options.onError(error);
          else window.PortalUI.toast(error.message || String(error), "error");
        })
        .finally(() => redrawMessages());
    };
    // A menu item belongs to the menu's message; any other action to the row it
    // sits in, or to the message that row follows.
    const runMessageAction = button => {
      const menu = button.closest("[data-portal-message-menu]");
      const article = button.closest("[data-message-key]");
      const key = String(menu?.dataset.portalMessageKey || article?.dataset.messageAfterOf || article?.dataset.messageKey || "");
      const item = state.messageByKey.get(key);
      const action = String(button.dataset.portalMessageAction || "");
      if (menu) closeMessageMenu();
      if (action === 'round-follow') { currentController().toggleRoundFollow(key); return; }
      if (action === "pin" && options.pinMessages) {
        currentController().toggleMessagePin(key);
        return;
      }
      if (action === "resident-handoff-ask") {
        askHandoffResident(button, item);
        return;
      }
      const handler = state.messageRenderOptions?.onMessageAction || options.onMessageAction;
      if (handler && item && action) {
        Promise.resolve(handler({ action, key, item, button, workspace: currentController() }))
          .catch(error => options.onError?.(error));
      }
    };
    // The first reply of a run can copy the whole run.
    const copyMessages = button => {
      const menu = button.closest("[data-portal-message-menu]");
      const key = String(menu?.dataset.portalMessageKey || "");
      const item = state.messageByKey.get(key);
      if (!item) return;
      let items = [item];
      if (button.dataset.portalMessageCopyScope === "replies") {
        if (messageRole(item) !== "agent") return;
        const index = state.messages.findIndex(message => messageRenderKey(message) === key);
        const group = consecutiveAgentGroups(state.messages).get(index);
        if (group) items = state.messages.slice(group.start, group.end + 1);
      }
      const format = button.dataset.portalMessageCopy === "markdown" ? "markdown" : "text";
      const valueResolver = state.messageRenderOptions?.messageCopyValue || options.messageCopyValue;
      Promise.all(items.map(item => {
        const key = messageRenderKey(item);
        const article = els.messages.querySelector(`[data-message-key="${CSS.escape(key)}"]`);
        const raw = String(item?.content || "");
        return valueResolver
          ? valueResolver({ format, item, key, article, workspace: currentController() })
          : (format === "markdown" ? raw : messageCopyText(article, raw));
      }))
        .then(values => window.PortalUI.copyText(values.map(value => String(value ?? "")).join("\n\n"), window.PortalI18n.t("消息")))
        .catch(error => options.onError?.(error));
      if (menu) closeMessageMenu();
    };

    const handleMessagesClick = event => {
      const toggles = [
        ["[data-portal-work-group-toggle]", toggleWorkGroup],
        ["[data-message-fold-toggle]", toggleMessageFold],
        ["[data-portal-agent-group-toggle]", toggleAgentGroup],
      ];
      for (const [selector, toggle] of toggles) {
        const button = event.target.closest(selector);
        if (!button) continue;
        event.preventDefault();
        event.stopPropagation();
        toggle(button);
        return;
      }
      const openLinkButton = event.target.closest("[data-portal-message-open-link]");
      if (openLinkButton) {
        event.preventDefault();
        openMessageLink(openLinkButton);
        return;
      }
      const copyLinkButton = event.target.closest("[data-portal-message-copy-link]");
      if (copyLinkButton) {
        event.preventDefault();
        closeMessageMenu();
        void window.PortalUI.copyText(copyLinkButton.dataset.portalMessageCopyLink, window.PortalI18n.t("链接"));
        return;
      }
      const actionButton = event.target.closest("[data-portal-message-action]");
      if (actionButton) {
        runMessageAction(actionButton);
        return;
      }
      const copyButton = event.target.closest("[data-portal-message-copy]");
      if (copyButton) copyMessages(copyButton);
    };

    // The flex gap after a message belongs to that message, up to the next
    // visible message. Side gutters, history controls, runtime cards and the
    // space before or after the conversation belong to none.
    const messageAtGap = (list, event) => {
      let previous = null;
      for (const row of list.children) {
        if (!row.getClientRects().length) continue;
        const bounds = row.getBoundingClientRect();
        if (bounds.top > event.clientY) {
          if (previous?.matches(".messageItem[data-message-key]")
            && row.matches(".messageItem[data-message-key]")
            && state.messageByKey.has(row.dataset.messageKey)) {
            const before = previous.getBoundingClientRect();
            if (event.clientY >= before.bottom && event.clientX >= before.left && event.clientX <= before.right) return previous;
          }
          return null;
        }
        previous = row;
      }
      return null;
    };
    // A web link under the pointer adds "open link" and "copy link" to the menu.
    const offerMessageLink = (menu, article, target) => {
      menu.querySelectorAll("[data-portal-message-open-link], [data-portal-message-copy-link]").forEach(node => node.remove());
      const link = target.closest("a[href]");
      if (!link || !article.contains(link) || !["http:", "https:"].includes(link.protocol)) return;
      const open = document.createElement("button");
      open.type = "button";
      open.dataset.portalMessageOpenLink = link.href;
      open.textContent = window.PortalI18n.t("打开链接");
      open.title = window.PortalI18n.t("在新标签页打开这个网页链接");
      const copy = document.createElement("button");
      copy.type = "button";
      copy.dataset.portalMessageCopyLink = link.href;
      copy.textContent = window.PortalI18n.t("复制链接");
      copy.title = link.href;
      const anchor = menu.querySelector("[data-portal-message-copy]");
      menu.insertBefore(open, anchor);
      menu.insertBefore(copy, anchor);
    };
    // The reading pane owns its context menu, including blank space and status
    // blocks. Only an actual message may supply message actions.
    const handleMessagesContextMenu = event => {
      if (event.target.closest("input, textarea, select") || event.target.isContentEditable) return;
      event.preventDefault();
      event.stopPropagation();
      let article = event.target.closest(".messageItem[data-message-key]");
      if (!article && event.target.matches(".messageList") && event.target.parentElement === els.messages) {
        article = messageAtGap(event.target, event);
      }
      const key = article && els.messages.contains(article) ? article.dataset.messageKey : "";
      const menu = key && state.messageByKey.has(key)
        ? article.querySelector("[data-portal-message-menu]")
          || (openMessageMenu?.dataset.portalMessageKey === key ? openMessageMenu : null)
        : null;
      if (!menu) {
        closeMessageMenu();
        return;
      }
      offerMessageLink(menu, article, event.target);
      openMessageMenuAt(menu, { x: event.clientX, y: event.clientY });
    };

    els.messages?.addEventListener("click", handleMessagesClick);
    els.messages?.addEventListener("contextmenu", handleMessagesContextMenu);
    const destroyMessageActions = () => {
      window.removeEventListener('portal:chat-round-flags-changed', syncOpenRoundAction);
      window.removeEventListener('storage', syncOpenRoundAction);
      els.messages?.removeEventListener("click", handleMessagesClick);
      els.messages?.removeEventListener("contextmenu", handleMessagesContextMenu);
      closeMessageMenu();
    };

    return { destroyMessageActions };
  }

  window.PortalChatWorkspaceMessageActions = Object.freeze({ createMessageActions, dependencies: DEPENDENCIES });
})();
