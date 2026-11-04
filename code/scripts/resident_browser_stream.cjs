"use strict";

// Playwright route.fetch buffers the entire response. Instruction watches must
// deliver headers and chunks before the subscription ends. Keep the credential
// in Node and pull the real upstream bytes with backpressure across the binding.
const http = require("node:http");
const https = require("node:https");
const zlib = require("node:zlib");

const HOP_HEADERS = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"]);

async function installInstructionStreams(context, { origin, headers }) {
  const active = new Map();
  const counters = { opened: 0, closed: 0, receivedBytes: 0 };
  function entry(source, id) {
    const item = active.get(id);
    if (!item || item.frame !== source.frame) throw new Error("Unknown native subscription");
    return item;
  }
  function close(id) {
    const item = active.get(id);
    if (!item) return;
    active.delete(id);
    counters.closed++;
    item.response?.destroy();
    item.request.destroy();
  }
  function closeFrame(frame) {
    for (const [id, item] of active) if (item.frame === frame) close(id);
  }
  context.on("page", page => {
    page.on("framedetached", closeFrame);
    page.on("framenavigated", closeFrame);
    page.on("close", () => {
      for (const [id, item] of active) if (item.frame.page() === page) close(id);
    });
  });
  context.on("close", () => { for (const id of active.keys()) close(id); });
  await context.exposeBinding("__portalNativeStreamOpen", async (source, options) => {
    const target = new URL(options.url);
    if (new URL(source.frame.url()).origin !== origin || target.origin !== origin || target.username || target.password ||
        target.pathname !== "/api/instructions/execute" || target.search || target.hash ||
        options.method !== "POST" || typeof options.body !== "string" || options.body.length > 2_000_000 ||
        !/^[0-9a-f-]{36}$/.test(options.id) || active.has(options.id)) {
      throw new Error("Invalid native subscription target");
    }
    const safeHeaders = {};
    for (const [name, value] of Object.entries(options.headers || {})) {
      const key = name.toLowerCase();
      if (!HOP_HEADERS.has(key) && !["authorization", "cookie", "x-portal-lineage-id", "accept-encoding"].includes(key)) safeHeaders[key] = value;
    }
    if (safeHeaders.accept !== "text/event-stream") throw new Error("Native subscription requires an event stream");
    return new Promise((resolve, reject) => {
      const request = (target.protocol === "https:" ? https : http).request(target, {
        method: "POST", headers: { ...safeHeaders, ...headers, "Accept-Encoding": "identity" },
      });
      const item = { request, frame: source.frame, response: null, reader: null };
      active.set(options.id, item);
      counters.opened++;
      request.on("error", error => { close(options.id); reject(error); });
      // This is an inactivity limit, not a total lifetime for an active watch.
      request.setTimeout(360000, () => request.destroy(new Error("Native subscription idle timeout")));
      request.on("response", response => {
        if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
          close(options.id); reject(new Error("Native subscription redirects are blocked")); return;
        }
        item.response = response;
        let body = response;
        const encoding = String(response.headers["content-encoding"] || "").toLowerCase();
        if (encoding === "gzip") body = response.pipe(zlib.createGunzip());
        else if (encoding === "deflate") body = response.pipe(zlib.createInflate());
        else if (encoding === "br") body = response.pipe(zlib.createBrotliDecompress());
        else if (encoding && encoding !== "identity") {
          close(options.id); reject(new Error("Unsupported native stream encoding")); return;
        }
        item.reader = body[Symbol.asyncIterator]();
        const responseHeaders = {};
        for (const [name, value] of Object.entries(response.headers)) {
          if (!HOP_HEADERS.has(name) && !["set-cookie", "content-encoding"].includes(name) && value !== undefined) {
            responseHeaders[name] = Array.isArray(value) ? value.join(", ") : value;
          }
        }
        resolve({ status: response.statusCode, statusText: response.statusMessage, headers: responseHeaders });
      });
      request.end(options.body);
    });
  });
  await context.exposeBinding("__portalNativeStreamRead", async (source, id) => {
    const item = entry(source, id);
    try {
      const next = await item.reader.next();
      if (next.done) { close(id); return { done: true }; }
      counters.receivedBytes += next.value.byteLength;
      return { done: false, chunk: Buffer.from(next.value).toString("base64") };
    } catch (error) { close(id); throw error; }
  });
  await context.exposeBinding("__portalNativeStreamClose", (source, id) => {
    if (active.has(id)) { entry(source, id); close(id); }
  });
  await context.addInitScript(({ origin }) => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = async function (input, init) {
      const target = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const headers = new Headers(init?.headers || input?.headers);
      if (location.origin !== origin || target.origin !== origin || target.pathname !== "/api/instructions/execute" ||
          method !== "POST" || headers.get("Accept") !== "text/event-stream") return nativeFetch(input, init);
      const request = new Request(input, init);
      const id = crypto.randomUUID();
      let stopped = false;
      const finish = () => { stopped = true; request.signal.removeEventListener("abort", abort); };
      const abort = () => { finish(); globalThis.__portalNativeStreamClose(id).catch(() => {}); };
      if (request.signal.aborted) throw new DOMException("Subscription aborted", "AbortError");
      request.signal.addEventListener("abort", abort, { once: true });
      try {
        const body = await request.text();
        if (request.signal.aborted) throw new DOMException("Subscription aborted", "AbortError");
        const meta = await globalThis.__portalNativeStreamOpen({ id, url: target.href, method,
          headers: Object.fromEntries(request.headers), body });
        if (stopped) {
          await globalThis.__portalNativeStreamClose(id);
          throw new DOMException("Subscription aborted", "AbortError");
        }
        const stream = new ReadableStream({
          async pull(controller) {
            try {
              const next = await globalThis.__portalNativeStreamRead(id);
              if (next.done) { finish(); controller.close(); }
              else controller.enqueue(Uint8Array.from(atob(next.chunk), c => c.charCodeAt(0)));
            } catch (error) { finish(); controller.error(request.signal.aborted ? new DOMException("Subscription aborted", "AbortError") : error); }
          },
          cancel() { abort(); },
        });
        if ([204, 205, 304].includes(meta.status)) { await stream.cancel(); return new Response(null, meta); }
        const response = new Response(stream, meta);
        Object.defineProperty(response, "url", { value: target.href });
        return response;
      } catch (error) {
        finish(); await globalThis.__portalNativeStreamClose(id).catch(() => {});
        throw request.signal.aborted ? new DOMException("Subscription aborted", "AbortError") : error;
      }
    };
  }, { origin });
  return () => ({ ...counters, active: active.size });
}

module.exports = { installInstructionStreams };
