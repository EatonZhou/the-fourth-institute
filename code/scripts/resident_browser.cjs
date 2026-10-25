"use strict";

// Native Portal browser access with the runtime-issued Resident credential.
// Supply Playwright's chromium; no human profile, cookies, or TAU account.
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

async function openResidentPortal({ chromium, origin, residentId, lineageId, headless = true, python = "python3" }) {
  const url = new URL(origin);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) throw new Error("Supply one Portal HTTP(S) origin");
  if (!/^lineage_[0-9a-f]{12}$/.test(lineageId || "")) throw new Error("Supply the current Lineage ID");
  const codeRoot = path.resolve(__dirname, "..");
  // Only the current identity and its credential reference cross stdout.
  const identity = JSON.parse(execFileSync(python, ["-c", [
    "import json, sys",
    "from scripts.resident_hatg_instruction import _resident_id, TOKEN_ROOT",
    "actor = _resident_id(sys.argv[1])",
    "print(json.dumps({'resident_id': actor, 'token_path': str(TOKEN_ROOT / (actor + '.token'))}))",
  ].join("\n"), residentId || ""], { cwd: codeRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }));
  const token = fs.readFileSync(identity.token_path, "utf8").trim();
  if (!token) throw new Error("Current Resident has no Portal credential");
  const headers = { Authorization: `Bearer ${token}`, "X-Portal-Lineage-ID": lineageId };
  const browser = await chromium.launch({ headless });
  try {
    const context = await browser.newContext({ serviceWorkers: "block" });
    await context.route("**/*", async route => {
      const request = route.request();
      const target = new URL(request.url());
      if (target.origin !== url.origin) return route.abort("blockedbyclient");
      const authenticated = { ...request.headers(), ...headers };
      // This native stream only returns data or a same-origin login redirect.
      // Redirects bypass Playwright's next route callback, so reject them before
      // handing a response to Chromium. Callers must use a direct native URL.
      if (target.pathname === "/api/events" && request.method() === "GET") {
        return route.continue({ headers: authenticated });
      }
      try {
        const response = await route.fetch({ headers: authenticated, maxRedirects: 0 });
        if ([301, 302, 303, 307, 308].includes(response.status())) {
          await route.abort("blockedbyclient");
          return;
        }
        await route.fulfill({ response });
      } catch {
        await route.abort("failed").catch(() => {});
      }
    });
    async function verifyIdentity() {
      let response;
      try {
        response = await context.request.get(`${url.origin}/api/me`, { headers, maxRedirects: 0 });
      } catch {
        throw new Error("Portal identity request failed");
      }
      if (response.status() !== 200) throw new Error(`Portal identity rejected: HTTP ${response.status()}`);
      const me = await response.json();
      if (me.principal?.kind !== "resident" || me.principal.id !== identity.resident_id ||
          me.username !== identity.resident_id) throw new Error("Portal returned a different principal");
      return me;
    }
    const me = await verifyIdentity();
    const page = await context.newPage();
    return { browser, context, page, identity: me, verifyIdentity, close: () => browser.close() };
  } catch (error) {
    await browser.close();
    throw error;
  }
}

module.exports = { openResidentPortal };
