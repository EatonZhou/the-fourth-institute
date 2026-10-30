(function (global) {
  "use strict";

  if (global.PortalWorkbenchChrome) return;

  // The workbench chrome: the stage, the toolbar with the hidden-Panel rail,
  // fixed-Panel switchers, layout and lock buttons, the layout menu, the
  // canvas, the mobile switcher and the parking area for unplaced Panels,
  // placed in the stage or in the page header.

  const DEPENDENCIES = Object.freeze([
    "elementOf", "escapeHtml", "id", "options", "root", "shellState",
  ]);

  function createChrome(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchChrome is missing: ${missing.join(", ")}`);
    const {
      elementOf, escapeHtml, id, options, root, shellState,
    } = dependencies;

    const stage = document.createElement("div");
    stage.className = "portalWorkbenchStage";
    stage.dataset.portalWorkbenchStage = "1";
    stage.style.setProperty(
      "--portal-workbench-toolbar-right",
      `${Math.max(0, Number(options.toolbarRight ?? 2))}px`,
    );
    const toolbarPlacement = options.toolbarPlacement === "tabs" ? "tabs" : "row";
    const pageChromeHost = toolbarPlacement === "tabs"
      ? null
      : elementOf(options.toolbarHost) || document.querySelector(".portalNavTop");
    const chrome = pageChromeHost ? document.createElement("div") : null;
    shellState.hostVisible = options.visible !== false;
    if (chrome) {
      chrome.className = "portalWorkbenchChrome";
      chrome.dataset.portalWorkbenchChrome = id;
      chrome.hidden = !shellState.hostVisible;
      stage.classList.add("has-chrome-toolbar");
    }
    const toolbar = document.createElement("div");
    toolbar.className = "portalWorkbenchToolbar";
    const hiddenRail = document.createElement("div");
    hiddenRail.className = "portalWorkbenchHiddenRail";
    hiddenRail.setAttribute("aria-label", window.PortalI18n.t("已隐藏面板"));
    const customizeButton = document.createElement("button");
    customizeButton.className = "portalButton is-compact is-quiet portalWorkbenchCustomizeButton";
    customizeButton.type = "button";
    customizeButton.dataset.portalIcon = "auto-layout";
    const customizeLabel = window.PortalI18n.t("调整布局");
    customizeButton.setAttribute("aria-label", customizeLabel);
    customizeButton.title = customizeLabel;
    customizeButton.setAttribute("aria-expanded", "false");
    customizeButton.innerHTML = window.PortalI18n.html`<span>${escapeHtml(customizeLabel)}</span>`;
    const lockButton = document.createElement("button");
    lockButton.className = "portalButton is-compact is-icon is-quiet portalWorkbenchLockButton";
    lockButton.type = "button";
    lockButton.dataset.portalIcon = "lock";
    const fixedSwitchers = document.createElement("div");
    fixedSwitchers.className = "portalWorkbenchFixedSwitchers";
    fixedSwitchers.hidden = true;
    const menu = document.createElement("div");
    menu.className = "portalWorkbenchMenu";
    menu.hidden = true;
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", window.PortalI18n.t("工作区布局"));
    const canvas = document.createElement("div");

    canvas.className = "portalWorkbenchCanvas";
    const mobileSwitcher = document.createElement("nav");
    mobileSwitcher.className = "portalWorkbenchMobileSwitcher";
    mobileSwitcher.setAttribute("aria-label", window.PortalI18n.t("移动工作区面板"));
    mobileSwitcher.hidden = true;
    const parking = document.createElement("div");
    parking.className = "portalWorkbenchParking";
    parking.hidden = true;
    toolbar.append(hiddenRail, fixedSwitchers, customizeButton, lockButton);
    if (options.showSingleTabHeader === true) stage.classList.add("show-single-tab-header");
    if (toolbarPlacement === "tabs") {
      stage.classList.add("has-tab-toolbar");
      stage.append(toolbar, menu, canvas, mobileSwitcher, parking);
    } else if (chrome) {
      chrome.append(toolbar, menu);
      pageChromeHost.insertBefore(chrome, pageChromeHost.querySelector(".portalUser"));
      stage.append(canvas, mobileSwitcher, parking);
    } else {
      stage.append(toolbar, menu, canvas, mobileSwitcher, parking);
    }
    root.append(stage);

    return {
      canvas, chrome, customizeButton, customizeLabel, fixedSwitchers, hiddenRail, lockButton, menu, mobileSwitcher,
      parking, stage, toolbar,
    };
  }

  global.PortalWorkbenchChrome = Object.freeze({ createChrome, dependencies: DEPENDENCIES });
})(window);
