(function () {
  "use strict";

  if (window.PortalAPI) return;

  const DEFAULT_TIMEOUT_MS = 360_000;

  async function parseJson(response) {
    const text = await response.text();
    if (!text.trim()) return { empty: true, invalid: false, payload: {}, text };
    try {
      return { empty: false, invalid: false, payload: JSON.parse(text), text };
    } catch {
      return { empty: false, invalid: true, payload: null, text };
    }
  }

  function errorMessage(response, payload) {
    return window.PortalI18n.message(payload?.error || payload?.message || `HTTP ${response.status}`);
  }

  function bodyOptions(body, headers) {
    if (body === undefined) return { headers };
    if (body instanceof FormData) {
      headers.delete("Content-Type");
      return { body, headers };
    }
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    return {
      body: typeof body === "string" ? body : JSON.stringify(body),
      headers,
    };
  }

  function requestSignal(callerSignal, timeoutMs) {
    const controller = new AbortController();
    const abortFromCaller = () => controller.abort(callerSignal?.reason);
    if (callerSignal?.aborted) abortFromCaller();
    else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });

    const parsedTimeout = timeoutMs === undefined
      ? DEFAULT_TIMEOUT_MS
      : Number(timeoutMs);
    const timer = Number.isFinite(parsedTimeout) && parsedTimeout > 0
      ? window.setTimeout(() => {
          controller.abort(new DOMException("Request timed out", "TimeoutError"));
        }, parsedTimeout)
      : 0;
    return {
      signal: controller.signal,
      cleanup() {
        if (timer) window.clearTimeout(timer);
        callerSignal?.removeEventListener("abort", abortFromCaller);
      },
    };
  }

  async function requestResult(path, options = {}) {
    const method = options.method || (options.body === undefined ? "GET" : "POST");
    const headers = new Headers(options.headers);
    if (window.PortalI18n && !headers.has("X-Portal-Language")) {
      headers.set("X-Portal-Language", window.PortalI18n.requestLanguage());
    }
    const init = {
      ...options,
      method,
      cache: options.cache || "no-store",
      headers,
    };
    delete init.detectLoginRedirect;
    delete init.parseJson;
    delete init.timeoutMs;
    Object.assign(init, bodyOptions(options.body, headers));
    const composedSignal = requestSignal(options.signal, options.timeoutMs);
    init.signal = composedSignal.signal;
    let streaming = false;
    try {
      let response = await fetch(path, init);
      if (options.detectLoginRedirect !== false && (response.status === 401
        || (response.redirected && new URL(response.url).pathname === "/login"))) {
        window.location.assign("/login");
        throw Object.assign(new Error("登录状态已失效，请重新登录后再试。"), { status: 401 });
      }
      if (options.parseJson === false && response.body) {
        response = responseWithCleanup(response, composedSignal);
        streaming = true;
      }
      const parsed = options.parseJson === false ? null : await parseJson(response);
      return {
        response,
        payload: parsed ? parsed.payload : null,
        empty: parsed ? parsed.empty : false,
        invalid: parsed ? parsed.invalid : false,
        text: parsed ? parsed.text : "",
      };
    } finally {
      if (!streaming) composedSignal.cleanup();
    }
  }

  function responseWithCleanup(response, lifetime) {
    const reader = response.body.getReader();
    const cleanup = () => {
      lifetime.signal.removeEventListener("abort", abort);
      lifetime.cleanup();
    };
    let streamController;
    let finished = false;
    const finish = () => { finished = true; cleanup(); };
    const abort = () => {
      if (finished) return;
      streamController.error(lifetime.signal.reason);
      finish();
      reader.cancel(lifetime.signal.reason).catch(() => {});
    };
    const body = new ReadableStream({
      start(controller) {
        streamController = controller;
        lifetime.signal.addEventListener("abort", abort, { once: true });
        if (lifetime.signal.aborted) abort();
      },
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (finished) return;
          if (done) { controller.close(); finish(); }
          else controller.enqueue(value);
        } catch (error) {
          if (!finished) { controller.error(error); finish(); }
        }
      },
      cancel(reason) { finish(); return reader.cancel(reason); },
    });
    const wrapped = new Response(body, {
      status: response.status, statusText: response.statusText, headers: response.headers,
    });
    for (const property of ["url", "redirected", "type"]) {
      Object.defineProperty(wrapped, property, { value: response[property] });
    }
    // Chromium turns a synthetic stream's abort into TypeError in Body readers.
    // Preserve the request's cancellation reason for the public body methods.
    for (const method of ["text", "json", "blob", "arrayBuffer", "formData", "bytes"]) {
      if (typeof wrapped[method] !== "function") continue;
      const read = wrapped[method].bind(wrapped);
      wrapped[method] = async (...args) => {
        try { return await read(...args); }
        catch (error) { throw lifetime.signal.aborted ? lifetime.signal.reason : error; }
      };
    }
    return wrapped;
  }

  async function raw(path, options = {}) {
    const result = await requestResult(path, { ...options, parseJson: false });
    return result.response;
  }

  function requireJson(result) {
    if (!result || result.invalid) {
      return JSON.parse(result?.text || "");
    }
    return result.payload;
  }

  async function request(path, options = {}) {
    const result = await requestResult(path, options);
    const { response, payload } = result;
    if (options.parseJson !== false && response.ok && result.invalid) {
      throw new Error("请求没有返回有效数据，请刷新页面后重试。");
    }
    if (!response.ok) {
      const error = new Error(errorMessage(response, payload));
      error.code = String(payload?.code || "");
      error.data = payload && typeof payload === "object" ? payload : {};
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function get(path, options = {}) {
    return request(path, { ...options, method: "GET" });
  }

  function post(path, body, options = {}) {
    return request(path, { ...options, method: "POST", body });
  }

  function postForm(path, formData, options = {}) {
    return request(path, { ...options, method: "POST", body: formData });
  }

  async function requestStatus(path, options = {}) {
    const result = await requestResult(path, options);
    return {
      status: result.response.status,
      ok: result.response.ok,
      data: result.invalid ? {} : (result.payload ?? {}),
      response: result.response,
      text: result.text,
    };
  }

  function postStatus(path, body, options = {}) {
    return requestStatus(path, { ...options, method: "POST", body });
  }

  function createClient(defaultOptions = {}) {
    const mergeOptions = (options = {}) => {
      const headers = new Headers(defaultOptions.headers);
      new Headers(options.headers).forEach((value, name) => headers.set(name, value));
      return { ...defaultOptions, ...options, headers };
    };
    return {
      requireJson,
      request: (path, options = {}) => request(path, mergeOptions(options)),
      requestResult: (path, options = {}) => requestResult(path, mergeOptions(options)),
      requestStatus: (path, options = {}) => requestStatus(path, mergeOptions(options)),
      get: (path, options = {}) => get(path, mergeOptions(options)),
      post: (path, body, options = {}) => post(path, body, mergeOptions(options)),
      postForm: (path, formData, options = {}) => postForm(path, formData, mergeOptions(options)),
      postStatus: (path, body, options = {}) => postStatus(path, body, mergeOptions(options)),
      raw: (path, options = {}) => raw(path, mergeOptions(options)),
    };
  }

  window.PortalAPI = {
    DEFAULT_TIMEOUT_MS,
    createClient,
    get,
    post,
    postForm,
    postStatus,
    raw,
    request,
    requestResult,
    requestStatus,
    requireJson,
  };
})();
