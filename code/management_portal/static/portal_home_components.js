(() => {
  "use strict";

  if (window.PortalHomeComponents) return;

  // Workbench owns visibility; Home owns the first data connection. Retain the
  // lifecycle projection because nested Workbenches mount before Home does.
  const panelVisibility = new WeakMap();

  function resolveRoute(search = "") {
    const params = new URLSearchParams(search);
    return Object.freeze({
      scheduledTriggeredId: String(params.get("scheduled_triggered_resident") || "").trim(),
    });
  }

  // Panels installed features add to Home (web.home_panel). Their scripts come
  // before home.js and register: id, group (the Home group showing it), order
  // among the group's panels, title, icon, source (its element), connect, and
  // requested when the address asks to open it.
  const featurePanels = [];
  function registerPanel(panel = {}) {
    const id = String(panel.id || "").trim();
    const group = String(panel.group || "").trim();
    if (!id || !group || typeof panel.connect !== "function") {
      throw new TypeError("A Home panel requires an id, a group and connect.");
    }
    if (featurePanels.some(item => item.id === id)) throw new TypeError(`Duplicate Home panel: ${id}`);
    featurePanels.push(Object.freeze({ ...panel, id, group, order: Number(panel.order) || 0 }));
  }
  function panels(group = "") {
    return featurePanels.filter(panel => !group || panel.group === group);
  }

  function resolveRoot(ref, parentRoot) {
    if (typeof ref === "function") return ref(parentRoot || document);
    if (ref instanceof Element || ref === document || ref === document.body) return ref;
    const selector = String(ref || "").trim();
    if (!selector) return parentRoot || document;
    return parentRoot?.querySelector?.(selector) || document.querySelector(selector);
  }

  function normalizeTree(raw, ids = new Set(), parentId = "") {
    const id = String(raw?.id || "").trim();
    if (!id) throw new TypeError("Home component requires a stable id.");
    if (ids.has(id)) throw new TypeError(`Duplicate Home component id: ${id}`);
    ids.add(id);
    const children = Array.isArray(raw.children)
      ? raw.children.map(child => normalizeTree(child, ids, id))
      : [];
    return Object.freeze({
      id,
      parentId,
      root: raw.root,
      required: raw.required !== false,
      loadPolicy: raw.loadPolicy || "when-visible",
      connect: typeof raw.connect === "function" ? raw.connect : null,
      children: Object.freeze(children),
    });
  }

  function create(options = {}) {
    const id = String(options.id || "home").trim();
    const context = options.context && typeof options.context === "object" ? options.context : {};
    const tree = normalizeTree(options.tree || {});
    const records = new Map();
    let mounted = false;
    let generation = 0;
    let activationQueued = false;
    let activationReady = false;

    function isVisible(record) {
      for (let current = record; current; current = records.get(current.parentId)) {
        if (panelVisibility.get(current.root) === false) return false;
      }
      return true;
    }

    function connect(record) {
      const { component, root } = record;
      mark(record, "mounting");
      try {
        const result = component.connect?.({
          root, context, signal: record.abortController.signal,
          component: Object.freeze({ id: record.id, parentId: record.parentId }),
        });
        if (result && typeof result.then === "function") {
          result.then(value => {
            retainCleanup(record, value);
            if (mounted && record.generation === generation) mark(record, "ready");
          }).catch(error => {
            console.error(window.PortalI18n.text`Home component ${record.id} failed to connect`, error);
            if (mounted && record.generation === generation) mark(record, "error", error);
          });
        } else {
          retainCleanup(record, result);
          mark(record, "ready");
        }
      } catch (error) {
        console.error(window.PortalI18n.text`Home component ${record.id} failed to connect`, error);
        mark(record, "error", error);
      }
    }

    function activateVisible() {
      activationQueued = false;
      if (!mounted || !activationReady) return;
      records.forEach(record => {
        if (record.status === "deferred" && isVisible(record)) connect(record);
      });
    }

    function visibilityChanged() {
      if (activationQueued || !mounted) return;
      activationQueued = true;
      // Finish the whole nested layout transaction before starting any reads.
      queueMicrotask(activateVisible);
    }

    function publish(record) {
      window.dispatchEvent(new CustomEvent("portal:home-component-state", {
        detail: Object.freeze({
          pageId: id,
          componentId: record.id,
          parentId: record.parentId,
          status: record.status,
        }),
      }));
    }

    function mark(record, status, error = null) {
      record.status = status;
      record.error = error;
      if (record.root instanceof Element) {
        record.root.dataset.homeComponentState = status;
      }
      publish(record);
    }

    function retainCleanup(record, value) {
      const cleanup = typeof value === "function"
        ? value
        : typeof value?.destroy === "function" ? () => value.destroy() : null;
      if (!cleanup) return;
      if (!mounted || record.generation !== generation) cleanup();
      else record.cleanup = cleanup;
    }

    function mountNode(component, parentRoot) {
      const root = resolveRoot(component.root, parentRoot);
      const record = {
        id: component.id,
        parentId: component.parentId,
        component,
        root,
        status: "mounting",
        error: null,
        cleanup: null,
        abortController: new AbortController(),
        generation,
      };
      records.set(record.id, record);
      root?.addEventListener("portal:home-panel-lifecycle", visibilityChanged);
      if (!root && component.required) {
        mark(record, "error", new Error(`Home component root not found: ${component.id}`));
      } else if (component.loadPolicy === "when-visible" && (!activationReady || !isVisible(record))) {
        mark(record, "deferred");
      } else {
        connect(record);
      }
      component.children.forEach(child => mountNode(child, root || parentRoot));
    }

    function mount() {
      if (mounted) return controller;
      mounted = true;
      generation += 1;
      activationReady = typeof options.ready !== "function";
      mountNode(tree, resolveRoot(options.root, document));
      if (!activationReady) {
        const epoch = generation;
        Promise.resolve().then(() => options.ready(context)).then(() => {
          if (!mounted || epoch !== generation) return;
          activationReady = true;
          activateVisible();
        }).catch(error => {
          if (!mounted || epoch !== generation) return;
          records.forEach(record => {
            if (record.status === "deferred") mark(record, "error", error);
          });
        });
      }
      return controller;
    }

    function destroy() {
      if (!mounted) return;
      mounted = false;
      [...records.values()].reverse().forEach(record => {
        record.root?.removeEventListener("portal:home-panel-lifecycle", visibilityChanged);
        record.abortController.abort();
        try { record.cleanup?.(); } catch (error) {
          console.error(window.PortalI18n.text`Home component ${record.id} failed to destroy`, error);
        }
        if (record.root instanceof Element) delete record.root.dataset.homeComponentState;
        record.status = "destroyed";
        publish(record);
      });
      records.clear();
    }

    function snapshot() {
      return Object.freeze({
        id,
        mounted,
        components: Object.freeze([...records.values()].map(record => Object.freeze({
          id: record.id,
          parentId: record.parentId,
          status: record.status,
          connected: Boolean(record.root),
          visible: isVisible(record),
        }))),
      });
    }

    const controller = Object.freeze({ mount, destroy, snapshot,
      isComponentVisible: componentId => {
        const record = records.get(componentId);
        return Boolean(record && isVisible(record));
      },
    });
    return controller;
  }

  const HOME_PANEL_PACKAGE = "home-dom-component";

  // Home's panel identities have their own schema, independent of Workbench's
  // layout shape. Migrate before mounting, including after identity resolves.
  function migrateAgentStatusState(state) {
    if (state.contentSchemaVersion >= 2) return state;
    const next = JSON.parse(JSON.stringify(state));
    const rename = id => id === "background-agents" ? "scheduled-triggered" : id;
    function layout(node) {
      if (!node) return;
      if (node.type === "panel") node.panelId = rename(node.panelId);
      if (node.activePanelId) node.activePanelId = rename(node.activePanelId);
      (node.children || []).forEach(layout);
    }
    function workspace(value) {
      layout(value.layout);
      for (const key of ["panelIds", "hidden", "collapsed"]) {
        if (Array.isArray(value[key])) value[key] = value[key].map(rename);
      }
    }
    workspace(next);
    Object.values(next.savedWorkspaces || {}).forEach(workspace);
    for (const key of ["placements", "panelStates"]) {
      const records = next[key];
      if (records && Object.hasOwn(records, "background-agents")) {
        records["scheduled-triggered"] = records["background-agents"];
        delete records["background-agents"];
      }
    }
    for (const editor of next.openEditors || []) editor.id = rename(editor.id);
    if (next.activeOpenTargetPanelId) next.activeOpenTargetPanelId = rename(next.activeOpenTargetPanelId);
    next.contentSchemaVersion = 2;
    return next;
  }

  function ensureWorkbenchPanelPackage() {
    const workbench = window.PortalWorkbench;
    if (!workbench?.registerPanelPackage) {
      throw new Error("Home composition requires PortalWorkbench.");
    }
    if (workbench.registeredPanelPackages().some(item => item.type === HOME_PANEL_PACKAGE)) return;
    workbench.registerPanelPackage({
      type: HOME_PANEL_PACKAGE,
      version: 1,
      component: "HomeComponentPanel",
      presentation: { title: window.PortalI18n.t("Home component"), icon: "component" },
      sizing: { minWidth: 220, minHeight: 140 },
      instances: "multiple",
      capabilities: ["home-component"],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
      context: { channels: [] },
      persistence: { schemaVersion: 1 },
      create({ source, returnHost, panelId }) {
        const element = resolveRoot(source, document);
        const staging = resolveRoot(returnHost, document);
        if (!(element instanceof Element) || !(staging instanceof Element)) {
          throw new Error(`Home Workbench panel source is missing: ${panelId}`);
        }
        element.hidden = false;
        const dispatch = phase => {
          if (phase === "show") panelVisibility.set(element, true);
          else if (["hide", "disconnect", "destroy"].includes(phase)) panelVisibility.set(element, false);
          element.dispatchEvent(new CustomEvent("portal:home-panel-lifecycle", {
            bubbles: false,
            detail: Object.freeze({ panelId, phase }),
          }));
        };
        return {
          root: element,
          connect() { dispatch("connect"); },
          show() { dispatch("show"); },
          hide() { dispatch("hide"); },
          disconnect() { dispatch("disconnect"); },
          destroy() {
            dispatch("destroy");
            staging.append(element);
          },
        };
      },
    });
  }

  function createWorkbenchComposition(options = {}) {
    const definitions = Array.isArray(options.workbenches) ? options.workbenches.slice() : [];
    if (!definitions.length) throw new TypeError("Home Workbench composition requires workbenches.");
    let controllers = [];

    function mount() {
      if (controllers.length) return controller;
      ensureWorkbenchPanelPackage();
      definitions.forEach(definition => definition.panels.forEach(panel => {
        const root = resolveRoot(panel.source, document);
        if (root) panelVisibility.set(root, false);
      }));
      const initialWorkspaces = Object.assign({}, ...definitions.map(definition => (
        (definition.presets || []).find(preset => preset.id === definition.defaultPreset)?.workbenches || {}
      )));
      controllers = definitions.map(definition => {
        const initialWorkspace = initialWorkspaces[definition.id];
        const initialEditors = definition.panels.map(panel => ({
          id: panel.id,
          type: HOME_PANEL_PACKAGE,
          title: panel.title,
          icon: panel.icon || "component",
          minWidth: panel.minWidth,
          minHeight: panel.minHeight,
          canHide: panel.canHide !== false,
          canCollapse: panel.canCollapse !== false,
          canMove: panel.canMove !== false,
          options: {
            source: panel.source,
            returnHost: panel.returnHost || definition.staging,
          },
        }));
        return window.PortalWorkbench.create({
          id: definition.id,
          root: definition.root,
          toolbarHost: definition.toolbarHost,
          label: definition.label,
          contentSchemaVersion: definition.contentSchemaVersion,
          migrateState: definition.migrateState,
          initialEditors,
          defaultLayout: definition.defaultLayout,
          presets: initialWorkspace
            ? [initialWorkspace, ...(definition.presets || []).filter(preset => preset.id !== initialWorkspace.id)]
            : definition.presets || [],
          defaultPreset: initialWorkspace?.id || definition.defaultPreset,
          mobilePrimaryPanelId: definition.mobilePrimaryPanelId || initialEditors[0]?.id,
          toolbarPlacement: definition.toolbarPlacement,
          toolbarRight: definition.toolbarRight,
          showSingleTabHeader: definition.showSingleTabHeader,
          insets: { top: 0, right: 0, bottom: 0, left: 0 },
          onWorkspaceApplied(workspace) {
            const preset = (definition.presets || []).find(item => String(item.id) === workspace.id);
            Object.entries(preset?.workbenches || {}).forEach(([workbenchId, targetWorkspace]) => {
              const target = controllers.find(item => item.id === workbenchId);
              if (target && target.id !== definition.id) target.applyWorkspace(targetWorkspace);
            });
          },
        });
      });
      return controller;
    }

    function destroy() {
      controllers.slice().reverse().forEach(item => item.destroy());
      controllers = [];
    }

    function snapshot() {
      return Object.freeze({
        mounted: controllers.length === definitions.length,
        workbenches: Object.freeze(controllers.map(item => Object.freeze({
          id: item.id,
          state: item.getState(),
        }))),
      });
    }

    const controller = Object.freeze({ mount, destroy, snapshot });
    return controller;
  }

  window.PortalHomeComponents = Object.freeze({
    create, createWorkbenchComposition, resolveRoute, migrateAgentStatusState, registerPanel, panels,
  });
})();
