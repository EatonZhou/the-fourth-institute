(function () {
  "use strict";

  if (window.PortalLineageCatalogs) return;

  const DEFAULT_MAX_AGE_MS = 15000;
  const entries = new Map();
  // The catalogs of the accounts whose Provider lists its models live.
  const liveCatalogs = new Map();

  function normalizeRuntimeCatalog(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const defaultModel = String(data.default_model || "").trim();
    const defaultEffort = String(data.default_reasoning_effort || "").trim();
    const models = [...new Set([
      defaultModel,
      ...(Array.isArray(data.models) ? data.models : []),
    ].map(item => String(item || "").trim()).filter(Boolean))];
    return {
      default_model: defaultModel,
      default_reasoning_effort: defaultEffort,
      models,
      efforts_by_model: data.efforts_by_model && typeof data.efforts_by_model === "object" ? data.efforts_by_model : {},
      default_efforts_by_model: data.default_efforts_by_model && typeof data.default_efforts_by_model === "object"
        ? data.default_efforts_by_model
        : {},
    };
  }

  function clone(value) {
    if (value === undefined || value === null) return value;
    if (typeof structuredClone === "function") {
      try {
        return structuredClone(value);
      } catch (_error) {
        // API payloads are JSON-compatible; use the portable fallback below.
      }
    }
    return JSON.parse(JSON.stringify(value));
  }

  function maxAgeMs(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : DEFAULT_MAX_AGE_MS;
  }

  function catalogKey(kind, path) {
    const actor = String(window.PortalIdentity?.username?.() || "anonymous").trim().toLowerCase();
    return `${actor || "anonymous"}:${kind}:${path}`;
  }

  function read(kind, path, options = {}) {
    const key = catalogKey(kind, path);
    const current = entries.get(key);
    if (current?.promise) return current.promise.then(clone);

    const fresh = current
      && Object.prototype.hasOwnProperty.call(current, "value")
      && Date.now() - Number(current.loadedAt || 0) <= maxAgeMs(options.maxAgeMs);
    if (options.force !== true && fresh) return Promise.resolve(clone(current.value));

    let request;
    request = window.PortalAPI.get(path, { cache: "no-store" })
      .then(payload => {
        const value = clone(payload);
        entries.set(key, {
          kind,
          path,
          value,
          loadedAt: Date.now(),
          promise: null,
        });
        return value;
      })
      .catch(error => {
        if (entries.get(key)?.promise === request) entries.delete(key);
        throw error;
      });
    entries.set(key, {
      kind,
      path,
      value: current?.value,
      loadedAt: Number(current?.loadedAt || 0),
      promise: request,
    });
    return request.then(clone);
  }

  function readLoader(kind, cacheKey, loader, options = {}) {
    const key = catalogKey(kind, cacheKey);
    const current = entries.get(key);
    if (current?.promise) return current.promise.then(clone);
    const fresh = current
      && Object.prototype.hasOwnProperty.call(current, "value")
      && Date.now() - Number(current.loadedAt || 0) <= maxAgeMs(options.maxAgeMs);
    if (options.force !== true && fresh) return Promise.resolve(clone(current.value));
    let request;
    request = Promise.resolve(loader()).then(payload => {
      const value = clone(payload);
      entries.set(key, { kind, path: cacheKey, value, loadedAt: Date.now(), promise: null });
      return value;
    }).catch(error => {
      if (entries.get(key)?.promise === request) entries.delete(key);
      throw error;
    });
    entries.set(key, {
      kind,
      path: cacheKey,
      value: current?.value,
      loadedAt: Number(current?.loadedAt || 0),
      promise: request,
    });
    return request.then(clone);
  }

  function residents(options = {}) {
    const values = options.values && typeof options.values === "object" ? options.values : {};
    const cacheKey = JSON.stringify(values);
    const load = typeof options.load === "function"
      ? options.load
      : query => window.PortalResidentService.list(query);
    return readLoader(
      "residents",
      cacheKey,
      () => load(values),
      options,
    );
  }

  function users(options = {}) {
    return read("users", "/api/users", options);
  }

  function runtimeCatalog(accountId, options = {}) {
    const query = new URLSearchParams({ account_id: String(accountId || "") });
    return read("agent-runtime", `/api/agent-workbench/agent-runtime-catalog?${query}`, options);
  }

  // The models and efforts of a route.  A Provider that lists its models live
  // (Codex) gives none on its routes: its account's catalog is read once and
  // ``onLoaded`` runs when it arrives.  No Provider borrows another's list.
  function routeRuntimeCatalog(route, onLoaded) {
    if (!route || route.models) return route || {};
    const accountId = String(route.account_id || "");
    if (!accountId) return route;
    if (liveCatalogs.has(accountId)) return { ...liveCatalogs.get(accountId), ...route };
    runtimeCatalog(accountId)
      .then(payload => {
        liveCatalogs.set(accountId, normalizeRuntimeCatalog(payload));
        if (typeof onLoaded === "function") onLoaded();
      })
      .catch(() => {});
    return route;
  }

  function agentBackends(options = {}) {
    return read("agent-backends", "/api/agent-workbench/agent-backends", options);
  }

  function populateBackends(select, catalog) {
    if (!select || !catalog?.backends?.length) return;
    const current = select.value;
    select.replaceChildren(...catalog.backends.map(row => new Option(row.label, row.backend)));
    if (catalog.backends.some(row => row.backend === current)) select.value = current;
  }

  function projectCatalog(options = {}) {
    return read("project-registry", "/api/projects/catalog", options);
  }

  // Both Fork forms use the execution catalog, including Model API routes.
  // The source identity is supplied by the opened form, never live selection.
  function renderForkProvider({ catalog, providerSelect, accountSelect, source, detail, provider, account }) {
    const sourceProvider = String(detail?.resident?.backend || source?.backend || "");
    const sourceAccount = String(detail?.execution_account?.backend_route_id || detail?.provider_session?.backend_route_id
      || detail?.resident?.backend_route_id || detail?.account_selection?.source_account_id
      || source?.backend_route_id || "");
    populateBackends(providerSelect, catalog);
    providerSelect.value = provider || sourceProvider;
    const definition = catalog?.backends?.find(row => row.backend === providerSelect.value);
    const routes = definition?.routes || [];
    const sourceRoute = providerSelect.value === sourceProvider
      ? routes.find(row => row.backend_route_id === sourceAccount || row.account_id === sourceAccount)
      : null;
    const selected = routes.find(row => account !== undefined && row.backend_route_id === account)
      || sourceRoute || routes.find(row => row.backend_route_id === definition?.default_route) || routes[0];
    accountSelect.replaceChildren(...routes.map(row => new Option(
      `${row.label || row.account_id || definition.label}${row === sourceRoute ? "（来源账号）" : ""}`,
      row.backend_route_id,
    )));
    accountSelect.value = selected?.backend_route_id || "";
    accountSelect.dataset.sourceAccount = sourceRoute?.backend_route_id || "";
    accountSelect.disabled = !routes.length;
    return { provider: providerSelect.value, account: accountSelect.value, definition,
      available: !!definition && routes.length > 0 };
  }

  function invalidate(targets = null) {
    if (targets === null || targets === undefined) {
      entries.clear();
      return;
    }
    const requested = new Set(
      (Array.isArray(targets) ? targets : [targets])
        .map(value => String(value || "").trim())
        .filter(Boolean),
    );
    for (const [key, entry] of entries) {
      if (requested.has(key) || requested.has(entry.kind) || requested.has(entry.path)) {
        entries.delete(key);
      }
    }
  }

  window.PortalLineageCatalogs = Object.freeze({
    agentBackends,
    populateBackends,
    renderForkProvider,
    invalidate,
    normalizeRuntimeCatalog,
    projectCatalog,
    residents,
    routeRuntimeCatalog,
    runtimeCatalog,
    users,
  });
})();
