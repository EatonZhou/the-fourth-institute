(() => {
  if (window.PortalChatWorkspaceComposerUndo) return;

  // One Workspace's composer undo. Ctrl/⌘+Z takes back what the Workspace put
  // in itself: pasted text and inserted attachments, newest first, the last
  // twenty. Each change records a frame with the composition before it and,
  // once done, the composition after it; when the composition is no longer
  // that, the key is left to the browser's own undo. A send that fails puts
  // its composition back with the same snapshots.
  const DEPENDENCIES = Object.freeze([
    "composerSelectionSnapshot", "currentController", "discardComposerAttachments", "els",
    "normalizeComposerAttachments", "options", "refsPayload", "renderAttachmentTrayOnly", "renderAttachments",
    "restoreComposerSelection", "serializeComposerInput", "state", "withoutAttachmentReconcile",
  ]);
  const UNDO_LIMIT = 20;

  function createComposerUndo(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposerUndo is missing: ${missing.join(", ")}`);
    const {
      composerSelectionSnapshot, currentController, discardComposerAttachments, els,
      normalizeComposerAttachments, options, refsPayload, renderAttachmentTrayOnly, renderAttachments,
      restoreComposerSelection, serializeComposerInput, state, withoutAttachmentReconcile,
    } = dependencies;

    let undoStack = [];

    const composerSnapshot = () => ({
      inputHtml: String(els.input?.innerHTML || ""),
      selection: composerSelectionSnapshot(els.input),
      attachments: state.attachments.map(item => ({ ...item })),
    });
    // What the composition says: a change since a frame was done turns its undo off.
    const composerMutationSignature = () => JSON.stringify({
      draftText: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
      attachmentIds: state.attachments.map(item => String(item?.id || "")),
      refs: refsPayload(state.refs),
    });
    // A frame for a change about to be made. When the composition is back where
    // the newest frame began, that frame starts over as this one.
    const pushComposerUndoSnapshot = () => {
      if (!els.input) return null;
      const snapshot = composerSnapshot();
      const signature = JSON.stringify({
        inputHtml: snapshot.inputHtml,
        attachments: snapshot.attachments.map(item => [item.id, item.path, item.preview_url, item.uploading, item.error]),
      });
      if (undoStack[undoStack.length - 1]?.signature === signature) undoStack.pop();
      const frame = { ...snapshot, signature, afterSignature: "" };
      undoStack = [...undoStack, frame].slice(-UNDO_LIMIT);
      return frame;
    };
    // A change that did not happen, or that the browser made itself.
    const dropComposerUndoSnapshot = (frame) => {
      if (!frame) return;
      undoStack = undoStack.filter(item => item !== frame);
    };
    const markComposerUndoAfterSnapshot = (frame) => {
      if (!frame || !els.input) return;
      frame.afterSignature = composerMutationSignature();
    };
    const forgetComposerUndo = () => {
      undoStack = [];
    };

    // Put a composition back. An attachment still in the composer keeps how it is
    // now, in the tray and in the input: an upload that finished since stays finished.
    const restoreComposerSnapshot = (snapshot) => {
      if (!snapshot || !els.input) return false;
      return withoutAttachmentReconcile(() => {
        const current = new Map(state.attachments.map(item => [String(item?.id || ""), item]));
        state.attachments = normalizeComposerAttachments((snapshot.attachments || [])
          .map(item => current.get(String(item?.id || "")) || item));
        els.input.innerHTML = String(snapshot.inputHtml || "");
        renderAttachments({ notify: true });
        restoreComposerSelection(els.input, snapshot.selection);
        options.onInput?.(currentController().payload(), currentController());
        return true;
      });
    };
    // Inserting an attachment put its token and one space after it into the
    // input; taking it back removes those and leaves the rest as it is.
    const undoComposerAttachmentInsert = (frame) => {
      const ids = new Set((frame?.attachmentIds || []).map(id => String(id || "")).filter(Boolean));
      if (!ids.size || !els.input) return false;
      let removed = false;
      for (const id of ids) {
        els.input.querySelectorAll(`[data-portal-composer-attachment-token="${CSS.escape(id)}"]`).forEach(node => {
          let next = node.nextSibling;
          while (next?.nodeType === Node.TEXT_NODE && !next.nodeValue) next = next.nextSibling;
          if (next?.nodeType === Node.TEXT_NODE && next.nodeValue.startsWith(" ")) {
            next.nodeValue = next.nodeValue.slice(1);
            if (!next.nodeValue) next.remove();
          }
          node.remove();
          removed = true;
        });
      }
      discardComposerAttachments(state.attachments.filter(item => ids.has(String(item?.id || ""))));
      state.attachments = state.attachments.filter(item => !ids.has(String(item?.id || "")));
      renderAttachmentTrayOnly({ notify: true });
      if (removed) els.input.dispatchEvent?.(new Event("input", { bubbles: true }));
      return true;
    };
    // Ctrl/⌘+Z: whether the Workspace took it.
    const undoComposerMutation = () => {
      const frame = undoStack[undoStack.length - 1];
      if (!frame) return false;
      if (frame.afterSignature && composerMutationSignature() !== frame.afterSignature) return false;
      undoStack = undoStack.slice(0, -1);
      if (frame.kind === "attachment-insert") return undoComposerAttachmentInsert(frame);
      return restoreComposerSnapshot(frame);
    };

    return {
      composerSnapshot, composerMutationSignature, pushComposerUndoSnapshot, dropComposerUndoSnapshot,
      markComposerUndoAfterSnapshot, forgetComposerUndo, restoreComposerSnapshot, undoComposerMutation,
    };
  }

  window.PortalChatWorkspaceComposerUndo = Object.freeze({ createComposerUndo, dependencies: DEPENDENCIES });
})();
