(() => {
  "use strict";

  if (window.PortalPdfPreview) return;

  const activeByRoot = new WeakMap();
  const documentCache = new Map();
  const MAX_CACHED_DOCUMENTS = 4;
  let pdfJsPromise = null;

  const escapeHtml = window.PortalUIPrimitives.escapeHtml;

  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (pdfJsPromise) return pdfJsPromise;
    pdfJsPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-portal-pdfjs]');
      const script = existing || document.createElement("script");
      const complete = () => window.pdfjsLib
        ? resolve(window.pdfjsLib)
        : reject(new Error("PDF.js loaded without exposing pdfjsLib"));
      script.addEventListener("load", complete, { once: true });
      script.addEventListener("error", () => reject(new Error("Unable to load PDF.js")), { once: true });
      if (!existing) {
        script.src = "/static/vendor/pdfjs/pdf.min.js";
        script.dataset.portalPdfjs = "1";
        document.head.appendChild(script);
      }
    }).catch(error => {
      pdfJsPromise = null;
      throw error;
    });
    return pdfJsPromise;
  }

  function trimDocumentCache() {
    if (documentCache.size <= MAX_CACHED_DOCUMENTS) return;
    const removable = [...documentCache.values()]
      .filter(entry => entry.refs === 0 && entry.pdf)
      .sort((left, right) => left.lastUsed - right.lastUsed);
    while (documentCache.size > MAX_CACHED_DOCUMENTS && removable.length) {
      const entry = removable.shift();
      documentCache.delete(entry.key);
      try { entry.pdf?.destroy?.(); } catch (_error) {}
    }
  }

  function acquireDocument(pdfjs, requestUrl, options) {
    const key = `${requestUrl}|${options.withCredentials ?? requestUrl.startsWith("/")}`;
    let entry = documentCache.get(key);
    if (!entry) {
      const loadingTask = pdfjs.getDocument({
        url: requestUrl,
        withCredentials: options.withCredentials ?? requestUrl.startsWith("/"),
        cMapUrl: "/static/vendor/pdfjs/cmaps/",
        cMapPacked: true,
        disableAutoFetch: true,
        disableStream: true,
        rangeChunkSize: 262144,
      });
      entry = {
        key,
        refs: 0,
        pdf: null,
        loadingTask,
        lastUsed: Date.now(),
        promise: null,
      };
      entry.promise = loadingTask.promise.then(pdf => {
        entry.loadingTask = null;
        entry.pdf = pdf;
        entry.lastUsed = Date.now();
        trimDocumentCache();
        return pdf;
      }).catch(error => {
        documentCache.delete(key);
        throw error;
      });
      documentCache.set(key, entry);
    }
    entry.refs += 1;
    entry.lastUsed = Date.now();
    return entry;
  }

  function releaseDocument(active) {
    const entry = active?.documentEntry;
    if (!entry) return;
    entry.refs = Math.max(0, entry.refs - 1);
    entry.lastUsed = Date.now();
    active.documentEntry = null;
    trimDocumentCache();
  }

  function cancel(root) {
    const active = root ? activeByRoot.get(root) : null;
    if (!active) return false;
    active.cancelled = true;
    active.observer?.disconnect?.();
    for (const task of active.renderTasks || []) {
      try { task?.cancel?.(); } catch (_error) {}
    }
    for (const task of active.textLayerTasks || []) {
      try { task?.cancel?.(); } catch (_error) {}
    }
    active.observer = null;
    active.renderTasks?.clear?.();
    active.textLayerTasks?.clear?.();
    active.pdf = null;
    releaseDocument(active);
    activeByRoot.delete(root);
    return true;
  }

  function iframeFallback(root, options, message = "") {
    if (!root) return;
    root.dataset.pdfStatus = "fallback";
    root.innerHTML = window.PortalI18n.html`
      ${message ? window.PortalI18n.html`<div class="${escapeHtml(options.statusClass)}">${escapeHtml(message)}</div>` : ""}
      <iframe src="${escapeHtml(options.url)}" title="${escapeHtml(options.title)} PDF"></iframe>`;
    options.onFallback?.(root, message);
  }

  function render(config = {}) {
    const root = typeof config.root === "string" ? document.querySelector(config.root) : config.root;
    if (!root) throw new TypeError("PortalPdfPreview.render root required");
    const url = String(config.url || "").trim();
    const options = {
      url,
      title: String(config.title || window.PortalI18n.t("Preview")),
      statusClass: String(config.statusClass || "previewPdfStatus"),
      pageClass: String(config.pageClass || "previewPdfPage"),
      textLayerClass: String(config.textLayerClass || "textLayer"),
      minPageWidth: Math.max(240, Number(config.minPageWidth || 300)),
      maxPageWidth: Math.max(300, Number(config.maxPageWidth || 980)),
      minScale: Math.max(0.1, Number(config.minScale || 0.45)),
      maxScale: Math.max(0.1, Number(config.maxScale || 2)),
      scaleMultiplier: Math.max(0.1, Number(config.scaleMultiplier || 1)),
      horizontalPadding: Math.max(0, Number(config.horizontalPadding || 28)),
      outputScale: config.outputScale,
      version: String(config.version || "").trim(),
      withCredentials: config.withCredentials,
      ensurePdfJs: config.ensurePdfJs,
      onPage: config.onPage,
      onReady: config.onReady,
      onFallback: config.onFallback,
      onError: config.onError,
      loadingMessage: String(config.loadingMessage || ""),
      errorMessage: String(config.errorMessage || ""),
    };
    cancel(root);
    const active = {
      cancelled: false,
      documentEntry: null,
      gestureScaleMultiplier: null,
      observer: null,
      pdf: null,
      options,
      pages: [],
      recordsByPage: new Map(),
      recordsByElement: new WeakMap(),
      renderQueue: Promise.resolve(),
      renderTasks: new Set(),
      textLayerTasks: new Set(),
      pageStates: new WeakMap(),
    };
    activeByRoot.set(root, active);
    root.style.overflowAnchor = "none";
    root.dataset.pdfStatus = "loading";
    root.innerHTML = window.PortalI18n.html`<div class="${escapeHtml(options.statusClass)}" data-portal-pdf-status>${escapeHtml(options.loadingMessage || window.PortalI18n.t("Loading PDF…"))}</div>`;

    const promise = (async () => {
      if (!url) throw new Error("PDF URL is required");
      const pdfjs = await (typeof options.ensurePdfJs === "function" ? options.ensurePdfJs() : loadPdfJs());
      if (active.cancelled) return null;
      if (pdfjs?.GlobalWorkerOptions) {
        pdfjs.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.js";
      }
      const separator = url.includes("?") ? "&" : "?";
      const requestUrl = options.version ? `${url}${separator}v=${encodeURIComponent(options.version)}` : url;
      active.documentEntry = acquireDocument(pdfjs, requestUrl, options);
      const pdf = await active.documentEntry.promise;
      active.pdf = pdf;
      if (active.cancelled) return null;
      root.dataset.pdfStatus = "rendering";
      root.dataset.pdfPageCount = String(pdf.numPages);
      root.dataset.pdfInitializedPages = "0";
      const status = root.querySelector("[data-portal-pdf-status]");
      const updateScaleVariables = () => {
        const availableWidth = Math.max(options.minPageWidth, root.clientWidth - options.horizontalPadding);
        const targetWidth = Math.min(options.maxPageWidth, availableWidth);
        root.style.setProperty("--portal-pdf-fit-width", `${targetWidth}px`);
        root.style.setProperty("--portal-pdf-scale-multiplier", String(options.scaleMultiplier));
        return targetWidth;
      };
      updateScaleVariables();

      const updateRecordMetrics = (record, baseViewport, viewportForScale = scale => ({
        width: baseViewport.width * scale,
        height: baseViewport.height * scale,
        scale,
      })) => {
        const targetWidth = updateScaleVariables();
        const fitScale = targetWidth / baseViewport.width;
        const scale = Math.max(options.minScale, Math.min(options.maxScale, fitScale * options.scaleMultiplier));
        const viewport = viewportForScale(scale);
        record.baseViewport = baseViewport;
        record.fitScale = fitScale;
        record.scale = scale;
        record.viewport = viewport;
        record.pageElement.dataset.pdfScale = String(scale);
        record.pageElement.dataset.pdfFitScale = String(fitScale);
        record.pageElement.style.width = `clamp(${baseViewport.width * options.minScale}px, calc(var(--portal-pdf-fit-width) * var(--portal-pdf-scale-multiplier)), ${baseViewport.width * options.maxScale}px)`;
        record.pageElement.style.aspectRatio = `${baseViewport.width} / ${baseViewport.height}`;
        return record;
      };

      const updateRecordPage = (record, page) => {
        record.page = page;
        return updateRecordMetrics(record, page.getViewport({ scale: 1 }), scale => page.getViewport({ scale }));
      };

      const loadRecordPage = record => {
        if (record.page) return Promise.resolve(record);
        if (record.pagePromise) return record.pagePromise;
        record.pagePromise = pdf.getPage(record.pageNumber)
          .then(page => updateRecordPage(record, page))
          .finally(() => { record.pagePromise = null; });
        return record.pagePromise;
      };

      let renderedPages = 0;
      const enhanceWithTextLayer = async (record, revision, canvas, scale, viewport) => {
        record.pageElement.dataset.pdfTextLayerState = "loading";
        let textTask = null;
        try {
          const textContentSource = await record.page.getTextContent();
          if (active.cancelled || revision !== record.revision || !record.pageElement.contains(canvas)) return;
          const textLayer = document.createElement("div");
          textLayer.className = options.textLayerClass;
          textLayer.style.width = "100%";
          textLayer.style.height = "100%";
          textLayer.style.setProperty("--scale-factor", String(scale));
          textTask = pdfjs.renderTextLayer({
            textContentSource,
            container: textLayer,
            viewport,
            textDivs: [],
            enhanceTextSelection: true,
          });
          active.textLayerTasks.add(textTask);
          await textTask.promise;
          if (active.cancelled || revision !== record.revision || !record.pageElement.contains(canvas)) return;
          record.pageElement.appendChild(textLayer);
          record.textLayer = textLayer;
          record.pageElement.dataset.pdfTextLayerState = "done";
        } catch (_error) {
          if (!active.cancelled && revision === record.revision && record.pageElement.contains(canvas)) {
            record.pageElement.dataset.pdfTextLayerState = "error";
          }
        } finally {
          if (textTask) active.textLayerTasks.delete(textTask);
        }
      };

      const renderPage = async (record, revision) => {
        if (active.cancelled || revision !== record.revision) return;
        await loadRecordPage(record);
        if (active.cancelled || revision !== record.revision) return;
        const previouslyRendered = record.pageElement.dataset.pdfRenderState === "done";
        active.pageStates.set(record.pageElement, "rendering");
        record.pageElement.dataset.pdfRenderState = "rendering";
        const viewport = record.viewport;
        const scale = record.scale;

        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d");
        const outputScale = Math.max(1, Number(
          typeof options.outputScale === "function"
            ? options.outputScale(window.devicePixelRatio || 1)
            : options.outputScale || window.devicePixelRatio || 1,
        ));
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = "100%";
        canvas.style.height = "100%";
        context.setTransform(outputScale, 0, 0, outputScale, 0, 0);

        const renderTask = record.page.render({ canvasContext: context, viewport });
        active.renderTasks.add(renderTask);
        try {
          await renderTask.promise;
        } finally {
          active.renderTasks.delete(renderTask);
        }
        if (active.cancelled || revision !== record.revision) return null;
        record.pageElement.replaceChildren(canvas);
        record.canvas = canvas;
        record.textLayer = null;
        active.pageStates.set(record.pageElement, "done");
        record.pageElement.dataset.pdfRenderState = "done";
        record.pageElement.dataset.pdfTextLayerState = "pending";
        active.observer?.unobserve(record.pageElement);
        if (!previouslyRendered) renderedPages += 1;
        if (status) status.textContent = window.PortalI18n.text`Rendered ${renderedPages} / ${pdf.numPages} pages`;
        options.onPage?.({ ...record, scale, viewport, canvas, textLayer: null });
        await new Promise(resolve => window.requestAnimationFrame(resolve));
        void enhanceWithTextLayer(record, revision, canvas, scale, viewport);
      };

      const schedule = (record, force = false) => {
        const pageState = record && active.pageStates.get(record.pageElement);
        if (!record || active.cancelled || (!force && pageState !== "pending")) return;
        const revision = record.revision + 1;
        record.revision = revision;
        active.pageStates.set(record.pageElement, "queued");
        active.renderQueue = active.renderQueue
          .then(() => renderPage(record, revision))
          .catch(error => {
            if (active.cancelled || revision !== record.revision) return null;
            active.pageStates.set(record.pageElement, "error");
            record.pageElement.dataset.pdfRenderState = "error";
            record.pageElement.innerHTML = window.PortalI18n.html`<div class="${escapeHtml(options.statusClass)}">Unable to render page ${record.pageNumber}</div>`;
            options.onError?.(error);
            return null;
          });
      };
      active.schedule = schedule;

      if (typeof IntersectionObserver === "function") {
        active.observer = new IntersectionObserver(entries => {
          for (const entry of entries) {
            if (entry.isIntersecting) schedule(active.recordsByElement.get(entry.target));
          }
        }, { root, rootMargin: "800px 0px" });
      }

      const firstPage = await pdf.getPage(1);
      if (active.cancelled) return null;
      const firstBaseViewport = firstPage.getViewport({ scale: 1 });
      const createPageRecord = pageNumber => {
        const normalizedPage = Math.trunc(Number(pageNumber));
        if (normalizedPage < 1 || normalizedPage > pdf.numPages) {
          throw new RangeError(`PDF page ${pageNumber} is outside 1-${pdf.numPages}`);
        }
        const existing = active.recordsByPage.get(normalizedPage);
        if (existing) return existing;
        const pageElement = document.createElement("div");
        pageElement.className = options.pageClass;
        pageElement.dataset.pageNumber = String(normalizedPage);
        pageElement.dataset.pdfPage = String(normalizedPage);
        pageElement.dataset.pdfRenderState = "pending";
        pageElement.style.height = "auto";
        pageElement.innerHTML = window.PortalI18n.html`<div class="${escapeHtml(options.statusClass)}">Page ${normalizedPage}</div>`;
        const initialPage = normalizedPage === 1 ? firstPage : null;
        const record = {
          baseViewport: firstBaseViewport,
          fitScale: 1,
          page: initialPage,
          pageElement,
          pageNumber: normalizedPage,
          pagePromise: null,
          revision: 0,
          scale: 1,
          viewport: firstBaseViewport,
        };
        if (initialPage) updateRecordPage(record, initialPage);
        else updateRecordMetrics(record, firstBaseViewport);
        active.pages.push(record);
        active.recordsByPage.set(normalizedPage, record);
        active.recordsByElement.set(pageElement, record);
        active.pageStates.set(pageElement, "pending");
        return record;
      };
      active.ensurePageRecord = pageNumber => Promise.resolve(createPageRecord(pageNumber));

      const pageFragment = document.createDocumentFragment();
      for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
        pageFragment.appendChild(createPageRecord(pageNumber).pageElement);
      }
      root.appendChild(pageFragment);
      root.dataset.pdfInitializedPages = String(active.pages.length);
      for (const record of active.pages) {
        if (active.observer) active.observer.observe(record.pageElement);
        else schedule(record);
      }

      const firstPageRecord = active.recordsByPage.get(1);
      schedule(firstPageRecord);
      await active.renderQueue;
      if (active.cancelled) return null;
      status?.remove();
      root.dataset.pdfStatus = "done";
      options.onReady?.({ root, pdf, lazy: typeof IntersectionObserver === "function" });
      return pdf;
    })().catch(error => {
      if (active.cancelled) return null;
      if (options.errorMessage) {
        root.dataset.pdfStatus = "error";
        root.innerHTML = window.PortalI18n.html`<div class="${escapeHtml(options.statusClass)}" role="alert">${escapeHtml(options.errorMessage)}</div>`;
      } else {
        iframeFallback(root, options, `PDF.js could not load this PDF: ${error?.message || error}`);
      }
      options.onError?.(error);
      return null;
    });

    const previewScale = ({ scaleMultiplier = options.scaleMultiplier } = {}) => {
      if (active.cancelled || !active.pages.length) return false;
      options.scaleMultiplier = Math.max(0.1, Number(scaleMultiplier || 1));
      const availableWidth = Math.max(options.minPageWidth, root.clientWidth - options.horizontalPadding);
      const targetWidth = Math.min(options.maxPageWidth, availableWidth);
      root.style.setProperty("--portal-pdf-fit-width", `${targetWidth}px`);
      root.style.setProperty("--portal-pdf-scale-multiplier", String(options.scaleMultiplier));
      root.dataset.pdfPreviewScaleCount = String(Number(root.dataset.pdfPreviewScaleCount || 0) + 1);
      for (const record of active.pages) {
        const fitScale = targetWidth / record.baseViewport.width;
        const scale = Math.max(options.minScale, Math.min(options.maxScale, fitScale * options.scaleMultiplier));
        const viewport = record.page
          ? record.page.getViewport({ scale })
          : {
              width: record.baseViewport.width * scale,
              height: record.baseViewport.height * scale,
              scale,
            };
        record.fitScale = fitScale;
        record.scale = scale;
        record.viewport = viewport;
        record.pageElement.dataset.pdfScale = String(scale);
        record.pageElement.dataset.pdfFitScale = String(fitScale);
        record.textLayer?.style.setProperty("--scale-factor", String(scale));
      }
      return true;
    };

    const finishGesturePreview = ({ restoreCommittedScale = false } = {}) => {
      const activeGesture = active.gestureScaleMultiplier !== null;
      if (activeGesture && restoreCommittedScale) {
        root.style.setProperty("--portal-pdf-scale-multiplier", String(options.scaleMultiplier));
      }
      if (activeGesture) {
        for (const record of active.pages) {
          record.pageElement.style.transform = "";
          record.pageElement.style.transformOrigin = "";
        }
      }
      active.gestureScaleMultiplier = null;
      delete root.dataset.pdfGesturePreviewActive;
      return activeGesture;
    };

    const cancelGestureScale = () => finishGesturePreview({ restoreCommittedScale: true });

    const previewGestureScale = ({ scaleMultiplier = options.scaleMultiplier, anchor = null } = {}) => {
      if (active.cancelled || !active.pages.length) return false;
      const targetMultiplier = Math.max(0.1, Number(scaleMultiplier || 1));
      active.gestureScaleMultiplier = targetMultiplier;
      root.style.setProperty("--portal-pdf-scale-multiplier", String(targetMultiplier));
      for (const record of active.pages) {
        record.pageElement.style.transformOrigin = "top center";
        record.pageElement.style.transform = "scale(1)";
      }
      if (anchor) restoreAnchor(anchor);
      root.dataset.pdfGesturePreviewActive = "true";
      root.dataset.pdfGesturePreviewCount = String(Number(root.dataset.pdfGesturePreviewCount || 0) + 1);
      return true;
    };

    const commitGestureScale = ({ scaleMultiplier = options.scaleMultiplier } = {}) => {
      finishGesturePreview();
      return previewScale({ scaleMultiplier });
    };

    const captureAnchor = (point = {}) => {
      if (active.cancelled || !active.pages.length) return null;
      const viewportBounds = root.getBoundingClientRect();
      const candidateX = Number(point.clientX);
      const candidateY = Number(point.clientY);
      const pointInsideViewport = Number.isFinite(candidateX)
        && Number.isFinite(candidateY)
        && candidateX >= viewportBounds.left
        && candidateX <= viewportBounds.right
        && candidateY >= viewportBounds.top
        && candidateY <= viewportBounds.bottom;
      const clientX = pointInsideViewport ? candidateX : viewportBounds.left + viewportBounds.width / 2;
      const clientY = pointInsideViewport ? candidateY : viewportBounds.top + viewportBounds.height / 2;
      let pageElement = document.elementFromPoint(clientX, clientY)?.closest?.("[data-pdf-page]");
      if (!pageElement || !root.contains(pageElement)) {
        pageElement = active.pages
          .map(record => ({ record, bounds: record.pageElement.getBoundingClientRect() }))
          .filter(entry => entry.bounds.bottom >= viewportBounds.top && entry.bounds.top <= viewportBounds.bottom)
          .sort((left, right) => (
            Math.abs(((left.bounds.top + left.bounds.bottom) / 2) - clientY)
            - Math.abs(((right.bounds.top + right.bounds.bottom) / 2) - clientY)
          ))[0]?.record.pageElement || null;
      }
      const record = pageElement ? active.recordsByElement.get(pageElement) : null;
      if (!record) return null;
      const pageBounds = pageElement.getBoundingClientRect();
      return {
        kind: "pdf-page",
        pageNumber: record.pageNumber,
        pageXRatio: (clientX - pageBounds.left) / Math.max(1, pageBounds.width),
        pageYRatio: (clientY - pageBounds.top) / Math.max(1, pageBounds.height),
        viewportX: clientX - viewportBounds.left,
        viewportY: clientY - viewportBounds.top,
      };
    };

    const restoreAnchor = anchor => {
      if (active.cancelled || anchor?.kind !== "pdf-page") return false;
      const record = active.recordsByPage.get(Number(anchor.pageNumber));
      if (!record) return false;
      const viewportBounds = root.getBoundingClientRect();
      const pageBounds = record.pageElement.getBoundingClientRect();
      const anchoredClientX = pageBounds.left + (pageBounds.width * Number(anchor.pageXRatio || 0));
      const anchoredClientY = pageBounds.top + (pageBounds.height * Number(anchor.pageYRatio || 0));
      const targetClientX = viewportBounds.left + Number(anchor.viewportX || 0);
      const targetClientY = viewportBounds.top + Number(anchor.viewportY || 0);
      root.scrollLeft += anchoredClientX - targetClientX;
      root.scrollTop += anchoredClientY - targetClientY;
      return true;
    };

    const retargetAnchor = (anchor, point = {}) => {
      if (active.cancelled || anchor?.kind !== "pdf-page") return captureAnchor(point);
      const viewportBounds = root.getBoundingClientRect();
      const candidateX = Number(point.clientX);
      const candidateY = Number(point.clientY);
      const pointInsideViewport = Number.isFinite(candidateX)
        && Number.isFinite(candidateY)
        && candidateX >= viewportBounds.left
        && candidateX <= viewportBounds.right
        && candidateY >= viewportBounds.top
        && candidateY <= viewportBounds.bottom;
      const clientX = pointInsideViewport ? candidateX : viewportBounds.left + viewportBounds.width / 2;
      const clientY = pointInsideViewport ? candidateY : viewportBounds.top + viewportBounds.height / 2;
      return {
        ...anchor,
        viewportX: clientX - viewportBounds.left,
        viewportY: clientY - viewportBounds.top,
      };
    };

    const redraw = async () => {
      if (active.cancelled) return false;
      await promise;
      if (active.cancelled || typeof active.schedule !== "function") return false;
      const redrawCount = Number(root.dataset.pdfRedrawCount || 0);
      for (const record of active.pages) {
        const pageState = active.pageStates.get(record.pageElement);
        if (["done", "rendering", "error"].includes(pageState)) active.schedule(record, true);
      }
      await active.renderQueue;
      if (!active.cancelled) root.dataset.pdfRedrawCount = String(redrawCount + 1);
      return !active.cancelled;
    };

    const ensurePage = async (pageNumber, { render: shouldRender = true } = {}) => {
      await promise;
      if (active.cancelled || typeof active.ensurePageRecord !== "function") return null;
      const record = await active.ensurePageRecord(pageNumber);
      if (!record || active.cancelled || !shouldRender) return record;
      const pageState = active.pageStates.get(record.pageElement);
      if (pageState !== "done" && pageState !== "rendering" && pageState !== "queued") {
        active.schedule?.(record);
      }
      await active.renderQueue;
      return active.cancelled ? null : record;
    };

    return Object.freeze({
      cancel: () => cancel(root),
      cancelGestureScale,
      captureAnchor,
      commitGestureScale,
      ensurePage,
      previewGestureScale,
      previewScale,
      promise,
      redraw,
      retargetAnchor,
      restoreAnchor,
      root,
    });
  }

  window.PortalPdfPreview = Object.freeze({ cancel, render });
})();
