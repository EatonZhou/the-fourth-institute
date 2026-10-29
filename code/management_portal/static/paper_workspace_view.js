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
        titleClass: "workspaceTitle",
        titleId: "workspaceTitle",
        idId: "workspaceResourceId",
      });
      const idElement = $("workspaceResourceIdentity")?.querySelector("#workspaceResourceId");
      if (idElement && idSlot) idSlot.append(idElement);
      const metaRow = idSlot?.closest(".workspaceIdentityMetaRow");
      const idWidth = idElement ? Math.ceil(idElement.scrollWidth) : 0;
      metaRow?.style.setProperty("--workspace-resource-id-width", `${idWidth}px`);
    }

    function paperHeaderMeta(paper) {
      if (!paper) return "Browse, read, chat, and review papers from one workspace.";
      return [
        chip(paper.venue || "Venue"),
        paper.year ? chip(paper.year) : "",
        paper.has_analysis ? chip("Analyzed", "good") : chip("No analysis", "warn"),
        paper.authors ? chip(paper.authors, "author") : "",
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

    function renderWorkspace() {
      syncFavoriteButtons();
      capturePaperReaderViewState();
      $("workspaceMain")?.classList.remove("is-reader-mode");
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
      const semantic = librarySummary.semantic_search || null;
      const collection = librarySummary.paper_collection || null;
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
        renderCcfWorkspace();
        return;
      }
      const workspaceTitle = isLibraryMode
        ? (semantic ? window.PortalI18n.text`Search: ${semantic.query || window.PortalI18n.t("Semantic search")}` : (collection ? window.PortalI18n.text`Collect: ${collection.direction || window.PortalI18n.t("Paper collection")}` : (state.mode === "today" ? "Today" : window.PortalI18n.t("Paper Library"))))
        : (paper?.title || window.PortalI18n.t("Paper Research"));
      renderPaperResourceIdentity(workspaceTitle, isLibraryMode ? semantic : paper);
      const summaryText = isLibraryMode
        ? String(semantic?.query_interpretation || semantic?.notes || collection?.summary || collection?.error || (state.mode === "today" ? window.PortalI18n.t("Papers registered today.") : "")).trim()
        : String(state.detail?.summary?.content || "").trim();
      renderWorkspaceDetails(summaryText, isLibraryMode ? libraryHeaderMeta() : paperHeaderMeta(paper));
      renderWorkspaceModeActions(paper);
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
              Export
            </button>
          `;
        return;
      }
      if (state.mode === "reader" && paper) {
        const deepState = deepReadStateFor(paper);
        const uploadButton = !paper.has_pdf
          ? window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" id="uploadPaperPdfBtn" ${state.pdfUploadInFlight ? "disabled" : ""}>${state.pdfUploadInFlight ? "Uploading..." : "+ PDF"}</button>`
          : "";
        const deletePdfButton = paper.has_pdf
          ? window.PortalI18n.html`<button class="portalButton is-compact is-danger" type="button" id="deletePaperPdfBtn" ${state.pdfDeleteInFlight ? "disabled" : ""}>${state.pdfDeleteInFlight ? window.PortalI18n.t("Deleting PDF...") : window.PortalI18n.t("Delete PDF")}</button>`
          : "";
        target.innerHTML = window.PortalI18n.html`
            <span class="paneNote" id="deepReadStatusText">${esc(deepState.text)}</span>
            <span class="workspaceActionButtons">
              ${uploadButton}
              ${deletePdfButton}
              <button class="portalButton is-compact is-primary" type="button" id="deepReadBtn" ${paper.has_pdf && !deepState.running ? "" : "disabled"}>
                ${esc(deepState.buttonText)}
              </button>
              <button class="portalButton is-compact is-danger" type="button" id="deletePaperBtn">删除</button>
            </span>
          `;
        return;
      }
      if (paper) {
        target.innerHTML = window.PortalI18n.html`
            <span class="workspaceActionButtons">
              ${!paper.has_pdf ? window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" id="uploadPaperPdfBtn" ${state.pdfUploadInFlight ? "disabled" : ""}>${state.pdfUploadInFlight ? "Uploading..." : "+ PDF"}</button>` : ""}
              <button class="portalButton is-compact is-danger" type="button" id="deletePaperBtn">删除</button>
            </span>
          `;
        return;
      }
      target.innerHTML = "";
    }

    function needPaperHtml() {
      return paperStateHtml("Select a paper from the left library first.");
    }

    function detailSectionLoadingHtml(label) {
      return paperStateHtml(`Loading ${label}…`, { state: "loading" });
    }

    function deepReadStateFor(paper = state.detail?.paper) {
      const deep = state.deepReadStatus && state.deepReadStatus.paper_id === paper?.id ? state.deepReadStatus : null;
      const running = deep && ["queued", "running"].includes(String(deep.status || ""));
      const done = deep && String(deep.status || "") === "done";
      const error = deep && String(deep.status || "") === "error";
      const text = running
        ? `Deep Read: ${deep.step || deep.status} · ${deep.progress || 0}%`
        : done
          ? `Deep Read done${deep.usage?.total_tokens ? ` · ${deep.usage.total_tokens} tokens` : ""}`
          : error
            ? `Deep Read failed: ${deep.error || "unknown error"}`
            : paper?.has_analysis
              ? "Analysis ready"
              : "No analysis yet";
      const buttonText = running ? "Deep Reading" : paper?.has_analysis ? "Re-run Deep Read" : "Deep Read";
      return { deep, running, done, error, text, buttonText };
    }

    function updateDeepReadControls() {
      const paper = state.detail?.paper || null;
      if (!paper) return;
      const status = deepReadStateFor(paper);
      const textEl = $("deepReadStatusText");
      const btn = $("deepReadBtn");
      if (textEl) textEl.textContent = status.text;
      if (btn) {
        btn.textContent = status.buttonText;
        btn.disabled = !paper.has_pdf || !!status.running;
      }
    }

    async function renderApiMode() {
      const docs = await paperApi.request("/paper/api/docs");
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="apiCard" style="margin-bottom:10px;">
            <h3>Agent-ready paper library API</h3>
            <p>同一套接口服务 Paper 页面、Research Chat，以及之后的 deep-read / review agent。</p>
            <p><code>${esc(docs.data_root || "")}</code></p>
          </div>
          <table class="apiTable">
            <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
            <tbody>
              ${(docs.endpoints || []).map(row => window.PortalI18n.html`
                <tr><td><code>${esc(row.method)}</code></td><td><code>${esc(row.path)}</code></td><td>${esc(row.purpose)}</td></tr>
              `).join("")}
            </tbody>
          </table>
        `;
    }

    return Object.freeze({
      chip, detailSectionLoadingHtml, needPaperHtml, paperTags, renderWorkspace, updateDeepReadControls,
    });
  }

  window.PortalPaperWorkspaceView = Object.freeze({ createWorkspaceView, dependencies: DEPENDENCIES });
})();
