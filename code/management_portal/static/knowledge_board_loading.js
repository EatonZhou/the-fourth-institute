// Knowledge board: loading the Knowledge index page by page.

// ── Load ──────────────────────────────────────────────────────────────
function indexRequestParams(offset, { refresh = false, waitSeconds = 0 } = {}) {
  const params = new URLSearchParams({ active_only: "0", surface: "resource-knowledge" });
  if (activeSemanticSearchHasResults()) params.set("semantic", state.activeSemanticSearchId);
  params.set("offset", String(Math.max(0, Number(offset || 0))));
  params.set("limit", String(state.indexPaging.pageSize || 200));
  if (refresh && !activeSemanticSearchHasResults()) params.set("refresh", "1");
  if (waitSeconds > 0 && !activeSemanticSearchHasResults()) params.set("wait", String(waitSeconds));
  return params;
}

async function waitForRefreshedIndex(initialGeneratedAt, requestSeq) {
  const refresh = state.raw?.refresh || {};
  if (!refresh.running || activeSemanticSearchHasResults()) return;
  try {
    const params = indexRequestParams(0, { waitSeconds: 5 });
    const result = await knowledgeApi.requestResult(`/knowledge/api/index?${params}`, {
      cache: "no-store",
      detectLoginRedirect: false,
    });
    if (!result.response.ok || requestSeq !== state.indexPaging.requestSeq) return;
    const page = knowledgeApi.requireJson(result);
    if (String(page.generated_at || "") !== String(initialGeneratedAt || "")) {
      const refreshedSeq = beginIndexLoad();
      applyLoadedIndex(page, refreshedSeq, {
        preserveScroll: true,
        scrollY: window.scrollY,
      });
    }
  } catch (_) {
    // The initial cards remain usable if the optional wait request fails.
  }
}

function applyIndexPage(page, { reset = false } = {}) {
  const pageInfo = page?.page || {};
  const pageItems = (Array.isArray(page?.items) ? page.items : [])
    .filter(item => !state.deletedKnowledgeIds.has(item.kb_id));
  if (reset || !state.raw) {
    state.raw = page || {};
    state.raw.items = pageItems;
  } else {
    const seen = new Set((state.raw.items || []).map(item => item.kb_id));
    const merged = [...(state.raw.items || [])];
    for (const item of pageItems) {
      if (!seen.has(item.kb_id)) {
        merged.push(item);
        seen.add(item.kb_id);
      }
    }
    state.raw = { ...state.raw, ...page, items: merged };
  }
  state.indexPaging.nextOffset = Number(pageInfo.next_offset ?? (state.raw.items || []).length);
  state.indexPaging.total = Number(pageInfo.total ?? (state.raw.items || []).length);
  state.indexPaging.hasMore = !!pageInfo.has_more;
}

function updateIndexMeta() {
  if (!state.raw) return;
  const loaded = state.raw.items?.length || 0;
  const total = state.indexPaging.total || loaded;
  if (state.indexPaging.hasMore || state.indexPaging.prefetching) {
    el.meta.textContent = window.PortalI18n.text`已加载 ${loaded} / ${total} 项${state.indexPaging.prefetching ? window.PortalI18n.t("，继续加载中…") : ""}`;
  }
}

async function prefetchIndexPages(seq) {
  if (state.indexPaging.prefetching || !state.indexPaging.hasMore) return;
  state.indexPaging.prefetching = true;
  updateIndexMeta();
  try {
    while (seq === state.indexPaging.requestSeq && state.indexPaging.hasMore) {
      const params = indexRequestParams(state.indexPaging.nextOffset);
      const result = await knowledgeApi.requestResult(`/knowledge/api/index?${params}`, {
        cache: "no-store",
        detectLoginRedirect: false,
      });
      if (!result.response.ok) throw new Error(`HTTP ${result.response.status}`);
      const page = knowledgeApi.requireJson(result);
      if (seq !== state.indexPaging.requestSeq) return;
      applyIndexPage(page);
      buildFilterOptions();
      renderAll();
      await new Promise(resolve => setTimeout(resolve, 80));
    }
  } catch (err) {
    if (seq === state.indexPaging.requestSeq) {
      state.indexPaging.error = err.message || String(err);
      renderAll();
    }
    console.warn("knowledge index prefetch failed", err);
  } finally {
    if (seq === state.indexPaging.requestSeq) {
      state.indexPaging.prefetching = false;
      renderAll();
    }
  }
}

function beginIndexLoad() {
  const seq = state.indexPaging.requestSeq + 1;
  state.indexPaging.requestSeq = seq;
  state.indexPaging.nextOffset = 0;
  state.indexPaging.total = 0;
  state.indexPaging.hasMore = false;
  state.indexPaging.prefetching = false;
  state.indexPaging.error = "";
  el.meta.textContent = window.PortalI18n.t("加载中…");
  return seq;
}

function applyLoadedIndex(firstPage, seq, { preserveScroll = false, scrollY = 0 } = {}) {
  if (seq !== state.indexPaging.requestSeq) return;
  applyIndexPage(firstPage, { reset: true });
  // Default type to first allowed type if unset
  if (filter.type === null) {
    // keep null = show all on first load, or set to first type:
    // filter.type = state.raw.allowed_types?.[0] || null;
  }
  normalizeFilterStateForIndex();
  buildFilterOptions();
  syncKnowledgeFilterInputs();
  knowledgeBoardShell.syncFilterState();
  renderAll();
  prefetchIndexPages(seq);
  if (preserveScroll) {
    window.requestAnimationFrame(() => window.scrollTo({ top: scrollY }));
  }
}

async function loadIndex({ refresh = false, preserveScroll = false, waitForRefresh = true } = {}) {
  const scrollY = preserveScroll ? window.scrollY : 0;
  const seq = beginIndexLoad();
  try {
    const result = await knowledgeApi.requestResult(
      `/knowledge/api/index?${indexRequestParams(0, { refresh })}`,
      { cache: "no-store", detectLoginRedirect: false },
    );
    if (seq !== state.indexPaging.requestSeq) return;
    if (!result.response.ok) throw new Error(`HTTP ${result.response.status}`);
    const firstPage = knowledgeApi.requireJson(result);
    applyLoadedIndex(firstPage, seq, { preserveScroll, scrollY });
    if (waitForRefresh) waitForRefreshedIndex(firstPage.generated_at, seq);
  } catch (error) {
    if (seq !== state.indexPaging.requestSeq) return;
    state.indexPaging.error = error.message || String(error);
    if (state.raw) renderAll();
    else el.meta.textContent = window.PortalI18n.text`索引加载失败：${state.indexPaging.error}。请点击刷新后重试。`;
    throw error;
  }
}
