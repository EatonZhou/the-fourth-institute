(() => {
  "use strict";
  // Message meaning is shared by every Resident chat host. Only Home decides
  // whether a new response may automatically replace its selected preview.
  function create({residentId, onOpen}) {
    const actions = new Map();
    function render(item, rendering) {
      const id = item.message_id || item.id;
      if (!id || !["assistant", "agent"].includes(item.role)) return null;
      const scope = residentId(), previous = actions.get(id) || [], next = [];
      let recognized = false, fenceIndex = 0;
      const html = rendering.renderContent(rendering.content, {renderCodeFence(block) {
        if (block.info !== "portal-home-display") return null;
        const index = fenceIndex++; recognized = true;
        if (!block.complete) return '<span class="chatDisplayNotice" role="status">正在准备展示…</span>';
        const command = window.PortalDisplayProtocol.displayCommand(block);
        if (!command) return '<span class="chatDisplayNotice" role="status">展示操作暂不可用：指令格式无效或当前版本不支持。</span>';
        const prior = previous[next.length];
        const action = {token: prior?.source === block.source && prior.scope === scope ? prior.token : window.PortalUIPrimitives.randomUUID(),
          source:block.source, scope, intent:command.intent, messageOrigin:{residentId:scope, messageId:id, fenceIndex:index}};
        next.push(action);
        return `<button type="button" class="chatDisplayAction btn" data-home-display-action="${window.MD.esc(action.token)}">${window.MD.esc(window.PortalI18n.t(window.PortalDisplayProtocol.label(action.intent)))}</button>`;
      }});
      actions.set(id, next);
      return recognized ? html : null;
    }
    async function open(event) {
      const button = event.target.closest?.("button[data-home-display-action]");
      if (!button) return;
      const action = [...actions.values()].flat().find(a => a.token === button.dataset.homeDisplayAction && a.scope === residentId());
      if (!action) return;
      event.preventDefault(); event.stopPropagation();
      if (await onOpen?.(action) === true) return;
      const opened = await window.PortalPreview?.request(window.PortalDisplayProtocol.previewRequest(action.intent, action.messageOrigin));
      if (!opened) throw new Error("当前页面没有可用的预览区域");
    }
    return {render, open, clear: () => actions.clear()};
  }
  window.PortalChatDisplayActions = Object.freeze({create});
})();
