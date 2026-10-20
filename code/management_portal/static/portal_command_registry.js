(function (global) {
  "use strict";

  if (global.PortalCommandRegistry) return;

  const scopes = new Map();
  const listeners = new Set();
  let order = 0;

  const asArray = value => Array.isArray(value) ? value : (value ? [value] : []);
  const truth = (rule, context, fallback = true) => {
    if (typeof rule === "function") return Boolean(rule(context));
    return rule === undefined ? fallback : Boolean(rule);
  };
  const text = (value, context, fallback = "") => String(
    (typeof value === "function" ? value(context) : value) ?? fallback,
  );

  function notify(change) {
    const snapshot = Object.freeze({ ...change });
    listeners.forEach(listener => {
      try { listener(snapshot); }
      catch (error) { console.error(window.PortalI18n.t("PortalCommandRegistry listener failed"), error); }
    });
    document.dispatchEvent(new CustomEvent("portal:commands-change", { detail: snapshot }));
  }

  function normalizeCommand(raw, scopeId) {
    const id = String(raw?.id || "").trim();
    if (!id) throw new TypeError("PortalCommandRegistry command requires id.");
    if (typeof raw.execute !== "function") {
      throw new TypeError(`PortalCommandRegistry command requires execute(): ${id}`);
    }
    return Object.freeze({
      id,
      scope: scopeId,
      title: raw.title ?? id,
      description: raw.description ?? "",
      category: raw.category ?? "General",
      icon: raw.icon ?? "",
      keybindings: Object.freeze(asArray(raw.keybindings).map(String).filter(Boolean)),
      allowInInput: raw.allowInInput === true,
      allowConcurrent: raw.allowConcurrent === true,
      priority: Number(raw.priority || 0),
      visible: raw.visible,
      enabled: raw.enabled,
      execute: raw.execute,
      order: order += 1,
    });
  }

  function createScope(options = {}) {
    const id = String(options.id || "").trim();
    if (!id) throw new TypeError("PortalCommandRegistry.createScope requires id.");
    if (scopes.has(id)) throw new Error(`PortalCommandRegistry scope is already registered: ${id}`);
    const commands = new Map();
    const busy = new WeakMap();
    const busyCount = command => Number(busy.get(command) || 0);
    let contextProvider = typeof options.context === "function"
      ? options.context
      : () => (options.context && typeof options.context === "object" ? options.context : {});
    let disposed = false;

    const context = override => Object.freeze({
      ...(contextProvider() || {}),
      ...(override && typeof override === "object" ? override : {}),
      commandScope: id,
    });
    const api = Object.freeze({
      id,
      register(raw) {
        if (disposed) throw new Error(`PortalCommandRegistry scope is disposed: ${id}`);
        const command = normalizeCommand(raw, id);
        if (commands.has(command.id)) throw new Error(`Portal command is already registered in ${id}: ${command.id}`);
        commands.set(command.id, command);
        notify({ type: "register", scope: id, commandId: command.id });
        return () => commands.get(command.id) === command && api.unregister(command.id);
      },
      registerMany(items = []) { return items.map(item => api.register(item)); },
      unregister(commandId) {
        const removed = commands.delete(String(commandId || ""));
        if (removed) notify({ type: "unregister", scope: id, commandId: String(commandId) });
        return removed;
      },
      setContext(provider) {
        contextProvider = typeof provider === "function" ? provider : () => (provider || {});
        notify({ type: "context", scope: id });
      },
      get(commandId) { return commands.get(String(commandId || "")) || null; },
      list(override = {}) {
        const current = context(override.context);
        return [...commands.values()]
          .filter(command => override.includeHidden === true || truth(command.visible, current))
          .map(command => {
            const isBusy = busyCount(command) > 0;
            return Object.freeze({
              ...command,
              title: text(command.title, current, command.id),
              description: text(command.description, current),
              category: text(command.category, current, "General"),
              icon: text(command.icon, current),
              busy: isBusy,
              enabled: truth(command.enabled, current) && (!isBusy || command.allowConcurrent),
            });
          })
          .sort((left, right) => right.priority - left.priority || left.order - right.order);
      },
      async execute(commandId, invocation = {}) {
        const command = commands.get(String(commandId || ""));
        if (!command) throw new Error(`Portal command is not registered in ${id}: ${commandId}`);
        const current = context(invocation.context);
        if (!truth(command.visible, current) || !truth(command.enabled, current)) return false;
        if (busyCount(command) > 0 && !command.allowConcurrent) return false;
        busy.set(command, busyCount(command) + 1);
        notify({ type: "busy", scope: id, commandId: command.id, busy: true });
        try {
          const resolvedCommand = api.list({ includeHidden: true, context: invocation.context })
            .find(item => item.id === command.id) || command;
          return await command.execute(Object.freeze({
            command: resolvedCommand,
            context: current,
            args: invocation.args,
            event: invocation.event,
            source: String(invocation.source || "api"),
            scope: api,
          }));
        } finally {
          const remaining = Math.max(0, busyCount(command) - 1);
          if (remaining) busy.set(command, remaining);
          else busy.delete(command);
          if (!disposed && commands.get(command.id) === command) {
            notify({ type: "busy", scope: id, commandId: command.id, busy: remaining > 0 });
          }
        }
      },
      dispose() {
        if (disposed) return;
        disposed = true;
        commands.clear();
        scopes.delete(id);
        notify({ type: "dispose", scope: id });
      },
    });
    scopes.set(id, api);
    notify({ type: "scope", scope: id });
    return api;
  }

  const root = createScope({ id: "portal" });

  function normalizeKeybinding(event, mainKey = event.key) {
    const parts = [];
    if (event.ctrlKey) parts.push("Ctrl");
    if (event.metaKey) parts.push("Meta");
    if (event.altKey) parts.push("Alt");
    if (event.shiftKey) parts.push("Shift");
    const key = String(mainKey || "");
    if (!["Control", "Meta", "Alt", "Shift"].includes(key)) {
      parts.push(key.length === 1 ? key.toUpperCase() : key);
    }
    return parts.join("+");
  }

  function isEditableTarget(target) {
    return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']"));
  }

  function dispatchKeybinding(event, commandScopes, exclusive = false, editableTarget) {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
    const binding = normalizeKeybinding(event);
    if (!binding) return;
    const bindings = [binding, normalizeKeybinding(event, event.code)].map(value => value.toLowerCase());
    const editable = editableTarget ?? event.composedPath().some(isEditableTarget);
    const candidates = commandScopes
      .flatMap(scope => scope.list())
      .filter(command => command.enabled)
      .filter(command => command.keybindings.some(item => bindings.includes(item.toLowerCase())))
      .filter(command => command.allowInInput || !editable)
      .sort((left, right) => right.priority - left.priority || left.order - right.order);
    const command = candidates[0];
    if (!command) return;
    event.preventDefault();
    if (exclusive) event.stopImmediatePropagation();
    scopes.get(command.scope)?.execute(command.id, { event, source: "keybinding" })
      .catch(error => console.error(window.PortalI18n.text`Portal command failed: ${command.id}`, error));
    return true;
  }

  // Portal commands own the key before page listeners and embedded editors.
  // Numeric priority only resolves conflicts within the same precedence tier.
  global.addEventListener("keydown", event => {
    dispatchKeybinding(event, [root], true);
  }, true);
  document.addEventListener("keydown", event => {
    dispatchKeybinding(event, [...scopes.values()].filter(scope => scope !== root));
  });

  global.PortalCommandRegistry = Object.freeze({
    createScope,
    root,
    dispatchGlobalKeybinding: (event, editable) => dispatchKeybinding(event, [root], true, editable),
    getScope: id => scopes.get(String(id || "")) || null,
    scopes: () => [...scopes.values()],
    list(options = {}) {
      const requestedScope = String(options.scope || "");
      const selected = requestedScope ? [scopes.get(requestedScope)].filter(Boolean) : [...scopes.values()];
      return selected.flatMap(scope => scope.list(options));
    },
    execute(commandId, options = {}) {
      const requestedScope = String(options.scope || "portal");
      const scope = scopes.get(requestedScope);
      if (!scope) throw new Error(`Portal command scope is not registered: ${requestedScope}`);
      return scope.execute(commandId, options);
    },
    subscribe(listener) {
      if (typeof listener !== "function") return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
})(window);
