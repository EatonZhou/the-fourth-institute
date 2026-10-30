(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewLinks) return;

  // Where a link points: a file next to the open document or in one of the
  // Resident's roots; opening local files, Workspaces and resources in the
  // Preview.

  const DEPENDENCIES = Object.freeze([
    "$id", "activePreviewPayload", "apiGet", "currentLineageWorkbench", "currentResident", "ensurePreviewVisible",
    "fileBaseName", "linkLooksLikeRelativeLocalPath", "normalizeJoinedPath", "normalizeLinkFsPath",
    "openAgentPreviewDocument", "previewState", "residentFileParts", "setPreviewOpenInput", "setPreviewTitle",
    "setSideView", "showPersonalWorkspaces", "state", "stopPreviewRefresh", "withInitialPreviewLine",
  ]);

  function createPreviewLinks(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewLinks is missing: ${missing.join(", ")}`);
    const {
      $id, activePreviewPayload, apiGet, currentLineageWorkbench, currentResident, ensurePreviewVisible, fileBaseName,
      linkLooksLikeRelativeLocalPath, normalizeJoinedPath, normalizeLinkFsPath, openAgentPreviewDocument,
      previewState, residentFileParts, setPreviewOpenInput, setPreviewTitle, setSideView, showPersonalWorkspaces,
      state, stopPreviewRefresh, withInitialPreviewLine,
    } = dependencies;

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
          return withInitialPreviewLine(previewState.agentPreviewSource.normalize(payload, {
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

    return {
      loadLocalLinkPreview, openResourceChipPreview, openResourcePreview, openWorkspaceResource,
      previewRelativeTargetFromLink, residentVirtualPathFromLink,
    };
  }

  window.PortalLineageWorkbenchPreviewLinks = Object.freeze({ createPreviewLinks, dependencies: DEPENDENCIES });
})();
