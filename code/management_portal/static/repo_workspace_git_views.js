(() => {
  "use strict";

  if (window.PortalRepoWorkspaceGitViews) return;

  // The Git tabs as markup: working-tree changes and a change's diff, the
  // commit list and one commit with split or unified file diffs, branches,
  // and settings with remotes, push policy, SSH key and saved credentials;
  // and the filters that hide rows of those rendered lists.
  const DEPENDENCIES = Object.freeze([
    "dirname", "elements", "esc", "formatDateTime", "repoStateHtml", "state", "uiIcon",
  ]);

  function createGitViews(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceGitViews is missing: ${missing.join(", ")}`);
    const { dirname, elements, esc, formatDateTime, repoStateHtml, state, uiIcon } = dependencies;

    function renderChangesView() {
      const status = state.overview.status || {};
      const changes = status.changes || [];
      if (!changes.length) {
        return window.PortalI18n.html`
        <section class="repoCleanState">
          <span class="repoCleanIcon" data-portal-icon="check"></span>
          <h2>没有本地变更</h2>
          <p>当前 checkout 与 HEAD 一致。编辑或新建文件后，改动会出现在这里。</p>
          <button class="portalButton is-primary" type="button" data-repo-tab="code">返回代码</button>
        </section>`;
      }
      const counts = {
        staged: changes.filter(change => change.staged).length,
        unstaged: changes.filter(change => change.unstaged).length,
        untracked: changes.filter(change => change.untracked).length,
        conflicts: changes.filter(change => change.conflicted).length,
      };
      const diffContent = state.changeDiffLoading
        ? repoStateHtml(`正在读取 ${state.selectedChange} 的 Diff。`, { state: "loading", density: "compact" })
        : state.changeDiffError
          ? window.PortalI18n.html`<div class="repoDiffError"><strong>无法读取 Diff</strong><span>${esc(state.changeDiffError)}</span><button class="portalButton" type="button" data-retry-change="${esc(state.selectedChange)}">重试</button></div>`
          : state.changeDiff
            ? renderDiff(state.changeDiff)
            : repoStateHtml("选择一个变更查看 Diff。", { density: "compact" });
      return window.PortalI18n.html`
      <div class="repoChangesLayout repoComposableWorkbench" id="repoComposableWorkbench">
        <aside class="repoChangesList" data-repo-workbench-panel="changes" data-repo-workbench-type="navigation" aria-label="Changed Files">
          <header class="repoChangesListHeader">
            <div><strong>Changes</strong><span id="repoChangesVisibleCount">${changes.length} 个文件</span></div>
            <label data-portal-search-field data-portal-search-density="compact"><input data-portal-search-input id="repoChangesFilter" type="search" value="${esc(state.changeListQuery)}" placeholder="筛选文件…" autocomplete="off" /><kbd>⇧T</kbd></label>
            <nav aria-label="变更类型筛选">
              ${[
                  ["all", window.PortalI18n.t("全部"), changes.length],
                  ["staged", "Staged", counts.staged],
                  ["unstaged", "Unstaged", counts.unstaged],
                  ["untracked", "Untracked", counts.untracked],
                  ["conflicts", "Conflicts", counts.conflicts],
                ].map(([key, label, count]) => window.PortalI18n.html`<button type="button" data-change-filter="${key}" aria-pressed="${state.changeFilter === key}" class="${state.changeFilter === key ? "active" : ""}" ${count || key === "all" ? "" : "disabled"}>${label}<small>${count}</small></button>`).join("")}
            </nav>
          </header>
          <div class="repoChangesItems">
            ${changes.map(change => window.PortalI18n.html`
              <button type="button" data-change-path="${esc(change.path)}" data-change-search="${esc(`${change.path} ${change.label || ""}`.toLowerCase())}" data-change-staged="${change.staged ? "1" : "0"}" data-change-unstaged="${change.unstaged ? "1" : "0"}" data-change-untracked="${change.untracked ? "1" : "0"}" data-change-conflicts="${change.conflicted ? "1" : "0"}" aria-current="${change.path === state.selectedChange ? "true" : "false"}" class="${change.path === state.selectedChange ? "active" : ""}">
                <span class="repoStatusCode status-${esc(change.code.trim() || "new")}">${esc(change.code)}</span>
                <span class="repoChangeDetails">
                  <strong>${esc(change.path.split("/").at(-1))}</strong>
                  <span class="repoChangeMeta">
                    <small>${esc(dirname(change.path) || window.PortalI18n.t("仓库根目录"))}</small>
                    ${change.modified_at ? window.PortalI18n.html`<time datetime="${esc(change.modified_at)}" title="${esc(formatDateTime(change.modified_at))}">${esc(formatDateTime(change.modified_at))}</time>` : ""}
                  </span>
                </span>
              </button>
            `).join("")}
            <div class="repoFilterEmpty" id="repoChangesFilterEmpty" hidden>没有匹配的变更。</div>
          </div>
        </aside>
        <section class="repoDiffPanel" data-repo-workbench-panel="diff" data-repo-workbench-type="editor" aria-label="Diff Preview">
          ${diffContent}
        </section>
        <aside class="repoCommitPanel" data-repo-workbench-panel="commit" data-repo-workbench-type="document" aria-label="Commit Changes">
          <div class="repoCommitPanelHeading">
            <span class="repoCommitAvatar">${esc(String(window.portalCurrentUser || "DB").slice(0, 2).toUpperCase())}</span>
            <div><strong>提交到 ${esc(state.overview.current_branch || "HEAD")}</strong><span>${changes.length} 个文件将一起提交</span></div>
          </div>
          ${status.conflict_count ? window.PortalI18n.html`<div class="repoCommitWarning">存在 ${status.conflict_count} 个冲突，解决后才能提交。</div>` : ""}
          <form id="repoCommitForm">
            <label><span>Commit message</span><input name="message" maxlength="200" required placeholder="简短说明这次改动" autocomplete="off" value="${esc(state.commitDraft.message)}" /></label>
            <label><span>Extended description（可选）</span><textarea name="description" maxlength="8000" placeholder="补充背景、实现方式或验证结果">${esc(state.commitDraft.description)}</textarea></label>
            <button class="portalButton is-primary repoFull" type="submit" ${status.conflict_count ? "disabled" : ""}>提交 ${changes.length} 个文件</button>
          </form>
          <p>Portal 会执行 <code>git add -A</code>，提交全部 ${changes.length} 个文件（${counts.staged} staged · ${counts.unstaged} unstaged · ${counts.untracked} untracked）；Push 需要单独确认。<kbd>Ctrl/⌘ Enter</kbd> 可提交。</p>
        </aside>
      </div>`;
    }

    function renderDiff(payload) {
      const change = payload.change || {};
      const lines = String(payload.diff || "").split("\n");
      return window.PortalI18n.html`
      <header class="repoDiffHeader">
        <div><span class="repoStatusCode">${esc(change.code || "")}</span><strong>${esc(payload.path)}</strong></div>
        <div class="repoDiffHeaderActions">
          <span>${esc(window.PortalI18n.t(change.label || "变更"))}${change.modified_at ? ` · ${esc(formatDateTime(change.modified_at))}` : ""}${payload.truncated ? window.PortalI18n.t(" · Diff 已截断") : ""}</span>
          <button type="button" data-copy-file-path="${esc(payload.path)}" aria-label="复制文件路径" title="复制文件路径">${uiIcon("copy")}</button>
          ${String(change.code || "").includes("D") ? "" : window.PortalI18n.html`<button type="button" data-open-file="${esc(payload.path)}" aria-label="打开文件" title="打开文件">${uiIcon("code")}</button>`}
        </div>
      </header>
      ${payload.diff
          ? window.PortalI18n.html`<div class="repoDiffViewer">${lines.map(line => {
              const kind = line.startsWith("+") && !line.startsWith("+++")
                ? "add"
                : line.startsWith("-") && !line.startsWith("---")
                  ? "delete"
                  : line.startsWith("@@")
                    ? "hunk"
                    : line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("---") || line.startsWith("+++")
                      ? "meta"
                      : "";
              return window.PortalI18n.html`<div class="repoDiffLine ${kind}"><span>${esc(line) || " "}</span></div>`;
            }).join("")}</div>`
          : window.PortalI18n.t('<div class="repoDiffEmpty">该变更没有可显示的文本 Diff。</div>')
        }`;
    }

    function renderCommitsView() {
      const commits = state.commits?.commits || [];
      if (state.commitListLoading && !commits.length) {
        return repoStateHtml("正在读取提交记录。", { state: "loading", density: "panel" });
      }
      if (state.commitListError && !commits.length) {
        return window.PortalI18n.html`<section class="repoListError"><strong>无法读取提交记录</strong><span>${esc(state.commitListError)}</span><button class="portalButton" type="button" data-repo-action="reload-tab">重试</button></section>`;
      }
      if (!commits.length) {
        return window.PortalI18n.markup('<div class="repoCleanState"><h2>尚无提交记录</h2><p>在 Changes 中创建第一个 commit。</p></div>');
      }
      if (state.commitDetail) return renderCommitDetail(state.commitDetail);
      const groups = new Map();
      commits.forEach(commit => {
        const date = new Date(commit.authored_at);
        // Commit groups retain weekday and long-month semantics; PortalUI owns the formatting.
        const key = Number.isNaN(date.getTime())
          ? "日期未知"
          : window.PortalUI.formatDateTime(date, {
              format: {
              year: "numeric",
              month: "long",
              day: "numeric",
              weekday: "short",
              },
            });
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(commit);
      });
      return window.PortalI18n.html`
      <section class="repoCommitsPage">
        ${state.commitListError ? window.PortalI18n.html`<div class="repoInlineError">刷新失败：${esc(state.commitListError)}</div>` : ""}
        <div class="repoCommitGroups">
          ${[...groups.entries()].map(([date, items]) => window.PortalI18n.html`
            <section class="repoCommitGroup" data-commit-group>
              <h3>Commits on ${esc(date)}</h3>
              <div>
                ${items.map(commit => window.PortalI18n.html`
                  <article class="repoCommitListItem" data-commit-search="${esc(`${commit.subject || ""} ${commit.body || ""} ${commit.author || ""} ${commit.sha || ""} ${(commit.refs || []).join(" ")}`.toLowerCase())}">
                    <span class="repoCommitAvatar">${esc(String(commit.author || "DB").slice(0, 2).toUpperCase())}</span>
                    <button class="repoCommitListMain" type="button" data-open-commit="${esc(commit.sha)}">
                      <strong>${esc(commit.subject)}</strong>
                      <span><b>${esc(commit.author || "Unknown")}</b> committed ${esc(formatDateTime(commit.authored_at))}</span>
                    </button>
                    <span class="repoCommitRefs">${commit.refs.slice(0, 3).map(ref => window.PortalI18n.html`<em>${esc(ref)}</em>`).join("")}</span>
                    <div class="repoCommitListActions">
                      <button class="repoCommitSha" type="button" data-open-commit="${esc(commit.sha)}" title="打开 commit ${esc(commit.sha)}">${esc(commit.short_sha)}</button>
                      <button class="portalButton is-icon is-quiet repoIconButton" type="button" data-copy-commit="${esc(commit.sha)}" aria-label="复制 commit SHA" title="复制 commit SHA">${uiIcon("copy")}</button>
                    </div>
                  </article>
                `).join("")}
              </div>
            </section>
          `).join("")}
          <div class="repoFilterEmpty" id="repoCommitsFilterEmpty" hidden>没有匹配的 commit。</div>
        </div>
        ${state.commits?.has_more ? window.PortalI18n.html`<footer class="repoListFooter"><button class="portalButton" type="button" data-repo-action="load-more-commits" ${state.commitListLoading ? "disabled" : ""}>${state.commitListLoading ? window.PortalI18n.t("加载中…") : window.PortalI18n.t("加载更多")}</button></footer>` : ""}
      </section>`;
    }

    function renderCommitDetail(payload) {
      const commit = payload.commit;
      const stats = payload.stats || {};
      const files = Array.isArray(payload.files) ? payload.files : [];
      const totalChanges = Number(stats.additions || 0) + Number(stats.deletions || 0);
      const additionShare = totalChanges
        ? Number(stats.additions || 0) * 100 / totalChanges
        : 0;
      return window.PortalI18n.html`
      <section class="repoCommitPage">
        <header class="repoCommitTitleBar">
          <div><span>Commit</span><code>${esc(commit.short_sha)}</code></div>
          <div>
            <button class="portalButton" type="button" data-repo-action="close-commit">返回提交列表</button>
            <button class="portalButton" type="button" data-repo-action="browse-files">${uiIcon("code")}<span>浏览当前工作区</span></button>
          </div>
        </header>
        <div class="repoCommitAuthorLine">
          <span class="repoCommitAvatar">${esc(String(commit.author || "DB").slice(0, 2).toUpperCase())}</span>
          <strong>${esc(commit.author || "Unknown")}</strong>
          <span>committed ${esc(formatDateTime(commit.authored_at))}</span>
        </div>
        <section class="repoCommitSummaryCard">
          <div class="repoCommitMessage">
            <strong>${esc(commit.subject)}</strong>
            ${commit.body ? window.PortalI18n.html`<p>${esc(commit.body)}</p>` : ""}
          </div>
          <div class="repoCommitMetaRow">
            <span class="repoCommitRefs">${commit.refs.map(ref => window.PortalI18n.html`<em>${esc(ref)}</em>`).join("")}</span>
            <span class="repoCommitIdentifiers">
              ${commit.parents.length
                  ? `${commit.parents.length} parent${commit.parents.length === 1 ? "" : "s"} ${commit.parents.map(parent => window.PortalI18n.html`<button type="button" data-open-commit="${esc(parent)}">${esc(parent.slice(0, 7))}</button>`).join(" ")}`
                  : window.PortalI18n.t("root commit")
                }
              <span>commit <code>${esc(commit.short_sha)}</code></span>
              <button class="portalButton is-icon is-quiet repoIconButton" type="button" data-copy-commit="${esc(commit.sha)}" aria-label="复制完整 commit SHA" title="${esc(commit.sha)}">${uiIcon("copy")}</button>
            </span>
          </div>
          <div class="repoCommitStatsRow">
            <strong>变更 ${Number(stats.files_changed || files.length)} 个文件</strong>
            <span class="repoCommitTotals"><b>+${Number(stats.additions || 0).toLocaleString()}</b><i>−${Number(stats.deletions || 0).toLocaleString()}</i></span>
            <span class="repoCommitChangeBar" aria-label="新增 ${Number(stats.additions || 0)} 行，删除 ${Number(stats.deletions || 0)} 行">
              <i style="width:${additionShare}%"></i><b style="width:${100 - additionShare}%"></b>
            </span>
          </div>
        </section>
        <div class="repoCommitDiffLayout">
          <aside class="repoCommitFileNav">
            <label data-portal-search-field data-portal-search-density="compact">
              <input data-portal-search-input id="repoCommitFileFilter" type="search" placeholder="筛选文件…" autocomplete="off" />
            </label>
            <span id="repoCommitFileCount">${files.length} 个文件</span>
            <nav>
              ${files.map((file, index) => window.PortalI18n.html`
                <button type="button" data-commit-file-target="repoCommitFile${index}" data-commit-path="${esc(file.path)}">
                  <span class="repoCommitFileStatus status-${esc(String(file.status_code || "M").toLowerCase())}">${esc(file.status_code || "M")}</span>
                  <span title="${esc(file.path)}">${esc(file.path)}</span>
                  <small><b>+${Number(file.additions || 0)}</b><i>−${Number(file.deletions || 0)}</i></small>
                </button>
              `).join("")}
            </nav>
          </aside>
          <main class="repoCommitDiffMain">
            ${payload.truncated || payload.files_truncated || payload.hidden_file_count ? window.PortalI18n.html`
              <div class="repoCommitNotice">
                <strong>部分内容未展开</strong>
                <span>该提交较大；页面保留完整文件统计，并只渲染后端安全上限内的文本 diff。</span>
              </div>` : ""}
            <div class="repoCommitDiffToolbar">
              <label data-portal-search-field data-portal-search-density="compact"><input data-portal-search-input id="repoCommitCodeSearch" type="search" placeholder="在 diff 中搜索…" autocomplete="off" /></label>
              <div class="repoSegmented" aria-label="Diff 显示方式">
                <button type="button" data-commit-diff-mode="split" aria-pressed="${state.commitDiffMode === "split"}" class="${state.commitDiffMode === "split" ? "active" : ""}">Split</button>
                <button type="button" data-commit-diff-mode="unified" aria-pressed="${state.commitDiffMode === "unified"}" class="${state.commitDiffMode === "unified" ? "active" : ""}">Unified</button>
              </div>
            </div>
            <div class="repoCommitFileDiffs">
              ${files.map((file, index) => renderCommitFileDiff(file, index)).join("")}
            </div>
          </main>
        </div>
      </section>`;
    }

    function parsePatchRows(patch) {
      const rows = [];
      let oldLine = 0;
      let newLine = 0;
      let inHunk = false;
      String(patch || "").split("\n").forEach(line => {
        const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
        if (hunk) {
          oldLine = Number(hunk[1]);
          newLine = Number(hunk[2]);
          inHunk = true;
          rows.push({ kind: "hunk", text: line });
          return;
        }
        if (!inHunk) return;
        if (line.startsWith("+") && !line.startsWith("+++")) {
          rows.push({ kind: "add", old: "", next: newLine++, text: line.slice(1) });
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          rows.push({ kind: "delete", old: oldLine++, next: "", text: line.slice(1) });
        } else if (line.startsWith(" ")) {
          rows.push({ kind: "context", old: oldLine++, next: newLine++, text: line.slice(1) });
        } else if (line.startsWith("\\")) {
          rows.push({ kind: "meta", text: line });
        }
      });
      return rows;
    }

    function renderUnifiedDiff(rows) {
      if (!rows.length) return window.PortalI18n.markup('<div class="repoCommitDiffEmpty">没有可显示的文本 diff。</div>');
      return window.PortalI18n.html`<div class="repoUnifiedDiff">${rows.map(row => {
        if (row.kind === "hunk" || row.kind === "meta") {
          return window.PortalI18n.html`<div class="repoUnifiedDiffRow ${row.kind}"><span></span><span></span><code>${esc(row.text)}</code></div>`;
        }
        const marker = row.kind === "add" ? "+" : row.kind === "delete" ? "−" : " ";
        return window.PortalI18n.html`<div class="repoUnifiedDiffRow ${row.kind}" data-diff-text="${esc(row.text.toLowerCase())}">
        <span>${row.old}</span><span>${row.next}</span><code><i>${marker}</i>${esc(row.text) || " "}</code>
      </div>`;
      }).join("")}</div>`;
    }

    function renderSplitDiff(rows) {
      if (!rows.length) return window.PortalI18n.markup('<div class="repoCommitDiffEmpty">没有可显示的文本 diff。</div>');
      const output = [];
      for (let index = 0; index < rows.length;) {
        const row = rows[index];
        if (row.kind === "hunk" || row.kind === "meta") {
          output.push(window.PortalI18n.html`<div class="repoSplitDiffRow ${row.kind}"><code>${esc(row.text)}</code></div>`);
          index += 1;
          continue;
        }
        if (row.kind === "context") {
          output.push(window.PortalI18n.html`<div class="repoSplitDiffRow context" data-diff-text="${esc(row.text.toLowerCase())}">
          <span>${row.old}</span><code>${esc(row.text) || " "}</code>
          <span>${row.next}</span><code>${esc(row.text) || " "}</code>
        </div>`);
          index += 1;
          continue;
        }
        const changed = [];
        while (index < rows.length && ["add", "delete"].includes(rows[index].kind)) {
          changed.push(rows[index]);
          index += 1;
        }
        const deleted = changed.filter(item => item.kind === "delete");
        const added = changed.filter(item => item.kind === "add");
        const count = Math.max(deleted.length, added.length);
        for (let offset = 0; offset < count; offset += 1) {
          const left = deleted[offset];
          const right = added[offset];
          const searchText = `${left?.text || ""} ${right?.text || ""}`.trim().toLowerCase();
          output.push(window.PortalI18n.html`<div class="repoSplitDiffRow change" data-diff-text="${esc(searchText)}">
          <span class="${left ? "delete" : "empty"}">${left?.old || ""}</span>
          <code class="${left ? "delete" : "empty"}">${left ? esc(left.text) || " " : ""}</code>
          <span class="${right ? "add" : "empty"}">${right?.next || ""}</span>
          <code class="${right ? "add" : "empty"}">${right ? esc(right.text) || " " : ""}</code>
        </div>`);
        }
      }
      return window.PortalI18n.html`<div class="repoSplitDiff">${output.join("")}</div>`;
    }

    function renderCommitFileDiff(file, index) {
      const rows = parsePatchRows(file.patch);
      return window.PortalI18n.html`
      <section class="repoCommitFileCard" id="repoCommitFile${index}" data-commit-path="${esc(file.path)}">
        <header>
          <div>
            <span class="repoCommitFileStatus status-${esc(String(file.status_code || "M").toLowerCase())}">${esc(file.status_code || "M")}</span>
            <strong title="${esc(file.path)}">${esc(file.path)}</strong>
            ${file.original_path ? window.PortalI18n.html`<small>from ${esc(file.original_path)}</small>` : ""}
          </div>
          <span><b>+${Number(file.additions || 0)}</b><i>−${Number(file.deletions || 0)}</i></span>
        </header>
        ${file.binary
            ? window.PortalI18n.t('<div class="repoCommitDiffEmpty">二进制文件已变更。</div>')
            : file.patch
              ? state.commitDiffMode === "split"
                ? renderSplitDiff(rows)
                : renderUnifiedDiff(rows)
              : window.PortalI18n.t('<div class="repoCommitDiffEmpty">该文件的 diff 未在当前响应中展开。</div>')
          }
      </section>`;
    }

    function branchTrackingLabel(value) {
      return {
        "=": window.PortalI18n.t("已同步"),
        ">": window.PortalI18n.t("领先 upstream"),
        "<": window.PortalI18n.t("落后 upstream"),
        "<>": window.PortalI18n.t("已分叉"),
      }[String(value || "")] || String(value || "");
    }

    function renderBranchesView() {
      const overview = state.overview;
      return window.PortalI18n.html`
      <div class="repoBranchesPage">
        <section class="repoBranchList">
          ${overview.branches.map(branch => window.PortalI18n.html`
            <article class="repoBranchRow ${branch.current ? "current" : ""}" data-branch-search="${esc(`${branch.name} ${branch.subject || ""} ${branch.author || ""} ${branch.upstream || ""} ${branch.short_sha || ""}`.toLowerCase())}">
              <span class="repoBranchGlyph">${uiIcon("branch")}</span>
              <div class="repoBranchMain">
                <div><strong>${esc(branch.name)}</strong>${branch.name === overview.default_branch ? "<em>default</em>" : ""}${branch.current ? "<em>current</em>" : ""}</div>
                <p>${esc(branch.subject || window.PortalI18n.t("尚无 commit message"))}</p>
                <small><button type="button" data-open-commit="${esc(branch.sha)}">${esc(branch.short_sha)}</button> · ${esc(formatDateTime(branch.updated_at))}${branch.upstream ? ` · ${esc(branch.upstream)}` : ""}</small>
              </div>
              <div class="repoBranchActions">
                ${branch.tracking ? window.PortalI18n.html`<span>${esc(branchTrackingLabel(branch.tracking))}</span>` : ""}
                <button class="portalButton is-icon is-quiet repoIconButton" type="button" data-copy-branch="${esc(branch.name)}" aria-label="复制分支名 ${esc(branch.name)}" title="复制分支名">${uiIcon("copy")}</button>
                <button class="portalButton" type="button" data-switch-branch="${esc(branch.name)}" ${branch.current || overview.status.dirty ? "disabled" : ""}>${branch.current ? window.PortalI18n.t("已签出") : window.PortalI18n.t("切换")}</button>
              </div>
            </article>
          `).join("")}
          <div class="repoFilterEmpty" id="repoBranchesFilterEmpty" hidden>没有匹配的分支。</div>
        </section>
        <section class="repoTagsSection">
          <header><h2>Tags</h2><span>${overview.tag_count}</span></header>
          ${overview.tags.length
              ? window.PortalI18n.html`<div>${overview.tags.map(tag => window.PortalI18n.html`
                <article><strong>${esc(tag.name)}</strong><span>${esc(tag.subject || tag.short_sha)}</span><time>${esc(formatDateTime(tag.created_at))}</time></article>
              `).join("")}</div>`
              : window.PortalI18n.t('<p>当前仓库没有 tag。</p>')
            }
        </section>
      </div>`;
    }

    function credentialTypeLabel(value) {
      return {
        token: "TOKEN",
        password_or_token: "HTTPS",
        ssh_secret: "SSH",
      }[String(value || "")] || "GIT";
    }

    function renderCredentialSettings() {
      if (state.credentials == null) {
        return window.PortalI18n.markup('<div class="repoCredentialSettings"><div class="repoCredentialEmpty">正在读取当前账号的 Git 凭据…</div></div>');
      }
      if (state.credentials.error) {
        return window.PortalI18n.html`<div class="repoCredentialSettings"><div class="repoCredentialEmpty">${esc(state.credentials.error)}</div></div>`;
      }
      const remotes = Array.isArray(state.credentials.remotes) ? state.credentials.remotes : [];
      const credentials = Array.isArray(state.credentials.credentials) ? state.credentials.credentials : [];
      const remoteMarkup = remotes.length
        ? window.PortalI18n.html`<div class="repoCredentialContextList">${remotes.map(remote => {
            const protocol = String(remote.protocol || "").toUpperCase() || "LOCAL";
            const status = remote.secure === false ? window.PortalI18n.t("需升级") : protocol;
            return window.PortalI18n.html`
            <article class="repoCredentialContext">
              <div>
                <strong>${esc(remote.host || window.PortalI18n.t("本地 remote"))}</strong>
                <span>${esc(`${remote.remote || "remote"}${remote.path ? ` · ${remote.path}` : ""}`)}</span>
              </div>
              <em>${esc(status)}</em>
            </article>`;
          }).join("")}</div>`
        : window.PortalI18n.markup('<div class="repoCredentialEmpty">当前 Repo 没有 remote，无需配置认证。</div>');
      const credentialMarkup = credentials.length
        ? window.PortalI18n.html`<div class="repoSavedCredentialList">${credentials.map(credential => {
            const active = credential.status === "active";
            const lastUsed = credential.last_used_at
              ? window.PortalI18n.text` · ${formatDateTime(credential.last_used_at)}使用`
              : "";
            return window.PortalI18n.html`
            <article class="repoSavedCredential ${active ? "active" : "invalid"}">
              <div>
                <strong>${esc(credential.username || "git")}@${esc(credential.host)}</strong>
                <span>${esc(String(credential.protocol || "").toUpperCase())}${esc(lastUsed)}</span>
              </div>
              <em>${active ? credentialTypeLabel(credential.auth_type) : window.PortalI18n.t("需更新")}</em>
              <button class="portalButton is-danger" type="button" data-delete-git-credential="${esc(credential.credential_id)}">删除</button>
            </article>`;
          }).join("")}</div>`
        : window.PortalI18n.markup('<div class="repoCredentialEmpty">尚未保存 Git 凭据。Remote 要求认证时，Portal 会自动打开安全认证窗口。</div>');
      return window.PortalI18n.html`
      <div class="repoCredentialSettings">
        <p class="repoCredentialSettingsIntro">凭据按当前 Portal 用户和 Git 主机隔离。页面只显示类型与使用状态，不会返回令牌、密码或私钥口令。</p>
        ${remoteMarkup}
        ${credentialMarkup}
      </div>`;
    }

    function renderSettingsView() {
      const overview = state.overview;
      const primary = overview.remotes.find(remote => remote.name === "origin") || overview.remotes[0] || {};
      return window.PortalI18n.html`
      <div class="repoSettingsPage">
        <section class="repoSettingsCard">
          <header><h3>Checkout</h3><span>LOCAL</span></header>
          <dl>
            <div><dt>Unified ID</dt><dd><code>${esc(overview.id)}</code><button type="button" data-repo-action="copy-id">${uiIcon("copy")}</button></dd></div>
            <div><dt>Path</dt><dd><code>${esc(overview.checkout_path)}</code></dd></div>
            <div><dt>Current branch</dt><dd>${esc(overview.current_branch || window.PortalI18n.t("detached HEAD"))}</dd></div>
            <div><dt>Projects</dt><dd>${overview.project_ids.map(project => window.PortalI18n.html`<span class="repoSettingPill">${esc(project)}</span>`).join("") || "—"}</dd></div>
          </dl>
        </section>
        <section class="repoSettingsCard">
          <header><h3>Remote / Push source</h3><span>GIT</span></header>
          <form id="repoRemoteForm" class="repoRemoteSettings">
            <label><span>Name</span><input name="name" value="${esc(primary.name || "origin")}" required autocomplete="off" /></label>
            <label><span>Fetch URL</span><input name="url" value="${esc(primary.credentials_redacted ? "" : (primary.url || ""))}" placeholder="${primary.credentials_redacted ? window.PortalI18n.t("凭据已隐藏；请输入完整的新 URL") : "git@host:owner/repo.git"}" autocomplete="off" /></label>
            <label><span>Push URL</span><input name="push_url" value="${esc(primary.credentials_redacted ? "" : (primary.push_url || primary.url || ""))}" placeholder="${primary.credentials_redacted ? window.PortalI18n.t("凭据已隐藏；请输入完整的新 URL") : window.PortalI18n.t("默认使用 Fetch URL")}" autocomplete="off" /></label>
            ${primary.credentials_redacted ? window.PortalI18n.t('<p class="repoSettingsNotice">现有 URL 中的凭据已隐藏。只有重新输入完整 URL 并保存时才会更新 remote。</p>') : ""}
            <div><button class="portalButton is-primary" type="submit">保存 Remote</button></div>
          </form>
        </section>
        <section class="repoSettingsCard">
          <header><h3>Push 行为</h3><span>POLICY</span></header>
          <form id="repoPushPolicyForm" class="repoPushPolicy">
            <label>
              <input type="checkbox" name="push_enabled" ${overview.push_enabled === false ? "" : "checked"} />
              <span><strong>允许 Portal Push 此 Repo</strong><small>关闭后，工具栏 Push 会停用；AI 一键提交只执行 git add 和 commit。</small></span>
            </label>
            <button class="portalButton is-primary" type="submit">保存设置</button>
          </form>
        </section>
        <section class="repoSettingsCard">
          <header><h3>SSH 密钥</h3><span>AUTH</span></header>
          <form id="repoSshKeyForm" class="repoRemoteSettings repoSshKeySettings">
            <label>
              <span>此 Repo 使用的密钥</span>
              <select name="ssh_key">
                <option value="">遵循 SSH config / 系统默认</option>
                ${(overview.available_ssh_keys || []).map(key => (
                    window.PortalI18n.html`<option value="${esc(key)}" ${overview.ssh_key === key ? "selected" : ""}>${esc(key)}</option>`
                  )).join("")}
              </select>
            </label>
            <p class="repoSettingsNotice">仅保存密钥文件名。私钥内容不会进入 Repo 注册记录，也不会返回到浏览器。</p>
            <div><button class="portalButton is-primary" type="submit">保存密钥</button></div>
          </form>
        </section>
        <section class="repoSettingsCard">
          <header><h3>Git 凭据</h3><span>MY ACCOUNT</span></header>
          ${renderCredentialSettings()}
        </section>
        <section class="repoSettingsCard danger">
          <header><h3>解除 Repo 注册</h3><span>DANGER ZONE</span></header>
          <div class="repoDangerRow">
            <div><strong>从 Resource 中移除此 Repo</strong><p>只删除注册记录。Git checkout、分支、提交和工作文件都会保留。</p></div>
            <button class="portalButton is-danger" type="button" data-repo-action="unregister">解除注册</button>
          </div>
        </section>
      </div>`;
    }

    function filterCommitFiles(query) {
      const clean = String(query || "").trim().toLowerCase();
      let visible = 0;
      elements.workspace.querySelectorAll(".repoCommitFileCard").forEach(card => {
        const match = !clean || String(card.dataset.commitPath || "").toLowerCase().includes(clean);
        card.hidden = !match;
        if (match) visible += 1;
      });
      elements.workspace.querySelectorAll(".repoCommitFileNav [data-commit-path]").forEach(button => {
        button.hidden = Boolean(clean) && !String(button.dataset.commitPath || "").toLowerCase().includes(clean);
      });
      const count = elements.workspace.querySelector("#repoCommitFileCount");
      if (count) count.textContent = window.PortalI18n.text`${visible} 个文件`;
    }

    function filterCommitCode(query) {
      const clean = String(query || "").trim().toLowerCase();
      elements.workspace.querySelectorAll("[data-diff-text]").forEach(row => {
        row.hidden = Boolean(clean) && !String(row.dataset.diffText || "").includes(clean);
      });
    }

    function applyChangeFilters() {
      const query = state.changeListQuery.trim().toLowerCase();
      let visible = 0;
      elements.workspace.querySelectorAll(".repoChangesItems > [data-change-path]").forEach(button => {
        const matchesText = !query || String(button.dataset.changeSearch || "").includes(query);
        const matchesType = state.changeFilter === "all"
          || button.dataset[`change${state.changeFilter[0].toUpperCase()}${state.changeFilter.slice(1)}`] === "1";
        button.hidden = !(matchesText && matchesType);
        if (!button.hidden) visible += 1;
      });
      elements.workspace.querySelectorAll("[data-change-filter]").forEach(button => {
        const active = button.dataset.changeFilter === state.changeFilter;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      const count = elements.workspace.querySelector("#repoChangesVisibleCount");
      if (count) count.textContent = window.PortalI18n.text`${visible} 个文件`;
      const empty = elements.workspace.querySelector("#repoChangesFilterEmpty");
      if (empty) empty.hidden = visible > 0;
    }

    function applyCommitListFilter() {
      const query = state.commitListQuery.trim().toLowerCase();
      let visible = 0;
      elements.workspace.querySelectorAll("[data-commit-group]").forEach(group => {
        let groupVisible = 0;
        group.querySelectorAll("[data-commit-search]").forEach(item => {
          const matches = !query || String(item.dataset.commitSearch || "").includes(query);
          item.hidden = !matches;
          if (matches) {
            visible += 1;
            groupVisible += 1;
          }
        });
        group.hidden = groupVisible === 0;
      });
      const count = elements.workspace.querySelector("#repoCommitListCount");
      if (count) {
        const branch = state.commitRevision === "HEAD"
          ? (state.overview.current_branch || "HEAD")
          : state.commitRevision;
        count.textContent = window.PortalI18n.text`${branch} · ${visible} 条`;
      }
      const empty = elements.workspace.querySelector("#repoCommitsFilterEmpty");
      if (empty) empty.hidden = visible > 0;
    }

    function applyBranchFilter() {
      const query = state.branchListQuery.trim().toLowerCase();
      let visible = 0;
      elements.workspace.querySelectorAll("[data-branch-search]").forEach(row => {
        const matches = !query || String(row.dataset.branchSearch || "").includes(query);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      const empty = elements.workspace.querySelector("#repoBranchesFilterEmpty");
      if (empty) empty.hidden = visible > 0;
    }

    function applyRenderedFilters() {
      if (state.activeTab === "changes") applyChangeFilters();
      else if (state.activeTab === "commits" && !state.commitDetail) applyCommitListFilter();
      else if (state.activeTab === "branches") applyBranchFilter();
    }

    return Object.freeze({
      applyBranchFilter, applyChangeFilters, applyCommitListFilter, applyRenderedFilters, filterCommitCode,
      filterCommitFiles, renderBranchesView, renderChangesView, renderCommitsView, renderSettingsView,
    });
  }

  window.PortalRepoWorkspaceGitViews = Object.freeze({ createGitViews, dependencies: DEPENDENCIES });
})();
