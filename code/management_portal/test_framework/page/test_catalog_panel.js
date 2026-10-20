(() => {
  "use strict";
  const T = window.PortalTest;
  function create({ root }) {
    const filters = { search: "", importance: "", module: "", feature: "", design: "", aspects: new Set(), liveOnly: false };
    const PAGE_SIZE = 100;
    let visibleLimit = PAGE_SIZE;
    let filtersOpen = false;
    let matchedCaseIds = null;
    let queryPending = false;
    let queryRevision = 0;
    let searchTimer = 0;
    const closeFilters = ({ restoreFocus = true } = {}) => {
      if (!filtersOpen) return;
      filtersOpen = false;
      const panel = root.querySelector("#testCatalogFilters");
      if (panel) panel.hidden = true;
      const button = root.querySelector("[data-filter-toggle]");
      button?.setAttribute("aria-expanded", "false");
      if (restoreFocus) button?.focus();
    };
    const handleDocumentKeydown = event => {
      if (event.key === "Escape" && filtersOpen) closeFilters();
    };
    const handleDocumentPointerdown = event => {
      if (!filtersOpen) return;
      const panel = root.querySelector("#testCatalogFilters");
      const button = root.querySelector("[data-filter-toggle]");
      if (panel?.contains(event.target) || button?.contains(event.target)) return;
      closeFilters({ restoreFocus: false });
    };
    const visible = () => T.store.cases.filter(item => (
      item.lifecycle !== "retired"
      && (matchedCaseIds === null || matchedCaseIds.has(item.id))
    ));
    const hasQueryFilters = () => Boolean(
      filters.search.trim()
      || filters.importance
      || filters.module
      || filters.feature
      || filters.design
      || filters.aspects.size
      || filters.liveOnly
    );
    const queryParams = () => {
      const params = new URLSearchParams();
      if (filters.search.trim()) params.set("search", filters.search.trim());
      if (filters.importance) params.append("importance", filters.importance);
      if (filters.module.startsWith("group:")) {
        const groupId = filters.module.slice(6);
        const group = (T.store.catalog?.filters?.modules || []).find(item => item.id === groupId);
        (group?.modules || []).forEach(item => params.append("module", item.id));
      } else if (filters.module) {
        params.append("module", filters.module);
      }
      if (filters.feature) params.append("feature", filters.feature);
      if (filters.design) params.set("is_design_point", filters.design);
      filters.aspects.forEach(aspect => params.append("aspect", aspect));
      if (filters.liveOnly) params.set("has_live", "true");
      return params;
    };
    const activeFilterCount = () => [filters.importance, filters.module, filters.feature, filters.design].filter(Boolean).length + filters.aspects.size + Number(filters.liveOnly);
    const selectedModuleLabel = () => {
      if (!filters.module) return "";
      const groups = T.store.catalog?.filters?.modules || [];
      if (filters.module.startsWith("group:")) {
        return groups.find(group => group.id === filters.module.slice(6))?.label || filters.module.slice(6);
      }
      for (const group of groups) {
        const module = (group.modules || []).find(item => item.id === filters.module);
        if (module) return module.label || module.id;
      }
      return filters.module;
    };
    const filterButtonLabel = () => {
      const count = activeFilterCount();
      const moduleLabel = selectedModuleLabel();
      return [window.PortalI18n.t("筛选"), count ? String(count) : "", moduleLabel].filter(Boolean).join(" · ");
    };
    const rowsHtml = rows => {
      if (T.store.catalogStatus === "error") return window.PortalI18n.html`<div class="testEmpty">测试目录加载失败：${T.escapeHtml(T.store.catalogError)}</div>`;
      if (T.store.catalogStatus !== "loaded") return window.PortalI18n.markup('<div class="testEmpty" role="status">正在加载测试目录…</div>');
      return rows.length ? rows.map(item => {
        const label = T.caseLabel(item);
        const tags = [...(item.aspects || []).map(aspect => T.aspectLabels[aspect] || aspect), ...(item.is_design_point ? [item.design_point_ref ? "WALL 点已绑定" : "设计点未绑定"] : []), ...(item.live ? ["Live"] : [])];
        return window.PortalI18n.html`<article class="testCaseRow ${item.id === T.store.selectedId ? "is-selected" : ""}" data-case-id="${T.escapeHtml(item.id)}"><input class="testCaseSelect" type="checkbox" data-select-case="${T.escapeHtml(item.id)}" ${T.store.selection.has(item.id) ? "checked" : ""} aria-label="将 ${T.escapeHtml(label)} 加入运行选择"><button class="testCaseRowOpen" data-open-case="${T.escapeHtml(item.id)}" data-portal-tooltip="打开测试用例" type="button"><span class="testCaseRowContext"><span title="${T.escapeHtml(T.store.moduleLabel(item))}">${T.escapeHtml(T.store.moduleLabel(item))}</span><span class="testCaseRowImportance">${T.escapeHtml(item.importance)}</span></span><span class="testCaseRowTitle" title="${T.escapeHtml(label)}">${T.escapeHtml(label)}</span><span class="testCaseRowSource" title="${T.escapeHtml(T.caseSource(item))}">${T.escapeHtml(T.caseSource(item))}</span>${tags.length ? window.PortalI18n.html`<span class="testCaseRowTags">${tags.map(tag => window.PortalI18n.html`<span>${T.escapeHtml(window.PortalI18n.t(tag))}</span>`).join("")}</span>` : ""}</button></article>`;
      }).join("") : window.PortalI18n.markup('<div class="testEmpty">当前组合筛选没有测试用例。</div>');
    };
    const syncSelectionAction = () => {
      const button = root.querySelector("[data-run-selected]");
      if (!button) return;
      button.disabled = T.store.catalogStatus !== "loaded" || T.store.selection.size === 0;
      button.textContent = T.store.selection.size ? window.PortalI18n.text`生成运行计划（${T.store.selection.size}）` : window.PortalI18n.t("生成运行计划");
    };
    const bindRows = () => {
      root.querySelectorAll("[data-open-case]").forEach(button => button.addEventListener("click", () => T.openCase(button.dataset.openCase)));
      root.querySelectorAll("[data-case-id]").forEach(row => row.addEventListener("click", event => {
        if (event.target.closest("button, input, select, textarea, a")) return;
        T.openCase(row.dataset.caseId);
      }));
      root.querySelectorAll("[data-select-case]").forEach(input => input.addEventListener("change", () => {
        input.checked ? T.store.selection.add(input.dataset.selectCase) : T.store.selection.delete(input.dataset.selectCase);
        syncSelectionAction();
      }));
    };
    const updateResults = () => {
      const matches = visible();
      const rows = matches.slice(0, visibleLimit);
      const activeTotal = T.store.cases.filter(item => item.lifecycle !== "retired").length;
      const list = root.querySelector(".testCaseList");
      if (list) list.innerHTML = rowsHtml(rows);
      const meta = root.querySelector("[data-portal-board-meta]");
      if (meta) meta.textContent = queryPending ? window.PortalI18n.t("筛选中…") : `${matches.length} / ${activeTotal}`;
      const count = root.querySelector("[data-visible-case-count]");
      if (count) count.textContent = window.PortalI18n.text`已显示 ${rows.length} / ${matches.length} 条`;
      const filterLabel = root.querySelector("[data-filter-toggle] span");
      if (filterLabel) filterLabel.textContent = filterButtonLabel();
      bindRows();
      bindMore(matches.length);
      syncSelectionAction();
    };
    const bindMore = total => {
      const host = root.querySelector("[data-load-more-host]");
      if (!host) return;
      host.innerHTML = visibleLimit < total ? window.PortalI18n.html`<button class="portalButton testLoadMore" data-load-more type="button">再显示 ${Math.min(PAGE_SIZE, total - visibleLimit)} 条</button>` : "";
      host.querySelector("[data-load-more]")?.addEventListener("click", () => { visibleLimit += PAGE_SIZE; updateResults(); });
    };
    const applyMatchedResults = () => {
      const filterPanel = root.querySelector(".testCatalogFilterPanel");
      const filterScrollTop = filterPanel?.scrollTop || 0;
      visibleLimit = PAGE_SIZE;
      updateResults();
      const matches = visible();
      if (!matches.some(item => item.id === T.store.selectedId)) {
        T.openCase(matches[0]?.id || "", { reveal: false });
      }
      if (filterPanel && filterScrollTop) {
        const restoreFilterScroll = () => {
          if (filterPanel.isConnected) filterPanel.scrollTop = filterScrollTop;
        };
        restoreFilterScroll();
        window.setTimeout(restoreFilterScroll, 0);
        window.setTimeout(restoreFilterScroll, 60);
        window.requestAnimationFrame(() => window.requestAnimationFrame(restoreFilterScroll));
      }
    };
    const refilter = ({ debounce = false } = {}) => {
      window.clearTimeout(searchTimer);
      const execute = async () => {
        const revision = ++queryRevision;
        if (!hasQueryFilters()) {
          matchedCaseIds = null;
          queryPending = false;
          applyMatchedResults();
          return;
        }
        queryPending = true;
        updateResults();
        try {
          const payload = await T.api(`/test/api/catalog/query?${queryParams().toString()}`);
          if (revision !== queryRevision) return;
          matchedCaseIds = new Set(payload.case_ids || []);
          queryPending = false;
          applyMatchedResults();
        } catch (error) {
          if (revision !== queryRevision) return;
          queryPending = false;
          updateResults();
          T.notify(`筛选测试目录失败：${error.message || error}`, "error");
        }
      };
      if (debounce) searchTimer = window.setTimeout(execute, 160);
      else execute();
    };
    const syncSelectedCase = () => {
      root.querySelectorAll("[data-case-id]").forEach(row => {
        row.classList.toggle("is-selected", row.dataset.caseId === T.store.selectedId);
      });
    };
    const render = () => {
      const previousBody = root.querySelector(".testPanelBody");
      const scroll = previousBody
        ? { top: previousBody.scrollTop, left: previousBody.scrollLeft }
        : null;
      const summary = T.store.catalog?.summary || {};
      const groups = T.store.catalog?.filters?.modules || [];
      const catalogReady = T.store.catalogStatus === "loaded";
      const matches = visible();
      const rows = matches.slice(0, visibleLimit);
      const refreshAction = window.PortalI18n.markup('<button class="portalButton is-icon is-quiet iconBtn" data-refresh data-portal-icon="refresh" type="button" aria-label="刷新测试目录" title="刷新"></button>');
      root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("测试目录"), window.PortalI18n.t("浏览、筛选并运行 Portal 测试用例。"), refreshAction)}
        <div class="testPanelBody">
          <dl class="testCatalogSummary" aria-label="测试目录概览"><div><dt>用例</dt><dd>${summary.total ?? "—"}</dd></div><div><dt>设计点</dt><dd>${summary.design_points ?? "—"}</dd></div><div><dt>缺实现</dt><dd>${summary.without_implementation ?? "—"}</dd></div><div><dt>待检查</dt><dd>${summary.needs_review ?? "—"}</dd></div></dl>
          <div class="testCatalogToolbar" data-portal-board-toolbar>
            <div data-portal-board-search><label for="testCatalogSearch">搜索</label><input data-portal-board-search-input data-search id="testCatalogSearch" type="search" autocomplete="off" value="${T.escapeHtml(filters.search)}" placeholder="自然语言目标 / 模块 / key" ${catalogReady ? "" : "disabled"} /></div>
            <div data-portal-board-actions>
              <div class="testCatalogFilterOwner" data-portal-board-filter>
                <button data-portal-board-action="filter" data-filter-toggle type="button" aria-expanded="${filtersOpen ? "true" : "false"}" aria-controls="testCatalogFilters" ${catalogReady ? "" : "disabled"}><span>${T.escapeHtml(filterButtonLabel())}</span></button>
              </div>
            </div>
            <span data-portal-board-meta>${catalogReady ? `${matches.length} / ${T.store.cases.filter(item => item.lifecycle !== "retired").length}` : T.store.catalogStatus === "error" ? window.PortalI18n.t("加载失败") : window.PortalI18n.t("加载中")}</span>
          </div>
          <div class="testCatalogFilterPanel testFilters" id="testCatalogFilters" ${filtersOpen ? "" : "hidden"}>
                  <p class="testFilterHelp" role="note"><strong>组合规则：</strong>不同维度按 AND；同一维度多选按 OR。P0 阻断核心能力，P1 影响主要流程，P2 影响局部体验，P3 为低风险细节。设计点用例直接约束架构或关键产品决策。</p>
                  <div class="portalCollectionFilterLabel" id="testImportanceLabel">重要等级</div><select class="portalBoardSelect" data-importance aria-labelledby="testImportanceLabel"><option value="">全部重要等级</option>${["P0", "P1", "P2", "P3"].map(value => window.PortalI18n.html`<option ${filters.importance === value ? "selected" : ""}>${value}</option>`).join("")}</select>
                  <div class="portalCollectionFilterLabel" id="testModuleLabel">功能模块</div><select class="portalBoardSelect" data-module aria-labelledby="testModuleLabel" title="选择功能模块；展开可查看完整名称"><option value="">全部功能模块</option>${groups.map(group => window.PortalI18n.html`<optgroup label="${T.escapeHtml(group.label)}"><option value="group:${T.escapeHtml(group.id)}" ${filters.module === `group:${group.id}` ? "selected" : ""}>全部 · ${T.escapeHtml(group.label)}</option>${(group.modules || []).map(module => window.PortalI18n.html`<option value="${T.escapeHtml(module.id)}" title="${T.escapeHtml(module.label)}" ${filters.module === module.id ? "selected" : ""}>${T.escapeHtml(module.label)}</option>`).join("")}</optgroup>`).join("")}</select>
                  <div class="portalCollectionFilterLabel" id="testFeatureLabel">所需功能</div><select class="portalBoardSelect" data-feature aria-labelledby="testFeatureLabel" title="用例运行所需的可选功能；核心用例不需要任何可选功能"><option value="">全部用例</option>${(T.store.catalog?.filters?.features || []).map(item => window.PortalI18n.html`<option value="${T.escapeHtml(item.id)}" ${filters.feature === item.id ? "selected" : ""}>${T.escapeHtml(window.PortalI18n.t(item.label))}</option>`).join("")}</select>
                  <div class="portalCollectionFilterLabel" id="testDesignLabel">设计点</div><select class="portalBoardSelect" data-design aria-labelledby="testDesignLabel"><option value="">全部用例</option><option value="true" ${filters.design === "true" ? "selected" : ""}>设计点</option><option value="false" ${filters.design === "false" ? "selected" : ""}>非设计点</option></select>
                  <div class="portalCollectionFilterLabel" id="testAspectLabel">测试方面</div><div class="portalCollectionFilterOptions" role="group" aria-labelledby="testAspectLabel">${Object.entries(T.aspectLabels).map(([id, label]) => window.PortalI18n.html`<label class="portalCollectionFilterOption"><input type="checkbox" data-aspect="${id}" ${filters.aspects.has(id) ? "checked" : ""}><span>${label}</span></label>`).join("")}</div>
                  <div class="portalCollectionFilterLabel" id="testCapabilityLabel">可用能力</div><div class="portalCollectionFilterOptions" role="group" aria-labelledby="testCapabilityLabel"><label class="portalCollectionFilterOption"><input type="checkbox" data-live-only ${filters.liveOnly ? "checked" : ""}><span>有 Live</span></label></div>
          </div>
          <div class="testPanelActions testCatalogResultBar"><span data-visible-case-count>${catalogReady ? window.PortalI18n.text`已显示 ${rows.length} / ${matches.length} 条` : T.store.catalogStatus === "error" ? window.PortalI18n.t("请刷新后重试") : window.PortalI18n.t("正在加载")}</span><button class="portalButton" data-run-selected type="button" ${catalogReady && T.store.selection.size ? "" : "disabled"}>${T.store.selection.size ? window.PortalI18n.text`生成运行计划（${T.store.selection.size}）` : window.PortalI18n.t("生成运行计划")}</button></div>
          <div class="testCaseList">${rowsHtml(rows)}</div>
          <div class="testLoadMoreHost" data-load-more-host></div>
        </div>`;
      const currentBody = root.querySelector(".testPanelBody");
      if (scroll && currentBody) {
        currentBody.scrollTop = scroll.top;
        currentBody.scrollLeft = scroll.left;
      }
      root.querySelector("[data-filter-toggle]")?.addEventListener("click", () => {
        filtersOpen = !filtersOpen;
        const panel = root.querySelector("#testCatalogFilters");
        if (panel) panel.hidden = !filtersOpen;
        root.querySelector("[data-filter-toggle]")?.setAttribute("aria-expanded", String(filtersOpen));
      });
      root.querySelector("[data-refresh]")?.addEventListener("click", () => T.store.load().catch(error => T.notify(error.message, "error")));
      root.querySelector("[data-search]")?.addEventListener("input", event => { filters.search = event.target.value; refilter({ debounce: true }); });
      root.querySelector("[data-importance]")?.addEventListener("change", event => { filters.importance = event.target.value; refilter(); });
      root.querySelector("[data-module]")?.addEventListener("change", event => { filters.module = event.target.value; refilter(); });
      root.querySelector("[data-feature]")?.addEventListener("change", event => { filters.feature = event.target.value; refilter(); });
      root.querySelector("[data-design]")?.addEventListener("change", event => { filters.design = event.target.value; refilter(); });
      root.querySelectorAll("[data-aspect]").forEach(input => input.addEventListener("change", () => { input.checked ? filters.aspects.add(input.dataset.aspect) : filters.aspects.delete(input.dataset.aspect); refilter(); }));
      root.querySelector("[data-live-only]")?.addEventListener("change", event => { filters.liveOnly = event.target.checked; refilter(); });
      bindRows();
      bindMore(matches.length);
      root.querySelector("[data-run-selected]")?.addEventListener("click", () => T.prepareRun([...T.store.selection]));
    };
    root.classList.add("testPanel");
    const unsubscribe = T.store.subscribe(event => {
      // Selecting a case only changes row emphasis. Rebuilding the whole catalog here
      // discards the user's browsing position and couples detail navigation to catalog layout.
      if (event === "selection") {
        syncSelectedCase();
        return;
      }
      render();
      // CatalogRepository owns filter semantics. A refreshed catalog therefore needs a
      // fresh canonical query instead of reusing IDs projected from the previous revision.
      if (event === "catalog" && T.store.catalogStatus === "loaded" && hasQueryFilters()) {
        refilter();
      }
    });
    document.addEventListener("keydown", handleDocumentKeydown);
    document.addEventListener("pointerdown", handleDocumentPointerdown);
    render();
    return {
      element: root,
      destroy() {
        window.clearTimeout(searchTimer);
        queryRevision += 1;
        document.removeEventListener("keydown", handleDocumentKeydown);
        document.removeEventListener("pointerdown", handleDocumentPointerdown);
        unsubscribe();
      },
      visibleCaseIds: () => visible().map(item => item.id),
    };
  }
  T.registerPanel({ type: "test-catalog", title: window.PortalI18n.t("测试目录"), icon: "list", component: "PortalTest.CatalogPanel", create });
})();
