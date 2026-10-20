(function (global) {
  "use strict";

  if (global.PortalEditorOpenService) return;

  const OPEN_MODES = new Set(["reuse", "new-tab", "side"]);

  function normalizeIntent(raw = {}) {
    const resource = raw.resource && typeof raw.resource === "object" ? { ...raw.resource } : {};
    const type = String(raw.type || resource.kind || resource.type || "").trim();
    if (!type) throw new TypeError("Editor open intent requires resource.kind.");
    const mode = OPEN_MODES.has(String(raw.mode || "")) ? String(raw.mode) : "reuse";
    const side = ["left", "right", "top", "bottom"].includes(String(raw.side || ""))
      ? String(raw.side)
      : "right";
    return Object.freeze({
      ...raw,
      type,
      mode,
      side,
      resource: Object.freeze({ ...resource, kind: type }),
    });
  }

  function resolve(input = {}) {
    const intent = normalizeIntent(input.intent || input);
    const instances = Array.isArray(input.instances) ? input.instances : [];
    const byId = new Map(instances.map(item => [String(item.id || ""), item]));
    const explicit = byId.get(String(intent.targetInstanceId || ""));
    const active = byId.get(String(input.activeTargetInstanceId || ""));
    const singleton = instances.find(item => (
      String(item?.type || "") === intent.type
      && item.singleton === true
    ));
    if (singleton) {
      return Object.freeze({ action: "reuse", instanceId: String(singleton.id), intent });
    }
    if (intent.mode === "reuse") {
      const preferred = [explicit, active, ...instances.slice().reverse()].find(item => (
        item
        && String(item.type || "") === intent.type
        && item.reusable !== false
        && item.locked !== true
        && item.dirty !== true
      ));
      if (preferred) {
        return Object.freeze({ action: "reuse", instanceId: String(preferred.id), intent });
      }
    }
    const stackId = String(
      intent.targetStackId
      || explicit?.stackId
      || active?.stackId
      || input.activeTargetStackId
      || input.focusedStackId
      || "",
    );
    return Object.freeze({
      action: "create",
      stackId,
      zone: intent.mode === "side" ? intent.side : "center",
      intent,
    });
  }

  function create(options = {}) {
    if (typeof options.state !== "function") {
      throw new TypeError("PortalEditorOpenService.create requires state().");
    }
    if (typeof options.createInstance !== "function" || typeof options.reuseInstance !== "function") {
      throw new TypeError("PortalEditorOpenService.create requires createInstance() and reuseInstance().");
    }
    let sequence = 0;
    return Object.freeze({
      async open(rawIntent = {}) {
        const intent = normalizeIntent(rawIntent);
        const state = options.state() || {};
        const decision = resolve({ ...state, intent });
        const requestId = ++sequence;
        if (decision.action === "reuse") {
          const value = await options.reuseInstance(decision.instanceId, intent, { requestId, decision });
          return Object.freeze({ requestId, decision, instanceId: decision.instanceId, value });
        }
        const value = await options.createInstance(intent, {
          requestId,
          decision,
          stackId: decision.stackId,
          zone: decision.zone,
        });
        return Object.freeze({
          requestId,
          decision,
          instanceId: String(value?.instanceId || value?.id || ""),
          value,
        });
      },
      resolve(rawIntent = {}) {
        return resolve({ ...(options.state() || {}), intent: normalizeIntent(rawIntent) });
      },
    });
  }

  global.PortalEditorOpenService = Object.freeze({ create, resolve });
})(window);
