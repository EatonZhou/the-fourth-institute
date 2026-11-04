"use strict";
// Fixed loopback sample for the Preview toolbar's target=_blank attachment.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const cp = require("node:child_process");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "native-popup-"));
  const tokenPath = path.join(root, "fixture.token");
  fs.writeFileSync(tokenPath, "synthetic-popup-credential", { mode: 0o600 });
  const residentId = "resident_0123456789ab";
  const originalExec = cp.execFileSync;
  let denyGuard = false;
  let guardClosed;
  const guardClosure = new Promise(resolve => { guardClosed = resolve; });
  const NativeWebSocket = globalThis.WebSocket;
  let guardSocket;
  globalThis.WebSocket = class extends NativeWebSocket {
    constructor(...args) {
      super(...args);
      guardSocket = this;
      this.addEventListener("message", event => {
        if (denyGuard && JSON.parse(event.data).method === "Target.detachedFromTarget") guardClosed();
      });
    }
    send(value) {
      const command = JSON.parse(value);
      if (denyGuard && command.method === "Fetch.enable") {
        queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", {
          data: JSON.stringify({ id: command.id, error: { message: "fixture guard unavailable" } }),
        })));
      } else super.send(value);
    }
  };
  cp.execFileSync = () => JSON.stringify({ resident_id: residentId, token_path: tokenPath });
  const { openResidentPortal } = require("../../../scripts/resident_browser.cjs");
  cp.execFileSync = originalExec;
  const deniedRequests = [];
  let redirectTargets = 0;
  let fileRequests = 0;
  const native = http.createServer((req, res) => {
    if (req.headers.authorization !== "Bearer synthetic-popup-credential") {
      deniedRequests.push(req.url);
      res.writeHead(401); return res.end("not authenticated");
    }
    if (req.url === "/api/me") {
      res.setHeader("Content-Type", "application/json");
      return res.end(JSON.stringify({ username: residentId, principal: { kind: "resident", id: residentId } }));
    }
    if (req.url.startsWith("/api/agent-workbench/link-file-raw")) {
      fileRequests++;
      const status = new URL(req.url, "http://fixture").searchParams.get("status");
      if (status) {
        const foreign = new URL(req.url, "http://fixture").searchParams.has("foreign");
        res.writeHead(Number(status), { Location: foreign ? `http://localhost:${native.address().port}/redirect-target` : "/redirect-target" });
        return res.end();
      }
      res.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="toolbar.txt"' });
      return res.end("native toolbar bytes\n");
    }
    if (req.url === "/redirect-target") { redirectTargets++; return res.end("unexpected"); }
    res.setHeader("Content-Type", "text/html");
    res.end('<button onclick="const a=document.createElement(\'a\');a.href=window.downloadUrl||\'/api/agent-workbench/link-file-raw?download=1\';a.target=\'_blank\';a.rel=\'noopener noreferrer\';document.body.append(a);a.click();a.remove()">Download</button>');
  });
  await new Promise(resolve => native.listen(0, "127.0.0.1", resolve));
  let portal;
  try {
    portal = await openResidentPortal({ chromium, origin: `http://127.0.0.1:${native.address().port}`, residentId, lineageId: "lineage_0123456789ab" });
    const errors = [];
    portal.context.on("requestfailed", r => errors.push({ url: r.url(), failure: r.failure() }));
    const downloads = [];
    const observe = page => page.on("download", d => downloads.push(d));
    for (const page of portal.context.pages()) observe(page);
    portal.context.on("page", observe);
    await portal.page.goto(`http://127.0.0.1:${native.address().port}`);
    await portal.page.getByRole("button", { name: "Download", exact: true }).click();
    for (let i = 0; i < 100 && !downloads.length; i++) await new Promise(r => setTimeout(r, 50));
    assert.equal(downloads.length, 1, JSON.stringify({ errors, deniedRequests, pages: portal.context.pages().length }));
    try { await downloads[0].saveAs(path.join(root, "saved.txt")); }
    catch (error) { throw new Error(JSON.stringify({ error: error.message, errors, deniedRequests, pages: portal.context.pages().length })); }
    assert.equal(await downloads[0].failure(), null);
    assert.equal(fs.readFileSync(path.join(root, "saved.txt"), "utf8"), "native toolbar bytes\n");
    for (const status of [301, 302, 303, 307, 308]) {
      for (const foreign of [false, true]) {
      await portal.page.evaluate(({status, foreign}) => { window.downloadUrl = `/api/agent-workbench/link-file-raw?download=1&status=${status}${foreign ? "&foreign=1" : ""}`; }, {status, foreign});
      const opened = portal.context.waitForEvent("page", { timeout: 5000 });
      await portal.page.getByRole("button", { name: "Download", exact: true }).click();
      const popup = await opened;
      await popup.waitForLoadState("domcontentloaded");
      assert.equal(await popup.locator("body").innerText().then(text => text.includes("unexpected")), false);
      await popup.close();
      }
    }
    assert.equal(redirectTargets, 0, "popup redirects are stopped before following");
    assert.deepEqual(deniedRequests, [], "every native request retains the runtime identity");
    denyGuard = true;
    await portal.page.evaluate(() => { window.downloadUrl = "/api/agent-workbench/link-file-raw?download=1&unavailable=1"; });
    const before = fileRequests;
    await portal.page.getByRole("button", { name: "Download", exact: true }).click();
    let deadline;
    try { await Promise.race([guardClosure, new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error("guard did not close target")), 5000); })]); }
    finally { clearTimeout(deadline); }
    assert.equal(fileRequests, before, "guard attachment failure never releases credentials");
    denyGuard = false;
    await portal.page.evaluate(() => { window.downloadUrl = "/api/agent-workbench/link-file-raw?download=1&recovered=1"; });
    await portal.page.getByRole("button", { name: "Download", exact: true }).click();
    for (let i = 0; i < 100 && downloads.length < 2; i++) await new Promise(r => setTimeout(r, 20));
    assert.equal(downloads.length, 2);
    await downloads[1].saveAs(path.join(root, "recovered.txt"));
    assert.equal(fs.readFileSync(path.join(root, "recovered.txt"), "utf8"), "native toolbar bytes\n");
    assert.deepEqual(await portal.context.cookies(), []);
    const disconnected = portal.context.waitForEvent("close", { timeout: 5000 });
    guardSocket.close();
    await disconnected;
    console.log("PASS native Preview popup bytes/identity; five local/foreign redirects contained; guard failure/recovery; disconnect closes context; no Cookie");
  } finally {
    if (portal) await portal.close();
    globalThis.WebSocket = NativeWebSocket;
    native.closeAllConnections();
    await new Promise(resolve => native.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
