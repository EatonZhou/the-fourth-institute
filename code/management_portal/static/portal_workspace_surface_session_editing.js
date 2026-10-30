(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSessionEditing) return;

  // Editing in a document session: starting, the draft, cancelling,
  // saving (whose result a newer save supersedes) and the actions the
  // session allows.

  const DEPENDENCIES = Object.freeze([
    "documentSessionState", "guardDiscard", "options", "snapshot", "transition",
  ]);

  function createSessionEditing(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSessionEditing is missing: ${missing.join(", ")}`);
    const {
      documentSessionState, guardDiscard, options, snapshot, transition,
    } = dependencies;

    const beginEdit = meta => {
      if (documentSessionState.destroyed || !documentSessionState.state.payload || documentSessionState.state.saving) return false;
      if (typeof options.canEdit === "function" && !options.canEdit(documentSessionState.state.payload, snapshot())) return false;
      transition({
        status: "editing",
        editing: true,
        draft: String(documentSessionState.state.payload.content || ""),
        dirty: false,
        error: "",
      }, "edit-start", meta);
      options.onEditStart?.(snapshot(), meta);
      return true;
    };
    const updateDraft = (draft, meta) => {
      if (documentSessionState.destroyed || !documentSessionState.state.editing || documentSessionState.state.saving) return false;
      transition({
        draft: String(draft ?? ""),
        dirty: String(draft ?? "") !== String(documentSessionState.state.payload?.content || ""),
      }, "draft-change", meta);
      return true;
    };
    const cancelEdit = async (meta = {}) => {
      if (documentSessionState.destroyed || !documentSessionState.state.editing || documentSessionState.state.saving) return false;
      const discardDecision = guardDiscard(meta);
      const discardApproved = discardDecision && typeof discardDecision.then === "function"
        ? await discardDecision
        : discardDecision;
      if (!discardApproved) return false;
      const payload = documentSessionState.state.payload;
      const continuousEdit = options.continuousEdit === true && !!payload;
      const current = transition({
        status: continuousEdit ? "editing" : (payload ? "ready" : "empty"),
        editing: continuousEdit,
        draft: continuousEdit ? String(payload?.content || "") : "",
        dirty: false,
        saving: false,
        error: "",
      }, "edit-cancel", meta);
      options.onPresent?.(payload, current, { ...meta, reason: "edit-cancel" });
      meta.onPresent?.(payload, current, { ...meta, reason: "edit-cancel" });
      return true;
    };
    const save = async (request = {}) => {
      if (documentSessionState.destroyed || !actionState().canSave || typeof request.save !== "function") {
        return { accepted: false };
      }
      const id = ++documentSessionState.saveId;
      const payload = documentSessionState.state.payload;
      const draft = documentSessionState.state.draft;
      transition({ status: "saving", saving: true, error: "" }, "saving", { request, saveId: id });
      options.onSaving?.(snapshot(), request);
      request.onSaving?.(snapshot(), request);
      try {
        const updated = await request.save({ payload, draft, saveId: id });
        if (id !== documentSessionState.saveId) return { accepted: true, stale: true };
        const savedPayload = updated
          ? {
            ...payload,
            ...updated,
            content: Object.hasOwn(updated, "content") ? updated.content : draft,
          }
          : { ...payload, content: draft };
        transition({
          status: "editing",
          payload: savedPayload,
          editing: true,
          draft: String(savedPayload.content ?? ""),
          dirty: false,
          saving: false,
          error: "",
        }, "saved", { request, saveId: id });
        options.onSaved?.(savedPayload, snapshot(), request);
        request.onSaved?.(savedPayload, snapshot(), request);
        return { accepted: true, stale: false, payload: savedPayload };
      } catch (error) {
        if (id !== documentSessionState.saveId) return { accepted: true, stale: true, error };
        const failed = transition({
          status: "editing",
          saving: false,
          editing: true,
          error: String(error?.message || error || "Save failed"),
        }, "save-error", { request, saveId: id, error });
        options.onSaveError?.(error, failed, request);
        request.onError?.(error, failed, request);
        return { accepted: true, stale: false, error };
      }
    };
    const actionState = () => {
      const current = snapshot();
      const editable = !documentSessionState.destroyed && !!current.payload
        && (typeof options.canEdit !== "function" || !!options.canEdit(current.payload, current));
      return Object.freeze({
        status: current.status,
        hasPayload: !!current.payload,
        loading: current.status === "loading",
        editing: current.editing,
        dirty: current.dirty,
        saving: current.saving,
        error: current.error,
        editable,
        canEdit: editable && !current.saving && current.status !== "loading",
        canSave: editable && current.editing && current.dirty && !current.saving,
        canOpen: !!current.payload && !current.editing && !current.saving && current.status === "ready",
      });
    };

    return {
      actionState, beginEdit, cancelEdit, save, updateDraft,
    };
  }

  window.PortalWorkspaceSurfaceSessionEditing = Object.freeze({ createSessionEditing, dependencies: DEPENDENCIES });
})();
