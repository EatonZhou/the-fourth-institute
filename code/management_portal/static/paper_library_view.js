(() => {
  "use strict";

  if (window.PortalPaperLibraryView) return;

  // The Library as markup: the folder tree, the Library mode's Paper rows,
  // favorites and the selected Paper in the tree.
  const DEPENDENCIES = Object.freeze([
    "$", "bindPdfSelectionEvents", "chip", "disclosureIcon", "esc", "isCollectionMode", "loadLibrary",
    "paperApi", "paperStateHtml", "paperTags", "renderCcfTree", "state", "updateLeftFaceTabs",
  ]);

  function createLibraryView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperLibraryView is missing: ${missing.join(", ")}`);
    const {
      $, bindPdfSelectionEvents, chip, disclosureIcon, esc, isCollectionMode, loadLibrary, paperApi,
      paperStateHtml, paperTags, renderCcfTree, state, updateLeftFaceTabs,
    } = dependencies;

    function paperTreeFolderKey(venue, year = "", session = "") {
      return JSON.stringify([String(venue || ""), String(year || ""), String(session || "")]);
    }

    function paperTreeNodeExpanded(key, defaultExpanded) {
      if (Object.prototype.hasOwnProperty.call(state.paperTreeExpansion || {}, key)) {
        return !!state.paperTreeExpansion[key];
      }
      return !!defaultExpanded;
    }

    function paperTreePapersByFolder() {
      const result = new Map();
      for (const paper of state.library?.papers || []) {
        const key = paperTreeFolderKey(
          paper.venue || "Unsorted",
          paper.year || "Unknown",
          paper.session || "General",
        );
        if (!result.has(key)) result.set(key, []);
        result.get(key).push(paper);
      }
      return result;
    }

    function renderTree() {
      if (state.leftFace === "ccf") {
        renderCcfTree();
        return;
      }
      updateLeftFaceTabs();
      const meta = state.library?.summary || {};
      $("libraryMeta").textContent = `${meta.visible_count || 0}/${meta.paper_count || 0} papers · ${meta.analysis_count || 0} analyses`;
      const folders = state.library?.folders || [];
      if (!folders.length) {
        const hasAny = Number(meta.paper_count || 0) > 0;
        $("folderTree").innerHTML = hasAny
          ? paperStateHtml("No paper matches current filters.", { density: "inline" })
          : paperStateHtml("No papers registered yet. Use + to register PDF and analysis paths.", {
            density: "inline",
          });
        return;
      }
      const papersByFolder = paperTreePapersByFolder();
      const paperLeafHtml = paper => window.PortalI18n.html`
          <div class="paperLeaf">
            <button class="folderLine ${!isCollectionMode() && paper.id === state.selectedId ? "selected" : ""}" type="button" data-paper-id="${esc(paper.id)}">
              <span class="statusDot ${paper.has_pdf ? "ready" : ""}" title="${paper.has_pdf ? window.PortalI18n.t("PDF available") : window.PortalI18n.t("PDF missing")}"></span>
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(paper.title)}</span>
            </button>
            ${paperFavoriteButton(paper.id)}
          </div>
        `;
      const rendered = [];
      const limit = state.paperTreeRenderLimit;
      let visibleCount = 0;
      const append = markup => {
        visibleCount += 1;
        if (visibleCount <= limit) rendered.push(markup());
      };
      folders.forEach(venue => {
        const venueKey = paperTreeFolderKey(venue.name);
        const venueExpanded = paperTreeNodeExpanded(venueKey, true);
        append(() => window.PortalI18n.html`
            <div class="folderNode">
              <button class="folderLine" type="button" data-paper-tree-toggle="${esc(venueKey)}" aria-expanded="${venueExpanded ? "true" : "false"}">
                ${disclosureIcon(venueExpanded)}<strong>${esc(venue.name)}</strong><span class="folderCount">${venue.count}</span>
              </button>
            </div>
          `);
        if (!venueExpanded) return;
        (venue.children || []).forEach(year => {
          const yearKey = paperTreeFolderKey(venue.name, year.name);
          const yearExpanded = paperTreeNodeExpanded(yearKey, true);
          append(() => window.PortalI18n.html`
              <div class="folderNode" style="padding-left: 12px;">
                <button class="folderLine" type="button" data-paper-tree-toggle="${esc(yearKey)}" aria-expanded="${yearExpanded ? "true" : "false"}">
                  ${disclosureIcon(yearExpanded)}${esc(year.name)}<span class="folderCount">${year.count}</span>
                </button>
              </div>
            `);
          if (!yearExpanded) return;
          (year.children || []).forEach(session => {
            const sessionKey = paperTreeFolderKey(venue.name, year.name, session.name);
            const sessionExpanded = paperTreeNodeExpanded(sessionKey, false);
            const papers = (session.papers || []).length
              ? session.papers
              : (papersByFolder.get(sessionKey) || []);
            append(() => window.PortalI18n.html`
                <div class="folderNode" style="padding-left: 24px;">
                  <button class="folderLine" type="button" data-paper-tree-toggle="${esc(sessionKey)}" aria-expanded="${sessionExpanded ? "true" : "false"}">
                    ${disclosureIcon(sessionExpanded)}${esc(session.name)}<span class="folderCount">${session.count}</span>
                  </button>
                </div>
              `);
            if (sessionExpanded) papers.forEach(paper => append(() => window.PortalI18n.html`
                <div style="padding-left: 24px;">${paperLeafHtml(paper)}</div>
              `));
          });
        });
      });
      if (visibleCount > limit) rendered.push(window.PortalI18n.html`
          <button class="folderLine" type="button" data-paper-tree-more>
            ${window.PortalI18n.t("显示更多目录")} (${visibleCount - limit})
          </button>
        `);
      $("folderTree").innerHTML = rendered.join("");
    }

    function libraryHeaderMeta() {
      const summary = state.library?.summary || {};
      const semantic = summary.semantic_search || null;
      const collection = summary.paper_collection || null;
      const chips = [];
      if (semantic) {
        chips.push(chip("Semantic search", "accent"));
        chips.push(chip(`${summary.visible_count || 0} matches`, "good"));
        chips.push(chip(`${summary.paper_count || 0} papers in library`));
        if (semantic.updated_at) chips.push(chip(`updated ${new Date(semantic.updated_at * 1000).toLocaleString()}`));
      } else if (collection) {
        chips.push(chip("Collect", "accent"));
        chips.push(chip(`${summary.visible_count || 0} papers`, "good"));
        chips.push(chip(`${summary.paper_count || 0} papers in library`));
        if (collection.updated_at) chips.push(chip(`updated ${new Date(collection.updated_at * 1000).toLocaleString()}`));
      } else {
        chips.push(chip(`${summary.visible_count || 0}/${summary.paper_count || 0} papers`));
        chips.push(chip(`${summary.analysis_count || 0} analyses`, "good"));
        chips.push(chip(`${summary.review_count || 0} reviews`));
        const activeFilters = Object.entries(state.filters || {}).filter(([, value]) => String(value || "").trim()).map(([key, value]) => `${key}: ${value}`);
        if (state.query) activeFilters.unshift(`q: ${state.query}`);
        if (state.mode === "today") activeFilters.unshift("registered: today");
        if (activeFilters.length) chips.push(chip(activeFilters.join(" · "), "accent"));
      }
      return chips.join("");
    }

    function renderLibraryMode() {
      if (state.libraryLoading) {
        $("workspaceMain").innerHTML = paperStateHtml(
          `Loading ${state.mode === "today" ? "Today" : "Paper Library"}…`,
          { state: "loading" },
        );
        return;
      }
      const summary = state.library?.summary || {};
      const papers = state.library?.papers || [];
      const visiblePapers = papers.slice(0, state.libraryRenderLimit);
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="overviewGrid">
            <div class="metric"><div class="metricValue">${summary.paper_count || 0}</div><div class="metricLabel">Registered papers</div></div>
            <div class="metric"><div class="metricValue">${summary.analysis_count || 0}</div><div class="metricLabel">Analysis ready</div></div>
            <div class="metric"><div class="metricValue">${summary.review_count || 0}</div><div class="metricLabel">AI reviews</div></div>
          </div>
          <div class="paperList">
            ${visiblePapers.length ? visiblePapers.map(renderPaperRow).join("") : paperStateHtml(window.PortalI18n.t("No paper matches current filters."))}
          </div>
          ${visiblePapers.length < papers.length ? window.PortalI18n.html`<div class="portalToolbar paperToolbar" style="justify-content:center;margin-top:10px;"><button class="portalButton is-compact is-quiet" type="button" id="loadMoreLibraryBtn">Show more · ${visiblePapers.length}/${papers.length}</button></div>` : ""}
        `;
      bindPdfSelectionEvents();
    }

    function paperFavoriteButton(paperId) {
      const favorite = state.favoriteIds.has(paperId);
      const label = favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("收藏论文");
      return window.PortalI18n.html`<button class="portalButton is-icon is-quiet paperFavorite" type="button" data-paper-favorite="${esc(paperId)}" aria-pressed="${favorite}" aria-label="${label}" title="${label}" ${state.favoritePending.has(paperId) ? "disabled" : ""}>${favorite ? "★" : "☆"}</button>`;
    }

    function syncFavoriteButtons() {
      const header = $("paperFavoriteBtn");
      const paperId = state.leftFace === "library" && !isCollectionMode() ? state.detail?.paper?.id : "";
      header.hidden = !paperId;
      header.dataset.paperFavorite = paperId || "";
      document.querySelectorAll("[data-paper-favorite]").forEach(button => {
        const id = button.dataset.paperFavorite;
        const favorite = state.favoriteIds.has(id);
        const label = favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("收藏论文");
        button.setAttribute("aria-pressed", String(favorite));
        button.setAttribute("aria-label", label);
        button.title = label;
        button.textContent = favorite ? "★" : "☆";
        button.disabled = state.favoritePending.has(id);
      });
    }

    async function togglePaperFavorite(paperId) {
      if (!paperId || state.favoritePending.has(paperId)) return;
      state.favoritePending.add(paperId);
      state.favoriteRevision += 1;
      syncFavoriteButtons();
      let saved = false;
      try {
        const result = await paperApi.request("/paper/api/favorites", {
          method: "POST",
          body: { paper_id: paperId, favorite: !state.favoriteIds.has(paperId) },
        });
        if (result.favorite) state.favoriteIds.add(paperId);
        else state.favoriteIds.delete(paperId);
        saved = true;
      } finally {
        state.favoriteRevision += 1;
        state.favoritePending.delete(paperId);
        syncFavoriteButtons();
      }
      if (saved && state.filters.favorite === "1") {
        await loadLibrary({ keepDetail: true, hydrateDetail: false });
      }
    }

    function renderPaperRow(paper) {
      const tags = paperTags(paper).slice(0, 7).map(t => chip(t, "accent")).join("");
      const relevance = Number.isFinite(Number(paper.semantic_score))
        ? window.PortalI18n.html`<span class="relevanceBadge" title="${esc(paper.semantic_reason || "")}">Relevance ${esc(paper.semantic_score)}</span>`
        : Number.isFinite(Number(paper.collection_rank))
          ? window.PortalI18n.html`<span class="relevanceBadge">Collected #${esc(paper.collection_rank)}</span>`
        : "";
      return window.PortalI18n.html`
          <article class="paperRow ${!isCollectionMode() && paper.id === state.selectedId ? "selected" : ""}" data-paper-id="${esc(paper.id)}">
            <div class="paperRowHead">
              <div class="paperRowTitle">${esc(paper.title)}</div>
              <div class="paperRowActions">${relevance}${paperFavoriteButton(paper.id)}</div>
            </div>
            <div class="workspaceMeta">
              ${chip(paper.venue || "Venue")} ${paper.year ? chip(paper.year) : ""} ${paper.session ? chip(paper.session) : ""}
              ${paper.has_pdf ? chip("PDF", "good") : chip(window.PortalI18n.t("PDF missing"), "warn")}
              ${paper.has_analysis ? chip("Analyzed", "good") : chip(window.PortalI18n.t("Analysis missing"), "warn")}
            </div>
            ${tags ? window.PortalI18n.html`<div class="chips">${tags}</div>` : ""}
            ${paper.abstract ? window.PortalI18n.html`<div class="paneNote">${esc(paper.abstract).slice(0, 260)}</div>` : ""}
          </article>
        `;
    }

    function syncSelectedPaperInTree(previousId = "", nextId = state.selectedId) {
      const tree = $("folderTree");
      if (!tree) return;
      for (const id of [previousId, nextId]) {
        const cleanId = String(id || "").trim();
        if (!cleanId) continue;
        tree.querySelector(`[data-paper-id="${CSS.escape(cleanId)}"]`)?.classList.toggle("selected", cleanId === String(nextId || ""));
      }
    }

    return Object.freeze({
      libraryHeaderMeta, renderLibraryMode, renderTree, syncFavoriteButtons, syncSelectedPaperInTree,
      togglePaperFavorite,
    });
  }

  window.PortalPaperLibraryView = Object.freeze({ createLibraryView, dependencies: DEPENDENCIES });
})();
