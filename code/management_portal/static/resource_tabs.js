(function () {
  "use strict";

  if (window.PortalResourceTabs) return;

  let ITEMS = Object.freeze(
    (window.PortalNav?.secondaryItems?.("resource") || [])
      .map(item => Object.freeze({ ...item })),
  );
  const KNOWLEDGE_TYPES = new Set(window.PortalResourceRoutes.routes
    .filter(route => route.detail_kind === "knowledge").map(route => route.resource_type));
  const ALL_FILTER_VALUE = "__all__";
  const roots = new Set();
  let popoverSequence = 0;
  let matrixRequest = null;
  const matrixState = {
    status: "idle",
    error: "",
    matrix: null,
    selection: selectionFromLocation(),
  };

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);

  function categoryFromLocation() {
    const match = window.location.pathname.match(/^\/resource\/([^/]+)/);
    const pathCategory = String(match?.[1] || "").toLowerCase();
    if (ITEMS.some(item => item.id === pathCategory)) return pathCategory;
    const queryCategory = String(new URLSearchParams(window.location.search).get("category") || "").toLowerCase();
    if (ITEMS.some(item => item.id === queryCategory)) return queryCategory;
    const address = String(new URLSearchParams(window.location.search).get("address") || "");
    const addressCategory = address.split("_", 1)[0].toLowerCase();
    if (KNOWLEDGE_TYPES.has(addressCategory)) return "knowledge";
    return ITEMS.some(item => item.id === addressCategory) ? addressCategory : "";
  }

  function selectionFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const rawType = String(params.get("type") || "").trim();
    const rawProject = String(params.get("project") || "").trim();
    return {
      type: rawType && rawType !== ALL_FILTER_VALUE ? rawType : "",
      projects: rawProject && rawProject !== ALL_FILTER_VALUE ? new Set([rawProject]) : null,
    };
  }

  function normalizeSelection(selection = {}) {
    const rawProjects = selection.projects;
    let projects = null;
    if (rawProjects instanceof Set) projects = new Set([...rawProjects].map(String).filter(Boolean));
    else if (Array.isArray(rawProjects)) projects = new Set(rawProjects.map(String).filter(Boolean));
    if (projects && !projects.size) projects = null;
    return {
      type: String(selection.type || "").trim(),
      projects,
    };
  }

  function normalizeMatrix(payload) {
    const items = (Array.isArray(payload?.items) ? payload.items : []).filter(item => (
      item && String(item.type || "").trim() && String(item.project || "").trim()
    ));
    const presentTypes = new Set(items.map(item => String(item.type).trim()));
    const configuredTypes = (Array.isArray(payload?.allowed_types) ? payload.allowed_types : [])
      .map(String)
      .filter(type => presentTypes.has(type));
    const types = [...configuredTypes];
    [...presentTypes].sort((left, right) => left.localeCompare(right)).forEach(type => {
      if (!types.includes(type)) types.push(type);
    });
    const projects = [...new Set(items.map(item => String(item.project).trim()))]
      .sort((left, right) => left.localeCompare(right));
    const counts = new Map();
    const typeTotals = new Map(types.map(type => [type, 0]));
    const projectTotals = new Map(projects.map(project => [project, 0]));
    items.forEach(item => {
      const type = String(item.type).trim();
      const project = String(item.project).trim();
      const key = `${type}\u0000${project}`;
      counts.set(key, (counts.get(key) || 0) + 1);
      typeTotals.set(type, (typeTotals.get(type) || 0) + 1);
      projectTotals.set(project, (projectTotals.get(project) || 0) + 1);
    });
    return { types, projects, counts, typeTotals, projectTotals, total: items.length };
  }

  function matrixCount(matrix, type, project) {
    return matrix.counts.get(`${type}\u0000${project}`) || 0;
  }

  function isTypeActive(type) {
    return matrixState.selection.type === type;
  }

  function isProjectActive(project) {
    return matrixState.selection.projects instanceof Set && matrixState.selection.projects.has(project);
  }

  function pressedAttribute(active) {
    return `aria-pressed="${active ? "true" : "false"}"`;
  }

  function filterButton({
    label,
    ariaLabel = label,
    type = "",
    project = "",
    count = 0,
    className = "",
    empty = false,
    active = false,
  }) {
    const countMarkup = count == null
      ? ""
      : window.PortalI18n.html`<span class="resourceKnowledgeMatrixCount">${count}</span>`;
    return window.PortalI18n.html`<button
      class="resourceKnowledgeMatrixButton${className ? ` ${className}` : ""}${active ? " is-active" : ""}${empty ? " is-empty" : ""}"
      type="button"
      data-knowledge-matrix-filter
      data-knowledge-type="${esc(type)}"
      data-knowledge-project="${esc(project)}"
      ${pressedAttribute(active)}
      aria-label="${esc(ariaLabel)}"
      data-portal-tooltip="${esc(ariaLabel)}"
    ><span class="resourceKnowledgeMatrixLabel">${esc(label)}</span>${countMarkup}</button>`;
  }

  function matrixMarkup(matrix) {
    const selection = matrixState.selection;
    const allActive = !selection.type && selection.projects === null;
    const header = matrix.types.map(type => {
      const count = matrix.typeTotals.get(type) || 0;
      return window.PortalI18n.html`<th scope="col">${filterButton({
        label: type,
        type,
        count,
        className: "resourceKnowledgeTypeButton",
        active: isTypeActive(type) && selection.projects === null,
      })}</th>`;
    }).join("");
    const rows = matrix.projects.map(project => {
      const rowHeader = filterButton({
        label: project,
        project,
        count: matrix.projectTotals.get(project) || 0,
        className: "resourceKnowledgeProjectButton",
        active: !selection.type && isProjectActive(project),
      });
      const cells = matrix.types.map(type => {
        const count = matrixCount(matrix, type, project);
        return window.PortalI18n.html`<td>${filterButton({
          label: `${project} · ${type} · ${count} items`,
          type,
          project,
          count,
          className: "resourceKnowledgeCellButton",
          empty: count === 0,
          active: isTypeActive(type) && isProjectActive(project),
        })}</td>`;
      }).join("");
      return window.PortalI18n.html`<tr><th scope="row">${rowHeader}</th>${cells}</tr>`;
    }).join("");
    const clearFilter = allActive ? "" : filterButton({
      label: window.PortalI18n.t("清除筛选"),
      ariaLabel: window.PortalI18n.t("全部 Knowledge"),
      count: null,
      className: "resourceKnowledgeAllButton portalButton is-compact is-quiet",
    });
    return window.PortalI18n.html`
      <div class="resourceKnowledgeMatrixHeading">
        <div>
          <strong>Knowledge 筛选</strong>
          <span>行：Project · 列：类型</span>
        </div>
        <div class="resourceKnowledgeMatrixHeadingActions">
          <span class="resourceKnowledgeMatrixTotal" aria-label="共 ${matrix.total} 条 Knowledge">
            共 <span>${matrix.total}</span> 条
          </span>
          ${clearFilter}
        </div>
      </div>
      <div class="resourceKnowledgeMatrixScroller">
        <table class="resourceKnowledgeMatrix" aria-label="以 Project 为行、知识类型为列筛选 Knowledge 条目">
          <thead><tr><th scope="col" class="resourceKnowledgeMatrixCorner">Project / 类型</th>${header}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function renderMatrix(root) {
    const content = root.querySelector("[data-resource-knowledge-matrix-content]");
    if (!content) return;
    if (matrixState.status === "loading") {
      content.innerHTML = window.PortalI18n.markup('<div class="resourceKnowledgeMatrixStatus">正在读取 Knowledge 分类…</div>');
      return;
    }
    if (matrixState.status === "error") {
      content.innerHTML = window.PortalI18n.html`<div class="resourceKnowledgeMatrixStatus resourceKnowledgeMatrixError">
        <span>${esc(matrixState.error || window.PortalI18n.t("Knowledge 分类读取失败"))}</span>
        <button type="button" data-knowledge-matrix-retry>重试</button>
      </div>`;
      return;
    }
    const matrix = matrixState.matrix;
    if (!matrix || !matrix.types.length || !matrix.projects.length) {
      content.innerHTML = window.PortalI18n.markup('<div class="resourceKnowledgeMatrixStatus">当前没有可用于筛选的 Knowledge 条目。</div>');
      return;
    }
    content.innerHTML = matrixMarkup(matrix);
  }

  function renderAllMatrices() {
    [...roots].forEach(root => {
      if (!root.isConnected) {
        roots.delete(root);
        return;
      }
      renderMatrix(root);
    });
  }

  async function ensureMatrixData({ refresh = false } = {}) {
    if (!refresh && matrixState.status === "ready" && matrixState.matrix) return matrixState.matrix;
    if (!refresh && matrixRequest) return matrixRequest;
    matrixState.status = "loading";
    matrixState.error = "";
    renderAllMatrices();
    matrixRequest = window.PortalAPI.get(
      "/knowledge/api/index?active_only=0&surface=resource-knowledge&offset=0&limit=5000",
    ).then(payload => {
      matrixState.matrix = normalizeMatrix(payload);
      matrixState.status = "ready";
      return matrixState.matrix;
    }).catch(error => {
      matrixState.status = "error";
      matrixState.error = String(error?.message || error || "Knowledge 分类读取失败");
      throw error;
    }).finally(() => {
      matrixRequest = null;
      renderAllMatrices();
    });
    return matrixRequest;
  }

  function knowledgeFilterUrl(type, project) {
    const url = new URL("/resource/knowledge", window.location.origin);
    url.searchParams.set("type", type || ALL_FILTER_VALUE);
    url.searchParams.set("project", project || ALL_FILTER_VALUE);
    return `${url.pathname}${url.search}`;
  }

  function requestKnowledgeFilter(type, project) {
    const detail = { type: String(type || ""), project: String(project || "") };
    const event = new CustomEvent("portal:knowledge-filter-request", {
      bubbles: false,
      cancelable: true,
      detail,
    });
    if (window.dispatchEvent(event)) window.location.assign(knowledgeFilterUrl(detail.type, detail.project));
  }

  function bindRoot(root, knowledgeLink, popover) {
    if (!knowledgeLink) return () => {};
    let openTimer = 0;
    let closeTimer = 0;

    const clearTimers = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      openTimer = 0;
      closeTimer = 0;
    };
    const popoverController = window.PortalOverlay.bindPopover({
      trigger: knowledgeLink,
      panel: popover,
      placement: "bottom-start",
      ariaHaspopup: "dialog",
      closeOnEscape: true,
      closeOnOutside: true,
      onOpen: () => {
        renderMatrix(root);
        ensureMatrixData().catch(() => {});
      },
    });
    const open = () => {
      clearTimers();
      popoverController.open();
    };
    const close = () => {
      clearTimers();
      popoverController.close();
    };
    const scheduleOpen = () => {
      window.clearTimeout(closeTimer);
      if (!popover.hidden) return;
      window.clearTimeout(openTimer);
      openTimer = window.setTimeout(open, 90);
    };
    const scheduleClose = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(close, 180);
    };

    knowledgeLink.addEventListener("mouseenter", scheduleOpen);
    knowledgeLink.addEventListener("mouseleave", scheduleClose);
    knowledgeLink.addEventListener("focus", open);
    popover.addEventListener("mouseenter", () => {
      window.clearTimeout(closeTimer);
    });
    popover.addEventListener("mouseleave", scheduleClose);
    root.querySelectorAll(".resourceCategoryLink:not([data-resource-category-link=\"knowledge\"])").forEach(link => {
      link.addEventListener("mouseenter", close);
      link.addEventListener("focus", close);
    });
    root.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!root.contains(document.activeElement)) close();
      }, 0);
    });
    popover.addEventListener("click", event => {
      const retry = event.target.closest("[data-knowledge-matrix-retry]");
      if (retry) {
        ensureMatrixData({ refresh: true }).catch(() => {});
        return;
      }
      const button = event.target.closest("[data-knowledge-matrix-filter]");
      if (!button) return;
      requestKnowledgeFilter(button.dataset.knowledgeType || "", button.dataset.knowledgeProject || "");
      close();
    });
    return () => {
      clearTimers();
      popoverController.destroy();
    };
  }

  function render(root) {
    if (!root) return;
    const requested = String(root.dataset.resourceCategory || "").trim().toLowerCase();
    const active = ITEMS.some(item => item.id === requested) ? requested : categoryFromLocation();
    if (root.dataset.resourceTabsRendered === "1" && root.dataset.resourceTabsActive === active) {
      roots.add(root);
      renderMatrix(root);
      return;
    }
    root._portalResourceTabsCleanup?.();
    const popoverId = `resourceKnowledgeMatrix-${++popoverSequence}`;
    root.classList.add("portalSectionShell", "resourceCategoryShell");
    root.dataset.resourceTabsRendered = "1";
    root.dataset.resourceTabsActive = active;
    root.innerHTML = window.PortalI18n.html`
      <div class="portalSectionViewport resourceCategoryViewport">
        <div class="portalSectionRow resourceCategoryRow">
        <nav class="portalSectionTabs resourceCategoryTabs" aria-label="Resource sections">
          ${ITEMS.map(item => window.PortalI18n.html`
            <a class="portalSectionLink resourceCategoryLink${item.id === active ? " active" : ""}"
               href="${esc(item.href)}"
               data-resource-category-link="${esc(item.id)}"
               ${item.id === "knowledge" ? `aria-haspopup="dialog" aria-expanded="false" aria-controls="${popoverId}"` : ""}
               ${item.id === active ? 'aria-current="page"' : ""}>${esc(item.label)}</a>
          `).join("")}
        </nav>
        <div class="resourceCategoryExtra" data-resource-tab-extra></div>
        </div>
      </div>
      <section class="resourceKnowledgeMatrixPopover" id="${popoverId}" role="dialog" aria-label="Knowledge 分类筛选" hidden>
        <div data-resource-knowledge-matrix-content></div>
      </section>`;
    roots.add(root);
    renderMatrix(root);
    const knowledgeLink = root.querySelector('[data-resource-category-link="knowledge"]');
    const popover = root.querySelector(".resourceKnowledgeMatrixPopover");
    root._portalResourceTabsCleanup = bindRoot(root, knowledgeLink, popover);
    const selected = root.querySelector('[aria-current="page"]');
    if (selected) window.requestAnimationFrame(() => selected.scrollIntoView({ block: "nearest", inline: "nearest" }));
  }

  function renderAll() {
    document.querySelectorAll("[data-resource-tabs]").forEach(render);
  }

  function setKnowledgeSelection(selection) {
    matrixState.selection = normalizeSelection(selection);
    renderAllMatrices();
  }

  window.PortalResourceTabs = {
    get items() { return ITEMS.slice(); },
    render,
    setKnowledgeSelection,
  };
  window.addEventListener("portal:resource-sections-changed", () => {
    ITEMS = window.PortalNav.secondaryItems("resource");
    for (const root of roots) {
      if (!root.isConnected) { roots.delete(root); continue; }
      // Preserve page-owned controls while rebuilding only the category links.
      const extra = [...(root.querySelector("[data-resource-tab-extra]")?.childNodes || [])];
      delete root.dataset.resourceTabsRendered;
      render(root);
      root.querySelector("[data-resource-tab-extra]").append(...extra);
    }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", renderAll, { once: true });
  else renderAll();
})();
