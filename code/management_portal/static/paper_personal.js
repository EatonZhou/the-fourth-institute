(() => {
  "use strict";
  window.PortalPaperPersonal = {mount({state, paperApi, loadDetail, setMode, syncFavoriteButtons = () => {}}) {
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
      <div class="portalToolbar"><button type="button" data-export="single">当前论文 BibTeX</button>
      <button type="button" data-export="favorites">我的收藏 BibTeX</button></div>
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
    const loadedViews = new Set();
    function accountChanged() {
      const next = String(state.principalId || '');
      if (next === account) return;
      account = next; ++sequence; loading = false; views.clear(); loadedViews.clear();
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
        sync();
      }
      else if (personal && account) page(true);
      else status.textContent = '';
      if (body) body.scrollTop = saved?.scroll || 0;
    }
    const pending = state.favoritePending || new Set(), acknowledged = new Set();
    const message = error => { status.textContent = error?.message || String(error); };
    function sync() {
      rows.querySelectorAll("[data-favorite]").forEach(button => {
        const saved = state.favoriteIds.has(button.dataset.favorite);
        button.textContent = saved ? "取消收藏" : "收藏";
        button.setAttribute("aria-pressed", String(saved));
        button.disabled = pending.has(button.dataset.favorite);
      });
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
        for (const paper of data.papers) {
          if (rows.querySelector(`[data-row="${CSS.escape(paper.id)}"]`)) continue;
          if (requestedView === "favorites" || paper.favorite) state.favoriteIds.add(paper.id);
          else state.favoriteIds.delete(paper.id);
          const row = document.createElement("article"); row.dataset.row = paper.id;
          const open = document.createElement("button"); open.type = "button";
          open.className = "portalButton is-quiet"; open.textContent = paper.title || paper.id;
          open.onclick = () => loadDetail(paper.id).then(() => setMode("reader")).catch(message);
          const favorite = document.createElement("button"); favorite.type = "button";
          favorite.className = "portalButton is-compact"; favorite.dataset.favorite = paper.id;
          row.append(open, favorite);
          if (paper.last_read_at) { const time = document.createElement("time");
            time.dateTime = paper.last_read_at; time.textContent = new Date(paper.last_read_at).toLocaleString(); row.append(time); }
          rows.append(row);
        }
        cursor = data.next_cursor || ""; offset += data.papers.length;
        loadedViews.add(view);
        more.hidden = requestedView === "recent" ? !cursor : data.papers.length < 50;
        status.textContent = rows.childElementCount ? "" : "暂无记录";
        sync();
      } catch (error) { if (current === sequence) message(error); }
      finally { if (current === sequence) { loading = false; more.disabled = false; } }
    }
    host.addEventListener("click", async event => {
      const button = event.target.closest("button"); if (!button) return;
      accountChanged();
      if (button.dataset.scope || button.dataset.view) {
        const next = button.dataset.scope === 'personal' ? personalView : button.dataset.view;
        if (next !== view) show(next);
        else if (view !== 'repository' && !loading && !loadedViews.has(view)) page(true);
      } else if (button.hasAttribute("data-more")) page();
      else if (button.dataset.favorite && !pending.has(button.dataset.favorite)) {
        const principal = account;
        const id = button.dataset.favorite; pending.add(id);
        state.favoriteRevision = (state.favoriteRevision || 0) + 1;
        sync(); syncFavoriteButtons();
        try {
          const result = await paperApi.request("/paper/api/favorites", {method: "POST", body: {paper_id: id, favorite: !state.favoriteIds.has(id)}});
          accountChanged(); if (principal !== account) return;
          if (result.favorite) state.favoriteIds.add(id); else state.favoriteIds.delete(id);
          if (!result.favorite && view === "favorites") {
            button.closest("[data-row]")?.remove();
            offset = Math.max(0, offset - 1);
          }
          window.dispatchEvent(new CustomEvent("portal:paper-favorite-changed"));
        } catch (error) { message(error); } finally {
          if (principal === account) {pending.delete(id); state.favoriteRevision += 1; sync(); syncFavoriteButtons();}
        }
      } else if (button.dataset.export) {
        const principal = account;
        const body = button.dataset.export === "favorites" ? {favorites: true} : {paper_ids: [state.selectedId]};
        if (button.dataset.export === "single" && !state.selectedId) { status.textContent = "请先选择论文"; return; }
        button.disabled = true;
        try {
          const data = await paperApi.request("/paper/api/bibtex/export", {method: "POST", body});
          accountChanged(); if (principal !== account) return;
          status.textContent = data.warnings.length ? `导出提示：${JSON.stringify(data.warnings)}` : "导出完成";
          const url = URL.createObjectURL(new Blob([data.bibtex], {type: "application/x-bibtex;charset=utf-8"}));
          const link = document.createElement("a"); link.href = url; link.download = "references.bib"; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { message(error); } finally { button.disabled = false; }
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
