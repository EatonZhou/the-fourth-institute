import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = fs.readFileSync(new URL("../../../management_portal/static/portal_api.js", import.meta.url), "utf8");

function harness() {
  let elapsed = 0;
  let sequence = 0;
  const timers = new Map();
  const window = {
    setTimeout(fn, ms) { const id = ++sequence; timers.set(id, { fn, at: elapsed + ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    PortalI18n: { message: value => value, requestLanguage: () => "zh-CN" },
  };
  vm.runInNewContext(source, {
    window, Headers, FormData, AbortController, DOMException, URL,
    fetch(_path, { signal }) {
      return new Promise((_resolve, reject) => {
        if (signal.aborted) reject(signal.reason);
        else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    },
  });
  return {
    api: window.PortalAPI,
    advance(ms) {
      elapsed += ms;
      for (const [id, timer] of timers) {
        if (timer.at <= elapsed) { timers.delete(id); timer.fn(); }
      }
    },
    timers,
  };
}

test("default HTTP request survives 120 seconds and expires at 360 seconds", async () => {
  const h = harness();
  let settled = false;
  const request = h.api.request("/slow").finally(() => { settled = true; });
  const rejected = assert.rejects(request, error => error.name === "TimeoutError");
  h.advance(120_000);
  await Promise.resolve();
  assert.equal(settled, false);
  h.advance(239_999);
  await Promise.resolve();
  assert.equal(settled, false);
  h.advance(1);
  await rejected;
  assert.equal(h.timers.size, 0);
});

test("explicit timeout remains milliseconds, and caller cancellation stays immediate", async () => {
  const h = harness();
  const request = h.api.request("/custom", { timeoutMs: 37 });
  const rejected = assert.rejects(request, error => error.name === "TimeoutError");
  h.advance(37);
  await rejected;
  const controller = new AbortController();
  const pending = h.api.request("/cancel", { signal: controller.signal });
  const cancelled = assert.rejects(pending, error => error.name === "AbortError");
  controller.abort();
  await cancelled;
  assert.equal(h.timers.size, 0);
});

test("zero preserves the no-timeout contract", async () => {
  const h = harness();
  const controller = new AbortController();
  const request = h.api.request("/unbounded", { timeoutMs: 0, signal: controller.signal });
  const rejected = assert.rejects(request, error => error.name === "AbortError");
  assert.equal(h.timers.size, 0);
  h.advance(1_000_000);
  controller.abort();
  await rejected;
});
