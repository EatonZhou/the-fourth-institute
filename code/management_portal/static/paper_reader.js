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

    let fullscreenReader = null;
    let readerGeneration = 0;
    let readingSession = null;
    // Code only: importing this module does not create a Reader, fetch a PDF,
    // import an attachment or call any AI endpoint.
    window.requestIdleCallback?.(() => import('/static/paper_fullscreen_reader.js').catch(() => {}));
    function sessionFor(paperId) {
      const account = String(state.principalId || "");
      if (!readingSession || readingSession.closed || readingSession.paperId !== paperId || readingSession.account !== account) {
        readingSession = {paperId, account, token: crypto.randomUUID(), acknowledged: false};
      }
      return readingSession;
    }
    function acknowledge(session, generation) {
      if (session.closed || session.acknowledged || session !== readingSession ||
          generation !== readerGeneration || state.mode !== "reader" ||
          state.selectedId !== session.paperId || !session.account || String(state.principalId || "") !== session.account) return;
      session.acknowledged = true;
      window.dispatchEvent(new CustomEvent("portal:paper-reader-ready", {detail: {
        paperId: session.paperId, generation, sessionToken: session.token, account: session.account,
      }}));
    }
    const paperReaderViewStates = new Map();

    async function cancelPdfRendering() {
      const old = fullscreenReader;
      if (old && !await old.close()) return false;
      if (fullscreenReader === old) fullscreenReader = null;
      readerGeneration += 1;
      if (readingSession) readingSession.closed = true;
      return true;
    }

    async function openFullscreenReader(paper) {
      await ensureReader(paper.pdf_url);
      fullscreenReader?.setFullscreen(true);
    }

    function capturePaperReaderViewState() {
      const grid = $("workspaceMain")?.querySelector(".readerGrid[data-reader-paper-id]");
      const paperId = String(grid?.dataset.readerPaperId || "").trim();
      if (!paperId) return null;
      const previous = paperReaderViewStates.get(paperId) || {};
      const analysis = $("analysisReaderContent");
      const analysisVisible = !!analysis && !analysis.closest(".readerPanel")?.classList.contains("collapsed");
      const next = {
        ...previous,
        ...(analysisVisible ? {
          analysisLeft: analysis.scrollLeft,
          analysisTop: analysis.scrollTop,
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

    async function ensureReader(pdfUrl) {
      const paper = state.detail?.paper;
      if (!paper || !pdfUrl) return;
      const principal = String(state.principalId || '');
      if (fullscreenReader?.paperId === paper.id && fullscreenReader.principal === principal && fullscreenReader.pdfUrl === pdfUrl) return;
      if (!await cancelPdfRendering()) return;
      const generation = readerGeneration;
      const session = sessionFor(paper.id);
      const {openReader} = await import('/static/paper_fullscreen_reader.js');
      if (generation !== readerGeneration || state.selectedId !== paper.id || String(state.principalId || '') !== principal) return;
      fullscreenReader = openReader({paper, principal,
        isCurrent: () => generation === readerGeneration && state.mode === 'reader' && state.selectedId === paper.id && String(state.principalId || '') === principal,
        container: () => document.getElementById('paperPdfViewer'),
        onReady: signal => {
          if (signal.instanceId !== fullscreenReader?.instanceId || signal.source !== fullscreenReader.sourceWindow || signal.paperId !== paper.id || !signal.fingerprint) return;
          acknowledge(session, generation);
        }});
    }

    function renderReaderMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["analysis", "summary"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Paper Reader");
        return;
      }
      const paper = detail.paper;
      const analysisBody = detail.analysis?.body ?? detail.analysis?.content ?? detail.summary?.content ?? "";
      const analysisKind = detail.analysis?.kind || (detail.analysis?.content ? "analysis" : "summary");
      const sourceHtml = analysisBody ? analysisSourceHtml(detail.analysis?.provenance || {}) : "";
      const analysisHtml = analysisBody
        ? sourceHtml + md(analysisBody)
        : paperStateHtml("No analysis markdown registered for this paper.", { density: "compact" });
      return renderReaderPanels(detail, paper, analysisKind, analysisHtml);
    }

    function analysisSourceHtml(facts) {
      const recorded = value => !value || value === "未记录" ? window.PortalI18n.t("未记录") : value;
      const resident = /^resident_[0-9a-f]{12}$/.test(facts.resident_id || "") ? facts.resident_id : "";
      const humanTokens = value => Number.isFinite(value)
        ? value >= 1e9 ? `${(value / 1e9).toFixed(1).replace(/\.0$/, "")}B`
          : value >= 1e6 ? `${(value / 1e6).toFixed(1).replace(/\.0$/, "")}M`
          : value >= 1e3 ? `${(value / 1e3).toFixed(1).replace(/\.0$/, "")}K`
          : String(value)
        : window.PortalI18n.t("未记录");
      const tokenText = Number.isFinite(facts.total_tokens)
        ? window.PortalI18n.text`输入 ${humanTokens(facts.input_tokens)} · 输出 ${humanTokens(facts.output_tokens)} · 共 ${humanTokens(facts.total_tokens)} tokens`
        : window.PortalI18n.t("Token 用量未记录");
      const exactTokens = window.PortalI18n.text`输入 ${facts.input_tokens ?? window.PortalI18n.t("未记录")} · 输出 ${facts.output_tokens ?? window.PortalI18n.t("未记录")} · 共 ${facts.total_tokens ?? window.PortalI18n.t("未记录")} tokens`;
      return window.PortalI18n.html`<div class="paneNote analysisProvenance">
        ${resident ? window.PortalI18n.html`<a href="/lineage?resident=${esc(resident)}">生成 Agent：${esc(resident)}</a><!--portal-i18n:"·"--> · ` : ""}
        ${window.PortalI18n.t("Paper Model")}: ${esc(recorded(facts.model))}${facts.model_evidence === "resident_current_settings" ? window.PortalI18n.t("（Resident 配置）") : ""} · <span title="${esc(exactTokens)}">${esc(tokenText)}</span>
        ${facts.execution_id || facts.input_message_id || facts.provider || facts.account_id || facts.api_id ? window.PortalI18n.html`<details><summary><!--portal-i18n:"生成记录"-->生成记录</summary>
          ${window.PortalI18n.t("Paper Provider")}: ${esc(recorded(facts.provider))} · ${window.PortalI18n.t("Paper Account")}: ${esc(recorded(facts.account_id))} · API: ${esc(recorded(facts.api_id))}<br>
          ${esc(facts.execution_id || "")}<br>${esc(facts.input_message_id || "")}<br>${esc(facts.model_evidence || "")}</details>` : ""}
        </div>`;
    }

    function refreshReaderLanguage() {
      const detail = state.detail;
      const source = $("analysisReaderContent")?.querySelector(".analysisProvenance");
      if (source && detail?.paper) {
        const open = !!source.querySelector("details[open]");
        source.outerHTML = analysisSourceHtml(detail.analysis?.provenance || {});
        if (open) $("analysisReaderContent")?.querySelector(".analysisProvenance details")?.setAttribute("open", "");
      }
    }

    function renderReaderPanels(detail, paper, analysisKind, analysisHtml) {
      const pdfHtml = paper.pdf_url
        ? window.PortalI18n.html`<div class="pdfJsViewer" id="paperPdfViewer" data-pdf-url="${esc(paper.pdf_url)}">
              <div class="pdfJsStatus"><!--portal-i18n:"Loading PDF..."-->Loading PDF...</div>
            </div>`
        : paperStateHtml("No PDF path or URL registered for this paper.", { density: "compact" });
      const readerGridClasses = [
        "readerGrid",
        state.readerAnalysisCollapsed ? "analysisCollapsed" : "",
        state.readerPdfCollapsed ? "pdfCollapsed" : "",
      ].filter(Boolean).join(" ");
      const readerViewState = paperReaderViewStates.get(String(paper.id || "")) || null;
      $("workspaceMain").classList.add("is-reader-mode");
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="${readerGridClasses}" data-reader-paper-id="${esc(paper.id || "")}">
            <section class="readerPanel ${state.readerAnalysisCollapsed ? "collapsed" : ""}" aria-label="Analysis 阅读面板" data-portal-i18n-attrs='{"aria-label":"Analysis 阅读面板"}'>
              <header class="readerHead">
                <div class="readerHeadIdentity">
                  <span class="readerHeadIcon" aria-hidden="true"><!--portal-i18n:"MD"-->MD</span>
                  <span class="readerHeadText">
                    <span class="readerHeadTitle" data-portal-i18n-text="${analysisKind === "summary" ? window.PortalI18n.t("分析 v0 · 短摘要") : window.PortalI18n.t("Analysis")}">${window.PortalI18n.t(analysisKind === "summary" ? "分析 v0 · 短摘要" : "Analysis")}</span>
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
            <section class="readerPanel pdfReaderPanel ${state.readerPdfCollapsed ? "collapsed" : ""}" aria-label="PDF 阅读面板" data-portal-i18n-attrs='{"aria-label":"PDF 阅读面板"}'>
              <header class="readerHead">
                <div class="readerHeadIdentity">
                  <span class="readerHeadIcon" aria-hidden="true"><!--portal-i18n:"PDF"-->PDF</span>
                  <span class="readerHeadText">
                    <span class="readerHeadTitle"><!--portal-i18n:"PDF"-->PDF</span>
                    ${paper.pdf_url ? window.PortalI18n.html`<span class="readerPath" aria-label="PDF 文件：${esc(paper.pdf_url)}">${esc(shortPath(paper.pdf_url, 72))}</span>` : window.PortalI18n.html`<span class="readerPath"><!--portal-i18n:"尚未注册 PDF"-->尚未注册 PDF</span>`}
                  </span>
                </div>
                <div class="readerHeadActions">
                  ${paper.pdf_url ? window.PortalI18n.html`<a class="portalButton is-icon is-quiet iconBtn readerExternalLink" href="${esc(paper.pdf_url)}" target="_blank" rel="noopener noreferrer" data-portal-tooltip="在新标签页打开 PDF" aria-label="在新标签页打开 PDF" data-portal-icon="external-link" data-portal-i18n-attrs='{"data-portal-tooltip":"在新标签页打开 PDF","aria-label":"在新标签页打开 PDF"}'></a>` : ""}
                  ${paper.pdf_url ? window.PortalI18n.html`<button class="portalButton is-compact" id="paperFullscreenBtn" type="button" data-portal-i18n-text="Fullscreen">${window.PortalI18n.t("Fullscreen")}</button>` : ""}
                  ${paper.has_pdf ? window.PortalI18n.html`<button class="portalButton is-compact is-danger" type="button" id="deletePaperPdfBtn" ${state.pdfDeleteInFlight ? "disabled" : ""}>${state.pdfDeleteInFlight ? window.PortalI18n.t("Deleting PDF...") : window.PortalI18n.t("Delete")}</button>` : ""}
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
      $("paperFullscreenBtn")?.addEventListener("click", () => openFullscreenReader(paper));
      if (paper.pdf_url && !state.readerPdfCollapsed) {
        ensureReader(paper.pdf_url).catch(alertError);
      } else if (!paper.pdf_url) {
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
      refreshReaderLanguage, renderReaderMode, runSelectionAction,
    });
  }

  window.PortalPaperReader = Object.freeze({ createReader, dependencies: DEPENDENCIES });
})();
