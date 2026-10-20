(() => {
  "use strict";
  const definitions = new Map();
  function register(definition) {
    if (!definition?.id || typeof definition.matches !== "function" || typeof definition.create !== "function") {
      throw new TypeError("Watchboard application requires id, matches and create.");
    }
    if (definitions.has(definition.id)) throw new TypeError(`Duplicate Watchboard application: ${definition.id}`);
    definitions.set(definition.id, Object.freeze({ ...definition }));
  }
  function create(context) {
    const instances = new Map();
    function reset() {
      for (const instance of instances.values()) {
        try {
          instance.destroy?.();
        } catch (error) {
          console.warn("Watchboard application cleanup failed", error);
        }
      }
      instances.clear();
    }
    return Object.freeze({
      resolve(dashboard) {
        const definition = [...definitions.values()].find(item => item.matches(dashboard));
        if (!definition) return null;
        if (!instances.has(definition.id)) {
          const instance = definition.create(context);
          if (typeof instance?.render !== "function") throw new TypeError(`Watchboard application requires render: ${definition.id}`);
          instances.set(definition.id, Object.freeze({ ...instance, id: definition.id }));
        }
        return instances.get(definition.id);
      },
      reset, destroy: reset,
    });
  }
  window.PortalWatchboardApplications = Object.freeze({ register, create });
})();
