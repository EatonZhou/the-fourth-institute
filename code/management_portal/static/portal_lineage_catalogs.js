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
  function routeRuntimeCatalog(route, onLoaded, onError) {
    if (!route || route.models) return route || {};
    const accountId = String(route.account_id || "");
    if (!accountId) return route;
    if (liveCatalogs.has(accountId)) return { ...liveCatalogs.get(accountId), ...route };
    runtimeCatalog(accountId)
      .then(payload => {
        liveCatalogs.set(accountId, normalizeRuntimeCatalog(payload));
        if (typeof onLoaded === "function") onLoaded();
      })
      .catch(error => { if (typeof onError === "function") onError(error); });
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

  // { projects: [{ project_id, status, project_type, ... }], status_vocab } from ListProjects.
  function projectCatalog(options = {}) {
    return readLoader("projects", "ListProjects", () => window.PortalInstructionClient.execute("ListProjects", {}), options);
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

  function forkControls(host) {
    if (host._memoryControls) return host._memoryControls;
    const editor = window.PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: window.PortalUI.escapeHtml || (s => String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`))}).createRuntimeSettingsEditor();
    const label = document.createElement("label"), method = document.createElement("select"), status = document.createElement("p");
    label.className = "residentFormRow"; label.append("记忆交接", method);
    host.replaceChildren(editor.element, label, status);
    let generation = 0, ready = false, lastKey = "";
    const control = {
      open(residentId, provider, route) {
        const key = JSON.stringify([residentId, provider, route]);
        if (key === lastKey) return;
        lastKey = key; ready = false;
        const ticket = ++generation;
        editor.element.disabled = true; method.disabled = true; status.textContent = "正在读取账号、模型与交接能力…";
        window.PortalResidentService.sessionRestartOptions(residentId)
          .then(async config => {
            const account = config.accounts?.[provider]?.find(row => row.id === route || row.account_id === route);
            if (!account?.account_id) throw new Error("目标账号已不可用，请重新选择");
            return [config, account, await runtimeCatalog(account.account_id)];
          })
          .then(([config, account, catalog]) => {
            if (ticket !== generation) return;
            const choices = account?.context_options || [];
            method.replaceChildren(...choices.map(row => {const o = new Option(row.label, row.id); o.disabled = !row.enabled; return o;}));
            method.value = choices.find(row => row.enabled)?.id || "";
            if (!method.value) throw new Error("账号交接能力不可用，请重新打开窗口");
            const current = config.source?.provider === provider ? config.runtime_settings || {} : {};
            const model = catalog.models?.includes(current.model) ? current.model : catalog.default_model;
            const effort = catalog.efforts_by_model?.[model]?.includes(current.reasoning_effort) ? current.reasoning_effort : catalog.default_efforts_by_model?.[model] || catalog.default_reasoning_effort || "";
            editor.open({model, reasoning_effort: effort}, {catalog, modelOnly: true, showApiAccount: false, showAccountSelection: false, hint: "仅创建分支；第一条新消息才交接记忆，不提前调用模型。"});
            editor.element.disabled = false; method.disabled = false; ready = true; status.textContent = "";
          }).catch(error => {if (ticket === generation) {lastKey = ""; status.textContent = String(error.message || error);}});
      },
      read() {
        if (!ready) throw new Error("请等待目标账号配置读取完成");
        const values = editor.read({complete: true});
        return {fork_mode: method.value, model: values.model, ...(values.reasoning_effort !== undefined ? {reasoning_effort: values.reasoning_effort} : {})};
      },
      reset() { generation += 1; ready = false; lastKey = ""; },
    };
    host._memoryControls = control;
    return control;
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
    renderForkProvider, forkControls,
    invalidate,
    normalizeRuntimeCatalog,
    projectCatalog,
    residents,
    routeRuntimeCatalog,
    runtimeCatalog,
    users,
  });
})();
