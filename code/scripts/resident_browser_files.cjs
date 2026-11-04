"use strict";

// Response guards precede each Chromium target's first request, including popup
// navigations whose Playwright Page does not exist yet. Bodies stay in Chromium.
const fs = require("node:fs");
const path = require("node:path");
const FILE_PATHS = new Set(["/api/resources/content", "/api/agent-workbench/link-file-raw"]);
const REDIRECTS = new Set([301, 302, 303, 307, 308]);

async function installNativeFiles(context, origin) {
  const bootstrap = await context.browser().newBrowserCDPSession();
  let args;
  try { args = (await bootstrap.send("Browser.getBrowserCommandLine")).arguments; }
  finally { await bootstrap.detach(); }
  // This is the fresh helper-owned profile, never a human browser profile.
  const profile = args.find(arg => arg.startsWith("--user-data-dir="))?.slice(16);
  if (!profile) throw new Error("Native Chromium profile unavailable");
  const [port, endpoint] = fs.readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").trim().split(/\r?\n/);
  if (!/^\d+$/.test(port) || !/^\/devtools\/browser\/[a-zA-Z0-9-]+$/.test(endpoint)) throw new Error("Invalid native CDP endpoint");
  const socket = new WebSocket("ws://127.0.0.1:" + port + endpoint);
  const pending = new Map();
  let sequence = 0;
  let closed = false;
  const filter = [{ type: "page" }, { type: "iframe" }, { exclude: true }];
  const patterns = [...FILE_PATHS].flatMap(file => [file, file + "?*"].map(file => ({
    urlPattern: origin + file, requestStage: "Response",
  })));
  function allowed(value) {
    const url = new URL(value);
    return url.origin === origin && !url.username && !url.password && FILE_PATHS.has(url.pathname);
  }
  function send(method, params = {}, sessionId) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject, sessionId });
      try { socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
      catch (error) { pending.delete(id); reject(error); }
    });
  }
  async function protect(event) {
    const {sessionId, targetInfo} = event;
    try {
      await send("Fetch.enable", { patterns }, sessionId);
      await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true, filter }, sessionId);
      await send("Runtime.runIfWaitingForDebugger", {}, sessionId);
    } catch {
      // Never release an unguarded target; this browser belongs to this helper.
      if (!closed) await send("Target.closeTarget", { targetId: targetInfo.targetId }).catch(() => {});
    }
  }
  async function response(sessionId, event) {
    try {
      if (!allowed(event.request.url) || event.responseErrorReason ||
          !Number.isInteger(event.responseStatusCode) || REDIRECTS.has(event.responseStatusCode)) {
        await send("Fetch.failRequest", { requestId: event.requestId, errorReason: "BlockedByClient" }, sessionId);
      } else {
        await send("Fetch.continueResponse", { requestId: event.requestId }, sessionId);
      }
    } catch {
      await send("Fetch.failRequest", { requestId: event.requestId, errorReason: "Failed" }, sessionId).catch(() => {});
    }
  }
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const call = pending.get(message.id);
      if (!call) return;
      pending.delete(message.id);
      if (message.error) call.reject(new Error(message.error.message));
      else call.resolve(message.result);
    } else if (message.method === "Target.attachedToTarget") {
      void protect(message.params);
    } else if (message.method === "Fetch.requestPaused") {
      void response(message.sessionId, message.params);
    } else if (message.method === "Target.detachedFromTarget") {
      for (const [id, call] of pending) if (call.sessionId === message.params.sessionId) {
        pending.delete(id); call.reject(new Error("Native file target detached"));
      }
    }
  });
  socket.addEventListener("close", () => {
    for (const call of pending.values()) call.reject(new Error("Native file guard disconnected"));
    pending.clear();
    if (!closed) void context.close().catch(() => {});
  });
  context.once("close", () => { closed = true; socket.close(); });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("Native file guard connection failed")), { once: true });
  });
  await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true, filter });
  return {
    handles: request => request.method() === "GET" && allowed(request.url()),
    forward: (route, headers) => route.continue({ headers }),
  };
}

module.exports = { installNativeFiles };
