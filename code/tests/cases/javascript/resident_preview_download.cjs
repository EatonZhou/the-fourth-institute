"use strict";
// Fixed native Chromium sample: actual shared Preview controls, a tiny attachment,
// and the unchanged Resident browser owner. No product account or live data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const childProcess = require("node:child_process");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");
const staticRoot = path.resolve(__dirname, "../../../management_portal/static");
const scripts = ["portal_preview_contract", "portal_preview_runtime", "portal_preview_renderers"];
const body = Buffer.from("PK-small-native-download-fixture");
const residentId = "resident_0123456789ab";
const html = `<!doctype html><a id="toolbar">Download</a><div id="preview"></div>
<script>
const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
window.PortalI18n = { t: text => text, html: template, text: template };
window.PortalUIPrimitives = { escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;') };
window.PortalPdfPreview = {};
</script>${scripts.map(name => '<script src="/static/' + name + '.js"></script>').join('')}`;

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "resident-preview-download-"));
  const tokenPath = path.join(directory, "fixture.token");
  fs.writeFileSync(tokenPath, "synthetic-preview-download", { mode: 0o600 });
  const originalExec = childProcess.execFileSync;
  let openResidentPortal;
  try {
    childProcess.execFileSync = () => JSON.stringify({ resident_id: residentId, token_path: tokenPath });
    ({ openResidentPortal } = require("../../../scripts/resident_browser.cjs"));
  } finally { childProcess.execFileSync = originalExec; }
  const downloads = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://fixture");
    const actor = request.headers.authorization === "Bearer synthetic-preview-download" &&
      request.headers["x-portal-lineage-id"] === "lineage_0123456789ab" ? "resident" :
      request.headers.cookie === "fixture_user=ordinary" ? "user" : "anonymous";
    if (url.pathname === "/api/me") {
      response.setHeader("Content-Type", "application/json");
      return response.end(JSON.stringify({ username: residentId, principal: { kind: "resident", id: residentId } }));
    }
    if (["/api/resources/content", "/api/agent-workbench/link-file-raw"].includes(url.pathname)) {
      downloads.push({ actor, path: url.pathname });
      if (actor === "anonymous") {
        response.writeHead(401, { "Content-Type": "application/json" });
        return response.end('{"error":"authentication required"}');
      }
      assert.equal(url.searchParams.get("download"), "1");
      response.writeHead(200, { "Content-Type": "application/zip", "Content-Length": body.length,
        "Content-Disposition": "attachment; filename=fixture.zip; filename*=UTF-8''" + encodeURIComponent("鼓与吉他.zip") });
      return response.end(body);
    }
    const script = scripts.find(name => url.pathname === `/static/${name}.js`);
    response.setHeader("Content-Type", script ? "text/javascript" : "text/html");
    response.end(script ? fs.readFileSync(path.join(staticRoot, script + ".js")) : html);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let portal, human;
  try {
    portal = await openResidentPortal({ chromium, origin, residentId, lineageId: "lineage_0123456789ab" });
    human = await chromium.launch({ headless: true });
    const humanContext = await human.newContext();
    await humanContext.addCookies([{ name: "fixture_user", value: "ordinary", url: origin }]);
    for (const [actor, page] of [["resident", portal.page], ["user", await humanContext.newPage()]]) {
      await page.goto(origin);
      await page.evaluate(async () => {
        window.PortalPreview.configureDownloadLink(document.querySelector('#toolbar'), {
          url: '/api/resources/content?address=workspace_0123456789ab/file/fixture.zip&download=1', filename: 'client-name.zip' });
        await window.PortalPreview.render({ root: '#preview', document: { path: 'fixture.zip', kind: 'binary',
          downloadUrl: '/api/agent-workbench/link-file-raw?path=fixture.zip&download=1' } }).promise;
      });
      for (const selector of ["#toolbar", "#preview a"]) {
        const pending = page.waitForEvent("download", { timeout: 10000 });
        await page.locator(selector).click();
        const download = await pending;
        assert.equal(await download.failure(), null, `${actor} ${selector} must finish the native attachment`);
        assert.equal(download.suggestedFilename(), "鼓与吉他.zip");
        assert.deepEqual(fs.readFileSync(await download.path()), body);
        assert.equal(page.url(), origin + "/", "the source page must remain open");
      }
      assert.equal(await page.evaluate(() => {
        const a = document.createElement('a');
        window.PortalPreview.configureDownloadLink(a, { url: 'blob:' + location.origin + '/fixture', filename: 'generated.zip' });
        return a.getAttribute('download');
      }), "generated.zip", "client generated blobs retain their filename");
    }
    assert.deepEqual(downloads.map(row => row.actor), ["resident", "resident", "user", "user"]);
    assert.deepEqual(await portal.context.cookies(), []);
    console.log("PASS real shared toolbar/fallback downloads: Resident + User, bytes, server filename, source page, blob filename");
  } finally {
    if (portal) await portal.close();
    if (human) await human.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
