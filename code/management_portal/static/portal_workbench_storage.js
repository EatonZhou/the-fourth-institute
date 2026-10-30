(function (global) {
  "use strict";

  if (global.PortalWorkbenchStorage) return;

  // What a workbench starts from: the store of the signed-in user, the
  // state read (and migrated) from it, whether that state locks the layout
  // and the Panel states the page passes.

  const DEPENDENCIES = Object.freeze([
    "SCHEMA_VERSION", "defaultWorkbenchStore", "id", "options", "shellState",
  ]);

  function createStorage(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchStorage is missing: ${missing.join(", ")}`);
    const {
      SCHEMA_VERSION, defaultWorkbenchStore, id, options, shellState,
    } = dependencies;

    shellState.storageUser = global.PortalIdentity?.username?.() || "";
    shellState.storage = options.store || defaultWorkbenchStore(id, shellState.storageUser);
    function readStoredState() {
      const state = shellState.storage.read();
      const migrated = options.migrateState?.(state) || state;
      if (migrated !== state) shellState.storage.replace(migrated);
      return migrated;
    }
    const stored = readStoredState();
    shellState.layoutLocked = stored.schemaVersion === SCHEMA_VERSION && stored.layoutLocked === true;

    const initialPanelStates = new Map(Object.entries(options.initialPanelStates || {}));

    return {
      initialPanelStates, readStoredState, stored,
    };
  }

  global.PortalWorkbenchStorage = Object.freeze({ createStorage, dependencies: DEPENDENCIES });
})(window);
