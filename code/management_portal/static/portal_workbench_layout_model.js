(function (global) {
  "use strict";

  if (global.PortalWorkbenchLayoutModel) return;

  // The layout state: the Panel descriptors (with the editors the stored
  // state reopens), presets, saved workspaces and remembered placements, the
  // layout model with its hidden and collapsed Panels, the focused stack,
  // open targets and workspace name, and switching to the stored layout of
  // another signed-in user.

  const DEPENDENCIES = Object.freeze([
    "SCHEMA_VERSION", "clone", "collectPanelIds", "defaultWorkbenchStore", "descriptorOf", "id",
    "layoutLockSynchronizers", "normalizeLayout", "normalizeNode", "options", "panelPackages", "persist",
    "readStoredState", "render", "restorePanelState", "root", "runtimeContext", "shellState", "stackNode", "stored",
  ]);

  function createLayoutModel(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchLayoutModel is missing: ${missing.join(", ")}`);
    const {
      SCHEMA_VERSION, clone, collectPanelIds, defaultWorkbenchStore, descriptorOf, id, layoutLockSynchronizers,
      normalizeLayout, normalizeNode, options, panelPackages, persist, readStoredState, render, restorePanelState,
      root, runtimeContext, shellState, stackNode, stored,
    } = dependencies;

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
          // Packages may persist their target in a different snapshot field.
          // Keep the saved locator across later layout saves and reloads.
          binding: clone(restored.binding || spec.binding || null),
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
    shellState.hidden = hiddenPanelsForLayout(
      stored.schemaVersion === SCHEMA_VERSION
        ? (stored.hidden || [])
        : (defaultPreset?.hidden || []),
      storedPanelIds,
    );
    shellState.collapsed = new Set(
      stored.schemaVersion === SCHEMA_VERSION
        ? (stored.collapsed || []).filter(panelId => panelIds.has(String(panelId)))
        : [],
    );
    [...shellState.hidden].forEach(panelId => shellState.collapsed.delete(String(panelId)));
    const visiblePanelIds = () => new Set([...panelIds].filter(panelId => !shellState.hidden.has(panelId)));
    shellState.model = normalizeLayout(
      stored.schemaVersion === SCHEMA_VERSION ? stored.layout : (defaultPreset?.layout || defaultLayout),
      visiblePanelIds(),
    );

    shellState.activeOpenTargetPanelId = descriptors.has(String(stored.activeOpenTargetPanelId || ""))
      ? String(stored.activeOpenTargetPanelId)
      : (collectPanelIds(shellState.model).find(panelId => !descriptors.get(panelId)?.openSource) || "");
    shellState.mobileActivePanelId = String(
      options.mobilePrimaryPanelId
      || [...descriptors.values()].find(descriptor => descriptor.mobilePrimary)?.id
      || shellState.activeOpenTargetPanelId
      || collectPanelIds(shellState.model)[0]
      || "",
    );
    const lastOpenTargetByType = new Map();
    shellState.activeWorkspaceName = String(stored.activeWorkspaceName || defaultPreset?.name || options.label || "工作区");

    function restoreIdentityLayout() {
      const user = global.PortalIdentity?.username?.() || "";
      if (shellState.destroyed || options.store || user === shellState.storageUser) return;
      const previousUser = shellState.storageUser;
      shellState.storageUser = user;
      shellState.storage = defaultWorkbenchStore(id, user);
      const saved = readStoredState();
      if (saved.schemaVersion !== SCHEMA_VERSION && !previousUser) {
        persist();
        return;
      }
      const state = saved.schemaVersion === SCHEMA_VERSION ? saved : {};
      shellState.layoutLocked = state.layoutLocked === true;
      for (const record of [placements, savedWorkspaces]) {
        Object.keys(record).forEach(key => delete record[key]);
      }
      Object.assign(placements, state.placements || {});
      Object.assign(savedWorkspaces, state.savedWorkspaces || {});
      shellState.hidden = hiddenPanelsForLayout(state.hidden || defaultPreset?.hidden || [], state.panelIds || []);
      shellState.collapsed = new Set(state.collapsed || []);
      shellState.model = normalizeNode(state.layout || defaultPreset?.layout || defaultLayout, visiblePanelIds());
      shellState.activeWorkspaceName = state.activeWorkspaceName || defaultPreset?.name || options.label || "工作区";
      shellState.activeOpenTargetPanelId = state.activeOpenTargetPanelId || "";
      descriptors.forEach(descriptor => restorePanelState(descriptor, state));
      normalizeCurrent();
      render("restore-identity");
      layoutLockSynchronizers.forEach(sync => sync());
    }

    function normalizeCurrent() {
      shellState.model = normalizeLayout(shellState.model, visiblePanelIds());
    }

    return {
      defaultLayout, defaultPreset, descriptors, factoryPresets, hiddenPanelsForLayout, lastOpenTargetByType,
      normalizeCurrent, panelIds, placements, restoreIdentityLayout, savedWorkspaces,
    };
  }

  global.PortalWorkbenchLayoutModel = Object.freeze({ createLayoutModel, dependencies: DEPENDENCIES });
})(window);
