(() => {
  "use strict";

  if (window.PortalPaperReader) return;

  // The reader mode: the PDF viewer and the analysis views, the reading place
  // kept per Paper, and the menu for a selection with its actions.
  const DEPENDENCIES = Object.freeze([
    "$", "addSelectionToChatDraft", "alertError", "applyPaneCollapse", "currentPaperWorkbench",
    "detailSectionLoadingHtml", "detailSectionsLoaded", "esc", "md", "needPaperHtml", "paperSelectionPopover",
    "paperStateHtml", "state", "submitChatMessage",
  ]);

  function createReader(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperReader is missing: ${missing.join(", ")}`);
    const {
      $, addSelectionToChatDraft, alertError, applyPaneCollapse, currentPaperWorkbench,
      detailSectionLoadingHtml, detailSectionsLoaded, esc, md, needPaperHtml, paperSelectionPopover,
      paperStateHtml, state, submitChatMessage,
    } = dependencies;

    function shortPath(value, maxLength = 44) {
      const text = String(value || "").trim();
      if (!text) return "";
      const parts = text.split("/").filter(Boolean);
      let display = text;
      if (parts.length >= 2) {
        display = `.../${parts.slice(-2).join("/")}`;
      } else if (parts.length === 1) {
        display = parts[0];
      }
      return display.length > maxLength ? `...${display.slice(-maxLength)}` : display;
    }

    let paperPdfController = null;
    let paperPdfZoom = null;
    const paperReaderViewStates = new Map();

    function cancelPdfRendering() {
      paperPdfZoom?.dispose?.();
      paperPdfZoom = null;
      paperPdfController?.cancel?.();
      paperPdfController = null;
    }

    function capturePaperReaderViewState() {
      const grid = $("workspaceMain")?.querySelector(".readerGrid[data-reader-paper-id]");
      const paperId = String(grid?.dataset.readerPaperId || "").trim();
      if (!paperId) return null;
      const previous = paperReaderViewStates.get(paperId) || {};
      const analysis = $("analysisReaderContent");
      const analysisVisible = !!analysis && !analysis.closest(".readerPanel")?.classList.contains("collapsed");
      const pdfViewport = paperPdfController?.root || $("paperPdfViewer");
      const pdfVisible = !!pdfViewport && !pdfViewport.closest(".readerPanel")?.classList.contains("collapsed");
      const pdfReady = !paperPdfController || pdfViewport?.dataset.pdfStatus === "done";
      const next = {
        ...previous,
        ...(analysisVisible ? {
          analysisLeft: analysis.scrollLeft,
          analysisTop: analysis.scrollTop,
        } : {}),
        ...(pdfVisible && pdfReady ? {
          pdfAnchor: paperPdfController?.captureAnchor?.() || null,
          pdfLeft: pdfViewport.scrollLeft,
          pdfTop: pdfViewport.scrollTop,
        } : {}),
      };
      paperReaderViewStates.delete(paperId);
      paperReaderViewStates.set(paperId, next);
      while (paperReaderViewStates.size > 24) {
        paperReaderViewStates.delete(paperReaderViewStates.keys().next().value);
      }
      return next;
    }

    function restoreAnalysisReaderViewState(viewState) {
      const analysis = $("analysisReaderContent");
      if (!analysis || !viewState) return;
      analysis.scrollLeft = Number(viewState.analysisLeft || 0);
      analysis.scrollTop = Number(viewState.analysisTop || 0);
    }

    function restorePdfReaderViewState(controller, viewState) {
      const viewport = controller?.root;
      if (!viewport || !viewState) return;
      if (viewState.pdfAnchor && controller.restoreAnchor(viewState.pdfAnchor)) return;
      viewport.scrollLeft = Number(viewState.pdfLeft || 0);
      viewport.scrollTop = Number(viewState.pdfTop || 0);
    }

    async function renderPdfJsViewer(pdfUrl, title, viewState = null) {
      const viewer = $("paperPdfViewer");
      const root = $("pdfReaderContent");
      if (!viewer || !root || !pdfUrl) return;
      let userScrolledBeforeRestore = false;
      const markUserScroll = event => {
        if (event.isTrusted) userScrolledBeforeRestore = true;
      };
      viewer.addEventListener("scroll", markUserScroll, { passive: true });
      cancelPdfRendering();
      const controller = window.PortalPdfPreview.render({
        root: viewer,
        url: pdfUrl,
        title,
        statusClass: "pdfJsStatus",
        pageClass: "pdfJsPage",
        textLayerClass: "textLayer",
        minPageWidth: 360,
        maxPageWidth: 980,
        minScale: 0.5,
        horizontalPadding: 32,
        onFallback: bindPdfSelectionEvents,
        onReady: bindPdfSelectionEvents,
      });
      const zoom = window.PortalPreview.mountZoom({
        root,
        viewport: viewer,
        min: 0.5,
        max: 4,
        step: 0.1,
        enabled: false,
        captureAnchor: point => controller.captureAnchor(point),
        retargetAnchor: (anchor, point) => controller.retargetAnchor(anchor, point),
        restoreAnchor: anchor => controller.restoreAnchor(anchor),
        restoreOnChange: false,
        onChange: (value, { point, anchor }) => controller.previewGestureScale({
          scaleMultiplier: value,
          point,
          anchor,
        }),
        onCommit: async (value, { anchor, restoreAnchor }) => {
          controller.commitGestureScale({ scaleMultiplier: value });
          if (anchor) restoreAnchor(anchor);
          await controller.redraw();
        },
        onError: alertError,
      });
      paperPdfController = controller;
      paperPdfZoom = zoom;
      const pdf = await controller.promise;
      if (paperPdfController !== controller || paperPdfZoom !== zoom) return;
      zoom.setEnabled(Boolean(pdf));
      if (!userScrolledBeforeRestore) restorePdfReaderViewState(controller, viewState);
      viewer.removeEventListener("scroll", markUserScroll);
    }

    function renderReaderMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["analysis", "summary"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Reader");
        return;
      }
      const paper = detail.paper;
      const analysisHtml = detail.analysis?.content
        ? md(detail.analysis.content)
        : paperStateHtml("No analysis markdown registered for this paper.", { density: "compact" });
      const pdfHtml = paper.pdf_url
        ? window.PortalI18n.html`<div class="pdfJsViewer" id="paperPdfViewer" data-pdf-url="${esc(paper.pdf_url)}">
              <div class="pdfJsStatus">Loading PDF...</div>
            </div>`
        : paperStateHtml("No PDF path or URL registered for this paper.", { density: "compact" });
      const analysisPath = String(detail.analysis?.path || "");
      const readerGridClasses = [
        "readerGrid",
        state.readerAnalysisCollapsed ? "analysisCollapsed" : "",
        state.readerPdfCollapsed ? "pdfCollapsed" : "",
      ].filter(Boolean).join(" ");
      const readerViewState = paperReaderViewStates.get(String(paper.id || "")) || null;
      $("workspaceMain").classList.add("is-reader-mode");
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="${readerGridClasses}" data-reader-paper-id="${esc(paper.id || "")}">
            <section class="readerPanel ${state.readerAnalysisCollapsed ? "collapsed" : ""}" aria-label="Analysis 阅读面板">
              <header class="readerHead">
                <div class="readerHeadIdentity">
                  <span class="readerHeadIcon" aria-hidden="true">MD</span>
                  <span class="readerHeadText">
                    <span class="readerHeadTitle">Analysis</span>
                    ${analysisPath ? window.PortalI18n.html`<span class="readerPath" aria-label="Analysis 文件：${esc(analysisPath)}">${esc(shortPath(analysisPath, 72))}</span>` : ""}
                  </span>
                </div>
                <div class="readerHeadActions">
                  <button
                    class="portalButton is-icon is-quiet iconBtn readerPanelToggle"
                    data-portal-icon="chevron-left"
                    data-portal-tooltip="${state.readerAnalysisCollapsed ? window.PortalI18n.t("展开 Analysis 面板") : window.PortalI18n.t("收起 Analysis 面板")}"
                    id="toggleAnalysisPanelBtn"
                    type="button"
                    aria-label="${state.readerAnalysisCollapsed ? window.PortalI18n.t("展开 Analysis 面板") : window.PortalI18n.t("收起 Analysis 面板")}"
                    aria-controls="analysisReaderContent"
                    aria-pressed="${state.readerAnalysisCollapsed ? "true" : "false"}"
                  ></button>
                </div>
              </header>
              <div class="readerContent markdownBody" id="analysisReaderContent">${analysisHtml}</div>
            </section>
            <section class="readerPanel ${state.readerPdfCollapsed ? "collapsed" : ""}" aria-label="PDF 阅读面板">
              <header class="readerHead">
                <div class="readerHeadIdentity">
                  <span class="readerHeadIcon" aria-hidden="true">PDF</span>
                  <span class="readerHeadText">
                    <span class="readerHeadTitle">PDF</span>
                    ${paper.pdf_url ? window.PortalI18n.html`<span class="readerPath" aria-label="PDF 文件：${esc(paper.pdf_url)}">${esc(shortPath(paper.pdf_url, 72))}</span>` : window.PortalI18n.html`<span class="readerPath">尚未注册 PDF</span>`}
                  </span>
                </div>
                <div class="readerHeadActions">
                  ${paper.pdf_url ? window.PortalI18n.html`<a class="portalButton is-icon is-quiet iconBtn readerExternalLink" href="${esc(paper.pdf_url)}" target="_blank" rel="noopener noreferrer" data-portal-tooltip="在新标签页打开 PDF" aria-label="在新标签页打开 PDF" data-portal-icon="external-link"></a>` : ""}
                  <button
                    class="portalButton is-icon is-quiet iconBtn readerPanelToggle"
                    data-portal-icon="chevron-right"
                    data-portal-tooltip="${state.readerPdfCollapsed ? window.PortalI18n.t("展开 PDF 面板") : window.PortalI18n.t("收起 PDF 面板")}"
                    id="togglePdfPanelBtn"
                    type="button"
                    aria-label="${state.readerPdfCollapsed ? window.PortalI18n.t("展开 PDF 面板") : window.PortalI18n.t("收起 PDF 面板")}"
                    aria-controls="pdfReaderContent"
                    aria-pressed="${state.readerPdfCollapsed ? "true" : "false"}"
                  ></button>
                </div>
              </header>
              <div class="readerContent pdfReaderContent" id="pdfReaderContent">${pdfHtml}</div>
            </section>
          </div>
        `;
      restoreAnalysisReaderViewState(readerViewState);
      if (paper.pdf_url && !state.readerPdfCollapsed) {
        renderPdfJsViewer(paper.pdf_url, paper.title || "Paper", readerViewState).catch(alertError);
      } else {
        cancelPdfRendering();
      }
    }

    function selectionInfoFromWindow(win, offset = { left: 0, top: 0 }) {
      try {
        const selection = win?.getSelection?.();
        const text = String(selection || "").replace(/\s+/g, " ").trim();
        if (!text || !selection.rangeCount) return null;
        const rect = selection.getRangeAt(0).getBoundingClientRect();
        return {
          text,
          x: offset.left + rect.left + Math.max(0, rect.width / 2),
          y: offset.top + rect.top - 8,
        };
      } catch {}
      return null;
    }

    function currentReaderSelectionInfo(fallbackEvent = null) {
      const workspace = $("workspaceMain");
      const pageInfo = selectionInfoFromWindow(window);
      if (pageInfo && (!workspace || workspace.contains(document.getSelection?.()?.anchorNode || null))) return pageInfo;
      const frame = $("paperPdfFrame");
      try {
        const frameRect = frame?.getBoundingClientRect?.();
        const frameInfo = selectionInfoFromWindow(frame?.contentWindow, frameRect || { left: 0, top: 0 });
        if (frameInfo) return frameInfo;
      } catch {}
      const text = String(window.getSelection?.() || "").replace(/\s+/g, " ").trim();
      if (text && fallbackEvent) return { text, x: fallbackEvent.clientX, y: fallbackEvent.clientY - 8 };
      return null;
    }

    function showSelectionMenu(info) {
      if (!info?.text) return hideSelectionMenu();
      state.selectionText = info.text;
      paperSelectionPopover.open({ x: info.x, y: info.y });
    }

    function hideSelectionMenu() {
      paperSelectionPopover.close();
    }

    function maybeShowSelectionMenu(event = null) {
      setTimeout(() => {
        if (event?.target?.closest?.("#selectionMenu")) return;
        const info = currentReaderSelectionInfo(event);
        if (info?.text) showSelectionMenu(info);
        else hideSelectionMenu();
      }, 20);
    }

    function bindPdfSelectionEvents() {
      const frame = $("paperPdfFrame");
      if (!frame) return;
      const bind = () => {
        try {
          frame.contentWindow?.addEventListener("mouseup", event => maybeShowSelectionMenu(event));
          frame.contentWindow?.addEventListener("keyup", event => maybeShowSelectionMenu(event));
        } catch {}
      };
      frame.addEventListener("load", bind, { once: true });
      bind();
    }

    function selectionPrompt(action, text) {
      const clipped = text.length > 8000 ? `${text.slice(0, 8000)}\n\n[Selection truncated by Portal.]` : text;
      if (action === "translate") return `请把下面这段论文原文翻译成中文。保留关键英文术语，并在必要时补一句术语解释：\n\n${clipped}`;
      if (action === "find-ref") return `下面这段论文原文可能包含 citation、作者名、论文名或 reference marker。请结合当前论文全文，找到它引用的论文条目；输出标题、作者、年份、venue/arXiv/DOI，以及你定位它的依据：\n\n${clipped}`;
      if (action === "regist-ref") return `下面这段论文原文可能包含一个被引用论文。请结合当前论文全文定位该引用，并输出可注册到 Paper Library 的 metadata JSON，包括 title、authors、year、venue、abstract_or_note、arxiv_or_doi_or_url、reason。如果无法唯一确定，请列出候选并说明缺失信息：\n\n${clipped}`;
      return `请解释下面这段论文原文，并说明它在本文方法或实验中的作用：\n\n${clipped}`;
    }

    async function runSelectionAction(action) {
      const selected = String(state.selectionText || "").trim();
      hideSelectionMenu();
      if (!selected) {
        window.PortalUI.toast(window.PortalI18n.t("没有读到选中文本。原生浏览器 PDF viewer 有时不允许页面读取选区；可以先复制 PDF 文字，再粘贴到 Research Chat。"), "warning");
        return;
      }
      if (action === "add-chat") {
        if (addSelectionToChatDraft(selected)) {
          window.PortalUI.toast(window.PortalI18n.t("已添加到 Research Chat 输入框。"), "success");
        } else {
          window.PortalUI.toast(window.PortalI18n.t("Research Chat 输入框尚未就绪，请稍后重试。"), "warning");
        }
        return;
      }
      if (!currentPaperWorkbench()?.isPanelVisible("chat")) currentPaperWorkbench()?.showPanel("chat");
      applyPaneCollapse();
      await submitChatMessage(selectionPrompt(action, selected));
    }

    return Object.freeze({
      bindPdfSelectionEvents, cancelPdfRendering, capturePaperReaderViewState, maybeShowSelectionMenu,
      renderReaderMode, runSelectionAction,
    });
  }

  window.PortalPaperReader = Object.freeze({ createReader, dependencies: DEPENDENCIES });
})();
