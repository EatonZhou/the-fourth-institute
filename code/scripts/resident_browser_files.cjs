"use strict";

// Keep native file bodies in Chromium's network/download stack. Playwright's
// fetch/fulfill path buffers and serializes the complete body through CDP.
const FILE_PATHS = new Set(["/api/resources/content", "/api/agent-workbench/link-file-raw"]);
const REDIRECTS = new Set([301, 302, 303, 307, 308]);

function installNativeFiles(context, origin) {
  const sessions = new Map();
  const observedPages = new WeakSet();
  function allowed(value) {
    const url = new URL(value);
    return url.origin === origin && !url.username && !url.password && FILE_PATHS.has(url.pathname);
  }
  function release(frame) {
    const pending = sessions.get(frame);
    if (!pending) return;
    sessions.delete(frame);
    pending.then(value => { if (value.owned) return value.session.detach(); }).catch(() => {});
  }
  function observe(page) {
    if (observedPages.has(page)) return;
    observedPages.add(page);
    page.on("framedetached", release);
    page.on("framenavigated", release);
    page.once("close", () => {
      for (const frame of sessions.keys()) if (frame.page() === page) release(frame);
    });
  }
  for (const page of context.pages()) observe(page);
  context.on("page", observe);
  context.once("close", () => { for (const frame of sessions.keys()) release(frame); });
  async function protect(frame) {
    if (sessions.has(frame)) return sessions.get(frame);
    let session;
    const pending = (async () => {
      try { session = await context.newCDPSession(frame); }
      catch (error) {
        // Ordinary subframes share their parent's Chromium target; OOPIFs have
        // their own. Only this precise no-separate-target result walks upward.
        // Other attachment/permission failures stay closed.
        if (frame.parentFrame() && String(error.message).includes("does not have a separate CDP session")) {
          return { session: (await protect(frame.parentFrame())).session, owned: false };
        }
        throw error;
      }
      session.on("Fetch.requestPaused", async event => {
        try {
          if (!allowed(event.request.url) || event.responseErrorReason ||
              !Number.isInteger(event.responseStatusCode) || REDIRECTS.has(event.responseStatusCode)) {
            await session.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "BlockedByClient" });
          } else {
            // Headers are inspected before Chromium can follow a redirect. No
            // response body crosses Node/CDP, including permission errors.
            await session.send("Fetch.continueResponse", { requestId: event.requestId });
          }
        } catch {
          await session.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "Failed" }).catch(() => {});
        }
      });
      await session.send("Fetch.enable", {
        patterns: [...FILE_PATHS].flatMap(file => [file, file + "?*"].map(file => ({
          urlPattern: origin + file, requestStage: "Response",
        }))),
      });
      return { session, owned: true };
    })();
    sessions.set(frame, pending);
    observe(frame.page());
    try { return await pending; }
    catch (error) {
      sessions.delete(frame);
      if (session) await session.detach().catch(() => {});
      throw error;
    }
  }
  return {
    handles: request => request.method() === "GET" && allowed(request.url()),
    async forward(route, headers) {
      // Attachment navigation may belong to a popup or sandboxed iframe. Attach
      // to that actual target before releasing the authenticated request.
      await protect(route.request().frame());
      return route.continue({ headers });
    },
  };
}

module.exports = { installNativeFiles };
