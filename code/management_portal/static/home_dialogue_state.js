(() => {
  "use strict";
  const views = new Set(["report", "map", "frontier", "preview"]);
  function normalizeIntent(value) {
    if (!value || typeof value !== "object") throw new Error("展示指令为空");
    const action = value.action || "open";
    if (!["open", "generate", "focus", "back"].includes(action)) throw new Error("不支持的展示动作");
    if (action === "back") return { action };
    if (!views.has(value.view)) throw new Error("不支持的展示视图");
    const target = value.target || {};
    if (value.view === "preview") {
      const address = String(target.address || target.resourceAddress || target.path || target.href || "").trim();
      if (!address || address.length > 4096 || /[\x00-\x1f]/.test(address) || /^(javascript|data|vbscript):/i.test(address)) throw new Error("无效的预览地址");
      return { action, view: value.view, target: { address } };
    }
    const kind = String(target.kind || ""), id = String(target.id || "").trim();
    if (kind === "report") {
      if (value.view !== "report" || !/^report_[a-f0-9]{12}$/.test(id)
        || (target.revision !== undefined && (!Number.isSafeInteger(target.revision) || target.revision < 1))) throw new Error("汇报引用需有效的 report_ID 和正整数版本");
      return { action, view: "report", target: { kind, id,
        ...(target.revision === undefined ? {} : { revision: target.revision }),
        ...(typeof target.focus === "string" ? { focus: target.focus.slice(0, 200) } : {}) } };
    }
    if (!["task", "lineage", "project"].includes(kind) || !id || id.length > 200 || /[\x00-\x1f]/.test(id)) throw new Error("请指定任务、Lineage 或 Project");
    if (kind === "task" && !/^resident_[a-f0-9]{12}\/msg_[a-f0-9]{12,64}$/.test(id)) throw new Error("任务使用 resident_ID/msg_ID");
    if (kind === "lineage" && !/^lineage_[0-9a-f]{12}$/.test(id)) throw new Error("Lineage ID 无效");
    if (kind === "project" && !/^[\p{L}\p{N}_.@+\-]{1,80}$/u.test(id)) throw new Error("Project ID 无效");
    if (value.view !== "report" && kind === "task") throw new Error("地图需要 Lineage 或 Project");
    return { action, view: value.view, target: { kind, id, ...(typeof target.focus === "string" ? { focus: target.focus.slice(0, 200) } : {}) } };
  }
  // The host owns selection. Requests only acquire permission to present at the
  // user's current navigation epoch; data loaders never acquire that permission.
  function create({ uuid = () => window.PortalUIPrimitives.randomUUID() } = {}) {
    let epoch = 0, latest = "", active = null, pinned = false;
    const requests = new Map(), entries = [];
    function issue(residentId, baseline = []) {
      const id = uuid(); latest = id;
      requests.set(id, { id, epoch, residentId, baseline: new Set(baseline), version: 0, acknowledged: false });
      return id;
    }
    function acknowledge(requestId, residentId) {
      const request = requests.get(requestId);
      if (!request || request.residentId !== residentId) return false;
      request.acknowledged = true;
      return true;
    }
    function accept(value, { residentId, messageId, dirty = false } = {}) {
      const request = requests.get(value?.request_id);
      if (!request || !request.acknowledged || request.residentId !== residentId || request.baseline.has(messageId)
        || value.protocol !== "portal-home-display/v1" || !Number.isSafeInteger(value.version) || value.version <= request.version) return null;
      const intent = normalizeIntent(value);
      request.version = value.version;
      return { intent, present: request.id === latest && request.epoch === epoch && !pinned && !dirty };
    }
    return {
      issue, acknowledge, accept, entries,
      add(entry) { entries.push(entry); return entry; },
      select(entry, manual = true) { if (manual) epoch++; active = entry; },
      manual() { epoch++; },
      pin(value) { pinned = !!value; epoch++; },
      get active() { return active; }, get pinned() { return pinned; },
      clear() { requests.clear(); entries.length = 0; active = null; },
    };
  }
  function migrateHomeState(state) {
    if (state.contentSchemaVersion >= 2) return state;
    const next = JSON.parse(JSON.stringify(state));
    for (const item of [next, ...Object.values(next.savedWorkspaces || {})]) {
      item.hidden = [...new Set([...(item.hidden || []), "dialogue-display"])];
    }
    return next;
  }
  function displayCommand(block) {
    if (!block?.complete || block.info !== "portal-home-display" || block.source.length > 8192) return null;
    try {
      const value = JSON.parse(block.source);
      if (!value || value.protocol !== "portal-home-display/v1" || typeof value.request_id !== "string"
        || !value.request_id.trim() || value.request_id.length > 200 || /[\x00-\x1f]/.test(value.request_id)
        || !Number.isSafeInteger(value.version) || value.version < 1) return null;
      const intent = normalizeIntent(value);
      if (intent.target?.kind === "report" && !intent.target.revision) return null;
      return { value, intent };
    } catch { return null; }
  }
  function results(messages) {
    const found = [];
    for (const message of messages || []) {
      if (!["assistant", "agent"].includes(message.role)) continue;
      const text = (message.content_parts || []).filter(p => p.type === "text").map(p => p.text || "").join("\n");
      let fenceIndex = 0;
      for (const block of window.MD?.fencedBlocks(text) || []) {
        if (block.info !== "portal-home-display") continue;
        const index = fenceIndex++;
        const command = displayCommand(block);
        if (command) found.push({ value: command.value, messageId: message.message_id || message.id, fenceIndex: index });
      }
    }
    return found;
  }
  // This is a materialization identity, not authorization or automatic-display
  // admission. No request nonce, body or credentials are persisted in this key.
  function normalizeMessageOrigin(value) {
    const { residentId, messageId, fenceIndex } = value || {};
    if (!/^resident_[a-f0-9]{12}$/.test(residentId) || !/^msg_[a-f0-9]{12,64}$/.test(messageId)
      || !Number.isSafeInteger(fenceIndex) || fenceIndex < 0) return null;
    return { residentId, messageId, fenceIndex };
  }
  async function messageOriginKey(source, value) {
    const provenance = normalizeMessageOrigin(source);
    if (!provenance) throw new Error("汇报需要真实对话消息来源");
    const { residentId, messageId, fenceIndex } = provenance;
    const intent = normalizeIntent(value);
    const { sha256 } = await import("./portal_sha256.js");
    return "home-message/v1:" + await sha256(new TextEncoder().encode(JSON.stringify([residentId, messageId, fenceIndex, intent])));
  }
  function normalizeOriginKey(value) {
    return typeof value === "string" && /^(?:home-message\/v1:[a-f0-9]{64}|home-request\/v1:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/.test(value) ? value : null;
  }
  window.PortalHomeDialogueState = Object.freeze({ create, normalizeIntent, migrateHomeState, results, displayCommand, messageOriginKey, normalizeOriginKey, normalizeMessageOrigin });
})();
