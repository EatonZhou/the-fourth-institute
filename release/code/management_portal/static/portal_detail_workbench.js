(function (global) {
  "use strict";

  if (global.PortalDetailWorkbench) return;

  const instances = new WeakMap();

  function elementOf(value, root = document) {
    if (typeof value === "string") return root.querySelector(value);
    return value instanceof Element ? value : null;
  }

  function stableId(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "panel";
  }

  function directPanels(root) {
    return [...root.children].filter(node => node.matches?.("[data-portal-detail-panel]"));
  }

  function descriptorOf(element, index) {
    const id = stableId(element.dataset.portalDetailPanel || element.id || `panel-${index + 1}`);
    const type = stableId(element.dataset.panelType || "document");
    if (!element.id) element.id = `${id}-panel`;
    element.classList.add("portalDetailWorkbenchPanel");
    return {
      id,
      type: "authored",
      presentation: type,
      options: { source: element },
      title: element.dataset.panelTitle || element.getAttribute("aria-label") || id,
      icon: element.dataset.panelIcon || "panel",
      required: element.dataset.panelRequired === "true",
      canHide: element.dataset.panelRequired !== "true",
      minWidth: Number(element.dataset.panelMinWidth || 0) || undefined,
      minHeight: Number(element.dataset.panelMinHeight || 0) || undefined,
    };
  }

  function defaultLayout(descriptors, options = {}) {
    const model = global.PortalWorkbench.model;
    const panelIds = descriptors.map(panel => panel.id);
    if (panelIds.length === 1) return model.stack(panelIds);
    const orientation = options.orientation === "vertical" ? "vertical" : "horizontal";
    return model.split(
      orientation,
      panelIds.map(panelId => model.stack([panelId])),
      Array.isArray(options.weights) ? options.weights : panelIds.map(() => 1),
    );
  }

  function destroy(rootValue) {
    const root = elementOf(rootValue);
    if (!root) return;
    const record = instances.get(root);
    if (!record) return;
    record.controller.destroy();
    root.classList.remove("portalDetailWorkbenchRoot");
    instances.delete(root);
  }

  function mount(options = {}) {
    if (!global.PortalWorkbench) {
      throw new Error("PortalDetailWorkbench requires PortalWorkbench.");
    }
    const root = elementOf(options.root);
    if (!root) throw new TypeError("PortalDetailWorkbench.mount requires root.");
    destroy(root);
    const descriptors = directPanels(root).map(descriptorOf);
    if (!descriptors.length) {
      throw new TypeError("PortalDetailWorkbench requires direct data-portal-detail-panel children.");
    }
    const id = stableId(options.id || root.id || "detail-workbench");
    root.classList.add("portalDetailWorkbenchRoot");
    const layout = options.defaultLayout || defaultLayout(descriptors, options);
    const presets = options.presets || [{
      id: "default",
      name: options.presetName || window.PortalI18n.t("详情"),
      layout,
      hidden: [],
    }];
    const controller = global.PortalWorkbench.create({
      id,
      root,
      initialEditors: descriptors,
      presets,
      defaultPreset: options.defaultPreset || presets[0]?.id,
      defaultLayout: layout,
      label: options.label || window.PortalI18n.t("详情工作台"),
      toolbarHost: elementOf(options.toolbarHost),
      store: options.store,
    });
    instances.set(root, { controller, descriptors });
    return controller;
  }

  function normalizeNodes(root, value) {
    const values = Array.isArray(value) ? value : [value];
    const result = [];
    values.forEach(candidate => {
      if (!candidate) return;
      if (typeof candidate === "string") {
        root.querySelectorAll(candidate).forEach(node => result.push(node));
      } else if (candidate instanceof Element) {
        result.push(candidate);
      } else if (typeof candidate[Symbol.iterator] === "function") {
        [...candidate].forEach(node => {
          if (node instanceof Element) result.push(node);
        });
      }
    });
    return [...new Set(result)];
  }

  function partition(rootValue, groups = []) {
    const root = elementOf(rootValue);
    if (!root) throw new TypeError("PortalDetailWorkbench.partition requires root.");
    destroy(root);
    const originalChildren = [...root.children];
    const claimed = new Set();
    const sections = [];
    groups.forEach((group, index) => {
      const candidates = group.catchAll
        ? originalChildren.filter(node => !claimed.has(node))
        : normalizeNodes(root, group.nodes || group.selector || []);
      const nodes = candidates.filter(node => {
        if (claimed.has(node)) return false;
        if (node === root || !root.contains(node)) return false;
        const topLevel = node.parentElement === root
          ? node
          : originalChildren.find(child => child.contains(node));
        if (!topLevel || claimed.has(topLevel)) return false;
        claimed.add(topLevel);
        return true;
      }).map(node => node.parentElement === root
        ? node
        : originalChildren.find(child => child.contains(node))
      ).filter(Boolean);
      if (!nodes.length && group.optional !== false) return;
      const section = document.createElement("section");
      const id = stableId(group.id || `panel-${index + 1}`);
      section.dataset.portalDetailPanel = id;
      section.dataset.panelType = group.type || "document";
      section.dataset.panelTitle = group.title || id;
      section.dataset.panelIcon = group.icon || "panel";
      if (group.required) section.dataset.panelRequired = "true";
      if (group.className) section.classList.add(...String(group.className).split(/\s+/).filter(Boolean));
      nodes.forEach(node => section.append(node));
      sections.push(section);
    });
    originalChildren.filter(node => !claimed.has(node)).forEach(node => {
      let fallback = sections.find(section => section.dataset.portalDetailPanel === "overview");
      if (!fallback) {
        fallback = document.createElement("section");
        fallback.dataset.portalDetailPanel = "overview";
        fallback.dataset.panelType = "document";
        fallback.dataset.panelTitle = "概览";
        fallback.dataset.panelIcon = "file-text";
        sections.unshift(fallback);
      }
      fallback.append(node);
    });
    root.replaceChildren(...sections);
    return sections;
  }

  function section(options = {}) {
    const node = document.createElement("section");
    node.dataset.portalDetailPanel = stableId(options.id);
    node.dataset.panelType = options.type || "document";
    node.dataset.panelTitle = options.title || options.id || "Panel";
    node.dataset.panelIcon = options.icon || "panel";
    if (options.required) node.dataset.panelRequired = "true";
    if (options.className) node.className = options.className;
    if (options.html !== undefined) node.innerHTML = String(options.html);
    return node;
  }

  global.PortalDetailWorkbench = Object.freeze({
    destroy,
    get: rootValue => instances.get(elementOf(rootValue))?.controller || null,
    mount,
    partition,
    section,
  });
})(window);
