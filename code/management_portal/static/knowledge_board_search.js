// Knowledge board: the local query, exact Knowledge ID lookup and semantic search.

function requestedSemanticSearchId() {
  const params = new URLSearchParams(window.location.search);
  return String(params.get("semantic") || params.get("search") || "").trim();
}

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = `${item.title || ""}\n${item.preview || ""}\n${item.kb_id || ""}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

function looksLikeKnowledgeId(value) {
  return /^[a-z][a-z0-9-]*_[0-9a-f]+$/i.test(String(value || "").trim());
}

async function resolveExactKnowledgeQuery(value) {
  const query = String(value || "").trim();
  const seq = state.exactQuery.seq + 1;
  state.exactQuery = { seq, id: query, status: "", error: "" };
  if (!looksLikeKnowledgeId(query)) return;
  if ((state.raw?.items || []).some(item => String(item.kb_id || "").toLowerCase() === query.toLowerCase())) return;
  state.exactQuery.status = "loading";
  renderAll();
  try {
    const result = await knowledgeApi.requestResult(
      `/knowledge/api/item?kb_id=${encodeURIComponent(query)}`,
      { cache: "no-store", detectLoginRedirect: false },
    );
    if (seq !== state.exactQuery.seq) return;
    if (!result.response.ok) {
      throw new Error(result.response.status === 404 ? "未找到这个 Resource ID" : `查询失败（HTTP ${result.response.status}）`);
    }
    const item = knowledgeApi.requireJson(result);
    if (!item?.kb_id) throw new Error("服务返回了无效的知识条目");
    if (state.deletedKnowledgeIds.has(item.kb_id)) return;
    const existing = (state.raw?.items || []).filter(entry => entry.kb_id !== item.kb_id);
    state.raw = { ...(state.raw || {}), items: [item, ...existing] };
    state.indexPaging.total = Math.max(state.indexPaging.total, state.raw.items.length);
    state.exactQuery = { seq, id: query, status: "resolved", error: "" };
    renderAll();
  } catch (error) {
    if (seq !== state.exactQuery.seq) return;
    state.exactQuery = { seq, id: query, status: "error", error: error.message || String(error) };
    renderAll();
  }
}

function activeSemanticSearchHasResults() {
  const active = state.semanticSearch?.activeJob?.();
  return !!active && Number(active.result_count || 0) > 0;
}

function initSemanticSearch() {
  if (state.semanticSearch) return state.semanticSearch;
  const client = {
    get: path => knowledgeApi.request(path, { cache: "no-store" }),
    post: (path, body) => knowledgeApi.request(path, {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  };
  state.semanticSearch = window.PortalSemanticSearch.create({
    input: el.q,
    button: el.semanticBtn,
    panel: el.semanticPanel,
    identity: el.semanticIdentity,
    client,
    initialActiveId: state.activeSemanticSearchId,
    projectionScope: "knowledge",
    urls: {
      list: "/knowledge/api/semantic-searches",
      start: "/knowledge/api/semantic-search/start",
      refresh: "/knowledge/api/semantic-search/refresh",
      delete: "/knowledge/api/semantic-search/delete",
    },
    identityOptions: {
      titleId: "knowledgeSemanticSearchTitle",
      idId: "knowledgeSemanticSearchId",
    },
    onStart: () => {
      state.query = "";
      state.activeSemanticSearchId = "";
      el.q.value = "";
      saveKnowledgeUiState();
      renderAll();
    },
    onActiveChange: searchId => {
      state.activeSemanticSearchId = String(searchId || "");
      if (searchId) {
        state.query = "";
        el.q.value = "";
      }
      saveKnowledgeUiState();
      if (semanticSearchReady) loadIndex().catch(alertError);
    },
    onError: alertError,
  });
  return state.semanticSearch;
}
