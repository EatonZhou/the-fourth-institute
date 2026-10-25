(() => {
  if (window.PortalChatWorkspaceRuntimeSettings) return;

  // A Resident's runtime controls: the Runtime Settings dialog (account, model,
  // mode, effort, speed, permissions), the notices shown after saving, and the
  // session restart dialog with its progress.
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
      if (label) rows.push(["Permissions", label]);
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

    function createRuntimeSettingsDialog(options = {}) {
      const id = String(options.id || `portalRuntimeSettings_${Math.random().toString(16).slice(2)}`);
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay";
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">Runtime Settings</div>
        <button class="portalDetailClose" data-portal-icon="close" data-runtime-settings-close type="button" aria-label="关闭 runtime 设置" title="关闭"></button>
      </div>
      <form class="portalDetailForm portalRuntimeSettingsForm" data-runtime-settings-form>
        <div data-resident-backend-notices role="status" hidden></div>
        <div class="portalRuntimeSettingsGrid">
          <label class="portalRuntimeSettingsField" hidden><span>API 账号</span><select data-runtime-setting="backend_route_id"></select></label>
          <label class="portalRuntimeSettingsField"><span>Model</span><select data-runtime-setting="model"></select></label>
          <label class="portalRuntimeSettingsField"><span>Mode</span><select data-runtime-setting="collaboration_mode"><option value="default">Default · 默认执行</option><option value="plan">Plan · 先制定计划</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Effort</span><select data-runtime-setting="reasoning_effort"></select></label>
          <label class="portalRuntimeSettingsField"><span>Speed</span><select data-runtime-setting="service_tier"><option value="">Standard (default)</option><option value="priority">Fast</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Permissions</span><select data-runtime-setting="permission_preset"><option value="">Full Access (default)</option><option value="default">Ask</option><option value="auto-review">Auto-review</option><option value="full-access">Full Access</option></select></label>
        </div>
        <p class="portalRuntimeSettingsHint" data-runtime-settings-hint>设置从下一轮生效。</p>
        <button class="portalButton is-quiet" data-runtime-settings-restart type="button" hidden>切换 Backend／账号或重启…</button>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" data-runtime-settings-close type="button">取消</button>
          <button class="portalButton is-primary" data-runtime-settings-submit type="submit">保存</button>
        </div>
      </form>
    </div>`;
      document.body.appendChild(overlay);
      const form = overlay.querySelector("[data-runtime-settings-form]");
      const submit = overlay.querySelector("[data-runtime-settings-submit]");
      const apiFields = window.PortalModelApiSettings.create();
      form.querySelector(".portalRuntimeSettingsGrid").after(apiFields.element);
      const model = overlay.querySelector('[data-runtime-setting="model"]');
      const apiAccount = overlay.querySelector('[data-runtime-setting="backend_route_id"]');
      let apiRoutes = [], initialApiRoute = "";
      const mode = overlay.querySelector('[data-runtime-setting="collaboration_mode"]');
      const effort = overlay.querySelector('[data-runtime-setting="reasoning_effort"]');
      const tier = overlay.querySelector('[data-runtime-setting="service_tier"]');
      const permissions = overlay.querySelector('[data-runtime-setting="permission_preset"]');
      const setOptions = (select, values, current, defaultLabel) => {
        const choices = [...new Set(["", ...values, current].filter(value => value !== undefined && value !== null).map(value => String(value)))];
        select.innerHTML = choices.map(value => window.PortalI18n.html`<option value="${escapeHtml(value)}">${escapeHtml(value || defaultLabel)}</option>`).join("");
        select.value = String(current || "");
      };
      let currentSettings = {};
      let initialSettings = {};
      let providerCatalog = null;
      const syncModelControls = () => {
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
        effort.closest(".portalRuntimeSettingsField").hidden = knownModel && !efforts.length && !currentEffort;
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
        tierField.hidden = !tierIds.length;
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
      let save = null;
      let restartSession = null;
      const dialog = window.PortalUI.createDialogController({
        modal: overlay,
        manageHidden: true,
        escapeClose: true,
        backdropClose: true,
        initialFocus: () => model,
        onClose: context => options.onClose?.(context),
      });
      const close = context => dialog?.close?.(context);
      const restartButton = overlay.querySelector("[data-runtime-settings-restart]");
      restartButton.addEventListener("click", async () => {
        if (!restartSession || submit.disabled) return;
        const restart = restartSession;
        close({ source: "session-restart" });
        try { await restart(); }
        catch (error) { window.PortalUI.toast(error.message, "error"); }
      });
      overlay.querySelectorAll("[data-runtime-settings-close]").forEach(button => {
        button.addEventListener("click", () => close({ source: "button" }));
      });
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!save || submit.disabled) return;
        submit.disabled = true;
        submit.textContent = window.PortalI18n.t("保存中");
        try {
          const selected = { model: String(model.value || "").trim() };
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
              && permissions.value !== String(initialSettings.permission_preset || "")) {
              selected.permission_preset = String(permissions.value || "").trim();
            }
            if (providerCatalog && (modelChanged || effortChanged)) {
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
          await save(selected);
          close({ accepted: true });
        } catch (error) {
          window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存 runtime 设置"), error), "error");
        } finally {
          submit.disabled = false;
          submit.textContent = window.PortalI18n.t("保存");
        }
      });
      return {
        open(settings = {}, onSave = null, dialogOptions = {}) {
          currentSettings = settings && typeof settings === "object" ? settings : {};
          initialSettings = { ...currentSettings };
          void showResidentBackendNotices(overlay.querySelector("[data-resident-backend-notices]"), dialogOptions.residentId, {
            settings: dialogOptions.residentSettings, accountId: dialogOptions.accountId,
          });
          overlay.querySelector(".portalRuntimeSettingsTitle").textContent = dialogOptions.title || window.PortalI18n.t("Runtime Settings");
          providerCatalog = dialogOptions.catalog || null;
          apiFields.open(currentSettings, providerCatalog);
          apiRoutes = providerCatalog?.api_settings ? providerCatalog.api_routes || [providerCatalog] : [];
          initialApiRoute = providerCatalog?.api_settings ? providerCatalog.backend_route_id || providerCatalog.account_id : "";
          apiAccount.closest(".portalRuntimeSettingsField").hidden = !apiRoutes.length;
          apiAccount.replaceChildren(...apiRoutes.map(route => new Option(route.account_id || route.backend_route_id, route.backend_route_id || route.account_id)));
          apiAccount.value = initialApiRoute;
          model.title = "";
          save = onSave;
          restartSession = dialogOptions.onSessionRestart || null;
          restartButton.hidden = !restartSession;
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
          overlay.querySelector("[data-runtime-settings-hint]").textContent = dialogOptions.hint || window.PortalI18n.t("设置从下一轮生效。");
          dialog?.open?.({ focusTarget: model });
        },
        close,
        destroy() {
          dialog?.destroy?.();
          overlay.remove();
        },
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
      dismissButton.setAttribute("aria-label", window.PortalI18n.t("已知悉，关闭本次迁移失败提示"));
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
        accepted: window.PortalI18n.t("已受理，正在准备"), queued: window.PortalI18n.t("已受理，等待调度"), waiting_current_execution: window.PortalI18n.t("已受理，等待本轮完成后迁移"),
        waiting_quota_reserve: window.PortalI18n.t("已受理，等待额度"), waiting_capacity: window.PortalI18n.t("已受理，等待执行容量"),
        waiting_dispatch_interval: window.PortalI18n.t("已受理，等待调度间隔"), waiting_session_binding: window.PortalI18n.t("已受理，等待 Session 空闲"),
        creating_session: window.PortalI18n.t("正在创建新 Session"), initializing: window.PortalI18n.t("正在初始化交接"),
        switching: window.PortalI18n.t("正在切换绑定"), cancelling: window.PortalI18n.t("正在取消迁移"),
        completed: window.PortalI18n.t("迁移已完成"), failed: window.PortalI18n.t("迁移失败"), cancelled: window.PortalI18n.t("迁移已取消"),
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
        detailsButton.textContent = failed ? window.PortalI18n.t("查看详情") : window.PortalI18n.t("查看迁移进度");
        dismissButton.hidden = !failed || !key || !restart.restart_id;
        const label = endpoint => endpoint?.account_id || endpoint?.provider || window.PortalI18n.t("当前 Session");
        const phase = restart.status === "accepted" ? restart.phase || "accepted" : restart.status;
        heading.textContent = `Session：${label(restart.source)} → ${label(restart.target)} · ${labels[phase] || window.PortalI18n.t("迁移进行中")}`;
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
            ? window.PortalI18n.t("迁移未完成，原会话已保留。请核对目标设置后重新迁移。")
            : window.PortalI18n.t("迁移未完成。请核对当前会话与目标设置后重新迁移。"))
          : current.error ? window.PortalI18n.t("暂时无法同步迁移状态，请稍后查看；请勿重复提交。") : sessionRestartActive(restart)
          ? (restart.status === "cancelling" ? window.PortalI18n.t("取消请求已受理，原 Session 将继续保留。") : window.PortalI18n.t("关闭窗口不会取消迁移；需要终止时请使用“取消迁移”。"))
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

    function createSessionRestartDialog(options = {}) {
      const id = String(options.id || `portalSessionRestart_${Math.random().toString(16).slice(2)}`);
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay portalSessionRestartOverlay";
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">Session 操作</div>
        <button class="portalDetailClose" data-portal-icon="close" data-session-restart-close type="button" aria-label="关闭 Session 操作" title="关闭"></button>
      </div>
      <form class="portalDetailForm portalRuntimeSettingsForm" data-session-restart-form>
        <p class="portalRuntimeSettingsHint" data-session-restart-source></p>
        <div data-resident-backend-notices role="status" hidden></div>
        <details class="portalSessionBindingSnapshot" data-session-binding-snapshot><summary>当前会话绑定快照</summary><dl data-session-binding-fields></dl><p class="portalRuntimeSettingsHint">状态版本（Generation）用于核验此会话绑定是否变化，不是 Worker 进程代际；这里展示打开弹窗时的权威快照。</p></details>
        <div class="portalRuntimeSettingsGrid">
          <label class="portalRuntimeSettingsField"><span>目标 Provider</span><select data-session-restart="provider"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-account-field><span>Account / Route</span><select data-session-restart="account"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-model-field hidden><span>目标模型</span><select data-session-restart="model"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-effort-field hidden><span>推理强度</span><select data-session-restart="effort"></select></label>
          <label class="portalRuntimeSettingsField"><span>上下文方案</span><select data-session-restart="strategy"></select></label>
          <label class="portalRuntimeSettingsField"><span>正在运行的工作</span><select data-session-restart="active-execution"><option value="wait">等待完成</option><option value="cancel">取消后重启</option></select></label>
          <label class="portalRuntimeSettingsField"><span>失败的工作</span><span><input type="checkbox" data-session-restart="resume-failed" checked> 切换成功后继续最近的失败输入</span></label>
        </div>
        <p class="portalRuntimeSettingsHint" data-session-restart-hint></p>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" data-session-reload type="button">只刷新数据</button>
          <button class="portalButton is-danger" data-session-restart-cancel type="button" hidden>取消迁移</button>
          <button class="portalButton" data-session-restart-close type="button">关闭</button>
          <button class="portalButton is-primary" data-session-restart-submit type="submit">重启 Session</button>
        </div>
      </form>
    </div>`;
      document.body.appendChild(overlay);
      const form = overlay.querySelector("[data-session-restart-form]");
      const submit = overlay.querySelector("[data-session-restart-submit]");
      const cancelMigration = overlay.querySelector("[data-session-restart-cancel]");
      const provider = overlay.querySelector('[data-session-restart="provider"]');
      const account = overlay.querySelector('[data-session-restart="account"]');
      const model = overlay.querySelector('[data-session-restart="model"]');
      const effort = overlay.querySelector('[data-session-restart="effort"]');
      const modelField = overlay.querySelector('[data-session-restart-model-field]');
      const effortField = overlay.querySelector('[data-session-restart-effort-field]');
      const strategy = overlay.querySelector('[data-session-restart="strategy"]');
      const activeExecution = overlay.querySelector('[data-session-restart="active-execution"]');
      const resumeFailed = overlay.querySelector('[data-session-restart="resume-failed"]');
      const accountField = overlay.querySelector("[data-session-restart-account-field]");
      const source = overlay.querySelector("[data-session-restart-source]");
      const bindingFields = overlay.querySelector("[data-session-binding-fields]");
      const hint = overlay.querySelector("[data-session-restart-hint]");
      const apiFields = window.PortalModelApiSettings.create();
      hint.before(apiFields.element);
      let restart = null;
      let cancelRestart = null;
      let reload = null;
      let current = {};
      let requestKey = "";
      let submitting = false;
      let tracking = false;
      let restartView = {};
      let unobserve = null;
      let modelCatalog = null, catalogLoading = false, catalogRevision = 0;
      const createRequestKey = () => `portal-session-restart:${globalThis.PortalUIPrimitives.randomUUID()}`;
      const restartService = window.PortalResidentService;
      const progress = createSessionRestartProgress();
      source.after(progress.element);
      const dialog = window.PortalUI.createDialogController({
        modal: overlay,
        manageHidden: true,
        escapeClose: true,
        backdropClose: true,
        initialFocus: () => provider,
        onClose: context => { unobserve?.(); unobserve = null; options.onClose?.(context); },
      });
      const close = context => dialog?.close?.(context);
      const optionsHtml = (items, fallback = []) => {
        const values = Array.isArray(items) && items.length ? items : fallback;
        return values.map(item => {
          const value = typeof item === "string" ? item : String(item?.id || "");
          const label = typeof item === "string" ? item : String(item?.label || value);
          return window.PortalI18n.html`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
        }).join("");
      };
      const update = () => {
        const routed = !!current.accounts?.[provider.value]?.some(row => row.id);
        const selectedDefinition = current.providers?.find(row => row.id === provider.value);
        const nativeOption = [...strategy.options].find(option => option.value === "native");
        if (nativeOption) nativeOption.disabled = selectedDefinition?.operations?.includes("fork") === false;
        if (strategy.value === "native" && nativeOption?.disabled) strategy.value = "smart";
        const hasSession = !!current.source?.has_session;
        const apiMigration = current.source?.provider === "provider_api" || provider.value === "provider_api";
        const locked = submitting || tracking;
        const retry = restartView.restart?.status === "confirming";
        accountField.hidden = !routed;
        accountField.style.display = routed ? "" : "none";
        provider.disabled = locked;
        account.disabled = !routed || locked;
        strategy.disabled = (!hasSession && current.source?.provider !== "provider_api" && provider.value !== "provider_api") || locked;
        activeExecution.disabled = locked;
        model.disabled = locked || catalogLoading;
        effort.disabled = locked || catalogLoading;
        submit.disabled = submitting || catalogLoading || !restart || (tracking && !retry) || (routed && !account.value)
          || (provider.value === "provider_api" && !modelCatalog);
        cancelMigration.hidden = !tracking;
        cancelMigration.disabled = submitting || restartView.restart?.status === "cancelling";
        submit.textContent = retry && !submitting ? window.PortalI18n.t("重试同一请求") : tracking && !submitting ? window.PortalI18n.t("查看上方迁移状态") : submitting
          ? (hasSession || apiMigration ? window.PortalI18n.t("正在切换…") : window.PortalI18n.t("正在保存…"))
          : apiMigration ? window.PortalI18n.t("迁移 Backend")
          : (hasSession ? restartView.restart?.status === "failed" ? window.PortalI18n.t("重新迁移") : window.PortalI18n.t("重启 Session") : routed ? window.PortalI18n.t("保存账号设置") : window.PortalI18n.t("保存 Provider"));
        const selectedStrategy = strategy.value || "smart";
        hint.textContent = provider.value === "provider_api"
          ? window.PortalI18n.t("切换到直接调用模型 API，不创建原生 Session。按下方历史设置发送上下文；选择全新上下文时从切换后开始积累历史，页面旧对话仍保留。")
          : !hasSession && current.source?.provider !== "provider_api"
          ? window.PortalI18n.t("仅保存 Provider / 账号选择，已有对话和 Fork 上下文保持不变；下次运行时使用新配置。")
          : selectedStrategy === "native"
          ? window.PortalI18n.t("原生 Fork 仅适用于同一 Provider/Account；旧 session 会保留为只读 generation。")
          : selectedStrategy === "fresh"
            ? window.PortalI18n.t("只初始化新的系统上下文，不带入旧对话；旧 session 会保留。")
            : selectedStrategy === "visible"
              ? window.PortalI18n.t("把 Portal 当前可见对话作为资料交给新 session，适合跨 Provider 迁移。")
              : selectedStrategy === "compact"
                ? window.PortalI18n.t("迁移最近的重要对话与当前工作，适合跨 Provider，开销较小。")
                : window.PortalI18n.t("系统优先使用同 Provider 原生 Fork，否则使用精简交接。");
      };
      overlay.querySelectorAll("[data-session-restart-close]").forEach(button => {
        button.addEventListener("click", () => close({ source: "button" }));
      });
      const populateAccounts = (selected = "") => {
        account.innerHTML = optionsHtml(current.accounts?.[provider.value], [{ id: "", label: window.PortalI18n.t("自动选择") }]);
        if ([...account.options].some(option => option.value === selected)) account.value = selected;
      };
      const populateEfforts = (selected = "") => {
        const values = modelCatalog?.efforts_by_model?.[model.value] || [];
        effort.innerHTML = optionsHtml(values, [{ id: "", label: window.PortalI18n.t("默认") }]);
        effort.value = values.includes(selected) ? selected : modelCatalog?.default_efforts_by_model?.[model.value] || values[0] || "";
        effortField.hidden = !modelCatalog || !values.length;
      };
      const loadModelCatalog = async () => {
        const revision = ++catalogRevision;
        modelCatalog = null; modelField.hidden = true; effortField.hidden = true;
        apiFields.open({}, {});
        if (!current.choose_runtime_settings && provider.value !== "provider_api") { catalogLoading = false; return; }
        catalogLoading = true; update();
        const route = current.accounts?.[provider.value]?.find(row => row.id === account.value);
        const query = new URLSearchParams({ backend: provider.value });
        if (provider.value === "provider_api") query.set("account_id", account.value);
        else if (route?.account_id) query.set("account_id", route.account_id);
        try {
          const catalog = await window.PortalResidentService.runtimeCatalog({
            backend: query.get("backend"), accountId: query.get("account_id") || "",
          });
          if (revision !== catalogRevision) return;
          modelCatalog = catalog;
          model.innerHTML = optionsHtml(catalog.models);
          const saved = current.runtime_settings || {};
          model.value = catalog.models?.includes(saved.model) ? saved.model : catalog.default_model || model.options[0]?.value || "";
          modelField.hidden = !model.value;
          populateEfforts(saved.reasoning_effort);
          if (catalog.api_settings) {
            const sameRoute = current.source?.provider === "provider_api" && account.value === current.source?.account_id;
            apiFields.open(sameRoute ? saved : {model: model.value, prompt_context: {history: {
              enabled: catalog.api_settings.default_history, max_tokens: catalog.api_settings.history_max_tokens,
            }}}, catalog);
          }
        } catch (error) {
          if (revision === catalogRevision) window.PortalUI.toast(provider.value === "provider_api"
            ? window.PortalI18n.text`无法读取目标模型，请重新选择账号后重试：${error.message}`
            : window.PortalI18n.text`无法读取目标模型，迁移将使用目标账号默认设置：${error.message}`, "error");
        } finally {
          if (revision === catalogRevision) { catalogLoading = false; update(); }
        }
      };
      provider.addEventListener("change", () => {
        populateAccounts();
        void loadModelCatalog();
        update();
      });
      account.addEventListener("change", () => { void loadModelCatalog(); });
      model.addEventListener("change", () => { populateEfforts(); apiFields.syncModel(model.value); });
      strategy.addEventListener("change", update);
      overlay.querySelector("[data-session-reload]").addEventListener("click", async () => {
        close({ source: "reload" });
        await reload?.();
      });
      cancelMigration.addEventListener("click", async () => {
        const restartId = String(restartView.restart?.restart_id || current.restart?.restart_id || "");
        if (!restartId || !cancelRestart || cancelMigration.disabled) return;
        submitting = true;
        update();
        try {
          const result = await cancelRestart({ restart_id: restartId });
          current.restart = result;
          restartView.restart = result;
          tracking = sessionRestartActive(result);
          window.PortalUI.toast(window.PortalI18n.t("Session 迁移取消请求已受理，原 Session 将保留。"), "success");
        } catch (error) {
          window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("取消 Session 迁移"), error), "error");
        } finally {
          submitting = false;
          update();
        }
      });
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!restart || submit.disabled) return;
        submitting = true;
        tracking = true;
        update();
        source.textContent = current.source?.has_session || current.source?.provider === "provider_api"
          ? window.PortalI18n.t("正在提交迁移请求，请等待后台受理回执；请勿重复操作。")
          : window.PortalI18n.t("正在保存 Provider / 账号选择，不创建 Session。");
        try {
          const result = restartView.restart?.status === "confirming"
            ? await restartService.retrySessionRestart(current.resident_id)
            : await restart({
            target_provider: provider.value,
            target_account: account.value,
            context_strategy: strategy.value || "smart",
            active_execution_policy: activeExecution.value || "wait",
            resume_failed_input: resumeFailed.checked,
            ...apiFields.read(),
            ...(modelCatalog && model.value ? { model: model.value, ...(effort.value ? { reasoning_effort: effort.value } : {}) } : {}),
            idempotency_key: requestKey,
          });
          current.restart = result;
          if (result?.status === "completed") close({ accepted: true });
        } catch (error) {
          tracking = sessionRestartActive(restartView.restart);
          if (!error.restartPending) {
            requestKey = createRequestKey();
            window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("重启 Session"), error), "error");
          }
        } finally {
          submitting = false;
          update();
        }
      });
      return {
        open(config = {}, onRestart = null, onReload = null, onCancel = null) {
          current = config && typeof config === "object" ? config : {};
          void showResidentBackendNotices(overlay.querySelector("[data-resident-backend-notices]"), current.resident_id, {
            accountId: current.source?.account_id, settings: current.runtime_settings || {},
          });
          submitting = false;
          tracking = sessionRestartActive(current.restart);
          restartView = { restart: current.restart };
          unobserve?.();
          requestKey = createRequestKey();
          restart = onRestart;
          cancelRestart = onCancel;
          reload = onReload;
          provider.innerHTML = optionsHtml(current.providers);
          const sourceProvider = String(current.source?.provider || "");
          provider.value = sourceProvider;
          // The server's Provider list names each Provider.
          const sourceLabel = current.providers?.find(row => row.id === sourceProvider)?.label || sourceProvider;
          const sourceAccount = String(current.source?.account_id || "");
          populateAccounts(sourceAccount);
          strategy.innerHTML = optionsHtml(current.strategies);
          strategy.value = "smart";
          activeExecution.value = "wait";
          resumeFailed.checked = current.restart?.resume_failed_input !== false;
          if (current.restart?.status === "failed") {
            const failedTarget = current.restart.target || {};
            if ([...provider.options].some(option => option.value === failedTarget.provider)) provider.value = failedTarget.provider;
            populateAccounts(failedTarget.account_id);
            const requestedStrategy = current.restart.requested_strategy || current.restart.context_strategy;
            if ([...strategy.options].some(option => option.value === requestedStrategy)) strategy.value = requestedStrategy;
          }
          const sourceBinding = current.source || {};
          const bindingStatus = { current: "当前绑定", unbound: "尚未绑定" }[sourceBinding.binding_status] || "未提供";
          bindingFields.innerHTML = [
            ["Provider", sourceLabel],
            ["账号", sourceAccount || "未提供"],
            ["绑定状态", bindingStatus],
            ["Session ID", sourceBinding.backend_session_id || "未绑定或未提供"],
            ["Provider Session 引用", sourceBinding.provider_session_ref || "未绑定或未提供"],
            ["状态版本（Generation）", sourceBinding.generation || "未提供"],
          ].map(([label, value]) => window.PortalI18n.html`<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
          source.textContent = current.source?.has_session
            ? window.PortalI18n.text`当前：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}。切换成功前会继续保留当前 session。`
            : sourceProvider === "provider_api"
            ? window.PortalI18n.text`当前：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}，通过 API 直接调用。迁移到原生 Backend 时会按上下文方案初始化新 Session，成功后再切换。`
            : window.PortalI18n.text`尚未创建 Provider session。当前配置：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}。可以直接修改，不会启动模型。`;
          update();
          dialog?.open?.({ focusTarget: provider });
          void loadModelCatalog();
          if (current.resident_id && restartService) {
            unobserve = restartService.observeSessionRestart(current.resident_id, view => {
              restartView = view;
              const restartState = view.restart || current.restart;
              const wasTracking = tracking;
              tracking = sessionRestartActive(restartState);
              if (tracking && restartState?.target) {
                provider.value = restartState.target.provider || provider.value;
                populateAccounts(restartState.target.account_id);
                source.textContent = restartState.status === "cancelling"
                  ? window.PortalI18n.t("正在取消已提交的迁移；原 Session 将继续保留。")
                  : window.PortalI18n.t("正在跟踪已提交的迁移；目标已锁定。关闭窗口不会取消，可使用“取消迁移”终止。");
              } else if (wasTracking && restartState?.status !== "completed") {
                source.textContent = window.PortalI18n.t("迁移已结束，请核对当前会话状态后再操作。");
                if (restartState?.status === "failed") requestKey = createRequestKey();
              }
              progress.update({ ...view, restart: restartState });
              update();
              if (wasTracking && ["completed", "cancelled"].includes(restartState?.status)) close({ accepted: true });
            });
          }
        },
        close,
        destroy() {
          catalogRevision += 1;
          unobserve?.();
          progress.destroy();
          dialog?.destroy?.();
          overlay.remove();
        },
      };
    }

    return Object.freeze({
      residentRuntimeSettingRows, residentRuntimeStatusText, residentRuntimeMetaHtml,
      notifyRuntimeSettingsSaved, showResidentBackendNotices, createRuntimeSettingsDialog,
      sessionRestartActive, createSessionRestartProgress, createSessionRestartDialog,
    });
  }

  window.PortalChatWorkspaceRuntimeSettings = Object.freeze({
    createRuntimeSettings, dependencies: DEPENDENCIES, residentRuntimeSettingRows,
    residentRuntimeStatusText, notifyRuntimeSettingsSaved, showResidentBackendNotices,
    sessionRestartActive,
  });
})();
