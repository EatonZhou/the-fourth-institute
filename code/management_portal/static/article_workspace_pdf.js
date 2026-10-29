(() => {
  "use strict";

  if (window.PortalArticleWorkspacePdf) return;

  // The PDF output: rendering the built PDF, zoom by buttons and gestures with
  // the reading position kept, redraws after layout changes, and SyncTeX
  // from the source to the PDF and back.
  const DEPENDENCIES = Object.freeze([
    "$", "editorPosition", "esc", "openArticleFile", "request", "selectedArticle", "setOutputView",
    "showToast", "state",
  ]);

  function createPdf(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspacePdf is missing: ${missing.join(", ")}`);
    const {
      $, editorPosition, esc, openArticleFile, request, selectedArticle, setOutputView, showToast, state,
    } = dependencies;

    let articlePdfController = null;
    let articlePdfRenderKey = "";

    const articlePdfGestureFrames = window.PortalPreview.createFrameCoalescer(
      pending => previewArticlePdfGestureScale(pending.value, pending.point),
      error => showPdfError(error),
    );

    function cancelPdf() {
      window.cancelAnimationFrame(state.pdfLayoutFrame);
      articlePdfGestureFrames.cancel();
      window.clearTimeout(state.pdfRedrawTimer);
      window.clearTimeout(state.pdfGestureCommitTimer);
      state.pdfLayoutFrame = 0;
      state.pdfRedrawTimer = 0;
      state.pdfGestureCommitTimer = 0;
      state.pdfGestureAnchor = null;
      state.pdfScaleVersion += 1;
      articlePdfController?.cancelGestureScale?.();
      articlePdfController?.cancel?.();
      articlePdfController = null;
      articlePdfRenderKey = "";
    }

    function pdfUrl() {
      if (state.build?.pdf_available) {
        return state.build.pdf_url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`;
      }
      return selectedArticle()?.compiled_pdf?.available
        ? (selectedArticle().compiled_pdf.url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`)
        : "";
    }

    function showPdfError(error) {
      if (state.outputView !== "pdf") return;
      updateArticlePdfZoomControls({ available: false });
      $("articlePdfView").innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>无法渲染 PDF</h2><p>${esc(error?.message || error)}</p></div>`;
    }

    function updateArticlePdfZoomControls({ available = Boolean(pdfUrl()) } = {}) {
      const controls = $("articlePdfZoomControls");
      if (!controls) return;
      controls.hidden = state.outputView !== "pdf" || !available;
      $("articlePdfZoomValue").textContent = `${Math.round(state.pdfZoom * 100)}%`;
      $("articlePdfZoomOut").disabled = state.pdfZoom <= 0.6;
      $("articlePdfZoomIn").disabled = state.pdfZoom >= 2.4;
      $("articlePdfZoomFit").classList.toggle("active", Math.abs(state.pdfZoom - 1) < 0.001);
    }

    function captureArticlePdfPosition(point = {}) {
      const scroller = $("articlePdfView");
      if (!scroller) return null;
      const scrollerRect = scroller.getBoundingClientRect();
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

    function restoreArticlePdfPosition(position) {
      if (!position) return;
      const scroller = $("articlePdfView");
      const page = scroller?.querySelector?.(`[data-pdf-page="${Number(position.page || 1)}"]`);
      if (!scroller || !page) return;
      const bounds = page.getBoundingClientRect();
      const targetX = bounds.left + (Math.max(0, Math.min(1, Number(position.xRatio ?? 0.5))) * bounds.width);
      const targetY = bounds.top + (Math.max(0, Math.min(1, Number(position.yRatio ?? 0.5))) * bounds.height);
      scroller.scrollLeft += targetX - Number(position.clientX || 0);
      scroller.scrollTop += targetY - Number(position.clientY || 0);
    }

    function scheduleArticlePdfRedraw(delay = 180) {
      window.clearTimeout(state.pdfRedrawTimer);
      const controller = articlePdfController;
      state.pdfRedrawTimer = window.setTimeout(() => {
        state.pdfRedrawTimer = 0;
        if (controller !== articlePdfController || state.outputView !== "pdf") return;
        controller?.redraw?.().catch(error => showToast(window.PortalI18n.text`PDF redraw: ${error.message || error}`, "warning"));
      }, delay);
    }

    async function previewArticlePdfScale(point = {}) {
      const controller = articlePdfController;
      if (!controller?.previewScale) return false;
      const scaleVersion = ++state.pdfScaleVersion;
      await controller.promise;
      if (controller !== articlePdfController || scaleVersion !== state.pdfScaleVersion) return false;
      const position = captureArticlePdfPosition(point);
      const changed = controller.previewScale({ scaleMultiplier: state.pdfZoom });
      if (changed) restoreArticlePdfPosition(position);
      return changed;
    }

    function scheduleArticlePdfLayout() {
      window.cancelAnimationFrame(state.pdfLayoutFrame);
      state.pdfLayoutFrame = window.requestAnimationFrame(() => {
        state.pdfLayoutFrame = 0;
        previewArticlePdfScale().then(changed => {
          if (changed) scheduleArticlePdfRedraw(220);
        }).catch(() => {});
      });
    }

    async function setArticlePdfZoom(value, point = {}) {
      window.clearTimeout(state.pdfGestureCommitTimer);
      state.pdfGestureCommitTimer = 0;
      state.pdfGestureAnchor = null;
      articlePdfGestureFrames.cancel();
      articlePdfController?.cancelGestureScale?.();
      const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
      if (Math.abs(next - state.pdfZoom) < 0.001) return;
      state.pdfZoom = next;
      updateArticlePdfZoomControls();
      if (await previewArticlePdfScale(point)) {
        const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
        articlePdfRenderKey = `${state.selectedId}|${pdfUrl()}|${version}|${state.pdfZoom}`;
        scheduleArticlePdfRedraw();
        return;
      }
      await renderPdf({ preservePosition: true });
    }

    function previewArticlePdfGestureScale(value, point = {}) {
      const controller = articlePdfController;
      if (!controller?.previewGestureScale) return false;
      const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
      if (!state.pdfGestureAnchor) state.pdfGestureAnchor = captureArticlePdfPosition(point);
      state.pdfZoom = next;
      updateArticlePdfZoomControls();
      return controller.previewGestureScale({ scaleMultiplier: next, point });
    }

    async function commitArticlePdfGestureScale() {
      window.clearTimeout(state.pdfGestureCommitTimer);
      state.pdfGestureCommitTimer = 0;
      await Promise.resolve(articlePdfGestureFrames.flush());
      const controller = articlePdfController;
      const position = state.pdfGestureAnchor;
      state.pdfGestureAnchor = null;
      if (!controller?.commitGestureScale) return false;
      const changed = controller.commitGestureScale({ scaleMultiplier: state.pdfZoom });
      if (changed) restoreArticlePdfPosition(position);
      const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
      articlePdfRenderKey = `${state.selectedId}|${pdfUrl()}|${version}|${state.pdfZoom}`;
      if (changed) scheduleArticlePdfRedraw(80);
      return changed;
    }

    function scheduleArticlePdfGestureCommit(delay = 110) {
      window.clearTimeout(state.pdfGestureCommitTimer);
      state.pdfGestureCommitTimer = window.setTimeout(() => {
        state.pdfGestureCommitTimer = 0;
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
      host.addEventListener("wheel", event => {
        if (!event.ctrlKey || state.outputView !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * 0.01);
        const queuedZoom = Number(articlePdfGestureFrames.pending()?.value || state.pdfZoom);
        scheduleArticlePdfGestureZoom(queuedZoom * factor, {
          clientX: event.clientX,
          clientY: event.clientY,
        });
        scheduleArticlePdfGestureCommit();
      }, { passive: false });
      host.addEventListener("touchstart", event => {
        if (event.touches.length !== 2 || state.outputView !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        state.pdfTouchGesture = {
          distance: Math.max(1, touchDistance(event.touches)),
          zoom: state.pdfZoom,
        };
      }, { passive: false });
      host.addEventListener("touchmove", event => {
        if (event.touches.length !== 2 || !state.pdfTouchGesture) return;
        event.preventDefault();
        const scale = touchDistance(event.touches) / state.pdfTouchGesture.distance;
        scheduleArticlePdfGestureZoom(state.pdfTouchGesture.zoom * scale, touchMidpoint(event.touches));
      }, { passive: false });
      const finishTouchGesture = () => {
        state.pdfTouchGesture = null;
        commitArticlePdfGestureScale().catch(error => showPdfError(error));
      };
      host.addEventListener("touchend", event => {
        if (event.touches.length < 2) finishTouchGesture();
      }, { passive: true });
      host.addEventListener("touchcancel", finishTouchGesture, { passive: true });

      host.addEventListener("gesturestart", event => {
        if (state.outputView !== "pdf" || !pdfUrl()) return;
        event.preventDefault();
        state.pdfTouchGesture = { zoom: state.pdfZoom, distance: 1 };
      }, { passive: false });
      host.addEventListener("gesturechange", event => {
        if (!state.pdfTouchGesture) return;
        event.preventDefault();
        scheduleArticlePdfGestureZoom(state.pdfTouchGesture.zoom * Number(event.scale || 1), {
          clientX: event.clientX,
          clientY: event.clientY,
        });
      }, { passive: false });
      host.addEventListener("gestureend", finishTouchGesture, { passive: true });
    }

    async function renderPdf({ preservePosition = false } = {}) {
      if (state.outputView !== "pdf") return;
      const host = $("articlePdfView");
      if (state.buildLoading) {
        host.innerHTML = window.PortalI18n.markup('<div class="articlePdfStatus">正在读取构建状态…</div>');
        return;
      }
      const url = pdfUrl();
      if (!url) {
        cancelPdf();
        updateArticlePdfZoomControls({ available: false });
        host.innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>尚无 PDF</h2><p>编译 Article 后将在此显示文档。</p></div>`;
        return;
      }
      updateArticlePdfZoomControls({ available: true });
      const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
      const renderKey = `${state.selectedId}|${url}|${version}|${state.pdfZoom}`;
      if (articlePdfController && articlePdfRenderKey === renderKey && $("articlePdfDocument")) {
        await articlePdfController.promise;
        return;
      }
      const readingPosition = preservePosition ? captureArticlePdfPosition() : null;
      cancelPdf();
      host.innerHTML = window.PortalI18n.html`<div class="articlePdfDocument" id="articlePdfDocument"><div class="articlePdfStatus" id="articlePdfStatus">正在加载 PDF…</div></div>`;
      const documentHost = $("articlePdfDocument");
      articlePdfController = window.PortalPdfPreview.render({
        root: documentHost,
        url,
        title: selectedArticle()?.title || window.PortalI18n.t("Article"),
        version,
        statusClass: "articlePdfStatus",
        pageClass: "articlePdfPage",
        textLayerClass: "articlePdfText",
        horizontalPadding: 12,
        minScale: 0.45,
        maxScale: 4,
        scaleMultiplier: state.pdfZoom,
        outputScale: ratio => Math.min(2, ratio),
        onPage: ({ pageNumber, pageElement }) => {
          pageElement.dataset.pageLabel = `第 ${pageNumber} 页 · 双击返回源码`;
        },
        onError: error => showToast(window.PortalI18n.text`PDF.js fallback: ${error.message || error}`, "warning"),
      });
      const controller = articlePdfController;
      articlePdfRenderKey = renderKey;
      await controller.promise;
      if (articlePdfController === controller && readingPosition?.page) {
        await controller.ensurePage?.(readingPosition.page, { render: true });
      }
      if (articlePdfController === controller) restoreArticlePdfPosition(readingPosition);
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
      if (state.pdfSyncPending || !state.settings?.synctex_available || !state.selectedId || !state.openFile) return;
      const position = editorPosition();
      state.pdfSyncPending = true;
      $("articleSyncToPdf").disabled = true;
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/synctex/source-to-pdf`, {
          method: "POST",
          body: JSON.stringify({
            path: state.openFile,
            line: position.line,
            column: position.column,
            page_hint: Number(state.pdfSyncLocation?.page || 0),
          }),
        });
        state.pdfSyncLocation = {
          page: Number(payload.match?.page || 0),
          x: Number(payload.match?.x || 0),
          y: Number(payload.match?.y || 0),
          source_path: state.openFile,
          source_line: position.line,
        };
        if (state.outputView !== "pdf") setOutputView("pdf");
        await renderPdf();
        await articlePdfController?.ensurePage?.(payload.match?.page, { render: true });
        if (!revealPdfSyncMatch(payload.match)) {
          throw new Error("PDF 页面尚未完成渲染");
        }

      } catch (error) {
        showToast(window.PortalI18n.text`源码 → PDF：${error.message || error}`, "warning");
      } finally {
        state.pdfSyncPending = false;
        $("articleSyncToPdf").disabled = !state.settings?.synctex_available || !pdfUrl() || !state.openFile.toLowerCase().endsWith(".tex");
      }
    }

    async function syncPdfToSource(pageElement, event) {
      if (state.pdfSyncPending || !state.settings?.synctex_available || !state.selectedId || !pageElement) return;
      const page = Number(pageElement.dataset.pdfPage || 1);
      const scale = Math.max(.01, Number(pageElement.dataset.pdfScale || 1));
      const rect = pageElement.getBoundingClientRect();
      const x = Math.max(0, (event.clientX - rect.left) / scale);
      const y = Math.max(0, (event.clientY - rect.top) / scale);
      state.pdfSyncPending = true;
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/synctex/pdf-to-source`, {
          method: "POST",
          body: JSON.stringify({ page, x, y }),
        });
        const match = payload.match || {};
        state.pdfSyncLocation = {
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
        state.pdfSyncPending = false;
      }
    }

    return Object.freeze({
      bindArticlePdfGestures, cancelPdf, pdfUrl, renderPdf, scheduleArticlePdfLayout, setArticlePdfZoom,
      showPdfError, syncPdfToSource, syncSourceToPdf, updateArticlePdfZoomControls,
    });
  }

  window.PortalArticleWorkspacePdf = Object.freeze({ createPdf, dependencies: DEPENDENCIES });
})();
