(() => {
  if (window.PortalChatWorkspaceComposerMenus) return;

  // The two menus a Workspace's composer opens from what is typed: slash
  // commands after "/" and Resident mentions after "@". Each Workspace creates
  // its own menus; their items and active row live in the Workspace state, the
  // mention search's timer, request and abort here.
  const DEPENDENCIES = Object.freeze([
    "attachmentPreviewOptions", "bindMessageAvatars", "composerMentionToken",
    "currentController", "els", "insertPlainText", "isComposerImeActive", "mentionMenuHtml",
    "normalizeSlashCommands", "options", "saveActiveDraft", "setComposerDraftInput",
    "slashCommandMatches", "slashCommandMenuHtml", "state", "updateControls",
  ]);

  function createComposerMenus(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposerMenus is missing: ${missing.join(", ")}`);
    const {
      attachmentPreviewOptions, bindMessageAvatars, composerMentionToken, currentController, els,
      insertPlainText, isComposerImeActive, mentionMenuHtml, normalizeSlashCommands, options,
      saveActiveDraft, setComposerDraftInput, slashCommandMatches, slashCommandMenuHtml, state,
      updateControls,
    } = dependencies;

    const slashCommandConfig = () => {
      const configured = state.composer.slash_commands || options.slash_commands;
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
      options.onInput?.(currentController().payload(), currentController());
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
    // Closing schedules one more render; a destroyed workspace drops it.
    const destroyMentionMenu = () => {
      closeMentionMenu();
      window.cancelAnimationFrame(mentionRenderFrame);
      mentionRenderFrame = 0;
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
        Promise.resolve().then(() => options.mentionCandidates(token.query, currentController(), {
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

    return Object.freeze({
      refreshSlashCommandMenu, closeSlashCommandMenu, chooseSlashCommand, handleSlashCommandKey,
      scheduleMentionRender, closeMentionMenu, destroyMentionMenu, refreshMentionMenu,
      chooseMention, handleMentionKey,
    });
  }

  window.PortalChatWorkspaceComposerMenus = Object.freeze({
    createComposerMenus, dependencies: DEPENDENCIES,
  });
})();
