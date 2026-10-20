(function (global) {
  "use strict";

  if (global.PortalWorkbenchContext) return;

  const SELECTION_CHANNELS = Object.freeze([
    "inspectionTarget",
    "conversationTarget",
    "documentTarget",
    "graphSelection",
    "commandContext",
  ]);

  function cloneRecord(value) {
    return value && typeof value === "object" ? { ...value } : {};
  }

  function create(options = {}) {
    const workbenchId = String(options.workbenchId || options.id || "").trim();
    if (!workbenchId) throw new TypeError("PortalWorkbenchContext.create requires workbenchId.");
    let values = cloneRecord(options.initial);
    let revision = 0;
    let disposed = false;
    const listeners = new Set();

    const snapshot = () => Object.freeze({
      workbenchId,
      revision,
      values: Object.freeze({ ...values }),
    });

    const publish = (changedChannels, meta = {}) => {
      if (!changedChannels.length) return snapshot();
      revision += 1;
      const change = Object.freeze({
        ...snapshot(),
        changedChannels: Object.freeze([...changedChannels]),
        source: String(meta.source || "api"),
        reason: String(meta.reason || "update"),
      });
      listeners.forEach(entry => {
        if (entry.channels !== "*" && !changedChannels.some(channel => entry.channels.has(channel))) return;
        try { entry.listener(change); }
        catch (error) { console.error(window.PortalI18n.t("PortalWorkbenchContext listener failed"), error); }
      });
      document.dispatchEvent(new CustomEvent("portal:workbench-context-change", { detail: change }));
      return change;
    };

    const api = Object.freeze({
      workbenchId,
      get revision() { return revision; },
      get(channel) { return values[String(channel || "")]; },
      snapshot,
      set(channel, value, meta = {}) {
        if (disposed) return snapshot();
        const key = String(channel || "").trim();
        if (!key) throw new TypeError("PortalWorkbenchContext.set requires channel.");
        if (Object.is(values[key], value)) return snapshot();
        values = { ...values, [key]: value };
        return publish([key], meta);
      },
      patch(next = {}, meta = {}) {
        if (disposed) return snapshot();
        const changed = Object.keys(next).filter(key => !Object.is(values[key], next[key]));
        if (!changed.length) return snapshot();
        values = { ...values, ...next };
        return publish(changed, meta);
      },
      replace(next = {}, meta = {}) {
        if (disposed) return snapshot();
        const replacement = cloneRecord(next);
        const changed = [...new Set([...Object.keys(values), ...Object.keys(replacement)])]
          .filter(key => !Object.is(values[key], replacement[key]));
        if (!changed.length) return snapshot();
        values = replacement;
        return publish(changed, meta);
      },
      select(channel, value, meta = {}) {
        const key = String(channel || "");
        if (!SELECTION_CHANNELS.includes(key)) {
          throw new TypeError(`Unknown Portal Workbench selection channel: ${key}`);
        }
        return api.set(key, value, { ...meta, reason: meta.reason || "selection" });
      },
      subscribe(channels, listener, options = {}) {
        if (disposed || typeof listener !== "function") return () => {};
        const normalized = channels === "*"
          ? "*"
          : new Set((Array.isArray(channels) ? channels : [channels]).map(String).filter(Boolean));
        const entry = { channels: normalized, listener };
        listeners.add(entry);
        if (options.immediate === true) {
          listener(Object.freeze({
            ...snapshot(),
            changedChannels: Object.freeze([]),
            source: "subscribe",
            reason: "initial",
          }));
        }
        return () => listeners.delete(entry);
      },
      dispose() {
        disposed = true;
        listeners.clear();
      },
    });
    return api;
  }

  global.PortalWorkbenchContext = Object.freeze({ create });
})(window);
