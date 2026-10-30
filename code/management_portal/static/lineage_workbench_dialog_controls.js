(() => {
  "use strict";

  if (window.PortalLineageWorkbenchDialogControls) return;

  // The controls of the Resident dialogs on the page: register, attach, edit,
  // fork and cross-Lineage fork forms and their account, route and model fields.
  const DEPENDENCIES = Object.freeze([
    "$id", "closeCrossLineageForkModal", "closeResidentAttachChoiceModal", "closeResidentEditModal",
    "closeResidentForkCreateModal", "closeResidentRegisterModal", "handleForkCreateAction",
    "lineageIdForResident", "lineageLeaderResidentId", "loadCrossLineageForkNodes",
    "openAttachResidentModal", "openResidentForkCreateModal", "renderCrossLineageForkModal",
    "renderResidentAttachChoiceModal", "renderResidentForkAccount", "resetResidentAttachOptionState",
    "residentAttachChoiceItem", "restoreResidentRegisterForm", "state", "submitCrossLineageFork",
    "submitResidentEditSurface", "submitResidentRegister", "syncResidentBackendRuntimeFields",
    "syncResidentEffortSelect",
  ]);

  function createDialogControls(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchDialogControls is missing: ${missing.join(", ")}`);
    const {
      $id, closeCrossLineageForkModal, closeResidentAttachChoiceModal, closeResidentEditModal,
      closeResidentForkCreateModal, closeResidentRegisterModal, handleForkCreateAction,
      lineageIdForResident, lineageLeaderResidentId, loadCrossLineageForkNodes, openAttachResidentModal,
      openResidentForkCreateModal, renderCrossLineageForkModal, renderResidentAttachChoiceModal,
      renderResidentForkAccount, resetResidentAttachOptionState, residentAttachChoiceItem,
      restoreResidentRegisterForm, state, submitCrossLineageFork, submitResidentEditSurface,
      submitResidentRegister, syncResidentBackendRuntimeFields, syncResidentEffortSelect,
    } = dependencies;

    $id("residentRegisterForm").addEventListener("submit", submitResidentRegister);
    $id("residentRegisterClose").addEventListener("click", closeResidentRegisterModal);
    $id("residentRegisterCancel").addEventListener("click", closeResidentRegisterModal);
    $id("residentAttachClose").addEventListener("click", closeResidentAttachChoiceModal);
    $id("residentAttachOverlay").addEventListener("click", async event => {
      const target = event.target instanceof Element ? event.target : null;
      const forkCreateBtn = target?.closest("[data-fork-create-action]");
      if (forkCreateBtn) {
        await handleForkCreateAction(forkCreateBtn.dataset.forkCreateAction || "");
      }
    });
    $id("residentAttachNewButton").addEventListener("click", () => {
      const item = residentAttachChoiceItem();
      if (!item) return;
      closeResidentForkCreateModal({ keepAttachOpen: true });
      resetResidentAttachOptionState();
      state.residentAttachChoice = {
        open: false,
        lineageId: "",
        leaderResidentId: "",
        title: "",
      };
      renderResidentAttachChoiceModal();
      openAttachResidentModal(item);
    });
    $id("residentAttachForkDetails").addEventListener("toggle", event => {
      const details = event.currentTarget;
      if (!(details instanceof HTMLDetailsElement) || !details.open) return;
      restoreResidentRegisterForm();
      const item = residentAttachChoiceItem();
      const lineageId = lineageIdForResident(item);
      const leaderResidentId = String(
        state.residentAttachChoice?.leaderResidentId
        || lineageLeaderResidentId(item)
        || ""
      ).trim();
      if (!item || !lineageId || !leaderResidentId) {
        window.PortalUI.toast(
          window.PortalI18n.t("当前 Lineage 缺少可用于生成分支的 Leader Resident。"),
          "warning",
        );
        details.open = false;
        return;
      }
      const leader = state.residents.find(candidate => (
        String(candidate?.resident_id || "") === leaderResidentId
      )) || {
        resident_id: leaderResidentId,
        title: window.PortalI18n.t("Leader Resident"),
        lineage_id: lineageId,
      };
      openResidentForkCreateModal(leader, {
        intent: "attach",
        inline: true,
        lineageId,
      });
    });
    $id("residentAttachForkDetails").addEventListener("toggle", event => {
      const details = event.currentTarget;
      if (!(details instanceof HTMLDetailsElement) || details.open) return;
      if (state.residentForkCreateInlineHost === "attach") {
        closeResidentForkCreateModal({ keepAttachOpen: true });
      }
    });
    $id("residentEditForm").addEventListener("submit", submitResidentEditSurface);
    $id("residentEditClose").addEventListener("click", closeResidentEditModal);
    $id("residentEditCancel").addEventListener("click", closeResidentEditModal);
    $id("residentForkCreateOverlay").addEventListener("click", async event => {
      const target = event.target instanceof Element ? event.target : null;
      const actionBtn = target?.closest("[data-fork-create-action]");
      if (!actionBtn) return;
      await handleForkCreateAction(actionBtn.dataset.forkCreateAction || "");
    });
    $id("residentForkCreateClose").addEventListener("click", closeResidentForkCreateModal);
    $id("residentForkCreateCancel").addEventListener("click", closeResidentForkCreateModal);
    $id("residentForkAccountInput").addEventListener("change", event => {
      state.residentForkCreate.backendRouteId = String(
        event.target.value || ""
      ).trim();
      renderResidentForkAccount();
    });
    $id("residentForkProviderInput").addEventListener("change", event => {
      state.residentForkCreate.provider = event.target.value;
      state.residentForkCreate.backendRouteId = undefined;
      renderResidentForkAccount();
    });
    $id("crossLineageForkForm").addEventListener("submit", submitCrossLineageFork);
    $id("crossLineageForkClose").addEventListener("click", closeCrossLineageForkModal);
    $id("crossLineageForkCancel").addEventListener("click", closeCrossLineageForkModal);
    $id("crossLineageForkLineageSelect").addEventListener("change", event => {
      loadCrossLineageForkNodes(event.target.value || "");
    });
    $id("crossLineageForkResidentSelect").addEventListener("change", event => {
      state.crossLineageFork.residentId = String(event.target.value || "");
      renderCrossLineageForkModal();
    });
    $id("residentBackendSelect").addEventListener("change", syncResidentBackendRuntimeFields);
    $id("residentRouteSelect").addEventListener("change", () => {
      state.residentRouteChosen = true;
      syncResidentBackendRuntimeFields();
    });
    $id("residentRuntimeModelSelect").addEventListener("change", () => {
      syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", $id("residentRuntimeEffortSelect")?.value || "");
    });

    return Object.freeze({});
  }

  window.PortalLineageWorkbenchDialogControls = Object.freeze({
    createDialogControls, dependencies: DEPENDENCIES,
  });
})();
