(function (global) {
  "use strict";

  if (global.PortalEditorOpenCommands) return;

  const COMMANDS = Object.freeze([
    Object.freeze({ id: "open-new-tab", mode: "new-tab", label: window.PortalI18n.t("Open in New Tab") }),
    Object.freeze({ id: "open-side", mode: "side", side: "right", label: window.PortalI18n.t("Open to the Side") }),
  ]);
  const COMMAND_BY_ID = new Map(COMMANDS.map(command => [command.id, command]));

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  function actionsHtml(options = {}) {
    const attributes = typeof options.attributes === "function" ? options.attributes : () => "";
    return COMMANDS.map(command => window.PortalI18n.html`<button class="${escapeHtml(options.className || "portalEditorOpenMenuAction")} portalEditorOpenCommandAction" type="button" role="menuitem" data-portal-tooltip-opt-out="1" data-portal-editor-open-command="${escapeHtml(command.id)}" ${attributes(command)}>${escapeHtml(command.label)}</button>`).join("");
  }

  function create(options = {}) {
    const owner = typeof options.owner === "string" ? document.querySelector(options.owner) : options.owner;
    if (!owner) throw new TypeError("PortalEditorOpenCommands.create requires owner.");
    if (typeof options.onCommand !== "function") throw new TypeError("PortalEditorOpenCommands.create requires onCommand().");
    const panel = document.createElement("div");
    panel.className = "portalEditorOpenMenu";
    panel.setAttribute("role", "menu");
    panel.setAttribute("aria-label", options.label || window.PortalI18n.t("Open resource"));
    panel.innerHTML = actionsHtml();
    owner.appendChild(panel);

    let context = null;
    let additionalCommands = [];
    let trigger = null;
    const popover = global.PortalOverlay.bindFloatingPopover({
      panel,
      placement: "bottom",
      align: "end",
      gap: 5,
      margin: 8,
      portal: true,
      closeOnOutside: true,
      closeOnEscape: true,
      onClose: () => {
        if (panel.contains(document.activeElement) && trigger?.isConnected) trigger.focus({ preventScroll: true });
        context = null;
        trigger = null;
      },
    });

    panel.addEventListener("click", event => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-portal-editor-open-command]")
        : null;
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      const command = COMMAND_BY_ID.get(button.dataset.portalEditorOpenCommand || "")
        || additionalCommands.find(item => item.id === button.dataset.portalEditorOpenCommand);
      if (!command) return;
      const commandContext = context;
      popover.close(event);
      Promise.resolve().then(() => typeof command.run === "function" ? command.run(commandContext) : options.onCommand(command, commandContext)).catch(error => {
        global.PortalUI?.toast?.(`打开编辑器失败：${error?.message || error}`, "error");
      });
    });

    return Object.freeze({
      open({ event, trigger: nextTrigger, context: nextContext, focus = true } = {}) {
        const anchor = nextTrigger instanceof Element ? nextTrigger : null;
        const rect = anchor?.getBoundingClientRect?.();
        const point = rect
          ? { x: rect.right, y: rect.bottom }
          : event && Number.isFinite(Number(event.clientX)) && Number.isFinite(Number(event.clientY))
            ? { x: Number(event.clientX), y: Number(event.clientY) }
            : { x: 0, y: 0 };
        context = nextContext;
        additionalCommands = (options.additionalCommands?.(context) || [])
          .filter(command => command?.id && !COMMAND_BY_ID.has(command.id) && typeof command.run === "function");
        panel.innerHTML = actionsHtml() + additionalCommands.map(command => window.PortalI18n.html`<button class="portalEditorOpenMenuAction" type="button" role="menuitem" data-portal-editor-open-command="${escapeHtml(command.id)}">${escapeHtml(command.label)}</button>`).join("");
        trigger = anchor;
        global.PortalButtonTooltips?.hide?.();
        popover.open(point);
        if (focus) requestAnimationFrame(() => {
          panel.querySelector("[role='menuitem']")?.focus({ preventScroll: true });
          global.PortalButtonTooltips?.hide?.();
        });
      },
      close(event) { popover.close(event); },
      destroy() {
        popover.destroy();
        panel.remove();
      },
      element: panel,
    });
  }

  global.PortalEditorOpenCommands = Object.freeze({ actionsHtml, create });
})(window);
