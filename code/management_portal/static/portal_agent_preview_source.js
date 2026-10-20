(() => {
  "use strict";

  if (window.PortalAgentPreviewSource) return;

  function create(options = {}) {
    const apiGet = options.apiGet;
    const apiPost = options.apiPost;
    if (typeof apiGet !== "function" || typeof apiPost !== "function") {
      throw new TypeError("PortalAgentPreviewSource requires apiGet and apiPost");
    }
    const residentService = options.residentService || window.PortalResidentService;
    if (!residentService) {
      throw new TypeError("PortalAgentPreviewSource requires PortalResidentService");
    }
    const resourceLoader = options.resourceLoader || window.PortalResourceChips?.loadPreview;
    const selectedResidentId = () => String(options.selectedResidentId?.() || "").trim();
    const residentParts = path => options.residentFileParts?.(path) || { directoryId: "", relPath: "" };
    const entryVersion = entry => String(options.entryVersion?.(entry) || entry?.mtime_ns || entry?.mtime || "");
    const versioned = (url, document) => {
      const clean = String(url || "").trim();
      const version = entryVersion(document?.entry);
      if (!clean || !version) return clean;
      return `${clean}${clean.includes("?") ? "&" : "?"}_preview_mtime=${encodeURIComponent(version)}`;
    };
    const asDownload = url => {
      const clean = String(url || "").trim();
      if (!clean || /(?:\?|&)download(?:=|&|$)/.test(clean)) return clean;
      const hashIndex = clean.indexOf("#");
      const base = hashIndex >= 0 ? clean.slice(0, hashIndex) : clean;
      const hash = hashIndex >= 0 ? clean.slice(hashIndex) : "";
      return `${base}${base.includes("?") ? "&" : "?"}download=1${hash}`;
    };
    const sourceType = document => String(
      document?.previewSource
      || document?.source
      || "",
    ).trim();
    const versionToken = value => {
      if (value && typeof value === "object") {
        return String(value.number ?? value.revision ?? "").trim();
      }
      return String(value ?? "").trim();
    };

    function normalize(payload = {}, defaults = {}) {
      const source = sourceType(payload) || String(defaults.source || "");
      const document = {
        ...defaults,
        ...payload,
        source: source === "link" ? "workspace_link" : source,
        previewSource: source,
        path: String(payload.path || defaults.path || ""),
        resident_id: String(payload.resident_id || defaults.residentId || ""),
        directory_id: String(payload.directory_id || defaults.directoryId || ""),
        rel_path: String(payload.rel_path || defaults.relPath || ""),
        version: versionToken(payload.version || entryVersion(payload.entry)),
      };
      document.rawUrl = rawUrl(document);
      document.downloadUrl = downloadUrl(document);
      document.editable = canEdit(document);
      document.capabilities = {
        ...(document.capabilities && typeof document.capabilities === "object"
          ? document.capabilities
          : {}),
        download: Boolean(document.downloadUrl),
        edit: document.editable,
        refresh: Boolean(refreshKey(document)),
        save: document.editable,
      };
      return window.PortalPreviewContract.normalizeDocument(document);
    }

    function rawUrl(document = {}) {
      if (document.rawUrl || document.raw_url) return versioned(document.raw_url || document.rawUrl, document);
      if (String(document.source || "") === "resource") return String(document.content_url || "");
      if (String(document.source || "") === "workspace_link") {
        const path = String(document.path || "").trim();
        return path ? versioned(`/api/agent-workbench/link-file-raw?path=${encodeURIComponent(path)}`, document) : "";
      }
      if (sourceType(document) === "lineage-workspace") {
        return versioned(String(document.raw_url || ""), document);
      }
      return "";
    }

    function downloadUrl(document = {}) {
      if (["directory", "symlink"].includes(String(document.kind || ""))) return "";
      if (document.downloadUrl || document.download_url) return String(document.download_url || document.downloadUrl);
      if (String(document.source || "") === "resource") return asDownload(document.content_url);
      if (String(document.source || "") === "workspace_link") {
        const path = String(document.path || "").trim();
        return window.PortalFileLinks.downloadUrl(path);
      }
      return "";
    }

    function canEdit(document = {}) {
      if (String(document.source || "") === "resource") {
        const capabilities = document.capabilities && typeof document.capabilities === "object"
          ? document.capabilities
          : {};
        return capabilities.edit === true && capabilities.save === true;
      }
      const kind = window.PortalPreviewContract.normalizeKind(document);
      if (!["markdown", "json", "jsonl", "text", "code"].includes(kind) || document.binary || document.truncated) return false;
      // The save button runs UpdateWorkspaceFile; the preview says whether this caller may run it on this file.
      return Boolean(workspaceFileAddress(document)) && document.allows?.UpdateWorkspaceFile === true;
    }

    function refreshKey(document = {}) {
      if (!document) return "";
      if (String(document.source || "") === "resource") {
        const address = String(
          document.resource_address
          || document.address
          || document.resource_id
          || "",
        ).trim();
        return address ? `resource:${address}` : "";
      }
      if (String(document.source || "") === "workspace_link") return `link:${String(document.path || "")}`;
      if (document.resident_id || document.directory_id || sourceType(document) === "resident") {
        return `resident:${String(document.resident_id || selectedResidentId())}:${String(document.directory_id || "")}:${String(document.rel_path || "")}`;
      }
      return "";
    }

    async function fetchMeta(document = {}) {
      if (String(document.source || "") === "workspace_link") {
        const path = String(document.path || "").trim();
        return path ? apiGet(options.linkFileUrl(path, { meta: true })) : null;
      }
      if (document.resident_id || document.directory_id || sourceType(document) === "resident") {
        const parts = residentParts(document.path || "");
        const residentId = String(document.resident_id || selectedResidentId()).trim();
        const directoryId = String(document.directory_id || parts.directoryId || "").trim();
        const relPath = String(document.rel_path || parts.relPath || "").trim();
        return residentId && directoryId && relPath
          ? residentService.file({ residentId, directoryId, path: relPath, metadata: true })
          : null;
      }
      return null;
    }

    async function reload(document = {}) {
      if (String(document.source || "") === "resource") {
        const address = String(
          document.resource_address
          || document.address
          || document.resource_id
          || "",
        ).trim();
        if (!address || typeof resourceLoader !== "function") return null;
        const payload = await resourceLoader(address, { allowCached: false });
        return payload == null ? null : normalize(payload, {
          ...document,
          source: "resource",
          resource_address: address,
          path: address,
        });
      }
      if (String(document.source || "") === "workspace_link") {
        const path = String(document.path || "").trim();
        if (!path) return null;
        return normalize(await apiGet(options.linkFileUrl(path)), { ...document, source: "workspace_link" });
      }
      if (document.resident_id || document.directory_id || sourceType(document) === "resident") {
        const parts = residentParts(document.path || "");
        const residentId = String(document.resident_id || selectedResidentId()).trim();
        const directoryId = String(document.directory_id || parts.directoryId || "").trim();
        const relPath = String(document.rel_path || parts.relPath || "").trim();
        if (!residentId || !directoryId || !relPath) return null;
        return normalize(await residentService.file({ residentId, directoryId, path: relPath }), {
          ...document, source: "resident", residentId, directoryId, relPath,
        });
      }
      return null;
    }

    // Content digest used as the file's version token (UpdateWorkspaceFile).
    async function contentDigest(text) {
      const bytes = new TextEncoder().encode(String(text ?? ""));
      const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    }

    // UpdateWorkspaceFile takes a Workspace address, <workspace_id>/file/<relative path>.
    function workspaceFileAddress(document) {
      // A link file is editable only through the Workspace address the server gives it.
      if (String(document.source || "") === "workspace_link") return String(document.file_address || "");
      const workspaceId = String(document.workspace_id || document.directory_id || "").trim();
      const relPath = String(document.rel_path || "").replace(/^\/+/, "");
      return /^workspace_[0-9a-f]{12}$/.test(workspaceId) && relPath ? `${workspaceId}/file/${relPath}` : "";
    }

    async function saveFile(address, document, draft) {
      if (!address) throw new Error("文件路径无效");
      const expected = await contentDigest(document.content);
      const result = await window.PortalInstructionClient.execute("UpdateWorkspaceFile", { address, content: draft }, {
        idempotencyKey: `workspace-file:${globalThis.PortalUIPrimitives.randomUUID()}`,
        expectedVersion: { [address]: expected },
      });
      return { content: draft, version: result?.version, modified_at: result?.modified_at };
    }

    async function save(document, draft) {
      let updated;
      const source = sourceType(document);
      if (String(document.source || "") === "resource") {
        const resourceId = String(document.resource_id || "").trim();
        if (!resourceId) throw new Error("Resource ID 无效");
        const version = versionToken(document.version);
        const response = await window.PortalInstructionClient.execute("UpdateResource", {
          resource_id: resourceId,
          changes: {
            address: String(document.resource_address || document.address || resourceId),
            text: draft,
          },
        }, {
          idempotencyKey: `resource-update:${globalThis.PortalUIPrimitives.randomUUID()}`,
          ...(version ? { expectedVersion: { [resourceId]: version } } : {}),
        });
        updated = response?.resource || {};
      } else if (source === "lineage-workspace" || String(document.source || "") === "workspace_link"
        || document.resident_id || document.directory_id || source === "resident") {
        updated = await saveFile(workspaceFileAddress(document), document, draft);
      } else {
        throw new Error("不支持编辑此预览来源");
      }
      return normalize({
        ...document,
        ...(updated || {}),
        content: updated?.content ?? updated?.text ?? draft,
      }, document);
    }

    return Object.freeze({ canEdit, downloadUrl, fetchMeta, normalize, rawUrl, refreshKey, reload, save });
  }

  window.PortalAgentPreviewSource = Object.freeze({ create });
})();
