(() => {
  if (window.PortalChatWorkspaceMessageViewport) return;

  // One Workspace's message viewport: which scrolls are the reader's (the wheel,
  // scroll keys, a scrollbar drag, a touch), following the tail, and the reading
  // place kept while Workbench Panels lay out. It listens on the message list and
  // the Workspace root from its creation; destroyMessageViewport stops that and
  // drops a pending restore.
  const DEPENDENCIES = Object.freeze([
    "els", "messageInstanceSelector", "messageNodeKey", "messageViewportAtTail", "root", "state",
  ]);

  function createMessageViewport(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageViewport is missing: ${missing.join(", ")}`);
    const {
      els, messageInstanceSelector, messageNodeKey, messageViewportAtTail, root, state,
    } = dependencies;

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
    // Another conversation starts with no scroll gesture in progress.
    const resetMessageScrollGesture = () => {
      messageScrollGesturePending = false;
      messageScrollPointerActive = false;
      messageScrollTouchActive = false;
    };
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
      if (!["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) return;
      expectMessageScrollFromGesture();
    };
    const handleMessageViewportScroll = () => {
      if (!messageViewportMeasurable()) return;
      if (!messageScrollGesturePending && !messageScrollPointerActive && !messageScrollTouchActive) return;
      // A user scroll supersedes the viewport captured before a panel layout.
      // Otherwise its delayed restoration can snap a new reading position back.
      dropPendingWorkbenchViewport();
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
    // Forgets the viewport captured before a Workbench layout, with its restores.
    const dropPendingWorkbenchViewport = () => {
      window.cancelAnimationFrame(workbenchViewportRestoreFrame);
      window.cancelAnimationFrame(workbenchViewportSettleFrame);
      workbenchViewportRestoreFrame = 0;
      workbenchViewportSettleFrame = 0;
      if (!pendingWorkbenchViewport) return;
      els.messages.style.overflowAnchor = pendingWorkbenchViewport.overflowAnchorStyle;
      pendingWorkbenchViewport = null;
    };
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
      };
    };
    const handleBeforeWorkbenchLayout = () => {
      // Several synchronous Workbench mutations may precede one painted layout.
      // Keep the first stable viewport, and the message it anchors, instead of
      // recapturing an already-clamped one, unless a user action or
      // history-scope change has since changed the authoritative tail-following
      // intent.
      const previous = pendingWorkbenchViewport;
      if (previous?.followTail === state.messageTailFollowIntent) return;
      pendingWorkbenchViewport = {
        ...(captureWorkbenchViewport() || {
          followTail: state.messageTailFollowIntent,
          scrollTop: previous ? previous.scrollTop : 0,
          anchor: null,
          anchorKey: "",
          anchorOffset: 0,
        }),
        // Anchoring is off from the first capture on; the restore gives back
        // the style from before it.
        overflowAnchorStyle: previous ? previous.overflowAnchorStyle : els.messages.style.overflowAnchor,
      };
      els.messages.style.overflowAnchor = "none";
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
    // After the window resizes, a reader following the tail stays at it.
    const followTailAfterResize = () => {
      window.cancelAnimationFrame(windowResizeFollowFrame);
      if (!state.messageTailFollowIntent) return;
      windowResizeFollowFrame = window.requestAnimationFrame(() => {
        windowResizeFollowFrame = 0;
        if (state.messageTailFollowIntent && messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      });
    };
    const destroyMessageViewport = () => {
      els.messages?.removeEventListener("scroll", handleMessageViewportScroll);
      els.messages?.removeEventListener("wheel", expectMessageScrollFromGesture);
      els.messages?.removeEventListener("touchstart", beginMessageScrollTouchGesture);
      els.messages?.removeEventListener("touchmove", beginMessageScrollTouchGesture);
      els.messages?.removeEventListener("touchend", endMessageScrollTouchGesture);
      els.messages?.removeEventListener("touchcancel", endMessageScrollTouchGesture);
      els.messages?.removeEventListener("pointerdown", handleMessageScrollPointerDown);
      els.messages?.removeEventListener("keydown", handleMessageScrollKeyDown);
      window.removeEventListener("pointerup", endMessageScrollPointerGesture);
      window.removeEventListener("pointercancel", endMessageScrollPointerGesture);
      root.removeEventListener("portal:workbench-before-layout", handleBeforeWorkbenchLayout);
      root.removeEventListener("portal:workbench-resize", handleWorkbenchResize);
      window.cancelAnimationFrame(windowResizeFollowFrame);
      dropPendingWorkbenchViewport();
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

    return Object.freeze({
      messageViewportMeasurable, syncMessageScrollMode, resetMessageScrollGesture,
      followTailAfterResize, destroyMessageViewport,
    });
  }

  window.PortalChatWorkspaceMessageViewport = Object.freeze({
    createMessageViewport, dependencies: DEPENDENCIES,
  });
})();
