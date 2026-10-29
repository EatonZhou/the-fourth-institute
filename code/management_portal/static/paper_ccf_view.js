(() => {
  "use strict";

  if (window.PortalPaperCcfView) return;

  // The Library and CCF faces of the left pane, the CCF tree, the CCF
  // workspace with its Paper list and preview, and registering a CCF Paper.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "activeChatWorkspaceId", "alertError", "applyCcfPaperListFilter",
    "ccfCategoryKey", "ccfCollectionPapers", "ccfEntryMatchesYearFilter", "ccfPaperAuthors",
    "ccfPaperEnrichment", "ccfPaperEnrichmentKey", "ccfRankLabel", "ccfVenueEntryId", "ccfVenueLabel",
    "ccfVenueMatchesFilter", "ccfVenueShortName", "ccfVenueYearLabel", "ccfYearKey", "chip",
    "currentCcfEntry", "currentCcfPayload", "currentCcfVenue", "currentPaperWorkspaceSnapshot",
    "disclosureIcon", "ensureCcfPaperEnrichment", "ensureCcfYearsForFilter", "esc", "loadCcfPapers",
    "loadCcfVenues", "loadCcfYears", "loadChatHistory", "loadDetail", "loadLibrary", "paperApi",
    "paperStateHtml", "rememberPreviousPaperWorkspace", "renderTree", "renderWorkspace", "state",
    "syncModeTabs", "writeUiState",
  ]);

  function createCcfView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperCcfView is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, activeChatWorkspaceId, alertError, applyCcfPaperListFilter, ccfCategoryKey,
      ccfCollectionPapers, ccfEntryMatchesYearFilter, ccfPaperAuthors, ccfPaperEnrichment,
      ccfPaperEnrichmentKey, ccfRankLabel, ccfVenueEntryId, ccfVenueLabel, ccfVenueMatchesFilter,
      ccfVenueShortName, ccfVenueYearLabel, ccfYearKey, chip, currentCcfEntry, currentCcfPayload,
      currentCcfVenue, currentPaperWorkspaceSnapshot, disclosureIcon, ensureCcfPaperEnrichment,
      ensureCcfYearsForFilter, esc, loadCcfPapers, loadCcfVenues, loadCcfYears, loadChatHistory, loadDetail,
      loadLibrary, paperApi, paperStateHtml, rememberPreviousPaperWorkspace, renderTree, renderWorkspace,
      state, syncModeTabs, writeUiState,
    } = dependencies;

    function updateLeftFaceTabs() {
      document.querySelectorAll("#leftFaceTabs button[data-left-face]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.leftFace === state.leftFace);
      });
      const isCcf = state.leftFace === "ccf";
      const paneTitle = document.querySelector(".leftPane .paneTitle");
      if (paneTitle) paneTitle.textContent = isCcf ? window.PortalI18n.t("CCF A/B + Curated") : window.PortalI18n.t("Paper Library");
      const libraryMeta = $("libraryMeta");
      if (libraryMeta && isCcf) libraryMeta.textContent = window.PortalI18n.t("CCF v7 + curated venues from DBLP");
      $("ccfFilterBar")?.classList.toggle("hidden", !isCcf);
      const ccfYearInput = $("ccfYearFilterInput");
      if (ccfYearInput && ccfYearInput.value !== state.ccfYearFilter) ccfYearInput.value = state.ccfYearFilter;
      document.querySelector(".searchWrap")?.classList.toggle("hidden", isCcf);
      document.querySelector(".filterGrid")?.classList.toggle("hidden", isCcf);
      $("semanticSearchBtn")?.toggleAttribute("disabled", isCcf);
      $("collectorBtn")?.toggleAttribute("disabled", isCcf);
    }

    async function showCcfFace({ remember = true } = {}) {
      if (remember) rememberPreviousPaperWorkspace(currentPaperWorkspaceSnapshot({ leftFace: "ccf", mode: "library" }));
      state.leftFace = "ccf";
      state.mode = "library";
      state.activeSemanticSearchId = "";
      state.semanticSearch?.clearActive?.({ notify: false });
      state.activeCollectorJobId = "";
      writeUiState();
      updateLeftFaceTabs();
      renderTree();
      renderWorkspace();
      try {
        await loadCcfVenues();
        await ensureCcfYearsForFilter();
        renderTree();
        renderWorkspace();
      } catch (error) {
        alertError(error);
      }
    }

    function showLibraryFace({ remember = true } = {}) {
      if (remember) rememberPreviousPaperWorkspace(currentPaperWorkspaceSnapshot({ leftFace: "library" }));
      state.leftFace = "library";
      writeUiState();
      updateLeftFaceTabs();
      renderTree();
      renderWorkspace();
    }

    async function refreshCurrentLeftFace() {
      if (state.leftFace !== "ccf") {
        await loadLibrary({ keepDetail: true });
        return;
      }
      await loadCcfVenues({ refresh: true });
      if (state.ccfExpandedVenue) await loadCcfYears(state.ccfExpandedVenue, { refresh: true });
      if (state.ccfSelectedVenue && state.ccfSelectedYear) {
        await loadCcfPapers(state.ccfSelectedVenue, state.ccfSelectedYear, { refresh: true });
      }
      renderTree();
      renderWorkspace();
    }

    function renderCcfTree() {
      updateLeftFaceTabs();
      const venues = state.ccfVenues || [];
      if (!venues.length) {
        if (state.ccfVenues === null) {
          $("folderTree").innerHTML = paperStateHtml("Reading local CCF A/B venue catalog…", {
            state: "loading",
            density: "inline",
          });
          loadCcfVenues().then(() => renderTree()).catch(alertError);
        } else {
          $("folderTree").innerHTML = paperStateHtml(
            "No local CCF cache yet. Use refresh to build it.",
            { density: "inline" },
          );
        }
        return;
      }
      const yearFilter = String(state.ccfYearFilter || "").trim();
      const preferredRanks = ["A", "B", "Curated"];
      const seenRanks = new Set(venues.map(venue => String(venue.rank || "")).filter(Boolean));
      const ranks = [
        ...preferredRanks.filter(rank => seenRanks.has(rank)),
        ...Array.from(seenRanks).filter(rank => !preferredRanks.includes(rank)).sort(),
      ];
      const rankHtml = ranks.map(rank => {
        const rankVenues = venues.filter(venue => String(venue.rank || "") === rank);
        const categoryMap = new Map();
        rankVenues.forEach(venue => {
          const category = String(venue.category || "Other").trim() || "Other";
          if (!categoryMap.has(category)) categoryMap.set(category, []);
          categoryMap.get(category).push(venue);
        });
        const categories = Array.from(categoryMap.entries()).sort(([a], [b]) => a.localeCompare(b, "zh-Hans-CN"));
        let rankVisibleCount = 0;
        const categoryHtml = categories.map(([category, categoryVenues]) => {
          let categoryVisibleCount = 0;
          const venueHtml = categoryVenues.map(venue => {
            const key = String(venue.dblp_conf_key || "");
            const expanded = key && key === state.ccfExpandedVenue;
            const years = state.ccfYearsByVenue[key] || [];
            const venueMatches = yearFilter ? ccfVenueMatchesFilter(venue, yearFilter) : false;
            const displayYears = yearFilter
              ? (venueMatches ? years : years.filter(year => ccfEntryMatchesYearFilter(year, yearFilter)))
              : years;
            const yearsLoaded = Object.prototype.hasOwnProperty.call(state.ccfYearsByVenue, key);
            if (yearFilter && !venueMatches && yearsLoaded && !displayYears.length) return "";
            if (yearFilter && !venueMatches && !yearsLoaded) return "";
            const showYears = expanded || !!yearFilter;
            const fetchingYears = state.ccfYearsFetchInFlight === key;
            categoryVisibleCount += 1;
            rankVisibleCount += 1;
            return window.PortalI18n.html`
                <div class="folderNode">
                  <button class="folderLine ${expanded ? "selected" : ""}" type="button" data-ccf-venue="${esc(key)}">
                    ${disclosureIcon(showYears)}
                    <strong style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(ccfVenueLabel(venue))}</strong>
                    <span class="folderCount">${esc(venue.type || venue.rank || "")}</span>
                  </button>
                  ${showYears ? window.PortalI18n.html`
                    <div style="padding-left: 14px;">
                      ${displayYears.length ? displayYears.map(year => window.PortalI18n.html`
                        <button class="folderLine ${key === state.ccfSelectedVenue && ccfVenueEntryId(year) === String(state.ccfSelectedYear || "") ? "selected" : ""}" type="button" data-ccf-year="${esc(ccfVenueEntryId(year))}" data-ccf-year-venue="${esc(key)}">
                          <span>•</span>
                          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(ccfVenueYearLabel(venue, year))}</span>
                          <span class="folderCount">${esc(year.count || "")}</span>
                        </button>
                      `).join("") : paperStateHtml(
                      state.ccfYearsErrors[key] || (yearsLoaded ? window.PortalI18n.t("No local year or issue cache yet.") : window.PortalI18n.t("Reading local cache…")),
                      {
                        state: state.ccfYearsErrors[key] ? "error" : (yearsLoaded ? "empty" : "loading"),
                        density: "inline",
                        className: "ccfYearsState",
                        actionsHtml: yearsLoaded
                          ? window.PortalI18n.html`<button class="portalButton is-compact is-primary ccfFetchYearsButton" type="button" data-ccf-fetch-years="${esc(key)}" ${fetchingYears ? "disabled" : ""}>${fetchingYears ? "Fetching..." : window.PortalI18n.t("Fetch year / issue list")}</button>`
                          : "",
                      },
                    )}
                    </div>
                  ` : ""}
                </div>
              `;
          }).join("");
          if (!venueHtml.trim()) return "";
          const categoryKey = ccfCategoryKey(rank, category);
          const collapsed = !!state.ccfCollapsedCategories[categoryKey];
          return window.PortalI18n.html`
              <button class="ccfCategoryHeader" type="button" data-ccf-category="${esc(categoryKey)}" aria-expanded="${collapsed ? "false" : "true"}">
                ${disclosureIcon(!collapsed)}
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(category)}</span>
                <span class="folderCount">${categoryVisibleCount}</span>
              </button>
              ${collapsed ? "" : venueHtml}
            `;
        }).join("");
        if (!categoryHtml.trim() && yearFilter) return "";
        const rankCollapsed = !!state.ccfCollapsedRanks[rank];
        const rankCount = yearFilter ? rankVisibleCount : rankVenues.length;
        return window.PortalI18n.html`
            <button class="ccfRankHeader" type="button" data-ccf-rank="${esc(rank)}" aria-expanded="${rankCollapsed ? "false" : "true"}">
              ${disclosureIcon(!rankCollapsed)}
              <span>${esc(ccfRankLabel(rank))}</span>
              <span class="folderCount">${rankCount}</span>
            </button>
            ${rankCollapsed ? "" : categoryHtml}
          `;
      }).join("");
      $("folderTree").innerHTML = rankHtml.trim()
        ? rankHtml
        : paperStateHtml("No local CCF entries match this search.", { density: "inline" });
    }

    function ccfWorkspaceMeta() {
      const venue = currentCcfVenue();
      const payload = currentCcfPayload();
      const entry = currentCcfEntry();
      const chips = [chip("CCF v7", "accent")];
      if (venue) {
        chips.push(chip(ccfRankLabel(venue), "good"));
        chips.push(chip(ccfVenueShortName(venue) || state.ccfSelectedVenue || "Venue"));
        if (venue.type) chips.push(chip(venue.type));
        if (venue.category) chips.push(chip(venue.category));
      }
      if (entry?.year) chips.push(chip(entry.year));
      if (entry?.issue) chips.push(chip(`Vol. ${entry.issue}`));
      if (!entry && state.ccfSelectedYear) chips.push(chip(state.ccfSelectedYear));
      if (payload?.papers) chips.push(chip(`${payload.papers.length} DBLP papers`));
      return chips.join("");
    }

    function renderCcfPaperRow(paper, index) {
      const authors = ccfPaperAuthors(paper);
      const registered = paper.registered_paper_id ? chip("Registered", "good") : chip("Not registered", "warn");
      const doi = paper.doi ? chip(`DOI ${paper.doi}`) : "";
      return window.PortalI18n.html`
          <article class="paperRow ${state.ccfSelectedPaper === paper ? "selected" : ""}" data-ccf-paper-index="${index}">
            <div class="paperRowHead">
              <div class="paperRowTitle">${esc(paper.title || window.PortalI18n.t("Untitled DBLP paper"))}</div>
              ${paper.registered_paper_id ? window.PortalI18n.html`<span class="relevanceBadge">Open</span>` : window.PortalI18n.html`<span class="relevanceBadge">Regist</span>`}
            </div>
            <div class="workspaceMeta">
              ${registered}
              ${paper.type ? chip(paper.type) : ""}
              ${doi}
            </div>
            ${authors ? window.PortalI18n.html`<div class="paneNote">${esc(authors)}</div>` : ""}
          </article>
        `;
    }

    function renderCcfPaperPreview(paper) {
      if (!paper) return "";
      const venue = currentCcfVenue();
      const authors = ccfPaperAuthors(paper);
      const registeredId = paper.registered_paper_id || "";
      const enrichment = ccfPaperEnrichment(paper);
      const enrichmentStatus = String(enrichment?.status || "");
      const abstract = String(enrichment?.abstract || "").trim();
      const summaryZh = String(enrichment?.summary_zh || "").trim();
      const webStatus = String(enrichment?.web_status || "");
      const webSummaryZh = String(enrichment?.web_summary_zh || "").trim();
      const webFindings = Array.isArray(enrichment?.web_findings) ? enrichment.web_findings : [];
      const sources = [...new Map([
        ...(Array.isArray(enrichment?.sources) ? enrichment.sources : []),
        ...(Array.isArray(enrichment?.web_sources) ? enrichment.web_sources : []),
      ].filter(source => source && typeof source === "object")
        .map(source => [String(source.url || source.title || source.source || ""), source])).values()];
      const webRunning = webStatus === "pending" || enrichmentStatus === "web_pending";
      const webButtonHtml = !registeredId && webStatus !== "done"
        ? window.PortalI18n.html`<button class="portalButton is-compact is-quiet" type="button" data-ccf-web-enrich ${webRunning ? "disabled" : ""}>${webRunning ? "Investigating..." : (webStatus === "error" ? window.PortalI18n.t("Retry web investigation") : window.PortalI18n.t("Web investigation"))}</button>`
        : "";
      const webRunningNote = webRunning
        ? window.PortalI18n.html`<div class="paneNote">Web investigation is running in the background. Fast metadata may appear first.</div>`
        : "";
      const sourcesHtml = sources.length ? window.PortalI18n.html`
          <section class="markdownBody">
            <h4>Sources</h4>
            <ul>
              ${sources.map(source => {
              const title = String(source?.title || source?.url || "source").trim();
              const candidate = String(source?.url || "").trim();
              const url = /^https?:\/\//i.test(candidate) ? candidate : "";
              const note = String(source?.note || source?.type || source?.source || "").trim();
              return window.PortalI18n.html`<li>${url ? window.PortalI18n.html`<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(title)}</a>` : esc(title)}${note ? ` · ${esc(note)}` : ""}</li>`;
            }).join("")}
            </ul>
          </section>
        ` : "";
      const enrichmentHtml = registeredId ? "" : (
        ["done", "web_pending"].includes(enrichmentStatus)
          ? `
              ${webSummaryZh ? window.PortalI18n.html`<section class="markdownBody"><h4>网页综合调查</h4><p>${esc(webSummaryZh)}</p>${webFindings.length ? window.PortalI18n.html`<ul>${webFindings.map(item => window.PortalI18n.html`<li>${esc(item)}</li>`).join("")}</ul>` : ""}</section>` : ""}
              ${webRunningNote}
              ${webStatus === "error" ? window.PortalI18n.html`<div class="paneNote">Web investigation failed: ${esc(enrichment.web_error || window.PortalI18n.t("unknown error"))}</div>` : ""}
              ${summaryZh ? window.PortalI18n.html`<section class="markdownBody"><h4>元数据概览</h4><p>${esc(summaryZh)}</p></section>` : ""}
              ${abstract ? window.PortalI18n.html`<section class="markdownBody"><h4>Abstract</h4><p>${esc(abstract)}</p></section>` : (webRunning ? window.PortalI18n.html`<div class="paneNote">Metadata overview is still being prepared.</div>` : window.PortalI18n.html`<div class="paneNote">未从公开元数据中找到摘要。</div>`)}
              ${sourcesHtml}
            `
          : enrichmentStatus === "error"
            ? window.PortalI18n.html`<div class="paneNote">Enrichment failed: ${esc(enrichment.error || window.PortalI18n.t("unknown error"))}</div>`
            : webRunning
              ? webRunningNote
              : window.PortalI18n.html`<div class="paneNote">Enriching abstract and metadata overview in the background...</div>`
      );
      return window.PortalI18n.html`
          <div class="ccfPaperDetail">
            <div>
              <h3>${esc(paper.title || window.PortalI18n.t("Untitled DBLP paper"))}</h3>
              <div class="workspaceMeta">
                ${chip(ccfRankLabel(venue), "good")}
                ${chip(ccfVenueShortName(venue) || state.ccfSelectedVenue || "Venue")}
                ${state.ccfSelectedYear ? chip(state.ccfSelectedYear) : ""}
                ${paper.type ? chip(paper.type) : ""}
                ${paper.doi ? chip(`DOI ${paper.doi}`) : ""}
              </div>
            </div>
            ${authors ? window.PortalI18n.html`<div class="paneNote">${esc(authors)}</div>` : ""}
            ${enrichmentHtml}
            <div class="workspaceModeActions" style="justify-content:flex-start;margin-left:0;">
              ${webButtonHtml}
              ${registeredId
              ? window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" data-paper-id="${esc(registeredId)}">Open registered paper</button>`
              : window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" data-register-ccf-paper ${state.ccfRegisterInFlight === (paper.url || paper.title) ? "disabled" : ""}>${state.ccfRegisterInFlight === (paper.url || paper.title) ? "Registering..." : "Regist"}</button>`}
            </div>
          </div>
        `;
    }

    function renderCcfWorkspace() {
      const payload = currentCcfPayload();
      if (!state.ccfSelectedVenue || !state.ccfSelectedYear) {
        $("workspaceMain").innerHTML = paperStateHtml(
          "Select a CCF-A or CCF-B venue year or issue from the left panel.",
        );
        return;
      }
      if (!payload) {
        $("workspaceMain").innerHTML = paperStateHtml("Reading local DBLP cache…", { state: "loading" });
        loadCcfPapers(state.ccfSelectedVenue, state.ccfSelectedYear)
          .then(() => {
            renderWorkspace();
            return loadChatHistory({ paperId: activeChatPaperId(), workspaceId: activeChatWorkspaceId(), render: true });
          })
          .catch(alertError);
        return;
      }
      if (payload.error) {
        $("workspaceMain").innerHTML = paperStateHtml(payload.error, { state: "error" });
        return;
      }
      if (payload.cache_missing) {
        const fetchKey = ccfYearKey(state.ccfSelectedVenue, state.ccfSelectedYear);
        const fetching = state.ccfPaperFetchInFlight === fetchKey;
        $("workspaceMain").innerHTML = paperStateHtml(
          "No local paper list cache for this entry yet.",
          {
            actionsHtml: window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" data-fetch-ccf-papers ${fetching ? "disabled" : ""}>${fetching ? "Fetching..." : window.PortalI18n.t("Fetch DBLP paper list")}</button>`,
          },
        );
        return;
      }
      if (state.ccfSelectedPaper) {
        const selectedPaper = state.ccfSelectedPaper;
        const selectedKey = ccfPaperEnrichmentKey(selectedPaper);
        $("workspaceMain").innerHTML = renderCcfPaperPreview(selectedPaper);
        const existing = ccfPaperEnrichment(selectedPaper);
        queueMicrotask(() => {
          if (!state.ccfSelectedPaper || ccfPaperEnrichmentKey(state.ccfSelectedPaper) !== selectedKey) return;
          ensureCcfPaperEnrichment(selectedPaper, { web: String(existing?.status || "") === "web_pending" }).catch(alertError);
        });
        return;
      }
      const papers = ccfCollectionPapers();
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="ccfPaperSearchBar">
            <div data-portal-search-field data-portal-search-density="compact">
              <input data-portal-search-input type="search" data-ccf-paper-search placeholder="Search title, author, DOI, ID..." value="${esc(state.ccfPaperQuery || "")}" />
            </div>
            <span class="ccfPaperSearchMeta" id="ccfPaperSearchMeta">${papers.length}/${papers.length} papers</span>
          </div>
          <div class="paperList ccfPaperList" id="ccfPaperListResults">
          </div>
          ${papers.length ? paperStateHtml(window.PortalI18n.t("No paper matches this list search."), {
          density: "compact",
          className: "hidden",
          id: "ccfPaperSearchEmpty",
        }) : ""}
          ${papers.length ? window.PortalI18n.html`<div class="portalToolbar paperToolbar" style="justify-content:center;margin-top:10px;"><button class="portalButton is-compact is-quiet" type="button" id="ccfPaperLoadMore"></button></div>` : paperStateHtml(window.PortalI18n.t("No DBLP papers found for this venue entry."))}
        `;
      queueMicrotask(applyCcfPaperListFilter);
    }

    async function registerCcfPaper(paper) {
      if (!paper) return;
      const marker = paper.url || paper.title || "ccf";
      const returnSnapshot = currentPaperWorkspaceSnapshot({
        leftFace: "ccf",
        mode: "library",
        selectedId: "",
      });
      state.ccfRegisterInFlight = marker;
      renderWorkspace();
      try {
        const relatedProject = String(state.filters.project || "").trim();
        const payload = await paperApi.request("/paper/api/ccf/register", {
          method: "POST",
          body: JSON.stringify({
            paper,
            project_ids: relatedProject ? [relatedProject] : [],
            session: relatedProject,
          }),
        });
        const registered = payload.paper || payload;
        const paperId = registered.id || payload.paper_id || "";
        if (paperId) {
          paper.registered_paper_id = paperId;
          state.previousPaperWorkspace = returnSnapshot;
          state.leftFace = "library";
          state.mode = "reader";
          state.ccfSelectedPaper = null;
          state.selectedId = paperId;
          state.detail = null;
          updateLeftFaceTabs();
          syncModeTabs();
          writeUiState();
          await loadLibrary({ keepDetail: true });
          await loadDetail(paperId, { render: true });
        } else {
          await loadLibrary({ keepDetail: true });
          renderWorkspace();
        }
      } finally {
        state.ccfRegisterInFlight = "";
        renderWorkspace();
      }
    }

    return Object.freeze({
      ccfWorkspaceMeta, refreshCurrentLeftFace, registerCcfPaper, renderCcfPaperRow, renderCcfTree,
      renderCcfWorkspace, showCcfFace, showLibraryFace, updateLeftFaceTabs,
    });
  }

  window.PortalPaperCcfView = Object.freeze({ createCcfView, dependencies: DEPENDENCIES });
})();
