(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSessionTransitions) return;

  // How a document session changes: its snapshots, transitions announced
  // to its listeners, the guard before discarding unsaved edits,
  // invalidating work in flight, subscribing, read errors and destroying
  // the session.

  const DEPENDENCIES = Object.freeze([
    "documentSessionState", "options",
  ]);

  function createSessionTransitions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSessionTransitions is missing: ${missing.join(", ")}`);
    const {
      documentSessionState, options,
    } = dependencies;

    const listeners = new Set();
    documentSessionState.state = {
      status: "empty",
      payload: null,
      requestId: documentSessionState.requestId,
      requestKey: "",
      editing: false,
      draft: "",
      dirty: false,
      saving: false,
      error: "",
      readError: "",
    };

    const snapshot = () => Object.freeze({ ...documentSessionState.state });
    const emit = (reason, meta = {}) => {
      const current = snapshot();
      options.onStateChange?.(current, reason, meta);
      listeners.forEach(listener => listener(current, reason, meta));
      return current;
    };
    const transition = (patch, reason, meta = {}) => {
      documentSessionState.state = { ...documentSessionState.state, ...patch };
      return emit(reason, meta);
    };
    const confirmDiscard = context => {
      if (!documentSessionState.state.dirty) return true;
      const handler = context?.confirmDiscard || options.confirmDiscard;
      if (typeof handler === "function") {
        const decision = handler(snapshot(), context);
        return decision && typeof decision.then === "function"
          ? Promise.resolve(decision).then(value => value !== false)
          : decision !== false;
      }
      return window.PortalUI.confirm({
        title: window.PortalI18n.t("放弃未保存的修改？"),
        message: options.discardMessage || window.PortalI18n.t("关闭后将丢失尚未保存的文件修改。"),
        confirmLabel: window.PortalI18n.t("放弃修改"),
        tone: "danger",
      });
    };
    const guardDiscard = (context = {}) => {
      if (documentSessionState.destroyed) return false;
      const guarded = snapshot();
      const stillCurrent = approved => approved !== false
        && !documentSessionState.destroyed
        && documentSessionState.requestId === guarded.requestId
        && documentSessionState.state.payload === guarded.payload
        && documentSessionState.state.draft === guarded.draft
        && documentSessionState.state.editing === guarded.editing
        && documentSessionState.state.saving === guarded.saving;
      const decision = confirmDiscard(context);
      return decision && typeof decision.then === "function"
        ? Promise.resolve(decision).then(stillCurrent)
        : stillCurrent(decision);
    };
    const invalidate = (reason = "invalidate") => {
      if (documentSessionState.destroyed) return documentSessionState.requestId;
      documentSessionState.openSequence += 1;
      documentSessionState.requestId += 1;
      documentSessionState.saveId += 1;
      documentSessionState.abortController?.abort?.();
      documentSessionState.abortController = null;
      documentSessionState.state = { ...documentSessionState.state, requestId: documentSessionState.requestId };
      emit(reason);
      return documentSessionState.requestId;
    };

    const subscribe = listener => {
      if (documentSessionState.destroyed || typeof listener !== "function") return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    };
    const setReadError = error => {
      if (documentSessionState.destroyed) return snapshot();
      return transition({ readError: String(error?.message || error || "") }, "read-error");
    };
    const destroy = () => {
      if (documentSessionState.destroyed) return;
      invalidate("destroy");
      documentSessionState.destroyed = true;
      listeners.clear();
    };

    return {
      destroy, emit, guardDiscard, invalidate, setReadError, snapshot, subscribe, transition,
    };
  }

  window.PortalWorkspaceSurfaceSessionTransitions = Object.freeze({ createSessionTransitions, dependencies: DEPENDENCIES });
})();
