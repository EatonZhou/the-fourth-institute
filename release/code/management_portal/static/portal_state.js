(function (global) {
  "use strict";

  // Browser storage, channels and transfer types belong to the application.
  // Deployment configuration cannot redirect them to historical identifiers.
  const identifiers = Object.freeze({
    page_state: "tfi.portal.pageState.v1",
    interface_history: "tfi.portal.interfaceHistory.v1",
    live_tour_session: "tfi.portal.liveTour.v1",
    language: "tfi.portal.language",
    theme: "tfi.portal.theme",
    nav_user: "tfi.portal.navUser.v1",
    usage_cached_input: "tfi.home.tokenUsage.includeCachedInput.v3",
    usage_grouping: "tfi.home.tokenUsage.grouping.v1",
    usage_filters: "tfi.home.tokenUsage.filters.v1",
    usage_database: "tfi.home.tokenUsage.v1",
    conversation_snapshot: "tfi.portal.conversations.v7:",
    conversation_recent: "tfi.portal.conversations.recent.v1:",
    preview_word_wrap: "tfi.portal.previewWordWrap.v1",
    overlay_runtime: "tfi.portal.overlay.runtime",
    refresh_channel: "tfi-portal-refresh",
    refresh_lock_prefix: "tfi-refresh:",
    backup_refresh_channel: "tfi-resource-backup-refresh",
    conversation_refresh_channel: "tfi-agent-conversation-index",
    events_channel: "tfi-portal-events",
    events_leader_lock: "tfi-portal-events-leader",
    nav_transfer: "application/x-tfi-portal-nav",
    template_transfer: "application/x-tfi-template",
    primitive_transfer: "application/x-tfi-hatg-primitive",
    demo_frame_scope: "tfi.portal-demo-frame.v1",
  });

  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

  function identifier(key) {
    if (!own(identifiers, key)) throw new Error(`Unknown browser identifier: ${key}`);
    return identifiers[key];
  }
  function schemaMatches(value, canonical) {
    if (typeof value !== "string" || typeof canonical !== "string" || !canonical.startsWith("tfi.")) return false;
    return value === canonical;
  }

  global.PortalCompatibility = Object.freeze({ identifier, schemaMatches });
})(window);

