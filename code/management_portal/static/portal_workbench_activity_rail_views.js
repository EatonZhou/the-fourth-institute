(function (global) {
  "use strict";

  if (global.PortalWorkbenchActivityRailViews) return;

  // The views of an activity rail: their buttons in the user's order,
  // selecting a view, reordering views by dragging and keeping the order in
  // the rail's store.

  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "itemsAction", "options", "railState", "renderItems", "root", "storage",
  ]);

  function createActivityRailViews(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchActivityRailViews is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, itemsAction, options, railState, renderItems, root, storage,
    } = dependencies;

    const normalizeOrder = value => {
      const byId = new Map(railState.views.map(view => [String(view.id), view]));
      const seen = new Set();
      return [...(Array.isArray(value) ? value : []), ...railState.views.map(view => String(view.id))]
        .filter(id => byId.has(id) && !seen.has(id) && seen.add(id))
        .map(String);
    };
    const orderedViews = () => {
      const byId = new Map(railState.views.map(view => [String(view.id), view]));
      return normalizeOrder(railState.order).map(viewId => byId.get(viewId));
    };
    const persistOrder = () => {
      railState.order = normalizeOrder(railState.order);
      storage?.replace?.({ order: railState.order.slice() });
      options.onViewOrderChange?.(railState.order.slice(), railState.controller);
    };

    const render = () => {
      root.innerHTML = window.PortalI18n.html`${orderedViews().map(view => {
        const id = String(view.id || "");
        return window.PortalI18n.html`<button class="portalActivityRailView activityBtn ${id === railState.activeView ? "active" : ""}" data-portal-icon="${escapeHtml(view.icon || "window")}" type="button" data-activity-view="${escapeHtml(id)}" data-side-view="${escapeHtml(id)}" aria-label="${escapeHtml(view.label || id)}" title="${escapeHtml(view.title || view.label || id)}" aria-pressed="${id === railState.activeView ? "true" : "false"}" draggable="true"></button>`;
      }).join("")}<div class="portalActivityRailItems activityRecentRail" ${options.itemsId ? `id="${escapeHtml(options.itemsId)}"` : ""} data-activity-rail-items aria-label="${escapeHtml(options.itemsLabel || window.PortalI18n.t("Recent sessions"))}" hidden></div>${itemsAction ? window.PortalI18n.html`<button class="portalActivityRailItemsAction" type="button" data-activity-items-action data-portal-icon="${escapeHtml(itemsAction.icon || "trash")}" aria-label="${escapeHtml(itemsAction.label || window.PortalI18n.t("Clear recent items"))}" title="${escapeHtml(itemsAction.title || itemsAction.label || window.PortalI18n.t("Clear recent items"))}" hidden></button>` : ""}`;
      root.querySelectorAll("[data-activity-view]").forEach(button => {
        button.addEventListener("click", () => {
          railState.activeView = String(button.dataset.activityView || "");
          railState.controller.render();
          options.onViewSelect?.(railState.activeView, railState.controller);
        });
        button.addEventListener("dragstart", event => {
          event.dataTransfer?.setData("application/x-portal-activity-view", button.dataset.activityView || "");
          button.classList.add("activityDragging");
        });
        button.addEventListener("dragover", event => event.preventDefault());
        button.addEventListener("drop", event => {
          event.preventDefault();
          const source = event.dataTransfer?.getData("application/x-portal-activity-view") || "";
          const target = button.dataset.activityView || "";
          if (!source || source === target) return;
          const next = orderedViews().map(view => String(view.id)).filter(id => id !== source);
          next.splice(Math.max(0, next.indexOf(target)), 0, source);
          railState.order = next;
          persistOrder();
          railState.controller.render();
        });
        button.addEventListener("dragend", () => button.classList.remove("activityDragging"));
      });
      root.querySelector("[data-activity-items-action]")?.addEventListener("click", () => {
        options.onItemsAction?.(railState.controller);
      });
      renderItems();
      return railState.controller;
    };

    return {
      normalizeOrder, persistOrder, render,
    };
  }

  global.PortalWorkbenchActivityRailViews = Object.freeze({ createActivityRailViews, dependencies: DEPENDENCIES });
})(window);
