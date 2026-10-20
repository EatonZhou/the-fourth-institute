(function () {
  "use strict";

  if (window.PortalSemanticSearch) return;

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);

  function element(value) {
    return typeof value === "string" ? document.getElementById(value) : value;
  }

  function create(options = {}) {
    const input = element(options.input);
    const button = element(options.button);
    const panel = element(options.panel);
    const identity = element(options.identity);
    const client = options.client || window.PortalAPI.createClient({ cache: "no-store" });
    const urls = options.urls || {};
    let jobs = [];
    let activeId = String(options.initialActiveId || "").trim();
    let starting = false;
    let loadedAt = 0;
    let destroyed = false;
    const projectionScope = String(options.projectionScope || "").trim();
    const resultIdField = ({
      knowledge: "kb_id",
      paper: "paper_id",
      wall: "item_id",
    })[projectionScope] || "resource_id";
    const labels = {
      idle: window.PortalI18n.t("AI 语义检索"),
      starting: window.PortalI18n.t("启动中…"),
      results: window.PortalI18n.t("条结果"),
      searching: window.PortalI18n.t("检索中"),
      error: window.PortalI18n.t("失败"),
      ...(options.labels || {}),
    };

    function activeJob() {
      return jobs.find(job => String(job.search_id || "") === activeId) || null;
    }

    function isSelectable(job) {
      return job?.status === "done" && Number(job.result_count || 0) > 0;
    }

    function statusLabel(job) {
      const status = String(job?.status || "queued");
      if (status === "done") return `${Number(job.result_count || 0)} ${labels.results}`;
      if (status === "error") return labels.error;
      return status === "running" ? labels.searching : status;
    }

    function renderIdentity() {
      if (!identity) return;
      const job = activeJob();
      identity.hidden = !isSelectable(job);
      if (!isSelectable(job)) {
        identity.replaceChildren();
        return;
      }
      window.PortalResourceHeader?.mount(identity, {
        title: window.PortalI18n.text`Search: ${String(job.query || "").trim() || window.PortalI18n.t("Semantic search")}`,
        type: "search",
        resource_id: String(job.search_id || ""),
        copy_label: "search_id",
      }, {
        compact: true,
        headingTag: "div",
        ...(options.identityOptions || {}),
      });
    }

    function render() {
      if (destroyed) return;
      renderIdentity();
      if (!panel || !button) return;
      panel.innerHTML = jobs.map(job => {
        const searchId = String(job.search_id || "");
        const status = String(job.status || "queued");
        const selectable = isSelectable(job);
        const meta = job.query_interpretation || job.error || job.step || "";
        return window.PortalI18n.html`
          <div class="portalSemanticItem${searchId === activeId ? " active" : ""}">
            <button class="portalSemanticMain" type="button" data-semantic-select="${esc(searchId)}"${selectable ? "" : " disabled"}>
              <span class="portalSemanticTop">
                <span class="portalSemanticQuery">${esc(job.query || "")}</span>
                <span class="portalSemanticStatus ${esc(status)}">${esc(statusLabel(job))}</span>
              </span>
              ${meta ? window.PortalI18n.html`<span class="portalSemanticMeta">${esc(meta)}</span>` : ""}
            </button>
            <span class="portalSemanticActions">
              <button class="portalSemanticAction" data-portal-icon="refresh" type="button" data-semantic-refresh="${esc(searchId)}" title="刷新语义搜索" aria-label="刷新语义搜索"></button>
              <button class="portalSemanticAction danger" data-portal-icon="close" type="button" data-semantic-delete="${esc(searchId)}" title="删除语义搜索" aria-label="删除语义搜索"></button>
            </span>
          </div>`;
      }).join("");
      button.disabled = starting || !!options.isDisabled?.();
      const label = button.querySelector("[data-semantic-label]");
      if (label) label.textContent = starting ? labels.starting : labels.idle;
      else button.textContent = starting ? labels.starting : labels.idle;
      options.onJobsChange?.([...jobs], activeId);
    }

    function notifyActive() {
      if (destroyed) return;
      options.onActiveChange?.(activeId, activeJob());
    }

    function setActiveId(value, { notify = true } = {}) {
      const requestedId = String(value || "").trim();
      const requestedJob = jobs.find(job => String(job.search_id || "") === requestedId);
      activeId = isSelectable(requestedJob) || (!loadedAt && !requestedJob) ? requestedId : "";
      render();
      if (notify) notifyActive();
    }

    function reportError(error) {
      if (destroyed) return;
      const message = window.PortalUI?.errorMessage?.(window.PortalI18n.t("语义搜索"), error) || String(error?.message || error);
      options.onError?.(error, message);
      if (!options.onError) window.PortalUI.toast(message, "error");
    }

    const coordinator = window.PortalRefreshCoordinator.create({
      key: () => projectionScope || urls.list,
      interval: 2000,
      fetch: ({ signal }) => client.get(urls.list, { signal }),
      apply(payload) {
        jobs = Array.isArray(payload?.searches) ? payload.searches : [];
        loadedAt = Date.now();
        if (activeId) {
          if (!isSelectable(activeJob())) activeId = "";
          notifyActive();
        }
        render();
      },
      subscribe: window.PortalRefreshCoordinator.portalSubscription({
        matches: semanticProjectionMatches,
        refreshOnAttach: false,
      }),
      onError: reportError,
    });

    function load({ fresh = false } = {}) {
      if (destroyed) return Promise.resolve({ searches: [] });
      coordinator.start();
      return coordinator.refresh({ force: fresh });
    }

    async function start() {
      if (destroyed || starting || options.isDisabled?.()) return;
      const query = String(input?.value || "").trim();
      if (!query) {
        window.PortalUI.toast(window.PortalI18n.t("请输入语义搜索内容。"), "warning");
        return;
      }
      starting = true;
      setActiveId("", { notify: true });
      options.onStart?.(query);
      render();
      try {
        const payload = { query, ...(options.startPayload?.() || {}) };
        const job = await client.post(urls.start, payload);
        if (destroyed) return;
        jobs = [job, ...jobs.filter(item => item.search_id !== job.search_id)];
        render();
      } finally {
        starting = false;
        render();
      }
    }

    async function refresh(searchId) {
      if (activeId === searchId) setActiveId("", { notify: true });
      const job = await client.post(urls.refresh, {
        search_id: searchId,
        ...(options.actionPayload?.() || {}),
      });
      if (destroyed) return;
      jobs = [job, ...jobs.filter(item => item.search_id !== job.search_id)];
      if (activeId === searchId) setActiveId("", { notify: true });
      render();
    }

    async function remove(searchId) {
      const job = jobs.find(item => String(item.search_id || "") === String(searchId || "")) || null;
      if (options.confirmRemove && !await options.confirmRemove(searchId, job)) return;
      await client.post(urls.delete, {
        search_id: searchId,
        ...(options.actionPayload?.() || {}),
      });
      if (destroyed) return;
      jobs = jobs.filter(item => item.search_id !== searchId);
      if (activeId === searchId) setActiveId("", { notify: true });
      render();
    }

    function semanticProjectionMatches(detail) {
      const changes = detail?.projection_changes?.["semantic-search"];
      if (!changes || typeof changes !== "object") return false;
      return Object.values(changes).some(fields => (
        !projectionScope
        || (Array.isArray(fields) && fields.includes(`scope:${projectionScope}`))
      ));
    }

    function resultMap() {
      const active = activeJob();
      if (!isSelectable(active)) return new Map();
      return new Map((active.results || []).map((result, index) => [String(
        result[resultIdField] || "",
      ), {
        ...result,
        rank: index + 1,
      }]).filter(([itemId]) => itemId));
    }

    function resultFor(itemId) {
      return resultMap().get(String(itemId || "")) || null;
    }

    function filterAndSort(items, itemId) {
      const results = resultMap();
      if (!results.size) return [...items];
      return items
        .filter(item => results.has(String(itemId(item) || "")))
        .sort((left, right) => {
          const leftRank = results.get(String(itemId(left) || ""))?.rank || 10_000;
          const rightRank = results.get(String(itemId(right) || ""))?.rank || 10_000;
          return leftRank - rightRank;
        });
    }

    const handlePanelClick = event => {
      const select = event.target.closest("[data-semantic-select]");
      if (select) {
        const searchId = select.dataset.semanticSelect || "";
        setActiveId(activeId === searchId ? "" : searchId, { notify: true });
        return;
      }
      const refreshButton = event.target.closest("[data-semantic-refresh]");
      if (refreshButton) {
        refresh(refreshButton.dataset.semanticRefresh || "").catch(reportError);
        return;
      }
      const deleteButton = event.target.closest("[data-semantic-delete]");
      if (deleteButton) remove(deleteButton.dataset.semanticDelete || "").catch(reportError);
    };
    const handleStartClick = () => start().catch(reportError);
    panel?.addEventListener("click", handlePanelClick);
    button?.addEventListener("click", handleStartClick);
    render();

    return {
      activeId: () => activeId,
      activeJob,
      clearActive: options => setActiveId("", options),
      destroy: () => {
        destroyed = true;
        panel?.removeEventListener("click", handlePanelClick);
        button?.removeEventListener("click", handleStartClick);
        coordinator.destroy();
      },
      filterAndSort,
      jobs: () => [...jobs],
      load,
      resultFor,
      setActiveId,
      start,
    };
  }

  window.PortalSemanticSearch = { create };
})();
