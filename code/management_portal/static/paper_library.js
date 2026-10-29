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
    "updateChatComposerState", "writeUiState",
  ]);

  function createLibrary(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperLibrary is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, ccfCollectionChatScope, ccfCollectionPapers,
      ccfPaperAuthors, ccfVenueYearLabel, currentCcfEntry, currentCcfVenue, currentPaperIdentityReady, esc,
      isSemanticSearchReady, loadChatHistory, loadDetail, paperApi, renderAssistantContext, renderChat,
      renderCollectorJobs, renderTree, renderWorkspace, state, updateChatComposerState, writeUiState,
    } = dependencies;

    function libraryQuery() {
      const params = new URLSearchParams({ compact: "1", preview_limit: "100" });
      if (state.query) params.set("q", state.query);
      if (activeSemanticSearchHasResults()) params.set("semantic", state.activeSemanticSearchId);
      if (!activeSemanticSearchHasResults() && activeCollectorJobHasResults()) params.set("collector", state.activeCollectorJobId);
      if (state.mode === "today") params.set("registered", "today");
      ["year", "venue", "tag", "project", "favorite"].forEach(key => {
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
            ? "Today"
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
      state.libraryLoading = true;
      let firstPayload;
      try {
        firstPayload = await paperApi.request(libraryQuery());
      } catch (error) {
        if (requestSeq === state.libraryRequestSeq) {
          state.libraryLoading = false;
          renderWorkspace();
        }
        throw error;
      }
      if (requestSeq !== state.libraryRequestSeq) return null;
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
            renderWorkspace();
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
      $("favoriteFilter").checked = state.filters.favorite === "1";
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
        startPayload: () => ({ project: String(state.filters.project || "") }),
        urls: {
          list: "/paper/api/semantic-searches",
          start: "/paper/api/semantic-search/start",
          refresh: "/paper/api/semantic-search/refresh",
          delete: "/paper/api/semantic-search/delete",
        },
        labels: { idle: "Semantic", starting: "Starting", results: "results", searching: "searching", error: "error" },
        isDisabled: () => state.leftFace === "ccf",
        confirmRemove: () => window.PortalUI.confirmAction("delete", "这个语义搜索条目"),
        onStart: () => {
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
        onActiveChange: searchId => {
          state.activeSemanticSearchId = String(searchId || "");
          if (state.activeSemanticSearchId) state.activeCollectorJobId = "";
          writeUiState();
          renderCollectorJobs();
          if (isSemanticSearchReady()) loadLibrary({ keepDetail: false }).catch(alertError);
        },
        onError: alertError,
      });
      return state.semanticSearch;
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
      const options = [window.PortalI18n.html`<option value="">${esc(label)}</option>`];
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
      loadLibrary, loadMoreLibrary,
    });
  }

  window.PortalPaperLibrary = Object.freeze({ createLibrary, dependencies: DEPENDENCIES });
})();
