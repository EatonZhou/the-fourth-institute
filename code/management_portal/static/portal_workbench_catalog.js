/* Workbench directory (decision_f67ae5e70793), laid out like the Project list:
   a heading with the directory's figures, search and filters, then the
   workbenches grouped by Project as cards. Presentation and local filtering
   only; workbench_studio.js reads the directory and the bound teams. */
(function () {
  'use strict';

  const mounted = new WeakMap();
  const createUrl = '/workbenches?panel=studio';
  // Group keys and project filter values besides Project IDs.
  const FAVORITE_GROUP = ':favorite';
  const BUILTIN_GROUP = ':builtin';
  const UNASSIGNED_GROUP = ':unassigned';
  const PUBLISH_FILTERS = [['', '全部状态'], ['published', '已发布'], ['draft', '草稿']];
  const t = (source, values) => window.PortalI18n.t(source, values);
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const settingsUrl = item => `/workbenches?panel=settings&id=${encodeURIComponent(item.workbench_id)}`;
  const projectUrl = id => `/projects?project=${encodeURIComponent(id)}`;
  const lineageUrl = id => `/lineage?lineage=${encodeURIComponent(id)}`;
  const published = item => Boolean(item.builtin || item.published_revision);
  // Versions, publication, edit times and settings concern those who may change the page.
  const manages = item => item.allows?.UpdateResource === true;
  const projectsOf = item => (item.project_ids || []).map(String);
  const updatedAt = item => Date.parse(item.updated_at || item.created_at || '') || 0;
  const mainLineage = item => {
    const bindings = item.bindings || {};
    return String((bindings.main || Object.values(bindings)[0] || {}).lineage_id || '');
  };
  // Every bound Lineage, the main one first; a workbench may bind several teams.
  const boundLineages = item => [...new Set([mainLineage(item),
    ...Object.values(item.bindings || {}).map(binding => String(binding?.lineage_id || ''))])].filter(Boolean);

  function hue(text) {
    let value = 0;
    for (const char of String(text || '')) value = (value * 31 + char.codePointAt(0)) % 360;
    return value;
  }

  // The name's first letter on a colour of its own, as a Project without an avatar shows.
  function markHtml(title) {
    const initial = Array.from(String(title || '?').trim())[0]?.toUpperCase() || '?';
    return `<span class="workbenchMark" style="--mark-hue:${hue(title)}" aria-hidden="true">${esc(initial)}</span>`;
  }

  function relativeTime(time, now = Date.now()) {
    if (!time) return '';
    const seconds = Math.max(0, Math.floor((now - time) / 1000));
    if (seconds < 60) return t('刚刚');
    if (seconds < 3600) return window.PortalI18n.text`${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86_400) return window.PortalI18n.text`${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86_400) return window.PortalI18n.text`${Math.floor(seconds / 86_400)} 天前`;
    return window.PortalUI.formatDateTime(time, { format: { year: 'numeric', month: 'numeric', day: 'numeric' } });
  }

  // The colour of the card's left edge: full while its team works, otherwise
  // fading from a page change just now to nothing over about a month.
  function heat(item, team, now = Date.now()) {
    if (team && !team.unavailable && Number(team.running || 0)) return 1;
    const time = updatedAt(item);
    const hours = Math.max(0, (now - time) / 3_600_000);
    const recent = time ? Math.max(0, Math.min(1, 1 - Math.log1p(hours) / Math.log1p(24 * 30))) : 0;
    return Math.max(team && Number(team.queued || 0) ? 0.6 : 0, recent * 0.7);
  }

  function mount(root, options = {}) {
    if (!(root instanceof Element)) throw new TypeError('Workbench catalog requires a root element');
    mounted.get(root)?.destroy();
    const items = Array.isArray(options.workbenches) ? options.workbenches.slice() : [];
    // Search, publication filter and folded groups are kept per user; the Project filter is in the address.
    const pageState = window.PortalPageState?.create('workbenches', {
      user: () => window.PortalIdentity?.username?.() || 'anonymous',
      defaults: { query: '', publish: '', collapsed: [] },
    });
    const saved = { ...(pageState?.read() || {}), ...(options.initialState || {}) };
    const state = {
      query: String(saved.query || ''),
      publish: PUBLISH_FILTERS.some(([key]) => key === saved.publish) ? saved.publish : '',
      project: String(options.initialState?.project ?? new URLSearchParams(location.search).get('project') ?? ''),
      collapsed: new Set(Array.isArray(saved.collapsed) ? saved.collapsed.map(String) : []),
    };
    let teams = new Map();
    let teamsRead = false;
    // Starred workbench IDs in the order they were starred; they lead the directory.
    let favorites = (Array.isArray(options.favorites) ? options.favorites : []).map(String);
    let destroyed = false;
    const container = document.createElement('div');
    container.className = 'workbenchCatalog';

    const starred = item => favorites.includes(item.workbench_id);
    // A workbench of several Projects stays in its first Project's group, unless the filter names another.
    const groupKey = item => {
      if (starred(item)) return FAVORITE_GROUP;
      if (item.builtin) return BUILTIN_GROUP;
      const projects = projectsOf(item);
      if (state.project && projects.includes(state.project)) return state.project;
      return projects[0] || UNASSIGNED_GROUP;
    };
    const groupLabel = key => ({ [FAVORITE_GROUP]: t('收藏'), [BUILTIN_GROUP]: t('Portal 内置'), [UNASSIGNED_GROUP]: t('未归属 Project') })[key] || key;
    const isProjectGroup = key => !key.startsWith(':');

    function persist() {
      pageState?.write({ query: state.query, publish: state.publish, collapsed: [...state.collapsed] });
    }

    function inProject(item) {
      if (!state.project) return true;
      if (state.project === BUILTIN_GROUP) return Boolean(item.builtin);
      if (state.project === UNASSIGNED_GROUP) return !item.builtin && !projectsOf(item).length;
      return !item.builtin && projectsOf(item).includes(state.project);
    }

    function matches(item, query) {
      if (state.publish && published(item) !== (state.publish === 'published')) return false;
      if (!inProject(item)) return false;
      if (!query) return true;
      const lineages = boundLineages(item);
      return [item.title, item.description, ...projectsOf(item), ...lineages, ...lineages.map(id => teams.get(id)?.title)]
        .some(value => String(value || '').toLocaleLowerCase().includes(query));
    }

    function statsHtml(visible) {
      const lineages = new Set(visible.flatMap(boundLineages));
      const running = [...lineages].reduce((total, id) => total + (teams.get(id)?.unavailable ? 0 : Number(teams.get(id)?.running || 0)), 0);
      const count = visible.length === items.length ? String(items.length) : `${visible.length} / ${items.length}`;
      return window.PortalI18n.html`<span><strong>${count}</strong> 个工作台</span>
        <span><strong>${lineages.size}</strong> 个团队</span>
        <span class="${running ? 'is-live' : ''}"><strong>${teamsRead ? running : '…'}</strong> 个 Resident 正在工作</span>`;
    }

    function optionsHtml(values, selected) {
      return values.map(([value, label]) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`).join('');
    }

    function toolsHtml() {
      const projects = [...new Set(items.filter(item => !item.builtin).flatMap(projectsOf))].sort((left, right) => left.localeCompare(right));
      const projectOptions = [['', t('全部 Project')], ...projects.map(project => [project, project])];
      if (items.some(item => !item.builtin && !projectsOf(item).length)) projectOptions.push([UNASSIGNED_GROUP, t('未归属 Project')]);
      if (items.some(item => item.builtin)) projectOptions.push([BUILTIN_GROUP, t('Portal 内置')]);
      // An address naming a Project with no workbench here still shows it, so the empty result explains itself.
      if (state.project && !projectOptions.some(([value]) => value === state.project)) projectOptions.push([state.project, state.project]);
      // Only those who may change a workbench see drafts, so only they need the publication filter.
      const publish = items.some(item => manages(item) && !item.builtin)
        ? `<select data-workbench-filter="publish" aria-label="${esc(t('发布状态'))}">${optionsHtml(PUBLISH_FILTERS.map(([key, label]) => [key, t(label)]), state.publish)}</select>`
        : '';
      const refresh = typeof options.onRefresh === 'function'
        ? `<button class="portalButton is-icon" type="button" data-workbench-refresh data-portal-icon="refresh" aria-label="${esc(t('刷新工作台'))}" title="${esc(t('刷新'))}"></button>`
        : '';
      return `<div class="workbenchListTools" role="search" aria-label="${esc(t('工作台筛选'))}">
        <label class="workbenchSearch"><span class="srOnly">${esc(t('搜索工作台名称或描述'))}</span>
          <input type="search" data-workbench-query placeholder="${esc(t('搜索名称、说明、Project 或团队'))}" value="${esc(state.query)}" autocomplete="off" /></label>
        <select data-workbench-filter="project" aria-label="${esc(t('所属 Project'))}">${optionsHtml(projectOptions, state.project)}</select>
        ${publish}${refresh}
        <a class="portalButton is-primary" href="${createUrl}" data-workbench-create data-portal-icon="plus"><span>${esc(t('创建工作台'))}</span></a>
      </div>`;
    }

    // The main team: Leader avatar, Lineage name, members and current work; other bound teams as "+N".
    function teamHtml(item) {
      const lineageId = mainLineage(item);
      if (!lineageId) return `<footer class="workbenchCardTeam"><span class="workbenchTeamEmpty">${esc(t('未绑定团队'))}</span></footer>`;
      const team = teams.get(lineageId);
      if (!team) return `<footer class="workbenchCardTeam is-loading"><span class="workbenchTeamEmpty">${esc(t('正在读取团队…'))}</span></footer>`;
      const title = team.title || lineageId;
      const portrait = team.avatar && window.PortalResidentAvatar
        ? window.PortalResidentAvatar.markup(team.avatar, { label: title, size: 22 })
        : '<span data-portal-icon="users"></span>';
      const others = boundLineages(item).slice(1);
      const more = others.length ? (() => {
        const label = window.PortalI18n.text`另绑定团队：${others.map(id => teams.get(id)?.title || id).join('、')}`;
        return `<span class="workbenchTeamMore" title="${esc(label)}" aria-label="${esc(label)}">${esc(window.PortalI18n.text`+${others.length} 个团队`)}</span>`;
      })() : '';
      const running = Number(team.running || 0);
      const queued = Number(team.queued || 0);
      let activity = '';
      if (!team.unavailable && (running || queued)) {
        activity = `<span class="workbenchActivity${running ? ' is-running' : ' is-queued'}">${running ? '<i class="workbenchPulse" aria-hidden="true"></i>' : ''}${esc(queued
          ? window.PortalI18n.text`${running} 在工作 · ${queued} 排队`
          : window.PortalI18n.text`${running} 在工作`)}</span>`;
      } else if (!team.unavailable) {
        activity = `<span class="workbenchActivity">${esc(t('空闲'))}</span>`;
      }
      return `<footer class="workbenchCardTeam">
        <span class="workbenchTeamAvatar">${portrait}</span>
        <a class="workbenchTeamName" href="${esc(lineageUrl(lineageId))}" title="${esc(`${title} · ${t(team.unavailable ? '团队信息暂不可读取' : '打开团队 Lineage')}`)}">${esc(title)}</a>
        <span class="workbenchTeamDetail">${esc(team.unavailable ? t('团队信息暂不可读取') : window.PortalI18n.text`${Number(team.member_count || 0)} 位成员`)}</span>
        ${more}${activity}
      </footer>`;
    }

    function cardHtml(item, group) {
      const title = String(item.title || t('未命名工作台'));
      const description = String(item.description || '');
      // Published pages open; a draft continues in its editor.
      const open = published(item) && item.url;
      const href = open ? item.url : settingsUrl(item);
      const action = t(open ? '打开工作台' : '继续编辑');
      const managed = manages(item);
      const marks = [];
      // Built-in entries are always live; their group already says so.
      if (managed && !item.builtin) {
        marks.push(published(item)
          ? `<span class="workbenchState is-published">${esc(window.PortalI18n.text`已发布 v${item.published_revision}`)}</span>`
          : `<span class="workbenchState">${esc(window.PortalI18n.text`草稿 v${item.revision || 1}`)}</span>`);
        if (item.published_revision && item.revision > item.published_revision) {
          marks.push(`<span class="workbenchState is-pending">${esc(t('有未发布修改'))}</span>`);
        }
      }
      const time = managed ? updatedAt(item) : 0;
      if (time) {
        const exact = window.PortalUI.formatDateTime(time, { style: 'calendar-minute', shortYear: true });
        marks.push(`<span class="workbenchUpdated" title="${esc(exact)}">${esc(window.PortalI18n.text`${relativeTime(time)}更新`)}</span>`);
      }
      const chips = [
        // The Projects besides the one its group names.
        ...projectsOf(item).filter(project => project !== group).map(project =>
          `<a class="workbenchChip" href="${esc(projectUrl(project))}" title="${esc(t('打开项目'))}">${esc(project)}</a>`),
        item.navigation === 'top' ? `<span class="workbenchChip">${esc(t('顶部导航'))}</span>` : '',
      ].filter(Boolean).join('');
      const on = starred(item);
      const starLabel = t(on ? '取消收藏' : '添加到收藏');
      // The same star as a Resident's favorite.
      const star = typeof options.onFavorite === 'function'
        ? `<button class="workbenchFavorite${on ? ' is-on' : ''}" type="button" data-workbench-favorite="${esc(item.workbench_id)}" aria-pressed="${on}" data-portal-icon="${on ? 'star-filled' : 'star'}" aria-label="${esc(`${starLabel} · ${title}`)}" title="${esc(starLabel)}"></button>`
        : '';
      const settings = managed
        ? `<a class="workbenchCardSettings" href="${esc(settingsUrl(item))}" data-workbench-settings data-portal-icon="settings" aria-label="${esc(`${t('页面与绑定设置')} · ${title}`)}" title="${esc(t('页面与绑定设置'))}"></a>`
        : '';
      return `<article class="workbenchCard${item.builtin ? ' is-builtin' : ''}${open ? '' : ' is-draft'}" data-workbench-id="${esc(item.workbench_id)}" data-workbench-href="${esc(href)}" style="--heat:${heat(item, teams.get(mainLineage(item))).toFixed(2)}">
        <a class="workbenchCardLink" href="${esc(href)}" data-workbench-open aria-label="${esc(`${action} · ${title}`)}"></a>
        <header class="workbenchCardHead">
          ${markHtml(title)}
          <div class="workbenchCardTitle"><h3>${esc(title)}</h3>${marks.length ? `<div class="workbenchCardMarks">${marks.join('')}</div>` : ''}</div>
          <div class="workbenchCardTools">${settings}${star}</div>
        </header>
        <p class="workbenchCardDescription${description ? '' : ' is-empty'}"${description ? ` title="${esc(description)}"` : ''}>${esc(description || t('暂无说明'))}</p>
        ${chips ? `<div class="workbenchCardChips">${chips}</div>` : ''}
        ${teamHtml(item)}
      </article>`;
    }

    function groupHtml(key, entries) {
      const label = groupLabel(key);
      const collapsed = state.collapsed.has(key);
      return `<section class="workbenchGroup${collapsed ? ' is-collapsed' : ''}" data-workbench-group="${esc(key)}" aria-label="${esc(label)}" style="--cards:${entries.length}">
        <div class="workbenchGroupHead">
          <button class="workbenchGroupToggle" type="button" data-workbench-group-toggle aria-expanded="${!collapsed}">
            <span>${esc(label)}</span><span class="workbenchGroupCount">${entries.length}</span>
          </button>
          ${isProjectGroup(key) ? `<a class="workbenchGroupLink" href="${esc(projectUrl(key))}" data-portal-icon="arrow-up-right"><span>${esc(t('项目主页'))}</span></a>` : ''}
        </div>
        <div class="workbenchGrid"${collapsed ? ' hidden' : ''}>${entries.map(item => cardHtml(item, key)).join('')}</div>
      </section>`;
    }

    function emptyHtml(filtered) {
      return `<div class="workbenchCatalogEmpty">
        <span class="workbenchCatalogEmptyMark" data-portal-icon="${filtered ? 'search' : 'window'}"></span>
        <h2>${esc(t(filtered ? '没有匹配的工作台' : '创建你的第一个工作台'))}</h2>
        <p>${esc(t(filtered ? '试试其他关键词，或清除筛选查看全部工作台。' : '把常用工具与团队工作放在一个页面里。'))}</p>
        ${filtered
          ? `<button class="portalButton" type="button" data-workbench-reset>${esc(t('清除筛选'))}</button>`
          : `<a class="portalButton is-primary" href="${createUrl}" data-portal-icon="plus"><span>${esc(t('创建工作台'))}</span></a>`}
      </div>`;
    }

    function renderResults() {
      const query = state.query.trim().toLocaleLowerCase();
      const visible = items.filter(item => matches(item, query));
      container.querySelector('[data-workbench-stats]').innerHTML = statsHtml(visible);
      const groups = new Map();
      for (const item of visible) {
        const key = groupKey(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      }
      // Starred first, then Projects by their latest change, then workbenches without one;
      // the built-in entries come last because the top navigation already leads to them.
      const order = { [FAVORITE_GROUP]: 0, [UNASSIGNED_GROUP]: 2, [BUILTIN_GROUP]: 3 };
      const rank = ([key, entries]) => [order[key] ?? 1, -Math.max(0, ...entries.map(updatedAt))];
      const ordered = [...groups.entries()].sort((left, right) => {
        const [a, b] = [rank(left), rank(right)];
        return a[0] - b[0] || a[1] - b[1] || left[0].localeCompare(right[0]);
      });
      for (const [key, entries] of ordered) {
        if (key === FAVORITE_GROUP) entries.sort((left, right) => favorites.indexOf(left.workbench_id) - favorites.indexOf(right.workbench_id));
        else entries.sort((left, right) => updatedAt(right) - updatedAt(left) || String(left.title || '').localeCompare(String(right.title || '')));
      }
      const node = container.querySelector('[data-workbench-groups]');
      const focused = focusedControl(node);
      node.innerHTML = visible.length
        ? ordered.map(([key, entries]) => groupHtml(key, entries)).join('')
        : emptyHtml(Boolean(query || state.publish || state.project));
      window.PortalUIPrimitives.mountIcons(node);
      if (focused) node.querySelector(focused)?.focus({ preventScroll: true });
    }

    // Redrawing the cards (teams arriving, a star moving a card) keeps the keyboard
    // focus on the same control of the same card or group.
    function focusedControl(node) {
      const active = document.activeElement;
      if (!active || !node.contains(active)) return '';
      const control = ['data-workbench-open', 'data-workbench-favorite', 'data-workbench-settings', 'data-workbench-group-toggle']
        .find(name => active.hasAttribute(name));
      if (!control) return '';
      const card = active.closest('[data-workbench-id]');
      if (card) return `[data-workbench-id="${CSS.escape(card.dataset.workbenchId)}"] [${control}]`;
      const group = active.closest('[data-workbench-group]');
      return group ? `[data-workbench-group="${CSS.escape(group.dataset.workbenchGroup)}"] [${control}]` : '';
    }

    function render() {
      container.innerHTML = `<header class="workbenchListHero">
          <div class="workbenchListHeading">
            <h1>${esc(t('Workbenches'))}</h1>
            <p class="workbenchListStats" data-workbench-stats data-portal-board-meta role="status" aria-live="polite" aria-atomic="true"></p>
          </div>
          ${toolsHtml()}
        </header>
        <div class="workbenchGroups" data-workbench-groups></div>`;
      window.PortalUIPrimitives.mountIcons(container.querySelector('.workbenchListTools'));
      renderResults();
    }

    function setProject(project) {
      state.project = project;
      const url = new URL(location.href);
      if (project) url.searchParams.set('project', project); else url.searchParams.delete('project');
      history.replaceState(history.state, '', url);
    }

    async function toggleFavorite(id) {
      const favorite = !favorites.includes(id);
      const previous = favorites;
      // The card moves between groups; its star keeps the focus without scrolling the page.
      const show = next => {
        favorites = next;
        renderResults();
        container.querySelector(`[data-workbench-favorite="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
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

    container.addEventListener('click', event => {
      const toggle = event.target.closest('[data-workbench-group-toggle]');
      if (toggle) {
        const section = toggle.closest('[data-workbench-group]');
        const key = section.dataset.workbenchGroup;
        const collapsed = !state.collapsed.has(key);
        if (collapsed) state.collapsed.add(key); else state.collapsed.delete(key);
        toggle.setAttribute('aria-expanded', String(!collapsed));
        section.classList.toggle('is-collapsed', collapsed);
        section.querySelector('.workbenchGrid').hidden = collapsed;
        persist();
        return;
      }
      const star = event.target.closest('[data-workbench-favorite]');
      if (star) {
        void toggleFavorite(star.dataset.workbenchFavorite);
        return;
      }
      if (event.target.closest('[data-workbench-refresh]')) {
        options.onRefresh?.(api.snapshot());
        return;
      }
      if (event.target.closest('[data-workbench-reset]')) {
        state.query = '';
        state.publish = '';
        setProject('');
        persist();
        render();
        container.querySelector('[data-workbench-query]')?.focus();
      }
    });
    container.addEventListener('input', event => {
      if (!event.target.matches('[data-workbench-query]')) return;
      state.query = event.target.value;
      persist();
      renderResults();
    });
    container.addEventListener('change', event => {
      const filter = event.target.closest('[data-workbench-filter]');
      if (!filter) return;
      if (filter.dataset.workbenchFilter === 'project') setProject(filter.value);
      else state.publish = filter.value;
      persist();
      renderResults();
    });

    render();
    root.append(container);
    window.addEventListener('portal:language-changed', render);
    const api = {
      // Team summaries keyed by Lineage ID, read after the directory is shown.
      setTeams(next) {
        if (destroyed) return;
        teams = next instanceof Map ? next : new Map(Object.entries(next || {}));
        teamsRead = true;
        renderResults();
      },
      snapshot() {
        return { query: state.query, publish: state.publish, project: state.project, collapsed: [...state.collapsed] };
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        window.removeEventListener('portal:language-changed', render);
        pageState?.destroy?.();
        container.remove();
        if (mounted.get(root) === api) mounted.delete(root);
      },
    };
    mounted.set(root, api);
    return api;
  }

  window.PortalWorkbenchCatalog = Object.freeze({ mount, mainLineage, boundLineages });
})();
