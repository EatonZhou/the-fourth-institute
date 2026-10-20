(function () {
  "use strict";

  if (window.PortalResidentDiscoveryService) return;

  const navigationRequestsByResolver = new WeakMap();
  const resolveDefaultNavigationTarget = residentId => window.PortalResidentService.navigationTarget(residentId);

  function normalizeNavigationTarget(payload, expectedResidentId) {
    const residentId = String(payload?.resident_id || "").trim();
    const lineageId = String(payload?.lineage_id || "").trim();
    if (
      residentId !== expectedResidentId
      || !/^resident_[0-9a-f]{12}$/.test(residentId)
      || !/^lineage_[0-9a-f]{12}$/.test(lineageId)
    ) {
      const error = new Error("Resident navigation target response is invalid");
      error.status = 502;
      throw error;
    }
    return Object.freeze({ resident_id: residentId, lineage_id: lineageId });
  }

  function createClient(options = {}) {
    const post = options.post || ((path, body) => window.PortalAPI.post(path, body));
    const resolveTarget = options.resolveNavigationTarget || resolveDefaultNavigationTarget;
    if (typeof resolveTarget !== "function" || typeof post !== "function") {
      throw new TypeError("PortalResidentDiscoveryService requires Resident navigation and POST transports");
    }
    if (!navigationRequestsByResolver.has(resolveTarget)) navigationRequestsByResolver.set(resolveTarget, new Map());
    const pendingNavigationTargets = navigationRequestsByResolver.get(resolveTarget);

    return Object.freeze({
      resolveNavigationTarget(residentId) {
        const id = String(residentId || "").trim();
        if (!id) throw new TypeError("resident_id required");
        if (pendingNavigationTargets.has(id)) return pendingNavigationTargets.get(id);
        const request = Promise.resolve().then(() => resolveTarget(id))
          .then(payload => normalizeNavigationTarget(payload, id))
          .finally(() => {
            if (pendingNavigationTargets.get(id) === request) {
              pendingNavigationTargets.delete(id);
            }
          });
        pendingNavigationTargets.set(id, request);
        return request;
      },
      search: payload => post("/api/agent-workbench/resident-discovery/search", payload),
      recommend: payload => post("/api/agent-workbench/resident-discovery/recommend", payload),
      latestRecommendation: () => post("/api/agent-workbench/resident-discovery/recommend/latest", {}),
      loadRecommendationResult: payload => post("/api/agent-workbench/resident-discovery/recommend/result", payload),
      searchRoutingEvalResidents: payload => post("/api/agent-workbench/resident-discovery/routing-evals/residents/search", payload),
      submitRoutingEval: payload => post("/api/agent-workbench/resident-discovery/routing-evals/submit", payload),
    });
  }

  window.PortalResidentDiscoveryService = Object.freeze({ createClient });
})();
