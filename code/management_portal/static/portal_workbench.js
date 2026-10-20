(function (global) {
  "use strict";

  if (global.PortalWorkbench) return;

  const SCHEMA_VERSION = 2;
  const SPLIT_HANDLE_SIZE = 7;
  const COLLAPSED_PANEL_SIZE = Object.freeze({ x: 44, y: 34 });
  const EXPANDED_PANEL_VISIBILITY_FLOOR = Object.freeze({ x: 96, y: 72 });
  const instances = new Map();
  const layoutLockSynchronizers = new Set();
  const panelPackages = new Map();
  let generatedId = 0;

  function elementOf(value, root = document) {
    if (typeof value === "function") return value();
    if (typeof value === "string") return root.querySelector(value);
    return value instanceof Element ? value : null;
  }

  function uid(prefix) {
    generatedId += 1;
    return `${prefix}-${generatedId.toString(36)}`;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  function clamp(value, minimum, maximum) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return Number(minimum);
    return Math.max(Number(minimum), Math.min(Number(maximum), numeric));
  }

  function numericOption(value, event) {
    const resolved = typeof value === "function" ? value(event) : value;
    const numeric = Number(resolved);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function syncSeparatorAria(handle, options = {}, event) {
    const minimum = numericOption(options.min, event);
    const maximum = numericOption(options.max, event);
    const value = numericOption(options.readValue, event);
    if (minimum === null) handle.removeAttribute("aria-valuemin");
    else handle.setAttribute("aria-valuemin", String(Math.round(minimum)));
    if (maximum === null) handle.removeAttribute("aria-valuemax");
    else handle.setAttribute("aria-valuemax", String(Math.round(maximum)));
    if (value === null) handle.removeAttribute("aria-valuenow");
    else handle.setAttribute("aria-valuenow", String(Math.round(value)));
    const text = typeof options.valueText === "function"
      ? options.valueText(value, { event, minimum, maximum })
      : "";
    if (text) handle.setAttribute("aria-valuetext", String(text));
    else handle.removeAttribute("aria-valuetext");
  }

  function bindPointerResize(options = {}) {
    const handle = elementOf(options.handle);
    if (!handle || typeof options.onMove !== "function") return () => {};
    const boundKey = String(options.boundKey || "portalWorkbenchPointerBound");
    if (handle.dataset[boundKey] === "1") return () => {};
    handle.dataset[boundKey] = "1";
    const axis = options.axis === "y" ? "y" : "x";
    const activeClass = options.activeClass || "active";
    const bodyClass = options.bodyClass === undefined ? "resizingWorkbenchPane" : options.bodyClass;
    const pointerDown = event => {
      if (options.disabled?.(event)) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      const startPosition = axis === "y" ? event.clientY : event.clientX;
      const start = options.readStart?.(event);
      handle.classList.add(activeClass);
      if (bodyClass) document.body.classList.add(bodyClass);
      handle.setPointerCapture?.(event.pointerId);
      const move = moveEvent => {
        const position = axis === "y" ? moveEvent.clientY : moveEvent.clientX;
        options.onMove({ delta: position - startPosition, event: moveEvent, start });
      };
      const done = doneEvent => {
        handle.classList.remove(activeClass);
        if (bodyClass) document.body.classList.remove(bodyClass);
        if (handle.hasPointerCapture?.(doneEvent.pointerId)) handle.releasePointerCapture(doneEvent.pointerId);
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", done);
        handle.removeEventListener("pointercancel", done);
        options.onDone?.({ event: doneEvent, start });
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", done);
      handle.addEventListener("pointercancel", done);
      event.preventDefault();
    };
    handle.addEventListener("pointerdown", pointerDown);
    return () => {
      handle.removeEventListener("pointerdown", pointerDown);
      delete handle.dataset[boundKey];
    };
  }

  function bindKeyboardResize(options = {}) {
    const handle = elementOf(options.handle);
    if (!handle || typeof options.onMove !== "function") return () => {};
    const boundKey = String(options.boundKey || "portalWorkbenchKeyboardBound");
    if (handle.dataset[boundKey] === "1") return () => {};
    handle.dataset[boundKey] = "1";
    const axis = options.axis === "y" ? "y" : "x";
    const orientation = axis === "y" ? "horizontal" : "vertical";
    handle.setAttribute("role", "separator");
    handle.setAttribute("tabindex", "0");
    handle.setAttribute("aria-orientation", orientation);
    const keydown = event => {
      if (options.disabled?.(event)) return;
      const decrease = axis === "y" ? "ArrowUp" : "ArrowLeft";
      const increase = axis === "y" ? "ArrowDown" : "ArrowRight";
      const step = Math.max(1, Number(options.keyboardStep || 24)) * (event.shiftKey ? 4 : 1);
      let delta = event.key === decrease ? -step : event.key === increase ? step : null;
      const value = numericOption(options.readValue, event);
      if (delta === null && ["Home", "End"].includes(event.key)) {
        const target = numericOption(event.key === "Home" ? options.min : options.max, event);
        if (target === null || value === null) return;
        delta = (target - value) / (Number(options.direction || 1) || 1);
      }
      if (delta === null) return;
      event.preventDefault();
      const start = options.readStart?.(event);
      options.onMove({ delta, event, start, keyboard: true });
      options.onDone?.({ event, start, keyboard: true });
      syncSeparatorAria(handle, options, event);
    };
    handle.addEventListener("keydown", keydown);
    syncSeparatorAria(handle, options);
    return () => {
      handle.removeEventListener("keydown", keydown);
      delete handle.dataset[boundKey];
    };
  }

  function createStore(options = {}) {
    const storageKey = String(options.storageKey || "").trim();
    const read = typeof options.read === "function"
      ? options.read
      : () => window.PortalState.storage.readJson(storageKey, {});
    const write = typeof options.write === "function"
      ? options.write
      : value => {
          if (!storageKey) return;
          window.PortalState.storage.writeJson(storageKey, value || {});
        };
    return Object.freeze({
      read() {
        const value = read();
        return value && typeof value === "object" && !Array.isArray(value) ? clone(value) : {};
      },
      write(patch = {}) {
        const next = { ...this.read(), ...(patch || {}) };
        write(next);
        return clone(next);
      },
      replace(value = {}) {
        const next = value && typeof value === "object" && !Array.isArray(value) ? clone(value) : {};
        write(next);
        return clone(next);
      },
    });
  }

  function storagePart(value, fallback) {
    const text = String(value || "").trim() || fallback;
    return encodeURIComponent(text).replace(/%/g, "~");
  }

  function userStorageKey(kind, id, user) {
    return `portal.${storagePart(kind, "state")}.${storagePart(id, "workspace")}.user.${storagePart(user, "anonymous")}.v1`;
  }

  function createUserStore(options = {}) {
    const kind = String(options.kind || "").trim();
    const id = String(options.id || "").trim();
    const user = String(options.user || "").trim();
    if (!kind || !id || !user) {
      throw new TypeError("PortalWorkbench.storage.createUserStore requires kind, id, and user.");
    }
    const storageKey = userStorageKey(kind, id, user);
    return createStore({ storageKey });
  }

  function createMemoryStore(initialValue = {}) {
    let value = initialValue && typeof initialValue === "object" && !Array.isArray(initialValue)
      ? clone(initialValue)
      : {};
    return Object.freeze({
      read: () => clone(value),
      write(patch = {}) {
        value = { ...value, ...(patch || {}) };
        return clone(value);
      },
      replace(next = {}) {
        value = next && typeof next === "object" && !Array.isArray(next) ? clone(next) : {};
        return clone(value);
      },
    });
  }

  function defaultWorkbenchStore(id, user) {
    if (!user) return createMemoryStore();
    return createUserStore({ kind: "workbench", id, user });
  }

  const storage = Object.freeze({
    createMemoryStore,
    createUserStore,
    userStorageKey,
  });

  function syncCollapseButton(buttonRef, collapsed, labels = {}) {
    const button = elementOf(buttonRef);
    if (!button) return;
    const label = collapsed ? (labels.expand || window.PortalI18n.t("展开面板")) : (labels.collapse || window.PortalI18n.t("折叠面板"));
    button.setAttribute("aria-pressed", collapsed ? "true" : "false");
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  function setCollapsed(options = {}) {
    const root = elementOf(options.root);
    if (!root || !options.className) return false;
    const collapsed = Boolean(options.collapsed);
    root.classList.toggle(options.className, collapsed);
    (Array.isArray(options.buttons) ? options.buttons : [options.button]).forEach(button => {
      syncCollapseButton(button, collapsed, options.labels);
    });
    options.onChange?.(collapsed);
    if (options.persist !== false && options.store && options.stateKey) {
      options.store.write({ [options.stateKey]: collapsed });
    }
    return collapsed;
  }

  function bindSplitter(options = {}) {
    const root = elementOf(options.root) || document.documentElement;
    const handle = elementOf(options.handle, root);
    if (!handle) return () => {};
    if (typeof options.readSize !== "function" || typeof options.applySize !== "function") {
      throw new TypeError("PortalWorkbench.layout.bindSplitter requires readSize and applySize.");
    }
    const direction = Number(options.direction) < 0 ? -1 : 1;
    const limits = event => {
      const minimum = numericOption(options.min, event);
      const maximum = numericOption(options.max, event);
      return { minimum, maximum: minimum !== null && maximum !== null && maximum < minimum ? minimum : maximum };
    };
    const readStart = event => ({
      size: Number(options.readSize(event) || 0),
      context: options.readContext?.(event),
    });
    const move = ({ delta, event, start }) => {
      const range = limits(event);
      const raw = start.size + delta * direction;
      const size = clamp(raw, range.minimum ?? raw, range.maximum ?? raw);
      options.applySize(size, { event, start });
      syncSeparatorAria(handle, {
        min: range.minimum,
        max: range.maximum,
        readValue: options.readSize,
        valueText: options.valueText,
      }, event);
    };
    const done = ({ event, start }) => {
      const size = Number(options.readSize(event) || start?.size || 0);
      if (options.store && options.stateKey) options.store.write({ [options.stateKey]: size });
      options.onDone?.(size, { event, start });
    };
    const common = {
      handle,
      axis: options.axis,
      bodyClass: options.bodyClass,
      disabled: options.disabled,
      readStart,
      onMove: move,
      onDone: done,
    };
    const unbindPointer = bindPointerResize({ ...common, boundKey: options.boundKey || "portalWorkbenchBound" });
    const unbindKeyboard = bindKeyboardResize({
      ...common,
      boundKey: `${options.boundKey || "portalWorkbenchBound"}Keyboard`,
      direction,
      keyboardStep: options.keyboardStep,
      min: options.min,
      max: options.max,
      readValue: options.readSize,
      valueText: options.valueText,
    });
    return () => { unbindPointer(); unbindKeyboard(); };
  }

  const layout = Object.freeze({
    bindKeyboardResize,
    bindPointerResize,
    bindSplitter,
    createStore,
    setCollapsed,
    syncCollapseButton,
  });

  function createActivityRail(options = {}) {
    const root = elementOf(options.root);
    if (!root) throw new TypeError("PortalWorkbench.createActivityRail requires root.");
    const id = String(options.id || "").trim();
    const storage = options.store || (id
      ? createStore({ storageKey: `portal.activity-rail.${id}.v1` })
      : null);
    const stored = storage?.read?.() || {};
    let views = Array.isArray(options.views) ? options.views.slice() : [];
    let items = [];
    let pendingItemFocus = null;
    const itemsAction = options.itemsAction && typeof options.itemsAction === "object"
      ? options.itemsAction
      : null;
    let order = Array.isArray(options.order)
      ? options.order.map(String)
      : (Array.isArray(stored.order) ? stored.order.map(String) : []);
    let activeView = String(options.activeView || "");
    root.classList.add("portalActivityRail", "activityBar");
    root.setAttribute("aria-label", options.label || window.PortalI18n.t("Workspace panels"));

    const normalizeOrder = value => {
      const byId = new Map(views.map(view => [String(view.id), view]));
      const seen = new Set();
      return [...(Array.isArray(value) ? value : []), ...views.map(view => String(view.id))]
        .filter(id => byId.has(id) && !seen.has(id) && seen.add(id))
        .map(String);
    };
    const orderedViews = () => {
      const byId = new Map(views.map(view => [String(view.id), view]));
      return normalizeOrder(order).map(viewId => byId.get(viewId));
    };
    const persistOrder = () => {
      order = normalizeOrder(order);
      storage?.replace?.({ order: order.slice() });
      options.onViewOrderChange?.(order.slice(), controller);
    };
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
        options.onItemSelect?.(event.currentTarget.dataset.activityItem || "", controller);
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
        options.onItemDismiss?.(event.currentTarget.dataset.activityItem || "", controller, { source: "keyboard" });
      });
      slot.querySelector("[data-activity-dismiss]")?.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        options.onItemDismiss?.(event.currentTarget.dataset.activityDismiss || "", controller);
      });
    };
    const renderItems = () => {
      const itemsRoot = root.querySelector("[data-activity-rail-items]");
      if (!itemsRoot) return controller;
      const actionButton = root.querySelector("[data-activity-items-action]");
      itemsRoot.hidden = !items.length;
      if (actionButton) actionButton.hidden = !items.length;
      const existing = new Map(
        [...itemsRoot.children].map(slot => [String(slot.dataset.activityItemKey || ""), slot]),
      );
      const retained = new Set();
      items.slice(0, controller.capacity()).forEach((item, index) => {
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
      return controller;
    };
    const render = () => {
      root.innerHTML = window.PortalI18n.html`${orderedViews().map(view => {
        const id = String(view.id || "");
        return window.PortalI18n.html`<button class="portalActivityRailView activityBtn ${id === activeView ? "active" : ""}" data-portal-icon="${escapeHtml(view.icon || "window")}" type="button" data-activity-view="${escapeHtml(id)}" data-side-view="${escapeHtml(id)}" aria-label="${escapeHtml(view.label || id)}" title="${escapeHtml(view.title || view.label || id)}" aria-pressed="${id === activeView ? "true" : "false"}" draggable="true"></button>`;
      }).join("")}<div class="portalActivityRailItems activityRecentRail" ${options.itemsId ? `id="${escapeHtml(options.itemsId)}"` : ""} data-activity-rail-items aria-label="${escapeHtml(options.itemsLabel || window.PortalI18n.t("Recent sessions"))}" hidden></div>${itemsAction ? window.PortalI18n.html`<button class="portalActivityRailItemsAction" type="button" data-activity-items-action data-portal-icon="${escapeHtml(itemsAction.icon || "trash")}" aria-label="${escapeHtml(itemsAction.label || window.PortalI18n.t("Clear recent items"))}" title="${escapeHtml(itemsAction.title || itemsAction.label || window.PortalI18n.t("Clear recent items"))}" hidden></button>` : ""}`;
      root.querySelectorAll("[data-activity-view]").forEach(button => {
        button.addEventListener("click", () => {
          activeView = String(button.dataset.activityView || "");
          controller.render();
          options.onViewSelect?.(activeView, controller);
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
          order = next;
          persistOrder();
          controller.render();
        });
        button.addEventListener("dragend", () => button.classList.remove("activityDragging"));
      });
      root.querySelector("[data-activity-items-action]")?.addEventListener("click", () => {
        options.onItemsAction?.(controller);
      });
      renderItems();
      return controller;
    };
    const controller = Object.freeze({
      root,
      capacity() {
        const itemsRoot = root.querySelector("[data-activity-rail-items]");
        const height = Number(itemsRoot?.clientHeight || root.clientHeight || 0);
        const limit = Math.max(1, Math.floor(Number(options.itemLimit || 24)));
        return height ? Math.min(limit, Math.max(1, Math.floor((height + 7) / 39))) : limit;
      },
      render,
      setActiveView(value) { activeView = String(value || ""); return render(); },
      setItems(value) { items = Array.isArray(value) ? value.slice() : []; return renderItems(); },
      getOrder: () => normalizeOrder(order),
      setOrder(value, meta = {}) {
        order = normalizeOrder(Array.isArray(value) ? value.map(String) : []);
        if (meta.persist === true) persistOrder();
        return render();
      },
      setViews(value) {
        views = Array.isArray(value) ? value.slice() : [];
        order = normalizeOrder(order);
        return render();
      },
      destroy() { root.replaceChildren(); root.classList.remove("portalActivityRail", "activityBar"); },
    });
    order = normalizeOrder(order);
    return render();
  }

  function panelNode(panelId) {
    return { type: "panel", id: uid("panel"), panelId: String(panelId) };
  }

  function stackNode(panelIds, activePanelId) {
    const panels = [...new Set((panelIds || []).map(String).filter(Boolean))];
    return {
      type: "stack",
      id: uid("stack"),
      activePanelId: panels.includes(String(activePanelId)) ? String(activePanelId) : panels[0] || "",
      children: panels.map(panelNode),
    };
  }

  function splitNode(orientation, children, weights) {
    const normalizedChildren = (children || []).filter(Boolean);
    const normalizedWeights = normalizedChildren.map((_, index) => {
      const value = Number(weights?.[index]);
      return Number.isFinite(value) && value > 0 ? value : 1;
    });
    return {
      type: "split",
      id: uid("split"),
      orientation: orientation === "vertical" ? "vertical" : "horizontal",
      weights: normalizedWeights,
      children: normalizedChildren,
    };
  }

  function collectPanelIds(node, result = []) {
    if (!node || typeof node !== "object") return result;
    if (node.type === "panel") result.push(String(node.panelId || ""));
    (node.children || []).forEach(child => collectPanelIds(child, result));
    return result;
  }

  function normalizeNode(raw, validIds, seen = new Set()) {
    if (!raw || typeof raw !== "object") return null;
    if (raw.type === "panel") {
      const panelId = String(raw.panelId || "");
      if (!validIds.has(panelId) || seen.has(panelId)) return null;
      seen.add(panelId);
      return { type: "panel", id: String(raw.id || uid("panel")), panelId };
    }
    if (raw.type === "stack") {
      const children = (raw.children || []).map(child => normalizeNode(child, validIds, seen)).filter(Boolean);
      if (!children.length) return null;
      return {
        type: "stack",
        id: String(raw.id || uid("stack")),
        activePanelId: children.some(child => child.panelId === raw.activePanelId)
          ? String(raw.activePanelId)
          : children[0].panelId,
        children,
      };
    }
    if (raw.type === "split") {
      const entries = (raw.children || []).map((child, index) => ({
        child: normalizeNode(child, validIds, seen),
        weight: Number(raw.weights?.[index]),
      })).filter(entry => entry.child);
      const children = entries.map(entry => entry.child);
      if (!children.length) return null;
      if (children.length === 1) return children[0];
      return {
        type: "split",
        id: String(raw.id || uid("split")),
        orientation: raw.orientation === "vertical" ? "vertical" : "horizontal",
        weights: entries.map(({ weight }) => {
          return Number.isFinite(weight) && weight > 0 ? weight : 1;
        }),
        children,
      };
    }
    return null;
  }

  function normalizeLayout(raw, visibleIds) {
    const model = normalizeNode(raw, visibleIds);
    const placed = new Set(collectPanelIds(model));
    const missing = [...visibleIds].filter(panelId => !placed.has(panelId));
    if (!model) return missing.length ? stackNode(missing) : null;
    if (!missing.length) return model;
    return splitNode("horizontal", [model, ...missing.map(panelId => stackNode([panelId]))]);
  }

  function compactExtentForNode(node, axis, collapsed = new Set()) {
    if (!node) return null;
    if (node.type === "panel") {
      return collapsed.has(String(node.panelId || "")) ? COLLAPSED_PANEL_SIZE[axis] : null;
    }
    if (node.type === "stack") {
      return collapsed.has(String(node.activePanelId || "")) ? COLLAPSED_PANEL_SIZE[axis] : null;
    }
    if (node.type !== "split" || !node.children?.length) return null;
    const childExtents = node.children.map(child => compactExtentForNode(child, axis, collapsed));
    if (childExtents.some(value => value === null)) return null;
    const sameAxis = (axis === "x" && node.orientation === "horizontal")
      || (axis === "y" && node.orientation === "vertical");
    return sameAxis
      ? childExtents.reduce((sum, value) => sum + value, 0)
      : Math.max(...childExtents);
  }

  function minimumForNode(node, axis, descriptors, collapsed = new Set()) {
    if (!node) return 96;
    const compactExtent = compactExtentForNode(node, axis, collapsed);
    if (compactExtent !== null) return compactExtent;
    if (node.type === "panel") {
      const descriptor = descriptors.get(node.panelId);
      return axis === "y" ? descriptor?.minHeight || 100 : descriptor?.minWidth || 120;
    }
    const values = (node.children || []).map(child => minimumForNode(child, axis, descriptors, collapsed));
    if (node.type === "stack") return Math.max(96, ...values);
    const sameAxis = (axis === "x" && node.orientation === "horizontal")
      || (axis === "y" && node.orientation === "vertical");
    return sameAxis
      ? values.reduce((sum, value) => sum + value, Math.max(0, values.length - 1) * SPLIT_HANDLE_SIZE)
      : Math.max(96, ...values);
  }

  function splitChildMinimumCss(split, child, index, descriptors, collapsed) {
    const axis = split.orientation === "vertical" ? "y" : "x";
    const compactExtents = split.children.map(item => compactExtentForNode(item, axis, collapsed));
    if (compactExtents[index] !== null) return `${compactExtents[index]}px`;
    const expandedMinimum = split.children.reduce((sum, item, childIndex) => (
      sum + (compactExtents[childIndex] === null ? minimumForNode(item, axis, descriptors, collapsed) : 0)
    ), 0);
    const collapsedPixels = compactExtents.reduce((sum, value) => sum + (value ?? 0), 0);
    const visibleHandleCount = split.children.slice(0, -1).filter((_, handleIndex) => (
      compactExtents[handleIndex] === null && compactExtents[handleIndex + 1] === null
    )).length;
    const reservedPixels = collapsedPixels + visibleHandleCount * SPLIT_HANDLE_SIZE;
    const semanticMinimum = minimumForNode(child, axis, descriptors, collapsed);
    const minimumShare = semanticMinimum / expandedMinimum;
    const responsiveShare = `calc(${100 * minimumShare}% - ${reservedPixels * minimumShare}px)`;
    return `min(${semanticMinimum}px, max(${EXPANDED_PANEL_VISIBILITY_FLOOR[axis]}px, ${responsiveShare}))`;
  }

  function findNode(node, nodeId, parent = null, index = -1) {
    if (!node) return null;
    if (node.id === nodeId) return { node, parent, index };
    for (let childIndex = 0; childIndex < (node.children || []).length; childIndex += 1) {
      const found = findNode(node.children[childIndex], nodeId, node, childIndex);
      if (found) return found;
    }
    return null;
  }

  function firstStack(node) {
    if (!node) return null;
    if (node.type === "stack") return node;
    for (const child of node.children || []) {
      const stack = firstStack(child);
      if (stack) return stack;
    }
    return null;
  }

  function findPanel(node, panelId) {
    if (!node) return null;
    if (node.type === "stack" && node.children.some(child => child.panelId === panelId)) {
      return { stack: node };
    }
    for (const child of node.children || []) {
      const found = findPanel(child, panelId);
      if (found) return found;
    }
    return null;
  }

  function panelPlacement(node, panelId, parent = null, index = -1) {
    if (!node) return null;
    if (node.type === "stack" && node.children.some(child => child.panelId === panelId)) {
      if (node.children.length > 1) return { stackId: node.id, zone: "center" };
      if (parent?.type === "split") {
        const siblingIndex = index < parent.children.length - 1 ? index + 1 : index - 1;
        const target = firstStack(parent.children[siblingIndex]);
        if (target) {
          const before = index < siblingIndex;
          const zone = parent.orientation === "vertical"
            ? (before ? "top" : "bottom")
            : (before ? "left" : "right");
          return {
            stackId: target.id,
            zone,
            splitId: parent.id,
            index,
            weight: Number(parent.weights?.[index]) || 1,
            siblingWeight: Number(parent.weights?.[siblingIndex]) || 1,
          };
        }
      }
      return null;
    }
    for (let childIndex = 0; childIndex < (node.children || []).length; childIndex += 1) {
      const placement = panelPlacement(node.children[childIndex], panelId, node, childIndex);
      if (placement) return placement;
    }
    return null;
  }

  function removePanelFromNode(node, panelId) {
    if (!node) return null;
    if (node.type === "panel") return node.panelId === panelId ? null : node;
    const entries = (node.children || []).map((child, index) => ({
      child: removePanelFromNode(child, panelId),
      weight: Number(node.weights?.[index]) || 1,
    })).filter(entry => entry.child);
    const children = entries.map(entry => entry.child);
    if (!children.length) return null;
    if (node.type === "stack") {
      return {
        ...node,
        children,
        activePanelId: children.some(child => child.panelId === node.activePanelId)
          ? node.activePanelId
          : children[0].panelId,
      };
    }
    if (children.length === 1) return children[0];
    return { ...node, children, weights: entries.map(entry => entry.weight) };
  }

  function replaceNode(root, nodeId, replacement) {
    if (!root) return replacement;
    if (root.id === nodeId) return replacement;
    return {
      ...root,
      children: (root.children || []).map(child => replaceNode(child, nodeId, replacement)),
    };
  }

  function runtimeElement(runtime, fallback) {
    if (runtime instanceof Element) return runtime;
    return elementOf(runtime?.root || runtime?.element) || fallback;
  }

  function normalizePanelPackage(raw = {}) {
    const type = String(raw.type || raw.id || "").trim();
    if (!type) throw new TypeError("PortalWorkbench.registerPanelPackage requires type.");
    if (typeof raw.create !== "function") {
      throw new TypeError(`PortalWorkbench Panel Package requires create(): ${type}`);
    }
    const persistence = raw.persistence && typeof raw.persistence === "object"
      ? raw.persistence
      : {};
    const persistenceFields = new Set(["schemaVersion", "serialize", "restore"]);
    const unsupportedPersistenceFields = Object.keys(persistence)
      .filter(field => !persistenceFields.has(field));
    const required = [
      [Number.isInteger(raw.version) && raw.version > 0, "version"],
      [raw.presentation && typeof raw.presentation === "object", "presentation"],
      [String(raw.presentation?.title || "").trim(), "presentation.title"],
      [String(raw.presentation?.icon || "").trim(), "presentation.icon"],
      [raw.sizing && typeof raw.sizing === "object", "sizing"],
      [Number.isFinite(Number(raw.sizing?.minWidth)) && Number(raw.sizing.minWidth) > 0, "sizing.minWidth"],
      [Number.isFinite(Number(raw.sizing?.minHeight)) && Number(raw.sizing.minHeight) > 0, "sizing.minHeight"],
      [["single", "multiple"].includes(raw.instances), "instances"],
      [Array.isArray(raw.capabilities), "capabilities"],
      [raw.behavior && typeof raw.behavior === "object", "behavior"],
      [Array.isArray(raw.context?.channels), "context.channels"],
      [raw.commands === undefined || Array.isArray(raw.commands), "commands"],
      [Number.isInteger(persistence.schemaVersion) && persistence.schemaVersion > 0, "persistence.schemaVersion"],
    ];
    const missing = required.filter(([valid]) => !valid).map(([, field]) => field);
    if (missing.length) {
      throw new TypeError(`PortalWorkbench Panel Package ${type} is incomplete: ${missing.join(", ")}`);
    }
    if (unsupportedPersistenceFields.length) {
      throw new TypeError(
        `PortalWorkbench Panel Package ${type} has unsupported persistence fields: ${unsupportedPersistenceFields.join(", ")}`,
      );
    }
    const version = raw.version;
    const presentation = Object.freeze({
      title: String(raw.presentation.title).trim(),
      icon: String(raw.presentation.icon).trim(),
    });
    const sizing = Object.freeze({
      minWidth: Math.max(120, Number(raw.sizing.minWidth)),
      minHeight: Math.max(100, Number(raw.sizing.minHeight)),
    });
    const instances = raw.instances;
    const capabilities = [...new Set(raw.capabilities.map(String).filter(Boolean))];
    const channels = [...new Set(raw.context.channels.map(String).filter(Boolean))];
    const commands = (raw.commands || []).map(command => {
      const commandId = String(command?.id || "").trim();
      if (!commandId || typeof command?.execute !== "function") {
        throw new TypeError(`PortalWorkbench Panel Package command requires id and execute(): ${type}`);
      }
      const capability = String(command.capability || "").trim();
      if (capability && !capabilities.includes(capability)) {
        throw new TypeError(`PortalWorkbench Panel Package command ${type}.${commandId} requires undeclared capability: ${capability}`);
      }
      return Object.freeze({ ...command, id: commandId, capability });
    });
    return Object.freeze({
      type,
      version,
      component: String(raw.component || ""),
      presentation,
      sizing,
      instances,
      capabilities: Object.freeze(capabilities),
      behavior: Object.freeze({
        canHide: raw.behavior.canHide !== false,
        canCollapse: raw.behavior.canCollapse !== false,
        canMove: raw.behavior.canMove !== false,
        closable: raw.behavior.closable === true,
        locked: raw.behavior.locked === true,
      }),
      context: Object.freeze({ channels: Object.freeze(channels) }),
      commands: Object.freeze(commands),
      persistence: Object.freeze({
        schemaVersion: persistence.schemaVersion,
        serialize: typeof persistence.serialize === "function" ? persistence.serialize : null,
        restore: typeof persistence.restore === "function" ? persistence.restore : null,
      }),
      create: raw.create,
    });
  }

  function createPanelContext(runtimeContext, channels) {
    const allowed = new Set(channels);
    const permits = channel => allowed.has("*") || allowed.has(String(channel));
    const values = input => Object.freeze(Object.fromEntries(
      Object.entries(input || {}).filter(([channel]) => permits(channel)),
    ));
    const project = snapshot => Object.freeze({
      ...snapshot,
      values: values(snapshot?.values),
      ...(snapshot?.changedChannels ? { changedChannels: Object.freeze(snapshot.changedChannels.filter(permits)) } : {}),
    });
    const subscriptions = new Set();
    let disposed = false;
    const dispose = () => {
      if (disposed) return;
      disposed = true;
      subscriptions.forEach(unsubscribe => unsubscribe());
      subscriptions.clear();
    };
    const subscribe = (requested, listener, options = {}) => {
      if (disposed || typeof listener !== "function") return () => {};
      const requestedChannels = requested === "*" ? channels : (Array.isArray(requested) ? requested : [requested]);
      const selected = requestedChannels.includes("*") && allowed.has("*") ? "*" : requestedChannels.filter(permits);
      if (!selected.length) return () => {};
      const unsubscribe = runtimeContext.subscribe(selected, change => listener(project(change)), options);
      subscriptions.add(unsubscribe);
      return () => { if (subscriptions.delete(unsubscribe)) unsubscribe(); };
    };
    const contextStore = Object.freeze({
      ...runtimeContext.contextStore,
      snapshot: () => project(runtimeContext.contextStore.snapshot()),
      get: channel => permits(channel) ? runtimeContext.contextStore.get(channel) : undefined,
      patch: (...args) => project(runtimeContext.contextStore.patch(...args)),
      replace: (...args) => project(runtimeContext.contextStore.replace(...args)),
      set: (...args) => project(runtimeContext.contextStore.set(...args)),
      select: (...args) => project(runtimeContext.contextStore.select(...args)),
      subscribe,
      dispose,
    });
    const context = Object.freeze({
      ...runtimeContext,
      contextStore,
      get context() { return values(runtimeContext.context); },
      getSelection: channel => contextStore.get(channel),
      setContext: (...args) => contextStore.set(...args),
      select: (...args) => contextStore.select(...args),
      subscribe,
    });
    return { context, values, project, dispose };
  }

  function descriptorOf(raw, root, workbenchId, runtimeContext) {
    const typeId = String(raw.type || raw.id || "");
    if (Object.prototype.hasOwnProperty.call(raw, "create")) {
      throw new TypeError(`PortalWorkbench panel instances cannot override their registered factory: ${typeId}`);
    }
    const panelPackage = panelPackages.get(typeId);
    if (typeof panelPackage?.create !== "function") {
      throw new TypeError(`PortalWorkbench panel requires a registered Panel Package: ${typeId}`);
    }
    const descriptor = {
      ...raw,
      title: raw.title || panelPackage.presentation.title,
      icon: raw.icon || panelPackage.presentation.icon,
      minWidth: Math.max(panelPackage.sizing.minWidth, Number(raw.minWidth || 0)),
      minHeight: Math.max(panelPackage.sizing.minHeight, Number(raw.minHeight || 0)),
      singleton: panelPackage.instances === "single" || raw.singleton === true,
      create: panelPackage.create,
    };
    const id = String(descriptor.id || typeId).trim();
    if (!id) throw new TypeError("PortalWorkbench panel requires a stable id.");
    const mount = document.createElement("section");
    mount.id = String(descriptor.domId || `${workbenchId}-${id}-panel`);
    mount.className = "portalWorkbenchComponentMount";
    const contextScope = createPanelContext(runtimeContext, panelPackage.context.channels);
    let runtime;
    let element;
    try {
      const panelOptions = descriptor.options && typeof descriptor.options === "object"
        ? descriptor.options
        : {};
      runtime = descriptor.create({
        ...panelOptions,
        root: mount,
        panelId: id,
        workbenchId,
        context: contextScope.context,
        panelPackage,
        commands: Object.freeze({
          id: commandId => `panel.${id}.${String(commandId || "").trim()}`,
          execute: (commandId, invocation = {}) => runtimeContext.commandScope.execute(
            `panel.${id}.${String(commandId || "").trim()}`,
            { ...invocation, source: invocation.source || `panel:${id}` },
          ),
        }),
        emit: (event, detail) => runtimeContext.emit(id, event, detail),
      }) || mount;
      element = runtimeElement(runtime, mount);
      if (element !== mount) mount.replaceWith(element);
    } catch (error) {
      contextScope.dispose();
      mount.remove();
      throw error;
    }
    element.dataset.portalWorkbenchPanel = id;
    // Each panel is a named region, addressable by its visible title (not only by data attributes).
    if (!element.hasAttribute("role")) element.setAttribute("role", "region");
    if (!element.hasAttribute("aria-label") && !element.hasAttribute("aria-labelledby")) {
      element.setAttribute("aria-label", String(descriptor.title || id));
    }
    element.dataset.portalPanelPackage = panelPackage.type;
    element.dataset.portalPanelPackageVersion = String(panelPackage.version);
    element.dataset.portalPanelCapabilities = panelPackage.capabilities.join(" ");
    const requestedInset = String(
      descriptor.inset || element.dataset.portalWorkbenchInset || "none",
    ).trim().toLowerCase();
    const inset = ["none", "compact", "comfortable"].includes(requestedInset)
      ? requestedInset
      : "none";
    return {
      id,
      type: typeId || id,
      panelPackage,
      title: String(descriptor.title || element.getAttribute("aria-label") || id),
      layoutTitle: String(descriptor.layoutTitle || ""),
      icon: String(descriptor.icon || "panel"),
      element,
      required: descriptor.required === true,
      capabilities: panelPackage.capabilities,
      canHide: panelPackage.behavior.canHide && descriptor.canHide !== false && descriptor.required !== true,
      canCollapse: panelPackage.behavior.canCollapse && descriptor.canCollapse !== false,
      canMove: panelPackage.behavior.canMove
        && !panelPackage.behavior.locked
        && descriptor.canMove !== false
        && descriptor.locked !== true,
      defaultHidden: descriptor.defaultHidden === true,
      mobilePrimary: descriptor.mobilePrimary === true,
      minWidth: Math.max(120, Number(descriptor.minWidth || 180)),
      minHeight: Math.max(100, Number(descriptor.minHeight || 140)),
      inset,
      runtime,
      contextScope,
      ownsRuntime: true,
      closable: panelPackage.behavior.closable && descriptor.closable === true,
      restoreOnReload: descriptor.restoreOnReload === true,
      binding: descriptor.binding && typeof descriptor.binding === "object" ? clone(descriptor.binding) : null,
      openSource: descriptor.openSource === true,
      reusable: descriptor.reusable !== false,
      singleton: descriptor.singleton === true,
      locked: panelPackage.behavior.locked || descriptor.locked === true,
      serialize: typeof descriptor.serialize === "function" ? descriptor.serialize : null,
      openResource: typeof descriptor.openResource === "function" ? descriptor.openResource : null,
      onShow: typeof descriptor.onShow === "function" ? descriptor.onShow : null,
      onHide: typeof descriptor.onHide === "function" ? descriptor.onHide : null,
    };
  }

  function create(options = {}) {
    const id = String(options.id || "").trim();
    const root = elementOf(options.root);
    if (!id || !root) throw new TypeError("PortalWorkbench.create requires id and root.");
    instances.get(id)?.destroy?.();

    let storageUser = global.PortalIdentity?.username?.() || "";
    let storage = options.store || defaultWorkbenchStore(id, storageUser);
    function readStoredState() {
      const state = storage.read();
      const migrated = options.migrateState?.(state) || state;
      if (migrated !== state) storage.replace(migrated);
      return migrated;
    }
    const stored = readStoredState();
    let layoutLocked = stored.schemaVersion === SCHEMA_VERSION && stored.layoutLocked === true;
    let controller = null;
    const unavailableBindings = new Set();
    const initialPanelStates = new Map(Object.entries(options.initialPanelStates || {}));
    if (!global.PortalWorkbenchContext?.create) {
      throw new Error("PortalWorkbench requires PortalWorkbenchContext.");
    }
    if (!global.PortalCommandRegistry?.createScope) {
      throw new Error("PortalWorkbench requires PortalCommandRegistry.");
    }
    const ownsContextStore = !options.contextStore;
    const contextStore = options.contextStore || global.PortalWorkbenchContext.create({
      workbenchId: id,
      initial: options.context,
    });
    const commandScope = global.PortalCommandRegistry.createScope({
      id: `workbench.${id}`,
      context: () => ({
        ...contextStore.snapshot().values,
        workbench: controller,
        workbenchId: id,
      }),
    });
    const runtimeContext = Object.freeze({
      workbenchId: id,
      contextStore,
      commandScope,
      get context() { return contextStore.snapshot().values; },
      getSelection(channel) { return contextStore.get(channel); },
      setContext(channel, value, meta = {}) { return contextStore.set(channel, value, meta); },
      select(channel, value, meta = {}) { return contextStore.select(channel, value, meta); },
      subscribe(channels, listener, subscribeOptions = {}) {
        return contextStore.subscribe(channels, listener, subscribeOptions);
      },
      emit(sourcePanelId, event, detail) {
        const payload = Object.freeze({
          workbenchId: id,
          sourcePanelId: String(sourcePanelId || ""),
          event: String(event || ""),
          detail,
        });
        root.dispatchEvent(new CustomEvent("portal:workbench-panel-event", { detail: payload }));
        options.onPanelEvent?.(payload, controller);
        if (payload.event === "binding-unavailable" || payload.event === "binding-ready") {
          if (payload.event === "binding-unavailable") unavailableBindings.add(payload.sourcePanelId);
          else unavailableBindings.delete(payload.sourcePanelId);
          if (controller) persist();
        }
        const selectionChannel = ({
          "inspect": "inspectionTarget",
          "inspection-change": "inspectionTarget",
          "conversation-change": "conversationTarget",
          "document-change": "documentTarget",
          "graph-selection": "graphSelection",
          "command-context": "commandContext",
        })[payload.event];
        if (selectionChannel) {
          const value = detail?.value ?? detail?.target ?? detail?.selection ?? detail;
          contextStore.select(selectionChannel, value, {
            source: `panel:${payload.sourcePanelId}`,
            reason: payload.event,
          });
        } else if (payload.event === "view-select") {
          contextStore.set("navigationView", detail?.view ?? detail, {
            source: `panel:${payload.sourcePanelId}`,
            reason: payload.event,
          });
        }
        if (payload.event === "open-resource") {
          if (!controller?.openResource) {
            throw new Error(`PortalWorkbench ${id} cannot open a resource before initialization.`);
          }
          return Promise.resolve(controller.openResource(detail)).then(result => {
            if (result?.value?.accepted === false || result?.value?.stale === true) return result;
            contextStore.select("documentTarget", detail?.value ?? detail?.target ?? detail?.selection ?? detail, {
              source: `panel:${payload.sourcePanelId}`,
              reason: payload.event,
            });
            return result;
          });
        }
        return payload;
      },
      getPanel(panelId) { return controller?.getPanel?.(panelId) || null; },
      invoke(panelId, method, ...args) { return controller?.invokePanel?.(panelId, method, ...args); },
    });
    const executeCommand = (commandId, event, source = "workbench") => commandScope
      .execute(commandId, { event, source })
      .catch(error => console.error(`Portal Workbench command failed: ${commandId}`, error));
    const rawPanels = Array.isArray(options.initialEditors) ? options.initialEditors.slice() : [];
    const singletonTypes = new Set(rawPanels
      .filter(panel => panel?.singleton === true || panelPackages.get(String(panel?.type || ""))?.instances === "single")
      .map(panel => String(panel.type || ""))
      .filter(Boolean));
    if (stored.schemaVersion === SCHEMA_VERSION && Array.isArray(stored.openEditors)) {
      stored.openEditors.forEach(spec => {
        if (singletonTypes.has(String(spec?.type || ""))) return;
        const restored = options.restorePanel?.(clone(spec));
        if (restored) rawPanels.push({
          ...restored,
          id: String(spec.id || restored.id || ""),
          closable: true,
          restoreOnReload: true,
        });
      });
    }
    const descriptors = new Map(rawPanels.map(raw => {
      const descriptor = descriptorOf(raw, root, id, runtimeContext);
      return [descriptor.id, descriptor];
    }));
    if (!descriptors.size) throw new TypeError(`PortalWorkbench ${id} requires at least one panel.`);
    const panelIds = new Set(descriptors.keys());
    const requiredIds = new Set([...descriptors.values()].filter(item => item.required).map(item => item.id));
    const factoryPresets = new Map((options.presets || []).map(preset => [String(preset.id), {
      id: String(preset.id),
      name: String(preset.name || preset.id),
      layout: clone(preset.layout),
      hidden: [...new Set((preset.hidden || []).map(String))],
    }]));
    const defaultPreset = factoryPresets.get(String(options.defaultPreset || "")) || factoryPresets.values().next().value;
    const defaultLayout = clone(options.defaultLayout || defaultPreset?.layout || stackNode([...panelIds]));
    const storedPanelIds = new Set(
      stored.schemaVersion === SCHEMA_VERSION && Array.isArray(stored.panelIds)
        ? stored.panelIds.map(String)
        : [],
    );
    const savedWorkspaces = stored.schemaVersion === SCHEMA_VERSION && stored.savedWorkspaces
      ? { ...stored.savedWorkspaces }
      : {};
    const placements = stored.schemaVersion === SCHEMA_VERSION && stored.placements
      ? { ...stored.placements }
      : {};
    function hiddenPanelsForLayout(hiddenIds, knownPanelIds) {
      const result = new Set(hiddenIds);
      const known = new Set(knownPanelIds);
      descriptors.forEach(descriptor => {
        if (descriptor.defaultHidden && !known.has(descriptor.id)) result.add(descriptor.id);
      });
      requiredIds.forEach(panelId => result.delete(panelId));
      return result;
    }
    let hidden = hiddenPanelsForLayout(
      stored.schemaVersion === SCHEMA_VERSION
        ? (stored.hidden || [])
        : (defaultPreset?.hidden || []),
      storedPanelIds,
    );
    let collapsed = new Set(
      stored.schemaVersion === SCHEMA_VERSION
        ? (stored.collapsed || []).filter(panelId => panelIds.has(String(panelId)))
        : [],
    );
    [...hidden].forEach(panelId => collapsed.delete(String(panelId)));
    const visiblePanelIds = () => new Set([...panelIds].filter(panelId => !hidden.has(panelId)));
    let model = normalizeLayout(
      stored.schemaVersion === SCHEMA_VERSION ? stored.layout : (defaultPreset?.layout || defaultLayout),
      visiblePanelIds(),
    );

    root.classList.add("portalWorkbench");
    root.dataset.portalWorkbenchId = id;
    const viewportShell = root.closest(".portalWorkbenchViewportShell");
    const viewportFit = options.fitViewport === true
      ? {}
      : (options.fitViewport && typeof options.fitViewport === "object" ? options.fitViewport : null);
    let viewportResizeFrame = 0;
    let viewportStabilizeFrame = 0;
    const syncViewportHeight = () => {
      if ((!viewportFit && !viewportShell) || destroyed) return;
      cancelAnimationFrame(viewportResizeFrame);
      viewportResizeFrame = requestAnimationFrame(() => {
        if (viewportShell) {
          const offset = Math.max(0, Math.ceil(viewportShell.getBoundingClientRect().top + window.scrollY));
          viewportShell.style.setProperty("--portal-workbench-shell-offset", `${offset}px`);
        }
        if (!viewportFit) return;
        const top = root.getBoundingClientRect().top;
        const bottom = Math.max(0, Number(viewportFit.bottom || 0));
        const minimum = Math.max(100, Number(viewportFit.minHeight || 320));
        const maximum = Math.max(minimum, Number(viewportFit.maxHeight || Number.MAX_SAFE_INTEGER));
        const available = Math.floor(window.innerHeight - top - bottom);
        const height = Math.max(minimum, Math.min(maximum, available));
        root.style.setProperty("--portal-workbench-viewport-height", `${height}px`);
      });
    };
    const viewportObserver = (viewportFit || viewportShell) && window.ResizeObserver
      ? new window.ResizeObserver(syncViewportHeight) : null;
    if (viewportFit || viewportShell) {
      if (viewportFit) root.dataset.portalWorkbenchFitViewport = "true";
      window.addEventListener("resize", syncViewportHeight, { passive: true });
      window.visualViewport?.addEventListener("resize", syncViewportHeight, { passive: true });
      const header = document.querySelector(".portalSiteHeader");
      if (header) viewportObserver?.observe(header);
    }
    ["top", "right", "bottom", "left"].forEach(edge => {
      const value = Number(options.insets?.[edge] || 0);
      root.style.setProperty(`--portal-workbench-inset-${edge}`, `${Math.max(0, value)}px`);
    });
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
    let hostVisible = options.visible !== false;
    if (chrome) {
      chrome.className = "portalWorkbenchChrome";
      chrome.dataset.portalWorkbenchChrome = id;
      chrome.hidden = !hostVisible;
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
    let pendingPanelMounts = [];
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

    let focusedStackId = "";
    let activeOpenTargetPanelId = descriptors.has(String(stored.activeOpenTargetPanelId || ""))
      ? String(stored.activeOpenTargetPanelId)
      : (collectPanelIds(model).find(panelId => !descriptors.get(panelId)?.openSource) || "");
    let mobileActivePanelId = String(
      options.mobilePrimaryPanelId
      || [...descriptors.values()].find(descriptor => descriptor.mobilePrimary)?.id
      || activeOpenTargetPanelId
      || collectPanelIds(model)[0]
      || "",
    );
    const lastOpenTargetByType = new Map();
    let activeWorkspaceName = String(stored.activeWorkspaceName || defaultPreset?.name || options.label || "工作区");
    let destroyed = false;
    let editorOpenService = null;
    const panelCommandDisposers = new Map();
    let outsidePointerHandler = null;
    let activeRuntimePanelIds = new Set();
    let renderedModelReference = null;
    let renderedTopology = "";
    let renderedMobileMode = false;
    let renderedMobilePanelId = "";
    let committedWorkbenchState = null;
    let fixedSwitcherSignature = "";
    const layoutLockAnimations = new Set();
    const reducedLayoutMotion = global.matchMedia?.("(prefers-reduced-motion: reduce)");

    function cancelLayoutLockAnimations() {
      layoutLockAnimations.forEach(animation => animation.cancel());
      layoutLockAnimations.clear();
    }

    function onLayoutMotionPreferenceChange() {
      if (reducedLayoutMotion.matches) cancelLayoutLockAnimations();
    }
    reducedLayoutMotion?.addEventListener("change", onLayoutMotionPreferenceChange);

    function animateLayoutChrome(element, from, to) {
      const animation = element.animate?.([from, to], {
        duration: 220, easing: "cubic-bezier(.2,.8,.2,1)",
      });
      if (!animation) return;
      animation.id = "portal-workbench-layout-lock";
      layoutLockAnimations.add(animation);
      animation.finished.then(
        () => layoutLockAnimations.delete(animation),
        () => layoutLockAnimations.delete(animation),
      );
    }

    // Layout locking belongs to the shell, not to Panel runtimes. Nested shells
    // inherit the lock without persisting it as their own user preference.
    function inheritedLayoutLock() {
      const ancestor = root.parentElement?.closest("[data-portal-workbench-id]");
      return instances.get(ancestor?.dataset.portalWorkbenchId)?.isLayoutLocked() === true;
    }

    function isLayoutLocked() { return layoutLocked || inheritedLayoutLock(); }

    function syncLayoutLock(animate = false) {
      if (destroyed) return;
      const locked = isLayoutLocked();
      const inherited = inheritedLayoutLock();
      const changed = root.dataset.portalWorkbenchLayoutLocked !== String(locked);
      const own = selector => [...canvas.querySelectorAll(selector)]
        .filter(element => element.closest("[data-portal-workbench-id]") === root);
      // Snapshot shell chrome only. Panel bodies take their final geometry once;
      // no content transform, clone, remount or per-frame resize is involved.
      const chromeBefore = animate && changed && !reducedLayoutMotion?.matches
        ? own(".portalWorkbenchStack:not(.is-collapsed), .portalWorkbenchStack:not(.is-collapsed) > .portalWorkbenchTabs")
          .filter(element => element.getClientRects().length || element.classList.contains("portalWorkbenchTabs"))
          .map(element => {
            const style = getComputedStyle(element);
            return {
              element, visible: style.display !== "none", height: element.getBoundingClientRect().height,
              opacity: style.opacity, transform: style.transform,
              frame: { borderColor: style.borderColor, borderRadius: style.borderRadius, boxShadow: style.boxShadow },
            };
          })
        : [];
      if (changed) cancelLayoutLockAnimations();
      root.dataset.portalWorkbenchLayoutLocked = String(locked);
      stage.classList.toggle("is-layout-locked", locked);
      toolbar.classList.toggle("is-layout-locked", locked);
      toolbar.classList.toggle("is-layout-lock-inherited", inherited);
      const lockLabel = locked ? window.PortalI18n.t("解锁") : window.PortalI18n.t("固定");
      lockButton.title = inherited ? window.PortalI18n.t("布局已由外层工作台固定") : lockLabel;
      lockButton.setAttribute("aria-label", lockButton.title);
      lockButton.setAttribute("aria-pressed", String(locked));
      lockButton.disabled = inherited;
      customizeButton.disabled = locked;
      if (locked) closeMenu();
      own(".portalWorkbenchTabs").forEach(tabs => { tabs.inert = locked; });
      own("[data-portal-workbench-tab]").forEach(tab => {
        tab.draggable = !locked && descriptors.get(tab.dataset.portalWorkbenchTab)?.canMove === true;
      });
      own(".portalWorkbenchStack").forEach(stack => {
        if (locked) {
          stack.classList.remove("is-tab-strip-revealed");
          delete stack.dataset.portalWorkbenchDropZone;
        }
      });
      own(".portalWorkbenchSplitHandle").forEach(handle => {
        handle.tabIndex = locked ? -1 : 0;
        handle.setAttribute("aria-disabled", String(locked));
        handle.setAttribute("aria-label", locked ? window.PortalI18n.t("面板分隔线") : window.PortalI18n.t("调整工作区面板大小"));
      });
      const tabGroups = [];
      const collectTabGroups = node => {
        if (node?.type === "stack" && node.children.length > 1) tabGroups.push(node);
        else if (node?.type === "split") node.children.forEach(collectTabGroups);
      };
      if (locked) collectTabGroups(model);
      const signature = JSON.stringify(tabGroups.map(node => [node.id, node.children.map(child => (
        [child.panelId, descriptors.get(child.panelId).title, collapsed.has(child.panelId)]
      ))]));
      if (signature !== fixedSwitcherSignature) {
        fixedSwitcherSignature = signature;
        fixedSwitchers.replaceChildren();
        tabGroups.forEach(node => {
          const select = document.createElement("select");
          select.className = "portalWorkbenchFixedPanelSelect";
          select.setAttribute("aria-label", window.PortalI18n.t("切换固定面板"));
          select.disabled = collapsed.has(node.activePanelId);
          node.children.forEach(child => {
            const descriptor = descriptors.get(child.panelId);
            const option = document.createElement("option");
            option.value = child.panelId;
            option.textContent = descriptor.title;
            option.disabled = collapsed.has(child.panelId);
            select.append(option);
          });
          select.addEventListener("change", () => focusPanel(select.value));
          fixedSwitchers.append(select);
        });
      }
      tabGroups.forEach((node, index) => { fixedSwitchers.children[index].value = node.activePanelId; });
      fixedSwitchers.hidden = !locked || !fixedSwitchers.childElementCount || global.PortalResponsive?.isMobile?.() === true;
      toolbar.hidden = inherited && fixedSwitchers.hidden;
      chromeBefore.forEach(before => {
        const { element } = before;
        const after = getComputedStyle(element);
        if (element.classList.contains("portalWorkbenchStack")) {
          animateLayoutChrome(element, before.frame, {
            borderColor: after.borderColor, borderRadius: after.borderRadius, boxShadow: after.boxShadow,
          });
        } else if (locked && before.visible && before.height) {
          // The departing header is an inert overlay: it cannot reserve a grid
          // row or intercept the now-available content beneath it.
          const overlay = {
            display: "flex", position: "absolute", top: "0px", left: "0px", right: "0px",
            height: `${before.height}px`, zIndex: 3, pointerEvents: "none",
          };
          const transform = before.transform === "none" ? "" : before.transform;
          animateLayoutChrome(element,
            { ...overlay, opacity: before.opacity, transform: before.transform },
            { ...overlay, opacity: 0, transform: `${transform} translateY(-8px)` });
        } else if (!locked && element.getClientRects().length) {
          const transform = after.transform === "none" ? "" : after.transform;
          animateLayoutChrome(element,
            { opacity: 0, transform: `${transform} translateY(-8px)` },
            { opacity: after.opacity, transform: after.transform });
        }
      });
    }

    function setLayoutLocked(value) {
      if (destroyed || inheritedLayoutLock()) return false;
      const next = value === true;
      if (layoutLocked === next) return true;
      announceBeforeLayout("layout-lock");
      layoutLocked = next;
      layoutLockSynchronizers.forEach(sync => sync(true));
      persist();
      announceChange("layout-lock");
      return true;
    }
    layoutLockSynchronizers.add(syncLayoutLock);

    function commitWorkbenchState() {
      committedWorkbenchState = {
        model: clone(model),
        hidden: [...hidden],
        collapsed: [...collapsed],
        focusedStackId,
        activeOpenTargetPanelId,
        mobileActivePanelId,
        activeWorkspaceName,
        placements: clone(placements),
        lastOpenTargets: [...lastOpenTargetByType.entries()],
      };
    }

    function restoreCommittedWorkbenchState() {
      if (!committedWorkbenchState) return false;
      model = clone(committedWorkbenchState.model);
      hidden = new Set(committedWorkbenchState.hidden);
      collapsed = new Set(committedWorkbenchState.collapsed);
      focusedStackId = committedWorkbenchState.focusedStackId;
      activeOpenTargetPanelId = committedWorkbenchState.activeOpenTargetPanelId;
      mobileActivePanelId = committedWorkbenchState.mobileActivePanelId;
      activeWorkspaceName = committedWorkbenchState.activeWorkspaceName;
      Object.keys(placements).forEach(panelId => delete placements[panelId]);
      Object.assign(placements, clone(committedWorkbenchState.placements));
      lastOpenTargetByType.clear();
      committedWorkbenchState.lastOpenTargets.forEach(([type, panelId]) => {
        lastOpenTargetByType.set(type, panelId);
      });
      return true;
    }

    function callPanelLifecycle(descriptor, method, ...args) {
      const handler = descriptor?.runtime?.[method];
      if (typeof handler !== "function") return undefined;
      const scopedArgs = args.map(argument => argument === runtimeContext ? descriptor.contextScope.context : argument);
      if (method === "setContext") {
        scopedArgs[0] = descriptor.contextScope.values(scopedArgs[0]);
        if (scopedArgs[2]?.changedChannels) scopedArgs[2] = descriptor.contextScope.project(scopedArgs[2]);
      }
      try { return handler.apply(descriptor.runtime, scopedArgs); }
      catch (error) {
        console.error(`PortalWorkbench ${id} panel ${descriptor.id} ${method} failed`, error);
        return undefined;
      }
    }

    const unsubscribeWorkbenchContext = contextStore.subscribe("*", change => {
      descriptors.forEach(descriptor => {
        const channels = descriptor.panelPackage.context.channels;
        if (!channels.includes("*") && !change.changedChannels.some(channel => channels.includes(channel))) return;
        callPanelLifecycle(descriptor, "setContext", change.values, runtimeContext, change);
      });
      root.dispatchEvent(new CustomEvent("portal:workbench-selection-change", { detail: change }));
    });

    function activePanelIds(node, result = new Set()) {
      if (!node) return result;
      if (node.type === "panel") {
        if (!collapsed.has(node.panelId) && !hidden.has(node.panelId)) result.add(node.panelId);
        return result;
      }
      if (node.type === "stack") {
        if (!collapsed.has(node.activePanelId) && !hidden.has(node.activePanelId)) result.add(node.activePanelId);
        return result;
      }
      (node.children || []).forEach(child => activePanelIds(child, result));
      return result;
    }

    function reconcilePanelVisibility() {
      const mobileProjected = global.PortalResponsive?.isMobile?.()
        && descriptors.has(mobileActivePanelId)
        ? mobileActivePanelId
        : "";
      const next = !hostVisible ? new Set() : mobileProjected ? new Set([mobileProjected]) : activePanelIds(model);
      const previous = activeRuntimePanelIds;
      // Commit visibility before lifecycle callbacks. A callback may synchronously
      // focus, hide, or show a panel and trigger another render; the nested
      // reconciliation must observe the state that caused this callback.
      activeRuntimePanelIds = next;
      previous.forEach(panelId => {
        if (next.has(panelId)) return;
        const descriptor = descriptors.get(panelId);
        callPanelLifecycle(descriptor, "hide", runtimeContext);
        descriptor?.onHide?.({ workbench: controller, panel: descriptor });
      });
      next.forEach(panelId => {
        if (previous.has(panelId)) return;
        const descriptor = descriptors.get(panelId);
        callPanelLifecycle(descriptor, "show", runtimeContext);
        descriptor?.onShow?.({ workbench: controller, panel: descriptor });
      });
    }

    function stateSnapshot() {
      const panelStates = {};
      const openEditors = [];
      descriptors.forEach(descriptor => {
        // The owner confirmed this binding no longer exists. Keep the visible
        // error panel and separate drafts, but do not revive it after reload.
        if (unavailableBindings.has(descriptor.id)) return;
        const value = descriptor.capabilities.includes("snapshot")
          ? callPanelLifecycle(descriptor, "snapshot", runtimeContext)
          : undefined;
        if (value !== undefined) {
          const persistence = descriptor.panelPackage.persistence;
          const serialized = persistence.serialize
            ? persistence.serialize(value, { descriptor, runtime: descriptor.runtime, context: runtimeContext })
            : value;
          panelStates[descriptor.id] = {
            packageType: descriptor.panelPackage.type,
            packageVersion: descriptor.panelPackage.version,
            schemaVersion: persistence.schemaVersion,
            value: clone(serialized),
          };
        }
        if (descriptor.restoreOnReload) {
          const serialized = descriptor.serialize?.({ descriptor, runtime: descriptor.runtime, state: value }) || {};
          openEditors.push({
            id: descriptor.id,
            type: descriptor.type,
            title: descriptor.title,
            icon: descriptor.icon,
            binding: clone(serialized.binding || value?.binding || descriptor.binding || {}),
            locked: descriptor.locked === true,
          });
        }
      });
      return {
        schemaVersion: SCHEMA_VERSION,
        ...(options.contentSchemaVersion ? { contentSchemaVersion: options.contentSchemaVersion } : {}),
        layoutLocked,
        panelIds: [...panelIds],
        openEditors,
        activeWorkspaceName,
        activeOpenTargetPanelId,
        layout: clone(model),
        hidden: [...hidden],
        collapsed: [...collapsed],
        placements: clone(placements),
        savedWorkspaces: clone(savedWorkspaces),
        panelStates,
      };
    }

    function persist() {
      if (destroyed) return;
      storage.replace(stateSnapshot());
    }

    function restoreIdentityLayout() {
      const user = global.PortalIdentity?.username?.() || "";
      if (destroyed || options.store || user === storageUser) return;
      const previousUser = storageUser;
      storageUser = user;
      storage = defaultWorkbenchStore(id, user);
      const saved = readStoredState();
      if (saved.schemaVersion !== SCHEMA_VERSION && !previousUser) {
        persist();
        return;
      }
      const state = saved.schemaVersion === SCHEMA_VERSION ? saved : {};
      layoutLocked = state.layoutLocked === true;
      for (const record of [placements, savedWorkspaces]) {
        Object.keys(record).forEach(key => delete record[key]);
      }
      Object.assign(placements, state.placements || {});
      Object.assign(savedWorkspaces, state.savedWorkspaces || {});
      hidden = hiddenPanelsForLayout(state.hidden || defaultPreset?.hidden || [], state.panelIds || []);
      collapsed = new Set(state.collapsed || []);
      model = normalizeNode(state.layout || defaultPreset?.layout || defaultLayout, visiblePanelIds());
      activeWorkspaceName = state.activeWorkspaceName || defaultPreset?.name || options.label || "工作区";
      activeOpenTargetPanelId = state.activeOpenTargetPanelId || "";
      descriptors.forEach(descriptor => restorePanelState(descriptor, state));
      normalizeCurrent();
      render("restore-identity");
      layoutLockSynchronizers.forEach(sync => sync());
    }

    function announceChange(reason) {
      const detail = { id, reason, layout: clone(model), hidden: [...hidden], collapsed: [...collapsed] };
      root.dispatchEvent(new CustomEvent("portal:workbench-change", { detail }));
      options.onChange?.(detail);
      requestAnimationFrame(() => {
        descriptors.forEach(descriptor => {
          descriptor.element.dispatchEvent(new CustomEvent("portal:workbench-resize", { bubbles: false, detail }));
        });
      });
    }

    function announceBeforeLayout(reason) {
      const detail = { id, reason, layout: clone(model), hidden: [...hidden], collapsed: [...collapsed] };
      root.dispatchEvent(new CustomEvent("portal:workbench-before-layout", { detail }));
      descriptors.forEach(descriptor => {
        descriptor.element.dispatchEvent(new CustomEvent("portal:workbench-before-layout", {
          bubbles: false,
          detail,
        }));
      });
    }

    function normalizeCurrent() {
      model = normalizeLayout(model, visiblePanelIds());
    }

    function activatePanel(stack, panelId) {
      if (isLayoutLocked()) return;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.openSource) {
        activeOpenTargetPanelId = String(panelId);
        lastOpenTargetByType.set(descriptor.type, descriptor.id);
      }
      if (stack.activePanelId === panelId) {
        focusedStackId = stack.id;
        if (collapsed.has(panelId)) expandPanel(panelId);
        return;
      }
      stack.activePanelId = panelId;
      focusedStackId = stack.id;
      render("activate-panel");
    }

    function moveConnectedPanelElement(parent, element) {
      if (typeof parent.moveBefore === "function") {
        try { parent.moveBefore(element, null); }
        catch (_error) { return false; }
        return element.parentElement === parent && element.isConnected;
      }
      // Older browsers can reparent ordinary DOM, but disconnecting custom
      // elements or embedded documents/media can destroy their live state.
      const nodes = document.createTreeWalker(element, NodeFilter.SHOW_ELEMENT);
      for (let node = nodes.currentNode; node; node = nodes.nextNode()) {
        if (node.localName.includes("-") || node.shadowRoot
          || node.matches("iframe, object, embed, audio, video, [is], [popover], dialog[open]")) return false;
      }
      if (document.fullscreenElement && element.contains(document.fullscreenElement)) return false;
      const focused = element.contains(document.activeElement) ? document.activeElement : null;
      const selection = focused && typeof focused.selectionStart === "number"
        ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
      try { parent.append(element); }
      catch (_error) { return false; }
      if (focused) {
        focused.focus({ preventScroll: true });
        if (selection) focused.setSelectionRange(...selection);
      }
      return element.parentElement === parent && element.isConnected;
    }

    function mountPanelElement(parent, element) {
      if (!parent || !element) return false;
      if (element.parentElement === parent) return !parent.isConnected || element.isConnected;
      if (!parent.isConnected && element.isConnected) {
        pendingPanelMounts.push([parent, element]);
        return true;
      } else if (parent.isConnected && element.isConnected) {
        return moveConnectedPanelElement(parent, element);
      } else {
        parent.append(element);
        return element.parentElement === parent && (!parent.isConnected || element.isConnected);
      }
    }

    function panelFrameIn(layoutRoot, panelId) {
      // Panel IDs are local to a Workbench. A nested Workbench can contain a
      // frame with the same ID, but never owns this instance's Panel root.
      return [...layoutRoot.querySelectorAll(
        `[data-portal-workbench-panel-frame="${CSS.escape(panelId)}"]`,
      )].find(frame => frame.closest("[data-portal-workbench-id]") === root) || null;
    }

    function reconcilePanelMounts(layoutRoot) {
      let complete = true;
      descriptors.forEach(descriptor => {
        const frame = panelFrameIn(layoutRoot, descriptor.id);
        if (!mountPanelElement(frame || parking, descriptor.element)) complete = false;
      });
      return complete;
    }

    function restorePanelMounts(originalParents) {
      let complete = true;
      descriptors.forEach(descriptor => {
        const originalParent = originalParents.get(descriptor.id) || null;
        const element = descriptor.element;
        if (element.parentElement === originalParent) return;
        if (!originalParent) {
          element.remove();
          return;
        }
        if (originalParent.isConnected && element.isConnected) {
          if (!moveConnectedPanelElement(originalParent, element)) complete = false;
        } else if (!originalParent.isConnected || !element.isConnected) {
          originalParent.append(element);
        }
        if (element.parentElement !== originalParent) complete = false;
      });
      return complete;
    }

    function renderPanelFrame(descriptor, active, panelCollapsed = false) {
      const frame = document.createElement("div");
      frame.className = `portalWorkbenchPanelFrame${descriptor.id === activeOpenTargetPanelId ? " is-open-target" : ""}`;
      frame.dataset.portalWorkbenchPanelFrame = descriptor.id;
      frame.dataset.portalWorkbenchInset = descriptor.inset;
      frame.hidden = !active || panelCollapsed;
      mountPanelElement(frame, descriptor.element);
      return frame;
    }

    function refreshPanelFrame(frame, descriptor, active, panelCollapsed = false) {
      if (!frame || frame.dataset.portalWorkbenchPanelFrame !== descriptor.id) return false;
      frame.classList.toggle("is-open-target", descriptor.id === activeOpenTargetPanelId);
      frame.dataset.portalWorkbenchInset = descriptor.inset;
      frame.hidden = !active || panelCollapsed;
      mountPanelElement(frame, descriptor.element);
      return true;
    }

    function bindTabDrag(tab, panelId) {
      if (!descriptors.get(String(panelId))?.canMove) return;
      tab.draggable = true;
      tab.addEventListener("dragstart", event => {
        if (isLayoutLocked()) { event.preventDefault(); return; }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("application/x-portal-workbench-panel", panelId);
        tab.classList.add("is-dragging");
        document.body.classList.add("draggingWorkbenchTab");
      });
      tab.addEventListener("dragend", () => {
        tab.classList.remove("is-dragging");
        document.body.classList.remove("draggingWorkbenchTab");
        canvas.querySelectorAll("[data-portal-workbench-drop-zone]").forEach(node => {
          node.removeAttribute("data-portal-workbench-drop-zone");
        });
      });
    }

    function dropZone(stackElement, event) {
      const rect = stackElement.getBoundingClientRect();
      const x = (event.clientX - rect.left) / Math.max(1, rect.width);
      const y = (event.clientY - rect.top) / Math.max(1, rect.height);
      if (x < 0.24) return "left";
      if (x > 0.76) return "right";
      if (y < 0.24) return "top";
      if (y > 0.76) return "bottom";
      return "center";
    }

    function movePanel(panelId, targetStackId, zone) {
      if (isLayoutLocked()) return false;
      if (!descriptors.get(String(panelId))?.canMove) return;
      const targetBefore = findNode(model, targetStackId)?.node;
      if (!targetBefore || targetBefore.type !== "stack") return;
      if (zone === "center" && targetBefore.children.some(child => child.panelId === panelId)) {
        targetBefore.activePanelId = panelId;
        focusedStackId = targetBefore.id;
        render("activate-panel");
        return;
      }
      if (zone !== "center" && targetBefore.children.length === 1 && targetBefore.children[0].panelId === panelId) return;
      let next = removePanelFromNode(clone(model), panelId);
      hidden.delete(panelId);
      if (!next) {
        model = stackNode([panelId]);
        render("move-panel");
        return;
      }
      const target = findNode(next, targetStackId)?.node;
      if (!target || target.type !== "stack") {
        model = splitNode("horizontal", [next, stackNode([panelId])]);
      } else if (zone === "center") {
        target.children.push(panelNode(panelId));
        target.activePanelId = panelId;
        model = next;
      } else {
        const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
        const sourceStack = stackNode([panelId]);
        const replacement = ["left", "top"].includes(zone)
          ? splitNode(orientation, [sourceStack, target])
          : splitNode(orientation, [target, sourceStack]);
        model = replaceNode(next, targetStackId, replacement);
      }
      normalizeCurrent();
      render("move-panel");
    }

    function bindStackDrop(stackElement, stack) {
      stackElement.addEventListener("dragover", event => {
        if (isLayoutLocked()) return;
        if (!event.dataTransfer.types.includes("application/x-portal-workbench-panel")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        stackElement.dataset.portalWorkbenchDropZone = dropZone(stackElement, event);
      });
      stackElement.addEventListener("dragleave", event => {
        if (!stackElement.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      stackElement.addEventListener("drop", event => {
        const panelId = event.dataTransfer.getData("application/x-portal-workbench-panel");
        if (!panelId) return;
        event.preventDefault();
        const zone = stackElement.dataset.portalWorkbenchDropZone || "center";
        stackElement.removeAttribute("data-portal-workbench-drop-zone");
        movePanel(panelId, stack.id, zone);
      });
    }

    function bindTabGroupDrop(tabs, stackElement, stack) {
      tabs.addEventListener("dragover", event => {
        if (isLayoutLocked()) return;
        if (!event.dataTransfer.types.includes("application/x-portal-workbench-panel")) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "move";
        stackElement.dataset.portalWorkbenchDropZone = "center";
      });
      tabs.addEventListener("dragleave", event => {
        event.stopPropagation();
        if (!tabs.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      tabs.addEventListener("drop", event => {
        const panelId = event.dataTransfer.getData("application/x-portal-workbench-panel");
        if (!panelId) return;
        event.preventDefault();
        event.stopPropagation();
        stackElement.removeAttribute("data-portal-workbench-drop-zone");
        movePanel(panelId, stack.id, "center");
      });
    }

    function bindSingleTabReveal(stackElement, tabs, stack) {
      if (stack.children.length !== 1) return;
      const reveal = () => { if (!isLayoutLocked()) stackElement.classList.add("is-tab-strip-revealed"); };
      const conceal = () => stackElement.classList.remove("is-tab-strip-revealed");
      stackElement.addEventListener("pointermove", event => {
        const bounds = stackElement.getBoundingClientRect();
        const localY = event.clientY - bounds.top;
        if (tabs.contains(event.target) || (stackElement.classList.contains("is-tab-strip-revealed") && localY <= 34)) {
          reveal();
          return;
        }
        const nestedControl = event.target instanceof Element
          ? event.target.closest("button, a, input, select, textarea, [role='tab'], [role='button']")
          : null;
        const ownerWorkbench = stackElement.closest(".portalWorkbench");
        if (nestedControl && nestedControl.closest(".portalWorkbench") !== ownerWorkbench) return;
        if (localY <= 16) reveal();
        else if (localY > 34) conceal();
      }, true);
      stackElement.addEventListener("pointerleave", conceal);
      tabs.addEventListener("focusin", reveal);
      tabs.addEventListener("focusout", () => requestAnimationFrame(() => {
        if (!tabs.contains(document.activeElement)) conceal();
      }));
    }

    function renderStack(stack, parentOrientation = "") {
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      const mobileOwnsStack = mobileMode
        && stack.children.some(child => child.panelId === mobileActivePanelId);
      const displayedPanelId = mobileOwnsStack ? mobileActivePanelId : stack.activePanelId;
      const activeCollapsed = !mobileOwnsStack && collapsed.has(String(displayedPanelId || ""));
      const element = document.createElement("section");
      element.className = `portalWorkbenchStack${activeCollapsed ? " is-collapsed" : ""}`;
      element.dataset.portalWorkbenchStack = stack.id;
      element.dataset.tabCount = String(stack.children.length);
      element.dataset.portalMobileActive = String(
        mobileOwnsStack,
      );
      if (activeCollapsed) element.dataset.collapseAxis = parentOrientation || "vertical";
      element.addEventListener("pointerdown", () => {
        focusedStackId = stack.id;
        const descriptor = descriptors.get(String(stack.activePanelId || ""));
        if (descriptor && !descriptor.openSource) {
          activeOpenTargetPanelId = descriptor.id;
          lastOpenTargetByType.set(descriptor.type, descriptor.id);
        }
      });
      const tabs = document.createElement("div");
      tabs.className = "portalWorkbenchTabs";
      if (stack.children.length === 1) tabs.dataset.portalOverlayLayer = "sticky";
      tabs.setAttribute("role", "tablist");
      const body = document.createElement("div");
      body.className = "portalWorkbenchStackBody";
      stack.children.forEach(child => {
        const descriptor = descriptors.get(child.panelId);
        if (!descriptor) return;
        const active = child.panelId === displayedPanelId;
        const tabSlot = document.createElement("span");
        tabSlot.className = "portalWorkbenchTabSlot";
        const tab = document.createElement("button");
        tab.className = `portalWorkbenchTab${active ? " is-active" : ""}`;
        tab.type = "button";
        tab.dataset.portalWorkbenchTab = descriptor.id;
        tab.dataset.portalIcon = descriptor.icon;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(active));
        tab.title = descriptor.title;
        const tabLabel = document.createElement("span");
        tabLabel.textContent = descriptor.title;
        tab.append(tabLabel);
        tab.addEventListener("click", () => activatePanel(stack, descriptor.id));
        bindTabDrag(tab, descriptor.id);
        tabSlot.append(tab);
        if (descriptor.canHide) {
          const close = document.createElement("button");
          close.className = "portalWorkbenchTabClose";
          close.type = "button";
          close.dataset.portalIcon = "close";
          close.setAttribute("aria-label", window.PortalI18n.text`关闭 ${descriptor.title}`);
          close.title = window.PortalI18n.text`关闭 ${descriptor.title}`;
          close.addEventListener("click", event => {
            event.stopPropagation();
            executeCommand(`panel.${descriptor.id}.${descriptor.closable ? "close" : "toggle-visibility"}`, event, "panel-tab");
          });
          tabSlot.append(close);
        }
        tabs.append(tabSlot);
        body.append(renderPanelFrame(descriptor, active, active && activeCollapsed));
      });
      element.append(tabs, body);
      bindSingleTabReveal(element, tabs, stack);
      bindTabGroupDrop(tabs, element, stack);
      bindStackDrop(element, stack);
      return element;
    }

    function refreshStack(element, stack, parentOrientation = "") {
      if (!element || element.dataset.portalWorkbenchStack !== stack.id) return false;
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      const mobileOwnsStack = mobileMode
        && stack.children.some(child => child.panelId === mobileActivePanelId);
      const displayedPanelId = mobileOwnsStack ? mobileActivePanelId : stack.activePanelId;
      const activeCollapsed = !mobileOwnsStack && collapsed.has(String(displayedPanelId || ""));
      element.classList.toggle("is-collapsed", activeCollapsed);
      element.dataset.tabCount = String(stack.children.length);
      element.dataset.portalMobileActive = String(mobileOwnsStack);
      if (activeCollapsed) element.dataset.collapseAxis = parentOrientation || "vertical";
      else delete element.dataset.collapseAxis;
      const tabs = element.querySelector(":scope > .portalWorkbenchTabs");
      const body = element.querySelector(":scope > .portalWorkbenchStackBody");
      if (!tabs || !body) return false;
      for (const child of stack.children) {
        const descriptor = descriptors.get(child.panelId);
        if (!descriptor) return false;
        const active = child.panelId === displayedPanelId;
        const tab = tabs.querySelector(`[data-portal-workbench-tab="${CSS.escape(descriptor.id)}"]`);
        const frame = body.querySelector(`:scope > [data-portal-workbench-panel-frame="${CSS.escape(descriptor.id)}"]`);
        if (!tab || !refreshPanelFrame(frame, descriptor, active, active && activeCollapsed)) return false;
        tab.classList.toggle("is-active", active);
        tab.dataset.portalIcon = descriptor.icon;
        tab.setAttribute("aria-selected", String(active));
        tab.title = descriptor.title;
        const label = tab.querySelector(":scope > span");
        if (label) label.textContent = descriptor.title;
        const close = tab.parentElement?.querySelector(":scope > .portalWorkbenchTabClose");
        if (close) {
          close.setAttribute("aria-label", window.PortalI18n.text`关闭 ${descriptor.title}`);
          close.title = window.PortalI18n.text`关闭 ${descriptor.title}`;
        }
      }
      return true;
    }

    function bindSplitHandle(handle, split, before, after, beforeElement, afterElement, index) {
      const axis = split.orientation === "vertical" ? "y" : "x";
      const beforeMinimum = minimumForNode(before, axis, descriptors, collapsed);
      const afterMinimum = minimumForNode(after, axis, descriptors, collapsed);
      const bounds = total => {
        const usable = Math.max(2, Number(total) || 2);
        const minimum = Math.min(beforeMinimum, usable / 2);
        const afterFloor = Math.min(afterMinimum, usable - minimum);
        return { minimum, maximum: Math.max(minimum, usable - afterFloor) };
      };
      const readSizes = () => {
        const children = [...beforeElement.parentElement.children].filter(element => element.classList.contains("portalWorkbenchSplitChild"));
        const sizes = children.map(element => element.getBoundingClientRect()[axis === "x" ? "width" : "height"]);
        return {
          before: sizes[index],
          after: sizes[index + 1],
          children,
          sizes,
        };
      };
      const applySizes = (beforeSize, afterSize, start) => {
        if (isLayoutLocked()) return;
        const sizes = start.sizes.slice();
        sizes[index] = beforeSize;
        sizes[index + 1] = afterSize;
        const edges = axis === "x" ? ["Left", "Right"] : ["Top", "Bottom"];
        const expanded = split.children.map((child, childIndex) => childIndex)
          .filter(childIndex => compactExtentForNode(split.children[childIndex], axis, collapsed) === null);
        const weight = expanded.reduce((sum, childIndex) => sum + split.weights[childIndex], 0);
        expanded.forEach(childIndex => {
          const style = getComputedStyle(start.children[childIndex]);
          sizes[childIndex] = Math.max(1, sizes[childIndex] - edges.reduce((sum, edge) => (
            sum + (parseFloat(style[`padding${edge}`]) || 0) + (parseFloat(style[`border${edge}Width`]) || 0)
          ), 0));
        });
        const total = expanded.reduce((sum, childIndex) => sum + sizes[childIndex], 0);
        expanded.forEach(childIndex => {
          split.weights[childIndex] = weight * sizes[childIndex] / total;
          start.children[childIndex].style.flexGrow = String(split.weights[childIndex]);
        });
      };
      bindPointerResize({
        handle,
        axis,
        disabled: isLayoutLocked,
        bodyClass: axis === "x" ? "resizingWorkbenchPane" : "resizingWorkbenchRow",
        readStart: readSizes,
        onMove: ({ delta, start }) => {
          const total = start.before + start.after;
          const range = bounds(total);
          const nextBefore = clamp(start.before + delta, range.minimum, range.maximum);
          const nextAfter = total - nextBefore;
          applySizes(nextBefore, nextAfter, start);
          announceChange("resize-live");
        },
        onDone: () => {
          persist();
          announceChange("resize");
        },
      });
      bindKeyboardResize({
        handle,
        axis,
        disabled: isLayoutLocked,
        keyboardStep: 24,
        readStart: readSizes,
        readValue: () => readSizes().before,
        min: () => {
          const sizes = readSizes();
          return bounds(sizes.before + sizes.after).minimum;
        },
        max: () => {
          const sizes = readSizes();
          return bounds(sizes.before + sizes.after).maximum;
        },
        onMove: ({ delta, start }) => {
          const total = start.before + start.after;
          const range = bounds(total);
          const nextBefore = clamp(start.before + delta, range.minimum, range.maximum);
          applySizes(nextBefore, total - nextBefore, start);
        },
        onDone: () => { persist(); announceChange("resize"); },
        valueText: value => `${Math.round(value || 0)} 像素`,
      });
    }

    function renderSplit(split) {
      const element = document.createElement("div");
      element.className = "portalWorkbenchSplit";
      element.dataset.orientation = split.orientation;
      element.dataset.portalWorkbenchSplit = split.id;
      const children = split.children.map((child, index) => {
        const childElement = renderNode(child, split.orientation);
        childElement.classList.add("portalWorkbenchSplitChild");
        const axis = split.orientation === "vertical" ? "y" : "x";
        const compactExtent = compactExtentForNode(child, axis, collapsed);
        const childCompact = compactExtent !== null;
        childElement.style.flexGrow = childCompact ? "0" : String(Number(split.weights?.[index]) || 1);
        childElement.style.flexShrink = childCompact ? "0" : "1";
        childElement.style.flexBasis = childCompact
          ? `${compactExtent}px`
          : "0px";
        if (split.orientation === "horizontal") {
          childElement.style.minWidth = splitChildMinimumCss(split, child, index, descriptors, collapsed);
        } else {
          childElement.style.minHeight = splitChildMinimumCss(split, child, index, descriptors, collapsed);
        }
        element.append(childElement);
        return childElement;
      });
      for (let index = children.length - 2; index >= 0; index -= 1) {
        const handle = document.createElement("div");
        handle.className = "portalWorkbenchSplitHandle";
        handle.dataset.orientation = split.orientation;
        handle.setAttribute("aria-label", window.PortalI18n.t("调整工作区面板大小"));
        handle.hidden = [split.children[index], split.children[index + 1]].some(child => (
          compactExtentForNode(child, split.orientation === "vertical" ? "y" : "x", collapsed) !== null
        ));
        children[index].after(handle);
        bindSplitHandle(handle, split, split.children[index], split.children[index + 1], children[index], children[index + 1], index);
      }
      return element;
    }

    function refreshSplit(element, split) {
      if (!element || element.dataset.portalWorkbenchSplit !== split.id) return false;
      element.dataset.orientation = split.orientation;
      const children = [...element.querySelectorAll(":scope > .portalWorkbenchSplitChild")];
      const handles = [...element.querySelectorAll(":scope > .portalWorkbenchSplitHandle")];
      if (children.length !== split.children.length || handles.length !== Math.max(0, children.length - 1)) return false;
      for (let index = 0; index < split.children.length; index += 1) {
        const child = split.children[index];
        const childElement = children[index];
        const axis = split.orientation === "vertical" ? "y" : "x";
        const compactExtent = compactExtentForNode(child, axis, collapsed);
        const childCompact = compactExtent !== null;
        childElement.style.flexGrow = childCompact ? "0" : String(Number(split.weights?.[index]) || 1);
        childElement.style.flexShrink = childCompact ? "0" : "1";
        childElement.style.flexBasis = childCompact
          ? `${compactExtent}px`
          : "0px";
        if (split.orientation === "horizontal") {
          childElement.style.minWidth = splitChildMinimumCss(split, child, index, descriptors, collapsed);
          childElement.style.removeProperty("min-height");
        } else {
          childElement.style.minHeight = splitChildMinimumCss(split, child, index, descriptors, collapsed);
          childElement.style.removeProperty("min-width");
        }
        if (!refreshNode(childElement, child, split.orientation)) return false;
      }
      handles.forEach((handle, index) => {
        handle.dataset.orientation = split.orientation;
        const axis = split.orientation === "vertical" ? "y" : "x";
        handle.hidden = [split.children[index], split.children[index + 1]].some(child => (
          compactExtentForNode(child, axis, collapsed) !== null
        ));
      });
      return true;
    }

    function stackForPanelNode(node) {
      return {
        type: "stack",
        id: `${node.id}-stack`,
        activePanelId: node.panelId,
        children: [node],
      };
    }

    function renderNode(node, parentOrientation = "") {
      if (node.type === "stack") return renderStack(node, parentOrientation);
      if (node.type === "split") return renderSplit(node);
      return renderStack(stackForPanelNode(node), parentOrientation);
    }

    function refreshNode(element, node, parentOrientation = "") {
      if (node.type === "stack") return refreshStack(element, node, parentOrientation);
      if (node.type === "split") return refreshSplit(element, node);
      return refreshStack(element, stackForPanelNode(node), parentOrientation);
    }

    function topologyOf(node) {
      if (!node) return null;
      if (node.type === "panel") return ["panel", node.id, node.panelId];
      return [
        node.type,
        node.id,
        node.type === "split" ? node.orientation : "",
        (node.children || []).map(topologyOf),
      ];
    }

    function renderMenu() {
      const editorOptionsOpen = Boolean(menu.querySelector(".portalWorkbenchEditorOptions")?.open);
      const presetRows = [...factoryPresets.values()].map(preset => window.PortalI18n.html`
        <button class="portalWorkbenchMenuRow" type="button" data-workbench-preset="${escapeHtml(preset.id)}">
          <span data-portal-icon="window"></span><span>${escapeHtml(preset.name)}</span>
        </button>`).join("");
      const savedRows = Object.entries(savedWorkspaces).map(([workspaceId, workspace]) => window.PortalI18n.html`
        <span class="portalWorkbenchSavedRow">
          <button class="portalWorkbenchMenuRow" type="button" data-workbench-saved="${escapeHtml(workspaceId)}">
            <span data-portal-icon="star"></span><span>${escapeHtml(String(workspace.name || workspaceId))}</span>
          </button>
          <button class="portalWorkbenchSavedDelete" type="button" data-workbench-delete="${escapeHtml(workspaceId)}" aria-label="删除 ${escapeHtml(String(workspace.name || workspaceId))}" data-portal-icon="trash"></button>
        </span>`).join("");
      const panelRows = [...descriptors.values()].filter(descriptor => !descriptor.closable && !descriptor.required && descriptor.canHide).map(descriptor => window.PortalI18n.html`
        <label class="portalWorkbenchPanelToggle${descriptor.required ? " is-required" : ""}">
          <input type="checkbox" data-workbench-panel-toggle="${escapeHtml(descriptor.id)}" ${hidden.has(descriptor.id) ? "" : "checked"} ${descriptor.required ? "disabled" : ""} />
          <span data-portal-icon="${escapeHtml(descriptor.icon)}"></span><span>${escapeHtml(descriptor.layoutTitle || descriptor.title)}</span>
        </label>`).join("");
      const editorRows = (options.editorTypes || []).map(raw => {
        const type = String(raw.type || raw.id || raw || "").trim();
        if (!type) return "";
        const panelPackage = panelPackages.get(type);
        const title = String(raw.title || panelPackage?.presentation.title || type);
        const icon = String(raw.icon || panelPackage?.presentation.icon || "panel");
        return window.PortalI18n.html`<span class="portalWorkbenchEditorCreateRow">
          <span data-portal-icon="${escapeHtml(icon)}"></span><span>${escapeHtml(title)}</span>
          <button class="portalButton is-icon is-quiet" type="button" data-workbench-create-editor="${escapeHtml(type)}" data-open-mode="new-tab" data-portal-icon="copy" aria-label="新建 ${escapeHtml(title)} 标签" title="新建标签"></button>
          <button class="portalButton is-icon is-quiet" type="button" data-workbench-create-editor="${escapeHtml(type)}" data-open-mode="side" data-portal-icon="split-right" aria-label="在侧边新建 ${escapeHtml(title)}" title="在侧边打开"></button>
        </span>`;
      }).join("");
      menu.innerHTML = window.PortalI18n.html`
        <div class="portalWorkbenchMenuHeading"><strong>调整布局</strong></div>
        ${panelRows ? window.PortalI18n.html`<section><h3>面板</h3>${panelRows}</section>` : ""}
        ${presetRows ? window.PortalI18n.html`<section><h3>预设</h3><div class="portalWorkbenchPresets">${presetRows}</div></section>` : ""}
        ${savedRows ? window.PortalI18n.html`<section><h3>我的工作区</h3>${savedRows}</section>` : ""}
        <form class="portalWorkbenchSaveForm">
          <label><span>保存当前布局</span><input class="portalControl" name="name" maxlength="60" placeholder="工作区名称" required /></label>
          <button class="portalButton is-compact is-primary" type="submit">保存</button>
        </form>
        <button class="portalButton is-compact portalWorkbenchReset" type="button" data-workbench-reset>恢复默认布局</button>
        ${editorRows ? window.PortalI18n.html`<details class="portalWorkbenchEditorOptions" ${editorOptionsOpen ? "open" : ""}><summary>新建编辑器</summary>${editorRows}</details>` : ""}`;
      menu.querySelectorAll("[data-workbench-panel-toggle]").forEach(input => {
        input.addEventListener("change", event => {
          executeCommand(`panel.${input.dataset.workbenchPanelToggle}.toggle-visibility`, event, "customizer")
            .finally(renderMenu);
        });
      });
      menu.querySelectorAll("[data-workbench-create-editor]").forEach(button => {
        button.addEventListener("click", async () => {
          const type = String(button.dataset.workbenchCreateEditor || "");
          const resource = options.resolveEditorResource?.(type, controller) || { kind: type };
          button.disabled = true;
          try {
            await openResource({
              type,
              resource: { ...resource, kind: type },
              mode: String(button.dataset.openMode || "new-tab"),
              targetStackId: focusedStackId,
            });
            closeMenu();
          } catch (error) {
            window.PortalUI?.toast?.(window.PortalI18n.text`打开编辑器失败：${error.message || error}`, "error");
          } finally {
            button.disabled = false;
          }
        });
      });
      menu.querySelectorAll("[data-workbench-preset]").forEach(button => {
        button.addEventListener("click", () => applyWorkspace(factoryPresets.get(button.dataset.workbenchPreset)));
      });
      menu.querySelectorAll("[data-workbench-saved]").forEach(button => {
        button.addEventListener("click", () => applyWorkspace(savedWorkspaces[button.dataset.workbenchSaved]));
      });
      menu.querySelectorAll("[data-workbench-delete]").forEach(button => {
        button.addEventListener("click", () => {
          delete savedWorkspaces[button.dataset.workbenchDelete];
          persist();
          renderMenu();
        });
      });
      menu.querySelector(".portalWorkbenchSaveForm")?.addEventListener("submit", event => {
        event.preventDefault();
        const name = String(new FormData(event.currentTarget).get("name") || "").trim();
        if (!name) return;
        const workspaceId = `workspace-${Date.now().toString(36)}`;
        savedWorkspaces[workspaceId] = { id: workspaceId, name, layout: clone(model), hidden: [...hidden], collapsed: [...collapsed] };
        activeWorkspaceName = name;
        persist();
        renderMenu();
      });
      menu.querySelector("[data-workbench-reset]")?.addEventListener("click", () => {
        executeCommand("workbench.reset-layout", null, "customizer");
      });
      global.PortalUIPrimitives?.mountIcons?.(menu);
    }

    function renderHiddenRail() {
      hiddenRail.replaceChildren();
      [...hidden].forEach(panelId => {
        const descriptor = descriptors.get(panelId);
        if (!descriptor) return;
        const button = document.createElement("button");
        button.className = "portalWorkbenchHiddenPanelButton";
        button.type = "button";
        button.dataset.workbenchRestorePanel = descriptor.id;
        button.dataset.portalIcon = descriptor.icon;
        button.setAttribute("aria-label", window.PortalI18n.text`显示 ${descriptor.title}`);
        button.title = window.PortalI18n.text`显示 ${descriptor.title}`;
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.toggle-visibility`, event, "hidden-rail"));
        hiddenRail.append(button);
      });
      hiddenRail.hidden = !hiddenRail.childElementCount;
    }

    function openMenu() {
      if (isLayoutLocked()) return false;
      menu.hidden = false;
      customizeButton.setAttribute("aria-expanded", "true");
      renderMenu();
      outsidePointerHandler = event => {
        if (menu.contains(event.target) || customizeButton.contains(event.target)) return;
        closeMenu();
      };
      document.addEventListener("pointerdown", outsidePointerHandler, true);
      requestAnimationFrame(() => menu.querySelector("input, button")?.focus());
    }

    function closeMenu() {
      menu.hidden = true;
      const editorOptions = menu.querySelector(".portalWorkbenchEditorOptions");
      if (editorOptions) editorOptions.open = false;
      customizeButton.setAttribute("aria-expanded", "false");
      if (outsidePointerHandler) document.removeEventListener("pointerdown", outsidePointerHandler, true);
      outsidePointerHandler = null;
    }

    function render(reason = "render") {
      if (destroyed) return false;
      pendingPanelMounts = [];
      const originalParents = new Map(
        [...descriptors.values()].map(descriptor => [descriptor.id, descriptor.element.parentElement]),
      );
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      if (mobileMode && !renderedMobileMode) {
        const focusedStack = findNode(model, focusedStackId)?.node;
        if (focusedStack?.type === "stack" && descriptors.has(focusedStack.activePanelId)) {
          mobileActivePanelId = focusedStack.activePanelId;
        }
      }
      const mobilePanels = [...descriptors.values()].filter(descriptor => !hidden.has(descriptor.id));
      if (mobileMode && !mobilePanels.some(descriptor => descriptor.id === mobileActivePanelId)) {
        mobileActivePanelId = mobilePanels[0]?.id || "";
      } else if (!descriptors.has(mobileActivePanelId)) {
        mobileActivePanelId = mobilePanels[0]?.id || "";
      }
      const mobilePlacement = mobileMode ? findPanel(model, mobileActivePanelId) : null;
      const renderedModel = mobileMode && mobileActivePanelId && !mobilePlacement
        ? {
          type: "stack",
          id: `${id}-mobile-projection`,
          activePanelId: mobileActivePanelId,
          children: [{ type: "panel", id: `${id}-mobile-panel`, panelId: mobileActivePanelId }],
        }
        : model;
      announceBeforeLayout(reason);
      const topology = JSON.stringify(topologyOf(renderedModel));
      const mobilePanelId = mobileMode ? mobileActivePanelId : "";
      const refreshed = !!renderedModel
        && renderedModelReference === model
        && renderedTopology === topology
        && renderedMobileMode === mobileMode
        && renderedMobilePanelId === mobilePanelId
        && refreshNode(canvas.firstElementChild, renderedModel);
      const previousRoots = [...canvas.children];
      const layoutRoot = refreshed ? canvas.firstElementChild : renderedModel ? renderNode(renderedModel) : renderEmptyState();
      if (!refreshed) canvas.append(layoutRoot);
      const mounts = pendingPanelMounts;
      pendingPanelMounts = [];
      let mountComplete = true;
      mounts.forEach(([parent, element]) => {
        if (parent.isConnected && !mountPanelElement(parent, element)) mountComplete = false;
      });
      mountComplete = reconcilePanelMounts(layoutRoot) && mountComplete;
      if (!mountComplete) {
        const restored = restorePanelMounts(originalParents);
        if (restored && layoutRoot && !previousRoots.includes(layoutRoot)) layoutRoot.remove();
        restoreCommittedWorkbenchState();
        renderedModelReference = model;
        root.dispatchEvent(new CustomEvent("portal:workbench-layout-error", {
          detail: Object.freeze({
            workbenchId: id,
            reason,
            code: restored ? "panel-move-unavailable" : "panel-move-rollback-failed",
          }),
        }));
        console.error(
          `PortalWorkbench ${id} preserved the previous layout because a Panel could not be moved atomically.`,
        );
        return false;
      }
      descriptors.forEach(descriptor => {
        const frame = panelFrameIn(layoutRoot, descriptor.id);
        descriptor.element.hidden = !frame || frame.hidden;
      });
      previousRoots.forEach(previous => { if (previous !== layoutRoot) previous.remove(); });
      renderedModelReference = model;
      renderedTopology = topology;
      renderedMobileMode = mobileMode;
      renderedMobilePanelId = mobilePanelId;
      commitWorkbenchState();
      customizeButton.title = `${customizeLabel} · ${activeWorkspaceName}`;
      renderHiddenRail();
      renderMobileSwitcher(mobileMode, mobilePanels);
      renderMenu();
      global.PortalUIPrimitives?.mountIcons?.(stage);
      global.PortalUIPrimitives?.mountIcons?.(toolbar);
      syncLayoutLock();
      reconcilePanelVisibility();
      if (!(mobileMode && ["focus-panel", "responsive-mode"].includes(reason))) persist();
      announceChange(reason);
      return true;
    }

    function renderEmptyState() {
      const empty = document.createElement("section");
      empty.className = "portalWorkbenchEmpty";
      empty.setAttribute("aria-label", window.PortalI18n.t("没有打开的 Panel"));
      const title = document.createElement("strong");
      title.textContent = window.PortalI18n.t("暂无打开的 Panel");
      const hint = document.createElement("span");
      hint.textContent = window.PortalI18n.t("选择一个 Panel 继续工作。");
      const actions = document.createElement("div");
      actions.className = "portalWorkbenchEmptyActions";
      [...hidden].forEach(panelId => {
        const descriptor = descriptors.get(panelId);
        if (!descriptor) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton is-compact";
        button.dataset.portalIcon = descriptor.icon;
        button.dataset.workbenchEmptyRestore = descriptor.id;
        button.textContent = window.PortalI18n.text`打开 ${descriptor.title}`;
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.toggle-visibility`, event, "empty-state"));
        actions.append(button);
      });
      empty.append(title, hint, actions);
      return empty;
    }

    function renderMobileSwitcher(mobileMode, mobilePanels) {
      mobileSwitcher.replaceChildren();
      mobilePanels.forEach(descriptor => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `portalWorkbenchMobilePanel${descriptor.id === mobileActivePanelId ? " is-active" : ""}`;
        button.dataset.portalIcon = descriptor.icon;
        button.dataset.portalWorkbenchMobilePanel = descriptor.id;
        button.setAttribute("aria-label", descriptor.title);
        button.setAttribute("aria-pressed", String(descriptor.id === mobileActivePanelId));
        button.title = descriptor.title;
        const label = document.createElement("span");
        label.textContent = descriptor.title;
        button.append(label);
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.focus`, event, "mobile-switcher"));
        mobileSwitcher.append(button);
      });
      mobileSwitcher.hidden = !mobileMode || !mobilePanels.length;
    }

    function focusPanel(panelId) {
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      if (isLayoutLocked() && !canFocusFixedPanel(descriptor.id)) return false;
      mobileActivePanelId = descriptor.id;
      if (!descriptor.openSource) activeOpenTargetPanelId = descriptor.id;
      if (hidden.has(descriptor.id)) return showPanel(descriptor.id);
      if (global.PortalResponsive?.isMobile?.()) {
        render("focus-panel");
        return true;
      }
      collapsed.delete(descriptor.id);
      const placement = findPanel(model, descriptor.id);
      if (placement?.stack) {
        placement.stack.activePanelId = descriptor.id;
        focusedStackId = placement.stack.id;
      }
      render("focus-panel");
      return true;
    }

    function collapsePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.canCollapse || hidden.has(descriptor.id)) return false;
      collapsed.add(descriptor.id);
      render("collapse-panel");
      return true;
    }

    function expandPanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor || hidden.has(descriptor.id)) return false;
      collapsed.delete(descriptor.id);
      const placement = findPanel(model, descriptor.id);
      if (placement?.stack) placement.stack.activePanelId = descriptor.id;
      render("expand-panel");
      return true;
    }

    function toggleCollapsed(panelId) {
      return collapsed.has(String(panelId)) ? expandPanel(panelId) : collapsePanel(panelId);
    }

    function hidePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.canHide) return false;
      const placement = panelPlacement(model, descriptor.id);
      if (placement) placements[descriptor.id] = placement;
      model = removePanelFromNode(clone(model), descriptor.id);
      hidden.add(descriptor.id);
      collapsed.delete(descriptor.id);
      normalizeCurrent();
      render("hide-panel");
      return true;
    }

    function closePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      if (!descriptor.closable) return hidePanel(descriptor.id);
      const close = approved => {
        if (approved === false || destroyed || isLayoutLocked() || descriptors.get(descriptor.id) !== descriptor) return false;
        model = removePanelFromNode(clone(model), descriptor.id);
        hidden.delete(descriptor.id);
        collapsed.delete(descriptor.id);
        delete placements[descriptor.id];
        const wasVisible = activeRuntimePanelIds.delete(descriptor.id);
        unregisterPanelCommands(descriptor.id);
        descriptors.delete(descriptor.id);
        panelIds.delete(descriptor.id);
        if (activeOpenTargetPanelId === descriptor.id) activeOpenTargetPanelId = "";
        if (lastOpenTargetByType.get(descriptor.type) === descriptor.id) lastOpenTargetByType.delete(descriptor.type);
        if (wasVisible) {
          callPanelLifecycle(descriptor, "hide", runtimeContext);
          descriptor.onHide?.({ workbench: controller, panel: descriptor });
        }
        callPanelLifecycle(descriptor, "disconnect", runtimeContext);
        if (descriptor.ownsRuntime) callPanelLifecycle(descriptor, "destroy", runtimeContext);
        descriptor.contextScope.dispose();
        descriptor.element.remove();
        normalizeCurrent();
        render("close-editor");
        return true;
      };
      const decision = descriptor.runtime?.canClose?.();
      return decision && typeof decision.then === "function"
        ? Promise.resolve(decision).then(close)
        : close(decision);
    }

    function showPanel(panelId, optionsValue = {}) {
      if (isLayoutLocked()) return focusPanel(panelId);
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      hidden.delete(descriptor.id);
      collapsed.delete(descriptor.id);
      model = removePanelFromNode(clone(model), descriptor.id);
      const remembered = placements[descriptor.id] || {};
      const targetStackId = String(optionsValue.stackId || remembered.stackId || focusedStackId);
      const zone = String(optionsValue.zone || remembered.zone || "center");
      const targetStack = findNode(model, targetStackId)?.node;
      const rememberedSplit = !optionsValue.stackId && !optionsValue.zone
        ? findNode(model, remembered.splitId)?.node
        : null;
      if (rememberedSplit?.type === "split") {
        const insertAt = Math.max(0, Math.min(
          rememberedSplit.children.length,
          Number.isInteger(remembered.index) ? remembered.index : rememberedSplit.children.length,
        ));
        rememberedSplit.children.splice(insertAt, 0, stackNode([descriptor.id]));
        rememberedSplit.weights.splice(insertAt, 0, Number(remembered.weight) || 1);
      } else if (targetStack?.type === "stack") {
        if (zone === "center") {
          targetStack.children.push(panelNode(descriptor.id));
          targetStack.activePanelId = descriptor.id;
        } else {
          const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
          const sourceStack = stackNode([descriptor.id]);
          const sourceWeight = Number(remembered.weight) || 1;
          const targetWeight = Number(remembered.siblingWeight) || 1;
          const replacement = ["left", "top"].includes(zone)
            ? splitNode(orientation, [sourceStack, targetStack], [sourceWeight, targetWeight])
            : splitNode(orientation, [targetStack, sourceStack], [targetWeight, sourceWeight]);
          model = replaceNode(model, targetStack.id, replacement);
        }
      } else if (model) {
        const sourceStack = stackNode([descriptor.id]);
        const existingMinimum = minimumForNode(model, "x", descriptors, collapsed);
        const sourceMinimum = minimumForNode(sourceStack, "x", descriptors, collapsed);
        const availableWidth = canvas.clientWidth;
        const receivingStack = firstStack(model.children?.at(-1)) || firstStack(model);
        if (
          receivingStack && availableWidth > 0
          && existingMinimum + sourceMinimum + SPLIT_HANDLE_SIZE > availableWidth
        ) {
          receivingStack.children.push(panelNode(descriptor.id));
          receivingStack.activePanelId = descriptor.id;
          focusedStackId = receivingStack.id;
        } else {
          model = splitNode("horizontal", [model, sourceStack], [existingMinimum, sourceMinimum]);
        }
      } else {
        model = stackNode([descriptor.id]);
      }
      render("show-panel");
      return true;
    }

    function addPanel(rawDescriptor = {}, placement = {}) {
      // A hidden panel only joins the hidden rail, so it leaves a fixed layout as it is.
      const hiddenPlacement = placement.hidden === true;
      if (isLayoutLocked() && !hiddenPlacement) throw new Error("布局已固定，请先解锁布局再添加面板。");
      const rawId = String(rawDescriptor.id || "").trim();
      if (!rawId || descriptors.has(rawId)) throw new Error(`PortalWorkbench editor instance already exists: ${rawId}`);
      const descriptor = descriptorOf(rawDescriptor, root, id, runtimeContext);
      descriptors.set(descriptor.id, descriptor);
      if (controller) registerPanelCommands(descriptor);
      panelIds.add(descriptor.id);
      hidden.delete(descriptor.id);
      collapsed.delete(descriptor.id);
      callPanelLifecycle(descriptor, "connect", runtimeContext);
      callPanelLifecycle(descriptor, "setContext", runtimeContext.context, runtimeContext);
      if (hiddenPlacement) {
        hidden.add(descriptor.id);
        render("create-editor");
        return descriptor;
      }
      const targetStackId = String(placement.stackId || focusedStackId || "");
      const targetStack = findNode(model, targetStackId)?.node;
      const zone = ["left", "right", "top", "bottom", "center"].includes(String(placement.zone || ""))
        ? String(placement.zone)
        : "center";
      if (targetStack?.type === "stack" && zone === "center") {
        targetStack.children.push(panelNode(descriptor.id));
        targetStack.activePanelId = descriptor.id;
      } else if (targetStack?.type === "stack") {
        const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
        const sourceStack = stackNode([descriptor.id]);
        const replacement = ["left", "top"].includes(zone)
          ? splitNode(orientation, [sourceStack, targetStack])
          : splitNode(orientation, [targetStack, sourceStack]);
        model = replaceNode(model, targetStack.id, replacement);
      } else if (model) {
        model = splitNode("horizontal", [model, stackNode([descriptor.id])]);
      } else {
        model = stackNode([descriptor.id]);
      }
      activeOpenTargetPanelId = descriptor.openSource ? activeOpenTargetPanelId : descriptor.id;
      focusedStackId = findPanel(model, descriptor.id)?.stack?.id || focusedStackId;
      if (!descriptor.openSource) lastOpenTargetByType.set(descriptor.type, descriptor.id);
      render("create-editor");
      return descriptor;
    }

    function canFocusFixedPanel(panelId) {
      const stack = findPanel(model, panelId)?.stack;
      return !hidden.has(panelId) && !collapsed.has(panelId) && !collapsed.has(stack?.activePanelId);
    }

    async function reusePanel(panelId, intent) {
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) throw new Error(`PortalWorkbench editor instance not found: ${panelId}`);
      if (isLayoutLocked() && !canFocusFixedPanel(descriptor.id)) {
        throw new Error("布局已固定，请先解锁布局再显示此面板。");
      }
      const resource = intent.resource || {};
      let result;
      if (descriptor.openResource) result = await descriptor.openResource(resource, intent, descriptor.runtime);
      else if (typeof descriptor.runtime?.openResource === "function") result = await descriptor.runtime.openResource(resource, intent);
      else {
        throw new Error(`PortalWorkbench editor ${descriptor.id} cannot open ${intent.type}.`);
      }
      if (result && typeof result === "object" && (result.accepted === false || result.stale === true)) {
        return result;
      }
      descriptor.binding = clone(resource);
      const nextTitle = String(options.editorTitle?.(intent.type, resource, descriptor) || resource.title || resource.label || "").trim();
      if (nextTitle) descriptor.title = nextTitle;
      activeOpenTargetPanelId = descriptor.openSource ? activeOpenTargetPanelId : descriptor.id;
      const placement = findPanel(model, descriptor.id);
      if (placement?.stack) {
        placement.stack.activePanelId = descriptor.id;
        focusedStackId = placement.stack.id;
      }
      lastOpenTargetByType.set(descriptor.type, descriptor.id);
      render("reuse-editor");
      return result;
    }

    async function createPanelForIntent(intent, placement = {}) {
      if (isLayoutLocked()) throw new Error("布局已固定，请先解锁布局再打开新面板。");
      const singleton = [...descriptors.values()].find(descriptor => (
        descriptor.singleton === true
        && descriptor.type === String(intent.type || "")
      ));
      if (singleton) {
        await reusePanel(singleton.id, intent);
        return { id: singleton.id, instanceId: singleton.id, runtime: singleton.runtime };
      }
      const instanceId = String(intent.instanceId || `${intent.type}-${Date.now().toString(36)}-${uid("editor")}`);
      const raw = options.createEditorDescriptor?.({
        instanceId,
        type: intent.type,
        // Runtime opening payloads may carry loaders, document sessions, or
        // other callable adapters.  They belong to the new panel's runtime
        // construction path and must not pass through the persistence clone.
        // The descriptor binding below remains the serializable locator that
        // is safe to store and restore.
        resource: intent.resource,
        intent,
        workbench: controller,
      });
      if (!raw) throw new Error(`PortalWorkbench cannot create editor type: ${intent.type}`);
      const descriptor = addPanel({
        ...raw,
        id: instanceId,
        type: String(raw.type || intent.type),
        binding: clone(intent.resource),
        closable: true,
        restoreOnReload: true,
      }, placement);
      return { id: descriptor.id, instanceId: descriptor.id, runtime: descriptor.runtime };
    }

    function editorOpenState() {
      return {
        activeTargetInstanceId: activeOpenTargetPanelId || lastOpenTargetByType.values().next().value || "",
        activeTargetStackId: findPanel(model, activeOpenTargetPanelId)?.stack?.id || focusedStackId,
        focusedStackId,
        instances: [...descriptors.values()].map(descriptor => ({
          id: descriptor.id,
          type: descriptor.type,
          stackId: findPanel(model, descriptor.id)?.stack?.id || "",
          reusable: descriptor.reusable !== false,
          singleton: descriptor.singleton === true,
          locked: descriptor.locked === true,
          dirty: Boolean(descriptor.runtime?.snapshot?.()?.dirty),
        })),
      };
    }

    function openResource(intent = {}) {
      if (!editorOpenService) return Promise.reject(new Error("PortalWorkbench EditorOpenService is not ready."));
      return editorOpenService.open(intent);
    }

    function togglePanel(panelId) {
      return hidden.has(String(panelId)) ? showPanel(panelId) : hidePanel(panelId);
    }

    function setPanelTitle(panelId, title) {
      const descriptor = descriptors.get(String(panelId || ""));
      const nextTitle = String(title || "").trim();
      if (!descriptor || !nextTitle || descriptor.title === nextTitle) return false;
      descriptor.title = nextTitle;
      render("set-panel-title");
      return true;
    }

    function applyWorkspace(workspace) {
      if (isLayoutLocked()) return false;
      if (!workspace) return false;
      [...panelIds].forEach(panelId => {
        const placement = panelPlacement(model, panelId);
        if (placement) placements[panelId] = placement;
      });
      hidden = hiddenPanelsForLayout(
        (workspace.hidden || []).filter(panelId => panelIds.has(panelId)),
        collectPanelIds(workspace.layout),
      );
      collapsed = new Set((workspace.collapsed || []).filter(panelId => panelIds.has(panelId) && !hidden.has(panelId)));
      model = normalizeNode(clone(workspace.layout), panelIds);
      activeWorkspaceName = String(workspace.name || workspace.id || options.label || "工作区");
      normalizeCurrent();
      if (!render("apply-workspace")) return false;
      closeMenu();
      options.onWorkspaceApplied?.(Object.freeze({
        id: String(workspace.id || ""),
        name: activeWorkspaceName,
      }), controller);
      return true;
    }

    controller = Object.freeze({
      id,
      root,
      descriptors,
      hidePanel,
      collapsePanel,
      expandPanel,
      showPanel,
      addPanel,
      setEditorTypes(types) {
        if (!Array.isArray(types)) throw new TypeError("Editor types must be an array.");
        options.editorTypes = types.map(type => typeof type === "object" ? { ...type } : type);
        if (!menu.hidden) renderMenu();
      },
      closePanel,
      openResource,
      setPanelTitle,
      togglePanel,
      toggleCollapsed,
      focusPanel,
      // Outer workspaces own visibility; their selection is not a saved layout
      // change. Keep panel nodes and drafts while suspending their lifecycle.
      setVisible(visible) {
        if (destroyed) return false;
        hostVisible = Boolean(visible);
        if (chrome) chrome.hidden = !hostVisible;
        if (!hostVisible) closeMenu();
        reconcilePanelVisibility();
        return true;
      },
      applyWorkspace,
      getLayout: () => clone(model),
      getState: stateSnapshot,
      isLayoutLocked,
      setLayoutLocked,
      getPanel: panelId => descriptors.get(String(panelId))?.runtime || null,
      getPanelsByType: type => [...descriptors.values()]
        .filter(descriptor => descriptor.type === String(type || ""))
        .map(descriptor => descriptor.runtime),
      getActiveOpenTarget: () => activeOpenTargetPanelId,
      setActiveOpenTarget(panelId) {
        const descriptor = descriptors.get(String(panelId || ""));
        if (!descriptor || descriptor.openSource) return false;
        activeOpenTargetPanelId = descriptor.id;
        lastOpenTargetByType.set(descriptor.type, descriptor.id);
        render("set-open-target");
        return true;
      },
      invokePanel(panelId, method, ...args) {
        const descriptor = descriptors.get(String(panelId));
        if (!descriptor) return undefined;
        return callPanelLifecycle(descriptor, String(method || ""), ...args, runtimeContext);
      },
      setContext(next = {}) {
        return contextStore.replace(next, { source: "workbench", reason: "replace-context" }).values;
      },
      patchContext(next = {}, meta = {}) { return contextStore.patch(next, meta).values; },
      setSelection(channel, value, meta = {}) { return contextStore.select(channel, value, meta); },
      getSelection: channel => contextStore.get(channel),
      panelCapabilities(panelId) {
        return descriptors.get(String(panelId))?.capabilities || Object.freeze([]);
      },
      hasPanelCapability(panelId, capability) {
        return Boolean(descriptors.get(String(panelId))?.capabilities.includes(String(capability || "")));
      },
      contextStore,
      commandScope,
      isPanelVisible: panelId => !hidden.has(String(panelId)),
      isPanelCollapsed: panelId => collapsed.has(String(panelId)),
      isPanelExpanded: panelId => !hidden.has(String(panelId)) && !collapsed.has(String(panelId)),
      openCustomizer: openMenu,
      reset: () => applyWorkspace(defaultPreset || { name: options.label || window.PortalI18n.t("默认工作区"), layout: defaultLayout, hidden: [] }),
      destroy() {
        if (destroyed) return;
        persist();
        destroyed = true;
        layoutLockSynchronizers.delete(syncLayoutLock);
        cancelLayoutLockAnimations();
        reducedLayoutMotion?.removeEventListener("change", onLayoutMotionPreferenceChange);
        window.removeEventListener("pagehide", persist);
        window.removeEventListener("portal:identity-changed", restoreIdentityLayout);
        closeMenu();
        activeRuntimePanelIds.forEach(panelId => {
          callPanelLifecycle(descriptors.get(panelId), "hide", runtimeContext);
        });
        activeRuntimePanelIds.clear();
        descriptors.forEach(descriptor => {
          callPanelLifecycle(descriptor, "disconnect", runtimeContext);
          callPanelLifecycle(descriptor, "destroy", runtimeContext);
          descriptor.contextScope.dispose();
        });
        unsubscribeWorkbenchContext();
        commandScope.dispose();
        if (ownsContextStore) contextStore.dispose();
        chrome?.remove();
        document.body.classList.remove("draggingWorkbenchTab");
        stage.remove();
        unsubscribeResponsive?.();
        root.classList.remove("portalWorkbench");
        delete root.dataset.portalWorkbenchId;
        delete root.dataset.portalWorkbenchLayoutLocked;
        if (viewportFit || viewportShell) {
          viewportObserver?.disconnect();
          cancelAnimationFrame(viewportResizeFrame);
          cancelAnimationFrame(viewportStabilizeFrame);
          window.removeEventListener("resize", syncViewportHeight);
          window.visualViewport?.removeEventListener("resize", syncViewportHeight);
          delete root.dataset.portalWorkbenchFitViewport;
          root.style.removeProperty("--portal-workbench-viewport-height");
        }
        instances.delete(id);
      },
    });

    commandScope.register({
      id: "workbench.reset-layout",
      title: window.PortalI18n.t("恢复默认布局"),
      category: "Workbench",
      icon: "refresh",
      enabled: () => !isLayoutLocked(),
      execute: () => controller.reset(),
    });
    commandScope.register({
      id: "workbench.toggle-layout-lock",
      title: () => isLayoutLocked() ? window.PortalI18n.t("解锁") : window.PortalI18n.t("固定"),
      category: "Workbench",
      icon: "lock",
      enabled: () => !inheritedLayoutLock(),
      execute: () => setLayoutLocked(!layoutLocked),
    });
    function unregisterPanelCommands(panelId) {
      (panelCommandDisposers.get(String(panelId || "")) || []).forEach(dispose => dispose());
      panelCommandDisposers.delete(String(panelId || ""));
    }

    function registerPanelCommands(descriptor) {
      unregisterPanelCommands(descriptor.id);
      const prefix = `panel.${descriptor.id}`;
      const disposers = commandScope.registerMany([
        {
          id: `${prefix}.toggle-visibility`,
          title: () => `${hidden.has(descriptor.id) ? window.PortalI18n.t("显示") : window.PortalI18n.t("隐藏")} ${descriptor.title}`,
          category: "Panel",
          icon: descriptor.icon,
          enabled: () => !isLayoutLocked() && descriptor.canHide,
          execute: () => controller.togglePanel(descriptor.id),
        },
        {
          id: `${prefix}.toggle-collapsed`,
          title: window.PortalI18n.text`折叠或展开 ${descriptor.title}`,
          category: "Panel",
          icon: "chevron-down",
          enabled: () => !isLayoutLocked() && descriptor.canCollapse && !hidden.has(descriptor.id),
          execute: () => controller.toggleCollapsed(descriptor.id),
        },
        {
          id: `${prefix}.focus`,
          title: window.PortalI18n.text`聚焦 ${descriptor.title}`,
          category: "Panel",
          icon: descriptor.icon,
          enabled: () => !hidden.has(descriptor.id) && (!isLayoutLocked() || canFocusFixedPanel(descriptor.id)),
          execute: () => controller.focusPanel(descriptor.id),
        },
      ]);
      if (descriptor.closable) {
        disposers.push(commandScope.register({
          id: `${prefix}.close`,
          title: window.PortalI18n.text`关闭 ${descriptor.title}`,
          category: "Panel",
          icon: "close",
          enabled: () => !isLayoutLocked(),
          execute: () => controller.closePanel(descriptor.id),
        }));
      }
      descriptor.panelPackage.commands.forEach(contribution => {
        disposers.push(commandScope.register({
          ...contribution,
          id: `${prefix}.${String(contribution.id || "").trim()}`,
          execute: invocation => contribution.execute({
            ...invocation,
            context: descriptor.contextScope.values(invocation.context),
            panel: descriptor.runtime,
            panelId: descriptor.id,
            workbench: controller,
          }),
        }));
      });
      panelCommandDisposers.set(descriptor.id, disposers);
    }
    descriptors.forEach(registerPanelCommands);

    const unsubscribeResponsive = global.PortalResponsive?.subscribe?.(() => {
      render("responsive-mode");
    }, { immediate: false }) || null;

    if (!global.PortalEditorOpenService?.create) {
      throw new Error("PortalWorkbench requires PortalEditorOpenService.");
    }
    editorOpenService = global.PortalEditorOpenService.create({
      state: editorOpenState,
      reuseInstance: reusePanel,
      createInstance: createPanelForIntent,
    });

    function restorePanelState(descriptor, state) {
      try {
        restorePanelStateValue(descriptor, state);
      } catch (error) {
        console.error(`PortalWorkbench ${id} panel ${descriptor.id} restore failed`, error);
      }
    }

    function restorePanelStateValue(descriptor, state) {
      if (initialPanelStates.has(descriptor.id)) {
        const initialState = initialPanelStates.get(descriptor.id);
        initialPanelStates.delete(descriptor.id);
        callPanelLifecycle(descriptor, "restore", clone(initialState), runtimeContext);
        return;
      }
      const storedPanelState = state.schemaVersion === SCHEMA_VERSION
        ? state.panelStates?.[descriptor.id]
        : undefined;
      if (
        descriptor.capabilities.includes("restore")
        &&
        storedPanelState?.packageType === descriptor.panelPackage.type
        && storedPanelState.schemaVersion === descriptor.panelPackage.persistence.schemaVersion
      ) {
        const persistence = descriptor.panelPackage.persistence;
        const panelState = clone(storedPanelState.value);
        if (panelState !== undefined) {
          const restored = persistence.restore
            ? persistence.restore(panelState, { descriptor, runtime: descriptor.runtime, context: descriptor.contextScope.context })
            : panelState;
          if (restored !== undefined) {
            callPanelLifecycle(descriptor, "restore", clone(restored), runtimeContext);
          }
        }
      }
    }

    descriptors.forEach(descriptor => {
      callPanelLifecycle(descriptor, "connect", runtimeContext);
      restorePanelState(descriptor, stored);
      callPanelLifecycle(descriptor, "setContext", runtimeContext.context, runtimeContext);
    });

    customizeButton.addEventListener("click", () => menu.hidden ? openMenu() : closeMenu());
    lockButton.addEventListener("click", event => executeCommand("workbench.toggle-layout-lock", event, "workbench-toolbar"));
    menu.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeMenu();
      customizeButton.focus();
    });
    const initialized = render("initialize");
    window.addEventListener("pagehide", persist);
    if (!options.store) window.addEventListener("portal:identity-changed", restoreIdentityLayout);
    syncViewportHeight();
    viewportStabilizeFrame = requestAnimationFrame(syncViewportHeight);
    instances.set(id, controller);
    layoutLockSynchronizers.forEach(sync => sync());
    if (initialized) global.PortalPageBoot?.ready(root);
    return controller;
  }

  function registerPanelPackage(descriptor = {}) {
    const panelPackage = normalizePanelPackage(descriptor);
    if (panelPackages.has(panelPackage.type)) {
      throw new Error(`PortalWorkbench Panel Package is already registered: ${panelPackage.type}`);
    }
    panelPackages.set(panelPackage.type, panelPackage);
    return panelPackage;
  }

  function createPanel(type, options = {}) {
    const panelPackage = panelPackages.get(String(type || "").trim());
    if (!panelPackage) throw new Error(`PortalWorkbench Panel Package is not registered: ${type}`);
    return panelPackage.create({ ...options, panelPackage });
  }

  global.PortalWorkbench = Object.freeze({
    create,
    get: id => instances.get(String(id)) || null,
    layout,
    storage,
    model: Object.freeze({ panel: panelNode, split: splitNode, stack: stackNode }),
    createPanel,
    createActivityRail,
    registerPanelPackage,
    registeredPanelPackages: () => [...panelPackages.values()],
  });
})(window);
