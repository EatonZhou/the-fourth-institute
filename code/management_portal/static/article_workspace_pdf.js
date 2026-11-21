(() => {
  "use strict";

  if (window.PortalArticleWorkspacePdf) return;

  // The PDF output: rendering the built PDF, zoom by buttons and gestures with
  // the reading position kept, redraws after layout changes, and SyncTeX
  // from the source to the PDF and back.
  const DEPENDENCIES = Object.freeze([
    "editorPosition", "esc", "openArticleFile", "request", "getContext", "setOutputView",
    "showToast",
  ]);

  function createPdf(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspacePdf is missing: ${missing.join(", ")}`);
    const {
      editorPosition, esc, openArticleFile, request, getContext, setOutputView, showToast,
    } = dependencies;

    const root = document.getElementById("articleOutputTemplate").content.firstElementChild.cloneNode(true);
    const $ = id => root.id === id ? root : root.querySelector("#" + id);
    const view = { pdfZoom: 1, pdfScaleVersion: 0, pdfLayoutFrame: 0, pdfRedrawTimer: 0,
      pdfGestureCommitTimer: 0, pdfGestureAnchor: null, pdfTouchGesture: null,
      pdfSyncLocation: null, pdfSyncPending: false, pdfRestorePosition: null };
    const events = new AbortController();
    const listen = (node, type, handler, options = {}) => node.addEventListener(type, handler, { ...options, signal: events.signal });
    let destroyed = false, observer = null, dragging = false;
    function onResize(event) {
      dragging = event.detail?.reason === "resize-live";
      scheduleArticlePdfLayout({ resizing: dragging });
    }
    function connect() {
      if (destroyed || observer) return;
      observer = new ResizeObserver(() => scheduleArticlePdfLayout({ resizing: dragging }));
      observer.observe(root);
      root.addEventListener("portal:workbench-resize", onResize);
    }
    function disconnect() {
      observer?.disconnect(); observer = null;
      root.removeEventListener("portal:workbench-resize", onResize);
      window.cancelAnimationFrame(view.pdfLayoutFrame);
      window.clearTimeout(view.pdfRedrawTimer);
    }
    function restoreReadingPosition(saved = {}) {
      view.pdfZoom = Math.max(0.6, Math.min(2.4, Number(saved.pdfZoom) || 1));
      view.pdfRestorePosition = Number(saved.pdf?.page) > 0 ? { ...saved.pdf } : null;
      view.pdfSyncLocation = null;
    }
    let articlePdfController = null;
    let articlePdfRenderKey = "";
    let articlePdfPending = null;
    let articlePdfCompilation = null;

    function setPdfCompiling(compiling) {
      if (compiling && !articlePdfCompilation) {
        const position = captureArticlePdfPosition();
        articlePdfCompilation = { articleId: getContext().articleId, position: position && {
          page: position.page, xRatio: position.xRatio, yRatio: position.yRatio,
        } };
      } else if (!compiling) articlePdfCompilation = null;
      const host = $("articlePdfView");
      host.classList.toggle("articlePdfCompiling", compiling);
      host.inert = compiling;
      if (compiling) host.setAttribute("aria-busy", "true");
      else if (!articlePdfPending) host.removeAttribute("aria-busy");
      $("articlePdfCompileStatus").hidden = !compiling;
    }

    function cancelPendingPdf() {
      const pending = articlePdfPending;
      articlePdfPending = null;
      pending?.controller?.cancel?.();
      pending?.staging?.remove();
      if (!articlePdfCompilation) $("articlePdfView")?.removeAttribute("aria-busy");
    }

    const articlePdfGestureFrames = window.PortalPreview.createFrameCoalescer(
      pending => previewArticlePdfGestureScale(pending.value, pending.point),
      error => showPdfError(error),
    );

    function cancelPdf({ keepCompilation = false } = {}) {
      cancelPendingPdf();
      if (!keepCompilation) setPdfCompiling(false);
      window.cancelAnimationFrame(view.pdfLayoutFrame);
      articlePdfGestureFrames.cancel();
      window.clearTimeout(view.pdfRedrawTimer);
      window.clearTimeout(view.pdfGestureCommitTimer);
      view.pdfLayoutFrame = 0;
      view.pdfRedrawTimer = 0;
      view.pdfGestureCommitTimer = 0;
      view.pdfGestureAnchor = null;
      view.pdfScaleVersion += 1;
      articlePdfController?.cancelGestureScale?.();
      articlePdfController?.cancel?.();
      articlePdfController = null;
      articlePdfRenderKey = "";
    }

    function pdfUrl() {
      if (getContext().build?.pdf_available) {
        return getContext().build.pdf_url || `/api/articles/${encodeURIComponent(getContext().articleId)}/pdf`;
      }
      return getContext().article?.compiled_pdf?.available
        ? (getContext().article.compiled_pdf.url || `/api/articles/${encodeURIComponent(getContext().articleId)}/pdf`)
        : "";
    }

    function showPdfError(error) {
      if (getContext().view !== "pdf") return;
      updateArticlePdfZoomControls({ available: false });
      $("articlePdfView").innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>无法渲染 PDF</h2><p>${esc(error?.message || error)}</p></div>`;
    }

    function updateArticlePdfZoomControls({ available = Boolean(pdfUrl()) } = {}) {
      const controls = $("articlePdfZoomControls");
      if (!controls) return;
      controls.hidden = getContext().view !== "pdf" || !available;
      $("articlePdfZoomValue").textContent = `${Math.round(view.pdfZoom * 100)}%`;
      $("articlePdfZoomOut").disabled = view.pdfZoom <= 0.6;
      $("articlePdfZoomIn").disabled = view.pdfZoom >= 2.4;
      $("articlePdfZoomFit").classList.toggle("active", Math.abs(view.pdfZoom - 1) < 0.001);
    }

    function captureArticlePdfPosition(point = {}) {
      if (articlePdfCompilation?.articleId === getContext().articleId) return articlePdfCompilation.position;
      const scroller = $("articlePdfView");
      if (!scroller) return null;
      const scrollerRect = scroller.getBoundingClientRect();
      if (!scrollerRect.width || !scrollerRect.height) return null;
      const clientX = Number.isFinite(point.clientX) ? point.clientX : scrollerRect.left + (scrollerRect.width / 2);
      const clientY = Number.isFinite(point.clientY) ? point.clientY : scrollerRect.top + (scrollerRect.height / 2);
      let page = document.elementFromPoint(clientX, clientY)?.closest?.("[data-pdf-page]");
      if (!page || !scroller.contains(page)) {
        const pages = scroller.querySelectorAll("[data-pdf-page]");
        if (!pages.length) return null;
        const documentHost = $("articlePdfDocument");
        const contentY = scroller.scrollTop + clientY - scrollerRect.top - Number(documentHost?.offsetTop || 0);
        let low = 0;
        let high = pages.length - 1;
        while (low <= high) {
          const middle = Math.floor((low + high) / 2);
          const candidate = pages[middle];
          const top = candidate.offsetTop;
          const bottom = top + candidate.offsetHeight;
          if (contentY < top) high = middle - 1;
          else if (contentY > bottom) low = middle + 1;
          else {
            page = candidate;
            break;
          }
        }
        if (!page) {
          const candidates = [pages[Math.max(0, Math.min(pages.length - 1, low))], pages[Math.max(0, high)]]
            .filter(Boolean);
          page = candidates.reduce((closest, candidate) => {
            const top = candidate.offsetTop;
            const distance = contentY < top ? top - contentY : Math.max(0, contentY - top - candidate.offsetHeight);
            return !closest || distance < closest.distance ? { candidate, distance } : closest;
          }, null)?.candidate;
        }
      }
      if (!page) return null;
      const bounds = page.getBoundingClientRect();
      return {
        page: Number(page.dataset.pdfPage || 1),
        xRatio: Math.max(0, Math.min(1, (clientX - bounds.left) / Math.max(1, bounds.width))),
        yRatio: Math.max(0, Math.min(1, (clientY - bounds.top) / Math.max(1, bounds.height))),
        clientX,
        clientY,
      };
    }

    function restoreArticlePdfPosition(position, scroller = $("articlePdfView")) {
      if (!position) return;
      const page = scroller?.querySelector?.(`[data-pdf-page="${Number(position.page || 1)}"]`)
        || Array.from(scroller?.querySelectorAll?.("[data-pdf-page]") || []).at(-1);
      if (!scroller || !page) return;
      const bounds = page.getBoundingClientRect();
      const targetX = bounds.left + (Math.max(0, Math.min(1, Number(position.xRatio ?? 0.5))) * bounds.width);
      const targetY = bounds.top + (Math.max(0, Math.min(1, Number(position.yRatio ?? 0.5))) * bounds.height);
      const viewport = scroller.getBoundingClientRect();
      scroller.scrollLeft += targetX - Number(position.clientX ?? (viewport.left + viewport.width / 2));
      scroller.scrollTop += targetY - Number(position.clientY ?? (viewport.top + viewport.height / 2));
    }

    function scheduleArticlePdfRedraw(delay = 180) {
      window.clearTimeout(view.pdfRedrawTimer);
      const controller = articlePdfController;
      view.pdfRedrawTimer = window.setTimeout(() => {
        view.pdfRedrawTimer = 0;
        if (controller !== articlePdfController || getContext().view !== "pdf") return;
        controller?.redraw?.().catch(error => showToast(window.PortalI18n.text`PDF redraw: ${error.message || error}`, "warning"));
      }, delay);
    }

    async function previewArticlePdfScale(point = {}) {
      const controller = articlePdfController;
      if (!controller?.previewScale) return false;
      const scaleVersion = ++view.pdfScaleVersion;
      await controller.promise;
      if (controller !== articlePdfController || scaleVersion !== view.pdfScaleVersion) return false;
      const position = captureArticlePdfPosition(point);
      const changed = controller.previewScale({ scaleMultiplier: view.pdfZoom });
      if (changed) restoreArticlePdfPosition(position);
      return changed;
    }

    function scheduleArticlePdfLayout({ resizing = false } = {}) {
      window.cancelAnimationFrame(view.pdfLayoutFrame);
      view.pdfLayoutFrame = 0;
      if (resizing) {
        // Keep the existing bitmap and reading position while the pointer is
        // held, even during a pause. Invalidate work queued before this drag.
        view.pdfScaleVersion += 1;
        window.clearTimeout(view.pdfRedrawTimer);
        view.pdfRedrawTimer = 0;
        return;
      }
      view.pdfLayoutFrame = window.requestAnimationFrame(() => {
        view.pdfLayoutFrame = 0;
        previewArticlePdfScale().then(changed => {
          if (changed) scheduleArticlePdfRedraw(220);
        }).catch(() => {});
      });
    }

    async function setArticlePdfZoom(value, point = {}) {
      window.clearTimeout(view.pdfGestureCommitTimer);
      view.pdfGestureCommitTimer = 0;
      view.pdfGestureAnchor = null;
      articlePdfGestureFrames.cancel();
      articlePdfController?.cancelGestureScale?.();
      const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
      if (Math.abs(next - view.pdfZoom) < 0.001) return;
      view.pdfZoom = next;
      updateArticlePdfZoomControls();
      if (await previewArticlePdfScale(point)) {
        articlePdfRenderKey = `${articlePdfRenderKey.slice(0, articlePdfRenderKey.lastIndexOf("|"))}|${view.pdfZoom}`;
        scheduleArticlePdfRedraw();
        return;
      }
      await renderPdf({ preservePosition: true });
    }

    function previewArticlePdfGestureScale(value, point = {}) {
      const controller = articlePdfController;
      if (!controller?.previewGestureScale) return false;
      const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
      if (!view.pdfGestureAnchor) view.pdfGestureAnchor = captureArticlePdfPosition(point);
      view.pdfZoom = next;
      updateArticlePdfZoomControls();
      return controller.previewGestureScale({ scaleMultiplier: next, point });
    }

    async function commitArticlePdfGestureScale() {
      window.clearTimeout(view.pdfGestureCommitTimer);
      view.pdfGestureCommitTimer = 0;
      await Promise.resolve(articlePdfGestureFrames.flush());
      const controller = articlePdfController;
      const position = view.pdfGestureAnchor;
      view.pdfGestureAnchor = null;
      if (!controller?.commitGestureScale) return false;
      const changed = controller.commitGestureScale({ scaleMultiplier: view.pdfZoom });
      if (changed) restoreArticlePdfPosition(position);
      articlePdfRenderKey = `${articlePdfRenderKey.slice(0, articlePdfRenderKey.lastIndexOf("|"))}|${view.pdfZoom}`;
      if (changed) scheduleArticlePdfRedraw(80);
      return changed;
    }

    function scheduleArticlePdfGestureCommit(delay = 110) {
      window.clearTimeout(view.pdfGestureCommitTimer);
      view.pdfGestureCommitTimer = window.setTimeout(() => {
        view.pdfGestureCommitTimer = 0;
        commitArticlePdfGestureScale().catch(error => showPdfError(error));
      }, delay);
    }

    function scheduleArticlePdfGestureZoom(value, point = {}) {
      articlePdfGestureFrames.schedule({ value, point });
    }

    function bindArticlePdfGestures() {
      const host = $("articlePdfView");
      const touchDistance = touches => Math.hypot(
        touches[0].clientX - touches[1].clientX,
        touches[0].clientY - touches[1].clientY,
      );
      const touchMidpoint = touches => ({
        clientX: (touches[0].clientX + touches[1].clientX) / 2,
        clientY: (touches[0].clientY + touches[1].clientY) / 2,
      });
      listen(host, "wheel", event => {
        if (!event.ctrlKey || getContext().view !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * 0.01);
        const queuedZoom = Number(articlePdfGestureFrames.pending()?.value || view.pdfZoom);
        scheduleArticlePdfGestureZoom(queuedZoom * factor, {
          clientX: event.clientX,
          clientY: event.clientY,
        });
        scheduleArticlePdfGestureCommit();
      }, { passive: false });
      listen(host, "touchstart", event => {
        if (event.touches.length !== 2 || getContext().view !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        view.pdfTouchGesture = {
          distance: Math.max(1, touchDistance(event.touches)),
          zoom: view.pdfZoom,
        };
      }, { passive: false });
      listen(host, "touchmove", event => {
        if (event.touches.length !== 2 || !view.pdfTouchGesture) return;
        event.preventDefault();
        const scale = touchDistance(event.touches) / view.pdfTouchGesture.distance;
        scheduleArticlePdfGestureZoom(view.pdfTouchGesture.zoom * scale, touchMidpoint(event.touches));
      }, { passive: false });
      const finishTouchGesture = () => {
        view.pdfTouchGesture = null;
        commitArticlePdfGestureScale().catch(error => showPdfError(error));
      };
      listen(host, "touchend", event => {
        if (event.touches.length < 2) finishTouchGesture();
      }, { passive: true });
      listen(host, "touchcancel", finishTouchGesture, { passive: true });

      listen(host, "gesturestart", event => {
        if (getContext().view !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        view.pdfTouchGesture = { zoom: view.pdfZoom, distance: 1 };
      }, { passive: false });
      listen(host, "gesturechange", event => {
        if (!view.pdfTouchGesture) return;
        event.preventDefault();
        scheduleArticlePdfGestureZoom(view.pdfTouchGesture.zoom * Number(event.scale || 1), {
          clientX: event.clientX,
          clientY: event.clientY,
        });
      }, { passive: false });
      listen(host, "gestureend", finishTouchGesture, { passive: true });
    }

    async function renderPdf({ preservePosition = true } = {}) {
      if (getContext().view !== "pdf") return;
      const host = $("articlePdfView");
      if (getContext().buildLoading) {
        host.innerHTML = window.PortalI18n.markup('<div class="articlePdfStatus">正在读取构建状态…</div>');
        return;
      }
      const url = pdfUrl();
      if (!url) {
        cancelPdf();
        updateArticlePdfZoomControls({ available: false });
        const blocked = getContext().preflight?.status === "blocked";
        const issues = (getContext().preflight?.issues || []).filter(issue => issue.severity === "error");
        const title = window.PortalI18n.t(blocked ? "构建条件不完整" : "尚无 PDF");
        const detail = blocked
          ? issues.map(issue => `<p>${esc(issue.message || "")}</p>`).join("")
          : window.PortalI18n.html`<p>编译 Article 后将在此显示文档。</p>`;
        host.innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>${title}</h2>${detail}</div>`;
        return;
      }
      updateArticlePdfZoomControls({ available: true });
      const version = String(getContext().build?.compiled_at || getContext().article?.compiled_pdf?.mtime_ns || "");
      const articleId = getContext().articleId;
      const renderBase = `${articleId}|${url}|${version}`;
      const renderKey = `${renderBase}|${view.pdfZoom}`;
      if (articlePdfPending?.renderKey === renderKey) return articlePdfPending.promise;
      if (articlePdfController && articlePdfRenderKey === renderKey && $("articlePdfDocument")) {
        await articlePdfController.promise;
        return;
      }
      const resumePosition = view.pdfRestorePosition;
      const sameArticle = articlePdfController && articlePdfRenderKey.startsWith(`${articleId}|`);
      cancelPendingPdf();
      if (!sameArticle) {
        cancelPdf({ keepCompilation: true });
        host.innerHTML = window.PortalI18n.html`<div class="articlePdfStatus">正在加载 PDF…</div>`;
      }
      // Keep the compilation status (or current preview on a passive refresh)
      // until every page at the destination has been painted.
      const staging = host.cloneNode(false);
      staging.removeAttribute("id");
      staging.setAttribute("aria-hidden", "true");
      staging.inert = true;
      staging.dataset.articlePdfStaging = "";
      const sizeStaging = () => {
        const rect = host.getBoundingClientRect();
        Object.assign(staging.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`,
          width: `${rect.width}px`, height: `${rect.height}px`, boxSizing: "border-box",
          visibility: "hidden", pointerEvents: "none" });
        return `${rect.width}|${rect.height}|${view.pdfZoom}`;
      };
      let geometry = sizeStaging();
      const documentHost = document.createElement("div");
      documentHost.className = "articlePdfDocument";
      staging.append(documentHost);
      host.parentElement.append(staging);
      host.setAttribute("aria-busy", "true");
      const pending = { renderKey, staging, controller: null, promise: null };
      articlePdfPending = pending;
      const current = () => !destroyed && articlePdfPending === pending && getContext().articleId === articleId;
      pending.promise = (async () => {
        const prepareViewport = () => {
          const anchor = resumePosition || (preservePosition && sameArticle ? captureArticlePdfPosition() : null);
          const readingPosition = anchor ? { ...anchor, page: Math.max(1, Math.min(
            Number(documentHost.dataset.pdfPageCount) || anchor.page, anchor.page)) } : null;
          restoreArticlePdfPosition(readingPosition, staging);
          const viewport = staging.getBoundingClientRect();
          const needed = Array.from(documentHost.querySelectorAll("[data-pdf-page]")).filter(page => {
            const bounds = page.getBoundingClientRect();
            return Number(page.dataset.pdfPage) === readingPosition?.page
              || (bounds.bottom > viewport.top && bounds.top < viewport.bottom);
          });
          return { readingPosition, needed };
        };
        const controller = pending.controller = window.PortalPdfPreview.render({
          root: documentHost,
          viewport: host,
          initialPages: () => prepareViewport().needed.map(page => Number(page.dataset.pdfPage)),
          url,
          title: getContext().article?.title || window.PortalI18n.t("Article"),
          version,
          statusClass: "articlePdfStatus",
          pageClass: "articlePdfPage",
          textLayerClass: "articlePdfText",
          horizontalPadding: 12,
          minScale: 0.45,
          maxScale: 4,
          scaleMultiplier: view.pdfZoom,
          outputScale: ratio => Math.min(2, ratio),
          onPage: ({ pageNumber, pageElement }) => {
            pageElement.dataset.pageLabel = `第 ${pageNumber} 页 · 双击返回源码`;
          },
          onError: error => showToast(window.PortalI18n.text`PDF.js fallback: ${error.message || error}`, "warning"),
        });
        await controller.promise;
        const prepared = new Set();
        while (current() && getContext().view === "pdf") {
          if (sameArticle && ["error", "fallback"].includes(documentHost.dataset.pdfStatus)) {
            throw new Error(window.PortalI18n.t("新 PDF 加载失败，已保留原预览。"));
          }
          const nextGeometry = sizeStaging();
          if (geometry !== nextGeometry) {
            geometry = nextGeometry;
            controller.previewScale?.({ scaleMultiplier: view.pdfZoom });
            await controller.redraw?.();
            prepared.clear();
            if (!current()) return;
          }
          const { readingPosition, needed } = prepareViewport();
          const unprepared = needed.filter(page => !prepared.has(Number(page.dataset.pdfPage)));
          if (unprepared.length) {
            for (const page of unprepared) {
              await controller.ensurePage?.(Number(page.dataset.pdfPage), { render: true });
              if (!current()) return;
              if (page.dataset.pdfRenderState === "error") throw new Error(window.PortalI18n.t("新 PDF 页面渲染失败。"));
              prepared.add(Number(page.dataset.pdfPage));
            }
            // Reading can continue while the new pages render. Recheck the live
            // position before choosing which viewport will become visible.
            continue;
          }
          // No await between replacing the DOM and restoring its scroll offsets:
          // the first painted frame of the new PDF is already at the destination.
          articlePdfPending = null;
          cancelPdf({ keepCompilation: true });
          documentHost.id = "articlePdfDocument";
          host.replaceChildren(documentHost);
          articlePdfController = controller;
          articlePdfRenderKey = `${renderBase}|${view.pdfZoom}`;
          if (readingPosition) restoreArticlePdfPosition(readingPosition);
          else { host.scrollTop = 0; host.scrollLeft = 0; }
          if (resumePosition === view.pdfRestorePosition) view.pdfRestorePosition = null;
          return;
        }
      })().catch(error => {
        if (!current()) return;
        if (sameArticle) showToast(window.PortalI18n.text`PDF 更新失败，已保留原预览：${error.message || error}`, "warning");
        else throw error;
      }).finally(() => {
        if (articlePdfController !== pending.controller) pending.controller?.cancel?.();
        staging.remove();
        if (articlePdfPending === pending) {
          articlePdfPending = null;
          if (!articlePdfCompilation) host.removeAttribute("aria-busy");
        }
      });
      return pending.promise;
    }

    function revealPdfSyncMatch(match) {
      const page = Math.max(1, Number(match?.page || 1));
      const pageElement = $("articlePdfDocument")?.querySelector?.(`[data-pdf-page="${page}"]`);
      if (!pageElement) return false;
      pageElement.querySelectorAll(".articlePdfSyncHighlight").forEach(item => item.remove());
      const scale = Math.max(.01, Number(pageElement.dataset.pdfScale || 1));
      const left = Math.max(0, Number(match.h ?? match.x ?? 0) * scale);
      const top = Math.max(0, Number(match.v ?? match.y ?? 0) * scale);
      const width = Math.max(18, Number(match.width || 0) * scale);
      const height = Math.max(10, Number(match.height || 0) * scale);
      const marker = document.createElement("div");
      marker.className = "articlePdfSyncHighlight";
      marker.style.left = `${Math.min(left, pageElement.clientWidth - 8)}px`;
      marker.style.top = `${Math.min(top, pageElement.clientHeight - 8)}px`;
      marker.style.width = `${Math.min(width, Math.max(18, pageElement.clientWidth - left))}px`;
      marker.style.height = `${Math.min(height, Math.max(10, pageElement.clientHeight - top))}px`;
      pageElement.appendChild(marker);
      pageElement.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      return true;
    }

    async function syncSourceToPdf() {
      if (view.pdfSyncPending || !getContext().settings?.synctex_available || !getContext().articleId || !getContext().path) return;
      const position = editorPosition();
      view.pdfSyncPending = true;
      dependencies.syncBusy?.(true);
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(getContext().articleId)}/synctex/source-to-pdf`, {
          method: "POST",
          body: JSON.stringify({
            path: getContext().path,
            line: position.line,
            column: position.column,
            page_hint: Number(view.pdfSyncLocation?.page || 0),
          }),
        });
        view.pdfSyncLocation = {
          page: Number(payload.match?.page || 0),
          x: Number(payload.match?.x || 0),
          y: Number(payload.match?.y || 0),
          source_path: getContext().path,
          source_line: position.line,
        };
        if (getContext().view !== "pdf") setOutputView("pdf");
        await renderPdf();
        await articlePdfController?.ensurePage?.(payload.match?.page, { render: true });
        if (!revealPdfSyncMatch(payload.match)) {
          throw new Error("PDF 页面尚未完成渲染");
        }

      } catch (error) {
        showToast(window.PortalI18n.text`源码 → PDF：${error.message || error}`, "warning");
      } finally {
        view.pdfSyncPending = false;
        dependencies.syncBusy?.(false);
      }
    }

    async function syncPdfToSource(pageElement, event) {
      if (view.pdfSyncPending || !getContext().settings?.synctex_available || !getContext().articleId || !pageElement) return;
      const page = Number(pageElement.dataset.pdfPage || 1);
      const scale = Math.max(.01, Number(pageElement.dataset.pdfScale || 1));
      const rect = pageElement.getBoundingClientRect();
      const x = Math.max(0, (event.clientX - rect.left) / scale);
      const y = Math.max(0, (event.clientY - rect.top) / scale);
      view.pdfSyncPending = true;
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(getContext().articleId)}/synctex/pdf-to-source`, {
          method: "POST",
          body: JSON.stringify({ page, x, y }),
        });
        const match = payload.match || {};
        view.pdfSyncLocation = {
          page,
          x,
          y,
          source_path: match.path || "",
          source_line: Number(match.line || 1),
        };
        await openArticleFile(match.path, {
          line: Number(match.line || 1),
          column: Number(match.column || 1),
        });
        showToast(window.PortalI18n.text`已定位到 ${match.path}:${Number(match.line || 1)}`, "success");

      } catch (error) {
        showToast(window.PortalI18n.text`PDF → 源码：${error.message || error}`, "warning");
      } finally {
        view.pdfSyncPending = false;
      }
    }

    const { compileArticle, stopCompilation, setCompileMenu, currentArticleCompilePopover,
      updateCompilePreference, scheduleReadingPosition } = dependencies;
    listen($("articleCompile"), "click", () => compileArticle());
    listen($("articleCompileSettings"), "click", event => {
      setCompileMenu(!currentArticleCompilePopover()?.isOpen(), event);
    });
    root.querySelectorAll("[data-compile-setting]").forEach(button => {
      listen(button, "click", () => updateCompilePreference(button));
    });
    listen($("articleStopCompile"), "click", stopCompilation);
    listen($("articleRecompileScratch"), "click", () => compileArticle({ fromScratch: true }));
    listen($("articleLogButton"), "click", () => setOutputView("log"));
    listen($("articleBackToPdf"), "click", () => setOutputView("pdf"));
    listen($("articlePdfZoomOut"), "click", () => {
      setArticlePdfZoom(view.pdfZoom - 0.1).catch(error => showPdfError(error));
    });
    listen($("articlePdfZoomIn"), "click", () => {
      setArticlePdfZoom(view.pdfZoom + 0.1).catch(error => showPdfError(error));
    });
    listen($("articlePdfZoomFit"), "click", () => {
      setArticlePdfZoom(1).catch(error => showPdfError(error));
    });
    bindArticlePdfGestures();

    listen(root, "dblclick", event => {
      const page = event.target.closest(".articlePdfPage");
      if (!page) return;
      event.preventDefault();
      syncPdfToSource(page, event).catch(error => showToast(error.message || error, "error"));
    });

    listen($("articlePdfView"), "scroll", scheduleReadingPosition, { passive: true });
    return Object.freeze({
      root, connect, disconnect, compile: () => compileArticle(), fit: () => setArticlePdfZoom(1), getZoom: () => view.pdfZoom,
      getSyncLocation: () => view.pdfSyncLocation, restoreReadingPosition,
      getReadingPosition: () => view.pdfRestorePosition || captureArticlePdfPosition(),
      show() { dragging = false; scheduleArticlePdfLayout(); },
      hide() { window.cancelAnimationFrame(view.pdfLayoutFrame); window.clearTimeout(view.pdfRedrawTimer); },
      snapshot() { return { articleId: getContext().articleId, pdfZoom: view.pdfZoom, pdf: captureArticlePdfPosition() }; },
      restore(saved) { if (saved?.articleId === getContext().articleId) restoreReadingPosition(saved); },
      destroy() { if (destroyed) return; destroyed = true; disconnect(); events.abort(); cancelPdf(); root.remove(); },
      bindArticlePdfGestures, cancelPdf, captureArticlePdfPosition, pdfUrl, renderPdf, scheduleArticlePdfLayout, setArticlePdfZoom,
      setPdfCompiling, showPdfError, syncPdfToSource, syncSourceToPdf, updateArticlePdfZoomControls,
    });
  }

  function register() {
    if (window.PortalWorkbench.registeredPanelPackages().some(pkg => pkg.type === "article-output")) return;
    window.PortalWorkbench.registerPanelPackage({
      type: "article-output", version: 1, component: "PortalArticleWorkspacePdf",
      presentation: { title: "PDF / Build", icon: "file-text" }, sizing: { minWidth: 280, minHeight: 180 },
      instances: "single", capabilities: ["preview"],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
      context: { channels: [] }, persistence: { schemaVersion: 1 },
      commands: [
        { id: "compile", title: "编译", execute: ({ panel }) => panel.compile() },
        { id: "fit", title: "PDF 适合宽度", execute: ({ panel }) => panel.fit() },
      ],
      create: createPdf,
    });
  }
  window.PortalArticleWorkspacePdf = Object.freeze({ createPdf, register, dependencies: DEPENDENCIES });
})();
