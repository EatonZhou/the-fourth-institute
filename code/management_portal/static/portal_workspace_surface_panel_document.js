(() => {
  "use strict";

  if (window.PortalWorkspaceSurfacePanelDocument) return;

  // The document a Preview panel shows: its header and initial line,
  // rendering, opening a resource (discarding results a newer request
  // superseded), clearing the panel and going back in its history.

  const DEPENDENCIES = Object.freeze([
    "hasPreviewTarget", "ids", "notifyPersistence", "options", "previewPanelState", "rememberPreviousResource",
    "restoreNavigationResource", "root", "surface", "updateBackAction",
  ]);

  function createPanelDocument(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfacePanelDocument is missing: ${missing.join(", ")}`);
    const {
      hasPreviewTarget, ids, notifyPersistence, options, previewPanelState, rememberPreviousResource,
      restoreNavigationResource, root, surface, updateBackAction,
    } = dependencies;

    const previewRoot = surface.element("preview");
    previewPanelState.binding = { ...(options.resource || {}) };
    previewPanelState.documentValue = options.document || options.payload || previewPanelState.binding.document || null;
    let previewRender = null;

    const updateDocumentHeader = document => {
      const title = root.querySelector(`#${CSS.escape(ids.title)}`);
      if (title) title.textContent = String(document?.title || document?.name || options.heading || window.PortalI18n.t("Preview"));
      surface.setPath(String(document?.path || document?.resourceAddress || ""));
    };
    const initialPreviewLine = resource => {
      const candidates = [resource, resource?.request, resource?.document, resource?.payload]
        .filter(candidate => candidate && typeof candidate === "object");
      const explicit = candidates
        .map(candidate => Number(candidate.lineStart ?? candidate.line))
        .find(value => Number.isInteger(value) && value > 0);
      if (explicit) return explicit;
      const locator = candidates.map(candidate => String(candidate.locator || "").trim()).find(Boolean) || "";
      const parsed = Number(locator.match(/(?:#L|:)(\d+)/i)?.[1] || 0);
      return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
    };
    const focusInitialPreviewLine = resource => {
      const lineNumber = initialPreviewLine(resource);
      if (!lineNumber) return false;
      const target = previewRoot.querySelector(`[data-preview-line="${lineNumber}"]`);
      if (!target) return false;
      previewRoot.querySelector('[data-preview-line][aria-current="location"]')?.removeAttribute("aria-current");
      target.setAttribute("aria-current", "location");
      (target.querySelector(".previewLineContent") || target).scrollIntoView({ block: "center", inline: "nearest" });
      return true;
    };
    const renderDocument = async document => {
      previewPanelState.documentValue = window.PortalPreview.normalizeDocument(document || {});
      updateDocumentHeader(previewPanelState.documentValue);
      surface.setDownload(
        previewPanelState.documentValue.downloadUrl,
        String(
          previewPanelState.documentValue.downloadName
          || previewPanelState.documentValue.filename
          || previewPanelState.documentValue.name
          || previewPanelState.documentValue.title
          || "",
        ).split("/").at(-1),
      );
      previewRender = window.PortalPreview.render({
        root: previewRoot,
        document: previewPanelState.documentValue,
        onPreviewRequest: options.onPreviewRequest,
        context: options.renderContext,
      });
      surface.setDocumentStatistics(previewPanelState.documentValue);
      await previewRender.promise;
      surface.setPath(String(previewPanelState.documentValue.path || previewPanelState.documentValue.resourceAddress || ""));
      return previewPanelState.documentValue;
    };
    const clearPreview = (config = {}) => {
      previewPanelState.requestRevision += 1;
      previewPanelState.binding = {};
      previewPanelState.pendingBindingRestore = null;
      previewPanelState.documentValue = null;
      surface.setDocumentStatistics(null);
      if (config.preserveHistory !== true) previewPanelState.navigationHistory = [];
      window.PortalPreview.dispose(previewRoot);
      previewRender = null;
      updateDocumentHeader(null);
      surface.setDownload("");
      surface.setPath("");
      previewRoot.textContent = String(config.emptyText || options.emptyText || window.PortalI18n.t("选择文件后显示内容。"));
      updateBackAction();
      notifyPersistence();
      return null;
    };
    const openResource = async (resource, navigation = {}) => {
      if (!hasPreviewTarget(resource)) return clearPreview();
      const previousBinding = previewPanelState.binding;
      const revision = ++previewPanelState.requestRevision;
      surface.setState(window.PortalI18n.t("加载中…"));
      try {
        const embedded = resource?.document || resource?.payload;
        const request = resource?.request || resource;
        const document = embedded || await (typeof request?.load === "function"
          ? request.load()
          : options.load
            ? options.load(resource)
            : window.PortalPreview.load(request));
        if (revision !== previewPanelState.requestRevision) return null;
        const rendered = await renderDocument(document);
        if (revision !== previewPanelState.requestRevision) return null;
        previewPanelState.binding = { ...(resource || {}) };
        focusInitialPreviewLine(resource);
        if (navigation.recordHistory !== false) rememberPreviousResource(previousBinding, resource);
        updateBackAction();
        notifyPersistence();
        return rendered;
      } catch (error) {
        if (revision !== previewPanelState.requestRevision) return null;
        previewPanelState.binding = previousBinding;
        surface.setState(window.PortalI18n.text`预览失败：${error?.message || error}`);
        window.PortalUIPrimitives?.renderAsyncState?.(previewRoot, {
          state: "error",
          title: window.PortalI18n.t("Preview 加载失败"),
          message: error?.message || String(error),
          density: "panel",
        });
        throw error;
      }
    };
    const goBack = async () => {
      if (previewPanelState.navigationBusy || previewPanelState.navigationHistory.length === 0) return null;
      const target = previewPanelState.navigationHistory.at(-1);
      previewPanelState.navigationBusy = true;
      updateBackAction();
      try {
        const resource = await restoreNavigationResource(target);
        const rendered = await openResource(resource, { recordHistory: false });
        if (rendered) {
          previewPanelState.navigationHistory.pop();
          notifyPersistence();
        }
        return rendered;
      } finally {
        previewPanelState.navigationBusy = false;
        updateBackAction();
      }
    };

    return {
      clearPreview, goBack, openResource, previewRoot, renderDocument,
    };
  }

  window.PortalWorkspaceSurfacePanelDocument = Object.freeze({ createPanelDocument, dependencies: DEPENDENCIES });
})();
