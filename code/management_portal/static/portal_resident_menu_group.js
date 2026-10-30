(() => {
  "use strict";

  if (window.PortalResidentMenuGroup) return;

  // A Function Block's Menu: the tabs of the block's own panel and of the
  // Residents inside it, the block panel and switching between the tabs in
  // this one dialog.

  const DEPENDENCIES = Object.freeze([
    "$id", "esc", "leaveNodeMenu", "menuState", "modal", "open",
  ]);

  function createGroup(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuGroup is missing: ${missing.join(", ")}`);
    const {
      $id, esc, leaveNodeMenu, menuState, modal, open,
    } = dependencies;

    // A Function Block's Menu: tabs switch between the block's own
    // panel and the Menus of the Residents inside it, in this one dialog.
    function groupTab(key = menuState.options.group?.active) {
      return (menuState.options.group?.tabs || []).find(tab => tab.key === key) || null;
    }

    function renderGroupTabs() {
      const strip = $id("nodeMenuGroupTabs");
      const tabs = menuState.options.group?.tabs || [];
      strip.hidden = tabs.length === 0;
      strip.innerHTML = tabs.map(tab => {
        const active = tab.key === menuState.options.group.active;
        return window.PortalI18n.html`<button class="residentMenuGroupTab${active ? " active" : ""}" type="button" role="tab" data-group-tab="${esc(tab.key)}" title="${esc(tab.label)}" aria-selected="${active}" aria-controls="${tab.residentId ? "nodeDisplayPanel" : "nodeMenuGroupPanel"}" tabindex="${active ? 0 : -1}">${esc(tab.label)}</button>`;
      }).join("");
    }

    function disposeGroupPanel() {
      const cleanup = menuState.groupPanelCleanup;
      menuState.groupPanelCleanup = null;
      cleanup?.();
      $id("nodeMenuGroupPanel").replaceChildren();
    }

    function setGroupPanelMode(panel) {
      $id("nodeMenuGroupPanel").hidden = !panel;
      modal.querySelector(".nodeSettingsResidentTabs").hidden = panel;
      if (panel) modal.querySelectorAll(".nodeSettingsPanels > section").forEach(section => { section.hidden = true; });
      modal.querySelector(".residentMenuIdentity").hidden = panel;
      $id("nodeMenuPin").hidden = panel;
      if (panel) {
        $id("nodeMenuStatus").hidden = true;
        $id("nodeTransferFields").hidden = true;
      }
    }

    async function switchGroupTab(key) {
      const tab = groupTab(key);
      if (!tab || key === menuState.options.group.active || !await leaveNodeMenu()) return;
      await open({...menuState.options, residentId: tab.residentId || "", node: tab.node || null, tab: "display",
        group: {...menuState.options.group, active: key}});
    }

    return {
      disposeGroupPanel, groupTab, renderGroupTabs, setGroupPanelMode, switchGroupTab,
    };
  }

  window.PortalResidentMenuGroup = Object.freeze({ createGroup, dependencies: DEPENDENCIES });
})();
