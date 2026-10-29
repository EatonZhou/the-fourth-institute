(() => {
  "use strict";

  if (window.PortalRepoWorkspaceCodeView) return;

  // The Code tab: the branch toolbar, the file finder's results and keyboard
  // selection, the breadcrumb, a directory with its last commits, the README,
  // the About panel with size and language statistics, and one file's view.
  const DEPENDENCIES = Object.freeze([
    "dirname", "elements", "esc", "formatDateTime", "getJson", "isMarkdown", "joinPath", "markdown",
    "repoStateHtml", "state", "uiIcon", "workspaceCapability",
  ]);

  function createCodeView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceCodeView is missing: ${missing.join(", ")}`);
    const {
      dirname, elements, esc, formatDateTime, getJson, isMarkdown, joinPath, markdown, repoStateHtml, state,
      uiIcon, workspaceCapability,
    } = dependencies;

    function formatBytes(value) {
      return window.PortalUI.formatBytes(value, {
        invalidFallback: "—",
        maximumUnit: "TB",
        precision: "single",
      });
    }

    function repositoryStatisticsKey() {
      return workspaceCapability("statistics") && state.activeTab === "code" && state.tree && !state.file
        ? `${state.selectedId}:${state.tree.path || ""}`
        : "";
    }

    function repositoryStatisticsMarkup() {
      const stats = state.statistics;
      if (!stats || `${stats.id}:${stats.path || ""}` !== repositoryStatisticsKey()) {
        return window.PortalI18n.html`<p class="repoMuted">${esc(state.statisticsError || "正在读取仓库统计…")}</p>`;
      }
      return window.PortalI18n.html`${renderLanguages(stats.languages, stats.code_line_count)}${renderRepositorySize(stats.repository_size_bytes)}`;
    }

    function scheduleRepositoryStatistics() {
      const key = repositoryStatisticsKey();
      if (!key || state.statisticsLoadingKey === key ||
          (state.statistics && `${state.statistics.id}:${state.statistics.path || ""}` === key)) return;
      state.statisticsLoadingKey = key;
      state.statisticsError = "";
      const repoId = state.selectedId;
      const path = state.tree.path || "";
      window.setTimeout(async () => {
        try {
          const stats = await getJson(`/api/repos/${encodeURIComponent(repoId)}/workspace/stats?path=${encodeURIComponent(path)}`);
          if (repositoryStatisticsKey() !== key) return;
          state.statistics = stats;
        } catch (error) {
          if (repositoryStatisticsKey() !== key) return;
          state.statisticsError = error.message || String(error);
        } finally {
          if (state.statisticsLoadingKey === key) state.statisticsLoadingKey = "";
          if (repositoryStatisticsKey() === key) {
            const host = elements.workspace.querySelector("[data-repo-statistics]");
            if (host) host.innerHTML = repositoryStatisticsMarkup();
          }
        }
      }, 0);
    }

    function statusSummary() {
      if (!workspaceCapability("git")) return "";
      const status = state.overview.status || {};
      const chips = [];
      if (status.conflict_count) chips.push(window.PortalI18n.html`<span class="repoSyncState conflict">${status.conflict_count} 个冲突</span>`);
      if (status.dirty) chips.push(window.PortalI18n.html`<span class="repoSyncState dirty">${status.changes?.length || 0} 项本地改动</span>`);
      else chips.push(window.PortalI18n.t('<span class="repoSyncState clean">工作区干净</span>'));
      if (status.ahead) chips.push(window.PortalI18n.html`<span class="repoSyncState ahead">↑${Number(status.ahead)} 个提交领先</span>`);
      if (status.behind) chips.push(window.PortalI18n.html`<span class="repoSyncState behind">↓${Number(status.behind)} 个提交落后</span>`);
      if (!state.overview.upstream) chips.push(window.PortalI18n.t('<span class="repoSyncState muted">无 upstream</span>'));
      if (state.overview.remotes?.length && state.overview.push_enabled === false) {
        chips.push(window.PortalI18n.t('<span class="repoSyncState pull-only">仅拉取</span>'));
      }
      return window.PortalI18n.html`<span class="repoSyncStates">${chips.join("")}</span>`;
    }

    function branchToolbar() {
      const overview = state.overview;
      if (!workspaceCapability("git")) {
        return window.PortalI18n.html`<div class="repoCodeToolbar">
        <span>${uiIcon("code")}<strong>Source</strong></span>
        <div class="repoToolbarSpacer"></div>
        <button class="portalButton is-icon is-quiet repoIconButton" data-portal-icon="refresh" type="button" data-repo-action="refresh-workspace" aria-label="刷新 Repo 状态" title="刷新 Repo 状态"></button>
      </div>`;
      }
      const status = overview.status || {};
      const dirty = Boolean(status.dirty);
      const defaultBranch = String(overview.default_branch || "");
      const onDefaultBranch = !defaultBranch || defaultBranch === overview.current_branch;
      const pullDisabledReason = !overview.remotes.length
        ? "没有 remote，不能 Pull"
        : dirty
          ? "存在未提交改动，先提交或放弃后再 Pull"
          : "";
      const pushDisabledReason = !overview.remotes.length
        ? "没有 remote，不能 Push"
        : !overview.current_branch
          ? "Detached HEAD 不能直接 Push"
          : overview.push_enabled === false
            ? "已在 Repo 设置中禁用 Push"
            : "";
      return window.PortalI18n.html`
      <div class="repoCodeToolbar">
        <div class="repoBranchControl">
          ${uiIcon("branch")}
          <span class="repoBranchCurrent">${esc(overview.current_branch || window.PortalI18n.t("detached HEAD"))}</span>
          <select id="repoBranchPicker" aria-label="切换分支" ${dirty ? window.PortalI18n.t('disabled title="存在未提交改动时不能切换分支"') : ""}>
            ${overview.branches.map(branch => window.PortalI18n.html`<option value="${esc(branch.name)}" ${branch.current ? "selected" : ""}>${esc(branch.name)}</option>`).join("")}
          </select>
          ${uiIcon("chevron-down")}
        </div>
        ${!onDefaultBranch ? window.PortalI18n.html`<button class="portalButton is-compact repoDefaultBranchButton" type="button" data-switch-branch="${esc(defaultBranch)}" ${dirty ? "disabled" : ""} title="${dirty ? window.PortalI18n.t("存在未提交改动，不能切换分支") : window.PortalI18n.text`返回默认分支 ${defaultBranch}`}">↩ <span>${esc(defaultBranch)}</span></button>` : ""}
        <button class="portalButton is-compact is-quiet repoTextButton" type="button" data-repo-tab="branches">${uiIcon("branch")}<span>${overview.branch_count} branches</span></button>
        <span class="repoToolbarDivider"></span>
        <button class="portalButton is-compact is-quiet repoTextButton" type="button" data-repo-tab="branches"><span>${overview.tag_count} tags</span></button>
        <span class="repoToolbarUpstream" title="${esc(overview.upstream || window.PortalI18n.t("未配置 upstream"))}">${esc(overview.upstream || window.PortalI18n.t("No upstream"))}${status.ahead ? ` · ↑${Number(status.ahead)}` : ""}${status.behind ? ` · ↓${Number(status.behind)}` : ""}</span>
        <div class="repoToolbarSpacer"></div>
        <div class="repoFileFinderWrap">
          <button class="portalButton repoGoToFile" type="button" data-repo-action="file-finder">${uiIcon("search")}<span>转到文件</span><kbd>t</kbd></button>
          ${state.fileFinderOpen ? renderFileFinder() : ""}
        </div>
        <button class="portalButton" type="button" data-repo-action="new-file" ${overview.checkout_writable ? "" : "disabled"}>${uiIcon("plus")}<span>新建文件</span></button>
        <button class="portalButton is-icon is-quiet repoIconButton" data-portal-icon="refresh" type="button" data-repo-action="refresh-workspace" aria-label="刷新 Repo 状态" title="刷新 Repo 状态"></button>
        <div class="repoSyncButtons" aria-label="Git 同步">
          <button class="portalButton" type="button" data-repo-sync="pull" ${pullDisabledReason ? "disabled" : ""} title="${esc(pullDisabledReason || `Pull ${overview.upstream || "remote"}`)}">Pull</button>
          <button class="portalButton is-primary" type="button" data-repo-sync="push" ${pushDisabledReason ? "disabled" : ""} title="${esc(pushDisabledReason || `Push ${overview.current_branch}`)}">Push</button>
          <button class="portalButton is-danger" type="button" data-repo-discard ${status.dirty ? "" : "disabled"} title="放弃全部 staged、unstaged 和 untracked changes">放弃改动</button>
        </div>
      </div>`;
    }

    function renderFileFinder() {
      const results = state.fileSearch?.results || [];
      return window.PortalI18n.html`
      <div class="repoFileFinder" role="dialog" aria-label="转到文件">
        <div class="repoFileFinderInput" data-portal-search-field data-portal-search-density="compact">
          <input data-portal-search-input id="repoFileFinderInput" type="search" role="combobox" aria-controls="repoFileFinderResults" aria-expanded="true" aria-autocomplete="list" aria-activedescendant="${results.length ? `repoFileFinderOption${Math.min(state.fileSearchIndex, results.length - 1)}` : ""}" autocomplete="off" placeholder="输入文件名或路径…" value="${esc(state.fileSearchQuery)}" />
          <kbd>Esc</kbd>
        </div>
        <div class="repoFileFinderResults" id="repoFileFinderResults" role="listbox">
          ${state.fileSearch == null
              ? window.PortalI18n.t('<div class="repoFileFinderHint">输入关键词，在当前 checkout 中查找文件。</div>')
              : results.length
                ? results.map((result, index) => window.PortalI18n.html`
                <button id="repoFileFinderOption${index}" type="button" role="option" aria-selected="${index === state.fileSearchIndex ? "true" : "false"}" class="${index === state.fileSearchIndex ? "active" : ""}" data-file-finder-index="${index}" data-open-file="${esc(result.path)}">
                  ${uiIcon("file")}
                  <span><strong>${esc(result.name)}</strong><small>${esc(result.directory || window.PortalI18n.t("仓库根目录"))}</small></span>
                </button>
              `).join("")
                : window.PortalI18n.t('<div class="repoFileFinderHint">没有匹配文件。</div>')
            }
        </div>
      </div>`;
    }

    function breadcrumb(path, { file = false } = {}) {
      const parts = String(path || "").split("/").filter(Boolean);
      const directoryParts = file ? parts.slice(0, -1) : parts;
      const currentFile = file ? parts.at(-1) : "";
      let accumulated = "";
      const links = [
        window.PortalI18n.html`<button type="button" data-open-directory="">${esc(state.overview.name)}</button>`,
        ...directoryParts.map(part => {
          accumulated = joinPath(accumulated, part);
          return window.PortalI18n.html`<span>/</span><button type="button" data-open-directory="${esc(accumulated)}">${esc(part)}</button>`;
        }),
      ];
      if (currentFile) links.push(window.PortalI18n.html`<span>/</span><strong>${esc(currentFile)}</strong>`);
      return window.PortalI18n.html`<nav class="repoBreadcrumb" aria-label="Repo path">${links.join("")}</nav>`;
    }

    function renderCodeView() {
      if (state.file) return renderFileView();
      if (!state.tree) return repoStateHtml("正在读取目录。", { state: "loading", density: "compact" });
      const tree = state.tree;
      return window.PortalI18n.html`
      ${branchToolbar()}
      <div class="repoCodeLayout repoComposableWorkbench" id="repoComposableWorkbench">
        <div class="repoCodeMain" data-repo-workbench-panel="code" data-repo-workbench-type="navigation" data-portal-workbench-inset="comfortable" aria-label="Repository Files">
          ${breadcrumb(tree.path)}
          <section class="repoTreePanel">
            ${renderTreeCommitHeader(tree.head)}
            <div class="repoTreeTable" role="table" aria-label="${esc(tree.path || window.PortalI18n.t("仓库根目录"))}">
              ${tree.path ? window.PortalI18n.html`
                <button class="repoTreeRow repoTreeParent" type="button" data-open-directory="${esc(dirname(tree.path))}" role="row">
                  <span class="repoTreeName" role="cell">${uiIcon("folder")}<strong>..</strong></span>
                  <span role="cell"></span><span role="cell"></span>
                </button>` : ""}
              ${tree.entries.map(renderTreeEntry).join("")}
              ${!tree.entries.length ? window.PortalI18n.html`
                <div class="repoTreeEmpty">
                  ${uiIcon("folder")}
                  <strong>这个目录是空的</strong>
                  ${workspaceCapability("write_files") ? window.PortalI18n.html`<span>可以直接在当前目录创建第一个文件。</span>
                  <button class="portalButton is-primary" type="button" data-repo-action="new-file">${uiIcon("plus")}<span>新建文件</span></button>` : ""}
                </div>` : ""}
              ${tree.truncated ? window.PortalI18n.t('<div class="repoTreeTruncated">目录内容较多，仅显示前 1000 项。</div>') : ""}
            </div>
          </section>
          ${tree.readme ? renderReadme(tree.readme) : ""}
        </div>
        ${renderAboutPanel()}
      </div>`;
    }

    function renderTreeCommitHeader(head) {
      if (!workspaceCapability("git")) return "";
      if (!head) {
        return window.PortalI18n.html`<header class="repoTreeCommit"><span class="repoCommitAvatar">DB</span><strong>尚无提交</strong><span>新建文件后可在 Changes 中创建首个 commit。</span></header>`;
      }
      return window.PortalI18n.html`
      <header class="repoTreeCommit">
        <span class="repoCommitAvatar">${esc(String(head.author || "DB").slice(0, 2).toUpperCase())}</span>
        <strong>${esc(head.author || "Unknown")}</strong>
        <span class="repoTreeCommitMessage">${esc(head.subject)}</span>
        <button type="button" data-open-commit="${esc(head.sha)}" title="${esc(head.sha)}"><code>${esc(head.short_sha)}</code></button>
        <time>${esc(formatDateTime(head.authored_at))}</time>
        <button class="repoTreeHistory" type="button" data-repo-tab="commits">${uiIcon("history")}<span>提交记录</span></button>
      </header>`;
    }

    function renderTreeEntry(entry) {
      const icon = entry.kind === "directory"
        ? uiIcon("folder")
        : entry.kind === "symlink"
          ? uiIcon("symlink")
          : uiIcon("file");
      const openAttribute = entry.kind === "directory"
        ? `data-open-directory="${esc(entry.path)}"`
        : `data-open-file="${esc(entry.path)}"`;
      return window.PortalI18n.html`
      <button class="repoTreeRow" type="button" ${openAttribute} role="row">
        <span class="repoTreeName" role="cell">${icon}<strong>${esc(entry.name)}</strong>${entry.change_count > 1 ? window.PortalI18n.html`<small class="repoEntryStatus">${Number(entry.change_count)} changes</small>` : entry.status ? window.PortalI18n.html`<small class="repoEntryStatus">${esc(entry.status)}</small>` : ""}</span>
        <span class="repoTreeMessage" role="cell">${esc(entry.last_commit?.subject || (entry.status ? window.PortalI18n.t("尚未提交") : ""))}</span>
        <time role="cell">${esc(formatDateTime(entry.last_commit?.authored_at || entry.modified_at))}</time>
      </button>`;
    }

    function renderReadme(readme) {
      return window.PortalI18n.html`
      <section class="repoReadmePanel">
        <header>
          <div>${uiIcon("file")}<strong>${esc(readme.name)}</strong></div>
          <button class="portalButton is-icon is-quiet repoIconButton" type="button" data-open-file="${esc(readme.path)}" aria-label="打开 ${esc(readme.name)}" title="打开文件">${uiIcon("code")}</button>
        </header>
        <article class="repoMarkdown">${markdown(readme.content, readme.path)}</article>
      </section>`;
    }

    function renderAboutPanel() {
      const overview = state.overview;
      if (!workspaceCapability("git")) {
        return window.PortalI18n.html`<aside class="repoAbout" data-repo-workbench-panel="about" data-repo-workbench-type="document" data-portal-workbench-inset="comfortable" aria-label="Repository About">
        <section><h2>About</h2><p>${esc(overview.description || overview.name)}</p></section>
        <section><div class="repoAboutHeading"><h3>Source</h3>
          <button type="button" data-copy-checkout="${esc(overview.checkout_path)}" aria-label="复制 checkout 路径" title="复制 checkout 路径">${uiIcon("copy")}</button></div>
          <code class="repoCheckoutPath" title="${esc(overview.checkout_path)}">${esc(overview.checkout_path)}</code>
        </section>
      </aside>`;
      }
      const primaryRemote = overview.remotes.find(remote => remote.name === "origin") || overview.remotes[0];
      return window.PortalI18n.html`
      <aside class="repoAbout" data-repo-workbench-panel="about" data-repo-workbench-type="document" data-portal-workbench-inset="comfortable" aria-label="Repository About">
        <section>
          <h2>About</h2>
          <p>${esc(overview.description || window.PortalI18n.t("Portal host 上注册的 Git checkout。"))}</p>
          <dl>
            <div><dt>当前分支</dt><dd>${esc(overview.current_branch || window.PortalI18n.t("detached HEAD"))}</dd></div>
            <div><dt>默认分支</dt><dd>${esc(overview.default_branch || window.PortalI18n.t("未设置"))}</dd></div>
            <div><dt>Upstream</dt><dd>${esc(overview.upstream || window.PortalI18n.t("未配置"))}</dd></div>
          </dl>
          ${statusSummary()}
        </section>
        <section>
          <div class="repoAboutHeading">
            <h3>Checkout</h3>
            <button type="button" data-copy-checkout="${esc(overview.checkout_path)}" aria-label="复制 checkout 路径" title="复制 checkout 路径">${uiIcon("copy")}</button>
          </div>
          <code class="repoCheckoutPath" title="${esc(overview.checkout_path)}">${esc(overview.checkout_path)}</code>
        </section>
        <section>
          <div class="repoAboutHeading"><h3>Remote</h3></div>
          ${primaryRemote
              ? window.PortalI18n.html`<div class="repoRemoteSummary">
                <div><strong>${esc(primaryRemote.name)}</strong><span>
                  <button type="button" data-copy-remote="${esc(primaryRemote.url)}" aria-label="复制 Remote URL" title="复制 Remote URL">${uiIcon("copy")}</button>
                  ${primaryRemote.web_url ? window.PortalI18n.html`<a href="${esc(primaryRemote.web_url)}" target="_blank" rel="noopener noreferrer" aria-label="在浏览器打开 Remote" title="${esc(primaryRemote.web_url)}">${uiIcon("external-link")}</a>` : ""}
                </span></div>
                <code title="${esc(primaryRemote.url)}">${esc(primaryRemote.url)}</code>
              </div>`
              : window.PortalI18n.t('<p class="repoMuted">尚未配置 remote。</p>')
            }
        </section>
        <section class="repoStats">
          <button type="button" data-repo-tab="commits"><strong>${esc(state.overview.head?.short_sha || "—")}</strong><span>HEAD</span></button>
          <button type="button" data-repo-tab="branches"><strong>${state.overview.branch_count}</strong><span>Branches</span></button>
          <button type="button" data-repo-tab="branches"><strong>${state.overview.tag_count}</strong><span>Tags</span></button>
        </section>
        <div data-repo-statistics>${repositoryStatisticsMarkup()}</div>
      </aside>`;
    }

    function renderRepositorySize(sizeBytes) {
      return window.PortalI18n.html`
      <section class="repoRepositorySize" aria-label="仓库总大小" title="已跟踪及未忽略的工作区文件，不含 .git">
        <div>
          <h3>Repository size</h3>
          <span>不含 .git</span>
        </div>
        <strong>${esc(formatBytes(sizeBytes))}</strong>
      </section>`;
    }

    function renderLanguages(languages, lineCount) {
      const items = Array.isArray(languages) ? languages : [];
      if (!items.length) return "";
      const lines = Math.max(0, Number(lineCount || 0));
      return window.PortalI18n.html`
      <section class="repoLanguages">
        <div class="repoLanguageHeading">
          <h3>Languages</h3>
          <span>${lines.toLocaleString()} lines</span>
        </div>
        <div class="repoLanguageBar" aria-label="仓库语言占比">
          ${items.map(language => {
              const percent = Math.max(0, Math.min(100, Number(language.percent || 0)));
              return window.PortalI18n.html`<span style="width:${percent}%;background:${esc(language.color || "#8c959f")}" title="${esc(language.name)} ${percent.toFixed(1)}%"></span>`;
            }).join("")}
        </div>
        <div class="repoLanguageLegend">
          ${items.map(language => window.PortalI18n.html`
            <span>
              <i style="background:${esc(language.color || "#8c959f")}"></i>
              <strong>${esc(language.name)}</strong>
              <em>${Number(language.percent || 0).toFixed(1)}%</em>
            </span>
          `).join("")}
        </div>
      </section>`;
    }

    function renderFileView() {
      const file = state.file;
      const rawUrl = `/api/resources/content?address=${encodeURIComponent(`${state.selectedId}/file/${file.path}`)}`;
      const markdownFile = isMarkdown(file.path, file.mime_type);
      return window.PortalI18n.html`
      ${branchToolbar()}
      <div class="repoFilePage">
        ${breadcrumb(file.path, { file: true })}
        <section class="repoFilePanel">
          <header class="repoFileHeader">
            <div class="repoFileMeta">
              ${file.kind === "symlink" ? uiIcon("symlink") : uiIcon("file")}
              <strong>${esc(file.name)}</strong>
              <span>${file.kind === "symlink" ? window.PortalI18n.t("symbolic link") : `${file.line_count} lines · ${formatBytes(file.size)}`}</span>
            </div>
            <div class="repoFileActions">
              ${markdownFile && !file.binary ? window.PortalI18n.html`
                <div class="repoSegmented" aria-label="文件显示方式">
                  <button type="button" data-file-view="code" aria-pressed="${state.fileView === "code"}" class="${state.fileView === "code" ? "active" : ""}">Code</button>
                  <button type="button" data-file-view="preview" aria-pressed="${state.fileView === "preview"}" class="${state.fileView === "preview" ? "active" : ""}">Preview</button>
                </div>` : ""}
              <button class="portalButton is-compact" type="button" data-copy-file-path="${esc(file.path)}">${uiIcon("copy")}<span>复制路径</span></button>
              ${!file.binary && file.kind !== "symlink" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-copy-file-content>${uiIcon("copy")}<span>复制内容</span></button>` : ""}
              ${workspaceCapability("git") ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-file-history="${esc(file.path)}">${uiIcon("history")}<span>History</span></button>
              <button class="portalButton is-compact" type="button" data-file-blame="${esc(file.path)}">Blame <kbd>b</kbd></button>` : ""}
              <a class="portalButton is-compact" href="${esc(rawUrl)}" target="_blank" rel="noopener">${uiIcon("external-link")}<span>Raw</span></a>
              ${workspaceCapability("write_files") && file.editable ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-edit-file="${esc(file.path)}">${uiIcon("edit")}<span>编辑</span></button>` : ""}
              ${workspaceCapability("write_files") && file.kind !== "symlink" ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet is-danger repoIconButton" type="button" data-delete-file="${esc(file.path)}" aria-label="删除 ${esc(file.name)}" title="删除文件">${uiIcon("trash")}</button>` : ""}
            </div>
          </header>
          ${renderFileContent(file)}
        </section>
        ${file.last_commit ? window.PortalI18n.html`
          <div class="repoFileLastCommit">
            <span>最近修改</span>
            <strong>${esc(file.last_commit.subject)}</strong>
            <code>${esc(file.last_commit.short_sha)}</code>
            <time>${esc(formatDateTime(file.last_commit.authored_at))}</time>
          </div>` : ""}
      </div>`;
    }

    function renderFileContent(file) {
      if (file.kind === "symlink") {
        return window.PortalI18n.html`<div class="repoBinaryState">${uiIcon("symlink")}<strong>Symbolic link</strong><code>${esc(file.symlink_target)}</code></div>`;
      }
      return window.PortalI18n.markup('<div id="repoFilePreviewHost" class="repoFilePreviewHost"></div>');
    }

    function moveFileFinderSelection(delta) {
      const results = state.fileSearch?.results || [];
      if (!results.length) return;
      state.fileSearchIndex = (state.fileSearchIndex + delta + results.length) % results.length;
      elements.workspace.querySelectorAll("[data-file-finder-index]").forEach((button, index) => {
        const active = index === state.fileSearchIndex;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
        if (active) button.scrollIntoView({ block: "nearest" });
      });
      const input = elements.workspace.querySelector("#repoFileFinderInput");
      if (input) input.setAttribute("aria-activedescendant", window.PortalI18n.text`repoFileFinderOption${state.fileSearchIndex}`);
    }

    return Object.freeze({
      moveFileFinderSelection, renderCodeView, scheduleRepositoryStatistics, statusSummary,
    });
  }

  window.PortalRepoWorkspaceCodeView = Object.freeze({ createCodeView, dependencies: DEPENDENCIES });
})();
