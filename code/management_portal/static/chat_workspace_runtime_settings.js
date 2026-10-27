(() => {
  if (window.PortalChatWorkspaceRuntimeSettings) return;

  // A single Resident runtime and Session card, with a shared parameter editor
  // also used by workspaces that expose settings without Session operations.
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
              && (complete || permissions.value !== String(initialSettings.permission_preset || ""))) {
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
          return selected;
        },
        open(settings = {}, dialogOptions = {}) {
          currentSettings = settings && typeof settings === "object" ? settings : {};
          initialSettings = { ...currentSettings };
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

    function createCapabilityEditor() {
      const element = document.createElement("div");
      element.className = "portalCapabilityEditor";
      const guidance = document.createElement("p");
      guidance.textContent = "选择已有的工具、MCP 连接与 Skill；保存不会连接服务、调用模型或自动重建会话。未列出的项目沿用现有默认装配。Prompt 注入与预览继续使用设置中的 Prompt 注入入口。";
      element.append(guidance);
      let original = "", application = {};
      const groups = {};
      const labels = {tools: "工具", mcp: "MCP 连接", skills: "Skill"};
      const addRow = (kind, value = {}) => {
        const row = document.createElement("div"); row.className = "portalCapabilityRow";
        const ref = document.createElement("input"); ref.type = "text"; ref.value = value.ref || "";
        ref.placeholder = kind === "tools" ? "portal:hatg_instruction" : "已登记资源的稳定引用";
        ref.setAttribute("aria-label", labels[kind] + "引用"); ref.dataset.capabilityRef = kind;
        const choice = document.createElement("select"); choice.setAttribute("aria-label", "已选择状态");
        choice.append(new Option("启用", "enabled"), new Option("停用", "disabled"));
        choice.value = value.enabled === false ? "disabled" : "enabled";
        const requiredLabel = document.createElement("label"), required = document.createElement("input");
        required.type = "checkbox"; required.checked = value.required === true; required.dataset.capabilityRequired = "1";
        requiredLabel.append(required, "必需");
        const remove = document.createElement("button"); remove.type = "button"; remove.className = "portalButton";
        remove.textContent = "恢复默认"; remove.title = "删除此覆盖项，恢复现有默认装配";
        remove.addEventListener("click", () => row.remove());
        row.append(ref, choice, requiredLabel, remove);
        choice.addEventListener("change", () => {if (choice.value === "disabled") required.checked = false; required.disabled = choice.value === "disabled";});
        required.disabled = choice.value === "disabled";
        if (kind === "mcp") {
          const details = document.createElement("details"), summary = document.createElement("summary"); summary.textContent = "工具名列表（可空）";
          details.open = true;
          const names = document.createElement("input"); names.type = "text"; names.dataset.mcpTools = "1";
          names.setAttribute("aria-label", "MCP 工具名称，逗号分隔"); names.placeholder = "精确工具名，用逗号分隔；留空表示不选任何工具";
          names.value = (value.tools || []).join(", ");
          const hint = document.createElement("p"); hint.textContent = "仅使用列表中的工具；留空不选择任何工具。新发现的工具不会自动加入。";
          details.append(summary, names, hint); row.append(details);
        }
        groups[kind].rows.append(row);
      };
      for (const [kind, label] of Object.entries(labels)) {
        const fieldset = document.createElement("fieldset"); fieldset.className = "portalCapabilityGroup";
        const legend = document.createElement("legend"); legend.textContent = label;
        const rows = document.createElement("div"), add = document.createElement("button"); add.type = "button"; add.className = "portalButton";
        add.textContent = "添加" + label; add.dataset.capabilityAdd = kind;
        add.addEventListener("click", () => addRow(kind));
        fieldset.append(legend, rows, add); element.append(fieldset); groups[kind] = {rows, fieldset};
      }
      const status = document.createElement("ul"); status.className = "portalCapabilityApplication"; status.dataset.capabilityApplication = "1"; element.append(status);
      const timings = {unknown: "未知", disabled: "已配置停用", next_turn: "下一轮", pending_session: "待显式重新开始会话", pending_compaction_or_session: "下次压缩后或新会话", applied: "已生效", unsupported: "后端不支持", failed: "失败"};
      const deliveryLabels = {unknown: "未知", not_sent: "未发送", sent: "已发送", accepted: "已接受", rejected: "已拒绝"};
      const showApplication = value => {
        application = value || {};
        const items = application.items || [];
        status.replaceChildren();
        for (const item of items.length ? items : [{path: "当前配置", status: "unknown", delivery: "unknown"}]) {
          const li = document.createElement("li");
          const observation = item.observation?.status || "unknown";
          li.textContent = (item.ref || item.path) + " · 已选择：" + (item.enabled === false ? "停用" : item.enabled === true ? "启用" : "沿用现有设置") +
            " · 当前观察：" + (deliveryLabels[observation] || observation) + " · 交付：" + (deliveryLabels[item.delivery] || item.delivery || "未知") +
            " · 生效时机：" + (timings[item.status] || item.status || "未知") + (item.reason ? " · " + item.reason : "");
          status.append(li);
        }
      };
      const read = () => {
        const value = {schema: "tfi.resident-capabilities.v1"};
        for (const [kind, group] of Object.entries(groups)) {
          const seen = new Set();
          value[kind] = [...group.rows.children].map(row => {
            const ref = row.querySelector("[data-capability-ref]").value.trim();
            if (!ref || seen.has(ref)) throw new Error("能力引用不能为空，也不能在同一组重复。");
            seen.add(ref);
            const enabled = row.querySelector("select").value === "enabled";
            const result = {ref, enabled, required: enabled && row.querySelector("[data-capability-required]").checked};
            if (kind === "mcp") {
              result.tools = row.querySelector("[data-mcp-tools]").value.split(",").map(s => s.trim()).filter(Boolean);
            }
            return result;
          });
        }
        return value;
      };
      return {element, showApplication,
        open(settings = {}, value) {
          for (const [kind, group] of Object.entries(groups)) {group.rows.replaceChildren(); (settings.capability_config?.[kind] || []).forEach(item => addRow(kind, item));}
          original = JSON.stringify(read()); showApplication(value);
        },
        changes() {const value = read(); return JSON.stringify(value) === original ? {} : {capability_config: value};},
        disabled(value) {element.querySelectorAll("input, select, button").forEach(control => {control.disabled = value;});},
      };
    }

    function createRuntimeSettingsDialog(options = {}) {
      const id = String(options.id || `portalRuntimeSettings_${Math.random().toString(16).slice(2)}`);
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay";
      overlay.hidden = true;
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
        <div class="portalDetailHeader" data-portal-detail-header><div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">Runtime Settings</div><button class="portalDetailClose" data-portal-icon="close" data-runtime-settings-close type="button" aria-label="关闭运行设置"></button></div>
        <form class="portalDetailForm portalRuntimeSettingsForm" data-runtime-settings-form>
          <div data-resident-backend-notices role="status" hidden></div>
          <div class="portalDetailFooter" data-portal-detail-footer><button class="portalButton" data-runtime-settings-close type="button">取消</button><button class="portalButton is-primary" data-runtime-settings-submit type="submit">保存</button></div>
        </form></div>`;
      document.body.append(overlay);
      const editor = createRuntimeSettingsEditor();
      const capabilities = createCapabilityEditor();
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "能力配置与实际交付";
      details.append(summary, capabilities.element);
      const form = overlay.querySelector("form"), submit = overlay.querySelector("[data-runtime-settings-submit]");
      form.querySelector(".portalDetailFooter").before(editor.element, details);
      let save;
      const dialog = window.PortalUI.createDialogController({modal: overlay, manageHidden: true, escapeClose: true, backdropClose: true,
        initialFocus: () => editor.model, onClose: context => options.onClose?.(context)});
      const close = context => dialog?.close?.(context);
      overlay.querySelectorAll("[data-runtime-settings-close]").forEach(button => button.addEventListener("click", () => close({source: "button"})));
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!save || submit.disabled) return;
        submit.disabled = true;
        try { const selected = {...editor.read(), ...capabilities.changes()}; editor.element.disabled = true; await save(selected); close({accepted: true}); }
        catch (error) { window.PortalUI.toast(window.PortalUI.errorMessage("保存运行参数", error), "error"); }
        finally { submit.disabled = false; editor.element.disabled = false; }
      });
      return {
        open(settings = {}, onSave = null, dialogOptions = {}) {
          save = onSave; editor.open(settings, dialogOptions);
          capabilities.open(settings, dialogOptions.application);
          overlay.querySelector(".portalRuntimeSettingsTitle").textContent = dialogOptions.title || "Runtime Settings";
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

    function createSessionRestartDialog(options = {}) {
      const id = String(options.id || "portalSessionRestart_" + Math.random().toString(16).slice(2));
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay portalSessionRestartOverlay";
      overlay.hidden = true;
      overlay.dataset.portalDetailOverlay = "1";
      overlay.innerHTML = '<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="' + escapeHtml(id) + 'Title">' +
        '<div class="portalDetailHeader" data-portal-detail-header><div class="portalRuntimeSettingsTitle" id="' + escapeHtml(id) + 'Title">切换账号</div>' +
        '<button class="portalDetailClose" data-portal-icon="close" data-session-restart-close type="button" aria-label="关闭"></button></div>' +
        '<nav class="portalRuntimePurposeNav" aria-label="运行设置">' +
        '<button type="button" class="portalButton" data-runtime-view="switch_account">切换账号</button>' +
        '<button type="button" class="portalButton" data-runtime-view="parameters">运行参数</button>' +
        '<button type="button" class="portalButton" data-runtime-view="capabilities">能力配置</button>' +
        '<button type="button" class="portalButton" data-runtime-view="restart">重新开始会话</button></nav>' +
        '<form class="portalDetailForm portalRuntimeSettingsForm" data-session-restart-form>' +
        '<p class="portalRuntimeSettingsHint" data-session-restart-source></p><div data-resident-backend-notices role="status" hidden></div>' +
        '<section data-runtime-panel="switch_account"><label class="portalRuntimeSettingsField"><span>目标账号</span><select data-session-target></select></label>' +
        '<p>保留当前有效上下文，等待正在运行的工作完成。切换失败时继续保留原会话。</p>' +
        '<div data-target-editor-host hidden><p>请选择目标 Provider 的模型。</p></div></section>' +
        '<section data-runtime-panel="parameters" hidden><div data-runtime-editor-host></div>' +
        '<p>保存后从下一轮生效，不会重新开始会话。执行沙箱控制 Provider 的本机执行；Portal 资源访问仍按 Portal 权限检查。</p></section>' +
        '<section data-runtime-panel="capabilities" hidden><div data-capability-editor-host></div></section>' +
        '<section data-runtime-panel="restart" hidden><label class="portalRuntimeSettingsField"><span>新会话的上下文</span>' +
        '<select data-session-restart="strategy"><option value="preserve">保留当前有效上下文</option><option value="fresh">清空上下文，从新任务开始</option></select></label>' +
        '<p data-session-restart-hint>使用当前账号重新开始会话，等待正在运行的工作完成。旧会话和历史记录保留。</p></section>' +
        '<p class="portalRuntimeSettingsHint" data-runtime-save-hint role="status"></p>' +
        '<details class="portalSessionBindingSnapshot" data-session-binding-snapshot><summary>诊断</summary><dl data-session-binding-fields></dl>' +
        '<button class="portalButton" data-session-reload type="button">刷新状态（保留未保存的选择）</button></details>' +
        '<div class="portalDetailFooter" data-portal-detail-footer><button class="portalButton is-danger" data-session-restart-cancel type="button" hidden>取消操作</button>' +
        '<button class="portalButton" data-session-restart-close type="button">取消</button>' +
        '<button class="portalButton is-primary" data-runtime-settings-submit type="button" hidden>保存参数</button>' +
        '<button class="portalButton is-primary" data-capability-submit type="button" hidden>保存能力配置</button>' +
        '<button class="portalButton is-primary" data-session-restart-submit type="submit">切换账号</button></div></form></div>';
      document.body.append(overlay);
      const find = selector => overlay.querySelector(selector);
      const form = find("form"), target = find("[data-session-target]"), strategy = find('[data-session-restart="strategy"]');
      const submit = find("[data-session-restart-submit]"), cancel = find("[data-session-restart-cancel]");
      const saveButton = find("[data-runtime-settings-submit]"), capabilityButton = find("[data-capability-submit]");
      const source = find("[data-session-restart-source]"), saveHint = find("[data-runtime-save-hint]");
      const editor = createRuntimeSettingsEditor(), targetEditor = createRuntimeSettingsEditor();
      const capabilities = createCapabilityEditor();
      find("[data-capability-editor-host]").replaceWith(capabilities.element);
      find("[data-runtime-editor-host]").replaceWith(editor.element);
      find("[data-target-editor-host]").append(targetEditor.element);
      const progress = createSessionRestartProgress();
      source.after(progress.element);
      let current = {}, view = "switch_account", restart, reload, cancelRestart, unobserve;
      let requestKey, submitting = false, tracking = false, restartView = {}, catalogRevision = 0, targetReady = false;
      const restartService = window.PortalResidentService;
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
      const dialog = window.PortalUI.createDialogController({
        modal: overlay, manageHidden: true, escapeClose: true, backdropClose: true,
        initialFocus: () => target,
        onClose: context => {catalogRevision++; unobserve?.(); unobserve = null; options.onClose?.(context);},
      });
      const close = context => dialog.close(context);
      const update = () => {
        const locked = submitting || tracking;
        const retry = restartView.restart?.status === "confirming";
        const purpose = {switch_account: "切换账号", parameters: "运行参数", capabilities: "能力配置", restart: "重新开始会话"};
        find(".portalRuntimeSettingsTitle").textContent = window.PortalI18n.t(purpose[view]);
        overlay.querySelectorAll("[data-runtime-panel]").forEach(panel => {panel.hidden = panel.dataset.runtimePanel !== view;});
        overlay.querySelectorAll("[data-runtime-view]").forEach(button => {
          button.disabled = locked;
          button.setAttribute("aria-pressed", String(button.dataset.runtimeView === view));
          button.hidden = ["parameters", "capabilities"].includes(button.dataset.runtimeView) && !current.onSave;
        });
        target.disabled = locked; strategy.disabled = locked;
        editor.element.disabled = locked; targetEditor.element.disabled = locked;
        capabilities.disabled(locked);
        saveButton.hidden = view !== "parameters" || !current.onSave; saveButton.disabled = locked;
        capabilityButton.hidden = view !== "capabilities" || !current.onSave; capabilityButton.disabled = locked;
        submit.hidden = !["switch_account", "restart"].includes(view);
        submit.disabled = submitting || !restart || (tracking && !retry) || (!retry && view === "switch_account" && (!target.value || sameAccount() || (crossProvider() && !targetReady)));
        submit.textContent = window.PortalI18n.t(retry ? "重试同一请求" : submitting ? "正在提交…" : purpose[view]);
        cancel.hidden = !tracking; cancel.disabled = submitting || restartView.restart?.status === "cancelling";
      };
      const loadTarget = async () => {
        const revision = ++catalogRevision;
        const cross = crossProvider();
        find("[data-target-editor-host]").hidden = !cross;
        targetReady = false; update();
        if (!cross) return;
        const [provider, account] = selected();
        try {
          const catalog = await restartService.runtimeCatalog({backend: provider, accountId: canonicalAccount(provider, account)});
          if (revision !== catalogRevision || overlay.hidden) return;
          targetEditor.open({}, {catalog, showApiAccount: false, hint: "选择目标模型后再切换；不自动压缩或摘要交接。"});
          targetReady = true;
        } catch (error) {
          if (revision === catalogRevision) window.PortalUI.toast(window.PortalUI.errorMessage("读取目标模型", error), "error");
        } finally { if (revision === catalogRevision) update(); }
      };
      overlay.querySelectorAll("[data-runtime-view]").forEach(button => button.addEventListener("click", () => {
        if (submitting || tracking) return;
        view = button.dataset.runtimeView; saveHint.textContent = ""; update();
      }));
      overlay.querySelectorAll("[data-session-restart-close]").forEach(button => button.addEventListener("click", () => close({source: "button"})));
      target.addEventListener("change", () => {requestKey = requestId(); void loadTarget();});
      saveButton.addEventListener("click", async () => {
        if (saveButton.disabled || !current.onSave) return;
        try {
          const values = editor.read(); submitting = true; update();
          const result = await current.onSave(values);
          current.runtime_settings = {...current.runtime_settings, ...values, ...(result?.settings || {})};
          editor.open(current.runtime_settings, {catalog: current.runtime_catalog, showApiAccount: false});
          saveHint.textContent = window.PortalI18n.t("运行参数已保存，从下一轮生效。");
        } catch (error) { window.PortalUI.toast(window.PortalUI.errorMessage("保存参数", error), "error"); }
        finally {submitting = false; update();}
      });
      capabilityButton.addEventListener("click", async () => {
        if (capabilityButton.disabled || !current.onSave) return;
        try {
          const values = capabilities.changes();
          if (!Object.keys(values).length) return;
          submitting = true; update();
          const result = await current.onSave(values);
          current.runtime_settings = {...current.runtime_settings, ...values, ...(result?.settings || {})};
          capabilities.open(current.runtime_settings, result?.application);
          saveHint.textContent = "能力配置已保存；实际交付与生效时机见下方状态。";
        } catch (error) {window.PortalUI.toast(window.PortalUI.errorMessage("保存能力配置", error), "error");}
        finally {submitting = false; update();}
      });
      find("[data-session-reload]").addEventListener("click", async () => {
        try {
          const refreshed = await reload?.();
          if (refreshed?.application) capabilities.showApplication(refreshed.application);
          await restartService.refreshSessionRestart?.(current.resident_id);
        }
        catch (error) {window.PortalUI.toast(window.PortalUI.errorMessage("刷新状态", error), "error");}
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
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!restart || submit.disabled || submit.hidden) return;
        try {
          const isSwitch = view === "switch_account";
          const [provider, account] = isSwitch ? selected() : [current.source?.provider, current.source?.account_id];
          if (isSwitch && sameAccount() && restartView.restart?.status !== "confirming") return;
          const runtime = isSwitch && crossProvider() && restartView.restart?.status !== "confirming" ? targetEditor.read({complete: true}) : {};
          if (isSwitch && crossProvider() && !runtime.model && restartView.restart?.status !== "confirming") throw new Error("请选择目标 Provider 的模型。");
          submitting = true; update();
          const result = restartView.restart?.status === "confirming"
            ? await restartService.retrySessionRestart(current.resident_id)
            : await restart({operation: isSwitch ? "switch_account" : "restart",
              target_provider: provider, target_account: account,
              context_strategy: isSwitch ? "preserve" : strategy.value,
              active_execution_policy: "wait", resume_failed_input: false, ...runtime, idempotency_key: requestKey});
          current.restart = result; restartView.restart = result;
          tracking = sessionRestartActive(result); progress.update(restartView);
          if (result?.status === "completed") close({accepted: true});
        } catch (error) {
          tracking = sessionRestartActive(restartView.restart);
          if (!error.restartPending) {requestKey = requestId(); window.PortalUI.toast(window.PortalUI.errorMessage("会话操作", error), "error");}
        } finally {submitting = false; update();}
      });
      return {
        open(config = {}, onRestart = null, onReload = null, onCancel = null) {
          current = {...config}; view = config.initial_view || "switch_account";
          restart = onRestart; reload = onReload; cancelRestart = onCancel; requestKey = requestId();
          submitting = false; tracking = sessionRestartActive(config.restart); restartView = {restart: config.restart};
          unobserve?.(); saveHint.textContent = ""; strategy.value = "preserve";
          const groups = (current.providers || []).map(provider => {
            const group = document.createElement("optgroup"); group.label = provider.label || provider.id;
            for (const row of current.accounts?.[provider.id] || []) {
              const option = document.createElement("option");
              option.value = JSON.stringify([provider.id, row.id]); option.textContent = row.label || row.account_id || row.id;
              group.append(option);
            }
            return group;
          });
          target.replaceChildren(new Option(window.PortalI18n.t("选择目标账号"), ""), ...groups);
          const sourceAccount = current.accounts?.[current.source?.provider]?.find(row => row.id === current.source.account_id || row.account_id === current.source.account_id);
          target.value = sourceAccount ? JSON.stringify([current.source.provider, sourceAccount.id]) : "";
          const label = current.providers?.find(row => row.id === current.source?.provider)?.label || current.source?.provider || "";
          source.textContent = "当前：" + label + " · " + (sourceAccount?.label || current.source?.account_id || "未绑定账号");
          find("[data-session-binding-fields]").innerHTML = [
            ["Provider", current.source?.provider], ["账号", current.source?.account_id], ["Session ID", current.source?.backend_session_id],
            ["绑定版本", current.source?.generation],
          ].map(([name, value]) => "<dt>" + escapeHtml(name) + "</dt><dd>" + escapeHtml(value == null ? "未知" : String(value)) + "</dd>").join("");
          editor.open(current.runtime_settings || {}, {catalog: current.runtime_catalog, showApiAccount: false});
          capabilities.open(current.runtime_settings, current.application);
          void showResidentBackendNotices(find("[data-resident-backend-notices]"), current.resident_id, {accountId: current.source?.account_id, settings: current.runtime_settings || {}});
          update(); dialog.open({focusTarget: view === "parameters" ? editor.model : target}); void loadTarget();
          if (current.resident_id && restartService) {
            unobserve = restartService.observeSessionRestart(current.resident_id, value => {
              const wasTracking = tracking;
              restartView = value; tracking = sessionRestartActive(value.restart);
              if (tracking && value.restart?.operation) view = value.restart.operation;
              if (tracking && value.restart?.target) {
                const endpoint = value.restart.target;
                const row = current.accounts?.[endpoint.provider]?.find(item => item.id === endpoint.account_id || item.account_id === endpoint.account_id);
                if (row) target.value = JSON.stringify([endpoint.provider, row.id]);
              }
              progress.update(value); update();
              if (wasTracking && ["completed", "cancelled"].includes(value.restart?.status)) close({accepted: true});
            });
          }
        }, close,
        destroy() {catalogRevision++; unobserve?.(); progress.destroy(); dialog.destroy(); overlay.remove();},
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
