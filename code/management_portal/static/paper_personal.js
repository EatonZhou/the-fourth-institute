(() => {
  "use strict";
  window.PortalPaperPersonal = {mount({state, paperApi, loadDetail, setMode, syncFavoriteButtons = () => {}, renderTree, renderLibraryMode, loadLibrary}) {
    const tree = document.getElementById("folderTree");
    if (!tree) return;
    const host = document.createElement("section");
    host.className = "paperPersonal";
    host.innerHTML = `<nav class="portalSegmented leftFaceTabs" data-scopes aria-label="查看范围">
      <button type="button" data-scope="repository" data-view="repository">全库</button>
      <button type="button" data-scope="personal">个人</button></nav>
      <nav class="portalSegmented leftFaceTabs" data-personal-tabs aria-label="个人文献">
      <button type="button" data-view="recent">近期阅读</button>
      <button type="button" data-view="favorites">个人档案</button>
      </nav>
      <p data-status role="status" aria-live="polite"></p>
      <div data-personal hidden role="region" aria-label="近期阅读"><span data-heading hidden></span>
      <div class="paperPersonalFilters">
        <label data-archive-field><span data-portal-i18n-text="个人档案">个人档案</span><select class="portalControl" data-archive></select></label>
        <button class="portalButton is-compact" type="button" data-new-archive data-portal-i18n-text="新建档案">新建档案</button>
        <label class="paperArchiveLayout"><span data-portal-i18n-text="排布方式">排布方式</span><select class="portalControl" data-layout>
          <option value="venue" data-portal-i18n-text="出版地点 → 年份">出版地点 → 年份</option>
          <option value="year" data-portal-i18n-text="年份 → 出版地点">年份 → 出版地点</option>
          <option value="tags" data-portal-i18n-text="标签 → 年份">标签 → 年份</option>
        </select></label>
        <label><span data-portal-i18n-text="搜索">搜索</span><input class="portalControl" data-personal-query type="search" placeholder="标题、作者、关键词、ID…" data-portal-i18n-attrs='{"placeholder":"标题、作者、关键词、ID…"}'></label>
        <label><span data-portal-i18n-text="年份">年份</span><select class="portalControl" data-personal-year><option value="">全部年份</option></select></label>
        <label><span data-portal-i18n-text="出版地点">出版地点</span><select class="portalControl" data-personal-venue><option value="">全部出版地点</option></select></label>
        <button class="portalButton is-compact" type="button" data-personal-clear data-portal-i18n-text="清除筛选">清除筛选</button>
      </div><div data-rows></div>
      <button type="button" data-more hidden>加载更多</button></div>`;
    host.querySelector("[data-more]").classList.add("portalButton", "is-compact");
    host.querySelectorAll('[data-scope], [data-view], [data-more]').forEach(el => {
      el.dataset.portalI18nText = el.textContent;
    });
    host.querySelectorAll('nav[aria-label]').forEach(el => {
      el.dataset.portalI18nAttrs = JSON.stringify({'aria-label':el.getAttribute('aria-label')});
    });
    const body = tree.closest('.leftBody');
    if (body) body.prepend(host); else tree.before(host);
    const panel = host.querySelector("[data-personal]");
    const rows = host.querySelector("[data-rows]");
    const more = host.querySelector("[data-more]");
    const status = host.querySelector("[data-status]");
    let view = "repository", cursor = "", offset = 0, sequence = 0, loading = false;
    let account = String(state.principalId || ''), personalView = 'recent';
    const archiveSelect = host.querySelector('[data-archive]');
    const layout = host.querySelector('[data-layout]');
    const tr = key => window.PortalI18n.t(key);
    const archiveName = name => name || tr('默认收藏');
    state.paperArchive = '';
    let picker = null;
    async function refreshArchives() {
      const owner = account;
      const result = await paperApi.request('/paper/api/archives');
      if (owner !== account) return;
      archiveSelect.replaceChildren(...result.archives.map(({name}) => new Option(archiveName(name), name)));
      archiveSelect.value = state.paperArchive;
    }
    async function selectArchive(name) {
      state.paperArchive = name;
      views.delete('favorites'); loadedViews.delete('favorites'); scopeData.delete('favorites');
      archiveSelect.value = name;
      if (window.PortalPaperNavigation?.current) await window.PortalPaperNavigation.current.goList('favorites');
      else show('favorites', false);
    }
    archiveSelect.addEventListener('change', () => selectArchive(archiveSelect.value).catch(message));
    layout.addEventListener('change', renderPersonalTree);
    const views = new Map();
    const scopeData = new Map(), expansions = new Map();
    const loadedViews = new Set();
    const filters = new Map();
    const controls = Object.fromEntries(['query', 'year', 'venue'].map(key => [key, host.querySelector(`[data-personal-${key}]`)]));
    const filterState = () => {
      if (!filters.has(view)) filters.set(view, {query: '', year: '', venue: ''});
      return filters.get(view);
    };
    function accountChanged() {
      const next = String(state.principalId || '');
      if (next === account) return;
      account = next; ++sequence; loading = false; views.clear(); loadedViews.clear(); scopeData.clear(); expansions.clear();
      state.paperArchive = ''; archiveSelect.replaceChildren(); picker?.remove(); picker = null;
      filters.clear(); rows.replaceChildren(); cursor = ''; offset = 0; status.textContent = '';
      state.favoriteIds.clear(); pending.clear(); acknowledged.clear(); personalView = 'recent';
      show('recent', false);
    }
    window.addEventListener('portal:paper-history-open', () => { if(view !== 'repository') show('repository'); });
    function show(next, preserve = true) {
      if (preserve && (view === 'repository' || loadedViews.has(view))) {
        const content = document.createDocumentFragment();
        if (view !== 'repository') content.append(...rows.childNodes);
        views.set(view, {content, cursor, offset, moreHidden: more.hidden, status: status.textContent, scroll: body?.scrollTop || 0});
      }
      view = next; ++sequence; loading = false;
      state.paperListScope=view==='repository'?'library':view;
      const personal = view !== 'repository';
      if (!personal && state.filters?.favorite) {
        delete state.filters.favorite;
        const requestedSequence = sequence, requestedAccount = account;
        loadLibrary?.({keepDetail: true, hydrateDetail: false}).catch(error => {
          if (sequence === requestedSequence && account === requestedAccount && view === 'repository') status.textContent = error.message;
        });
      }
      if (personal) personalView = view;
      body?.classList.toggle('is-personal', personal); tree.hidden = personal; panel.hidden = !personal;
      host.querySelector('[data-personal-tabs]').hidden = !personal;
      host.querySelectorAll('[data-scope]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.scope === (personal ? 'personal' : 'repository'))));
      host.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
      host.querySelector('[data-heading]').textContent = window.PortalI18n.t(view === 'recent' ? '近期阅读' : '我的收藏');
      panel.setAttribute('aria-label', host.querySelector('[data-heading]').textContent);
      host.querySelector('[data-archive-field]').hidden = view !== 'favorites';
      if (view === 'favorites' && account) refreshArchives().catch(message);
      if (personal) syncFilters();
      rows.replaceChildren();
      const saved = views.get(view);
      if (saved) {
        rows.append(saved.content); cursor = saved.cursor; offset = saved.offset; more.hidden = saved.moreHidden; status.textContent = saved.status;
        if (personal) { renderPersonalTree(); sync(); }
        else status.textContent = '';
      }
      else if (personal && account) page(true);
      else status.textContent = '';
      if (body) body.scrollTop = saved?.scroll || 0;
    }
    const pending = state.favoritePending || new Set(), acknowledged = new Set();
    const message = error => { status.textContent = error?.message || String(error); };
    function sync() {
      syncFavoriteButtons();
    }
    function renderPersonalTree() {
      if (view === 'repository') return;
      syncFilters();
      const all = scopeData.get(view) || [], filter = filterState();
      const normalize = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase();
      const tokens = normalize(filter.query).trim().split(/\s+/).filter(Boolean);
      const papers = all.filter(paper => {
        const text = normalize([paper.id, paper.title, paper.authors, paper.tags, paper.keywords, paper.venue, paper.session].flat().filter(Boolean).join(' '));
        return tokens.every(token => text.includes(token)) && (!filter.year || String(paper.year || '') === filter.year)
          && (!filter.venue || paper.venue === filter.venue);
      });
      const active = tokens.length || filter.year || filter.venue;
      status.textContent = !all.length ? window.PortalI18n.t('暂无记录') : !papers.length ? window.PortalI18n.t('没有符合筛选条件的论文') : active ? window.PortalPaperLibraryView.paperCountLabel(papers.length) : '';
      const folders = [];
      for (const paper of papers) {
        const venue = paper.venue || 'Unsorted', year = String(paper.year || 'Unknown');
        const groups = layout.value === 'tags' ? [...new Set(paper.tags?.length ? paper.tags : ['Unsorted'])] :
          [layout.value === 'year' ? year : venue];
        for (const group of groups) {
          let children = folders, node;
          for (const name of [group, layout.value === 'year' ? venue : year, paper.session || 'General']) {
            node = children.find(item => item.name === name);
            if (!node) { node = {name, count: 0, children: []}; children.push(node); }
            node.count++; children = node.children;
          }
          (node.papers ||= []).push(paper);
        }
      }
      if (!expansions.has(view)) expansions.set(view, {});
      renderTree({target: rows, folders, papers, expansion: expansions.get(view),
        summary: {paper_count: papers.length, visible_count: papers.length}});
      rows.querySelectorAll('[data-paper-id]').forEach(el => el.closest('.paperLeaf').dataset.row = el.dataset.paperId);
      if (!papers.length) rows.replaceChildren();
    }
    function syncFilters() {
      const filter = filterState(), papers = scopeData.get(view) || [];
      controls.query.value = filter.query;
      for (const key of ['year', 'venue']) {
        const select = controls[key];
        select.replaceChildren(new Option(window.PortalI18n.t(key === 'year' ? '全部年份' : '全部出版地点'), ''));
        const values = [...new Set(papers.map(p => String(p[key] || '')).filter(Boolean))].sort((a, b) => key === 'year' ? b.localeCompare(a) : a.localeCompare(b));
        // Keep an active value even when the last matching favorite is removed.
        if (filter[key] && !values.includes(filter[key])) values.push(filter[key]);
        values.forEach(value => select.add(new Option(value, value)));
        select.value = filter[key];
      }
    }
    for (const [key, control] of Object.entries(controls)) control.addEventListener(key === 'query' ? 'input' : 'change', () => {
      accountChanged();
      filterState()[key] = control.value;
      renderPersonalTree(); sync();
    });
    async function page(reset = false) {
      if (loading && !reset) return;
      const current = ++sequence, requestedView = view;
      const principal = account;
      loading = true; more.disabled = true;
      if (reset) { cursor = ""; offset = 0; rows.replaceChildren(); }
      try {
        const url = requestedView === "recent"
          ? `/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(cursor)}`
          : `/paper/api/library?compact=1&rows_only=1&favorite=1&archive=${encodeURIComponent(state.paperArchive)}&limit=50&offset=${offset}`;
        const data = await paperApi.request(url);
        accountChanged();
        if (current !== sequence || requestedView !== view || principal !== account) return;
        const papers = [...data.papers];
        let next = data.next_cursor || '', count = data.papers.length;
        while (requestedView === 'recent' ? !!next : count === 50) {
          const nextUrl = requestedView === 'recent'
            ? `/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(next)}`
            : `/paper/api/library?compact=1&rows_only=1&favorite=1&archive=${encodeURIComponent(state.paperArchive)}&limit=50&offset=${papers.length}`;
          const batch = await paperApi.request(nextUrl);
          accountChanged();
          if (current !== sequence || requestedView !== view || principal !== account) return;
          if (next && batch.next_cursor === next) throw Error('个人列表游标未推进');
          papers.push(...batch.papers); count = batch.papers.length; next = batch.next_cursor || '';
        }
        for (const paper of papers) {
          if (requestedView === 'favorites' && state.paperArchive) continue;
          if (requestedView === 'favorites' || paper.favorite) state.favoriteIds.add(paper.id);
          else state.favoriteIds.delete(paper.id);
        }
        scopeData.set(view, [...new Map(papers.map(p => [p.id, p])).values()]);
        (state.paperPersonalLists ||= {})[view] = scopeData.get(view);
        if (state.mode === 'library' && state.paperListScope === view && !state.libraryLoading) renderLibraryMode?.();
        cursor = ''; offset = papers.length;
        renderPersonalTree();
        loadedViews.add(view);
        more.hidden = true;
        sync();
      } catch (error) { if (current === sequence) message(error); }
      finally { if (current === sequence) { loading = false; more.disabled = false; } }
    }
    host.addEventListener("click", async event => {
      const button = event.target.closest("button"); if (!button) return;
      accountChanged();
      if (button.hasAttribute('data-new-archive')) {
        const name = window.prompt(tr('档案名称'))?.trim();
        if (!name) return;
        const owner = account;
        try {
          await paperApi.request('/paper/api/archives', {method:'POST', body:{name}});
          if (owner === state.principalId) await selectArchive(name);
        } catch (error) { message(error); }
        return;
      }
      if (button.hasAttribute('data-personal-clear')) {
        filters.delete(view); renderPersonalTree(); sync(); return;
      }
      if (button.dataset.paperTreeToggle) {
        expansions.get(view)[button.dataset.paperTreeToggle] = button.getAttribute('aria-expanded') !== 'true';
        renderPersonalTree(); return;
      }
      if (button.dataset.paperId) {
        loadDetail(button.dataset.paperId).then(() => setMode('reader')).catch(message); return;
      }
      if (button.dataset.scope || button.dataset.view) {
        const next = button.dataset.scope === 'personal' ? personalView : button.dataset.view;
        if(window.PortalPaperNavigation?.current){window.PortalPaperNavigation.current.goList(next==='repository'?'library':next).catch(message);return;}
        if (next !== view) show(next);
        else if (view !== 'repository' && !loading && !loadedViews.has(view)) page(true);
      } else if (button.hasAttribute("data-more")) page();
      else if (button.dataset.paperFavorite) openPicker(button.dataset.paperFavorite, button).catch(message);
    });
    async function openPicker(id, anchor) {
      picker?.remove();
      const owner = state.principalId;
      const menu = document.createElement('div');
      menu.popover = 'auto';
      menu.className = 'paperArchiveMenu';
      menu.setAttribute('role','dialog');
      menu.setAttribute('aria-label',tr('添加到档案'));
      menu.textContent = tr('加载中…');
      document.body.append(menu);
      picker = menu;
      menu.addEventListener('toggle', event => {
        if (event.newState === 'closed') { menu.remove(); if (picker === menu) picker = null; }
      });
      menu.showPopover();
      const rect = anchor.getBoundingClientRect();
      menu.style.left = Math.max(4, Math.min(rect.left, innerWidth - menu.offsetWidth - 4)) + 'px';
      const below = innerHeight - rect.bottom;
      if (below >= 150 || below >= rect.top) {
        menu.style.top = rect.bottom + 'px'; menu.style.maxHeight = Math.max(40, below - 4) + 'px';
      } else {
        menu.style.bottom = (innerHeight - rect.top) + 'px'; menu.style.maxHeight = (rect.top - 4) + 'px';
      }
      try {
        const {archives} = await paperApi.request('/paper/api/archives?paper_id=' + encodeURIComponent(id));
        if (menu !== picker || owner !== state.principalId) return;
        menu.replaceChildren();
        for (const archive of archives) {
          const row = document.createElement('label'), check = document.createElement('input');
          check.type = 'checkbox'; check.checked = archive.saved;
          row.append(check, document.createTextNode(window.PortalI18n.text`添加到 ${archiveName(archive.name)} 档案`));
          menu.append(row);
          check.addEventListener('change', async () => {
            const favorite = check.checked;
            check.disabled = true;
            try {
              await paperApi.request('/paper/api/favorites', {method:'POST', body:{paper_id:id, archive:archive.name, favorite}});
              if (owner !== state.principalId) return;
              if (!archive.name) {
                state.favoriteRevision++;
                if (favorite) state.favoriteIds.add(id); else state.favoriteIds.delete(id);
              }
              window.dispatchEvent(new CustomEvent('portal:paper-favorite-changed'));
            } catch (error) { check.checked = !favorite; menu.append(document.createTextNode(error.message)); }
            finally { check.disabled = false; }
          });
        }
        menu.querySelector('input')?.focus({preventScroll:true});
      } catch (error) { menu.textContent = error.message; }
    }
    window.PortalPaperPersonal.openArchivePicker = openPicker;
    show('recent', false);
    document.addEventListener('portal:paper-scope-restore',event=>show(['recent','favorites'].includes(event.detail.scope)?event.detail.scope:'repository'));
    window.addEventListener('portal:paper-principal-changed', accountChanged);
    window.addEventListener("portal:paper-favorite-changed", event => {
      if (event.detail?.personalHost === host && view === 'favorites') return;
      views.delete('favorites'); loadedViews.delete('favorites');
      if (view === 'favorites') page(true);
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
        if (account !== String(state.principalId || '') || view !== 'recent') return;
        status.textContent = error?.status === 409 || error?.message?.includes("session_changed")
          ? window.PortalI18n.t("账号已变化，请刷新身份并重新打开论文；本次阅读未记录。") : error?.message || String(error);
      });
    });
    window.addEventListener('portal:language-changed', () => {
      host.querySelector('[data-heading]').textContent = window.PortalI18n.t(view === 'recent' ? '近期阅读' : '我的收藏');
      panel.setAttribute('aria-label', host.querySelector('[data-heading]').textContent);
      if (view !== 'repository') renderPersonalTree();
    });
    window.PortalI18n.renderStatic?.();
  }};
})();
