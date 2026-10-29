(() => {
  "use strict";

  // The catalog page (decision_f2a6a5dd4646): every current case, module by
  // module, filtered by the address.  The server filters and pages the list;
  // a filter change replaces the address without adding a history entry.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;
  const PAGE_SIZE = 100;

  function create({ root }) {
    let filters = {};
    let cases = [];
    let count = 0;
    let revision = 0;
    let loading = false;
    let failure = "";
    let searchTimer = 0;
    let selectingAll = false;
    let disposed = false;

    const active = () => Object.keys(filters).length > 0;
    const query = (offset = 0) => {
      const params = T.filterQuery(filters);
      params.set("projection", "page");
      params.set("offset", String(offset));
      params.set("limit", String(PAGE_SIZE));
      return `/test/api/catalog?${params}`;
    };

    function setFilter(key, value) {
      const next = { ...filters };
      const empty = value === undefined || value === "" || value === false || (Array.isArray(value) && !value.length);
      if (empty) delete next[key]; else next[key] = value;
      if (key === "module") delete next.group;
      if (key === "group") delete next.module;
      filters = next;
      window.history.replaceState({}, "", T.catalogHref(filters));
      syncToolbar();
      void fetchPage();
    }

    async function fetchPage({ append = false } = {}) {
      window.clearTimeout(searchTimer);
      const current = ++revision;
      loading = true;
      failure = "";
      if (!append) renderList();
      else syncMore();
      try {
        const payload = await T.api(query(append ? cases.length : 0));
        if (current !== revision || disposed) return;
        cases = append ? [...cases, ...(payload.cases || [])] : payload.cases || [];
        count = payload.count || 0;
      } catch (error) {
        if (current !== revision || disposed) return;
        failure = error.message || String(error);
        if (!append) { cases = []; count = 0; }
      }
      loading = false;
      renderList();
    }

    function moduleOptions() {
      const selected = [].concat(filters.module || []);
      const value = filters.group ? `group:${filters.group}` : selected.length === 1 ? selected[0] : selected.length ? "many" : "";
      const groups = T.moduleGroups().map(group => `<optgroup label="${esc(group.label)}"><option value="group:${esc(group.id)}"${value === `group:${group.id}` ? " selected" : ""}>${esc(window.PortalI18n.text`整组 · ${group.label}`)}</option>${(group.modules || []).map(module =>
        `<option value="${esc(module.id)}"${value === module.id ? " selected" : ""}>${esc(module.label)}</option>`).join("")}</optgroup>`).join("");
      const many = selected.length > 1 ? `<option value="many" selected>${esc(window.PortalI18n.text`${selected.length} 个模块`)}</option>` : "";
      return `<option value="">${esc(t("全部功能模块"))}</option>${many}${groups}`;
    }
    const options = (pairs, value, empty) => `<option value="">${esc(empty)}</option>${pairs.map(([key, label]) => `<option value="${esc(key)}"${key === value ? " selected" : ""}>${esc(label)}</option>`).join("")}`;

    function toolbarHtml() {
      const features = T.overview()?.filters?.features || [];
      const aspects = Object.entries(T.aspectLabels);
      return window.PortalI18n.html`<div class="testCatalogToolbar" data-catalog-toolbar>
        <label class="testSearch"><span class="testSearchIcon" data-portal-icon="search" aria-hidden="true"></span><input type="search" data-catalog-search placeholder="搜索标题、说明、key 或入口" aria-label="搜索用例" autocomplete="off" value="${esc(filters.q || "")}"></label>
        <select class="portalBoardSelect" data-catalog-module aria-label="功能模块">${moduleOptions()}</select>
        <select class="portalBoardSelect" data-catalog-importance aria-label="重要等级">${options(["P0", "P1", "P2", "P3"].map(value => [value, value]), filters.importance || "", t("全部重要等级"))}</select>
        <select class="portalBoardSelect" data-catalog-design aria-label="设计点">${options([["true", t("设计点")], ["false", t("非设计点")]], filters.design || "", t("设计点与否"))}</select>
        <select class="portalBoardSelect" data-catalog-runner aria-label="执行方式">${options([["pytest", "pytest"], ["tau", "TAU"], ["none", t("缺实现")]], filters.runner || "", t("全部执行方式"))}</select>
        ${features.length ? `<select class="portalBoardSelect" data-catalog-feature aria-label="${esc(t("所需功能"))}" title="${esc(t("用例运行所需的可选功能；核心用例不需要任何可选功能"))}">${options(features.map(item => [item.id, item.label]), filters.feature || "", t("全部功能"))}</select>` : ""}
        <div class="testChipRow" role="group" aria-label="测试方面">${aspects.map(([id, label]) => `<button type="button" class="testToggle" data-catalog-aspect="${esc(id)}" aria-pressed="${(filters.aspect || []).includes(id)}">${esc(label)}</button>`).join("")}<button type="button" class="testToggle" data-catalog-live aria-pressed="${Boolean(filters.live)}">Live</button></div>
        <button type="button" class="portalButton is-compact is-quiet" data-catalog-clear ${active() ? "" : "hidden"}>清除筛选</button>
      </div>`;
    }

    function syncToolbar() {
      const toolbar = root.querySelector("[data-catalog-toolbar]");
      if (!toolbar) return;
      toolbar.querySelector("[data-catalog-module]").innerHTML = moduleOptions();
      toolbar.querySelectorAll("[data-catalog-aspect]").forEach(button => button.setAttribute("aria-pressed", String((filters.aspect || []).includes(button.dataset.catalogAspect))));
      toolbar.querySelector("[data-catalog-live]").setAttribute("aria-pressed", String(Boolean(filters.live)));
      for (const [selector, key] of [["[data-catalog-importance]", "importance"], ["[data-catalog-design]", "design"], ["[data-catalog-runner]", "runner"], ["[data-catalog-feature]", "feature"]]) {
        const select = toolbar.querySelector(selector);
        if (select) select.value = filters[key] || "";
      }
      const search = toolbar.querySelector("[data-catalog-search]");
      if (search && document.activeElement !== search) search.value = filters.q || "";
      toolbar.querySelector("[data-catalog-clear]").hidden = !active();
      syncHeading();
    }

    function headingText() {
      if (loading && !cases.length) return t("正在筛选…");
      if (failure && !cases.length) return t("筛选失败");
      const total = T.overview()?.summary?.total;
      return active() && total !== undefined ? window.PortalI18n.text`${count} 条匹配 · 共 ${total} 条` : window.PortalI18n.text`${count} 条用例`;
    }
    function syncHeading() {
      const node = root.querySelector("[data-catalog-count]");
      if (node) node.textContent = headingText();
      const scope = root.querySelector("[data-catalog-scope]");
      if (scope) scope.textContent = scopeText();
    }
    function scopeText() {
      const modules = [].concat(filters.module || []);
      if (filters.group) return T.moduleGroups().find(group => group.id === filters.group)?.label || filters.group;
      if (modules.length === 1) return T.moduleLabel(modules[0]);
      if (modules.length > 1) return modules.map(T.moduleLabel).join("、");
      return "";
    }

    function rowsHtml() {
      let previous = "";
      return cases.map(item => {
        const header = item.module !== previous
          ? `<h3 class="testCaseGroupHead"><a href="${esc(T.catalogHref({ module: item.module }))}">${esc(T.moduleLabel(item.module))}</a></h3>`
          : "";
        previous = item.module;
        const checked = T.selection.has(item.id);
        return `${header}<article class="testCaseRow" data-case-id="${esc(item.id)}" data-selected="${checked}">
          <input type="checkbox" data-select-case="${esc(item.id)}" ${checked ? "checked" : ""} aria-label="${esc(window.PortalI18n.text`选择 ${item.title || item.key}`)}">
          <a class="testCaseRowMain" href="${esc(T.caseHref(item.id))}" data-open-case="${esc(item.id)}"><strong>${esc(item.title || item.key)}</strong><code>${esc(item.key)}</code></a>
          <span class="testCaseRowChips">${T.caseChipsHtml(item)}</span>
        </article>`;
      }).join("");
    }

    function selectionHtml() {
      const size = T.selection.size;
      const all = active() && count > 0 && !failure;
      if (!size && !all) return "";
      return window.PortalI18n.html`<div class="testSelectionBar" data-selection-bar>
        <span>${esc(size ? window.PortalI18n.text`已选 ${size} 条用例` : window.PortalI18n.text`当前筛选出 ${count} 条用例`)}</span>
        ${all ? `<button type="button" class="portalButton is-compact" data-select-all ${selectingAll ? "disabled" : ""}>${esc(selectingAll ? t("正在选择…") : window.PortalI18n.text`选择全部 ${count} 条`)}</button>` : ""}
        ${size ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-clear-selection>清空选择</button><button type="button" class="portalButton is-compact is-primary" data-run-selection data-portal-icon="play"><span>生成运行计划</span></button>` : ""}
      </div>`;
    }

    function syncMore() {
      const host = root.querySelector("[data-catalog-more]");
      if (!host) return;
      const rest = count - cases.length;
      host.innerHTML = rest > 0 && !failure
        ? `<button type="button" class="portalButton" data-load-more ${loading ? "disabled" : ""}>${esc(loading ? t("正在读取…") : window.PortalI18n.text`再显示 ${Math.min(PAGE_SIZE, rest)} 条（还有 ${rest} 条）`)}</button>`
        : "";
      host.querySelector("[data-load-more]")?.addEventListener("click", () => void fetchPage({ append: true }));
    }

    function syncSelection() {
      const host = root.querySelector("[data-selection-host]");
      if (host) host.innerHTML = selectionHtml();
      window.PortalUIPrimitives?.mountIcons?.(host);
      host?.querySelector("[data-select-all]")?.addEventListener("click", () => void selectAll());
      host?.querySelector("[data-clear-selection]")?.addEventListener("click", () => {
        T.selection.clear();
        root.querySelectorAll("[data-select-case]").forEach(input => { input.checked = false; input.closest("[data-case-id]").dataset.selected = "false"; });
        syncSelection();
      });
      host?.querySelector("[data-run-selection]")?.addEventListener("click", () => T.prepareRun([...T.selection], {}, t("用例目录中的选择")));
    }

    function renderList() {
      const list = root.querySelector("[data-catalog-list]");
      if (!list) return;
      list.setAttribute("aria-busy", String(loading));
      if (failure && !cases.length) {
        list.innerHTML = T.stateHtml("error", t("筛选失败"), failure, `<button type="button" class="portalButton is-compact" data-catalog-retry>${esc(t("重试"))}</button>`);
        list.querySelector("[data-catalog-retry]")?.addEventListener("click", () => void fetchPage());
      } else if (loading && !cases.length) {
        list.innerHTML = T.stateHtml("loading", t("正在读取用例…"));
      } else if (!cases.length) {
        list.innerHTML = T.stateHtml("empty", active() ? t("没有符合筛选的用例") : t("目录中还没有用例"), active() ? t("换一组筛选条件，或清除筛选。") : "");
      } else {
        list.innerHTML = rowsHtml();
        list.classList.toggle("is-refreshing", loading);
      }
      syncHeading();
      syncMore();
      syncSelection();
    }

    async function selectAll() {
      selectingAll = true;
      syncSelection();
      try {
        const params = T.filterQuery(filters);
        params.set("current", "true");
        const result = await T.api(`/test/api/catalog/query?${params}`);
        (result.case_ids || []).forEach(id => T.selection.add(id));
        root.querySelectorAll("[data-select-case]").forEach(input => { input.checked = T.selection.has(input.dataset.selectCase); input.closest("[data-case-id]").dataset.selected = String(input.checked); });
      } catch (error) {
        T.notify(window.PortalI18n.text`无法选择全部：${error.message || error}`, "error");
      }
      selectingAll = false;
      syncSelection();
    }

    function bind() {
      const toolbar = root.querySelector("[data-catalog-toolbar]");
      toolbar.querySelector("[data-catalog-search]").addEventListener("input", event => {
        const value = event.target.value.trim();
        window.clearTimeout(searchTimer);
        // Typing invalidates the shown result at once; the request waits for a pause.
        revision += 1;
        searchTimer = window.setTimeout(() => setFilter("q", value), 220);
      });
      toolbar.querySelector("[data-catalog-module]").addEventListener("change", event => {
        const value = event.target.value;
        if (value === "many") return;
        if (value.startsWith("group:")) setFilter("group", value.slice(6));
        else setFilter("module", value ? [value] : []);
      });
      for (const [selector, key] of [["[data-catalog-importance]", "importance"], ["[data-catalog-design]", "design"], ["[data-catalog-runner]", "runner"], ["[data-catalog-feature]", "feature"]]) {
        toolbar.querySelector(selector)?.addEventListener("change", event => setFilter(key, event.target.value));
      }
      toolbar.querySelectorAll("[data-catalog-aspect]").forEach(button => button.addEventListener("click", () => {
        const current = new Set(filters.aspect || []);
        current.has(button.dataset.catalogAspect) ? current.delete(button.dataset.catalogAspect) : current.add(button.dataset.catalogAspect);
        setFilter("aspect", [...current]);
      }));
      toolbar.querySelector("[data-catalog-live]").addEventListener("click", () => setFilter("live", !filters.live));
      toolbar.querySelector("[data-catalog-clear]").addEventListener("click", () => {
        filters = {};
        window.history.replaceState({}, "", T.catalogHref());
        root.querySelector("[data-catalog-search]").value = "";
        syncToolbar();
        void fetchPage();
      });
      root.querySelector("[data-catalog-list]").addEventListener("change", event => {
        const input = event.target.closest("[data-select-case]");
        if (!input) return;
        input.checked ? T.selection.add(input.dataset.selectCase) : T.selection.delete(input.dataset.selectCase);
        input.closest("[data-case-id]").dataset.selected = String(input.checked);
        syncSelection();
      });
    }

    async function load(addressFilters = {}) {
      await T.loadOverview();
      if (disposed) return null;
      filters = { ...addressFilters };
      root.innerHTML = window.PortalI18n.html`<article class="testCatalog">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("用例目录") }])}
        <header class="testListHead"><h1>用例目录</h1><span class="testListScope" data-catalog-scope></span><span class="testListCount" data-catalog-count></span></header>
        ${toolbarHtml()}
        <div class="testCaseList" data-catalog-list></div>
        <div class="testLoadMore" data-catalog-more></div>
        <div class="testSelectionHost" data-selection-host></div>
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      bind();
      syncToolbar();
      await fetchPage();
      return { count };
    }

    return {
      load,
      destroy() { disposed = true; revision += 1; window.clearTimeout(searchTimer); },
      snapshot: () => ({ filters: { ...filters }, count, caseIds: cases.map(item => item.id), loading, failure }),
    };
  }

  window.PortalTestCatalog = Object.freeze({ create });
})();
