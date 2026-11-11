(() => {
  "use strict";

  if (window.PortalArticleWorkspaceGit) return;

  // Lanes are pending parent SHAs. Each edge joins actual commit parents, including merges.
  function graphRows(commits) {
    let lanes = [];
    return commits.map(commit => {
      let lane = lanes.indexOf(commit.sha);
      const incoming = lane >= 0;
      if (!incoming) { lane = lanes.length; lanes.push(commit.sha); }
      const before = [...lanes];
      lanes.splice(lane, 1);
      (commit.parents || []).forEach((parent, index) => {
        if (!lanes.includes(parent)) lanes.splice(Math.min(lane + index, lanes.length), 0, parent);
      });
      const edges = before.flatMap((sha, index) => sha === commit.sha ? [] : [[index, lanes.indexOf(sha), false]]);
      (commit.parents || []).forEach(parent => edges.push([lane, lanes.indexOf(parent), true]));
      return { lane, incoming, edges, width: Math.max(before.length, lanes.length, 1) * 16 + 12 };
    });
  }

  // The Git side view: status and recent history with their refresh, staging,
  // committing and initializing a repository, and the diff viewer for a
  // changed file or a commit. Refresh stops while an Article is unregistered.
  const DEPENDENCIES = Object.freeze([
    "$", "ARTICLE_GIT_REFRESH_MS", "esc", "languageForPath", "monacoApi", "renderDocumentTabs", "request",
    "showToast", "state", "captureActiveDocument", "hasDirtyDocuments", "refreshAfterPull", "openArticleFile",
  ]);

  function createGit(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceGit is missing: ${missing.join(", ")}`);
    const {
      $, ARTICLE_GIT_REFRESH_MS, esc, languageForPath, monacoApi, renderDocumentTabs, request, showToast,
      state, captureActiveDocument, hasDirtyDocuments, refreshAfterPull, openArticleFile,
    } = dependencies;
    const busyArticles = new Map();
    const collapsedGroups = new Set();
    let historyLimit = 40;
    let historyHasMore = false;
    const actionLabels = { fetch: "获取", pull: "拉取", push: "推送", sync: "同步", branch: "切换分支", discard: "放弃更改", stage: "更新暂存区", commit: "提交", init: "启用 Git" };

    function actionButton(action, label, icon, extra = "") {
      return `<button class="portalButton is-icon is-quiet iconBtn" type="button" data-git-action="${action}" title="${esc(label)}" aria-label="${esc(label)}" data-portal-icon="${icon}" ${extra}></button>`;
    }

    function renderHistory() {
      const commits = state.gitHistory || [];
      const rows = graphRows(commits);
      const width = Math.max(28, ...rows.map(row => row.width));
      const colors = ["var(--accent)", "#b578df", "#3e9b76", "#bd8427", "#db719b"];
      const x = lane => 10 + lane * 16;
      const path = (from, to, half) => `<path d="M${x(from)},${half ? 18 : 0} C${x(from)},28 ${x(to)},28 ${x(to)},36" stroke="${colors[from % colors.length]}" fill="none" stroke-width="2"/>`;
      $("articleGitHistoryCount").textContent = String(commits.length);
      $("articleGitHistory").hidden = collapsedGroups.has("history");
      $("articleGitHistorySection").classList.toggle("is-collapsed", collapsedGroups.has("history"));
      const toggle = $("articleGitHistorySection").querySelector('[data-git-action="toggle-history"]');
      toggle.textContent = `${collapsedGroups.has("history") ? "›" : "⌄"} 图表`;
      toggle.setAttribute("aria-expanded", String(!collapsedGroups.has("history")));
      $("articleGitHistory").innerHTML = commits.map((commit, i) => {
        const row = rows[i];
        return `<button class="articleGitCommitRow" type="button" data-git-commit="${esc(commit.sha)}" data-git-commit-short="${esc(commit.short_sha)}" data-git-commit-subject="${esc(commit.subject)}" title="${esc(`${commit.short_sha} · ${commit.author} · ${commit.authored_at}\n${commit.subject}`)}">
          <svg aria-hidden="true" width="${width}" height="36" viewBox="0 0 ${width} 36">${row.edges.map(edge => path(...edge)).join("")}${row.incoming ? `<path d="M${x(row.lane)},0 V18" stroke="${colors[row.lane % colors.length]}" stroke-width="2"/>` : ""}<circle cx="${x(row.lane)}" cy="18" r="4" fill="${colors[row.lane % colors.length]}"/></svg>
          <span class="articleGitCommitText"><strong>${esc(commit.subject || "(无提交信息)")}</strong><small>${esc(commit.author || "")} · ${esc(commit.short_sha || "")}</small></span>
          <span class="articleGitRefs">${(commit.refs || []).map(ref => `<span>${esc(ref)}</span>`).join("")}</span></button>`;
      }).join("") + (historyHasMore && historyLimit < 500 ? '<button class="portalButton is-quiet" data-git-action="more-history" type="button">加载更多提交</button>' : "")
        || '<div class="articleGitEmpty">尚无提交</div>';
    }

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
      const busy = busyArticles.get(state.selectedId);
      $("articleGitPull").disabled = !!busy || !status?.pull?.available;
      $("articleGitPull").setAttribute("aria-busy", String(!!busy));
      $("articleGitPullLabel").textContent = window.PortalI18n.t(busy === "pull" ? "正在拉取…" : "拉取");
      $("articleGitPullHint").textContent = busy ? `正在${actionLabels[busy] || busy}…`
        : status?.pull?.reason || (status?.pull?.remote ? `${status.pull.remote}/${status.pull.branch}` : window.PortalI18n.t("尚未配置远端仓库。"));
      renderArticleGitBadge();
      panel.classList.toggle("is-unavailable", !available);
      $("articleGitIdentity").hidden = !available;
      $("articleGitCommitForm").hidden = !available;
      $("articleGitHistorySection").hidden = !available;
      $("articleGitCommit").disabled = !!busy || !available || !Number(status?.staged_count || 0) || !!status?.conflict_count;
      panel.querySelectorAll('[data-git-action="fetch"], [data-git-action="push"], [data-git-action="sync"]').forEach(button => {
        const capability = button.dataset.gitAction === "sync" ? status?.pull : status?.remote;
        button.disabled = !!busy || !capability?.available;
        button.title = capability?.reason || `${actionLabels[button.dataset.gitAction]} ${capability?.remote || ""}/${capability?.branch || ""}`;
      });
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
      $("articleGitIdentity").innerHTML = `<button class="articleGitBranch" type="button" data-git-action="branch" ${busy || status.scope !== "." ? "disabled" : ""} title="切换或创建分支"><span data-portal-icon="branch"></span><strong>${esc(status.branch)}</strong><span>⌄</span></button><small title="${esc(status.upstream || "尚未设置上游分支")}">${status.ahead == null ? esc(head.short_sha || "尚无提交") : `↓${status.behind} ↑${status.ahead}`}</small>`;
      const sections = [
        ["已暂存的更改", changes.filter(item => item.staged), true],
        ["更改", changes.filter(item => item.unstaged || item.untracked), false],
      ];
      changesHost.innerHTML = sections.map(([title, items, staged]) => {
        const group = staged ? "staged" : "changes";
        const collapsed = collapsedGroups.has(group);
        return window.PortalI18n.html`<section class="articleGitChangeSection"><div class="articleGitSectionHead"><button class="articleGitGroupToggle" type="button" data-git-action="toggle-${group}" aria-expanded="${!collapsed}">${collapsed ? "›" : "⌄"} ${title}</button><small>${items.length}</small>${actionButton(staged ? "unstage-all" : "stage-all", staged ? "取消全部暂存" : "暂存全部更改", staged ? "minus" : "plus", busy || !items.length ? "disabled" : "")}</div><div ${collapsed ? "hidden" : ""}>${items.map(item => window.PortalI18n.html`
        <div class="articleGitChangeRow" title="${esc(item.path)}">
          <button class="articleGitChangePath" type="button" data-git-diff="${esc(item.path)}" data-git-diff-staged="${staged}"><span class="articleGitFileIcon" aria-hidden="true">${/\.tex$/i.test(item.path) ? "TᴇX" : /\.pdf$/i.test(item.path) ? "PDF" : "◇"}</span>${esc(item.path.split("/").at(-1) || item.path)}<small>${esc(item.path.includes("/") ? item.path.slice(0, item.path.lastIndexOf("/")) : "")}</small></button>
          <span class="articleGitStatusCode" data-code="${esc(item.untracked ? "U" : item.conflicted ? "!" : String(item.code || "MM")[staged ? 0 : 1])}" title="${esc(item.label || "")}">${esc(item.untracked ? "U" : item.conflicted ? "!" : String(item.code || "MM")[staged ? 0 : 1])}</span>
          <span class="articleGitRowActions">${actionButton("open", `打开 ${item.path}`, "file", `data-git-path="${esc(item.path)}"`)}${!staged && !item.conflicted ? actionButton("discard", `放弃 ${item.path} 的更改`, "undo", `data-git-path="${esc(item.path)}" ${busy ? "disabled" : ""}`) : ""}
          <button class="portalButton is-icon is-quiet iconBtn articleGitStageButton" type="button" data-git-stage="${esc(item.path)}" data-git-staged="${staged ? "false" : "true"}" aria-label="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")} ${esc(item.path)}" title="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")}" data-portal-icon="${staged ? "minus" : "plus"}"></button>
          </span>
        </div>`).join("")}</div></section>`;
      }).join("") || window.PortalI18n.html`<div class="portalAsyncState is-compact" data-state="empty" data-density="compact" role="status"><span class="portalAsyncStateIcon" aria-hidden="true"></span><span class="portalAsyncStateMessage">工作树干净，没有未提交的稿件改动。</span></div>`;
      renderHistory();
      changesHost.querySelectorAll("[data-git-stage]").forEach(button => { button.disabled = !!busy; });
      window.PortalIcons?.enhance?.($("articleGitPanel"));
    }

    const unregisteringArticles = new Set();
    let articleGitHistoryRequested = false;
    const articleGitRefresh = window.PortalRefreshCoordinator.create({
      key: () => state.selectedId,
      interval: ARTICLE_GIT_REFRESH_MS,
      initialDelay: ARTICLE_GIT_REFRESH_MS,
      shouldRefresh: () => !!state.selectedId && !unregisteringArticles.has(state.selectedId) && !busyArticles.has(state.selectedId),
      fetch: async ({ key, signal }) => {
        const id = encodeURIComponent(key);
        const includeHistory = articleGitHistoryRequested || state.sideView === "git";
        articleGitHistoryRequested = false;
        let historyError = null;
        const [status, history] = await Promise.all([
          request(`/api/articles/${id}/git`, { signal }),
          includeHistory ? request(`/api/articles/${id}/git/history?limit=${historyLimit}`, { signal }).catch(error => {
            historyError = error;
            return null;
          }) : Promise.resolve(null),
        ]);
        return { status, history, historyError };
      },
      apply: ({ status, history, historyError }) => {
        if (busyArticles.has(state.selectedId)) return;
        if (history) historyHasMore = !!history.has_more;
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

    function requestGitCredential(challenge, operation) {
      return new Promise(resolve => {
        const modal = document.createElement("div");
        modal.className = "portalDetailOverlay";
        modal.setAttribute("data-portal-detail-overlay", "");
        modal.innerHTML = window.PortalI18n.html`<form class="portalDetailSurface articleEntrySurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-label="Git 认证">
          <div class="portalDetailHeader" data-portal-detail-header><h2>Git 认证</h2></div>
          <div class="portalDetailBody" data-portal-detail-body>
            <p>${esc(challenge.host || "")} · ${esc(challenge.help_text || "请输入远端仓库的访问凭据。")}</p>
            <label class="portalField">用户名<input name="username" autocomplete="username" required value="${esc(challenge.username || "")}"></label>
            <label class="portalField">${esc(challenge.secret_label || "访问令牌或密码")}<input name="secret" type="password" autocomplete="current-password" required></label>
            <label><input name="remember" type="checkbox"> 为当前用户记住凭据</label>
          </div>
          <div class="portalDetailFooter" data-portal-detail-footer><button type="button" class="portalButton" data-cancel>取消</button><button type="submit" class="portalButton is-primary">${actionLabels[operation] || "继续"}</button></div>
        </form>`;
        document.body.append(modal);
        let value = null;
        const dialog = window.PortalUI.createDialogController({
          modal, openClass: "portalDialogOpen", bodyClass: null, backdropClose: true, escapeClose: true,
          onClose: () => {
            modal.querySelector('[name="secret"]').value = "";
            dialog.destroy?.();
            modal.remove();
            resolve(value);
          },
        });
        modal.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
        modal.querySelector("form").addEventListener("submit", event => {
          event.preventDefault();
          value = { credential: {
            username: modal.querySelector('[name="username"]').value.trim(),
            secret: modal.querySelector('[name="secret"]').value,
          }, remember: modal.querySelector('[name="remember"]').checked };
          dialog.close();
        });
        dialog.open({ focusTarget: modal.querySelector(challenge.username ? '[name="secret"]' : '[name="username"]') });
      });
    }

    async function runGitOperation(operation, payload = {}, { afterCommit = "", stageAll = false } = {}) {
      const articleId = state.selectedId;
      if (!articleId || busyArticles.has(articleId)) return;
      captureActiveDocument();
      if (["pull", "sync", "branch", "discard", "commit"].includes(operation) && hasDirtyDocuments()) {
        showToast(window.PortalI18n.t("请先保存编辑中的更改，再进行此操作。"), "error");
        return;
      }
      busyArticles.set(articleId, operation);
      renderArticleGit();
      let committed = false;
      let completed = false;
      const current = () => state.selectedId === articleId;
      async function post(action, data) {
        let credentials = {};
        while (current()) {
          try {
            return await request(`/api/articles/${encodeURIComponent(articleId)}/git/${action}`, {
              method: "POST", timeoutMs: 390000, headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ...data, ...credentials }),
            });
          } catch (error) {
            if (!current()) return null;
            if (!error?.data?.auth_challenge) throw error;
            credentials = await requestGitCredential(error.data.auth_challenge, action);
            if (!credentials) return null;
          }
        }
        return null;
      }
      try {
        if (stageAll) {
          const paths = changePaths(false);
          if (paths.length && !(await post("stage", { paths, staged: true }))) return;
        }
        let result = await post(operation, payload);
        if (!result || !current()) return;
        if (operation === "commit") {
          committed = true;
          if ($("articleGitMessage").value.trim() === payload.message) $("articleGitMessage").value = "";
          if (afterCommit) {
            state.gitStatus = result.status;
            busyArticles.set(articleId, afterCommit);
            renderArticleGit();
            result = await post(afterCommit, {});
            if (!result || !current()) return;
          }
        }
        completed = true;
        state.gitStatus = result.status || result;
        if (["pull", "sync", "branch", "discard"].includes(operation) || afterCommit === "sync") await refreshAfterPull(articleId);
        if (!current()) return;
        showToast(`${actionLabels[operation]}${afterCommit ? `并${actionLabels[afterCommit]}` : ""}完成${hasDirtyDocuments() ? "；编辑中的草稿已保留。" : "。"}`, "success");
      } catch (error) {
        if (current()) showToast(`${completed ? "操作已完成，但页面刷新失败：" : committed ? "本地提交已完成，远端操作未完成：" : ""}${error.message || error}`, "error");
      } finally {
        busyArticles.delete(articleId);
        if (current()) {
          await loadArticleGit({ includeHistory: true, silent: true });
          renderArticleGit();
        }
      }
    }

    function pullArticleGit() { return runGitOperation("pull"); }

    function changePaths(staged) {
      return [...new Set((state.gitStatus?.changes || []).filter(item => staged ? item.staged : item.unstaged || item.untracked)
        .flatMap(item => [item.path, staged ? item.original_path : ""].filter(Boolean)))];
    }

    async function updateArticleGitStage(path, staged) {
      const item = state.gitStatus?.changes?.find(change => change.path === path);
      return runGitOperation("stage", { paths: [path, !staged ? item?.original_path : ""].filter(Boolean), staged });
    }

    async function initializeArticleGit() {
      return runGitOperation("init");
    }

    async function commitArticleGit(event, options = {}) {
      event?.preventDefault();
      const message = $("articleGitMessage").value.trim();
      if (!message) { $("articleGitMessage").focus(); return; }
      return runGitOperation("commit", { message }, options);
    }

    function chooseBranch() {
      return new Promise(resolve => {
        const modal = document.createElement("div");
        modal.className = "portalDetailOverlay";
        modal.setAttribute("data-portal-detail-overlay", "");
        modal.innerHTML = `<form class="portalDetailSurface articleEntrySurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-label="切换或创建分支">
          <div class="portalDetailHeader" data-portal-detail-header><h2>切换或创建分支</h2></div>
          <div class="portalDetailBody" data-portal-detail-body><label class="portalField">已有分支<select name="branch">${(state.gitStatus?.branches || []).map(branch => `<option ${branch.current ? "selected" : ""} value="${esc(branch.name)}">${esc(branch.name)}</option>`).join("")}</select></label>
          <label class="portalField">或创建新分支<input name="newBranch" placeholder="从当前提交创建，例如 paper/revision"></label></div>
          <div class="portalDetailFooter" data-portal-detail-footer><button class="portalButton" type="button" data-cancel>取消</button><button class="portalButton is-primary" type="submit">切换</button></div></form>`;
        document.body.append(modal);
        let value = null;
        const dialog = window.PortalUI.createDialogController({ modal, openClass: "portalDialogOpen", bodyClass: null, backdropClose: true, escapeClose: true,
          onClose: () => { dialog.destroy?.(); modal.remove(); resolve(value); },
        });
        modal.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
        modal.querySelector("form").addEventListener("submit", event => {
          event.preventDefault();
          const newName = modal.querySelector('[name="newBranch"]').value.trim();
          value = { name: newName || modal.querySelector('[name="branch"]').value, create: !!newName };
          dialog.close();
        });
        dialog.open({ focusTarget: modal.querySelector("select") });
      });
    }

    async function handleArticleGitAction(button) {
      const action = button.dataset.gitAction;
      const articleId = state.selectedId;
      $("articleGitPanel").querySelectorAll("details[open]").forEach(menu => { menu.open = false; });
      if (action.startsWith("toggle-")) {
        const group = action.slice(7);
        collapsedGroups.has(group) ? collapsedGroups.delete(group) : collapsedGroups.add(group);
        renderArticleGit(); return;
      }
      if (action === "open") return openArticleFile(button.dataset.gitPath);
      if (action === "refresh" || action === "more-history") {
        if (action === "more-history") historyLimit = Math.min(500, historyLimit + 40);
        return loadArticleGit({ includeHistory: true });
      }
      if (busyArticles.has(articleId)) return;
      if (["fetch", "push", "sync"].includes(action)) return runGitOperation(action);
      if (action === "stage-all" || action === "unstage-all") return runGitOperation("stage", { paths: changePaths(action === "unstage-all"), staged: action === "stage-all" });
      if (action.startsWith("commit-")) return commitArticleGit(null, { stageAll: action === "commit-all", afterCommit: action === "commit-all" ? "" : action.slice(7) });
      if (action === "discard") {
        const path = button.dataset.gitPath;
        const approved = await window.PortalUI.confirm({ title: "放弃文件更改", message: `放弃 ${path} 的未暂存更改？`, description: "已跟踪文件恢复到暂存区版本；未跟踪文件会被删除。此操作不可撤销。", confirmLabel: "放弃更改", tone: "danger" });
        if (approved && state.selectedId === articleId) return runGitOperation("discard", { paths: [path] });
      }
      if (action === "branch") {
        const choice = await chooseBranch();
        if (choice && state.selectedId === articleId) return runGitOperation("branch", choice);
      }
    }

    // A newly selected Article starts without the previous one's Git state.
    function resetArticleGit() {
      articleGitRefresh.stop();
      articleGitHistoryRequested = false;
      historyLimit = 40;
      historyHasMore = false;
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

    async function openArticleGitDiff(path, staged = false) {
      const articleId = state.selectedId;
      const payload = await request(`/api/articles/${encodeURIComponent(articleId)}/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`);
      if (state.selectedId !== articleId) return;
      state.gitDiffPath = path;
      renderArticleGitDiff(`${path} · ${staged ? "已暂存的更改" : "未暂存的更改"}`, String(payload.diff || ""), payload.truncated ? "差异过大，已截断。" : "该文件当前没有可显示的差异。", { fallbackPath: path, scope: payload.scope || "" });
    }

    async function openArticleGitCommit(commitSha, shortSha, subject) {
      const articleId = state.selectedId;
      const payload = await request(`/api/articles/${encodeURIComponent(articleId)}/git/diff?commit=${encodeURIComponent(commitSha)}`);
      if (state.selectedId !== articleId) return;
      const commit = payload.commit || {};
      const label = commit.short_sha || shortSha || commitSha.slice(0, 7);
      const message = commit.subject || subject || window.PortalI18n.t("(无提交信息)");
      state.gitDiffPath = `commit:${commit.sha || commitSha}`;
      renderArticleGitDiff(`${label} · ${message}`, String(payload.diff || ""), "该提交在当前 Article 目录中没有可显示的更改。", { scope: payload.scope || "" });
    }

    return Object.freeze({
      articleGitRefresh, closeArticleGitDiff, commitArticleGit, handleArticleGitAction, initializeArticleGit, loadArticleGit,
      openArticleGitCommit, openArticleGitDiff, pullArticleGit, renderArticleGitDiffFile, resetArticleGit,
      scheduleArticleGitRefresh, toggleArticleGitDiffLayout, unregisteringArticles, updateArticleGitStage,
    });
  }

  window.PortalArticleWorkspaceGit = Object.freeze({ createGit, graphRows, dependencies: DEPENDENCIES });
})();
