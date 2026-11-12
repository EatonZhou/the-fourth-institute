(() => {
  "use strict";

  if (window.PortalPaperLibrary) return;

  // The Library's data: the query with its filters, loading and loading more,
  // semantic search, the collection a semantic search, Collector job or CCF
  // list selects (its Papers and Chat scope), and the Markdown export.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "activeChatWorkspaceId", "alertError", "ccfCollectionChatScope",
    "ccfCollectionPapers", "ccfPaperAuthors", "ccfVenueYearLabel", "currentCcfEntry", "currentCcfVenue",
    "currentPaperIdentityReady", "esc", "isSemanticSearchReady", "loadChatHistory", "loadDetail", "paperApi",
    "renderAssistantContext", "renderChat", "renderCollectorJobs", "renderTree", "renderWorkspace", "state",
    "syncFavoriteButtons", "updateChatComposerState", "writeUiState",
  ]);

  function createLibrary(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperLibrary is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, ccfCollectionChatScope, ccfCollectionPapers,
      ccfPaperAuthors, ccfVenueYearLabel, currentCcfEntry, currentCcfVenue, currentPaperIdentityReady, esc,
      isSemanticSearchReady, loadChatHistory, loadDetail, paperApi, renderAssistantContext, renderChat,
      renderCollectorJobs, renderTree, renderWorkspace, state, syncFavoriteButtons, updateChatComposerState, writeUiState,
    } = dependencies;

    function libraryQuery() {
      delete state.filters.favorite;
      const params = new URLSearchParams({ compact: "1", preview_limit: "100" });
      const route=window.PortalPaperNavigation?.current?.current;
      if (state.query) params.set("q", state.query);
      if (state.mode === "library" && state.activeSemanticSearchId
          && (activeSemanticSearchHasResults() || !isSemanticSearchReady() || route?.kind==='search'&&route.id===state.activeSemanticSearchId)) params.set("semantic", state.activeSemanticSearchId);
      if (state.mode === "library" && !params.has("semantic") && (activeCollectorJobHasResults() || route?.kind==='collector'&&route.id===state.activeCollectorJobId)) params.set("collector", state.activeCollectorJobId);
      if (state.mode === "today") params.set("registered", "today");
      ["year", "venue", "tag", "project"].forEach(key => {
        const value = state.filters[key];
        if (value) params.set(key, value);
      });
      const suffix = params.toString();
      return `/paper/api/library${suffix ? `?${suffix}` : ""}`;
    }

    function activeSemanticSearchHasResults() {
      const active = state.semanticSearch?.activeJob?.();
      return !!active && Number(active.result_count || 0) > 0;
    }

    function activeCollectorJobHasResults() {
      if (!state.activeCollectorJobId) return false;
      const active = (state.collectorJobs || []).find(job => job.job_id === state.activeCollectorJobId);
      return !!active && Number(active.result_count || active.registered_count || 0) > 0;
    }

    function isCollectionMode(mode = state.mode) {
      return mode === "library" || mode === "today";
    }

    function isCcfCollectionMode(mode = state.mode) {
      return state.leftFace === "ccf" && mode === "library";
    }

    function hashString(text) {
      let hash = 0;
      for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
      }
      return Math.abs(hash).toString(36);
    }

    function collectionChatScope() {
      if (isCcfCollectionMode()) return ccfCollectionChatScope();
      const summary = state.library?.summary || {};
      const semantic = summary.semantic_search || null;
      const collection = summary.paper_collection || null;
      const filters = {};
      Object.entries(state.filters || {}).forEach(([key, value]) => {
        const text = String(value || "").trim();
        if (text) filters[key] = text;
      });
      const payload = {
        mode: state.mode === "today" ? "today" : "library",
        query: String(state.query || "").trim(),
        filters,
        semantic_search_id: state.activeSemanticSearchId || "",
        collector_job_id: state.activeCollectorJobId || "",
      };
      const label = semantic
        ? window.PortalI18n.text`Semantic search: ${semantic.query || state.query || window.PortalI18n.t("current search")}`
        : collection
          ? window.PortalI18n.text`Paper collection: ${collection.direction || window.PortalI18n.t("current collection")}`
          : state.mode === "today"
            ? window.PortalI18n.t("Today")
            : window.PortalI18n.t("Paper Library");
      const summaryText = String(
        semantic?.query_interpretation
        || semantic?.notes
        || collection?.summary
        || collection?.error
        || (state.mode === "today" ? window.PortalI18n.t("Papers registered today.") : window.PortalI18n.t("Current paper library view."))
      ).trim();
      return { payload, label, summaryText, key: `paper-collection:${payload.mode}:${hashString(JSON.stringify(payload))}` };
    }

    function collectionPaperIds() {
      if (isCcfCollectionMode()) {
        return ccfCollectionPapers()
          .map(paper => String(paper.registered_paper_id || "").trim())
          .filter(Boolean)
          .slice(0, 120);
      }
      return (state.library?.papers || []).map(paper => String(paper.id || "").trim()).filter(Boolean).slice(0, 120);
    }

    function collectionItems() {
      if (!isCcfCollectionMode()) return [];
      return ccfCollectionPapers().slice(0, 120).map(paper => ({
        title: String(paper.title || "").trim(),
        authors: ccfPaperAuthors(paper),
        venue: ccfVenueYearLabel(currentCcfVenue(), currentCcfEntry() || state.ccfSelectedYear),
        year: String(currentCcfEntry()?.year || paper.year || "").trim(),
        doi: String(paper.doi || "").trim(),
        url: String(paper.url || paper.ee || paper.dblp_url || "").trim(),
        dblp_key: String(paper.key || paper.dblp_key || "").trim(),
        registered_paper_id: String(paper.registered_paper_id || "").trim(),
      }));
    }

    function collectionVisibleCount() {
      if (isCcfCollectionMode()) return ccfCollectionPapers().length;
      return state.library?.summary?.visible_count ?? collectionPaperIds().length;
    }

    async function loadLibrary({ keepDetail = true, hydrateDetail = true } = {}) {
      const requestSeq = ++state.libraryRequestSeq;
      const favoriteRevision = state.favoriteRevision;
      const detailBefore = state.detail;
      const modeBefore = state.mode;
      const faceBefore = state.leftFace;
      // Sidebar filters change the catalog, not the open document. Rebuilding
      // it discards PDF canvases, text layers and selection and renders them all
      // again. Only preserve a detail view that is still the same after the read.
      const preservesDetailView = () => keepDetail && detailBefore?.paper
        && faceBefore === "library" && state.leftFace === faceBefore && state.mode === modeBefore && !isCollectionMode()
        && state.detail === detailBefore && state.selectedId === detailBefore.paper.id;
      state.libraryLoading = true;
      const header = $("libraryMeta");
      const renderHeaderError = error => {
        if (state.leftFace !== "library" || !header) return;
        header.replaceChildren();
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'portalButton is-compact';
        retry.textContent = window.PortalI18n.t('刷新');
        retry.addEventListener('click', () => loadLibrary({keepDetail, hydrateDetail}).catch(alertError));
        header.append(`${window.PortalI18n.t('错误')}: ${error.message || error} `, retry);
      };
      if (header && state.leftFace === "library") header.textContent = window.PortalI18n.t('Loading...');
      let firstPayload;
      const initialQuery = libraryQuery();
      const requestedHistory = new URLSearchParams(initialQuery.split('?')[1] || '');
      try {
        firstPayload = await paperApi.request(initialQuery);
      } catch (error) {
        if (requestSeq === state.libraryRequestSeq) {
          state.libraryLoading = false;
          renderHeaderError(error);
          if (!preservesDetailView()) renderWorkspace();
        }
        throw error;
      }
      if (requestSeq !== state.libraryRequestSeq) return null;
      const missingSemantic = requestedHistory.get('semantic') && !firstPayload.summary?.semantic_search;
      const missingCollector = requestedHistory.get('collector') && !firstPayload.summary?.paper_collection;
      if (missingSemantic || missingCollector) {
        // A restored ID is not evidence that this request asked for its results.
        // Lose only the unavailable selection; never reject Reader startup.
        if (missingSemantic && state.activeSemanticSearchId === requestedHistory.get('semantic')) {
          state.activeSemanticSearchId = '';
          state.semanticSearch?.clearActive?.({ notify: false });
        }
        if (missingCollector && state.activeCollectorJobId === requestedHistory.get('collector')) {
          state.activeCollectorJobId = '';
          renderCollectorJobs();
        }
        writeUiState();
        window.PortalUI.toast(window.PortalI18n.t('该次历史结果已不可用，请刷新历史列表。'), 'warning');
        try {
          firstPayload = await paperApi.request(libraryQuery());
        } catch (error) {
          if (requestSeq === state.libraryRequestSeq) {
            state.libraryLoading = false;
            renderHeaderError(error);
          }
          throw error;
        }
        if (requestSeq !== state.libraryRequestSeq) return null;
      }
      state.library = firstPayload;
      state.libraryRenderLimit = 100;
      state.paperTreeRenderLimit = 240;
      state.libraryPageLoading = false;
      if (normalizePaperFiltersForLibrary()) {
        writeUiState();
        let normalizedPayload;
        try {
          normalizedPayload = await paperApi.request(libraryQuery());
        } catch (error) {
          if (requestSeq === state.libraryRequestSeq) {
            state.libraryLoading = false;
            renderHeaderError(error);
            if (!preservesDetailView()) renderWorkspace();
          }
          throw error;
        }
        if (requestSeq !== state.libraryRequestSeq) return null;
        state.library = normalizedPayload;
      }
      if (favoriteRevision === state.favoriteRevision && !state.favoritePending.size) {
        state.favoriteIds = new Set(state.library.favorite_ids || []);
      }
      renderFilters();
      renderTree();
      if (!isCollectionMode()) {
        if (!keepDetail || !state.selectedId) {
          const first = state.library.papers?.[0];
          if (first) state.selectedId = first.id;
        }
        if (state.selectedId) {
          const exists = (state.library.papers || []).some(p => p.id === state.selectedId);
          if (!exists && !keepDetail) state.selectedId = state.library.papers?.[0]?.id || "";
        }
        if (hydrateDetail && state.selectedId && !state.detail) {
          try {
            await loadDetail(state.selectedId, { render: false });
          } catch {
            state.selectedId = "";
            state.detail = null;
            writeUiState();
          }
        }
      }
      if (requestSeq !== state.libraryRequestSeq) return null;
      state.libraryLoading = false;
      if (preservesDetailView()) {
        syncFavoriteButtons();
        return state.library;
      }
      renderWorkspace();
      renderAssistantContext();
      await currentPaperIdentityReady();
      if (requestSeq !== state.libraryRequestSeq) return null;
      renderChat();
      updateChatComposerState();
      if (isCollectionMode()) {
        loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true }).catch(alertError);
      }
      return state.library;
    }

    async function loadMoreLibrary() {
      if (state.libraryPageLoading || !state.library?.papers) return;
      const offset = state.libraryRenderLimit;
      const expected = state.library.papers.slice(offset, offset + 100);
      if (!expected.length) return;
      const requestSeq = state.libraryRequestSeq;
      const params = new URLSearchParams(libraryQuery().split("?")[1] || "");
      params.delete("preview_limit");
      params.set("rows_only", "1");
      params.set("offset", String(offset));
      params.set("limit", String(expected.length));
      state.libraryPageLoading = true;
      const button = $("loadMoreLibraryBtn");
      if (button) button.disabled = true;
      try {
        const payload = await paperApi.request(`/paper/api/library?${params}`);
        if (requestSeq !== state.libraryRequestSeq) return;
        const rows = payload.papers || [];
        if (rows.length !== expected.length || rows.some((row, index) => row.id !== expected[index].id)) {
          await loadLibrary({ keepDetail: true, hydrateDetail: false });
          return;
        }
        state.library.papers.splice(offset, rows.length, ...rows);
        state.libraryRenderLimit = offset + rows.length;
        renderWorkspace();
      } finally {
        state.libraryPageLoading = false;
        const currentButton = $("loadMoreLibraryBtn");
        if (currentButton) currentButton.disabled = false;
      }
    }

    function renderFilters() {
      $("searchInput").value = state.query;
      const filters = state.library?.filters || {};
      fillSelect("yearFilter", "All years", filters.years || [], state.filters.year);
      fillSelect("venueFilter", "All venues", filters.venues || [], state.filters.venue);
      fillSelect("tagFilter", "All tags", [...(filters.tags || []), ...(filters.workload || []), ...(filters.platform || []), ...(filters.architecture || []), ...(filters.optimization || [])], state.filters.tag);
      fillSelect("projectFilter", "All projects", filters.projects || [], state.filters.project);
    }

    function normalizePaperFiltersForLibrary() {
      const filters = state.library?.filters || {};
      const allowed = {
        year: new Set((filters.years || []).map(String)),
        venue: new Set((filters.venues || []).map(String)),
        tag: new Set([...(filters.tags || []), ...(filters.workload || []), ...(filters.platform || []), ...(filters.architecture || []), ...(filters.optimization || [])].map(String)),
        project: new Set((filters.projects || []).map(String)),
      };
      let changed = false;
      for (const key of ["year", "venue", "tag", "project"]) {
        const value = String(state.filters?.[key] || "").trim();
        if (value && !allowed[key].has(value)) {
          state.filters[key] = "";
          changed = true;
        }
      }
      return changed;
    }

    function initSemanticSearch() {
      if (state.semanticSearch) return state.semanticSearch;
      const client = {
        get: path => paperApi.request(path, { cache: "no-store" }),
        post: (path, body) => paperApi.request(path, {
          method: "POST",
          body: JSON.stringify(body || {}),
        }),
      };
      state.semanticSearch = window.PortalSemanticSearch.create({
        input: "searchInput",
        button: "semanticSearchBtn",
        panel: "semanticSearchList",
        client,
        initialActiveId: state.activeSemanticSearchId,
        projectionScope: "paper",
        toggleActive: false,
        startPayload: () => ({ project: String(state.filters.project || "") }),
        urls: {
          list: "/paper/api/semantic-searches",
          start: "/paper/api/semantic-search/start",
          refresh: "/paper/api/semantic-search/refresh",
          delete: "/paper/api/semantic-search/delete",
        },
        labels: { idle: "Paper Semantic", starting: "Starting", results: "results", searching: "searching", error: "error" },
        isDisabled: () => state.leftFace === "ccf",
        confirmRemove: () => window.PortalUI.confirmAction("delete", "这个语义搜索条目"),
        onStart: () => {
          window.dispatchEvent(new CustomEvent('portal:paper-main-list-save'));
          clearTimeout(state.searchTimer);
          state.searchTimer = null;
          state.query = "";
          state.activeSemanticSearchId = "";
          state.activeCollectorJobId = "";
          $("searchInput").value = "";
          writeUiState();
          renderCollectorJobs();
          if (isSemanticSearchReady()) loadLibrary({ keepDetail: false }).catch(alertError);
        },
        onSelect: searchId => {
          window.dispatchEvent(new CustomEvent('portal:paper-main-list-save'));
          state.mode = 'library'; state.leftFace = 'library';
          state.query = ''; state.filters = {}; $('searchInput').value = '';
          window.dispatchEvent(new CustomEvent('portal:paper-history-open'));
          // Only a user selecting a saved search creates a history entry.
          // Background catalog notifications must never navigate off a paper.
          if (searchId) {
            if(window.PortalPaperNavigation?.current){window.PortalPaperNavigation.current.selectResult('search',searchId);return;}
            const address = new URL(window.location.href);
            if (address.searchParams.get('semantic') !== searchId || address.searchParams.has('paper')) {
              address.searchParams.delete('paper');
              address.searchParams.set('semantic', searchId);
              history.pushState(history.state || {}, '', address.href);
            }
          }
        },
        onActiveChange: searchId => {
          const route=window.PortalPaperNavigation?.current?.current;
          if(route&&String(searchId||'')!==(route.kind==='search'?route.id:''))return;
          state.activeSemanticSearchId = String(searchId || "");
          if (state.activeSemanticSearchId) state.activeCollectorJobId = "";
          writeUiState();
          renderCollectorJobs();
          if (isSemanticSearchReady()) loadLibrary({ keepDetail: false }).catch(alertError);
        },
        onError: alertError,
        onJobsChange: jobs => refreshSemanticLanguage(jobs),
      });
      return state.semanticSearch;
    }

    function refreshSemanticLanguage(jobs = state.semanticSearch?.jobs?.() || []) {
      const label = $("semanticSearchBtn")?.querySelector("[data-semantic-label]");
      if (label) label.textContent = window.PortalI18n.t(label.textContent === "Starting" ? "Starting" : "Paper Semantic");
      for (const job of jobs) {
        const row = Array.from($("semanticSearchList")?.querySelectorAll("[data-semantic-select]") || [])
          .find(element => element.dataset.semanticSelect === job.search_id);
        const status = row?.querySelector(".portalSemanticStatus");
        if (status) status.textContent = job.status === "done"
          ? window.PortalI18n.text`${Number(job.result_count || 0)} results`
          : window.PortalI18n.t(job.status === "running" ? "Searching" : job.status === "error" ? "Failed" : "Queued");
      }
    }

    function currentLibraryMarkdown() {
      return (state.library?.papers || [])
        .map(paper => `- ${String(paper.title || "").trim()}`)
        .filter(line => line !== "-")
        .join("\n");
    }

    async function exportLibraryMarkdown() {
      const text = currentLibraryMarkdown();
      if (!text) return;
      await window.PortalUI.copyText(text, "Paper list");
      const btn = $("exportLibraryMarkdownBtn");
      if (btn) {
        const oldText = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(() => {
          if ($("exportLibraryMarkdownBtn") === btn) btn.textContent = oldText;
        }, 1400);
      }
    }

    function fillSelect(id, label, values, selected) {
      const seen = new Set();
      const options = [window.PortalI18n.html`<option value="" data-portal-i18n-text="${esc(label)}">${esc(window.PortalI18n.t(label))}</option>`];
      values.forEach(value => {
        const text = String(value || "").trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        options.push(window.PortalI18n.html`<option value="${esc(text)}"${text === selected ? " selected" : ""}>${esc(text)}</option>`);
      });
      $(id).innerHTML = options.join("");
    }

    return Object.freeze({
      activeCollectorJobHasResults, collectionChatScope, collectionItems, collectionPaperIds,
      collectionVisibleCount, exportLibraryMarkdown, hashString, initSemanticSearch, isCollectionMode,
      loadLibrary, loadMoreLibrary, refreshSemanticLanguage,
    });
  }

  window.PortalPaperLibrary = Object.freeze({ createLibrary, dependencies: DEPENDENCIES });
})();
