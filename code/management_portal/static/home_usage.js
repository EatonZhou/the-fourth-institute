(() => {
  if (window.PortalHomeUsage) return;

  // Home's usage attribution panel: token usage by Project, Lineage or Resident
  // over a range, its filters and the stored snapshot shown while it refreshes.
  const DEPENDENCIES = Object.freeze([
    "currentPortalUsername", "esc", "formatDateTime", "homeApi",
  ]);

  function createUsage(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeUsage is missing: ${missing.join(", ")}`);
    const {
      currentPortalUsername, esc, formatDateTime, homeApi,
    } = dependencies;

    const AGENT_STATUS_CACHE_HITS_KEY = window.PortalCompatibility.identifier("usage_cached_input");
    const AGENT_STATUS_GROUPING_KEY = window.PortalCompatibility.identifier("usage_grouping");
    // Usage grouping -> its payload fields, broadest first.
    const AGENT_STATUS_GROUPINGS = {
      project: {label: "Project", series: "project_chart_series", ranking: "projects"},
      lineage: {label: "Lineage", series: "lineage_chart_series", ranking: "lineages"},
      resident: {label: "Resident", series: "chart_series", ranking: "residents"},
    };
    let agentStatusGrouping = "lineage";
    let usageBackendFilter = "";
    let usageAccountFilter = "";
    const USAGE_FILTER_KEY = window.PortalCompatibility.identifier("usage_filters");
    let agentStatusChartRange = "today";
    let agentStatusIncludeCachedInput = false;
    let agentStatusDetailsOpen = false;
    let usageData = null;
    let usageRefresh = null;
    let usageError = "";
    let usageRequestPending = false;
    let usageRenderedSignature = "";
    const RESIDENT_USAGE_RANK_LIMIT = 20;
    const AGENT_STATUS_USAGE_RANGES = ["today", "week", "month", "year"];

    function formatTokens(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number) || number <= 0) return "0";
      if (number >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`;
      if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
      if (number >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
      return String(Math.round(number));
    }

    function agentStatusColorMap(keys) {
      const fixed = {
        unregistered: "#c0c6cf",
        other_residents: "#64748b",
      };
      const palette = [
        "#1e5eb8", "#e1475a", "#1eb838", "#8e47e1", "#b8921e",
        "#47dae1", "#b81e85", "#80e147", "#1e2bb8", "#e16747",
        "#1eb86b", "#c147e1", "#abb81e", "#47a7e1", "#b81e51",
        "#4de147", "#451eb8", "#e19b47", "#1eb89f", "#e147cd",
      ];
      const colors = new Map();
      let residentIndex = 0;
      for (const key of keys) {
        if (fixed[key]) {
          colors.set(key, fixed[key]);
        } else {
          colors.set(key, palette[residentIndex % palette.length]);
          residentIndex += 1;
        }
      }
      return colors;
    }

    function agentStatusColor(key, index, colorMap) {
      if (colorMap?.has(key)) return colorMap.get(key);
      const fallback = ["#1e5eb8", "#e1475a", "#1eb838", "#8e47e1", "#b8921e", "#47dae1"];
      return fallback[index % fallback.length];
    }

    function chartSegments(point) {
      return Array.isArray(point?.segments) ? point.segments : [];
    }

    function chartSeries(data) {
      const seriesByRange = data?.[AGENT_STATUS_GROUPINGS[agentStatusGrouping].series] || {};
      return Array.isArray(seriesByRange[agentStatusChartRange]) ? seriesByRange[agentStatusChartRange] : [];
    }

    function selectedTokenUsage(data) {
      const scopes = data?.scopes || [];
      const tokenUsage = usageBackendFilter || usageAccountFilter
        ? scopes.find(scope => scope.provider === usageBackendFilter && scope.account_id === (usageAccountFilter === "__unrecorded__" ? "" : usageAccountFilter || null)) || {status: "loading", windows: {}}
        : data || {};
      const variants = tokenUsage.variants || {};
      const key = agentStatusIncludeCachedInput ? "including_cached_input" : "excluding_cached_input";
      return {...(variants[key] || tokenUsage), account_history_incomplete: tokenUsage.account_history_incomplete};
    }

    function usageWindowValue(window, key) {
      const value = Number(window?.[key] || 0);
      return Number.isFinite(value) && value > 0 ? value : 0;
    }

    function usageWindowSummary(tokenUsage) {
      const windows = tokenUsage?.windows || {};
      return AGENT_STATUS_USAGE_RANGES.map(range => {
        const usageWindow = windows[range] || {};
        const total = usageWindowValue(usageWindow, "total_tokens");
        const label = range[0].toUpperCase() + range.slice(1);
        return window.PortalI18n.html`
      <div class="agent-status-usage-pill">
        <strong>${esc(label)} ${Number.isFinite(usageWindow.total_tokens) ? formatTokens(total) : "--"}</strong>
      </div>
    `;
      }).join("");
    }

    function usageChartNote(tokenUsage) {
      if (tokenUsage?.status === "error") {
        return "Usage unavailable.";
      }
      if (tokenUsage?.status === "partial") {
        return "Usage data incomplete.";
      }
      if (tokenUsage?.status === "loading") {
        return "Loading usage…";
      }
      return "";
    }

    function renderUsageAttribution() {
      const node = document.querySelector('[data-home-component="usage-attribution"]');
      if (!node) return;
      const data = usageData || {};
      const statusDetails = node.querySelector(".agent-status-details");
      if (statusDetails) agentStatusDetailsOpen = statusDetails.open;
      const componentHosts = {"usage-attribution": node};
      const backends = data.filters?.backends || [];
      if (data.filters && usageBackendFilter && !backends.includes(usageBackendFilter)) {
        usageBackendFilter = "";
        usageAccountFilter = "";
      }
      const accounts = (data.filters?.accounts || []).filter(item => !usageBackendFilter || item.provider === usageBackendFilter);
      const hasUnknown = (data.scopes || []).some(item => item.provider === usageBackendFilter && item.account_id === "");
      if (data.filters && usageAccountFilter && !(usageAccountFilter === "__unrecorded__" ? hasUnknown : accounts.some(item => item.account_id === usageAccountFilter))) usageAccountFilter = "";
      const accountOption = account => window.PortalI18n.html`<option value="${esc(usageBackendFilter ? account.account_id : JSON.stringify([account.provider, account.account_id]))}" ${usageAccountFilter === account.account_id ? "selected" : ""}>${esc(account.account_id)}</option>`;
      const tokenUsage = selectedTokenUsage(data);
      const windows = tokenUsage.windows || {};
      const ranking = tokenUsage[AGENT_STATUS_GROUPINGS[agentStatusGrouping].ranking];
      const residents = Array.isArray(ranking) ? ranking : [];
      const groupingLabel = AGENT_STATUS_GROUPINGS[agentStatusGrouping].label;
      const visibleSeries = chartSeries(tokenUsage);
      const legendSource = visibleSeries.flatMap(item => chartSegments(item));
      const legendMap = new Map();
      for (const segment of legendSource) {
        if (!legendMap.has(segment.key)) legendMap.set(segment.key, segment.label);
      }
      const colorMap = agentStatusColorMap([...legendMap.keys()]);
      const maxTokens = Math.max(1, ...visibleSeries.map(item => Number(item.tokens || 0)));
      const bars = visibleSeries.map(item => {
        const tokens = Number(item.tokens || 0);
        const height = Math.max(2, Math.round((tokens / maxTokens) * 78));
        const segments = chartSegments(item);
        const totalForSegments = Math.max(1, tokens);
        const segmentHtml = segments.map((segment, index) => {
          const value = Number(segment.tokens || 0);
          const pct = Math.max(0, Math.min(100, (value / totalForSegments) * 100));
          return window.PortalI18n.html`<span class="agent-status-bar-assigned agent-status-segment-color" style="height:${pct}%; --agent-status-segment-color:${agentStatusColor(segment.key, index, colorMap)}"></span>`;
        }).join("");
        const tooltip = [
          item.label || item.date,
          window.PortalI18n.text`Total ${formatTokens(tokens)}`,
          ...segments.map(segment => `${segment.label || segment.key} ${formatTokens(segment.tokens)}`),
        ].filter(Boolean).join(" · ");
        return window.PortalI18n.html`<div class="agent-status-bar" data-agent-chart-bar data-portal-tooltip="${esc(tooltip)}" role="img" tabindex="0" aria-label="${esc(tooltip)}" style="height:${height}px">${segmentHtml}</div>`;
      }).join("");
      const detailRange = AGENT_STATUS_USAGE_RANGES.includes(agentStatusChartRange)
        ? agentStatusChartRange
        : "today";
      const detailRangeLabel = detailRange[0].toUpperCase() + detailRange.slice(1);
      const topResidents = [...residents]
        .filter(item => Number(item.periods?.[detailRange] || 0) > 0)
        .sort((left, right) => (
          Number(right.periods?.[detailRange] || 0)
          - Number(left.periods?.[detailRange] || 0)
        ))
        .slice(0, RESIDENT_USAGE_RANK_LIMIT);
      const maxResident = Math.max(1, ...topResidents.map(item => Number(item.periods?.[detailRange] || 0)));
      const residentRows = topResidents.length ? topResidents.map(item => {
        const tokens = Number(item.periods?.[detailRange] || 0);
        const width = Math.max(2, Math.round((tokens / maxResident) * 100));
        return window.PortalI18n.html`
      <div class="agent-status-row">
        <span class="agent-status-row-name" title="${esc(item.title || item.project_id || item.lineage_id || item.resident_id)}">${esc(item.title || item.project_id || item.lineage_id || item.resident_id)}</span>
        <span class="agent-status-row-value">${formatTokens(tokens)}</span>
      </div>
      <div class="agent-status-progress" aria-hidden="true"><span style="width:${width}%"></span></div>
    `;
      }).join("") : window.PortalI18n.html`<div class="agent-status-muted">No ${groupingLabel.toLowerCase()} token data yet.</div>`;
      const detailWindow = windows[detailRange] || {};
      const detailTotal = usageWindowValue(detailWindow, "total_tokens");
      const detailSummary = window.PortalI18n.text`Total ${formatTokens(detailTotal)}`;
      // A partial summary names the accounts whose native usage could not be
      // read; everything else in it is current.
      const statusNote = tokenUsage?.status === "partial" && data.warning ? data.warning : usageChartNote(tokenUsage);
      const legend = [...legendMap.entries()].map(([key, label], index) => window.PortalI18n.html`
    <span class="agent-status-legend-item"><span class="agent-status-swatch agent-status-segment-color" style="--agent-status-segment-color:${agentStatusColor(key, index, colorMap)}"></span><span class="agent-status-legend-label">${esc(label)}</span></span>
  `).join("");
      componentHosts["usage-attribution"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-section">
        <div class="agent-status-head">
          <div class="agent-status-usage-controls">
            <label class="agent-status-usage-filter">Backend
              <select data-usage-backend aria-label="AI Backend">
                <option value="">全部 Backend</option>
                ${backends.map(backend => window.PortalI18n.html`<option value="${esc(backend)}" ${usageBackendFilter === backend ? "selected" : ""}>${esc(backend)}</option>`).join("")}
              </select>
            </label>
            <label class="agent-status-usage-filter">账号
              <select data-usage-account aria-label="用量账号">
                <option value="">全部账号</option>
                ${usageBackendFilter ? accounts.map(accountOption).join("") : backends.map(backend => window.PortalI18n.html`<optgroup label="${esc(backend)}">${accounts.filter(account => account.provider === backend).map(accountOption).join("")}</optgroup>`).join("")}
                ${hasUnknown ? window.PortalI18n.html`<option value="__unrecorded__" ${usageAccountFilter === "__unrecorded__" ? "selected" : ""}>未记录账号</option>` : ""}
              </select>
            </label>
            <label class="agent-status-cache-toggle">
              <input type="checkbox" data-agent-status-cache-hits ${agentStatusIncludeCachedInput ? "checked" : ""} />
              <span>Include cache</span>
            </label>
            <div class="agent-status-tabs" role="tablist" aria-label="Usage time range">
              ${AGENT_STATUS_USAGE_RANGES.map(range => window.PortalI18n.html`<button class="agent-status-tab ${agentStatusChartRange === range ? "active" : ""}" type="button" data-agent-status-range="${range}">${range[0].toUpperCase()}${range.slice(1)}</button>`).join("")}
            </div>
            <div class="agent-status-tabs" role="group" aria-label="Usage grouping">
              ${Object.entries(AGENT_STATUS_GROUPINGS).map(([group, grouping]) => window.PortalI18n.html`<button class="agent-status-tab ${agentStatusGrouping === group ? "active" : ""}" type="button" data-agent-status-grouping="${group}" aria-pressed="${agentStatusGrouping === group}">${grouping.label}</button>`).join("")}
            </div>
          </div>
          <button class="portalButton agent-status-refresh" type="button" data-usage-refresh data-portal-icon="refresh" title="刷新用量"><span data-usage-refresh-label>刷新</span></button>
        </div>
        <div class="agent-status-usage-summary">${usageWindowSummary(tokenUsage)}</div>
        <div class="agent-status-chart" style="grid-template-columns:repeat(${Math.max(1, visibleSeries.length)}, minmax(10px, 1fr))" aria-label="Model token usage">${bars || ""}</div>
        <div class="agent-status-legend">${legend}</div>
        ${statusNote ? window.PortalI18n.html`<div class="agent-status-muted" role="status">${esc(statusNote)}</div>` : ""}
        ${tokenUsage.account_history_incomplete ? window.PortalI18n.html`<div class="agent-status-muted" data-usage-history-note>部分历史用量未记录账号，可在“未记录账号”中查看。</div>` : ""}
        ${tokenUsage.account_totals_omitted ? window.PortalI18n.html`<div class="agent-status-muted" data-usage-cache-note>账号自报的日用量含缓存且无法拆分，此视图不计入；勾选 Include cache 后计入。</div>` : ""}
      </div>
      ${usageError || data.refresh_error ? window.PortalI18n.html`<div class="agent-status-error" role="status">Usage refresh failed: ${esc(usageError || data.refresh_error)}</div>` : ""}
      ${data.manual_refresh_skipped ? window.PortalI18n.html`<div class="agent-status-muted">Please retry in ${Math.ceil(data.manual_refresh_retry_after_seconds / 60)} minutes.</div>` : ""}
      <details class="agent-status-details"${agentStatusDetailsOpen ? " open" : ""}>
        <summary><span>Details</span><span data-portal-icon="chevron-down" data-portal-icon-class="statusDisclosureIcon" class="statusDisclosureIcon" aria-hidden="true"></span></summary>
        <div class="agent-status-section">
          <div class="agent-status-section-title">${groupingLabel} attributed usage · ${esc(detailRangeLabel)}</div>
          <div class="agent-status-rows">${residentRows}</div>
        </div>
        <div class="agent-status-muted">${esc(detailSummary)}</div>
        <div class="agent-status-muted">Updated ${esc(formatDateTime(data?.updated_at) || window.PortalI18n.t("just now"))}</div>
      </details>
  `;
      for (const button of node.querySelectorAll("[data-agent-status-range]")) {
        button.addEventListener("click", () => {
          agentStatusChartRange = button.getAttribute("data-agent-status-range") || "week";
          renderUsageAttribution();
        });
      }
      for (const button of node.querySelectorAll("[data-agent-status-grouping]")) {
        button.addEventListener("click", () => {
          agentStatusGrouping = button.getAttribute("data-agent-status-grouping");
          window.PortalState.storage.writeText(
            `${AGENT_STATUS_GROUPING_KEY}:${currentPortalUsername()}`,
            agentStatusGrouping,
          );
          renderUsageAttribution();
        });
      }
      const cacheHitsInput = node.querySelector("[data-agent-status-cache-hits]");
      if (cacheHitsInput) {
        cacheHitsInput.addEventListener("change", () => {
          agentStatusIncludeCachedInput = Boolean(cacheHitsInput.checked);
          window.PortalState.storage.writeText(
            `${AGENT_STATUS_CACHE_HITS_KEY}:${currentPortalUsername()}`,
            agentStatusIncludeCachedInput ? "1" : "0",
          );
          renderUsageAttribution();
        });
      }
      const nextDetails = node.querySelector(".agent-status-details");
      if (nextDetails) {
        nextDetails.addEventListener("toggle", () => {
          agentStatusDetailsOpen = nextDetails.open;
        });
      }

      const saveFilters = () => {
        window.PortalState.storage.writeText(`${USAGE_FILTER_KEY}:${currentPortalUsername()}`, JSON.stringify({backend: usageBackendFilter, account: usageAccountFilter}));
        renderUsageAttribution();
      };
      node.querySelector("[data-usage-backend]").addEventListener("change", event => {
        usageBackendFilter = event.target.value;
        usageAccountFilter = "";
        saveFilters();
      });
      node.querySelector("[data-usage-account]").addEventListener("change", event => {
        const value = event.target.value;
        if (!usageBackendFilter && value) {
          [usageBackendFilter, usageAccountFilter] = JSON.parse(value);
        } else {
          usageAccountFilter = value;
        }
        saveFilters();
      });
      node.querySelector("[data-usage-refresh]").addEventListener("click", () => loadUsageAttribution(true));
      updateUsageRefreshButton();
      window.PortalButtonTooltips?.enhance?.(node);
    }

    function updateUsageRefreshButton() {
      const button = document.querySelector("[data-usage-refresh]");
      if (!button) return;
      const busy = usageRequestPending || (!usageError && usageData?.refreshing === true);
      button.disabled = busy;
      button.classList.toggle("is-refreshing", busy);
      button.setAttribute("aria-busy", String(busy));
      button.querySelector("[data-usage-refresh-label]").textContent = busy ? window.PortalI18n.t("刷新中…") : window.PortalI18n.t("刷新");
    }

    function hasUsageSnapshot(data) {
      return Object.values(data?.windows || {}).some(item => Number.isFinite(item?.total_tokens));
    }

    function storedUsageSnapshot(actor, snapshot) {
      // Usage scopes exceed localStorage quotas; keep one IndexedDB snapshot per user.
      return new Promise(resolve => {
        let request;
        try { request = window.indexedDB.open(window.PortalCompatibility.identifier("usage_database"), 1); }
        catch { resolve(null); return; }
        request.onupgradeneeded = () => request.result.createObjectStore("snapshots");
        request.onerror = request.onblocked = () => resolve(null);
        request.onsuccess = () => {
          const database = request.result;
          try {
            const transaction = database.transaction("snapshots", snapshot ? "readwrite" : "readonly");
            const store = transaction.objectStore("snapshots");
            const operation = snapshot ? store.put(snapshot, actor) : store.get(actor);
            transaction.oncomplete = () => { database.close(); resolve(operation.result || null); };
            transaction.onerror = transaction.onabort = () => { database.close(); resolve(null); };
          } catch { database.close(); resolve(null); }
        };
      });
    }

    function acceptUsageSnapshot(data) {
      const version = data?.snapshot_version;
      if (!Number.isSafeInteger(version) || version < 0) throw new Error("Invalid usage snapshot version");
      if (!hasUsageSnapshot(data) && hasUsageSnapshot(usageData)) {
        usageData = {...usageData, refreshing: data.refreshing === true};
        usageError = data.refresh_error || (data.status === "error" ? "Usage unavailable." : "");
        renderUsageAttribution();
        updateUsageRefreshButton();
        return;
      }
      if (usageData && version < usageData.snapshot_version) return;
      const previousVersion = usageData?.snapshot_version;
      const hadError = Boolean(usageError);
      usageData = data;
      usageError = "";
      const signature = JSON.stringify(data);
      if (hadError || signature !== usageRenderedSignature) {
        usageRenderedSignature = signature;
        renderUsageAttribution();
      }
      updateUsageRefreshButton();
      if (hasUsageSnapshot(data) && version !== previousVersion) {
        void storedUsageSnapshot(currentPortalUsername(), {...data, refreshing: false, refresh_error: ""});
      }
    }

    function ensureUsageRefresh() {
      if (usageRefresh) return usageRefresh;
      usageRefresh = window.PortalRefreshCoordinator.create({
        key: () => `home-token-usage:${currentPortalUsername() || "anonymous"}`,
        interval: () => !usageError && (usageData?.refreshing || usageData?.status === "loading") ? 3000 : 300000,
        hiddenInterval: 300000,
        pauseWhenHidden: false,
        initialDelay: 300000,
        fetch: ({force = false, signal} = {}) => force
          ? homeApi.request("/api/agent-status/usage/refresh", {method: "POST", signal})
          : homeApi.get("/api/agent-status/usage", {signal}),
        apply: acceptUsageSnapshot,
        onError: error => {
          usageError = error?.message || String(error);
          renderUsageAttribution();
        },
      });
      return usageRefresh;
    }

    async function loadUsageAttribution(force = false) {
      const coordinator = ensureUsageRefresh();
      coordinator.start();
      usageRequestPending = true;
      updateUsageRefreshButton();
      try {
        await coordinator.refresh({force});
      } catch {
      } finally {
        usageRequestPending = false;
        updateUsageRefreshButton();
      }
    }

    function connectUsageAttribution({context, signal}) {
      context.currentUserReady.catch(() => {}).then(() => {
        if (!signal.aborted) {
          try {
            const filters = JSON.parse(window.PortalState.storage.readText(`${USAGE_FILTER_KEY}:${currentPortalUsername()}`, "{}"));
            usageBackendFilter = typeof filters.backend === "string" ? filters.backend : "";
            usageAccountFilter = typeof filters.account === "string" ? filters.account : "";
          } catch { usageBackendFilter = ""; usageAccountFilter = ""; }
          const storedGrouping = window.PortalState.storage.readText(
            `${AGENT_STATUS_GROUPING_KEY}:${currentPortalUsername()}`, "lineage",
          );
          agentStatusGrouping = Object.hasOwn(AGENT_STATUS_GROUPINGS, storedGrouping) ? storedGrouping : "lineage";
          agentStatusIncludeCachedInput = window.PortalState.storage.readText(
            `${AGENT_STATUS_CACHE_HITS_KEY}:${currentPortalUsername()}`, "",
          ) === "1";
          renderUsageAttribution();
          const actor = currentPortalUsername();
          void storedUsageSnapshot(actor).then(snapshot => {
            if (signal.aborted || currentPortalUsername() !== actor || hasUsageSnapshot(usageData)
              || !hasUsageSnapshot(snapshot) || !Number.isSafeInteger(snapshot.snapshot_version)) return;
            usageData = {...snapshot, refreshing: usageRequestPending || usageData?.refreshing === true};
            renderUsageAttribution();
            updateUsageRefreshButton();
          });
          loadUsageAttribution();
        }
      });
      return () => {
        usageRefresh?.destroy?.();
        usageRefresh = null;
        usageData = null;
        usageError = "";
        usageRequestPending = false;
        usageRenderedSignature = "";
      };
    }

    return Object.freeze({
      connectUsageAttribution,
    });
  }

  window.PortalHomeUsage = Object.freeze({ createUsage, dependencies: DEPENDENCIES });
})();
