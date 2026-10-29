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
      if (pin) pin.textContent = window.PortalI18n.t(currentController().isMessagePinned(menu.dataset.portalMessageKey) ? "取消置顶" : "置顶这轮对话");
      if (openMessageMenu !== menu) closeMessageMenu();
      bindMessageSubmenus(menu);
      closeMessageSubmenus(menu);
      messageMenuPopover(menu).open({ point });
    };
    const closeMessageMenu = () => {
      if (openMessageMenu) messageMenuPopovers.get(openMessageMenu).close();
    };

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
    const askHandoffResident = button => {
      const card = residentHandoffCards(button.dataset.handoffCard)[0];
      if (!card?.ask_message || button.disabled || !options.onSend || state.composer.can_send === false) return;
      button.disabled = true;
      Promise.resolve(options.onSend({ text: `替我问一下 ${card.title}（${card.resident_id}）：${card.ask_message}`, refs: [], attachments: [] }, currentController(),
        { clear: false, focus: false, quote: false, includeContext: false, clearRefsOnSuccess: false }))
        .catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"))
        .finally(() => { button.disabled = false; });
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
      if (action === "pin" && options.pinMessages) {
        currentController().toggleMessagePin(key);
        return;
      }
      if (action === "resident-handoff-ask") {
        askHandoffResident(button);
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
    // A web link under the pointer adds "open link" to the menu.
    const offerMessageLink = (menu, article, target) => {
      menu.querySelector("[data-portal-message-open-link]")?.remove();
      const link = target.closest("a[href]");
      if (!link || !article.contains(link) || !["http:", "https:"].includes(link.protocol)) return;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.portalMessageOpenLink = link.href;
      button.textContent = window.PortalI18n.t("打开链接");
      button.title = window.PortalI18n.t("在新标签页打开这个网页链接");
      menu.insertBefore(button, menu.querySelector("[data-portal-message-copy]"));
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
      els.messages?.removeEventListener("click", handleMessagesClick);
      els.messages?.removeEventListener("contextmenu", handleMessagesContextMenu);
      closeMessageMenu();
    };

    return { destroyMessageActions };
  }

  window.PortalChatWorkspaceMessageActions = Object.freeze({ createMessageActions, dependencies: DEPENDENCIES });
})();
