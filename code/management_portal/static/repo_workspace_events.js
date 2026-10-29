(() => {
  "use strict";

  if (window.PortalRepoWorkspaceEvents) return;

  // The page's event wiring, bound when the part is created: delegated clicks,
  // changes, inputs, keys and submits in the workspace; the board's search box,
  // Project filter, reload, mode and bulk commit buttons; the dialogs' buttons;
  // the keyboard shortcuts and browser history navigation.
  const DEPENDENCIES = Object.freeze([
    "applyBranchFilter", "applyChangeFilters", "applyCommitListFilter", "bindProjectFilter",
    "cancelRepoQuery", "closeBranchDialog", "closeCredentialDialog", "closeEditor", "closeRegisterModal",
    "copyRepoText", "createBranch", "deleteFile", "deleteGitCredential", "dialogs", "discardRepoChanges",
    "elements", "filterCommitCode", "filterCommitFiles", "hideAutomationOverlay", "invalidateRevisionViews",
    "jumpToFileLine", "loadChangeDiff", "loadCommitList", "loadRepos", "loadWorkspace",
    "moveFileFinderSelection", "openBranchDialog", "openCommit", "openDirectory", "openEditor", "openFile",
    "openFileBlame", "openFileHistory", "openRegisterModal", "openRepoWorkspace", "postJson", "queryState",
    "refreshOverview", "registerRepo", "renderAutomationDialog", "renderRepoBoard", "renderWorkspace",
    "saveEditor", "savePushPolicy", "saveRemote", "saveSshKey", "searchFiles", "selectTab", "setEditorMode",
    "setFilterOpen", "showToast", "startAllRepoAutomation", "startRepoAutomation", "state", "submitCommit",
    "submitGitCredential", "switchBranch", "switchRepoMode", "syncRegisterPlacement", "syncRepo",
    "unregisterRepo", "updateAutomationControls", "workspaceCapability", "writeQuery",
  ]);

  function createEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceEvents is missing: ${missing.join(", ")}`);
    const {
      applyBranchFilter, applyChangeFilters, applyCommitListFilter, bindProjectFilter, cancelRepoQuery,
      closeBranchDialog, closeCredentialDialog, closeEditor, closeRegisterModal, copyRepoText, createBranch,
      deleteFile, deleteGitCredential, dialogs, discardRepoChanges, elements, filterCommitCode,
      filterCommitFiles, hideAutomationOverlay, invalidateRevisionViews, jumpToFileLine, loadChangeDiff,
      loadCommitList, loadRepos, loadWorkspace, moveFileFinderSelection, openBranchDialog, openCommit,
      openDirectory, openEditor, openFile, openFileBlame, openFileHistory, openRegisterModal,
      openRepoWorkspace, postJson, queryState, refreshOverview, registerRepo, renderAutomationDialog,
      renderRepoBoard, renderWorkspace, saveEditor, savePushPolicy, saveRemote, saveSshKey, searchFiles,
      selectTab, setEditorMode, setFilterOpen, showToast, startAllRepoAutomation, startRepoAutomation, state,
      submitCommit, submitGitCredential, switchBranch, switchRepoMode, syncRegisterPlacement, syncRepo,
      unregisterRepo, updateAutomationControls, workspaceCapability, writeQuery,
    } = dependencies;

    elements.workspace.addEventListener("click", event => {
      const autoCommit = event.target.closest("[data-repo-auto-commit]");
      if (autoCommit) {
        startRepoAutomation(autoCommit.dataset.repoAutoCommit)
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const projectToggle = event.target.closest("[data-resource-project-toggle]");
      if (projectToggle) {
        const project = projectToggle.closest(".portalProjectGroup");
        const collapsed = project.classList.toggle("collapsed");
        projectToggle.setAttribute("aria-expanded", String(!collapsed));
        projectToggle.setAttribute("aria-label", `${collapsed ? window.PortalI18n.t("展开") : window.PortalI18n.t("折叠")} ${project.dataset.resourceProject || window.PortalI18n.t("Project")}`);
        return;
      }
      const repoCard = event.target.closest("[data-resource-open]");
      if (repoCard) {
        openRepoWorkspace(repoCard.dataset.resourceOpen)
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const directory = event.target.closest("[data-open-directory]");
      if (directory) {
        openDirectory(directory.dataset.openDirectory).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const file = event.target.closest("[data-open-file]");
      if (file) {
        openFile(file.dataset.openFile).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const tab = event.target.closest("[data-repo-tab]");
      if (tab) {
        const key = tab.dataset.repoTab;
        selectTab(key)
          .then(() => window.setTimeout(() => elements.workspace.querySelector(`[data-repo-tab="${CSS.escape(key)}"]`)?.focus(), 0))
          .catch(error => showToast(error.message || error, "error"));
        return;
      }
      const change = event.target.closest("[data-change-path]");
      if (change) {
        loadChangeDiff(change.dataset.changePath).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const changeFilter = event.target.closest("[data-change-filter]");
      if (changeFilter) {
        state.changeFilter = changeFilter.dataset.changeFilter || "all";
        applyChangeFilters();
        return;
      }
      const retryChange = event.target.closest("[data-retry-change]");
      if (retryChange) {
        loadChangeDiff(retryChange.dataset.retryChange).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const commit = event.target.closest("[data-open-commit]");
      if (commit) {
        openCommit(commit.dataset.openCommit).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const copyCommit = event.target.closest("[data-copy-commit]");
      if (copyCommit) {
        void copyRepoText(copyCommit.dataset.copyCommit, "Commit SHA");
        return;
      }
      const copyBranch = event.target.closest("[data-copy-branch]");
      if (copyBranch) {
        void copyRepoText(copyBranch.dataset.copyBranch, "分支名");
        return;
      }
      const copyFilePath = event.target.closest("[data-copy-file-path]");
      if (copyFilePath) {
        void copyRepoText(copyFilePath.dataset.copyFilePath, "文件路径");
        return;
      }
      const copyFileContent = event.target.closest("[data-copy-file-content]");
      if (copyFileContent) {
        void copyRepoText(String(state.file?.content || ""), "文件内容");
        return;
      }
      const copyRemote = event.target.closest("[data-copy-remote]");
      if (copyRemote) {
        void copyRepoText(copyRemote.dataset.copyRemote, "Remote URL");
        return;
      }
      const copyCheckout = event.target.closest("[data-copy-checkout]");
      if (copyCheckout) {
        void copyRepoText(copyCheckout.dataset.copyCheckout, "Checkout 路径");
        return;
      }
      const commitFile = event.target.closest("[data-commit-file-target]");
      if (commitFile) {
        elements.workspace.querySelector(`#${commitFile.dataset.commitFileTarget}`)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const diffMode = event.target.closest("[data-commit-diff-mode]");
      if (diffMode) {
        state.commitDiffMode = diffMode.dataset.commitDiffMode === "unified" ? "unified" : "split";
        renderWorkspace();
        return;
      }
      const edit = event.target.closest("[data-edit-file]");
      if (edit) {
        openEditor(state.file);
        return;
      }
      const history = event.target.closest("[data-file-history]");
      if (history) {
        openFileHistory(history.dataset.fileHistory).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const blame = event.target.closest("[data-file-blame]");
      if (blame) {
        openFileBlame(blame.dataset.fileBlame).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const remove = event.target.closest("[data-delete-file]");
      if (remove) {
        deleteFile(remove.dataset.deleteFile, remove).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const branch = event.target.closest("[data-switch-branch]");
      if (branch) {
        switchBranch(branch.dataset.switchBranch, branch).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const sync = event.target.closest("[data-repo-sync]");
      if (sync) {
        syncRepo(sync.dataset.repoSync, sync).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const discard = event.target.closest("[data-repo-discard]");
      if (discard) {
        discardRepoChanges(discard).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const deleteCredential = event.target.closest("[data-delete-git-credential]");
      if (deleteCredential) {
        deleteGitCredential(
          deleteCredential.dataset.deleteGitCredential,
          deleteCredential,
        ).catch(error => showToast(error.message || error, "error"));
        return;
      }
      const fileView = event.target.closest("[data-file-view]");
      if (fileView) {
        state.fileView = fileView.dataset.fileView;
        renderWorkspace();
        return;
      }
      const action = event.target.closest("[data-repo-action]");
      if (!action) return;
      const name = action.dataset.repoAction;
      if (name === "register") openRegisterModal();
      else if (name === "reload") loadWorkspace();
      else if (name === "reload-tab") {
        if (state.activeTab === "commits") {
          loadCommitList().catch(error => showToast(error.message || error, "error"));
        } else {
          selectTab(state.activeTab).catch(error => showToast(error.message || error, "error"));
        }
      } else if (name === "load-more-commits") {
        loadCommitList({ append: true }).catch(error => showToast(error.message || error, "error"));
      } else if (name === "view-all-commits") {
        state.commitHistoryPath = "";
        state.commitListQuery = "";
        state.commitDetail = null;
        loadCommitList({ path: "" }).catch(error => showToast(error.message || error, "error"));
      } else if (name === "refresh-workspace") {
        refreshOverview()
          .then(() => {
            invalidateRevisionViews();
            showToast(window.PortalI18n.t("Repo 状态已刷新。"), "success");
          })
          .catch(error => showToast(error.message || error, "error"));
      } else if (name === "copy-id") {
        void copyRepoText(state.selectedId, "Repo ID");
      } else if (name === "new-file") openEditor();
      else if (name === "new-branch") openBranchDialog();
      else if (name === "unregister") unregisterRepo();
      else if (name === "file-finder") {
        state.fileFinderOpen = !state.fileFinderOpen;
        state.fileSearch = null;
        state.fileSearchQuery = "";
        state.fileSearchIndex = 0;
        renderWorkspace();
      } else if (name === "shortcuts") {
        dialogs.shortcuts.open({ trigger: action });
      } else if (name === "close-shortcuts") {
        dialogs.shortcuts.close();
      } else if (name === "close-commit") {
        state.commitDetail = null;
        renderWorkspace();
      } else if (name === "browse-files") {
        state.activeTab = "code";
        state.commitDetail = null;
        state.file = null;
        state.tree = null;
        openDirectory("").catch(error => showToast(error.message || error, "error"));
      }
    });

    elements.workspace.addEventListener("change", event => {
      if (event.target.id === "repoBranchPicker") {
        switchBranch(event.target.value, event.target)
          .catch(error => showToast(error.message || error, "error"));
      } else if (event.target.id === "repoCommitRevisionPicker") {
        state.commitRevision = event.target.value || "HEAD";
        state.commitDetail = null;
        loadCommitList({ revision: state.commitRevision })
          .catch(error => showToast(error.message || error, "error"));
      }
    });

    elements.workspace.addEventListener("input", event => {
      if (event.target.id === "repoFileFinderInput") {
        window.clearTimeout(state.fileSearchTimer);
        const value = event.target.value;
        state.fileSearchQuery = value;
        state.fileSearchTimer = window.setTimeout(() => searchFiles(value), 180);
      } else if (event.target.id === "repoChangesFilter") {
        state.changeListQuery = event.target.value;
        applyChangeFilters();
      } else if (event.target.id === "repoCommitListFilter") {
        state.commitListQuery = event.target.value;
        applyCommitListFilter();
      } else if (event.target.id === "repoBranchFilter") {
        state.branchListQuery = event.target.value;
        applyBranchFilter();
      } else if (event.target.id === "repoCommitFileFilter") {
        filterCommitFiles(event.target.value);
      } else if (event.target.id === "repoCommitCodeSearch") {
        filterCommitCode(event.target.value);
      } else if (event.target.matches('#repoCommitForm input[name="message"]')) {
        state.commitDraft.message = event.target.value;
      } else if (event.target.matches('#repoCommitForm textarea[name="description"]')) {
        state.commitDraft.description = event.target.value;
      }
    });

    elements.workspace.addEventListener("keydown", event => {
      const finder = event.target.closest("#repoFileFinderInput");
      if (finder) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          moveFileFinderSelection(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter") {
          const active = elements.workspace.querySelector(`[data-file-finder-index="${state.fileSearchIndex}"]`);
          if (active) {
            event.preventDefault();
            active.click();
          }
        }
        return;
      }
      const tab = event.target.closest(".repoTab");
      if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        const tabs = [...elements.workspace.querySelectorAll(".repoTab")];
        const current = tabs.indexOf(tab);
        const index = event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        event.preventDefault();
        tabs[index]?.click();
        return;
      }
      if (
        event.target.closest("#repoCommitForm")
        && event.key === "Enter"
        && (event.ctrlKey || event.metaKey)
      ) {
        event.preventDefault();
        event.target.closest("#repoCommitForm")?.requestSubmit();
      }
    });

    elements.workspace.addEventListener("submit", event => {
      if (event.target.id === "repoCommitForm") {
        event.preventDefault();
        submitCommit(event.target).catch(error => showToast(error.message || error, "error"));
      } else if (event.target.id === "repoRemoteForm") {
        event.preventDefault();
        saveRemote(event.target).catch(error => showToast(error.message || error, "error"));
      } else if (event.target.id === "repoPushPolicyForm") {
        event.preventDefault();
        savePushPolicy(event.target).catch(error => showToast(error.message || error, "error"));
      } else if (event.target.id === "repoSshKeyForm") {
        event.preventDefault();
        saveSshKey(event.target).catch(error => showToast(error.message || error, "error"));
      }
    });

    elements.query.addEventListener("input", () => {
      window.clearTimeout(state.queryTimer);
      cancelRepoQuery();
      updateAutomationControls();
      const value = elements.query.value.trim();
      state.mode = "board";
      writeQuery({
        q: value,
        semantic: value ? "" : queryState().semantic,
        item: "",
        tab: "",
        path: "",
        file: "",
      });
      if (value && state.semanticSearch?.activeId()) state.semanticSearch.clearActive();
      state.queryTimer = window.setTimeout(() => loadRepos(), 220);
    });
    elements.query.addEventListener("keydown", event => {
      if (event.key !== "Escape" || !elements.query.value) return;
      elements.query.value = "";
      state.mode = "board";
      writeQuery({ q: "", item: "" });
      loadRepos();
    });

    bindProjectFilter();
    elements.projectOptions.addEventListener("change", event => {
      if (event.target.name !== "resourceProject") return;
      state.mode = "board";
      writeQuery({ project: event.target.value, item: "", tab: "", path: "", file: "" });
      setFilterOpen(false);
      loadRepos({ keepWorkspace: false });
    });
    elements.reload.addEventListener("click", async () => {
      elements.reload.disabled = true;
      try {
        await postJson("/api/resource-index/refresh", {
          adapters: ["resource_registry"],
          wait: true,
          timeout: 45,
        });
        await loadRepos({ keepWorkspace: false });
        showToast(window.PortalI18n.t("Repo 资源与 checkout 状态已刷新。"), "success");
      } catch (error) {
        showToast(window.PortalI18n.text`刷新失败：${error.message || error}`, "error");
      } finally {
        elements.reload.disabled = false;
      }
    });
    elements.modeTabs.forEach(tab => {
      tab.addEventListener("click", () => switchRepoMode(tab.dataset.repoMode));
    });
    elements.autoCommitAll.addEventListener("click", () => {
      startAllRepoAutomation().catch(error => showToast(error.message || error, "error"));
    });

    elements.registerOpen.addEventListener("click", openRegisterModal);
    elements.registerClose.addEventListener("click", closeRegisterModal);
    elements.registerCancel.addEventListener("click", closeRegisterModal);
    elements.registerForm.addEventListener("submit", registerRepo);
    elements.registerForm.querySelector('[name="placement"]').addEventListener("change", syncRegisterPlacement);

    elements.editorClose.addEventListener("click", closeEditor);
    elements.editorCancel.addEventListener("click", closeEditor);
    elements.editorSave.addEventListener("click", saveEditor);
    elements.editorTabs.forEach(tab => {
      tab.addEventListener("click", () => setEditorMode(tab.dataset.editorMode));
    });
    elements.editorPath.addEventListener("input", () => {
      elements.editorTitle.textContent = elements.editorPath.value.split("/").at(-1) || window.PortalI18n.t("New file");
      if (state.editorMode === "preview") setEditorMode("preview");
    });
    elements.editorSource.addEventListener("input", () => {
      if (state.editorMode === "preview") setEditorMode("preview");
    });

    elements.branchClose.addEventListener("click", closeBranchDialog);
    elements.branchCancel.addEventListener("click", closeBranchDialog);
    elements.branchForm.addEventListener("submit", createBranch);
    elements.automationClose.addEventListener("click", hideAutomationOverlay);
    elements.automationDone.addEventListener("click", hideAutomationOverlay);
    elements.automationStop.addEventListener("click", () => {
      state.automationCancelRequested = true;
      renderAutomationDialog();
    });
    elements.credentialForm.addEventListener("submit", submitGitCredential);
    elements.credentialClose.addEventListener("click", () => closeCredentialDialog(null));
    elements.credentialCancel.addEventListener("click", () => closeCredentialDialog(null));
    elements.shortcutClose.addEventListener("click", () => dialogs.shortcuts.close());
    window.addEventListener("keydown", event => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
        || document.activeElement?.isContentEditable;
      const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
      if (typing || !plain) return;
      if (event.key === "?" && state.mode === "workspace" && workspaceCapability("git")) {
        event.preventDefault();
        if (elements.shortcutOverlay.hidden) dialogs.shortcuts.open();
        else dialogs.shortcuts.close();
        return;
      }
      if (event.key === "T" && state.mode === "workspace" && state.activeTab === "changes") {
        event.preventDefault();
        elements.workspace.querySelector("#repoChangesFilter")?.focus();
        return;
      }
      if (event.key === "t" && state.mode === "workspace" && workspaceCapability("search_files")) {
        event.preventDefault();
        state.fileFinderOpen = true;
        state.fileSearch = null;
        state.fileSearchQuery = "";
        state.fileSearchIndex = 0;
        renderWorkspace();
        return;
      }
      if (event.key === "w" && state.mode === "workspace" && workspaceCapability("git")) {
        event.preventDefault();
        const focusBranch = () => {
          const picker = elements.workspace.querySelector("#repoBranchPicker");
          if (picker?.disabled) showToast(window.PortalI18n.t("存在未提交改动，当前不能切换分支。"));
          else picker?.focus();
        };
        if (state.activeTab === "code") focusBranch();
        else selectTab("code").then(() => window.setTimeout(focusBranch, 0));
        return;
      }
      if (event.key === "b" && state.activeTab === "code" && state.file && workspaceCapability("git")) {
        event.preventDefault();
        openFileBlame(state.file.path).catch(error => showToast(error.message || error, "error"));
        return;
      }
      if (event.key === "l" && state.activeTab === "code" && state.file) {
        event.preventDefault();
        jumpToFileLine().catch(error => showToast(error.message || error, "error"));
      }
    });

    window.addEventListener("popstate", () => {
      const current = queryState();
      if (!current.item) {
        state.mode = "board";
        renderRepoBoard();
        return;
      }
      state.mode = "workspace";
      state.selectedId = current.item;
      loadWorkspace({ path: current.path, file: current.file, tab: current.tab });
    });
  }

  window.PortalRepoWorkspaceEvents = Object.freeze({ createEvents, dependencies: DEPENDENCIES });
})();
