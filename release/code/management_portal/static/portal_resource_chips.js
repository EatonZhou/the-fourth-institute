(function (global) {
  "use strict";

  if (global.PortalResourceChips) return;

  const RESOURCE_ADDRESS_PATTERN = String.raw`[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}(?:\/(?:[A-Za-z0-9._~:@+\-]|%[0-9A-Fa-f]{2})+)*`;
  const RESOURCE_ADDRESS_EXACT_RE = new RegExp(`^(?:${RESOURCE_ADDRESS_PATTERN})$`);
  const RESOURCE_ROOT_PARTS_RE = /^(?<resourceType>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)_[0-9a-f]{12}$/;
  const routeContract = global.PortalResourceRoutes;
  if (
    !global.PortalCompatibility.schemaMatches(routeContract?.schema, "tfi.resource-portal-routes.v1")
  ) {
    throw new Error("PortalResourceChips requires the published resource route contract.");
  }
  const TEXT_RESOURCE_TYPES = new Set(routeContract.text_resource_types);
  const NAVIGATION_RESOURCE_TYPES = new Set(routeContract.navigation_resource_types || []);
  const NAVIGATION_REFERENCE_ROUTES = (routeContract.navigation_references || [])
    .map(item => ({
      referenceType: String(item.reference_type || "").trim(),
      pattern: String(item.pattern || "").trim(),
      path: String(item.path || "").replace(/\/+$/, ""),
      parameter: String(item.parameter || "").trim(),
      title: String(item.title || "").trim(),
    }))
    .filter(item => item.referenceType && item.pattern && item.path && item.parameter)
    .map(item => Object.freeze({
      ...item,
      exactMatcher: new RegExp(`^(?:${item.pattern})$`),
    }));
  const PORTAL_REFERENCE_PATTERN = [
    RESOURCE_ADDRESS_PATTERN,
    ...NAVIGATION_REFERENCE_ROUTES.map(item => `(?:${item.pattern})`),
  ].join("|");
  const PORTAL_REFERENCE_RE = new RegExp(
    `(^|[^A-Za-z0-9_./\\\\])(${PORTAL_REFERENCE_PATTERN})(?![A-Za-z0-9_])`,
    "g",
  );
  const RESOURCE_CONTENT_ROUTE = routeContract.content_route || {};
  const PORTAL_RESOURCE_IDENTITY_PARAMETERS = new Set(routeContract.identity_parameters);
  const PORTAL_RESOURCE_ROUTE_CONTRACT = new Map();
  for (const item of routeContract.routes) {
    const path = item.path.replace(/\/+$/, "");
    const routes = PORTAL_RESOURCE_ROUTE_CONTRACT.get(path) || [];
    let route = routes.find(candidate => candidate.parameter === item.parameter);
    if (!route) {
      route = { parameter: item.parameter, types: new Set(), context: item.context || {} };
      routes.push(route);
    }
    route.types.add(item.resource_type);
    PORTAL_RESOURCE_ROUTE_CONTRACT.set(path, routes);
  }
  const RESOURCE_LAZY_PREVIEW_KINDS = new Set(["image", "video", "audio", "pdf", "office", "binary"]);
  const RESOURCE_TEXT_PREVIEW_MAX_BYTES = 2 * 1024 * 1024;
  const EXCLUDED_TAGS = new Set([
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
  const REFERENCE_HOVER_CACHE_MS = 60_000;
  const referenceCache = new Map();
  const chipOptions = new WeakMap();
  const resourceLinkOptions = new WeakMap();
  const resourceLabelAddresses = new WeakMap();
  const hoverState = {
    card: null,
    node: null,
    showTimer: 0,
    hideTimer: 0,
    requestToken: 0,
    overlay: null,
  };

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  function isRootId(value) {
    return RESOURCE_ROOT_PARTS_RE.test(String(value || "").trim());
  }

  function resourceTypeFromAddress(address) {
    const clean = String(address || "").trim().replace(/^\/+|\/+$/g, "");
    if (!RESOURCE_ADDRESS_EXACT_RE.test(clean)) return "";
    const rootId = clean.split("/", 1)[0];
    return rootId.match(RESOURCE_ROOT_PARTS_RE)?.groups?.resourceType || "";
  }

  function normalizeResourceAddress(value) {
    const clean = String(value || "").trim();
    if (RESOURCE_ADDRESS_EXACT_RE.test(clean)) return clean;
    const parts = clean.split("/");
    const rootId = parts.shift() || "";
    if (!RESOURCE_ROOT_PARTS_RE.test(rootId) || !parts.length || parts.some(part => !part)) return "";
    try {
      const encoded = [rootId, ...parts.map(part => encodeURIComponent(decodeURIComponent(part))
        .replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`))]
        .join("/");
      return RESOURCE_ADDRESS_EXACT_RE.test(encoded) ? encoded : "";
    } catch {
      return "";
    }
  }

  function navigationReferenceRoute(value) {
    const clean = String(value || "").trim();
    return NAVIGATION_REFERENCE_ROUTES.find(route => route.exactMatcher.test(clean)) || null;
  }

  function normalizePortalReference(value) {
    const clean = String(value || "").trim();
    return normalizeResourceAddress(clean) || (navigationReferenceRoute(clean) ? clean : "");
  }

  function navigationReferenceHref(value, route = navigationReferenceRoute(value)) {
    const clean = String(value || "").trim();
    if (!route || !route.exactMatcher.test(clean)) return "";
    const query = new URLSearchParams();
    query.set(route.parameter, clean);
    return `${route.path}?${query.toString()}`;
  }

  function portalReferenceHref(value) {
    const clean = normalizePortalReference(value);
    if (!clean) return "";
    return navigationReferenceHref(clean) || `/r/${encodeURIComponent(clean)}`;
  }

  function portalReferenceFromNode(node) {
    return String(
      node?.dataset?.resourceAddress
      || node?.dataset?.navigationReference
      || "",
    ).trim();
  }

  function portalUrlFromValue(value) {
    const raw = String(value || "").trim();
    if (!raw) return null;

    const fallbackBase = "http://portal.invalid/";
    const locationHref = ["http:", "https:"].includes(String(global.location?.protocol || ""))
      ? String(global.location?.href || "")
      : "";
    const locationOrigin = String(global.location?.origin || "");
    let url;
    try {
      url = new URL(raw, locationHref || fallbackBase);
    } catch {
      return null;
    }
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (url.username || url.password) return null;
    if (
      locationOrigin
      && locationOrigin !== "null"
      && url.origin !== locationOrigin
    ) {
      return null;
    }
    if (
      (!locationOrigin || locationOrigin === "null")
      && /^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)
      && url.origin !== new URL(fallbackBase).origin
    ) {
      return null;
    }
    return url;
  }

  function resourceAddressFromPortalHref(value) {
    const url = portalUrlFromValue(value);
    if (!url) return "";

    let address = "";
    const routePath = url.pathname.replace(/\/+$/, "");
    if (url.pathname.startsWith("/r/")) {
      try {
        address = decodeURIComponent(url.pathname.slice("/r/".length));
      } catch {
        return "";
      }
    } else if (
      routePath === String(RESOURCE_CONTENT_ROUTE.path || "").replace(/\/+$/, "")
      && RESOURCE_CONTENT_ROUTE.parameter
    ) {
      const items = url.searchParams.getAll(RESOURCE_CONTENT_ROUTE.parameter);
      const conflictingIdentity = [...PORTAL_RESOURCE_IDENTITY_PARAMETERS]
        .some(parameter => url.searchParams.has(parameter));
      if (items.length !== 1 || conflictingIdentity) return "";
      address = items[0];
    } else {
      const routes = PORTAL_RESOURCE_ROUTE_CONTRACT.get(routePath) || [];
      const route = routes.find(candidate => {
        const items = url.searchParams.getAll(candidate.parameter);
        const candidateAddress = items.length === 1
          ? normalizeResourceAddress(items[0])
          : "";
        if (!candidateAddress) return false;
        if (!candidate.types.has(resourceTypeFromAddress(candidateAddress))) return false;
        return [...PORTAL_RESOURCE_IDENTITY_PARAMETERS].every(parameter => {
          if (parameter === candidate.parameter || !url.searchParams.has(parameter)) return true;
          const contextType = candidate.context[parameter];
          const values = url.searchParams.getAll(parameter);
          return !!contextType && values.length === 1 && isRootId(values[0])
            && resourceTypeFromAddress(values[0]) === contextType;
        });
      });
      if (!route) return "";
      address = url.searchParams.get(route.parameter);
    }
    return normalizeResourceAddress(address);
  }

  function navigationReferenceFromPortalHref(value) {
    const url = portalUrlFromValue(value);
    if (!url) return "";
    const routePath = url.pathname.replace(/\/+$/, "");
    const route = NAVIGATION_REFERENCE_ROUTES.find(candidate => {
      if (candidate.path !== routePath) return false;
      const values = url.searchParams.getAll(candidate.parameter);
      if (values.length !== 1 || !candidate.exactMatcher.test(values[0])) return false;
      return [...PORTAL_RESOURCE_IDENTITY_PARAMETERS].every(parameter => (
        parameter === candidate.parameter || !url.searchParams.has(parameter)
      ));
    });
    return route ? url.searchParams.get(route.parameter) || "" : "";
  }

  function portalReferenceFromPortalHref(value) {
    return resourceAddressFromPortalHref(value) || navigationReferenceFromPortalHref(value);
  }

  // Canonical URLs own resource identity/location; explicit links own the
  // destination view (Chat, message anchor, selected graph item, etc.). Keep
  // view parameters only when both URLs resolve to the same resource/page.
  function navigationHrefForReference(address, href, referenceUrl) {
    const canonicalHref = String(referenceUrl || href).trim();
    const source = portalUrlFromValue(href), canonical = portalUrlFromValue(canonicalHref);
    if (!source || !canonical || source.pathname !== canonical.pathname
      || resourceAddressFromPortalHref(href) !== address
      || resourceAddressFromPortalHref(canonicalHref) !== address) return canonicalHref;
    const viewParameters = new Set([...source.searchParams.keys()]
      .filter(key => !PORTAL_RESOURCE_IDENTITY_PARAMETERS.has(key)));
    for (const key of viewParameters) {
      canonical.searchParams.delete(key);
      for (const value of source.searchParams.getAll(key)) canonical.searchParams.append(key, value);
    }
    if (source.hash) canonical.hash = source.hash;
    return `${canonical.pathname}${canonical.search}${canonical.hash}`;
  }

  function resourceAddressFromPreviewRequest(request = {}) {
    return (
      normalizeResourceAddress(request.resourceAddress || request.address)
      || resourceAddressFromPortalHref(request.href || request.path || "")
    );
  }

  function decodedResourceAddress(address) {
    try {
      return decodeURIComponent(String(address || ""));
    } catch (_error) {
      return String(address || "");
    }
  }

  function resourcePreviewInput(contentType, address, reference = {}, binary = true) {
    return {
      contentKind: reference.content_kind,
      mediaType: contentType || reference.media_type,
      path: reference.filename || decodedResourceAddress(address),
      binary,
    };
  }

  function resourceSupportsTextPreview(address, reference = {}) {
    const clean = normalizeResourceAddress(address);
    if (!clean) return false;
    if (!clean.includes("/") && TEXT_RESOURCE_TYPES.has(resourceTypeFromAddress(clean))) return true;
    return global.PortalPreviewContract.isTextDocument(
      { contentKind: resourcePreviewKind("", clean, reference) },
    );
  }

  function resourceContentTypeIsText(contentType, address = "") {
    return global.PortalPreviewContract.isTextDocument(
      { contentKind: resourcePreviewKind(contentType, address) },
    );
  }

  function resourcePreviewKind(contentType, address = "", reference = {}) {
    return global.PortalPreviewContract.normalizeKind(
      resourcePreviewInput(contentType, address, reference),
    );
  }

  function resourceUsesNavigation(address, reference = {}) {
    if (navigationReferenceRoute(address)) return true;
    const clean = normalizeResourceAddress(address);
    if (!clean) return false;
    if (clean.includes("/")) return false;
    const resourceType = String(reference?.resource_type || resourceTypeFromAddress(clean)).trim();
    return NAVIGATION_RESOURCE_TYPES.has(resourceType);
  }

  function resourceSupportsPreview(address, reference = {}) {
    if (navigationReferenceRoute(address)) return false;
    const clean = normalizeResourceAddress(address);
    if (!clean || resourceUsesNavigation(clean, reference)) return false;
    if (resourceSupportsTextPreview(clean, reference)) return true;
    if (clean.includes("/")) return true;
    return resourcePreviewKind("", clean, reference) !== "binary";
  }

  function resourceChipHtml(address, extraClass = "") {
    const clean = normalizePortalReference(address);
    if (!clean) return "";
    const navigationRoute = navigationReferenceRoute(clean);
    const dataAttribute = navigationRoute
      ? `data-navigation-reference="${escapeHtml(clean)}"`
      : `data-resource-address="${escapeHtml(clean)}"`;
    const targetLabel = navigationRoute?.title || window.PortalI18n.t("Resource");
    const classes = [
      "portalResourceChip",
      ...String(extraClass || "")
        .split(/\s+/)
        .filter(value => /^[A-Za-z][A-Za-z0-9_-]*$/.test(value)),
    ].join(" ");
    return window.PortalI18n.html`<a class="${escapeHtml(classes)}" href="${escapeHtml(portalReferenceHref(clean))}" ${dataAttribute} title="${escapeHtml(window.PortalI18n.text`打开 ${targetLabel} ${clean}`)}"><code>${escapeHtml(clean)}</code></a>`;
  }

  function resourceLabelHtml(address, { focusable = true } = {}) {
    const clean = String(address || "").trim();
    if (!isRootId(clean)) return escapeHtml(clean);
    return window.PortalI18n.html`<span data-resource-label="${escapeHtml(clean)}" data-resource-chip-skip title="${escapeHtml(clean)}"${focusable ? ' tabindex="0"' : ""} aria-label="${escapeHtml(clean)}">${escapeHtml(clean)}</span>`;
  }

  function bindResourceLabels(root, options = {}) {
    const nodes = [];
    if (root?.matches?.("[data-resource-label]")) nodes.push(root);
    root?.querySelectorAll?.("[data-resource-label]").forEach(node => nodes.push(node));
    nodes.forEach(node => bindResourceLabel(node, options));
  }

  function bindResourceLabel(node, options = {}) {
    const address = String(node.dataset.resourceLabel || "").trim();
    if (!isRootId(address) || resourceLabelAddresses.get(node) === address) return;
    resourceLabelAddresses.set(node, address);
    loadReference(address).then(reference => {
      if (resourceLabelAddresses.get(node) !== address || node.dataset.resourceLabel !== address) return;
      const title = String(reference?.title || address).trim() || address;
      node.textContent = title;
      node.title = address;
      node.setAttribute("aria-label", `${title} (${address})`);
    }).catch(error => {
      if (resourceLabelAddresses.get(node) !== address || node.dataset.resourceLabel !== address) return;
      options.onResourceLabelError?.({ node, address, error });
    });
  }

  function resourceChipElement(address, extraClass = "") {
    const template = document.createElement("template");
    template.innerHTML = resourceChipHtml(address, extraClass);
    return template.content.firstElementChild;
  }

  function resourceChipExcluded(node) {
    let element = node?.parentElement || null;
    while (element) {
      if (
        EXCLUDED_TAGS.has(element.tagName)
        || element.classList?.contains("portalResourceChip")
        || element.hasAttribute?.("data-message-attachment-path")
        || element.hasAttribute?.("data-resource-chip-skip")
      ) {
        return true;
      }
      element = element.parentElement;
    }
    return false;
  }

  function linkifyStandaloneInlineCode(root) {
    if (!root || typeof document === "undefined") return root;
    const codeNodes = [];
    if (root.matches?.("code")) codeNodes.push(root);
    root.querySelectorAll?.("code").forEach(node => codeNodes.push(node));
    codeNodes.forEach(code => {
      if (
        code.closest("pre, a, button")
        || code.closest("[data-resource-chip-skip]")
      ) return;
      const address = String(code.textContent || "").trim();
      if (!isRootId(address) && !navigationReferenceRoute(address)) return;
      const chip = resourceChipElement(address);
      if (!chip) return;
      code.replaceWith(chip);
      chip.replaceChildren(code);
    });
    return root;
  }

  function resourceChipTextLooksLikeCode(value) {
    return String(value || "").split(/\r?\n/).some(rawLine => {
      const line = rawLine.trim().replace(/^(?:[$#>]\s*)+/, "");
      if (!line) return false;
      if (
        /^(?:const|let|var|function|class|def|import|from|return|export|interface|type|enum|package|use)\b/.test(line)
        || /^(?:if|for|while|switch|catch)\s*\(/.test(line)
        || /^[A-Za-z_$][\w$.[\]-]*\s*(?:=|:=)\s*/.test(line)
        || /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\([^)]*\)\s*;?$/.test(line)
        || /^"[^"]+"\s*:\s*/.test(line)
      ) {
        return true;
      }
      const command = line.match(/^(?:sudo\s+)?[a-z0-9][a-z0-9._+/-]*(?:\s+|$)/);
      if (!command) return false;
      return (
        /(?:^|\s)(?:\/|~\/|\.\.?\/)\S+/.test(line)
        || /\s--?[A-Za-z0-9][\w-]*(?:[=\s]|$)/.test(line)
        || /(?:^|\s)[A-Za-z_][A-Za-z0-9_]*=\S+/.test(line)
        || /(?:&&|\|\||[|;])/.test(line)
      );
    });
  }

  function resourceAddressEmbeddedInLocator(value, start) {
    const source = String(value || "");
    const prefix = source.slice(Math.max(
      source.lastIndexOf(" ", start - 1),
      source.lastIndexOf("\t", start - 1),
      source.lastIndexOf("\n", start - 1),
    ) + 1, start);
    return /[/\\]/.test(prefix) || /(?:https?|file):/i.test(prefix);
  }

  function resourceAddressInCodeLikeLine(value, start) {
    const source = String(value || "");
    const lineStart = source.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const nextLine = source.indexOf("\n", start);
    const lineEnd = nextLine < 0 ? source.length : nextLine;
    return resourceChipTextLooksLikeCode(source.slice(lineStart, lineEnd));
  }

  function linkifyElement(root) {
    if (!root || typeof document === "undefined") return root;
    linkifyStandaloneInlineCode(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      if (!resourceChipExcluded(walker.currentNode)) nodes.push(walker.currentNode);
    }
    nodes.forEach(node => {
      const value = String(node.nodeValue || "");
      PORTAL_REFERENCE_RE.lastIndex = 0;
      let match = PORTAL_REFERENCE_RE.exec(value);
      if (!match) return;
      const fragment = document.createDocumentFragment();
      let cursor = 0;
      do {
        const rawAddress = match[2];
        const address = rawAddress.includes("/")
          ? rawAddress.replace(/[.:]+$/, "")
          : rawAddress;
        const start = match.index + match[1].length;
        if (
          resourceAddressEmbeddedInLocator(value, start)
          || resourceAddressInCodeLikeLine(value, start)
        ) {
          match = PORTAL_REFERENCE_RE.exec(value);
          continue;
        }
        // The text matcher finds candidates. Removing sentence punctuation can
        // leave an invalid path (for example, "workspace_<id>/file/...").
        // Keep that candidate verbatim; only advance the cursor for a real chip.
        const chip = resourceChipElement(address);
        if (!chip) {
          match = PORTAL_REFERENCE_RE.exec(value);
          continue;
        }
        if (start > cursor) {
          fragment.appendChild(document.createTextNode(value.slice(cursor, start)));
        }
        fragment.appendChild(chip);
        cursor = start + address.length;
        match = PORTAL_REFERENCE_RE.exec(value);
      } while (match);
      if (cursor < value.length) {
        fragment.appendChild(document.createTextNode(value.slice(cursor)));
      }
      node.replaceWith(fragment);
    });
    return root;
  }

  function linkifyHtml(html) {
    const source = String(html || "");
    if (!source || typeof document === "undefined") return source;
    const template = document.createElement("template");
    template.innerHTML = source;
    linkifyElement(template.content);
    return template.innerHTML;
  }

  async function resourceResponseError(response, address) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.error || `无法读取资源 ${address}（HTTP ${response.status}）`);
    error.code = String(payload.code || "").trim();
    error.retryable = payload.retryable === true;
    error.status = response.status;
    return error;
  }

  async function loadReference(address, options = {}) {
    const clean = String(address || "").trim();
    const cached = referenceCache.get(clean);
    const maxAgeMs = Math.max(
      0,
      Number(options.maxAgeMs ?? REFERENCE_HOVER_CACHE_MS),
    );
    if (
      options.allowCached !== false
      && cached?.value
      && Date.now() - Number(cached.loadedAt || 0) <= maxAgeMs
    ) {
      return cached.value;
    }
    if (cached?.promise) return cached.promise;
    const promise = window.PortalAPI.get(
      `/api/resources/reference?address=${encodeURIComponent(clean)}`,
    ).catch(error => {
      error.retryable = error?.data?.retryable === true;
      throw error;
    });
    referenceCache.set(clean, {
      value: cached?.value || null,
      loadedAt: Number(cached?.loadedAt || 0),
      promise,
    });
    try {
      const value = await promise;
      referenceCache.set(clean, {
        value,
        loadedAt: Date.now(),
        promise: null,
      });
      if (referenceCache.size > 200) {
        referenceCache.delete(referenceCache.keys().next().value);
      }
      return value;
    } catch (error) {
      referenceCache.delete(clean);
      throw error;
    }
  }

  function resourceContentSize(reference = {}) {
    if (reference.content_size == null || reference.content_size === "") return null;
    const value = Number(reference.content_size);
    return Number.isSafeInteger(value) && value >= 0 ? value : null;
  }

  function resourcePreviewDocument(address, reference, kind, options = {}) {
    const clean = String(address || "").trim();
    const encoded = encodeURIComponent(clean);
    const contentUrl = `/api/resources/content?address=${encoded}`;
    const capabilities = reference?.capabilities && typeof reference.capabilities === "object"
      ? { ...reference.capabilities }
      : {};
    capabilities.download = true;
    if (options.readOnly !== false) {
      capabilities.edit = false;
      capabilities.save = false;
    }
    return {
      source: "resource",
      address: clean,
      resource_address: clean,
      resource_id: String(reference.resource_id || clean.split("/", 1)[0]),
      resource_type: String(reference.resource_type || resourceTypeFromAddress(clean)),
      path: clean,
      preview_title: String(reference.title || reference.resource_id || clean),
      title: String(reference.title || reference.resource_id || clean),
      filename: String(reference.filename || clean.split("/").at(-1) || ""),
      summary: String(reference.summary || ""),
      kind,
      content: String(options.content || ""),
      content_type: String(options.contentType || reference.media_type || ""),
      content_url: contentUrl,
      raw_url: contentUrl,
      download_url: `${contentUrl}&download=1`,
      resource_url: String(reference.url || `/r/${encoded}`),
      version: reference.version || {},
      capabilities,
      binary: options.binary ?? !global.PortalPreviewContract.isTextDocument({ contentKind: kind }),
      truncated: Boolean(options.truncated),
      editable: options.readOnly === false && Boolean(capabilities.edit && capabilities.save),
      preview_unavailable_reason: String(options.previewUnavailableReason || ""),
    };
  }

  function oversizedTextPreview(address, reference) {
    return resourcePreviewDocument(address, reference, "binary", {
      truncated: true,
      previewUnavailableReason: "文件超过 2 MiB 的文本预览上限，请下载后查看。",
    });
  }

  async function readBoundedTextResponse(response) {
    const declaredHeader = response.headers.get("Content-Length");
    const declaredSize = declaredHeader == null || !String(declaredHeader).trim()
      ? Number.NaN
      : Number(declaredHeader);
    if (Number.isFinite(declaredSize) && declaredSize > RESOURCE_TEXT_PREVIEW_MAX_BYTES) {
      await response.body?.cancel?.().catch?.(() => {});
      return { tooLarge: true, text: "" };
    }
    if (!response.body?.getReader) {
      if (!Number.isFinite(declaredSize)) {
        return { tooLarge: true, text: "" };
      }
      const text = await response.text();
      return {
        tooLarge: new TextEncoder().encode(text).byteLength > RESOURCE_TEXT_PREVIEW_MAX_BYTES,
        text,
      };
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let bytes = 0;
    let text = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > RESOURCE_TEXT_PREVIEW_MAX_BYTES) {
        await reader.cancel().catch(() => {});
        return { tooLarge: true, text: "" };
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return { tooLarge: false, text };
  }

  async function loadTextPreview(address, options = {}) {
    const clean = normalizeResourceAddress(address);
    if (!clean) return null;
    const reference = options.reference || await loadReference(clean, options);
    if (!resourceSupportsTextPreview(clean, reference)) return null;
    if ((resourceContentSize(reference) ?? 0) > RESOURCE_TEXT_PREVIEW_MAX_BYTES) {
      return oversizedTextPreview(clean, reference);
    }
    const contentUrl = `/api/resources/content?address=${encodeURIComponent(clean)}`;
    const contentResponse = await window.PortalAPI.raw(contentUrl, {
      cache: "no-store",
      credentials: "same-origin",
      signal: options.signal,
      timeoutMs: options.timeoutMs,
    });
    if (!contentResponse.ok) throw await resourceResponseError(contentResponse, clean);
    const contentType = String(contentResponse.headers.get("Content-Type") || "");
    const kind = resourcePreviewKind(contentType, clean, reference);
    if (!global.PortalPreviewContract.isTextDocument({ contentKind: kind })) {
      return resourcePreviewDocument(clean, reference, kind);
    }
    const body = await readBoundedTextResponse(contentResponse);
    if (body.tooLarge) return oversizedTextPreview(clean, reference);
    return resourcePreviewDocument(clean, reference, kind, {
      binary: false,
      content: body.text,
      contentType,
      readOnly: false,
    });
  }

  async function loadPreview(address, options = {}) {
    const clean = normalizeResourceAddress(address);
    if (!clean) return null;
    const reference = options.reference || await loadReference(clean, options);
    if (!resourceSupportsPreview(clean, reference)) return null;
    if (resourceSupportsTextPreview(clean, reference)) {
      return loadTextPreview(clean, { ...options, reference });
    }
    const kind = resourcePreviewKind("", clean, reference);
    if (!RESOURCE_LAZY_PREVIEW_KINDS.has(kind)) return null;
    return resourcePreviewDocument(clean, reference, kind);
  }

  function showUnavailableNotice(address) {
    const clean = String(address || "").trim();
    return global.PortalUI.toast(clean
      ? window.PortalI18n.text`Resource 已删除或不可用：${clean}`
      : window.PortalI18n.t("Resource 已删除或不可用"), "warning", {
      key: "resource-unavailable",
    });
  }

  function showServiceUnavailableNotice(error) {
    const code = String(error?.code || "").trim();
    const service = ["lineage.unavailable", "lineage.busy"].includes(code)
      ? "SocietyService"
      : "ResourceService";
    return global.PortalUI.toast(
      window.PortalI18n.text`${service} 暂时不可用，已保留当前页面，请稍后重试`,
      "warning",
      { key: `${code || "resource_service_unavailable"}-navigation` },
    );
  }

  function resourceTypeLabel(value) {
    const clean = String(value || "").trim();
    if (!clean) return window.PortalI18n.t("Resource");
    return clean
      .split("_")
      .filter(Boolean)
      .map(part => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function ensureHoverCard() {
    if (hoverState.card?.isConnected) return hoverState.card;
    const card = document.createElement("div");
    card.id = "portalResourceHoverCard";
    card.className = "portalResourceHoverCard";
    card.hidden = true;
    card.setAttribute("role", "tooltip");
    card.innerHTML = window.PortalI18n.html`
      <div class="portalResourceHoverTitle" data-resource-hover-title></div>
      <div class="portalResourceHoverMeta" data-resource-hover-meta></div>
      <code class="portalResourceHoverId" data-resource-hover-id></code>
      <div class="portalResourceHoverHint" data-resource-hover-hint></div>
    `;
    card.addEventListener("mouseenter", () => {
      global.clearTimeout(hoverState.hideTimer);
      hoverState.hideTimer = 0;
    });
    card.addEventListener("mouseleave", () => hideHoverCard());
    global.addEventListener(
      "scroll",
      () => hideHoverCard({ immediate: true }),
      true,
    );
    global.addEventListener(
      "resize",
      () => hideHoverCard({ immediate: true }),
    );
    hoverState.overlay = global.PortalOverlay?.register(card, {
      kind: "popover",
      closeOnEscape: true,
      onRequestClose: () => hideHoverCard({ immediate: true }),
    });
    document.body.appendChild(card);
    hoverState.card = card;
    return card;
  }

  function positionHoverCard(node, card) {
    if (!node?.isConnected || !card || card.hidden) return;
    global.PortalOverlay?.positionAnchored(node, card, {
      placement: "bottom-start",
      gap: 8,
      margin: 8,
    });
  }

  function renderHoverCard(card, payload = {}) {
    const title = card.querySelector("[data-resource-hover-title]");
    const meta = card.querySelector("[data-resource-hover-meta]");
    const resourceId = card.querySelector("[data-resource-hover-id]");
    const hint = card.querySelector("[data-resource-hover-hint]");
    if (title) title.textContent = String(payload.title || window.PortalI18n.t("Resource"));
    if (meta) {
      meta.textContent = String(payload.meta || "");
      meta.hidden = !meta.textContent;
    }
    if (resourceId) resourceId.textContent = String(payload.resourceId || "");
    if (hint) {
      hint.textContent = String(payload.hint || "");
      hint.hidden = !hint.textContent;
    }
    card.dataset.state = String(payload.state || "resolved");
  }

  function hideHoverCard(options = {}) {
    global.clearTimeout(hoverState.showTimer);
    hoverState.showTimer = 0;
    const hide = () => {
      hoverState.requestToken += 1;
      hoverState.node?.removeAttribute("aria-describedby");
      hoverState.node = null;
      if (hoverState.card) {
        hoverState.card.hidden = true;
        hoverState.overlay?.deactivate();
      }
    };
    global.clearTimeout(hoverState.hideTimer);
    hoverState.hideTimer = 0;
    if (options.immediate) hide();
    else hoverState.hideTimer = global.setTimeout(hide, 100);
  }

  async function showHoverCard(node, address) {
    if (!node?.isConnected || !address) return;
    global.clearTimeout(hoverState.hideTimer);
    hoverState.hideTimer = 0;
    const card = ensureHoverCard();
    const requestToken = hoverState.requestToken + 1;
    hoverState.requestToken = requestToken;
    hoverState.node?.removeAttribute("aria-describedby");
    hoverState.node = node;
    node.setAttribute("aria-describedby", card.id);
    const navigationRoute = navigationReferenceRoute(address);
    if (navigationRoute) {
      const meta = resourceTypeLabel(navigationRoute.referenceType);
      renderHoverCard(card, {
        title: navigationRoute.title || meta,
        meta,
        resourceId: address,
        hint: node.dataset.resourceHoverHint || "",
        state: "resolved",
      });
      node.setAttribute(
        "aria-label",
        `${navigationRoute.title || meta}，${meta}，${address}`,
      );
      card.hidden = false;
      hoverState.overlay?.activate({ trigger: node });
      positionHoverCard(node, card);
      return;
    }
    renderHoverCard(card, {
      title: window.PortalI18n.t("正在读取 Resource…"),
      resourceId: address.split("/", 1)[0],
      hint: node.dataset.resourceHoverHint || "",
      state: "loading",
    });
    card.hidden = false;
    hoverState.overlay?.activate({ trigger: node });
    positionHoverCard(node, card);
    try {
      const reference = await loadReference(address);
      if (
        requestToken !== hoverState.requestToken
        || hoverState.node !== node
      ) return;
      const attributes = window.PortalUIPrimitives.objectOf(reference?.attributes);
      const projects = Array.isArray(attributes.project_ids)
        ? attributes.project_ids.map(value => String(value || "").trim()).filter(Boolean)
        : [];
      const status = String(attributes.status || "").trim();
      const meta = [
        resourceTypeLabel(reference.resource_type || resourceTypeFromAddress(address)),
        projects.length ? `Project · ${projects.join(", ")}` : "",
        status ? `Status · ${status}` : "",
      ].filter(Boolean).join(" · ");
      renderHoverCard(card, {
        title: reference.title || reference.resource_id || address,
        meta,
        resourceId: reference.resource_id || address.split("/", 1)[0],
        hint: node.dataset.resourceHoverHint || "",
        state: "resolved",
      });
      node.setAttribute(
        "aria-label",
        `${reference.title || reference.resource_id || address}，${meta || window.PortalI18n.t("Resource")}，${reference.resource_id || address}`,
      );
      positionHoverCard(node, card);
    } catch (_error) {
      if (
        requestToken !== hoverState.requestToken
        || hoverState.node !== node
      ) return;
      renderHoverCard(card, {
        title: window.PortalI18n.t("Resource 已删除或不可用"),
        meta: resourceTypeLabel(resourceTypeFromAddress(address)),
        resourceId: address.split("/", 1)[0],
        hint: window.PortalI18n.t("点击时不会跳转"),
        state: "unavailable",
      });
      positionHoverCard(node, card);
    }
  }

  function resourcePreviewHandler(options = {}) {
    if (typeof options.onResourcePreview === "function") return options.onResourcePreview;
    if (typeof global.PortalPreview?.request !== "function") return null;
    return context => global.PortalPreview.request({
      ...context,
      resourceAddress: context.address,
      path: context.address,
      title: context.node?.textContent?.trim() || context.address,
      source: "resource",
      target: options.target || context.node,
    });
  }

  function updateChipPresentation(node) {
    const address = portalReferenceFromNode(node);
    const handler = resourcePreviewHandler(chipOptions.get(node));
    const supportsPreview = resourceSupportsPreview(address);
    const navigationRoute = navigationReferenceRoute(address);
    const targetLabel = navigationRoute?.title || window.PortalI18n.t("Resource");
    const actionHint = supportsPreview && typeof handler === "function"
      ? window.PortalI18n.t("点击在 Preview 中打开 · Ctrl/⌘ 点击新标签页")
      : window.PortalI18n.text`点击打开 ${targetLabel} · Ctrl/⌘ 点击新标签页`;
    node.dataset.resourceHoverHint = actionHint;
    node.removeAttribute("title");
    node.setAttribute("aria-label", `${address}，${actionHint}`);
    if (supportsPreview && typeof handler === "function") {
      node.dataset.portalResourcePreviewBound = "1";
      node.dataset.resourcePreviewKind = resourcePreviewKind("", address);
    } else {
      delete node.dataset.portalResourcePreviewBound;
      delete node.dataset.resourcePreviewKind;
    }
  }

  async function activateResourceNode(node, address, options = {}) {
    hideHoverCard({ immediate: true });
    if (node.getAttribute("aria-busy") === "true") return;
    const href = node.getAttribute("href") || portalReferenceHref(address);
    let navigationHref = href;
    const navigate = () => {
      if (options.openInNewTab) global.open(navigationHref, "_blank", "noopener");
      else global.location.assign(navigationHref);
    };
    if (navigationReferenceRoute(address)) {
      navigate();
      return;
    }
    node.setAttribute("aria-busy", "true");
    try {
      const reference = await loadReference(address, {
        allowCached: false,
      });
      const navigationResource = resourceUsesNavigation(address, reference);
      if (navigationResource) {
        navigationHref = navigationHrefForReference(address, href, reference?.url);
      }
      const handler = resourcePreviewHandler(options);
      if (
        !options.openInNewTab
        && !navigationResource
        && resourceSupportsPreview(address, reference)
        && typeof handler === "function"
      ) {
        let preview;
        try {
          preview = await loadPreview(address, { reference });
        } catch (error) {
          global.PortalUI.toast(window.PortalI18n.text`资源预览读取失败，请重试：${error.message || error}`, "error");
          return;
        }
        try {
          const handled = await handler({
            address,
            href,
            node,
            load: () => Promise.resolve(preview),
            navigate,
          });
          if (handled === false) navigate();
        } catch (_error) {
          navigate();
        }
        return;
      }
      navigate();
    } catch (error) {
      if (error?.retryable) showServiceUnavailableNotice(error);
      else showUnavailableNotice(address);
    } finally {
      node.removeAttribute("aria-busy");
    }
  }

  function bindChip(node) {
    if (node.dataset.portalResourceChipBound === "1") return;
    node.dataset.portalResourceChipBound = "1";
    const address = portalReferenceFromNode(node);
    bindResourceFileMenu(node, () => chipOptions.get(node) || {});
    node.addEventListener("mouseenter", () => {
      global.clearTimeout(hoverState.showTimer);
      hoverState.showTimer = global.setTimeout(
        () => showHoverCard(node, address),
        180,
      );
    });
    node.addEventListener("mouseleave", () => hideHoverCard());
    node.addEventListener("focus", () => {
      global.clearTimeout(hoverState.showTimer);
      showHoverCard(node, address);
    });
    node.addEventListener("blur", () => hideHoverCard());
    node.addEventListener("click", async event => {
      if (event.button !== 0 || event.shiftKey || event.altKey) return;
      const openInNewTab = event.metaKey || event.ctrlKey;
      event.preventDefault();
      event.stopPropagation();
      await activateResourceNode(node, address, {
        ...(chipOptions.get(node) || {}),
        openInNewTab,
      });
    });
  }

  function resourceLinkNodes(root) {
    if (!root) return [];
    const nodes = [];
    if (root.matches?.("a[href]:not(.portalResourceChip)")) nodes.push(root);
    root.querySelectorAll?.("a[href]:not(.portalResourceChip)")
      .forEach(node => nodes.push(node));
    return nodes.filter(node => (
      portalReferenceFromPortalHref(node.getAttribute("href") || "")
    ));
  }

  function bindResourceLink(node, options = {}) {
    resourceLinkOptions.set(node, {
      ...(resourceLinkOptions.get(node) || {}),
      ...options,
    });
    if (node.dataset.portalResourceLinkBound === "1") return;
    node.dataset.portalResourceLinkBound = "1";
    bindResourceFileMenu(node, () => resourceLinkOptions.get(node) || {});
    node.addEventListener("click", async event => {
      if (
        event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
      ) return;
      const address = portalReferenceFromPortalHref(
        node.getAttribute("href") || "",
      );
      if (!address) return;
      event.preventDefault();
      event.stopPropagation();
      await activateResourceNode(node, address, resourceLinkOptions.get(node) || {});
    });
  }

  function bindResourceLinks(root, options = {}) {
    resourceLinkNodes(root).forEach(node => bindResourceLink(node, options));
    return root;
  }

  function bindResourceFileMenu(node, options) {
    node.addEventListener("contextmenu", event => {
      const address = portalReferenceFromNode(node)
        || portalReferenceFromPortalHref(node.getAttribute("href") || "");
      if (!resourceSupportsPreview(address) || resourceUsesNavigation(address)) return;
      const handler = resourcePreviewHandler(options());
      if (!handler) return;
      global.PortalPreview?.openFileMenu?.({
        event, trigger: node,
        request: { resourceAddress: address, path: address, title: node.textContent?.trim() || address },
        onOpen: request => handler({ ...request, address, node, load: () => loadPreview(address) }),
      });
    });
  }

  function resourceChipNodes(root) {
    if (!root) return [];
    const nodes = [];
    if (root.matches?.(".portalResourceChip[data-resource-address], .portalResourceChip[data-navigation-reference]")) {
      nodes.push(root);
    }
    root.querySelectorAll?.(".portalResourceChip[data-resource-address], .portalResourceChip[data-navigation-reference]")
      .forEach(node => nodes.push(node));
    return nodes;
  }

  function bind(root, options = {}) {
    bindResourceLabels(root, options);
    resourceChipNodes(root).forEach(node => enhanceChip(node, options));
    return root;
  }

  function enhanceChip(node, options = {}) {
      if (!chipOptions.has(node) || Object.keys(options || {}).length > 0) {
        chipOptions.set(node, {
          ...(chipOptions.get(node) || {}),
          ...(options || {}),
        });
      }
      updateChipPresentation(node);
      bindChip(node);
  }

  function enhance(root, options = {}) {
    linkifyElement(root);
    bind(root, options);
    bindResourceLinks(root, options);
    return root;
  }

  function initializeBindings() {
    const start = () => {
      global.PortalUIPrimitives.registerEnhancer({
        id: "resource-labels", selector: "[data-resource-label]", enhance: bindResourceLabel,
      });
      global.PortalUIPrimitives.registerEnhancer({
        id: "resource-chips",
        selector: ".portalResourceChip[data-resource-address], .portalResourceChip[data-navigation-reference]",
        enhance: enhanceChip,
      });
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
      start();
    }
  }

  global.PortalResourceChips = Object.freeze({
    bind,
    bindResourceLinks,
    enhance,
    isRootId,
    linkifyHtml,
    loadReference,
    loadPreview,
    loadTextPreview,
    navigationReferenceFromPortalHref,
    normalizeResourceAddress,
    portalReferenceFromPortalHref,
    resourceChipElement,
    resourceChipHtml,
    resourceLabelHtml,
    resourceAddressFromPortalHref,
    resourceAddressFromPreviewRequest,
    resourceContentTypeIsText,
    resourcePreviewKind,
    resourceSupportsPreview,
    resourceSupportsTextPreview,
    resourceTypeFromAddress,
    resourceUsesNavigation,
    showUnavailableNotice,
  });

  initializeBindings();
})(window);
