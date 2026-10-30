(() => {
  "use strict";

  if (window.PortalResidentMenuEvents) return;

  // The dialog's controls: closing, the name form, the group and Resident
  // tabs, Move, the intake switch, journal retry, pin, copying the Session
  // ID, renaming and removal.

  const DEPENDENCIES = Object.freeze([
    "$id", "dialog", "loadNodeJournal", "menuState", "moveNodeSettingsTab", "nodeMenuNotice", "nodeSettingsNode",
    "openNodeTransfer", "removeResident", "submitNodeSettings", "submitNodeTransfer", "switchGroupTab",
    "switchNodeSettingsTab", "toast", "toggleNodeMenuPin", "updateIntake",
  ]);

  function createEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuEvents is missing: ${missing.join(", ")}`);
    const {
      $id, dialog, loadNodeJournal, menuState, moveNodeSettingsTab, nodeMenuNotice, nodeSettingsNode,
      openNodeTransfer, removeResident, submitNodeSettings, submitNodeTransfer, switchGroupTab, switchNodeSettingsTab,
      toast, toggleNodeMenuPin, updateIntake,
    } = dependencies;

    $id("nodeSettingsModal").querySelector("[data-portal-detail-close]").addEventListener("click", () => dialog.close());
    $id("nodeSettingsForm").addEventListener("submit", submitNodeSettings);
    $id("nodeMenuGroupTabs").addEventListener("click", event => {
      const button = event.target.closest("[data-group-tab]");
      if (button) void switchGroupTab(button.dataset.groupTab).catch(error => toast(error.message || String(error), "error"));
    });
    $id("nodeMenuGroupTabs").addEventListener("keydown", event => {
      const tabs = menuState.options.group?.tabs || [];
      const index = tabs.findIndex(tab => tab.key === event.target.closest("[data-group-tab]")?.dataset.groupTab);
      if (index < 0) return;
      const next = { ArrowDown: (index + 1) % tabs.length, ArrowUp: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      void switchGroupTab(tabs[next].key).then(() => $id("nodeMenuGroupTabs").querySelector("[aria-selected='true']")?.focus())
        .catch(error => toast(error.message || String(error), "error"));
    });
    $id("nodeDisplayTab").addEventListener("click", () => switchNodeSettingsTab("display"));
    $id("nodeHarnessTab").addEventListener("click", () => switchNodeSettingsTab("harness"));
    $id("nodeDetailsTab").addEventListener("click", () => switchNodeSettingsTab("details"));
    $id("nodeMailboxTab").addEventListener("click", () => switchNodeSettingsTab("mailbox"));
    $id("nodeDisplayTab").closest('[role="tablist"]').addEventListener("keydown", moveNodeSettingsTab);
    $id("transferNodeBtn").addEventListener("click", openNodeTransfer);
    $id("nodeTransferConfirmBtn").addEventListener("click", submitNodeTransfer);
    $id("nodeTransferTarget").addEventListener("change", () => {
      $id("nodeTransferConfirmBtn").disabled = !menuState.nodeTransferContext || menuState.nodeTransferContext.busy || !$id("nodeTransferTarget").value;
    });
    $id("nodeSettingsIntakeToggle").addEventListener("click", () => {
      const node = nodeSettingsNode();
      if (node) updateIntake(node);
    });
    $id("nodeJournalTab").addEventListener("click", () => switchNodeSettingsTab("journal"));
    $id("nodeJournalRetry").addEventListener("click", () => loadNodeJournal());
    $id("nodeMenuPin").addEventListener("click", toggleNodeMenuPin);
    $id("nodeMenuCopySession").addEventListener("click", () => {
      if (menuState.nodeMenuContext?.sessionId) {
        void window.PortalUI.copyText(menuState.nodeMenuContext.sessionId, window.PortalI18n.t("Session ID")).catch(error => nodeMenuNotice(error.message));
      }
    });
    $id("nodeMenuRename").addEventListener("click", () => {
      $id("nodeMenuRenameFields").hidden = false;
      $id("nodeSettingsName").focus();
    });
    $id("nodeMenuRenameCancel").addEventListener("click", () => {
      $id("nodeSettingsName").value = nodeSettingsNode()?.title || "";
      $id("nodeMenuRenameFields").hidden = true;
    });
    $id("removeNodeBtn").addEventListener("click", () => {
      const node = nodeSettingsNode();
      if (node) removeResident(node);
    });
  }

  window.PortalResidentMenuEvents = Object.freeze({ createEvents, dependencies: DEPENDENCIES });
})();
