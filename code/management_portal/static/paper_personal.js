(() => {
  "use strict";
  window.PortalPaperPersonal = {mount({state, paperApi, loadDetail, setMode, syncFavoriteButtons = () => {}}) {
    const tree = document.getElementById("folderTree");
    if (!tree) return;
    const host = document.createElement("section");
    host.className = "paperPersonal";
    host.innerHTML = `<nav class="portalToolbar" aria-label="文献入口">
      <button type="button" data-view="repository">全仓库</button>
      <button type="button" data-view="favorites">我的收藏</button>
      <button type="button" data-view="recent">近期阅读</button></nav>
      <div class="portalToolbar"><button type="button" data-export="single">当前论文 BibTeX</button>
      <button type="button" data-export="favorites">我的收藏 BibTeX</button></div>
      <p data-status role="status" aria-live="polite"></p>
      <div data-personal hidden><h3 data-heading></h3><div data-rows></div>
      <button type="button" data-more hidden>加载更多</button></div>`;
    host.querySelectorAll("button").forEach(button => button.classList.add("portalButton", "is-compact"));
    tree.before(host);
    const panel = host.querySelector("[data-personal]");
    const rows = host.querySelector("[data-rows]");
    const more = host.querySelector("[data-more]");
    const status = host.querySelector("[data-status]");
    let view = "repository", cursor = "", offset = 0, sequence = 0, loading = false;
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
      loading = true; more.disabled = true;
      if (reset) { cursor = ""; offset = 0; rows.replaceChildren(); }
      try {
        const url = requestedView === "recent"
          ? `/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(cursor)}`
          : `/paper/api/library?compact=1&rows_only=1&favorite=1&limit=50&offset=${offset}`;
        const data = await paperApi.request(url);
        if (current !== sequence || requestedView !== view) return;
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
        more.hidden = requestedView === "recent" ? !cursor : data.papers.length < 50;
        status.textContent = rows.childElementCount ? "" : "暂无记录";
        sync();
      } catch (error) { if (current === sequence) message(error); }
      finally { if (current === sequence) { loading = false; more.disabled = false; } }
    }
    host.addEventListener("click", async event => {
      const button = event.target.closest("button"); if (!button) return;
      if (button.dataset.view) {
        view = button.dataset.view;
        ++sequence; loading = false; tree.hidden = view !== "repository"; panel.hidden = view === "repository";
        host.querySelectorAll("[data-view]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
        if (view !== "repository") { host.querySelector("[data-heading]").textContent = view === "recent" ? "近期阅读" : "我的收藏"; page(true); }
      } else if (button.hasAttribute("data-more")) page();
      else if (button.dataset.favorite && !pending.has(button.dataset.favorite)) {
        const id = button.dataset.favorite; pending.add(id);
        state.favoriteRevision = (state.favoriteRevision || 0) + 1;
        sync(); syncFavoriteButtons();
        try {
          const result = await paperApi.request("/paper/api/favorites", {method: "POST", body: {paper_id: id, favorite: !state.favoriteIds.has(id)}});
          if (result.favorite) state.favoriteIds.add(id); else state.favoriteIds.delete(id);
          if (!result.favorite && view === "favorites") {
            button.closest("[data-row]")?.remove();
            offset = Math.max(0, offset - 1);
          }
          window.dispatchEvent(new CustomEvent("portal:paper-favorite-changed"));
        } catch (error) { message(error); } finally {
          pending.delete(id); state.favoriteRevision += 1; sync(); syncFavoriteButtons();
        }
      } else if (button.dataset.export) {
        const body = button.dataset.export === "favorites" ? {favorites: true} : {paper_ids: [state.selectedId]};
        if (button.dataset.export === "single" && !state.selectedId) { status.textContent = "请先选择论文"; return; }
        button.disabled = true;
        try {
          const data = await paperApi.request("/paper/api/bibtex/export", {method: "POST", body});
          status.textContent = data.warnings.length ? `导出提示：${JSON.stringify(data.warnings)}` : "导出完成";
          const url = URL.createObjectURL(new Blob([data.bibtex], {type: "application/x-bibtex;charset=utf-8"}));
          const link = document.createElement("a"); link.href = url; link.download = "references.bib"; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { message(error); } finally { button.disabled = false; }
      }
    });
    window.addEventListener("portal:paper-favorite-changed", sync);
    window.addEventListener("portal:paper-reader-ready", event => {
      const {paperId, generation, sessionToken, account} = event.detail || {};
      const key = sessionToken || `${generation}:${paperId}`;
      if (!account || account !== String(state.principalId || "")) return;
      if (!paperId || state.selectedId !== paperId || state.mode !== "reader" || acknowledged.has(key)) return;
      acknowledged.add(key);
      paperApi.request("/paper/api/recent-reading/open", {method: "POST", body: {
        paper_id: paperId, expected_principal: account,
      }}).catch(error => {
        status.textContent = error?.status === 409 || error?.message?.includes("session_changed")
          ? "账号已变化，请刷新身份并重新打开论文；本次阅读未记录。" : error?.message || String(error);
      });
    });
  }};
})();
