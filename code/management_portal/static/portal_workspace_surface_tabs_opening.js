(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceTabsOpening) return;

  // Moving through a tab set: activating a tab, opening a document (in a
  // new tab, in place of an unpinned one only once the new one is
  // readable, or in an existing one), closing a tab and pinning one.

  const DEPENDENCIES = Object.freeze([
    "active", "createDocumentSession", "documentTabsState", "documents", "notify", "options", "pendingSessions",
    "render", "reportError", "restoreToken",
  ]);

  function createTabsOpening(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceTabsOpening is missing: ${missing.join(", ")}`);
    const {
      active, createDocumentSession, documentTabsState, documents, notify, options, pendingSessions, render,
      reportError, restoreToken,
    } = dependencies;

    const activate = (key, meta = {}) => {
      const documentState = documents.get(String(key || ""));
      if (!documentState) return false;
      documentTabsState.navigationSequence += 1;
      documentTabsState.activeKey = documentState.key;
      notify(meta.reason || "activate", { ...meta, document: documentState });
      options.onActivate?.(
        documentState,
        documentState.session?.snapshot?.() || null,
        meta,
      );
      render();
      return true;
    };
    const open = async (request = {}) => {
      const navigation = ++documentTabsState.navigationSequence;
      const restoring = request.__portalPreviewRestoreToken === restoreToken;
      if (!restoring) documentTabsState.restoreSequence += 1;
      const key = String(request.key || "").trim();
      if (!key) return { accepted: false, stale: false };
      const existing = documents.get(key);
      if (existing) {
        if (request.mode === "new-tab") existing.pinned = true;
        activate(key, { reason: "open-existing", request });
        if (request.reload === true) {
          const result = await existing.session?.open?.(request);
          render();
          return result;
        }
        return {
          accepted: true,
          stale: false,
          existing: true,
          payload: existing.session?.snapshot?.().payload || null,
        };
      }

      const previous = active();
      const inheritedPin = previous?.pinned === true;
      const newTab = request.mode === "new-tab";
      const replacing = previous && !previous.pinned && !restoring && !newTab;
      let insertIndex = documentTabsState.tabs.length;
      if (replacing) {
        const discardApproved = await previous.session?.guardDiscard?.(request);
        if (discardApproved === false) return { accepted: false, stale: false };
        if (navigation !== documentTabsState.navigationSequence) return { accepted: false, stale: true };
        insertIndex = Math.max(0, documentTabsState.tabs.indexOf(previous.key));
      }

      const created = options.createDocument?.(request, {
        active: previous,
        insertIndex,
      }) || {};
      const documentState = {
        ...created,
        key,
        label: String(
          created.label
          || request.label
          || request.title
          || request.path
          || key,
        ).split("/").at(-1) || window.PortalI18n.t("Preview"),
        title: String(created.title || request.title || request.path || key),
        path: String(created.path || request.path || ""),
        pinned: created.pinned === undefined
          ? (restoring ? request.pinned === true : Boolean(newTab || request.pinned || inheritedPin))
          : created.pinned === true,
        session: null,
      };
      documentState.session = options.createSession?.(documentState, request)
        || createDocumentSession(options.sessionOptions || {});
      if (previous) {
        // Read into a detached session. Persistence and the visible document
        // retain the old identity until the new resource is actually readable.
        const before = previous.session?.snapshot?.() || {};
        pendingSessions.add(documentState.session);
        let result;
        try {
          result = await documentState.session.open({ ...request,
            onLoading: undefined, onPresent: undefined, onEmpty: undefined, onError: undefined,
          });
        } catch (error) {
          result = { accepted: true, stale: false, error };
        } finally {
          pendingSessions.delete(documentState.session);
        }
        const after = previous.session?.snapshot?.() || {};
        const superseded = navigation !== documentTabsState.navigationSequence || active() !== previous
          || ['draft', 'editing', 'dirty', 'saving'].some(field => before[field] !== after[field]);
        if (superseded || result?.error || result?.stale || result?.accepted === false || result?.payload == null) {
          documentState.session.destroy?.();
          if (!superseded && result?.error && !result.stale) reportError(result.error);
          return { ...result, stale: superseded || result?.stale === true };
        }
        if (replacing) {
          previous.session?.destroy?.();
          documents.delete(previous.key);
          documentTabsState.tabs = documentTabsState.tabs.filter(item => item !== previous.key);
        }
        if (newTab) previous.pinned = true;
        documents.set(key, documentState);
        documentTabsState.tabs.splice(insertIndex, 0, key);
        activate(key, { reason: "open-complete", request, result });
        request.onPresent?.(result.payload, documentState.session.snapshot?.(), { request });
        return result;
      }
      documents.set(key, documentState);
      documentTabsState.tabs.splice(insertIndex, 0, key);
      documentTabsState.activeKey = key;
      notify("open-create", { document: documentState, request });
      options.onActivate?.(
        documentState,
        documentState.session?.snapshot?.() || null,
        { reason: "open-create", request },
      );
      render();
      const result = await documentState.session.open(request);
      notify("open-complete", { document: documentState, request, result });
      render();
      return result;
    };
    const close = async (key, meta = {}) => {
      const documentState = documents.get(String(key || ""));
      if (!documentState) return false;
      const discardApproved = await documentState.session?.guardDiscard?.(meta);
      if (discardApproved === false) return false;
      documentTabsState.navigationSequence += 1;
      const index = documentTabsState.tabs.indexOf(documentState.key);
      const wasActive = documentState.key === documentTabsState.activeKey;
      documentState.session?.destroy?.();
      documents.delete(documentState.key);
      documentTabsState.tabs = documentTabsState.tabs.filter(item => item !== documentState.key);
      if (wasActive) {
        documentTabsState.activeKey = "";
        const next = documentTabsState.tabs[index] || documentTabsState.tabs[index - 1] || "";
        if (next) {
          activate(next, { ...meta, reason: meta.reason || "close-activate-next" });
        } else {
          notify(meta.reason || "close-empty", { ...meta, document: documentState });
          options.onEmpty?.(meta);
        }
      } else {
        notify(meta.reason || "close", { ...meta, document: documentState });
      }
      render();
      return true;
    };
    const togglePin = () => {
      const documentState = active();
      if (!documentState || options.canPin?.(documentState) === false) return false;
      documentState.pinned = !documentState.pinned;
      notify("pin-change", { document: documentState, pinned: documentState.pinned });
      options.onPinChange?.(documentState, documentState.pinned);
      render();
      return documentState.pinned;
    };

    return {
      activate, close, open, togglePin,
    };
  }

  window.PortalWorkspaceSurfaceTabsOpening = Object.freeze({ createTabsOpening, dependencies: DEPENDENCIES });
})();
