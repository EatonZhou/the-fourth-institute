"use strict";
// Synthetic credentials and isolated loopback HTTP only. Actual Chromium file
// downloads exercise bodies larger than the former fetch/fulfill failure.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const childProcess = require("node:child_process");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "resident-file-test-"));
  const tokenPath = path.join(directory, "fixture.token");
  const residentId = "resident_0123456789ab";
  fs.writeFileSync(tokenPath, "synthetic-native-file-fixture", { mode: 0o600 });
  const originalExec = childProcess.execFileSync;
  let openResidentPortal;
  try {
    childProcess.execFileSync = () => JSON.stringify({ resident_id: residentId, token_path: tokenPath });
    ({ openResidentPortal } = require("../../../scripts/resident_browser.cjs"));
  } finally { childProcess.execFileSync = originalExec; }
  const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const size = 326902668;
  const block = Buffer.alloc(256 * 1024, 0x61);
  let foreignRequests = 0;
  let redirectTargetRequests = 0;
  let authenticated = true;
  let slowClosed = false;
  let delayedRedirect;
  const foreign = http.createServer((_request, response) => { foreignRequests++; response.end("foreign"); });
  await listen(foreign);
  const foreignOrigin = `http://127.0.0.1:${foreign.address().port}`;
  const native = http.createServer((request, response) => {
    const valid = request.headers.authorization === "Bearer synthetic-native-file-fixture" &&
      request.headers["x-portal-lineage-id"] === "lineage_0123456789ab";
    authenticated &&= valid;
    if (!valid) { response.writeHead(401); response.end("denied"); return; }
    const url = new URL(request.url, "http://fixture");
    if (url.pathname === "/api/me") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ principal: { kind: "resident", id: residentId }, username: residentId }));
    } else if (url.pathname === "/redirect-target") {
      redirectTargetRequests++;
      response.end("unexpected redirect");
    } else if (["/api/resources/content", "/api/agent-workbench/link-file-raw"].includes(url.pathname)) {
      const mode = url.searchParams.get("mode");
      if (mode === "delayed-redirect") { delayedRedirect = response; return; }
      if (mode === "redirect" || mode === "local-redirect") {
        response.writeHead(Number(url.searchParams.get("status")), {
          Location: mode === "redirect" ? foreignOrigin + "/foreign" : "/redirect-target",
        });
        response.end(); return;
      }
      if (mode === "denied") {
        response.writeHead(403, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "resource denied" })); return;
      }
      if (mode === "range") {
        assert.equal(request.headers.range, "bytes=10-19");
        response.writeHead(206, { "Content-Type": "application/octet-stream", "Content-Range": "bytes 10-19/100" });
        response.end("0123456789"); return;
      }
      if (mode === "slow") {
        response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="cancel.bin"' });
        response.write(block);
        const timer = setInterval(() => response.write(block), 50);
        response.on("close", () => { clearInterval(timer); slowClosed = true; });
        return;
      }
      const bytes = mode === "small" ? 32771 : size;
      const filename = mode === "small" ? "复音-恢复.bin" : "large-native.bin";
      response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": bytes,
        "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(filename)}` });
      let sent = 0;
      function write() {
        while (sent < bytes && !response.destroyed) {
          const chunk = block.subarray(0, Math.min(block.length, bytes - sent));
          sent += chunk.length;
          if (!response.write(chunk)) { response.once("drain", write); return; }
        }
        if (!response.destroyed) response.end();
      }
      write();
    } else {
      response.setHeader("Content-Type", "text/html");
      response.end('<a id="large" href="/api/resources/content?mode=large&download=1">Large</a>' +
        '<a id="slow" href="/api/resources/content?mode=slow&download=1">Cancel</a>');
    }
  });
  await listen(native);
  const origin = `http://127.0.0.1:${native.address().port}`;
  let portal;
  try {
    portal = await openResidentPortal({ chromium, origin, residentId, lineageId: "lineage_0123456789ab" });
    const { page, context } = portal;
    await page.goto(origin);
    const largePromise = page.waitForEvent("download", { timeout: 30000 });
    await page.locator("#large").click();
    const large = await largePromise;
    const target = path.join(directory, "large.bin");
    await large.saveAs(target);
    assert.equal(await large.failure(), null);
    assert.equal(large.suggestedFilename(), "large-native.bin");
    assert.equal(fs.statSync(target).size, size);
    const expected = crypto.createHash("sha256");
    for (let offset = 0; offset < size; offset += block.length) expected.update(block.subarray(0, Math.min(block.length, size - offset)));
    const actual = crypto.createHash("sha256");
    for await (const chunk of fs.createReadStream(target)) actual.update(chunk);
    assert.equal(actual.digest("hex"), expected.digest("hex"));
    fs.unlinkSync(target);
    for (const status of [301, 302, 303, 307, 308]) {
      for (const mode of ["redirect", "local-redirect"]) {
        await assert.rejects(page.evaluate(url => fetch(url),
          origin + `/api/resources/content?mode=${mode}&status=${status}`));
      }
    }
    assert.equal(foreignRequests, 0, "no foreign redirect request, including with credentials");
    assert.equal(redirectTargetRequests, 0, "same-origin redirects also remain rejected");
    const denied = await page.evaluate(async () => {
      const response = await fetch("/api/resources/content?mode=denied");
      return { status: response.status, body: await response.json() };
    });
    assert.deepEqual(denied, { status: 403, body: { error: "resource denied" } });
    const range = await page.evaluate(async () => {
      const response = await fetch("/api/resources/content?mode=range", { headers: { Range: "bytes=10-19", Authorization: "forged" } });
      return { status: response.status, body: await response.text() };
    });
    assert.deepEqual(range, { status: 206, body: "0123456789" });
    // An ordinary same-origin iframe shares its parent's Chromium target. This
    // does not grant opaque workbench projections any new navigation capability.
    await page.evaluate(origin => {
      const iframe = document.createElement("iframe");
      iframe.sandbox = "allow-scripts allow-same-origin allow-downloads";
      iframe.srcdoc = `<a href="${origin}/api/agent-workbench/link-file-raw?mode=small&download=1">Inner file</a>`;
      document.body.append(iframe);
    }, origin);
    const inner = page.frameLocator("iframe").getByRole("link", { name: "Inner file", exact: true });
    const smallPromise = page.waitForEvent("download", { timeout: 10000 });
    await inner.click();
    const small = await smallPromise;
    await small.saveAs(path.join(directory, "small.bin"));
    assert.equal(small.suggestedFilename(), "复音-恢复.bin");
    assert.equal(fs.statSync(path.join(directory, "small.bin")).size, 32771);
    assert.equal(await small.failure(), null);
    const cancelPromise = page.waitForEvent("download", { timeout: 10000 });
    await page.locator("#slow").click();
    const cancelled = await cancelPromise;
    await cancelled.cancel();
    assert.equal(await cancelled.failure(), "canceled");
    for (let i = 0; i < 100 && !slowClosed; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(slowClosed, "cancelling the actual native transfer releases the upstream");
    await page.evaluate(() => {
      window.pendingFile = fetch("/api/resources/content?mode=delayed-redirect").catch(() => "cancelled");
    });
    for (let i = 0; i < 100 && !delayedRedirect; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(delayedRedirect);
    await page.goto(origin);
    delayedRedirect.writeHead(302, { Location: foreignOrigin + "/late-redirect" });
    delayedRedirect.end();
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(foreignRequests, 0, "navigation before headers must not expose a late redirect");
    const unavailable = await context.newPage();
    await unavailable.goto(origin);
    await unavailable.evaluate(origin => {
      const a = document.createElement("a"); a.textContent = "Recovery";
      a.href = origin + "/api/resources/content?mode=small"; document.body.append(a);
    }, origin);
    const recoveredPromise = unavailable.waitForEvent("download", { timeout: 10000 });
    await unavailable.getByRole("link", { name: "Recovery", exact: true }).click();
    const recovered = await recoveredPromise;
    await recovered.saveAs(path.join(directory, "recovered.bin"));
    assert.equal(fs.statSync(path.join(directory, "recovered.bin")).size, 32771);
    assert.equal(await recovered.failure(), null);
    await unavailable.close();
    await page.reload();
    assert.equal((await portal.verifyIdentity()).principal.id, residentId);
    assert.ok(authenticated, "all resource requests retain the actual runtime identity");
    assert.deepEqual(await context.cookies(), []);
    console.log("PASS native 326902668-byte hash/filename; same-origin iframe file; redirect containment including navigation; 403; Range; forged-header override; new-page file; cancel/reopen; same Resident/no Cookie");
  } finally {
    if (portal) await portal.close();
    native.closeAllConnections(); foreign.closeAllConnections();
    await Promise.all([new Promise(resolve => native.close(resolve)), new Promise(resolve => foreign.close(resolve))]);
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
