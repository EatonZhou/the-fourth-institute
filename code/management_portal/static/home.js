const homeApi = window.PortalAPI.createClient({ cache: "no-store" });
const ACTIVE_AGENT_STATUS_LOADING_RETRY_MS = 750;
const ACTIVE_AGENT_STATUS_RETRY_BASE_MS = 1000;
const ACTIVE_AGENT_STATUS_RETRY_MAX_MS = 30000;
let portalUsername = "anonymous";
let canSetScheduler = false;
let canRegisterAccount = false;
let agentStatusData = null;
let agentStatusSummaryLoaded = false;
let agentStatusRefresh = null;
let diskStatusRefresh = null;
let diskStatusData = null;
let gpuStatusData = null;
let activeAgentStatus = null;
let activeResidentStateCollection = null;
let activeResidentStateSignature = "";
let activeResidentStateFrame = 0;
let activeAgentStatusRefresh = null;
let activeAgentStatusRetryAttempt = 0;
let agentStatusRunboardHtml = "";
const homeRoute = window.PortalHomeComponents.resolveRoute(window.location.search);

// Home's panels are parts that the home_*.js scripts index.html loads before
// this one register; the root creates them here, after its shared state.
const {
  residentNavigationUrl, loadAgentAccess,
} = window.PortalHomeAgentAccess.createAgentAccess({
  currentActiveAgentStatus: () => activeAgentStatus, esc, homeApi, renderActiveAgentStatus,
});

const {
  loadDailyNews, loadPortalPulseOnce,
} = window.PortalHomePulse.createPulse({
  esc, formatPortalTime, homeApi,
});

const {
  gpuResourceHtml, gpuDetailsHtml, diskResourceHtml, diskDetailsHtml,
} = window.PortalHomeSystemResources.createSystemResources({
  esc, formatDateTime,
});

const {
  executionAccounts, staleExecutionAccounts, refreshAccountQuotaWindows, accountRows,
  accountManagementActions, accountEmptyState, syncAccountDetailsButtons, destroyAccountDetails,
} = window.PortalHomeAccounts.createAccounts({
  currentActiveAgentStatusRefresh: () => activeAgentStatusRefresh,
  currentAgentStatusData: () => agentStatusData, currentCanRegisterAccount: () => canRegisterAccount,
  currentCanSetScheduler: () => canSetScheduler, esc, formatDateTime, homeApi, loadAgentStatus,
  renderAgentStatus,
});

const {
  connectUsageAttribution,
} = window.PortalHomeUsage.createUsage({
  currentPortalUsername: () => portalUsername, esc, formatDateTime, homeApi,
});

const {
  setScheduledTriggeredTarget, renderScheduledTriggeredStatus, loadScheduledTriggeredStatus,
  connectScheduledTriggered, currentScheduledTriggeredStatus,
} = window.PortalHomeScheduledTriggered.createScheduledTriggered({
  currentAgentStatusData: () => agentStatusData, currentHomeController: () => homeController,
  displayDateTime, esc, homeApi, listen, renderAgentStatus,
});

const {
  agentStatusRunboard, focusedResidentIcon, restoreResidentIconFocus, bindAgentStatusResidentMenu,
} = window.PortalHomeActivity.createActivity({
  currentAgentStatusData: () => agentStatusData, ensureActiveAgentStatusRefresh, esc, executionAccounts,
  listen, residentNavigationUrl,
});

function formatPortalTime(value) {
  return window.PortalUI.formatDateTime(value, { style: "compact" });
}

function esc(value) {
  return window.PortalUIPrimitives.escapeHtml(value);
}

function formatDateTime(value) {
  return window.PortalUI.formatDateTime(value, { style: "status", invalidFallback: false });
}

function displayDateTime(value) {
  const formatted = formatDateTime(value);
  return formatted || String(value || "").trim();
}

function ensureAgentStatusComponentHosts(node) {
  const required = ["agent-accounts", "system-resources", "scheduled-triggered", "usage-attribution"];
  const hosts = Object.fromEntries(required.map(id => [id, node?.querySelector?.(`[data-home-component="${id}"]`)]));
  const missing = required.filter(id => !hosts[id]);
  if (missing.length) {
    throw new Error(`Agent Status Workbench is missing component hosts: ${missing.join(", ")}`);
  }
  return hosts;
}