(function () {
  const ROOT_KEY = window.PortalCompatibility.identifier("page_state");
  const NAV_KEY = window.PortalCompatibility.identifier("interface_history");
  let ephemeralTransaction = null;
  let interfaceNavigator = null;

  function cleanPart(value, fallback) {
    const text = String(value || "").trim();
    return text || fallback;
  }

  function storageKey(page, user) {
    return `${ROOT_KEY}.${cleanPart(user, "anonymous")}.${cleanPart(page, "page")}`;
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function storageArea(scope = "local") {
    return scope === "session" ? window.sessionStorage : window.localStorage;
  }

  function scopedStorageKey(key) {
    const namespace = window.__PORTAL_SITE__?.storage_namespace;
    // Historical unscoped values have no provable installation owner. Leave
    // them intact, but do not adopt them into a newly opened installation.
    return namespace ? `tfi.installation.${namespace}:${key}` : key;
  }

  function readText(key, fallback = "", options = {}) {
    if (!key) return fallback;
    try {
      const value = storageArea(options.scope).getItem(scopedStorageKey(key));
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function writeText(key, value, options = {}) {
    if (!key) return value;
    try {
      storageArea(options.scope).setItem(scopedStorageKey(key), String(value));
    } catch {
      // Persistence is best-effort in private or restricted browser modes.
    }
    return value;
  }

  function removeStored(key, options = {}) {
    if (!key) return false;
    try {
      storageArea(options.scope).removeItem(scopedStorageKey(key));
      return true;
    } catch {
      return false;
    }
  }

  function readJson(key, fallback = null, options = {}) {
    const raw = readText(key, "", options);
    if (!raw) return fallback;
    try {
      const value = JSON.parse(raw);
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function writeJson(key, value, options = {}) {
    if (!key) return value;
    try {
      storageArea(options.scope).setItem(scopedStorageKey(key), JSON.stringify(value));
    } catch {
      // Persistence is best-effort in private or restricted browser modes.
    }
    return value;
  }

  const storage = Object.freeze({
    key: scopedStorageKey,
    readText,
    writeText,
    remove: removeStored,
    readJson,
    writeJson,
  });

  function safeRead(key) {
    const data = readJson(key, {});
    return isPlainObject(data) ? data : {};
  }

  function safeWrite(key, value) {
    writeJson(key, isPlainObject(value) ? value : {});
  }

  function storedValue(key) {
    if (ephemeralTransaction?.values.has(key)) {
      return ephemeralTransaction.values.get(key);
    }
    return safeRead(key);
  }

  function read(page, user, defaults) {
    return {
      ...(isPlainObject(defaults) ? defaults : {}),
      ...storedValue(storageKey(page, user)),
    };
  }

  function writeForTransaction(transactionId, page, user, patch) {
    const key = storageKey(page, user);
    const cleanTransactionId = String(transactionId || "");
    if (cleanTransactionId) {
      if (cleanTransactionId !== ephemeralTransaction?.id) {
        return safeRead(key);
      }
      const current = storedValue(key);
      const next = { ...current, ...(isPlainObject(patch) ? patch : {}) };
      ephemeralTransaction.values.set(key, next);
      return next;
    }
    const current = safeRead(key);
    const next = { ...current, ...(isPlainObject(patch) ? patch : {}) };
    safeWrite(key, next);
    return next;
  }

  function write(page, user, patch) {
    return writeForTransaction(currentTransactionId(), page, user, patch);
  }

  function replaceForTransaction(transactionId, page, user, value) {
    const key = storageKey(page, user);
    const next = isPlainObject(value) ? value : {};
    const cleanTransactionId = String(transactionId || "");
    if (cleanTransactionId) {
      if (cleanTransactionId !== ephemeralTransaction?.id) {
        return safeRead(key);
      }
      ephemeralTransaction.values.set(key, next);
      return next;
    }
    safeWrite(key, next);
    return next;
  }

  function replace(page, user, value) {
    return replaceForTransaction(currentTransactionId(), page, user, value);
  }

  function currentTransactionId() {
    return String(ephemeralTransaction?.id || "");
  }

  function beginEphemeral(id) {
    const cleanId = String(id || "").trim();
    if (!cleanId) throw new Error("PortalState ephemeral transaction id is required");
    if (ephemeralTransaction && ephemeralTransaction.id !== cleanId) {
      throw new Error("A different PortalState ephemeral transaction is already active");
    }
    if (!ephemeralTransaction) {
      ephemeralTransaction = {
        id: cleanId,
        values: new Map(),
      };
    }
    return cleanId;
  }

  function rollbackEphemeral(id) {
    const cleanId = String(id || "").trim();
    if (!ephemeralTransaction || ephemeralTransaction.id !== cleanId) return false;
    ephemeralTransaction = null;
    return true;
  }

  function setToArray(value) {
    return Array.from(value instanceof Set ? value : new Set()).map(String).filter(Boolean);
  }

  function arrayToSet(value) {
    return new Set((Array.isArray(value) ? value : []).map(String).filter(Boolean));
  }

  function currentInterfaceUrl() {
    return `${window.location.pathname || "/"}${window.location.search || ""}${window.location.hash || ""}`;
  }

  // Ctrl/⌘+Tab and Ctrl/⌘+` return to the interface this tab left last. Each
  // tab keeps its own record; frames (resource details) are not interfaces;
  // and a page that changes its interface in place (the Society page switching
  // Resident) reports the switch, so the record holds where the user really was.
  const topLevelWindow = (() => {
    try {
      return window.top === window;
    } catch {
      return false;
    }
  })();

  function readInterfaceHistory() {
    const data = readJson(NAV_KEY, {}, { scope: "session" });
    return isPlainObject(data) ? data : {};
  }

  function writeInterfaceHistory(value) {
    if (!topLevelWindow) return;
    writeJson(NAV_KEY, isPlainObject(value) ? value : {}, { scope: "session" });
  }

  // The page's address changed without leaving the interface (a query
  // parameter dropped after use): the current entry follows it.
  function keepCurrentInterface() {
    const history = readInterfaceHistory();
    const current = isPlainObject(history.current) ? history.current : null;
    if (!current) return;
    writeInterfaceHistory({
      ...history,
      current: { ...current, url: currentInterfaceUrl(), title: document.title || current.title || "", at: Date.now() },
    });
  }

  function noteInterfaceVisit(url = currentInterfaceUrl()) {
    const currentUrl = String(url || currentInterfaceUrl() || "/");
    const history = readInterfaceHistory();
    const current = isPlainObject(history.current) ? history.current : {};
    const previous = isPlainObject(history.previous) ? history.previous : {};
    if (String(current.url || "") === currentUrl) {
      writeInterfaceHistory({
        ...history,
        current: { ...current, url: currentUrl, title: document.title || current.title || "", at: Date.now() },
      });
      return;
    }
    writeInterfaceHistory({
      previous: current.url ? current : previous,
      current: { url: currentUrl, title: document.title || "", at: Date.now() },
    });
  }

  function previousInterfaceUrl() {
    const history = readInterfaceHistory();
    const previous = isPlainObject(history.previous) ? history.previous : {};
    const url = String(previous.url || "").trim();
    return url && url !== currentInterfaceUrl() ? url : "";
  }

  function goPreviousInterface() {
    if (interfaceNavigator?.canGoBack()) return interfaceNavigator.goBack();
    const url = previousInterfaceUrl();
    if (!url) return false;
    window.location.assign(url);
    return true;
  }

  function registerInterfaceNavigator(navigator) {
    if (typeof navigator?.canGoBack !== "function" || typeof navigator?.goBack !== "function") {
      throw new TypeError("Interface navigator requires canGoBack() and goBack().");
    }
    if (interfaceNavigator) throw new Error("An interface navigator is already registered.");
    interfaceNavigator = navigator;
    return () => {
      if (interfaceNavigator === navigator) interfaceNavigator = null;
    };
  }

  function bindPreviousInterfaceShortcut() {
    // State-only documents (such as authentication pages) have no command shell.
    window.PortalCommandRegistry?.root.register({
      id: "portal.interface.previous",
      title: window.PortalI18n.t("返回上一个界面"),
      category: "Portal",
      keybindings: ["Ctrl+Tab", "Meta+Tab", "Ctrl+`", "Meta+`", "Ctrl+Backquote", "Meta+Backquote"],
      allowInInput: true,
      enabled: () => Boolean(interfaceNavigator?.canGoBack() || previousInterfaceUrl()),
      execute: goPreviousInterface,
    });
  }

  noteInterfaceVisit();
  // Shared across tabs before 10-12, when another tab's page load replaced it.
  removeStored(NAV_KEY);
  window.addEventListener("pagehide", keepCurrentInterface);
  // A page loading its scripts deferred runs this file before the registry;
  // every deferred script has run by DOMContentLoaded.
  if (document.readyState === "loading" || !window.PortalCommandRegistry) {
    document.addEventListener("DOMContentLoaded", bindPreviousInterfaceShortcut, { once: true });
  } else {
    bindPreviousInterfaceShortcut();
  }

  const LIVE_TOUR_SESSION_KEY = window.PortalCompatibility.identifier("live_tour_session");
  const liveTourSessionStore = window.PortalLiveTourSessionStore || Object.freeze({
    key: LIVE_TOUR_SESSION_KEY,
    readRaw() {
      return window.PortalState.storage.readText(LIVE_TOUR_SESSION_KEY, "", { scope: "session" });
    },
    write(value) {
      window.PortalState.storage.writeJson(LIVE_TOUR_SESSION_KEY, value, { scope: "session" });
      return value;
    },
    clear() {
      window.PortalState.storage.remove(LIVE_TOUR_SESSION_KEY, { scope: "session" });
    },
  });
  window.PortalLiveTourSessionStore = liveTourSessionStore;

  window.PortalState = {
    storage,
    read,
    write,
    replace,
    beginEphemeral,
    rollbackEphemeral,
    currentTransactionId,
    writeForTransaction,
    replaceForTransaction,
    setToArray,
    arrayToSet,
    interfaceHistory: Object.freeze({
      keepCurrent: keepCurrentInterface,
      visit: noteInterfaceVisit,
      previousUrl: previousInterfaceUrl,
      registerNavigator: registerInterfaceNavigator,
    }),
  };

  // A Live tour started in Test goes on in the page it opens.  Pages load its
  // controller only while a tour session exists, after their own scripts
  // (deferred ones included) have run.
  function resumeLiveTour() {
    const primitives = window.PortalUIPrimitives;
    if (!primitives || window.PortalLiveTour || !liveTourSessionStore.readRaw()) return;
    primitives.loadAsset("/static/portal_live_tour.js", { ready: () => !!window.PortalLiveTour })
      .catch(error => console.error(error));
  }
  if (document.readyState === "complete") {
    resumeLiveTour();
  } else {
    document.addEventListener("DOMContentLoaded", resumeLiveTour, { once: true });
    window.addEventListener("load", resumeLiveTour, { once: true });
  }
})();
