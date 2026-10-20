(function () {
  "use strict";

  // Registers a resource with the RegisterResource instruction. A directory
  // that is not yet a Git checkout can be initialized after the user agrees.
  if (window.PortalResourceRegistrationService) return;

  function execute(fields, resourceType) {
    return window.PortalInstructionClient.execute("RegisterResource", {
      resource_type: resourceType,
      fields,
    }, { idempotencyKey: `resource-register:${globalThis.PortalUIPrimitives.randomUUID()}` });
  }

  async function register(payload, options = {}) {
    const { resource_type: resourceType, ...fields } = payload || {};
    try {
      return await execute(fields, resourceType);
    } catch (error) {
      const details = error?.details || {};
      if (details.reason !== "git_checkout_required" || typeof options.confirmInitialize !== "function") {
        throw error;
      }
      if (!await options.confirmInitialize({ message: error.message, path: details.path || "" })) return { cancelled: true };
      return execute({ ...fields, initialize_git: true }, resourceType);
    }
  }

  window.PortalResourceRegistrationService = Object.freeze({ register });
})();