function renderAgentStatus(data) {
  const summary = {...(data || {})};
  delete summary.active_agents;
  delete summary.token_usage;
  agentStatusData = {
    ...summary,
    disk: diskStatusData,
    gpu: gpuStatusData,
    scheduled_triggered: currentScheduledTriggeredStatus(),
    active_agents: activeAgentStatus || {},
  };
  data = agentStatusData;
  const node = document.querySelector('[data-home-component="agent-status"]');
  if (!node) return;
  const componentHosts = ensureAgentStatusComponentHosts(node);
  const currentRunboard = componentHosts["agent-accounts"].querySelector("[data-agent-status-runboard]");
  const focusedResident = focusedResidentIcon(currentRunboard);
  const active = data?.active_agents || {};
  const gpu = data?.gpu || {};
  const disk = data?.disk || {};
  const accounts = executionAccounts(data);
  const refreshRetry = Number(data?.refresh_retry_after_seconds || 0);
  const refreshNote = refreshRetry > 0 && !data?.refreshing
    ? window.PortalI18n.html`<div class="agent-status-muted">余额和账号资料刷新有间隔限制，请在 ${Math.ceil(refreshRetry / 60)} 分钟后重试。</div>`
    : "";
  const statusRefreshing = data?.refreshing === true;
  const runboardHtml = agentStatusRunboard(active);
  if (agentStatusSummaryLoaded) componentHosts["agent-accounts"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-head">
        <div data-agent-status-runboard>${runboardHtml}</div>
        ${accountManagementActions(accounts.length > 0)}
        <button
          class="portalButton agent-status-refresh${statusRefreshing ? " is-refreshing" : ""}"
          type="button"
          data-agent-status-refresh
          data-portal-icon="refresh"
          aria-busy="${statusRefreshing ? "true" : "false"}"
          ${statusRefreshing ? "disabled" : ""}
        ><span data-agent-status-refresh-label>${statusRefreshing ? window.PortalI18n.t("刷新中…") : window.PortalI18n.t("刷新")}</span></button>
      </div>
      <div class="agent-status-limit-list">

        ${accounts.length
          ? accountRows(accounts)
          : accountEmptyState(data)}
      </div>
      ${refreshNote}
      ${data?.accounts_refresh_error || data?.accounts_error ? window.PortalI18n.html`<div class="agent-status-error" role="alert" data-account-refresh-error>Agent status unavailable: ${esc(data.accounts_refresh_error || data.accounts_error)}</div>` : ""}
  `;
  componentHosts["system-resources"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-resource-line">
        ${gpuResourceHtml(gpu)}
        ${diskResourceHtml(disk)}
      </div>
      ${gpuDetailsHtml(gpu)}
      ${diskDetailsHtml(disk)}
  `;
  renderScheduledTriggeredStatus(componentHosts["scheduled-triggered"]);
  const nextRunboard = componentHosts["agent-accounts"].querySelector("[data-agent-status-runboard]");
  if (currentRunboard && nextRunboard && agentStatusRunboardHtml === runboardHtml) {
    nextRunboard.replaceWith(currentRunboard);
  }
  restoreResidentIconFocus(componentHosts["agent-accounts"], focusedResident);
  agentStatusRunboardHtml = runboardHtml;
  const refreshButton = node.querySelector("[data-agent-status-refresh]");
  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      if (homeController.isComponentVisible("agent-accounts")) await loadAgentStatus(true);
      const reads = [];
      if (homeController.isComponentVisible("system-resources")) reads.push(loadGpuStatus(), loadDiskStatus());
      if (homeController.isComponentVisible("scheduled-triggered")) reads.push(loadScheduledTriggeredStatus());
      await Promise.allSettled(reads);
    });
  }
  syncAccountDetailsButtons();
  window.PortalButtonTooltips?.enhance?.(node);
  refreshAccountQuotaWindows();
  }

