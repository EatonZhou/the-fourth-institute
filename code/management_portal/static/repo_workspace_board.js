(() => {
  "use strict";

  if (window.PortalRepoWorkspaceBoard) return;

  // The board of Repo cards grouped by Project: the Project filter and its
  // summary, the mode tabs, the bulk commit button, each card with its
  // checkout status and automation state, and opening a Repo from the board.
  const DEPENDENCIES = Object.freeze([
    "destroyFileFinderPopover", "detailHistory", "elements", "esc", "loadRepos", "loadWorkspace", "objectOf",
    "queryState", "renderWorkspace", "repoStateHtml", "showToast", "state", "uiIcon", "workspaceCapability",
    "writeQuery",
  ]);

  function createBoard(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceBoard is missing: ${missing.join(", ")}`);
    const {
      destroyFileFinderPopover, detailHistory, elements, esc, loadRepos, loadWorkspace, objectOf, queryState,
      renderWorkspace, repoStateHtml, showToast, state, uiIcon, workspaceCapability, writeQuery,
    } = dependencies;

    function projectIds(record) {
      const values = objectOf(record?.attributes).project_ids;
      return Array.isArray(values) ? values.map(String).filter(Boolean) : [];
    }

    function formatModified(value) {
      return window.PortalUI.formatDateTime(value, {
        style: "calendar-date",
        shortYear: true,
        invalidFallback: false,
      });
    }

    let projectFilterDropdown = null;
    let projectFilterSummary = null;

    // The Project filter's dropdown and the summary that clears it.
    function bindProjectFilter() {
      projectFilterDropdown = window.PortalFormControls.bindFilterDropdown({
        button: elements.filterToggle,
        panel: elements.filterPanel,
        onToggle: open => {
          elements.filterToggle.classList.toggle("active", open || Boolean(queryState().project));
        },
      });
      projectFilterSummary = window.PortalFormControls?.bindFilterSummary?.({
        target: elements.filterSummary,
        items: () => {
          const project = queryState().project;
          return project ? [{ label: window.PortalI18n.t("Project"), value: project }] : [];
        },
        onClear: () => {
          state.mode = "board";
          writeQuery({ project: "", item: "", tab: "", path: "", file: "" });
          setFilterOpen(false);
          return loadRepos({ keepWorkspace: false });
        },
      });
    }

    function setFilterOpen(open) {
      if (open) projectFilterDropdown?.open();
      else projectFilterDropdown?.close();
      elements.filterToggle.classList.toggle("active", Boolean(open) || Boolean(queryState().project));
    }

    function renderProjectOptions(records) {
      records.flatMap(projectIds).forEach(project => state.projects.add(project));
      const current = queryState().project;
      if (current) state.projects.add(current);
      elements.projectOptions.innerHTML = window.PortalI18n.html`
      <label class="portalCollectionFilterOption">
        <input type="radio" name="resourceProject" value="" ${current ? "" : "checked"} />
        <span>全部 Project</span>
      </label>
      ${[...state.projects].sort().map(project => window.PortalI18n.html`
        <label class="portalCollectionFilterOption">
          <input type="radio" name="resourceProject" value="${esc(project)}" ${project === current ? "checked" : ""} />
          <span>${esc(project)}</span>
        </label>
      `).join("")}`;
      const label = elements.filterToggle.querySelector("span");
      if (label) label.textContent = current ? window.PortalI18n.text`Project · ${current}` : window.PortalI18n.t("筛选");
      elements.filterToggle.title = current ? window.PortalI18n.text`当前 Project：${current}` : window.PortalI18n.t("按 Project 筛选 Repo");
      projectFilterSummary?.sync?.();
    }

    function updateModeControls() {
      elements.modeTabs.forEach(tab => {
        const active = tab.dataset.repoMode === state.mode;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      document.body.dataset.repoMode = state.mode;
      updateAutomationControls();
    }

    function modifiedTime(record) {
      const parsed = Date.parse(String(objectOf(record?.source).modified_at || ""));
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function projectFor(record) {
      const projects = projectIds(record);
      const selected = queryState().project;
      if (selected && projects.includes(selected)) return selected;
      return projects[0] || "Unassigned";
    }

    function groupReposByProject(records) {
      const groups = new Map();
      records.forEach(record => {
        const project = projectFor(record);
        if (!groups.has(project)) groups.set(project, []);
        groups.get(project).push(record);
      });
      groups.forEach(items => items.sort((left, right) => (
        modifiedTime(right) - modifiedTime(left)
        || String(left.title || left.resource_id || "").localeCompare(
          String(right.title || right.resource_id || ""),
        )
      )));
      return [...groups.entries()].sort((left, right) => {
        if (left[0] === "Unassigned" && right[0] !== "Unassigned") return 1;
        if (right[0] === "Unassigned" && left[0] !== "Unassigned") return -1;
        const newestLeft = Math.max(0, ...left[1].map(modifiedTime));
        const newestRight = Math.max(0, ...right[1].map(modifiedTime));
        return newestRight - newestLeft || left[0].localeCompare(right[0]);
      });
    }

    function automationStatusFor(repoId) {
      return state.automationStatuses.get(String(repoId || "")) || null;
    }

    function updateAutomationControls() {
      elements.autoCommitAll.hidden = state.mode === "workspace" && !workspaceCapability("git");
      const actionable = [...state.automationStatuses.values()]
        .filter(item => item?.actionable).length;
      const label = elements.autoCommitAll.querySelector("span");
      if (state.automationActive) {
        label.textContent = window.PortalI18n.t("查看提交进度");
        elements.autoCommitAll.classList.add("active");
        elements.autoCommitAll.disabled = false;
        elements.autoCommitCount.hidden = false;
        elements.autoCommitCount.textContent = `${state.automationCompleted}/${state.automationTargets.length}`;
        return;
      }
      label.textContent = window.PortalI18n.t("AI 提交全部");
      elements.autoCommitAll.classList.remove("active");
      elements.autoCommitAll.disabled = false;
      elements.autoCommitCount.hidden = !actionable;
      elements.autoCommitCount.textContent = String(actionable);
      elements.autoCommitAll.title = actionable
        ? window.PortalI18n.text`${actionable} 个当前筛选的 Repo 有变更；提交前会重新检查`
        : window.PortalI18n.t("检查当前筛选的 Repo；有 remote 的仓库会在提交后继续 push");
    }

    function repoCardAutomation(record) {
      const id = String(record.resource_id || "");
      const status = automationStatusFor(id);
      const lastRun = state.automationResults.find(item => item.id === id);
      const running = state.automationCurrent === id;
      let label = window.PortalI18n.t("AI Commit");
      let detail = "AI 生成原因";
      let disabled = false;
      let title = window.PortalI18n.t("由 AI 生成 commit 标题和原因，然后提交当前全部变更");
      if (running) {
        label = "AI 分析中…";
        detail = status?.will_push ? "完成后自动 push" : "完成后仅本地 commit";
        disabled = true;
      } else if (!status && state.automationStatusLoading) {
        label = window.PortalI18n.t("检查中…");
        detail = window.PortalI18n.t("读取 Git 状态");
        disabled = true;
      } else if (status?.error) {
        label = "状态不可用";
        detail = String(status.error);
        title = detail;
        disabled = true;
      } else if (lastRun?.state === "error") {
        label = "重试 AI Commit";
        detail = lastRun.error || "上次执行失败";
        title = detail;
      } else if (status?.blocked_reason) {
        label = status.conflict_count ? "存在冲突" : "暂不可提交";
        detail = status.blocked_reason;
        title = status.blocked_reason;
        disabled = true;
      } else if (status && !status.dirty) {
        label = "工作区干净";
        detail = status.has_remote ? `${status.remote || "remote"} · 无变更` : "无本地变更";
        title = "当前 Repo 没有需要提交的变更";
        disabled = true;
      } else if (status?.dirty) {
        label = status.will_push ? "AI Commit & Push" : "AI Commit";
        detail = `${status.change_count} changes · ${status.will_push ? (status.remote || "remote") : "local only"}`;
        title = status.will_push
          ? "AI 生成原因后执行 git add、commit 并 push"
          : "AI 生成原因后执行 git add 与 commit；该 Repo 不会自动 Push";
      }
      return window.PortalI18n.html`
      <div class="repoCardAutomation">
        <button
          class="repoCardAutoButton${running ? " running" : ""}"
          type="button"
          data-repo-auto-commit="${esc(id)}"
          aria-label="${esc(`${record.title || id}：${label}`)}"
          title="${esc(title)}"
          ${disabled ? "disabled" : ""}
        >${uiIcon("repository-sparkles")}<span>${esc(label)}</span></button>
        <span class="repoCardAutomationMeta" title="${esc(detail)}">${esc(detail)}</span>
      </div>`;
    }

    function repoCardStatus(record) {
      const status = automationStatusFor(record.resource_id);
      if (!status) {
        return window.PortalI18n.markup('<div class="repoCardStatus is-loading"><span>正在读取 Git 状态…</span></div>');
      }
      if (status.error) {
        return window.PortalI18n.html`<div class="repoCardStatus is-error" title="${esc(String(status.error))}"><span>Git 状态不可用</span></div>`;
      }
      const sync = [];
      if (Number(status.ahead || 0)) sync.push(`↑${Number(status.ahead)}`);
      if (Number(status.behind || 0)) sync.push(`↓${Number(status.behind)}`);
      if (!sync.length && status.has_remote) sync.push("已配置远程");
      return window.PortalI18n.html`
      <div class="repoCardStatus" aria-label="Repo 状态">
        <span class="repoCardBranch">${uiIcon("branch")}${esc(status.branch || window.PortalI18n.t("detached HEAD"))}</span>
        <span class="${status.dirty ? "is-dirty" : "is-clean"}">${status.dirty ? `${Number(status.change_count || 0)} changes` : "Clean"}</span>
        ${sync.length ? window.PortalI18n.html`<span>${esc(sync.join(" "))}</span>` : ""}
        <span>${status.will_push ? esc(status.remote || "remote") : status.has_remote ? window.PortalI18n.t("仅拉取") : window.PortalI18n.t("仅本地")}</span>
      </div>`;
    }

    function renderRepoCard(record) {
      const id = String(record.resource_id || "");
      const semanticResult = state.semanticSearch?.resultFor(id) || null;
      const description = String(record.summary || "").trim();
      const descriptionPending = state.descriptionPending.has(id);
      const descriptionFailed = state.descriptionFailed.has(id);
      state.automationNames.set(id, String(record.title || id));
      return window.PortalI18n.html`
      <article
        class="portalItemCard portalCollectionCard repoCard${semanticResult ? " is-semantic-match" : ""}"
        title="${esc(id)}"
        data-resource-card="${esc(id)}"
      >
        <button
          class="repoCardOpenSurface"
          type="button"
          aria-label="打开 ${esc(record.title || id)} 工作台"
          data-resource-open="${esc(id)}"
        ></button>
        <div class="repoCardContent">
          <h3 class="portalItemTitle">${esc(record.title || id)}</h3>
          <div class="portalCollectionCardMeta">
            <span class="portalCollectionItemMeta">${esc(formatModified(objectOf(record?.source).modified_at))}</span>
          </div>
          <p
            class="portalItemPreview repoCardDescription${descriptionPending ? " pending" : ""}${descriptionFailed ? " unavailable" : ""}"
            ${description ? `title="${esc(description)}"` : ""}
          >
            ${description
                ? esc(description)
                : descriptionPending
                  ? window.PortalI18n.t('<span class="repoDescriptionPulse" aria-hidden="true"></span><span>正在生成一句话简介…</span>')
                  : descriptionFailed
                    ? window.PortalI18n.t("简介暂未生成")
                    : window.PortalI18n.t("正在准备一句话简介…")
              }
          </p>
          ${repoCardStatus(record)}
          ${semanticResult ? window.PortalI18n.html`<div class="portalCollectionSemanticMatch"><strong>${Number(semanticResult.score || 0)}%</strong><span>${esc(semanticResult.reason || window.PortalI18n.t("语义匹配"))}</span></div>` : ""}
        </div>
        ${repoCardAutomation(record)}
        <span class="portalCollectionTypeChip" data-type="repo">repo</span>
      </article>`;
    }

    function renderRepoProject(project, records) {
      return window.PortalI18n.html`
      <section class="portalProjectGroup" data-resource-project="${esc(project)}">
        <header class="portalProjectHeader">
          <h2 class="portalProjectTitle">${esc(project)}</h2>
          <div class="portalProjectActions">
            <span class="portalProjectCount">${records.length} ${records.length === 1 ? "repo" : "repos"}</span>
            <button class="portalProjectToggle" type="button" aria-expanded="true" aria-label="折叠 ${esc(project)}" data-resource-project-toggle>
              ${uiIcon("chevron-down")}
            </button>
          </div>
        </header>
        <div class="portalProjectItems">${records.map(renderRepoCard).join("")}</div>
      </section>`;
    }

    function renderRepoBoard() {
      destroyFileFinderPopover();
      state.fileFinderOpen = false;
      state.fileSearch = null;
      state.mode = "board";
      updateModeControls();
      updateAutomationControls();
      if (!state.visibleRecords.length) {
        renderEmpty();
        return;
      }
      elements.workspace.innerHTML = window.PortalI18n.html`
      <div class="portalProjectCollection repoProjectList">
        ${groupReposByProject(state.visibleRecords)
            .map(([project, records]) => renderRepoProject(project, records))
            .join("")}
      </div>`;
    }

    async function openRepoWorkspace(repoId) {
      const id = String(repoId || "").trim();
      if (!id) return;
      state.selectedId = id;
      state.mode = "workspace";
      detailHistory.open(id, { tab: "", path: "", file: "" });
      updateModeControls();
      await loadWorkspace({ path: "", file: "", tab: "code" });
    }

    function switchRepoMode(mode) {
      if (mode === "board") {
        state.mode = "board";
        detailHistory.close({ tab: "", path: "", file: "" });
        renderRepoBoard();
        return;
      }
      if (!state.selectedId) {
        showToast(window.PortalI18n.t("请先从 Repo 列表中选择一个仓库。"));
        return;
      }
      state.mode = "workspace";
      detailHistory.open(state.selectedId, { tab: "", path: "", file: "" });
      state.activeTab = "code";
      state.file = null;
      updateModeControls();
      if (state.overview?.id === state.selectedId && state.tree) renderWorkspace();
      else loadWorkspace({ path: "", file: "", tab: "code" })
        .catch(error => showToast(error.message || error, "error"));
    }

    function renderEmpty() {
      const current = queryState();
      const filtered = Boolean(current.q || current.project || current.semantic || state.semanticSearch?.activeId() || state.records.length);
      elements.workspace.innerHTML = repoStateHtml(
        filtered
          ? window.PortalI18n.t("调整搜索或 Project 筛选，或者注册一个新的 checkout。")
          : window.PortalI18n.t("注册 Portal host 上的 Git checkout 后，可以在这里浏览、编辑和提交代码。"),
        {
          state: "empty",
          title: filtered ? window.PortalI18n.t("没有匹配的 Repo") : window.PortalI18n.t("还没有注册 Repo"),
          icon: "repository",
          actionsHtml: window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-repo-action="register">${uiIcon("plus")}<span>注册新 Repo</span></button>`,
        },
      );
    }

    return Object.freeze({
      automationStatusFor, bindProjectFilter, openRepoWorkspace, projectIds, renderProjectOptions,
      renderRepoBoard, setFilterOpen, switchRepoMode, updateAutomationControls, updateModeControls,
    });
  }

  window.PortalRepoWorkspaceBoard = Object.freeze({ createBoard, dependencies: DEPENDENCIES });
})();
