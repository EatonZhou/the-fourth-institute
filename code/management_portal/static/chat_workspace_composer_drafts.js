(() => {
  if (window.PortalChatWorkspaceComposerDrafts) return;

  // One Workspace's drafts: the message being written, saved per conversation
  // (decision_f560803947c6). An edit is saved after a pause, and at once when
  // the page is hidden or left, the conversation changes or the Workspace is
  // destroyed; a composition that was only loaded, shown or cleared is not an
  // edit and is not written. A send that fails is kept as the draft of the
  // message it tried to send until that message turns out to have arrived.
  // The Workspace's state holds the conversation shown (activeDraftScope), the
  // recovered send (recoveredSubmission) and whether a draft is being loaded
  // (restoringDraft); destroyComposerDrafts writes the last edit and stops
  // listening to the page.
  const DEPENDENCIES = Object.freeze([
    "composerSelectionSnapshot", "createDraftStore", "els", "id", "normalizeWorkspaceDraft", "options",
    "persistedDraftAttachments", "refsPayload", "serializeComposerInput", "state", "workspaceDraftHasContent",
  ]);

  function createComposerDrafts(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposerDrafts is missing: ${missing.join(", ")}`);
    const {
      composerSelectionSnapshot, createDraftStore, els, id, normalizeWorkspaceDraft, options,
      persistedDraftAttachments, refsPayload, serializeComposerInput, state, workspaceDraftHasContent,
    } = dependencies;

    const draftStore = options.draftStore || createDraftStore({
      namespace: String(options.draftNamespace || id),
      owner: options.draftOwner || "anonymous",
      delay: options.draftSaveDelay || 180,
    });
    // What the composition was when it was last loaded or saved.
    let draftBaseline = null;

    const draftContentSignature = draft => JSON.stringify({
      text: draft.text,
      attachments: draft.attachments,
      refs: draft.refs,
    });
    const draftSignatures = draft => ({
      content: draftContentSignature(draft),
      submission: draft.submitted_message_id || "",
      view: JSON.stringify({ selection: draft.selection, input_height: draft.input_height }),
    });
    // The composition shown, as a draft. It belongs to a recovered send only
    // while its content is what that send tried to send.
    const captureWorkspaceDraft = () => {
      const draft = {
        text: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
        attachments: persistedDraftAttachments(state.attachments),
        refs: refsPayload(state.refs),
        selection: composerSelectionSnapshot(els.input),
        input_height: state.composerInputHeight,
      };
      if (state.recoveredSubmission?.content !== draftContentSignature(draft)) {
        state.recoveredSubmission = null;
      }
      return { ...draft, submitted_message_id: state.recoveredSubmission?.messageId || "" };
    };
    // The composition shown is now the saved one; only later edits are saved.
    const markDraftSaved = () => {
      draftBaseline = draftSignatures(captureWorkspaceDraft());
    };
    const saveActiveDraft = ({ immediate = false } = {}) => {
      if (state.restoringDraft || !state.activeDraftScope) return;
      const draft = captureWorkspaceDraft();
      const signature = draftSignatures(draft);
      // Loading, hiding and destroying an untouched composition are not edits.
      // Selection/layout changes may only update the draft they belong to.
      if (
        signature.content !== draftBaseline?.content
        || signature.submission !== draftBaseline?.submission
        || (signature.view !== draftBaseline?.view
          && signature.content === draftContentSignature(draftStore.read(state.activeDraftScope)))
      ) {
        draftStore.schedule(state.activeDraftScope, draft);
      }
      draftBaseline = signature;
      if (immediate) draftStore.flush(state.activeDraftScope);
    };
    const saveDraftBeforePageLeave = () => saveActiveDraft({ immediate: true });
    const saveDraftWhenHidden = () => {
      if (document.visibilityState === "hidden") saveDraftBeforePageLeave();
    };
    window.addEventListener("pagehide", saveDraftBeforePageLeave);
    document.addEventListener("visibilitychange", saveDraftWhenHidden);

    // Saves the draft of the conversation being left and returns the draft of
    // `scope`, which becomes the conversation shown. The caller loads it and
    // then marks it saved.
    const openDraftScope = scope => {
      if (state.activeDraftScope && state.activeDraftScope !== scope) saveActiveDraft({ immediate: true });
      state.activeDraftScope = scope;
      const draft = draftStore.read(scope);
      state.recoveredSubmission = draft.submitted_message_id
        ? { messageId: draft.submitted_message_id, content: draftContentSignature(draft) }
        : null;
      return draft;
    };
    const clearActiveDraft = () => {
      if (state.activeDraftScope) draftStore.clear(state.activeDraftScope);
      markDraftSaved();
    };
    // A host clears a conversation's draft, if `expected` is given only while it is that draft.
    const clearDraft = (scope, expected = null) => {
      if (expected && draftContentSignature(draftStore.read(scope)) !== draftContentSignature(normalizeWorkspaceDraft(expected))) return false;
      draftStore.clear(scope);
      if (scope === state.activeDraftScope) markDraftSaved();
      return true;
    };

    // A send that failed in the conversation shown: the input, put back, is
    // kept as the draft of the message it tried to send.
    const recoverSubmission = messageId => {
      state.recoveredSubmission = {
        messageId,
        content: draftContentSignature(captureWorkspaceDraft()),
      };
      saveActiveDraft({ immediate: true });
    };
    // A send that failed after its conversation was left is kept there, unless
    // that conversation has a draft again.
    const keepSubmissionDraft = (scope, draft, messageId) => {
      if (!workspaceDraftHasContent(draftStore.read(scope))) {
        draftStore.write(scope, { ...draft, submitted_message_id: messageId });
      }
    };
    // Messages that arrived: a draft kept for one of them goes. Returns whether
    // the composition shown is that recovered send, unedited, for the caller to clear.
    const forgetConfirmedSubmission = (messageIds, scope) => {
      const accepted = new Set((messageIds || []).filter(Boolean));
      if (!accepted.size) return false;
      if (accepted.has(draftStore.read(scope).submitted_message_id)) draftStore.clear(scope);
      if (state.activeDraftScope !== scope) return false;
      return accepted.has(captureWorkspaceDraft().submitted_message_id);
    };

    const destroyComposerDrafts = () => {
      saveActiveDraft({ immediate: true });
      window.removeEventListener("pagehide", saveDraftBeforePageLeave);
      document.removeEventListener("visibilitychange", saveDraftWhenHidden);
    };

    return {
      captureWorkspaceDraft, saveActiveDraft, markDraftSaved, openDraftScope, clearActiveDraft, clearDraft,
      recoverSubmission, keepSubmissionDraft, forgetConfirmedSubmission, destroyComposerDrafts,
    };
  }

  window.PortalChatWorkspaceComposerDrafts = Object.freeze({ createComposerDrafts, dependencies: DEPENDENCIES });
})();
