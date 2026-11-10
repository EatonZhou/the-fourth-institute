(() => {
  if (window.PortalChatWorkspaceRuntimeSettings) return;

  // The Resident runtime panel (account and parameters on one page), shown in
  // a dialog or in place, and the shared parameter editor also used by
  // workspaces that expose settings without Session operations.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml",
  ]);

  function residentRuntimeSettingRows(settings = {}, catalog = {}) {
    const data = settings && typeof settings === "object" ? settings : {};
    const model = String(data.model || catalog.default_model || window.PortalI18n.t("Provider default")).trim();
    const fields = catalog.runtime_capabilities?.settings || [];
    const rows = fields.includes("model") ? [["Model", model]] : [];
    const effort = data.reasoning_effort
      || (catalog.default_efforts_by_model?.[model] ?? catalog.default_reasoning_effort);
    if (fields.includes("reasoning_effort") && effort) rows.push(["Effort", effort]);
    const modelTiers = catalog.service_tiers_by_model?.[model] || [];
    if (fields.includes("service_tier") && modelTiers.length) rows.push(["Speed", data.service_tier === "priority" ? window.PortalI18n.t("Fast on") : window.PortalI18n.t("Fast off")]);
    if (fields.includes("permissions")) {
      const capabilities = catalog.runtime_capabilities || {};
      const preset = String(data.permission_preset || "").trim();
      const label = capabilities.permission_labels?.[preset] || capabilities.default_permission_label;
      if (label) rows.push(["执行沙箱与审批", label]);
    }
    if (fields.includes("collaboration_mode")) rows.push(["Mode", data.collaboration_mode === "plan" ? "Plan" : window.PortalI18n.t("Default")]);
    if (catalog.api_settings) {
      rows.push(["API 账号", catalog.account_id || catalog.backend_route_id || "—"]);
      rows.push(["上下文", data.prompt_context?.history?.enabled ? window.PortalI18n.t("携带会话历史") : window.PortalI18n.t("单轮独立")]);
      if (catalog.api_settings.view === "jev") rows.push(["Jev 判定", `${Object.keys(data.backend_options?.parameters?.questions || {}).length} ${window.PortalI18n.t("个问题")}`]);
      rows.push(["高级", `${data.backend_options?.timeout_seconds ?? 360}s · ${data.backend_options?.max_retries ?? 0} ${window.PortalI18n.t("次重试")}`]);
    }
    return rows;
  }

  function residentRuntimeStatusText(settings = {}, catalog = {}) {
    return residentRuntimeSettingRows(settings, catalog)
      .filter(([label]) => label !== "Mode" || settings.collaboration_mode === "plan")
      .map(([, value]) => value).join(" · ");
  }

  function notifyRuntimeSettingsSaved(result) {
    const liveUpdate = result?.active_turn_settings_update;
    const changedFields = Array.isArray(result?.next_turn_fields) ? result.next_turn_fields : [];
    if (liveUpdate?.status === "delivered") {
      window.PortalUI?.toast?.("Runtime 设置已保存；Speed 已原生应用到当前 turn 的后续步骤，全部设置从下一条 turn 生效。", "success");
    } else if (liveUpdate?.status === "pending") {
      window.PortalUI?.toast?.("Runtime 设置已保存；Speed 正在应用到当前 turn，全部设置从下一条 turn 生效。", "success");
    } else if (liveUpdate?.status === "failed") {
      window.PortalUI?.toast?.(`Runtime 设置已保存并从下一条 turn 生效；当前 turn 的 Speed 更新失败：${liveUpdate.error || "unknown error"}`, "warning");
    } else if (changedFields.length) {
      window.PortalUI?.toast?.("Runtime 设置已保存，从下一轮生效。", "success");
    }
  }

  async function showResidentBackendNotices(container, residentId, { settings, accountId } = {}) {
    const request = {};
    container.backendNoticeRequest = request;
    container.replaceChildren();
    container.hidden = true;
    if (!residentId) return;
    const current = () => container.isConnected && container.backendNoticeRequest === request;
    const service = window.PortalResidentService;
    try {
      const [stored, boundAccountId, result] = await Promise.all([
        settings || service.runtimeSettings(residentId).then(result => result.settings || {}),
        accountId || window.PortalResidentProjection.load(residentId).then(detail => detail.execution_account?.account_id),
        service.runtimeAccounts(),
      ]);
      if (!current()) return;
      const messages = [];
      const account = result.accounts?.find(row => row.account_id === boundAccountId);
      if (account?.enabled === false) messages.push(window.PortalI18n.t("账号已停用"));
      const fallback = stored.backend_role_fallback;
      if (fallback?.used === "default" && typeof fallback.requested === "string" && fallback.requested) {
        messages.push(window.PortalI18n.text`角色 ${fallback.requested} 未映射，使用站点默认`);
      }
      for (const message of messages) {
        const note = document.createElement("p");
        note.className = "paneNote";
        note.textContent = message;
        container.append(note);
      }
      container.hidden = !messages.length;
    } catch (_) {
      if (!current()) return;
      container.textContent = window.PortalI18n.t("账号与角色状态读取失败，请重新打开设置。");
      container.hidden = false;
    }
  }

  const sessionRestartActive = restart => ["accepted", "submitting", "confirming", "cancelling"].includes(restart?.status);

  function formatSessionRestartDuration(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return [hours ? `${hours} 小时` : "", minutes ? `${minutes} 分` : "",
      remainder || !total ? `${remainder} 秒` : ""].filter(Boolean).join(" ");
  }

  function createRuntimeSettings(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceRuntimeSettings is missing: ${missing.join(", ")}`);
    const {
      escapeHtml,
    } = dependencies;

    function residentRuntimeMetaHtml(settings = {}, options = {}) {
      const label = residentRuntimeStatusText(settings, options.catalog || {});
      if (options.interactive === false) {
        return window.PortalI18n.html`<span class="paneNote composerMeta" title="${escapeHtml(options.title || window.PortalI18n.t("当前会话运行设置"))}">${escapeHtml(label)}</span>`;
      }
      const attribute = String(options.openAttribute || "data-runtime-settings-open").trim();
      const title = options.title || window.PortalI18n.t("切换模型与运行设置");
      return window.PortalI18n.html`<button class="paneNote composerMeta composerRuntimeBtn" type="button" ${escapeHtml(attribute)} aria-haspopup="dialog" title="${escapeHtml(`${title}：${label}`)}"><span class="composerRuntimeLabel">${escapeHtml(label)}</span></button>`;
    }

    function createRuntimeSettingsEditor() {
      const element = document.createElement("fieldset");
      element.className = "portalRuntimeSettingsEditor";
      element.innerHTML = window.PortalI18n.html`        <div class="portalRuntimeSettingsGrid">
          <label class="portalRuntimeSettingsField" hidden><span>API 账号</span><select data-runtime-setting="backend_route_id"></select></label>
          <label class="portalRuntimeSettingsField" hidden><span>账号策略</span><select data-runtime-setting="account_selection" title="自动：同后端、同模型，按可用额度选择账号"><option value="fixed">固定账号</option><option value="auto">按额度自动</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Model</span><select data-runtime-setting="model"></select></label>
          <label class="portalRuntimeSettingsField"><span>Mode</span><select data-runtime-setting="collaboration_mode"><option value="default">Default · 默认执行</option><option value="plan">Plan · 先制定计划</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Effort</span><select data-runtime-setting="reasoning_effort"></select></label>
          <label class="portalRuntimeSettingsField"><span>Speed</span><select data-runtime-setting="service_tier"><option value="">Standard (default)</option><option value="priority">Fast</option></select></label>
          <label class="portalRuntimeSettingsField"><span>执行沙箱与审批</span><select data-runtime-setting="permission_preset"><option value="">Full Access (default)</option><option value="default">Ask</option><option value="auto-review">Auto-review</option><option value="full-access">Full Access</option></select></label>
        </div>
        <p class="portalRuntimeSettingsHint" data-runtime-settings-hint>设置从下一轮生效。</p>
`;
      const apiFields = window.PortalModelApiSettings.create();
      element.querySelector(".portalRuntimeSettingsGrid").after(apiFields.element);
      const accountSelection = element.querySelector('[data-runtime-setting="account_selection"]');
      const model = element.querySelector('[data-runtime-setting="model"]');
      const apiAccount = element.querySelector('[data-runtime-setting="backend_route_id"]');
      let apiRoutes = [], initialApiRoute = "";
      const mode = element.querySelector('[data-runtime-setting="collaboration_mode"]');
      const effort = element.querySelector('[data-runtime-setting="reasoning_effort"]');
      const tier = element.querySelector('[data-runtime-setting="service_tier"]');
      const permissions = element.querySelector('[data-runtime-setting="permission_preset"]');
      const setOptions = (select, values, current, defaultLabel) => {
        const choices = [...new Set(["", ...values, current].filter(value => value !== undefined && value !== null).map(value => String(value)))];
        select.innerHTML = choices.map(value => window.PortalI18n.html`<option value="${escapeHtml(value)}">${escapeHtml(value || defaultLabel)}</option>`).join("");
        select.value = String(current || "");
      };
      let currentSettings = {};
      let initialSettings = {};
      let providerCatalog = null;
      const syncModelControls = () => {
        accountSelection.closest(".portalRuntimeSettingsField").hidden = providerCatalog?.account_selection?.enabled !== true;
        if (!providerCatalog) return;
        const selectedModel = model.value || providerCatalog.default_model;
        const knownModel = providerCatalog.models.includes(selectedModel);
        model.title = knownModel ? "" : window.PortalI18n.t("Provider 目录未返回此模型的能力信息");
        const efforts = providerCatalog.efforts_by_model?.[selectedModel] || [];
        const currentEffort = currentSettings.reasoning_effort || "";
        const defaultEffort = providerCatalog.default_efforts_by_model?.[selectedModel] || providerCatalog.default_reasoning_effort || "默认";
        setOptions(effort, efforts, currentEffort, `${defaultEffort} (default)`);
        for (const option of effort.options) {
          if (!efforts.includes(option.value || defaultEffort)) option.title = knownModel ? window.PortalI18n.t("Provider 未声明此模型支持该 Effort") : window.PortalI18n.t("Provider 目录未返回此模型的能力信息");
        }
        effort.closest(".portalRuntimeSettingsField").hidden = !providerCatalog.runtime_capabilities?.settings?.includes("reasoning_effort") || (knownModel && !efforts.length && !currentEffort);
        const tiers = providerCatalog.service_tiers_by_model?.[selectedModel] || [];
        const tierIds = tiers.map(item => typeof item === "string" ? item : item.id).filter(Boolean);
        const currentTier = String(currentSettings.service_tier || "").toLowerCase() === "priority"
          ? "priority"
          : String(currentSettings.service_tier || "");
        const tierField = tier.closest(".portalRuntimeSettingsField");
        setOptions(tier, tierIds, currentTier, tierIds.length ? "Standard (default)" : "Unavailable");
        for (const option of tier.options) {
          if (option.value === "priority") option.textContent = tierIds.includes("priority") ? "Fast" : window.PortalI18n.t("Fast (saved; unavailable)");
        }
        tier.disabled = !tierIds.length;
        tierField.hidden = !providerCatalog.runtime_capabilities?.settings?.includes("service_tier") || !tierIds.length;
        tierField.title = tierIds.length
          ? ""
          : knownModel
            ? window.PortalI18n.t("Provider 未声明此模型支持 Speed")
            : window.PortalI18n.text`当前账号${providerCatalog.account_id ? ` ${providerCatalog.account_id}` : ""}的 Provider 目录未返回 ${selectedModel}，无法确定 Speed 能力`;
      };
      model.addEventListener("change", () => {
        currentSettings = { ...currentSettings, reasoning_effort: effort.value, service_tier: tier.value };
        syncModelControls();
        apiFields.syncModel(model.value);
      });
      apiAccount.addEventListener("change", () => {
        const route = apiRoutes.find(item => item.backend_route_id === apiAccount.value);
        if (!route) return;
        providerCatalog = route;
        currentSettings = apiAccount.value === initialApiRoute ? {...initialSettings} : {
          ...initialSettings, model: route.default_model,
          backend_options: {parameters: {}, timeout_seconds: Math.min(initialSettings.backend_options?.timeout_seconds ?? 360, route.api_settings.timeout_max), max_retries: initialSettings.backend_options?.max_retries ?? 0},
        };
        setOptions(model, route.models, currentSettings.model, `${route.default_model || "Provider"} (default)`);
        apiFields.open(currentSettings, route);
        syncModelControls();
      });
      return {
        element, model,
        read({complete = false} = {}) {
          // A new provider session needs a concrete model; ordinary edits may inherit its default.
          const selected = { model: String(model.value || (complete ? providerCatalog?.default_model : "") || "").trim() };
          if (providerCatalog?.account_selection?.enabled === true && accountSelection.value !== (initialSettings.account_selection || "fixed")) selected.account_selection = accountSelection.value;
          const modelChanged = selected.model !== String(initialSettings.model || "");
          const effortChanged = effort.value !== String(initialSettings.reasoning_effort || "");
          if (!mode.closest(".portalRuntimeSettingsField").hidden) {
            selected.collaboration_mode = mode.value;
          }
          if (modelChanged && !model.closest(".portalRuntimeSettingsField").hidden && providerCatalog && model.value && !providerCatalog.models.includes(model.value)) {
            throw new Error("当前模型未出现在此账号的 Provider 能力列表中，请选择可用模型。");
          }
          {
            if (!permissions.closest(".portalRuntimeSettingsField").hidden
              && (complete || permissions.value !== String(initialSettings.permission_preset || ""))) {
              selected.permission_preset = String(permissions.value || "").trim();
            }
            if (providerCatalog && (complete || modelChanged || effortChanged)) {
              const selectedModel = model.value || providerCatalog.default_model;
              if (!providerCatalog.models.includes(selectedModel)) throw new Error("默认模型未出现在此账号的 Provider 能力列表中，请选择可用模型。");
              const selectedEffort = effort.value || providerCatalog.default_efforts_by_model?.[selectedModel] || providerCatalog.default_reasoning_effort;
              const supported = providerCatalog.efforts_by_model?.[selectedModel] || [];
              if (selectedEffort && !effort.closest(".portalRuntimeSettingsField").hidden && !supported.includes(selectedEffort)) {
                throw new Error(`Provider 未声明当前模型支持 ${selectedEffort}，请选择可用的 Effort；原设置未修改。`);
              }
            }
            if (!effort.closest(".portalRuntimeSettingsField").hidden) selected.reasoning_effort = String(effort.value || "").trim();
            if (!tier.disabled && !tier.closest(".portalRuntimeSettingsField").hidden) selected.service_tier = String(tier.value || "").trim();
          }
          Object.assign(selected, apiFields.read());
          if (initialApiRoute && apiAccount.value !== initialApiRoute) selected.backend_route_id = apiAccount.value;
          return selected;
        },
        open(settings = {}, dialogOptions = {}) {
          currentSettings = settings && typeof settings === "object" ? settings : {};
          initialSettings = { ...currentSettings };
          accountSelection.value = currentSettings.account_selection || "fixed";
          providerCatalog = dialogOptions.catalog || null;
          apiFields.open(currentSettings, providerCatalog);
          apiRoutes = providerCatalog?.api_settings ? providerCatalog.api_routes || [providerCatalog] : [];
          initialApiRoute = providerCatalog?.api_settings ? providerCatalog.backend_route_id || providerCatalog.account_id : "";
          apiAccount.closest(".portalRuntimeSettingsField").hidden = dialogOptions.showApiAccount === false || !apiRoutes.length;
          apiAccount.replaceChildren(...apiRoutes.map(route => new Option(route.account_id || route.backend_route_id, route.backend_route_id || route.account_id)));
          apiAccount.value = initialApiRoute;
          model.title = "";
          const models = Array.isArray(dialogOptions.models) && dialogOptions.models.length
            ? dialogOptions.models.map(item => Array.isArray(item) ? item[0] : item)
            : providerCatalog?.models || [];
          setOptions(model, models, currentSettings.model || "", dialogOptions.defaultModelLabel || `${providerCatalog?.default_model || "Provider"} (default)`);
          setOptions(effort, dialogOptions.efforts || [], currentSettings.reasoning_effort || "", "Provider default");
          tier.value = String(currentSettings.service_tier || "").toLowerCase() === "priority" ? "priority" : String(currentSettings.service_tier || "");
          tier.disabled = false;
          tier.closest(".portalRuntimeSettingsField").title = "";
          const permissionPresets = providerCatalog?.runtime_capabilities?.permission_presets || [];
          const defaultPermissionLabel = providerCatalog?.runtime_capabilities?.default_permission_label;
          setOptions(permissions, permissionPresets, currentSettings.permission_preset || "",
            defaultPermissionLabel ? `${defaultPermissionLabel} (default)` : "Provider default");
          for (const option of permissions.options) {
            option.textContent = option.value
              ? providerCatalog?.runtime_capabilities?.permission_labels?.[option.value] || option.value
              : defaultPermissionLabel ? `${defaultPermissionLabel} (default)` : window.PortalI18n.t("Provider default");
          }
          mode.value = currentSettings.collaboration_mode === "plan" ? "plan" : "default";
          syncModelControls();
          const fields = providerCatalog?.runtime_capabilities?.settings || [];
          for (const [control, field] of [[mode, "collaboration_mode"], [effort, "reasoning_effort"], [tier, "service_tier"], [permissions, "permissions"]]) {
            const modelUnavailable = control === effort && !(providerCatalog?.efforts_by_model?.[model.value || providerCatalog?.default_model] || []).length
              || control === tier && !(providerCatalog?.service_tiers_by_model?.[model.value || providerCatalog?.default_model] || []).length;
            control.closest(".portalRuntimeSettingsField").hidden = !fields.includes(field) || modelUnavailable;
          }
          element.querySelector("[data-runtime-settings-hint]").textContent = dialogOptions.hint ?? window.PortalI18n.t("设置从下一轮生效。");
        },
      };
    }

    function createRuntimeSettingsDialog(options = {}) {
      const id = String(options.id || `portalRuntimeSettings_${Math.random().toString(16).slice(2)}`);
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay";
      overlay.hidden = true;
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
        <div class="portalDetailHeader" data-portal-detail-header><div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">运行设置</div><button class="portalDetailClose" data-portal-icon="close" data-runtime-settings-close type="button" aria-label="关闭运行设置"></button></div>
        <form class="portalDetailForm portalRuntimeSettingsForm" data-runtime-settings-form>
          <div data-resident-backend-notices role="status" hidden></div>
          <div class="portalDetailFooter" data-portal-detail-footer><button class="portalButton" data-runtime-settings-close type="button">取消</button><button class="portalButton is-primary" data-runtime-settings-submit type="submit">保存</button></div>
        </form></div>`;
      document.body.append(overlay);
      const editor = createRuntimeSettingsEditor();
      const form = overlay.querySelector("form"), submit = overlay.querySelector("[data-runtime-settings-submit]");
      form.querySelector(".portalDetailFooter").before(editor.element);
      let save;
      const dialog = window.PortalUI.createDialogController({modal: overlay, manageHidden: true, escapeClose: true, backdropClose: true,
        initialFocus: () => editor.model, onClose: context => options.onClose?.(context)});
      const close = context => dialog?.close?.(context);
      overlay.querySelectorAll("[data-runtime-settings-close]").forEach(button => button.addEventListener("click", () => close({source: "button"})));
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!save || submit.disabled) return;
        submit.disabled = true;
        try { const selected = editor.read(); editor.element.disabled = true; await save(selected); close({accepted: true}); }
        catch (error) { window.PortalUI.toast(window.PortalUI.errorMessage("保存运行参数", error), "error"); }
        finally { submit.disabled = false; editor.element.disabled = false; }
      });
      return {
        open(settings = {}, onSave = null, dialogOptions = {}) {
          save = onSave; editor.open(settings, dialogOptions);
          overlay.querySelector(".portalRuntimeSettingsTitle").textContent = dialogOptions.title || window.PortalI18n.t("运行设置");
          void showResidentBackendNotices(overlay.querySelector("[data-resident-backend-notices]"), dialogOptions.residentId, {settings: dialogOptions.residentSettings, accountId: dialogOptions.accountId});
          dialog.open({focusTarget: editor.model});
        }, close,
        destroy() { dialog.destroy(); overlay.remove(); },
      };
    }

    const acknowledgedSessionRestarts = new Map();

    function createSessionRestartProgress(options = {}) {
      const compact = !!options.compact;
      const element = document.createElement("div");
      element.className = "portalSessionRestartProgress";
      element.classList.toggle("is-compact", compact);
      element.hidden = true;
      const heading = document.createElement("div");
      heading.setAttribute("role", "status");
      heading.setAttribute("aria-live", "polite");
      const timing = document.createElement("div");
      timing.className = "paneNote";
      const message = document.createElement("div");
      message.className = "paneNote";
      const diagnostics = document.createElement("details");
      diagnostics.className = "portalSessionRestartDiagnostics";
      const summary = document.createElement("summary");
      summary.textContent = window.PortalI18n.t("技术诊断");
      const technicalError = document.createElement("pre");
      diagnostics.append(summary, technicalError);
      if (compact) element.append(heading);
      else element.append(heading, timing, message, diagnostics);
      const detailsButton = document.createElement("button");
      detailsButton.type = "button";
      detailsButton.className = "portalButton is-compact";
      detailsButton.dataset.sessionRestartProgressOpen = "1";
      detailsButton.addEventListener("click", () => options.onOpen?.());
      const dismissButton = document.createElement("button");
      dismissButton.type = "button";
      dismissButton.className = "portalButton is-compact";
      dismissButton.textContent = window.PortalI18n.t("已知悉");
      dismissButton.setAttribute("aria-label", window.PortalI18n.t("已知悉，关闭本次操作失败提示"));
      dismissButton.dataset.sessionRestartDismiss = "1";
      if (compact) element.append(detailsButton, dismissButton);
      let current = {};
      let timer = null;
      const acknowledgementKey = () => {
        const residentId = current.config?.resident_id;
        const owner = typeof options.owner === "function" ? options.owner() : options.owner;
        return residentId ? ["portal.sessionRestartAcknowledged.v1", owner || "anonymous", residentId]
          .map(part => encodeURIComponent(part)).join(":") : "";
      };
      const labels = {
        submitting: window.PortalI18n.t("正在提交，等待受理回执"), confirming: window.PortalI18n.t("受理结果待确认，请勿重复提交"),
        accepted: window.PortalI18n.t("已受理，正在准备"), queued: window.PortalI18n.t("已受理，等待调度"), waiting_current_execution: window.PortalI18n.t("等待当前工作完成"),
        waiting: window.PortalI18n.t("等待当前工作完成"), preparing: window.PortalI18n.t("正在准备会话"), verifying: window.PortalI18n.t("正在核对上下文与能力"), committing: window.PortalI18n.t("正在提交绑定"),
        waiting_quota_reserve: window.PortalI18n.t("已受理，等待额度"), waiting_capacity: window.PortalI18n.t("已受理，等待执行容量"),
        waiting_dispatch_interval: window.PortalI18n.t("已受理，等待调度间隔"), waiting_session_binding: window.PortalI18n.t("已受理，等待 Session 空闲"),
        creating_session: window.PortalI18n.t("正在创建新 Session"), initializing: window.PortalI18n.t("正在初始化交接"),
        switching: window.PortalI18n.t("正在切换绑定"), cancelling: window.PortalI18n.t("正在取消迁移"),
        completed: window.PortalI18n.t("已完成"), failed: window.PortalI18n.t("失败"), cancelled: window.PortalI18n.t("已取消"),
      };
      const render = () => {
        const restart = current.restart;
        const failed = restart?.status === "failed";
        const key = compact ? acknowledgementKey() : "";
        const acknowledged = key && (acknowledgedSessionRestarts.get(key) || window.PortalState.storage.readText(key, ""));
        element.dataset.restartStatus = restart?.status || "";
        element.hidden = !restart?.status || ["completed", "cancelled"].includes(restart.status)
          || !!(failed && restart.restart_id && acknowledged === restart.restart_id);
        if (element.hidden) return;
        detailsButton.textContent = failed ? window.PortalI18n.t("查看详情") : window.PortalI18n.t("查看操作进度");
        dismissButton.hidden = !failed || !key || !restart.restart_id;
        const label = endpoint => endpoint?.account_id || endpoint?.provider || window.PortalI18n.t("当前 Session");
        const phase = restart.status === "accepted" ? restart.phase || "accepted" : restart.status;
        const operation = restart.operation === "switch_account" ? "账号切换" : restart.operation === "restart" ? "重新开始会话" : "会话操作";
        const endpoints = restart.operation !== "restart" && label(restart.source) !== label(restart.target)
          ? `${label(restart.source)} → ${label(restart.target)}` : label(restart.source);
        heading.textContent = `${operation}：${endpoints} · ${labels[phase] || phase || "进行中"}`;
        if (failed && restart.submitted_at) heading.textContent += ` · ${new Date(restart.submitted_at).toLocaleString()}`;
        const started = Date.parse(restart.submitted_at || "");
        const ended = Date.parse(restart.restarted_at || restart.configured_at || "");
        const end = Number.isFinite(ended) ? ended : sessionRestartActive(restart) ? Date.now() : NaN;
        timing.textContent = Number.isFinite(started) && Number.isFinite(end)
          ? `${failed ? window.PortalI18n.t("耗时") : window.PortalI18n.t("已耗时")} ${formatSessionRestartDuration((end - started) / 1000)}`
          : failed ? window.PortalI18n.t("耗时未记录") : "";
        if (current.lastSyncedAt && sessionRestartActive(restart)) {
          timing.textContent += window.PortalI18n.text` · 最近同步 ${formatSessionRestartDuration((Date.now() - current.lastSyncedAt) / 1000)}前`;
        }
        message.textContent = failed
          ? (restart.previous_session_retained === true
            ? `操作未完成，仍使用 ${label(restart.source)}，原会话已保留。`
            : window.PortalI18n.t("操作未完成，请在诊断中核对当前绑定和原错误。"))
          : current.error ? window.PortalI18n.t("暂时无法同步迁移状态，请稍后查看；请勿重复提交。") : sessionRestartActive(restart)
          ? (restart.status === "cancelling" ? window.PortalI18n.t("取消请求已受理，原会话将继续保留。") : window.PortalI18n.t("关闭窗口不会取消操作；需要终止时请使用“取消操作”。"))
          : restart.configuration_only ? window.PortalI18n.t("账号配置已保存，尚未创建 Session。") : window.PortalI18n.t("迁移已结束。");
        technicalError.textContent = String(restart.error || current.error || "");
        diagnostics.hidden = !technicalError.textContent;
      };
      dismissButton.addEventListener("click", () => {
        const key = acknowledgementKey();
        if (!key || current.restart?.status !== "failed" || !current.restart.restart_id) return;
        acknowledgedSessionRestarts.set(key, current.restart.restart_id);
        window.PortalState.storage.writeText(key, current.restart.restart_id);
        window.dispatchEvent(new CustomEvent("portal-session-restart-acknowledged"));
        options.onDismiss?.();
      });
      const syncAcknowledgement = event => {
        if (!event.key || event.key === window.PortalState.storage.key(acknowledgementKey())) acknowledgedSessionRestarts.delete(acknowledgementKey());
        render();
      };
      if (compact) {
        window.addEventListener("portal-session-restart-acknowledged", render);
        window.addEventListener("storage", syncAcknowledgement);
      }
      return {
        element,
        update(view) {
          if (current.restart?.restart_id !== view?.restart?.restart_id) diagnostics.open = false;
          current = view || {};
          clearInterval(timer);
          render();
          if (sessionRestartActive(current.restart)) timer = setInterval(render, 1000);
        },
        destroy() {
          clearInterval(timer);
          window.removeEventListener("portal-session-restart-acknowledged", render);
          window.removeEventListener("storage", syncAcknowledgement);
          element.remove();
        },
      };
    }

    // One runtime panel: the account the Resident runs on and the parameters of
    // its next turns, on a single page. Choosing another account switches the
    // Session; changing only parameters saves them for the next turn. The
    // dialog and the Resident menu both mount this panel.
    function createRuntimePanel(options = {}) {
      const inline = !!options.inline;
      // In place the panel sits inside the host page's own form, so it is not
      // a form itself and its primary button does not submit the host.
      const element = document.createElement(inline ? "div" : "form");
      element.className = "portalRuntimePanel portalRuntimeSettingsForm";
      element.classList.toggle("is-inline", inline);
      element.dataset.sessionRestartForm = "1";
      element.innerHTML = '<div class="portalRuntimeSettingsBody">' +
        '<div data-resident-backend-notices role="status" hidden></div>' +
        '<section class="portalRuntimeSection" data-runtime-section="account">' +
        '<div class="portalRuntimeSettingsGrid portalRuntimeAccountGrid">' +
        '<label class="portalRuntimeSettingsField"><span>AI 后端</span><select data-session-backend></select></label>' +
        '<label class="portalRuntimeSettingsField"><span>账号</span><select data-session-target></select></label></div>' +
        '<p class="portalRuntimeSettingsHint" data-switch-context-status role="status"></p>' +
        '<p class="portalRuntimeSettingsHint" data-switch-context-description role="status"></p></section>' +
        '<section class="portalRuntimeSection" data-runtime-section="initialization" hidden>' +
        '<p class="portalRuntimeSettingsHint">继续当前初始化，保留继承的上下文及已排队的消息。credits 确认只适用于这次初始化及它创建的 Session。</p></section>' +
        '<section class="portalRuntimeSection" data-runtime-section="parameters"><div data-runtime-editor-host></div><div data-target-editor-host hidden></div></section>' +
        '<label class="portalRuntimeSettingsField portalRuntimeCredits" data-session-credits-confirmation hidden><span data-session-credits-description></span>' +
        '<span><input type="checkbox" data-session-credits-consent> 我明确授权本次目标 Session 使用该账号的付费 credits；不授权后续新建、重置或迁移的会话。</span></label>' +
        '<p class="portalRuntimeSettingsHint" data-runtime-save-hint role="status"></p>' +
        '</div>' +
        '<div class="portalDetailFooter portalRuntimePanelFooter"><button class="portalButton is-danger" data-session-restart-cancel type="button" hidden>取消操作</button>' +
        '<button class="portalButton" data-runtime-panel-reset type="button">' + (inline ? "还原" : "取消") + '</button>' +
        '<button class="portalButton is-primary" data-session-restart-submit type="' + (inline ? "button" : "submit") + '">保存</button></div>';
      const find = selector => element.querySelector(selector);
      const target = find("[data-session-target]"), backend = find("[data-session-backend]");
      const submit = find("[data-session-restart-submit]"), cancel = find("[data-session-restart-cancel]");
      const reset = find("[data-runtime-panel-reset]"), saveHint = find("[data-runtime-save-hint]");
      const creditsConsent = find("[data-session-credits-consent]");
      const editor = createRuntimeSettingsEditor(), targetEditor = createRuntimeSettingsEditor();
      editor.element.dataset.runtimeEditor = "current"; targetEditor.element.dataset.runtimeEditor = "target";
      find("[data-runtime-editor-host]").replaceWith(editor.element);
      find("[data-target-editor-host]").append(targetEditor.element);
      const progress = createSessionRestartProgress();
      find("[data-resident-backend-notices]").after(progress.element);
      let current = {}, restart, cancelRestart, unobserve, lastOpen = [];
      let requestKey, submitting = false, tracking = false, restartView = {}, catalogRevision = 0, targetReady = false;
      let baseline = "";
      const restartService = window.PortalResidentService;
      const initializing = () => Boolean(current.initialization?.initialization_id)
        && ["pending", "running", "failed"].includes(current.initialization.status);
      const requestId = () => "portal-session-restart:" + globalThis.PortalUIPrimitives.randomUUID();
      const selected = () => { try { return JSON.parse(target.value); } catch (_) { return []; } };
      const canonicalAccount = (provider, account) => {
        const row = current.accounts?.[provider]?.find(item => item.id === account || item.account_id === account);
        return row?.account_id || account;
      };
      const sameAccount = () => {
        const [provider, account] = selected();
        return provider === current.source?.provider && canonicalAccount(provider, account) === canonicalAccount(provider, current.source?.account_id);
      };
      const crossProvider = () => selected()[0] && selected()[0] !== current.source?.provider;
      const canSwitch = () => Boolean(restart) && (current.providers || []).length > 0;
      const switching = () => canSwitch() && !initializing() && Boolean(target.value) && !sameAccount();
      const targetContextOptions = () => {
        const [provider, account] = selected();
        return current.accounts?.[provider]?.find(row => row.id === account || row.account_id === account)?.context_options || [];
      };
      // The server owns transfer capabilities. Prefer preservation, then summary
      // handoff; never turn an unsupported transfer into a blank session.
      const contextChoice = () => ["preserve", "compact"].map(id => targetContextOptions().find(row => row.id === id && row.enabled)).find(Boolean);
      const readEditor = () => { try { return JSON.stringify(editor.read()); } catch (_) { return "invalid"; } };
      const parametersChanged = () => Boolean(current.onSave) && readEditor() !== baseline;
      const renderContext = () => {
        const choice = contextChoice();
        find("[data-switch-context-status]").textContent = window.PortalI18n.t(!target.value || sameAccount() ? ""
          : choice?.id === "preserve" ? choice.description
          : choice ? "目标账号无法完整保留上下文，将通过摘要交接后继续。"
          : "目标账号暂不支持保留上下文或摘要交接，无法切换；原会话保持不变。");
      };
      const update = () => {
        const locked = submitting || tracking;
        const retry = restartView.restart?.status === "confirming";
        const init = initializing(), switchingAccount = switching(), cross = switchingAccount && crossProvider();
        find('[data-runtime-section="account"]').hidden = !canSwitch() || init;
        find('[data-runtime-section="initialization"]').hidden = !init;
        editor.element.hidden = cross || !current.onSave;
        find('[data-runtime-section="parameters"]').hidden = !cross && !current.onSave;
        find("[data-target-editor-host]").hidden = !cross;
        const creditTarget = init ? [current.source?.provider, current.source?.account_id] : selected();
        const showCredits = current.credits_confirmation?.allowed === true && (switchingAccount || init)
          && creditTarget[0] === "codex" && Boolean(creditTarget[1]);
        find("[data-session-credits-confirmation]").hidden = !showCredits;
        creditsConsent.disabled = locked || !showCredits;
        find("[data-session-credits-description]").textContent = window.PortalI18n.t("本次目标账号：") + canonicalAccount(...creditTarget);
        target.disabled = locked; backend.disabled = locked;
        editor.element.disabled = locked; targetEditor.element.disabled = locked;
        const choice = contextChoice();
        find("[data-switch-context-description]").textContent = window.PortalI18n.t(switchingAccount && choice?.id === "compact" ? choice.description : "");
        const changed = !cross && parametersChanged();
        let label = "保存", disabled = locked || !changed;
        if (retry) { label = "重试同一请求"; disabled = submitting; }
        else if (init) { label = "继续当前初始化"; disabled = locked || !restart; }
        else if (switchingAccount) {
          label = choice?.id === "compact" ? "交接并切换" : "切换账号";
          disabled = locked || !choice || (cross && !targetReady);
        }
        submit.disabled = disabled;
        submit.textContent = window.PortalI18n.t(submitting ? "正在提交…" : label);
        saveHint.textContent = window.PortalI18n.t(tracking || retry || init ? ""
          : switchingAccount ? (choice ? "等待正在运行的工作完成后切换；切换失败时保留原会话。" : "")
          : changed ? "参数从下一轮生效，不会重新开始会话。" : "");
        reset.disabled = submitting || (inline && !switchingAccount && !changed);
        cancel.hidden = !tracking; cancel.disabled = submitting || restartView.restart?.status === "cancelling";
      };
      const loadTarget = async () => {
        const revision = ++catalogRevision;
        const cross = switching() && crossProvider();
        targetReady = false; update();
        if (!cross) return;
        const [provider, account] = selected();
        try {
          const catalog = await restartService.runtimeCatalog({backend: provider, accountId: canonicalAccount(provider, account)});
          if (revision !== catalogRevision || !element.isConnected) return;
          targetEditor.open({}, {catalog, showApiAccount: false, hint: window.PortalI18n.t("请选择目标 Provider 的模型。")});
          targetReady = true;
        } catch (error) {
          if (revision === catalogRevision) window.PortalUI.toast(window.PortalUI.errorMessage("读取目标模型", error), "error");
        } finally { if (revision === catalogRevision) update(); }
      };
      const detach = () => { catalogRevision++; unobserve?.(); unobserve = null; };
      const done = context => { detach(); options.onDone?.(context); };
      // The account list follows the chosen backend; returning to the current
      // backend preselects the current account.
      const fillAccounts = provider => {
        const choices = (current.accounts?.[provider] || []).map(row =>
          new Option(row.label || row.account_id || row.id, JSON.stringify([provider, row.id])));
        target.replaceChildren(new Option(window.PortalI18n.t("选择账号"), ""), ...choices);
        const sourceAccount = provider === current.source?.provider
          && current.accounts?.[provider]?.find(row => row.id === current.source.account_id || row.account_id === current.source.account_id);
        target.value = sourceAccount ? JSON.stringify([provider, sourceAccount.id])
          : choices.length === 1 ? choices[0].value : "";
      };
      element.addEventListener("change", event => {
        if (event.target === backend) fillAccounts(backend.value);
        if (event.target === target || event.target === backend) {
          creditsConsent.checked = false; requestKey = requestId(); renderContext(); void loadTarget(); return;
        }
        if (event.target === creditsConsent) { requestKey = requestId(); return; }
        update();
      });
      reset.addEventListener("click", () => {
        if (reset.disabled) return;
        if (inline) panel.open(...lastOpen);
        else done({source: "button"});
      });
      cancel.addEventListener("click", async () => {
        const restartId = restartView.restart?.restart_id || current.restart?.restart_id;
        if (!restartId || !cancelRestart || cancel.disabled) return;
        submitting = true; update();
        try {
          const result = await cancelRestart({restart_id: restartId});
          restartView.restart = result; tracking = sessionRestartActive(result); progress.update(restartView);
        } catch (error) {window.PortalUI.toast(window.PortalUI.errorMessage("取消操作", error), "error");}
        finally {submitting = false; update();}
      });
      const submitPanel = async () => {
        if (submit.disabled) return;
        const retry = restartView.restart?.status === "confirming";
        try {
          const consentFor = (provider, account) => creditsConsent.checked && !creditsConsent.disabled
            ? {account_id: canonicalAccount(provider, account), confirmed: true} : null;
          if (retry || initializing() || switching()) {
            if (!restart) return;
            const [provider, account] = initializing() || !switching() ? [current.source?.provider, current.source?.account_id] : selected();
            const cross = !retry && switching() && crossProvider();
            const runtime = cross ? targetEditor.read({complete: true}) : {};
            if (cross && !runtime.model) throw new Error("请选择目标 Provider 的模型。");
            // Parameters edited together with a same-Provider switch are saved first.
            const parameters = !retry && !cross && parametersChanged() ? editor.read() : null;
            const consent = consentFor(provider, account);
            submitting = true; update();
            if (!retry && initializing()) {
              await restartService.retryResidentInitialization(current.resident_id, {
                expected_initialization_id: current.initialization.initialization_id,
                ...(consent ? {credits_consent: consent} : {}), idempotency_key: requestKey,
              });
              await restartService.refreshSessionRestart?.(current.resident_id);
              done({accepted: true});
              return;
            }
            if (parameters) {
              const saved = await current.onSave(parameters);
              current.runtime_settings = {...current.runtime_settings, ...parameters, ...(saved?.settings || {})};
            }
            const result = retry
              ? await restartService.retrySessionRestart(current.resident_id)
              : await restart({operation: contextChoice()?.id === "preserve" ? "switch_account" : "restart",
                target_provider: provider, target_account: account,
                context_strategy: contextChoice().id,
                ...(consent ? {credits_consent: consent} : {}),
                active_execution_policy: "wait", resume_failed_input: false, ...runtime, idempotency_key: requestKey});
            current.restart = result; restartView.restart = result;
            tracking = sessionRestartActive(result); progress.update(restartView);
            if (result?.status === "completed") done({accepted: true});
            return;
          }
          const values = editor.read();
          submitting = true; update();
          await current.onSave(values);
          done({accepted: true});
        } catch (error) {
          tracking = sessionRestartActive(restartView.restart);
          if (!error.restartPending) {
            if (!initializing()) requestKey = requestId();
            window.PortalUI.toast(window.PortalUI.errorMessage(switching() ? "切换账号" : "保存运行参数", error), "error");
          }
        } finally {submitting = false; if (element.isConnected) update();}
      };
      if (inline) submit.addEventListener("click", () => void submitPanel());
      else element.addEventListener("submit", event => { event.preventDefault(); void submitPanel(); });
      const panel = {
        element, detach,
        focusTarget: () => (current.initial_view === "parameters" && current.onSave) || !canSwitch() ? editor.model : target,
        open(config = {}, onRestart = null, onReload = null, onCancel = null) {
          lastOpen = [config, onRestart, onReload, onCancel];
          detach();
          current = {...config};
          restart = onRestart; cancelRestart = onCancel; requestKey = requestId();
          submitting = false; tracking = sessionRestartActive(config.restart); restartView = {restart: config.restart};
          creditsConsent.checked = false;
          const providers = (current.providers || []).filter(provider => (current.accounts?.[provider.id] || []).length);
          backend.replaceChildren(...providers.map(provider => new Option(provider.label || provider.id, provider.id)));
          backend.value = providers.some(provider => provider.id === current.source?.provider) ? current.source.provider : providers[0]?.id || "";
          fillAccounts(backend.value);
          renderContext();
          editor.open(current.runtime_settings || {}, {catalog: current.runtime_catalog, showApiAccount: false, hint: ""});
          baseline = readEditor();
          progress.update(restartView);
          void showResidentBackendNotices(find("[data-resident-backend-notices]"), current.resident_id, {accountId: current.source?.account_id, settings: current.runtime_settings || {}});
          update(); void loadTarget();
          if (current.resident_id && restartService && restart) {
            unobserve = restartService.observeSessionRestart(current.resident_id, value => {
              const wasTracking = tracking;
              if (value.config?.initialization && value.config.initialization.initialization_id !== current.initialization?.initialization_id) {
                creditsConsent.checked = false; requestKey = requestId();
              }
              if (value.config?.initialization) current.initialization = value.config.initialization;
              restartView = value; tracking = sessionRestartActive(value.restart);
              const endpoint = tracking ? value.restart?.target : null;
              const row = endpoint && current.accounts?.[endpoint.provider]?.find(item => item.id === endpoint.account_id || item.account_id === endpoint.account_id);
              if (row) {
                const selectedValue = JSON.stringify([endpoint.provider, row.id]);
                if (target.value !== selectedValue) {
                  if (backend.value !== endpoint.provider) {backend.value = endpoint.provider; fillAccounts(endpoint.provider);}
                  target.value = selectedValue; renderContext();
                }
              }
              progress.update(value); update();
              if (wasTracking && ["completed", "cancelled"].includes(value.restart?.status)) done({accepted: true});
            });
          }
        },
        destroy() { detach(); progress.destroy(); element.remove(); },
      };
      return panel;
    }

    function createSessionRestartDialog(options = {}) {
      const id = String(options.id || "portalSessionRestart_" + Math.random().toString(16).slice(2));
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay portalSessionRestartOverlay";
      overlay.hidden = true;
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = '<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="' + escapeHtml(id) + 'Title">' +
        '<div class="portalDetailHeader" data-portal-detail-header><div class="portalRuntimeSettingsTitle" id="' + escapeHtml(id) + 'Title">运行设置</div>' +
        '<button class="portalDetailClose" data-portal-icon="close" data-session-restart-close type="button" aria-label="关闭"></button></div></div>';
      document.body.append(overlay);
      let dialog = null;
      const panel = createRuntimePanel({onDone: context => dialog?.close(context)});
      panel.element.classList.add("portalDetailForm");
      panel.element.querySelector(".portalRuntimeSettingsBody").dataset.portalDetailBody = "";
      panel.element.querySelector(".portalRuntimePanelFooter").dataset.portalDetailFooter = "";
      overlay.querySelector("[data-portal-detail-surface]").append(panel.element);
      dialog = window.PortalUI.createDialogController({
        modal: overlay, manageHidden: true, escapeClose: true, backdropClose: true,
        initialFocus: () => panel.focusTarget(),
        onClose: context => {panel.detach(); options.onClose?.(context);},
      });
      const close = context => dialog.close(context);
      overlay.querySelector("[data-session-restart-close]").addEventListener("click", () => close({source: "button"}));
      return {
        open(...args) { panel.open(...args); dialog.open({focusTarget: panel.focusTarget()}); },
        close,
        destroy() {panel.destroy(); dialog.destroy(); overlay.remove();},
      };
    }
    return Object.freeze({
      residentRuntimeSettingRows, residentRuntimeStatusText, residentRuntimeMetaHtml,
      notifyRuntimeSettingsSaved, showResidentBackendNotices, createRuntimeSettingsDialog,
      sessionRestartActive, createSessionRestartProgress, createRuntimePanel, createSessionRestartDialog,
    });
  }

  window.PortalChatWorkspaceRuntimeSettings = Object.freeze({
    createRuntimeSettings, dependencies: DEPENDENCIES, residentRuntimeSettingRows,
    residentRuntimeStatusText, notifyRuntimeSettingsSaved, showResidentBackendNotices,
    sessionRestartActive,
  });
})();
