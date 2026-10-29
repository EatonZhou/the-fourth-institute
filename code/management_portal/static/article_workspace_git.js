(() => {
  "use strict";

  if (window.PortalArticleWorkspaceGit) return;

  // The Git side view: status and recent history with their refresh, staging,
  // committing and initializing a repository, and the diff viewer for a
  // changed file or a commit. Refresh stops while an Article is unregistered.
  const DEPENDENCIES = Object.freeze([
    "$", "ARTICLE_GIT_REFRESH_MS", "esc", "languageForPath", "monacoApi", "renderDocumentTabs", "request",
    "showToast", "state",
  ]);

  function createGit(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceGit is missing: ${missing.join(", ")}`);
    const {
      $, ARTICLE_GIT_REFRESH_MS, esc, languageForPath, monacoApi, renderDocumentTabs, request, showToast,
      state,
    } = dependencies;

    function renderArticleGitBadge() {
      const button = $("articleGitView");
      const badge = $("articleGitBadge");
      if (!button || !badge) return;
      const available = Boolean(state.gitStatus?.available);
      const count = available && Array.isArray(state.gitStatus?.changes)
        ? state.gitStatus.changes.length
        : 0;
      badge.hidden = count === 0;
      badge.textContent = count > 99 ? "99+" : String(count);
      const label = count ? window.PortalI18n.text`源代码管理，${count} 个未提交变更` : window.PortalI18n.t("源代码管理");
      button.setAttribute("aria-label", label);
      button.title = label;
    }

    function renderArticleGit() {
      const status = state.gitStatus;
      const panel = $("articleGitPanel");
      const changesHost = $("articleGitChanges");
      const historyHost = $("articleGitHistory");
      const available = Boolean(status?.available);
      renderArticleGitBadge();
      panel.classList.toggle("is-unavailable", !available);
      $("articleGitIdentity").hidden = !available;
      $("articleGitCommitForm").hidden = !available;
      $("articleGitHistorySection").hidden = !available;
      $("articleGitCommit").disabled = !available || !Number(status?.staged_count || 0);
      if (!available) {
        $("articleGitSummary").textContent = window.PortalI18n.t("版本控制尚未启用");
        changesHost.innerHTML = window.PortalI18n.html`<div class="articleGitOnboarding">
        <div class="articleGitOnboardingMark" data-portal-icon="branch" aria-hidden="true"></div>
        <div class="articleGitOnboardingCopy">
          <strong>开始记录稿件版本</strong>
          <p>创建本地 Git 仓库，之后可以逐行审阅大模型修改、选择要提交的文件，并随时回看历史版本。</p>
        </div>
        <button class="portalButton is-primary" type="button" data-git-init data-portal-icon="plus"><span>启用 Git 版本管理</span></button>
        <small>${esc(status?.reason || window.PortalI18n.t("当前 Article 目录尚未纳入 Git 管理。"))}</small>
      </div>`;
        historyHost.replaceChildren();
        $("articleGitHistoryCount").textContent = "0";
        window.PortalIcons?.enhance?.(changesHost);
        return;
      }
      const head = status.head || {};
      const changes = Array.isArray(status.changes) ? status.changes : [];
      $("articleGitSummary").textContent = window.PortalI18n.text`${changes.length} 个变更 · ${Number(status.staged_count || 0)} 个已暂存`;
      $("articleGitIdentity").innerHTML = window.PortalI18n.html`<span data-portal-icon="branch" aria-hidden="true"></span><strong>${esc(status.branch || window.PortalI18n.t("detached HEAD"))}</strong><code>${esc(head.short_sha || window.PortalI18n.t("尚无提交"))} · ${esc(status.scope || ".")}</code>`;
      const sections = [
        ["已暂存的更改", changes.filter(item => item.staged), true],
        ["更改", changes.filter(item => item.unstaged || item.untracked), false],
      ];
      changesHost.innerHTML = sections.map(([title, items, staged]) => {
        if (!items.length) return "";
        return window.PortalI18n.html`<section class="articleGitChangeSection"><div class="articleGitSectionHead"><strong>${title}</strong><small>${items.length}</small></div>${items.map(item => window.PortalI18n.html`
        <div class="articleGitChangeRow" title="${esc(item.path)}">
          <button class="articleGitChangePath" type="button" data-git-diff="${esc(item.path)}">${esc(item.path.split("/").at(-1) || item.path)}<small>${esc(item.path.includes("/") ? item.path.slice(0, item.path.lastIndexOf("/")) : "")}</small></button>
          <span class="articleGitStatusCode" title="${esc(item.label || "")}">${esc(item.untracked ? "U" : item.conflicted ? "!" : String(item.code || "").trim() || "M")}</span>
          <button class="portalButton is-icon is-quiet iconBtn articleGitStageButton" type="button" data-git-stage="${esc(item.path)}" data-git-staged="${staged ? "false" : "true"}" aria-label="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")} ${esc(item.path)}" title="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")}" data-portal-icon="${staged ? "minus" : "plus"}"></button>
        </div>`).join("")}</section>`;
      }).join("") || window.PortalI18n.html`<div class="portalAsyncState is-compact" data-state="empty" data-density="compact" role="status"><span class="portalAsyncStateIcon" aria-hidden="true"></span><span class="portalAsyncStateMessage">工作树干净，没有未提交的稿件改动。</span></div>`;
      $("articleGitHistoryCount").textContent = String(state.gitHistory.length);
      historyHost.innerHTML = state.gitHistory.map(commit => window.PortalI18n.html`<button class="articleGitCommitRow" type="button" data-git-commit="${esc(commit.sha || "")}" data-git-commit-short="${esc(commit.short_sha || "")}" data-git-commit-subject="${esc(commit.subject || "")}" title="查看 ${esc(commit.short_sha || "commit")} 的更改"><code>${esc(commit.short_sha || "")}</code><span>${esc(commit.subject || window.PortalI18n.t("(无提交信息)"))}</span><span data-portal-icon="chevron-right" aria-hidden="true"></span></button>`).join("");
      window.PortalIcons?.enhance?.($("articleGitPanel"));
    }

    const unregisteringArticles = new Set();
    let articleGitHistoryRequested = false;
    const articleGitRefresh = window.PortalRefreshCoordinator.create({
      key: () => state.selectedId,
      interval: ARTICLE_GIT_REFRESH_MS,
      initialDelay: ARTICLE_GIT_REFRESH_MS,
      shouldRefresh: () => !!state.selectedId && !unregisteringArticles.has(state.selectedId),
      fetch: async ({ key, signal }) => {
        const id = encodeURIComponent(key);
        const includeHistory = articleGitHistoryRequested || state.sideView === "git";
        articleGitHistoryRequested = false;
        let historyError = null;
        const [status, history] = await Promise.all([
          request(`/api/articles/${id}/git`, { signal }),
          includeHistory ? request(`/api/articles/${id}/git/history?limit=12`, { signal }).catch(error => {
            historyError = error;
            return null;
          }) : Promise.resolve(null),
        ]);
        return { status, history, historyError };
      },
      apply: ({ status, history, historyError }) => {
        const commits = history ? (Array.isArray(history.commits) ? history.commits : []) : state.gitHistory;
        const changed = JSON.stringify([state.gitStatus, state.gitHistory]) !== JSON.stringify([status, commits]);
        state.gitStatus = status;
        state.gitHistory = commits;
        if (changed) renderArticleGit();
        if (historyError) throw historyError;
      },
    });

    async function loadArticleGit({ includeHistory = false, silent = false } = {}) {
      if (!state.selectedId || unregisteringArticles.has(state.selectedId)) return;
      articleGitHistoryRequested ||= includeHistory;
      articleGitRefresh.start();
      try {
        await articleGitRefresh.refresh({ force: true });
      } catch (error) {
        if (!silent) throw error;
      }
    }

    function scheduleArticleGitRefresh(delay = ARTICLE_GIT_REFRESH_MS) {
      if (!state.selectedId || unregisteringArticles.has(state.selectedId)) return;
      articleGitRefresh.start();
      articleGitRefresh.invalidate({ delay });
    }

    async function updateArticleGitStage(path, staged) {
      state.gitStatus = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/stage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: [path], staged }),
      });
      renderArticleGit();
    }

    async function initializeArticleGit() {
      state.gitStatus = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      await loadArticleGit({ includeHistory: true });
      showToast(window.PortalI18n.t("已为当前 Article 初始化 Git 仓库"), "success");
    }

    async function commitArticleGit(event) {
      event.preventDefault();
      const message = $("articleGitMessage").value.trim();
      if (!message) return;
      $("articleGitCommit").disabled = true;
      try {
        const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/commit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        });
        $("articleGitMessage").value = "";
        state.gitStatus = result.status;
        await loadArticleGit({ includeHistory: true });
        showToast(window.PortalI18n.text`已提交 ${result.commit?.short_sha || window.PortalI18n.t("Article 更改")}`, "success");
      } finally {
        $("articleGitCommit").disabled = !Number(state.gitStatus?.staged_count || 0);
      }
    }

    // A newly selected Article starts without the previous one's Git state.
    function resetArticleGit() {
      articleGitRefresh.stop();
      articleGitHistoryRequested = false;
      state.gitStatus = null;
      state.gitHistory = [];
      renderArticleGitBadge();
      closeArticleGitDiff();
    }

    function closeArticleGitDiff() {
      state.gitDiffPath = "";
      state.gitDiffTitle = "";
      state.gitDiffEditor?.dispose?.();
      state.gitDiffEditor = null;
      state.gitDiffModels.forEach(model => model?.dispose?.());
      state.gitDiffModels = [];
      state.gitDiffFiles = [];
      state.gitDiffActiveFile = 0;
      $("articleGitDiffEditor").replaceChildren();
      $("articleGitDiffFiles").replaceChildren();
      $("articleGitDiffView").hidden = true;
      $("articleEditorToolbar").hidden = false;
      renderDocumentTabs();
    }

    function cleanGitDiffPath(path, scope = "") {
      let value = String(path || "").replace(/^['\"]|['\"]$/g, "");
      value = value.replace(/^[ab]\//, "");
      const prefix = String(scope || "").replace(/^\.\/$|^\.$/, "").replace(/\/$/, "");
      if (prefix && value.startsWith(`${prefix}/`)) value = value.slice(prefix.length + 1);
      return value === "/dev/null" ? "" : value;
    }

    function gitPatchBlocks(diff) {
      const source = String(diff || "");
      const starts = [];
      const matcher = /^diff --git /gm;
      let match;
      while ((match = matcher.exec(source))) starts.push(match.index);
      if (!starts.length) return source ? [source] : [];
      return starts.map((start, index) => source.slice(start, starts[index + 1] ?? source.length));
    }

    function parseGitPatch(diff, { fallbackPath = "", scope = "" } = {}) {
      const blocks = gitPatchBlocks(diff);
      return blocks.map((block, blockIndex) => {
        const lines = block.split("\n");
        const header = lines.find(line => line.startsWith("diff --git ")) || "";
        const headerMatch = header.match(/^diff --git a\/(.+) b\/(.+)$/);
        const oldHeader = lines.find(line => line.startsWith("--- "))?.slice(4).split("\t")[0] || "";
        const newHeader = lines.find(line => line.startsWith("+++ "))?.slice(4).split("\t")[0] || "";
        const oldPath = cleanGitDiffPath(oldHeader || headerMatch?.[1] || "", scope);
        const newPath = cleanGitDiffPath(newHeader || headerMatch?.[2] || "", scope);
        const path = cleanGitDiffPath((blocks.length === 1 && fallbackPath) || newPath || oldPath || `变更 ${blockIndex + 1}`, scope);
        const original = [];
        const modified = [];
        let firstOriginalLine = 1;
        let firstModifiedLine = 1;
        let foundHunk = false;
        let inHunk = false;
        for (const line of lines) {
          const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
          if (hunk) {
            const oldStart = Math.max(1, Number(hunk[1]) || 1);
            const newStart = Math.max(1, Number(hunk[2]) || 1);
            if (!foundHunk) {
              firstOriginalLine = oldStart;
              firstModifiedLine = newStart;
            }
            while (original.length < oldStart - 1) original.push("");
            while (modified.length < newStart - 1) modified.push("");
            foundHunk = true;
            inHunk = true;
            continue;
          }
          if (!inHunk || line.startsWith("\\ No newline at end of file")) continue;
          if (line.startsWith("+")) modified.push(line.slice(1));
          else if (line.startsWith("-")) original.push(line.slice(1));
          else if (line.startsWith(" ")) {
            original.push(line.slice(1));
            modified.push(line.slice(1));
          } else if (line.startsWith("diff --git ")) inHunk = false;
        }
        return {
          path,
          originalPath: oldPath || path,
          modifiedPath: newPath || path,
          original: original.join("\n"),
          modified: modified.join("\n"),
          firstOriginalLine,
          firstModifiedLine,
          binary: /^(?:Binary files|GIT binary patch)/m.test(block),
          hasChanges: foundHunk,
        };
      });
    }

    function renderArticleGitDiffFile(index) {
      const file = state.gitDiffFiles[index];
      if (!file) return;
      state.gitDiffActiveFile = index;
      document.querySelectorAll("[data-git-diff-file]").forEach(button => {
        const active = Number(button.dataset.gitDiffFile) === index;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
      });
      state.gitDiffModels.forEach(model => model?.dispose?.());
      state.gitDiffModels = [];
      const monaco = monacoApi();
      const empty = $("articleGitDiffEmpty");
      if (!monaco || !file.hasChanges) {
        state.gitDiffEditor?.setModel?.(null);
        empty.textContent = file.binary ? window.PortalI18n.text`${file.path} 是二进制文件，无法显示文本差异。` : window.PortalI18n.text`${file.path} 没有可显示的文本差异。`;
        empty.hidden = false;
        $("articleGitDiffEditor").hidden = true;
        return;
      }
      empty.hidden = true;
      $("articleGitDiffEditor").hidden = false;
      if (!state.gitDiffEditor) {
        state.gitDiffEditor = monaco.editor.createDiffEditor($("articleGitDiffEditor"), {
          automaticLayout: true,
          readOnly: true,
          originalEditable: false,
          renderSideBySide: state.gitDiffSideBySide,
          useInlineViewWhenSpaceIsLimited: false,
          enableSplitViewResizing: true,
          renderIndicators: true,
          ignoreTrimWhitespace: false,
          diffWordWrap: "on",
          wordWrap: "on",
          minimap: { enabled: false },
          folding: false,
          glyphMargin: false,
          lineNumbersMinChars: 3,
          scrollBeyondLastLine: false,
          fontSize: 12,
          renderOverviewRuler: true,
          hideUnchangedRegions: { enabled: true, contextLineCount: 3, minimumLineCount: 4, revealLineCount: 8 },
        });
      }
      const revision = `${Date.now()}-${index}`;
      const language = languageForPath(file.modifiedPath || file.originalPath || file.path);
      const originalModel = monaco.editor.createModel(file.original, language, monaco.Uri.parse(`article-diff://old/${revision}/${file.originalPath}`));
      const modifiedModel = monaco.editor.createModel(file.modified, language, monaco.Uri.parse(`article-diff://new/${revision}/${file.modifiedPath}`));
      state.gitDiffModels = [originalModel, modifiedModel];
      state.gitDiffEditor.setModel({ original: originalModel, modified: modifiedModel });
      window.requestAnimationFrame(() => {
        state.gitDiffEditor?.layout?.();
        state.gitDiffEditor?.getModifiedEditor?.().revealLineInCenter(file.firstModifiedLine);
      });
    }

    function renderArticleGitDiff(title, diff, emptyMessage, options = {}) {
      state.gitDiffTitle = title;
      $("articleGitDiffTitle").textContent = title;
      $("articleGitDiff").textContent = diff;
      state.gitDiffFiles = parseGitPatch(diff, options);
      const filesHost = $("articleGitDiffFiles");
      filesHost.innerHTML = state.gitDiffFiles.map((file, index) => window.PortalI18n.html`<button class="articleGitDiffFile${index === 0 ? " active" : ""}" type="button" role="tab" aria-selected="${index === 0 ? "true" : "false"}" data-git-diff-file="${index}" title="${esc(file.path)}"><span>${esc(file.path)}</span><small>${file.binary ? "BIN" : "M"}</small></button>`).join("");
      filesHost.hidden = state.gitDiffFiles.length <= 1;
      $("articleGitDiffEmpty").textContent = emptyMessage;
      $("articleGitDiffEmpty").hidden = state.gitDiffFiles.length > 0;
      $("articleGitDiffEditor").hidden = state.gitDiffFiles.length === 0;
      $("articleGitDiffView").hidden = false;
      $("articleEditorToolbar").hidden = true;
      renderDocumentTabs();
      if (state.gitDiffFiles.length) renderArticleGitDiffFile(0);
    }

    function toggleArticleGitDiffLayout() {
      state.gitDiffSideBySide = !state.gitDiffSideBySide;
      state.gitDiffEditor?.updateOptions?.({
        renderSideBySide: state.gitDiffSideBySide,
        useInlineViewWhenSpaceIsLimited: false,
      });
      $("articleGitDiffLayout").textContent = state.gitDiffSideBySide ? window.PortalI18n.t("并排") : window.PortalI18n.t("内联");
      $("articleGitDiffLayout").setAttribute("aria-pressed", String(state.gitDiffSideBySide));
    }

    async function openArticleGitDiff(path) {
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/diff?path=${encodeURIComponent(path)}`);
      state.gitDiffPath = path;
      renderArticleGitDiff(`${path} · 工作树变更`, String(payload.diff || ""), "该文件当前没有可显示的工作树差异。", { fallbackPath: path, scope: payload.scope || "" });
    }

    async function openArticleGitCommit(commitSha, shortSha, subject) {
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/diff?commit=${encodeURIComponent(commitSha)}`);
      const commit = payload.commit || {};
      const label = commit.short_sha || shortSha || commitSha.slice(0, 7);
      const message = commit.subject || subject || window.PortalI18n.t("(无提交信息)");
      state.gitDiffPath = `commit:${commit.sha || commitSha}`;
      renderArticleGitDiff(`${label} · ${message}`, String(payload.diff || ""), "该提交在当前 Article 目录中没有可显示的更改。", { scope: payload.scope || "" });
    }

    return Object.freeze({
      articleGitRefresh, closeArticleGitDiff, commitArticleGit, initializeArticleGit, loadArticleGit,
      openArticleGitCommit, openArticleGitDiff, renderArticleGitDiffFile, resetArticleGit,
      scheduleArticleGitRefresh, toggleArticleGitDiffLayout, unregisteringArticles, updateArticleGitStage,
    });
  }

  window.PortalArticleWorkspaceGit = Object.freeze({ createGit, dependencies: DEPENDENCIES });
})();
