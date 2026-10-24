(() => {
  "use strict";

  if (window.PortalHatgGraphSearch) return;

  // Finding objects on the HATG canvas: local matches by identity and text,
  // the server-side smart search with its deep rerank, and the results popover.
  const DEPENDENCIES = Object.freeze([
    "$id", "$query", "activeSeeds", "currentPanelActive", "esc", "focusGraphNode",
    "nodeResidentId", "residentDiscovery", "state", "toast", "workspaceGraphModel",
  ]);



  function createGraphSearch(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphSearch is missing: ${missing.join(", ")}`);
    const {
      $id, $query, activeSeeds, currentPanelActive, esc, focusGraphNode, nodeResidentId,
      residentDiscovery, state, toast, workspaceGraphModel,
    } = dependencies;

    let graphNodeSearchPopover = null;

    let graphNodeSearchRenderFrame = 0;

    let graphSmartSearchTimer = 0;

    let graphSmartSearchRequest = 0;

    let graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };

    const graphSearchTextCache = new WeakMap();

    function localGraphSearchMatches(query) {
      const normalized = String(query || "").trim().toLocaleLowerCase();
      if (!normalized) return [];
      if (state.graphViewMode === "workspace") {
        return workspaceGraphModel().overviewNodes.filter(node => (
          [node.title, node.workspace_path, node.workspace_id, node.resident_id]
            .some(value => String(value || "").toLocaleLowerCase().includes(normalized))
        )).slice(0, 8);
      }
      const matches = [];
      const addMatches = (nodes, kind) => {
        for (const node of nodes) {
          const values = [
            node.title,
            node.display_title,
            node.summary,
            node.artifact_path,
            nodeResidentId(node),
          ];
          const signature = values.map(value => String(value || "")).join("\u0000");
          const cached = graphSearchTextCache.get(node);
          const text = cached?.signature === signature
            ? cached.text
            : signature.toLocaleLowerCase();
          if (cached?.signature !== signature) graphSearchTextCache.set(node, { signature, text });
          if (!text.includes(normalized)) continue;
          matches.push({ ...node, kind });
          if (matches.length >= 8) return true;
        }
        return false;
      };
      if (addMatches(state.lineage?.nodes || [], "agent")) return matches;
      addMatches(activeSeeds(), "seed");
      return matches;
    }

    function graphSearchIdentityMatch(query) {
      const normalized = String(query || "").trim().toLocaleLowerCase();
      if (!normalized.startsWith("resident_")) return null;
      const candidates = (state.lineage?.nodes || []).map(node => ({
        node,
        residentId: nodeResidentId(node).toLocaleLowerCase(),
      })).filter(candidate => candidate.residentId);
      const exact = candidates.find(candidate => candidate.residentId === normalized);
      if (exact) return { ...exact.node, kind: "agent" };
      const prefixMatches = candidates.filter(candidate => candidate.residentId.startsWith(normalized));
      return prefixMatches.length === 1
        ? { ...prefixMatches[0].node, kind: "agent" }
        : null;
    }

    function graphSearchMatches(query) {
      const local = localGraphSearchMatches(query);
      if (state.graphViewMode === "workspace") return local;
      const identity = graphSearchIdentityMatch(query);
      if (String(graphSmartSearch.query || "") !== String(query || "").trim()) {
        return identity ? [identity, ...local.filter(node => node.id !== identity.id)] : local;
      }
      const nodesByResidentId = new Map(
        (state.lineage?.nodes || []).map(node => [nodeResidentId(node), node]),
      );
      const semantic = (graphSmartSearch.results || []).map(result => {
        const residentId = String(result.resident_id || "");
        const node = nodesByResidentId.get(residentId);
        const sources = Array.isArray(result.retrieval_sources) ? result.retrieval_sources : [];
        const matchKind = sources.includes("vector") ? "向量匹配" : sources.includes("lexical") ? "文字匹配" : "相关匹配";
        const focus = result.reason || result.summary?.current_focus || "";
        return node ? { ...node, kind: "agent", smartReason: focus ? `${matchKind} · ${focus}` : matchKind } : null;
      }).filter(Boolean);
      const semanticReady = !graphSmartSearch.loading && semantic.length > 0;
      const ordered = [
        ...(identity ? [identity] : []),
        ...(semanticReady ? semantic : []),
        ...local,
      ];
      const seen = new Set();
      return ordered.filter(node => {
        if (seen.has(node.id)) return false;
        seen.add(node.id);
        return true;
      }).slice(0, 8);
    }

    function graphSearchResidentIds() {
      return [...new Set((state.lineage?.nodes || []).map(nodeResidentId).filter(Boolean))];
    }

    function setGraphDeepRerankBusy(busy) {
      const button = $id("graphDeepRerankBtn");
      if (!button) return;
      button.disabled = Boolean(busy);
      button.setAttribute("aria-busy", busy ? "true" : "false");
      button.textContent = busy ? window.PortalI18n.t("深度中…") : window.PortalI18n.t("深度");
    }

    async function runGraphSmartSearch(query, { deep = false } = {}) {
      if (state.graphViewMode === "workspace") {
        graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };
        renderGraphNodeSearchResults();
        return;
      }
      const clean = String(query || "").trim();
      if (clean.length < 2) {
        graphSmartSearch = { query: clean, loading: false, mode: "", results: [], error: "" };
        setGraphDeepRerankBusy(false);
        renderGraphNodeSearchResults();
        return;
      }
      const requestId = ++graphSmartSearchRequest;
      const mode = deep ? "deep" : "search";
      graphSmartSearch = { query: clean, loading: true, mode, results: [], error: "" };
      setGraphDeepRerankBusy(deep);
      renderGraphNodeSearchResults();
      let autoFocusNodeId = "";
      const residentIds = graphSearchResidentIds();
      if (!residentIds.length) {
        graphSmartSearch = { query: clean, loading: false, mode, results: [], error: "" };
        setGraphDeepRerankBusy(false);
        renderGraphNodeSearchResults();
        return;
      }
      try {
        const result = deep
          ? await residentDiscovery.recommend({
              query: clean,
              limit: 8,
              resident_ids: residentIds,
              allow_fallback: false,
            })
          : await residentDiscovery.search({
              query: clean,
              limit: 8,
              resident_ids: residentIds,
            });
        if (requestId !== graphSmartSearchRequest) return;
        if (!result) return;
        graphSmartSearch = {
          query: clean,
          loading: false,
          mode,
          results: deep ? result.recommendations || [] : result.results || [],
          error: "",
        };
        if (deep) autoFocusNodeId = graphSearchMatches(clean)[0]?.id || "";
        if (deep && result.fallback) toast(window.PortalI18n.t("深度判断不可用，已保留常规搜索排序。"), "warning");
      } catch (error) {
        if (requestId !== graphSmartSearchRequest) return;
        graphSmartSearch = { query: clean, loading: false, mode, results: [], error: error.message || String(error) };
      } finally {
        if (requestId === graphSmartSearchRequest) setGraphDeepRerankBusy(false);
      }
      renderGraphNodeSearchResults();
      if (autoFocusNodeId) await focusGraphNode(autoFocusNodeId);
    }

    function scheduleGraphSmartSearch(query) {
      window.clearTimeout(graphSmartSearchTimer);
      const clean = String(query || "").trim();
      ++graphSmartSearchRequest;
      if (clean.length < 2) {
        graphSmartSearch = { query: clean, loading: false, mode: "", results: [], error: "" };
        setGraphDeepRerankBusy(false);
        return;
      }
      graphSmartSearch = { query: clean, loading: true, mode: "search", results: [], error: "" };
      graphSmartSearchTimer = window.setTimeout(() => runGraphSmartSearch(clean), 320);
    }

    function runGraphDeepRerank() {
      window.clearTimeout(graphSmartSearchTimer);
      const query = $id("graphNodeSearchInput").value;
      if (String(query || "").trim().length < 2) {
        toast(window.PortalI18n.t("先输入至少两个字，再进行深度重排。"), "warning");
        return;
      }
      runGraphSmartSearch(query, { deep: true });
    }

    function renderGraphNodeSearchResults() {
      // The popover owns these elements even while its panel is mounted in body.
      // Querying by ID would cross runtime boundaries when several HATGs are open.
      const input = graphNodeSearchPopover?.trigger;
      const root = graphNodeSearchPopover?.element;
      if (!input || !root || !currentPanelActive()) return;
      const matches = graphSearchMatches(input.value);
      if (!input.value.trim()) {
        graphNodeSearchPopover?.close();
        root.innerHTML = "";
        return;
      }
      const loadingText = graphSmartSearch.mode === "deep"
        ? "正在深度重排当前 Lineage 节点…"
        : "正在搜索当前 Lineage 节点…";
      const content = matches.length
        ? `${matches.map(node => window.PortalI18n.html`<button type="button" data-graph-search-node="${esc(node.id)}"><strong>${esc(node.display_title || node.title)}</strong><small>${esc(node.kind === "workspace" ? `${node.workspace_id} · ${node.workspace_path}` : node.kind === "seed" ? window.PortalI18n.text`种子 · ${node.artifact_path}` : node.smartReason || nodeResidentId(node))}</small></button>`).join("")}${graphSmartSearch.loading ? window.PortalI18n.html`<div class="graphSearchEmpty">${loadingText}</div>` : ""}`
        : window.PortalI18n.html`<div class="graphSearchEmpty">${graphSmartSearch.loading ? loadingText : graphSmartSearch.error ? window.PortalI18n.t("智能搜索暂时不可用") : window.PortalI18n.t("没有匹配节点")}</div>`;
      root.innerHTML = window.PortalI18n.html`<div class="graphNodeSearchResultList">${content}</div>`;
      graphNodeSearchPopover.open();
    }

    function scheduleGraphNodeSearchResultsRender() {
      if (graphNodeSearchRenderFrame) return;
      graphNodeSearchRenderFrame = window.requestAnimationFrame(() => {
        graphNodeSearchRenderFrame = 0;
        renderGraphNodeSearchResults();
      });
    }

    function handleGraphNodeSearchInput(event) {
      scheduleGraphSmartSearch(event.currentTarget.value);
      scheduleGraphNodeSearchResultsRender();
    }

    function cancelGraphNodeSearch() {
      window.clearTimeout(graphSmartSearchTimer);
      if (graphNodeSearchRenderFrame) window.cancelAnimationFrame(graphNodeSearchRenderFrame);
      graphNodeSearchRenderFrame = 0;
      ++graphSmartSearchRequest;
      graphSmartSearch.loading = false;
      setGraphDeepRerankBusy(false);
    }

    function clearGraphNodeSearch() {
      cancelGraphNodeSearch();
      graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };
      const input = graphNodeSearchPopover?.trigger;
      const results = graphNodeSearchPopover?.element;
      if (input) input.value = "";
      if (results) results.innerHTML = "";
      graphNodeSearchPopover?.close();
    }

    // The canvas search box: its results popover, input, result clicks and Enter.
    function bindGraphNodeSearch() {
      graphNodeSearchPopover = window.PortalOverlay.bindPopover({
        trigger: $id("graphNodeSearchInput"),
        anchor: $query(".graphNodeSearch"),
        panel: $id("graphNodeSearchResults"),
        portal: true,
        placement: "bottom-end",
        ariaHaspopup: "listbox",
        closeOnOutside: true,
        closeOnEscape: true,
        onClose: cancelGraphNodeSearch,
      });
      $id("graphNodeSearchInput").addEventListener("input", handleGraphNodeSearchInput);
      $id("graphNodeSearchResults").addEventListener("click", event => {
        const button = event.target.closest("[data-graph-search-node]");
        if (!button) return;
        focusGraphNode(button.dataset.graphSearchNode || "");
      });
      $id("graphDeepRerankBtn").addEventListener("click", runGraphDeepRerank);
      $id("graphNodeSearchInput").addEventListener("keydown", event => {
        if (event.key !== "Enter") return;
        if (graphSmartSearch.loading) {
          event.preventDefault();
          return;
        }
        const first = graphSearchMatches(event.currentTarget.value)[0];
        if (!first) return;
        event.preventDefault();
        focusGraphNode(first.id);
      });
    }

    // A redrawn canvas refreshes the open results, whose nodes may have changed.
    function refreshGraphNodeSearchResults() {
      if (graphNodeSearchPopover?.isOpen()) renderGraphNodeSearchResults();
    }

    function destroyGraphNodeSearch() {
      clearGraphNodeSearch();
      graphNodeSearchPopover?.destroy();
      graphNodeSearchPopover = null;
    }

    return Object.freeze({
      clearGraphNodeSearch, bindGraphNodeSearch, refreshGraphNodeSearchResults,
      destroyGraphNodeSearch,
    });
  }

  window.PortalHatgGraphSearch = Object.freeze({
    createGraphSearch, dependencies: DEPENDENCIES,
  });
})();
