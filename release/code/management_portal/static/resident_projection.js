(function () {
  "use strict";

  if (window.PortalResidentProjection) return;

  const entries = new Map();
  const invalidationGenerations = new Map();
  const subscribers = new Map();
  const pendingNotifications = new Set();
  const retryTimers = new Map();
  const retryDelays = new Map();
  const MAX_CACHED_RESIDENTS = 32;
  const MAX_INVALIDATION_GENERATIONS = 256;
  let globalInvalidationGeneration = 0;

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function keyFor(residentId) {
    return String(residentId || "").trim();
  }

  function clearRetry(key) {
    if (retryTimers.has(key)) window.clearTimeout(retryTimers.get(key));
    retryTimers.delete(key);
    retryDelays.delete(key);
  }

  function scheduleRetry(key, error) {
    const status = Number(error?.status || 0);
    const transient = status === 408 || status === 429 || (status >= 500 && status <= 599)
      || (!status && (error instanceof TypeError || ["NetworkError", "TimeoutError", "AbortError"].includes(error?.name)));
    if (!transient || !subscribers.has(key) || retryTimers.has(key)) return;
    const delay = retryDelays.get(key) || 1000;
    retryDelays.set(key, Math.min(delay * 2, 10000));
    retryTimers.set(key, window.setTimeout(() => {
      retryTimers.delete(key);
      scheduleNotification(key);
    }, delay));
  }

  function generationFor(key) {
    return `${globalInvalidationGeneration}:${invalidationGenerations.get(key) || 0}`;
  }

  function touch(key, entry) {
    entries.delete(key);
    entries.set(key, entry);
    while (entries.size > MAX_CACHED_RESIDENTS) {
      const oldest = [...entries.entries()].find(([, candidate]) => !candidate.loading);
      if (!oldest) break;
      entries.delete(oldest[0]);
    }
  }

  function peek(residentId) {
    const key = keyFor(residentId);
    const entry = entries.get(key);
    if (!entry?.value) return null;
    touch(key, entry);
    return clone(entry.value);
  }

  function load(residentId, options = {}) {
    const key = keyFor(residentId);
    if (!key) return Promise.reject(new Error("resident id required"));
    let entry = entries.get(key);
    if (!entry) entry = { value: null, loading: null };
    touch(key, entry);
    if (!options.force && entry.value) return Promise.resolve(clone(entry.value));
    if (entry.loading) return entry.loading.then(clone);
    const requestGeneration = generationFor(key);
    const request = window.PortalResidentService.projection({ residentId: key }).then(value => {
      if (String(value?.resident_id || "") !== key) {
        throw new Error("Resident projection target mismatch");
      }
      if (generationFor(key) !== requestGeneration) {
        return load(key, { force: true });
      }
      entry.value = clone(value);
      clearRetry(key);
      touch(key, entry);
      return value;
    }).finally(() => {
      if (entry.loading === request) entry.loading = null;
      if (entries.get(key) === entry) touch(key, entry);
    });
    entry.loading = request;
    return request.then(clone);
  }

  function invalidate(residentId) {
    const key = keyFor(residentId);
    if (key) {
      clearRetry(key);
      const nextGeneration = (invalidationGenerations.get(key) || 0) + 1;
      invalidationGenerations.delete(key);
      invalidationGenerations.set(key, nextGeneration);
      while (invalidationGenerations.size > MAX_INVALIDATION_GENERATIONS) {
        invalidationGenerations.delete(invalidationGenerations.keys().next().value);
      }
      entries.delete(key);
      scheduleNotification(key);
    } else {
      for (const retryKey of retryTimers.keys()) clearRetry(retryKey);
      retryDelays.clear();
      globalInvalidationGeneration += 1;
      invalidationGenerations.clear();
      entries.clear();
      for (const residentKey of subscribers.keys()) scheduleNotification(residentKey);
    }
  }

  function scheduleNotification(key) {
    if (!subscribers.has(key) || pendingNotifications.has(key)) return;
    pendingNotifications.add(key);
    queueMicrotask(async () => {
      pendingNotifications.delete(key);
      if (!subscribers.has(key)) return;
      let update;
      try { update = { detail: await load(key) }; }
      catch (error) {
        scheduleRetry(key, error);
        update = { error };
      }
      for (const listener of subscribers.get(key) || []) {
        try { listener(update.error ? update : { detail: clone(update.detail) }); }
        catch (error) { console.error(window.PortalI18n.t("Resident projection subscriber failed"), error); }
      }
    });
  }

  function subscribe(residentId, listener) {
    const key = keyFor(residentId);
    if (!key || typeof listener !== "function") throw new Error("Resident projection subscriber required");
    if (!subscribers.has(key)) subscribers.set(key, new Set());
    subscribers.get(key).add(listener);
    // Also closes the gap between the initial read and mounting the consumer.
    scheduleNotification(key);
    return () => {
      const listeners = subscribers.get(key);
      listeners?.delete(listener);
      if (!listeners?.size) {
        subscribers.delete(key);
        clearRetry(key);
      }
    };
  }

  window.addEventListener("portal:change", event => {
    const detail = event?.detail || {};
    for (const [key, scopes] of Object.entries(detail.resident_changes || {})) {
      if (!Array.isArray(scopes)) continue;
      const projection = entries.get(key)?.value;
      const sessionUnbound = projection
        && !String(projection.provider_session?.id || "").trim();
      if (
        scopes.includes("identity")
        || scopes.includes("session_restart")
        || (sessionUnbound && scopes.includes("runtime"))
      ) {
        invalidate(key);
      }
    }
    if (detail.snapshot_required) invalidate();
  });
  window.addEventListener("portal:events-ready", event => {
    if (event?.detail?.reconnected) invalidate();
  });
  window.addEventListener(
    window.PortalIdentity?.eventName || "portal:identity-changed",
    () => invalidate(),
  );

  window.PortalResidentProjection = { load, peek, invalidate, subscribe };
})();
