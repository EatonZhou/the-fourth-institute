(function () {
  "use strict";

  if (window.PortalTerminal) return;

  let assetPromise = null;

  function loadAmdModule(src) {
    return new Promise((resolve, reject) => {
      window.require([src], resolve, reject);
    });
  }

  function exposeAmdAssets(xtermModule, fitModule) {
    const TerminalClass = xtermModule?.Terminal || xtermModule?.default?.Terminal;
    const FitAddonClass = fitModule?.FitAddon || fitModule?.default?.FitAddon;
    if (!TerminalClass || !FitAddonClass) {
      throw new Error("xterm.js AMD modules did not expose their constructors");
    }
    window.Terminal = TerminalClass;
    window.FitAddon = { ...(window.FitAddon || {}), FitAddon: FitAddonClass };
  }

  function loadAssets() {
    if (window.Terminal && window.FitAddon?.FitAddon) return Promise.resolve();
    if (assetPromise) return assetPromise;
    if (typeof window.define === "function" && window.define.amd && typeof window.require === "function") {
      assetPromise = Promise.all([
        loadAmdModule("/static/vendor/xterm/xterm.js"),
        loadAmdModule("/static/vendor/xterm/addon-fit.js"),
      ]).then(([xtermModule, fitModule]) => exposeAmdAssets(xtermModule, fitModule));
      return assetPromise.catch(error => {
        assetPromise = null;
        throw error;
      });
    }
    const load = (src, marker) => new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[${marker}]`);
      const script = existing || document.createElement("script");
      script.addEventListener("load", resolve, { once: true });
      script.addEventListener("error", () => {
        script.remove();
        reject(new Error(`Unable to load ${src}`));
      }, { once: true });
      if (!existing) {
        script.src = src;
        script.setAttribute(marker, "1");
        document.head.appendChild(script);
      }
    });
    assetPromise = (window.Terminal ? Promise.resolve() : load("/static/vendor/xterm/xterm.js", "data-portal-xterm"))
      .then(() => window.FitAddon?.FitAddon ? undefined : load("/static/vendor/xterm/addon-fit.js", "data-portal-xterm-fit"))
      .catch(error => {
        assetPromise = null;
        throw error;
      });
    return assetPromise;
  }

  function createLoaded({
    mount,
    apiGet,
    apiPost,
    startUrl,
    readUrl = "/api/terminal/read",
    inputUrl = "/api/terminal/input",
    controlUrl = "/api/terminal/control",
    startPayload = () => ({}),
    isActive = () => true,
    TerminalClass = window.Terminal,
    FitAddonClass = window.FitAddon?.FitAddon,
    messages = {},
  }) {
    if (!mount) throw new Error("terminal container missing");
    if (!TerminalClass || !FitAddonClass) throw new Error("xterm.js 静态资源未加载");
    if (typeof apiGet !== "function" || typeof apiPost !== "function") {
      throw new Error("terminal API client missing");
    }
    const labels = {
      starting: window.PortalI18n.t("正在启动终端…"),
      startFailed: window.PortalI18n.t("终端启动失败"),
      readFailed: window.PortalI18n.t("终端读取失败"),
      inputFailed: window.PortalI18n.t("终端输入失败"),
      ended: window.PortalI18n.t("会话已结束"),
      ...messages,
    };
    const state = {
      sessionId: "",
      cursor: 0,
      startPromise: null,
      inputPromise: null,
      controlPromise: null,
      closePromise: null,
      pendingInput: "",
      generation: 0,
      pasteRequest: 0,
      pollInFlight: false,
      paused: false,
      pollTimer: 0,
      resizeTimer: 0,
      destroyed: false,
    };
    const term = new TerminalClass({
      cursorBlink: true,
      convertEol: false,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      scrollback: 8000,
      theme: {
        background: "#07110d",
        foreground: "#d7ffe7",
        cursor: "#6ee7b7",
        selectionBackground: "#174b38",
      },
    });
    const fitAddon = new FitAddonClass();
    term.loadAddon(fitAddon);
    term.open(mount);
    const clipboardHint = window.PortalI18n.t("选中文字后 Ctrl+C / ⌘C 复制；Ctrl+Shift+C 复制；右键或 Ctrl+Shift+V 粘贴；Ctrl+V / ⌘V 使用浏览器粘贴；Shift+右键打开浏览器菜单。无选中内容时 Ctrl+C 中断命令。");
    term.element?.setAttribute("title", clipboardHint);
    term.textarea?.setAttribute("aria-description", clipboardHint);

    async function pasteClipboard() {
      if (state.destroyed || state.closePromise || state.paused || !isActive()) return;
      const generation = state.generation;
      const request = ++state.pasteRequest;
      term.focus();
      const stillCurrent = () => !state.destroyed && !state.paused && isActive()
        && generation === state.generation && request === state.pasteRequest
        && mount.contains(document.activeElement);
      try {
        const text = await navigator.clipboard.readText();
        if (text && stillCurrent()) term.paste(text);
      } catch {
        if (stillCurrent()) {
          window.PortalUI.toast(window.PortalI18n.t("无法读取剪贴板，请允许浏览器访问剪贴板，或使用 Ctrl+V / ⌘V 粘贴。"), "warning");
        }
      }
    }

    function clipboardKeyDown(event) {
      if (state.destroyed || event.isComposing || event.altKey) return;
      const key = event.key.toLowerCase();
      const copy = key === "c" && (event.ctrlKey || event.metaKey);
      const paste = key === "v" && event.ctrlKey && event.shiftKey && !event.metaKey;
      if (!copy && !paste) return;
      const selection = copy ? term.getSelection() : "";
      if (copy && !selection && !event.shiftKey) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      if (copy && selection) void window.PortalUI.copyText(selection, window.PortalI18n.t("终端选中文字"));
      if (paste) void pasteClipboard();
    }

    function clipboardContextMenu(event) {
      if (event.shiftKey || state.destroyed) return;
      event.preventDefault();
      event.stopPropagation();
      void pasteClipboard();
    }

    // Capture before xterm moves its textarea or handles terminal control keys.
    mount.addEventListener("keydown", clipboardKeyDown, true);
    mount.addEventListener("contextmenu", clipboardContextMenu, true);

    function size() {
      return { cols: term.cols || 100, rows: term.rows || 30 };
    }

    function render(payload = {}) {
      if (payload.session_id !== undefined) state.sessionId = String(payload.session_id || "");
      let chunk = String(payload.chunk ?? "");
      if (payload.cursor !== undefined && Number.isFinite(Number(payload.cursor))) {
        const nextCursor = Number(payload.cursor);
        const unread = Math.max(0, nextCursor - state.cursor);
        chunk = unread ? Array.from(chunk).slice(-unread).join("") : "";
        state.cursor = Math.max(state.cursor, nextCursor);
      }
      if (chunk) term.write(chunk);
      if (payload.alive === false) endSession();
    }

    function endSession() {
      if (!state.sessionId) return;
      state.sessionId = "";
      state.pendingInput = "";
      state.pasteRequest += 1;
      clearPoll();
      clearResize();
      term.write(`\r\n[${labels.ended}]\r\n`);
    }

    function clearPoll() {
      if (!state.pollTimer) return;
      window.clearTimeout(state.pollTimer);
      state.pollTimer = 0;
    }

    function clearResize() {
      if (!state.resizeTimer) return;
      window.clearTimeout(state.resizeTimer);
      state.resizeTimer = 0;
    }

    function schedulePoll(delay = 900) {
      clearPoll();
      if (state.destroyed || state.paused || !isActive() || !state.sessionId) return;
      state.pollTimer = window.setTimeout(read, delay);
    }

    function start() {
      if (state.destroyed || state.closePromise) return Promise.resolve();
      state.paused = false;
      if (state.startPromise) return state.startPromise;
      if (state.sessionId) {
        schedulePoll(100);
        return Promise.resolve();
      }
      const generation = state.generation;
      state.cursor = 0;
      term.write(`\r\n${labels.starting}\r\n`);
      state.startPromise = Promise.resolve().then(async () => {
        if (state.destroyed || generation !== state.generation) return;
        try {
          const extra = typeof startPayload === "function" ? startPayload() : startPayload;
          const payload = await apiPost(
            typeof startUrl === "function" ? startUrl() : startUrl,
            { ...size(), ...(extra || {}) },
          );
          if (state.destroyed || generation !== state.generation) {
            if (payload.session_id) await apiPost(controlUrl, { session_id: payload.session_id, action: "close" });
            return;
          }
          render(payload);
          schedulePoll(100);
        } catch (error) {
          if (!state.destroyed && generation === state.generation) {
            term.write(`\r\n[${labels.startFailed}: ${error.message || error}]\r\n`);
          }
        }
      }).finally(() => { state.startPromise = null; });
      return state.startPromise;
    }

    async function read() {
      clearPoll();
      if (state.destroyed || state.paused || !isActive() || !state.sessionId || state.pollInFlight) return;
      state.pollInFlight = true;
      const generation = state.generation;
      const sessionId = state.sessionId;
      try {
        const payload = await apiGet(
          `${readUrl}?session_id=${encodeURIComponent(sessionId)}&cursor=${encodeURIComponent(state.cursor)}`,
        );
        if (!state.destroyed && generation === state.generation) render(payload);
      } catch (error) {
        if (!state.destroyed && generation === state.generation) {
          if (error.status === 404) endSession();
          else term.write(`\r\n[${labels.readFailed}: ${error.message || error}]\r\n`);
        }
      } finally {
        state.pollInFlight = false;
        schedulePoll();
      }
    }

    function drainInput() {
      if (state.inputPromise) return state.inputPromise;
      if (!state.pendingInput || state.destroyed) return Promise.resolve();
      const generation = state.generation;
      const controlPromise = state.controlPromise;
      state.inputPromise = Promise.resolve().then(async () => {
        await controlPromise;
        if (state.destroyed || generation !== state.generation) return;
        await start();
        if (state.destroyed || generation !== state.generation) return;
        if (!state.sessionId) {
          state.pendingInput = "";
          return;
        }
        const sessionId = state.sessionId;
        while (state.pendingInput && !state.destroyed && generation === state.generation) {
          const text = Array.from(state.pendingInput).slice(0, 1024).join("");
          state.pendingInput = state.pendingInput.slice(text.length);
          await apiPost(inputUrl, { session_id: sessionId, input: text });
          if (generation === state.generation) schedulePoll(100);
        }
      }).catch(error => {
        if (!state.destroyed && generation === state.generation) {
          state.pendingInput = "";
          term.write(`\r\n[${labels.inputFailed}: ${error.message || error}]\r\n`);
        }
      }).finally(() => {
        state.inputPromise = null;
        if (state.pendingInput && !state.destroyed) void drainInput();
      });
      return state.inputPromise;
    }

    function input(value) {
      if (state.destroyed || state.closePromise) return Promise.resolve();
      state.pendingInput += String(value || "");
      return drainInput();
    }

    function resize(cols, rows) {
      clearResize();
      if (!state.sessionId) return;
      state.resizeTimer = window.setTimeout(() => {
        apiPost(controlUrl, {
          session_id: state.sessionId,
          action: "resize",
          cols: Math.max(20, Math.min(240, Number(cols || 100))),
          rows: Math.max(6, Math.min(80, Number(rows || 30))),
        }).catch(() => {});
      }, 120);
    }

    function fit() {
      try {
        fitAddon.fit();
      } catch {}
      if (state.sessionId) resize(term.cols, term.rows);
    }

    function control(action) {
      const actionId = String(action || "").trim();
      if (actionId === "close") return close();
      if (!actionId || state.destroyed || state.closePromise) return Promise.resolve();
      const pending = [state.startPromise, state.inputPromise, state.controlPromise];
      if (actionId === "restart") {
        state.generation += 1;
        state.pendingInput = "";
        clearPoll();
        clearResize();
      }
      const generation = state.generation;
      const promise = Promise.resolve().then(async () => {
        await Promise.all(pending);
        if (state.destroyed || generation !== state.generation) return;
        if (!state.sessionId) {
          await start();
          return;
        }
        const payload = await apiPost(controlUrl, {
          session_id: state.sessionId,
          action: actionId,
          cursor: state.cursor,
          ...size(),
        });
        if (state.destroyed || generation !== state.generation) {
          if (actionId === "restart" && payload.session_id) {
            await apiPost(controlUrl, { session_id: payload.session_id, action: "close" });
          }
          return;
        }
        if (actionId === "restart") {
          state.cursor = 0;
          term.reset();
        }
        render(payload);
        schedulePoll(100);
      }).finally(() => {
        if (state.controlPromise === promise) state.controlPromise = null;
      });
      state.controlPromise = promise;
      return promise;
    }

    function close() {
      if (state.closePromise) return state.closePromise;
      clearPoll();
      clearResize();
      state.generation += 1;
      state.pendingInput = "";
      const sessionId = state.sessionId;
      state.sessionId = "";
      state.cursor = 0;
      const pending = [state.startPromise, state.inputPromise, state.controlPromise];
      state.closePromise = Promise.allSettled(pending).then(async () => {
        if (sessionId) await apiPost(controlUrl, { session_id: sessionId, action: "close" });
      }).finally(() => { state.closePromise = null; });
      return state.closePromise;
    }

    async function reset({ restart = false } = {}) {
      await close().catch(() => {});
      term.reset();
      if (restart) await start();
    }

    async function destroy() {
      state.destroyed = true;
      mount.removeEventListener("keydown", clipboardKeyDown, true);
      mount.removeEventListener("contextmenu", clipboardContextMenu, true);
      await close().catch(() => {});
      term.dispose();
    }

    term.onData(input);
    term.onResize(next => resize(next.cols, next.rows));

    return Object.freeze({
      close,
      control,
      destroy,
      fit,
      pause: () => { state.paused = true; state.pasteRequest += 1; clearPoll(); },
      reset,
      schedulePoll: delay => { state.paused = false; schedulePoll(delay); },
      start,
      terminal: term,
    });
  }

  function create(options = {}) {
    let controller = null;
    let controllerPromise = null;
    let destroyed = false;
    let generation = 0;
    const ensure = () => {
      if (destroyed) return Promise.reject(new Error("terminal destroyed"));
      if (controller) return Promise.resolve(controller);
      if (!controllerPromise) {
        controllerPromise = loadAssets().then(() => {
          if (destroyed) throw new Error("terminal destroyed");
          controller = createLoaded({
            ...options,
            TerminalClass: options.TerminalClass || window.Terminal,
            FitAddonClass: options.FitAddonClass || window.FitAddon?.FitAddon,
          });
          return controller;
        }).catch(error => {
          controllerPromise = null;
          throw error;
        });
      }
      return controllerPromise;
    };
    const invoke = operation => {
      const requestGeneration = generation;
      return ensure().then(value => {
        if (!destroyed && generation === requestGeneration) return operation(value);
      });
    };
    const close = () => {
      generation += 1;
      return controller ? controller.close() : Promise.resolve();
    };
    return Object.freeze({
      close,
      control: action => String(action || "").trim() === "close" ? close() : invoke(value => value.control(action)),
      destroy: async () => {
        destroyed = true;
        generation += 1;
        if (controller) await controller.destroy();
        controller = null;
      },
      fit: () => { invoke(value => value.fit()).catch(() => {}); },
      pause: () => controller?.pause(),
      reset: options => {
        generation += 1;
        return options?.restart
          ? invoke(value => value.reset(options))
          : (controller ? controller.reset(options) : Promise.resolve());
      },
      schedulePoll: delay => controller?.schedulePoll(delay),
      start: () => invoke(value => value.start()),
      get terminal() { return controller?.terminal || null; },
    });
  }

  window.PortalTerminal = Object.freeze({ create, loadAssets });
})();
