(() => {
  "use strict";

  if (window.PortalPaperCcfCatalog) return;

  // The CCF catalog's data: venues, years and Papers, their labels and
  // ranks, the year and Paper filters, and the enrichment of a CCF Paper.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "activeChatWorkspaceId", "alertError", "hashString", "loadChatHistory",
    "paperApi", "renderCcfPaperRow", "renderTree", "renderWorkspace", "state",
  ]);

  function createCcfCatalog(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperCcfCatalog is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, hashString, loadChatHistory, paperApi,
      renderCcfPaperRow, renderTree, renderWorkspace, state,
    } = dependencies;

    function ccfYearKey(venue, year) {
      return `${String(venue || "")}:${String(year || "")}`;
    }

    function currentCcfPayload() {
      return state.ccfPapersByYear[ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear)] || null;
    }

    function currentCcfVenue() {
      const venues = state.ccfVenues || [];
      return venues.find(venue => String(venue.dblp_conf_key || "") === String(state.ccfSelectedVenue || ""))
        || currentCcfPayload()?.venue
        || null;
    }

    function currentCcfEntry() {
      const entries = state.ccfYearsByVenue[state.ccfSelectedVenue] || [];
      return entries.find(entry => ccfVenueEntryId(entry) === String(state.ccfSelectedYear || ""))
        || currentCcfPayload()
        || null;
    }

    function orderCcfPapers(papers) {
      if (!Array.isArray(papers)) return [];
      return papers
        .map((paper, index) => ({ paper, index }))
        .sort((a, b) => {
          const aRegistered = !!(a.paper?.registered_paper_id || a.paper?.registered);
          const bRegistered = !!(b.paper?.registered_paper_id || b.paper?.registered);
          if (aRegistered !== bRegistered) return aRegistered ? -1 : 1;
          return a.index - b.index;
        })
        .map(item => item.paper);
    }

    function ccfCollectionPapers() {
      const payload = currentCcfPayload();
      return orderCcfPapers(payload?.papers);
    }

    function ccfVenueShortName(venue) {
      return String(venue?.short_name || venue?.abbr || venue?.dblp_conf_key || "").trim();
    }

    function ccfVenueFullName(venue) {
      return String(venue?.name || "").trim();
    }

    function ccfVenueLabel(venue) {
      const shortName = ccfVenueShortName(venue);
      const fullName = ccfVenueFullName(venue);
      if (shortName && fullName && shortName !== fullName) return `${shortName} (${fullName})`;
      return shortName || fullName || String(venue?.dblp_conf_key || "Venue");
    }

    function ccfRankLabel(venueOrRank) {
      const rank = typeof venueOrRank === "string"
        ? venueOrRank
        : String(venueOrRank?.rank || "");
      return rank === "Curated" ? "Curated" : window.PortalI18n.text`CCF-${rank || ""}`;
    }

    function ccfVenueYearLabel(venue, year) {
      const shortName = ccfVenueShortName(venue);
      const fullName = ccfVenueFullName(venue);
      if (year && typeof year === "object") {
        const issue = String(year.issue || "").trim();
        const yearText = String(year.year || "").trim();
        const value = venue?.type === "期刊" && issue
          ? [yearText, issue].filter(Boolean).join("/")
          : yearText || String(year.label || year.entry_id || "").trim();
        const prefix = [shortName, value].filter(Boolean).join(" ");
        if (prefix && fullName) return `${prefix} (${fullName})`;
        return prefix || fullName || String(year.label || year.entry_id || "");
      }
      const prefix = [shortName, year].filter(Boolean).join(" ");
      if (prefix && fullName) return `${prefix} (${fullName})`;
      return prefix || fullName || String(year || "");
    }

    function ccfVenueEntryId(entry) {
      return String(entry?.entry_id || entry?.year || entry?.issue || "").trim();
    }

    function ccfCollectionChatScope() {
      const venue = currentCcfVenue();
      const entry = currentCcfEntry();
      const papers = ccfCollectionPapers();
      const label = venue
        ? `${ccfRankLabel(venue)}: ${ccfVenueYearLabel(venue, entry || state.ccfSelectedYear)}`
        : window.PortalI18n.t("CCF A/B Proceedings");
      const summaryParts = [];
      if (venue?.category) summaryParts.push(String(venue.category));
      if (venue?.type) summaryParts.push(String(venue.type));
      if (papers.length) summaryParts.push(`${papers.length} DBLP papers in this local CCF collection`);
      const payload = {
        mode: "ccf",
        ccf_venue: String(state.ccfSelectedVenue || ""),
        ccf_year: String(state.ccfSelectedYear || ""),
        ccf_rank: String(venue?.rank || ""),
        ccf_category: String(venue?.category || ""),
        ccf_label: label,
        paper_count: papers.length,
      };
      return {
        payload,
        label,
        summaryText: summaryParts.join(" · ") || "Current CCF A/B proceedings collection.",
        key: `paper-collection:ccf:${hashString(JSON.stringify(payload))}`,
      };
    }

    function ccfCategoryKey(rank, category) {
      return `${String(rank || "").trim()}::${String(category || "Other").trim() || "Other"}`;
    }

    function ccfEntryMatchesYearFilter(entry, filterText = state.ccfYearFilter) {
      const filter = String(filterText || "").trim().toLowerCase();
      if (!filter) return true;
      const haystack = [
        entry?.year,
        entry?.issue,
        entry?.entry_id,
        entry?.label,
        entry?.file,
      ].map(value => String(value || "").toLowerCase()).join(" ");
      return haystack.includes(filter);
    }

    function ccfVenueMatchesFilter(venue, filterText = state.ccfYearFilter) {
      const filter = String(filterText || "").trim().toLowerCase();
      if (!filter) return true;
      const haystack = [
        venue?.abbr,
        venue?.short_name,
        venue?.name,
        venue?.rank,
        venue?.type,
        venue?.category,
        venue?.dblp_conf_key,
        venue?.dblp_section,
        venue?.dblp_url,
        ccfVenueLabel(venue),
      ].map(value => String(value || "").toLowerCase()).join(" ");
      return haystack.includes(filter);
    }

    async function ensureCcfYearsForFilter() {
      const filter = String(state.ccfYearFilter || "").trim();
      const venues = state.ccfVenues || [];
      if (!filter || !venues.length) return;
      const venueMatches = venues.filter(venue => ccfVenueMatchesFilter(venue, filter));
      const shouldSearchYearEntries = /\d/.test(filter) || !venueMatches.length;
      const targets = shouldSearchYearEntries ? venues : venueMatches;
      await Promise.all(targets.map(venue => {
        const key = String(venue.dblp_conf_key || "");
        if (!key || Object.prototype.hasOwnProperty.call(state.ccfYearsByVenue, key)) return Promise.resolve();
        return loadCcfYears(key).catch(() => []);
      }));
    }

    function scheduleCcfYearFilterRender() {
      if (state.ccfFilterTimer) clearTimeout(state.ccfFilterTimer);
      state.ccfFilterTimer = setTimeout(() => {
        state.ccfFilterTimer = null;
        ensureCcfYearsForFilter().then(() => {
          if (state.leftFace === "ccf") renderTree();
        }).catch(alertError);
      }, 180);
    }

    function ccfPaperAuthors(paper) {
      const authors = paper?.authors || [];
      if (Array.isArray(authors)) return authors.map(author => String(author || "").trim()).filter(Boolean).slice(0, 8).join(", ");
      return String(authors || "").trim();
    }

    function ccfPaperMatchesQuery(paper, queryText = state.ccfPaperQuery) {
      const query = String(queryText || "").trim().toLowerCase();
      if (!query) return true;
      const haystack = [
        paper?.title,
        ccfPaperAuthors(paper),
        paper?.doi,
        paper?.dblp_key,
        paper?.dblp_url,
        paper?.ee,
        paper?.venue,
        paper?.booktitle,
        paper?.year,
        paper?.registered_paper_id,
      ].map(value => String(value || "").toLowerCase()).join(" ");
      return haystack.includes(query);
    }

    function applyCcfPaperListFilter() {
      const papers = ccfCollectionPapers();
      const query = String(state.ccfPaperQuery || "").trim();
      const key = ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear);
      const limit = Number(state.ccfRenderLimits[key] || 100);
      const matches = papers
        .map((paper, index) => ({ paper, index }))
        .filter(item => ccfPaperMatchesQuery(item.paper, query));
      const visible = matches.slice(0, limit);
      const results = $("ccfPaperListResults");
      if (results) {
        results.innerHTML = visible.length
          ? visible.map(item => renderCcfPaperRow(item.paper, item.index)).join("")
          : "";
      }
      const meta = $("ccfPaperSearchMeta");
      if (meta) meta.textContent = `${visible.length}/${matches.length} matches · ${papers.length} papers`;
      const empty = $("ccfPaperSearchEmpty");
      if (empty) empty.classList.toggle("hidden", !papers.length || visible.length > 0);
      const more = $("ccfPaperLoadMore");
      if (more) {
        more.classList.toggle("hidden", visible.length >= matches.length);
        more.textContent = window.PortalI18n.text`Show more · ${visible.length}/${matches.length}`;
      }
    }

    function ccfPaperEnrichmentKey(paper) {
      return String(paper?.dblp_key || paper?.doi || paper?.dblp_url || paper?.ee || paper?.title || "").trim();
    }

    function ccfPaperEnrichment(paper) {
      const key = ccfPaperEnrichmentKey(paper);
      return (key && state.ccfEnrichmentByKey[key]) || paper?.enrichment || {};
    }

    function storeCcfPaperEnrichment(paper, enrichment) {
      const key = ccfPaperEnrichmentKey(paper);
      if (!key || !enrichment) return;
      const previous = (state.ccfEnrichmentByKey || {})[key] || paper?.enrichment || {};
      const incoming = enrichment && typeof enrichment === "object" ? enrichment : {};
      const webStatus = String(incoming.web_status || "");
      const preserveRequestedWeb = previous?.requested_web && !["done", "error"].includes(webStatus);
      const next = preserveRequestedWeb
        ? { ...incoming, status: "web_pending", web_status: "pending", requested_web: true }
        : incoming;
      state.ccfEnrichmentByKey = { ...(state.ccfEnrichmentByKey || {}), [key]: next };
      if (paper) paper.enrichment = next;
    }

    async function ensureCcfPaperEnrichment(paper, { refresh = false, web = false } = {}) {
      if (!paper || paper.registered_paper_id) return;
      const key = ccfPaperEnrichmentKey(paper);
      if (!key) return;
      const flightKey = web ? `${key}::web` : key;
      if (state.ccfEnrichmentInFlight[flightKey]) return;
      if (!web && state.ccfEnrichmentInFlight[key]) return;
      const existing = ccfPaperEnrichment(paper);
      const existingStatus = String(existing?.status || "");
      if (!web && !refresh && ["pending", "running"].includes(existingStatus)) return;
      if (!web && !refresh && ["done", "error", "web_pending"].includes(existingStatus)) return;
      if (web && !refresh && String(existing?.web_status || "") === "done") return;
      if (web && !refresh) {
        storeCcfPaperEnrichment(paper, {
          ...(existing && typeof existing === "object" ? existing : {}),
          status: "web_pending",
          web_status: "pending",
          requested_web: true,
        });
        renderWorkspace();
      }
      state.ccfEnrichmentInFlight[flightKey] = true;
      try {
        const enrichment = await paperApi.request("/paper/api/ccf/enrich", {
          method: "POST",
          body: JSON.stringify({ paper, web }),
        });
        storeCcfPaperEnrichment(paper, enrichment);
        const status = String(enrichment?.status || "");
        const nextWebStatus = String(enrichment?.web_status || "");
        const shouldRender = !["pending", "running"].includes(status) && !(status === "web_pending" && nextWebStatus === "pending");
        if (shouldRender && state.ccfSelectedPaper && ccfPaperEnrichmentKey(state.ccfSelectedPaper) === key) renderWorkspace();
      } finally {
        delete state.ccfEnrichmentInFlight[flightKey];
      }
    }

    async function loadCcfVenues({ refresh = false } = {}) {
      if (state.ccfVenues && !refresh) return state.ccfVenues;
      const payload = await paperApi.request(`/paper/api/ccf/venues${refresh ? "?refresh=1" : ""}`);
      state.ccfVenues = Array.isArray(payload.venues) ? payload.venues : [];
      return state.ccfVenues;
    }

    async function loadCcfYears(venueKey, { refresh = false } = {}) {
      const key = String(venueKey || "").trim();
      if (!key) return [];
      if (state.ccfYearsByVenue[key] && !refresh) return state.ccfYearsByVenue[key];
      const suffix = refresh ? "&refresh=1" : "";
      const payload = await paperApi.request(`/paper/api/ccf/years?venue=${encodeURIComponent(key)}${suffix}`);
      state.ccfYearsByVenue[key] = Array.isArray(payload.years) ? payload.years : [];
      return state.ccfYearsByVenue[key];
    }

    async function fetchCcfYearsForVenue(venueKey) {
      const key = String(venueKey || "").trim();
      if (!key) return;
      state.ccfYearsFetchInFlight = key;
      delete state.ccfYearsErrors[key];
      renderTree();
      try {
        const years = await loadCcfYears(key, { refresh: true });
        if (!years.length) throw new Error("DBLP 未返回可识别的年份或期次目录，无法继续选择论文。请稍后重试。");
        renderTree();
      } catch (error) {
        state.ccfYearsErrors[key] = error.message || "目录获取失败，请稍后重试。";
        throw error;
      } finally {
        if (state.ccfYearsFetchInFlight === key) state.ccfYearsFetchInFlight = "";
        renderTree();
      }
    }

    async function loadCcfPapers(venueKey, year, { refresh = false } = {}) {
      const key = ccfYearKey(venueKey, year);
      if (state.ccfPapersByYear[key] && !refresh) return state.ccfPapersByYear[key];
      const suffix = refresh ? "&refresh=1" : "";
      const payload = await paperApi.request(`/paper/api/ccf/papers?venue=${encodeURIComponent(venueKey)}&year=${encodeURIComponent(year)}${suffix}`);
      state.ccfPapersByYear[key] = payload || {};
      if (Array.isArray(state.ccfPapersByYear[key].papers)) {
        state.ccfPapersByYear[key].papers = orderCcfPapers(state.ccfPapersByYear[key].papers);
      }
      (state.ccfPapersByYear[key].papers || []).forEach(paper => {
        if (paper?.enrichment?.status) storeCcfPaperEnrichment(paper, paper.enrichment);
      });
      return state.ccfPapersByYear[key];
    }

    async function fetchSelectedCcfPapers() {
      if (!state.ccfSelectedVenue || !state.ccfSelectedYear) return;
      const key = ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear);
      state.ccfPaperFetchInFlight = key;
      renderWorkspace();
      try {
        await loadCcfPapers(state.ccfSelectedVenue, state.ccfSelectedYear, { refresh: true });
        renderWorkspace();
        await loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true });
      } finally {
        if (state.ccfPaperFetchInFlight === key) state.ccfPaperFetchInFlight = "";
        renderWorkspace();
      }
    }

    return Object.freeze({
      applyCcfPaperListFilter, ccfCategoryKey, ccfCollectionChatScope, ccfCollectionPapers,
      ccfEntryMatchesYearFilter, ccfPaperAuthors, ccfPaperEnrichment, ccfPaperEnrichmentKey, ccfRankLabel,
      ccfVenueEntryId, ccfVenueLabel, ccfVenueMatchesFilter, ccfVenueShortName, ccfVenueYearLabel, ccfYearKey,
      currentCcfEntry, currentCcfPayload, currentCcfVenue, ensureCcfPaperEnrichment, ensureCcfYearsForFilter,
      fetchCcfYearsForVenue, fetchSelectedCcfPapers, loadCcfPapers, loadCcfVenues, loadCcfYears,
      scheduleCcfYearFilterRender,
    });
  }

  window.PortalPaperCcfCatalog = Object.freeze({ createCcfCatalog, dependencies: DEPENDENCIES });
})();
