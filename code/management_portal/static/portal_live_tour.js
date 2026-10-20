(function (global) {
  "use strict";

  const SESSION_VERSION = 1;
  const SESSION_TTL = 15 * 60 * 1000;
  const STEP_DELAY = 850;
  const READY_TIMEOUT = 15_000;
  // Page adapters load on demand into PortalDesignLiveAdapters; production
  // components carry none.  Test's own adapter (test-lab) comes with its view.
  const DESIGN_ADAPTER_ASSETS = Object.freeze({
    "design-ui": "/static/portal_design_live_ui.js",
    "design-workbench": "/static/portal_design_live_workbench.js",
    "design-chat": "/static/portal_design_live_chat.js",
    "design-lineage": "/static/portal_design_live_lineage.js",
    "design-resource": "/static/portal_design_live_resource.js",
    "design-testlab": "/static/portal_design_live_testlab.js",
    "design-wall": "/static/portal_design_live_wall.js",
    "design-shell": "/static/portal_design_live_shell.js",
  });
  const designAdapterLoads = new Map();
  const BLOCKED_GET_PATHS = new Set([
    "/logout",
    "/api/restart",
    "/api/reload",
  ]);
  const sessionStore = global.PortalLiveTourSessionStore;
  if (!sessionStore) {
    throw new Error("PortalLiveTour requires PortalLiveTourSessionStore.");
  }

  if (global.PortalLiveTour) {
    void global.PortalLiveTour.resume?.();
    return;
  }

  const runtime = {
    adapter: null,
    adapterSnapshot: null,
    aborter: null,
    committing: false,
    descriptor: null,
    expiryTimer: 0,
    generation: 0,
    guard: null,
    playing: false,
    resumePromise: null,
    root: null,
    session: null,
  };

  function delay(milliseconds, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      const timer = global.setTimeout(resolve, Math.max(0, Number(milliseconds || 0)));
      const abort = () => {
        global.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      signal?.addEventListener("abort", abort, { once: true });
    });
  }

  function nonce() {
    return global.PortalUIPrimitives.randomUUID();
  }

  function normalizePath(value) {
    const clean = String(value || "/").replace(/\/+$/, "");
    return clean || "/";
  }

  function localUrl(value) {
    const url = new URL(String(value || ""), global.location.origin);
    if (url.origin !== global.location.origin) {
      throw new Error("行为演示只能导航到当前 Portal");
    }
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function pageRoute(value) {
    const url = new URL(value, global.location.origin);
    const path = normalizePath(url.pathname);
    // The development page hosts independently mounted views (WALL, Test, ...);
    // /wall is its former address and redirects there. Selection/filter query
    // parameters remain same-page state, but switching the view changes hosts.
    if (path === "/wall" || path === "/development") {
      const view = url.searchParams.get("view") || (path === "/wall" ? "wall" : "development");
      return `/development?view=${view}`;
    }
    return path;
  }

  function readSession() {
    const raw = sessionStore.readRaw();
    if (!raw) return null;
    try {
      const session = JSON.parse(raw);
      const startedAt = Number(session?.startedAt || 0);
      if (
        session?.v !== SESSION_VERSION
        || !/^[A-Za-z0-9-]{16,100}$/.test(String(session.sessionId || ""))
        || !String(session.caseId || "")
        || !["target", "returning"].includes(String(session.phase || ""))
        || !startedAt
        || Date.now() - startedAt > SESSION_TTL
      ) {
        clearSession();
        return null;
      }
      session.origin = localUrl(session.origin);
      session.target = localUrl(session.target);
      session.targetPath = normalizePath(new URL(
        session.target,
        global.location.origin,
      ).pathname);
      return session;
    } catch {
      clearSession();
      return null;
    }
  }

  function writeSession(session) {
    const next = {
      ...session,
      updatedAt: Date.now(),
    };
    sessionStore.write(next);
    runtime.session = next;
    return next;
  }

  function clearSession() {
    sessionStore.clear();
    runtime.session = null;
  }

  function loadStyles() {
    if (document.querySelector('link[data-portal-live-tour-style]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/portal_live_tour.css";
    link.dataset.portalLiveTourStyle = "1";
    document.head.appendChild(link);
  }

  function adapterFor(id) {
    if (Object.hasOwn(DESIGN_ADAPTER_ASSETS, id)) {
      return global.PortalDesignLiveAdapters?.[id] || null;
    }
    if (id === "test-lab") return global.PortalTestLabDemoAdapter || null;
    return null;
  }

  function loadDesignAdapter(id) {
    if (!Object.hasOwn(DESIGN_ADAPTER_ASSETS, id) || adapterFor(id)) {
      return Promise.resolve();
    }
    if (designAdapterLoads.has(id)) return designAdapterLoads.get(id);
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const finish = error => {
        global.clearTimeout(timer);
        script.onload = null;
        script.onerror = null;
        if (error) {
          script.remove();
          reject(error);
        } else {
          resolve();
        }
      };
      const timer = global.setTimeout(() => {
        finish(new Error(`Live 适配器 ${id} 加载超时`));
      }, READY_TIMEOUT);
      script.src = DESIGN_ADAPTER_ASSETS[id];
      script.async = true;
      script.onload = () => finish(adapterFor(id) ? null : new Error(`Live 适配器 ${id} 未注册`));
      script.onerror = () => finish(new Error(`Live 适配器 ${id} 加载失败`));
      document.head.appendChild(script);
    }).catch(error => {
      designAdapterLoads.delete(id);
      throw error;
    });
    designAdapterLoads.set(id, promise);
    return promise;
  }

  function adapterContext() {
    return Object.freeze({
      caseId: String(runtime.session?.caseId || ""),
      sessionId: String(runtime.session?.sessionId || ""),
      targetPath: String(runtime.session?.targetPath || ""),
    });
  }

  async function waitForAdapter(id, expectedVersion) {
    await loadDesignAdapter(id);
    const started = performance.now();
    const version = Number(expectedVersion);
    while (performance.now() - started < READY_TIMEOUT) {
      const adapter = adapterFor(id);
      if (
        adapter
        && adapter.id === id
        && Number(adapter.version) === version
        && adapter.ready?.()
      ) return adapter;
      await delay(80);
    }
    throw new Error(`真实页面没有注册可用的 ${id} 只读演示适配器`);
  }

  async function fetchDescriptor(caseId) {
    const payload = await global.PortalAPI.get(
      `/test/api/demo?case_id=${encodeURIComponent(String(caseId || ""))}`,
      { headers: { Accept: "application/json" } },
    );
    const demo = payload?.demo;
    if (
      !global.PortalCompatibility.schemaMatches(payload?.schema, "tfi.portal-live-tour.v1")
      || String(payload.case_id || "") !== String(caseId || "")
      || !demo
      || demo.mode !== "direct"
      || demo.risk !== "read-only"
      || !String(demo.adapter || "")
      || Number(demo.adapter_version) !== 1
      || !Array.isArray(demo.direct_steps)
      || !demo.direct_steps.length
      || demo.direct_steps.length > 40
    ) {
      throw new Error("服务器没有返回有效的只读真实页面演示计划");
    }
    for (const step of demo.direct_steps) {
      if (
        !step
        || typeof step !== "object"
        || !String(step.capability || "")
        || ["selector", "target", "action", "expect", "url", "method"]
          .some(key => Object.prototype.hasOwnProperty.call(step, key))
      ) {
        throw new Error("真实页面演示计划包含未登记的动作字段");
      }
    }
    payload.revision = String(payload.revision || "");
    payload.demo.target = localUrl(payload.demo.target);
    return payload;
  }

  function mount() {
    if (runtime.root?.isConnected) return runtime.root;
    loadStyles();
    const root = document.createElement("section");
    root.className = "portalLiveTour";
    root.dataset.portalLiveTour = "1";
    root.setAttribute("aria-label", window.PortalI18n.t("Portal 真实页面行为演示"));
    root.innerHTML = window.PortalI18n.html`
      <div class="portalLiveTourBar" data-portal-live-tour-control>
        <div class="portalLiveTourCopy">
          <div class="portalLiveTourIdentity">
            <span class="portalLiveTourSafety">真实页面 · 只读保护</span>
            <strong class="portalLiveTourTitle"></strong>
          </div>
          <p class="portalLiveTourCaption" role="status" aria-live="polite">准备演示…</p>
        </div>
        <div class="portalLiveTourActions">
          <span class="portalLiveTourCounter" aria-live="polite">0 / 0</span>
          <button class="portalLiveTourButton" type="button" data-tour-restart>重播</button>
          <button class="portalLiveTourButton" type="button" data-tour-next>下一步</button>
          <button class="portalLiveTourButton is-primary" type="button" data-tour-play>暂停</button>
          <button class="portalLiveTourButton" type="button" data-tour-stop>返回 Test</button>
        </div>
        <div class="portalLiveTourProgress"><span></span></div>
      </div>
      <div class="portalLiveTourSpotlight" hidden aria-hidden="true"></div>
      <div class="portalLiveTourCursor" data-portal-icon="cursor" hidden aria-hidden="true"></div>`;
    root.querySelector("[data-tour-play]").addEventListener("click", () => {
      if (runtime.playing) pause("演示已暂停。");
      else void play();
    });
    root.querySelector("[data-tour-next]").addEventListener("click", () => void next());
    root.querySelector("[data-tour-restart]").addEventListener("click", () => void restart());
    root.querySelector("[data-tour-stop]").addEventListener("click", () => void stop());
    document.body.appendChild(root);
    runtime.root = root;
    installInterruptionHandlers();
    render();
    return root;
  }

  function unmount() {
    removeInterruptionHandlers();
    runtime.root?.remove();
    runtime.root = null;
  }

  function setCaption(value, tone = "") {
    if (!runtime.root) return;
    runtime.root.dataset.tone = tone;
    runtime.root.querySelector(".portalLiveTourCaption").textContent = String(value || "");
  }

  function render() {
    if (!runtime.root) return;
    const steps = runtime.descriptor?.demo?.direct_steps || [];
    const index = Math.min(Number(runtime.session?.index || 0), steps.length);
    runtime.root.querySelector(".portalLiveTourTitle").textContent = String(
      runtime.descriptor?.demo?.title || runtime.descriptor?.title || window.PortalI18n.t("行为演示"),
    );
    runtime.root.querySelector(".portalLiveTourCounter").textContent = `${index} / ${steps.length}`;
    runtime.root.querySelector(".portalLiveTourProgress > span").style.width = `${
      steps.length ? (index / steps.length) * 100 : 0
    }%`;
    const completed = runtime.session?.status === "completed";
    const failed = runtime.session?.status === "failed";
    const playButton = runtime.root.querySelector("[data-tour-play]");
    playButton.textContent = runtime.playing ? window.PortalI18n.t("暂停") : completed ? window.PortalI18n.t("已完成") : window.PortalI18n.t("继续");
    playButton.disabled = completed || failed || !runtime.adapter;
    runtime.root.querySelector("[data-tour-next]").disabled = (
      runtime.playing || completed || failed || !runtime.adapter || index >= steps.length
    );
    runtime.root.querySelector("[data-tour-restart]").disabled = failed || !runtime.adapter;
  }

  function resetAborter() {
    runtime.aborter?.abort();
    runtime.aborter = new AbortController();
  }

  function hidePointer() {
    if (!runtime.root) return;
    runtime.root.querySelector(".portalLiveTourSpotlight").hidden = true;
    runtime.root.querySelector(".portalLiveTourCursor").hidden = true;
  }

  function intersects(left, top, width, height, rect) {
    return (
      left < rect.right
      && left + width > rect.left
      && top < rect.bottom
      && top + height > rect.top
    );
  }

  async function pointAt(target, signal) {
    if (!(target instanceof Element) || !target.isConnected) {
      throw new Error("演示目标已经离开真实页面");
    }
    target.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
    await new Promise(resolve => global.requestAnimationFrame(
      () => global.requestAnimationFrame(resolve),
    ));
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const rect = target.getBoundingClientRect();
    if (!rect.width || !rect.height || !target.getClientRects().length) {
      throw new Error("演示目标当前不可见");
    }
    const margin = 6;
    const spotlight = runtime.root.querySelector(".portalLiveTourSpotlight");
    const cursor = runtime.root.querySelector(".portalLiveTourCursor");
    spotlight.hidden = false;
    spotlight.style.left = `${Math.max(2, rect.left - margin)}px`;
    spotlight.style.top = `${Math.max(2, rect.top - margin)}px`;
    spotlight.style.width = `${Math.max(1, rect.width + margin * 2)}px`;
    spotlight.style.height = `${Math.max(1, rect.height + margin * 2)}px`;
    cursor.hidden = false;
    cursor.style.left = `${Math.max(5, Math.min(
      global.innerWidth - 24,
      rect.left + Math.max(6, Math.min(rect.width * .72, rect.width - 4)),
    ))}px`;
    cursor.style.top = `${Math.max(5, Math.min(
      global.innerHeight - 28,
      rect.top + Math.max(6, Math.min(rect.height * .68, rect.height - 4)),
    ))}px`;
    runtime.root.classList.remove("is-bottom");
    const bar = runtime.root.querySelector(".portalLiveTourBar").getBoundingClientRect();
    runtime.root.classList.toggle(
      "is-bottom",
      intersects(rect.left, rect.top, rect.width, rect.height, bar),
    );
    await delay(
      global.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? 20 : 420,
      signal,
    );
  }

  function blockedEffect(kind) {
    const error = new Error(`真实页面只读保护已阻止 ${kind}`);
    error.code = "blocked_effect";
    return error;
  }

  function requestPolicy(input, init = {}) {
    const requestMethod = input instanceof global.Request ? input.method : "";
    const method = String(init.method || requestMethod || "GET").toUpperCase();
    let url;
    try {
      url = new URL(
        input instanceof global.Request ? input.url : String(input || ""),
        global.location.href,
      );
    } catch {
      throw blockedEffect("无效网络地址");
    }
    if (!["GET", "HEAD"].includes(method)) throw blockedEffect(method);
    if (url.origin !== global.location.origin) throw blockedEffect("跨源读取");
    if (BLOCKED_GET_PATHS.has(normalizePath(url.pathname))) {
      throw blockedEffect(url.pathname);
    }
  }

  function installWriteGuard() {
    if (runtime.guard) return;
    const original = {
      fetch: global.fetch,
      open: global.open,
      sendBeacon: global.navigator?.sendBeacon,
      xhrOpen: global.XMLHttpRequest?.prototype?.open,
      xhrSend: global.XMLHttpRequest?.prototype?.send,
      formSubmit: global.HTMLFormElement?.prototype?.submit,
      formRequestSubmit: global.HTMLFormElement?.prototype?.requestSubmit,
      showOpenFilePicker: global.showOpenFilePicker,
      showSaveFilePicker: global.showSaveFilePicker,
    };
    global.fetch = function (input, init) {
      if (runtime.committing) requestPolicy(input, init || {});
      return original.fetch.call(this, input, init);
    };
    if (original.xhrOpen && original.xhrSend) {
      global.XMLHttpRequest.prototype.open = function (method, url) {
        this.__portalLiveTourRequest = { method, url };
        return original.xhrOpen.apply(this, arguments);
      };
      global.XMLHttpRequest.prototype.send = function () {
        if (runtime.committing) {
          requestPolicy(
            this.__portalLiveTourRequest?.url || "",
            { method: this.__portalLiveTourRequest?.method || "GET" },
          );
        }
        return original.xhrSend.apply(this, arguments);
      };
    }
    if (original.formSubmit) {
      global.HTMLFormElement.prototype.submit = function () {
        if (runtime.committing) throw blockedEffect("表单提交");
        return original.formSubmit.apply(this, arguments);
      };
    }
    if (original.formRequestSubmit) {
      global.HTMLFormElement.prototype.requestSubmit = function () {
        if (runtime.committing) throw blockedEffect("表单提交");
        return original.formRequestSubmit.apply(this, arguments);
      };
    }
    try {
      global.navigator.sendBeacon = function () {
        if (runtime.committing) return false;
        return original.sendBeacon?.apply(this, arguments) || false;
      };
    } catch {
      // Some browsers expose sendBeacon as read-only; adapters still cannot call it.
    }
    global.open = function () {
      if (runtime.committing) throw blockedEffect("window.open");
      return original.open?.apply(this, arguments) || null;
    };
    if (typeof original.showOpenFilePicker === "function") {
      global.showOpenFilePicker = function () {
        if (runtime.committing) return Promise.reject(blockedEffect("文件选择"));
        return original.showOpenFilePicker.apply(this, arguments);
      };
    }
    if (typeof original.showSaveFilePicker === "function") {
      global.showSaveFilePicker = function () {
        if (runtime.committing) return Promise.reject(blockedEffect("文件保存"));
        return original.showSaveFilePicker.apply(this, arguments);
      };
    }
    const onSubmit = event => {
      if (!runtime.committing) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      throw blockedEffect("表单提交");
    };
    const onClick = event => {
      if (!runtime.committing) return;
      const unsafe = event.target?.closest?.(
        'a[href], [download], [formaction], input[type="file"], button[type="submit"], input[type="submit"]',
      );
      if (!unsafe) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      throw blockedEffect("导航、下载或提交控件");
    };
    document.addEventListener("submit", onSubmit, true);
    document.addEventListener("click", onClick, true);
    runtime.guard = { onClick, onSubmit, original };
  }

  function removeWriteGuard() {
    const guard = runtime.guard;
    if (!guard) return;
    const { original } = guard;
    global.fetch = original.fetch;
    global.open = original.open;
    if (original.xhrOpen) global.XMLHttpRequest.prototype.open = original.xhrOpen;
    if (original.xhrSend) global.XMLHttpRequest.prototype.send = original.xhrSend;
    if (original.formSubmit) global.HTMLFormElement.prototype.submit = original.formSubmit;
    if (original.formRequestSubmit) {
      global.HTMLFormElement.prototype.requestSubmit = original.formRequestSubmit;
    }
    try {
      if (original.sendBeacon) global.navigator.sendBeacon = original.sendBeacon;
    } catch {
      // Ignore read-only browser globals during cleanup.
    }
    if (original.showOpenFilePicker) global.showOpenFilePicker = original.showOpenFilePicker;
    if (original.showSaveFilePicker) global.showSaveFilePicker = original.showSaveFilePicker;
    document.removeEventListener("submit", guard.onSubmit, true);
    document.removeEventListener("click", guard.onClick, true);
    runtime.guard = null;
  }

  function userInterrupted(event) {
    if (
      !event.isTrusted
      || !runtime.playing
      || event.target?.closest?.("[data-portal-live-tour-control]")
    ) return;
    pause("你已接管真实页面；演示已暂停，可从当前步骤继续。");
  }

  function visibilityChanged() {
    if (document.hidden && runtime.playing) {
      pause("页面已隐藏；为避免后台自动操作，演示已暂停。");
    }
  }

  function installInterruptionHandlers() {
    document.addEventListener("pointerdown", userInterrupted, true);
    document.addEventListener("keydown", userInterrupted, true);
    document.addEventListener("wheel", userInterrupted, { capture: true, passive: true });
    document.addEventListener("visibilitychange", visibilityChanged);
  }

  function removeInterruptionHandlers() {
    document.removeEventListener("pointerdown", userInterrupted, true);
    document.removeEventListener("keydown", userInterrupted, true);
    document.removeEventListener("wheel", userInterrupted, true);
    document.removeEventListener("visibilitychange", visibilityChanged);
  }

  async function executeOne() {
    const session = runtime.session;
    const steps = runtime.descriptor?.demo?.direct_steps || [];
    const index = Number(session?.index || 0);
    if (!session || index >= steps.length || session.status === "failed") return false;
    const generation = runtime.generation;
    const signal = runtime.aborter?.signal;
    const step = steps[index];
    let committed = false;
    try {
      setCaption(step.caption || "执行登记过的只读行为…");
      const context = adapterContext();
      const prepared = await runtime.adapter.prepare(step, signal, context);
      const target = prepared?.target;
      if (!(target instanceof Element) || !target.isConnected) {
        throw new Error(`能力 ${step.capability} 没有返回稳定的页面目标`);
      }
      await pointAt(target, signal);
      if (signal?.aborted || generation !== runtime.generation) return false;
      runtime.committing = true;
      let result;
      try {
        result = await runtime.adapter.commit(
          step,
          prepared,
          signal,
          context,
        );
      } finally {
        runtime.committing = false;
      }
      committed = true;
      const committedTarget = result?.target;
      if (
        committedTarget instanceof Element
        && committedTarget.isConnected
        && committedTarget !== target
      ) {
        await pointAt(committedTarget, signal);
      }
      if (generation !== runtime.generation) return true;
      writeSession({
        ...runtime.session,
        index: index + 1,
        status: runtime.playing ? "playing" : "paused",
      });
      setCaption(
        result?.caption || step.caption || (
          result?.skipped ? "当前真实数据不适用，此步骤已安全跳过。" : "步骤完成。"
        ),
      );
      render();
      await delay(STEP_DELAY, signal);
      if (Number(runtime.session?.index || 0) >= steps.length) finish();
      return true;
    } catch (error) {
      runtime.committing = false;
      if (error?.name === "AbortError") return committed;
      fail(error);
      return false;
    }
  }

  async function play() {
    if (!runtime.adapter || !runtime.session) return;
    if (runtime.session.status === "completed") return;
    if (!runtime.aborter || runtime.aborter.signal.aborted) resetAborter();
    runtime.playing = true;
    writeSession({ ...runtime.session, status: "playing" });
    render();
    while (
      runtime.playing
      && Number(runtime.session?.index || 0)
        < (runtime.descriptor?.demo?.direct_steps?.length || 0)
    ) {
      if (!await executeOne()) break;
    }
  }

  function pause(message = "演示已暂停。") {
    runtime.playing = false;
    resetAborter();
    if (runtime.session && runtime.session.status !== "completed") {
      writeSession({ ...runtime.session, status: "paused" });
    }
    setCaption(message);
    render();
  }

  async function next() {
    if (runtime.playing || !runtime.adapter) return;
    if (!runtime.aborter || runtime.aborter.signal.aborted) resetAborter();
    await executeOne();
  }

  function finish() {
    runtime.playing = false;
    writeSession({ ...runtime.session, status: "completed" });
    setCaption(
      "演示完成：动作发生在真实 Portal 页面；这不等于测试 PASS，正式结果仍以测试运行记录为准。",
      "success",
    );
    render();
  }

  function fail(error) {
    runtime.playing = false;
    resetAborter();
    if (runtime.session) writeSession({ ...runtime.session, status: "failed" });
    setCaption(`演示已安全停止：${error?.message || error}`, "error");
    runtime.root && (runtime.root.dataset.tone = "error");
    render();
    global.PortalUI?.toast?.(
      `真实页面演示已停止：${error?.message || error}`,
      "error",
      { key: `portal-live-tour:${runtime.session?.caseId || ""}`, duration: 7000 },
    );
  }

  async function restoreAdapter() {
    if (!runtime.adapter) return;
    try {
      await runtime.adapter.restore?.(
        runtime.adapterSnapshot,
        adapterContext(),
      );
    } catch (error) {
      setCaption(`页面状态恢复失败：${error?.message || error}`, "error");
      throw error;
    }
  }

  async function restart() {
    if (!runtime.adapter || !runtime.session) return;
    runtime.generation += 1;
    runtime.playing = false;
    resetAborter();
    await restoreAdapter();
    global.PortalState?.rollbackEphemeral?.(`live-tour:${runtime.session.sessionId}`);
    global.PortalState?.beginEphemeral?.(`live-tour:${runtime.session.sessionId}`);
    runtime.adapterSnapshot = await runtime.adapter.capture?.(
      adapterContext(),
    );
    writeSession({ ...runtime.session, index: 0, status: "playing" });
    hidePointer();
    setCaption("已恢复演示前状态，正在重新开始。");
    render();
    await play();
  }

  async function restoreOrigin(session) {
    try {
      const adapter = await waitForAdapter("test-lab", 1);
      await adapter.restore?.(session.originState || {});
      clearSession();
      global.PortalUI?.toast?.("已返回 Test，并恢复演示前的筛选与位置。", "success", {
        key: "portal-live-tour:return",
      });
    } catch (error) {
      clearSession();
      global.PortalUI?.toast?.(
        `已返回 Test，但视图恢复失败：${error?.message || error}`,
        "error",
        { key: "portal-live-tour:return" },
      );
    }
  }

  async function stop() {
    // The persisted store is the canonical cross-page owner. It may have been
    // advanced by the destination page while this runtime still holds the
    // session that originally launched the tour.
    const session = readSession() || runtime.session;
    runtime.generation += 1;
    runtime.playing = false;
    runtime.aborter?.abort();
    global.clearTimeout(runtime.expiryTimer);
    if (runtime.adapter) {
      try {
        await restoreAdapter();
      } catch {
        // Returning to Test is still the safest recovery path.
      }
    }
    if (session) {
      global.PortalState?.rollbackEphemeral?.(`live-tour:${session.sessionId}`);
    }
    removeWriteGuard();
    unmount();
    runtime.adapter = null;
    runtime.adapterSnapshot = null;
    runtime.descriptor = null;
    if (!session) {
      clearSession();
      return;
    }
    const origin = localUrl(session.origin);
    if (pageRoute(origin) === pageRoute(global.location.href)) {
      try {
        global.history.replaceState(global.history.state, "", origin);
      } catch {
        // The current same-origin document is already the correct fallback.
      }
      if (pageRoute(session.target) === pageRoute(origin)) {
        clearSession();
      } else {
        await restoreOrigin(session);
      }
      return;
    }
    writeSession({ ...session, phase: "returning", status: "stopped" });
    global.location.replace(origin);
  }

  async function resumeInternal() {
    const session = readSession();
    if (!session) return false;
    runtime.session = session;
    if (session.phase === "returning") {
      await restoreOrigin(session);
      return true;
    }
    const descriptor = await fetchDescriptor(session.caseId);
    runtime.descriptor = descriptor;
    if (
      String(session.revision || "") !== descriptor.revision
      || String(session.adapter || "") !== String(descriptor.demo.adapter || "")
    ) {
      throw new Error("行为演示计划或页面适配器已经更新，请从 Test 重新开始");
    }
    const targetPath = normalizePath(
      new URL(descriptor.demo.target, global.location.origin).pathname,
    );
    if (pageRoute(descriptor.demo.target) !== pageRoute(global.location.href)) {
      mount();
      writeSession({ ...session, status: "paused" });
      setCaption(
        `你已离开演示目标；预期页面为 ${targetPath}。返回 Test 后可重新开始。`,
        "error",
      );
      runtime.root.dataset.tone = "error";
      render();
      return false;
    }
    runtime.adapter = await waitForAdapter(
      descriptor.demo.adapter,
      descriptor.demo.adapter_version,
    );
    global.PortalState?.beginEphemeral?.(`live-tour:${session.sessionId}`);
    runtime.adapterSnapshot = await runtime.adapter.capture?.();
    installWriteGuard();
    mount();
    resetAborter();
    runtime.expiryTimer = global.setTimeout(
      () => void stop(),
      Math.max(1, SESSION_TTL - (Date.now() - Number(session.startedAt || Date.now()))),
    );
    if (session.status === "completed") {
      finish();
      return true;
    }
    if (session.status === "paused") {
      setCaption("演示已暂停，可继续或单步执行。");
      render();
      return true;
    }
    await play();
    return true;
  }

  function resume() {
    if (runtime.resumePromise) return runtime.resumePromise;
    runtime.resumePromise = Promise.resolve()
      .then(resumeInternal)
      .catch(error => {
        const session = runtime.session || readSession();
        runtime.session = session;
        if (session) {
          mount();
          fail(error);
        } else {
          clearSession();
        }
        return false;
      })
      .finally(() => {
        runtime.resumePromise = null;
      });
    return runtime.resumePromise;
  }

  async function start(caseRecord, options = {}) {
    const demo = caseRecord?.demo;
    const caseId = String(caseRecord?.id || "");
    if (
      !caseId
      || demo?.mode !== "direct"
      || demo?.risk !== "read-only"
    ) return false;
    const descriptor = await fetchDescriptor(caseId);
    const target = descriptor.demo.target;
    const targetPath = normalizePath(new URL(target, global.location.origin).pathname);
    const origin = localUrl(
      `${global.location.pathname}${global.location.search}${global.location.hash}`,
    );
    if (pageRoute(origin) !== "/development?view=test") {
      throw new Error("真实页面演示必须从 Test 工作区发起");
    }
    const providedOriginState = options?.originState;
    const originState = (
      providedOriginState
      && typeof providedOriginState === "object"
      && !Array.isArray(providedOriginState)
    )
      ? providedOriginState
      : global.PortalTestLabDemoAdapter?.capture?.(caseId) || {
        scrollY: global.scrollY,
        focusCaseId: caseId,
        focusAction: "demo",
      };
    const session = writeSession({
      v: SESSION_VERSION,
      sessionId: nonce(),
      caseId,
      revision: descriptor.revision,
      adapter: descriptor.demo.adapter,
      target,
      targetPath,
      origin,
      originState,
      index: 0,
      status: "playing",
      phase: "target",
      startedAt: Date.now(),
    });
    runtime.descriptor = descriptor;
    runtime.session = session;
    if (pageRoute(target) !== pageRoute(global.location.href)) {
      global.location.replace(target);
      return true;
    }
    return Boolean(await resume());
  }

  global.PortalLiveTour = Object.freeze({
    key: sessionStore.key,
    next,
    pause,
    play,
    resume,
    restart,
    start,
    state: () => ({
      adapter: runtime.adapter?.id || "",
      caseId: runtime.session?.caseId || "",
      committing: runtime.committing,
      index: Number(runtime.session?.index || 0),
      playing: runtime.playing,
      status: runtime.session?.status || "",
    }),
    stop,
  });

  if (readSession()) void resume();
})(window);
