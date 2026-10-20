(function (global) {
  "use strict";

  if (global.PortalDemoPlayer) return;

  const BRIDGE_SCOPE = window.PortalCompatibility.identifier("demo_frame_scope");
  const DEFAULT_STEP_DELAY = 1050;
  const BRIDGE_TIMEOUT = 36_000;
  const MAX_READ_BYTES = 8 * 1024 * 1024;
  const element = ref => global.PortalUIPrimitives.elementOf(ref);

  function delay(milliseconds, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      const timer = global.setTimeout(resolve, Math.max(0, Number(milliseconds || 0)));
      signal?.addEventListener("abort", () => {
        global.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
  }

  function sessionNonce() {
    return global.PortalUIPrimitives.randomUUID();
  }

  function create(options = {}) {
    const overlay = element(options.overlay);
    const frame = element(options.frame);
    const stage = element(options.stage);
    const cursor = element(options.cursor);
    const spotlight = element(options.spotlight);
    const caption = element(options.caption);
    const counter = element(options.counter);
    const progress = element(options.progress);
    const playButton = element(options.playButton);
    const nextButton = element(options.nextButton);
    const restartButton = element(options.restartButton);
    const title = element(options.title);
    if (!overlay || !frame || !stage || !cursor || !spotlight) {
      throw new Error("Portal Demo Player requires overlay, frame, stage, cursor, and spotlight.");
    }

    const state = {
      aborter: null,
      autoplayRequested: false,
      caseId: "",
      demo: null,
      failed: false,
      generation: 0,
      index: 0,
      playing: false,
      ready: false,
      session: "",
    };
    const pendingBridge = new Map();
    let bridgeSequence = 0;
    let readyTimer = 0;

    const dialog = global.PortalUI.createDialogController({
      modal: overlay,
      manageHidden: true,
      escapeClose: true,
      backdropClose: false,
      onClose: () => stop({ destroyFrame: true }),
    });

    function setCaption(value, tone = "") {
      if (!caption) return;
      caption.textContent = String(value || "");
      caption.dataset.tone = tone;
    }

    function renderStatus() {
      const length = state.demo?.steps?.length || 0;
      const completed = Math.min(state.index, length);
      if (counter) counter.textContent = `${completed} / ${length}`;
      if (progress) progress.style.width = `${length ? (completed / length) * 100 : 0}%`;
      if (playButton) {
        playButton.textContent = state.playing ? window.PortalI18n.t("暂停") : completed >= length ? window.PortalI18n.t("再次播放") : window.PortalI18n.t("播放");
        playButton.disabled = !state.ready || state.failed;
      }
      if (nextButton) nextButton.disabled = !state.ready || state.playing || state.failed || completed >= length;
      if (restartButton) restartButton.disabled = !state.ready;
    }

    function hidePointer() {
      cursor.hidden = true;
      spotlight.hidden = true;
      cursor.classList.remove("clicking");
    }

    function rejectPending(error) {
      for (const pending of pendingBridge.values()) {
        global.clearTimeout(pending.timer);
        pending.reject(error);
      }
      pendingBridge.clear();
    }

    function resetAborter() {
      state.aborter?.abort();
      state.aborter = new AbortController();
    }

    function stop({ destroyFrame = false } = {}) {
      global.clearTimeout(readyTimer);
      readyTimer = 0;
      state.generation += 1;
      state.playing = false;
      state.ready = false;
      state.autoplayRequested = false;
      state.session = "";
      state.aborter?.abort();
      state.aborter = null;
      rejectPending(new DOMException("Aborted", "AbortError"));
      hidePointer();
      if (destroyFrame) {
        frame.removeAttribute("src");
        try {
          frame.src = "about:blank";
        } catch {
          // Removing the frame source is enough when navigation is unavailable.
        }
      }
      renderStatus();
    }

    function bridgePost(payload) {
      frame.contentWindow?.postMessage({
        scope: BRIDGE_SCOPE,
        case_id: state.caseId,
        session: state.session,
        ...payload,
      }, "*");
    }

    function bridgeCall(type, payload = {}, signal = state.aborter?.signal) {
      if (!state.ready && type !== "dispose") {
        return Promise.reject(new Error("演示 bridge 尚未就绪"));
      }
      const requestId = `parent-${++bridgeSequence}`;
      return new Promise((resolve, reject) => {
        if (signal?.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        const timer = global.setTimeout(() => {
          pendingBridge.delete(requestId);
          reject(new Error("演示 bridge 响应超时"));
        }, BRIDGE_TIMEOUT);
        const abort = () => {
          const pending = pendingBridge.get(requestId);
          if (!pending) return;
          pendingBridge.delete(requestId);
          global.clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        };
        signal?.addEventListener("abort", abort, { once: true });
        pendingBridge.set(requestId, {
          reject,
          resolve: value => {
            signal?.removeEventListener("abort", abort);
            resolve(value);
          },
          timer,
        });
        bridgePost({
          type,
          request_id: requestId,
          ...payload,
        });
      });
    }

    function readRoutePolicy(rawUrl) {
      let url;
      try {
        url = new URL(String(rawUrl || ""), global.location.origin);
      } catch {
        throw new Error("演示读取地址无效");
      }
      if (url.origin !== global.location.origin) {
        throw new Error("演示不能读取跨源地址");
      }
      const routes = Array.isArray(state.demo?.read_routes) ? state.demo.read_routes : [];
      const policy = routes.find(item => item && String(item.path || "") === url.pathname);
      if (!policy) throw new Error(`演示未授权读取 ${url.pathname}`);
      const allowed = new Set(Array.isArray(policy.query) ? policy.query.map(String) : []);
      const seen = new Set();
      for (const key of url.searchParams.keys()) {
        if (!allowed.has(key)) throw new Error(`演示未授权查询参数 ${key}`);
        if (seen.has(key)) throw new Error(`演示不允许重复查询参数 ${key}`);
        seen.add(key);
      }
      for (const [key, expected] of Object.entries(policy.required_query || {})) {
        if (url.searchParams.get(key) !== String(expected)) {
          throw new Error(`演示查询参数 ${key} 不符合只读策略`);
        }
      }
      return { policy, url };
    }

    function projectReadBody(policy, body) {
      if (String(policy?.projection || "") !== "test-catalog-demo") {
        return body;
      }
      let payload;
      try {
        payload = JSON.parse(String(body || "{}"));
      } catch {
        throw new Error("演示目录投影不是有效 JSON");
      }
      if (!payload || typeof payload !== "object") {
        throw new Error("演示目录投影格式无效");
      }
      const cases = Array.isArray(payload.cases)
        ? payload.cases.map(item => ({
          ...item,
          last_run: null,
        }))
        : [];
      const summary = payload.summary && typeof payload.summary === "object"
        ? { ...payload.summary, latest_gate_runs: {} }
        : {};
      return JSON.stringify({
        ...payload,
        cases,
        recent_analyses: [],
        recent_runs: [],
        summary,
      });
    }

    async function handleFrameFetch(message, identity) {
      const responsePayload = {
        type: "fetch-response",
        request_id: String(message.request_id || ""),
      };
      try {
        const method = String(message.method || "GET").toUpperCase();
        if (!["GET", "HEAD"].includes(method)) throw new Error(`演示已拒绝 ${method}`);
        const { policy, url } = readRoutePolicy(message.url);
        if (Object.prototype.hasOwnProperty.call(policy, "fixture")) {
          Object.assign(responsePayload, {
            body: method === "HEAD" ? "" : JSON.stringify(policy.fixture),
            headers: { "content-type": "application/json; charset=utf-8" },
            status: 200,
            status_text: "OK",
          });
        } else {
          const response = await global.PortalAPI.raw(url.href, {
            cache: "no-store",
            credentials: "same-origin",
            headers: { Accept: "application/json, text/plain;q=0.9" },
            method,
            redirect: "error",
          });
          const declaredLength = Number(response.headers.get("content-length") || 0);
          if (declaredLength > MAX_READ_BYTES) throw new Error("演示读取响应过大");
          const rawBody = method === "HEAD" ? "" : await response.text();
          if (rawBody.length > MAX_READ_BYTES) {
            throw new Error("演示读取响应过大");
          }
          const body = method === "HEAD"
            ? ""
            : projectReadBody(policy, rawBody);
          if (body.length > MAX_READ_BYTES) throw new Error("演示读取响应过大");
          Object.assign(responsePayload, {
            body,
            headers: {
              "content-type": response.headers.get("content-type") || "text/plain; charset=utf-8",
            },
            status: response.status,
            status_text: response.statusText,
          });
        }
      } catch (error) {
        responsePayload.error = String(error?.message || error);
      }
      if (
        identity.generation !== state.generation
        || identity.caseId !== state.caseId
        || identity.session !== state.session
      ) return;
      bridgePost(responsePayload);
    }

    function onBridgeMessage(event) {
      if (event.source !== frame.contentWindow) return;
      const message = event.data;
      if (
        !message
        || message.scope !== BRIDGE_SCOPE
        || message.case_id !== state.caseId
        || message.session !== state.session
      ) return;
      if (message.type === "fetch-request") {
        void handleFrameFetch(message, {
          caseId: state.caseId,
          generation: state.generation,
          session: state.session,
        });
        return;
      }
      if (message.type === "ready") {
        global.clearTimeout(readyTimer);
        readyTimer = 0;
        state.ready = true;
        state.failed = false;
        setCaption(window.PortalI18n.t("隔离沙盒已就绪。真实存储与写入通道均不可访问。"));
        renderStatus();
        if (state.autoplayRequested) {
          state.autoplayRequested = false;
          void play();
        }
        return;
      }
      if (message.type !== "reply") return;
      const requestId = String(message.request_id || "");
      const pending = pendingBridge.get(requestId);
      if (!pending) return;
      pendingBridge.delete(requestId);
      global.clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(String(message.error)));
      else pending.resolve(message.result || {});
    }
    global.addEventListener("message", onBridgeMessage);

    function rectPosition(rect) {
      const stageRect = stage.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      const width = Math.max(1, Number(rect?.width || 1));
      const height = Math.max(1, Number(rect?.height || 1));
      const left = frameRect.left - stageRect.left + Number(rect?.x || 0);
      const top = frameRect.top - stageRect.top + Number(rect?.y || 0);
      const margin = 5;
      return {
        cursorX: left + Math.max(4, Math.min(width * 0.72, width - 4)),
        cursorY: top + Math.max(4, Math.min(height * 0.68, height - 3)),
        height: height + margin * 2,
        left: left - margin,
        top: top - margin,
        width: width + margin * 2,
      };
    }

    async function pointAt(rect, signal) {
      const position = rectPosition(rect);
      spotlight.hidden = false;
      spotlight.style.left = `${Math.max(0, position.left)}px`;
      spotlight.style.top = `${Math.max(0, position.top)}px`;
      spotlight.style.width = `${position.width}px`;
      spotlight.style.height = `${position.height}px`;
      cursor.hidden = false;
      cursor.style.setProperty("--cursor-x", `${Math.max(4, position.cursorX)}px`);
      cursor.style.setProperty("--cursor-y", `${Math.max(4, position.cursorY)}px`);
      await delay(global.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? 20 : 520, signal);
    }

    async function perform(step, signal, onCommitted) {
      setCaption(step.caption || "执行行为步骤…");
      const prepared = await bridgeCall("prepare-step", { step }, signal);
      if (prepared.rect) await pointAt(prepared.rect, signal);
      if (step.action === "click") {
        cursor.classList.remove("clicking");
        void cursor.offsetWidth;
        cursor.classList.add("clicking");
      }
      await bridgeCall("commit-step", { step_id: prepared.step_id }, signal);
      onCommitted?.();
      await delay(step.action === "wait" ? 560 : DEFAULT_STEP_DELAY, signal);
    }

    async function executeOne() {
      const steps = Array.isArray(state.demo?.steps) ? state.demo.steps : [];
      if (!state.ready || state.failed || state.index >= steps.length) return false;
      const generation = state.generation;
      const signal = state.aborter?.signal;
      const stepIndex = state.index;
      let committed = false;
      try {
        await perform(steps[stepIndex], signal, () => {
          if (
            generation !== state.generation
            || state.index !== stepIndex
          ) return;
          committed = true;
          state.index += 1;
          renderStatus();
        });
        if (generation !== state.generation || signal?.aborted) {
          return committed;
        }
        if (state.index >= steps.length) {
          state.playing = false;
          setCaption("演示完成。真实 PASS / FAIL 请以该条目的测试运行结果为准。", "success");
          renderStatus();
        }
        return true;
      } catch (error) {
        if (error?.name === "AbortError") return committed;
        state.failed = true;
        state.playing = false;
        setCaption(`演示已停止：${error?.message || error}`, "error");
        global.PortalUI.toast(
          window.PortalI18n.text`行为演示可能已过时：${error?.message || error}`,
          "error",
          { key: `portal-demo:${state.caseId}`, duration: 7000 },
        );
        renderStatus();
        return false;
      }
    }

    async function play() {
      const steps = Array.isArray(state.demo?.steps) ? state.demo.steps : [];
      if (!state.ready || state.failed) return;
      if (state.index >= steps.length) {
        loadFrame({ autoplay: true });
        return;
      }
      if (!state.aborter || state.aborter.signal.aborted) resetAborter();
      state.playing = true;
      renderStatus();
      while (state.playing && state.index < steps.length) {
        if (!await executeOne()) break;
      }
    }

    function pause() {
      state.playing = false;
      resetAborter();
      setCaption("演示已暂停。再次播放会从当前步骤继续。");
      renderStatus();
    }

    async function next() {
      if (state.playing) return;
      if (!state.aborter || state.aborter.signal.aborted) resetAborter();
      await executeOne();
    }

    function loadFrame({ autoplay = true } = {}) {
      state.generation += 1;
      state.ready = false;
      state.failed = false;
      state.index = 0;
      state.playing = false;
      state.autoplayRequested = autoplay;
      state.session = sessionNonce();
      resetAborter();
      rejectPending(new DOMException("Aborted", "AbortError"));
      hidePointer();
      setCaption("正在构建不透明源只读演示沙盒…");
      renderStatus();
      const separator = String(options.frameUrl || "/test/api/demo-frame").includes("?") ? "&" : "?";
      frame.src = `${options.frameUrl || "/test/api/demo-frame"}${separator}case_id=${encodeURIComponent(state.caseId)}&session=${encodeURIComponent(state.session)}&v=${state.generation}`;
      const generation = state.generation;
      readyTimer = global.setTimeout(() => {
        if (generation !== state.generation || state.ready) return;
        state.failed = true;
        state.autoplayRequested = false;
        setCaption("演示已停止：沙盒页面没有完成初始化。", "error");
        global.PortalUI.toast(
          window.PortalI18n.t("行为演示无法初始化，请检查沙盒目标与演示接口。"),
          "error",
          { key: `portal-demo-ready:${state.caseId}`, duration: 7000 },
        );
        renderStatus();
      }, BRIDGE_TIMEOUT);
    }

    function restart({ autoplay = true } = {}) {
      if (!state.demo || !state.caseId) return;
      loadFrame({ autoplay });
    }

    function openSandbox(caseRecord) {
      const demo = caseRecord?.demo;
      if (!demo || !Array.isArray(demo.steps) || !demo.steps.length) {
        global.PortalUI.toast(window.PortalI18n.t("这个测试还没有可播放的行为演示。"), "warning");
        return false;
      }
      stop();
      state.caseId = String(caseRecord.id || "");
      state.demo = demo;
      state.index = 0;
      state.failed = false;
      if (title) title.textContent = demo.title || caseRecord.title || window.PortalI18n.t("行为演示");
      dialog.open({ caseId: state.caseId });
      loadFrame({ autoplay: true });
      return true;
    }

    async function openDirect(caseRecord, options = {}) {
      try {
        await global.PortalUIPrimitives.loadAsset("/static/portal_live_tour.js", { ready: () => !!global.PortalLiveTour });
        const liveTour = global.PortalLiveTour;
        if (!liveTour) throw new Error("真实页面演示控制器未注册");
        const started = await liveTour.start(caseRecord, options);
        if (!started) {
          await liveTour.stop?.();
          throw new Error("真实页面不满足只读演示契约");
        }
      } catch (error) {
        const canFallback = Array.isArray(caseRecord?.demo?.steps)
          && caseRecord.demo.steps.length > 0;
        global.PortalUI.toast(
          canFallback
            ? window.PortalI18n.text`真实页面演示不可用，已切换到只读沙盒：${error?.message || error}`
            : window.PortalI18n.text`真实页面演示不可用：${error?.message || error}`,
          "warning",
          { key: `portal-demo-fallback:${caseRecord?.id || ""}`, duration: 7000 },
        );
        if (canFallback) openSandbox(caseRecord);
      }
    }

    function open(caseRecord, options = {}) {
      const demo = caseRecord?.demo;
      if (demo?.mode === "direct" && demo?.risk === "read-only") {
        stop({ destroyFrame: true });
        void openDirect(caseRecord, options);
        return true;
      }
      return openSandbox(caseRecord);
    }

    playButton?.addEventListener("click", () => {
      if (state.playing) pause();
      else void play();
    });
    nextButton?.addEventListener("click", () => void next());
    restartButton?.addEventListener("click", () => restart({ autoplay: true }));
    overlay.querySelectorAll("[data-portal-detail-close]").forEach(button => {
      button.addEventListener("click", () => dialog.close());
    });

    return Object.freeze({
      close: () => dialog.close(),
      destroy() {
        stop({ destroyFrame: true });
        global.removeEventListener("message", onBridgeMessage);
        dialog.destroy();
      },
      next,
      open,
      pause,
      play,
      restart,
      state: () => ({ ...state }),
    });
  }

  global.PortalDemoPlayer = Object.freeze({ create });
})(window);
