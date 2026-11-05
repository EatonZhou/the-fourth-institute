(() => {
  "use strict";
  if (window.PortalDisplayProtocol) return;
  const views = new Set(["report", "map", "frontier", "preview", "explanation"]);
  function normalizeIntent(value) {
    if (!value || typeof value !== "object") throw new Error("展示指令为空");
    const action = value.action || "open";
    if (!["open", "generate", "focus", "back"].includes(action)) throw new Error("不支持的展示动作");
    if (action === "back") return { action };
    if (!views.has(value.view)) throw new Error("不支持的展示视图");
    if (value.view === "report" && value.quality !== undefined && !["low", "medium", "high"].includes(value.quality)) throw new Error("汇报质量无效");
    const target = value.target || {};
    if (value.view === "explanation") {
      const address = String(target.address || "").trim(), sha256 = String(target.sha256 || "").toLowerCase();
      if (!/^workspace_[a-f0-9]{12}\/file\/.+/.test(address) || address.length > 4096 || /[\x00-\x1f\x7f\\?#]/.test(address)
        || address.split("/").some(part => !part || part === "." || part === "..") || !/^[a-f0-9]{64}$/.test(sha256)) throw new Error("解释页需要正式 Workspace 文件地址和 SHA-256 版本");
      return { action, view: value.view, target: { address, sha256 } };
    }
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
    return { action, view: value.view, target: { kind, id, ...(typeof target.focus === "string" ? { focus: target.focus.slice(0, 200) } : {}) },
      ...(value.view === "report" && value.quality !== undefined ? { quality: value.quality } : {}) };
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
    const { quality, ...intent } = normalizeIntent(value);
    const { sha256 } = await import("./portal_sha256.js");
    // A different quality must conflict with the original binding at save,
    // not manufacture a new origin and silently replace the same request.
    return "home-message/v1:" + await sha256(new TextEncoder().encode(JSON.stringify([residentId, messageId, fenceIndex, intent])));
  }
  function normalizeOriginKey(value) {
    return typeof value === "string" && /^(?:home-message\/v1:[a-f0-9]{64}|home-request\/v1:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/.test(value) ? value : null;
  }
  function label(intent) {
    if (intent.action === "back") return "返回上一展示";
    if (intent.view === "preview") return /(?:slides?|\.pptx?)(?:[/.?#]|$)/i.test(intent.target.address) ? "预览幻灯片" : "预览文件/资源";
    return {report:"查看汇报",explanation:"打开解释页",map:"打开研究地图",frontier:"查看 Lineage Map"}[intent.view];
  }
  function previewRequest(value, messageOrigin) {
    const intent = normalizeIntent(value);
    if (intent.action === "back") return {displayIntent:intent, identity:"display:back"};
    if (intent.view === "preview") return {resourceAddress:intent.target.address, path:intent.target.address, title:label(intent)};
    return {displayIntent:intent, messageOrigin:normalizeMessageOrigin(messageOrigin),
      identity:"display:"+JSON.stringify(intent), path:"display:"+JSON.stringify(intent), title:label(intent)};
  }
  window.PortalDisplayProtocol = Object.freeze({normalizeIntent, displayCommand, results,
    messageOriginKey, normalizeOriginKey, normalizeMessageOrigin, label, previewRequest});
})();
