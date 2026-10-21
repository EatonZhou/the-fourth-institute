(function () {
  "use strict";

  if (window.PortalIdentity) return;

  const SESSION_USER_KEY = window.PortalCompatibility.identifier("nav_user");
  const CHANGE_EVENT = "portal:identity-changed";
  const HISTORY_RESTORED_EVENT = "portal:history-restored";
  const bootstrap = window.__PORTAL_IDENTITY_BOOTSTRAP__;
  let currentIdentity = bootstrap && typeof bootstrap === "object"
    ? { ...bootstrap }
    : readCachedIdentity();
  let identityPromise = null;
  let preferencesPromise = null;
  let preferenceSaveTimer = 0;
  let pendingPreferencePatch = {};
  let pendingPreferenceWaiters = [];
  let pendingPreferenceKeepalive = false;
  let preferenceSavePromise = null;
  let preferencesRevision = 0;
  let preferencesLoadedAt = Object.prototype.hasOwnProperty.call(currentIdentity, "preferences")
    ? Date.now()
    : 0;

  function passwordStrengthError(password) {
    if (!password.length) return "Enter a password.";
    if (password.length > 256) return "Use no more than 256 characters.";
    const characterClasses = [/\p{Ll}/u, /\p{Lu}/u, /\p{N}/u, /[^\p{L}\p{N}]/u]
      .filter(pattern => pattern.test(password)).length;
    if (characterClasses < 3) {
      return "Include at least three of: uppercase letters, lowercase letters, numbers, and symbols.";
    }
    return "";
  }

  function clone(value) {
    if (value === undefined || value === null) return value;
    if (typeof structuredClone === "function") {
      try {
        return structuredClone(value);
      } catch (_error) {
        // Identity payloads are JSON-compatible; use the portable fallback below.
      }
    }
    return JSON.parse(JSON.stringify(value));
  }

  function mergeObjects(base, patch) {
    const output = base && typeof base === "object" && !Array.isArray(base) ? clone(base) : {};
    for (const [key, value] of Object.entries(patch && typeof patch === "object" ? patch : {})) {
      output[key] = value && typeof value === "object" && !Array.isArray(value)
        ? mergeObjects(output[key], value)
        : clone(value);
    }
    return output;
  }

  function snapshot() {
    return clone(currentIdentity) || {};
  }

  function readCachedIdentity() {
    const username = String(window.PortalState.storage.readText(
      SESSION_USER_KEY,
      "",
      { scope: "session" },
    ) || "").trim();
    return username ? { username } : {};
  }

  function remember(identity = {}) {
    const next = identity && typeof identity === "object" ? (clone(identity) || {}) : {};
    const username = String(next.username || "").trim();
    currentIdentity = next;
    if (Object.prototype.hasOwnProperty.call(next, "preferences")) {
      preferencesLoadedAt = Date.now();
      preferencesRevision += 1;
    } else {
      preferencesLoadedAt = 0;
    }
    if (username) window.PortalState.storage.writeText(SESSION_USER_KEY, username, { scope: "session" });
    else window.PortalState.storage.remove(SESSION_USER_KEY, { scope: "session" });
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { identity: snapshot() } }));
    return snapshot();
  }

  function load(options = {}) {
    if (options.force === true) identityPromise = null;
    if (!identityPromise) {
      identityPromise = window.PortalAPI.get("/api/me", { cache: "no-store" })
        .then(identity => {
          remember(identity);
          return true;
        })
        .catch(error => {
          identityPromise = null;
          throw error;
        });
    }
    return identityPromise.then(snapshot);
  }

  async function revalidateHistoryRestore(event) {
    if (!event.persisted) return;
    try {
      const identity = await load({ force: true });
      window.dispatchEvent(new CustomEvent(HISTORY_RESTORED_EVENT, {
        detail: { identity, persisted: true },
      }));
    } catch (error) {
      window.dispatchEvent(new CustomEvent(`${HISTORY_RESTORED_EVENT}-error`, {
        detail: { error },
      }));
    }
  }

  function preferences() {
    return clone(currentIdentity.preferences) || {};
  }

  function updatePreferences(value = {}) {
    const next = value && typeof value === "object" && !Array.isArray(value)
      ? clone(value)
      : {};
    currentIdentity = { ...currentIdentity, preferences: next };
    preferencesLoadedAt = Date.now();
    preferencesRevision += 1;
    return preferences();
  }

  function loadPreferences(options = {}) {
    if (preferencesPromise) return preferencesPromise.then(clone);
    const force = options.force === true;
    const configuredMaxAge = Number(options.maxAgeMs);
    const maxAgeMs = Number.isFinite(configuredMaxAge) && configuredMaxAge >= 0
      ? configuredMaxAge
      : 5000;
    let request;
    request = Promise.resolve()
      .then(async () => {
        try {
          await load();
        } catch (_error) {
          // A transient /api/me failure must not prevent a preferences retry.
        }
        const isFresh = maxAgeMs > 0
          && preferencesLoadedAt > 0
          && Date.now() - preferencesLoadedAt <= maxAgeMs;
        if (!force && isFresh) return preferences();

        const requestRevision = preferencesRevision;
        const payload = await window.PortalAPI.get("/api/preferences", { cache: "no-store" });
        if (requestRevision !== preferencesRevision) return preferences();
        return updatePreferences(payload?.preferences || {});
      })
      .finally(() => {
        if (preferencesPromise === request) preferencesPromise = null;
      });
    preferencesPromise = request;
    return request.then(clone);
  }

  function flushPreferenceSaves() {
    if (preferenceSaveTimer) window.clearTimeout(preferenceSaveTimer);
    preferenceSaveTimer = 0;
    if (preferenceSavePromise || !Object.keys(pendingPreferencePatch).length) {
      return preferenceSavePromise || Promise.resolve(preferences());
    }
    const patch = pendingPreferencePatch;
    const waiters = pendingPreferenceWaiters;
    const keepalive = pendingPreferenceKeepalive;
    pendingPreferencePatch = {};
    pendingPreferenceWaiters = [];
    pendingPreferenceKeepalive = false;
    preferenceSavePromise = window.PortalAPI.post(
      "/api/preferences",
      { preferences: patch },
      { keepalive },
    )
      .then(result => {
        const saved = updatePreferences(result?.preferences || mergeObjects(preferences(), patch));
        waiters.forEach(waiter => waiter.resolve(saved));
        return saved;
      })
      .catch(error => {
        waiters.forEach(waiter => waiter.reject(error));
        throw error;
      })
      .finally(() => {
        preferenceSavePromise = null;
        if (Object.keys(pendingPreferencePatch).length) flushPreferenceSaves().catch(() => {});
      });
    return preferenceSavePromise;
  }

  function savePreferences(patch = {}, options = {}) {
    pendingPreferencePatch = mergeObjects(pendingPreferencePatch, patch);
    if (options.keepalive === true) pendingPreferenceKeepalive = true;
    const promise = new Promise((resolve, reject) => pendingPreferenceWaiters.push({ resolve, reject }));
    if (options.immediate === true) {
      flushPreferenceSaves().catch(() => {});
      return promise;
    }
    if (preferenceSaveTimer) window.clearTimeout(preferenceSaveTimer);
    preferenceSaveTimer = window.setTimeout(
      () => flushPreferenceSaves().catch(() => {}),
      Math.max(250, Number(options.delayMs || 1500)),
    );
    return promise;
  }

  function listUIAssociations(namespace) {
    const query = new URLSearchParams({ namespace: String(namespace || "").trim() });
    return window.PortalAPI.get(
      `/api/ui-state/associations?${query.toString()}`,
      { cache: "no-store" },
    );
  }

  function putUIAssociation(namespace, key, value = {}) {
    return window.PortalAPI.post("/api/ui-state/associations/put", {
      namespace,
      key,
      value,
    });
  }

  function removeUIAssociation(namespace, key) {
    return window.PortalAPI.post("/api/ui-state/associations/remove", {
      namespace,
      key,
    });
  }

  function clear() {
    identityPromise = null;
    preferencesPromise = null;
    preferencesRevision += 1;
    preferencesLoadedAt = 0;
    return remember({});
  }

  // With the permission system off there are no credentials or policies to show.
  const permissionSystemOff = () => load().then(identity => identity?.access?.permission_system === "open");

  // Instructions about the signed-in User name it as user_id; without a username nothing is sent.
  const userId = () => load().then(identity => {
    const value = String(identity?.username || "").trim();
    if (value) return value;
    const message = "当前用户读取失败，请刷新页面后重试";
    throw new Error(window.PortalI18n?.t ? window.PortalI18n.t(message) : message);
  });

  window.PortalIdentity = Object.freeze({
    permissionSystemOff,
    passwordStrengthError,
    passwordRequirements: "Include at least three of: uppercase letters, lowercase letters, numbers, and symbols.",
    clear,
    eventName: CHANGE_EVENT,
    load,
    loadPreferences,
    listUIAssociations,
    putUIAssociation,
    removeUIAssociation,
    peek: snapshot,
    preferences,
    savePreferences,
    username: () => String(currentIdentity.username || "").trim(),
    userId,
  });
  window.addEventListener("pageshow", revalidateHistoryRestore);
})();
