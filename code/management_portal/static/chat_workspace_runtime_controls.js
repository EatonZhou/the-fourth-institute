(() => {
  if (window.PortalChatWorkspaceRuntimeControls) return;

  // One Workspace's runtime controls, for the Resident whose Chat this is
  // (contractResidentId). PortalResidentRuntimeController holds the Resident's
  // settings, catalog and dialogs; this part binds one for the contract's
  // Resident and follows the contract to another Resident. The composer's
  // runtime button shows the settings and opens the runtime dialog; the header
  // button opens the Session restart when the contract offers one and
  // refreshes the Chat otherwise; the header shows a restart in progress. The
  // Chat refreshes when the controller reports a change: a saved setting or a
  // restart that ended. While the host changes the Resident
  // (releaseRuntimeTarget) the controls wait for the next contract;
  // destroyRuntimeControls lets the Resident go.
  const DEPENDENCIES = Object.freeze([
    "contractResidentId", "createSessionRestartProgress", "currentController", "els", "options",
    "residentRuntimeStatusText", "sessionRestartActive", "state",
  ]);

  function createRuntimeControls(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceRuntimeControls is missing: ${missing.join(", ")}`);
    const {
      contractResidentId, createSessionRestartProgress, currentController, els, options,
      residentRuntimeStatusText, sessionRestartActive, state,
    } = dependencies;

    const runtimeButton = () => els.composer.querySelector("[data-workspace-runtime-settings-open]");
    const showRuntimeView = view => {
      const button = runtimeButton();
      if (!button) return;
      button.disabled = view.loading || view.busy || sessionRestartActive(view.restart) || (!view.canEdit && !view.canRestart);
      // A reader who may not change the settings gets no catalog; the Backend's
      // declared fields still name them.
      const catalog = view.catalog || { runtime_capabilities: view.detail?.backend_capabilities?.runtime_capabilities };
      const text = residentRuntimeStatusText(view.settings, catalog);
      const label = button.querySelector(".composerRuntimeLabel");
      if (label) label.textContent = text;
      button.title = window.PortalI18n.text`切换模型与运行设置：${text}`;
    };

    // The binding goes as soon as the contract names another Resident
    // (followContractResident), so what it reports is always about this Chat's
    // Resident.
    let residentRuntime = null, runtimeResidentId = "", runtimeTargetPending = false;
    const releaseResidentRuntime = () => {
      residentRuntime?.destroy(); residentRuntime = null; runtimeResidentId = "";
    };
    const residentRuntimeController = () => {
      if (runtimeTargetPending) return null;
      const residentId = contractResidentId();
      if (!residentId) return null;
      if (!residentRuntime) {
        runtimeResidentId = residentId;
        residentRuntime = window.PortalResidentRuntimeController.bind(residentId, {
          onState: showRuntimeView,
          onChanged: () => void options.onRefresh?.(currentController()),
        });
      }
      return residentRuntime;
    };
    const openResidentDialog = async kind => {
      const runtime = residentRuntimeController();
      if (!runtime) return;
      try {
        await (kind === "restart" ? runtime.openRestart(runtimeButton) : runtime.openSettings(runtimeButton));
      } catch (error) {
        window.PortalUI.toast(error.message, "error");
      }
    };
    const sessionRestartAvailable = () => {
      const action = state.contract?.actions?.session_restart;
      return !runtimeTargetPending && !!contractResidentId() && !!(action?.instruction || action?.url);
    };

    const restartService = window.PortalResidentService;
    const restartProgress = createSessionRestartProgress({
      compact: true,
      owner: options.draftOwner,
      onOpen: () => openResidentDialog("restart"),
      onDismiss: () => els.refresh?.focus(),
    });
    els.status?.closest(".chatHeadText")?.append(restartProgress.element);
    let restartUnobserve = null;
    let restartResidentId = "";
    // The header follows the restarts of the contract's Resident while the Chat offers them.
    const syncSessionRestartObservation = () => {
      const residentId = sessionRestartAvailable() ? contractResidentId() : "";
      if (residentId === restartResidentId || !restartService) return;
      restartUnobserve?.();
      restartUnobserve = null;
      restartResidentId = residentId;
      restartProgress.update({});
      if (residentId) restartUnobserve = restartService.observeSessionRestart(residentId, view => restartProgress.update(view));
    };

    const handleRefreshClick = event => {
      event.preventDefault();
      if (sessionRestartAvailable()) void openResidentDialog("restart");
      else options.onRefresh?.(currentController());
    };
    els.refresh?.addEventListener("click", handleRefreshClick);

    // Every composer draw brings a new runtime button. Its label needs the
    // Resident's catalog, so the Resident's controller paints it at once and
    // keeps it current.
    const bindRuntimeButton = () => {
      const button = runtimeButton();
      if (!button || button.dataset.workspaceRuntimeSettingsBound === "1") return;
      button.dataset.workspaceRuntimeSettingsBound = "1";
      button.addEventListener("click", () => openResidentDialog("settings"));
      const runtime = residentRuntimeController();
      if (!runtime) {
        button.disabled = true;
        return;
      }
      showRuntimeView(runtime.state());
      runtime.load().catch(() => {});
    };
    // A contract names the Resident: the controls stop waiting and leave a previous one.
    const followContractResident = () => {
      runtimeTargetPending = false;
      if (runtimeResidentId && runtimeResidentId !== contractResidentId()) releaseResidentRuntime();
      syncSessionRestartObservation();
    };
    const releaseRuntimeTarget = () => {
      releaseResidentRuntime();
      runtimeTargetPending = true;
      const button = runtimeButton();
      if (button) button.disabled = true;
    };
    const destroyRuntimeControls = () => {
      releaseResidentRuntime();
      restartUnobserve?.();
      restartProgress.destroy();
      els.refresh?.removeEventListener("click", handleRefreshClick);
    };

    return { sessionRestartAvailable, bindRuntimeButton, followContractResident, releaseRuntimeTarget, destroyRuntimeControls };
  }

  window.PortalChatWorkspaceRuntimeControls = Object.freeze({ createRuntimeControls, dependencies: DEPENDENCIES });
})();
