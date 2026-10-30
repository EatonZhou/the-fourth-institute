(() => {
  "use strict";

  if (window.PortalResidentMenuTabs) return;

  // The Menu's vertical tabs: Settings, Harness, Package, Mailbox and Work
  // Journal. Switching tabs by click or arrow keys first leaves Package (an
  // unsaved credential change asks before it is dropped), then mounts the
  // chosen tab's panel: the Harness settings, the principal details, the
  // Mailbox queue, the journal or the runtime settings.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "disposeFamilySettings", "loadNodeJournal", "loadNodeMenuRuntime", "menuState",
    "nodeResidentId", "nodeSettingsNode", "toast",
  ]);

  function createTabs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuTabs is missing: ${missing.join(", ")}`);
    const {
      $id, changed, disposeFamilySettings, loadNodeJournal, loadNodeMenuRuntime, menuState, nodeResidentId,
      nodeSettingsNode, toast,
    } = dependencies;

    let nodeDetailsInstance = null;
    let nodeDetailsEpoch = 0;
    let nodeDetailsTransition = false;

    function disposeNodeDetails() {
      nodeDetailsEpoch += 1;
      nodeDetailsInstance?.destroy?.();
      nodeDetailsInstance = null;
      $id("nodeDetailsPanel")?.replaceChildren();
    }

    async function leaveNodeDetails() {
      if (nodeDetailsTransition || menuState.nodeTransferInFlight || menuState.nodeMenuContext?.busy || menuState.nodeMenuContext?.saving || menuState.nodeMenuContext?.intakeBusy) return false;
      if (nodeDetailsInstance?.isBusy?.()) {
        toast(window.PortalI18n.t("凭证操作正在处理，请稍后再试。"), "info");
        return false;
      }
      const epoch = nodeDetailsEpoch;
      nodeDetailsTransition = true;
      try {
        if (nodeDetailsInstance?.hasUnsavedChanges?.() && !await window.PortalUI.confirm({
          title: window.PortalI18n.t("放弃未保存的凭证修改？"), message: window.PortalI18n.t("离开 Package 将丢失尚未保存的凭证修改。"),
          confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger",
        })) return false;
        return epoch === nodeDetailsEpoch && !nodeDetailsInstance?.isBusy?.();
      } finally {
        nodeDetailsTransition = false;
      }
    }

    // The node dialog's vertical navigation: tab name -> [tab button, panel].
    const NODE_SETTINGS_TABS = Object.freeze({
      display: ["nodeDisplayTab", "nodeDisplayPanel"],
      harness: ["nodeHarnessTab", "nodeHarnessPanel"],
      details: ["nodeDetailsTab", "nodeDetailsPanel"],
      mailbox: ["nodeMailboxTab", "nodeMailboxPanel"],
      journal: ["nodeJournalTab", "nodeJournalPanel"],
    });
    let nodeMailboxInstance = null;
    let nodeHarnessInstance = null;

    // The Harness page holds the Resident's Harness settings, moved here from the Chat gear.
    function disposeNodeHarness() {
      nodeHarnessInstance?.destroy?.();
      nodeHarnessInstance = null;
      $id("nodeHarnessPanel")?.replaceChildren();
    }

    // The Mailbox tab follows the queue only while it is open.
    function disposeNodeMailbox() {
      nodeMailboxInstance?.destroy?.();
      nodeMailboxInstance = null;
      $id("nodeMailboxPanel")?.replaceChildren();
    }

    function currentNodeSettingsTab() {
      return Object.keys(NODE_SETTINGS_TABS).find(
        tab => $id(NODE_SETTINGS_TABS[tab][0])?.getAttribute("aria-selected") === "true",
      ) || "display";
    }

    function renderNodeSettingsTab(tab) {
      for (const [name, [tabId, panelId]] of Object.entries(NODE_SETTINGS_TABS)) {
        const selected = name === tab;
        $id(panelId).hidden = !selected;
        $id(tabId).setAttribute("aria-selected", String(selected));
        $id(tabId).tabIndex = selected ? 0 : -1;
        $id(tabId).classList.toggle("active", selected);
      }
      // The card keeps its size; each tab starts at its top.
      const body = $id(NODE_SETTINGS_TABS[tab]?.[1])?.closest(".nodeSettingsBody");
      if (body) body.scrollTop = 0;
    }

    function moveNodeSettingsTab(event) {
      const names = Object.keys(NODE_SETTINGS_TABS);
      const index = names.indexOf(currentNodeSettingsTab());
      const next = {
        ArrowDown: names[(index + 1) % names.length],
        ArrowUp: names[(index + names.length - 1) % names.length],
        Home: names[0],
        End: names[names.length - 1],
      }[event.key];
      if (!next) return;
      event.preventDefault();
      void switchNodeSettingsTab(next).then(() => $id(NODE_SETTINGS_TABS[currentNodeSettingsTab()][0])?.focus());
    }

    async function switchNodeSettingsTab(tab) {
      if (currentNodeSettingsTab() === tab) return;
      if (menuState.nodeMenuContext?.avatarEditor?.isBusy()) { toast(window.PortalI18n.t("头像正在保存，请稍后再试。"), "info"); return; }
      if (!await leaveNodeDetails()) return;
      // Leaving Settings drops an unsaved avatar choice, and the portrait shows the saved one again.
      menuState.nodeMenuContext?.avatarEditor?.close();
      disposeNodeDetails();
      disposeNodeMailbox();
      disposeNodeHarness();
      disposeFamilySettings();
      renderNodeSettingsTab(tab);
      const node = nodeSettingsNode();
      if (!node) return;
      if (tab === "mailbox") {
        nodeMailboxInstance = window.PortalMailboxPanel?.mount({
          container: $id("nodeMailboxPanel"),
          residentId: nodeResidentId(node),
          toast,
        }) || null;
        return;
      }
      if (tab === "journal") { await loadNodeJournal(); return; }
      if (tab === "harness") {
        const residentId = nodeResidentId(node);
        nodeHarnessInstance = window.PortalChatWorkspace?.mountResidentHarnessPanel?.({
          container: $id("nodeHarnessPanel"),
          residentId,
          onChanged: () => changed({type: "settings", residentId, lineageId: menuState.nodeMenuContext?.lineageId}),
        }) || null;
        return;
      }
      if (tab === "display") { await loadNodeMenuRuntime(); return; }
      if (tab !== "details") return;
      const epoch = nodeDetailsEpoch;
      const container = document.createElement("div");
      container.textContent = window.PortalI18n.t("正在加载 Package…");
      $id("nodeDetailsPanel").replaceChildren(container);
      try {
        const instance = await window.PortalNav.mountPrincipalDetails({ container, principalId: nodeResidentId(node) });
        if (epoch !== nodeDetailsEpoch) {
          instance?.destroy?.();
          return;
        }
        nodeDetailsInstance = instance;
      } catch (error) {
        if (epoch === nodeDetailsEpoch) container.textContent = window.PortalI18n.text`Package 加载失败：${error.message || error}`;
      }
    }

    return {
      disposeNodeDetails, disposeNodeHarness, disposeNodeMailbox, leaveNodeDetails, moveNodeSettingsTab,
      renderNodeSettingsTab, switchNodeSettingsTab,
    };
  }

  window.PortalResidentMenuTabs = Object.freeze({ createTabs, dependencies: DEPENDENCIES });
})();
