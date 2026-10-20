(function (global) {
  "use strict";

  if (global.PortalFileLinks) return;

  const FILE_NAME_SOURCE = "(?:[^\\s<>\"'`|/]+\\.[A-Za-z0-9][A-Za-z0-9+_-]{0,31}|(?:README|LICENSE|COPYING|Makefile|Dockerfile)|\\.env(?:\\.[A-Za-z0-9_-]+)?)";
  const TEXT_FILE_PATH_RE = new RegExp(
    `(^|[\\s([{<"'“‘,;:，、；：])((?:file:\\/\\/)?(?:\\/|~\\/|\\.\\.?\\/)(?:[^\\s<>"'\`|/]+\\/)*${FILE_NAME_SOURCE}(?:#L\\d+(?:-L\\d+)?|:\\d+(?::\\d+)?)?|(?:[A-Za-z0-9_.@+-]+\\/)+${FILE_NAME_SOURCE}(?:#L\\d+(?:-L\\d+)?|:\\d+(?::\\d+)?)?)(?=$|[\\s)\\]}>.,;:!?，。；：])`,
    "gi",
  );
  const EXCLUDED_TEXT_TAGS = new Set([
    "A",
    "BUTTON",
    "CODE",
    "INPUT",
    "KBD",
    "OPTION",
    "PRE",
    "SAMP",
    "SCRIPT",
    "SELECT",
    "STYLE",
    "TEXTAREA",
    "VAR",
  ]);
  const linkOptions = new WeakMap();

  function clean(value) {
    return String(value ?? "").trim();
  }

  function decode(value) {
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }

  function stripWrapping(value) {
    let text = clean(value);
    if (
      text.length >= 2
      && (
        (text.startsWith("<") && text.endsWith(">"))
        || (text.startsWith("`") && text.endsWith("`"))
        || (text.startsWith('"') && text.endsWith('"'))
        || (text.startsWith("'") && text.endsWith("'"))
      )
    ) {
      text = text.slice(1, -1).trim();
    }
    return text;
  }

  function splitLocator(value) {
    let path = clean(value);
    let locator = "";
    const hash = path.match(/(#L\d+(?:-L\d+)?)$/i);
    if (hash) {
      locator = hash[1];
      path = path.slice(0, -locator.length);
      return { path, locator };
    }
    const line = path.match(/(:\d+(?::\d+)?)$/);
    if (line) {
      locator = line[1];
      path = path.slice(0, -locator.length);
    }
    return { path, locator };
  }

  function normalizeSegments(value) {
    const source = String(value || "").replaceAll("\\", "/");
    const absolute = source.startsWith("/");
    const homeRelative = source.startsWith("~/");
    const parts = [];
    for (const part of source.split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") {
        if (!parts.length) {
          if (!absolute && !homeRelative) parts.push("..");
          continue;
        }
        if (parts.at(-1) !== "..") parts.pop();
        else if (!absolute && !homeRelative) parts.push("..");
        continue;
      }
      parts.push(part);
    }
    if (homeRelative) return `~/${parts.slice(parts[0] === "~" ? 1 : 0).join("/")}`;
    return `${absolute ? "/" : ""}${parts.join("/")}` || (absolute ? "/" : "");
  }

  function dirname(value) {
    const path = String(value || "").replaceAll("\\", "/").replace(/\/+$/, "");
    const index = path.lastIndexOf("/");
    if (index < 0) return "";
    return index === 0 ? "/" : path.slice(0, index);
  }

  function localPathFromApiUrl(value) {
    const text = clean(value);
    if (!text) return "";
    try {
      const url = new URL(text, global.location?.href || "http://localhost/");
      const sameOrigin = !global.location?.origin
        || global.location.origin === "null"
        || url.origin === global.location.origin;
      if (
        sameOrigin
        && (
          url.pathname === "/api/agent-workbench/link-file"
          || url.pathname === "/api/agent-workbench/link-file-raw"
        )
      ) {
        return clean(url.searchParams.get("path"));
      }
      if (url.protocol === "file:") return decode(url.pathname);
      if (/^https?:$/i.test(url.protocol)) return "";
    } catch {
      return "";
    }
    return "";
  }

  function pathHasFileShape(path, options = {}) {
    const cleanPath = splitLocator(String(path || "").split(/[?#]/, 1)[0]).path;
    if (global.PortalPreviewContract.hasFileShape(cleanPath)) return true;
    return options.allowExtensionless === true && Boolean(cleanPath.split("/").at(-1));
  }

  function classifyLocator(rawValue, options = {}) {
    const raw = stripWrapping(rawValue);
    if (!raw) return Object.freeze({ kind: "invalid", raw, path: "", requiresProbe: false });
    if (global.PortalResourceChips?.portalReferenceFromPortalHref?.(raw)) {
      return Object.freeze({ kind: "resource", raw, path: "", requiresProbe: false });
    }
    const apiPath = localPathFromApiUrl(raw);
    if (apiPath) {
      return Object.freeze({ kind: "local", raw, path: normalizePath(apiPath, options), requiresProbe: false });
    }
    if (raw.startsWith("//")) {
      return Object.freeze({ kind: "remote", raw, path: "", requiresProbe: false });
    }
    try {
      const url = new URL(raw, global.location?.href || "http://localhost/");
      if (url.protocol === "file:") {
        return Object.freeze({ kind: "local", raw, path: normalizePath(raw, options), requiresProbe: false });
      }
      if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) {
        return Object.freeze({ kind: "remote", raw, path: "", requiresProbe: false });
      }
    } catch {
      if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) {
        return Object.freeze({ kind: "remote", raw, path: "", requiresProbe: false });
      }
    }
    const path = normalizePath(raw, options);
    if (!path || (!path.startsWith("/") && !path.startsWith("~/"))) {
      return Object.freeze({ kind: "relative", raw, path, requiresProbe: false });
    }
    const knownLocalRoot = /^(?:~\/|\/(?:share|nas_ro|home|root|srv|tmp|var|etc|usr|opt|data|mnt|media|proc|sys|dev|run)(?:\/|$))/.test(path);
    return Object.freeze({
      kind: "local",
      raw,
      path,
      requiresProbe: !knownLocalRoot,
    });
  }

  function normalizePath(rawValue, options = {}) {
    let raw = stripWrapping(rawValue);
    if (!raw) return "";
    const apiPath = localPathFromApiUrl(raw);
    if (apiPath) raw = apiPath;
    else {
      try {
        const url = new URL(raw, global.location?.href || "http://localhost/");
        if (url.protocol === "file:") raw = decode(url.pathname);
        else if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return "";
      } catch {
        if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return "";
      }
    }
    raw = decode(raw).replaceAll("\\", "/");
    const split = splitLocator(raw);
    let path = split.path.split("?", 1)[0];
    // /nas_ro is a legacy locator alias, not a filesystem default. Without
    // bootstrap metadata leave it intact for the authoritative server resolver.
    const shareRoot = global.__PORTAL_SITE__?.paths?.share_root;
    if (path.startsWith("/nas_ro/") && typeof shareRoot === "string"
        && /^\/(?!\/)/.test(shareRoot) && shareRoot !== "/"
        && !/[\\\u0000-\u001f\u007f]/.test(shareRoot)
        && !shareRoot.split("/").some(part => part === "." || part === "..")) {
      path = `${shareRoot.replace(/\/+$/, "")}/${path.slice("/nas_ro/".length)}`;
    }
    const basePath = clean(options.basePath)
      .replaceAll("\\", "/");
    if (
      !path.startsWith("/")
      && !path.startsWith("~/")
      && basePath
    ) {
      path = `${dirname(basePath)}/${path}`;
    }
    return normalizeSegments(path);
  }

  function fileName(path) {
    const value = splitLocator(String(path || "").split("?", 1)[0]).path
      .replaceAll("\\", "/")
      .replace(/\/+$/, "");
    return value.split("/").filter(Boolean).at(-1) || value || "file";
  }

  function kindForPath(path) {
    const value = splitLocator(String(path || "").split("?", 1)[0]).path;
    return global.PortalPreviewContract.normalizeKind({ path: value, binary: true });
  }

  function imagePreviewUrl(path) {
    const text = clean(path);
    if (/^\/api\/uploads\/upload_[0-9a-f]+$/.test(text)) return text;
    if (text.includes("/portal_knowledge_item_chats/")) {
      return `/knowledge/api/item-chat/attachment-image?path=${encodeURIComponent(text)}`;
    }
    const upload = text.match(/\/uploads\/(upload_[0-9a-f]+)\//);
    if (upload) return `/api/uploads/${encodeURIComponent(upload[1])}`;
    const marker = "/resident_chat_images/";
    const index = text.indexOf(marker);
    if (index < 0) return "";
    const fileId = text.slice(index + marker.length).replace(/^\/+/, "");
    return fileId
      ? `/api/agent-workbench/chat-attachments/image?file_id=${encodeURIComponent(fileId)}`
      : "";
  }

  function describe(rawValue, options = {}) {
    const raw = stripWrapping(rawValue);
    if (!raw) return null;
    const locator = classifyLocator(raw, options);
    if (locator.kind !== "local") return null;
    let path = locator.path;
    if (path && typeof options.resolvePath === "function") {
      const resolved = options.resolvePath(Object.freeze({
        raw,
        path,
        name: fileName(path),
        kind: kindForPath(path),
      }));
      if (typeof resolved === "string" && resolved.trim()) {
        path = normalizePath(resolved);
      }
    }
    if (
      !path
      || (options.allowAnyLocalPath !== true && !pathHasFileShape(path, options))
      || (!path.startsWith("/") && !path.startsWith("~/"))
    ) return null;
    const { locator: lineLocator } = splitLocator(raw);
    return Object.freeze({
      raw,
      path,
      locator: lineLocator,
      name: fileName(path),
      kind: kindForPath(path),
    });
  }

  function apiUrl(path, options = {}) {
    const params = new URLSearchParams({ path: String(path || "") });
    if (options.meta) params.set("meta", "1");
    return `/api/agent-workbench/link-file?${params.toString()}`;
  }

  async function probe(path, options = {}) {
    const target = clean(path);
    if (!target) return null;
    try {
      const payload = await window.PortalAPI.get(apiUrl(target, { meta: true }), {
        signal: options.signal,
      });
      if (String(payload.kind || "") === "directory") return null;
      return payload;
    } catch (error) {
      if (options.throwOnError) throw error;
      return null;
    }
  }

  async function load(path, options = {}) {
    const target = clean(path);
    if (!target) throw new Error("文件路径不能为空");
    return window.PortalAPI.get(apiUrl(target, options), {
      signal: options.signal,
    });
  }

  function downloadUrl(path) {
    const target = clean(path);
    return target ? `/api/agent-workbench/link-file-raw?path=${encodeURIComponent(target)}&download=1` : "";
  }

  function nodeExcluded(node) {
    let element = node?.parentElement || null;
    while (element) {
      if (
        EXCLUDED_TEXT_TAGS.has(element.tagName)
        || element.classList?.contains("portalFileLink")
        || element.hasAttribute?.("data-portal-file-link-skip")
      ) return true;
      element = element.parentElement;
    }
    return false;
  }

  function linkElement(descriptor, options = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `portalFileLink ${options.inlineCode ? "portalFileInlineLink" : "portalFileTextLink"}`;
    button.dataset.portalFilePath = descriptor.path;
    button.dataset.portalFileRaw = descriptor.raw;
    button.title = descriptor.path;
    button.setAttribute("aria-label", window.PortalI18n.text`打开文件 ${descriptor.name}`);
    if (options.inlineCode) {
      const code = document.createElement("code");
      code.textContent = descriptor.raw;
      button.appendChild(code);
    } else {
      button.textContent = descriptor.raw;
    }
    return button;
  }

  function linkifyTextNodes(root, options = {}) {
    if (!root || typeof document === "undefined") return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      if (!nodeExcluded(walker.currentNode)) nodes.push(walker.currentNode);
    }
    nodes.forEach(node => {
      const value = String(node.nodeValue || "");
      TEXT_FILE_PATH_RE.lastIndex = 0;
      let match = TEXT_FILE_PATH_RE.exec(value);
      if (!match) return;
      const fragment = document.createDocumentFragment();
      let cursor = 0;
      do {
        const raw = match[2];
        const start = match.index + match[1].length;
        const descriptor = describe(raw, options);
        if (!descriptor) {
          match = TEXT_FILE_PATH_RE.exec(value);
          continue;
        }
        if (start > cursor) fragment.appendChild(document.createTextNode(value.slice(cursor, start)));
        fragment.appendChild(linkElement(descriptor));
        cursor = start + raw.length;
        match = TEXT_FILE_PATH_RE.exec(value);
      } while (match);
      if (cursor < value.length) fragment.appendChild(document.createTextNode(value.slice(cursor)));
      node.replaceWith(fragment);
    });
  }

  function linkifyInlineCode(root, options = {}) {
    root?.querySelectorAll?.("code").forEach(code => {
      if (
        code.closest("pre, a, button")
        || code.closest("[data-portal-file-link-skip]")
      ) return;
      const descriptor = describe(code.textContent, options);
      if (!descriptor) return;
      code.replaceWith(linkElement(descriptor, { inlineCode: true }));
    });
  }

  function descriptorForNode(node, options = {}) {
    if (node?.dataset?.portalResourceLinkBound === "1") return null;
    const raw = clean(
      node?.dataset?.portalFileRaw
      || node?.dataset?.portalFilePath
      || node?.dataset?.portalPreviewLink
      || node?.dataset?.portalPreviewImage
      || node?.getAttribute?.("href")
      || node?.getAttribute?.("src"),
    );
    return describe(raw, {
      ...options,
      allowExtensionless: node?.tagName === "A",
      allowAnyLocalPath: node?.tagName === "A",
    });
  }

  function collect(root, options = {}) {
    if (!root) return [];
    const nodes = [];
    if (root.matches?.("[data-portal-file-path], [data-portal-preview-link]")) nodes.push(root);
    root.querySelectorAll?.(
      "[data-portal-file-path], [data-portal-preview-link], img[data-portal-preview-image]",
    ).forEach(node => nodes.push(node));
    const seen = new Set();
    const descriptors = [];
    nodes.forEach(node => {
      const descriptor = descriptorForNode(node, options);
      if (!descriptor || seen.has(descriptor.path)) return;
      seen.add(descriptor.path);
      descriptors.push({ ...descriptor, node });
    });
    return descriptors;
  }

  function bindNode(node, options = {}) {
    if (!node) return;
    linkOptions.set(node, { ...(linkOptions.get(node) || {}), ...options });
    if (node.dataset.portalFileLinkBound === "1") return;
    node.dataset.portalFileLinkBound = "1";
    node.classList.add("portalFileLink");
    const previewRequest = () => {
      const currentOptions = linkOptions.get(node) || {};
      const descriptor = descriptorForNode(node, currentOptions);
      return descriptor && {
        ...descriptor, href: descriptor.path,
        title: node.textContent?.trim() || descriptor.name,
        node, source: String(currentOptions.source || "file-link"),
        target: currentOptions.target || node.closest(".markdownBody") || document,
      };
    };
    const open = request => {
      const onOpen = linkOptions.get(node)?.onOpen;
      return typeof onOpen === "function" ? onOpen(request) : global.PortalPreview?.request?.(request);
    };
    node.addEventListener("contextmenu", event => {
      global.PortalPreview?.openFileMenu?.({ event, trigger: node, request: previewRequest(), onOpen: open });
    });
    node.addEventListener("click", async event => {
      if (
        event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
      ) return;
      const request = previewRequest();
      if (!request) return;
      event.preventDefault();
      event.stopPropagation();
      await open(request);
    });
  }

  function bind(root, options = {}) {
    if (!root) return root;
    const nodes = [];
    if (root.matches?.("[data-portal-file-path], [data-portal-preview-link]")) nodes.push(root);
    root.querySelectorAll?.("[data-portal-file-path], [data-portal-preview-link]")
      .forEach(node => nodes.push(node));
    nodes.forEach(node => {
      if (descriptorForNode(node, options)) bindNode(node, options);
    });
    return root;
  }

  function enhance(root, options = {}) {
    if (!root) return [];
    linkifyInlineCode(root, options);
    if (options.linkifyText !== false) linkifyTextNodes(root, options);
    bind(root, options);
    const descriptors = collect(root, options);
    options.onDiscover?.(descriptors);
    return descriptors;
  }

  global.PortalFileLinks = Object.freeze({
    apiUrl,
    bind,
    classifyLocator,
    collect,
    describe,
    downloadUrl,
    enhance,
    fileName,
    imagePreviewUrl,
    kindForPath,
    load,
    normalizePath,
    probe,
  });
})(window);
