(() => {
  "use strict";

  if (window.PortalLineageWorkbenchResidentDialogs) return;

  // The dialogs that add a Resident to a Lineage (register a new one, attach an
  // existing one or fork it), edit a Resident, and fork a Resident into another
  // Lineage or from a message, with the runtime settings they share: backend,
  // account route, model, effort and permissions.
  const DEPENDENCIES = Object.freeze([
    "$id", "activeChatTargetId", "activeLineageId", "appendChatWorkspaceDraft",
    "beginChatWorkspaceNavigation", "beginResidentSidebarMutation", "chatTargetForPanel",
    "commitChatTarget", "currentAgentChatWorkspace", "currentLineageWorkbench",
    "derivedResidentForSidebar", "endResidentSidebarMutation", "esc", "lineageIdForResident",
    "lineageLeaderResidentId", "lineageSidebarRoots", "loadDetail", "loadFolder", "messageRole",
    "refreshResidentSidebarIfDue", "registeredLineageIdForResident",
    "rememberResidentShortcutSession", "renderAgentList", "renderFavoriteList",
    "residentChatTarget", "residentDiscovery", "residentIdForSelection", "residentSelectionId",
    "residentService", "revealResidentSidebarTarget", "selectResident", "societyService",
    "state", "syncSelectedResidentSurface", "workspaceMessageByKey", "writeLastSelection",
  ]);

  function runtimePermissionPreset(settings) {
    const data = settings && typeof settings === "object" ? settings : {};
    const preset = String(data.permission_preset || "").trim();
    if (preset) return preset;
    const profile = String(data.permissions || "").trim();
    const reviewer = String(data.approvals_reviewer || "").trim().toLowerCase();
    const policy = String(data.approval_policy || "").trim().toLowerCase();
    const sandbox = String(data.sandbox || "").trim();
    if (profile === ":danger-full-access" || sandbox === "danger-full-access") return "full-access";
    if (profile === ":workspace" && reviewer === "auto_review" && (!policy || policy === "on-request")) return "auto-review";
    if (profile === ":workspace" && (!reviewer || reviewer === "user") && (!policy || policy === "on-request")) return "default";
    return "";
  }

  function replaceSelectOptions(select, options, requestedValue = "") {
    if (!select) return;
    select.replaceChildren(...options.map(item => new Option(item.label, item.value)));
    const requested = String(requestedValue || "");
    select.value = options.some(item => item.value === requested) ? requested : "";
  }

  function accountSelectionConfig(selection, requestedSourceAccountId = "") {
    const payload = selection && typeof selection === "object" ? selection : {};
    const accountIds = [...new Set(
      (Array.isArray(payload.account_ids) ? payload.account_ids : [])
        .map(value => String(value || "").trim().toLowerCase())
        .filter(Boolean),
    )];
    const sourceAccountId = String(
      requestedSourceAccountId || payload.source_account_id || ""
    ).trim().toLowerCase();
    const defaultAccountId = String(
      sourceAccountId || payload.default_account_id || accountIds[0] || ""
    ).trim().toLowerCase();
    return {
      enabled: payload.enabled === true && accountIds.length > 0,
      accountIds,
      sourceAccountId,
      defaultAccountId,
    };
  }

  function emptyResidentEditor() {
    return {
      residentId: "",
      title: "",
      saving: false,
    };
  }

  function createResidentDialogs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchResidentDialogs is missing: ${missing.join(", ")}`);
    const {
      $id, activeChatTargetId, activeLineageId, appendChatWorkspaceDraft,
      beginChatWorkspaceNavigation, beginResidentSidebarMutation, chatTargetForPanel,
      commitChatTarget, currentAgentChatWorkspace, currentLineageWorkbench,
      derivedResidentForSidebar, endResidentSidebarMutation, esc, lineageIdForResident,
      lineageLeaderResidentId, lineageSidebarRoots, loadDetail, loadFolder, messageRole,
      refreshResidentSidebarIfDue, registeredLineageIdForResident,
      rememberResidentShortcutSession, renderAgentList, renderFavoriteList, residentChatTarget,
      residentDiscovery, residentIdForSelection, residentSelectionId, residentService,
      revealResidentSidebarTarget, selectResident, societyService, state,
      syncSelectedResidentSurface, workspaceMessageByKey, writeLastSelection,
    } = dependencies;

    const agentDialogControllers = new Map();

    const agentDialogCloseHandlers = {
      residentRegisterOverlay: () => closeResidentRegisterModal(),
      residentAttachOverlay: () => closeResidentAttachChoiceModal(),
      residentForkCreateOverlay: () => closeResidentForkCreateModal(),
      crossLineageForkOverlay: () => closeCrossLineageForkModal(),
      residentEditOverlay: () => closeResidentEditModal(),
    };

    function agentDialogController(ref) {
      const element = typeof ref === "string" ? $id(ref) : ref;
      const id = String(element?.id || "").trim();
      if (!element || !id) return null;
      if (!agentDialogControllers.has(id)) {
        agentDialogControllers.set(id, window.PortalUI.createDialogController({
          modal: element,
          bodyClass: null,
          backdropClose: true,
          escapeClose: true,
          requestClose: event => agentDialogCloseHandlers[id]?.(event),
        }));
      }
      return agentDialogControllers.get(id);
    }

    function setAgentDialogOpen(ref, open) {
      const controller = agentDialogController(ref);
      if (!controller) return false;
      return open ? controller.open() : controller.close();
    }

    function setSelectValue(id, value) {
      const el = $id(id);
      if (!el) return;
      const text = String(value || "");
      el.value = [...el.options].some(option => option.value === text) ? text : "";
    }

    function residentRuntimeDefinition() {
      return state.agentBackendCatalog?.backends?.find(row => row.backend === $id("residentBackendSelect")?.value) || {};
    }

    function residentRuntimeCatalog() {
      const definition = residentRuntimeDefinition();
      const routes = definition.routes || [];
      const selected = $id("residentRouteSelect")?.value || "";
      const route = routes.find(row => row.backend_route_id === selected) || routes[0] || {};
      return { ...definition, ...window.PortalLineageCatalogs.routeRuntimeCatalog(route, refreshOpenResidentRegistrationOptions) };
    }

    function residentEffortsForModel(model) {
      return [...(residentRuntimeCatalog()?.efforts_by_model?.[String(model || "").trim()] || [])];
    }

    function syncResidentEffortSelect(modelSelectId, effortSelectId, requestedValue = "") {
      const model = String($id(modelSelectId)?.value || "").trim();
      const select = $id(effortSelectId);
      if (!select) return;
      const efforts = residentEffortsForModel(model);
      const requested = String(requestedValue || "").trim();
      const options = [
        { value: "", label: "default" },
        ...efforts.map(effort => ({ value: effort, label: effort })),
      ];
      replaceSelectOptions(select, options, efforts.includes(requested) ? requested : "");
    }

    function resetResidentRuntimeSettings(settings = {}) {
      syncResidentBackendRuntimeFields();
      const catalog = residentRuntimeCatalog();
      const model = settings.model || catalog.default_model || "";
      if (catalog.models?.includes(model)) $id("residentRuntimeModelSelect").value = model;
      syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", settings.reasoning_effort || "");
      setSelectValue("residentRuntimeTierSelect", settings.service_tier === "priority" ? "priority" : "");
      setSelectValue("residentRuntimePermissionSelect", runtimePermissionPreset(settings));
    }

    function selectedResidentRuntimeSettings() {
      const settings = {};
      const model = String($id("residentRuntimeModelSelect")?.value || "").trim();
      const effort = String($id("residentRuntimeEffortSelect")?.value || "").trim();
      const tier = String($id("residentRuntimeTierSelect")?.value || "").trim();
      const permissionPreset = String($id("residentRuntimePermissionSelect")?.value || "").trim();
      if (model) settings.model = model;
      if (effort && residentRuntimeDefinition().runtime_settings?.includes("reasoning_effort")) settings.reasoning_effort = effort;
      if (tier && residentRuntimeDefinition().runtime_settings?.includes("service_tier")) settings.service_tier = tier;
      if (permissionPreset && residentRuntimeDefinition().runtime_settings?.includes("permissions")) settings.permission_preset = permissionPreset;
      return settings;
    }

    function syncResidentBackendRuntimeFields() {
      const definition = residentRuntimeDefinition();
      const fields = definition.runtime_settings || [];
      const routes = definition.routes || [];
      const routeSelect = $id("residentRouteSelect");
      const previousRoute = state.residentRouteChosen ? routeSelect?.value || "" : "";
      if (routeSelect) {
        routeSelect.replaceChildren(...routes.map(route => new Option(route.label, route.backend_route_id)));
        // The route is the one account the Resident uses. Until the user picks one, a
        // Codex Resident starts on the account a new session uses by default.
        const defaultAccount = residentAccountConfig().enabled ? residentAccountConfig().defaultAccountId : "";
        const preferred = routes.some(route => route.backend_route_id === previousRoute)
          ? previousRoute
          : routes.find(route => defaultAccount && String(route.account_id || "").toLowerCase() === defaultAccount)?.backend_route_id;
        if (preferred) routeSelect.value = preferred;
        routeSelect.required = routes.some(row => row.backend_route_id);
        routeSelect.disabled = !routeSelect.required;
        $id("residentRouteField").hidden = !routeSelect.required;
      }
      const catalog = residentRuntimeCatalog();
      const modelSelect = $id("residentRuntimeModelSelect");
      const previous = modelSelect?.value || "";
      if (modelSelect) {
        modelSelect.replaceChildren(...(catalog.models || []).map(value => new Option(value, value)));
        modelSelect.value = catalog.models?.includes(previous) ? previous : catalog.default_model || "";
      }
      for (const [id, field] of [["residentRuntimeEffortSelect", "reasoning_effort"], ["residentRuntimeTierSelect", "service_tier"], ["residentRuntimePermissionSelect", "permissions"]]) {
        const row = $id(id)?.closest(".residentFormRow");
        if (row) { row.hidden = !fields.includes(field); row.style.display = row.hidden ? "none" : ""; }
      }
      syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", $id("residentRuntimeEffortSelect")?.value || "");
    }

    function setResidentWorkspaceAdvancedOpen(open) {
      const details = $id("residentWorkspaceAdvancedDetails");
      if (details) details.open = !!open;
    }

    function refreshOpenResidentRegistrationOptions() {
      // Catalogs may arrive after the dialog opens (including after reconnect).
      // Refresh only derived choices, never reset user-authored form fields.
      if (!$id("residentRegisterForm")?.getClientRects().length) return;
      syncResidentBackendRuntimeFields();
    }

    function residentRegisterDefaultModal() {
      return $id("residentRegisterOverlay")?.querySelector(".portalDetailSurface") || null;
    }

    function restoreResidentRegisterForm() {
      const form = $id("residentRegisterForm");
      const modal = residentRegisterDefaultModal();
      if (form && modal && form.parentElement !== modal) {
        modal.appendChild(form);
      }
    }

    function resetResidentAttachOptionState() {
      const forkDetails = $id("residentAttachForkDetails");
      if (forkDetails) forkDetails.open = false;
    }

    function resetResidentRegisterModalState() {
      state.residentAttachSource = null;
    }

    function residentAttachChoiceItem() {
      const lineageId = String(
        state.residentAttachChoice?.lineageId || ""
      ).trim();
      return lineageSidebarRoots("", { respectFilters: false }).find(
        item => lineageIdForResident(item) === lineageId
      ) || null;
    }

    function renderResidentAttachChoiceModal() {
      const modal = state.residentAttachChoice || {};
      const overlay = $id("residentAttachOverlay");
      if (!overlay) return;
      setAgentDialogOpen(overlay, !!modal.open);
      overlay.setAttribute("aria-hidden", modal.open ? "false" : "true");
      $id("residentAttachSubtitle").textContent = modal.title ? window.PortalI18n.text`Target lineage: ${modal.title}` : "";
    }

    function openLineageAttachChoiceModal(item) {
      const lineageId = lineageIdForResident(item);
      if (!lineageId) return;
      restoreResidentRegisterForm();
      restoreResidentForkCreateBody();
      resetResidentForkCreateState();
      renderResidentForkCreateModal();
      resetResidentAttachOptionState();
      state.residentAttachChoice = {
        open: true,
        lineageId,
        leaderResidentId: lineageLeaderResidentId(item),
        title: item?.lineage?.title || item.lineage_title || item.title || lineageId,
      };
      state.agentMenuId = "";
      renderResidentAttachChoiceModal();
    }

    function closeResidentAttachChoiceModal() {
      if (state.residentRegisterSaving || forkCreateState().busy) return;
      restoreResidentRegisterForm();
      closeResidentForkCreateModal({ keepAttachOpen: true });
      resetResidentAttachOptionState();
      resetResidentRegisterModalState();
      state.residentAttachChoice = {
        open: false,
        lineageId: "",
        leaderResidentId: "",
        title: "",
      };
      renderResidentAttachChoiceModal();
    }

    function openAttachResidentModal(item) {
      const lineageId = lineageIdForResident(item);
      if (!lineageId) return;
      restoreResidentRegisterForm();
      const sourceTitle = item?.lineage?.title || item.lineage_title || item.title || item.resident_id || "Lineage";
      state.residentAttachSource = {
        lineageId,
        leaderResidentId: lineageLeaderResidentId(item),
        title: sourceTitle,
      };
      $id("residentRegisterTitle").textContent = window.PortalI18n.text`Attach to Lineage · ${sourceTitle}`;
      $id("residentRegisterSubmit").textContent = window.PortalI18n.t("附加");
      $id("residentTitleInput").value = `${sourceTitle} Resident`;
      // A new Resident starts on the first Provider of the server's catalog.
      $id("residentBackendSelect").selectedIndex = 0;
      state.residentRouteChosen = false;
      $id("residentAdditionalWorkspacesInput").value = "";
      resetResidentRuntimeSettings();
      setResidentWorkspaceAdvancedOpen(false);
      syncResidentBackendRuntimeFields();
      setAgentDialogOpen("residentRegisterOverlay", true);
      $id("residentRegisterOverlay").setAttribute("aria-hidden", "false");
      window.setTimeout(() => $id("residentTitleInput").focus(), 0);
    }

    function openResidentEditModal(item) {
      if (!item?.resident_id) return;
      openResidentEditSurface({
        residentId: String(item.resident_id || ""),
        title: String(item.title || item.resident_id || "Resident"),
      });
    }

    function closeResidentRegisterModal() {
      if (state.residentRegisterSaving) return;
      restoreResidentRegisterForm();
      setAgentDialogOpen("residentRegisterOverlay", false);
      $id("residentRegisterOverlay").setAttribute("aria-hidden", "true");
      resetResidentRegisterModalState();
    }

    async function submitResidentRegister(event) {
      event.preventDefault();
      await submitResidentAttach();
    }

    async function submitResidentAttach() {
      const source = state.residentAttachSource || {};
      const sourceLineageId = String(source.lineageId || "").trim();
      if (!sourceLineageId || state.residentRegisterSaving) return;
      const backend = $id("residentBackendSelect").value;
      const payload = {
        lineage_id: sourceLineageId,
        title: $id("residentTitleInput").value.trim(),
        workspace_paths: $id("residentAdditionalWorkspacesInput").value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
        backend,
        backend_route_id: selectedResidentRoute(),
        mode: "new_session",
        runtime_settings: selectedResidentRuntimeSettings(),
      };
      state.residentRegisterSaving = true;
      $id("residentRegisterSubmit").disabled = true;
      let attachedRecord = null;
      beginResidentSidebarMutation();
      try {
        const result = await residentService.attach(payload);
        const record = result.resident || result;
        if (!record?.resident_id) throw new Error("Attach 返回缺少 resident_id");
        attachedRecord = record;
        state.residents = [record, ...state.residents.filter(item => item.resident_id !== record.resident_id)];
        commitChatTarget(residentChatTarget(
          record.resident_id,
          registeredLineageIdForResident(record),
        ));
        beginChatWorkspaceNavigation();
        writeLastSelection();
        rememberResidentShortcutSession(record.resident_id);
        state.residentRegisterSaving = false;
        closeResidentRegisterModal();
        revealResidentSidebarTarget(record);
        syncSelectedResidentSurface();
        await Promise.allSettled([
          refreshResidentSidebarIfDue(true),
          loadDetail(true),
          loadFolder(),
        ]);
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`Attach 失败：${err.message || err}`, "error");
      } finally {
        endResidentSidebarMutation({
          preserveResidents: attachedRecord ? [attachedRecord] : [],
        });
        state.residentRegisterSaving = false;
        $id("residentRegisterSubmit").disabled = false;
      }
    }

    function forkCreateState() {
      return state.residentForkCreate || {};
    }

    function residentAccountConfig() {
      const selection = state.accountSelection
        && typeof state.accountSelection === "object"
        ? { ...state.accountSelection }
        : {};
      selection.enabled = selection.enabled === true
        && residentRuntimeDefinition().account_selection?.enabled === true;
      return accountSelectionConfig(selection);
    }

    function selectedResidentRoute() {
      return String($id("residentRouteSelect")?.value || "").trim();
    }

    function renderResidentForkAccount() {
      const modal = forkCreateState();
      const field = $id("residentForkAccountField");
      const select = $id("residentForkAccountInput");
      const hint = $id("residentForkAccountHint");
      if (!field || !select || !hint) return;
      const resident = state.residents.find(item => (
        String(item?.resident_id || "") === String(modal.residentId || "")
      )) || null;
      const providerSelect = $id("residentForkProviderInput");
      const selection = window.PortalLineageCatalogs.renderForkProvider({
        catalog: modal.catalog, source: resident, detail: modal.detail,
        providerSelect, accountSelect: select, provider: modal.provider, account: modal.backendRouteId,
      });
      const ready = selection.available && !modal.loading && !modal.busy;
      providerSelect.disabled = !!modal.loading || !!modal.busy;
      select.disabled = !ready;
      if (ready) {
        modal.provider = selection.provider;
        modal.backendRouteId = selection.account;
      }
      $id("residentForkCreateBody").querySelectorAll("[data-fork-create-action]").forEach(button => {
        button.disabled = !ready || (button.dataset.forkCreateAction === "compacted" && !selection.definition?.seeded_compaction);
      });
      hint.textContent = modal.loading ? window.PortalI18n.t("正在读取 Provider 和账号…")
        : ready ? window.PortalI18n.t("在所选 Provider 和账号建立独立会话，继承所选上下文。")
          : window.PortalI18n.t("该 Provider 暂无可用账号。");
    }

    function resetResidentForkCreateState() {
      state.residentForkCreate = {
        open: false,
        residentId: "",
        lineageId: "",
        title: "",
        intent: "fork",
        loading: false,
        detail: null,
        busy: false,
        backendRouteId: undefined,
        draftText: "",
      };
    }

    function residentForkCreateDefaultModal() {
      return $id("residentForkCreateOverlay")?.querySelector(".portalDetailSurface") || null;
    }

    function restoreResidentForkCreateBody() {
      const body = $id("residentForkCreateBody");
      const modal = residentForkCreateDefaultModal();
      if (body && modal && body.parentElement !== modal) {
        modal.appendChild(body);
      }
      state.residentForkCreateInlineHost = "";
    }

    function moveResidentForkCreateBodyToAttachPanel() {
      const body = $id("residentForkCreateBody");
      const panel = $id("residentAttachForkPanel");
      if (!body || !panel) return false;
      if (body.parentElement !== panel) {
        panel.appendChild(body);
      }
      state.residentForkCreateInlineHost = "attach";
      return true;
    }

    function renderResidentForkCreateModal() {
      const modal = forkCreateState();
      const overlay = $id("residentForkCreateOverlay");
      if (!overlay) return;
      const inlineAttach = state.residentForkCreateInlineHost === "attach";
      setAgentDialogOpen(overlay, !!modal.open && !inlineAttach);
      overlay.setAttribute("aria-hidden", modal.open && !inlineAttach ? "false" : "true");
      $id("residentForkCreateTitle").textContent = modal.intent === "attach"
        ? window.PortalI18n.t("创建并附加分支 Resident")
        : modal.intent === "lineage"
          ? window.PortalI18n.t("创建 Lineage 分支")
          : window.PortalI18n.t("创建分支 Resident");
      $id("residentForkCreateSubtitle").textContent = modal.title ? modal.title : "";
      renderResidentForkAccount();
    }

    async function openResidentForkCreateModal(item, options = {}) {
      const residentId = String(item?.resident_id || item?.instance_id || "").trim();
      if (!residentId) return;
      const inline = options.inline === true;
      if (inline) {
        restoreResidentRegisterForm();
        setAgentDialogOpen("residentRegisterOverlay", false);
        $id("residentRegisterOverlay").setAttribute("aria-hidden", "true");
      } else {
        restoreResidentForkCreateBody();
      }
      state.residentForkCreate = {
        open: true,
        residentId,
        lineageId: String(
          options.lineageId || registeredLineageIdForResident(item) || "",
        ).trim(),
        title: item?.title || residentId,
        intent: ["attach", "lineage"].includes(options.intent) ? options.intent : "fork",
        loading: true,
        detail: null,
        busy: false,
        backendRouteId: undefined,
        draftText: String(options.draftText || "").trim(),
      };
      if (inline) {
        moveResidentForkCreateBodyToAttachPanel();
      }
      renderResidentForkCreateModal();
      const modal = state.residentForkCreate;
      try {
        const [detail, catalog] = await Promise.all([window.PortalChatWorkspace.loadResidentConversation(
          { resident_id: residentId },
          { historyLimit: 0 },
        ), window.PortalLineageCatalogs.agentBackends()]);
        if (state.residentForkCreate !== modal) return;
        state.residentForkCreate.detail = detail;
        state.residentForkCreate.catalog = catalog;
      } catch (err) {
        if (state.residentForkCreate !== modal) return;
        modal.detail = null;
        window.PortalUI.toast(window.PortalI18n.text`来源 Resident 读取失败：${err.message || err}`, "error");
      } finally {
        if (state.residentForkCreate === modal) {
          modal.loading = false;
          renderResidentForkCreateModal();
        }
      }
    }

    function closeResidentForkCreateModal(options = {}) {
      if (forkCreateState().busy) return;
      const inlineAttach = state.residentForkCreateInlineHost === "attach";
      restoreResidentForkCreateBody();
      resetResidentForkCreateState();
      renderResidentForkCreateModal();
      if (inlineAttach && options.keepAttachOpen !== true) {
        resetResidentAttachOptionState();
        state.residentAttachChoice = {
          open: false,
          lineageId: "",
          leaderResidentId: "",
          title: "",
        };
        renderResidentAttachChoiceModal();
      }
    }

    async function handleForkCreateAction(action) {
      const residentId = String(forkCreateState().residentId || "").trim();
      if (!residentId) return;
      const lineageId = String(forkCreateState().lineageId || "").trim();
      const backendRouteId = String(
        forkCreateState().backendRouteId || ""
      ).trim();
      const provider = forkCreateState().provider;
      const draftText = String(forkCreateState().draftText || "").trim();
      const mode = action;
      closeResidentForkCreateModal();
      await createResidentForkFromMenu(residentId, {
        mode,
        backendRouteId,
        provider,
        lineageId,
        draftText,
      });
    }

    async function completeResidentFork(response, lineageId, options = {}) {
      const residentId = String(response?.resident_id || "").trim();
      const lineage = response?.lineage;
      if (!residentId || lineage?.lineage_id !== lineageId) {
        window.PortalUI.toast(window.PortalI18n.t("分支已创建，但返回结果不完整；请刷新 Lineage 后定位新 Resident。"), "warning");
        return false;
      }
      try {
        const hatgPanel = currentLineageWorkbench()?.getPanel("hatg");
        if (hatgPanel) {
          try {
            await hatgPanel.applyLineageMutation(lineage, { separateObjectIds: [residentId] });
          } catch (error) {
            window.PortalUI.toast(window.PortalI18n.text`分支已创建，但画布布局未保存：${error.message || error}`, "warning");
          }
        }
        rememberResidentShortcutSession(residentId);
        const opened = await selectResident(residentId, {
          forceView: true,
          lineageId,
          revealInGraph: true,
        });
        if (opened && options.draftText) appendChatWorkspaceDraft(options.draftText);
        return opened;
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`分支已创建，但页面未能定位新 Resident：${err.message || err}`, "warning");
        return false;
      }
    }

    async function createResidentForkFromMenu(residentId, options = {}) {
      const id = String(residentId || "").trim();
      const lineageId = String(options.lineageId || "").trim();
      if (!id || !lineageId) {
        window.PortalUI.toast(window.PortalI18n.t("来源 Resident 不属于可写入 HATG 的 Lineage，无法创建分支。"), "error");
        return;
      }
      state.agentMenuId = "";
      state.residentMemberMenuId = "";
      beginResidentSidebarMutation();
      try {
        const response = await societyService.branchNode({
          lineage_id: lineageId,
          source_resident_id: id,
          fork_mode: options.mode || "full",
          ...(options.provider ? { provider: options.provider } : {}),
          ...(options.backendRouteId ? { account_id: options.backendRouteId } : {}),
        });
        await completeResidentFork(response, lineageId, { draftText: options.draftText });
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`创建分支 Resident 失败：${err.message || err}`, "error");
        renderAgentList();
        renderFavoriteList();
      } finally {
        endResidentSidebarMutation();
      }
    }

    function openResidentEditSurface(config = {}) {
      state.residentEditor = {
        ...emptyResidentEditor(),
        residentId: String(config.residentId || ""),
        title: String(config.title || "").trim(),
        saving: false,
      };
      $id("residentEditTitle").textContent = window.PortalI18n.text`编辑 · ${state.residentEditor.title || "Resident"}`;
      $id("residentEditTitleInput").value = state.residentEditor.title;
      setAgentDialogOpen("residentEditOverlay", true);
      $id("residentEditOverlay").setAttribute("aria-hidden", "false");
      window.setTimeout(() => $id("residentEditTitleInput").focus(), 0);
    }

    function openDerivedResidentEditModal(residentId, derivedResidentId) {
      const sourceResident = state.residents.find(item => String(item.resident_id || "") === String(residentId)) || {};
      const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, sourceResident) || {};
      openResidentEditSurface({
        residentId: derivedResidentId,
        title: String(derivedResident?.title || derivedResident?.label || "").trim() || derivedResidentId,
      });
    }

    function closeResidentEditModal() {
      if (state.residentEditor.saving) return;
      setAgentDialogOpen("residentEditOverlay", false);
      $id("residentEditOverlay").setAttribute("aria-hidden", "true");
      state.residentEditor = emptyResidentEditor();
    }

    function editDerivedResidentFromSidebar(residentId, derivedResidentId) {
      openDerivedResidentEditModal(residentId, derivedResidentId);
    }

    async function submitResidentEditSurface(event) {
      event.preventDefault();
      if (state.residentEditor.saving) return;
      const residentId = state.residentEditor.residentId;
      const title = $id("residentEditTitleInput").value.trim();
      if (!residentId) return;
      if (!title) {
        window.PortalUI.toast(window.PortalI18n.t("title 不能为空。"), "warning");
        return;
      }
      state.residentEditor.saving = true;
      $id("residentEditSubmit").disabled = true;
      let record;
      try {
        record = await residentService.update({
          resident_id: residentId,
          title,
        });
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`更新 Resident 失败：${err.message || err}`, "error");
        return;
      } finally {
        state.residentEditor.saving = false;
        $id("residentEditSubmit").disabled = false;
      }
      closeResidentEditModal();
      try {
        await refreshResidentSidebarIfDue(true);
        renderAgentList();
        renderFavoriteList();
        if (activeChatTargetId() === residentSelectionId(record)) {
          state.folder = null;
          state.selectedFile = "";
          syncSelectedResidentSurface();
          await Promise.allSettled([loadDetail(true), loadFolder()]);
        }
      } catch (err) {
        window.PortalUI.toast(window.PortalI18n.text`Resident 名称已保存，但界面刷新失败：${err.message || err}`, "warning");
      }
    }

    function crossLineageForkTargets() {
      const currentLineageId = activeLineageId();
      return lineageSidebarRoots("", { respectFilters: false }).filter(item => (
        registeredLineageIdForResident(item)
        && registeredLineageIdForResident(item) !== currentLineageId
      ));
    }

    function resetCrossLineageForkState() {
      state.crossLineageFork = {
        open: false,
        messageKey: "",
        lineageId: "",
        residentId: "",
        nodes: [],
        loading: false,
        busy: false,
        error: "",
        sourceWorkspace: null,
      };
    }

    function renderCrossLineageForkModal() {
      const modal = state.crossLineageFork || {};
      const overlay = $id("crossLineageForkOverlay");
      if (!overlay) return;
      setAgentDialogOpen(overlay, !!modal.open);
      overlay.setAttribute("aria-hidden", modal.open ? "false" : "true");
      const lineageSelect = $id("crossLineageForkLineageSelect");
      const residentSelect = $id("crossLineageForkResidentSelect");
      const targets = crossLineageForkTargets();
      lineageSelect.innerHTML = targets.length
        ? targets.map(item => {
            const lineageId = registeredLineageIdForResident(item);
            const title = String(item?.lineage?.title || lineageId);
            return window.PortalI18n.html`<option value="${esc(lineageId)}">${esc(title)}</option>`;
          }).join("")
        : window.PortalI18n.markup('<option value="">没有其他可编辑的 Lineage</option>');
      lineageSelect.value = targets.some(item => (
        registeredLineageIdForResident(item) === modal.lineageId
      )) ? modal.lineageId : "";
      residentSelect.innerHTML = modal.loading
        ? window.PortalI18n.markup('<option value="">正在读取 HATG 节点…</option>')
        : (modal.nodes || []).map(node => {
            const residentId = String(node?.id || "").trim();
            const title = String(node?.title || residentId);
            return window.PortalI18n.html`<option value="${esc(residentId)}">${esc(title)} · ${esc(residentId)}</option>`;
          }).join("") || window.PortalI18n.markup('<option value="">该 Lineage 没有可用的 Resident 节点</option>');
      residentSelect.value = (modal.nodes || []).some(node => (
        String(node?.id || "") === modal.residentId
      )) ? modal.residentId : "";
      lineageSelect.disabled = !!modal.busy || !targets.length;
      residentSelect.disabled = !!modal.busy || !!modal.loading || !(modal.nodes || []).length;
      $id("crossLineageForkHint").textContent = modal.error
        || window.PortalI18n.t("分支操作会创建继承当前对话的普通 Resident，并挂到所选 Resident 节点下。");
      $id("crossLineageForkSubmit").disabled = (
        !!modal.busy
        || !!modal.loading
        || !modal.lineageId
        || !modal.residentId
      );
    }

    async function loadCrossLineageForkNodes(lineageId) {
      const targetLineageId = String(lineageId || "").trim();
      if (!targetLineageId) return;
      state.crossLineageFork.lineageId = targetLineageId;
      state.crossLineageFork.residentId = "";
      state.crossLineageFork.nodes = [];
      state.crossLineageFork.loading = true;
      state.crossLineageFork.error = "";
      renderCrossLineageForkModal();
      try {
        const lineage = await societyService.loadLineage(targetLineageId);
        if (
          !state.crossLineageFork?.open
          || state.crossLineageFork.lineageId !== targetLineageId
        ) return;
        const nodes = (lineage?.nodes || []).filter(node => (
          String(node?.id || "").trim()
        ));
        state.crossLineageFork.nodes = nodes;
        state.crossLineageFork.residentId = String(nodes[0]?.id || "");
      } catch (err) {
        if (
          state.crossLineageFork?.open
          && state.crossLineageFork.lineageId === targetLineageId
        ) {
          state.crossLineageFork.error = `目标 HATG 读取失败：${err.message || err}`;
        }
      } finally {
        if (
          state.crossLineageFork?.open
          && state.crossLineageFork.lineageId === targetLineageId
        ) {
          state.crossLineageFork.loading = false;
          renderCrossLineageForkModal();
        }
      }
    }

    function closeCrossLineageForkModal() {
      if (state.crossLineageFork?.busy) return;
      resetCrossLineageForkState();
      renderCrossLineageForkModal();
    }

    async function submitCrossLineageFork(event) {
      event.preventDefault();
      const modal = state.crossLineageFork || {};
      if (modal.busy || !modal.messageKey || !modal.lineageId || !modal.residentId) return;
      state.crossLineageFork.busy = true;
      renderCrossLineageForkModal();
      try {
        await performForkResidentFromMessage(modal.messageKey, {
          lineageId: modal.lineageId,
          residentId: modal.residentId,
        }, modal.sourceWorkspace);
        resetCrossLineageForkState();
        renderCrossLineageForkModal();
      } catch (err) {
        state.crossLineageFork.busy = false;
        state.crossLineageFork.error = `跨 Lineage 创建分支失败：${err.message || err}`;
        renderCrossLineageForkModal();
      }
    }

    async function performForkResidentFromMessage(key, target = {}, workspace = currentAgentChatWorkspace()) {
      const residentId = residentIdForSelection(chatTargetForPanel(workspace));
      const item = workspaceMessageByKey(key, workspace);
      if (!residentId || !item || messageRole(item) !== "human") return;
      let sourceLineageId = activeLineageId();
      if (!sourceLineageId && !target.lineageId) {
        const navigationTarget = await residentDiscovery.resolveNavigationTarget(residentId);
        sourceLineageId = String(navigationTarget?.lineage_id || "").trim();
      }
      const targetLineageId = String(target.lineageId || sourceLineageId).trim();
      const targetResidentId = String(target.residentId || residentId).trim();
      if (!targetLineageId) {
        throw new Error(window.PortalI18n.t("当前 Resident 尚未登记到 Lineage，无法创建 HATG 分支"));
      }
      const message = {
        message_id: item.id,
      };
      const targetLineage = await societyService.loadLineage(targetLineageId);
      const graphParent = (targetLineage?.nodes || []).find(node => (
        String(node?.id || "") === targetResidentId
      ));
      if (!graphParent) {
        throw new Error(window.PortalI18n.t("所选 Resident 不是目标 Lineage HATG 中的正式节点"));
      }
      const graphParentId = String(graphParent.id || "").trim();
      if (!graphParentId) throw new Error(window.PortalI18n.t("当前 HATG 节点缺少 canonical node ID"));
      const response = await societyService.branchNode({
        lineage_id: targetLineageId,
        source_resident_id: graphParentId,
        context_source_resident_id: residentId,
        fork_mode: "message",
        message,
      });
      await completeResidentFork(response, targetLineageId);
    }

    return Object.freeze({
      syncResidentEffortSelect, syncResidentBackendRuntimeFields,
      refreshOpenResidentRegistrationOptions, restoreResidentRegisterForm,
      resetResidentAttachOptionState, residentAttachChoiceItem, renderResidentAttachChoiceModal,
      openLineageAttachChoiceModal, closeResidentAttachChoiceModal, openAttachResidentModal,
      openResidentEditModal, closeResidentRegisterModal, submitResidentRegister,
      renderResidentForkAccount, openResidentForkCreateModal, closeResidentForkCreateModal,
      handleForkCreateAction, completeResidentFork, closeResidentEditModal,
      editDerivedResidentFromSidebar, submitResidentEditSurface, renderCrossLineageForkModal,
      loadCrossLineageForkNodes, closeCrossLineageForkModal, submitCrossLineageFork,
    });
  }

  window.PortalLineageWorkbenchResidentDialogs = Object.freeze({
    createResidentDialogs, dependencies: DEPENDENCIES,
  });
})();
