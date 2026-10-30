(() => {
  "use strict";

  if (window.PortalWorkspaceSurfacePanelOwnership) return;

  // What a Preview panel owns: the document sessions, tab sets, selection
  // controllers and Preview hosts created through it (a tab set restores
  // the tabs the panel's snapshot kept for it), and announcing that the
  // panel's persisted state changed.

  const DEPENDENCIES = Object.freeze([
    "clearPreview", "createDocumentSession", "createDocumentTabsController", "createPreviewSelectionController",
    "options", "previewPanelState",
  ]);

  function createPanelOwnership(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfacePanelOwnership is missing: ${missing.join(", ")}`);
    const {
      clearPreview, createDocumentSession, createDocumentTabsController, createPreviewSelectionController, options,
      previewPanelState,
    } = dependencies;

    const documentSessions = new Set();
    const tabControllers = new Set();
    const selectionControllers = new Set();
    const previewHostDisposers = new Set();

    let persistenceScheduled = false;
    const notifyPersistence = () => {
      if (previewPanelState.destroyed || persistenceScheduled) return;
      persistenceScheduled = true;
      // Tab callbacks run before activation renders the document. Snapshot
      // after those synchronous callbacks, without waiting for a layout edit.
      Promise.resolve().then(() => {
        persistenceScheduled = false;
        if (!previewPanelState.destroyed && ![...tabControllers].some(tabs => tabs.isRestoring())) {
          options.emit?.("persistence-change");
        }
      });
    };
    const ownDocumentSession = session => {
      if (documentSessions.has(session)) return session;
      documentSessions.add(session);
      session.subscribe((snapshot, reason) => {
        if (reason === "destroy") documentSessions.delete(session);
      });
      return session;
    };
    const createOwnedDocumentSession = config => ownDocumentSession(createDocumentSession(config));
    const createOwnedTabsController = config => {
      const createDocument = config?.createDocument;
      const restoreIndex = tabControllers.size;
      const tabs = createDocumentTabsController({
        ...(config || {}),
        onChange: (snapshot, reason, meta) => {
          // Closing the last owned document also retires its navigation
          // binding, before persistence observers take their next snapshot.
          if (!snapshot.documents.length
            && [...tabControllers].every(controller => !controller.snapshot().documents.length)) {
            clearPreview();
          }
          config?.onChange?.(snapshot, reason, meta);
          if (reason !== "open-create" && reason !== "destroy"
            && !meta?.request?.__portalPreviewRestoreToken
            && !meta?.result?.error && !meta?.result?.stale
            && meta?.result?.accepted !== false) notifyPersistence();
        },
        createDocument: request => {
          const documentState = createDocument?.(request) || {};
          if (documentState.session) ownDocumentSession(documentState.session);
          return documentState;
        },
      });
      tabControllers.add(tabs);
      const restored = previewPanelState.restoredTabSnapshots[restoreIndex];
      if (Array.isArray(restored?.documents) && restored.documents.length) {
        void tabs.restorePersistence(restored).catch(error => {
          config?.onError?.(error);
          if (typeof config?.onError !== "function") console.error(window.PortalI18n.t("Preview tab restore failed"), error);
        });
      }
      return tabs;
    };
    const createOwnedSelectionController = config => {
      const selection = createPreviewSelectionController(config);
      selectionControllers.add(selection);
      return selection;
    };
    const registerOwnedPreviewHost = (name, handler, config) => {
      const dispose = window.PortalPreview.registerHost(name, handler, config);
      previewHostDisposers.add(dispose);
      return () => { previewHostDisposers.delete(dispose); dispose(); };
    };

    return {
      createOwnedDocumentSession, createOwnedSelectionController, createOwnedTabsController, documentSessions,
      notifyPersistence, previewHostDisposers, registerOwnedPreviewHost, selectionControllers, tabControllers,
    };
  }

  window.PortalWorkspaceSurfacePanelOwnership = Object.freeze({ createPanelOwnership, dependencies: DEPENDENCIES });
})();
