(() => {
  "use strict";

  if (window.PortalPaperWorkspaceView) return;

  // The workspace around a mode: the Paper's identity, header and details,
  // the mode's actions, the deep read controls, the API mode, and
  // renderWorkspace, which draws the active mode.
  const DEPENDENCIES = Object.freeze([
    "$", "capturePaperReaderViewState", "ccfVenueYearLabel", "ccfWorkspaceMeta", "currentCcfEntry",
    "currentCcfVenue", "esc", "libraryHeaderMeta", "paperApi", "paperStateHtml", "renderCcfWorkspace",
    "renderExpMode", "renderFiguresMode", "renderLibraryMode", "renderNotesMode", "renderReaderMode",
    "renderRefsMode", "renderReviewMode", "state", "syncFavoriteButtons", "syncModeTabs", "writeUiState",
  ]);

  function createWorkspaceView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperWorkspaceView is missing: ${missing.join(", ")}`);
    const {
      $, capturePaperReaderViewState, ccfVenueYearLabel, ccfWorkspaceMeta, currentCcfEntry, currentCcfVenue,
      esc, libraryHeaderMeta, paperApi, paperStateHtml, renderCcfWorkspace, renderExpMode, renderFiguresMode,
      renderLibraryMode, renderNotesMode, renderReaderMode, renderRefsMode, renderReviewMode, state,
      syncFavoriteButtons, syncModeTabs, writeUiState,
    } = dependencies;

    function paperTags(paper) {
      const values = [];
      ["tags", "workload", "platform", "architecture", "optimization"].forEach(key => {
        (paper?.[key] || []).forEach(item => values.push(item));
      });
      return Array.from(new Set(values.map(String).filter(Boolean)));
    }

    function chip(label, cls = "") {
      const tones = { good: "success", warn: "warning", accent: "info", author: "neutral" };
      return window.PortalUIPrimitives.statusHtml(label, {
        label,
        tone: tones[cls] || "neutral",
        className: cls === "author" ? "paperStatusAuthor" : "",
        title: cls === "author" ? label : "",
      });
    }

    function renderPaperResourceIdentity(title, resource = null) {
      const resourceId = String(resource?.search_id || resource?.id || resource?.paper_id || resource?.registered_paper_id || "").trim();
      const isSearch = resourceId.startsWith("search_");
      const idSlot = $("workspaceResourceIdSlot");
      idSlot?.replaceChildren();
      window.PortalResourceHeader?.mount?.("workspaceResourceIdentity", {
        title: title || resource?.title || resourceId || window.PortalI18n.t("Paper Research"),
        type: resourceId ? (isSearch ? "search" : "paper") : "",
        resource_id: resourceId,
        copy_label: isSearch ? "search_id" : "paper_id",
      }, {
        headingTag: "div",
        showType: false,
        titleClass: "workspaceTitle",
        titleId: "workspaceTitle",
        idId: "workspaceResourceId",
      });
      const idElement = $("workspaceResourceIdentity")?.querySelector("#workspaceResourceId");
      $("workspaceTitle")?.removeAttribute("title");
      if (idElement && idSlot) idSlot.append(idElement);
      const metaRow = idSlot?.closest(".workspaceIdentityMetaRow");
      const idWidth = idElement ? Math.ceil(idElement.scrollWidth) : 0;
      metaRow?.style.setProperty("--workspace-resource-id-width", `${idWidth}px`);
    }

    function paperHeaderMeta(paper) {
      if (!paper) return window.PortalI18n.t("Browse, read, chat, and review papers from one workspace.");
      return [
        chip(paper.venue || window.PortalI18n.t("Venue")),
        paper.year ? chip(paper.year) : "",
        paper.has_analysis ? chip(window.PortalI18n.t("Analyzed"), "good") : paper.has_summary ? chip(window.PortalI18n.t("分析 v0 · 短摘要")) : chip(window.PortalI18n.t("No analysis"), "warn"),
        ...paperTags(paper).map(tag => chip(tag, "accent")),
      ].filter(Boolean).join("");
    }

    function renderWorkspaceDetails(summaryText, metaHtml) {
      const toggle = $("workspaceDetailsToggle");
      const body = $("workspaceDetailsBody");
      const summary = $("workspaceSummary");
      const meta = $("workspaceMeta");
      if (!toggle || !body || !summary || !meta) return;
      const normalizedSummary = String(summaryText || "").trim();
      summary.textContent = normalizedSummary;
      summary.classList.toggle("visible", !!normalizedSummary);
      meta.innerHTML = String(metaHtml || "");
      const hasDetails = !!normalizedSummary || !!meta.textContent.trim();
      toggle.hidden = !hasDetails;
      if (!hasDetails) toggle.setAttribute("aria-expanded", "false");
      body.hidden = !hasDetails || toggle.getAttribute("aria-expanded") !== "true";
    }

    function renderWorkspace(options = {}) {
      const result=renderWorkspaceContent(options);
      if(!options.chromeOnly)window.PortalPaperNavigation?.decorateHome(state);
      updateDeepReadControls();
      return result;
    }
    function renderWorkspaceContent({ chromeOnly = false } = {}) {
      syncFavoriteButtons();
      capturePaperReaderViewState();
      if (!chromeOnly) $("workspaceMain")?.classList.remove("is-reader-mode");
      if (state.leftFace === "ccf" && !["library", "reader"].includes(state.mode)) {
        state.mode = "library";
        state.ccfSelectedPaper = null;
        writeUiState();
      }
      if (state.leftFace === "ccf" && state.mode === "reader" && !state.ccfSelectedPaper) {
        state.mode = "library";
        writeUiState();
      }
      if (state.leftFace === "ccf" && state.mode === "library" && state.ccfSelectedPaper) {
        state.ccfSelectedPaper = null;
        writeUiState();
      }
      syncModeTabs();
      const paper = state.detail?.paper || null;
      const librarySummary = state.library?.summary || {};
      const semantic = state.mode === "library" && state.activeSemanticSearchId ? librarySummary.semantic_search || null : null;
      const collection = state.mode === "library" && state.activeCollectorJobId ? librarySummary.paper_collection || null : null;
      const isLibraryMode = state.mode === "library" || state.mode === "today";
      const isCcfMode = state.leftFace === "ccf" && (isLibraryMode || (state.mode === "reader" && !!state.ccfSelectedPaper));
      if (isCcfMode) {
        const venue = currentCcfVenue();
        const entry = currentCcfEntry();
        const workspaceTitle = state.ccfSelectedPaper
          ? (state.ccfSelectedPaper.title || window.PortalI18n.t("DBLP Paper"))
          : (venue ? ccfVenueYearLabel(venue, entry || state.ccfSelectedYear) : window.PortalI18n.t("CCF A/B Proceedings"));
        renderPaperResourceIdentity(
          workspaceTitle,
          state.ccfSelectedPaper?.registered_paper_id
            ? { ...state.ccfSelectedPaper, id: state.ccfSelectedPaper.registered_paper_id }
            : null,
        );
        renderWorkspaceDetails(
          venue?.name || "Browse CCF-A, CCF-B, and curated chip/circuit venues from DBLP.",
          ccfWorkspaceMeta(),
        );
        $("workspaceModeActions").innerHTML = "";
        $("workspaceModeActions").parentElement.style.display = "";
        if (!chromeOnly) renderCcfWorkspace();
        return;
      }
      const workspaceTitle = isLibraryMode
        ? (semantic ? window.PortalI18n.text`Search: ${semantic.query || window.PortalI18n.t("Semantic search")}` : (collection ? window.PortalI18n.text`Collect: ${collection.direction || window.PortalI18n.t("Paper collection")}` : window.PortalI18n.t(state.paperHome?'Paper Library':state.paperListScope==='recent'?'近期阅读':state.paperListScope==='favorites'?'我的收藏':state.mode === "today" ? "今日新增" : "全库")))
        : (paper?.title || window.PortalI18n.t("Paper Research"));
      renderPaperResourceIdentity(workspaceTitle, isLibraryMode ? semantic : paper);
      const summaryText = isLibraryMode
        ? String(semantic?.query_interpretation || semantic?.notes || collection?.summary || collection?.error || (state.mode === "today" ? window.PortalI18n.t("Papers registered today.") : "")).trim()
        : "";
      renderWorkspaceDetails(summaryText, isLibraryMode ? libraryHeaderMeta() : paperHeaderMeta(paper));
      renderWorkspaceModeActions(paper);
      if (chromeOnly) return updateDeepReadControls();
      if (isLibraryMode) return renderLibraryMode();
      if (state.mode === "reader") return renderReaderMode();
      if (state.mode === "review") return renderReviewMode();
      if (state.mode === "exp") return renderExpMode();
      if (state.mode === "figures") return renderFiguresMode();
      if (state.mode === "notes") return renderNotesMode();
      if (state.mode === "refs") return renderRefsMode();
      if (state.mode === "api") return renderApiMode();
    }

    function renderWorkspaceModeActions(paper) {
      const target = $("workspaceModeActions");
      if (!target) return;
      if (state.mode === "library" || state.mode === "today") {
        target.innerHTML = window.PortalI18n.html`
            <button class="portalButton is-compact is-quiet" type="button" id="exportLibraryMarkdownBtn" ${state.library?.papers?.length ? "" : "disabled"}>
              ${window.PortalI18n.t("Export")}
            </button>
          `;
        return;
      }
      if (paper) {
        target.innerHTML = '';
        return;
      }
      target.innerHTML = "";
    }

    function needPaperHtml() {
      return paperStateHtml("Select a paper from the left library first.");
    }

    function detailSectionLoadingHtml(label) {
      return paperStateHtml(window.PortalI18n.text`Loading ${window.PortalI18n.t(label)}…`, { state: "loading" });
    }

    function deepReadStateFor(paper = state.detail?.paper) {
      const deep = state.deepReadStatus && state.deepReadStatus.paper_id === paper?.id ? state.deepReadStatus : null;
      const running = deep && ["queued", "running"].includes(String(deep.status || ""));
      const done = deep && String(deep.status || "") === "done";
      const error = deep && String(deep.status || "") === "error";
      const confirming=running&&deep.step==='confirming submission';
      const text = confirming?window.PortalI18n.t('正在确认任务状态'):running
        ? window.PortalI18n.text`Deep Read: ${window.PortalI18n.t({queued: "Queued", "extracting pdf text": "Extracting PDF", "waiting for Resident": "Model generation", Submitting: "Submitting"}[deep.step] || deep.step || deep.status)} · ${deep.progress || 0}%`
        : done
          ? window.PortalI18n.t("Deep Read done")
          : error
            ? window.PortalI18n.t('Deep Read failed. See Details.')
            : paper?.has_analysis
              ? window.PortalI18n.t("Analysis ready")
              : window.PortalI18n.t("No analysis yet");
      const buttonText = window.PortalI18n.t(running ? "Deep Reading" : error ? "Retry Deep Read" : paper?.has_analysis ? "Re-run Deep Read" : "Deep Read");
      return { deep, running, done, error, confirming, text, buttonText };
    }

    function updateDeepReadControls() {
      const paper = state.detail?.paper || null;
      if (!paper) return;
      const status = deepReadStateFor(paper);
      const textEl = $("deepReadStatusText");
      if (textEl) textEl.textContent = status.text;
      const details=$('deepReadStatusDetails');
      if(details){details.hidden=!status.error;details.querySelector('summary').textContent=window.PortalI18n.t('Details');const body=details.querySelector('pre');if(body)body.textContent=status.deep?.error||window.PortalI18n.t('unknown error');}
      for(const btn of document.querySelectorAll('#deepReadBtn,[data-deep-read-start]')) {
        btn.textContent = status.buttonText;
        btn.disabled = !paper.has_pdf || !!status.running;
      }
    }
    function renderAnalysisControls(paper) {
      const status=deepReadStateFor(paper);
      const button=header=>`<button class="portalButton is-compact ${paper.has_analysis?'':'is-primary'}" type="button" ${header?'id="deepReadBtn"':'data-deep-read-start'} ${paper.has_pdf&&!status.running?'':'disabled'}>${esc(status.buttonText)}</button>`;
      return {button:button(true),emptyButton:button(false),subtitle:`<span class="readerDeepReadStatus"><span class="readerDeepReadSubtitle" id="deepReadStatusText" role="status" aria-live="polite">${esc(status.text)}</span><details id="deepReadStatusDetails" ${status.error?'':'hidden'}><summary>${esc(window.PortalI18n.t('Details'))}</summary><pre>${esc(status.deep?.error||'')}</pre></details></span>`};
    }

    async function renderApiMode() {
      const docs = await paperApi.request("/paper/api/docs");
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="apiCard" style="margin-bottom:10px;">
            <h3><!--portal-i18n:"Agent-ready paper library API"-->Agent-ready paper library API</h3>
            <p><!--portal-i18n:"同一套接口服务 Paper 页面、Research Chat，以及之后的 deep-read / review agent。"-->同一套接口服务 Paper 页面、Research Chat，以及之后的 deep-read / review agent。</p>
            <p><code>${esc(docs.data_root || "")}</code></p>
          </div>
          <table class="apiTable">
            <thead><tr><th><!--portal-i18n:"Paper Method"-->Method</th><th><!--portal-i18n:"Paper Path"-->Path</th><th><!--portal-i18n:"Paper Purpose"-->Purpose</th></tr></thead>
            <tbody>
              ${(docs.endpoints || []).map(row => window.PortalI18n.html`
                <tr><td><code>${esc(row.method)}</code></td><td><code>${esc(row.path)}</code></td><td>${esc(row.purpose)}</td></tr>
              `).join("")}
            </tbody>
          </table>
        `;
    }

    return Object.freeze({
      chip, detailSectionLoadingHtml, needPaperHtml, paperTags, renderWorkspace, updateDeepReadControls, renderAnalysisControls,
    });
  }

  window.PortalPaperWorkspaceView = Object.freeze({ createWorkspaceView, dependencies: DEPENDENCIES });
})();
