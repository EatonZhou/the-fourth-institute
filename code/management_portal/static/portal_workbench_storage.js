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

  // Named layouts are portable account data; the live layout and Panel state
  // remain in the host's local store. Each preset is a separate association so
  // saving on one device cannot overwrite another device's unrelated presets.
  function createSavedLayouts({ id, savedWorkspaces, shellState, persist, clone }) {
    const namespace = "workbench.saved-layouts";
    const key = workspaceId => JSON.stringify([id, workspaceId]);
    const fields = workspace => ({
      id: workspace.id, name: workspace.name, layout: clone(workspace.layout),
      hidden: [...(workspace.hidden || [])], collapsed: [...(workspace.collapsed || [])],
    });
    function check(user) {
      if (shellState.destroyed || !user || global.PortalIdentity.username() !== user
        || shellState.storageUser !== user || global.PortalState.currentTransactionId?.()) {
        throw new Error("账户或页面状态已变化，请重新打开布局菜单。");
      }
    }
    async function account() {
      if (global.PortalState.currentTransactionId?.()) throw new Error("临时演示期间不能同步账户布局。");
      const previousUser = global.PortalIdentity.username();
      await global.PortalIdentity.load();
      const user = global.PortalIdentity.username();
      if (previousUser && previousUser !== user) throw new Error("账户已变化，请重新打开布局菜单。");
      if (!user) throw new Error("请先登录，再保存或读取账户布局。");
      check(user);
      return user;
    }
    async function put(user, workspace) {
      check(user);
      await global.PortalIdentity.putUIAssociation(namespace, key(workspace.id), {
        schemaVersion: 1, workbenchId: id, workspace: fields(workspace),
      });
      check(user);
      savedWorkspaces[workspace.id] = { ...fields(workspace), accountSynced: true };
      persist();
    }
    return Object.freeze({
      async refresh() {
        const user = await account();
        const result = await global.PortalIdentity.listUIAssociations(namespace);
        check(user);
        if (!Array.isArray(result.items)) throw new Error("账户布局读取结果无效，请重试。");
        const remote = Object.create(null);
        for (const item of result.items || []) {
          const value = item.value;
          const workspace = value?.workspace;
          if (value?.schemaVersion !== 1 || value.workbenchId !== id
            || !/^workspace-[\w-]+$/.test(workspace?.id || "")
            || item.key !== key(workspace.id) || typeof workspace.name !== "string" || !workspace.layout) continue;
          remote[workspace.id] = { ...fields(workspace), accountSynced: true };
        }
        // Import only legacy local records. A formerly synced record absent
        // from the account was deleted elsewhere and must not be resurrected.
        for (const workspace of Object.values(savedWorkspaces)) {
          if (workspace.accountSynced || remote[workspace.id]) continue;
          await put(user, workspace);
          remote[workspace.id] = savedWorkspaces[workspace.id];
        }
        check(user);
        Object.keys(savedWorkspaces).forEach(workspaceId => delete savedWorkspaces[workspaceId]);
        Object.assign(savedWorkspaces, remote);
        persist();
      },
      async save(workspace) {
        const user = await account();
        await put(user, workspace);
        shellState.activeWorkspaceName = workspace.name;
        persist();
      },
      async remove(workspaceId) {
        const user = await account();
        await global.PortalIdentity.removeUIAssociation(namespace, key(workspaceId));
        check(user);
        delete savedWorkspaces[workspaceId];
        persist();
      },
    });
  }

  global.PortalWorkbenchStorage = Object.freeze({ createStorage, createSavedLayouts, dependencies: DEPENDENCIES });
})(window);
