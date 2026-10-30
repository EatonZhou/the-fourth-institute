(function (global) {
  "use strict";

  if (global.PortalWorkbenchActivityRailItems) return;

  // The items of an activity rail (recent sessions): their buttons with
  // marks, accents and running, queued and unread badges, redrawing only
  // the items that changed, selecting and dismissing them, and keeping the
  // focus near an item dismissed from the keyboard.

  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "options", "railState", "root",
  ]);

  function createActivityRailItems(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchActivityRailItems is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, options, railState, root,
    } = dependencies;

    let pendingItemFocus = null;

    const itemSignature = (item, index) => JSON.stringify([
      String(item.key || ""),
      String(item.kind || "main"),
      String(item.mark || ""),
      String(item.label || ""),
      String(item.glyph || "?"),
      String(item.title || item.label || ""),
      item.active === true,
      item.running === true,
      item.queued === true,
      item.unread === true,
      String(item.ariaLabel || ""),
      String(item.ariaDescription || ""),
      Number(item.accentHue),
      item.dismissible !== false,
      index,
    ]);
    const itemHtml = (item, index) => {
      const kind = String(item.kind || "main");
      const rawHue = Number(item.accentHue);
      const accentHue = Number.isFinite(rawHue) ? ((Math.round(rawHue) % 360) + 360) % 360 : null;
      return window.PortalI18n.html`<div class="portalActivityRailItemSlot activityRecentSlot">
        <button class="portalActivityRailItem activityRecentItem ${escapeHtml(kind)} ${item.mark ? "marked" : ""} ${accentHue !== null ? "hasStableAccent" : ""} ${item.active ? "active" : ""} ${item.running ? "running" : ""} ${item.queued ? "queued" : ""}" type="button" data-activity-item="${escapeHtml(item.key)}" data-recent-session="${escapeHtml(item.key)}" data-recent-kind-mark="${escapeHtml(item.mark || "")}" title="${escapeHtml(item.title || item.label || "")}" aria-label="${escapeHtml(item.ariaLabel || item.label || item.key)}"${item.ariaDescription ? ` aria-description="${escapeHtml(item.ariaDescription)}"` : ""} style="--recent-delay:${(index * 0.12).toFixed(2)}s${accentHue === null ? "" : `;--recent-accent-hue:${accentHue}`}" >
          ${escapeHtml(item.glyph || "?")}${item.unread ? window.PortalI18n.t('<span class="portalActivityRailUnread activityRecentUnreadDot" title="有未查看的新回复" aria-hidden="true"></span>') : ""}${item.running ? window.PortalI18n.t('<span class="portalActivityRailRunning" title="正在运行" aria-hidden="true"></span>') : ""}${item.queued ? window.PortalI18n.t('<span class="portalActivityRailQueued" title="排队中" aria-hidden="true"></span>') : ""}
        </button>
        ${item.dismissible === false ? "" : window.PortalI18n.html`<button class="portalActivityRailDismiss activityRecentClose" type="button" data-activity-dismiss="${escapeHtml(item.key)}" aria-label="从快捷栏移除 ${escapeHtml(item.label || item.key)}" title="从快捷栏移除" data-portal-icon="close"></button>`}
      </div>`;
    };
    const bindItem = slot => {
      const itemButton = slot.querySelector("[data-activity-item]");
      itemButton?.addEventListener("click", event => {
        options.onItemSelect?.(event.currentTarget.dataset.activityItem || "", railState.controller);
      });
      itemButton?.addEventListener("keydown", event => {
        if (!['Delete', 'Backspace'].includes(event.key) || !slot.querySelector("[data-activity-dismiss]")) return;
        event.preventDefault();
        event.stopPropagation();
        const siblings = [...slot.parentElement.querySelectorAll("[data-activity-item]")];
        const index = siblings.indexOf(event.currentTarget);
        const next = siblings[index + 1] || siblings[index - 1] || null;
        pendingItemFocus = {
          key: String(next?.dataset.activityItem || ""),
          fallbackView: String(options.itemFallbackView || ""),
        };
        options.onItemDismiss?.(event.currentTarget.dataset.activityItem || "", railState.controller, { source: "keyboard" });
      });
      slot.querySelector("[data-activity-dismiss]")?.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        options.onItemDismiss?.(event.currentTarget.dataset.activityDismiss || "", railState.controller);
      });
    };
    const renderItems = () => {
      const itemsRoot = root.querySelector("[data-activity-rail-items]");
      if (!itemsRoot) return railState.controller;
      const actionButton = root.querySelector("[data-activity-items-action]");
      itemsRoot.hidden = !railState.items.length;
      if (actionButton) actionButton.hidden = !railState.items.length;
      const existing = new Map(
        [...itemsRoot.children].map(slot => [String(slot.dataset.activityItemKey || ""), slot]),
      );
      const retained = new Set();
      railState.items.slice(0, railState.controller.capacity()).forEach((item, index) => {
        const key = String(item.key || "");
        const signature = itemSignature(item, index);
        let slot = existing.get(key);
        if (!slot || slot.dataset.activityItemSignature !== signature) {
          const template = document.createElement("template");
          template.innerHTML = itemHtml(item, index).trim();
          const replacement = template.content.firstElementChild;
          replacement.dataset.activityItemKey = key;
          replacement.dataset.activityItemSignature = signature;
          bindItem(replacement);
          if (slot) slot.replaceWith(replacement);
          slot = replacement;
        }
        retained.add(slot);
        itemsRoot.append(slot);
      });
      [...itemsRoot.children].forEach(slot => {
        if (!retained.has(slot)) slot.remove();
      });
      if (pendingItemFocus) {
        const request = pendingItemFocus;
        pendingItemFocus = null;
        window.requestAnimationFrame(() => {
          const item = request.key
            ? itemsRoot.querySelector(`[data-activity-item="${CSS.escape(request.key)}"]`)
            : null;
          const fallback = request.fallbackView
            ? root.querySelector(`[data-activity-view="${CSS.escape(request.fallbackView)}"]`)
            : root.querySelector("[data-activity-items-action]:not([hidden])");
          (item || fallback)?.focus?.();
        });
      }
      return railState.controller;
    };

    return {
      renderItems,
    };
  }

  global.PortalWorkbenchActivityRailItems = Object.freeze({ createActivityRailItems, dependencies: DEPENDENCIES });
})(window);
