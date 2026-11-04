"use strict";

// Real Chromium exercises redirect and cross-origin containment with a synthetic
// credential. Only the runtime credential locator is substituted.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const childProcess = require("node:child_process");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "resident-browser-test-"));
  const tokenPath = path.join(directory, "fixture.token");
  const residentId = "resident_0123456789ab";
  fs.writeFileSync(tokenPath, "synthetic-browser-fixture", { mode: 0o600 });
  const originalExec = childProcess.execFileSync;
  let openResidentPortal;
  try {
    childProcess.execFileSync = () => JSON.stringify({ resident_id: residentId, token_path: tokenPath });
    ({ openResidentPortal } = require("../../../scripts/resident_browser.cjs"));
  } finally { childProcess.execFileSync = originalExec; }
  const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let foreignRequests = 0;
  const foreign = http.createServer((_request, response) => { foreignRequests++; response.end("foreign"); });
  await listen(foreign);
  const foreignOrigin = `http://127.0.0.1:${foreign.address().port}`;
  let sameOriginAuthenticated = true;
  let returnedResident = residentId;
  let slowCalls = 0;
  let streamCalls = 0;
  let streamClosed = 0;
  const native = http.createServer((request, response) => {
    sameOriginAuthenticated &&= request.headers.authorization === "Bearer synthetic-browser-fixture" &&
      request.headers["x-portal-lineage-id"] === "lineage_0123456789ab";
    if (request.url === "/api/me") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ principal: { kind: "resident", id: returnedResident }, username: returnedResident }));
    } else if (request.url === "/api/instructions/execute") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        const call = JSON.parse(body);
        if (call.instruction === "RedirectWatch") {
          response.writeHead(302, { Location: foreignOrigin + "/stream-redirect" });
          response.end(); return;
        }
        if (call.instruction === "DeniedWatch") {
          response.writeHead(403, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ error: "denied" })); return;
        }
        if (call.instruction === "SilentWatch") {
          response.on("close", () => { streamClosed++; });
          return;
        }
        streamCalls++;
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        response.write('event: batch\ndata: {"items":[{"cursor":1}]}\n\n');
        const timer = setTimeout(() => response.write('event: batch\ndata: {"items":[{"cursor":2}]}\n\n'), 31000);
        response.on("close", () => { clearTimeout(timer); streamClosed++; });
      });
    } else if (request.url === "/slow-native-operation") {
      slowCalls++;
      setTimeout(() => {
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ completed: true }));
      }, 31000);
    } else if (request.url === "/redirect") {
      response.writeHead(302, { Location: foreignOrigin + "/redirect-target" });
      response.end();
    } else {
      response.setHeader("Content-Type", "text/html");
      response.end("<!doctype html><title>Native fixture</title>");
    }
  });
  await listen(native);
  const options = { chromium, origin: `http://127.0.0.1:${native.address().port}`,
    residentId, lineageId: "lineage_0123456789ab" };
  let portal;
  try {
    portal = await openResidentPortal(options);
    await portal.page.goto(options.origin);
    assert.equal(await portal.page.evaluate(url => fetch(url).then(() => "sent", () => "blocked"), foreignOrigin), "blocked");
    await assert.rejects(portal.page.goto(options.origin + "/redirect"));
    assert.equal(foreignRequests, 0, "foreign origin must never receive the request");
    assert.ok(sameOriginAuthenticated, "native requests keep the authenticated principal");
    assert.deepEqual(await portal.context.cookies(), []);
    const operationPage = await portal.context.newPage();
    await operationPage.goto(options.origin);
    const first = await operationPage.evaluate(async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2000);
      try {
        const response = await fetch("/api/instructions/execute", { method: "POST", headers: { Accept: "text/event-stream" },
          body: JSON.stringify({ instruction: "WatchConversationEvents" }), signal: controller.signal });
        const reader = response.body.getReader();
        window.subscription = { controller, reader };
        return new TextDecoder().decode((await reader.read()).value);
      } finally { clearTimeout(timer); }
    });
    assert.match(first, /"cursor":1/, "the first watch batch arrives while the real subscription remains open");
    const slow = await operationPage.evaluate(async () => {
      try {
        const response = await fetch("/slow-native-operation", { method: "POST" });
        return await response.json();
      } catch { return { failed: true }; }
    });
    assert.deepEqual(slow, { completed: true }, "finite native operations may exceed Playwright's implicit 30s fetch timeout");
    assert.equal(slowCalls, 1, "transport must not replay a potentially committed POST");
    const second = await operationPage.evaluate(async () => {
      const result = await window.subscription.reader.read();
      window.subscription.controller.abort();
      return new TextDecoder().decode(result.value);
    });
    assert.match(second, /"cursor":2/, "watch survives the former 30s whole-response timeout");
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(streamClosed, 1, "abort must release the upstream subscription");
    assert.equal(streamCalls, 1, "helper must not resubscribe on its own");
    const denied = await operationPage.evaluate(async () => {
      const response = await fetch("/api/instructions/execute", { method: "POST", headers: { Accept: "text/event-stream" },
        body: JSON.stringify({ instruction: "DeniedWatch" }) });
      return { status: response.status, body: await response.json() };
    });
    assert.deepEqual(denied, { status: 403, body: { error: "denied" } });
    await assert.rejects(operationPage.evaluate(() => fetch("/api/instructions/execute", {
      method: "POST", headers: { Accept: "text/event-stream" }, body: JSON.stringify({ instruction: "RedirectWatch" }),
    })));
    assert.equal(foreignRequests, 0, "stream redirects must not receive a credential or request");
    await assert.rejects(operationPage.evaluate(foreignUrl => window.__portalNativeStreamOpen({
      id: crypto.randomUUID(), url: foreignUrl, method: "POST", headers: { accept: "text/event-stream" }, body: "{}",
    }), foreignOrigin + "/api/instructions/execute"));
    assert.equal(foreignRequests, 0);
    const earlyAbort = await operationPage.evaluate(async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 100);
      try {
        await fetch("/api/instructions/execute", { method: "POST", headers: { Accept: "text/event-stream" },
          body: JSON.stringify({ instruction: "SilentWatch" }), signal: controller.signal });
        return "unexpected response";
      } catch (error) { return error.name; }
      finally { clearTimeout(timer); }
    });
    assert.equal(earlyAbort, "AbortError", "cancel before response headers must release the request");
    await operationPage.evaluate(async () => {
      const response = await fetch("/api/instructions/execute", { method: "POST", headers: { Accept: "text/event-stream" },
        body: JSON.stringify({ instruction: "WatchConversationEvents" }) });
      await response.body.getReader().read();
    });
    await operationPage.goto(options.origin + "/reopened");
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(portal.streamState().active, 0, "same-frame navigation must release the old document's subscriptions");
    assert.equal(portal.streamState().opened, portal.streamState().closed);
    assert.ok(portal.streamState().receivedBytes > 0);
    returnedResident = "resident_ffffffffffff";
    await assert.rejects(openResidentPortal(options), /different principal/);
    console.log("PASS authentication; first/live stream batches; cancel/navigation cleanup; slow POST once; deny/redirect/cross-origin containment; principal mismatch");
  } finally {
    if (portal) await portal.close();
    native.closeAllConnections();
    foreign.closeAllConnections();
    await Promise.all([new Promise(resolve => native.close(resolve)), new Promise(resolve => foreign.close(resolve))]);
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
