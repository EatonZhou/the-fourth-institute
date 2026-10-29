(function () {
  "use strict";

  // The Repo page's parts live in module scripts that repo_workspace.html
  // loads before this one; repoModule(name) returns what a script registers:
  //   repo_workspace_catalog.js  querying the Repo records, descriptions and
  //     automation statuses behind the board, the semantic search scope
  //   repo_workspace_board.js  the board of Repo cards by Project, the Project
  //     filter, mode tabs and the bulk commit controls
  //   repo_workspace_shell.js  one Repo's workspace: selector, tabs, context
  //     actions, its Workbench, the file preview and the file finder popover
  //   repo_workspace_code_view.js  the Code tab: tree, README, About and
  //     statistics, a file's view, the file finder results
  //   repo_workspace_git_views.js  the Changes, Commits, Branches and Settings
  //     tabs, commit diffs, filtering their rendered lists
  //   repo_workspace_navigation.js  loading a Repo's workspace, directories,
  //     files and tab data, commits, file history and blame, file search
  //   repo_workspace_dialogs.js  registering a Repo, the file editor, new
  //     branch, the Git credential prompt
  //   repo_workspace_git_actions.js  branch switches, sync, discard, delete,
  //     commit, remote, push policy, SSH key, credentials, unregistering
  //   repo_workspace_automation.js  automatic commits of one or every Repo and
  //     their progress dialog
  //   repo_workspace_events.js  the page's event wiring, created last
  // This script holds the page's elements and state and the requests every
  // part shares, and creates the parts in that order; a part that calls a
  // function of a part created after it gets an arrow forwarding the call.
  function repoModule(name) {
    const module = window[name];
    if (!module) throw new Error(`${name} is not loaded.`);
    return module;
  }

  const repoStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
    state: options.state || "empty",
    title: options.title || "",
    message,
    density: options.density || "panel",
    icon: options.icon,
    className: options.className || "",
    actionLabel: options.actionLabel,
    actionsHtml: options.actionsHtml,
  });
  const uiIcon = name => window.PortalUIPrimitives?.iconHtml?.(name) || "";

  const elements = {
    workspace: document.getElementById("repoWorkspace"),
    count: document.getElementById("resourceCount"),
    query: document.getElementById("resourceQuery"),
    filterToggle: document.getElementById("resourceFilterToggle"),
    filterPanel: document.getElementById("resourceFilterPanel"),
    projectOptions: document.getElementById("resourceProjectOptions"),
    filterSummary: document.getElementById("resourceFilterSummary"),
    reload: document.getElementById("resourceReload"),
    autoCommitAll: document.getElementById("repoAutoCommitAll"),
    autoCommitCount: document.getElementById("repoAutoCommitCount"),
    boardMode: document.getElementById("repoBoardMode"),
    workspaceMode: document.getElementById("repoWorkspaceMode"),
    modeTabs: [...document.querySelectorAll("[data-repo-mode]")],
    semanticButton: document.getElementById("resourceSemanticButton"),
    semanticPanel: document.getElementById("resourceSemanticPanel"),
    semanticIdentity: document.getElementById("repoSemanticIdentity"),
    tabs: document.querySelector("[data-resource-tabs]"),
    registerOpen: document.getElementById("repoRegisterOpen"),
    registerModal: document.getElementById("repoRegisterModal"),
    registerForm: document.getElementById("repoRegisterForm"),
    registerPath: document.getElementById("repoRegisterPath"),
    registerClose: document.getElementById("repoRegisterClose"),
    registerCancel: document.getElementById("repoRegisterCancel"),
    editorOverlay: document.getElementById("repoEditorOverlay"),
    editorClose: document.getElementById("repoEditorClose"),
    editorCancel: document.getElementById("repoEditorCancel"),
    editorSave: document.getElementById("repoEditorSave"),
    editorEyebrow: document.getElementById("repoEditorEyebrow"),
    editorTitle: document.getElementById("repoEditorTitle"),
    editorPath: document.getElementById("repoEditorPath"),
    editorSource: document.getElementById("repoEditorSource"),
    editorPreview: document.getElementById("repoEditorPreview"),
    editorStatus: document.getElementById("repoEditorStatus"),
    editorTabs: [...document.querySelectorAll("[data-editor-mode]")],
    branchOverlay: document.getElementById("repoBranchOverlay"),
    branchForm: document.getElementById("repoBranchForm"),
    branchClose: document.getElementById("repoBranchClose"),
    branchCancel: document.getElementById("repoBranchCancel"),
    branchName: document.getElementById("repoBranchName"),
    branchStart: document.getElementById("repoBranchStart"),
    automationOverlay: document.getElementById("repoAutomationOverlay"),
    automationClose: document.getElementById("repoAutomationClose"),
    automationDone: document.getElementById("repoAutomationDone"),
    automationStop: document.getElementById("repoAutomationStop"),
    automationTitle: document.getElementById("repoAutomationTitle"),
    automationStatus: document.getElementById("repoAutomationStatus"),
    automationSub: document.getElementById("repoAutomationSub"),
    automationProgress: document.querySelector(".repoAutomationProgress"),
    automationProgressBar: document.getElementById("repoAutomationProgressBar"),
    automationResults: document.getElementById("repoAutomationResults"),
    credentialOverlay: document.getElementById("repoCredentialOverlay"),
    credentialForm: document.getElementById("repoCredentialForm"),
    credentialClose: document.getElementById("repoCredentialClose"),
    credentialCancel: document.getElementById("repoCredentialCancel"),
    credentialSubmit: document.getElementById("repoCredentialSubmit"),
    credentialTitle: document.getElementById("repoCredentialTitle"),
    credentialHost: document.getElementById("repoCredentialHost"),
    credentialPath: document.getElementById("repoCredentialPath"),
    credentialProtocol: document.getElementById("repoCredentialProtocol"),
    credentialReason: document.getElementById("repoCredentialReason"),
    credentialUsernameRow: document.getElementById("repoCredentialUsernameRow"),
    credentialUsername: document.getElementById("repoCredentialUsername"),
    credentialSecretLabel: document.getElementById("repoCredentialSecretLabel"),
    credentialSecret: document.getElementById("repoCredentialSecret"),
    credentialHelp: document.getElementById("repoCredentialHelp"),
    credentialRemember: document.getElementById("repoCredentialRemember"),
    credentialError: document.getElementById("repoCredentialError"),
    shortcutOverlay: document.getElementById("repoShortcutOverlay"),
    shortcutClose: document.getElementById("repoShortcutClose"),
  };

  const state = {
    records: [],
    visibleRecords: [],
    projects: new Set(),
    mode: queryState().item ? "workspace" : "board",
    selectedId: "",
    overview: null,
    statistics: null,
    statisticsLoadingKey: "",
    statisticsError: "",
    tree: null,
    file: null,
    fileView: "code",
    activeTab: "code",
    commits: null,
    commitDetail: null,
    commitDiffMode: "split",
    commitDraft: {
      message: "",
      description: "",
    },
    commitHistoryPath: "",
    commitRevision: "HEAD",
    commitListQuery: "",
    commitListLoading: false,
    commitListError: "",
    selectedChange: "",
    changeDiff: null,
    changeDiffLoading: false,
    changeDiffError: "",
    changeFilter: "all",
    changeListQuery: "",
    fileFinderOpen: false,
    fileSearch: null,
    fileSearchQuery: "",
    fileSearchIndex: 0,
    fileSearchTimer: 0,
    fileSearchSequence: 0,
    navigationSequence: 0,
    changeDiffSequence: 0,
    branchListQuery: "",
    queryTimer: 0,
    requestSequence: 0,
    requestController: null,
    workspaceSequence: 0,
    commitListSequence: 0,
    credentialsSequence: 0,
    overviewSequence: 0,
    editorOriginalPath: "",
    editorOriginalContent: "",
    editorExpectedHash: "",
    editorNew: false,
    editorMode: "edit",
    semanticSearch: null,
    automationStatuses: new Map(),
    automationNames: new Map(),
    automationStatusLoading: false,
    automationStatusLoaded: false,
    automationActive: false,
    automationCancelRequested: false,
    automationTargets: [],
    automationResults: [],
    automationCompleted: 0,
    automationCurrent: "",
    automationScope: "single",
    credentials: null,
    credentialChallenge: null,
    credentialResolver: null,
    credentialRejecter: null,
    credentialSubmitter: null,
    descriptionPending: new Set(),
    descriptionAttempted: new Set(),
    descriptionFailed: new Set(),
  };
  const detailHistory = window.PortalResourceDetail.createDetailHistory({
    owner: "resource-workspace:repo",
    parameter: "item",
  });
  const resourceServiceControls = () => document.querySelectorAll([
    ".resourceSectionFrame button",
    ".resourceSectionFrame input",
    ".resourceSectionFrame select",
    "#repoWorkspace button",
    "#repoWorkspace input",
    "#repoWorkspace select",
    "#repoWorkspace textarea",
    ".portalDetailOverlay button",
    ".portalDetailOverlay input",
    ".portalDetailOverlay select",
    ".portalDetailOverlay textarea",
  ].join(","));
  const recoveryLoadRepos = () => loadRepos({
    keepWorkspace: true,
    propagateServiceUnavailable: true,
  });
  const resourceServiceRecovery = window.PortalComponentRecovery.create({
    endpoint: "/resource/api/service-status",
    serviceName: "ResourceService",
    unavailableCodes: ["resource_service_unavailable"],
    surfaceName: "Repo Workspace",
    host: ".repoPage",
    id: "repoResourceServiceRecovery",
    controls: resourceServiceControls,
    onReady: recoveryLoadRepos,
  });

  const dialogs = Object.freeze({
    register: window.PortalUI.createDialogController({
      modal: elements.registerModal,
      initialFocus: elements.registerPath,
      backdropClose: true,
      escapeClose: true,
    }),
    editor: window.PortalUI.createDialogController({
      modal: elements.editorOverlay,
      backdropClose: true,
      escapeClose: true,
      dirty: context => (
        !context?.force
        && (
          elements.editorPath.value.trim() !== state.editorOriginalPath
          || elements.editorSource.value !== state.editorOriginalContent
        )
      ),
      discardMessage: "放弃尚未保存的路径或内容修改？",
      requestClose: (context, controller) => controller.close(context),
    }),
    branch: window.PortalUI.createDialogController({
      modal: elements.branchOverlay,
      initialFocus: elements.branchName,
      backdropClose: true,
      escapeClose: true,
    }),
    automation: window.PortalUI.createDialogController({
      modal: elements.automationOverlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => hideAutomationOverlay(),
    }),
    credential: window.PortalUI.createDialogController({
      modal: elements.credentialOverlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => closeCredentialDialog(null),
    }),
    shortcuts: window.PortalUI.createDialogController({
      modal: elements.shortcutOverlay,
      initialFocus: elements.shortcutClose,
      backdropClose: true,
      escapeClose: true,
    }),
  });
  const detailResourceGuest = window.PortalResourceDetail.createGuest({
    resourceId: () => state.selectedId || queryState().item,
    resourceType: "repo",
    detailKind: "repo",
    guardClose: () => {
      if (elements.editorOverlay.hidden) return true;
      const dirty = elements.editorPath.value.trim() !== state.editorOriginalPath
        || elements.editorSource.value !== state.editorOriginalContent;
      if (!dirty) return true;
      return window.PortalUI.confirmAction("close", "当前 Repo 文件编辑器", {
        consequence: "关闭会放弃尚未保存的路径或内容修改。",
      });
    },
  });

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const objectOf = window.PortalUIPrimitives.objectOf;

  function queryState() {
    const params = new URLSearchParams(window.location.search);
    return {
      q: String(params.get("q") || "").trim(),
      project: String(params.get("project") || "").trim(),
      item: String(params.get("item") || "").trim(),
      semantic: String(params.get("semantic") || "").trim(),
      tab: String(params.get("tab") || "code").trim(),
      path: String(params.get("path") || "").trim(),
      file: String(params.get("file") || "").trim(),
    };
  }

  function writeQuery(patch, { historyMode = "replace" } = {}) {
    const normalized = { ...patch };
    if (
      Object.hasOwn(normalized, "tab")
      && String(normalized.tab || "").trim() === "code"
    ) normalized.tab = "";
    return historyMode === "push"
      ? detailHistory.push(normalized)
      : detailHistory.replace(normalized);
  }

  const getJson = (url, options = {}) => resourceServiceRecovery.guard(() => window.PortalAPI.get(url, options));

  async function postJson(url, payload = {}, options = {}) {
    const result = await resourceServiceRecovery.guard(() => window.PortalAPI.post(url, payload, options));
    const selectedId = String(state.selectedId || "");
    if (selectedId && String(url).includes(encodeURIComponent(selectedId))) {
      detailResourceGuest.changed({ resourceId: selectedId, operation: String(url) });
    }
    return result;
  }

  // Registry changes of the selected Repo are HATG ISA instructions.
  async function repoInstruction(name, args) {
    const result = await resourceServiceRecovery.guard(() => window.PortalInstructionClient.execute(
      name,
      { resource_id: state.selectedId, ...args },
      { idempotencyKey: `repo:${name}:${globalThis.PortalUIPrimitives.randomUUID()}` },
    ));
    if (name !== "UnregisterResource") {
      detailResourceGuest.changed({ resourceId: String(state.selectedId || ""), operation: name });
    }
    return result;
  }

  function showToast(message, kind = "") {
    const tone = kind || "success";
    return window.PortalUI.toast(message, tone, {
      duration: 4200,
      key: `repo-feedback-${tone}`,
    });
  }

  function showSettingsSaved(result, message) {
    if (result?.index_projection?.status === "unconfirmed") {
      showToast(`${message} ${window.PortalI18n.t("搜索索引尚未确认更新，搜索结果可能暂时显示旧信息，无需重复保存。")}`, "warning");
      return;
    }
    showToast(message, "success");
  }

  async function copyRepoText(value, label) {
    if (await window.PortalUI.copyText(value, label)) {
      showToast(window.PortalI18n.text`${label} 已复制。`, "success");
    }
  }

  const formatDateTime = window.PortalUI.formatDateTime;

  function dirname(path) {
    const value = String(path || "");
    const index = value.lastIndexOf("/");
    return index < 0 ? "" : value.slice(0, index);
  }

  function joinPath(...values) {
    const output = [];
    values.join("/").split("/").forEach(part => {
      if (!part || part === ".") return;
      if (part === "..") output.pop();
      else output.push(part);
    });
    return output.join("/");
  }

  function isMarkdown(path, mediaType = "") {
    return window.PortalPreviewContract.normalizeKind({
      path,
      mediaType,
      binary: true,
    }) === "markdown";
  }

  function markdown(content, path) {
    return window.MD?.render
      ? window.MD.render(String(content || ""), {
          basePath: path,
          repoId: state.selectedId,
        })
      : `<pre>${esc(content)}</pre>`;
  }

  window.MD.registerImagePathResolver("repo-workspace", function (rawPath, options = {}) {
    const value = String(rawPath || "").trim();
    if (!value || /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) return "";
    const repoId = String(options.repoId || state.selectedId || "");
    if (!repoId) return "";
    const relative = value.startsWith("/")
      ? value.replace(/^\/+/, "")
      : joinPath(dirname(options.basePath || ""), value);
    if (!relative) return "";
    return `/api/resources/content?address=${encodeURIComponent(`${repoId}/file/${relative}`)}`;
  });

  function setBusy(button, busy, label = "") {
    window.PortalUI.setButtonBusy(button, busy, label);
  }

  function setSettingsBusy(form, busy) {
    const page = form?.closest(".repoSettingsPage");
    if (!page) return;
    page.querySelectorAll("input, select, button").forEach(control => {
      control.disabled = busy;
    });
  }

  const {
    cancelRepoQuery, initializeSemanticSearch, loadAutomationStatuses, loadRepos, queryAllRepoRecords,
  } = repoModule("PortalRepoWorkspaceCatalog").createCatalog({
    elements, getJson, loadWorkspace: (...args) => loadWorkspace(...args), postJson, queryState,
    renderProjectOptions: (...args) => renderProjectOptions(...args),
    renderRepoBoard: (...args) => renderRepoBoard(...args),
    renderWorkspace: (...args) => renderWorkspace(...args), repoStateHtml, resourceServiceRecovery, showToast,
    state, updateAutomationControls: (...args) => updateAutomationControls(...args),
    updateModeControls: (...args) => updateModeControls(...args), writeQuery,
  });

  const {
    automationStatusFor, bindProjectFilter, openRepoWorkspace, projectIds, renderProjectOptions,
    renderRepoBoard, setFilterOpen, switchRepoMode, updateAutomationControls, updateModeControls,
  } = repoModule("PortalRepoWorkspaceBoard").createBoard({
    destroyFileFinderPopover: (...args) => destroyFileFinderPopover(...args), detailHistory, elements, esc,
    loadRepos, loadWorkspace: (...args) => loadWorkspace(...args), objectOf, queryState,
    renderWorkspace: (...args) => renderWorkspace(...args), repoStateHtml, showToast, state, uiIcon,
    workspaceCapability: (...args) => workspaceCapability(...args), writeQuery,
  });

  const {
    moveFileFinderSelection, renderCodeView, scheduleRepositoryStatistics, statusSummary,
  } = repoModule("PortalRepoWorkspaceCodeView").createCodeView({
    dirname, elements, esc, formatDateTime, getJson, isMarkdown, joinPath, markdown, repoStateHtml, state,
    uiIcon, workspaceCapability: (...args) => workspaceCapability(...args),
  });

  const {
    applyBranchFilter, applyChangeFilters, applyCommitListFilter, applyRenderedFilters, filterCommitCode,
    filterCommitFiles, renderBranchesView, renderChangesView, renderCommitsView, renderSettingsView,
  } = repoModule("PortalRepoWorkspaceGitViews").createGitViews({
    dirname, elements, esc, formatDateTime, repoStateHtml, state, uiIcon,
  });

  const {
    destroyFileFinderPopover, renderWorkspace, workspaceCapability,
  } = repoModule("PortalRepoWorkspaceShell").createShell({
    applyRenderedFilters, elements, esc, markdown, objectOf, projectIds, renderBranchesView,
    renderChangesView, renderCodeView, renderCommitsView, renderRepoBoard, renderSettingsView,
    scheduleRepositoryStatistics, showToast, state, statusSummary, uiIcon, updateModeControls,
  });

  const {
    invalidateRevisionViews, jumpToFileLine, loadChangeDiff, loadCommitList, loadWorkspace, openCommit,
    openDirectory, openFile, openFileBlame, openFileHistory, refreshOverview, searchFiles, selectTab,
  } = repoModule("PortalRepoWorkspaceNavigation").createNavigation({
    detailResourceGuest, dirname, elements, esc, formatDateTime, getJson, isMarkdown, queryState,
    renderRepoBoard, renderWorkspace, repoStateHtml, showToast, state, uiIcon, updateModeControls,
    workspaceCapability, writeQuery,
  });

  const {
    closeBranchDialog, closeCredentialDialog, closeEditor, closeRegisterModal, createBranch, openBranchDialog,
    openEditor, openRegisterModal, registerRepo, retryGitAuthentication, saveEditor, setEditorMode,
    submitGitCredential, syncRegisterPlacement,
  } = repoModule("PortalRepoWorkspaceDialogs").createDialogs({
    detailHistory, dialogs, dirname, elements, esc, getJson, invalidateRevisionViews, isMarkdown, loadRepos,
    loadWorkspace, markdown, postJson, queryState, renderWorkspace, setBusy, showToast, state,
    workspaceCapability, writeQuery,
  });

  const {
    deleteFile, deleteGitCredential, discardRepoChanges, savePushPolicy, saveRemote, saveSshKey, submitCommit,
    switchBranch, syncRepo, unregisterRepo,
  } = repoModule("PortalRepoWorkspaceGitActions").createGitActions({
    detailResourceGuest, dirname, getJson, invalidateRevisionViews, loadRepos, loadWorkspace, postJson,
    queryState, refreshOverview, renderWorkspace, repoInstruction, retryGitAuthentication, setBusy,
    setSettingsBusy, showSettingsSaved, showToast, state, writeQuery,
  });

  const {
    hideAutomationOverlay, renderAutomationDialog, startAllRepoAutomation, startRepoAutomation,
  } = repoModule("PortalRepoWorkspaceAutomation").createAutomation({
    automationStatusFor, dialogs, elements, esc, invalidateRevisionViews, loadAutomationStatuses,
    loadWorkspace, postJson, queryAllRepoRecords, queryState, renderRepoBoard, retryGitAuthentication,
    showToast, state, uiIcon, updateAutomationControls,
  });

  repoModule("PortalRepoWorkspaceEvents").createEvents({
    applyBranchFilter, applyChangeFilters, applyCommitListFilter, bindProjectFilter, cancelRepoQuery,
    closeBranchDialog, closeCredentialDialog, closeEditor, closeRegisterModal, copyRepoText, createBranch,
    deleteFile, deleteGitCredential, dialogs, discardRepoChanges, elements, filterCommitCode,
    filterCommitFiles, hideAutomationOverlay, invalidateRevisionViews, jumpToFileLine, loadChangeDiff,
    loadCommitList, loadRepos, loadWorkspace, moveFileFinderSelection, openBranchDialog, openCommit,
    openDirectory, openEditor, openFile, openFileBlame, openFileHistory, openRegisterModal, openRepoWorkspace,
    postJson, queryState, refreshOverview, registerRepo, renderAutomationDialog, renderRepoBoard,
    renderWorkspace, saveEditor, savePushPolicy, saveRemote, saveSshKey, searchFiles, selectTab,
    setEditorMode, setFilterOpen, showToast, startAllRepoAutomation, startRepoAutomation, state, submitCommit,
    submitGitCredential, switchBranch, switchRepoMode, syncRegisterPlacement, syncRepo, unregisterRepo,
    updateAutomationControls, workspaceCapability, writeQuery,
  });

  elements.tabs.dataset.resourceCategory = "repo";
  window.PortalResourceTabs?.render(elements.tabs);
  elements.query.value = queryState().q;
  state.selectedId = queryState().item;
  updateModeControls();
  initializeSemanticSearch();
  state.semanticSearch?.load().catch(error => {
    showToast(window.PortalUI.errorMessage(window.PortalI18n.t("加载 Repo 语义搜索"), error), "error");
  });
  void resourceServiceRecovery.run();
})();