function activeAgentStatusNeedsRetry() {
  return activeAgentStatus?.ready === false
    || activeAgentStatus?.refreshing === true
    || Boolean(activeAgentStatus?.error)
    || activeAgentStatus?.status === "loading";
}

function activeAgentStatusNextDelay(_data, error) {
  if (error || _data?.error) {
    const delay = Math.min(
      ACTIVE_AGENT_STATUS_RETRY_BASE_MS * (2 ** activeAgentStatusRetryAttempt),
      ACTIVE_AGENT_STATUS_RETRY_MAX_MS,
    );
    activeAgentStatusRetryAttempt += 1;
    return delay;
  }
  activeAgentStatusRetryAttempt = 0;
  return activeAgentStatusNeedsRetry() ? ACTIVE_AGENT_STATUS_LOADING_RETRY_MS : null;
}

function renderActiveAgentStatus(active) {
  activeAgentStatus = active || {};
  bindActiveResidentStates(activeAgentStatus);
  if (!agentStatusData) return;
  const nextData = {
    ...agentStatusData,
    active_agents: activeAgentStatus,
  };
  const node = document.querySelector('[data-home-component="agent-status"]');
  const runboard = node?.querySelector?.("[data-agent-status-runboard]");
  if (!runboard) {
    renderAgentStatus(nextData);
    return;
  }
  agentStatusData = nextData;
  const nextHtml = agentStatusRunboard(nextData.active_agents);
  if (nextHtml === agentStatusRunboardHtml) return;
  const focusedResident = focusedResidentIcon(runboard);
  runboard.innerHTML = nextHtml;
  restoreResidentIconFocus(runboard, focusedResident);
  agentStatusRunboardHtml = nextHtml;
}

function bindActiveResidentStates(active) {
  const groups = [active, ...(Array.isArray(active.account_groups) ? active.account_groups : [])];
  const residentIds = [...new Set(groups.flatMap(group => [
    ...(Array.isArray(group.running_runs) ? group.running_runs : []),
    ...(Array.isArray(group.queued_runs) ? group.queued_runs : []),
  ]).filter(run => run?.type === "resident" && run.resident_id).map(run => run.resident_id))].sort();
  const signature = JSON.stringify([portalUsername, residentIds]);
  if (signature === activeResidentStateSignature) return;
  activeResidentStateSignature = signature;
  const previousCollection = activeResidentStateCollection;
  activeResidentStateCollection = null;
  if (!residentIds.length) {
    previousCollection?.destroy();
    return;
  }
  activeResidentStateCollection = window.PortalResidentProtocol.acquireStates(residentIds, () => {
    if (activeResidentStateFrame) return;
    activeResidentStateFrame = window.requestAnimationFrame(() => {
      activeResidentStateFrame = 0;
      if (activeAgentStatus) renderActiveAgentStatus(activeAgentStatus);
    });
  });
  previousCollection?.destroy();
}

function agentStatusNeedsFollowup(data = agentStatusData) {
  const accounts = executionAccounts(data);
  return !agentStatusSummaryLoaded || !data
    || data?.refreshing === true
    || data?.status === "loading"
    || accounts.some(account => account?.status === "loading");
}

