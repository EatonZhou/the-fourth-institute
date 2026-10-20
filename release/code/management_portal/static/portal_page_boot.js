function installPortalPageBoot(config) {
  "use strict";
  if (window.PortalPageBoot) return;

  const initialUrl = location.href;
  // Embedded Portal documents share sessionStorage; never reset a sibling's budget.
  const storageKey = `portal.page-boot.retry.v1:${initialUrl}`;
  const maxRetries = 2;
  const deadlineMs = 30000;
  const assets = new Map(config.assets.map(url => [new URL(url, initialUrl).href, false]));
  const readyRoots = new WeakSet();
  const failures = new Set();
  let status = "loading";
  let parsed = false;
  let interacted = false;
  let hidden = false;
  let panel = null;
  let retryTimer = null;
  let checkTimer = null;
  let storageAvailable = true;
  let attempts = 0;
  // Recovery starts before external scripts; translate at display time when
  // the shared text facade is available, retaining raw diagnostics otherwise.
  const translate = source => window.PortalI18n?.message(source) ?? source;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (Number.isInteger(saved) && saved >= 0) {
      attempts = Math.min(maxRetries, saved);
    }
  } catch { storageAvailable = false; }

  function saveBudget(count) {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(count));
      return true;
    } catch {
      storageAvailable = false;
      return false;
    }
  }

  function pendingChecks() {
    const pending = [];
    if (config.navigation) {
      const headers = [...document.querySelectorAll("[data-portal-site-header]")];
      if (!headers.length || headers.some(root => !readyRoots.has(root))) pending.push("导航初始化");
    }
    config.checks.forEach(check => {
      const root = document.getElementById(check.id);
      if (!root || !readyRoots.has(root)) pending.push(check.label);
    });
    return pending;
  }

  function reloadPage() {
    status = "reloading";
    if (location.href !== initialUrl) history.replaceState(history.state, "", initialUrl);
    // Replacing the same URL with a hash is only a same-document navigation.
    // Explicit reload must run the document and its initializers again.
    location.reload();
  }

  function renderFailure() {
    // A parser-blocking script may stall before <body> exists.
    const host = document.body || document.documentElement;
    if (!host) return;
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "portalPageBootError";
      panel.setAttribute("role", "alert");
      panel.setAttribute("aria-live", "assertive");
      panel.style.cssText = "position:fixed;z-index:2147483647;inset:16px 16px auto auto;box-sizing:border-box;max-width:calc(100vw - 32px);width:460px;padding:20px;border:1px solid #94a3b8;border-radius:10px;background:#fff;color:#172033;box-shadow:0 6px 24px #0003;font:14px/1.5 system-ui;overflow-wrap:anywhere";
      const title = document.createElement("strong");
      const message = document.createElement("p");
      message.dataset.bootMessage = "";
      const detail = document.createElement("p");
      detail.dataset.bootDetail = "";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.style.cssText = "padding:8px 14px;cursor:pointer;border:1px solid #64748b;border-radius:6px;background:#f1f5f9;color:#172033;font:inherit";
      retry.addEventListener("click", () => {
        if (status === "reloading") return;
        clearTimeout(retryTimer);
        saveBudget(0);
        reloadPage();
      });
      panel.append(title, message, detail, retry);
      host.append(panel);
    }
    panel.querySelector("strong").textContent = translate("页面未能完成加载");
    panel.querySelector("button").textContent = translate("重新加载");
    panel.querySelector("[data-boot-message]").textContent = translate(status === "retrying"
      ? `正在自动重新加载（${attempts + 1}/${maxRetries}）…`
      : interacted ? "检测到页面操作，已停止自动重载。请保留未保存内容后手动重试。"
        : !storageAvailable ? "浏览器无法保存重试次数，已停止自动重载。请手动重试。"
          : attempts >= maxRetries ? "自动恢复次数已用完，请手动重试。"
            : "自动恢复已停止，请手动重试。");
    panel.querySelector("[data-boot-detail]").textContent = [...failures].slice(0, 5).map(translate).join(translate("；"));
  }

  function fail() {
    if (status !== "loading") return;
    clearTimeout(checkTimer);
    clearTimeout(deadline);
    status = storageAvailable && !interacted && attempts < maxRetries ? "retrying" : "error";
    renderFailure();
    if (status !== "retrying") return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (hidden) return;
      if (interacted || !saveBudget(attempts + 1)) {
        status = "error";
        renderFailure();
        return;
      }
      reloadPage();
    }, 1000 * (attempts + 1));
  }

  function check(expired = false) {
    if (hidden || status !== "loading") return;
    if (!parsed && !expired) return;
    const pendingAssets = [...assets].filter(([, loaded]) => !loaded).map(([url]) => new URL(url).pathname);
    const missing = pendingChecks();
    if (expired) {
      pendingAssets.forEach(path => failures.add(`资源加载超时：${path}`));
      missing.forEach(label => failures.add(`未完成：${label}`));
      if (!parsed) failures.add("页面文档加载超时");
    }
    if (failures.size) { fail(); return; }
    if (parsed && !pendingAssets.length && !missing.length) {
      status = "ready";
      clearTimeout(deadline);
      clearTimeout(checkTimer);
      try { sessionStorage.removeItem(storageKey); } catch { /* No retry is needed. */ }
      return;
    }
    clearTimeout(checkTimer);
    checkTimer = setTimeout(check, 100);
  }

  function resourceUrl(target) {
    if (target instanceof HTMLScriptElement) return target.src;
    if (target instanceof HTMLLinkElement && target.rel === "stylesheet") return target.href;
    return "";
  }

  // Element load/error events reach Document, but are not forwarded to Window.
  document.addEventListener("load", event => {
    const url = resourceUrl(event.target);
    if (assets.has(url)) { assets.set(url, true); check(); }
  }, true);
  document.addEventListener("error", event => {
    if (status !== "loading") return;
    const url = resourceUrl(event.target);
    if (assets.has(url)) failures.add(`资源加载失败：${new URL(url).pathname}`);
    check();
  }, true);
  window.addEventListener("error", event => {
    if (status !== "loading") return;
    if (event instanceof ErrorEvent && (assets.has(event.filename) || event.filename === initialUrl)) {
      failures.add(`启动脚本出错：${new URL(event.filename).pathname}:${event.lineno}`);
    }
    check();
  }, true);
  document.addEventListener("DOMContentLoaded", () => {
    parsed = true;
    if (status === "error" || status === "retrying") renderFailure();
    // Other DOMContentLoaded handlers publish their own readiness first.
    checkTimer = setTimeout(check, 0);
  }, { once: true });
  function onInteraction(event) {
    if (!event.isTrusted || panel?.contains(event.target) || status === "ready") return;
    interacted = true;
    if (status === "retrying") {
      clearTimeout(retryTimer);
      status = "error";
      renderFailure();
    }
  }
  for (const event of ["input", "pointerdown", "keydown"]) document.addEventListener(event, onInteraction, true);
  window.addEventListener("pagehide", () => {
    hidden = true;
    clearTimeout(deadline);
    clearTimeout(checkTimer);
    clearTimeout(retryTimer);
  });
  window.addEventListener("pageshow", event => {
    if (!event.persisted) return;
    hidden = false;
    if (status === "loading") {
      deadline = setTimeout(() => check(true), deadlineMs);
      check();
    } else if (status === "retrying" || status === "reloading") {
      status = "error";
      renderFailure();
    }
  });
  let deadline = setTimeout(() => check(true), deadlineMs);
  window.PortalPageBoot = Object.freeze({
    ready(root) { if (root instanceof Element) readyRoots.add(root); },
    get status() { return status; },
  });
}
