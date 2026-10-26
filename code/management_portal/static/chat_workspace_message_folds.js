(() => {
  if (window.PortalChatWorkspaceMessageFolds) return;

  // One Workspace's long-body folds (decision_f560803947c6): which bodies are
  // long, which messages start expanded, and the reader's fold toggle, which
  // keeps the reading place. It observes the list's width and the images that
  // load in it from its creation; destroyMessageFolds stops that.
  const DEPENDENCIES = Object.freeze([
    "MESSAGE_FOLD_MIN_LINES", "applyMessageFold", "els", "messageFoldChoices", "messageFoldDefaults",
    "messageRenderKey", "messageViewportAtTail", "messageViewportMeasurable", "state", "syncMessageScrollMode",
  ]);

  function createMessageFolds(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageFolds is missing: ${missing.join(", ")}`);
    const {
      MESSAGE_FOLD_MIN_LINES, applyMessageFold, els, messageFoldChoices, messageFoldDefaults,
      messageRenderKey, messageViewportAtTail, messageViewportMeasurable, state, syncMessageScrollMode,
    } = dependencies;

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
    // the user is reading stays open, so its text is not pulled away. Returns the shown
    // messages whose fold changes.
    const updateMessageFoldDefaults = () => {
      const previous = state.messageFoldDefaults;
      const next = messageFoldDefaults(state.messages, { runtimeLive: state.runtimeLive });
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
    // Rows already drawn take the changed defaults in place.
    const showMessageFoldDefaults = keys => {
      for (const key of keys) {
        els.messages.querySelectorAll(`[data-message-fold-toggle="${CSS.escape(key)}"]`)
          .forEach(button => applyMessageFold(button, state.messageFoldDefaults.has(key)));
      }
    };
    // A choice applies to the message wherever it is shown on the page.
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

    const destroyMessageFolds = () => {
      messageFoldObserver?.disconnect();
      els.messages?.removeEventListener("load", measureLoadedMessageImage, true);
    };

    return { measureMessageFolds, updateMessageFoldDefaults, showMessageFoldDefaults, toggleMessageFold, destroyMessageFolds };
  }

  window.PortalChatWorkspaceMessageFolds = Object.freeze({ createMessageFolds, dependencies: DEPENDENCIES });
})();
