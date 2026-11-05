(() => {
  "use strict";

  if (window.PortalWorkspaceSurfacePanelController) return;

  // The controller of a Preview panel: what the Workbench and pages call
  // (opening, closing, snapshots and restoring, navigation adapters and
  // the factories of owned sessions, tab sets and selections), and the
  // toolbar's back and refresh buttons.

  const DEPENDENCIES = Object.freeze([
    "clearPreview", "createDocumentEditorTools", "createOwnedDocumentSession", "createOwnedSelectionController",
    "createOwnedTabsController", "documentSessions", "goBack", "hasPreviewTarget", "ids", "navigationLocator",
    "needsNavigationAdapter", "normalizeNavigationHistory", "openResource", "options", "previewHostDisposers",
    "previewPanelState", "previewRoot", "registerOwnedPreviewHost", "restoreNavigationResource", "root",
    "sanitizeNavigationLocator", "selectionControllers", "surface", "tabControllers", "updateBackAction",
  ]);

  function createPanelController(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfacePanelController is missing: ${missing.join(", ")}`);
    const {
      clearPreview, createDocumentEditorTools, createOwnedDocumentSession, createOwnedSelectionController,
      createOwnedTabsController, documentSessions, goBack, hasPreviewTarget, ids, navigationLocator,
      needsNavigationAdapter, normalizeNavigationHistory, openResource, options, previewHostDisposers,
      previewPanelState, previewRoot, registerOwnedPreviewHost, restoreNavigationResource, root,
      sanitizeNavigationLocator, selectionControllers, surface, tabControllers, updateBackAction,
    } = dependencies;

    // A host may supply live presentation sessions instead of file sessions.
    // Their navigation/admission policy stays with the host; all Preview chrome
    // and action dispatch stay here, using the same tabs and toolbar as files.
    let presentation = null, tabSignature = "";
    const runPresentationAction = key => {
      const action = presentation?.actions?.[key];
      if (!action || action.disabled) return;
      Promise.resolve().then(() => action.run?.()).catch(error => surface.setReadError(error?.message || String(error)));
    };
    function setPresentation(value) {
      presentation = value;
      const items = value.tabs || [];
      const signature = JSON.stringify(items);
      if (signature !== tabSignature) {
        tabSignature = signature;
        window.PortalWorkspaceSurface.renderDocumentTabs({ root: surface.element("tabs"), items,
          onSelect: key => presentation?.onSelect?.(key),
          onClose: key => Promise.resolve(presentation?.onClose?.(key)).catch(error => surface.setReadError(error?.message || String(error))),
        });
      }
      surface.element("tabsShell").hidden = false;
      surface.setPath(value.path || "");
      surface.setDocumentStatistics(value.document || null);
      const pin = surface.element("pin");
      pin.disabled = !value.onPin;
      pin.setAttribute("aria-disabled", String(pin.disabled));
      pin.setAttribute("aria-pressed", String(!!value.pinned));
      pin.setAttribute("aria-label", window.PortalI18n.t(value.pinned ? "解除当前文件锁定" : "锁定当前文件"));
      pin.title = pin.getAttribute("aria-label");
      const backButton = root.querySelector(`#${CSS.escape(ids.back)}`);
      backButton.disabled = !value.actions?.back || value.actions.back.disabled === true;
      backButton.setAttribute("aria-disabled", String(backButton.disabled));
      for (const key of surface.actionKeys) {
        if (key === "download") continue;
        const element = surface.action(key), action = value.actions?.[key];
        if (!element) continue;
        element.disabled = !action || action.disabled === true;
        element.setAttribute("aria-disabled", String(element.disabled));
        element.classList.toggle("active", action?.active === true);
        if (element.classList.contains("portalDocumentMode")) element.setAttribute("aria-pressed", String(action?.active === true));
        if (typeof action?.pressed === "boolean") element.setAttribute("aria-pressed", String(action.pressed));
      }
      surface.setDownload(value.download?.url || "", value.download?.filename || "");
    }
    const controller = Object.freeze({
      root,
      ids,
      surface,
      setPresentation,
      createDocumentSession: createOwnedDocumentSession,
      createDocumentTabsController: createOwnedTabsController,
      createDocumentEditorTools,
      createPreviewSelectionController: createOwnedSelectionController,
      registerPreviewHost: registerOwnedPreviewHost,
      setDocument(document) { return openResource({ document }); },
      openResource,
      goBack,
      async canClose() {
        const sessions = new Set([...documentSessions, ...[...tabControllers].flatMap(tabs => (
          tabs.snapshot().documents.map(document => document.session).filter(Boolean)
        ))]);
        for (const session of sessions) {
          if (session.snapshot().dirty && !await session.guardDiscard()) return false;
        }
        return true;
      },
      setNavigationAdapter(adapter = null) {
        if (adapter == null) {
          previewPanelState.navigationAdapter = null;
          return null;
        }
        const id = String(adapter.id || "").trim();
        if (!id || typeof adapter.serialize !== "function" || typeof adapter.restore !== "function") {
          throw new TypeError("Preview navigation adapter requires id, serialize, and restore");
        }
        previewPanelState.navigationAdapter = Object.freeze({ id, serialize: adapter.serialize, restore: adapter.restore });
        previewPanelState.navigationHistory = normalizeNavigationHistory(previewPanelState.navigationHistory);
        const pending = previewPanelState.pendingBindingRestore;
        previewPanelState.pendingBindingRestore = null;
        if (pending) {
          void restoreNavigationResource(pending)
            .then(resource => openResource(resource, { recordHistory: false }))
            .catch(error => {
              previewPanelState.pendingBindingRestore = pending;
              surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
            });
        }
        updateBackAction();
        return previewPanelState.navigationAdapter;
      },
      setContext(context = {}) {
        const target = context.documentTarget;
        if (!target) return previewPanelState.documentValue;
        const targetPanelId = String(target.panelId || target.panel_id || "").trim();
        if (hasPreviewTarget(previewPanelState.binding) && targetPanelId !== String(options.panelId || "")) return previewPanelState.documentValue;
        return openResource(target);
      },
      clear: clearPreview,
      reload() {
        // A document-session adapter owns both edit protection and rendering.
        if (options.reload) return options.reload(previewPanelState.documentValue);
        return openResource(previewPanelState.binding, { recordHistory: false });
      },
      snapshot: () => ({
        binding: navigationLocator(previewPanelState.binding),
        navigationHistory: previewPanelState.navigationHistory.map(locator => ({ ...locator })),
        dirty: [...documentSessions].some(session => session.snapshot().dirty)
          || [...tabControllers].some(tabs => tabs.snapshot().documents.some(document => document.session?.snapshot().dirty)),
        documentTabs: tabControllers.size
          ? [...tabControllers].map(item => item.persistenceSnapshot())
          : previewPanelState.restoredTabSnapshots,
      }),
      restore(snapshot = {}) {
        previewPanelState.navigationHistory = normalizeNavigationHistory(snapshot.navigationHistory);
        updateBackAction();
        previewPanelState.restoredTabSnapshots = Array.isArray(snapshot.documentTabs)
          ? snapshot.documentTabs.filter(item => item && typeof item === "object")
          : [];
        [...tabControllers].forEach((tabs, index) => {
          const restored = previewPanelState.restoredTabSnapshots[index];
          if (!restored) return;
          void tabs.restorePersistence(restored).catch(error => console.error(window.PortalI18n.t("Preview tab restore failed"), error));
        });
        const restoredBinding = sanitizeNavigationLocator(snapshot.binding);
        if (!hasPreviewTarget(restoredBinding)) return clearPreview();
        previewPanelState.binding = { ...restoredBinding };
        if (!previewPanelState.navigationAdapter && needsNavigationAdapter(restoredBinding)) {
          previewPanelState.pendingBindingRestore = restoredBinding;
          return;
        }
        if (restoredBinding.navigationAdapter !== undefined
          && restoredBinding.navigationAdapter !== previewPanelState.navigationAdapter?.id) {
          previewPanelState.pendingBindingRestore = restoredBinding;
          return;
        }
        void restoreNavigationResource(restoredBinding)
          .then(resource => openResource(resource, { recordHistory: false }))
          .catch(error => {
            previewPanelState.pendingBindingRestore = restoredBinding;
            surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
          });
      },
      show() {},
      hide() {},
      async destroy() {
        previewPanelState.destroyed = true;
        previewPanelState.requestRevision += 1;
        window.PortalPreview.dispose(previewRoot);
        previewHostDisposers.forEach(dispose => dispose());
        previewHostDisposers.clear();
        tabControllers.forEach(item => item.destroy?.());
        tabControllers.clear();
        selectionControllers.forEach(item => item.destroy?.());
        selectionControllers.clear();
        documentSessions.forEach(item => item.destroy?.());
        documentSessions.clear();
        surface.destroy();
        root.replaceChildren();
      },
    });

    // The back and refresh buttons run the panel's commands, or the controller without them.
    function bindToolbar() {
      surface.element("pin")?.addEventListener("click", () => presentation?.onPin?.());
      for (const key of surface.actionKeys) {
        if (["refresh", "download"].includes(key)) continue;
        surface.action(key)?.addEventListener("click", () => runPresentationAction(key));
      }
      [ids.back].forEach(id => {
        root.querySelector(`#${CSS.escape(id)}`)?.addEventListener("click", event => {
          if (presentation) return runPresentationAction("back");
          const operation = options.commands?.execute
            ? options.commands.execute("back", { event, source: "preview-toolbar" })
            : controller.goBack();
          Promise.resolve(operation).catch(error => {
            surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
          });
        });
      });
      surface.action("refresh")?.addEventListener("click", event => {
        if (presentation) return runPresentationAction("refresh");
        const operation = options.commands?.execute
          ? options.commands.execute("reload", { event, source: "preview-toolbar" })
          : controller.reload();
        Promise.resolve(operation).catch(error => {
          surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
        });
      });
      const refresh = surface.action("refresh");
      if (refresh) refresh.disabled = false;
    }

    return {
      bindToolbar, controller,
    };
  }

  window.PortalWorkspaceSurfacePanelController = Object.freeze({ createPanelController, dependencies: DEPENDENCIES });
})();
