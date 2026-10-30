(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceTabsView) return;

  // Drawing a tab set: its shell, the tabs with their labels, titles and
  // dirty and pinned marks, selecting and closing tabs from them, and the
  // pin button of the active document.

  const DEPENDENCIES = Object.freeze([
    "activate", "active", "close", "documentTabsState", "options", "orderedDocuments", "renderDocumentTabs",
    "reportError", "resolveRoot", "snapshot",
  ]);

  function createTabsView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceTabsView is missing: ${missing.join(", ")}`);
    const {
      activate, active, close, documentTabsState, options, orderedDocuments, renderDocumentTabs, reportError,
      resolveRoot, snapshot,
    } = dependencies;

    const render = () => {
      const shell = resolveRoot(options.shell);
      const root = resolveRoot(options.root);
      const pinButton = resolveRoot(options.pinButton);
      const ordered = orderedDocuments();
      if (shell) shell.hidden = ordered.length === 0 && options.showWhenEmpty !== true;
      if (root) {
        renderDocumentTabs({
          root,
          emptyText: options.emptyText,
          items: ordered.map(documentState => {
            const sessionState = documentState.session?.snapshot?.() || {};
            return {
              key: documentState.key,
              label: options.label?.(documentState, sessionState)
                || documentState.label
                || String(documentState.key || "").split("/").at(-1)
                || window.PortalI18n.t("Preview"),
              title: options.title?.(documentState, sessionState)
                || documentState.title
                || documentState.path
                || documentState.key,
              active: documentState.key === documentTabsState.activeKey,
              dirty: sessionState.dirty === true,
              pinned: documentState.pinned === true,
              closable: documentState.closable !== false,
            };
          }),
          onSelect: key => {
            try {
              const result = activate(key, { reason: "tab-select" });
              if (result && typeof result.catch === "function") result.catch(reportError);
            } catch (error) {
              reportError(error);
            }
          },
          onClose: key => {
            close(key, { reason: "tab-close" }).catch(reportError);
          },
        });
      }
      const current = active();
      if (pinButton) {
        const canPin = !!current && options.canPin?.(current) !== false;
        pinButton.disabled = !canPin;
        pinButton.setAttribute("aria-pressed", current?.pinned ? "true" : "false");
        pinButton.setAttribute(
          "aria-label",
          current?.pinned ? window.PortalI18n.t("解除当前文件锁定") : window.PortalI18n.t("锁定当前文件"),
        );
        pinButton.title = current?.pinned
          ? window.PortalI18n.t("解除锁定；下次打开文件时可替换此标签")
          : window.PortalI18n.t("锁定当前文件；之后打开的文件会新增为标签");
      }
      options.onRender?.(snapshot());
      return ordered.length;
    };

    return {
      render,
    };
  }

  window.PortalWorkspaceSurfaceTabsView = Object.freeze({ createTabsView, dependencies: DEPENDENCIES });
})();
