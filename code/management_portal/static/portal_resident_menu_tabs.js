(() => {
  "use strict";

  if (window.PortalResidentMenuTabs) return;

  // The Menu's vertical tabs: Settings, Duties, Memory, Harness, Tools, Package,
  // Mailbox and Work Journal. Switching tabs by click or arrow keys first leaves
  // the active editor (unsaved changes ask before they are dropped), then
  // mounts the chosen tab's panel: the duties, the memory, the Harness
  // settings, the principal details, the Mailbox queue, the journal or the
  // runtime settings.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "disposeFamilySettings", "loadNodeDuties", "loadNodeJournal", "loadNodeMemory",
    "loadNodeMenuRuntime", "menuState", "nodeResidentId", "nodeSettingsNode", "toast",
  ]);

  function createTabs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuTabs is missing: ${missing.join(", ")}`);
    const {
      $id, changed, disposeFamilySettings, loadNodeDuties, loadNodeJournal, loadNodeMemory, loadNodeMenuRuntime, menuState,
      nodeResidentId, nodeSettingsNode, toast,
    } = dependencies;

    let nodeEditorInstance = null;
    let nodeEditorEpoch = 0;
    let nodeEditorTransition = false;

    function disposeNodeEditor() {
      nodeEditorEpoch += 1;
      nodeEditorInstance?.destroy?.();
      nodeEditorInstance = null;
      $id("nodeDetailsPanel")?.replaceChildren();
      $id("nodeToolsPanel")?.replaceChildren();
    }

    async function leaveNodeEditor() {
      if (nodeEditorTransition || menuState.nodeTransferInFlight || menuState.nodeMenuContext?.busy || menuState.nodeMenuContext?.saving || menuState.nodeMenuContext?.intakeBusy) return false;
      if (nodeEditorInstance?.isBusy?.()) {
        toast(window.PortalI18n.t(currentNodeSettingsTab() === "tools" ? "工具设置正在保存，请稍后再试。" : "凭证操作正在处理，请稍后再试。"), "info");
        return false;
      }
      const epoch = nodeEditorEpoch;
      nodeEditorTransition = true;
      try {
        if (nodeEditorInstance?.hasUnsavedChanges?.() && !await window.PortalUI.confirm({
          title: window.PortalI18n.t(currentNodeSettingsTab() === "tools" ? "放弃未保存的工具修改？" : "放弃未保存的凭证修改？"),
          message: window.PortalI18n.t(currentNodeSettingsTab() === "tools" ? "离开将清除尚未保存的工具修改。" : "离开 Package 将丢失尚未保存的凭证修改。"),
          confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger",
        })) return false;
        return epoch === nodeEditorEpoch && !nodeEditorInstance?.isBusy?.();
      } finally {
        nodeEditorTransition = false;
      }
    }

    // The node dialog's vertical navigation: tab name -> [tab button, panel].
    const NODE_SETTINGS_TABS = Object.freeze({
      display: ["nodeDisplayTab", "nodeDisplayPanel"],
      duties: ["nodeDutiesTab", "nodeDutiesPanel"],
      memory: ["nodeMemoryTab", "nodeMemoryPanel"],
      harness: ["nodeHarnessTab", "nodeHarnessPanel"],
      tools: ["nodeToolsTab", "nodeToolsPanel"],
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
      if (!await leaveNodeEditor()) return;
      // Leaving Settings drops an unsaved avatar choice, and the portrait shows the saved one again.
      menuState.nodeMenuContext?.avatarEditor?.close();
      disposeNodeEditor();
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
      if (tab === "duties") { await loadNodeDuties(); return; }
      if (tab === "memory") { await loadNodeMemory(); return; }
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
      if (tab !== "details" && tab !== "tools") return;
      const epoch = nodeEditorEpoch;
      const container = document.createElement("div");
      container.textContent = window.PortalI18n.t(tab === "tools" ? "正在加载工具设置…" : "正在加载 Package…");
      $id(NODE_SETTINGS_TABS[tab][1]).replaceChildren(container);
      try {
        let instance;
        if (tab === "tools") {
          await window.PortalUIPrimitives.loadAsset("/static/portal_tool_settings.js", {ready: () => window.PortalToolSettings});
          if (epoch !== nodeEditorEpoch) return;
          instance = window.PortalToolSettings.mount(container, {residentId: nodeResidentId(node)});
        } else {
          instance = await window.PortalNav.mountPrincipalDetails({ container, principalId: nodeResidentId(node) });
        }
        if (epoch !== nodeEditorEpoch) {
          instance?.destroy?.();
          return;
        }
        nodeEditorInstance = instance;
      } catch (error) {
        if (epoch === nodeEditorEpoch) container.textContent = tab === "tools"
          ? window.PortalI18n.text`工具设置加载失败：${error.message || error}`
          : window.PortalI18n.text`Package 加载失败：${error.message || error}`;
      }
    }

    return {
      disposeNodeEditor, disposeNodeHarness, disposeNodeMailbox, leaveNodeEditor, moveNodeSettingsTab,
      renderNodeSettingsTab, switchNodeSettingsTab,
    };
  }

  window.PortalResidentMenuTabs = Object.freeze({ createTabs, dependencies: DEPENDENCIES });
})();
