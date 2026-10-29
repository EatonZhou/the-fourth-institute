(() => {
  "use strict";

  if (window.PortalPaperEvents) return;

  // The page's event wiring; bindEvents binds every area's controls in order.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "activeChatWorkspaceId", "alertError", "applyCcfPaperListFilter",
    "canRestorePaperWorkspace", "ccfYearKey", "closePaperModal", "createExp", "createReview",
    "currentCcfPayload", "currentPaperWorkspaceSnapshot", "deleteCollectorJob", "deleteCurrentPaper",
    "deleteCurrentPaperPdf", "deleteGeneratedFigure", "deleteNote", "deleteReview",
    "ensureCcfPaperEnrichment", "explainFigure", "exportLibraryMarkdown", "extractFigures",
    "fetchCcfYearsForVenue", "fetchSelectedCcfPapers", "figureContextFromInput", "figureContextFromKey",
    "figureInputItems", "generateFigureDraft", "goPreviousPaperWorkspace", "isCollectionMode",
    "isPreviousPaperWorkspaceShortcut", "loadCcfPapers", "loadCcfYears", "loadChatHistory", "loadDetail",
    "loadLibrary", "loadMoreLibrary", "maybeShowSelectionMenu", "openFigureLightbox", "openPaperModal",
    "openPdfUploadPicker", "openRefPaper", "pastedImageFiles", "refreshCurrentLeftFace",
    "refreshFigureInputTray", "registerCcfPaper", "registerRef", "rememberPreviousPaperWorkspace",
    "renderCollectorJobs", "renderTree", "renderWorkspace", "resumePaperUpdates", "runSelectionAction",
    "scheduleCcfYearFilterRender", "selectCollectorJob", "setMode", "showCcfFace", "showLibraryFace",
    "startDeepRead", "state", "submitCollector", "submitPaper", "suspendPaperUpdates", "syncModeTabs",
    "togglePane", "togglePaperFavorite", "toggleReaderPanel", "updateLeftFaceTabs", "uploadCurrentPaperPdf",
    "uploadFigureInputFile", "uploadPaperRegistrationPdf", "writeUiState",
  ]);

  function createEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperEvents is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, applyCcfPaperListFilter,
      canRestorePaperWorkspace, ccfYearKey, closePaperModal, createExp, createReview, currentCcfPayload,
      currentPaperWorkspaceSnapshot, deleteCollectorJob, deleteCurrentPaper, deleteCurrentPaperPdf,
      deleteGeneratedFigure, deleteNote, deleteReview, ensureCcfPaperEnrichment, explainFigure,
      exportLibraryMarkdown, extractFigures, fetchCcfYearsForVenue, fetchSelectedCcfPapers,
      figureContextFromInput, figureContextFromKey, figureInputItems, generateFigureDraft,
      goPreviousPaperWorkspace, isCollectionMode, isPreviousPaperWorkspaceShortcut, loadCcfPapers,
      loadCcfYears, loadChatHistory, loadDetail, loadLibrary, loadMoreLibrary, maybeShowSelectionMenu,
      openFigureLightbox, openPaperModal, openPdfUploadPicker, openRefPaper, pastedImageFiles,
      refreshCurrentLeftFace, refreshFigureInputTray, registerCcfPaper, registerRef,
      rememberPreviousPaperWorkspace, renderCollectorJobs, renderTree, renderWorkspace, resumePaperUpdates,
      runSelectionAction, scheduleCcfYearFilterRender, selectCollectorJob, setMode, showCcfFace,
      showLibraryFace, startDeepRead, state, submitCollector, submitPaper, suspendPaperUpdates, syncModeTabs,
      togglePane, togglePaperFavorite, toggleReaderPanel, updateLeftFaceTabs, uploadCurrentPaperPdf,
      uploadFigureInputFile, uploadPaperRegistrationPdf, writeUiState,
    } = dependencies;

    function bindEvents() {
      bindLibraryEvents();
      bindRegistrationEvents();
      bindLayoutEvents();
      bindWorkspaceEvents();
      bindTreeEvents();
      bindSelectionEvents();
      bindSearchEvents();
      bindPageLifecycleEvents();
    }

    // The Library pane: favorites, the favorite filter, refreshing, Collector jobs.
    function bindLibraryEvents() {
      document.addEventListener("click", event => {
        const button = event.target.closest("[data-paper-favorite]");
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        togglePaperFavorite(button.dataset.paperFavorite).catch(alertError);
      }, true);
      $("favoriteFilter").addEventListener("change", event => {
        state.filters.favorite = event.target.checked ? "1" : "";
        writeUiState();
        loadLibrary({ keepDetail: true, hydrateDetail: false }).catch(alertError);
      });
      $("refreshBtn").addEventListener("click", () => refreshCurrentLeftFace().catch(alertError));
      $("collectorForm").addEventListener("submit", event => submitCollector(event).catch(alertError));
      $("collectorJobList").addEventListener("click", event => {
        const deleteBtn = event.target.closest("[data-collector-delete]");
        if (deleteBtn) {
          deleteCollectorJob(deleteBtn.dataset.collectorDelete || "").catch(alertError);
          return;
        }
        const selectBtn = event.target.closest("[data-collector-select]");
        if (!selectBtn || selectBtn.disabled) return;
        selectCollectorJob(selectBtn.dataset.collectorSelect || "").catch(alertError);
      });
    }

    // Registering a Paper: the dialog, its form, the PDF upload and drop zone.
    function bindRegistrationEvents() {
      $("newPaperBtn").addEventListener("click", openPaperModal);
      $("closePaperModal").addEventListener("click", closePaperModal);
      $("cancelPaperModal").addEventListener("click", closePaperModal);
      $("paperForm").addEventListener("submit", event => submitPaper(event).catch(alertError));
      ["input", "change"].forEach(eventName => {
        $("paperForm").addEventListener(eventName, event => {
          if (event.target.name) state.registrationEditedFields.add(event.target.name);
        });
      });
      $("paperRegisterUploadBtn").addEventListener("click", () => {
        const input = $("paperRegisterUploadInput");
        if (!input || state.registrationDraftInFlight || state.registrationSubmitInFlight) return;
        input.value = "";
        input.click();
      });
      $("paperRegisterUploadInput").addEventListener("change", event => {
        const file = event.target.files?.[0];
        if (file) uploadPaperRegistrationPdf(file).catch(alertError);
      });
      const registrationDropzone = $("paperRegisterUploadDropzone");
      ["dragenter", "dragover"].forEach(type => registrationDropzone.addEventListener(type, event => {
        event.preventDefault();
        if (!state.registrationDraftInFlight && !state.registrationSubmitInFlight) {
          registrationDropzone.classList.add("is-dragover");
        }
      }));
      ["dragleave", "drop"].forEach(type => registrationDropzone.addEventListener(type, event => {
        event.preventDefault();
        registrationDropzone.classList.remove("is-dragover");
      }));
      registrationDropzone.addEventListener("drop", event => {
        const file = event.dataTransfer?.files?.[0];
        if (file) uploadPaperRegistrationPdf(file).catch(alertError);
      });
    }

    // Panes, the CCF year filter, the Library and CCF faces, mode tabs, the workspace header.
    function bindLayoutEvents() {
      $("toggleLibraryPaneBtn").addEventListener("click", () => togglePane("left"));
      $("toggleChatPaneBtn")?.addEventListener("click", () => togglePane("chat"));
      $("ccfYearFilterInput")?.addEventListener("input", event => {
        state.ccfYearFilter = String(event.target.value || "");
        writeUiState();
        if (state.leftFace === "ccf") {
          renderTree();
          scheduleCcfYearFilterRender();
        }
      });
      $("leftFaceTabs").addEventListener("click", event => {
        const btn = event.target.closest("button[data-left-face]");
        if (!btn) return;
        if (btn.dataset.leftFace === "ccf") {
          showCcfFace().catch(alertError);
        } else {
          showLibraryFace();
        }
      });
      $("modeTabs").addEventListener("click", event => {
        const btn = event.target.closest("button[data-mode]");
        if (btn) setMode(btn.dataset.mode);
      });
      $("workspaceDetailsToggle")?.addEventListener("click", event => {
        const toggle = event.currentTarget;
        const expanded = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        const body = $("workspaceDetailsBody");
        if (body) body.hidden = !expanded;
      });
      $("workspaceModeActions").addEventListener("click", event => {
        if (event.target.closest("#exportLibraryMarkdownBtn")) {
          exportLibraryMarkdown().catch(alertError);
          return;
        }
        if (event.target.closest("#deepReadBtn")) startDeepRead().catch(alertError);
        if (event.target.closest("#uploadPaperPdfBtn")) openPdfUploadPicker();
        if (event.target.closest("#deletePaperPdfBtn")) deleteCurrentPaperPdf().catch(alertError);
        if (event.target.closest("#deletePaperBtn")) deleteCurrentPaper().catch(alertError);
      });
      $("paperPdfUploadInput").addEventListener("change", event => {
        const file = event.target.files?.[0] || null;
        uploadCurrentPaperPdf(file).catch(alertError);
      });
    }

    // The workspace: CCF search, clicks on every mode's controls, figure requests.
    function bindWorkspaceEvents() {
      $("workspaceMain").addEventListener("input", event => {
        const input = event.target.closest("[data-ccf-paper-search]");
        if (!input) return;
        state.ccfPaperQuery = String(input.value || "");
        state.ccfRenderLimits[ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear)] = 100;
        applyCcfPaperListFilter();
      });
      $("workspaceMain").addEventListener("click", event => {
        if (event.target.closest("#loadMoreLibraryBtn")) {
          loadMoreLibrary().catch(alertError);
          return;
        }
        if (event.target.closest("#ccfPaperLoadMore")) {
          const key = ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear);
          state.ccfRenderLimits[key] = Number(state.ccfRenderLimits[key] || 100) + 100;
          applyCcfPaperListFilter();
          return;
        }
        const fetchCcfPapersBtn = event.target.closest("[data-fetch-ccf-papers]");
        if (fetchCcfPapersBtn) {
          fetchSelectedCcfPapers().catch(alertError);
          return;
        }
        const ccfWebEnrichBtn = event.target.closest("[data-ccf-web-enrich]");
        if (ccfWebEnrichBtn) {
          if (!state.ccfSelectedPaper) return;
          ccfWebEnrichBtn.disabled = true;
          ccfWebEnrichBtn.textContent = "Investigating...";
          ensureCcfPaperEnrichment(state.ccfSelectedPaper, { web: true }).catch(alertError);
          return;
        }
        const registerCcfPaperBtn = event.target.closest("[data-register-ccf-paper]");
        if (registerCcfPaperBtn) {
          if (!state.ccfSelectedPaper) return;
          registerCcfPaperBtn.disabled = true;
          registerCcfPaperBtn.textContent = "Registering...";
          registerCcfPaper(state.ccfSelectedPaper).catch(alertError);
          return;
        }
        const ccfPaperRow = event.target.closest("[data-ccf-paper-index]");
        if (ccfPaperRow) {
          const payload = currentCcfPayload();
          const paper = payload?.papers?.[Number(ccfPaperRow.dataset.ccfPaperIndex)];
          if (!paper) return;
          if (paper.registered_paper_id) {
            rememberPreviousPaperWorkspace(currentPaperWorkspaceSnapshot({
              leftFace: "library",
              mode: "reader",
              selectedId: paper.registered_paper_id,
            }));
            state.leftFace = "library";
            state.ccfSelectedPaper = null;
            updateLeftFaceTabs();
            loadDetail(paper.registered_paper_id).then(() => setMode("reader")).catch(alertError);
          } else {
            state.ccfSelectedPaper = paper;
            state.mode = "reader";
            writeUiState();
            syncModeTabs();
            renderWorkspace();
          }
          return;
        }
        const registerRefBtn = event.target.closest("[data-register-ref]");
        if (registerRefBtn) {
          event.stopPropagation();
          registerRef(registerRefBtn.dataset.registerRef || "").catch(alertError);
          return;
        }
        const refCard = event.target.closest("[data-open-ref-paper]");
        if (refCard && !event.target.closest("button,a,input,textarea,select")) {
          openRefPaper(refCard.dataset.openRefPaper || "").catch(alertError);
          return;
        }
        const row = event.target.closest("[data-paper-id]");
        if (row) {
          rememberPreviousPaperWorkspace(currentPaperWorkspaceSnapshot({
            leftFace: "library",
            mode: "reader",
            selectedId: row.dataset.paperId || "",
          }));
          loadDetail(row.dataset.paperId).then(() => {
            if (isCollectionMode()) setMode("reader");
          }).catch(alertError);
          return;
        }
        if (event.target.closest("#createReviewBtn")) createReview().catch(alertError);
        if (event.target.closest("#createExpBtn")) createExp().catch(alertError);
        if (event.target.closest("#extractFiguresBtn")) {
          extractFigures().catch(alertError);
          return;
        }
        const selectFigureBtn = event.target.closest("[data-select-figure]");
        if (selectFigureBtn) {
          state.selectedFigureId = selectFigureBtn.dataset.selectFigure || "";
          renderWorkspace();
          return;
        }
        const explainFigureBtn = event.target.closest("[data-explain-figure]");
        if (explainFigureBtn) {
          explainFigure(explainFigureBtn.dataset.explainFigure || "").catch(alertError);
          return;
        }
        const generateFigureBtn = event.target.closest("[data-generate-figure]");
        if (generateFigureBtn) {
          generateFigureDraft(generateFigureBtn.dataset.generateFigure || "").catch(alertError);
          return;
        }
        const generateFigureVariantBtn = event.target.closest("[data-generate-figure-variant]");
        if (generateFigureVariantBtn) {
          generateFigureDraft(
            generateFigureVariantBtn.dataset.generateFigureVariant || "",
            generateFigureVariantBtn.dataset.sourceGeneratedId || "",
          ).catch(alertError);
          return;
        }
        const figureInputRemove = event.target.closest("[data-figure-input-remove]");
        if (figureInputRemove) {
          const key = figureInputRemove.dataset.figureInputRemove || "";
          const id = figureInputRemove.dataset.inputId || "";
          state.figureInputImages[key] = figureInputItems(key).filter(item => item.id !== id);
          refreshFigureInputTray(key);
          return;
        }
        const figureInputOpen = event.target.closest("[data-figure-input-open]");
        if (figureInputOpen) {
          const key = figureInputOpen.dataset.figureInputOpen || "";
          const id = figureInputOpen.dataset.inputId || "";
          const item = figureInputItems(key).find(candidate => candidate.id === id);
          if (item?.previewUrl) {
            const context = figureContextFromKey(key);
            openFigureLightbox({ src: item.previewUrl }, context.figureId, context.sourceGeneratedId, item.name || "Image", id).catch(alertError);
          }
          return;
        }
        const figureImage = event.target.closest("[data-open-figure-image]");
        if (figureImage) {
          openFigureLightbox(
            figureImage,
            figureImage.dataset.lightboxFigureId || "",
            figureImage.dataset.sourceGeneratedId || "",
            figureImage.dataset.lightboxTitle || "",
          ).catch(alertError);
          return;
        }
        const deleteGeneratedFigureBtn = event.target.closest("[data-delete-generated-figure]");
        if (deleteGeneratedFigureBtn) {
          deleteGeneratedFigure(
            deleteGeneratedFigureBtn.dataset.deleteGeneratedFigure || "",
            deleteGeneratedFigureBtn.dataset.generatedId || "",
          ).catch(alertError);
          return;
        }
        if (event.target.closest("#toggleAnalysisPanelBtn")) {
          toggleReaderPanel("analysis");
          return;
        }
        if (event.target.closest("#togglePdfPanelBtn")) {
          toggleReaderPanel("pdf");
          return;
        }
        const deleteReviewBtn = event.target.closest("[data-delete-review]");
        if (deleteReviewBtn) {
          deleteReview(deleteReviewBtn.dataset.deleteReview || "").catch(alertError);
          return;
        }
        const deleteNoteBtn = event.target.closest("[data-delete-note]");
        if (deleteNoteBtn) {
          deleteNote(deleteNoteBtn.dataset.deleteNote || "").catch(alertError);
          return;
        }
      });
      $("workspaceMain").addEventListener("keydown", event => {
        if (event.isComposing || !((event.metaKey || event.ctrlKey) && event.key === "Enter")) return;
        const variantBox = event.target.closest("[data-generated-variant-request]");
        if (variantBox) {
          event.preventDefault();
          const generatedId = variantBox.dataset.generatedVariantRequest || "";
          const button = document.querySelector(`[data-source-generated-id="${CSS.escape(generatedId)}"][data-generate-figure-variant]`);
          generateFigureDraft(button?.dataset.generateFigureVariant || state.selectedFigureId || "", generatedId).catch(alertError);
          return;
        }
        const requestBox = event.target.closest("[data-figure-request]");
        if (!requestBox) return;
        event.preventDefault();
        generateFigureDraft(requestBox.dataset.figureRequest || "").catch(alertError);
      });
      $("workspaceMain").addEventListener("paste", event => {
        const input = event.target.closest("[data-figure-request], [data-generated-variant-request]");
        if (!input) return;
        const files = pastedImageFiles(event);
        if (!files.length) return;
        event.preventDefault();
        const context = figureContextFromInput(input);
        files.forEach(file => uploadFigureInputFile(file, context).catch(alertError));
      });
    }

    // The Library and CCF trees.
    function bindTreeEvents() {
      $("folderTree").addEventListener("click", event => {
        if (event.target.closest("[data-paper-tree-more]")) {
          state.paperTreeRenderLimit += 240;
          renderTree();
          return;
        }
        const ccfRankBtn = event.target.closest("[data-ccf-rank]");
        if (ccfRankBtn) {
          const rank = ccfRankBtn.dataset.ccfRank || "";
          if (!rank) return;
          state.ccfCollapsedRanks = { ...(state.ccfCollapsedRanks || {}) };
          if (state.ccfCollapsedRanks[rank]) {
            delete state.ccfCollapsedRanks[rank];
          } else {
            state.ccfCollapsedRanks[rank] = true;
          }
          writeUiState();
          renderTree();
          return;
        }
        const ccfCategoryBtn = event.target.closest("[data-ccf-category]");
        if (ccfCategoryBtn) {
          const key = ccfCategoryBtn.dataset.ccfCategory || "";
          if (!key) return;
          state.ccfCollapsedCategories = { ...(state.ccfCollapsedCategories || {}) };
          if (state.ccfCollapsedCategories[key]) {
            delete state.ccfCollapsedCategories[key];
          } else {
            state.ccfCollapsedCategories[key] = true;
          }
          writeUiState();
          renderTree();
          return;
        }
        const ccfFetchYearsBtn = event.target.closest("[data-ccf-fetch-years]");
        if (ccfFetchYearsBtn) {
          const venueKey = ccfFetchYearsBtn.dataset.ccfFetchYears || "";
          fetchCcfYearsForVenue(venueKey).catch(alertError);
          return;
        }
        const ccfVenueBtn = event.target.closest("[data-ccf-venue]");
        if (ccfVenueBtn) {
          const venueKey = ccfVenueBtn.dataset.ccfVenue || "";
          state.ccfExpandedVenue = state.ccfExpandedVenue === venueKey ? "" : venueKey;
          if (state.ccfExpandedVenue) {
            state.ccfSelectedVenue = venueKey;
            state.ccfSelectedPaper = null;
          }
          writeUiState();
          renderTree();
          if (state.ccfExpandedVenue) {
            loadCcfYears(venueKey).then(() => renderTree()).catch(alertError);
          }
          return;
        }
        const ccfYearBtn = event.target.closest("[data-ccf-year]");
        if (ccfYearBtn) {
          const venueKey = ccfYearBtn.dataset.ccfYearVenue || "";
          const year = ccfYearBtn.dataset.ccfYear || "";
          state.ccfSelectedVenue = venueKey;
          state.ccfExpandedVenue = venueKey;
          state.ccfSelectedYear = year;
          state.ccfSelectedPaper = null;
          state.ccfPaperQuery = "";
          state.mode = "library";
          writeUiState();
          syncModeTabs();
          renderTree();
          renderWorkspace();
          loadCcfPapers(venueKey, year)
            .then(() => {
              renderWorkspace();
              return loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true });
            })
            .catch(alertError);
          return;
        }
        const paperBtn = event.target.closest("[data-paper-id]");
        const paperTreeToggle = event.target.closest("[data-paper-tree-toggle]");
        if (paperTreeToggle) {
          const key = paperTreeToggle.dataset.paperTreeToggle || "";
          if (!key) return;
          state.paperTreeExpansion = {
            ...(state.paperTreeExpansion || {}),
            [key]: paperTreeToggle.getAttribute("aria-expanded") !== "true",
          };
          renderTree();
          return;
        }
        if (paperBtn) {
          rememberPreviousPaperWorkspace(currentPaperWorkspaceSnapshot({
            leftFace: "library",
            mode: "reader",
            selectedId: paperBtn.dataset.paperId || "",
          }));
          loadDetail(paperBtn.dataset.paperId).then(() => setMode("reader")).catch(alertError);
          return;
        }
      });
    }

    // The reader's selection menu and the previous-workspace shortcut.
    function bindSelectionEvents() {
      $("workspaceMain").addEventListener("mouseup", event => maybeShowSelectionMenu(event));
      $("workspaceMain").addEventListener("keyup", event => maybeShowSelectionMenu(event));
      $("selectionMenu").addEventListener("mousedown", event => event.preventDefault());
      $("selectionMenu").addEventListener("click", event => {
        const btn = event.target.closest("[data-selection-action]");
        if (!btn) return;
        runSelectionAction(btn.dataset.selectionAction || "").catch(alertError);
      });
      document.addEventListener("keydown", event => {
        if (!isPreviousPaperWorkspaceShortcut(event) || !canRestorePaperWorkspace(state.previousPaperWorkspace)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        goPreviousPaperWorkspace().catch(alertError);
      }, true);
    }

    // The Library search box and filters.
    function bindSearchEvents() {
      $("searchInput").addEventListener("input", event => {
        state.query = event.target.value;
        state.activeSemanticSearchId = "";
        state.semanticSearch?.clearActive?.({ notify: false });
        state.activeCollectorJobId = "";
        writeUiState();
        renderCollectorJobs();
        clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(() => loadLibrary({ keepDetail: false }).catch(alertError), 180);
      });
      [["yearFilter", "year"], ["venueFilter", "venue"], ["tagFilter", "tag"], ["projectFilter", "project"]].forEach(([id, key]) => {
        $(id).addEventListener("change", event => {
          state.filters[key] = event.target.value;
          writeUiState();
          loadLibrary({ keepDetail: false }).catch(alertError);
        });
      });
    }

    // Leaving, hiding and showing the page.
    function bindPageLifecycleEvents() {
      document.querySelector(".portalModuleNav")?.addEventListener("click", event => {
        const target = event.target instanceof Element ? event.target : null;
        const link = target?.closest("a[href]");
        if (!link || link.target === "_blank") return;
        state.leaving = true;
        suspendPaperUpdates();
      });
      window.addEventListener("pagehide", () => {
        state.leaving = true;
        suspendPaperUpdates();
      });
      window.addEventListener("beforeunload", () => {
        state.leaving = true;
        suspendPaperUpdates();
      });
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
          suspendPaperUpdates({ includeDeferred: false, includePdf: false });
        } else if (!state.leaving) {
          resumePaperUpdates();
        }
      });
    }

    return Object.freeze({
      bindEvents,
    });
  }

  window.PortalPaperEvents = Object.freeze({ createEvents, dependencies: DEPENDENCIES });
})();
