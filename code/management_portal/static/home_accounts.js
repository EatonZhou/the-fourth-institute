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
        ["Gemini 4 Argon/Gemini 4 Argon Weekly Limit", "Argon"],
      ] : null;
      // Keep the shared rolling windows compact. Omitted exhausted limits are
      // reported separately so these bars do not imply every model is available.
      if (!summary) return windows;
      return summary.flatMap(([id, label]) => {
        const quotaWindow = windows.find(item => item.id === id);
        return quotaWindow ? [{...quotaWindow, full_label: quotaWindow.label, label}] : [];
      });
    }

    function accountQuotaWarning(account, expanded = false) {
      if (!account) return "";
      const shown = new Set(accountSummaryQuotaWindows(account).map(item => item.id));
      const exhausted = accountQuotaWindows(account).filter(item => !shown.has(item.id) && item.remaining_percent === 0);
      if (!exhausted.length) return "";
      const stale = exhausted.every(accountQuotaWindowIsStale);
      const label = stale ? window.PortalI18n.t("其他额度待确认") : window.PortalI18n.t("其他额度已耗尽");
      const explanation = window.PortalI18n.t("摘要只显示部分额度，模型专属或其他周期的限额仍可能阻止调用。");
      const readings = exhausted.map(item => {
        const sampled = typeof item.observed_at === "number" ? item.observed_at * 1000 : item.observed_at;
        return [item.label || item.id, accountQuotaWindowIsStale(item) ? window.PortalI18n.t("剩余 0%*，上次读数，待刷新确认") : window.PortalI18n.t("剩余 0%"),
          sampled && window.PortalI18n.text`采样于 ${formatDateTime(sampled)}`,
          item.resets_at && window.PortalI18n.text`预计重置 ${formatDateTime(item.resets_at * 1000)}`].filter(Boolean).join(" · ");
      });
      if (expanded) return window.PortalI18n.html`<div class="account-details-quota-warning" data-account-quota-warning><strong>${esc(label)}</strong><p>${esc(explanation)}</p><ul>${readings.map(reading => `<li>${esc(reading)}</li>`).join("")}</ul></div>`;
      return window.PortalI18n.html`<span data-account-quota-warning title="${esc([explanation, ...readings].join("\n"))}">${esc(label)} · ${window.PortalI18n.t("见详情")}</span>`;
    }

    // Refresh quota readings, status and an open details dialog together.
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
      for (const node of document.querySelectorAll("[data-account-limit-status]")) {
        const account = accounts.get(node.dataset.accountLimitStatus);
        if (account) node.innerHTML = accountLimitStatus(account);
      }
      renderAccountDetails();
    }

    function accountQuotaWindow(account, quotaWindow) {
      const value = quotaWindow?.remaining_percent;
      const known = typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
      const remaining = known ? value : null;
      // Use compact, consistent row labels without rounding a nonempty window
      // to exhausted (or a partly used window to full). Keep the exact sample
      // in the tooltip, meter value and bar width.
      const percentage = !known ? "—" : remaining > 0 && remaining < 1 ? "<1%"
        : remaining > 99 && remaining < 100 ? ">99%" : `${Math.round(remaining)}%`;
      const stale = accountQuotaWindowIsStale(quotaWindow);
      const label = window.PortalI18n.t(String(quotaWindow?.label || quotaWindow?.id || "额度"));
      const reset = quotaWindow?.resets_at ? formatDateTime(quotaWindow.resets_at * 1000) : "";
      const observed = quotaWindow?.observed_at;
      const sampled = formatDateTime(typeof observed === "number" ? observed * 1000 : observed || "");
      const title = [quotaWindow.full_label || label, stale ? window.PortalI18n.t("数据已过期，百分比为上次采样") : !known ? window.PortalI18n.t("额度数据不可用") : window.PortalI18n.t("剩余额度"),
        known && window.PortalI18n.text`本窗口剩余 ${remaining}%${stale ? "*" : ""}`,
        sampled && window.PortalI18n.text`采样于 ${sampled}`, reset && window.PortalI18n.text`重置 ${reset}`, account?.error].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<div class="agent-status-account-window" data-account-quota-account="${esc(account.account_id)}" data-account-quota-window="${esc(quotaWindow.id || "")}" title="${esc(title)}">
    <span class="agent-status-window-name">${esc(label)}</span>
    <span class="agent-status-limit-track${!known ? " is-unknown" : stale ? " is-stale" : ""}" ${known && !stale ? `role="meter" aria-label="${esc(window.PortalI18n.text`${account.account_id} ${label}剩余额度`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining}"` : `role="img" aria-label="${esc(title)}"`}>
      ${known ? window.PortalI18n.html`<span class="agent-status-limit-fill" style="width:${remaining}%"></span>` : ""}
    </span><span class="agent-status-limit-meta">${known ? `${esc(percentage)}${stale ? "*" : ""}` : "—"}</span>
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
      const amount = balance.comparison_amount;
      const coinCount = !available && hasValue && balance.comparison_currency === "CNY"
        && typeof amount === "number" && Number.isFinite(amount) && amount > 0 ? Math.round(amount / 10) : 0;
      const shownCoins = Math.min(24, coinCount);
      const coins = shownCoins ? window.PortalI18n.html`<span class="agent-status-balance-visual${!fresh || balance.comparison_stale ? " is-stale" : ""}" aria-hidden="true"><span class="agent-status-balance-coins" style="width:${15 + 10 * (shownCoins - 1)}px">${Array.from({length: shownCoins}, (_, index) => `<i style="--coin-position:${shownCoins > 1 ? index / (shownCoins - 1) : 0}"></i>`).join("")}</span>${coinCount > shownCoins ? `<span class="agent-status-coins-more">+${coinCount - shownCoins}</span>` : ""}</span>` : "";
      const sampled = formatDateTime(balance.updated_at || "");
      const title = [window.PortalI18n.t(explanation), balance.source_account_id && window.PortalI18n.text`计费账号 ${balance.source_account_id}`,
        shownCoins && window.PortalI18n.t("约每 ¥10 一枚金币，四舍五入"),
        shownCoins && balance.currency !== "CNY" && window.PortalI18n.text`约 ${amount.toFixed(2)} CNY · 汇率 ${balance.exchange_rate_date || ""}`,
        sampled && window.PortalI18n.text`更新于 ${sampled}`, balance.error].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<span class="agent-status-account-balance${balance.stale ? " is-stale" : ""}" data-account-balance title="${esc(title)}"><span class="agent-status-balance-value${available ? " is-availability" : ""}">${esc(value)}</span>${coins}</span>`;
    }

    function accountCreditsHtml(account) {
      const credits = account?.credits;
      if (!credits || typeof credits !== "object") return "";
      const known = typeof credits.balance === "number" && Number.isFinite(credits.balance);
      const stale = Boolean(credits.stale) || account.status === "unavailable";
      const value = credits.unlimited ? window.PortalI18n.t("不限")
        : known ? credits.balance.toLocaleString(undefined, {maximumFractionDigits: 2}) : "—";
      const observed = credits.observed_at;
      const sampled = formatDateTime(typeof observed === "number" ? observed * 1000 : observed || "");
      const title = [stale && window.PortalI18n.t("数据已过期，数值为上次采样"),
        sampled && window.PortalI18n.text`采样于 ${sampled}`, account.error].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<dl class="account-details-metrics" data-account-credits title="${esc(title)}"><div><dt>Credits</dt><dd>${esc(value)}${stale && (known || credits.unlimited) ? "*" : ""}</dd></div></dl>`;
    }

    function accountSubscriptionHtml(account) {
      if (account?.kind !== "subscription_login") return "";
      const t = window.PortalI18n.t;
      const subscription = account.subscription || {};
      const tier = subscription.tier || {};
      const stale = subscription.stale || (subscription.observed_at && Date.now() / 1000 - subscription.observed_at > 86400);
      const tierTitle = [subscription.source, subscription.observed_at && formatDateTime(subscription.observed_at * 1000),
        stale && t("数据已过期，数值为上次采样")].filter(Boolean).join(" · ");
      const cards = account.refresh_cards || {};
      const count = Number.isInteger(cards.available_count) ? cards.available_count : "—";
      const date = value => value == null ? t("未提供") : formatDateTime(typeof value === "number" ? value * 1000 : value);
      const lines = (cards.cards || []).map(card => [card.title || t("刷新卡"), card.status,
        `${t("有效期至")}: ${date(card.expiresAt)}`].filter(Boolean).join(" · "));
      if (count === "—") lines.push(t("次数未知"));
      else if (count === 0 && !lines.length) lines.push(t("暂无刷新卡"));
      else if (!Array.isArray(cards.cards)) lines.push(t("明细未提供"));
      if (Array.isArray(cards.cards) && cards.cards.length < (cards.available_count || 0)) lines.push(t("部分明细"));
      const cardsStale = cards.stale || account.status === "unavailable";
      if (cardsStale) lines.push(t("上次读数"));
      return window.PortalI18n.html`<dl class="account-details-metrics" data-account-subscription>
        <div data-account-tier-tooltip tabindex="0" title="${esc(tierTitle)}"><dt>套餐档位</dt><dd data-account-tier>${esc(tier.name || tier.id || "—")}${stale ? "*" : ""}</dd></div>
        ${["codex", "claude"].includes(account.provider) ? window.PortalI18n.html`<div data-account-refresh-cards-tooltip tabindex="0" title="${esc(lines.join("\n"))}"><dt>刷新卡次数</dt><dd data-account-refresh-cards>${esc(count)}${cardsStale && count !== "—" ? "*" : ""}</dd></div>` : ""}
      </dl>`;
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
      return window.PortalI18n.html`<div class="agent-status-account-limit agent-status-api-account" data-account-api="${esc(id)}">
        <span class="agent-status-account-name" title="${esc(id)}">${esc(id)}</span>
        <div class="agent-status-account-windows">${accountBalanceHtml(account) || window.PortalI18n.html`<span class="agent-status-muted" title="${window.PortalI18n.t("余额暂不可用")}">—</span>`}
        ${account.capabilities?.resident_details === false ? "" : window.PortalI18n.html`<button class="portalButton is-compact agent-status-account-details" type="button" data-account-details="${esc(id)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="account-details">详情</button>`}
        </div>
      </div>`;
    }

    function accountLimitStatus(account) {
      const windows = accountSummaryQuotaWindows(account);
      const quota = account.quota_reserve || {};
      const reserved = quota.status === "reserved";
      const exhausted = reserved && quota.remaining_percent === 0;
      const blocked = (quota.windows || []).filter(window => typeof window.remaining_percent === "number" && window.remaining_percent <= quota.reserve_percent);
      const recovery = blocked.length && blocked.every(window => window.resets_at > 0) ? Math.max(...blocked.map(window => window.resets_at)) * 1000 : 0;
      const authenticationFailed = account.error_code === "provider_authentication_failed";
      const warning = accountQuotaWarning(account);
      if (!authenticationFailed && !reserved && warning) return warning;
      const weeklyResets = windows.filter(w => {
        const minutes = Number(w.window_duration_mins || 0);
        const weekly = minutes ? minutes === 7 * 24 * 60 : [
          "seven_day", "limit7d", "Gemini Models/Weekly Limit Remaining",
          "Gemini 4 Argon/Gemini 4 Argon Weekly Limit",
        ].includes(w.id);
        return weekly && w.resets_at > Date.now() / 1000;
      }).map(w => w.resets_at);
      const refreshLabel = authenticationFailed ? window.PortalI18n.t("请重新登录该账号") : account.balance && !windows.length ? (account.balance.updated_at ? window.PortalI18n.text`更新 ${formatDateTime(account.balance.updated_at)}` : account.balance.status === "loading" ? window.PortalI18n.t("正在刷新余额…") : "") : exhausted ? recovery > Date.now() ? window.PortalI18n.text`预计恢复 ${formatDateTime(recovery)}` : window.PortalI18n.t("等待额度恢复确认") : account.refreshing ? window.PortalI18n.t("正在刷新用量…") : account.error ? window.PortalI18n.t("用量刷新失败")
        : weeklyResets.length ? window.PortalI18n.text`周刷新：${formatDateTime(Math.min(...weeklyResets) * 1000)}` : window.PortalI18n.t("等待用量刷新");
      return esc(refreshLabel);
    }

    function accountLimitRow(account) {
      const accountId = String(account.account_id || "");
      const windows = accountSummaryQuotaWindows(account);
      const reserved = account.quota_reserve?.status === "reserved";
      const authenticationFailed = account.error_code === "provider_authentication_failed";
      const authenticationLabel = authenticationFailed ? window.PortalI18n.t('<span class="agent-status-muted" data-account-status-error>登录凭据失效</span>') : "";
      const content = authenticationLabel + (windows.length ? windows.map(window => accountQuotaWindow(account, window)).join("")
        : authenticationFailed ? "" : account.balance ? accountBalanceHtml(account) : window.PortalI18n.html`<span class="agent-status-muted"${reserved ? "" : " data-account-limit-stale"}>${esc(account.status === "loading" ? window.PortalI18n.t("正在读取额度…") : account.error || window.PortalI18n.t("额度数据不可用"))}</span>`);
      return window.PortalI18n.html`<div class="agent-status-account-limit" data-account-limit-account="${esc(accountId)}">
    <span class="agent-status-account-name">${esc(accountId)}
      ${account.enabled === false ? window.PortalI18n.html`<small>已停用</small>` : ""}
      ${account.login_state === "login_expired" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-account-relogin="${esc(accountId)}" ${currentCanSetScheduler() ? "" : "disabled"}>重新登录</button>` : ""}
    </span>
    <div class="agent-status-account-windows">
      <div class="agent-status-account-rate-windows">${content}</div>
      <button class="portalButton is-compact agent-status-account-details" type="button" data-account-details="${esc(accountId)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="account-details">详情</button>

      <span class="agent-status-account-refresh" data-account-limit-status="${esc(accountId)}">${accountLimitStatus(account)}</span>
    </div></div>`;
    }

    const accountIntakePending = new Map();
    const accountConcurrencyEdits = new Map();
    const accountIntervalEdits = new Map();
    const accountThresholdEdits = new Map();
    let accountDetails = null;

    function accountConcurrencyRows(account) {
      return account?.details_scope === "model_api" ? account.execution_accounts || [] : account ? [account] : [];
    }

    function accountIntervalHtml(account) {
      return window.PortalI18n.html`<section class="account-concurrency"><h4>派发间隔（秒）</h4>${accountConcurrencyRows(account).map(row => {
        const edit = accountIntervalEdits.get(row.account_id);
        const known = [row.dispatch_interval_min_seconds, row.dispatch_interval_max_seconds].every(Number.isFinite);
        const disabled = !currentCanSetScheduler() || !known || accountSettingsPending(row.account_id);
        return window.PortalI18n.html`<form data-account-interval="${esc(row.account_id)}">
          <span>${esc(row.account_id)}</span>
          <label><span>最小秒数</span><input type="number" name="minimum" min="0" max="3600" step="any" required value="${esc(edit?.minimum ?? row.dispatch_interval_min_seconds ?? '')}" ${disabled ? 'disabled' : ''}></label>
          <label><span>最大秒数</span><input type="number" name="maximum" min="0" max="3600" step="any" required value="${esc(edit?.maximum ?? row.dispatch_interval_max_seconds ?? '')}" ${disabled ? 'disabled' : ''}></label>
          ${currentCanSetScheduler() ? window.PortalI18n.html`<button type="submit" class="portalButton is-compact" ${disabled ? 'disabled' : ''}>${window.PortalI18n.t(edit?.pending ? '保存中…' : '保存')}</button>` : ''}
          ${edit?.feedback ? window.PortalI18n.html`<p role="${edit.error ? 'alert' : 'status'}">${esc(edit.feedback)}</p>` : ''}
        </form>`;
      }).join('')}</section>`;
    }

    function accountSettingsPending(id) {
      return accountIntervalEdits.get(id)?.pending || accountConcurrencyEdits.get(id)?.pending || accountThresholdEdits.get(id)?.pending;
    }

    async function saveAccountInterval(form) {
      const id = form.dataset.accountInterval;
      if (!currentCanSetScheduler() || accountSettingsPending(id) || !form.reportValidity()) return;
      const minimum = Number(form.elements.minimum.value), maximum = Number(form.elements.maximum.value);
      if (![minimum, maximum].every(v => Number.isFinite(v) && v >= 0 && v <= 3600) || minimum > maximum) {
        accountIntervalEdits.set(id, {minimum: form.elements.minimum.value, maximum: form.elements.maximum.value, error: true, feedback: window.PortalI18n.t('最小派发间隔不能大于最大派发间隔')});
        renderAccountDetails(); return;
      }
      const edit = {minimum, maximum, pending: true};
      accountIntervalEdits.set(id, edit); renderAccountDetails();
      try {
        const latest = await window.PortalInstructionClient.execute('ListProviderAccounts', {account_id: id});
        const route = latest.accounts?.find(row => row.account_id === id);
        if (!Number.isInteger(route?.max_concurrency)) throw new Error(window.PortalI18n.t('执行账号设置不可用，请刷新后重试'));
        // Threshold arguments are omitted: the official owner retains them unchanged.
        const result = await window.PortalInstructionClient.execute('UpdateProviderAccount', {
          account_id: id, max_concurrency: route.max_concurrency,
          dispatch_interval_min_seconds: minimum, dispatch_interval_max_seconds: maximum,
        }, {idempotencyKey: `account-interval:${id}:${globalThis.PortalUIPrimitives.randomUUID()}`});
        for (const account of executionAccounts(currentAgentStatusData())) {
          for (const row of accountConcurrencyRows(account)) if (row.account_id === id) Object.assign(row, result.settings);
        }
        edit.minimum = edit.maximum = undefined;
        edit.feedback = window.PortalI18n.t('已保存');
      } catch (error) {
        edit.error = true; edit.feedback = window.PortalUI.errorMessage(window.PortalI18n.t('保存派发间隔'), error);
      } finally {edit.pending = false; renderAccountDetails();}
    }

    function accountConcurrencyHtml(account) {
      const rows = accountConcurrencyRows(account);
      if (!rows.length) return "";
      return window.PortalI18n.html`<section class="account-concurrency"><h4>并行数</h4>${rows.map(row => {
        const edit = accountConcurrencyEdits.get(row.account_id);
        const known = Number.isInteger(row.max_concurrency) && row.max_concurrency >= 1;
        const pending = accountSettingsPending(row.account_id);
        const label = account.details_scope === "model_api" ? row.account_id : window.PortalI18n.t("最大并行数");
        return window.PortalI18n.html`<form data-account-concurrency="${esc(row.account_id)}">
          <label><span>${esc(label)}</span><input type="number" name="max_concurrency" min="1" max="256" step="1" required aria-label="${esc(window.PortalI18n.text`${row.account_id} 最大并行数`)}" value="${esc(edit?.value ?? (known ? row.max_concurrency : ""))}" ${!currentCanSetScheduler() || !known || pending ? "disabled" : ""}></label>
          ${currentCanSetScheduler() ? window.PortalI18n.html`<button type="submit" class="portalButton is-compact" ${!known || pending ? "disabled" : ""}>${edit?.pending ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("保存")}</button>` : ""}
          ${edit?.feedback ? window.PortalI18n.html`<p role="${edit.error ? "alert" : "status"}">${esc(edit.feedback)}</p>` : !known ? window.PortalI18n.markup('<p role="status">并行数暂不可用，请刷新</p>') : ""}
        </form>`;
      }).join("")}</section>`;
    }

    async function saveAccountConcurrency(form) {
      const id = form.dataset.accountConcurrency;
      if (!currentCanSetScheduler() || accountSettingsPending(id) || !form.reportValidity()) return;
      const value = Number(form.elements.max_concurrency.value);
      if (!Number.isInteger(value) || value < 1 || value > 256) return;
      const edit = {value, pending: true, feedback: "", error: false};
      accountConcurrencyEdits.set(id, edit);
      renderAccountDetails();
      try {
        // Read the route itself, not the API summary, preserving its current intervals.
        const latest = await window.PortalInstructionClient.execute("ListProviderAccounts", {account_id: id});
        const route = latest.accounts?.find(row => row.account_id === id);
        if (!route || ![route.dispatch_interval_min_seconds, route.dispatch_interval_max_seconds].every(Number.isFinite)) throw new Error("执行账号设置不可用，请刷新后重试");
        const result = await window.PortalInstructionClient.execute("UpdateProviderAccount", {
          account_id: id, max_concurrency: value,
          dispatch_interval_min_seconds: route.dispatch_interval_min_seconds,
          dispatch_interval_max_seconds: route.dispatch_interval_max_seconds,
        }, {idempotencyKey: `account-concurrency:${id}:${globalThis.PortalUIPrimitives.randomUUID()}`});
        for (const account of executionAccounts(currentAgentStatusData())) {
          for (const row of accountConcurrencyRows(account)) {
            if (row.account_id === id) Object.assign(row, result.settings);
          }
        }
        edit.value = undefined;
        edit.feedback = window.PortalI18n.t("已保存");
      } catch (error) {
        edit.error = true;
        edit.feedback = window.PortalUI.errorMessage(window.PortalI18n.t("保存并行数"), error);
      } finally {
        edit.pending = false;
        renderAccountDetails();
      }
    }

    function accountThresholdHtml(account) {
      const rows = accountConcurrencyRows(account);
      if (!rows.length) return "";
      return window.PortalI18n.html`<section class="account-concurrency account-close-threshold"><h4>Close 阈值</h4>${rows.map(row => {
        const state = accountDetails?.thresholds?.get(row.account_id);
        const route = state?.route || row;
        const threshold = route.close_threshold;
        const edit = accountThresholdEdits.get(row.account_id);
        const known = threshold?.supported && Number.isFinite(threshold.value) && ["percent", "balance"].includes(threshold.metric) && threshold.unit;
        const label = threshold?.metric === "balance" ? window.PortalI18n.t("剩余余额") : window.PortalI18n.t("剩余额度");
        const changedUnit = edit?.value !== undefined && (edit.metric !== threshold?.metric || edit.unit !== threshold?.unit);
        const disabled = !currentCanSetScheduler() || !known || state?.loading || accountSettingsPending(row.account_id);
        return window.PortalI18n.html`<form data-account-threshold="${esc(row.account_id)}" data-threshold-metric="${esc(threshold?.metric)}" data-threshold-unit="${esc(threshold?.unit)}">
          <label><span>${account.details_scope === "model_api" ? window.PortalI18n.html`${esc(row.account_id)} · ` : ""}${esc(label)}${threshold?.unit ? window.PortalI18n.html` (${esc(threshold.unit)})` : ""}</span><input type="number" name="close_threshold" min="0" ${threshold?.metric === "percent" ? 'max="100"' : ""} step="any" required aria-label="${esc(window.PortalI18n.text`${row.account_id} Close 阈值 (${threshold?.unit || "—"})`)}" value="${esc(edit?.value ?? (known ? threshold.value : ""))}" ${disabled ? "disabled" : ""}></label>
          ${route.provider === "codex" && route.kind === "subscription_login" && route.credits_supported === true ? window.PortalI18n.html`<label class="account-credits-toggle"><span>允许使用 credits</span><input type="checkbox" name="allow_credits" aria-label="允许使用 credits" ${(edit?.allowCredits ?? route.allow_credits ?? false) ? "checked" : ""} ${disabled ? "disabled" : ""}></label>` : ""}
          ${currentCanSetScheduler() ? window.PortalI18n.html`<button type="submit" class="portalButton is-compact" ${disabled || changedUnit ? "disabled" : ""}>${edit?.pending ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("保存")}</button>` : ""}
          ${state?.error ? window.PortalI18n.html`<p role="alert">${esc(state.error)}</p>` : !known && !state?.loading ? window.PortalI18n.markup('<p role="status">当前账号的阈值设置暂不可用，请刷新</p>') : changedUnit ? window.PortalI18n.markup('<p role="alert">阈值单位已变化，请重新输入</p>') : edit?.feedback ? window.PortalI18n.html`<p role="${edit.error ? "alert" : "status"}">${esc(edit.feedback)}</p>` : ""}
        </form>`;
      }).join("")}<p class="account-details-note">正数阈值仍按剩余量自动暂停接单；阈值为 0 时，仅 Codex 订阅允许使用 credits 且 credits 有效可用，原生额度耗尽才不会自动 Close。允许使用 credits 不会打开手动 Closed 的 Resident；其他账号额度耗尽仍会暂停。</p></section>`;
    }

    async function loadAccountThresholds(details, version) {
      const account = executionAccounts(currentAgentStatusData()).find(row => row.account_id === details.accountId);
      details.thresholds ||= new Map();
      await Promise.all(accountConcurrencyRows(account).map(async row => {
        const id = row.account_id;
        if (accountThresholdEdits.get(id)?.pending) return;
        const state = {loading: true, route: details.thresholds.get(id)?.route || row};
        details.thresholds.set(id, state);
        try {
          const result = await window.PortalInstructionClient.execute("ListProviderAccounts", {account_id: id}, {signal: details.abort.signal});
          if (version !== details.requestVersion || details !== accountDetails || !details.popover.isOpen() || details.thresholds.get(id) !== state) return;
          state.route = result.accounts?.find(item => item.account_id === id);
          if (!state.route?.close_threshold) throw new Error("阈值设置不可用，请刷新后重试");
        } catch (error) {
          state.error = window.PortalUI.errorMessage(window.PortalI18n.t("读取 Close 阈值"), error);
        } finally {
          state.loading = false;
          if (version === details.requestVersion && details === accountDetails && details.popover.isOpen()) renderAccountDetails();
        }
      }));
    }

    async function saveAccountThreshold(form) {
      const id = form.dataset.accountThreshold;
      if (!currentCanSetScheduler() || accountSettingsPending(id) || form.elements.close_threshold.disabled || !form.reportValidity()) return;
      const value = Number(form.elements.close_threshold.value);
      const {thresholdMetric: metric, thresholdUnit: unit} = form.dataset;
      if (!Number.isFinite(value) || value < 0 || (metric === "percent" && value > 100)) return;
      const previous = accountThresholdEdits.get(id);
      if (previous?.value !== undefined && (previous.metric !== metric || previous.unit !== unit)) return;
      const allowCredits = form.elements.allow_credits?.checked;
      const edit = {value, metric, unit, allowCredits, pending: true};
      accountThresholdEdits.set(id, edit);
      const state = accountDetails?.thresholds?.get(id);
      renderAccountDetails();
      try {
        const latest = await window.PortalInstructionClient.execute("ListProviderAccounts", {account_id: id});
        const route = latest.accounts?.find(row => row.account_id === id);
        const threshold = route?.close_threshold;
        if (!threshold?.supported || !["percent", "balance"].includes(metric) || threshold.metric !== metric || threshold.unit !== unit) throw new Error("阈值单位不可用或已变化，请刷新后重新输入");
        if (allowCredits !== undefined && (route.credits_supported !== true || route.provider !== "codex" || route.kind !== "subscription_login")) throw new Error("当前账号不支持 credits，请刷新后重试");
        if (!Number.isInteger(route.max_concurrency) || ![route.dispatch_interval_min_seconds, route.dispatch_interval_max_seconds].every(Number.isFinite)) throw new Error("执行账号设置不可用，请刷新后重试");
        const result = await window.PortalInstructionClient.execute("UpdateProviderAccount", {
          account_id: id, max_concurrency: route.max_concurrency,
          dispatch_interval_min_seconds: route.dispatch_interval_min_seconds,
          dispatch_interval_max_seconds: route.dispatch_interval_max_seconds,
          [metric === "percent" ? "close_threshold_percent" : "close_threshold_amount"]: value,
          ...(allowCredits === undefined ? {} : {allow_credits: allowCredits}),
        }, {idempotencyKey: `account-threshold:${id}:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (state) state.route = {...route, ...result.settings};
        for (const account of executionAccounts(currentAgentStatusData())) {
          for (const row of accountConcurrencyRows(account)) {
            if (row.account_id === id) Object.assign(row, result.settings);
          }
        }
        edit.value = undefined;
        edit.allowCredits = undefined;
        edit.feedback = window.PortalI18n.t("已保存");
      } catch (error) {
        edit.error = true;
        edit.feedback = window.PortalUI.errorMessage(window.PortalI18n.t("保存 Close 阈值"), error);
      } finally {
        edit.pending = false;
        renderAccountDetails();
      }
    }

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
      Object.assign(details, { accountId, trigger: button, snapshot: null, error: "", result: "", moreExpanded: false, thresholds: new Map() });
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
      void loadAccountThresholds(details, version);
      renderAccountDetails();
      try {
        const scope = executionAccounts(currentAgentStatusData()).find(item => item.account_id === accountId)?.details_scope;
        const snapshot = await homeApi.get(`/api/agent-status/account-residents?account_id=${encodeURIComponent(accountId)}${scope === "model_api" ? "&account_scope=model_api" : ""}`, { signal: details.abort.signal });
        if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
        if (snapshot?.account_id !== accountId || (scope === "model_api" && snapshot.account_scope !== scope)) throw new Error("账号详情响应不匹配，请刷新重试");
        details.snapshot = snapshot;
      } catch (error) {
        if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
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
      const snapshot = details.snapshot || {};
      const pendingAction = accountIntakePending.get(details.accountId);
      const busy = Boolean(pendingAction);
      const metric = (name, label, count) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd data-account-metric="${esc(name)}">${details.snapshot ? Number(count || 0) : "—"}</dd></div>`;
      let body = "";
      if (executionAccounts(currentAgentStatusData()).find(row => row.account_id === details.accountId)?.capabilities?.resident_details !== false) {
        body = window.PortalI18n.html`<p class="account-details-note">${window.PortalI18n.t("统计范围：你可见的 Resident")}</p>
      <dl class="account-details-metrics">${metric("total", window.PortalI18n.t("节点总数"), snapshot.total)}${metric("queued_messages", window.PortalI18n.t("待发消息"), snapshot.queued_messages)}</dl>
      <h4>接单状态</h4><dl class="account-details-metrics is-intake">${metric("open", window.PortalI18n.t("Open"), snapshot.intake?.open)}${metric("closed", "Closed", snapshot.intake?.closed)}${snapshot.intake?.unknown ? metric("intake_unknown", window.PortalI18n.t("状态未知"), snapshot.intake.unknown) : ""}</dl>
      <h4>工作状态</h4><dl class="account-details-metrics is-work">${metric("running", window.PortalI18n.t("运行中"), snapshot.work?.running)}${metric("queued", window.PortalI18n.t("排队中"), snapshot.work?.queued)}${metric("idle", window.PortalI18n.t("空闲"), snapshot.work?.idle)}${metric("stopping", window.PortalI18n.t("停止中"), snapshot.work?.stopping)}${snapshot.work?.unknown ? metric("work_unknown", window.PortalI18n.t("状态未知"), snapshot.work.unknown) : ""}</dl>
      <details class="account-details-more" ${details.moreExpanded ? "open" : ""}><summary data-account-action="more">补充统计</summary>
        <p class="account-details-note">${esc(formatDateTime(snapshot.observed_at) || "")}<br>未创建 Session ${Number(snapshot.without_session || 0)} · 人工额度放行 ${Number(snapshot.quota_exempt || 0)}<br>最近输入失败 ${Number(snapshot.latest_input?.failed || 0)} · 已取消 ${Number(snapshot.latest_input?.cancelled || 0)}</p>
      </details>`;
      }
      const account = executionAccounts(currentAgentStatusData()).find(item => item.account_id === details.accountId);
      let billingUrl = "";
      try {
        const url = new URL(account?.balance?.billing_url || "");
        if (["https:", "http:"].includes(url.protocol)) billingUrl = url.href;
      } catch (_) { /* Unknown billing destinations do not get a link. */ }
      const focusedElement = details.panel.contains(document.activeElement) ? document.activeElement : null;
      const focused = focusedElement?.dataset.accountAction;
      const focusedRoute = focusedElement?.closest("[data-account-concurrency]")?.dataset.accountConcurrency;
      const focusedInterval = focusedElement?.closest("[data-account-interval]")?.dataset.accountInterval;
      const focusedThreshold = focusedElement?.closest("[data-account-threshold]")?.dataset.accountThreshold;
      const focusedControl = focusedElement?.tagName;
      const focusedName = focusedElement?.name;
      if (focusedThreshold) details.thresholdFocus = {id: focusedThreshold, name: focusedName, tag: focusedControl};
      else if (focusedElement && focusedElement !== document.body) details.thresholdFocus = null;
      details.panel.innerHTML = window.PortalI18n.html`<header><strong>${esc(details.accountId)} · ${window.PortalI18n.t("详情")}</strong><button class="portalButton is-compact" type="button" data-account-action="close" aria-label="关闭账号详情">×</button></header>
    <div class="account-details-body">${accountQuotaWarning(account, true)}${accountSubscriptionHtml(account)}${accountCreditsHtml(account)}${accountConcurrencyHtml(account)}${accountThresholdHtml(account)}${accountIntervalHtml(account)}${body}${details.error ? window.PortalI18n.html`<p role="alert">${esc(details.error)}</p>` : ""}${details.result ? window.PortalI18n.html`<p role="status" data-account-result>${esc(details.result)}</p>` : ""}</div>
    <footer><div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="refresh" ${details.loading || busy ? "disabled" : ""}>刷新</button>
      ${billingUrl ? window.PortalI18n.html`<a class="portalButton is-compact" data-account-action="billing" href="${esc(billingUrl)}" title="${esc(billingUrl)}" target="_blank" rel="noopener noreferrer">充值 / 账单</a>` : ""}
      </div>
      ${account?.capabilities?.resident_details === false ? "" : window.PortalI18n.html`<div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="close-all" data-account-close-residents="${esc(details.accountId)}" ${!snapshot?.open_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下 Resident 设为手动 Closed，保留队列与正在执行的任务">${pendingAction === "close" ? window.PortalI18n.t("关闭中…") : window.PortalI18n.t("全部关闭")}</button>
      <button class="portalButton is-compact" type="button" data-account-action="open-all" data-account-open-residents="${esc(details.accountId)}" ${!snapshot?.closed_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下全部关闭的 Resident 按人工 Open 打开">${pendingAction === "open" ? window.PortalI18n.t("打开中…") : window.PortalI18n.t("全部打开")}</button></div>`}</footer>`;
      details.panel.querySelector('[data-account-action="close"]').addEventListener("click", event => details.popover.close(event));
      details.panel.querySelector('[data-account-action="refresh"]').addEventListener("click", () => { loadAgentStatus(true); loadAccountDetails(details); });
      details.panel.querySelector('[data-account-open-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "open"));
      details.panel.querySelector('[data-account-close-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "close"));
      details.panel.querySelectorAll('[data-account-concurrency]').forEach(form => {
        form.addEventListener("input", () => accountConcurrencyEdits.set(form.dataset.accountConcurrency, {value: form.elements.max_concurrency.value}));
        form.addEventListener("submit", event => { event.preventDefault(); void saveAccountConcurrency(form); });
        if (form.dataset.accountConcurrency === focusedRoute) form.querySelector(`${focusedControl}:not(:disabled)`)?.focus({preventScroll: true});
      });
      details.panel.querySelectorAll('[data-account-interval]').forEach(form => {
        form.addEventListener('input', () => accountIntervalEdits.set(form.dataset.accountInterval, {minimum: form.elements.minimum.value, maximum: form.elements.maximum.value}));
        form.addEventListener('submit', event => {event.preventDefault(); void saveAccountInterval(form);});
        if (form.dataset.accountInterval === focusedInterval) form.querySelector(`${focusedControl}:not(:disabled)`)?.focus({preventScroll: true});
      });
      details.panel.querySelectorAll('[data-account-threshold]').forEach(form => {
        form.addEventListener("input", () => {
          accountThresholdEdits.set(form.dataset.accountThreshold, {value: form.elements.close_threshold.value, allowCredits: form.elements.allow_credits?.checked, metric: form.dataset.thresholdMetric, unit: form.dataset.thresholdUnit});
          form.querySelector('button')?.removeAttribute("disabled");
        });
        form.addEventListener("submit", event => { event.preventDefault(); void saveAccountThreshold(form); });
        if (form.dataset.accountThreshold === details.thresholdFocus?.id) form.querySelector(`${details.thresholdFocus.name ? `[name="${details.thresholdFocus.name}"]` : details.thresholdFocus.tag}:not(:disabled)`)?.focus({preventScroll: true});
      });
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
