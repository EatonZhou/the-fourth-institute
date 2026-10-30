(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceTabsRestore) return;

  // Restoring a persisted tab set (a newer restore or open supersedes it),
  // reporting the documents that could not be restored, and destroying the
  // tab set.

  const DEPENDENCIES = Object.freeze([
    "activate", "documentTabsState", "documents", "notify", "open", "options", "orderedDocuments", "pendingSessions",
    "render", "restoreToken", "snapshot",
  ]);

  function createTabsRestore(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceTabsRestore is missing: ${missing.join(", ")}`);
    const {
      activate, documentTabsState, documents, notify, open, options, orderedDocuments, pendingSessions, render,
      restoreToken, snapshot,
    } = dependencies;

    const reportRestoreError = (error, record) => {
      if (typeof options.onError === "function") {
        options.onError(error, { reason: "restore", record });
        return;
      }
      console.error(window.PortalI18n.t("Preview tab restore failed"), error);
    };
    const restorePersistence = async (persisted = {}) => {
      const records = persisted.schemaVersion === 1
        && Array.isArray(persisted.documents)
        ? persisted.documents
        : [];
      const sequence = ++documentTabsState.restoreSequence;
      documentTabsState.restoringSequence = sequence;
      orderedDocuments().forEach(documentState => documentState.session?.destroy?.());
      documents.clear();
      documentTabsState.tabs = [];
      documentTabsState.activeKey = "";
      for (const record of records) {
        if (sequence !== documentTabsState.restoreSequence) return snapshot();
        let key = "";
        try {
          const restoredRequest = await options.restoreDocument?.(record.value, record);
          if (sequence !== documentTabsState.restoreSequence) return snapshot();
          key = String(restoredRequest?.key || record.key || "").trim();
          if (!key || !restoredRequest || typeof restoredRequest !== "object") continue;
          const result = await open({
            ...restoredRequest,
            key,
            pinned: record.pinned === true,
            __portalPreviewRestoreToken: restoreToken,
          });
          if (result?.error) {
            throw result.error instanceof Error
              ? result.error
              : new Error(String(result.error || "Preview tab restore failed"));
          }
        } catch (error) {
          const partial = key ? documents.get(key) : null;
          partial?.session?.destroy?.();
          if (key) {
            documents.delete(key);
            documentTabsState.tabs = documentTabsState.tabs.filter(item => item !== key);
            if (documentTabsState.activeKey === key) documentTabsState.activeKey = "";
          }
          reportRestoreError(error, record);
        }
        if (sequence !== documentTabsState.restoreSequence) return snapshot();
      }
      const restoredActiveKey = String(persisted.activeKey || "");
      documentTabsState.restoringSequence = 0;
      if (documents.has(restoredActiveKey)) {
        activate(restoredActiveKey, { reason: "restore" });
      } else if (documentTabsState.tabs.length) {
        activate(documentTabsState.tabs.at(-1), { reason: "restore-fallback" });
      } else {
        notify("restore-empty");
        options.onEmpty?.({ reason: "restore-empty" });
      }
      render();
      return snapshot();
    };
    const destroy = () => {
      documentTabsState.navigationSequence += 1;
      for (const session of pendingSessions) session.destroy?.();
      pendingSessions.clear();
      documentTabsState.restoreSequence += 1;
      orderedDocuments().forEach(documentState => documentState.session?.destroy?.());
      documents.clear();
      documentTabsState.tabs = [];
      documentTabsState.activeKey = "";
      notify("destroy");
      render();
    };

    return {
      destroy, restorePersistence,
    };
  }

  window.PortalWorkspaceSurfaceTabsRestore = Object.freeze({ createTabsRestore, dependencies: DEPENDENCIES });
})();
