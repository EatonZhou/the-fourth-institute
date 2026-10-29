(() => {
  "use strict";

  if (window.PortalRepoWorkspaceShell) return;

  // One Repo's workspace page around its tabs: the Repo selector, the tabs
  // and context actions, the Workbench it mounts, the file preview panel and
  // the file finder popover; renderWorkspace draws the active tab into it.
  const DEPENDENCIES = Object.freeze([
    "applyRenderedFilters", "elements", "esc", "markdown", "objectOf", "projectIds", "renderBranchesView",
    "renderChangesView", "renderCodeView", "renderCommitsView", "renderRepoBoard", "renderSettingsView",
    "scheduleRepositoryStatistics", "showToast", "state", "statusSummary", "uiIcon", "updateModeControls",
  ]);

  function createShell(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceShell is missing: ${missing.join(", ")}`);
    const {
      applyRenderedFilters, elements, esc, markdown, objectOf, projectIds, renderBranchesView,
      renderChangesView, renderCodeView, renderCommitsView, renderRepoBoard, renderSettingsView,
      scheduleRepositoryStatistics, showToast, state, statusSummary, uiIcon, updateModeControls,
    } = dependencies;

    let repoWorkbench = null;
    let repoFilePreviewPanel = null;

    let fileFinderPopover = null;
    let suppressFileFinderClose = false;

    function destroyFileFinderPopover() {
      if (!fileFinderPopover) return;
      suppressFileFinderClose = true;
      fileFinderPopover.destroy();
      suppressFileFinderClose = false;
      fileFinderPopover = null;
    }

    function bindFileFinderPopover() {
      const trigger = elements.workspace.querySelector("[data-repo-action='file-finder']");
      const panel = elements.workspace.querySelector(".repoFileFinder");
      if (!state.fileFinderOpen || !trigger || !panel) return;
      fileFinderPopover = window.PortalOverlay.bindPopover({
        trigger,
        panel,
        boundary: elements.workspace,
        placement: "bottom-end",
        ariaHaspopup: "dialog",
        portal: false,
        closeOnOutside: true,
        closeOnEscape: true,
        onClose: () => {
          if (suppressFileFinderClose) return;
          state.fileFinderOpen = false;
          state.fileSearch = null;
          state.fileSearchQuery = "";
          state.fileSearchIndex = 0;
        },
      });
      fileFinderPopover.open();
      window.setTimeout(() => elements.workspace.querySelector("#repoFileFinderInput")?.focus(), 0);
    }

    function selectedRecord() {
      return state.records.find(record => record.resource_id === state.selectedId) || null;
    }

    function workspaceCapability(name) {
      return state.overview?.capabilities?.[name] === true;
    }

    function repoSelector() {
      const record = selectedRecord();
      const primaryRemote = state.overview?.remotes?.find(remote => remote.name === "origin")
        || state.overview?.remotes?.[0];
      const identity = window.PortalResourceHeader?.identityHtml?.({
        title: state.overview?.name || record?.title || state.selectedId,
        type: "repo",
        resource_id: state.selectedId,
        copy_label: "repo_id",
      }, {
        compact: true,
        headingTag: "h1",
        titleId: "repoWorkspaceTitle",
        idId: "repoWorkspaceId",
      }) || window.PortalI18n.html`<h1 id="repoWorkspaceTitle">${esc(state.overview?.name || record?.title || state.selectedId)}</h1>`;
      return window.PortalI18n.html`
      <div class="repoIdentity">
        <span class="repoIdentityIcon">${uiIcon("repository")}</span>
        <div class="repoIdentityText">${identity}</div>
        <span class="repoOwnership">${esc(state.overview?.ownership || objectOf(record?.attributes).ownership || "local")}</span>
        <span class="repoProjectPills">${(state.overview?.project_ids || projectIds(record)).map(project => window.PortalI18n.html`<span>${esc(project)}</span>`).join("")}</span>
        ${statusSummary()}
      </div>
      <div class="repoIdentityActions">
        ${primaryRemote?.web_url ? window.PortalI18n.html`<a class="portalButton is-compact" href="${esc(primaryRemote.web_url)}" target="_blank" rel="noopener noreferrer" title="${esc(primaryRemote.web_url)}">${uiIcon("external-link")}<span>打开 Remote</span></a>` : ""}
        ${workspaceCapability("git") ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-repo-action="shortcuts" aria-label="查看 Repo 快捷键" title="快捷键 (?)">${uiIcon("keyboard")}<span>快捷键</span></button>` : ""}
        <button class="portalButton is-compact" type="button" data-repo-action="copy-id" title="${esc(state.selectedId)}">${uiIcon("copy")}<span>复制 ID</span></button>
        <button class="portalButton is-compact" type="button" data-repo-action="register">${uiIcon("plus")}<span>注册 Repo</span></button>
      </div>`;
    }

    function workspaceContextActions() {
      if (state.activeTab === "branches") {
        const dirty = Boolean(state.overview?.status?.dirty);
        return window.PortalI18n.html`<div class="repoWorkspaceActions" data-repo-context-actions="branches">
        <label class="repoListSearch" data-portal-search-field data-portal-search-density="compact"><input data-portal-search-input id="repoBranchFilter" type="search" value="${esc(state.branchListQuery)}" placeholder="筛选分支…" autocomplete="off" /></label>
        <button class="portalButton is-compact is-primary" type="button" data-repo-action="new-branch" ${dirty ? window.PortalI18n.t('disabled title="存在未提交改动时不能创建并切换分支"') : ""}>${uiIcon("plus")}<span>新建分支</span></button>
      </div>`;
      }
      if (state.activeTab === "commits") {
        const commits = state.commits?.commits || [];
        const revision = state.commitRevision === "HEAD"
          ? (state.overview.current_branch || "HEAD")
          : state.commitRevision;
        return window.PortalI18n.html`<div class="repoWorkspaceActions" data-repo-context-actions="commits">
        <span class="repoWorkspaceMeta" id="repoCommitListCount">${esc(state.commitHistoryPath ? state.commitHistoryPath : window.PortalI18n.text`${revision} · ${commits.length} 条`)}</span>
        <label class="repoListSearch" data-portal-search-field data-portal-search-density="compact"><input data-portal-search-input id="repoCommitListFilter" type="search" value="${esc(state.commitListQuery)}" placeholder="搜索标题、作者或 SHA…" autocomplete="off" /></label>
        <label class="repoRevisionControl"><span>Revision</span><select id="repoCommitRevisionPicker" aria-label="浏览分支提交">${state.overview.branches.map(branch => window.PortalI18n.html`<option value="${esc(branch.name)}" ${(state.commitRevision === branch.name || (state.commitRevision === "HEAD" && branch.current)) ? "selected" : ""}>${esc(branch.name)}</option>`).join("")}</select></label>
        ${state.commitHistoryPath ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-repo-action="view-all-commits">全部提交</button><button class="portalButton is-compact" type="button" data-open-file="${esc(state.commitHistoryPath)}">返回文件</button>` : ""}
        <button class="portalButton is-compact" type="button" data-repo-action="reload-tab" ${state.commitListLoading ? "disabled" : ""}>${state.commitListLoading ? window.PortalI18n.t("刷新中…") : window.PortalI18n.t("刷新")}</button>
      </div>`;
      }
      return "";
    }

    function workspaceTabs() {
      const changes = Number(state.overview?.status?.changes?.length || 0);
      const definitions = [
        ["code", "代码", uiIcon("code"), ""],
        ["changes", "Changes", uiIcon("changes"), changes ? String(changes) : ""],
        ["commits", "Commits", uiIcon("history"), ""],
        ["branches", "Branches", uiIcon("branch"), String(state.overview?.branch_count || "")],
        ["settings", "设置", uiIcon("settings"), ""],
      ];
      return window.PortalI18n.html`<div class="repoWorkspaceNav">
      <nav class="repoTabs" role="tablist" aria-label="Repo workspace">
        ${definitions.filter(([key]) => key === "code" || workspaceCapability("git")).map(([key, label, icon, count]) => window.PortalI18n.html`
          <button class="repoTab ${state.activeTab === key ? "active" : ""}" type="button" role="tab" data-repo-tab="${key}" aria-selected="${state.activeTab === key ? "true" : "false"}" tabindex="${state.activeTab === key ? "0" : "-1"}">
            ${icon}<span>${esc(window.PortalI18n.t(label))}</span>${count ? window.PortalI18n.html`<small>${esc(count)}</small>` : ""}
          </button>
        `).join("")}
      </nav>
      ${workspaceContextActions()}
    </div>`;
    }

    function renderWorkspace() {
      void repoFilePreviewPanel?.destroy?.();
      repoFilePreviewPanel = null;
      repoWorkbench?.destroy?.();
      repoWorkbench = null;
      destroyFileFinderPopover();
      if (!state.selectedId || !state.overview) {
        state.mode = "board";
        renderRepoBoard();
        return;
      }
      state.mode = "workspace";
      updateModeControls();
      const content = {
        code: renderCodeView,
        changes: renderChangesView,
        commits: renderCommitsView,
        branches: renderBranchesView,
        settings: renderSettingsView,
      }[state.activeTab]();
      elements.workspace.innerHTML = window.PortalI18n.html`
      <header class="repoMasthead">
        <div class="repoMastheadMain">${repoSelector()}</div>
        ${workspaceTabs()}
      </header>
      <div class="repoWorkspaceBody">${content}</div>`;
      mountRepoWorkbench();
      mountRepoFilePreview();
      bindFileFinderPopover();
      applyRenderedFilters();
      scheduleRepositoryStatistics();
    }

    function mountRepoWorkbench() {
      const root = elements.workspace.querySelector("#repoComposableWorkbench");
      if (!root) return;
      const descriptors = [...root.querySelectorAll("[data-repo-workbench-panel]")].map(element => ({
        id: element.dataset.repoWorkbenchPanel,
        type: "authored",
        presentation: element.dataset.repoWorkbenchType || "document",
        options: { source: element },
        title: element.getAttribute("aria-label") || element.dataset.repoWorkbenchPanel,
      }));
      if (!descriptors.length) return;
      const { split, stack } = window.PortalWorkbench.model;
      const weights = state.activeTab === "code" ? [3, 1] : [1, 2, 1.3];
      const layout = split("horizontal", descriptors.map(descriptor => stack([descriptor.id])), weights);
      repoWorkbench = window.PortalWorkbench.create({
        id: `repo-${state.activeTab}`,
        root,
        label: `${state.activeTab === "changes" ? "Changes" : "Code"} Workbench`,
        initialEditors: descriptors,
        defaultLayout: layout,
        presets: [
          { id: "complete", name: window.PortalI18n.t("完整工作台"), layout },
          ...descriptors.slice(1).map(descriptor => ({
            id: `focus-${descriptor.id}`,
            name: window.PortalI18n.text`专注 ${descriptor.title}`,
            layout: stack([descriptor.id]),
            hidden: descriptors.filter(item => item.id !== descriptor.id).map(item => item.id),
          })),
        ],
        defaultPreset: "complete",
        fitViewport: {
          bottom: 18,
          minHeight: 240,
          maxHeight: 980,
        },
      });
    }

    function mountRepoFilePreview() {
      const root = elements.workspace.querySelector("#repoFilePreviewHost");
      const file = state.file;
      if (!root || !file || file.kind === "symlink") return;
      const rawUrl = `/api/resources/content?address=${encodeURIComponent(`${state.selectedId}/file/${file.path}`)}`;
      const documentKind = window.PortalPreviewContract.normalizeKind({
        path: file.path,
        mediaType: file.mime_type,
        binary: file.binary,
      });
      repoFilePreviewPanel = window.PortalWorkbench.createPanel("preview", {
        root,
        panelId: "repo-file-preview",
        heading: file.name,
        note: file.path,
        renderContext: {
          markdownLayout: state.fileView === "preview" ? "document" : "lines",
          renderMarkdown: (content, options) => markdown(content, options.basePath),
        },
      });
      void repoFilePreviewPanel.setDocument({
          id: `${state.selectedId}:${file.path}`,
          title: file.name,
          path: file.path,
          kind: documentKind,
          mediaType: file.mime_type,
          binary: file.binary,
          truncated: Boolean(file.truncated),
          previewUnavailableReason: String(file.preview_unavailable_reason || ""),
          content: file.binary ? "" : String(file.content || "").split("\n").slice(0, 5000).join("\n"),
          rawUrl,
          downloadUrl: `${rawUrl}&download=1`,
        })
        .then(() => {
          root.querySelectorAll("[data-preview-line]").forEach(line => {
            line.id = `L${line.dataset.previewLine}`;
          });
        })
        .catch(error => showToast(error.message || error, "error"));
    }

    return Object.freeze({
      destroyFileFinderPopover, renderWorkspace, workspaceCapability,
    });
  }

  window.PortalRepoWorkspaceShell = Object.freeze({ createShell, dependencies: DEPENDENCIES });
})();
