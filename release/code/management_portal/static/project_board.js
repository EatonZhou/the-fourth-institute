(function (global) {
  "use strict";

  if (global.PortalProjectBoard) return;

  const STATUS_LABELS = Object.freeze({
    not_started: window.PortalI18n.t("未开始"),
    active: window.PortalI18n.t("进行中"),
    paused: window.PortalI18n.t("已暂停"),
    done: window.PortalI18n.t("已完成"),
    archived: window.PortalI18n.t("已归档"),
  });

  const TYPE_LABELS = Object.freeze({
    company: window.PortalI18n.t("公司项目"),
    academic: window.PortalI18n.t("学术项目"),
    personal: window.PortalI18n.t("个人项目"),
    other: window.PortalI18n.t("其他"),
  });

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  const {text, array, object, metricHtml, cardHtml} = global.PortalProjectSummary;

  function projectTitle(project) {
    return text(project.resource_id, window.PortalI18n.t("未命名项目"));
  }

  function statusLabel(value) {
    const clean = text(value, "unknown");
    return STATUS_LABELS[clean] || clean.replaceAll("_", " ");
  }

  function typeLabel(value) {
    return TYPE_LABELS[text(value, "other")] || TYPE_LABELS.other;
  }

  function parseDeadline(value) {
    const raw = text(value);
    if (!raw) return null;
    const parsed = new Date(raw.includes("T") ? raw : raw.replace(/\s+/, "T"));
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDeadline(value, now = new Date()) {
    // Deadline labels include relative day semantics; only their calendar text is shared.
    const parsed = parseDeadline(value);
    if (!text(value)) {
      return {
        label: window.PortalI18n.t("设置截止时间"),
        date: "",
        tone: "neutral",
        title: window.PortalI18n.t("尚未设置截止时间"),
      };
    }
    if (!parsed) {
      return {
        label: window.PortalI18n.t("日期无效"),
        date: text(value),
        tone: "danger",
        title: window.PortalI18n.text`无法识别截止时间：${text(value)}`,
      };
    }
    const dayMs = 24 * 60 * 60 * 1000;
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfDeadline = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
    const days = Math.round((startOfDeadline.getTime() - startOfToday.getTime()) / dayMs);
    const date = global.PortalUI.formatDateTime(parsed, {
      format: { year: "numeric", month: "2-digit", day: "2-digit" },
    });
    let label = window.PortalI18n.t("今天截止");
    let tone = "warning";
    if (days > 0) {
      label = days === 1 ? window.PortalI18n.t("还剩 1 天") : window.PortalI18n.text`还剩 ${days} 天`;
      tone = days <= 7 ? "warning" : "neutral";
    } else if (days < 0) {
      label = days === -1 ? window.PortalI18n.t("已逾期 1 天") : window.PortalI18n.text`已逾期 ${Math.abs(days)} 天`;
      tone = "danger";
    }
    return {
      label,
      date,
      tone,
      title: `${label} · ${date}`,
    };
  }

  function matches(project, query, tag) {
    if (tag && !array(project.tags).map(String).includes(tag)) return false;
    if (!query) return true;
    const haystack = [
      project.resource_id,
      project.description,
      project.status,
      project.project_type,
      project.path,
      ...array(project.tags),
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  }

  function statusOptions(statuses, selected) {
    return array(statuses).map(status => {
      const clean = text(status);
      if (!clean) return "";
      return window.PortalI18n.html`<option value="${escapeHtml(clean)}"${clean === selected ? " selected" : ""}>${escapeHtml(statusLabel(clean))}</option>`;
    }).join("");
  }

  function tagHtml(project, selectedTag) {
    const tags = array(project.tags).map(item => text(item)).filter(Boolean);
    if (!tags.length) return "";
    return window.PortalI18n.html`<div class="projectRowTags" aria-label="项目标签">${tags.map(tag => (
      window.PortalI18n.html`<button class="projectTag${tag === selectedTag ? " is-active" : ""}" type="button" data-project-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
    )).join("")}</div>`;
  }

  function missingBriefHtml(project, contract) {
    const source = object(contract?.source);
    const message = text(
      source.message,
      window.PortalI18n.t("这个项目还没有发布 Project State。可以先阅读 Summary，或进入 Chat 补齐项目认知。"),
    );
    return window.PortalI18n.html`<div class="projectBriefEmpty">
      <div>
        <span class="projectBriefKicker">项目简报尚未发布</span>
        <h3>${escapeHtml(projectTitle(project))}</h3>
        <p>${escapeHtml(message)}</p>
      </div>
      <div class="projectBriefActions">
        <button class="portalButton" type="button" data-project-view="summary">查看 Summary</button>
        <button class="portalButton" type="button" data-project-view="chat">打开 Chat</button>
      </div>
    </div>`;
  }

  function briefHtml(project, contract) {
    const value = object(contract);
    if (["missing", "invalid", "error"].includes(text(value.status))) {
      return missingBriefHtml(project, value);
    }
    const brief = object(value.brief);
    const signals = object(brief.signals);
    const nextState = text(brief.next_move_state, "unknown");
    const nextLabel = nextState === "confirmed"
      ? window.PortalI18n.t("NEXT · CONFIRMED")
      : window.PortalI18n.t("NEXT · PROPOSED");
    return window.PortalI18n.html`<div class="projectBrief">
      <div class="projectBriefLead">
        <span class="projectBriefKicker">PROJECT UNDERSTANDING</span>
        <h3>${escapeHtml(text(brief.headline, project.description || window.PortalI18n.t("项目目标尚未定义。")))}</h3>
        <div class="projectBriefWhere">
          <span>WHERE WE ARE</span>
          <p>${escapeHtml(text(brief.current_state, window.PortalI18n.t("当前阶段尚未形成可靠判断。")))}</p>
        </div>
      </div>
      <div class="projectBriefGrid">
        ${cardHtml("PROGRESS", brief.progress, "progress", {compact: true})}
        ${cardHtml(window.PortalI18n.t("NEEDS ATTENTION"), brief.needs_attention, "attention", {compact: true})}
        ${cardHtml(nextLabel, brief.next_move, nextState === "confirmed" ? "next-confirmed" : "next-candidate", {compact: true})}
      </div>
      <div class="projectBriefFooter">
        <div class="projectBriefSignals" aria-label="项目关键数量">
          ${metricHtml(window.PortalI18n.t("能力"), signals.capabilities, {compact: true})}
          ${metricHtml("Residents", signals.residents, {compact: true})}
          ${metricHtml(window.PortalI18n.t("待人审"), signals.human_review, {compact: true, tone: Number(signals.human_review) > 0 ? "warning" : "neutral"})}
          ${metricHtml(window.PortalI18n.t("冲突"), signals.conflicts, {compact: true, tone: Number(signals.conflicts) > 0 ? "danger" : "neutral"})}
        </div>
        <div class="projectBriefActions">
          <button class="portalButton" type="button" data-project-view="summary">Summary</button>
          <button class="portalButton" type="button" data-project-view="chat">Chat</button>
          <button class="portalButton is-primary" type="button" data-project-open>进入项目</button>
        </div>
      </div>
    </div>`;
  }

  function rowHtml(project, statuses, expanded, selectedTag) {
    const id = text(project.resource_id);
    const deadline = formatDeadline(project.ddl);
    const description = text(project.description, window.PortalI18n.t("尚未填写项目简介。"));
    return window.PortalI18n.html`<article class="projectRow${expanded ? " is-expanded" : ""}" data-project-id="${escapeHtml(id)}" data-project-status="${escapeHtml(text(project.status, "unknown"))}">
      <div class="projectRowMain">
        <button class="projectRowToggle" type="button" data-project-toggle aria-expanded="${expanded ? "true" : "false"}" aria-controls="project-brief-${escapeHtml(id)}" aria-label="${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${escapeHtml(projectTitle(project))}">
          <span class="projectRowChevron" aria-hidden="true"></span>
        </button>
        <div class="projectRowIdentity">
          <div class="projectRowTitleLine">
            <h2><button class="projectRowTitleButton" type="button" data-project-toggle aria-expanded="${expanded ? "true" : "false"}" aria-controls="project-brief-${escapeHtml(id)}">${escapeHtml(projectTitle(project))}</button></h2>
            <span>${escapeHtml(typeLabel(project.project_type))}</span>
          </div>
          <p>${escapeHtml(description)}</p>
          ${tagHtml(project, selectedTag)}
        </div>
        <div class="projectRowControls">
          <label class="projectStatusControl">
            <span>状态</span>
            <select data-project-status-select aria-label="${escapeHtml(window.PortalI18n.text`${projectTitle(project)} 状态`)}">
              ${statusOptions(statuses, text(project.status))}
            </select>
          </label>
          <button class="projectDeadline" type="button" data-project-deadline data-tone="${escapeHtml(deadline.tone)}" title="${escapeHtml(deadline.title)}">
            <span>${escapeHtml(deadline.label)}</span>
            <small>${escapeHtml(deadline.date || window.PortalI18n.t("点击设置"))}</small>
          </button>
          <button class="portalButton is-primary projectRowOpen" type="button" data-project-open>进入项目</button>
        </div>
      </div>
      <div class="projectRowDisclosure" id="project-brief-${escapeHtml(id)}"${expanded ? "" : " hidden"}>
        <div class="projectBriefLoading" data-project-brief-host>
          <div class="portalAsyncState" data-state="loading" data-density="compact" role="status">
            <span class="portalAsyncStateIcon" aria-hidden="true"></span>
            <span class="portalAsyncStateMessage">正在整理项目简报。</span>
          </div>
        </div>
      </div>
    </article>`;
  }

  function emptyHtml(query, tag) {
    const constrained = !!(query || tag);
    return window.PortalI18n.html`<div class="projectRegistryEmpty">
      <strong>${constrained ? window.PortalI18n.t("没有匹配的项目") : window.PortalI18n.t("还没有项目")}</strong>
      <span>${constrained ? window.PortalI18n.t("清除搜索或标签筛选后再试。") : window.PortalI18n.t("使用“新建项目”创建第一项工作。")}</span>
    </div>`;
  }

  function registryHeaderHtml(projects, visible) {
    const active = projects.filter(project => project.status === "active").length;
    const attention = projects.filter(project => ["paused", "not_started"].includes(project.status)).length;
    return window.PortalI18n.html`<div class="projectRegistryIntro">
      <div>
        <span class="projectRegistryEyebrow">Project navigation</span>
        <h1>项目总览</h1>
        <p>先看方向、进展与需要你介入的地方，再进入证据和执行细节。</p>
      </div>
      <div class="projectRegistryStats" aria-label="项目概况">
        <span><strong>${projects.length}</strong>全部项目</span>
        <span><strong>${active}</strong>进行中</span>
        <span><strong>${attention}</strong>待推进</span>
        ${visible !== projects.length ? window.PortalI18n.html`<span><strong>${visible}</strong>当前显示</span>` : ""}
      </div>
    </div>`;
  }

  function create(options = {}) {
    const root = typeof options.root === "string"
      ? document.querySelector(options.root)
      : options.root;
    if (!root) throw new Error("PortalProjectBoard requires a root element.");
    if (typeof options.loadProjectState !== "function") {
      throw new Error("PortalProjectBoard requires loadProjectState(projectId).");
    }

    let projects = [];
    let statuses = [];
    let query = "";
    let tag = "";
    let expandedId = "";
    let hydrateSequence = 0;

    function visibleProjects() {
      return projects.filter(project => matches(project, query, tag));
    }

    async function hydrate(projectId) {
      const id = text(projectId);
      const project = projects.find(item => text(item.resource_id) === id);
      const host = root.querySelector(`[data-project-id="${CSS.escape(id)}"] [data-project-brief-host]`);
      if (!project || !host) return;
      const sequence = ++hydrateSequence;
      let contract;
      try { contract = await options.loadProjectState(id); }
      catch (error) {
        contract = {status: "error", source: {message: text(error?.message, window.PortalI18n.t("无法加载 Project State。"))}};
      }
      if (sequence !== hydrateSequence || expandedId !== id) return;
      const latestHost = root.querySelector(`[data-project-id="${CSS.escape(id)}"] [data-project-brief-host]`);
      if (latestHost) latestHost.innerHTML = briefHtml(project, contract);
    }

    function render() {
      const visible = visibleProjects();
      if (expandedId && !visible.some(project => project.resource_id === expandedId)) {
        expandedId = "";
      }
      root.innerHTML = window.PortalI18n.html`
        ${registryHeaderHtml(projects, visible.length)}
        <div class="projectRegistryList">
          ${visible.length
            ? visible.map(project => rowHtml(project, statuses, project.resource_id === expandedId, tag)).join("")
            : emptyHtml(query, tag)}
        </div>`;
      if (expandedId) hydrate(expandedId);
      options.onVisibleChange?.({
        total: projects.length,
        visible: visible.length,
        expandedProjectId: expandedId,
      });
    }

    function setExpanded(projectId) {
      const id = text(projectId);
      expandedId = expandedId === id ? "" : id;
      render();
      options.onExpandedChange?.(expandedId);
    }

    root.addEventListener("click", event => {
      const row = event.target.closest("[data-project-id]");
      const projectId = text(row?.dataset.projectId);
      if (!projectId) return;
      if (event.target.closest("[data-project-toggle]")) {
        setExpanded(projectId);
        return;
      }
      const tagButton = event.target.closest("[data-project-tag]");
      if (tagButton) {
        options.onSelectTag?.(text(tagButton.dataset.projectTag));
        return;
      }
      if (event.target.closest("[data-project-deadline]")) {
        const project = projects.find(item => item.resource_id === projectId);
        options.onEditDeadline?.(project);
        return;
      }
      const viewButton = event.target.closest("[data-project-view]");
      if (viewButton) {
        options.onOpenProject?.(projectId, {
          view: text(viewButton.dataset.projectView, "overview"),
        });
        return;
      }
      if (event.target.closest("[data-project-open]")) {
        options.onOpenProject?.(projectId, { view: "overview" });
      }
    });

    root.addEventListener("change", event => {
      const select = event.target.closest("[data-project-status-select]");
      if (!select) return;
      const row = select.closest("[data-project-id]");
      const projectId = text(row?.dataset.projectId);
      if (!projectId) return;
      options.onStatusChange?.(projectId, text(select.value));
    });

    return Object.freeze({
      root,
      setProjects(nextProjects, config = {}) {
        projects = array(nextProjects).slice();
        statuses = Array.from(new Set([
          ...array(config.statuses).map(item => text(item)).filter(Boolean),
          ...projects.map(project => text(project.status)).filter(Boolean),
        ]));
        const requested = text(config.expandedProjectId);
        if (requested && projects.some(project => project.resource_id === requested)) {
          expandedId = requested;
        } else if (!expandedId && config.autoExpandFirst !== false && projects.length) {
          expandedId = text(projects[0].resource_id);
        }
        render();
      },
      setFilters(filters = {}) {
        query = text(filters.query).toLowerCase();
        tag = text(filters.tag);
        render();
      },
      render,
      snapshot() {
        return {
          projects: projects.slice(),
          query,
          tag,
          expandedProjectId: expandedId,
        };
      },
    });
  }

  global.PortalProjectBoard = Object.freeze({
    create,
    statusLabel,
  });
})(window);
