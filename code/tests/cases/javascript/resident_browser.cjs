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
  const native = http.createServer((request, response) => {
    sameOriginAuthenticated &&= request.headers.authorization === "Bearer synthetic-browser-fixture" &&
      request.headers["x-portal-lineage-id"] === "lineage_0123456789ab";
    if (request.url === "/api/me") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ principal: { kind: "resident", id: returnedResident }, username: returnedResident }));
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
    returnedResident = "resident_ffffffffffff";
    await assert.rejects(openResidentPortal(options), /different principal/);
    console.log("PASS native authentication, clean context, cross-origin and redirect containment, principal mismatch");
  } finally {
    if (portal) await portal.close();
    native.closeAllConnections();
    foreign.closeAllConnections();
    await Promise.all([new Promise(resolve => native.close(resolve)), new Promise(resolve => foreign.close(resolve))]);
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
