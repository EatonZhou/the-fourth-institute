(function () {
  "use strict";

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
  let repoWorkbench = null;
  let repoFilePreviewPanel = null;

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
  let projectFilterDropdown = null;
  let fileFinderPopover = null;
  let suppressFileFinderClose = false;
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

  const REPO_QUERY_LIMIT = 500;

  function cancelRepoQuery() {
    state.requestController?.abort();
    state.requestController = null;
    state.requestSequence += 1;
    state.descriptionPending.forEach(id => state.descriptionAttempted.delete(id));
    state.descriptionPending.clear();
    state.automationStatusLoading = false;
  }

  const getJson = (url, options = {}) => resourceServiceRecovery.guard(() => window.PortalAPI.get(url, options));

  async function queryAllRepoRecords({q = "", project = "", signal} = {}) {
    const recordsById = new Map();
    let offset = 0;
    while (true) {
      const params = new URLSearchParams({
        resource_type: "repo",
        include_attributes: "1",
        limit: String(REPO_QUERY_LIMIT),
        offset: String(offset),
      });
      if (q) params.set("q", q);
      if (project) params.set("project", project);
      const payload = await getJson(`/api/resource-index/query?${params}`, {signal});
      const page = Array.isArray(payload.resources) ? payload.resources : [];
      const previousSize = recordsById.size;
      page.forEach(record => {
        const id = String(record?.resource_id || "").trim();
        if (id) recordsById.set(id, record);
      });
      const total = Number(payload.total);
      offset += page.length;
      if (
        !page.length
        || page.length < REPO_QUERY_LIMIT
        || recordsById.size === previousSize
        || (Number.isFinite(total) && offset >= total)
      ) break;
    }
    return [...recordsById.values()];
  }

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

  function formatBytes(value) {
    return window.PortalUI.formatBytes(value, {
      invalidFallback: "—",
      maximumUnit: "TB",
      precision: "single",
    });
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

  function initializeSemanticSearch() {
    state.semanticSearch = window.PortalSemanticSearch?.create({
      input: elements.query,
      button: elements.semanticButton,
      panel: elements.semanticPanel,
      identity: elements.semanticIdentity,
      identityOptions: {
        titleId: "repoSemanticSearchTitle",
        idId: "repoSemanticSearchId",
      },
      projectionScope: "repo",
      client: { get: getJson, post: postJson },
      initialActiveId: queryState().semantic,
      urls: {
        list: "/api/resource-semantic-searches?resource_scope=repo",
        start: "/api/resource-semantic-search/start",
        refresh: "/api/resource-semantic-search/refresh",
        delete: "/api/resource-semantic-search/delete",
      },
      startPayload: () => ({ resource_scope: "repo" }),
      actionPayload: () => ({ resource_scope: "repo" }),
      onStart: () => {
        elements.query.value = "";
        state.mode = "board";
        writeQuery({ q: "", semantic: "", item: "", tab: "", path: "", file: "" });
        loadRepos();
      },
      onActiveChange: searchId => {
        state.mode = "board";
        if (searchId) {
          elements.query.value = "";
          writeQuery({ q: "", semantic: searchId, item: "", tab: "", path: "", file: "" });
        } else {
          writeQuery({ semantic: "", item: "" });
        }
        applyVisibleRecords();
      },
      onError: (_error, message) => showToast(message, "error"),
    });
  }

  function applyVisibleRecords({ render = true } = {}) {
    state.visibleRecords = state.semanticSearch
      ? state.semanticSearch.filterAndSort(
          state.records,
          record => record.resource_id,
        )
      : state.records.slice();
    elements.count.textContent = state.semanticSearch?.activeId()
      ? window.PortalI18n.text`${state.visibleRecords.length} 个语义匹配`
      : window.PortalI18n.text`${state.records.length} 个 Repo`;
    updateModeControls();
    if (!render) return;
    if (state.mode === "board") renderRepoBoard();
    else if (state.overview?.id === state.selectedId) renderWorkspace();
  }

  async function loadRepoDescriptions(records, {
    render = true,
    querySequence = state.requestSequence,
    signal,
  } = {}) {
    if (querySequence !== state.requestSequence) return null;
    const missing = (Array.isArray(records) ? records : []).filter(record => {
      const id = String(record?.resource_id || "").trim();
      return id
        && !String(record?.summary || "").trim()
        && !state.descriptionAttempted.has(id);
    });
    if (!missing.length) return null;
    const ids = missing.map(record => String(record.resource_id));
    ids.forEach(id => {
      state.descriptionAttempted.add(id);
      state.descriptionPending.add(id);
      state.descriptionFailed.delete(id);
    });
    if (render && state.mode === "board") renderRepoBoard();
    try {
      // Generation may span multiple server-limited model calls.
      const payload = await postJson("/api/repos/descriptions/generate", {
        repo_ids: ids,
      }, { signal, timeoutMs: 0 });
      if (querySequence !== state.requestSequence) return null;
      const descriptions = new Map(
        (Array.isArray(payload.repos) ? payload.repos : [])
          .map(item => [String(item?.id || ""), String(item?.description || "").trim()])
          .filter(([id, description]) => id && description),
      );
      state.records.forEach(record => {
        const description = descriptions.get(String(record.resource_id || ""));
        if (description) record.summary = description;
      });
      const failed = new Set(
        (Array.isArray(payload.errors) ? payload.errors : [])
          .map(item => String(item?.id || ""))
          .filter(Boolean),
      );
      ids.forEach(id => {
        if (!descriptions.has(id) && failed.has(id)) {
          state.descriptionFailed.add(id);
        }
      });
      return payload;
    } catch (error) {
      if (querySequence !== state.requestSequence || error?.name === "AbortError") {
        ids.forEach(id => state.descriptionAttempted.delete(id));
      } else {
        ids.forEach(id => state.descriptionFailed.add(id));
      }
      throw error;
    } finally {
      ids.forEach(id => state.descriptionPending.delete(id));
      if (querySequence === state.requestSequence) applyVisibleRecords({ render });
    }
  }

  function queryVersionIsCurrent(querySequence) {
    return querySequence == null || querySequence === state.requestSequence;
  }

  async function loadAutomationStatuses(records, {
    render = true,
    querySequence = null,
    signal,
  } = {}) {
    if (!queryVersionIsCurrent(querySequence)) return [];
    const values = Array.isArray(records) ? records : [];
    const ids = [...new Set(values.map(record => (
      typeof record === "string" ? record : record?.resource_id
    )).map(value => String(value || "").trim()).filter(Boolean))];
    const names = new Map(values
      .filter(record => record && typeof record === "object" && record.resource_id)
      .map(record => [String(record.resource_id), String(record.title || record.resource_id)]));
    if (!ids.length) {
      state.automationStatusLoaded = true;
      updateAutomationControls();
      return [];
    }
    state.automationStatusLoading = true;
    updateAutomationControls();
    if (render && state.mode === "board") renderRepoBoard();
    const output = [];
    try {
      for (let offset = 0; offset < ids.length; offset += 120) {
        const params = new URLSearchParams();
        ids.slice(offset, offset + 120).forEach(id => params.append("repo_id", id));
        const payload = await getJson(`/api/repos/automation/status?${params}`, { signal });
        if (!queryVersionIsCurrent(querySequence)) return [];
        const statuses = Array.isArray(payload.repos) ? payload.repos : [];
        output.push(...statuses);
      }
      if (!queryVersionIsCurrent(querySequence)) return [];
      names.forEach((name, id) => state.automationNames.set(id, name));
      output.forEach(item => {
        if (item?.id) state.automationStatuses.set(String(item.id), item);
      });
      state.automationStatusLoaded = true;
      return output;
    } finally {
      if (queryVersionIsCurrent(querySequence)) {
        state.automationStatusLoading = false;
        updateAutomationControls();
        if (render && state.mode === "board") renderRepoBoard();
      }
    }
  }

  async function loadRepos({
    keepWorkspace = true,
    propagateServiceUnavailable = false,
  } = {}) {
    cancelRepoQuery();
    const controller = new AbortController();
    state.requestController = controller;
    const sequence = state.requestSequence;
    const current = queryState();
    elements.query.value = current.q;
    elements.count.textContent = window.PortalI18n.t("正在加载…");
    try {
      const records = await queryAllRepoRecords({...current, signal: controller.signal});
      if (sequence !== state.requestSequence) return;
      state.records = records;
      renderProjectOptions(state.records);
      const requestedId = current.item;
      if (requestedId) {
        if (requestedId !== state.selectedId) state.overview = null;
        state.selectedId = requestedId;
        state.mode = "workspace";
      }
      applyVisibleRecords({ render: false });
      if (state.mode === "board") {
        renderRepoBoard();
      } else if (
        keepWorkspace
        && state.selectedId
        && state.overview?.id === state.selectedId
      ) {
        renderWorkspace();
      } else if (state.selectedId) {
        await loadWorkspace();
      } else {
        state.mode = "board";
        renderRepoBoard();
      }
      loadAutomationStatuses(state.records, {
        querySequence: sequence,
        signal: controller.signal,
      })
        .catch(() => {
          if (sequence !== state.requestSequence) return;
          state.automationStatusLoaded = false;
          updateAutomationControls();
        });
      loadRepoDescriptions(state.records, {
        querySequence: sequence,
        signal: controller.signal,
      }).catch(error => {
        if (sequence !== state.requestSequence || error?.name === "AbortError") return;
        if (state.mode === "board") {
          showToast(window.PortalI18n.t("部分 Repo 简介暂未生成，可刷新页面重试。"), "error");
        }
      });
    } catch (error) {
      if (sequence !== state.requestSequence) return;
      if (resourceServiceRecovery.isUnavailable(error)) {
        if (propagateServiceUnavailable) throw error;
        void resourceServiceRecovery.check();
        return;
      }
      elements.count.textContent = window.PortalI18n.t("暂不可用");
      elements.workspace.innerHTML = repoStateHtml(error.message || error, {
        state: "error",
        title: window.PortalI18n.t("Repo 资源不可用"),
      });
    }
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

  function openRegisterModal() {
    syncRegisterPlacement();
    dialogs.register.open();
  }

  // Public content has no Projects; a Repo under Projects needs at least one.
  function syncRegisterPlacement() {
    const form = elements.registerForm;
    const projects = form.querySelector('[name="project_ids"]');
    const publicContent = form.querySelector('[name="placement"]').value === "public";
    projects.disabled = publicContent;
    projects.required = !publicContent;
  }

  function closeRegisterModal() {
    dialogs.register.close();
  }

  async function registerRepo(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const path = String(data.get("path") || "").trim();
    // A Repo names its place: Projects, or public content written as ["@public"].
    const projects = String(data.get("project_ids") || "").split(",").map(value => value.trim()).filter(Boolean);
    const publicContent = data.get("placement") === "public";
    if (!publicContent && !projects.length) {
      showToast(window.PortalI18n.t("填写 Project，或把位置选为公共内容。"), "error");
      return;
    }
    const payload = {
      resource_type: "repo",
      path,
      project_ids: publicContent ? ["@public"] : projects,
    };
    const submit = form.querySelector('button[type="submit"]');
    setBusy(submit, true, "注册中…");
    try {
      const result = await window.PortalResourceRegistrationService.register(payload, {
        confirmInitialize: error => window.PortalUI.confirm({
            title: window.PortalI18n.t("初始化 Git 仓库？"),
            message: window.PortalI18n.text`这个目录还不是 Git 仓库：\n${error.path || path}\n\n是否先创建 .git，再注册为 Repo？`,
            confirmLabel: window.PortalI18n.t("初始化并注册"),
          }),
      });
      if (result?.cancelled) return;
      const repoId = String(result.resource_id || "");
      form.reset();
      syncRegisterPlacement();
      closeRegisterModal();
      detailHistory.open(repoId, {
        q: "",
        project: "",
        tab: "",
        path: "",
        file: "",
      });
      elements.query.value = "";
      state.selectedId = repoId;
      state.mode = "workspace";
      await loadRepos({ keepWorkspace: false });
      showToast(window.PortalI18n.t("Repo 已注册并打开。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      setBusy(submit, false);
    }
  }

  function setEditorMode(mode) {
    state.editorMode = mode === "preview" ? "preview" : "edit";
    elements.editorTabs.forEach(tab => {
      const active = tab.dataset.editorMode === state.editorMode;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    elements.editorSource.hidden = state.editorMode !== "edit";
    elements.editorPreview.hidden = state.editorMode !== "preview";
    if (state.editorMode === "preview") {
      const path = elements.editorPath.value;
      elements.editorPreview.innerHTML = isMarkdown(path)
        ? markdown(elements.editorSource.value, path)
        : window.PortalI18n.html`<pre>${esc(elements.editorSource.value)}</pre>`;
    }
  }

  function openEditor(file = null) {
    if (!workspaceCapability("write_files")) return;
    const basePath = state.tree?.path || queryState().path || "";
    state.editorOriginalPath = String(file?.path || "");
    state.editorOriginalContent = String(file?.content || "");
    state.editorExpectedHash = String(file?.hash || "");
    state.editorNew = !file;
    elements.editorEyebrow.textContent = file ? window.PortalI18n.t("编辑文件") : window.PortalI18n.t("新建文件");
    elements.editorTitle.textContent = file?.name || window.PortalI18n.t("New file");
    elements.editorPath.value = file?.path || (basePath ? `${basePath}/` : "");
    elements.editorSource.value = file?.content || "";
    elements.editorStatus.textContent = window.PortalI18n.t("保存后进入 Changes，可与其他改动一起提交。");
    setEditorMode("edit");
    dialogs.editor.open({ focusTarget: file ? elements.editorSource : elements.editorPath });
    if (!file) {
      window.setTimeout(() => {
        elements.editorPath.setSelectionRange(elements.editorPath.value.length, elements.editorPath.value.length);
      }, 0);
    }
  }

  function closeEditor(options = {}) {
    return dialogs.editor.close(options);
  }

  async function saveEditor() {
    const path = elements.editorPath.value.trim();
    const content = elements.editorSource.value;
    if (!path) {
      elements.editorStatus.textContent = window.PortalI18n.t("请输入仓库内的文件路径。");
      elements.editorPath.focus();
      return;
    }
    setBusy(elements.editorSave, true, "保存中…");
    try {
      if (state.editorNew) {
        await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/entry`, {
          operation: "create",
          kind: "file",
          path,
          content,
        });
      } else {
        if (path !== state.editorOriginalPath) {
          await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/move`, {
            path: state.editorOriginalPath,
            destination: path,
          });
        }
        await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/file`, {
          path,
          content,
          expected_hash: state.editorExpectedHash,
        });
      }
      closeEditor({ force: true });
      writeQuery({ file: path, path: dirname(path), tab: "code" });
      await loadWorkspace({ file: path, tab: "code" });
      showToast(window.PortalI18n.t("文件已保存到工作区；可在 Changes 中统一提交。"), "success");
    } catch (error) {
      elements.editorStatus.textContent = error.message || String(error);
      showToast(error.message || error, "error");
    } finally {
      setBusy(elements.editorSave, false);
    }
  }

  function openBranchDialog() {
    elements.branchName.value = "";
    elements.branchStart.value = "HEAD";
    dialogs.branch.open();
  }

  function closeBranchDialog() {
    dialogs.branch.close();
  }

  async function createBranch(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    setBusy(submit, true, "创建中…");
    try {
      state.overview = await postJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/branch`,
        {
          operation: "create",
          name: String(data.get("name") || "").trim(),
          start_point: String(data.get("start_point") || "HEAD").trim(),
        },
      );
      closeBranchDialog();
      invalidateRevisionViews();
      state.activeTab = "code";
      writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
      state.file = null;
      state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
      renderWorkspace();
      showToast(window.PortalI18n.text`已切换到 ${state.overview.current_branch}。`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      setBusy(submit, false);
    }
  }

  async function switchBranch(name, button) {
    const select = button?.tagName === "SELECT";
    if (select) button.disabled = true;
    else setBusy(button, true, "切换中…");
    try {
      state.overview = await postJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/branch`,
        { operation: "switch", name },
      );
      invalidateRevisionViews();
      state.file = null;
      state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
      state.activeTab = "code";
      writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
      renderWorkspace();
      showToast(window.PortalI18n.text`已切换到 ${name}。`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
      if (select) {
        button.disabled = false;
        button.value = state.overview.current_branch;
      } else {
        setBusy(button, false);
      }
    }
  }

  function renderCredentialChallenge(challenge, errorMessage = "") {
    const value = challenge && typeof challenge === "object" ? challenge : {};
    state.credentialChallenge = value;
    const protocol = String(value.protocol || "https").toUpperCase();
    const host = String(value.host || "Git remote");
    const path = String(value.path || "");
    const operation = String(value.operation || "push");
    const isSsh = String(value.protocol || "") === "ssh";
    elements.credentialTitle.textContent = window.PortalI18n.text`${host} 需要认证`;
    elements.credentialHost.textContent = host;
    elements.credentialPath.textContent = `${value.remote || "remote"}${path ? ` · ${path}` : ""}`;
    elements.credentialProtocol.textContent = protocol;
    elements.credentialUsernameRow.hidden = isSsh;
    elements.credentialUsername.required = !isSsh;
    elements.credentialUsername.value = String(value.username || (isSsh ? "git" : ""));
    elements.credentialSecretLabel.textContent = String(value.secret_label || window.PortalI18n.t("访问令牌或密码"));
    elements.credentialSecret.placeholder = value.provider === "github"
      ? "github_pat_…"
      : isSsh
        ? window.PortalI18n.t("输入私钥口令或服务器密码")
        : window.PortalI18n.t("输入访问令牌或密码");
    elements.credentialHelp.textContent = String(value.help_text || "");
    elements.credentialRemember.checked = value.remember_default !== false;
    elements.credentialReason.textContent = value.rejected
      ? window.PortalI18n.t("刚才的凭据未被远程接受。请检查凭据是否已更新、过期或被撤销。")
      : operation === "push"
        ? window.PortalI18n.t("本地 commit 已保留。认证成功后，Portal 只会重试 Push。")
        : window.PortalI18n.text`Remote 要求认证后才能继续 ${operation.toUpperCase()}。`;
    elements.credentialReason.hidden = false;
    elements.credentialError.textContent = String(errorMessage || "");
    elements.credentialError.hidden = !errorMessage;
  }

  function closeCredentialDialog(result = null) {
    dialogs.credential.close();
    elements.credentialSecret.value = "";
    elements.credentialError.textContent = "";
    elements.credentialError.hidden = true;
    const resolve = state.credentialResolver;
    state.credentialResolver = null;
    state.credentialRejecter = null;
    state.credentialSubmitter = null;
    state.credentialChallenge = null;
    if (resolve) resolve(result);
  }

  function failCredentialDialog(error) {
    dialogs.credential.close();
    elements.credentialSecret.value = "";
    const reject = state.credentialRejecter;
    state.credentialResolver = null;
    state.credentialRejecter = null;
    state.credentialSubmitter = null;
    state.credentialChallenge = null;
    if (reject) reject(error);
  }

  function requestGitCredential(challenge, submitter) {
    if (state.credentialResolver) closeCredentialDialog(null);
    renderCredentialChallenge(challenge);
    state.credentialSubmitter = submitter;
    elements.credentialSecret.value = "";
    const target = elements.credentialUsernameRow.hidden
      || elements.credentialUsername.value
      ? elements.credentialSecret
      : elements.credentialUsername;
    dialogs.credential.open({ focusTarget: target });
    return new Promise((resolve, reject) => {
      state.credentialResolver = resolve;
      state.credentialRejecter = reject;
    });
  }

  async function submitGitCredential(event) {
    event.preventDefault();
    const submitter = state.credentialSubmitter;
    if (typeof submitter !== "function") {
      closeCredentialDialog(null);
      return;
    }
    const secret = elements.credentialSecret.value;
    const username = elements.credentialUsername.value.trim();
    if (!secret || (!elements.credentialUsernameRow.hidden && !username)) return;
    setBusy(elements.credentialSubmit, true, "正在验证…");
    elements.credentialError.hidden = true;
    try {
      const result = await submitter({
        username,
        secret,
        remember_credential: elements.credentialRemember.checked,
      });
      state.credentials = null;
      closeCredentialDialog(result);
    } catch (error) {
      const authChallenge = error?.data?.auth_challenge;
      if (authChallenge) {
        renderCredentialChallenge(
          authChallenge,
          error.message || String(error),
        );
        elements.credentialSecret.value = "";
        elements.credentialSecret.focus();
      } else {
        failCredentialDialog(error);
      }
    } finally {
      setBusy(elements.credentialSubmit, false);
    }
  }

  async function retryGitAuthentication(challenge) {
    const value = challenge && typeof challenge === "object" ? challenge : {};
    if (!value.repo_id || !value.remote || !value.operation) {
      throw new Error("Git 认证挑战缺少 remote 或操作信息。");
    }
    return requestGitCredential(value, credentials => postJson(
      `/api/repos/${encodeURIComponent(value.repo_id)}/workspace/sync`,
      {
        operation: value.operation,
        remote: value.remote,
        branch: value.branch || "",
        credential: {
          username: credentials.username,
          secret: credentials.secret,
        },
        remember_credential: credentials.remember_credential,
      },
    ));
  }

  async function syncRepo(operation, button) {
    if (
      operation === "push"
      && !await window.PortalUI.confirm({
        title: window.PortalI18n.t("Push 到 remote？"),
        message: window.PortalI18n.text`把 ${state.overview.current_branch} Push 到 remote？\n这会更新远程仓库。`,
        confirmLabel: "Push",
        tone: "warning",
      })
    ) return;
    setBusy(button, true, `${operation[0].toUpperCase()}${operation.slice(1)}…`);
    try {
      let result;
      try {
        result = await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/sync`,
          {
            operation,
            remote: state.overview.remotes.find(remote => remote.name === "origin")?.name
              || state.overview.remotes[0]?.name
              || "",
            branch: state.overview.current_branch,
          },
        );
      } catch (error) {
        const authChallenge = error?.data?.auth_challenge;
        if (!authChallenge) throw error;
        result = await retryGitAuthentication(authChallenge);
        if (!result) {
          setBusy(button, false);
          return;
        }
      }
      state.overview = result.workspace;
      invalidateRevisionViews();
      if (!state.file) {
        state.tree = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree?path=${encodeURIComponent(state.tree?.path || "")}`,
        );
      }
      renderWorkspace();
      showToast(window.PortalI18n.text`${operation[0].toUpperCase()}${operation.slice(1)} 完成。`, "success");
    } catch (error) {
      if (operation === "push") {
        await refreshOverview().catch(() => {});
      }
      showToast(error.message || error, "error");
      setBusy(button, false);
    }
  }

  async function discardRepoChanges(button) {
    const count = Number(state.overview?.status?.changes?.length || 0);
    if (!count) return;
    if (!await window.PortalUI.confirm({
      title: window.PortalI18n.t("放弃未提交改动？"),
      message: window.PortalI18n.text`放弃当前 Repo 的 ${count} 项未提交改动？\n`
        + window.PortalI18n.t("这会恢复 staged 与 unstaged 文件，并永久删除未跟踪文件和目录。此操作不能撤销。"),
      confirmLabel: window.PortalI18n.t("放弃改动"),
      tone: "danger",
    })) return;
    setBusy(button, true, "处理中…");
    // File reads started before or during the Discard would show discarded content.
    state.navigationSequence += 1;
    try {
      const result = await postJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/discard`,
        {},
      );
      state.navigationSequence += 1;
      state.overview = result.workspace;
      invalidateRevisionViews();
      state.commitDraft = { message: "", description: "" };
      state.file = null;
      if (queryState().file) writeQuery({ file: "" }, { historyMode: "replace" });
      state.tree = await getJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree?path=${encodeURIComponent(queryState().path || "")}`,
      );
      renderWorkspace();
      showToast(window.PortalI18n.text`已放弃 ${Number(result.discarded_count || 0)} 项改动。`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
      setBusy(button, false);
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

  async function deleteFile(path, button) {
    if (!await window.PortalUI.confirm({
      title: window.PortalI18n.t("删除文件？"),
      message: window.PortalI18n.text`删除「${path}」？\n文件会从 checkout 中移除，并作为未提交变更保留在 Changes。`,
      confirmLabel: window.PortalI18n.t("删除"),
      tone: "danger",
    })) return;
    setBusy(button, true);
    try {
      await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/entry`, {
        operation: "delete",
        path,
      });
      writeQuery({ file: "", path: dirname(path) });
      state.file = null;
      await loadWorkspace({ path: dirname(path), file: "", tab: "code" });
      showToast(window.PortalI18n.t("文件已删除；可在 Changes 中检查并提交。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
      setBusy(button, false);
    }
  }

  async function submitCommit(form) {
    const data = new FormData(form);
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, "Committing…");
    try {
      const result = await postJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/commit`,
        {
          message: String(data.get("message") || "").trim(),
          description: String(data.get("description") || "").trim(),
        },
      );
      state.overview = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace`);
      state.activeTab = "code";
      state.file = null;
      state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
      invalidateRevisionViews();
      state.commitDraft = { message: "", description: "" };
      writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
      renderWorkspace();
      showToast(window.PortalI18n.text`已创建 commit ${result.commit?.short_sha || ""}。`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
      setBusy(button, false);
    }
  }

  async function saveRemote(form) {
    const data = new FormData(form);
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, "保存中…");
    setSettingsBusy(form, true);
    try {
      const result = await repoInstruction("UpdateResource", {
        changes: {
          operation: "set_remote",
          name: String(data.get("name") || "").trim(),
          url: String(data.get("url") || "").trim(),
          push_url: String(data.get("push_url") || "").trim(),
        },
      });
      await refreshOverview();
      showSettingsSaved(result, window.PortalI18n.t("Remote 已保存。"));
    } catch (error) {
      showToast(error.message || error, "error");
      setSettingsBusy(form, false);
      setBusy(button, false);
    }
  }

  async function savePushPolicy(form) {
    const button = form.querySelector('button[type="submit"]');
    const enabled = form.elements.push_enabled.checked;
    setBusy(button, true, "保存中…");
    setSettingsBusy(form, true);
    try {
      const result = await repoInstruction("UpdateResource", {
        changes: {
          operation: "set_policy",
          push_enabled: enabled,
        },
      });
      await refreshOverview();
      showSettingsSaved(result, enabled ? window.PortalI18n.t("已允许 Portal Push。") : window.PortalI18n.t("已禁用此 Repo 的 Push。"));
    } catch (error) {
      showToast(error.message || error, "error");
      setSettingsBusy(form, false);
      setBusy(button, false);
    }
  }

  async function saveSshKey(form) {
    const data = new FormData(form);
    const button = form.querySelector('button[type="submit"]');
    const sshKey = String(data.get("ssh_key") || "").trim();
    setBusy(button, true, "保存中…");
    setSettingsBusy(form, true);
    try {
      const result = await repoInstruction("UpdateResource", {
        changes: {
          operation: "set_ssh_key",
          ssh_key: sshKey,
        },
      });
      await refreshOverview();
      showSettingsSaved(result, sshKey ? window.PortalI18n.text`已为此 Repo 使用 ${sshKey}。` : window.PortalI18n.t("已恢复 SSH 默认密钥。"));
    } catch (error) {
      showToast(error.message || error, "error");
      setSettingsBusy(form, false);
      setBusy(button, false);
    }
  }

  async function deleteGitCredential(credentialId, button) {
    if (!credentialId) return;
    if (!await window.PortalUI.confirm({
      title: window.PortalI18n.t("删除 Git 凭据？"),
      message: window.PortalI18n.t("后续 Remote 再次要求认证时，Portal 会重新提示。"),
      confirmLabel: window.PortalI18n.t("删除"),
      tone: "danger",
    })) return;
    setBusy(button, true, "删除中…");
    try {
      await postJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/credentials`,
        {
          operation: "delete",
          credential_id: credentialId,
        },
      );
      state.credentials = await getJson(
        `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/credentials`,
      );
      renderWorkspace();
      showToast(window.PortalI18n.t("Git 凭据已删除。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
      setBusy(button, false);
    }
  }

  async function unregisterRepo() {
    const name = state.overview.name || state.selectedId;
    if (!await window.PortalUI.confirm({
      title: window.PortalI18n.t("解除 Repo 注册？"),
      message: window.PortalI18n.text`解除「${name}」的 Repo 注册？\nGit checkout、分支、提交和文件不会被删除。`,
      confirmLabel: window.PortalI18n.t("解除注册"),
      tone: "danger",
    })) return;
    try {
      await repoInstruction("UnregisterResource", {});
      detailResourceGuest.deleted({ resourceId: state.selectedId, kind: "unregistered" });
      writeQuery({ item: "", tab: "code", path: "", file: "" });
      state.selectedId = "";
      state.mode = "board";
      state.overview = null;
      await loadRepos({ keepWorkspace: false });
      showToast(window.PortalI18n.t("Repo 注册已解除；checkout 保持不变。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
    }
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

  function showAutomationOverlay() {
    dialogs.automation.open();
  }

  function hideAutomationOverlay() {
    dialogs.automation.close();
  }

  function automationResultMarkup(item) {
    const stateName = String(item.state || "pending");
    const marks = {
      pending: "status-dot",
      running: "refresh",
      success: "check",
      partial: "alert-circle",
      auth_required: "lock",
      skipped: "minus",
      error: "close",
      stopped: "pause",
    };
    const result = item.result || {};
    const push = result.push || {};
    let stateLabel = {
      pending: window.PortalI18n.t("等待"),
      running: window.PortalI18n.t("AI 分析中"),
      success: result.pushed
        ? `${push.remote || "remote"}/${push.branch || ""}`
        : window.PortalI18n.t("本地 commit"),
      partial: window.PortalI18n.t("已 commit · push 失败"),
      auth_required: window.PortalI18n.t("等待 Git 认证"),
      skipped: window.PortalI18n.t("无变更"),
      error: window.PortalI18n.t("失败"),
      stopped: window.PortalI18n.t("已停止"),
    }[stateName] || stateName;
    if (stateName === "success" && result.ai?.model) {
      stateLabel = `${stateLabel} · ${result.ai.model}`;
    }
    const title = result.message
      ? window.PortalI18n.html`<p class="repoAutomationResultTitle">${esc(result.message)}</p>`
      : "";
    const reason = result.reason
      ? window.PortalI18n.html`<p class="repoAutomationResultReason">${esc(result.reason)}</p>`
      : "";
    const error = item.error || (
      ["partial", "auth_required"].includes(stateName) ? push.error : ""
    );
    const errorMarkup = error
      ? window.PortalI18n.html`<p class="repoAutomationResultError">${esc(error)}</p>`
      : "";
    const note = stateName === "skipped"
      ? window.PortalI18n.markup('<p class="repoAutomationResultReason">工作区没有需要提交的变更。</p>')
      : stateName === "stopped"
        ? window.PortalI18n.markup('<p class="repoAutomationResultReason">没有开始处理该仓库。</p>')
        : "";
    return window.PortalI18n.html`
      <article class="repoAutomationResult ${esc(stateName)}" data-automation-result="${esc(item.id)}">
        <span class="repoAutomationResultMark" aria-hidden="true">${uiIcon(marks[stateName] || "status-dot")}</span>
        <div class="repoAutomationResultBody">
          <div class="repoAutomationResultHead">
            <strong>${esc(item.name || item.id)}</strong>
            <span>${esc(stateLabel)}</span>
          </div>
          ${title}${reason}${errorMarkup}${note}
        </div>
      </article>`;
  }

  function renderAutomationDialog() {
    const total = state.automationTargets.length;
    const completed = Math.min(state.automationCompleted, total);
    const percent = total ? Math.round(completed * 100 / total) : (state.automationActive ? 8 : 100);
    const currentName = state.automationNames.get(state.automationCurrent) || state.automationCurrent;
    elements.automationTitle.textContent = state.automationScope === "all"
      ? window.PortalI18n.t("AI 提交所有 Repo")
      : window.PortalI18n.t("AI 提交 Repo");
    if (state.automationActive && state.automationCurrent) {
      elements.automationStatus.textContent = window.PortalI18n.text`正在处理 ${currentName}`;
      elements.automationSub.textContent = window.PortalI18n.text`${completed + 1}/${total} · AI 先生成 commit 原因，再执行 Git 操作`;
    } else if (state.automationActive) {
      elements.automationStatus.textContent = state.automationScope === "all"
        ? window.PortalI18n.t("正在检查当前筛选的 Repo…")
        : window.PortalI18n.t("正在准备 Repo 变更…");
      elements.automationSub.textContent = window.PortalI18n.t("由 AI 生成 commit 标题和原因");
    } else if (!total && !state.automationResults.length) {
      elements.automationStatus.textContent = window.PortalI18n.t("没有需要提交的 Repo");
      elements.automationSub.textContent = window.PortalI18n.t("所有已检查工作区均无本地变更");
    } else {
      const successful = state.automationResults.filter(item => item.state === "success").length;
      const partial = state.automationResults.filter(item => (
        ["partial", "auth_required"].includes(item.state)
      )).length;
      const failed = state.automationResults.filter(item => item.state === "error").length;
      elements.automationStatus.textContent = state.automationCancelRequested ? window.PortalI18n.t("已停止后续仓库") : window.PortalI18n.t("AI 提交已完成");
      elements.automationSub.textContent = window.PortalI18n.text`${successful} 成功${partial ? window.PortalI18n.text` · ${partial} push 失败` : ""}${failed ? window.PortalI18n.text` · ${failed} 失败` : ""}`;
    }
    elements.automationProgressBar.style.width = `${percent}%`;
    elements.automationProgress.classList.toggle("complete", !state.automationActive);
    elements.automationProgress.setAttribute("aria-valuenow", String(percent));
    elements.automationResults.innerHTML = state.automationResults.length
      ? state.automationResults.map(automationResultMarkup).join("")
      : window.PortalI18n.html`<div class="repoAutomationEmpty">${state.automationActive ? window.PortalI18n.t("正在读取 Git 状态…") : window.PortalI18n.t("当前没有需要提交的变更。")}</div>`;
    elements.automationStop.hidden = !state.automationActive || total <= 1;
    elements.automationStop.disabled = state.automationCancelRequested;
    elements.automationStop.textContent = state.automationCancelRequested ? window.PortalI18n.t("正在停止…") : window.PortalI18n.t("停止后续仓库");
    elements.automationDone.textContent = state.automationActive ? window.PortalI18n.t("隐藏") : window.PortalI18n.t("完成");
    updateAutomationControls();
  }

  function beginAutomation(scope, reveal = true) {
    state.automationActive = true;
    state.automationCancelRequested = false;
    state.automationTargets = [];
    state.automationResults = [];
    state.automationCompleted = 0;
    state.automationCurrent = "";
    state.automationScope = scope;
    if (reveal) showAutomationOverlay();
    renderAutomationDialog();
  }

  async function executeAutomationTargets(targets, initialResults = []) {
    state.automationTargets = targets.map(target => ({
      id: String(target.id || ""),
      name: String(target.name || target.id || ""),
    }));
    const pending = state.automationTargets.map(target => ({
      ...target,
      state: "pending",
      result: null,
      error: "",
    }));
    state.automationResults = [...initialResults, ...pending];
    state.automationCompleted = 0;
    renderAutomationDialog();

    for (const target of pending) {
      if (state.automationCancelRequested) break;
      target.state = "running";
      state.automationCurrent = target.id;
      renderAutomationDialog();
      if (state.mode === "board") renderRepoBoard();
      try {
        const result = await postJson(
          `/api/repos/${encodeURIComponent(target.id)}/workspace/auto-commit`,
          {},
          // The server owns model, Git and push timeouts for this multi-step operation.
          {timeoutMs: 0},
        );
        target.result = result;
        if (result.automation_status) {
          state.automationStatuses.set(target.id, result.automation_status);
        }
        if (result.outcome === "clean") {
          target.state = "skipped";
        } else if (result.auth_challenge) {
          target.state = "auth_required";
          renderAutomationDialog();
          const syncResult = await retryGitAuthentication(result.auth_challenge);
          if (syncResult) {
            result.outcome = "committed_and_pushed";
            result.pushed = true;
            result.auth_challenge = null;
            result.credential = syncResult.credential || {};
            result.push = {
              ...(result.push || {}),
              attempted: true,
              ok: true,
              error: "",
              auth_challenge: null,
            };
            target.state = "success";
          } else {
            target.state = "partial";
            target.error = "已取消 Git 认证；本地 commit 已保留，可稍后单独 Push。";
          }
        } else if (result.outcome === "committed_push_failed") {
          target.state = "partial";
        } else {
          target.state = "success";
        }
      } catch (error) {
        target.state = "error";
        target.error = String(error?.message || error);
      }
      state.automationCompleted += 1;
      state.automationCurrent = "";
      renderAutomationDialog();
      if (state.mode === "board") renderRepoBoard();
    }

    if (state.automationCancelRequested) {
      pending.filter(item => item.state === "pending").forEach(item => {
        item.state = "stopped";
        state.automationCompleted += 1;
      });
    }
    state.automationActive = false;
    state.automationCurrent = "";
    renderAutomationDialog();
    if (state.mode === "board") renderRepoBoard();
    else if (state.automationTargets.some(item => item.id === state.selectedId)) {
      state.commitDraft = { message: "", description: "" };
      invalidateRevisionViews();
      loadWorkspace().catch(error => showToast(error.message || error, "error"));
    }
    const successCount = state.automationResults.filter(item => item.state === "success").length;
    const partialCount = state.automationResults.filter(item => (
      ["partial", "auth_required"].includes(item.state)
    )).length;
    const errorCount = state.automationResults.filter(item => item.state === "error").length;
    const singleResult = state.automationScope === "single"
      ? state.automationResults[0]
      : null;
    if (singleResult?.state === "error") {
      showToast(window.PortalI18n.text`AI 提交失败：${singleResult.error || window.PortalI18n.t("未知错误")}`, "error");
    } else if (singleResult && ["partial", "auth_required"].includes(singleResult.state)) {
      showToast(
        window.PortalI18n.text`Commit 已创建，但 Push 失败：${singleResult.error || singleResult.result?.push?.error || window.PortalI18n.t("请检查 Remote 权限")}`,
        "error",
      );
    } else if (singleResult?.state === "success") {
      showToast(
        singleResult.result?.pushed
          ? window.PortalI18n.text`Commit 已创建并 Push：${singleResult.result.message || singleResult.name}`
          : window.PortalI18n.text`Commit 已创建：${singleResult.result?.message || singleResult.name}`,
        "success",
      );
    } else if (errorCount || partialCount) {
      showToast(window.PortalI18n.text`AI 提交完成：${successCount} 成功，${partialCount} push 失败，${errorCount} 失败。`, "error");
    } else if (successCount) {
      showToast(window.PortalI18n.text`AI 提交完成：${successCount} 个 Repo 已处理。`, "success");
    } else {
      showToast(window.PortalI18n.t("没有 Repo 需要提交。"));
    }
  }

  async function startRepoAutomation(repoId) {
    if (state.automationActive) {
      showToast(
        state.automationScope === "single"
          ? window.PortalI18n.t("这个 Repo 正在执行 AI Commit。")
          : window.PortalI18n.t("所有 Repo 的 AI 提交正在执行。"),
      );
      return;
    }
    const id = String(repoId || "").trim();
    if (!id) return;
    const status = automationStatusFor(id);
    if (status && !status.dirty) {
      showToast(window.PortalI18n.t("当前 Repo 工作区干净。"));
      return;
    }
    beginAutomation("single", false);
    await executeAutomationTargets([{
      id,
      name: state.automationNames.get(id) || id,
    }]);
  }

  async function startAllRepoAutomation() {
    if (state.automationActive) {
      showAutomationOverlay();
      renderAutomationDialog();
      return;
    }
    beginAutomation("all");
    try {
      const records = await queryAllRepoRecords(queryState());
      records.forEach(record => {
        state.automationNames.set(
          String(record.resource_id || ""),
          String(record.title || record.resource_id || ""),
        );
      });
      const statuses = await loadAutomationStatuses(records, { render: false });
      const byId = new Map(records.map(record => [String(record.resource_id || ""), record]));
      const blocked = statuses.filter(item => item?.error || (item?.dirty && !item?.actionable));
      const initialResults = blocked.map(item => ({
        id: String(item.id || ""),
        name: String(byId.get(String(item.id || ""))?.title || item.name || item.id || ""),
        state: "error",
        result: null,
        error: String(item.blocked_reason || item.error || "该 Repo 当前不能自动提交"),
      }));
      const targets = statuses.filter(item => item?.actionable).map(item => ({
        id: String(item.id || ""),
        name: String(byId.get(String(item.id || ""))?.title || item.name || item.id || ""),
      }));
      if (!targets.length) {
        state.automationActive = false;
        state.automationResults = initialResults;
        state.automationTargets = [];
        state.automationCompleted = 0;
        renderAutomationDialog();
        showToast(initialResults.length ? window.PortalI18n.t("有 Repo 需要先处理冲突或权限问题。") : window.PortalI18n.t("所有 Repo 工作区均无本地变更。"));
        return;
      }
      const statusById = new Map(statuses.map(item => [String(item.id || ""), item]));
      const preview = targets.slice(0, 10).map(target => {
        const item = statusById.get(target.id) || {};
        const destination = item.will_push
          ? `commit + push → ${item.remote || "remote"}`
          : "仅本地 commit";
        return `• ${target.name}：${Number(item.change_count || 0)} changes，${destination}`;
      });
      if (targets.length > preview.length) {
        preview.push(`• 以及另外 ${targets.length - preview.length} 个 Repo`);
      }
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.text`提交 ${targets.length} 个 Repo？`,
        message: window.PortalI18n.text`${preview.join("\n")}\n\nAI 会分别生成可审阅的 commit 标题与原因；此操作会修改以上仓库，标为 push 的仓库还会更新 remote。`,
        confirmLabel: window.PortalI18n.t("开始 AI 提交"),
        tone: "warning",
      });
      if (!approved) {
        state.automationActive = false;
        state.automationTargets = [];
        state.automationResults = [];
        hideAutomationOverlay();
        renderAutomationDialog();
        showToast(window.PortalI18n.t("已取消批量 AI 提交。"));
        return;
      }
      await executeAutomationTargets(targets, initialResults);
    } catch (error) {
      state.automationActive = false;
      state.automationTargets = [];
      state.automationResults = [{
        id: "preflight",
        name: window.PortalI18n.t("所有 Repo 检查"),
        state: "error",
        result: null,
        error: String(error?.message || error),
      }];
      renderAutomationDialog();
      showToast(error.message || error, "error");
    }
  }

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

  projectFilterDropdown = window.PortalFormControls.bindFilterDropdown({
    button: elements.filterToggle,
    panel: elements.filterPanel,
    onToggle: open => {
      elements.filterToggle.classList.toggle("active", open || Boolean(queryState().project));
    },
  });
  const projectFilterSummary = window.PortalFormControls?.bindFilterSummary?.({
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
        timeout: 15,
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
