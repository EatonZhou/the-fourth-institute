(() => {
  "use strict";
  window.PortalPaperPersonal = {mount({state, paperApi, loadDetail, setMode, syncFavoriteButtons = () => {}, renderTree}) {
    const tree = document.getElementById("folderTree");
    if (!tree) return;
    const host = document.createElement("section");
    host.className = "paperPersonal";
    host.innerHTML = `<nav class="portalSegmented" data-scopes aria-label="查看范围">
      <button type="button" data-scope="personal">个人</button>
      <button type="button" data-scope="repository" data-view="repository">全库</button></nav>
      <nav class="portalToolbar" data-personal-tabs aria-label="个人文献">
      <button type="button" data-view="recent">近期阅读</button>
      <button type="button" data-view="favorites">收藏</button>
      </nav><p data-scope-label>个人文献</p>
      <p data-status role="status" aria-live="polite"></p>
      <div data-personal hidden><h3 data-heading></h3><div data-rows></div>
      <button type="button" data-more hidden>加载更多</button></div>`;
    host.querySelectorAll("button").forEach(button => button.classList.add("portalButton", "is-compact"));
    const body = tree.closest('.leftBody');
    if (body) body.prepend(host); else tree.before(host);
    const panel = host.querySelector("[data-personal]");
    const rows = host.querySelector("[data-rows]");
    const more = host.querySelector("[data-more]");
    const status = host.querySelector("[data-status]");
    let view = "repository", cursor = "", offset = 0, sequence = 0, loading = false;
    let account = String(state.principalId || ''), personalView = 'recent';
    const views = new Map();
    const scopeData = new Map(), expansions = new Map();
    const loadedViews = new Set();
    function accountChanged() {
      const next = String(state.principalId || '');
      if (next === account) return;
      account = next; ++sequence; loading = false; views.clear(); loadedViews.clear(); scopeData.clear(); expansions.clear();
      rows.replaceChildren(); cursor = ''; offset = 0; status.textContent = '';
      state.favoriteIds.clear(); pending.clear(); acknowledged.clear(); personalView = 'recent';
      show('recent', false);
    }
    function show(next, preserve = true) {
      if (preserve && (view === 'repository' || loadedViews.has(view))) {
        const content = document.createDocumentFragment();
        if (view !== 'repository') content.append(...rows.childNodes);
        views.set(view, {content, cursor, offset, moreHidden: more.hidden, status: status.textContent, scroll: body?.scrollTop || 0});
      }
      view = next; ++sequence; loading = false;
      const personal = view !== 'repository';
      if (personal) personalView = view;
      body?.classList.toggle('is-personal', personal); tree.hidden = personal; panel.hidden = !personal;
      host.querySelector('[data-personal-tabs]').hidden = !personal;
      host.querySelector('[data-scope-label]').textContent = personal ? '个人文献（近期阅读 / 收藏）' : '全库范围：列表、搜索、分类、CCF、语义检索与采集';
      host.querySelectorAll('[data-scope]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.scope === (personal ? 'personal' : 'repository'))));
      host.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
      host.querySelector('[data-heading]').textContent = view === 'recent' ? '近期阅读' : '我的收藏';
      rows.replaceChildren();
      const saved = views.get(view);
      if (saved) {
        rows.append(saved.content); cursor = saved.cursor; offset = saved.offset; more.hidden = saved.moreHidden; status.textContent = saved.status;
        if (view === 'favorites') rows.querySelectorAll('[data-row]').forEach(row => {
          if (!state.favoriteIds.has(row.dataset.row)) {row.remove(); offset = Math.max(0, offset - 1);}
        });
        if (view === 'favorites') scopeData.set(view, (scopeData.get(view) || []).filter(p => state.favoriteIds.has(p.id)));
        renderPersonalTree(); sync();
      }
      else if (personal && account) page(true);
      else status.textContent = '';
      if (body) body.scrollTop = saved?.scroll || 0;
    }
    const pending = state.favoritePending || new Set(), acknowledged = new Set();
    const message = error => { status.textContent = error?.message || String(error); };
    function sync() {
      rows.querySelectorAll("[data-favorite], [data-paper-favorite]").forEach(button => {
        const saved = state.favoriteIds.has((button.dataset.favorite || button.dataset.paperFavorite));
        button.textContent = button.dataset.paperFavorite ? (saved ? "★" : "☆") : (saved ? "取消收藏" : "收藏");
        button.setAttribute("aria-pressed", String(saved));
        button.disabled = pending.has((button.dataset.favorite || button.dataset.paperFavorite));
      });
    }
    function renderPersonalTree() {
      const papers = scopeData.get(view) || [];
      const folders = [];
      for (const paper of papers) {
        let children = folders;
        for (const name of [paper.venue || 'Unsorted', String(paper.year || 'Unknown'), paper.session || 'General']) {
          let node = children.find(n => n.name === name);
          if (!node) {node = {name, count: 0, children: []}; children.push(node);}
          node.count++; children = node.children;
        }
      }
      if (!expansions.has(view)) expansions.set(view, {});
      renderTree({target: rows, folders, papers, expansion: expansions.get(view),
        summary: {paper_count: papers.length, visible_count: papers.length}});
      rows.querySelectorAll('[data-paper-id]').forEach(el => el.closest('.paperLeaf').dataset.row = el.dataset.paperId);
      if (!papers.length) rows.replaceChildren();
    }
    async function page(reset = false) {
      if (loading && !reset) return;
      const current = ++sequence, requestedView = view;
      const principal = account;
      loading = true; more.disabled = true;
      if (reset) { cursor = ""; offset = 0; rows.replaceChildren(); }
      try {
        const url = requestedView === "recent"
          ? `/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(cursor)}`
          : `/paper/api/library?compact=1&rows_only=1&favorite=1&limit=50&offset=${offset}`;
        const data = await paperApi.request(url);
        accountChanged();
        if (current !== sequence || requestedView !== view || principal !== account) return;
        const papers = [...data.papers];
        let next = data.next_cursor || '', count = data.papers.length;
        while (requestedView === 'recent' ? !!next : count === 50) {
          const nextUrl = requestedView === 'recent'
            ? `/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(next)}`
            : `/paper/api/library?compact=1&rows_only=1&favorite=1&limit=50&offset=${papers.length}`;
          const batch = await paperApi.request(nextUrl);
          accountChanged();
          if (current !== sequence || requestedView !== view || principal !== account) return;
          if (next && batch.next_cursor === next) throw Error('个人列表游标未推进');
          papers.push(...batch.papers); count = batch.papers.length; next = batch.next_cursor || '';
        }
        for (const paper of papers) {
          if (requestedView === 'favorites' || paper.favorite) state.favoriteIds.add(paper.id);
          else state.favoriteIds.delete(paper.id);
        }
        scopeData.set(view, [...new Map(papers.map(p => [p.id, p])).values()]);
        cursor = ''; offset = papers.length;
        renderPersonalTree();
        loadedViews.add(view);
        more.hidden = true;
        status.textContent = rows.childElementCount ? "" : "暂无记录";
        sync();
      } catch (error) { if (current === sequence) message(error); }
      finally { if (current === sequence) { loading = false; more.disabled = false; } }
    }
    host.addEventListener("click", async event => {
      const button = event.target.closest("button"); if (!button) return;
      accountChanged();
      if (button.dataset.paperTreeToggle) {
        expansions.get(view)[button.dataset.paperTreeToggle] = button.getAttribute('aria-expanded') !== 'true';
        renderPersonalTree(); return;
      }
      if (button.dataset.paperId) {
        loadDetail(button.dataset.paperId).then(() => setMode('reader')).catch(message); return;
      }
      if (button.dataset.scope || button.dataset.view) {
        const next = button.dataset.scope === 'personal' ? personalView : button.dataset.view;
        if (next !== view) show(next);
        else if (view !== 'repository' && !loading && !loadedViews.has(view)) page(true);
      } else if (button.hasAttribute("data-more")) page();
      else if ((button.dataset.favorite || button.dataset.paperFavorite) && !pending.has((button.dataset.favorite || button.dataset.paperFavorite))) {
        const principal = account;
        const id = (button.dataset.favorite || button.dataset.paperFavorite); pending.add(id);
        state.favoriteRevision = (state.favoriteRevision || 0) + 1;
        sync(); syncFavoriteButtons();
        try {
          const result = await paperApi.request("/paper/api/favorites", {method: "POST", body: {paper_id: id, favorite: !state.favoriteIds.has(id)}});
          accountChanged(); if (principal !== account) return;
          if (result.favorite) state.favoriteIds.add(id); else state.favoriteIds.delete(id);
          if (!result.favorite && view === "favorites") {
            scopeData.set(view, (scopeData.get(view) || []).filter(p => p.id !== id));
            renderPersonalTree();
            offset = Math.max(0, offset - 1);
          }
          window.dispatchEvent(new CustomEvent("portal:paper-favorite-changed"));
        } catch (error) { message(error); } finally {
          if (principal === account) {pending.delete(id); state.favoriteRevision += 1; sync(); syncFavoriteButtons();}
        }
      }
    });
    show('recent', false);
    window.addEventListener('portal:paper-principal-changed', accountChanged);
    window.addEventListener("portal:paper-favorite-changed", () => {
      if (view !== 'favorites') {views.delete('favorites'); loadedViews.delete('favorites');}
      sync();
    });
    window.addEventListener("portal:paper-reader-ready", event => {
      const {paperId, generation, sessionToken, account} = event.detail || {};
      const key = sessionToken || `${generation}:${paperId}`;
      if (!account || account !== String(state.principalId || "")) return;
      if (!paperId || state.selectedId !== paperId || state.mode !== "reader" || acknowledged.has(key)) return;
      acknowledged.add(key);
      paperApi.request("/paper/api/recent-reading/open", {method: "POST", body: {
        paper_id: paperId, expected_principal: account,
      }}).then(() => {
        if (account !== String(state.principalId || '')) return;
        views.delete('recent'); loadedViews.delete('recent');
        if (view === 'recent') page(true);
      }).catch(error => {
        status.textContent = error?.status === 409 || error?.message?.includes("session_changed")
          ? "账号已变化，请刷新身份并重新打开论文；本次阅读未记录。" : error?.message || String(error);
      });
    });
  }};
})();
