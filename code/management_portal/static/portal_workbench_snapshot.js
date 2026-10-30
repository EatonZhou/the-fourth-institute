(function (global) {
  "use strict";

  if (global.PortalWorkbenchSnapshot) return;

  // What the store keeps: the snapshot of the layout, the reopened editors
  // and every Panel's state, writing it, and restoring a Panel's initial or
  // stored state.

  const DEPENDENCIES = Object.freeze([
    "SCHEMA_VERSION", "callPanelLifecycle", "clone", "descriptors", "id", "initialPanelStates", "options", "panelIds",
    "placements", "runtimeContext", "savedWorkspaces", "shellState", "unavailableBindings",
  ]);

  function createSnapshot(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchSnapshot is missing: ${missing.join(", ")}`);
    const {
      SCHEMA_VERSION, callPanelLifecycle, clone, descriptors, id, initialPanelStates, options, panelIds, placements,
      runtimeContext, savedWorkspaces, shellState, unavailableBindings,
    } = dependencies;

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
        layoutLocked: shellState.layoutLocked,
        panelIds: [...panelIds],
        openEditors,
        activeWorkspaceName: shellState.activeWorkspaceName,
        activeOpenTargetPanelId: shellState.activeOpenTargetPanelId,
        layout: clone(shellState.model),
        hidden: [...shellState.hidden],
        collapsed: [...shellState.collapsed],
        placements: clone(placements),
        savedWorkspaces: clone(savedWorkspaces),
        panelStates,
      };
    }

    function persist() {
      if (shellState.destroyed) return;
      shellState.storage.replace(stateSnapshot());
    }

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

    return {
      persist, restorePanelState, stateSnapshot,
    };
  }

  global.PortalWorkbenchSnapshot = Object.freeze({ createSnapshot, dependencies: DEPENDENCIES });
})(window);
