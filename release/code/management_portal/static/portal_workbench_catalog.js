/* Workbench directory: presentation and local filtering only. */
(function () {
  'use strict';

  const mounted = new WeakMap();
  const createUrl = '/workbenches?panel=studio';
  const FAVORITE_GROUP = '\u0000favorite';
  const BUILTIN_GROUP = '\u0000builtin';
  const UNASSIGNED_GROUP = '\u0000unassigned';
  const icons = {
    workbench: 'M3 4h18v16H3zM3 9h18M9 9v11',
    arrow: 'M5 12h14M13 6l6 6-6 6',
    settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
    plus: 'M12 5v14M5 12h14',
    search: 'M21 21l-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z',
  };
  const filters = [['all', '全部'], ['published', '已发布'], ['draft', '草稿']];
  const t = (source, values) => window.PortalI18n.t(source, values);
  const settingsUrl = item => `/workbenches?panel=settings&id=${encodeURIComponent(item.workbench_id)}`;
  const published = item => Boolean(item.builtin || item.published_revision);
  const updatedAt = item => Date.parse(item.updated_at || item.created_at || '') || 0;
  const mainLineage = item => {
    const bindings = item.bindings || {};
    return String((bindings.main || Object.values(bindings)[0] || {}).lineage_id || '');
  };
  // Every bound Lineage, the main one first; a workbench may bind several teams.
  const boundLineages = item => [...new Set([mainLineage(item),
    ...Object.values(item.bindings || {}).map(binding => String(binding?.lineage_id || ''))])].filter(Boolean);

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.8');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('workbenchCatalogIcon');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', icons[name]);
    svg.append(path);
    return svg;
  }

  function link(text, href, className) {
    const node = element('a', className, text);
    node.href = href;
    return node;
  }

  // A team summary: { title, member_count, running, queued, avatar } or { unavailable: true }.
  // Teams bound besides the main one are named in a "+N" chip.
  function teamRow(lineageId, team, others = []) {
    const row = element('div', 'workbenchCatalogTeam');
    if (!lineageId) {
      row.append(element('span', 'workbenchCatalogTeamEmpty', t('未绑定团队')));
      return row;
    }
    if (!team) {
      row.classList.add('is-loading');
      row.append(element('span', 'workbenchCatalogTeamEmpty', t('正在读取团队…')));
      return row;
    }
    const portrait = element('span', 'workbenchCatalogTeamAvatar');
    if (team.avatar && window.PortalResidentAvatar) {
      portrait.innerHTML = window.PortalResidentAvatar.markup(team.avatar, { label: team.title || lineageId, size: 24 });
    } else {
      portrait.append(icon('workbench'));
    }
    const name = link(team.title || lineageId, `/lineage?lineage=${encodeURIComponent(lineageId)}`, 'workbenchCatalogTeamName');
    name.title = `${team.title || lineageId} · ${t(team.unavailable ? '团队信息暂不可读取' : '打开团队 Lineage')}`;
    const detail = element('span', 'workbenchCatalogTeamDetail');
    if (team.unavailable) {
      detail.textContent = t('团队信息暂不可读取');
    } else {
      const members = Number(team.member_count || 0);
      detail.textContent = window.PortalI18n.text`${members} 位成员`;
    }
    const activity = element('span', 'workbenchCatalogActivity');
    const running = Number(team.running || 0);
    const queued = Number(team.queued || 0);
    if (!team.unavailable && (running || queued)) {
      activity.classList.add('is-active');
      activity.textContent = queued
        ? window.PortalI18n.text`${running} 在工作 · ${queued} 排队`
        : window.PortalI18n.text`${running} 在工作`;
    } else if (!team.unavailable) {
      activity.textContent = t('空闲');
    }
    row.append(portrait, name, detail);
    if (others.length) {
      const names = others.map(other => other.title || other.lineage_id).join('、');
      const more = element('span', 'workbenchCatalogTeamMore', window.PortalI18n.text`+${others.length} 个团队`);
      more.title = window.PortalI18n.text`另绑定团队：${names}`;
      more.setAttribute('aria-label', more.title);
      row.append(more);
    }
    if (activity.textContent) row.append(activity);
    return row;
  }

  function mount(root, options = {}) {
    if (!(root instanceof Element)) throw new TypeError('Workbench catalog requires a root element');
    mounted.get(root)?.destroy();
    const items = Array.isArray(options.workbenches) ? options.workbenches.slice() : [];
    const initial = options.initialState || {};
    const state = {
      query: String(initial.query || ''),
      filter: filters.some(([key]) => key === initial.filter) ? initial.filter : 'all',
      collapsed: new Set(initial.collapsed || []),
    };
    let teams = new Map();
    // Starred workbench IDs in the order they were starred; they lead the directory.
    let favorites = (Array.isArray(options.favorites) ? options.favorites : []).map(String);
    const container = element('div', 'workbenchCatalog');
    let searchInput;
    let resultCount;
    let results;
    let filterInputs;
    let filterButton;
    let destroyed = false;

    const teamOf = item => teams.get(mainLineage(item));
    const starred = item => favorites.includes(item.workbench_id);
    const groupKey = item => starred(item) ? FAVORITE_GROUP : item.builtin ? BUILTIN_GROUP
      : String((item.project_ids || [])[0] || UNASSIGNED_GROUP);
    const groupLabel = key => ({ [FAVORITE_GROUP]: t('收藏'), [BUILTIN_GROUP]: t('Portal 内置'), [UNASSIGNED_GROUP]: t('未归属 Project') })[key] || key;

    function favoriteButton(item, title) {
      const on = starred(item);
      const button = element('button', `workbenchCatalogFavorite${on ? ' is-on' : ''}`);
      button.type = 'button';
      button.dataset.workbenchFavorite = item.workbench_id;
      button.setAttribute('aria-pressed', String(on));
      // The same star as a Resident's favorite.
      button.dataset.portalIcon = on ? 'star-filled' : 'star';
      button.setAttribute('aria-label', `${t(on ? '取消收藏' : '添加到收藏')} · ${title}`);
      button.title = t(on ? '取消收藏' : '添加到收藏');
      return button;
    }

    async function toggleFavorite(id) {
      if (typeof options.onFavorite !== 'function') return;
      const favorite = !favorites.includes(id);
      const previous = favorites;
      // The card moves between groups; its star keeps the focus without scrolling the page.
      const show = next => {
        favorites = next;
        renderResults();
        container.querySelector(`[data-workbench-favorite="${id}"]`)?.focus({ preventScroll: true });
      };
      show(favorite ? [...favorites, id] : favorites.filter(item => item !== id));
      try {
        const saved = await options.onFavorite(id, favorite);
        if (!destroyed && Array.isArray(saved)) show(saved.map(String));
      } catch (error) {
        if (destroyed) return;
        show(previous);
        window.PortalUI?.toast?.(window.PortalI18n.text`${t(favorite ? '添加收藏' : '取消收藏')}失败：${error.message || error}`, 'error');
      }
    }

    function card(item) {
      const title = String(item.title || t('未命名工作台'));
      const description = String(item.description || '');
      const open = published(item) && item.url;
      const href = open ? item.url : settingsUrl(item);
      const entry = element('article', `portalItemCard workbenchCatalogCard${item.builtin ? ' is-builtin' : ''}`);
      entry.dataset.workbenchId = item.workbench_id;
      entry.dataset.workbenchHref = href;

      // Versions, publication and edit times concern those who maintain the page.
      const manages = item.allows?.UpdateResource === true;
      const head = element('div', 'workbenchCatalogCardHead');
      const heading = element('h3', 'portalItemTitle workbenchCatalogTitle');
      heading.append(link(title, href, 'workbenchCatalogTitleLink'));
      head.append(heading);
      // Built-in entries are always live; their group already says so.
      if (!item.builtin && manages) {
        const status = element('span', `workbenchCatalogStatus${published(item) ? ' is-published' : ''}`);
        status.textContent = published(item)
          ? window.PortalI18n.text`已发布 v${item.published_revision}`
          : window.PortalI18n.text`草稿 v${item.revision || 1}`;
        head.append(status);
      }
      if (typeof options.onFavorite === 'function') head.append(favoriteButton(item, title));
      entry.append(head);

      const meta = element('div', 'portalCollectionCardMeta workbenchCatalogMeta');
      const time = manages ? updatedAt(item) : 0;
      if (time) {
        meta.append(element('span', 'portalCollectionItemMeta',
          window.PortalI18n.text`${window.PortalUI.formatDateTime(time, { style: 'calendar-minute', shortYear: true })} 更新`));
      }
      const badges = element('span', 'portalCollectionBadges');
      if (manages && !item.builtin && item.published_revision && item.revision > item.published_revision) {
        badges.append(element('span', 'portalCollectionBadge is-pending', t('有未发布修改')));
      }
      for (const project of (item.project_ids || []).slice(1)) badges.append(element('span', 'portalCollectionBadge', project));
      if (item.navigation === 'top') badges.append(element('span', 'portalCollectionBadge', t('顶部导航')));
      meta.append(badges);
      if (meta.textContent) entry.append(meta);

      const preview = element('p', `portalItemPreview workbenchCatalogDescription${description ? '' : ' is-empty'}`, description || t('暂无说明'));
      if (description) preview.title = description;
      entry.append(preview);

      const others = boundLineages(item).slice(1).map(id => ({ lineage_id: id, title: teams.get(id)?.title || '' }));
      entry.append(teamRow(mainLineage(item), teamOf(item), others));

      const actions = element('div', 'workbenchCatalogActions');
      const primary = link(t(open ? '打开工作台' : '继续编辑'), href, 'workbenchCatalogOpen');
      primary.append(icon('arrow'));
      primary.setAttribute('aria-label', `${t(open ? '打开工作台' : '继续编辑')} · ${title}`);
      const settings = link('', settingsUrl(item), 'workbenchCatalogSettings');
      settings.append(icon('settings'), element('span', '', t('设置')));
      settings.setAttribute('aria-label', `${t('页面与绑定设置')} · ${title}`);
      settings.title = t('页面与绑定设置');
      actions.append(primary);
      // Settings change the page: the link asks for UpdateResource.
      if (manages) actions.append(settings);
      entry.append(actions);
      return entry;
    }

    function group(key, entries) {
      const label = groupLabel(key);
      const collapsed = state.collapsed.has(key);
      const section = element('section', `portalProjectGroup workbenchCatalogGroup${collapsed ? ' collapsed' : ''}`);
      section.dataset.workbenchGroup = key;
      section.setAttribute('aria-label', label);
      const header = element('header', 'portalProjectHeader');
      const actions = element('div', 'portalProjectActions');
      const toggle = element('button', 'portalProjectToggle');
      toggle.type = 'button';
      toggle.dataset.portalIcon = 'chevron-down';
      toggle.dataset.workbenchGroupToggle = '';
      toggle.setAttribute('aria-expanded', String(!collapsed));
      toggle.setAttribute('aria-label', `${t(collapsed ? '展开' : '折叠')} ${label}`);
      actions.append(element('span', 'portalProjectCount', window.PortalI18n.text`${entries.length} 个`), toggle);
      header.append(element('h2', 'portalProjectTitle', label), actions);
      const grid = element('div', 'portalProjectItems workbenchCatalogItems');
      entries.forEach(item => grid.append(card(item)));
      section.append(header, grid);
      return section;
    }

    function matches(item, query) {
      if (state.filter !== 'all' && published(item) !== (state.filter === 'published')) return false;
      if (!query) return true;
      const lineages = boundLineages(item);
      return [item.title, item.description, ...(item.project_ids || []), ...lineages, ...lineages.map(id => teams.get(id)?.title)]
        .some(value => String(value || '').toLocaleLowerCase().includes(query));
    }

    function renderResults() {
      const query = state.query.trim().toLocaleLowerCase();
      const filtered = items.filter(item => matches(item, query));
      resultCount.textContent = filtered.length === 1 ? t('1 个工作台') : window.PortalI18n.text`${filtered.length} 个工作台`;
      for (const [key, input] of filterInputs) input.checked = key === state.filter;
      filterButton.classList.toggle('is-filtered', state.filter !== 'all');
      results.replaceChildren();
      const groups = new Map();
      for (const item of filtered) {
        const key = groupKey(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      }
      // Starred first, then Projects, then workbenches without one; the built-in
      // entries come last because the top navigation already leads to them.
      const order = { [FAVORITE_GROUP]: 0, [UNASSIGNED_GROUP]: 2, [BUILTIN_GROUP]: 3 };
      const rank = ([key, entries]) => [order[key] ?? 1, -Math.max(0, ...entries.map(updatedAt))];
      const ordered = [...groups.entries()].sort((left, right) => {
        const [a, b] = [rank(left), rank(right)];
        return a[0] - b[0] || a[1] - b[1] || left[0].localeCompare(right[0]);
      });
      for (const [key, entries] of ordered) {
        if (key === FAVORITE_GROUP) entries.sort((left, right) => favorites.indexOf(left.workbench_id) - favorites.indexOf(right.workbench_id));
        else entries.sort((left, right) => updatedAt(right) - updatedAt(left) || String(left.title || '').localeCompare(String(right.title || '')));
        results.append(group(key, entries));
      }
      if (!filtered.length) {
        const hasFilter = Boolean(query || state.filter !== 'all');
        const empty = element('div', 'workbenchCatalogEmpty');
        const mark = element('span', 'workbenchCatalogEmptyMark');
        mark.append(icon(hasFilter ? 'search' : 'workbench'));
        empty.append(mark,
          element('h2', '', t(hasFilter ? '没有匹配的工作台' : '创建你的第一个工作台')),
          element('p', '', t(hasFilter ? '试试其他关键词，或清除筛选查看全部工作台。' : '把常用工具与团队工作放在一个页面里。')));
        if (hasFilter) {
          const reset = element('button', 'portalButton', t('清除筛选'));
          reset.type = 'button';
          reset.addEventListener('click', () => {
            state.query = '';
            state.filter = 'all';
            searchInput.value = '';
            renderResults();
            searchInput.focus();
          });
          empty.append(reset);
        } else {
          const create = link(t('创建工作台'), createUrl, 'portalButton is-primary');
          create.prepend(icon('plus'));
          empty.append(create);
        }
        results.append(empty);
      }
    }

    function toolbar() {
      const header = element('div', 'portalBoardHeader workbenchCatalogHeader');
      const bar = element('div');
      bar.dataset.portalBoardToolbar = '';
      bar.setAttribute('aria-label', t('工作台筛选'));

      const search = element('div');
      search.dataset.portalBoardSearch = '';
      const searchLabel = element('label', '', t('搜索'));
      searchLabel.htmlFor = 'workbenchCatalogQuery';
      searchInput = element('input');
      searchInput.id = 'workbenchCatalogQuery';
      searchInput.type = 'search';
      searchInput.dataset.portalBoardSearchInput = '';
      searchInput.placeholder = t('名称 / 说明 / Project / 团队');
      searchInput.setAttribute('aria-label', t('搜索工作台名称或描述'));
      searchInput.autocomplete = 'off';
      searchInput.value = state.query;
      searchInput.addEventListener('input', () => {
        state.query = searchInput.value;
        renderResults();
      });
      search.append(searchLabel, searchInput);

      const actions = element('div');
      actions.dataset.portalBoardActions = '';
      const filter = element('div');
      filter.dataset.portalBoardFilter = '';
      filter.dataset.portalBoardFilterAuto = '';
      filter.dataset.portalToolbarItem = '';
      filter.dataset.portalToolbarPriority = 'normal';
      filterButton = element('button');
      filterButton.type = 'button';
      filterButton.dataset.portalBoardAction = 'filter';
      filterButton.setAttribute('aria-expanded', 'false');
      filterButton.setAttribute('aria-controls', 'workbenchCatalogFilterPanel');
      filterButton.append(element('span', '', t('筛选')));
      const panel = element('div', 'portalCollectionFilterPanel');
      panel.id = 'workbenchCatalogFilterPanel';
      panel.dataset.portalBoardFilterPanel = '';
      panel.hidden = true;
      const optionList = element('div', 'portalCollectionFilterOptions');
      optionList.setAttribute('role', 'radiogroup');
      optionList.setAttribute('aria-label', t('发布状态'));
      filterInputs = new Map();
      for (const [key, label] of filters) {
        const option = element('label', 'portalCollectionFilterOption');
        const input = element('input');
        input.type = 'radio';
        input.name = 'workbenchCatalogPublishState';
        input.value = key;
        input.addEventListener('change', () => {
          state.filter = key;
          renderResults();
        });
        filterInputs.set(key, input);
        option.append(input, element('span', '', t(label)));
        optionList.append(option);
      }
      panel.append(element('div', 'portalCollectionFilterLabel', t('发布状态')), optionList);
      filter.append(filterButton, panel);

      const refresh = element('button');
      refresh.type = 'button';
      refresh.dataset.portalBoardAction = 'refresh';
      refresh.dataset.portalToolbarItem = '';
      refresh.dataset.portalToolbarPriority = 'low';
      refresh.setAttribute('aria-label', t('刷新工作台'));
      refresh.title = t('刷新');
      refresh.hidden = typeof options.onRefresh !== 'function';
      refresh.addEventListener('click', () => options.onRefresh?.(api.snapshot()));

      const create = link('', createUrl, 'portalButton is-compact is-primary workbenchCatalogCreate');
      create.dataset.portalToolbarItem = '';
      create.dataset.portalToolbarPriority = 'critical';
      create.dataset.workbenchCreate = '';
      create.append(icon('plus'), element('span', '', t('创建工作台')));
      actions.append(filter, refresh, create);

      resultCount = element('span', 'workbenchCatalogResultCount');
      resultCount.dataset.portalBoardMeta = '';
      resultCount.setAttribute('role', 'status');
      resultCount.setAttribute('aria-live', 'polite');
      resultCount.setAttribute('aria-atomic', 'true');
      bar.append(search, actions, resultCount);
      header.append(bar);
      return { header, bar };
    }

    function render() {
      const { header, bar } = toolbar();
      results = element('div', 'portalProjectCollection workbenchCatalogResults');
      container.replaceChildren(header, results);
      renderResults();
      window.PortalBoardToolbar?.mount(bar);
    }

    function onResultsClick(event) {
      const toggle = event.target.closest('[data-workbench-group-toggle]');
      if (toggle) {
        const section = toggle.closest('[data-workbench-group]');
        const key = section.dataset.workbenchGroup;
        const collapsed = section.classList.toggle('collapsed');
        if (collapsed) state.collapsed.add(key); else state.collapsed.delete(key);
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.setAttribute('aria-label', `${t(collapsed ? '展开' : '折叠')} ${groupLabel(key)}`);
        return;
      }
      const star = event.target.closest('[data-workbench-favorite]');
      if (star) {
        void toggleFavorite(star.dataset.workbenchFavorite);
        return;
      }
      // The whole card opens its workbench; links and buttons inside keep their own targets.
      if (event.target.closest('a, button, input, label')) return;
      const entry = event.target.closest('[data-workbench-href]');
      if (entry && !window.getSelection()?.toString()) location.assign(entry.dataset.workbenchHref);
    }

    render();
    container.addEventListener('click', onResultsClick);
    root.append(container);
    window.addEventListener('portal:language-changed', render);
    const api = {
      // Team summaries keyed by Lineage ID, read after the directory is shown.
      setTeams(next) {
        if (destroyed) return;
        teams = next instanceof Map ? next : new Map(Object.entries(next || {}));
        renderResults();
      },
      snapshot() {
        return { query: state.query, filter: state.filter, collapsed: [...state.collapsed] };
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        window.removeEventListener('portal:language-changed', render);
        container.remove();
        if (mounted.get(root) === api) mounted.delete(root);
      },
    };
    mounted.set(root, api);
    return api;
  }

  window.PortalWorkbenchCatalog = Object.freeze({ mount, mainLineage, boundLineages });
})();
