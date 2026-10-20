(() => {
  "use strict";

  if (window.PortalPreviewContract) return;

  const textKinds = new Set(["markdown", "json", "jsonl", "text", "code"]);
  const mediaKinds = new Set(["image", "video", "audio", "pdf"]);
  const codeExtensions = new Set([
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".lua", ".mjs", ".cjs", ".php", ".py", ".r", ".rb", ".rs", ".sh",
    ".bash", ".zsh", ".fish", ".sql", ".swift", ".tex", ".ts", ".tsx", ".vue",
    ".xml", ".xsd", ".xsl", ".xslt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".html", ".htm", ".css", ".scss", ".less",
  ]);
  const textExtensions = new Set([".txt", ".rst", ".bib", ".csv", ".tsv", ".log"]);
  const extensionlessTextNames = new Set(["readme", "license", "copying", "makefile", "dockerfile"]);

  function extension(path) {
    const clean = String(path || "").split(/[?#]/, 1)[0].toLowerCase();
    const name = clean.split("/").at(-1) || "";
    const index = name.lastIndexOf(".");
    return index > 0 ? name.slice(index) : "";
  }

  function kindFromMediaType(mediaType) {
    const type = String(mediaType || "").split(";", 1)[0].trim().toLowerCase();
    if (type.startsWith("image/")) return "image";
    if (type.startsWith("video/")) return "video";
    if (type.startsWith("audio/")) return "audio";
    if (type === "application/pdf") return "pdf";
    if (["text/markdown", "text/x-markdown"].includes(type)) return "markdown";
    if (["application/json", "application/ld+json"].includes(type) || type.endsWith("+json")) return "json";
    if (["application/jsonl", "application/x-ndjson"].includes(type)) return "jsonl";
    if ([
      "application/javascript", "application/toml", "application/xml", "application/yaml",
      "application/x-javascript", "application/x-toml", "application/x-yaml", "text/javascript",
      "text/toml", "text/xml", "text/yaml", "text/x-yaml",
    ].includes(type) || type.endsWith("+xml")) return "code";
    if (type.startsWith("text/")) return "text";
    if (type.includes("officedocument") || type.includes("opendocument") || type.includes("msword") || type.includes("ms-powerpoint") || type.includes("ms-excel")) return "office";
    return "";
  }

  function kindFromPath(path) {
    let clean = String(path || "").split(/[?#]/, 1)[0];
    try { clean = decodeURIComponent(clean); } catch (_error) {}
    const name = clean.replaceAll("\\", "/").split("/").at(-1)?.toLowerCase() || "";
    const ext = extension(clean);
    if (extensionlessTextNames.has(name)) return "text";
    if ([".md", ".mdx", ".markdown"].includes(ext)) return "markdown";
    if (ext === ".json") return "json";
    if ([".jsonl", ".ndjson"].includes(ext)) return "jsonl";
    if (codeExtensions.has(ext)) return "code";
    if (textExtensions.has(ext)) return "text";
    if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".bmp", ".ico"].includes(ext)) return "image";
    if ([".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v"].includes(ext)) return "video";
    if ([".mp3", ".wav", ".m4a", ".aac", ".flac", ".oga"].includes(ext)) return "audio";
    if (ext === ".pdf") return "pdf";
    if ([".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".ods", ".odp"].includes(ext)) return "office";
    return "";
  }

  function hasFileShape(path) {
    let clean = String(path || "").split(/[?#]/, 1)[0];
    try { clean = decodeURIComponent(clean); } catch (_error) {}
    const name = clean.replaceAll("\\", "/").split("/").at(-1)?.toLowerCase() || "";
    return Boolean(
      extension(clean)
      || extensionlessTextNames.has(name)
      || /^\.env(?:\.[a-z0-9_-]+)?$/i.test(name),
    );
  }

  function normalizeKind(document = {}) {
    const explicit = String(document.contentKind || document.kind || "").trim().toLowerCase();
    if (explicit === "directory" || explicit === "remote-link" || explicit === "office" || explicit === "binary") return explicit;
    if (["xml", "yaml", "yml"].includes(explicit)) return "code";
    if (mediaKinds.has(explicit) || textKinds.has(explicit)) return explicit;
    return kindFromMediaType(document.mediaType || document.mime_type || document.content_type)
      || kindFromPath(document.path || document.rel_path || document.rawUrl || document.raw_url)
      || (document.binary ? "binary" : "text");
  }

  function normalizeDocument(input = {}) {
    if (!input || typeof input !== "object") throw new TypeError("PreviewDocument must be an object");
    const path = String(input.path || input.rel_path || input.href || "").trim();
    const title = String(input.title || input.name || path.split("/").at(-1) || window.PortalI18n.t("Preview")).trim();
    const downloadName = String(
      input.downloadName
      || input.download_name
      || input.filename
      || input.name
      || path.split("/").at(-1)
      || title,
    ).trim();
    const contentKind = normalizeKind(input);
    const declaredKind = String(input.contentKind || input.kind || "").trim().toLowerCase();
    const declaredLanguage = declaredKind === "yml" ? "yaml" : declaredKind;
    const capabilities = Object.freeze({
      download: Boolean(input.capabilities?.download ?? input.downloadUrl ?? input.download_url ?? input.raw_url ?? input.rawUrl),
      edit: Boolean(input.capabilities?.edit ?? input.editable),
      refresh: input.capabilities?.refresh !== false,
      save: Boolean(input.capabilities?.save ?? input.editable),
      ...(input.capabilities || {}),
    });
    return Object.freeze({
      ...input,
      id: String(input.id || input.identity || path || title),
      title,
      path,
      downloadName,
      mediaType: String(input.mediaType || input.mime_type || input.content_type || ""),
      language: String(input.language || (["xml", "yaml"].includes(declaredLanguage) ? declaredLanguage : "")),
      contentKind,
      kind: contentKind,
      content: input.content == null ? "" : String(input.content),
      rawUrl: String(input.rawUrl || input.raw_url || "").trim(),
      previewUrl: String(input.previewUrl || input.preview_url || "").trim(),
      downloadUrl: String(input.downloadUrl || input.download_url || "").trim(),
      version: String(input.version || input.etag || input.mtime_ns || input.entry?.mtime_ns || input.entry?.mtime || ""),
      capabilities,
    });
  }

  function isTextDocument(document = {}) {
    return !document.binary && textKinds.has(normalizeKind(document));
  }

  function normalizeRequest(request = {}) {
    if (!request || typeof request !== "object") throw new TypeError("PreviewRequest must be an object");
    const href = String(request.href || request.path || "").trim();
    const resourceAddress = String(
      window.PortalResourceChips?.resourceAddressFromPreviewRequest?.({ ...request, href }) || request.resourceAddress || "",
    ).trim();
    return Object.freeze({
      ...request,
      href,
      path: String(request.path || href).trim(),
      locatorKind: resourceAddress ? "resource" : String(request.locatorKind || "").trim(),
      resourceAddress,
      title: String(request.title || request.label || "").trim(),
      source: String(request.source || "chat").trim() || "chat",
    });
  }

  function assertSourceAdapter(adapter) {
    if (!adapter || typeof adapter !== "object") throw new TypeError("Preview source adapter must be an object");
    if (!String(adapter.id || "").trim()) throw new TypeError("Preview source adapter id required");
    if (typeof adapter.load !== "function") throw new TypeError(`Preview source adapter ${adapter.id} requires load()`);
    return adapter;
  }

  function assertRenderer(renderer) {
    if (!renderer || typeof renderer !== "object") throw new TypeError("Preview renderer must be an object");
    if (!String(renderer.id || "").trim()) throw new TypeError("Preview renderer id required");
    if (typeof renderer.accepts !== "function" || typeof renderer.render !== "function") {
      throw new TypeError(`Preview renderer ${renderer.id} requires accepts() and render()`);
    }
    return renderer;
  }

  window.PortalPreviewContract = Object.freeze({
    assertRenderer,
    assertSourceAdapter,
    extension,
    hasFileShape,
    kindFromPath,
    isTextDocument,
    normalizeDocument,
    normalizeKind,
    normalizeRequest,
  });
})();
