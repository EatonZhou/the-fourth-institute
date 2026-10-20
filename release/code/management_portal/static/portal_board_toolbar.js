(function (global) {
  "use strict";

  if (global.PortalBoardToolbar) return;

  const instances = new WeakMap();
  function icon(name, className = "") {
    return global.PortalUIPrimitives?.createIcon?.(name, className) || null;
  }

  function direct(root, selector) {
    return Array.from(root.children).find(child => child.matches(selector)) || null;
  }

  function ensureLeadingIcon(button, name) {
    if (!(button instanceof HTMLButtonElement) || !global.PortalUIPrimitives?.iconHtml?.(name)) return;
    const current = button.querySelector(":scope > svg");
    if (current?.dataset.portalBoardIcon === name) return;
    if (current) current.remove();
    const node = icon(name);
    if (!node) return;
    node.dataset.portalBoardIcon = name;
    button.prepend(node);
  }

  function normalizeSearch(root) {
    const search = direct(root, "[data-portal-board-search]");
    if (!search) return null;
    search.classList.add("portalBoardSearch");
    search.dataset.portalSearchField = "1";
    const input = search.querySelector("[data-portal-board-search-input], input");
    if (input instanceof HTMLInputElement) {
      input.dataset.portalBoardSearchInput = "1";
      global.PortalFormControls?.mountSearchField(search);
    }

    const action = search.querySelector("[data-portal-board-search-action], [data-portal-board-semantic]");
    if (action instanceof HTMLButtonElement) {
      action.classList.add("portalButton", "is-compact", "portalBoardSearchAction");
      if (action.matches("[data-portal-board-semantic]")) {
        action.classList.add("portalBoardSemantic");
      }
      const iconName = String(
        action.dataset.portalBoardSearchIcon
        || (action.matches("[data-portal-board-semantic]") ? "semantic" : ""),
      ).trim();
      if (iconName) ensureLeadingIcon(action, iconName);
    }
    return search;
  }

  function normalizeAction(item) {
    const action = item.dataset.portalBoardAction
      ? item
      : item.querySelector?.("[data-portal-board-action]");
    if (!(action instanceof HTMLButtonElement)) return;
    const kind = String(action.dataset.portalBoardAction || "action").trim().toLowerCase();
    action.classList.add("portalButton", "is-compact", "portalBoardAction");
    if (!action.classList.contains("is-primary")) action.classList.add("is-quiet");
    if (kind === "filter") ensureLeadingIcon(action, "filter");
    if (kind === "refresh") {
      action.classList.add("is-icon");
      ensureLeadingIcon(action, "refresh");
      if (!action.getAttribute("aria-label")) action.setAttribute("aria-label", window.PortalI18n.t("刷新"));
      if (!action.title) action.title = window.PortalI18n.t("刷新");
    }
  }

  function normalizeActions(root) {
    const actions = direct(root, "[data-portal-board-actions]");
    if (!actions) return null;
    actions.classList.add("portalBoardActions");
    actions.dataset.portalToolbar = "1";
    Array.from(actions.children).forEach(item => {
      if (item.matches("[data-portal-toolbar-more], .portalToolbarOverflowButton")) return;
      if (!item.hasAttribute("data-portal-toolbar-item")) item.dataset.portalToolbarItem = "1";
      if (item.matches("[data-portal-board-filter]")) item.classList.add("portalBoardFilter");
      if (item.matches("[data-portal-board-divider]")) item.classList.add("portalBoardDivider");
      normalizeAction(item);
      if (item.matches("[data-portal-board-filter-auto]") && !item.dataset.portalBoardFilterBound) {
        const button = item.querySelector("[data-portal-board-action='filter']");
        const panel = item.querySelector("[data-portal-board-filter-panel]");
        if (button && panel && global.PortalFormControls?.bindFilterDropdown) {
          global.PortalFormControls.bindFilterDropdown({
            button,
            panel,
            onToggle: open => button.classList.toggle("active", open),
          });
          item.dataset.portalBoardFilterBound = "true";
        }
      }
    });
    const toolbar = global.PortalToolbar?.mount(actions);
    toolbar?.refresh();
    return { actions, toolbar };
  }

  function mount(root) {
    if (!(root instanceof Element)) return null;
    if (instances.has(root)) return instances.get(root);
    root.classList.add("portalBoardCommand");
    root.dataset.portalBoardToolbarMounted = "true";

    const search = normalizeSearch(root);
    const actionState = normalizeActions(root);
    const meta = direct(root, "[data-portal-board-meta]");
    if (meta) meta.classList.add("portalBoardMeta");
    Array.from(root.children).forEach(child => {
      if (child.matches("[data-portal-board-slot]")) child.classList.add("portalBoardSlot");
    });

    const controller = Object.freeze({
      element: root,
      search,
      actions: actionState?.actions || null,
      refresh() {
        normalizeSearch(root);
        normalizeActions(root)?.toolbar?.refresh();
      },
    });
    instances.set(root, controller);
    return controller;
  }

  function mountAll(root = document) {
    const targets = [];
    if (root instanceof Element && root.matches("[data-portal-board-toolbar]")) targets.push(root);
    root.querySelectorAll?.("[data-portal-board-toolbar]").forEach(target => targets.push(target));
    return targets.map(mount).filter(Boolean);
  }

  function initialize() {
    global.PortalUIPrimitives.registerEnhancer({
      id: "board-toolbars", selector: "[data-portal-board-toolbar]", enhance: mount,
    });
  }

  global.PortalBoardToolbar = Object.freeze({ icon, mount, mountAll });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})(window);
