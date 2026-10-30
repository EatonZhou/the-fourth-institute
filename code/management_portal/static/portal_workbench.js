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

  // createActivityRail builds a rail of views and recent items. Its parts
  // live in module scripts that load before it runs:
  //   portal_workbench_activity_rail_items.js  the recent items
  //   portal_workbench_activity_rail_views.js  the views, their order and
  //     drawing the rail
  function createActivityRail(options = {}) {
    const root = elementOf(options.root);
    if (!root) throw new TypeError("PortalWorkbench.createActivityRail requires root.");
    const id = String(options.id || "").trim();
    const storage = options.store || (id
      ? createStore({ storageKey: `portal.activity-rail.${id}.v1` })
      : null);
    const stored = storage?.read?.() || {};

    // The state the parts share: the views and items shown, the order of the
    // views, the active view and the controller.
    const railState = {
      views: null, items: [], order: null, activeView: null, controller: null,
    };
    railState.views = Array.isArray(options.views) ? options.views.slice() : [];
    const itemsAction = options.itemsAction && typeof options.itemsAction === "object"
      ? options.itemsAction
      : null;
    railState.order = Array.isArray(options.order)
      ? options.order.map(String)
      : (Array.isArray(stored.order) ? stored.order.map(String) : []);
    railState.activeView = String(options.activeView || "");
    root.classList.add("portalActivityRail", "activityBar");
    root.setAttribute("aria-label", options.label || window.PortalI18n.t("Workspace panels"));

    const { renderItems } = workbenchPart("PortalWorkbenchActivityRailItems").createActivityRailItems({
      escapeHtml, options, railState, root,
    });
    const {
      normalizeOrder, persistOrder, render,
    } = workbenchPart("PortalWorkbenchActivityRailViews").createActivityRailViews({
      escapeHtml, itemsAction, options, railState, renderItems, root, storage,
    });
    railState.controller = Object.freeze({
      root,
      capacity() {
        const itemsRoot = root.querySelector("[data-activity-rail-items]");
        const height = Number(itemsRoot?.clientHeight || root.clientHeight || 0);
        const limit = Math.max(1, Math.floor(Number(options.itemLimit || 24)));
        return height ? Math.min(limit, Math.max(1, Math.floor((height + 7) / 39))) : limit;
      },
      render,
      setActiveView(value) { railState.activeView = String(value || ""); return render(); },
      setItems(value) { railState.items = Array.isArray(value) ? value.slice() : []; return renderItems(); },
      getOrder: () => normalizeOrder(railState.order),
      setOrder(value, meta = {}) {
        railState.order = normalizeOrder(Array.isArray(value) ? value.map(String) : []);
        if (meta.persist === true) persistOrder();
        return render();
      },
      setViews(value) {
        railState.views = Array.isArray(value) ? value.slice() : [];
        railState.order = normalizeOrder(railState.order);
        return render();
      },
      destroy() { root.replaceChildren(); root.classList.remove("portalActivityRail", "activityBar"); },
    });
    railState.order = normalizeOrder(railState.order);
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

  // create builds a workbench. Its parts live in module scripts that load
  // before create runs; workbenchPart(name) returns what a script registers:
  //   portal_workbench_storage.js  the store of the signed-in user, the
  //     state read from it and the Panel states the page passes
  //   portal_workbench_runtime_context.js  the context store, the command
  //     scope, the runtime context Panels get and calling a Panel runtime
  //   portal_workbench_layout_model.js  Panel descriptors, presets, the layout
  //     model with its hidden and collapsed Panels, focus and open targets,
  //     and switching to another user's stored layout
  //   portal_workbench_viewport.js  fitting the workbench to the viewport
  //   portal_workbench_chrome.js  the stage, toolbar, menu, canvas, mobile
  //     switcher and parking elements
  //   portal_workbench_layout_lock.js  the layout lock and the chrome that
  //     follows it
  //   portal_workbench_panel_runtimes.js  context changes, showing and hiding
  //     Panel runtimes, and each Panel's commands
  //   portal_workbench_snapshot.js  the snapshot the store keeps and restoring
  //     Panel states
  //   portal_workbench_panel_mounts.js  moving Panel elements into their
  //     frames without losing live state
  //   portal_workbench_tab_drag.js  dragging tabs and dropping Panels
  //   portal_workbench_layout_view.js  drawing stacks, tabs and splits and
  //     refreshing them in place
  //   portal_workbench_customizer.js  the layout menu and the hidden-Panel rail
  //   portal_workbench_render.js  rendering a layout, rolling back one that
  //     cannot be committed, and announcing changes
  //   portal_workbench_panel_actions.js  focusing, collapsing, hiding,
  //     closing, showing and adding Panels, and applying workspaces
  //   portal_workbench_editor_opening.js  opening resources in editor Panels
  // create holds the state the parts share, creates the parts in that order,
  // builds the controller and owns teardown; a part that calls a function of a
  // part created after it gets an arrow forwarding the call.
  function workbenchPart(name) {
    const part = global[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

  function create(options = {}) {
    const id = String(options.id || "").trim();
    const root = elementOf(options.root);
    if (!id || !root) throw new TypeError("PortalWorkbench.create requires id and root.");
    instances.get(id)?.destroy?.();

    // The state the parts share: the store and its user, whether this
    // workbench locks its layout, the controller, the layout model with its
    // hidden and collapsed Panels, whether the host shows the workbench, Panel
    // moves waiting for their frame to connect, the focused stack, open
    // targets, the workspace name, whether the workbench is destroyed and the
    // Panels whose runtimes are shown. The part that computes a field's first
    // value sets it; each part keeps the rest of its state to itself.
    const shellState = {
      storageUser: null, storage: null, layoutLocked: null, controller: null, hidden: null, collapsed: null,
      model: null, hostVisible: null, pendingPanelMounts: [], focusedStackId: "", activeOpenTargetPanelId: null,
      mobileActivePanelId: null, activeWorkspaceName: null, destroyed: false, activeRuntimePanelIds: new Set(),
    };

    const { initialPanelStates, readStoredState, stored } = workbenchPart("PortalWorkbenchStorage").createStorage({
      SCHEMA_VERSION, defaultWorkbenchStore, id, options, shellState,
    });

    const {
      callPanelLifecycle, commandScope, contextStore, executeCommand, ownsContextStore, runtimeContext,
      unavailableBindings,
    } = workbenchPart("PortalWorkbenchRuntimeContext").createRuntimeContext({
      id, options, persist: (...args) => persist(...args), root, shellState,
    });

    const {
      defaultLayout, defaultPreset, descriptors, factoryPresets, hiddenPanelsForLayout, lastOpenTargetByType,
      normalizeCurrent, panelIds, placements, restoreIdentityLayout, savedWorkspaces,
    } = workbenchPart("PortalWorkbenchLayoutModel").createLayoutModel({
      SCHEMA_VERSION, clone, collectPanelIds, defaultWorkbenchStore, descriptorOf, id, layoutLockSynchronizers,
      normalizeLayout, normalizeNode, options, panelPackages, persist: (...args) => persist(...args), readStoredState,
      render: (...args) => render(...args), restorePanelState: (...args) => restorePanelState(...args), root,
      runtimeContext, shellState, stackNode, stored,
    });

    root.classList.add("portalWorkbench");
    root.dataset.portalWorkbenchId = id;
    const { destroyViewport, followViewport } = workbenchPart("PortalWorkbenchViewport").createViewport({
      options, root, shellState,
    });

    const {
      canvas, chrome, customizeButton, customizeLabel, fixedSwitchers, hiddenRail, lockButton, menu, mobileSwitcher,
      parking, stage, toolbar,
    } = workbenchPart("PortalWorkbenchChrome").createChrome({ elementOf, escapeHtml, id, options, root, shellState });

    const {
      destroyLayoutLock, inheritedLayoutLock, isLayoutLocked, setLayoutLocked, syncLayoutLock,
    } = workbenchPart("PortalWorkbenchLayoutLock").createLayoutLock({
      announceBeforeLayout: (...args) => announceBeforeLayout(...args),
      announceChange: (...args) => announceChange(...args), canvas, closeMenu: (...args) => closeMenu(...args),
      customizeButton, descriptors, fixedSwitchers, focusPanel: (...args) => focusPanel(...args), instances,
      layoutLockSynchronizers, lockButton, persist: (...args) => persist(...args), root, shellState, stage, toolbar,
    });

    const {
      destroyPanelRuntimes, reconcilePanelVisibility, registerPanelCommands, unregisterPanelCommands,
    } = workbenchPart("PortalWorkbenchPanelRuntimes").createPanelRuntimes({
      callPanelLifecycle, canFocusFixedPanel: (...args) => canFocusFixedPanel(...args), commandScope, contextStore,
      descriptors, isLayoutLocked, root, runtimeContext, shellState,
    });

    const { persist, restorePanelState, stateSnapshot } = workbenchPart("PortalWorkbenchSnapshot").createSnapshot({
      SCHEMA_VERSION, callPanelLifecycle, clone, descriptors, id, initialPanelStates, options, panelIds, placements,
      runtimeContext, savedWorkspaces, shellState, unavailableBindings,
    });

    const {
      mountPanelElement, panelFrameIn, reconcilePanelMounts, refreshPanelFrame, renderPanelFrame, restorePanelMounts,
    } = workbenchPart("PortalWorkbenchPanelMounts").createPanelMounts({ descriptors, parking, root, shellState });

    const {
      bindSingleTabReveal, bindStackDrop, bindTabDrag, bindTabGroupDrop,
    } = workbenchPart("PortalWorkbenchTabDrag").createTabDrag({
      canvas, clone, descriptors, findNode, isLayoutLocked, normalizeCurrent, panelNode, removePanelFromNode,
      render: (...args) => render(...args), replaceNode, shellState, splitNode, stackNode,
    });

    const { refreshNode, renderNode, topologyOf } = workbenchPart("PortalWorkbenchLayoutView").createLayoutView({
      announceChange: (...args) => announceChange(...args), bindKeyboardResize, bindPointerResize,
      bindSingleTabReveal, bindStackDrop, bindTabDrag, bindTabGroupDrop, clamp, compactExtentForNode, descriptors,
      executeCommand, expandPanel: (...args) => expandPanel(...args), isLayoutLocked, lastOpenTargetByType,
      minimumForNode, persist, refreshPanelFrame, render: (...args) => render(...args), renderPanelFrame, shellState,
      splitChildMinimumCss,
    });

    const {
      closeMenu, openMenu, renderHiddenRail, renderMenu,
    } = workbenchPart("PortalWorkbenchCustomizer").createCustomizer({
      applyWorkspace: (...args) => applyWorkspace(...args), clone, customizeButton, descriptors, escapeHtml,
      executeCommand, factoryPresets, hiddenRail, isLayoutLocked, menu,
      openResource: (...args) => openResource(...args), options, panelPackages, persist, savedWorkspaces, shellState,
    });

    const { announceBeforeLayout, announceChange, render } = workbenchPart("PortalWorkbenchRender").createRender({
      canvas, clone, customizeButton, customizeLabel, descriptors, executeCommand, findNode, findPanel, id,
      lastOpenTargetByType, mobileSwitcher, mountPanelElement, options, panelFrameIn, persist, placements,
      reconcilePanelMounts, reconcilePanelVisibility, refreshNode, renderHiddenRail, renderMenu, renderNode,
      restorePanelMounts, root, shellState, stage, syncLayoutLock, toolbar, topologyOf,
    });

    const {
      addPanel, applyWorkspace, canFocusFixedPanel, closePanel, collapsePanel, expandPanel, focusPanel, hidePanel,
      setPanelTitle, showPanel, toggleCollapsed, togglePanel,
    } = workbenchPart("PortalWorkbenchPanelActions").createPanelActions({
      SPLIT_HANDLE_SIZE, callPanelLifecycle, canvas, clone, closeMenu, collectPanelIds, descriptorOf, descriptors,
      findNode, findPanel, firstStack, hiddenPanelsForLayout, id, isLayoutLocked, lastOpenTargetByType,
      minimumForNode, normalizeCurrent, normalizeNode, options, panelIds, panelNode, panelPlacement, placements,
      registerPanelCommands, removePanelFromNode, render, replaceNode, root, runtimeContext, shellState, splitNode,
      stackNode, unregisterPanelCommands,
    });

    const { openResource, startEditorOpening } = workbenchPart("PortalWorkbenchEditorOpening").createEditorOpening({
      addPanel, canFocusFixedPanel, clone, descriptors, findPanel, isLayoutLocked, lastOpenTargetByType, options,
      render, shellState, uid,
    });

    shellState.controller = Object.freeze({
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
        if (shellState.destroyed) return false;
        shellState.hostVisible = Boolean(visible);
        if (chrome) chrome.hidden = !shellState.hostVisible;
        if (!shellState.hostVisible) closeMenu();
        reconcilePanelVisibility();
        return true;
      },
      applyWorkspace,
      getLayout: () => clone(shellState.model),
      getState: stateSnapshot,
      isLayoutLocked,
      setLayoutLocked,
      getPanel: panelId => descriptors.get(String(panelId))?.runtime || null,
      getPanelsByType: type => [...descriptors.values()]
        .filter(descriptor => descriptor.type === String(type || ""))
        .map(descriptor => descriptor.runtime),
      getActiveOpenTarget: () => shellState.activeOpenTargetPanelId,
      setActiveOpenTarget(panelId) {
        const descriptor = descriptors.get(String(panelId || ""));
        if (!descriptor || descriptor.openSource) return false;
        shellState.activeOpenTargetPanelId = descriptor.id;
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
      isPanelVisible: panelId => !shellState.hidden.has(String(panelId)),
      isPanelCollapsed: panelId => shellState.collapsed.has(String(panelId)),
      isPanelExpanded: panelId => !shellState.hidden.has(String(panelId)) && !shellState.collapsed.has(String(panelId)),
      openCustomizer: openMenu,
      reset: () => applyWorkspace(defaultPreset || { name: options.label || window.PortalI18n.t("默认工作区"), layout: defaultLayout, hidden: [] }),
      destroy() {
        if (shellState.destroyed) return;
        persist();
        shellState.destroyed = true;
        destroyLayoutLock();
        window.removeEventListener("pagehide", persist);
        window.removeEventListener("portal:identity-changed", restoreIdentityLayout);
        closeMenu();
        destroyPanelRuntimes();
        commandScope.dispose();
        if (ownsContextStore) contextStore.dispose();
        chrome?.remove();
        document.body.classList.remove("draggingWorkbenchTab");
        stage.remove();
        unsubscribeResponsive?.();
        root.classList.remove("portalWorkbench");
        delete root.dataset.portalWorkbenchId;
        delete root.dataset.portalWorkbenchLayoutLocked;
        destroyViewport();
        instances.delete(id);
      },
    });

    commandScope.register({
      id: "workbench.reset-layout",
      title: window.PortalI18n.t("恢复默认布局"),
      category: "Workbench",
      icon: "refresh",
      enabled: () => !isLayoutLocked(),
      execute: () => shellState.controller.reset(),
    });
    commandScope.register({
      id: "workbench.toggle-layout-lock",
      title: () => isLayoutLocked() ? window.PortalI18n.t("解锁") : window.PortalI18n.t("固定"),
      category: "Workbench",
      icon: "lock",
      enabled: () => !inheritedLayoutLock(),
      execute: () => setLayoutLocked(!shellState.layoutLocked),
    });
    descriptors.forEach(registerPanelCommands);

    const unsubscribeResponsive = global.PortalResponsive?.subscribe?.(() => {
      render("responsive-mode");
    }, { immediate: false }) || null;

    startEditorOpening();

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
    followViewport();
    instances.set(id, shellState.controller);
    layoutLockSynchronizers.forEach(sync => sync());
    if (initialized) global.PortalPageBoot?.ready(root);
    return shellState.controller;
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
