(() => {
  if (window.PortalHomeAccounts) return;

  // The execution accounts on Home: quota windows, balance and credits, the
  // Resident details popover and registering an account.
  const DEPENDENCIES = Object.freeze([
    "currentActiveAgentStatusRefresh", "currentAgentStatusData", "currentCanRegisterAccount",
    "currentCanSetScheduler", "esc", "formatDateTime", "homeApi", "loadAgentStatus",
    "renderAgentStatus",
  ]);

  function createAccounts(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeAccounts is missing: ${missing.join(", ")}`);
    const {
      currentActiveAgentStatusRefresh, currentAgentStatusData, currentCanRegisterAccount,
      currentCanSetScheduler, esc, formatDateTime, homeApi, loadAgentStatus, renderAgentStatus,
    } = dependencies;

    let accountRegistrationPending = false;

    function rateLimitWindowLabel(quotaWindow, fallbackName = "Limit") {
      const minutes = Math.max(0, Number(quotaWindow?.window_duration_mins || 0));
      if (minutes === 7 * 24 * 60) return "Weekly";
      if (minutes && minutes % (24 * 60) === 0) return window.PortalI18n.text`${minutes / (24 * 60)}d`;
      if (minutes && minutes % 60 === 0) return window.PortalI18n.text`${minutes / 60}h`;
      if (minutes) return window.PortalI18n.text`${minutes}m`;
      return fallbackName;
    }

    function executionAccounts(data) {
      return Array.isArray(data?.accounts) ? data.accounts : [];
    }

    // A reading stays current until a read of it fails; age never expires it.
    function accountQuotaWindowIsStale(window) {
      return Boolean(window?.stale);
    }

    // A failed summary read leaves the shown readings in place, marked stale.
    function staleExecutionAccounts(accounts) {
      return accounts.map(account => ({
        ...account,
        rate_limits: (account.rate_limits || []).map(window => ({...window, stale: true})),
        ...(account.balance ? {balance: {...account.balance, stale: true}} : {}),
        ...(account.credits ? {credits: {...account.credits, stale: true}} : {}),
      }));
    }

    function accountQuotaWindows(account) {
      const windows = Array.isArray(account.rate_limits) ? account.rate_limits : [];
      const quota = account.quota_reserve || {};
      if (quota.status === "reserved" && quota.remaining_percent === 0
          && (!windows.length || windows.every(window => accountQuotaWindowIsStale(window)))) {
        return (quota.windows || []).filter(quotaWindow => quotaWindow.remaining_percent === 0).map(quotaWindow => ({
          id: quotaWindow.window, label: rateLimitWindowLabel(quotaWindow, window.PortalI18n.t("额度")), remaining_percent: 0, stale: true, resets_at: quotaWindow.resets_at,
        }));
      }
      return windows;
    }

    function accountSummaryQuotaWindows(account) {
      const windows = accountQuotaWindows(account);
      const summary = account.provider === "kimi" && account.kind === "subscription_login" ? [
        ["limit5h", window.PortalI18n.t("5 小时")],
        ["limit7d", window.PortalI18n.t("每周")],
      ] : account.provider === "antigravity" ? [
        ["Gemini Models/Five Hour Limit Remaining", window.PortalI18n.t("5 小时")],
        ["Gemini Models/Weekly Limit Remaining", window.PortalI18n.t("每周")],
      ] : null;
      // Home keeps the two shared rolling windows; monthly and model-specific
      // limits remain in the full details, including after an in-place refresh.
      if (!summary) return windows;
      return summary.flatMap(([id, label]) => {
        const quotaWindow = windows.find(item => item.id === id);
        return quotaWindow ? [{...quotaWindow, full_label: quotaWindow.label, label}] : [];
      });
    }

    // Refresh the selected Home windows and the complete dialog windows in place.
    function refreshAccountQuotaWindows() {
      const accounts = new Map(executionAccounts(currentAgentStatusData()).map(account => [account.account_id, account]));
      for (const node of document.querySelectorAll("[data-account-quota-account]")) {
        const account = accounts.get(node.dataset.accountQuotaAccount);
        const windows = account && (node.closest("[data-account-limit-account]")
          ? accountSummaryQuotaWindows(account) : accountQuotaWindows(account));
        const quotaWindow = windows && windows.find(window => window.id === node.dataset.accountQuotaWindow);
        if (quotaWindow) node.outerHTML = accountQuotaWindow(account, quotaWindow);
        else if (account) node.remove();
      }
    }

    function accountQuotaWindow(account, quotaWindow) {
      const value = quotaWindow?.remaining_percent;
      const known = typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
      const remaining = known ? Number(value.toFixed(2)) : null;
      const stale = accountQuotaWindowIsStale(quotaWindow);
      const label = window.PortalI18n.t(String(quotaWindow?.label || quotaWindow?.id || "额度"));
      const reset = quotaWindow?.resets_at ? formatDateTime(quotaWindow.resets_at * 1000) : "";
      const observed = quotaWindow?.observed_at;
      const sampled = formatDateTime(typeof observed === "number" ? observed * 1000 : observed || "");
      const title = [quotaWindow.full_label || label, stale ? window.PortalI18n.t("数据已过期，百分比为上次采样") : !known ? window.PortalI18n.t("额度数据不可用") : window.PortalI18n.t("剩余额度"),
        sampled && window.PortalI18n.text`采样于 ${sampled}`, reset && window.PortalI18n.text`重置 ${reset}`, account?.error].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<div class="agent-status-account-window" data-account-quota-account="${esc(account.account_id)}" data-account-quota-window="${esc(quotaWindow.id || "")}" title="${esc(title)}">
    <span class="agent-status-window-name">${esc(label)}</span>
    <span class="agent-status-limit-track${!known ? " is-unknown" : stale ? " is-stale" : ""}" ${known && !stale ? `role="meter" aria-label="${esc(window.PortalI18n.text`${account.account_id} ${label}剩余额度`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining}"` : `role="img" aria-label="${esc(title)}"`}>
      ${known ? window.PortalI18n.html`<span class="agent-status-limit-fill" style="width:${remaining}%"></span>` : ""}
    </span><span class="agent-status-limit-meta">${known ? `${remaining}%${stale ? "*" : ""}` : "—"}</span>
  </div>`;
    }

    function accountBalanceHtml(account) {
      const balance = account.balance;
      if (!balance) return "";
      const fresh = balance.status === "ok" && !balance.stale;
      const available = balance.metric_kind === "availability";
      const hasValue = balance.display !== undefined && balance.display !== null && String(balance.display) !== "";
      const missing = balance.status === "not_supported" || balance.reason === "unsupported_endpoint"
        ? "此连接未提供余额查询" : balance.reason === "unauthorized" ? "余额查询未获授权"
        : balance.reason === "invalid_response" ? "余额返回格式无法识别" : "余额暂不可用";
      const explanation = available ? (balance.status === "ok" ? "API 可用 · 余额未提供" : "API 状态不可用 · 余额未提供")
        : balance.status === "loading" ? "正在读取余额…" : hasValue ? (fresh ? "剩余余额" : "数据已过期，数值为上次采样") : missing;
      const value = !available && hasValue ? `${balance.display}${fresh ? "" : "*"}` : "—";
      const sampled = formatDateTime(balance.updated_at || "");
      const title = [window.PortalI18n.t(explanation), balance.source_account_id && window.PortalI18n.text`计费账号 ${balance.source_account_id}`,
        sampled && window.PortalI18n.text`更新于 ${sampled}`, balance.error].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<span class="agent-status-account-balance${balance.stale ? " is-stale" : ""}" data-account-balance title="${esc(title)}"><span class="agent-status-balance-value${available ? " is-availability" : ""}">${esc(value)}</span></span>`;
    }

    function accountRows(accounts) {
      const subscriptions = [], apis = [];
      for (const account of accounts) {
        const subscription = account.kind === "subscription_login" || (!account.kind && (account.rate_limits?.length
          || ["codex", "claude", "antigravity"].includes(account.provider)));
        (subscription ? subscriptions : apis).push(account);
      }
      return subscriptions.map(accountLimitRow).join("") + (apis.length ? window.PortalI18n.html`<div class="agent-status-api-list">${apis.map(accountApiRow).join("")}</div>` : "");
    }

    function accountApiRow(account) {
      const id = String(account.account_id || "");
      return window.PortalI18n.html`<div class="agent-status-api-account" data-account-api="${esc(id)}">
        <span class="agent-status-account-name" title="${esc(id)}">${esc(id)}</span>
        ${accountBalanceHtml(account) || window.PortalI18n.html`<span class="agent-status-muted" title="${window.PortalI18n.t("余额暂不可用")}">—</span>`}
        ${account.capabilities?.resident_details === false ? "" : window.PortalI18n.html`<button class="portalButton is-compact agent-status-account-details" type="button" data-account-details="${esc(id)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="account-details">详情</button>`}
      </div>`;
    }

    function accountLimitRow(account) {
      const accountId = String(account.account_id || "");
      const windows = accountSummaryQuotaWindows(account);
      const quota = account.quota_reserve || {};
      const reserved = quota.status === "reserved";
      const blocked = (quota.windows || []).filter(window => typeof window.remaining_percent === "number" && window.remaining_percent <= quota.reserve_percent);
      const recovery = blocked.length && blocked.every(window => window.resets_at > 0) ? Math.max(...blocked.map(window => window.resets_at)) * 1000 : 0;
      const recoveryPending = recovery > 0 && recovery <= Date.now();
      const authenticationFailed = account.error_code === "provider_authentication_failed";
      const authenticationLabel = authenticationFailed ? window.PortalI18n.t('<span class="agent-status-muted" data-account-status-error>登录凭据失效</span>') : "";
      const content = authenticationLabel + (windows.length ? windows.map(window => accountQuotaWindow(account, window)).join("")
        : authenticationFailed ? "" : account.balance ? accountBalanceHtml(account) : window.PortalI18n.html`<span class="agent-status-muted"${reserved ? "" : " data-account-limit-stale"}>${esc(account.status === "loading" ? window.PortalI18n.t("正在读取额度…") : account.error || window.PortalI18n.t("额度数据不可用"))}</span>`);
      const resets = windows.filter(w => w.resets_at > Date.now() / 1000).map(w => w.resets_at);
      const refreshLabel = authenticationFailed ? window.PortalI18n.t("请重新登录该账号") : account.balance && !windows.length ? (account.balance.updated_at ? window.PortalI18n.text`更新 ${formatDateTime(account.balance.updated_at)}` : account.balance.status === "loading" ? window.PortalI18n.t("正在刷新余额…") : "") : reserved ? recovery > Date.now() ? window.PortalI18n.text`预计恢复 ${formatDateTime(recovery)}` : window.PortalI18n.t("等待额度恢复确认") : account.refreshing ? window.PortalI18n.t("正在刷新用量…") : account.error ? window.PortalI18n.t("用量刷新失败")
        : resets.length ? window.PortalI18n.text`预计刷新 ${formatDateTime(Math.min(...resets) * 1000)}` : window.PortalI18n.t("等待用量刷新");
      return window.PortalI18n.html`<div class="agent-status-account-limit" data-account-limit-account="${esc(accountId)}">
    <span class="agent-status-account-name">${esc(accountId)}
      ${account.enabled === false ? window.PortalI18n.html`<small>已停用</small>` : ""}
      ${account.login_state === "login_expired" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-account-relogin="${esc(accountId)}" ${currentCanSetScheduler() ? "" : "disabled"}>重新登录</button>` : ""}
    </span>
    <div class="agent-status-account-windows">
      <div class="agent-status-account-rate-windows">${content}</div>
      <button class="portalButton is-compact agent-status-account-details" type="button" data-account-details="${esc(accountId)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="account-details">详情</button>

      <span class="agent-status-account-refresh">${reserved && quota.remaining_percent !== 0 ? window.PortalI18n.html`<span data-account-limit-stale>${recoveryPending ? window.PortalI18n.t("额度恢复待确认") : window.PortalI18n.t("额度不足，等待恢复")}</span> · ` : ""}${esc(refreshLabel)}</span>
    </div></div>`;
    }

    const accountIntakePending = new Map();
    let accountDetails = null;

    function accountManagementActions(hasAccounts) {
      return window.PortalI18n.html`
    ${currentCanSetScheduler() || currentCanRegisterAccount() ? window.PortalI18n.html`<button class="portalButton" type="button" data-account-manage="">管理 AI 连接</button>` : ""}
    ${hasAccounts && currentCanRegisterAccount() ? window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-account-register ${accountRegistrationPending ? "disabled" : ""}>新增 AI 账号</button>` : ""}`;
    }

    function accountEmptyState(data) {
      if (data?.status === "loading" || data?.refreshing === true) {
        return window.PortalI18n.html`<div class="agent-status-muted">Loading limits…</div>`;
      }
      const message = data?.accounts_error || data?.accounts_refresh_error
        ? window.PortalI18n.t("Limit data unavailable.") : window.PortalI18n.t("尚未添加 AI 账号。");
      return window.PortalI18n.html`<div class="agent-status-empty agent-status-muted"><p>${esc(message)}</p>
    ${currentCanRegisterAccount() ? window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-account-register ${accountRegistrationPending ? "disabled" : ""}>新增 AI 账号</button>` : ""}</div>`;
    }

    async function openAccountRegistration(button) {
      if (!currentCanRegisterAccount() || accountRegistrationPending) return;
      accountRegistrationPending = true;
      button.disabled = true;
      try {
        await window.PortalNav.openPermissionManagement({view: "backend", createAccount: true});
      } catch (error) { window.PortalUI.toast(error.message, "error"); }
      finally {
        accountRegistrationPending = false;
        document.querySelectorAll("[data-account-register]").forEach(el => { el.disabled = !currentCanRegisterAccount(); });
      }
    }

    function syncAccountDetailsButtons() {
      for (const button of document.querySelectorAll("[data-account-register]:not([data-backend-bound])")) {
        button.dataset.backendBound = "true";
        button.addEventListener("click", () => void openAccountRegistration(button));
      }
      for (const link of document.querySelectorAll("[data-account-manage]:not([data-backend-bound])")) {
        link.dataset.backendBound = "true";
        link.addEventListener("click", async event => {
          if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          accountDetails?.popover.close();
          try { await window.PortalNav.openPermissionManagement({view: "backend", accountId: link.dataset.accountManage}); }
          catch (error) { window.PortalUI.toast(error.message, "error"); }
        });
      }
      for (const button of document.querySelectorAll("[data-account-relogin]:not([data-backend-bound])")) {
        button.dataset.backendBound = "true";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try { (await import("/static/portal_management_backend.js")).openLogin(button.dataset.accountRelogin); }
          catch (error) { window.PortalUI.toast(error.message, "error"); }
          finally { button.disabled = !currentCanSetScheduler(); }
        });
      }
      for (const button of document.querySelectorAll("[data-account-details]")) {
        if (!button.dataset.detailsBound) {
          button.dataset.detailsBound = "true";
          button.addEventListener("click", () => openAccountDetails(button));
        }
        const active = accountDetails?.popover.isOpen() && accountDetails.accountId === button.dataset.accountDetails;
        button.setAttribute("aria-expanded", String(Boolean(active)));
        if (active) accountDetails.trigger = button;
      }
    }

    function ensureAccountDetails() {
      if (accountDetails) return accountDetails;
      const panel = document.createElement("section");
      panel.id = "account-details";
      panel.className = "portalDetailSurface account-details";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-label", window.PortalI18n.t("详情"));
      panel.tabIndex = -1;
      document.body.appendChild(panel);
      const details = { panel, accountId: "", snapshot: null, loading: false, error: "", result: "", requestVersion: 0, trigger: null };
      details.popover = window.PortalOverlay.bindFloatingPopover({
        panel, placement: "bottom", align: "start",
        onClose: event => {
          details.requestVersion += 1;
          details.abort?.abort();
          details.trigger?.setAttribute("aria-expanded", "false");
          if ((event?.key === "Escape" || panel.contains(event?.target)) && details.trigger?.isConnected) details.trigger.focus({ preventScroll: true });
        },
      });
      accountDetails = details;
      return details;
    }

    function destroyAccountDetails() {
      accountDetails?.popover.destroy();
      accountDetails?.panel.remove();
      accountDetails = null;
    }

    function openAccountDetails(button) {
      const details = ensureAccountDetails();
      const accountId = String(button.dataset.accountDetails || "");
      if (details.popover.isOpen() && details.accountId === accountId) {
        details.popover.close();
        return;
      }
      details.trigger?.setAttribute("aria-expanded", "false");
      details.panel.replaceChildren();
      Object.assign(details, { accountId, trigger: button, snapshot: null, error: "", result: "", moreExpanded: false });
      const bounds = button.getBoundingClientRect();
      details.popover.open({ x: bounds.left, y: bounds.bottom });
      syncAccountDetailsButtons();
      loadAccountDetails(details);
      details.panel.focus({ preventScroll: true });
    }

    async function loadAccountDetails(details) {
      const accountId = details.accountId;
      const version = ++details.requestVersion;
      details.abort?.abort();
      details.abort = new AbortController();
      if (executionAccounts(currentAgentStatusData()).find(item => item.account_id === accountId)?.capabilities?.resident_details === false) {
        details.loading = false;
        details.error = "";
        details.snapshot = null;
        renderAccountDetails();
        return;
      }
      details.loading = true;
      details.error = "";
      renderAccountDetails();
      try {
        const snapshot = await homeApi.get(`/api/agent-status/account-residents?account_id=${encodeURIComponent(accountId)}`, { signal: details.abort.signal });
        if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
        if (snapshot?.account_id !== accountId) throw new Error("账号详情响应不匹配，请刷新重试");
        details.snapshot = snapshot;
      } catch (error) {
        if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
        details.snapshot = null;
        details.error = window.PortalUI.errorMessage(window.PortalI18n.t("读取账号详情"), error);
      } finally {
        if (version === details.requestVersion && accountDetails === details && details.popover.isOpen()) {
          details.loading = false;
          renderAccountDetails();
        }
      }
    }

    function renderAccountDetails() {
      const details = accountDetails;
      if (!details?.popover.isOpen()) return;
      details.moreExpanded = details.panel.querySelector(".account-details-more")?.open ?? details.moreExpanded;
      const scrollTop = details.panel.querySelector(".account-details-body")?.scrollTop || 0;
      const snapshot = details.snapshot;
      const pendingAction = accountIntakePending.get(details.accountId);
      const busy = Boolean(pendingAction);
      const metric = (name, label, count) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd data-account-metric="${esc(name)}">${Number(count || 0)}</dd></div>`;
      let body = details.loading ? window.PortalI18n.markup('<p role="status">正在读取账号节点状态…</p>') : details.error ? window.PortalI18n.html`<p role="alert">${esc(details.error)}</p>` : "";
      if (snapshot && !details.loading && !details.error) {
        body = window.PortalI18n.html`<p class="account-details-note">${window.PortalI18n.t("统计范围：你可见的 Resident")}</p>
      <dl class="account-details-metrics">${metric("total", window.PortalI18n.t("节点总数"), snapshot.total)}${metric("queued_messages", window.PortalI18n.t("待发消息"), snapshot.queued_messages)}</dl>
      <h4>接单状态</h4><dl class="account-details-metrics is-intake">${metric("open", window.PortalI18n.t("Open"), snapshot.intake?.open)}${metric("closed", "Closed", snapshot.intake?.closed)}${snapshot.intake?.unknown ? metric("intake_unknown", window.PortalI18n.t("状态未知"), snapshot.intake.unknown) : ""}</dl>
      <h4>工作状态</h4><dl class="account-details-metrics is-work">${metric("running", window.PortalI18n.t("运行中"), snapshot.work?.running)}${metric("queued", window.PortalI18n.t("排队中"), snapshot.work?.queued)}${metric("idle", window.PortalI18n.t("空闲"), snapshot.work?.idle)}${metric("stopping", window.PortalI18n.t("停止中"), snapshot.work?.stopping)}${snapshot.work?.unknown ? metric("work_unknown", window.PortalI18n.t("状态未知"), snapshot.work.unknown) : ""}</dl>
      <details class="account-details-more" ${details.moreExpanded ? "open" : ""}><summary data-account-action="more">补充统计</summary>
        <p class="account-details-note">${esc(formatDateTime(snapshot.observed_at) || "")}<br>未创建 Session ${Number(snapshot.without_session || 0)} · 人工额度放行 ${Number(snapshot.quota_exempt || 0)}<br>最近输入失败 ${Number(snapshot.latest_input?.failed || 0)} · 已取消 ${Number(snapshot.latest_input?.cancelled || 0)}</p>
      </details>`;
      }
      const account = executionAccounts(currentAgentStatusData()).find(item => item.account_id === details.accountId);
      const focusedElement = details.panel.contains(document.activeElement) ? document.activeElement : null;
      const focused = focusedElement?.dataset.accountAction;
      details.panel.innerHTML = window.PortalI18n.html`<header><strong>${esc(details.accountId)} · ${window.PortalI18n.t("详情")}</strong><button class="portalButton is-compact" type="button" data-account-action="close" aria-label="关闭账号详情">×</button></header>
    <div class="account-details-body">${body}${details.result ? window.PortalI18n.html`<p role="status" data-account-result>${esc(details.result)}</p>` : ""}</div>
    <footer><div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="refresh" ${details.loading || busy ? "disabled" : ""}>刷新</button>
      </div>
      ${account?.capabilities?.resident_details === false ? "" : window.PortalI18n.html`<div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="close-all" data-account-close-residents="${esc(details.accountId)}" ${!snapshot?.open_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下 Resident 设为手动 Closed，保留队列与正在执行的任务">${pendingAction === "close" ? window.PortalI18n.t("关闭中…") : window.PortalI18n.t("全部关闭")}</button>
      <button class="portalButton is-compact" type="button" data-account-action="open-all" data-account-open-residents="${esc(details.accountId)}" ${!snapshot?.closed_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下全部关闭的 Resident 按人工 Open 打开">${pendingAction === "open" ? window.PortalI18n.t("打开中…") : window.PortalI18n.t("全部打开")}</button></div>`}</footer>`;
      details.panel.querySelector('[data-account-action="close"]').addEventListener("click", event => details.popover.close(event));
      details.panel.querySelector('[data-account-action="refresh"]').addEventListener("click", () => { loadAgentStatus(true); loadAccountDetails(details); });
      details.panel.querySelector('[data-account-open-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "open"));
      details.panel.querySelector('[data-account-close-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "close"));
      details.panel.querySelector(".account-details-more")?.addEventListener("toggle", event => {
        if (!event.currentTarget.isConnected) return;
        details.moreExpanded = event.currentTarget.open;
        details.popover.reposition();
      });
      details.panel.querySelector(".account-details-body").scrollTop = scrollTop;
      syncAccountDetailsButtons();
      if (focused) details.panel.querySelector(`[data-account-action="${focused}"]:not(:disabled)`)?.focus({ preventScroll: true });
      details.popover.reposition();
    }

    async function changeAccountResidentsIntake(button, action) {
      const opening = action === "open";
      const label = opening ? window.PortalI18n.t("打开") : window.PortalI18n.t("关闭");
      const accountId = String((opening ? button.dataset.accountOpenResidents : button.dataset.accountCloseResidents) || "");
      // The visible Residents not yet in the target state; each is checked on its own resident_id.
      const snapshot = accountDetails?.accountId === accountId ? accountDetails.snapshot : null;
      const residentIds = [...((opening ? snapshot?.closed_resident_ids : snapshot?.open_resident_ids) || [])];
      if (!accountId || !residentIds.length || accountIntakePending.has(accountId)) return;
      accountIntakePending.set(accountId, action);
      renderAccountDetails();
      try {
        const result = await window.PortalInstructionClient.execute("OpenIntake", { resident_ids: residentIds, open: opening }, {
          idempotencyKey: `account-intake:${accountId}:${globalThis.PortalUIPrimitives.randomUUID()}`,
        });
        const changed = Number(result.changed_count || 0);
        const unchanged = Number(result.unchanged_count || 0);
        const failed = Number(result.failed_count || 0);
        const effect = window.PortalI18n.t(opening ? "待发消息将继续调度，已取消的执行不会重放。" : "待发消息保留在队列中，正在执行的任务继续。");
        const message = changed
          ? window.PortalI18n.text`${accountId} 已${label} ${changed} 个 Resident；${effect}`
          : failed ? window.PortalI18n.text`${accountId} 本次未${label}任何节点。`
          : window.PortalI18n.text`${accountId} 当前没有需要${label}的节点。`;
        const feedback = [message,
          unchanged ? window.PortalI18n.text`${unchanged} 个节点本来就已${label}。` : "",
          failed ? window.PortalI18n.text`${failed} 个节点${label}失败，可重试。` : "",
          result.quota_override_pending ? window.PortalI18n.t("人工额度放行设置尚待同步，系统将自动重试。") : ""].filter(Boolean).join(" ");
        if (accountDetails?.accountId === accountId) accountDetails.result = feedback;
        window.PortalUI.toast(feedback, failed ? "error" : "success");
      } catch (err) {
        const feedback = window.PortalUI.errorMessage(window.PortalI18n.text`${label}账号 Resident`, err);
        if (accountDetails?.accountId === accountId) accountDetails.result = feedback;
        window.PortalUI.toast(feedback, "error");
      } finally {
        accountIntakePending.delete(accountId);
        if (accountDetails?.accountId === accountId && accountDetails.popover.isOpen()) loadAccountDetails(accountDetails);
        currentActiveAgentStatusRefresh()?.refresh({ force: true }).catch(() => {});
      }
    }

    return Object.freeze({
      executionAccounts, staleExecutionAccounts, refreshAccountQuotaWindows, accountRows,
      accountManagementActions, accountEmptyState, syncAccountDetailsButtons, destroyAccountDetails,
    });
  }

  window.PortalHomeAccounts = Object.freeze({ createAccounts, dependencies: DEPENDENCIES });
})();
