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

  function createPreview(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreview is missing: ${missing.join(", ")}`);
    const {
      $id, apiGet, apiPost, currentAgentChatPanel, currentAgentChatWorkspace,
      currentLineageWorkbench, currentPersonalWorkspaces, currentResident, ensurePreviewVisible,
      lineageIdForResident, residentService, selectedResidentId, setSideView,
      showPersonalWorkspaces, state, unavailableResidentBindings,
    } = dependencies;

    let previewController = null;

    let previewAuxiliaryController = null;

    let previewTabsController = null;

    let previewEditorTools = null;

    let previewCodeEditor = null;

    let previewCodeEditorGeneration = 0;

    let agentPreviewSource = null;

    let previewSelectionController = null;

    function activePreviewSnapshot() {
      return previewController?.snapshot?.() || emptyPreviewSnapshot;
    }

    function activePreviewPayload() {
      return activePreviewSnapshot().payload;
    }

    let agentPreviewPanelComponent = null;

    function previewRelativeTargetFromLink(href, payload = activePreviewPayload()) {
      if (!payload || !linkLooksLikeRelativeLocalPath(href)) return null;
      if (String(payload.source || "") === "resource") return null;
      const relTarget = normalizeLinkFsPath(href);
      if (!relTarget || relTarget.startsWith("/") || relTarget.startsWith("~")) return null;
      if (String(payload.source || "") === "workspace_link") {
        const basePath = normalizeLinkFsPath(payload.path || "");
        if (!basePath.startsWith("/")) return null;
        const baseDir = basePath.includes("/") ? basePath.split("/").slice(0, -1).join("/") : "";
        return { type: "local", path: normalizeJoinedPath(baseDir, relTarget, true) };
      }
      if (payload.resident_id || payload.directory_id) {
        const directoryId = String(payload.directory_id || residentFileParts(payload.path || "").directoryId || "").trim();
        const relPath = String(payload.rel_path || residentFileParts(payload.path || "").relPath || "").trim();
        if (!directoryId) return null;
        const baseDir = relPath.includes("/") ? relPath.split("/").slice(0, -1).join("/") : "";
        const nextRel = normalizeJoinedPath(baseDir, relTarget, false);
        return { type: "resident", path: nextRel ? `${directoryId}/${nextRel}` : directoryId };
      }
      return null;
    }

    function residentRootsForLinkPreview() {
      const roots = [];
      const seen = new Set();
      const addRoots = list => {
        (Array.isArray(list) ? list : []).forEach(root => {
          const directoryId = String(root?.directory_id || "").trim();
          if (!directoryId || seen.has(directoryId)) return;
          seen.add(directoryId);
          roots.push(root);
        });
      };
      addRoots(state.folder?.roots);
      addRoots(state.detail?.resident?.workspace_roots);
      addRoots(currentResident()?.workspace_roots);
      return roots;
    }

    function primaryResidentRootForLinkPreview(roots = residentRootsForLinkPreview()) {
      const primaryDirectoryId = String(state.folder?.primary_directory_id || "").trim();
      return roots.find(root => String(root?.directory_id || "") === primaryDirectoryId)
        || roots.find(root => root?.is_primary === true)
        || (roots.length === 1 ? roots[0] : null);
    }

    function residentVirtualPathFromLink(href) {
      if (!currentResident()) return "";
      const rawHref = String(href || "").trim();
      if (!rawHref) return "";
      const roots = residentRootsForLinkPreview();
      const rootById = new Map(roots.map(root => [String(root.directory_id || ""), root]));
      const candidates = [];
      const pushCandidate = value => {
        const clean = normalizeLinkFsPath(value);
        if (clean) candidates.push(clean);
      };
      pushCandidate(rawHref);
      try {
        const parsed = new URL(rawHref, window.location.href);
        if (parsed.protocol === "file:") {
          pushCandidate(parsed.pathname);
        } else if (parsed.origin === window.location.origin) {
          pushCandidate(parsed.pathname);
        }
      } catch {}
      for (const candidate of candidates) {
        const noLeadingSlash = candidate.replace(/^\/+/, "");
        const first = noLeadingSlash.split("/", 1)[0] || "";
        if (rootById.has(first)) return noLeadingSlash;
        if (
          !candidate.startsWith("/")
          && linkLooksLikeRelativeLocalPath(candidate)
          && window.PortalPreviewContract?.hasFileShape?.(candidate)
        ) {
          const primaryRoot = primaryResidentRootForLinkPreview(roots);
          const primaryDirectoryId = String(primaryRoot?.directory_id || "").trim();
          const rel = normalizeJoinedPath("", candidate, false);
          if (primaryDirectoryId && rel) return `${primaryDirectoryId}/${rel}`;
        }
        if (!candidate.startsWith("/")) continue;
        // The deepest root holding the path: a Workspace inside the Resident Folder wins.
        const holding = roots
          .map(root => ({ root, rootPath: normalizeLinkFsPath(root.path || "") }))
          .filter(({ rootPath }) => rootPath && (candidate === rootPath || candidate.startsWith(`${rootPath}/`)))
          .sort((a, b) => b.rootPath.length - a.rootPath.length)[0];
        if (holding) {
          const rel = candidate.slice(holding.rootPath.length + 1);
          return rel ? `${holding.root.directory_id}/${rel}` : String(holding.root.directory_id || "");
        }
      }
      return "";
    }

    async function loadLocalLinkPreview(path, label = "", options = {}) {
      const targetPath = String(path || "").trim();
      if (!targetPath) return;
      ensurePreviewVisible();
      stopPreviewRefresh();
      await openAgentPreviewDocument({
        key: `link:${targetPath}`,
        mode: options.mode,
        path: targetPath,
        title: label || fileBaseName(targetPath),
        sourceType: "link",
        locator: options.locator || "",
        line: options.line,
        lineStart: options.lineStart,
        onLoading: () => {
          setPreviewTitle(label || fileBaseName(targetPath));
          $id("previewPath").textContent = targetPath;
          setPreviewOpenInput(targetPath);
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.t("加载中…");
        },
        load: async () => {
          const payload = await window.PortalFileLinks.load(targetPath);
          if (options.previewTitle) payload.preview_title = String(options.previewTitle || "");
          return withInitialPreviewLine(agentPreviewSource.normalize(payload, {
            path: String(payload?.path || targetPath),
            source: "workspace_link",
          }), options);
        },
        onError: err => {
        $id("previewBox").textContent = window.PortalI18n.text`预览失败：${err.message || err}`;
        },
      });
    }

    async function openWorkspaceResource(workspaceId, { isCurrent = () => true } = {}) {
      const resolved = await apiGet(`/api/resources/resolve?address=${encodeURIComponent(workspaceId)}`);
      if (!isCurrent()) return false;
      const path = String(resolved?.record?.attributes?.path || "");
      if (resolved?.resource_type !== "workspace" || !resolved.exists || !path) {
        throw new Error("Workspace 已删除或不可用");
      }
      setSideView("workspaces", { userInitiated: true, openingResource: true });
      currentLineageWorkbench()?.focusPanel("navigation");
      await showPersonalWorkspaces().openResource({ workspaceId, title: resolved.record.title, path });
      return isCurrent();
    }

    async function openResourcePreview(context = {}, options = {}) {
      const address = String(context.address || "").trim();
      if (!address) return false;
      ensurePreviewVisible();
      stopPreviewRefresh();
      const loadingPayload = {
        source: "resource",
        resource_address: address,
        path: address,
        preview_title: address,
        loading: true,
      };
      const result = await openAgentPreviewDocument({
        key: `resource:${address}`,
        mode: context.mode || options.mode,
        path: address,
        title: address,
        sourceType: "resource",
        resourceAddress: address,
        loadingPayload,
        onLoading: () => {
          setPreviewTitle(address);
          $id("previewPath").textContent = address;
          setPreviewOpenInput(address);
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.t("正在读取资源…");
        },
        load: async () => {
          const payload = await (typeof context.load === "function"
            ? context.load()
            : window.PortalResourceChips.loadPreview(address));
          return payload == null ? null : window.PortalPreviewContract.normalizeDocument({
            ...payload,
            source: "resource",
            path: String(payload.path || address),
          });
        },
        onEmpty: () => {
          if (options.navigateOnUnsupported !== false && typeof context.navigate === "function") {
            context.navigate();
          } else {
            $id("previewBox").textContent = window.PortalI18n.t("这个资源没有可在 Preview 中显示的内容。");
          }
        },
        onError: error => {
          setPreviewTitle(address);
          $id("previewPath").textContent = address;
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.text`资源预览失败：${error.message || error}`;
        },
      });
      return result.accepted !== false;
    }

    function openResourceChipPreview(context = {}) {
      return openResourcePreview(context, { navigateOnUnsupported: true });
    }

    async function renderChatLinkPreview(href, label = "", options = {}) {
      const displayHref = safeDecodeLink(href);
      const displayLabel = String(label || "").trim();
      return openAgentPreviewDocument({
        key: `link-card:${href}`,
        mode: options.mode,
        path: displayHref || href,
        title: displayLabel || window.PortalI18n.t("链接"),
        sourceType: "link-card",
        href,
        load: async () => window.PortalPreviewContract.normalizeDocument({
          source: "remote_link_card",
          path: displayHref || href,
          href,
          title: displayLabel || window.PortalI18n.t("链接"),
          kind: "remote-link",
          binary: true,
        }),
      });
    }

    async function openChatMarkdownLink(href, label = "", options = {}) {
      const hrefLocator = String(href || "").trim().match(/(#L\d+(?:-L\d+)?|:\d+(?::\d+)?)$/i)?.[1] || "";
      const previewOptions = {
        ...options,
        locator: options.locator || hrefLocator,
      };
      const resourceAddress = String(
        window.PortalResourceChips?.resourceAddressFromPreviewRequest?.({
          resourceAddress: options.resourceAddress,
          href,
        })
        || "",
      ).trim();
      if (resourceAddress) {
        return openResourcePreview({
          mode: options.mode,
          address: resourceAddress,
          load: () => window.PortalResourceChips.loadPreview(resourceAddress),
          navigate: () => window.location.assign(
            href || `/r/${encodeURIComponent(resourceAddress)}`,
          ),
        }, { navigateOnUnsupported: true });
      }
      const relativeTarget = options.previewPayload
        ? previewRelativeTargetFromLink(href, options.previewPayload)
        : null;
      if (relativeTarget?.type === "local") {
        return loadLocalLinkPreview(relativeTarget.path, label || fileBaseName(relativeTarget.path), previewOptions);
      }
      if (relativeTarget?.type === "resident") {
        return loadResidentFile(relativeTarget.path, previewOptions);
      }
      const targetFile = residentVirtualPathFromLink(href);
      if (targetFile) {
        return loadResidentFile(targetFile, previewOptions);
      }
      const localPath = await localFilePathFromLink(href);
      if (localPath) {
        return loadLocalLinkPreview(localPath, label, previewOptions);
      }
      return renderChatLinkPreview(href, label, previewOptions);
    }

    function bindChatMarkdownLinks(root, previewDocument = null) {
      root?.querySelectorAll(".messageContent a[href], .previewBox.markdownBody a[href]").forEach(anchor => {
        if (anchor.matches(".portalResourceChip")) return;
        if (anchor.dataset.portalResourceLinkBound === "1") return;
        if (anchor.dataset.portalFileLinkBound === "1") return;
        if (anchor.dataset.portalPreviewLinkBound === "1") return;
        if (anchor.dataset.chatLinkPreviewBound === "1") return;
        anchor.dataset.chatLinkPreviewBound = "1";
        anchor.addEventListener("contextmenu", event => {
          const href = anchor.getAttribute("href") || "";
          if (!window.PortalFileLinks.describe(href, { allowAnyLocalPath: true })
            && !linkLooksLikeRelativeLocalPath(href)) return;
          window.PortalPreview.openFileMenu({
            event, trigger: anchor, request: { href, title: anchor.textContent || "" },
            onOpen: request => openChatMarkdownLink(request.href, request.title, {
              ...request, previewPayload: previewDocument,
            }),
          });
        });
        anchor.addEventListener("click", event => {
          if (
            event.button !== 0
            || event.metaKey
            || event.ctrlKey
            || event.shiftKey
            || event.altKey
          ) return;
          event.preventDefault();
          event.stopPropagation();
          const href = anchor.getAttribute("href") || "";
          const descriptor = window.PortalFileLinks?.describe?.(href, { allowAnyLocalPath: true });
          openChatMarkdownLink(href, anchor.textContent || "", {
            previewPayload: previewDocument,
            locator: descriptor?.locator || "",
          });
        });
      });
    }

    function bindWorkspacePreviewHost() {
      currentLineageWorkbench()?.getPanel("preview")?.registerPreviewHost("lineage", async request => {
        if (String(request.locatorKind || "") === "lineage-workspace") {
          const lineageId = String(request.lineageId || "").trim();
          const residentId = String(request.residentId || "").trim();
          const path = String(request.path || "").trim();
          if (!lineageId || !residentId || !path) return false;
          ensurePreviewVisible();
          await openAgentPreviewDocument({
            key: `lineage:${lineageId}:${residentId}:${path}`,
            mode: request.mode,
            path,
            title: request.title || fileBaseName(path),
            sourceType: "lineage-workspace",
            lineageId,
            residentId,
            locator: request.locator || "",
            line: request.line,
            lineStart: request.lineStart,
            reload: request.reload === true,
            load: async () => withInitialPreviewLine(agentPreviewSource.normalize(
              await residentService.file({ residentId, ...residentFilePartsForService(path) }),
              {
                path,
                lineage_id: lineageId,
                residentId,
                source: "lineage-workspace",
              },
            ), request),
          });
          return true;
        }
        await openChatMarkdownLink(
          request.href || request.path || "",
          request.title || "",
          {
            resourceAddress: request.resourceAddress || "",
            mode: request.mode,
            locator: request.locator || "",
            line: request.line,
            lineStart: request.lineStart,
          },
        );
        return true;
      }, { priority: 100 });
    }

    function syncLineageChatWorkspaceProjection(detail) {
      const workspaceItems = Array.isArray(detail?.chat_workspace?.messages)
        ? detail.chat_workspace.messages
        : [];
      state.workspaceMessageItems = workspaceItems.map((item, idx) => ({
        key: window.PortalChatWorkspace.messageIdentity(item),
        item,
        idx,
      }));
    }

    function refreshLineageChatView() {
      const detail = currentAgentChatPanel()?.refreshView?.();
      if (!detail) return null;
      state.detail = detail;
      syncLineageChatWorkspaceProjection(detail);
      return detail;
    }

    function setPreviewTitle(title) {
      const text = String(title || window.PortalI18n.t("预览")).trim() || window.PortalI18n.t("预览");
      const el = $id("previewTitle");
      el.textContent = text;
      el.title = text;
    }

    async function loadFolder() {
      if (currentResident()) {
        const residentId = selectedResidentId();
        if (!residentId || unavailableResidentBindings.has(residentId)) return;
        if (
          state.lineageDeleteSaving
          && lineageIdForResident(currentResident()) === state.lineageDeleteSaving
        ) return;
        const requestSequence = ++state.folderRequestSequence;
        try {
          const folder = await residentService.folder(residentId);
          if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
          state.folder = folder;
          currentPersonalWorkspaces()?.controller.setResident({ id: residentId, roots: folder.roots || [] });
          const first = topLevelResidentFilePath(state.folder, state.selectedFile);
          if (!activePreviewPayload() && !activeAgentPreviewDocument()) {
            if (first) {
              await loadResidentFile(first, {
                activatePreview: false,
                revealPanel: false,
              });
            } else {
              state.selectedFile = "";
              resetAgentPreviewToEmpty();
            }
          }
        } catch (err) {
          if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
          console.warn("Resident workspace metadata unavailable", err);
          state.folder = { resident_id: residentId, roots: [], error: String(err.message || err) };
          currentPersonalWorkspaces()?.controller.setResident({ id: residentId, error: state.folder.error });
        }
        return;
      }
    }

    async function loadResidentFileDocument(path, residentId) {
      const { directoryId, relPath } = residentFileParts(path);
      if (!residentId || !directoryId || !path) return null;
      const payload = await residentService.file({
        residentId,
        directoryId,
        path: relPath,
      });
      if (String(payload?.source || "") === "workspace_link") {
        return agentPreviewSource.normalize(payload, {
          path: String(payload?.path || path),
          source: "workspace_link",
        });
      }
      return agentPreviewSource.normalize(payload, {
        path: String(payload?.path || path),
        residentId,
        directoryId,
        relPath,
        source: "resident",
      });
    }

    async function loadResidentFile(path, options = {}) {
      const residentId = selectedResidentId();
      if (!residentId || !path) return;
      if (options.revealPanel !== false) ensurePreviewVisible();
      const { directoryId, relPath } = residentFileParts(path);
      if (!directoryId) return;
      stopPreviewRefresh();
      await openAgentPreviewDocument({
        key: `resident:${residentId}:${directoryId}:${relPath}`,
        mode: options.mode,
        path,
        title: fileBaseName(path),
        sourceType: "resident",
        residentId,
        directoryId,
        relPath,
        locator: options.locator || "",
        line: options.line,
        lineStart: options.lineStart,
        onLoading: () => {
          state.selectedFile = path;
          setPreviewTitle(fileBaseName(path));
          $id("previewPath").textContent = path;
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.t("加载中…");
        },
        load: async () => withInitialPreviewLine(
          await loadResidentFileDocument(path, residentId),
          options,
        ),
        onError: err => {
          $id("previewBox").textContent = window.PortalI18n.text`预览失败：${err.message || err}`;
        },
      });
    }

    function previewIsEditable(payload = activePreviewPayload()) {
      return Boolean(payload && agentPreviewSource?.canEdit?.(payload));
    }

    function activeAgentPreviewDocument() {
      return previewTabsController?.active?.() || null;
    }

    function agentPreviewSessionIsVisible(documentState) {
      if (documentState) return previewTabsController?.isActive?.(documentState.key) === true;
      return !activeAgentPreviewDocument();
    }

    function focusInitialPreviewLine(request = {}) {
      const lineNumber = initialPreviewLine(request);
      if (!lineNumber) return false;
      const target = $id("previewBox")?.querySelector(`[data-preview-line="${lineNumber}"]`);
      if (!target) return false;
      target.scrollIntoView({ block: "center", inline: "nearest" });
      target.focus({ preventScroll: true });
      return true;
    }

    function createAgentPreviewSession(documentState = null) {
      return currentLineageWorkbench().getPanel("preview").createDocumentSession({
        canEdit: payload => previewIsEditable(payload),
        onStateChange: snapshot => {
          if (agentPreviewSessionIsVisible(documentState)) {
            renderPreviewActions();
          }
          previewTabsController?.render?.();
        },
        onPresent: (payload, _snapshot, meta) => {
          if (payload && agentPreviewSessionIsVisible(documentState)) paintPreview(payload, meta);
        },
        onEditStart: snapshot => {
          if (!agentPreviewSessionIsVisible(documentState)) return;
          renderPreviewEditor();
        },
      });
    }

    function activateAgentPreviewDocument(documentState, snapshot = documentState?.session?.snapshot?.()) {
      if (!documentState?.session) return false;
      previewController = documentState.session;
      stopPreviewRefresh();
      const sourceType = String(documentState.sourceType || "");
      if (
        sourceType === "resident"
        && documentState.residentId === selectedResidentId()
      ) {
        state.selectedFile = documentState.path;
      }
      if (snapshot?.payload) {
        paintPreview(snapshot.payload);
        if (snapshot.editing) renderPreviewEditor();
      } else {
        const path = documentState.title || documentState.path || documentState.key;
        setPreviewTitle(documentState.label || fileBaseName(path));
        $id("previewPath").textContent = path;
        setPreviewOpenInput(path);
        $id("previewBox").className = "previewBox";
        $id("previewBox").textContent = snapshot?.status === "error"
          ? window.PortalI18n.text`预览失败：${snapshot.error || window.PortalI18n.t("无法读取文件")}`
          : window.PortalI18n.t("加载中…");
      }
      previewTabsController?.render?.();
      renderPreviewActions();
      return true;
    }

    function resetAgentPreviewToEmpty() {
      previewAuxiliaryController?.destroy?.();
      previewAuxiliaryController = createAgentPreviewSession();
      previewController = previewAuxiliaryController;
      stopPreviewRefresh();
      setPreviewTitle(window.PortalI18n.t("预览"));
      $id("previewPath").textContent = "";
      setPreviewOpenInput("");
      previewSelectionController?.hide?.();
      $id("previewBox").className = "previewBox";
      $id("previewBox").textContent = window.PortalI18n.t("选择文件后显示内容。");
      previewTabsController?.render?.();
      renderPreviewActions();
    }

    function connectPreviewSurface() {
      agentPreviewPanelComponent = currentLineageWorkbench().getPanel("preview")?.surface || null;
      return agentPreviewPanelComponent;
    }

    function restorePreviewDocumentRequest(locator = {}) {
      const restored = previewPersistenceLocator(locator);
      if (!restored) return null;
      const path = restored.path;
      if (restored.sourceType === "resident") {
        if (!restored.residentId) return null;
        return {
          ...restored,
          load: () => loadResidentFileDocument(path, restored.residentId),
        };
      }
      if (restored.sourceType === "link") {
        return {
          ...restored,
          load: async () => {
            const payload = await window.PortalFileLinks.load(path);
            return agentPreviewSource.normalize(payload, {
              path: String(payload?.path || path),
              source: "workspace_link",
            });
          },
        };
      }
      if (restored.sourceType === "resource") {
        const address = restored.resourceAddress || path;
        return {
          ...restored,
          load: async () => {
            const payload = await window.PortalResourceChips.loadPreview(address);
            return payload == null ? null : window.PortalPreviewContract.normalizeDocument({
              ...payload,
              source: "resource",
              path: String(payload.path || address),
            });
          },
        };
      }
      if (restored.sourceType === "link-card") {
        const href = restored.href || path;
        return {
          ...restored,
          href,
          load: async () => window.PortalPreviewContract.normalizeDocument({
            source: "remote_link_card",
            path,
            href,
            title: restored.title || window.PortalI18n.t("链接"),
            kind: "remote-link",
            binary: true,
          }),
        };
      }
      if (!restored.lineageId || !restored.residentId) return null;
      return {
        ...restored,
        load: async () => agentPreviewSource.normalize(
          await residentService.file({ residentId: restored.residentId, ...residentFilePartsForService(path) }),
          {
            path,
            lineage_id: restored.lineageId,
            residentId: restored.residentId,
            source: "lineage-workspace",
          },
        ),
      };
    }

    function initPreviewController() {
      const previewRuntime = currentLineageWorkbench().getPanel("preview");
      agentPreviewSource = window.PortalAgentPreviewSource.create({
        apiGet,
        apiPost,
        residentService,
        entryVersion: entryMtimeToken,
        linkFileUrl: window.PortalFileLinks.apiUrl,
        resourceLoader: (address, options) => (
          window.PortalResourceChips.loadPreview(address, options)
        ),
        residentFileParts,
        selectedResidentId,
      });
      previewRuntime.setNavigationAdapter({
        id: "lineage-preview",
        serialize: resource => previewPersistenceLocator(resource),
        restore: locator => restorePreviewDocumentRequest(locator),
      });
      previewEditorTools = previewRuntime.createDocumentEditorTools({
        storageKey: window.PortalCompatibility.identifier("preview_word_wrap"),
        defaultWordWrap: true,
      });
      previewAuxiliaryController = createAgentPreviewSession();
      previewController = previewAuxiliaryController;
      previewTabsController = previewRuntime.createDocumentTabsController({
        shell: "#agentPreviewTabsShell",
        root: "#agentPreviewTabs",
        pinButton: "#pinAgentPreviewBtn",
        createDocument: request => ({
          label: String(request.title || request.label || request.path || request.key || "").split("/").pop() || window.PortalI18n.t("预览"),
          title: String(request.path || request.title || request.key || ""),
          path: String(request.path || ""),
          sourceType: String(request.sourceType || ""),
          residentId: String(request.residentId || ""),
          directoryId: String(request.directoryId || ""),
          relPath: String(request.relPath || ""),
          restoreLocator: previewPersistenceLocator(request),
        }),
        createSession: documentState => createAgentPreviewSession(documentState),
        serializeDocument: documentState => documentState.restoreLocator,
        restoreDocument: locator => restorePreviewDocumentRequest(locator),
        label: documentState => documentState.label,
        title: (documentState, snapshot) => (
          snapshot?.payload?.path
          || documentState.title
          || documentState.path
        ),
        onActivate: (documentState, snapshot) => activateAgentPreviewDocument(documentState, snapshot),
        onEmpty: () => resetAgentPreviewToEmpty(),
        onRender: snapshot => {
          const shell = $id("agentPreviewTabsShell");
          if (!snapshot.documents.length && shell) shell.hidden = true;
        },
        onError: error => window.PortalUI.toast(
          window.PortalI18n.text`Preview 操作失败：${error.message || error}`,
          "error",
        ),
      });
      previewTabsController.render();
      previewSelectionController?.destroy?.();
      previewSelectionController = previewRuntime.createPreviewSelectionController({
        root: "#previewBox",
        toolbar: "#previewSelectionToolbar",
        payload: () => activePreviewPayload(),
        path: payload => String(payload?.path || $id("previewPath")?.textContent || "").trim(),
        title: payload => previewTitleForPayload(payload || {}) || window.PortalI18n.t("预览"),
        chatWorkspace: () => currentAgentChatWorkspace(),
        selection: () => previewCodeEditor?.selection?.() || null,
        clearSelection: () => previewCodeEditor?.clearSelection?.(),
        disabled: payload => !payload,
        label: window.PortalI18n.t("添加到当前聊天"),
        ariaLabel: window.PortalI18n.t("把选中的文件内容添加到当前 Chat Workspace"),
        onAdd: (_selection, attachment) => window.PortalUI?.toast?.(
          `已添加 ${attachment?.name || "文件选段"} 到当前消息上下文`,
          "success",
        ),
      });
      applyPreviewWordWrap();
    }

    async function openAgentPreviewDocument(request = {}) {
      return await (previewTabsController
        ? previewTabsController.open(request)
        : previewController.open(request));
    }

    function previewDownloadUrl(payload = activePreviewPayload()) {
      return payload ? agentPreviewSource?.downloadUrl?.(payload) || "" : "";
    }

    const previewRefreshCoordinator = window.PortalRefreshCoordinator.create({
      key: () => previewRefreshKey(),
      interval: false,
      fetch: async ({ force, isCurrent }) => {
        const payload = activePreviewPayload();
        const session = previewController;
        if (!previewCanAutoRefresh(payload, { allowCleanEditor: force })) return null;
        try {
          if (!force) {
            const meta = await fetchPreviewRefreshMeta(payload);
            if (!isCurrent() || !meta || !previewPayloadMtimeToken(meta)
              || previewPayloadMtimeToken(meta) === previewPayloadMtimeToken(payload)) return null;
          }
          const refreshed = await fetchPreviewRefreshPayload(payload);
          return refreshed ? { payload: refreshed, source: payload, session } : null;
        } catch (error) {
          if (isCurrent() && previewController === session && activePreviewPayload() === payload) {
            session.setReadError(error);
          }
          throw error;
        }
      },
      apply: ({ payload, source, session }, { force } = {}) => {
        if (previewController !== session || activePreviewPayload() !== source) return;
        if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
        const editing = activePreviewSnapshot().editing;
        renderPreview(payload, { preserveScroll: true, afterRender: () => {
          if (editing && previewController === session && activePreviewPayload() === payload
            && !activePreviewSnapshot().dirty) enterPreviewEdit();
        } });
      },
      onError: err => console.debug?.("Preview auto refresh failed", err),
    });

    function stopPreviewRefresh() {
      previewRefreshCoordinator.stop();
    }

    function previewRefreshKey(payload = activePreviewPayload()) {
      return payload ? agentPreviewSource?.refreshKey?.(payload) || "" : "";
    }

    function previewPayloadMtimeToken(payload = activePreviewPayload()) {
      return String(entryMtimeToken(payload?.entry || {}) || "");
    }

    function previewCanAutoRefresh(payload = activePreviewPayload(), { allowCleanEditor = false } = {}) {
      const snapshot = activePreviewSnapshot();
      if (!payload || (snapshot.editing && !allowCleanEditor) || snapshot.dirty || snapshot.saving) return false;
      const kind = String(payload.kind || "");
      if (!kind || kind === "directory" || kind === "symlink") return false;
      return !!previewRefreshKey(payload);
    }

    async function fetchPreviewRefreshMeta(payload) {
      return payload ? agentPreviewSource?.fetchMeta?.(payload) || null : null;
    }

    async function fetchPreviewRefreshPayload(payload) {
      return payload ? agentPreviewSource?.reload?.(payload) || null : null;
    }

    function schedulePreviewRefresh(delay = 120) {
      if (!previewCanAutoRefresh()) return;
      previewRefreshCoordinator.start();
      previewRefreshCoordinator.invalidate({ delay });
    }

    async function refreshPreviewIfChanged(options = {}) {
      const force = options?.force === true;
      if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
      previewRefreshCoordinator.start();
      try {
        await previewRefreshCoordinator.refresh({ force });
      } catch (err) {
        if (force) {
          window.PortalUI.toast(window.PortalI18n.text`Preview 刷新失败：${err.message || err}`, "error");
        } else {
          console.debug?.("Preview auto refresh failed", err);
        }
      }
    }

    function previewChangeMatches(detail = {}) {
      if (!previewCanAutoRefresh()) return false;
      if (detail.snapshot_required) return true;
      const payload = activePreviewPayload() || {};
      const domains = new Set(detail.domains || []);
      if (String(payload.source || "") === "workspace_link") {
        return domains.has("resource");
      }
      const residentId = String(payload.resident_id || selectedResidentId() || "");
      const residents = Array.isArray(detail?.changed?.resident)
        ? detail.changed.resident
        : [];
      return Boolean(residentId && residents.includes(residentId));
    }

    function setPreviewOpenInput(value) {
      const input = $id("previewOpenInput");
      if (input) input.value = String(value || "");
    }

    function previewUsesTextLayout(payload = activePreviewPayload()) {
      if (!payload || payload.binary) return false;
      return ["markdown", "json", "jsonl", "text", "code"].includes(String(payload.kind || ""));
    }

    function destroyPreviewCodeEditor() {
      previewCodeEditorGeneration += 1;
      previewCodeEditor?.destroy?.();
      previewCodeEditor = null;
    }

    function applyPreviewWordWrap() {
      previewEditorTools?.applyWordWrap?.({
        root: "#agentPreviewPanel",
        button: "#wrapPreviewBtn",
      });
      previewCodeEditor?.setWordWrap?.(previewEditorTools?.wordWrap?.() !== false);
    }

    function togglePreviewWordWrap() {
      previewEditorTools?.toggleWordWrap?.();
      applyPreviewWordWrap();
    }

    function previewExternalUrl(payload = activePreviewPayload()) {
      if (!payload) return "";
      if (String(payload.source || "") === "remote_link_card") return String(payload.href || payload.path || "");
      return window.PortalPreviewRenderers.previewUrl(payload, {
        rawUrl: current => agentPreviewSource?.rawUrl?.(current) || "",
      });
    }

    function openPreviewInNewTab() {
      const payload = activePreviewPayload();
      if (previewUsesTextLayout(payload)) {
        return window.PortalPreviewRenderers.openRenderedDocument($id("previewBox"), {
          title: previewTitleForPayload(payload),
        });
      }
      const url = previewExternalUrl();
      if (!url) return false;
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }

    function renderPreviewActions() {
      agentPreviewPanelComponent?.setReadError(activePreviewSnapshot().readError);
      const editBtn = $id("editPreviewBtn");
      const visualBtn = $id("visualPreviewBtn");
      const saveBtn = $id("savePreviewBtn");
      const undoBtn = $id("undoPreviewBtn");
      const redoBtn = $id("redoPreviewBtn");
      const wrapBtn = $id("wrapPreviewBtn");
      const externalBtn = $id("openPreviewExternalBtn");
      const refreshBtn = $id("refreshPreviewBtn");
      const panelActive = false;
      const action = previewController?.actionState?.() || {
        hasPayload: !!activePreviewPayload(),
        loading: false,
        editable: previewIsEditable(),
        editing: activePreviewSnapshot().editing,
        dirty: activePreviewSnapshot().dirty,
        saving: activePreviewSnapshot().saving,
        canEdit: previewIsEditable() && !activePreviewSnapshot().saving,
        canSave: activePreviewSnapshot().dirty && !activePreviewSnapshot().saving,
      };
      const downloadUrl = previewDownloadUrl();
      agentPreviewPanelComponent?.setDownload?.(panelActive ? "" : downloadUrl);
      const hasPayload = action.hasPayload ?? !!activePreviewPayload();
      const externalUrl = previewExternalUrl();
      if (refreshBtn) {
        refreshBtn.disabled = (
          panelActive
          || !hasPayload
          || !previewRefreshKey()
          || action.loading
          || action.dirty
          || action.saving
        );
      }
      editBtn.disabled = panelActive || !action.canEdit;
      editBtn.classList.toggle("active", action.editing);
      editBtn.setAttribute("aria-pressed", action.editing ? "true" : "false");
      visualBtn.disabled = panelActive || !hasPayload || action.saving;
      visualBtn.classList.toggle("active", !action.editing);
      visualBtn.setAttribute("aria-pressed", action.editing ? "false" : "true");
      saveBtn.disabled = panelActive || !action.canSave;
      undoBtn.disabled = panelActive || !action.editing || action.saving || !previewCodeEditor;
      redoBtn.disabled = panelActive || !action.editing || action.saving || !previewCodeEditor;
      wrapBtn.disabled = panelActive || !previewUsesTextLayout();
      externalBtn.disabled = panelActive || action.saving || !(previewUsesTextLayout() || externalUrl);
      const previewPath = hasPayload
        ? String(activePreviewPayload()?.path || activePreviewPayload()?.resourceAddress || "")
        : "";
      agentPreviewPanelComponent?.setPath?.(previewPath);
      applyPreviewWordWrap();
      previewTabsController?.render?.();
    }

    function renderPreview(payload, options = {}) {
      if (previewController) return previewController.present(payload, options);
      return paintPreview(payload, options);
    }

    function paintPreview(payload, options = {}) {
      destroyPreviewCodeEditor();
      const box = $id("previewBox");
      const preserveScroll = options.preserveScroll === true;
      const previousScrollTop = box ? box.scrollTop : 0;
      const restorePreviewScroll = () => {
        if (!preserveScroll || !box) return;
        window.requestAnimationFrame(() => {
          box.scrollTop = previousScrollTop;
        });
      };
      previewSelectionController?.hide?.();
      setPreviewTitle(previewTitleForPayload(payload));
      $id("previewPath").textContent = payload.path || window.PortalI18n.t("预览");
      setPreviewOpenInput(payload.path || "");
      const renderPromise = currentLineageWorkbench()?.getPanel("preview")?.setDocument(payload);
      previewTabsController?.render?.();
      applyPreviewWordWrap();
      renderPreviewActions();
      Promise.resolve(renderPromise)
        .then(() => {
          restorePreviewScroll();
          if (!preserveScroll) focusInitialPreviewLine(payload);
          options.afterRender?.();
        })
        .catch(error => window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("预览"), error), "error"));
      return renderPromise;
    }

    async function renderPreviewEditor() {
      const current = previewController?.snapshot?.();
      const payload = current?.payload || activePreviewPayload();
      if (!previewIsEditable(payload)) return;
      destroyPreviewCodeEditor();
      const generation = previewCodeEditorGeneration;
      const session = previewController;
      const box = $id("previewBox");
      box.className = "previewBox editing";
      box.innerHTML = window.PortalI18n.markup('<div class="previewMonacoEditor" id="previewMonacoEditor"></div>');
      applyPreviewWordWrap();
      renderPreviewActions();
      try {
        const editor = await window.PortalCodeEditor.mount({
          root: "#previewMonacoEditor",
          statusBar: true,
          path: payload.path || payload.rel_path || "",
          value: current?.draft ?? activePreviewSnapshot().draft,
          wordWrap: previewEditorTools?.wordWrap?.() !== false,
          onSelectionChange: () => previewSelectionController?.show?.(),
          onChange: value => {
            if (previewController !== session) return;
            session?.updateDraft?.(value);
          },
          onSave: () => {
            if (previewController === session) savePreviewEdit();
          },
        });
        if (
          generation !== previewCodeEditorGeneration
          || previewController !== session
          || !session?.snapshot?.().editing
        ) {
          editor.destroy();
          return;
        }
        previewCodeEditor = editor;
        editor.focus();
        renderPreviewActions();
      } catch (error) {
        if (generation !== previewCodeEditorGeneration) return;
        box.className = "previewBox";
        box.textContent = window.PortalI18n.text`编辑器加载失败：${error.message || error}`;
        window.PortalUI.toast(window.PortalI18n.t("编辑器加载失败，请重试。"), "error");
        renderPreviewActions();
      }
    }

    function enterPreviewEdit() {
      if (!previewIsEditable()) return;
      if (!previewController.snapshot().editing) previewController.beginEdit();
    }

    function cancelPreviewEdit() {
      return previewController?.cancelEdit?.();
    }

    async function setPreviewMode(mode) {
      const editing = !!previewController?.snapshot?.().editing;
      if (mode === "code") {
        if (!editing) enterPreviewEdit();
        return;
      }
      if (mode === "visual" && editing) await cancelPreviewEdit();
    }

    function runPreviewEditorCommand(command) {
      if (!previewCodeEditor || !["undo", "redo"].includes(command)) return false;
      previewCodeEditor[command]?.();
      return true;
    }

    async function savePreviewEdit() {
      const result = await previewController.save({
        save: ({ payload, draft }) => agentPreviewSource.save(payload, draft),
        onSaved: async updated => {
        if (String(updated?.source || "") !== "workspace_link") await loadFolder();
        },
        onError: err => window.PortalUI.toast(window.PortalUI.errorMessage("save", err), "error"),
      });
      return result;
    }

    return Object.freeze({
      activePreviewPayload, loadLocalLinkPreview, openWorkspaceResource, openResourceChipPreview,
      bindChatMarkdownLinks, bindWorkspacePreviewHost, syncLineageChatWorkspaceProjection,
      refreshLineageChatView, setPreviewTitle, loadFolder, activeAgentPreviewDocument,
      connectPreviewSurface, initPreviewController, previewRefreshCoordinator,
      stopPreviewRefresh, schedulePreviewRefresh, refreshPreviewIfChanged, previewChangeMatches,
      setPreviewOpenInput, togglePreviewWordWrap, openPreviewInNewTab, renderPreviewActions,
      setPreviewMode, runPreviewEditorCommand, savePreviewEdit,
      currentPreviewTabsController: () => previewTabsController,
      currentPreviewSelectionController: () => previewSelectionController,
      currentAgentPreviewSource: () => agentPreviewSource,
      currentAgentPreviewPanelComponent: () => agentPreviewPanelComponent,
    });
  }

  window.PortalLineageWorkbenchPreview = Object.freeze({
    createPreview, dependencies: DEPENDENCIES,
  });
})();
