(function () {
  "use strict";

  // The Project list (decision_46c37026e8ea): every Project the user can see,
  // grouped by status and ordered by its latest change.  Its data is one
  // ListProjects call; changing a status is one UpdateProject call.
  if (window.PortalProjectList) return;

  const common = () => window.PortalProjectCommon;
  const GROUPS = Object.freeze([
    { id: "active", title: "进行中", statuses: ["active"] },
    { id: "waiting", title: "未开始与暂停", statuses: ["not_started", "paused"] },
    { id: "closed", title: "已完成与归档", statuses: ["done", "archived"], collapsed: true },
  ]);

  function groupOf(status) {
    return GROUPS.find(group => group.statuses.includes(status)) || GROUPS[0];
  }

  function matches(project, filters) {
    if (filters.type && project.project_type !== filters.type) return false;
    if (filters.tag && !(project.tags || []).includes(filters.tag)) return false;
    const query = String(filters.query || "").trim().toLocaleLowerCase();
    if (!query) return true;
    return [project.project_id, project.description, ...(project.tags || [])]
      .some(value => String(value || "").toLocaleLowerCase().includes(query));
  }

  function create({ root, onOpen, onCreate } = {}) {
    if (!root) throw new TypeError("PortalProjectList.create requires root");
    const { esc, t } = common();
    const pageState = window.PortalPageState.create("projects", {
      user: () => window.PortalIdentity?.username?.() || "anonymous",
      defaults: { query: "", type: "", tag: "", collapsed: {} },
    });
    const saved = pageState.read();
    const state = {
      projects: [], statusVocab: [], loaded: false,
      filters: { query: String(saved.query || ""), type: String(saved.type || ""), tag: String(saved.tag || "") },
      collapsed: { ...(saved.collapsed || {}) },
    };

    const isCollapsed = group => (group.id in state.collapsed ? state.collapsed[group.id] : !!group.collapsed);

    function statsHtml() {
      const running = state.projects.reduce((total, project) => total + (project.running_resident_count || 0), 0);
      const active = state.projects.filter(project => project.status === "active").length;
      return window.PortalI18n.html`<span><strong>${state.projects.length}</strong> 个项目</span>
        <span><strong>${active}</strong> 个进行中</span>
        <span class="${running ? "is-live" : ""}"><strong>${running}</strong> 个 Resident 正在工作</span>`;
    }

    function optionsHtml(values, selected, labelOf, allLabel) {
      return [`<option value="">${esc(allLabel)}</option>`, ...values.map(value =>
        `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(labelOf(value))}</option>`)].join("");
    }

    function toolbarHtml() {
      const types = [...new Set(state.projects.map(project => project.project_type).filter(Boolean))].sort();
      const tags = [...new Set(state.projects.flatMap(project => project.tags || []))].sort((a, b) => a.localeCompare(b));
      return window.PortalI18n.html`<div class="projectListTools" role="search">
        <label class="projectSearch"><span class="srOnly">搜索项目</span>
          <input type="search" data-project-query placeholder="搜索项目、说明或标签" value="${esc(state.filters.query)}" autocomplete="off" /></label>
        <select data-project-filter="type" aria-label="项目类型">${optionsHtml(types, state.filters.type, value => common().typeLabel(value), t("全部类型"))}</select>
        <select data-project-filter="tag" aria-label="标签">${optionsHtml(tags, state.filters.tag, value => `#${value}`, t("全部标签"))}</select>
        <button class="portalButton is-primary" type="button" data-project-create data-portal-icon="plus"><span>新建项目</span></button>
      </div>`;
    }

    function statusSelectHtml(project) {
      const vocab = state.statusVocab.length ? state.statusVocab : Object.keys(common().STATUS_LABELS);
      const values = vocab.includes(project.status) ? vocab : [project.status, ...vocab];
      return `<select class="projectStatusSelect" data-project-status="${esc(project.project_id)}" data-status="${esc(project.status)}" aria-label="${esc(t("项目状态"))}">${
        values.map(value => `<option value="${esc(value)}"${value === project.status ? " selected" : ""}>${esc(common().statusLabel(value))}</option>`).join("")}</select>`;
    }

    function cardHtml(project) {
      const { avatarHtml, projectHref, typeLabel, relativeTime, heat, formatDeadline } = common();
      const closed = ["done", "archived"].includes(project.status);
      const deadline = closed ? { label: "" } : formatDeadline(project.ddl);
      const running = project.running_resident_count || 0;
      const tags = (project.tags || []).map(tag =>
        `<button type="button" class="projectTag${tag === state.filters.tag ? " is-active" : ""}" data-project-tag="${esc(tag)}">#${esc(tag)}</button>`).join("");
      const changed = relativeTime(project.last_changed_at);
      return `<article class="projectCard" data-project-id="${esc(project.project_id)}" style="--heat:${heat(project.last_changed_at).toFixed(2)}">
        <a class="projectCardLink" href="${esc(projectHref(project.project_id))}" data-project-open="${esc(project.project_id)}" aria-label="${esc(window.PortalI18n.text`打开项目 ${project.project_id}`)}"></a>
        <header class="projectCardHead">
          ${avatarHtml(project.project_id, project.avatar, 44)}
          <div class="projectCardTitle"><h3>${esc(project.project_id)}</h3>${project.project_type ? `<span>${esc(typeLabel(project.project_type))}</span>` : ""}</div>
          ${statusSelectHtml(project)}
        </header>
        <p class="projectCardDescription">${esc(project.description || t("还没有说明"))}</p>
        ${tags ? `<div class="projectCardTags">${tags}</div>` : ""}
        <footer class="projectCardSignals">
          ${running ? `<span class="projectSignal is-running"><i class="projectPulse" aria-hidden="true"></i>${esc(window.PortalI18n.text`${running} 个正在工作`)}</span>` : ""}
          <span class="projectSignal">${esc(window.PortalI18n.text`${project.lineage_count || 0} 个团队`)}</span>
          ${changed ? `<span class="projectSignal" title="${esc(project.last_changed_at)}">${esc(window.PortalI18n.text`${changed}更新`)}</span>` : ""}
          ${deadline.label ? `<span class="projectDeadline" data-tone="${esc(deadline.tone)}" title="${esc(project.ddl)}">${esc(deadline.label)}</span>` : ""}
        </footer>
      </article>`;
    }

    function groupsHtml() {
      const visible = state.projects.filter(project => matches(project, state.filters))
        .sort((left, right) => String(right.last_changed_at || "").localeCompare(String(left.last_changed_at || ""))
          || left.project_id.localeCompare(right.project_id));
      if (!visible.length) {
        return window.PortalUIPrimitives.asyncStateHtml({
          state: "empty",
          title: state.projects.length ? t("没有符合筛选条件的项目") : t("还没有项目"),
          message: state.projects.length ? t("换个关键词，或清除类型和标签筛选。") : t("新建一个项目，把知识、团队和资源放在一起。"),
        });
      }
      return GROUPS.map(group => {
        const items = visible.filter(project => groupOf(project.status) === group);
        if (!items.length) return "";
        const collapsed = isCollapsed(group);
        return `<section class="projectGroup" data-project-group="${group.id}">
          <button type="button" class="projectGroupHead" data-project-group-toggle="${group.id}" aria-expanded="${!collapsed}">
            <span>${esc(t(group.title))}</span><span class="projectGroupCount">${items.length}</span>
          </button>
          <div class="projectGrid"${collapsed ? " hidden" : ""}>${items.map(cardHtml).join("")}</div>
        </section>`;
      }).join("");
    }

    function render() {
      if (!state.loaded) return;
      root.innerHTML = window.PortalI18n.html`<section class="projectList">
        <header class="projectListHero">
          <div class="projectListHeading"><h1>项目</h1><p class="projectListStats">${statsHtml()}</p></div>
          ${toolbarHtml()}
        </header>
        <div class="projectGroups" data-project-groups>${groupsHtml()}</div>
      </section>`;
      window.PortalUIPrimitives.mountIcons(root);
    }

    function renderGroups() {
      const node = root.querySelector("[data-project-groups]");
      if (!node) return render();
      node.innerHTML = groupsHtml();
    }

    function setFilters(patch) {
      Object.assign(state.filters, patch);
      pageState.write({ ...state.filters });
      renderGroups();
    }

    async function changeStatus(select) {
      const project = state.projects.find(item => item.project_id === select.dataset.projectStatus);
      if (!project) return;
      const before = project.status;
      const after = select.value;
      if (before === after) return;
      project.status = after;
      select.disabled = true;
      try {
        await common().execute("UpdateProject", { project_id: project.project_id, changes: { status: after } }, "project-status");
        renderGroups();
      } catch (error) {
        project.status = before;
        select.value = before;
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      } finally {
        select.disabled = false;
      }
    }

    root.addEventListener("click", event => {
      const open = event.target.closest("[data-project-open]");
      if (open && common().plainClick(event)) {
        event.preventDefault();
        onOpen?.(open.dataset.projectOpen);
        return;
      }
      const tag = event.target.closest("[data-project-tag]");
      if (tag) {
        const value = tag.dataset.projectTag;
        setFilters({ tag: state.filters.tag === value ? "" : value });
        const select = root.querySelector('[data-project-filter="tag"]');
        if (select) select.value = state.filters.tag;
        return;
      }
      const toggle = event.target.closest("[data-project-group-toggle]");
      if (toggle) {
        const group = GROUPS.find(item => item.id === toggle.dataset.projectGroupToggle);
        state.collapsed[group.id] = !isCollapsed(group);
        pageState.write({ collapsed: { ...state.collapsed } });
        toggle.setAttribute("aria-expanded", String(!state.collapsed[group.id]));
        toggle.nextElementSibling.hidden = state.collapsed[group.id];
        return;
      }
      if (event.target.closest("[data-project-create]")) onCreate?.();
    });
    root.addEventListener("input", event => {
      if (event.target.matches("[data-project-query]")) setFilters({ query: event.target.value });
    });
    root.addEventListener("change", event => {
      const filter = event.target.closest("[data-project-filter]");
      if (filter) setFilters({ [filter.dataset.projectFilter]: filter.value });
      if (event.target.matches("[data-project-status]")) void changeStatus(event.target);
    });

    return {
      root,
      async load() {
        const result = await common().execute("ListProjects", {});
        state.projects = Array.isArray(result.projects) ? result.projects : [];
        state.statusVocab = Array.isArray(result.status_vocab) ? result.status_vocab : [];
        state.loaded = true;
        render();
        return state.projects;
      },
      render,
      snapshot: () => ({ projects: state.projects.map(project => ({ ...project })), statusVocab: [...state.statusVocab],
        filters: { ...state.filters }, collapsed: { ...state.collapsed } }),
      destroy() { pageState.destroy?.(); },
    };
  }

  window.PortalProjectList = Object.freeze({ create, GROUPS, matches });
})();