function ensureAgentStatusRefresh() {
  if (agentStatusRefresh) return agentStatusRefresh;
  const fetchAgentStatus = ({ force = false } = {}) => {
    const path = force ? "/api/agent-status/refresh" : "/api/agent-status";
    return force
      ? homeApi.request(path, { method: "POST" })
      : homeApi.get(path);
  };
  agentStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-agent-status:${portalUsername || "anonymous"}`,
    // Quota percentages come from the local scheduler's 15-second samples;
    // slow provider balances keep their independent backend refresh policy.
    interval: () => agentStatusNeedsFollowup() ? 3000 : 30000,
    hiddenInterval: 300000,
    pauseWhenHidden: false,
    initialDelay: 30000,
    fetch: fetchAgentStatus,
    apply: data => {
      agentStatusSummaryLoaded = true;
      renderAgentStatus(data);
    },
      onError: err => {
        agentStatusSummaryLoaded = true;
        renderAgentStatus({...agentStatusData, accounts: staleExecutionAccounts(executionAccounts(agentStatusData)),
          refreshing: false, accounts_refresh_error: err?.message || String(err)});
      },
  });
  return agentStatusRefresh;
}

function ensureActiveAgentStatusRefresh() {
  if (activeAgentStatusRefresh) return activeAgentStatusRefresh;
  activeAgentStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-active-agents:${portalUsername || "anonymous"}`,
    interval: 300000,
    pauseWhenHidden: true,
    fetch: () => window.PortalResidentService.activeAgents(),
    apply: renderActiveAgentStatus,
    nextDelay: activeAgentStatusNextDelay,
    subscribe: window.PortalRefreshCoordinator.portalSubscription({
      domains: ["projection", "resident"],
      refreshOnAttach: false,
      debounceMs: 1500,
      matches: detail => {
        if (detail?.snapshot_required) return true;
        if (Array.isArray(detail?.resident_events) && detail.resident_events.length > 0) return true;
        const changes = detail?.projection_changes?.["active-agents"] || {};
        return changes && typeof changes === "object" && Object.keys(changes).length > 0;
      },
    }),
  });
  return activeAgentStatusRefresh;
}

async function loadAgentStatus(force = false) {
  const node = document.querySelector('[data-home-component="agent-status"]');
  const button = node?.querySelector?.("[data-agent-status-refresh]");
  if (button) {
    button.disabled = true;
    button.classList.add("is-refreshing");
    button.setAttribute("aria-busy", "true");
    const label = button.querySelector("[data-agent-status-refresh-label]");
    if (label) label.textContent = window.PortalI18n.t("刷新中…");
  }
  const loadingTarget = node?.querySelector?.('[data-home-component="agent-accounts"]');
  if (loadingTarget && !loadingTarget.textContent.trim()) {
    loadingTarget.innerHTML = window.PortalI18n.html`<div class="agent-status-muted">Loading agent status…</div>`;
  }
  const coordinator = ensureAgentStatusRefresh();
  const activeCoordinator = ensureActiveAgentStatusRefresh();
  coordinator.start();
  activeCoordinator.start();
  await Promise.allSettled([
    coordinator.refresh({ force }),
    activeCoordinator.refresh({ force }),
  ]);
}

async function loadGpuStatus({force = true, signal} = {}) {
  const previous = gpuStatusData || {};
  gpuStatusData = {...previous, status: "loading"};
  renderAgentStatus(agentStatusData);
  try {
    gpuStatusData = await window.PortalInstructionClient.execute("GetDeviceStatus", force ? {kind: "gpu", refresh: true} : {kind: "gpu"}, {signal});
    if (!signal?.aborted) renderAgentStatus(agentStatusData);
  } catch (err) {
    if (signal?.aborted) { gpuStatusData = previous; return; }
    const message = err?.message || String(err);
    const hasPrevious = Array.isArray(previous.servers) && previous.servers.length > 0;
    gpuStatusData = hasPrevious
      ? {...previous, stale: true, refresh_error: message}
      : {...previous, status: "error", error: message, servers: []};
    renderAgentStatus(agentStatusData);
  }
}

