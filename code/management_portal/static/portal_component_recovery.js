(function (global) {
  "use strict";

  if (global.PortalComponentRecovery) return;

  const DEFAULT_RETRY_MS = 750;
  const DEFAULT_MAX_ATTEMPTS = 24;
  const DEFAULT_MAX_RETRY_MS = 15_000;
  const INDEPENDENT_STATUS_ENDPOINT = "/__portal_router/status";

  function create(options = {}) {
    const endpoint = String(options.endpoint || "").trim();
    const serviceName = String(options.serviceName || "Service").trim();
    const unavailableCodes = new Set(options.unavailableCodes || []);
    const hostSelector = String(options.host || "body").trim();
    const overlayId = String(options.id || "portalComponentRecovery").trim();
    const maxAttempts = Math.max(
      1,
      Math.min(60, Number(options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS) || DEFAULT_MAX_ATTEMPTS),
    );
    const retryBaseMs = Math.max(
      250,
      Number(options.retryBaseMs ?? DEFAULT_RETRY_MS) || DEFAULT_RETRY_MS,
    );
    const retryMaxMs = Math.max(
      retryBaseMs,
      Number(options.retryMaxMs ?? DEFAULT_MAX_RETRY_MS) || DEFAULT_MAX_RETRY_MS,
    );
    const disabledControls = new Set();
    let recoveryPromise = null;
    let terminalState = null;
    let lastOnReady = options.onReady;

    function controlNodes() {
      const source = typeof options.controls === "function"
        ? options.controls()
        : options.controls;
      if (typeof source === "string") return [...document.querySelectorAll(source)];
      if (!source) return [];
      if (source instanceof Element) return [source];
      return [...source].filter(node => node instanceof Element);
    }

    function setControlsDisabled(disabled) {
      if (disabled) {
        controlNodes().forEach(control => {
          if (!("disabled" in control) || control.disabled) return;
          control.disabled = true;
          disabledControls.add(control);
        });
        return;
      }
      disabledControls.forEach(control => {
        if (control.isConnected) control.disabled = false;
      });
      disabledControls.clear();
    }

    function hostRoot() {
      return typeof options.host === "object" && options.host
        ? options.host
        : document.querySelector(hostSelector);
    }

    function overlayRoot() {
      const host = hostRoot();
      if (!host) return null;
      let root = document.getElementById(overlayId);
      if (root) return root;
      root = document.createElement("div");
      root.id = overlayId;
      root.className = ["portalComponentRecovery", options.className || ""]
        .filter(Boolean)
        .join(" ");
      root.setAttribute("aria-live", "polite");
      root.hidden = true;
      host.append(root);
      return root;
    }

    function show(status = {}) {
      const root = overlayRoot();
      const host = hostRoot();
      if (!root) return;
      const restarting = String(status.state || "") === "restarting";
      const busy = String(status.state || "") === "busy";
      const terminal = isTerminal(status);
      const attempt = Number(status.recovery_attempt || 0);
      const retryProgress = attempt
        ? window.PortalI18n.text`（第 ${attempt}/${maxAttempts} 次）`
        : "";
      root.hidden = false;
      root.setAttribute("aria-live", terminal ? "assertive" : "polite");
      host?.setAttribute("aria-busy", "true");
      setControlsDisabled(true);
      global.PortalUIPrimitives?.renderAsyncState?.(root, {
        state: terminal ? "error" : "loading",
        title: terminal
          ? String(status.title || window.PortalI18n.text`${serviceName} 无法恢复`)
          : restarting
          ? window.PortalI18n.text`${serviceName} 正在重启`
          : busy
            ? window.PortalI18n.text`${serviceName} 正忙，正在等待`
          : window.PortalI18n.text`正在重新连接 ${serviceName}`,
        message: terminal
          ? String(status.message || window.PortalI18n.text`${serviceName} 无法恢复。自动重连已停止。`)
          : restarting
          ? window.PortalI18n.text`${options.surfaceName || window.PortalI18n.t("当前页面")}会在服务上线后自动恢复${retryProgress}，无需手动刷新。`
          : busy
            ? window.PortalI18n.text`${serviceName} 仍在线；当前请求完成后页面会自动恢复${retryProgress}。`
          : window.PortalI18n.text`${serviceName} 暂时不可用；正在后台自动重试${retryProgress}，Portal 其他页面不受影响。`,
        actionLabel: terminal ? window.PortalI18n.t("重新检查") : "",
        onRetry: terminal ? () => { void retry(); } : null,
      });
      options.onStatus?.({
        phase: terminal ? "terminal" : "waiting",
        restarting,
        busy,
        terminal,
        status,
      });
    }

    function hide() {
      const root = document.getElementById(overlayId);
      if (root) {
        root.hidden = true;
        root.replaceChildren();
      }
      hostRoot()?.removeAttribute("aria-busy");
      setControlsDisabled(false);
      terminalState = null;
      options.onStatus?.({ phase: "ready", restarting: false, status: null });
    }

    function isTerminal(status) {
      const state = String(status?.state || "").trim().toLowerCase();
      return status?.terminal === true
        || status?.retryable === false && status?.ready === false
        || ["blocked", "failed", "incompatible", "terminal"].includes(state);
    }

    function isUnavailable(error) {
      const code = String(error?.code || error?.data?.code || "").trim();
      return unavailableCodes.has(code);
    }

    async function readIndependentStatus() {
      try {
        const result = await global.PortalAPI.requestStatus(
          String(options.independentStatusEndpoint || INDEPENDENT_STATUS_ENDPOINT),
          { cache: "no-store" },
        );
        return result.ok && result.data && typeof result.data === "object"
          ? result.data
          : null;
      } catch {
        return null;
      }
    }

    async function readStatus(readOptions = {}) {
      let result = null;
      try {
        result = await global.PortalAPI.requestStatus(endpoint, {
          cache: "no-store",
        });
      } catch {
        const independent = await readIndependentStatus();
        if (isTerminal(independent)) return independent;
        if (readOptions.optimistic) {
          return { state: "unknown", ready: true, retry_after_ms: 0 };
        }
        return {
          state: "unavailable",
          ready: false,
          retry_after_ms: DEFAULT_RETRY_MS,
        };
      }
      if (result.ok && result.data && typeof result.data === "object") {
        if (isTerminal(result.data) || result.data.ready) return result.data;
        const independent = await readIndependentStatus();
        return isTerminal(independent) ? independent : result.data;
      }
      const independent = await readIndependentStatus();
      if (isTerminal(independent)) return independent;
      if (readOptions.optimistic) {
        return { state: "unknown", ready: true, retry_after_ms: 0 };
      }
      return {
        state: "unavailable",
        ready: false,
        retry_after_ms: DEFAULT_RETRY_MS,
      };
    }

    function waitForRetry(milliseconds, attempt) {
      const advertised = Math.max(0, Number(milliseconds) || 0);
      const backoff = retryBaseMs * (2 ** Math.min(5, Math.max(0, attempt - 1)));
      const delay = Math.max(
        250,
        Math.min(retryMaxMs, Math.max(advertised, backoff)),
      );
      return new Promise(resolve => global.setTimeout(resolve, delay));
    }

    async function fail(error) {
      hide();
      await options.onFatal?.(error);
      return false;
    }

    async function terminate(status = {}, error = null) {
      terminalState = {
        ...status,
        state: String(status.state || "terminal"),
        ready: false,
        terminal: true,
        retryable: false,
        title: String(status.title || window.PortalI18n.text`${serviceName} 无法恢复`),
        message: String(
          status.message
          || window.PortalI18n.text`${serviceName} 在有限自动重试窗口内仍不可用。自动重连已停止，可手动重新检查。`,
        ),
      };
      show(terminalState);
      const fatal = error instanceof Error
        ? error
        : new Error(terminalState.message);
      fatal.code = String(terminalState.code || "service_reconnect_exhausted");
      fatal.data = terminalState;
      await options.onFatal?.(fatal);
      return false;
    }

    function singleFlight(operation) {
      return (...args) => {
        if (recoveryPromise) return recoveryPromise;
        recoveryPromise = Promise.resolve().then(() => operation(...args)).finally(() => {
          recoveryPromise = null;
        });
        return recoveryPromise;
      };
    }

    async function recoverOperation(initialStatus = {}, onReady = options.onReady) {
      if (terminalState) {
        show(terminalState);
        return false;
      }
      if (typeof onReady === "function") lastOnReady = onReady;
      let status = initialStatus;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        if (!hostRoot()?.isConnected) return false;
        if (isTerminal(status)) return terminate(status);
        if (!status?.ready) {
          show({
            ...status,
            recovery_attempt: attempt,
            recovery_max_attempts: maxAttempts,
          });
        }
        if (status?.ready) {
          try {
            await onReady?.();
            hide();
            options.onRecovered?.();
            return true;
          } catch (error) {
            if (!isUnavailable(error)) {
              const currentStatus = await readStatus();
              if (currentStatus?.ready !== false) return fail(error);
              if (isTerminal(currentStatus)) return terminate(currentStatus, error);
              status = currentStatus;
            }
          }
        }
        if (attempt >= maxAttempts) break;
        await waitForRetry(status?.retry_after_ms, attempt);
        status = await readStatus();
      }
      return terminate({
        state: "terminal",
        code: "service_reconnect_exhausted",
        title: window.PortalI18n.text`${serviceName} 连接失败`,
        message: window.PortalI18n.text`${serviceName} 在有限自动重试窗口内仍不可用。自动重连已停止，可手动重新检查。`,
      });
    }

    async function runOperation(onReady = options.onReady) {
      if (terminalState) {
        show(terminalState);
        return false;
      }
      if (typeof onReady === "function") lastOnReady = onReady;
      let status = await readStatus({ optimistic: true });
      if (!status.ready) return recoverOperation(status, onReady);
      try {
        await onReady?.();
        hide();
        return true;
      } catch (error) {
        if (isUnavailable(error)) return recoverOperation({}, onReady);
        status = await readStatus();
        return status.ready ? fail(error) : recoverOperation(status, onReady);
      }
    }

    async function checkOperation(onReady = options.onReady) {
      if (terminalState) {
        show(terminalState);
        return false;
      }
      if (typeof onReady === "function") lastOnReady = onReady;
      const status = await readStatus();
      return status.ready ? false : recoverOperation(status, onReady);
    }

    async function retryOperation(onReady = lastOnReady) {
      terminalState = null;
      return runOperation(onReady);
    }

    const recover = singleFlight(recoverOperation);
    const run = singleFlight(runOperation);
    const check = singleFlight(checkOperation);
    const retry = singleFlight(retryOperation);

    // Runs a service request. When it fails because the service is
    // unavailable, recovery starts before the error reaches the caller.
    async function guard(request) {
      try {
        return await request();
      } catch (error) {
        if (isUnavailable(error)) void check();
        throw error;
      }
    }

    return Object.freeze({
      check,
      guard,
      hide,
      isUnavailable,
      readStatus,
      recover,
      retry,
      run,
      show,
    });
  }

  global.PortalComponentRecovery = Object.freeze({ create });
})(window);
