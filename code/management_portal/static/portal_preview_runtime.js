(() => {
  "use strict";

  if (window.PortalPreview) return;
  if (!window.PortalPreviewContract) throw new Error("portal_preview_contract.js must load before portal_preview_runtime.js");

  const hosts = new Map();
  const sources = new Map();
  const renderers = [];
  const mounted = new WeakMap();
  const contract = window.PortalPreviewContract;
  let fileMenu = null;

  const escapeHtml = window.PortalUIPrimitives.escapeHtml;

  function registerHost(name, handler, options = {}) {
    const id = String(name || "").trim();
    if (!id || typeof handler !== "function") throw new TypeError("Preview host id and handler required");
    const registration = { id, handler, priority: Number(options.priority || 0) };
    hosts.set(id, registration);
    return () => hosts.get(id) === registration && hosts.delete(id);
  }

  async function request(request = {}) {
    const normalized = contract.normalizeRequest(request);
    if (!normalized.resourceAddress && !normalized.href && !normalized.path && !normalized.identity) return false;
    const ordered = [...hosts.values()].sort((left, right) => right.priority - left.priority);
    for (const host of ordered) {
      if (await host.handler(normalized) !== false) return true;
    }
    return false;
  }

  async function downloadFile(input) {
    const target = contract.normalizeRequest(input);
    let document;
    if (target.resourceAddress) {
      document = await window.PortalResourceChips.loadPreview(target.resourceAddress);
    } else {
      const file = window.PortalFileLinks.describe(target.path, { allowAnyLocalPath: true });
      if (!file) throw new Error(window.PortalI18n.t("无法定位下载文件"));
      const metadata = await window.PortalFileLinks.probe(file.path, { throwOnError: true });
      if (!metadata) throw new Error(window.PortalI18n.t("此项目不支持下载"));
      document = { ...metadata, downloadUrl: window.PortalFileLinks.downloadUrl(metadata.path || file.path) };
    }
    const payload = document && contract.normalizeDocument(document);
    if (!payload || payload.contentKind === "directory" || payload.capabilities.download === false || !payload.downloadUrl) {
      throw new Error(window.PortalI18n.t("此项目不支持下载"));
    }
    const link = window.document.createElement("a");
    configureDownloadLink(link, { url: payload.downloadUrl, filename: payload.downloadName });
    link.hidden = true;
    window.document.body.appendChild(link);
    link.click();
    link.remove();
  }

  // The path "copy path" puts on the clipboard: a Portal resource keeps its
  // address, a file link its resolved absolute path without the line locator.
  function fileMenuPath(target) {
    if (target.resourceAddress) return target.resourceAddress;
    const raw = String(target.path || target.href || "").trim();
    if (!raw) return "";
    return window.PortalFileLinks?.describe?.(raw, { allowAnyLocalPath: true })?.path || raw;
  }

  function openFileMenu({ event, trigger, request: input, onOpen = request } = {}) {
    if (!input) return false;
    const target = contract.normalizeRequest(input);
    event?.preventDefault();
    event?.stopPropagation();
    if (!fileMenu) {
      const panel = document.createElement("div");
      panel.className = "portalEditorOpenMenu";
      panel.setAttribute("role", "menu");
      panel.setAttribute("aria-label", window.PortalI18n.t("文件操作"));
      document.body.appendChild(panel);
      let context = null;
      const popover = window.PortalOverlay.bindFloatingPopover({
        panel, portal: true, placement: "bottom", align: "start", margin: 8,
        closeOnOutside: true, closeOnEscape: true,
        onClose: () => {
          if (panel.contains(document.activeElement) && context?.trigger?.isConnected) {
            context.trigger.focus({ preventScroll: true });
          }
          context = null;
        },
      });
      panel.addEventListener("click", event => {
        const button = event.target.closest("[data-preview-file-action]");
        if (!button || !context) return;
        event.preventDefault();
        event.stopPropagation();
        const selected = context;
        const action = button.dataset.previewFileAction;
        popover.close(event);
        if (action === "copy-path") {
          void window.PortalUI.copyText(fileMenuPath(selected.target), window.PortalI18n.t("文件路径"));
          return;
        }
        Promise.resolve().then(() => action === "download"
          ? downloadFile(selected.target)
          : selected.onOpen({ ...selected.target, mode: "new-tab" })
        ).then(handled => {
          if (handled === false) throw new Error(window.PortalI18n.t("当前页面无法打开此文件"));
        }).catch(error => window.PortalUI?.toast?.(error.message || String(error), "error"));
      });
      panel.addEventListener("keydown", event => {
        const buttons = [...panel.querySelectorAll("[role=menuitem]")];
        const index = buttons.indexOf(document.activeElement);
        const direction = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
        if (!direction) return;
        event.preventDefault();
        buttons[(index + direction + buttons.length) % buttons.length]?.focus();
      });
      fileMenu = {
        open(next) {
          popover.close();
          context = next;
          const downloadable = next.target.resourceAddress || window.PortalFileLinks?.describe(next.target.path);
          panel.innerHTML = window.PortalI18n.html`<button type="button" role="menuitem" class="portalEditorOpenMenuAction" data-preview-file-action="new-tab">在新标签页打开</button>${downloadable ? window.PortalI18n.html`<button type="button" role="menuitem" class="portalEditorOpenMenuAction" data-preview-file-action="download">下载</button>` : ""}${window.PortalI18n.html`<button type="button" role="menuitem" class="portalEditorOpenMenuAction" data-preview-file-action="copy-path">复制路径</button>`}`;
          const copyPath = panel.querySelector("[data-preview-file-action=copy-path]");
          const copyValue = fileMenuPath(next.target);
          if (copyValue) copyPath.title = copyValue;
          else copyPath.remove();
          const rect = next.trigger?.getBoundingClientRect();
          const point = next.event?.type === "contextmenu" && (next.event.clientX || next.event.clientY)
            ? { x: next.event.clientX, y: next.event.clientY }
            : { x: rect?.right || 0, y: rect?.bottom || 0 };
          popover.open(point);
          panel.querySelector("[role=menuitem]")?.focus({ preventScroll: true });
        },
      };
    }
    fileMenu.open({ event, trigger, target, onOpen });
    return true;
  }

  function registerSource(adapter) {
    contract.assertSourceAdapter(adapter);
    const id = String(adapter.id).trim();
    if (sources.has(id)) throw new Error(`Preview source already registered: ${id}`);
    sources.set(id, adapter);
    let active = true;
    return () => {
      if (!active) return false;
      active = false;
      return sources.delete(id);
    };
  }

  function source(id) {
    return sources.get(String(id || "").trim()) || null;
  }

  async function load(request = {}, context = {}) {
    const normalized = contract.normalizeRequest(request);
    const adapter = source(normalized.source);
    if (!adapter) throw new Error(`Preview source is not registered: ${normalized.source}`);
    const payload = await adapter.load(normalized, context);
    return contract.normalizeDocument({ ...payload, source: normalized.source });
  }

  async function save(document, draft, context = {}) {
    const normalized = contract.normalizeDocument(document);
    const adapter = source(normalized.source);
    if (!adapter?.save) throw new Error(`Preview source is read-only: ${normalized.source}`);
    return contract.normalizeDocument(await adapter.save(normalized, String(draft ?? ""), context));
  }

  function registerRenderer(renderer, options = {}) {
    contract.assertRenderer(renderer);
    const id = String(renderer.id).trim();
    const existing = renderers.findIndex(item => item.id === id);
    if (existing >= 0) throw new Error(`Preview renderer already registered: ${id}`);
    const entry = { ...renderer, priority: Number(options.priority ?? renderer.priority ?? 0) };
    renderers.push(entry);
    renderers.sort((left, right) => right.priority - left.priority);
    return () => {
      const index = renderers.indexOf(entry);
      if (index >= 0) renderers.splice(index, 1);
    };
  }

  function rendererFor(document, context = {}) {
    return renderers.find(renderer => renderer.accepts(document, context)) || null;
  }

  function dispose(root) {
    const previous = root ? mounted.get(root) : null;
    if (!previous) return false;
    mounted.delete(root);
    try { previous.dispose?.(); } catch (_error) {}
    return true;
  }

  function createFrameCoalescer(callback, onError) {
    if (typeof callback !== "function") throw new TypeError("PortalPreview.createFrameCoalescer callback required");
    let disposed = false;
    let frame = 0;
    let pending = null;

    const cancel = () => {
      window.cancelAnimationFrame(frame);
      frame = 0;
      pending = null;
    };
    const flush = () => {
      window.cancelAnimationFrame(frame);
      frame = 0;
      if (disposed || !pending) return false;
      const payload = pending;
      pending = null;
      try {
        const result = callback(payload);
        Promise.resolve(result).catch(error => onError?.(error));
        return result;
      } catch (error) {
        onError?.(error);
        return false;
      }
    };
    const schedule = payload => {
      if (disposed) return false;
      pending = payload;
      if (!frame) frame = window.requestAnimationFrame(flush);
      return true;
    };

    return Object.freeze({
      cancel,
      dispose() {
        if (disposed) return;
        cancel();
        disposed = true;
      },
      flush,
      pending: () => pending,
      schedule,
    });
  }

  function mountZoom(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    const viewport = typeof options.viewport === "string" ? root?.querySelector(options.viewport) : options.viewport;
    if (!root || !viewport) throw new TypeError("PortalPreview.mountZoom root and viewport required");
    const minimum = Math.max(0.05, Number(options.min || 0.25));
    const maximum = Math.max(minimum, Number(options.max || 6));
    const step = Math.max(0.01, Number(options.step || 0.1));
    let zoom = Math.max(minimum, Math.min(maximum, Number(options.initial || 1)));
    let enabled = options.enabled !== false;
    let disposed = false;
    let commitTimer = 0;
    let touchGesture = null;
    let safariGesture = null;
    let wheelGesture = null;
    let restoreFrame = 0;
    let latestAnchor = null;

    const controls = document.createElement("div");
    controls.className = "portalPreviewZoomControls";
    controls.dataset.previewZoomControls = "";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", window.PortalI18n.t("预览缩放"));
    controls.innerHTML = window.PortalI18n.html`
      <button class="portalPreviewZoomButton" type="button" data-preview-zoom-out aria-label="缩小预览" title="缩小预览">−</button>
      <button class="portalPreviewZoomValue" type="button" data-preview-zoom-reset aria-label="恢复适合窗口大小" title="恢复适合窗口大小">100%</button>
      <button class="portalPreviewZoomButton" type="button" data-preview-zoom-in aria-label="放大预览" title="放大预览">+</button>`;
    root.classList.add("portalPreviewZoomHost");
    root.appendChild(controls);
    const zoomOut = controls.querySelector("[data-preview-zoom-out]");
    const zoomReset = controls.querySelector("[data-preview-zoom-reset]");
    const zoomIn = controls.querySelector("[data-preview-zoom-in]");

    const updateControls = () => {
      root.dataset.previewZoom = String(zoom);
      zoomReset.textContent = `${Math.round(zoom * 100)}%`;
      zoomOut.disabled = !enabled || zoom <= minimum + 0.001;
      zoomIn.disabled = !enabled || zoom >= maximum - 0.001;
      zoomReset.disabled = !enabled;
      controls.classList.toggle("is-disabled", !enabled);
    };

    const normalizeViewportPoint = (point = {}) => {
      const bounds = viewport.getBoundingClientRect();
      const candidateX = Number(point.clientX);
      const candidateY = Number(point.clientY);
      const inside = Number.isFinite(candidateX)
        && Number.isFinite(candidateY)
        && candidateX >= bounds.left
        && candidateX <= bounds.right
        && candidateY >= bounds.top
        && candidateY <= bounds.bottom;
      return inside
        ? { clientX: candidateX, clientY: candidateY }
        : { clientX: bounds.left + bounds.width / 2, clientY: bounds.top + bounds.height / 2 };
    };

    const captureDefaultAnchor = point => {
      const bounds = viewport.getBoundingClientRect();
      const { clientX, clientY } = normalizeViewportPoint(point);
      const localX = Math.max(0, Math.min(bounds.width, clientX - bounds.left));
      const localY = Math.max(0, Math.min(bounds.height, clientY - bounds.top));
      return {
        localX,
        localY,
        xRatio: (viewport.scrollLeft + localX) / Math.max(1, viewport.scrollWidth),
        yRatio: (viewport.scrollTop + localY) / Math.max(1, viewport.scrollHeight),
      };
    };

    const captureAnchor = point => (
      typeof options.captureAnchor === "function"
        ? options.captureAnchor(point)
        : captureDefaultAnchor(point)
    );

    const restoreAnchor = anchor => {
      window.cancelAnimationFrame(restoreFrame);
      restoreFrame = window.requestAnimationFrame(() => {
        restoreFrame = 0;
        if (disposed) return;
        if (typeof options.restoreAnchor === "function") {
          options.restoreAnchor(anchor);
          return;
        }
        viewport.scrollLeft = (anchor.xRatio * viewport.scrollWidth) - anchor.localX;
        viewport.scrollTop = (anchor.yRatio * viewport.scrollHeight) - anchor.localY;
      });
    };

    const retargetAnchor = (anchor, point = {}) => {
      if (!anchor) return captureAnchor(point);
      const normalizedPoint = normalizeViewportPoint(point);
      if (typeof options.retargetAnchor === "function") return options.retargetAnchor(anchor, normalizedPoint);
      const bounds = viewport.getBoundingClientRect();
      const { clientX, clientY } = normalizedPoint;
      return {
        ...anchor,
        localX: Math.max(0, Math.min(bounds.width, clientX - bounds.left)),
        localY: Math.max(0, Math.min(bounds.height, clientY - bounds.top)),
      };
    };

    const commit = () => {
      window.clearTimeout(commitTimer);
      commitTimer = 0;
      wheelGesture = null;
      if (disposed || typeof options.onCommit !== "function") return;
      Promise.resolve(options.onCommit(zoom, { anchor: latestAnchor, restoreAnchor }))
        .catch(error => options.onError?.(error));
    };

    const scheduleCommit = (delay = Number(options.commitDelay ?? 180)) => {
      window.clearTimeout(commitTimer);
      commitTimer = window.setTimeout(commit, Math.max(0, delay));
    };

    const setZoom = (value, point = {}, config = {}) => {
      if (disposed || (!enabled && !config.force)) return false;
      const next = Math.round(Math.max(minimum, Math.min(maximum, Number(value) || 1)) * 100) / 100;
      if (!config.force && Math.abs(next - zoom) < 0.001) return false;
      const previous = zoom;
      const normalizedPoint = normalizeViewportPoint(point);
      const anchor = config.anchor || captureAnchor(normalizedPoint);
      latestAnchor = anchor;
      zoom = next;
      const changed = options.onChange?.(zoom, { previous, point: normalizedPoint, anchor }) !== false;
      updateControls();
      if (changed && options.restoreOnChange !== false) restoreAnchor(anchor);
      options.onZoom?.(zoom, { previous, point: normalizedPoint, changed });
      if (config.commit !== false) scheduleCommit(config.commitDelay);
      return changed;
    };

    const zoomFrames = createFrameCoalescer(
      pending => setZoom(pending.value, pending.point, pending.config),
      options.onError,
    );

    const reset = point => setZoom(1, point, { commitDelay: 0 });
    const touchDistance = touches => Math.hypot(
      touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY,
    );
    const touchMidpoint = touches => ({
      clientX: (touches[0].clientX + touches[1].clientX) / 2,
      clientY: (touches[0].clientY + touches[1].clientY) / 2,
    });
    const listeners = [];
    const listen = (target, type, handler, config) => {
      target.addEventListener(type, handler, config);
      listeners.push(() => target.removeEventListener(type, handler, config));
    };

    listen(zoomOut, "click", () => setZoom(zoom - step, {}, { commitDelay: 0 }));
    listen(zoomIn, "click", () => setZoom(zoom + step, {}, { commitDelay: 0 }));
    listen(zoomReset, "click", () => reset());
    listen(viewport, "wheel", event => {
      if (!enabled || !event.ctrlKey || touchGesture || safariGesture) return;
      event.preventDefault();
      const point = normalizeViewportPoint({ clientX: event.clientX, clientY: event.clientY });
      if (!wheelGesture) wheelGesture = { anchor: captureAnchor(point) };
      const queuedZoom = Number(zoomFrames.pending()?.value || zoom);
      zoomFrames.schedule({
        value: queuedZoom * Math.exp(-event.deltaY * 0.01),
        point,
        config: { anchor: retargetAnchor(wheelGesture.anchor, point) },
      });
    }, { passive: false });
    listen(viewport, "touchstart", event => {
      if (!enabled || safariGesture || event.touches.length !== 2) return;
      event.preventDefault();
      const point = touchMidpoint(event.touches);
      touchGesture = {
        anchor: captureAnchor(point),
        distance: Math.max(1, touchDistance(event.touches)),
        zoom,
      };
    }, { passive: false });
    listen(viewport, "touchmove", event => {
      if (!enabled || !touchGesture || event.touches.length !== 2) return;
      event.preventDefault();
      const point = touchMidpoint(event.touches);
      zoomFrames.schedule({
        value: touchGesture.zoom * (touchDistance(event.touches) / touchGesture.distance),
        point,
        config: { anchor: retargetAnchor(touchGesture.anchor, point) },
      });
    }, { passive: false });
    const finishTouch = () => {
      if (!touchGesture) return;
      touchGesture = null;
      zoomFrames.flush();
      scheduleCommit(0);
    };
    listen(viewport, "touchend", finishTouch, { passive: true });
    listen(viewport, "touchcancel", finishTouch, { passive: true });
    listen(viewport, "gesturestart", event => {
      if (!enabled || touchGesture) return;
      event.preventDefault();
      const point = { clientX: event.clientX, clientY: event.clientY };
      safariGesture = { anchor: captureAnchor(point), zoom };
    }, { passive: false });
    listen(viewport, "gesturechange", event => {
      if (!enabled || !safariGesture) return;
      event.preventDefault();
      const point = { clientX: event.clientX, clientY: event.clientY };
      zoomFrames.schedule({
        value: safariGesture.zoom * Number(event.scale || 1),
        point,
        config: { anchor: retargetAnchor(safariGesture.anchor, point) },
      });
    }, { passive: false });
    listen(viewport, "gestureend", () => {
      if (!safariGesture) return;
      safariGesture = null;
      zoomFrames.flush();
      scheduleCommit(0);
    }, { passive: true });

    updateControls();
    return Object.freeze({
      dispose() {
        if (disposed) return;
        disposed = true;
        window.clearTimeout(commitTimer);
        window.cancelAnimationFrame(restoreFrame);
        zoomFrames.dispose();
        listeners.splice(0).forEach(remove => remove());
        controls.remove();
        root.classList.remove("portalPreviewZoomHost");
        delete root.dataset.previewZoom;
      },
      reset,
      reapply(point = {}) { return setZoom(zoom, point, { force: true, commit: false }); },
      setEnabled(value) { enabled = Boolean(value); updateControls(); },
      setZoom,
      zoom: () => zoom,
    });
  }

  function render(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new TypeError("PortalPreview.render root required");
    const documentValue = contract.normalizeDocument(options.document || options.payload || {});
    const context = Object.freeze({
      escapeHtml,
      onPreviewRequest: options.onPreviewRequest || request,
      ...options.context,
    });
    dispose(root);
    const renderer = rendererFor(documentValue, context);
    if (!renderer) throw new Error(`No Preview renderer accepts ${documentValue.contentKind}`);
    const result = renderer.render(root, documentValue, context) || {};
    const handle = Object.freeze({
      document: documentValue,
      renderer: renderer.id,
      dispose: typeof result === "function" ? result : result.dispose,
      promise: result?.promise || Promise.resolve(result),
    });
    mounted.set(root, handle);
    return handle;
  }

  function configureDownloadLink(link, options = {}) {
    if (!link) return false;
    const url = String(options.url || "").trim();
    const filename = String(options.filename || "")
      .replaceAll("\\", "/")
      .split("/")
      .at(-1);
    link.hidden = !url;
    link.setAttribute("aria-disabled", url ? "false" : "true");
    if (!url) {
      link.removeAttribute("href");
      link.removeAttribute("download");
      link.removeAttribute("target");
      link.removeAttribute("rel");
      return false;
    }
    link.href = url;
    link.setAttribute("download", filename);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return true;
  }

  window.PortalPreview = Object.freeze({
    configureDownloadLink,
    createFrameCoalescer,
    dispose,
    load,
    normalizeDocument: contract.normalizeDocument,
    registerHost,
    registerRenderer,
    openFileMenu,
    registerSource,
    render,
    mountZoom,
    request,
    save,
    source,
  });
})();
