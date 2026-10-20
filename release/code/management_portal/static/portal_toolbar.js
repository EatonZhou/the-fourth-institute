(function (global) {
  "use strict";

  if (global.PortalToolbar) return;

  const instances = new WeakMap();
  const PRIORITY = Object.freeze({
    low: 25,
    normal: 50,
    high: 75,
    critical: 100,
  });

  function priorityOf(item) {
    const raw = String(item.dataset.portalToolbarPriority || "normal").trim().toLowerCase();
    const number = Number(raw);
    return Number.isFinite(number) ? number : (PRIORITY[raw] || PRIORITY.normal);
  }

  function mount(root, options = {}) {
    if (!(root instanceof Element)) return null;
    if (instances.has(root)) return instances.get(root);
    root.classList.add("portalToolbar");
    const overflowMode = String(
      options.overflowMode || root.dataset.portalToolbarOverflow || "menu",
    ).trim().toLowerCase();
    const records = [];
    let layoutFrame = null;
    let destroyed = false;
    let attributeObserver = null;
    let childObserver = null;

    const moreButton = document.createElement("button");
    moreButton.type = "button";
    moreButton.className = "portalButton is-icon portalToolbarOverflowButton";
    moreButton.dataset.portalToolbarMore = "1";
    moreButton.dataset.portalIcon = "more-horizontal";
    moreButton.setAttribute("aria-label", options.moreLabel || window.PortalI18n.t("更多操作"));
    moreButton.setAttribute("title", options.moreLabel || window.PortalI18n.t("更多操作"));
    global.PortalUIPrimitives?.mountIcon?.(moreButton);
    moreButton.hidden = true;
    root.appendChild(moreButton);

    const panel = document.createElement("div");
    panel.className = "portalToolbarOverflowPanel";
    panel.setAttribute("role", "menu");
    panel.setAttribute("aria-label", options.moreLabel || window.PortalI18n.t("更多操作"));
    panel.hidden = true;
    (root.ownerDocument.body || root).appendChild(panel);
    const popover = global.PortalOverlay?.bindPopover({
      trigger: moreButton,
      panel,
      placement: "bottom-end",
      closeOnEscape: true,
      closeOnOutside: true,
    });
    moreButton.addEventListener("click", event => {
      event.stopPropagation();
      popover?.toggle(event);
    });
    panel.addEventListener("click", event => {
      if (
        event.target instanceof Element
        && event.target.closest("input, textarea, select, option, [contenteditable='true']")
      ) return;
      const action = event.target instanceof Element
        ? event.target.closest("[data-portal-toolbar-item]")
        : null;
      if (!action || action.matches(":disabled, [aria-disabled='true']")) return;
      queueMicrotask(() => popover?.close(event));
    });

    function discoverItems() {
      attributeObserver?.disconnect();
      for (let index = records.length - 1; index >= 0; index -= 1) {
        const record = records[index];
        if (record.anchor.parentNode === root && [root, panel].includes(record.item.parentNode)) continue;
        record.anchor.remove();
        records.splice(index, 1);
      }
      const known = new Set(records.map(record => record.item));
      [...root.children].forEach(item => {
        if (item === moreButton || !item.matches("[data-portal-toolbar-item]") || known.has(item)) return;
        const anchor = document.createComment("portal-toolbar-item");
        root.insertBefore(anchor, item);
        item.classList.add("portalToolbarItem");
        records.push({ anchor, index: records.length, item });
      });
      records.forEach(record => {
        attributeObserver?.observe(record.item, {
          attributes: true,
          attributeFilter: ["hidden", "style", "aria-busy", "disabled"],
          subtree: true,
        });
      });
    }

    function setOverflowing(value) {
      const next = value ? "true" : "false";
      if (root.dataset.overflowing !== next) root.dataset.overflowing = next;
    }

    function restoreItems({ closePopover = true, resetOverflow = true } = {}) {
      records.forEach(record => {
        // Moving an item that is already in place would blur the control focused inside it.
        if (record.anchor.parentNode && record.anchor.nextSibling !== record.item) {
          record.anchor.parentNode.insertBefore(record.item, record.anchor.nextSibling);
        }
      });
      panel.replaceChildren();
      moreButton.hidden = true;
      if (resetOverflow) setOverflowing(false);
      if (closePopover) popover?.close();
    }

    function hasOverflow() {
      const bounds = root.getBoundingClientRect();
      return [...root.children].some(item => {
        if (item === panel || item.hidden || global.getComputedStyle(item).display === "none") return false;
        const itemBounds = item.getBoundingClientRect();
        return itemBounds.left < bounds.left - 1 || itemBounds.right > bounds.right + 1;
      });
    }

    function layout() {
      if (destroyed) return;
      if (layoutFrame !== null) cancelAnimationFrame(layoutFrame);
      layoutFrame = null;
      childObserver?.disconnect();
      try {
        const keepPopoverOpen = popover?.isOpen?.() || false;
        discoverItems();
        // Keep the previous overflow mode while measuring. Toggling it off here
        // changes the toolbar height when a scrollbar is required, which wakes
        // the ResizeObserver and can create a self-sustaining layout loop.
        restoreItems({ closePopover: false, resetOverflow: false });
        if (!hasOverflow()) {
          setOverflowing(false);
          popover?.close();
          return;
        }
        if (["scroll", "wrap"].includes(overflowMode)) {
          moreButton.hidden = true;
          setOverflowing(true);
          popover?.close();
          return;
        }
        moreButton.hidden = false;
        const candidates = records
          .filter(record => !record.item.hidden && priorityOf(record.item) < PRIORITY.critical)
          .sort((left, right) => priorityOf(left.item) - priorityOf(right.item) || right.index - left.index);
        const overflowed = [];
        for (const record of candidates) {
          overflowed.push(record);
          record.item.remove();
          if (!hasOverflow()) break;
        }
        overflowed.sort((left, right) => left.index - right.index).forEach(record => panel.appendChild(record.item));
        moreButton.hidden = overflowed.length === 0;
        setOverflowing(hasOverflow());
        if (moreButton.hidden) popover?.close();
        else if (keepPopoverOpen) popover?.open();
      } finally {
        if (!destroyed) {
          childObserver?.observe(root, { childList: true });
          childObserver?.observe(panel, { childList: true });
        }
      }
    }

    function scheduleLayout() {
      if (destroyed || layoutFrame !== null) return;
      layoutFrame = requestAnimationFrame(layout);
    }

    discoverItems();
    const resizeObserver = new ResizeObserver(scheduleLayout);
    resizeObserver.observe(root);
    if (root.parentElement) resizeObserver.observe(root.parentElement);
    attributeObserver = new MutationObserver(scheduleLayout);
    records.forEach(record => attributeObserver.observe(record.item, {
      attributes: true,
      attributeFilter: ["hidden", "style", "aria-busy", "disabled"],
      subtree: true,
    }));
    childObserver = new MutationObserver(scheduleLayout);
    childObserver.observe(root, { childList: true });
    childObserver.observe(panel, { childList: true });

    const controller = {
      element: root,
      layout,
      refresh() {
        scheduleLayout();
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        if (layoutFrame !== null) cancelAnimationFrame(layoutFrame);
        layoutFrame = null;
        discoverItems();
        resizeObserver.disconnect();
        attributeObserver.disconnect();
        childObserver.disconnect();
        popover?.destroy();
        restoreItems();
        records.forEach(record => record.anchor.remove());
        moreButton.remove();
        panel.remove();
        instances.delete(root);
      },
    };
    instances.set(root, controller);
    scheduleLayout();
    return controller;
  }

  function mountAll(root = document) {
    const toolbars = [];
    if (root instanceof Element && root.matches("[data-portal-toolbar]")) toolbars.push(root);
    root.querySelectorAll?.("[data-portal-toolbar]").forEach(toolbar => toolbars.push(toolbar));
    return toolbars.map(toolbar => mount(toolbar)).filter(Boolean);
  }

  function initialize() {
    global.PortalUIPrimitives.registerEnhancer({
      id: "toolbars", selector: "[data-portal-toolbar], .portalToolbar",
      enhance(node) {
        // The class also identifies manually mounted instances for removal.
        // Only the declarative attribute requests automatic mounting.
        if (node.matches("[data-portal-toolbar]")) mount(node);
      },
      remove: node => instances.get(node)?.destroy(),
    });
  }

  global.PortalToolbar = Object.freeze({ mount, mountAll });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})(window);
