(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreview) return;

  // The Preview panel: Resident files and folders, resources and chat links opened
  // in it, its documents and tabs, the editor for text files, the refresh of a
  // changed file, and the saved location of the open document.
  const DEPENDENCIES = Object.freeze([
    "$id", "apiGet", "apiPost", "currentAgentChatPanel", "currentAgentChatWorkspace",
    "currentLineageWorkbench", "currentPersonalWorkspaces", "currentResident",
    "ensurePreviewVisible", "lineageIdForResident", "residentService", "selectedResidentId",
    "setSideView", "showPersonalWorkspaces", "state", "unavailableResidentBindings",
  ]);

  const emptyPreviewSnapshot = Object.freeze({
    status: "empty",
    payload: null,
    requestId: 0,
    editing: false,
    draft: "",
    dirty: false,
    saving: false,
  });

  function safeDecodeLink(value) {
    try {
      return decodeURI(String(value || ""));
    } catch {
      return String(value || "");
    }
  }

  function linkPathWithoutQuery(value) {
    return String(value || "").split("#", 1)[0].split("?", 1)[0].trim();
  }

  function stripLocalLinkLineSuffix(value) {
    const text = String(value || "").trim();
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(text) && !text.startsWith("file:")) return text;
    return text.replace(/:\d+(?::\d+)?$/, "");
  }

  function normalizeLinkFsPath(value) {
    return stripLocalLinkLineSuffix(linkPathWithoutQuery(safeDecodeLink(value))).replace(/\\/g, "/").replace(/\/+$/, "");
  }

  function linkLooksLikeRelativeLocalPath(href) {
    const raw = String(href || "").trim();
    if (!raw || raw.startsWith("#") || raw.startsWith("//")) return false;
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return false;
    return !raw.startsWith("/");
  }

  function normalizeJoinedPath(baseDir, relPath, absolute = false) {
    const parts = [];
    const base = String(baseDir || "").replace(/\\/g, "/");
    const rel = String(relPath || "").replace(/\\/g, "/");
    `${base}/${rel}`.split("/").forEach(part => {
      if (!part || part === ".") return;
      if (part === "..") {
        parts.pop();
        return;
      }
      parts.push(part);
    });
    return `${absolute ? "/" : ""}${parts.join("/")}`;
  }

  async function localFilePathFromLink(href, options = {}) {
    const rawHref = String(href || "").trim();
    if (!rawHref) return "";
    const locator = window.PortalFileLinks?.classifyLocator?.(rawHref);
    if (locator?.kind === "local" && locator.path) {
      if (options.explicitPath === true || locator.requiresProbe !== true) return locator.path;
      return await window.PortalFileLinks.probe(locator.path) ? locator.path : "";
    }
    if (options.explicitPath !== true) return "";
    const normalized = window.PortalFileLinks?.normalizePath?.(rawHref) || "";
    return normalized.startsWith("/") || normalized.startsWith("~/") ? normalized : "";
  }

  function fileBaseName(path) {
    const clean = String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
    return clean.split("/").filter(Boolean).pop() || clean || "预览";
  }

  function markdownDocumentTitle(content) {
    for (const raw of String(content || "").split(/\r?\n/).slice(0, 80)) {
      const match = raw.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
      if (match?.[1]) return match[1].trim();
    }
    return "";
  }

  function previewTitleForPayload(payload) {
    if (!payload) return window.PortalI18n.t("预览");
    if (payload.preview_title) return String(payload.preview_title || "");
    if (payload.title) return String(payload.title || "");
    if (payload.knowledge_item?.title) return String(payload.knowledge_item.title || "");
    const fallback = payload.entry?.label || payload.entry?.name || fileBaseName(payload.path);
    if (String(payload.kind || "") === "markdown") {
      return markdownDocumentTitle(payload.content) || fallback;
    }
    return fallback;
  }

  function residentEntryCanAutoPreview(item) {
    if (!item || item.type === "directory" || item.type === "symlink") return false;
    const name = String(item.name || item.label || "").trim().toLowerCase();
    if (!name || name.startsWith("._")) return false;
    return !new Set([".ds_store", ".localized", "thumbs.db", "desktop.ini"]).has(name);
  }

  function topLevelResidentFilePath(folder, preferredPath = "") {
    const roots = folder?.roots || [];
    const preferred = String(preferredPath || "");
    if (preferred) {
      for (const rootInfo of roots) {
        const item = (rootInfo.entries || []).find(entry => (
          String(entry.path || "") === preferred && residentEntryCanAutoPreview(entry)
        ));
        if (item) return item.path || "";
      }
    }
    for (const rootInfo of folder?.roots || []) {
      if (String(rootInfo.root_kind || "") !== "resident_folder") continue;
      for (const item of rootInfo.entries || []) {
        if (
          Number(item.depth || 0) === 0
          && residentEntryCanAutoPreview(item)
          && String(item.name || item.label || "").toLowerCase() === "readme.md"
        ) {
          return item.path || "";
        }
      }
    }
    for (const rootInfo of roots) {
      for (const item of rootInfo.entries || []) {
        if (Number(item.depth || 0) === 0 && residentEntryCanAutoPreview(item)) {
          return item.path || "";
        }
      }
    }
    return "";
  }

  function residentFileParts(virtualPath) {
    const clean = String(virtualPath || "").replace(/\\/g, "/").replace(/^\/+/, "");
    const parts = clean.split("/").filter(Boolean);
    const directoryId = parts.shift() || "";
    return { directoryId, relPath: parts.join("/") };
  }

  function residentFilePartsForService(virtualPath) {
    const { directoryId, relPath } = residentFileParts(virtualPath);
    return { directoryId, path: relPath };
  }

  function initialPreviewLine(request = {}) {
    const explicit = Number(request.lineStart ?? request.line);
    if (Number.isInteger(explicit) && explicit > 0) return explicit;
    const match = String(request.locator || "").trim().match(/(?:#L|:)(\d+)/i);
    const parsed = Number(match?.[1] || 0);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
  }

  function withInitialPreviewLine(payload, request = {}) {
    const lineStart = initialPreviewLine(request);
    return payload && lineStart ? { ...payload, lineStart } : payload;
  }

  function previewPersistenceLocator(request = {}) {
    const embedded = request?.document || request?.payload || request?.request || {};
    const sourceValue = String(
      request.sourceType
      || embedded.sourceType
      || embedded.previewSource
      || embedded.source
      || "",
    ).trim();
    const sourceType = ({
      workspace_link: "link",
      remote_link_card: "link-card",
    })[sourceValue] || sourceValue;
    const supported = new Set([
      "resident",
      "link",
      "resource",
      "link-card",
      "lineage-workspace",
    ]);
    if (!supported.has(sourceType)) return null;
    const path = String(request.path || embedded.path || "").trim();
    const parts = residentFileParts(path);
    const locator = {
      key: String(request.key || embedded.key || "").trim(),
      path,
      title: String(request.title || request.label || embedded.title || embedded.label || path || "").trim(),
      sourceType,
    };
    locator.residentId = String(
      request.residentId || request.resident_id || embedded.residentId || embedded.resident_id || "",
    ).trim();
    locator.directoryId = String(
      request.directoryId || request.directory_id || embedded.directoryId || embedded.directory_id || parts.directoryId || "",
    ).trim();
    locator.relPath = String(
      request.relPath || request.rel_path || embedded.relPath || embedded.rel_path || parts.relPath || "",
    ).trim();
    locator.lineageId = String(
      request.lineageId || request.lineage_id || embedded.lineageId || embedded.lineage_id || "",
    ).trim();
    locator.href = String(request.href || embedded.href || "").trim();
    locator.resourceAddress = String(
      request.resourceAddress
      || request.resource_address
      || embedded.resourceAddress
      || embedded.resource_address
      || embedded.address
      || "",
    ).trim();
    Object.keys(locator).forEach(key => {
      if (locator[key] === "") delete locator[key];
    });
    if (!locator.key && path) {
      if (sourceType === "resident") locator.key = `resident:${locator.residentId}:${locator.directoryId}:${locator.relPath}`;
      else if (sourceType === "resource") locator.key = `resource:${locator.resourceAddress || path}`;
      else if (sourceType === "link-card") locator.key = `link-card:${locator.href || path}`;
      else if (sourceType === "lineage-workspace") locator.key = `lineage:${locator.lineageId}:${locator.residentId}:${path}`;
      else locator.key = `link:${path}`;
    }
    return locator.key && locator.path ? locator : null;
  }

  function entryMtimeToken(entry) {
    return entry?.mtime_ns_text || entry?.mtime_ns_token || entry?.mtime_ns || 0;
  }

  // createPreview builds the Preview panel. Its parts live in module scripts
  // that load before this one; previewPart(name) returns what a script
  // registers:
  //   lineage_workbench_preview_documents.js  the document sessions and tabs,
  //     restoring a saved document and opening one
  //   lineage_workbench_preview_refresh.js  refreshing the open document when
  //     its file changes
  //   lineage_workbench_preview_rendering.js  painting a document and the
  //     panel's title, path and actions
  //   lineage_workbench_preview_editing.js  the code editor and saving
  //   lineage_workbench_preview_files.js  the selected Resident's folder and
  //     files
  //   lineage_workbench_preview_links.js  where a link points; opening local
  //     files, Workspaces and resources
  //   lineage_workbench_preview_chat_links.js  links in Chat messages, the
  //     Preview host and the Chat view's workspace messages
  // createPreview holds the state the parts share, creates the parts in that
  // order and returns the panel's functions; a part that calls a function of a
  // part created after it gets an arrow forwarding the call.
  function previewPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

  function createPreview(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreview is missing: ${missing.join(", ")}`);
    const {
      $id, apiGet, apiPost, currentAgentChatPanel, currentAgentChatWorkspace,
      currentLineageWorkbench, currentPersonalWorkspaces, currentResident, ensurePreviewVisible,
      lineageIdForResident, residentService, selectedResidentId, setSideView,
      showPersonalWorkspaces, state, unavailableResidentBindings,
    } = dependencies;

    const previewState = {
      previewController: null, previewTabsController: null, previewEditorTools: null, previewCodeEditor: null,
      agentPreviewSource: null, previewSelectionController: null, agentPreviewPanelComponent: null,
    };

    const {
      activeAgentPreviewDocument, activePreviewPayload, activePreviewSnapshot, connectPreviewSurface,
      focusInitialPreviewLine, initPreviewController, openAgentPreviewDocument, previewDownloadUrl, previewIsEditable,
      resetAgentPreviewToEmpty,
    } = previewPart("PortalLineageWorkbenchPreviewDocuments").createPreviewDocuments({
      $id, apiGet, apiPost, applyPreviewWordWrap: (...args) => applyPreviewWordWrap(...args),
      currentAgentChatWorkspace, currentLineageWorkbench, emptyPreviewSnapshot, entryMtimeToken, fileBaseName,
      initialPreviewLine, loadResidentFileDocument: (...args) => loadResidentFileDocument(...args),
      paintPreview: (...args) => paintPreview(...args), previewPersistenceLocator, previewState,
      previewTitleForPayload, renderPreviewActions: (...args) => renderPreviewActions(...args),
      renderPreviewEditor: (...args) => renderPreviewEditor(...args), residentFileParts, residentFilePartsForService,
      residentService, selectedResidentId, setPreviewOpenInput: (...args) => setPreviewOpenInput(...args),
      setPreviewTitle: (...args) => setPreviewTitle(...args), state,
      stopPreviewRefresh: (...args) => stopPreviewRefresh(...args),
    });
    const {
      previewChangeMatches, previewRefreshCoordinator, previewRefreshKey, refreshPreviewIfChanged,
      schedulePreviewRefresh, stopPreviewRefresh,
    } = previewPart("PortalLineageWorkbenchPreviewRefresh").createPreviewRefresh({
      activePreviewPayload, activePreviewSnapshot, enterPreviewEdit: (...args) => enterPreviewEdit(...args),
      entryMtimeToken, previewState, renderPreview: (...args) => renderPreview(...args), selectedResidentId,
    });
    const {
      applyPreviewWordWrap, openPreviewInNewTab, paintPreview, renderPreview, renderPreviewActions,
      setPreviewOpenInput, setPreviewTitle, togglePreviewWordWrap,
    } = previewPart("PortalLineageWorkbenchPreviewRendering").createPreviewRendering({
      $id, activePreviewPayload, activePreviewSnapshot, currentLineageWorkbench,
      destroyPreviewCodeEditor: (...args) => destroyPreviewCodeEditor(...args), focusInitialPreviewLine,
      previewDownloadUrl, previewIsEditable, previewRefreshKey, previewState, previewTitleForPayload,
    });
    const {
      destroyPreviewCodeEditor, enterPreviewEdit, renderPreviewEditor, runPreviewEditorCommand, savePreviewEdit,
      setPreviewMode,
    } = previewPart("PortalLineageWorkbenchPreviewEditing").createPreviewEditing({
      $id, activePreviewPayload, activePreviewSnapshot, applyPreviewWordWrap,
      loadFolder: (...args) => loadFolder(...args), previewIsEditable, previewState, renderPreviewActions,
    });
    const {
      loadFolder, loadResidentFile, loadResidentFileDocument,
    } = previewPart("PortalLineageWorkbenchPreviewFiles").createPreviewFiles({
      $id, activeAgentPreviewDocument, activePreviewPayload, currentPersonalWorkspaces, currentResident,
      ensurePreviewVisible, fileBaseName, lineageIdForResident, openAgentPreviewDocument, previewState,
      resetAgentPreviewToEmpty, residentFileParts, residentService, selectedResidentId, setPreviewTitle, state,
      stopPreviewRefresh, topLevelResidentFilePath, unavailableResidentBindings, withInitialPreviewLine,
    });
    const {
      loadLocalLinkPreview, openResourceChipPreview, openResourcePreview, openWorkspaceResource,
      previewRelativeTargetFromLink, residentVirtualPathFromLink,
    } = previewPart("PortalLineageWorkbenchPreviewLinks").createPreviewLinks({
      $id, activePreviewPayload, apiGet, currentLineageWorkbench, currentResident, ensurePreviewVisible, fileBaseName,
      linkLooksLikeRelativeLocalPath, normalizeJoinedPath, normalizeLinkFsPath, openAgentPreviewDocument,
      previewState, residentFileParts, setPreviewOpenInput, setPreviewTitle, setSideView, showPersonalWorkspaces,
      state, stopPreviewRefresh, withInitialPreviewLine,
    });
    const {
      bindChatMarkdownLinks, bindWorkspacePreviewHost, refreshLineageChatView, syncLineageChatWorkspaceProjection,
    } = previewPart("PortalLineageWorkbenchPreviewChatLinks").createPreviewChatLinks({
      currentAgentChatPanel, currentLineageWorkbench, ensurePreviewVisible, fileBaseName,
      linkLooksLikeRelativeLocalPath, loadLocalLinkPreview, loadResidentFile, localFilePathFromLink,
      openAgentPreviewDocument, openResourcePreview, previewRelativeTargetFromLink, previewState,
      residentFilePartsForService, residentService, residentVirtualPathFromLink, safeDecodeLink, state,
      withInitialPreviewLine,
    });

    return Object.freeze({
      activePreviewPayload, loadLocalLinkPreview, openWorkspaceResource, openResourceChipPreview,
      bindChatMarkdownLinks, bindWorkspacePreviewHost, syncLineageChatWorkspaceProjection,
      refreshLineageChatView, setPreviewTitle, loadFolder, activeAgentPreviewDocument,
      connectPreviewSurface, initPreviewController, previewRefreshCoordinator,
      stopPreviewRefresh, schedulePreviewRefresh, refreshPreviewIfChanged, previewChangeMatches,
      setPreviewOpenInput, togglePreviewWordWrap, openPreviewInNewTab, renderPreviewActions,
      setPreviewMode, runPreviewEditorCommand, savePreviewEdit,
      currentPreviewTabsController: () => previewState.previewTabsController,
      currentPreviewSelectionController: () => previewState.previewSelectionController,
      currentAgentPreviewSource: () => previewState.agentPreviewSource,
      currentAgentPreviewPanelComponent: () => previewState.agentPreviewPanelComponent,
    });
  }

  window.PortalLineageWorkbenchPreview = Object.freeze({
    createPreview, dependencies: DEPENDENCIES,
  });
})();
