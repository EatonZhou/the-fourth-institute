(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSessionLoading) return;

  // What a document session shows: presenting a payload, showing a
  // status, and opening a request (after the discard guard) whose result
  // a newer request or the session's end supersedes.

  const DEPENDENCIES = Object.freeze([
    "documentSessionState", "guardDiscard", "invalidate", "options", "snapshot", "transition",
  ]);

  function createSessionLoading(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSessionLoading is missing: ${missing.join(", ")}`);
    const {
      documentSessionState, guardDiscard, invalidate, options, snapshot, transition,
    } = dependencies;

    const present = (payload, meta = {}) => {
      if (documentSessionState.destroyed) return null;
      if (meta.invalidate !== false) invalidate("present-invalidate");
      const continuousEdit = options.continuousEdit === true && !!payload;
      const current = transition({
        status: continuousEdit ? "editing" : (payload ? "ready" : "empty"),
        payload: payload || null,
        requestId: documentSessionState.requestId,
        requestKey: String(meta.requestKey || ""),
        editing: continuousEdit,
        draft: continuousEdit ? String(payload?.content || "") : "",
        dirty: false,
        saving: false,
        error: "",
        readError: "",
      }, meta.reason || "present", meta);
      options.onPresent?.(payload || null, current, meta);
      meta.onPresent?.(payload || null, current, meta);
      return payload || null;
    };
    const show = (patch = {}, meta = {}) => {
      if (documentSessionState.destroyed) return snapshot();
      if (meta.invalidate !== false) invalidate("show-invalidate");
      return transition({
        status: String(patch.status || "empty"),
        payload: patch.payload || null,
        requestId: documentSessionState.requestId,
        requestKey: String(patch.requestKey || ""),
        editing: false,
        draft: "",
        dirty: false,
        saving: false,
        error: String(patch.error || ""),
        readError: "",
      }, meta.reason || "show", meta);
    };
    const open = async (request = {}) => {
      if (documentSessionState.destroyed) return { accepted: false, stale: true };
      const sequence = ++documentSessionState.openSequence;
      const discardDecision = guardDiscard(request);
      const discardApproved = discardDecision && typeof discardDecision.then === "function"
        ? await discardDecision
        : discardDecision;
      if (sequence !== documentSessionState.openSequence) return { accepted: true, stale: true };
      if (!discardApproved) return { accepted: false, stale: false };
      const id = invalidate("open-invalidate");
      documentSessionState.abortController = typeof AbortController === "function" ? new AbortController() : null;
      const requestKey = String(request.key || "");
      const loadingPayload = request.loadingPayload || null;
      const current = transition({
        status: "loading",
        payload: loadingPayload,
        requestId: id,
        requestKey,
        editing: false,
        draft: "",
        dirty: false,
        saving: false,
        error: "",
        readError: "",
      }, "loading", { request, requestId: id });
      options.onLoading?.(current, request);
      request.onLoading?.(current, request);
      try {
        const payload = await request.load?.({
          signal: documentSessionState.abortController?.signal,
          requestId: id,
          requestKey,
        });
        if (id !== documentSessionState.requestId) return { accepted: true, stale: true };
        documentSessionState.abortController = null;
        if (payload == null && request.allowEmpty !== true) {
          const empty = transition({
            status: "empty",
            payload: null,
            saving: false,
            error: "",
          }, "empty", { request, requestId: id });
          options.onEmpty?.(empty, request);
          request.onEmpty?.(empty, request);
          return { accepted: true, stale: false, payload: null };
        }
        present(payload, {
          invalidate: false,
          reason: "loaded",
          request,
          requestId: id,
          requestKey,
          onPresent: request.onPresent,
        });
        return { accepted: true, stale: false, payload };
      } catch (error) {
        if (id !== documentSessionState.requestId || error?.name === "AbortError") {
          return { accepted: true, stale: true, error };
        }
        documentSessionState.abortController = null;
        const failed = transition({
          status: "error",
          payload: request.errorPayload || loadingPayload,
          saving: false,
          error: String(error?.message || error || "Preview failed"),
        }, "error", { request, requestId: id, error });
        options.onError?.(error, failed, request);
        request.onError?.(error, failed, request);
        return { accepted: true, stale: false, error };
      }
    };

    return {
      open, present, show,
    };
  }

  window.PortalWorkspaceSurfaceSessionLoading = Object.freeze({ createSessionLoading, dependencies: DEPENDENCIES });
})();
