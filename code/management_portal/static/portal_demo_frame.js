(function (global) {
  "use strict";

  const raw = global.__PORTAL_DEMO_BOOTSTRAP__;
  if (!raw || !raw.caseId || !raw.session) return;

  // The sandbox guard installs before page scripts. Resolve the shared name
  // when the bridge is used, after the page's compatibility bootstrap has run.
  const bridgeScope = () => global.PortalCompatibility.identifier("demo_frame_scope");
  const config = Object.freeze({
    caseId: String(raw.caseId),
    session: String(raw.session),
    target: String(raw.target || ""),
  });
  const parentWindow = global.parent;
  const pendingFetches = new Map();
  const preparedSteps = new Map();
  let requestSequence = 0;

  function emit(type, payload = {}) {
    parentWindow.postMessage({
      scope: bridgeScope(),
      type,
      case_id: config.caseId,
      session: config.session,
      ...payload,
    }, "*");
  }

  function mutationError(method) {
    return new Error(`演示沙盒已阻止 ${String(method || "UNKNOWN")} 操作`);
  }

  function memoryStorage() {
    const values = new Map();
    return Object.freeze({
      get length() {
        return values.size;
      },
      clear() {
        values.clear();
      },
      getItem(key) {
        const clean = String(key);
        return values.has(clean) ? values.get(clean) : null;
      },
      key(index) {
        return [...values.keys()][Number(index)] ?? null;
      },
      removeItem(key) {
        values.delete(String(key));
      },
      setItem(key, value) {
        values.set(String(key), String(value));
      },
    });
  }

  function replaceGlobal(name, value) {
    try {
      Object.defineProperty(global, name, {
        configurable: false,
        enumerable: true,
        value,
        writable: false,
      });
      return true;
    } catch {
      return false;
    }
  }

  replaceGlobal("localStorage", memoryStorage());
  replaceGlobal("sessionStorage", memoryStorage());
  replaceGlobal("indexedDB", undefined);
  replaceGlobal("caches", undefined);
  replaceGlobal("open", () => null);

  try {
    Object.defineProperty(global.navigator, "serviceWorker", {
      configurable: false,
      value: undefined,
    });
  } catch {
    // The sandbox still prevents service worker registration for an opaque origin.
  }

  const blockedTransport = name => class {
    constructor() {
      throw mutationError(name);
    }
  };
  replaceGlobal("WebSocket", blockedTransport("WebSocket"));
  replaceGlobal("EventSource", blockedTransport("EventSource"));

  try {
    Object.defineProperty(global.navigator, "sendBeacon", {
      configurable: false,
      value: () => false,
    });
  } catch {
    global.navigator.sendBeacon = () => false;
  }

  const originalXhrOpen = global.XMLHttpRequest?.prototype?.open;
  const originalXhrSend = global.XMLHttpRequest?.prototype?.send;
  if (originalXhrOpen && originalXhrSend) {
    global.XMLHttpRequest.prototype.open = function (method) {
      this.__portalDemoMethod = String(method || "GET").toUpperCase();
      throw mutationError(window.PortalI18n.text`XHR ${this.__portalDemoMethod}`);
    };
    global.XMLHttpRequest.prototype.send = function () {
      throw mutationError(window.PortalI18n.text`XHR ${this.__portalDemoMethod || "UNKNOWN"}`);
    };
  }

  global.fetch = (input, init = {}) => {
    const requestMethod = input instanceof global.Request ? input.method : "";
    const method = String(init.method || requestMethod || "GET").toUpperCase();
    if (!["GET", "HEAD"].includes(method)) {
      return Promise.reject(mutationError(method));
    }
    let url;
    try {
      const rawUrl = input instanceof global.Request ? input.url : String(input || "");
      url = new URL(rawUrl, document.baseURI);
    } catch {
      return Promise.reject(new Error("演示读取地址无效"));
    }
    const requestId = `frame-fetch-${++requestSequence}`;
    return new Promise((resolve, reject) => {
      const timer = global.setTimeout(() => {
        pendingFetches.delete(requestId);
        reject(new Error("演示只读数据请求超时"));
      }, 15_000);
      pendingFetches.set(requestId, { method, reject, resolve, timer });
      emit("fetch-request", {
        request_id: requestId,
        method,
        url: url.href,
      });
    });
  };

  document.addEventListener("submit", event => {
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("click", event => {
    const link = event.target?.closest?.("a[href], [formaction]");
    const submit = event.target?.closest?.('button[type="submit"], input[type="submit"]');
    if (!link && !submit) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  if (global.HTMLFormElement) {
    global.HTMLFormElement.prototype.submit = function () {
      throw mutationError("FORM");
    };
    global.HTMLFormElement.prototype.requestSubmit = function () {
      throw mutationError("FORM");
    };
  }

  global.__PORTAL_DEMO_MODE__ = Object.freeze({
    safe: true,
    isolatedOrigin: true,
    caseId: config.caseId,
    target: config.target,
  });

  function delay(milliseconds) {
    return new Promise(resolve => global.setTimeout(resolve, Math.max(0, Number(milliseconds || 0))));
  }

  async function waitForTarget(selector) {
    const started = performance.now();
    while (performance.now() - started < 9000) {
      const target = selector ? document.querySelector(selector) : null;
      if (target instanceof global.HTMLElement && target.getClientRects().length) return target;
      await delay(90);
    }
    throw new Error(`找不到演示目标：${selector || "(empty selector)"}`);
  }

  function isVisible(target) {
    return target instanceof global.Element
      && target.getClientRects().length > 0
      && global.getComputedStyle(target).visibility !== "hidden";
  }

  function expectationMatches(expectation) {
    const selector = String(expectation?.selector || "");
    const targets = selector ? [...document.querySelectorAll(selector)] : [];
    const target = targets[0] || null;
    const state = String(expectation?.state || "visible");
    if (
      Object.prototype.hasOwnProperty.call(expectation || {}, "count")
      && targets.length !== Number(expectation.count)
    ) return false;
    if (state === "hidden") {
      return !target || !isVisible(target);
    }
    if (!target || !isVisible(target)) return false;
    if (state === "checked" && !target.checked) return false;
    if (state === "unchecked" && target.checked) return false;
    if (state === "focused" && document.activeElement !== target) return false;
    if (
      Object.prototype.hasOwnProperty.call(expectation || {}, "value")
      && String(target.value ?? "") !== String(expectation.value ?? "")
    ) return false;
    if (
      Object.prototype.hasOwnProperty.call(expectation || {}, "text")
      && !String(target.textContent || "").includes(String(expectation.text || ""))
    ) return false;
    const attribute = String(expectation?.attribute || "");
    if (
      attribute
      && String(target.getAttribute(attribute) ?? "")
        !== String(expectation?.equals ?? "")
    ) return false;
    return true;
  }

  async function assertExpectation(expectation) {
    if (!expectation?.selector) return null;
    const started = performance.now();
    while (performance.now() - started < 3500) {
      if (expectationMatches(expectation)) {
        const evidence = {
          selector: String(expectation.selector),
          state: String(expectation.state || "visible"),
        };
        if (Object.prototype.hasOwnProperty.call(expectation, "count")) {
          evidence.count = document.querySelectorAll(String(expectation.selector)).length;
        }
        if (Object.prototype.hasOwnProperty.call(expectation, "text")) {
          evidence.text = String(expectation.text || "");
        }
        if (String(expectation.state || "") === "focused") {
          evidence.focused = true;
        }
        return evidence;
      }
      await delay(70);
    }
    const criteria = [String(expectation.state || "visible")];
    if (Object.prototype.hasOwnProperty.call(expectation, "count")) {
      criteria.push(`count=${Number(expectation.count)}`);
    }
    if (Object.prototype.hasOwnProperty.call(expectation, "text")) {
      criteria.push(`text=${String(expectation.text || "")}`);
    }
    throw new Error(
      `行为结果未出现：${String(expectation.selector)} 应满足 ${criteria.join("、")}`,
    );
  }

  function setNativeValue(target, value) {
    const prototype = target instanceof global.HTMLTextAreaElement
      ? global.HTMLTextAreaElement.prototype
      : global.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor?.set) descriptor.set.call(target, value);
    else target.value = value;
    target.dispatchEvent(new global.Event("input", { bubbles: true }));
    target.dispatchEvent(new global.Event("change", { bubbles: true }));
  }

  function dispatchHover(target) {
    for (const type of ["pointerover", "mouseover", "mouseenter"]) {
      target.dispatchEvent(new global.MouseEvent(type, {
        bubbles: type !== "mouseenter",
        cancelable: true,
        view: global,
      }));
    }
  }

  function dispatchPointerClick(target) {
    const options = {
      bubbles: true,
      button: 0,
      buttons: 1,
      cancelable: true,
      composed: true,
      isPrimary: true,
      pointerId: 1,
      pointerType: "mouse",
    };
    target.dispatchEvent(new global.PointerEvent("pointerdown", options));
    target.dispatchEvent(new global.PointerEvent("pointerup", {
      ...options,
      buttons: 0,
    }));
    target.click();
  }

  function dispatchPointerDoubleClick(target) {
    dispatchPointerClick(target);
    dispatchPointerClick(target);
    target.dispatchEvent(new global.MouseEvent("dblclick", {
      bubbles: true,
      button: 0,
      buttons: 0,
      cancelable: true,
      composed: true,
      detail: 2,
      view: global,
    }));
  }

  function dispatchKey(value) {
    const target = document.activeElement || document.body;
    const parts = String(value)
      .split("+")
      .map(part => part.trim())
      .filter(Boolean);
    const rawKey = parts.pop() || "";
    const modifiers = new Set(parts.map(part => part.toLowerCase()));
    const key = rawKey.length === 1 ? rawKey.toLowerCase() : rawKey;
    const code = rawKey.length === 1
      ? `Key${rawKey.toUpperCase()}`
      : rawKey === " "
        ? "Space"
        : rawKey;
    const options = {
      key,
      code,
      ctrlKey: modifiers.has("ctrl") || modifiers.has("control"),
      metaKey: modifiers.has("meta") || modifiers.has("cmd") || modifiers.has("command"),
      altKey: modifiers.has("alt") || modifiers.has("option"),
      shiftKey: modifiers.has("shift"),
      bubbles: true,
      cancelable: true,
      view: global,
    };
    for (const type of ["keydown", "keyup"]) {
      target.dispatchEvent(new global.KeyboardEvent(type, options));
    }
  }

  function targetRect(target) {
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    return {
      height: rect.height,
      width: rect.width,
      x: rect.x,
      y: rect.y,
    };
  }

  async function prepareStep(requestId, step) {
    const action = String(step?.action || "");
    const target = action === "key"
      ? null
      : action === "click-if-visible"
        ? (() => {
          const candidate = document.querySelector(String(step?.selector || ""));
          return isVisible(candidate) ? candidate : null;
        })()
        : await waitForTarget(String(step?.selector || ""));
    if (target) {
      target.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
      await new Promise(resolve => global.requestAnimationFrame(() => global.requestAnimationFrame(resolve)));
    }
    const stepId = `prepared-${++requestSequence}`;
    preparedSteps.set(stepId, { action, step, target });
    emit("reply", {
      request_id: requestId,
      result: {
        rect: targetRect(target),
        step_id: stepId,
      },
    });
  }

  async function commitStep(requestId, stepId) {
    const prepared = preparedSteps.get(String(stepId || ""));
    preparedSteps.delete(String(stepId || ""));
    if (!prepared) throw new Error("演示步骤已经失效");
    const { action, step, target } = prepared;
    if (action === "click" || action === "dblclick" || action === "click-if-visible") {
      if (!target && action === "click-if-visible") {
        // Responsive layouts may already expose the following stable action.
      } else if (target.closest("a[href], [formaction]") || target.matches('button[type="submit"], input[type="submit"]')) {
        throw mutationError(window.PortalI18n.t("NAVIGATION OR FORM"));
      } else {
        target.focus({ preventScroll: true });
        if (action === "dblclick") dispatchPointerDoubleClick(target);
        else dispatchPointerClick(target);
      }
    } else if (action === "hover") {
      dispatchHover(target);
    } else if (action === "focus") {
      target.focus({ preventScroll: true });
    } else if (action === "type") {
      if (!(target instanceof global.HTMLInputElement || target instanceof global.HTMLTextAreaElement)) {
        throw new Error("演示输入目标不是文本控件");
      }
      target.focus({ preventScroll: true });
      setNativeValue(target, String(step?.value || ""));
    } else if (action === "key") {
      dispatchKey(String(step?.value || ""));
    } else if (action === "scroll") {
      target.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
    } else if (!["wait", "spotlight"].includes(action)) {
      throw new Error(`不支持的演示动作：${action}`);
    }
    await new Promise(resolve => global.requestAnimationFrame(() => global.requestAnimationFrame(resolve)));
    const evidence = await assertExpectation(step?.expect);
    emit("reply", {
      request_id: requestId,
      result: { evidence, ok: true },
    });
  }

  global.addEventListener("message", event => {
    if (event.source !== parentWindow) return;
    const message = event.data;
    if (
      !message
      || !global.PortalCompatibility
      || message.scope !== bridgeScope()
      || message.case_id !== config.caseId
      || message.session !== config.session
    ) return;
    if (message.type === "fetch-response") {
      const pending = pendingFetches.get(String(message.request_id || ""));
      if (!pending) return;
      pendingFetches.delete(String(message.request_id || ""));
      global.clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new Error(String(message.error)));
        return;
      }
      const headers = new global.Headers(message.headers || {});
      pending.resolve(new global.Response(
        pending.method === "HEAD" ? null : String(message.body || ""),
        {
          headers,
          status: Number(message.status || 500),
          statusText: String(message.status_text || ""),
        },
      ));
      return;
    }
    const requestId = String(message.request_id || "");
    const operation = message.type === "prepare-step"
      ? prepareStep(requestId, message.step)
      : message.type === "commit-step"
        ? commitStep(requestId, message.step_id)
        : null;
    if (!operation) return;
    Promise.resolve(operation).catch(error => {
      emit("reply", {
        request_id: requestId,
        error: String(error?.message || error),
      });
    });
  });

  global.addEventListener("load", () => {
    emit("ready", {
      target: config.target,
    });
  }, { once: true });
})(window);