function ensureDiskStatusRefresh() {
  if (diskStatusRefresh) return diskStatusRefresh;
  const delay = () => diskStatusData?.refreshing ? 1000 : 300000;
  diskStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-disk-status:${portalUsername || "anonymous"}`,
    interval: delay,
    initialDelay: delay,
    pauseWhenHidden: true,
    fetch: ({ force, signal }) => window.PortalInstructionClient.execute("GetDeviceStatus", force ? { kind: "disk", refresh: true } : { kind: "disk" }, { signal }),
    apply: disk => {
      diskStatusData = disk;
      renderAgentStatus(agentStatusData);
    },
    onError: err => {
      const previous = diskStatusData || {};
      diskStatusData = {...previous, stale: true, refresh_error: err?.message || String(err)};
      renderAgentStatus(agentStatusData);
    },
  });
  return diskStatusRefresh;
}

async function loadDiskStatus() {
  diskStatusData = {...(diskStatusData || {}), refreshing: true};
  renderAgentStatus(agentStatusData);
  await ensureDiskStatusRefresh().refresh({ force: true });
}
function listen(target, type, listener, signal) {
  target?.addEventListener?.(type, listener, { signal });
}

function connectHomeShell({ context }) {
  // The shell owns only the current user; every panel reads its own data.
  context.currentUserReady = window.PortalUI.loadCurrentUser()
    .then(({ username, data }) => {
      portalUsername = username || "anonymous";
      canSetScheduler = data?.access?.permission_system === "open" || data?.access?.allows?.UpdateProviderAccount === true;
      canRegisterAccount = data?.access?.permission_system === "open" || data?.access?.allows?.RegisterProviderAccount === true;
      return { username: portalUsername, data };
    });
}

function connectDailyNews() {
  loadDailyNews();
}

function connectAgentAccess({ context, signal }) {
  context.currentUserReady.then(() => {
    if (!signal.aborted) return loadAgentAccess();
  }).catch(() => {
    if (signal.aborted) return;
    const node = document.querySelector("[data-agent-access]");
    if (node) node.innerHTML = window.PortalI18n.html`<div class="agent-access-error">当前用户读取失败，无法加载个人 Agent 入口。</div>`;
  });
}

function connectPulse({ context, signal }) {
  context.currentUserReady.catch(() => {}).then(() => {
    if (!signal.aborted) loadPortalPulseOnce();
  });
}

function connectAgentStatus({ context, signal }) {
  const destroyResidentMenu = bindAgentStatusResidentMenu(signal);
  context.currentUserReady.catch(() => {}).then(() => {
    if (!signal.aborted) loadAgentStatus(false);
  });
  return () => {
    destroyResidentMenu();
    destroyAccountDetails();
    agentStatusRefresh?.destroy?.();
    activeAgentStatusRefresh?.destroy?.();
    agentStatusRefresh = null;
    agentStatusSummaryLoaded = false;
    activeAgentStatusRefresh = null;
  };
}

function connectSystemResources({ context, signal }) {
  context.currentUserReady.catch(() => {}).then(() => {
    if (signal.aborted) return;
    const disk = ensureDiskStatusRefresh();
    disk.start();
    Promise.allSettled([loadGpuStatus({force: false, signal}), disk.refresh()]);
  });
  return () => {
    diskStatusRefresh?.destroy();
    diskStatusRefresh = null;
    diskStatusData = null;
  };
}

// Installed features add panels to the Home groups (web.home_panel, PortalHomeComponents.registerPanel);
// their order places them among the group's own panels.
function groupPanels(group, panels) {
  return [...panels, ...window.PortalHomeComponents.panels(group)].sort((left, right) => left.order - right.order);
}
const homeGroupPanels = {
  "get-me-aligned": groupPanels("get-me-aligned", [
    { id: "daily-news", order: 20, title: window.PortalI18n.t("Daily News"), icon: "file-text", source: '[data-home-component="daily-news"]', connect: connectDailyNews },
    { id: "agent-access", order: 30, title: window.PortalI18n.t("Agent Access"), icon: "users", source: '[data-home-component="agent-access"]', connect: connectAgentAccess },
  ]),
  "my-pulse-publish": groupPanels("my-pulse-publish", [
    { id: "knowledge-pulse", order: 10, title: window.PortalI18n.t("Knowledge Pulse"), icon: "pulse", source: '[data-home-component="knowledge-pulse"]', connect: connectPulse },
    { id: "personal-pulse", order: 20, title: window.PortalI18n.t("Personal Pulse"), icon: "user", source: '[data-home-component="personal-pulse"]', connect: connectPulse },
  ]),
  "agent-status": groupPanels("agent-status", [
    { id: "agent-accounts", order: 10, title: "AI Activity & Accounts", icon: "pulse", source: '[data-home-component="agent-accounts"]', connect: connectAgentStatus },
    { id: "system-resources", order: 20, title: window.PortalI18n.t("GPU / Disk Resources"), icon: "server", source: '[data-home-component="system-resources"]', connect: connectSystemResources },
    { id: "scheduled-triggered", order: 30, title: window.PortalI18n.t("定时与自动触发"), icon: "bot", source: '[data-home-component="scheduled-triggered"]', connect: connectScheduledTriggered },
    { id: "usage-attribution", order: 40, title: window.PortalI18n.t("Usage Attribution"), icon: "list", source: '[data-home-component="usage-attribution"]', connect: connectUsageAttribution },
  ]),
};
// The nested workbench that shows each group's panels.
const HOME_GROUP_WORKBENCHES = { "get-me-aligned": "home-get-me-aligned", "my-pulse-publish": "home-my-pulse", "agent-status": "home-agent-status" };
const groupComponents = group => homeGroupPanels[group].map(({ id, source, connect }) => ({ id, root: source, connect }));
const groupWorkbenchPanels = group => homeGroupPanels[group].map(({ id, title, icon, source }) => ({ id, title, icon, source }));
const groupPanelIds = group => homeGroupPanels[group].map(panel => panel.id);

const homeComponentTree = Object.freeze({
  id: "home-shell",
  root: "#homeRoot",
  loadPolicy: "eager",
  connect: connectHomeShell,
  children: [
    {
      id: "get-me-aligned",
      root: "#homeOverviewCard",
      children: groupComponents("get-me-aligned"),
    },
    {
      id: "my-pulse-publish",
      root: '[data-home-component="my-pulse-publish"]',
      children: groupComponents("my-pulse-publish"),
    },
    {
      id: "agent-status",
      root: '[data-home-component="agent-status"]',
      children: groupComponents("agent-status"),
    },
  ],
});

const { split: homeSplit, stack: homeStack } = window.PortalWorkbench.model;
const getMeAlignedLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["notice-board"]), homeStack(["daily-news"])], [1, 1]),
  homeStack(["agent-access"]),
], [3, 2]);
const myPulseLayout = homeSplit("vertical", [
  homeStack(["knowledge-pulse"]),
  homeStack(["personal-pulse"]),
], [1, 1]);
const agentStatusLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["agent-accounts"]), homeStack(["system-resources"])], [3, 2]),
  homeSplit("horizontal", [homeStack(["scheduled-triggered"]), homeStack(["usage-attribution"])], [1, 2]),
], [1, 1]);
const agentStatusAccountsUsageLayout = homeSplit("vertical", [
  homeStack(["agent-accounts"]),
  homeStack(["usage-attribution"]),
], [1, 2]);
const homeMainLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["get-me-aligned"]), homeStack(["my-pulse-publish"])], [2, 1]),
  homeStack(["agent-status"]),
], [5, 6]);
const homeVerticalLayout = homeSplit("vertical", [
  homeStack(["get-me-aligned"]),
  homeStack(["my-pulse-publish"]),
  homeStack(["agent-status"]),
], [1, 1, 2]);

const homeWorkbench = window.PortalHomeComponents.createWorkbenchComposition({
  workbenches: [
    {
      id: "home-get-me-aligned",
      root: "#getMeAlignedWorkbench",
      staging: "#getMeAlignedStaging",
      label: window.PortalI18n.t("Get Me Aligned"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("get-me-aligned"),
      defaultLayout: getMeAlignedLayout,
      presets: [
        { id: "overview", name: window.PortalI18n.t("概览"), layout: getMeAlignedLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("get-me-aligned")) },
      ],
      defaultPreset: "overview",
    },
    {
      id: "home-my-pulse",
      root: "#myPulseWorkbench",
      staging: "#myPulseStaging",
      label: window.PortalI18n.t("My Pulse / Publish"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("my-pulse-publish"),
      defaultLayout: myPulseLayout,
      presets: [
        { id: "stacked", name: window.PortalI18n.t("上下排列"), layout: myPulseLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("my-pulse-publish")) },
      ],
      defaultPreset: "stacked",
    },
    {
      id: "home-agent-status",
      contentSchemaVersion: 2,
      migrateState: window.PortalHomeComponents.migrateAgentStatusState,
      root: "#agentStatusWorkbench",
      staging: "#agentStatusStaging",
      label: window.PortalI18n.t("Agent Status"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("agent-status"),
      defaultLayout: agentStatusLayout,
      presets: [
        { id: "overview", name: window.PortalI18n.t("运行概览"), layout: agentStatusLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("agent-status")) },
      ],
      defaultPreset: "overview",
    },
    {
      id: "home",
      root: "#homeWorkbenchRoot",
      staging: "#homeWorkbenchStaging",
      label: window.PortalI18n.t("Home Workbench"),
      panels: [
        { id: "get-me-aligned", title: window.PortalI18n.t("Get Me Aligned"), icon: "semantic", source: '#homeOverviewCard', minHeight: 320 },
        { id: "my-pulse-publish", title: window.PortalI18n.t("My Pulse / Publish"), icon: "send", source: '[data-home-component="my-pulse-publish"]', minHeight: 240 },
        { id: "agent-status", title: window.PortalI18n.t("Agent Status"), icon: "pulse", source: '[data-home-component="agent-status"]', minHeight: 420 },
      ],
      defaultLayout: homeMainLayout,
      presets: [
        {
          id: "daily-overview",
          name: window.PortalI18n.t("每日概览"),
          layout: homeMainLayout,
          workbenches: {
            "home-get-me-aligned": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: homeSplit("horizontal", [homeStack(["notice-board"]), homeStack(["daily-news"])], [1, 1]),
              hidden: ["agent-access"],
            },
            "home-my-pulse": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: myPulseLayout,
            },
            "home-agent-status": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: homeSplit("vertical", [homeStack(["agent-accounts"]), homeStack(["scheduled-triggered"])], [1, 1]),
              hidden: ["system-resources", "usage-attribution"],
            },
          },
        },
        { id: "overview", name: window.PortalI18n.t("概览"), layout: homeMainLayout },
        { id: "vertical", name: window.PortalI18n.t("从上到下"), layout: homeVerticalLayout },
        {
          id: "accounts-usage",
          name: window.PortalI18n.t("账号与用量"),
          layout: homeStack(["agent-status"]),
          hidden: ["get-me-aligned", "my-pulse-publish"],
          workbenches: {
            "home-agent-status": {
              id: "accounts-usage",
              name: window.PortalI18n.t("账号与用量"),
              layout: agentStatusAccountsUsageLayout,
              hidden: ["system-resources", "scheduled-triggered"],
            },
          },
        },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(["get-me-aligned", "my-pulse-publish", "agent-status"]) },
      ],
      defaultPreset: "accounts-usage",
      mobilePrimaryPanelId: "get-me-aligned",
    },
  ],
});

const homeController = window.PortalHomeComponents.create({
  id: "home",
  root: "#homeRoot",
  context: {},
  ready: context => context.currentUserReady.catch(() => {}),
  tree: homeComponentTree,
});

window.PortalHome = Object.freeze({
  mount() {
    if (homeController.snapshot().mounted) return;
    homeWorkbench.mount();
    // An explicit deep link is an open request, even if the saved layout hides
    // its host. Ordinary visits never expand panels as a side effect of loading.
    for (const panel of window.PortalHomeComponents.panels().filter(item => item.requested)) {
      window.PortalWorkbench.get("home").focusPanel(panel.group);
      window.PortalWorkbench.get(HOME_GROUP_WORKBENCHES[panel.group]).focusPanel(panel.id);
    }
    if (homeRoute.scheduledTriggeredId) {
      window.PortalWorkbench.get("home").focusPanel("agent-status");
      window.PortalWorkbench.get("home-agent-status").focusPanel("scheduled-triggered");
    }
    homeController.mount();
  },
  destroy() {
    homeController.destroy();
    homeWorkbench.destroy();
  },
  snapshot: () => Object.freeze({
    ...homeController.snapshot(),
    ...homeWorkbench.snapshot(),
  }),
  tree: homeComponentTree,
});

setScheduledTriggeredTarget(homeRoute.scheduledTriggeredId);
window.PortalHome.mount();
window.addEventListener("pagehide", () => window.PortalHome.destroy());
window.addEventListener("pageshow", event => {
  if (event.persisted) window.PortalHome.mount();
});
