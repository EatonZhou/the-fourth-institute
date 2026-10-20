(function () {
  "use strict";

  if (window.PortalPageState) return;

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function usernameFrom(source) {
    if (typeof source === "function") return source() || "anonymous";
    return source || "anonymous";
  }

  function create(page, options = {}) {
    const pageId = String(page || options.page || "page");
    const user = () => usernameFrom(options.user || options.getUser);
    let pendingPatch = {};
    let pendingUser = "";
    let pendingTransactionId = "";
    let writeTimer = 0;
    let destroyed = false;
    const defaults = () => {
      const value = typeof options.defaults === "function" ? options.defaults() : options.defaults;
      return isPlainObject(value) ? value : {};
    };
    const snapshot = () => {
      const value = typeof options.snapshot === "function" ? options.snapshot() : {};
      return isPlainObject(value) ? value : {};
    };
    const flushPending = () => {
      window.clearTimeout(writeTimer);
      writeTimer = 0;
      const next = pendingPatch;
      const targetUser = pendingUser;
      const transactionId = pendingTransactionId;
      pendingPatch = {};
      pendingUser = "";
      pendingTransactionId = "";
      if (!targetUser || !Object.keys(next).length) return {};
      return window.PortalState.writeForTransaction(transactionId, pageId, targetUser, next);
    };
    const scheduleWrite = (patch, delay) => {
      if (destroyed) return;
      const targetUser = user();
      const transactionId = window.PortalState.currentTransactionId();
      if (
        pendingUser
        && (
          pendingUser !== targetUser
          || pendingTransactionId !== transactionId
        )
      ) flushPending();
      pendingUser = targetUser;
      pendingTransactionId = transactionId;
      pendingPatch = { ...pendingPatch, ...(isPlainObject(patch) ? patch : {}) };
      window.clearTimeout(writeTimer);
      writeTimer = window.setTimeout(() => flushPending(), Number(delay || options.delay || 160));
    };
    const handlePageHide = () => flushPending();
    window.addEventListener("pagehide", handlePageHide);
    return {
      read(extraDefaults = {}) {
        return window.PortalState.read(pageId, user(), { ...defaults(), ...(isPlainObject(extraDefaults) ? extraDefaults : {}) });
      },
      write(patch = {}) {
        if (destroyed) return {};
        flushPending();
        const transactionId = window.PortalState.currentTransactionId();
        return window.PortalState.writeForTransaction(
          transactionId,
          pageId,
          user(),
          patch,
        );
      },
      replace(value = {}) {
        if (destroyed) return {};
        flushPending();
        const transactionId = window.PortalState.currentTransactionId();
        return window.PortalState.replaceForTransaction(
          transactionId,
          pageId,
          user(),
          value,
        );
      },
      writeSnapshot() {
        if (destroyed) return {};
        flushPending();
        const transactionId = window.PortalState.currentTransactionId();
        return window.PortalState.writeForTransaction(
          transactionId,
          pageId,
          user(),
          snapshot(),
        );
      },
      debounceSnapshot(delay = options.delay || 160) {
        scheduleWrite(snapshot(), delay);
      },
      debounceWrite(patch = {}, delay = options.delay || 160) {
        scheduleWrite(patch, delay);
      },
      flush() {
        return flushPending();
      },
      destroy() {
        if (destroyed) return {};
        const result = flushPending();
        destroyed = true;
        window.removeEventListener("pagehide", handlePageHide);
        return result;
      },
      setToArray(value) {
        return window.PortalState.setToArray(value);
      },
      arrayToSet(value) {
        return window.PortalState.arrayToSet(value);
      },
    };
  }

  window.PortalPageState = { create };
})();
