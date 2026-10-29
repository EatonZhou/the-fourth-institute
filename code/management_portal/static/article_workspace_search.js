(() => {
  "use strict";

  if (window.PortalArticleWorkspaceSearch) return;

  // Searching the Article's files and showing matches grouped by file.
  const DEPENDENCIES = Object.freeze([
    "$", "articleStateHtml", "esc", "request", "state",
  ]);

  function createSearch(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceSearch is missing: ${missing.join(", ")}`);
    const { $, articleStateHtml, esc, request, state } = dependencies;

    function highlightSearchPreview(preview, needle) {
      const source = String(preview || "");
      const tokens = [...new Set(String(needle || "").trim().split(/\s+/).filter(Boolean))]
        .sort((left, right) => right.length - left.length);
      if (!tokens.length) return esc(source);
      const lowerSource = source.toLowerCase();
      const lowerTokens = tokens.map(token => token.toLowerCase());
      let cursor = 0;
      let output = "";
      while (cursor < source.length) {
        let matchIndex = -1;
        let matchLength = 0;
        lowerTokens.forEach(token => {
          const index = lowerSource.indexOf(token, cursor);
          if (index < 0) return;
          if (matchIndex < 0 || index < matchIndex || (index === matchIndex && token.length > matchLength)) {
            matchIndex = index;
            matchLength = token.length;
          }
        });
        if (matchIndex < 0) {
          output += esc(source.slice(cursor));
          break;
        }
        output += window.PortalI18n.html`${esc(source.slice(cursor, matchIndex))}<mark>${esc(source.slice(matchIndex, matchIndex + matchLength))}</mark>`;
        cursor = matchIndex + matchLength;
      }
      return output || esc(source);
    }

    function renderSearchResults(results, needle) {
      const groups = new Map();
      results.forEach(result => {
        const path = String(result.path || "Unknown file");
        if (!groups.has(path)) groups.set(path, []);
        groups.get(path).push(result);
      });
      $("articleSearchResults").innerHTML = window.PortalI18n.html`
      <div class="articleSearchSummary"><strong>${results.length} 个结果</strong><span>位于 ${groups.size} 个文件</span></div>
      ${[...groups.entries()].map(([path, fileResults]) => {
          const collapsed = state.collapsedSearchFiles.has(path);
          return window.PortalI18n.html`<section class="articleSearchGroup ${collapsed ? "collapsed" : ""}" data-search-group-section="${esc(path)}">
          <button class="articleSearchGroupHead" type="button" data-search-group="${esc(path)}" aria-expanded="${collapsed ? "false" : "true"}" data-portal-icon="chevron-right">
            <span title="${esc(path)}">${esc(path)}</span><small>${fileResults.length}</small>
          </button>
          <div class="articleSearchGroupBody">
            ${fileResults.map(result => window.PortalI18n.html`
              <button class="articleSearchResult" type="button" data-search-file="${esc(path)}" data-search-line="${Number(result.line || 1)}" data-search-column="${Number(result.column || 1)}">
                <span class="articleSearchLine">${Number(result.line || 1)}</span>
                <span class="articleSearchPreview">${highlightSearchPreview(result.preview, needle)}</span>
              </button>`).join("")}
          </div>
        </section>`;
        }).join("")}`;
    }

    async function searchProject(event) {
      event.preventDefault();
      if (!state.selectedId) return;
      const search = $("articleGlobalSearchInput");
      const needle = search.value.trim();
      if (!needle) {
        $("articleSearchResults").innerHTML = articleStateHtml("Enter text to search the current Article project.");
        return;
      }
      $("articleSearchResults").innerHTML = articleStateHtml("Searching…", { state: "loading" });
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/search?q=${encodeURIComponent(needle)}`);
        const results = Array.isArray(payload.results) ? payload.results : [];
        state.collapsedSearchFiles.clear();
        if (results.length) renderSearchResults(results, needle);
        else $("articleSearchResults").innerHTML = articleStateHtml(`没有“${needle}”的搜索结果。`);
      } catch (error) {
        $("articleSearchResults").innerHTML = articleStateHtml(error.message || error, { state: "error" });
      }
    }

    return Object.freeze({
      searchProject,
    });
  }

  window.PortalArticleWorkspaceSearch = Object.freeze({ createSearch, dependencies: DEPENDENCIES });
})();
