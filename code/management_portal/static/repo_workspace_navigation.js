(() => {
  "use strict";

  if (window.PortalRepoWorkspaceNavigation) return;

  // Moving through one Repo: loading its workspace, opening directories and
  // files, switching tabs and loading their data (commits, credentials, a
  // change's diff), one commit, a file's history and blame, jumping to a line
  // and searching files. A reply for a Repo the user has left is dropped.
  const DEPENDENCIES = Object.freeze([
    "detailResourceGuest", "dirname", "elements", "esc", "formatDateTime", "getJson", "isMarkdown",
    "queryState", "renderRepoBoard", "renderWorkspace", "repoStateHtml", "showToast", "state", "uiIcon",
    "updateModeControls", "workspaceCapability", "writeQuery",
  ]);

  function createNavigation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceNavigation is missing: ${missing.join(", ")}`);
    const {
      detailResourceGuest, dirname, elements, esc, formatDateTime, getJson, isMarkdown, queryState,
      renderRepoBoard, renderWorkspace, repoStateHtml, showToast, state, uiIcon, updateModeControls,
      workspaceCapability, writeQuery,
    } = dependencies;

    async function loadWorkspace({ path, file, tab } = {}) {
      if (!state.selectedId) {
        state.mode = "board";
        renderRepoBoard();
        return;
      }
      const changedRepo = Boolean(state.overview?.id && state.overview.id !== state.selectedId);
      state.mode = "workspace";
      updateModeControls();
      const sequence = ++state.workspaceSequence;
      const current = queryState();
      state.activeTab = ["code", "changes", "commits", "branches", "settings"].includes(tab || current.tab)
        ? (tab || current.tab)
        : "code";
      state.file = null;
      state.tree = null;
      state.statistics = null;
      state.statisticsError = "";
      state.commits = null;
      state.commitDetail = null;
      state.commitHistoryPath = "";
      state.commitRevision = "HEAD";
      state.commitListQuery = "";
      state.commitListLoading = false;
      state.commitListError = "";
      state.changeDiff = null;
      state.changeDiffLoading = false;
      state.changeDiffError = "";
      state.selectedChange = "";
      state.credentials = null;
      if (changedRepo) {
        state.commitDraft = { message: "", description: "" };
        state.changeFilter = "all";
        state.changeListQuery = "";
        state.branchListQuery = "";
      }
      elements.workspace.innerHTML = repoStateHtml("正在读取目录。", { state: "loading" });
      const repoId = state.selectedId;
      const currentWorkspace = () => sequence === state.workspaceSequence && repoId === state.selectedId;
      try {
        const selectedFile = file == null ? current.file : file;
        const selectedPath = path == null ? current.path : path;
        const base = `/api/repos/${encodeURIComponent(repoId)}/workspace`;
        const overview = await getJson(base);
        if (!currentWorkspace()) return;
        state.overview = overview;
        if (!workspaceCapability("git") && state.activeTab !== "code") {
          state.activeTab = "code";
          writeQuery({ tab: "code" });
        }
        let detailRequest = Promise.resolve(null);
        if (state.activeTab === "code") {
          detailRequest = selectedFile
            ? getJson(`${base}/file?path=${encodeURIComponent(selectedFile)}`)
            : getJson(`${base}/tree?path=${encodeURIComponent(selectedPath || "")}`);
        } else if (state.activeTab === "commits") {
          detailRequest = getJson(`${base}/commits?limit=60`);
        } else if (state.activeTab === "settings") {
          detailRequest = getJson(`${base}/credentials`).catch(error => ({
            credentials: [], remotes: [], error: error.message || String(error),
          }));
        }
        const detail = await detailRequest;
        if (!currentWorkspace()) return;
        state.overview = overview;
        if (state.activeTab === "code") {
          if (selectedFile) state.file = detail;
          else state.tree = detail;
        } else if (state.activeTab === "commits") {
          state.commits = detail;
        } else if (state.activeTab === "settings") {
          state.credentials = detail;
          if (detail?.error) showToast(detail.error, "error");
        } else if (state.activeTab === "changes") {
          state.selectedChange = String(overview.status?.changes?.[0]?.path || "");
        }
        renderWorkspace();
        detailResourceGuest.setResource({ resourceId: state.selectedId });
        detailResourceGuest.ready();
        if (state.activeTab === "changes" && state.selectedChange) {
          void loadChangeDiff(state.selectedChange);
        }
      } catch (error) {
        if (sequence !== state.workspaceSequence) return;
        detailResourceGuest.failed({ resourceId: state.selectedId, error: error.message || String(error) });
        elements.workspace.innerHTML = repoStateHtml(error.message || error, {
          state: "error",
          title: window.PortalI18n.t("无法打开 Repo 工作台"),
          actionsHtml: window.PortalI18n.markup('<button class="portalButton" type="button" data-repo-action="reload">重试</button>'),
        });
      }
    }

    async function openDirectory(path, { historyMode = "push" } = {}) {
      const sequence = ++state.navigationSequence;
      const repoId = state.selectedId;
      state.file = null;
      state.fileView = "code";
      writeQuery({ path, file: "", tab: "code" }, { historyMode });
      state.tree = null;
      renderWorkspace();
      try {
        const tree = await getJson(
          `/api/repos/${encodeURIComponent(repoId)}/workspace/tree?path=${encodeURIComponent(path || "")}`,
        );
        if (sequence !== state.navigationSequence || repoId !== state.selectedId) return;
        state.tree = tree;
        renderWorkspace();
      } catch (error) {
        if (sequence !== state.navigationSequence || repoId !== state.selectedId) return;
        showToast(error.message || error, "error");
        await loadWorkspace();
      }
    }

    async function openFile(path, { historyMode = "push" } = {}) {
      const sequence = ++state.navigationSequence;
      const repoId = state.selectedId;
      state.fileFinderOpen = false;
      state.fileSearch = null;
      state.fileSearchQuery = "";
      state.tree = null;
      state.file = null;
      state.fileView = isMarkdown(path) ? "preview" : "code";
      writeQuery({ file: path, path: dirname(path), tab: "code" }, { historyMode });
      renderWorkspace();
      try {
        const file = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/file?path=${encodeURIComponent(path)}`,
        );
        // A superseded read (another navigation or a Discard) must not show its content.
        if (sequence !== state.navigationSequence || repoId !== state.selectedId) return;
        state.file = file;
        renderWorkspace();
      } catch (error) {
        if (sequence !== state.navigationSequence || repoId !== state.selectedId) return;
        showToast(error.message || error, "error");
        await openDirectory(dirname(path), { historyMode: "replace" });
      }
    }

    function invalidateRevisionViews() {
      state.commits = null;
      state.commitDetail = null;
      state.commitListError = "";
      state.commitListLoading = false;
    }

    async function loadCommitList({
      append = false,
      path = state.commitHistoryPath,
      revision = state.commitRevision,
    } = {}) {
      if (state.commitListLoading) return;
      const repoId = state.selectedId;
      const workspaceSequence = state.workspaceSequence;
      const sequence = ++state.commitListSequence;
      const current = () => repoId === state.selectedId && workspaceSequence === state.workspaceSequence && sequence === state.commitListSequence;
      state.commitListLoading = true;
      state.commitListError = "";
      if (!append) state.commits = null;
      if (state.activeTab === "commits") renderWorkspace();
      const existing = append ? (state.commits?.commits || []) : [];
      const offset = append ? existing.length : 0;
      try {
        const payload = await getJson(
          `/api/repos/${encodeURIComponent(repoId)}/workspace/commits?limit=60&offset=${offset}&revision=${encodeURIComponent(revision || "HEAD")}${path ? `&path=${encodeURIComponent(path)}` : ""}`,
        );
        if (!current()) return;
        const seen = new Set(existing.map(commit => commit.sha));
        const next = (payload.commits || []).filter(commit => !seen.has(commit.sha));
        state.commits = {
          ...payload,
          commits: [...existing, ...next],
        };
        state.commitHistoryPath = String(payload.path ?? path ?? "");
        state.commitRevision = String(payload.revision || revision || "HEAD");
      } catch (error) {
        if (!current()) return;
        state.commitListError = String(error?.message || error);
        if (append && existing.length) {
          state.commits = { ...(state.commits || {}), commits: existing };
        }
      } finally {
        if (!current()) return;
        state.commitListLoading = false;
        if (state.activeTab === "commits") renderWorkspace();
      }
    }

    async function selectTab(tab, { historyMode = "push" } = {}) {
      if (!["code", "changes", "commits", "branches", "settings"].includes(tab)) return;
      if (tab !== "code" && !workspaceCapability("git")) return;
      const previousTab = state.activeTab;
      state.activeTab = tab;
      writeQuery(
        { tab, file: tab === "code" ? queryState().file : "" },
        { historyMode },
      );
      if (tab === "code" && !state.file && !state.tree) {
        const current = queryState();
        if (current.file) {
          await openFile(current.file, { historyMode: "replace" });
        } else {
          await openDirectory(current.path || "", { historyMode: "replace" });
        }
        return;
      }
      if (tab === "commits") {
        if (previousTab !== "commits") {
          state.commitHistoryPath = "";
          state.commitRevision = "HEAD";
          state.commitListQuery = "";
          invalidateRevisionViews();
        }
        if (!state.commits) {
          await loadCommitList();
          return;
        }
      }
      if (tab === "changes") {
        const changes = state.overview.status?.changes || [];
        const selectedStillExists = changes.some(change => change.path === state.selectedChange);
        state.selectedChange = selectedStillExists
          ? state.selectedChange
          : String(changes[0]?.path || "");
        state.changeDiff = null;
        renderWorkspace();
        if (state.selectedChange) await loadChangeDiff(state.selectedChange);
        return;
      }
      if (tab === "settings") {
        state.credentials = null;
        renderWorkspace();
        await loadCredentials();
        return;
      }
      renderWorkspace();
    }

    async function loadCredentials() {
      const repoId = state.selectedId;
      const workspaceSequence = state.workspaceSequence;
      const sequence = ++state.credentialsSequence;
      const current = () => repoId === state.selectedId && workspaceSequence === state.workspaceSequence && sequence === state.credentialsSequence;
      try {
        const credentials = await getJson(`/api/repos/${encodeURIComponent(repoId)}/workspace/credentials`);
        if (!current()) return;
        state.credentials = credentials;
      } catch (error) {
        if (!current()) return;
        state.credentials = {credentials: [], remotes: [], error: error.message || String(error)};
        showToast(error.message || error, "error");
      }
      if (state.activeTab === "settings") renderWorkspace();
    }

    async function refreshOverview({ keepTab = true } = {}) {
      const repoId = state.selectedId;
      const workspaceSequence = state.workspaceSequence;
      const sequence = ++state.overviewSequence;
      const current = () => repoId === state.selectedId && workspaceSequence === state.workspaceSequence && sequence === state.overviewSequence;
      const overview = await getJson(`/api/repos/${encodeURIComponent(repoId)}/workspace`);
      if (!current()) return;
      state.overview = overview;
      if (!keepTab) state.activeTab = "code";
      if (state.activeTab === "code") {
        const filePath = state.file?.path;
        const treePath = state.tree?.path || queryState().path;
        const payload = await getJson(filePath
          ? `/api/repos/${encodeURIComponent(repoId)}/workspace/file?path=${encodeURIComponent(filePath)}`
          : `/api/repos/${encodeURIComponent(repoId)}/workspace/tree?path=${encodeURIComponent(treePath || "")}`);
        if (!current() || state.activeTab !== "code" || state.file?.path !== filePath) return;
        if (filePath) state.file = payload;
        else state.tree = payload;
      }
      renderWorkspace();
    }

    async function loadChangeDiff(path) {
      const sequence = ++state.changeDiffSequence;
      state.selectedChange = path;
      state.changeDiff = null;
      state.changeDiffLoading = true;
      state.changeDiffError = "";
      renderWorkspace();
      try {
        const payload = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/diff?path=${encodeURIComponent(path)}`,
        );
        if (sequence !== state.changeDiffSequence || path !== state.selectedChange) return;
        state.changeDiff = payload;
        state.changeDiffLoading = false;
        renderWorkspace();
      } catch (error) {
        if (sequence !== state.changeDiffSequence || path !== state.selectedChange) return;
        state.changeDiffLoading = false;
        state.changeDiffError = String(error?.message || error);
        renderWorkspace();
      }
    }

    async function openCommit(sha) {
      state.commitDetail = null;
      if (state.activeTab !== "commits") {
        state.activeTab = "commits";
        writeQuery({ tab: "commits", file: "" }, { historyMode: "push" });
        if (!state.commits) await loadCommitList();
      }
      renderWorkspace();
      try {
        state.commitDetail = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/commit?sha=${encodeURIComponent(sha)}`,
        );
        renderWorkspace();
      } catch (error) {
        showToast(error.message || error, "error");
      }
    }

    async function openFileHistory(path) {
      state.activeTab = "commits";
      state.commitDetail = null;
      state.commitHistoryPath = path;
      state.commitRevision = "HEAD";
      state.commitListQuery = "";
      state.commits = null;
      writeQuery({ tab: "commits", file: "" }, { historyMode: "push" });
      await loadCommitList({ path, revision: "HEAD" });
    }

    async function openFileBlame(path) {
      if (!workspaceCapability("git")) return;
      const panel = elements.workspace.querySelector(".repoFilePanel");
      if (panel) panel.innerHTML = repoStateHtml("正在生成 Blame。", {
        state: "loading",
        density: "compact",
      });
      try {
        const payload = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/blame?path=${encodeURIComponent(path)}`,
        );
        renderWorkspace();
        const currentPanel = elements.workspace.querySelector(".repoFilePanel");
        if (!currentPanel) return;
        currentPanel.innerHTML = window.PortalI18n.html`
        <header class="repoFileHeader">
          <div class="repoFileMeta">${uiIcon("history")}<strong>Blame · ${esc(path)}</strong><span>${payload.lines.length} lines</span></div>
          <button class="portalButton" type="button" data-open-file="${esc(path)}">返回文件</button>
        </header>
        <div class="repoBlameViewer">
          ${payload.lines.map(line => window.PortalI18n.html`
            <div class="repoBlameLine">
              <button type="button" data-open-commit="${esc(line.sha)}"><code>${esc(line.sha.slice(0, 7))}</code><strong>${esc(line.author || "Unknown")}</strong><time>${esc(formatDateTime(line.authored_at))}</time></button>
              <a href="#L${line.line}">${line.line}</a>
              <span>${esc(line.content) || " "}</span>
            </div>
          `).join("")}
        </div>`;
      } catch (error) {
        showToast(error.message || error, "error");
        renderWorkspace();
      }
    }

    async function jumpToFileLine() {
      if (!state.file || state.file.binary || state.file.kind === "symlink") return;
      const answer = await window.PortalUI.prompt({
        title: window.PortalI18n.text`跳转到 ${state.file.name}`,
        message: window.PortalI18n.text`输入 1–${Math.max(1, Number(state.file.line_count || 1))} 之间的行号。`,
        label: window.PortalI18n.t("行号"),
        value: "1",
        placeholder: window.PortalI18n.t("例如 42"),
        confirmLabel: "跳转",
      });
      if (answer == null) return;
      const line = Math.min(
        Math.max(1, Number.parseInt(String(answer), 10) || 1),
        Math.max(1, Math.min(5000, Number(state.file.line_count || 1))),
      );
      if (state.fileView !== "code") {
        state.fileView = "code";
        renderWorkspace();
      }
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        const target = elements.workspace.querySelector(`[data-preview-line="${line}"]`);
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
        target?.focus();
        if (target) window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#L${line}`);
      }));
    }

    async function searchFiles(query) {
      if (!state.fileFinderOpen) return;
      const clean = String(query || "").trim();
      state.fileSearchQuery = String(query || "");
      const sequence = ++state.fileSearchSequence;
      if (!clean) {
        state.fileSearch = null;
        state.fileSearchIndex = 0;
        renderWorkspace();
        return;
      }
      try {
        const payload = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/search?q=${encodeURIComponent(clean)}`,
        );
        if (sequence !== state.fileSearchSequence || clean !== state.fileSearchQuery.trim()) return;
        state.fileSearch = payload;
        state.fileSearchIndex = 0;
        renderWorkspace();
      } catch (error) {
        if (sequence !== state.fileSearchSequence) return;
        showToast(error.message || error, "error");
      }
    }

    return Object.freeze({
      invalidateRevisionViews, jumpToFileLine, loadChangeDiff, loadCommitList, loadWorkspace, openCommit,
      openDirectory, openFile, openFileBlame, openFileHistory, refreshOverview, searchFiles, selectTab,
    });
  }

  window.PortalRepoWorkspaceNavigation = Object.freeze({ createNavigation, dependencies: DEPENDENCIES });
})();
