(() => {
  "use strict";

  if (window.PortalLineageWorkbenchController) return;

  // The page state of its own namespace, or another controller's (HATG shares the Workbench's).
  function ownPageState(options) {
    if (typeof window.PortalPageState?.create !== "function") {
      throw new Error("Society Workbench requires PortalPageState.");
    }
    const user = typeof options.user === "function" ? options.user : () => "anonymous";
    const debounceMs = Number(options.debounceMs || 180);
    const pageState = window.PortalPageState.create(String(options.namespace || "lineage"), { user });
    return {
      read: () => pageState.read(),
      write: patch => pageState.write(patch),
      writeSoon: (patch, delay = debounceMs) => pageState.debounceWrite(patch, delay),
    };
  }

  function createController(options = {}) {
    const state = options.state;
    if (!state || typeof state !== "object") {
      throw new TypeError("Workbench controller requires its state object.");
    }
    const pageState = options.pageState || ownPageState(options);
    if (typeof pageState.read !== "function"
        || typeof pageState.write !== "function"
        || typeof pageState.writeSoon !== "function") {
      throw new TypeError("Workbench controller requires a complete page-state owner.");
    }
    const listenerGroups = new Map();

    function listen(group, target, type, handler, eventOptions) {
      if (!target?.addEventListener || typeof handler !== "function") return () => {};
      const groupName = String(group || "page");
      target.addEventListener(type, handler, eventOptions);
      const remove = () => target.removeEventListener(type, handler, eventOptions);
      if (!listenerGroups.has(groupName)) listenerGroups.set(groupName, new Set());
      listenerGroups.get(groupName).add(remove);
      return () => {
        remove();
        listenerGroups.get(groupName)?.delete(remove);
      };
    }

    function clearListeners(group) {
      const groupName = String(group || "page");
      const listeners = listenerGroups.get(groupName);
      if (!listeners) return;
      listeners.forEach(remove => remove());
      listenerGroups.delete(groupName);
    }

    function destroy() {
      [...listenerGroups.keys()].forEach(clearListeners);
    }

    return Object.freeze({
      state,
      listen,
      clearListeners,
      read: () => pageState.read(),
      write: patch => pageState.write(patch || {}),
      writeSoon: (patch, delay) => pageState.writeSoon(patch || {}, delay),
      destroy,
    });
  }

  window.PortalLineageWorkbenchController = Object.freeze({ createController });
})();
