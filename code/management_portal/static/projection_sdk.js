(() => {
  "use strict";
  const pending = new Map();
  const channel = document.currentScript.dataset.channel;
  const host = document.currentScript.dataset.hostDepth === "2" ? parent.parent : parent;
  const iconCatalog = JSON.parse(document.currentScript.dataset.icons || "{}");
  let globalKeybindings = JSON.parse(document.currentScript.dataset.keybindings || "[]");
  const workers = new Set();
  const loadedLibraries = new Map();
  let disposed = false;
  // Keyboard events do not bubble out of the sandbox. Reserve only registered
  // Portal shortcuts; ordinary typing and local/editor shortcuts stay here.
  addEventListener("keydown", event => {
    if (disposed || event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat) return;
    const editable = event.composedPath().some(node => node instanceof Element
      && node.closest("input, textarea, select, [contenteditable='true'], [role='textbox']"));
    const binding = key => [event.ctrlKey && "Ctrl", event.metaKey && "Meta", event.altKey && "Alt",
      event.shiftKey && "Shift", !["Control", "Meta", "Alt", "Shift"].includes(key) && key]
      .filter(Boolean).join("+").toLowerCase();
    const keys = [binding(event.key), binding(event.code)];
    if (!globalKeybindings.some(command => (command.allowInInput || !editable)
      && command.keybindings.some(key => keys.includes(key.toLowerCase())))) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    host.postMessage({ type: "portal-projection-keybinding", channel, editable,
      key: { key: event.key, code: event.code, ctrlKey: event.ctrlKey, metaKey: event.metaKey,
        altKey: event.altKey, shiftKey: event.shiftKey } }, "*");
  }, true);
  function requireActive() {
    if (disposed) throw Object.assign(new Error("功能区已卸载"), { code: "projection.disposed" });
  }
  function icon(name) {
    const key = iconCatalog.aliases?.[name] || name;
    const markup = iconCatalog.icons?.[key];
    if (typeof markup !== "string") return null;
    const template = document.createElement("template"); template.innerHTML = markup;
    return template.content.firstElementChild;
  }
  async function createWorker(path) {
    requireActive();
    const source = await call("worker-source", { path });
    requireActive();
    const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    let worker;
    try { worker = new Worker(url); }
    catch (error) { URL.revokeObjectURL(url); throw error; }
    const terminate = worker.terminate.bind(worker);
    worker.terminate = () => { terminate(); URL.revokeObjectURL(url); workers.delete(worker); };
    workers.add(worker);
    return worker;
  }
  async function loadLibrary(name) {
    requireActive();
    if (!loadedLibraries.has(name)) {
      const loading = call("library-source", { name }).then(result => {
        requireActive();
        const script = document.createElement("script");
        script.textContent = result.content;
        document.head.append(script);
        return result.library;
      }).catch(error => { loadedLibraries.delete(name); throw error; });
      loadedLibraries.set(name, loading);
    }
    return loadedLibraries.get(name);
  }
  addEventListener("pagehide", () => { disposed = true; for (const worker of workers) worker.terminate(); });
  let requestSequence = 0;
  const focusListeners = new Set(), changeListeners = new Set(), visibilityListeners = new Set();
  let focusRevision = -1, watchRevision = 0, activeWatchRevision = 0;
  function armTimeout(requestId, entry) {
    entry.startedAt = performance.now();
    entry.timeout = setTimeout(() => {
      pending.delete(requestId);
      entry.reject(Object.assign(new Error("工作台请求超时；结果待核实，请勿重复提交"), { code: "projection.request_timeout" }));
    }, entry.remainingMs);
  }
  function call(method, payload = {}) {
    // The host assigns each frame a fresh channel. Requests need only be
    // unique within that channel, including on sandboxed HTTP documents.
    const requestId = String(++requestSequence);
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, remainingMs: 270000, awaitingConfirmation: false };
      pending.set(requestId, entry);
      armTimeout(requestId, entry);
      host.postMessage({ type: "portal-projection-request", channel, requestId, method, payload }, "*");
    });
  }
  addEventListener("message", event => {
    const message = event.data;
    if (event.source === host && message?.type === "portal-projection-theme" && message.channel === channel) {
      const theme = document.getElementById("portalProjectionTheme");
      if (theme) theme.textContent = String(message.css || "");
      return;
    }
    if (event.source !== host || message?.channel !== channel) return;
    if (message.type === "portal-projection-keybindings") {
      globalKeybindings = Array.isArray(message.bindings) ? message.bindings : [];
      return;
    }
    if (message.type === "portal-projection-focus") {
      if (message.revision > focusRevision) {
        focusRevision = message.revision;
        focusListeners.forEach(fn => fn(message.object, { revision: message.revision }));
      }
      return;
    }
    if (message.type === "portal-projection-change") { changeListeners.forEach(fn => fn(message)); return; }
    if (message.type === "portal-projection-visibility") { visibilityListeners.forEach(fn => fn(message.visible)); return; }
    const entry = pending.get(message.requestId);
    if (!entry) return;
    if (message.type === "portal-projection-confirmation") {
      if (message.phase === "pending" && !entry.awaitingConfirmation) {
        clearTimeout(entry.timeout);
        entry.remainingMs = Math.max(0, entry.remainingMs - (performance.now() - entry.startedAt));
        entry.awaitingConfirmation = true;
      } else if (message.phase === "resolved" && entry.awaitingConfirmation) {
        entry.awaitingConfirmation = false;
        armTimeout(message.requestId, entry);
      }
      return;
    }
    if (message.type !== "portal-projection-result") return;
    pending.delete(message.requestId);
    clearTimeout(entry.timeout);
    if (message.error) entry.reject(Object.assign(new Error(message.error), { code: message.code }));
    else entry.resolve(message.result);
  });
  const hatg = (instruction, argumentsValue = {}, options = {}) => call("hatg", { ...options, instruction, arguments: argumentsValue });
  async function inputResult(messageId, options = {}) {
    const target = { ...options };
    const mismatch = () => Object.assign(new Error("回复与本次输入不匹配，请保留输入 ID 并刷新核验"), { code: "projection.reply_mismatch" });
    if (typeof messageId !== "string" || !messageId.trim()) throw mismatch();
    const state = (await hatg("GetInputState", { message_id: messageId }, target))?.input_state;
    if (state?.message_id !== messageId || !["queued", "running", "completed", "failed", "cancelled"].includes(state.state)) throw mismatch();
    if (state.state !== "completed") return { ...state, messages: [], text: "" };
    const replyIds = state.reply_message_ids;
    if (!Array.isArray(replyIds) || !replyIds.length) {
      throw Object.assign(new Error("输入已结束，但尚无关联回复；请保留输入 ID 并刷新核验"), { code: "projection.reply_unavailable" });
    }
    if (replyIds.some(replyId => typeof replyId !== "string" || !replyId.trim()) || new Set(replyIds).size !== replyIds.length) throw mismatch();
    const messages = await Promise.all(replyIds.map(async replyId => {
      const message = (await hatg("GetMessage", { message_id: replyId }, target))?.message;
      if (message?.message_id !== replyId || message.reply_to_message_id !== messageId || message.role !== "agent" || !Array.isArray(message.content_parts)) throw mismatch();
      if (message.content_parts.some(part => !part || (part.type === "text" && typeof part.text !== "string"))) throw mismatch();
      return message;
    }));
    const text = messages.flatMap(message => message.content_parts.filter(part => part.type === "text").map(part => part.text)).join("\n\n");
    return { ...state, messages, text };
  }
  // The input messages one TriggerEvent receipt delivered: each executed
  // Effect reports DeliverMessage's message_id in its typed_outputs.
  function eventInputIds(receipt) {
    const results = receipt?.delivery?.results;
    if (!Array.isArray(results)) return [];
    return [...new Set(results
      .filter(item => item?.status === "executed")
      .map(item => item?.typed_outputs?.message_id)
      .filter(id => typeof id === "string" && id.trim()))];
  }
  window.Projection = Object.freeze({
    createWorker,
    icon,
    libraries: () => call("libraries"),
    loadLibrary,
    context: () => call("context"),
    openPanel: panelId => call("open-panel", { panelId }),
    openChat: () => call("open-chat"),
    openPage: url => call("open-page", { url }),
    focus: object => call("focus", { object }),
    onFocus(fn) {
      focusListeners.add(fn);
      call("focus-read").then(value => { if (value && focusListeners.has(fn) && value.revision >= focusRevision) { focusRevision = value.revision; fn(value.object, { revision: value.revision }); } });
      return () => focusListeners.delete(fn);
    },
    feedback: value => call("feedback", value),
    cancelFeedback: () => call("cancel-feedback"),
    async watch(refs, fn) {
      const revision = ++watchRevision;
      await call("watch", { refs });
      if (revision > activeWatchRevision) {
        activeWatchRevision = revision;
        changeListeners.clear();
        if (fn) changeListeners.add(fn);
      }
      return async () => {
        if (revision !== activeWatchRevision) return;
        const stopping = ++watchRevision;
        try { await call("watch", { refs: [] }); }
        catch (error) {
          // A later subscription owns the host now. This disposer must not
          // clear its listener or turn an intentional replacement into failure.
          if (error.code === "projection.cancelled" && stopping < watchRevision) return;
          throw error;
        }
        if (stopping > activeWatchRevision) { activeWatchRevision = stopping; changeListeners.clear(); }
      };
    },
    onVisibility(fn) {
      visibilityListeners.add(fn);
      call("context").then(value => { if (visibilityListeners.has(fn)) fn(value.visible); });
      return () => visibilityListeners.delete(fn);
    },
    hatg,
    eventInputIds,
    inputResult,
    resource: (operation, argumentsValue) => call("resource", { operation, arguments: argumentsValue }),
    directories: argumentsValue => call("directories", { arguments: argumentsValue }),
    release: (operation, argumentsValue = {}) => call("release", { operation, arguments: argumentsValue }),
    state: value => call("state", value === undefined ? {} : { value }),
    openLineage: (binding = "main") => call("open-lineage", { binding }),
  });
})();
