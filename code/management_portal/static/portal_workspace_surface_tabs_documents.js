(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceTabsDocuments) return;

  // The documents of a tab set: their sessions while they read, the token
  // that marks restoring requests, the documents in tab order and the
  // active one, snapshots for observers and for persistence, announcing
  // changes and reporting errors.

  const DEPENDENCIES = Object.freeze([
    "documentTabsState", "options",
  ]);

  function createTabsDocuments(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceTabsDocuments is missing: ${missing.join(", ")}`);
    const {
      documentTabsState, options,
    } = dependencies;

    const documents = new Map();
    const pendingSessions = new Set();

    const restoreToken = Symbol("portal-preview-tab-restore");

    const orderedDocuments = () => documentTabsState.tabs
      .map(key => documents.get(key))
      .filter(Boolean);
    const active = () => documentTabsState.activeKey ? documents.get(documentTabsState.activeKey) || null : null;
    const snapshot = () => Object.freeze({
      activeKey: documentTabsState.activeKey,
      active: active(),
      tabs: Object.freeze([...documentTabsState.tabs]),
      documents: Object.freeze(orderedDocuments()),
    });
    const persistenceSnapshot = () => Object.freeze({
      schemaVersion: 1,
      activeKey: documentTabsState.activeKey,
      documents: Object.freeze(orderedDocuments().map(documentState => {
        const value = options.serializeDocument?.(
          documentState,
          documentState.session?.snapshot?.() || null,
        );
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        return Object.freeze({
          key: documentState.key,
          pinned: documentState.pinned === true,
          value,
        });
      }).filter(Boolean)),
    });
    const notify = (reason, meta = {}) => {
      const current = snapshot();
      options.onChange?.(current, reason, meta);
      return current;
    };
    const reportError = error => {
      if (typeof options.onError === "function") options.onError(error);
      else window.setTimeout(() => { throw error; }, 0);
    };

    const isActive = key => String(key || "") === documentTabsState.activeKey;
    const get = key => documents.get(String(key || "")) || null;

    return {
      active, documents, get, isActive, notify, orderedDocuments, pendingSessions, persistenceSnapshot, reportError,
      restoreToken, snapshot,
    };
  }

  window.PortalWorkspaceSurfaceTabsDocuments = Object.freeze({ createTabsDocuments, dependencies: DEPENDENCIES });
})();
