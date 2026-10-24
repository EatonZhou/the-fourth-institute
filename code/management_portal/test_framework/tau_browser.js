// Shared TAU browser bootstrap (decision_c953eab654cd).
//
// One Playwright browser per TAU case: the test-actor identity comes from the
// storage state through browser.newContext (launchPersistentContext ignores
// storageState), Playwright is resolved from TAU's optional locked runtime instead of the
// evidence directory, and the throwaway profile lives under a short temporary
// path because Chromium's profile sockets do not fit under long evidence paths.
// The browser runs in a host process (tau_browser_host.js) outside the helper:
// a helper that crashed and runs again reconnects to the same browser (the
// launch receipt says reused=true) instead of launching a second one. The host's
// and Chromium's command lines carry the evidence directory, so the framework's
// helper cleanup stops them when the case ends; close() stops them earlier.
//
//   const { openPortal } = require("<repo>/management_portal/test_framework/tau_browser.js");
//   const portal = await openPortal({ evidenceDir, storageState, origin });
//   try { await portal.page.goto("/"); ... } finally { await portal.close(); }
//
// Observing one message: `portal.inputState(residentId, messageId)` reads that
// message's own processing state (GetInputState). The returned input_state is
// already unwrapped: read input.state and input.message_id, not input.current
// or input.input_state.
//
// `portal.waitForInput(residentId, messageId, { states })` waits until it reaches
// one of `states`. Both are reads; use them before a Stop, and to check what a
// crashed helper already submitted instead of submitting it again.
//
// A tool that is running now: `portal.waitForRunningActivity({ timeoutMs })` waits until
// the page's live Chat card names a running command, native tool or MCP tool and
// returns that text with the time it was seen, so a Stop can follow the event itself
// instead of a fixed delay.
//
// `portal.action('static-step-name', async () => { ...operation and ready check... })`
// records the actual action, frame/document and observed requests together.
// Pass `{ page: otherPage }` for another tab. This observation scope does not
// prove request causality or explain cancellations; retain those limits.
// The callback receives that exact page; use its argument instead of a captured
// default page when the case operates multiple tabs.
// `portal.triggerPath(lineageId, triggerPathId, { offset, limit })` reads the
// original path with both required identities. It returns the entire GetTriggerPath
// result, including paginated steps/edges/executions; it never triggers work.
// `portal.sendThenStop({ send, stop })` accepts two exact, already discovered
// button locators. It prepares passive timing, then clicks Send and Stop without
// intervening helper reads. Call inside one portal.action. Original click timing
// and failures are retained in tau-send-stop.jsonl; this does not prove cancellation.

const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");
const { randomUUID } = require("crypto");
const { performance } = require("perf_hooks");
const { observeBrowser } = require("./tau_browser_observer");
const { safeError, safeTransport } = require("./tau_safe_error");
const { sendThenStop } = require('./tau_send_stop');

const { chromium } = require(path.join(__dirname, "tau_runtime", "node_modules", "playwright"));

const { testActor } = require('./site_compatibility.js');
const EXPECTED_ACTOR = testActor();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function readHost(evidence) {
  try {
    return JSON.parse(fs.readFileSync(path.join(evidence, "tau-browser-host.json"), "utf8"));
  } catch (_error) {
    return null;
  }
}

function ownerCommand(python, args) {
  return new Promise((resolve, reject) => {
    execFile(python, [path.join(__dirname, "tau_browser_owner.py"), ...args],
      { timeout: 30_000, maxBuffer: 1024 * 1024, windowsHide: true, encoding: "utf8" },
      (error, stdout, stderr) => {
        if (error) return reject(new Error(`browser ownership command failed: ${stderr || error.message}`));
        try { resolve(JSON.parse(stdout)); } catch (failure) { reject(failure); }
      });
  });
}

// The Python owner retains a process_runtime Job/session across helper exits.
async function hostedBrowser(evidence, headless, python) {
  let lease;
  try {
    lease = await ownerCommand(python, ["start", evidence, process.execPath, headless ? "1" : "0"]);
    const reused = lease.reused === true;
    const deadline = Date.now() + 60_000;
    for (;;) {
      const host = readHost(evidence);
      if (host?.token === lease.token && host.state === "running") {
        return { browser: await chromium.connect(host.ws_endpoint), host: { ...host, ...lease, python }, reused };
      }
      if (lease.state === "closed" || (host?.token === lease.token && host.state === "failed")) {
        throw new Error(`browser host failed: ${host?.error || lease.error || 'owner closed'}`);
      }
      if (Date.now() > deadline) throw new Error("browser host did not start");
      await sleep(100);
    }
  } catch (error) {
    // Startup failure and connection failure close only this evidence lease.
    // If creation did not return, use its durable intent; never kill a raw PID.
    if (!lease) {
      try { lease = JSON.parse(fs.readFileSync(path.join(evidence, 'tau-browser-lease.json'), 'utf8')); }
      catch (_) { /* No recorded process was created. */ }
    }
    if (lease) await stopHost({ ...lease, python });
    throw error;
  }
}

async function stopHost(host) {
  await ownerCommand(host.python, ["stop", host.evidence_dir, host.token]);
  return true;
}

function writeReceipt(evidenceDir, name, payload) {
  const target = path.join(evidenceDir, name);
  fs.writeFileSync(target, JSON.stringify({ ...payload, written_at: new Date().toISOString() }, null, 2));
  return target;
}

function instructionFailure(error) {
  return { ...safeError(error),
    code: /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/.test(error?.code || '') ? error.code : null,
    http_status: Number.isInteger(error?.status) && error.status >= 100 && error.status <= 599 ? error.status : null };
}

const repoPython = path.join(__dirname, "..", "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const defaultPython = fs.existsSync(repoPython) ? repoPython : (process.platform === "win32" ? "python" : "python3");

async function openPortal({ evidenceDir, storageState, origin, pythonExecutable, viewport = { width: 1440, height: 900 },
  headless = true, expectedActor = EXPECTED_ACTOR } = {}) {
  if (!evidenceDir || !fs.statSync(evidenceDir).isDirectory()) throw new Error("evidenceDir must be an existing directory");
  if (!storageState || !fs.statSync(storageState).isFile()) throw new Error("storageState must be the test-actor storage state file");
  if (!/^https?:\/\//.test(String(origin || ""))) throw new Error("origin must be the DEV entry URL");
  const evidence = fs.realpathSync(evidenceDir);
  const runContextPath = path.join(evidence, 'run-context.json');
  const runContext = fs.existsSync(runContextPath) ? JSON.parse(fs.readFileSync(runContextPath, 'utf8')) : {};
  const lifecycleId = runContext.resource_lifecycle_id || '';
  if (fs.existsSync(runContextPath) && !lifecycleId) {
    throw new Error('TAU run-context.json is missing resource_lifecycle_id; resume the case through the framework');
  }
  const frameworkPython = runContext.browser_runtime?.python_executable;
  if (frameworkPython != null && (typeof frameworkPython !== 'string' || !path.isAbsolute(frameworkPython))) {
    throw new Error('TAU browser_runtime.python_executable must be an absolute path');
  }
  const { browser, host, reused } = await hostedBrowser(evidence, headless, pythonExecutable ?? frameworkPython ?? defaultPython);
  let context;
  try {
    context = await browser.newContext({ storageState, baseURL: origin, viewport,
      extraHTTPHeaders: lifecycleId ? { 'X-Portal-Resource-Lifecycle': lifecycleId } : {} });
  } catch (error) { await stopHost(host); throw error; }
  const launch = { helper_pid: process.pid, host_pid: host.host_pid, reused, profile_root: host.profile_root, origin, evidence_dir: evidence };
  async function checkIdentity(source) {
    const started = performance.now();
    const receipt = { call_id: randomUUID(), source, helper_pid: process.pid,
      started_at: new Date().toISOString(), expected_actor: expectedActor, ok: false };
    try {
      const response = await safeTransport('identity', () => context.request.get("/api/me"));
      const me = response.ok() ? await response.json() : {};
      const actor = String(me.username || "");
      Object.assign(receipt, { actor, build_id: String(me.build_id || ""), me_status: response.status(),
        expected_build_id: String(runContext.build_id || ""), observed_at: new Date().toISOString() });
      if (source === 'authenticated_browser_launch') Object.assign(launch, receipt);
      if (!expectedActor || actor !== expectedActor) {
        throw new Error(`browser identity is ${actor || `HTTP ${response.status()}`}, expected ${expectedActor}`);
      }
      if (!receipt.build_id) throw new Error('Portal identity response has no build ID');
      // Record the authenticated generation before returning any page to the
      // caller. The original preflight identity remains immutable on resume.
      if (receipt.expected_build_id) {
        const current = JSON.parse(fs.readFileSync(runContextPath, 'utf8'));
        const observations = current.build_observations || [];
        if ((observations.at(-1)?.build_id || receipt.expected_build_id) !== receipt.build_id) {
          observations.push({ build_id: receipt.build_id, observed_at: receipt.observed_at,
            recorded_at: new Date().toISOString(), source });
          const temporary = path.join(evidence, `.run-context-browser-${process.pid}.tmp`);
          fs.writeFileSync(temporary, JSON.stringify({ ...current, build_observations: observations }, null, 2));
          fs.renameSync(temporary, runContextPath);
        }
      }
      receipt.ok = true;
      return receipt;
    } catch (error) {
      receipt.error = instructionFailure(error);
      throw error;
    } finally {
      fs.appendFileSync(path.join(evidence, 'tau-browser-identity.jsonl'), JSON.stringify({ ...receipt,
        finished_at: new Date().toISOString(), duration_ms: performance.now() - started }) + '\n');
    }
  }
  try {
    await checkIdentity('authenticated_browser_launch');
  } catch (error) {
    writeReceipt(evidence, "tau-browser-launch.json", { ...launch, ok: false, error: safeError(error) });
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await stopHost(host);
    throw error;
  }
  writeReceipt(evidence, "tau-browser-launch.json", { ...launch, ok: true });
  // Installed before the first document navigation. Observations do not change
  // request arguments, results, cancellation, retries or business verdicts.
  let observer;
  try { observer = await observeBrowser(context, { evidenceDir: evidence }); }
  catch (error) {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await stopHost(host);
    throw error;
  }
  let page;
  try { page = await context.newPage(); }
  catch (error) { observer.stop(); await stopHost(host); throw error; }

  // One HATG ISA call as the browser's test-actor session; a pending projection
  // or a call still running completes by repeating it with the same key.
  async function instruction(name, args = {}, { key = "", attempts = 30 } = {}) {
    const callId = randomUUID();
    const started = performance.now();
    const identity = { call_id: callId, tool: /^[A-Z][A-Za-z0-9]{0,79}$/.test(name) ? name : "instruction", helper_pid: process.pid };
    const append = record => fs.appendFileSync(path.join(evidence, 'tau-helper-calls.jsonl'),
      JSON.stringify({ ...identity, ...record }) + '\n');
    append({ kind: 'call_started', at: new Date().toISOString() });
    let status = 'failed';
    let failure;
    try {
      const result = await executeInstruction(name, args, { key, attempts, append });
      status = 'completed';
      return result;
    } catch (error) {
      failure = instructionFailure(error);
      throw error;
    } finally {
      append({ kind: 'call_finished', at: new Date().toISOString(), duration_ms: performance.now() - started, status,
        ...(failure ? { error: failure } : {}) });
    }
  }

  async function executeInstruction(name, args, { key, attempts, append }) {
    const call = { schema: "tfi.hatg-instruction.v2", instruction: name, arguments: args };
    if (key) call.idempotency_key = key;
    for (let attempt = 1; ; attempt += 1) {
      // APIRequestContext does not emit the page's request events. Preserve
      // each actual HTTP attempt separately from the idempotent ISA call.
      const requestId = randomUUID();
      const started = performance.now();
      const request = { attempt, request_id: requestId, transport: 'api_request_context',
        method: 'POST', path: '/api/instructions/execute' };
      append({ ...request, kind: 'request_started', at: new Date().toISOString() });
      let response;
      let body = {};
      let failure;
      try {
        response = await safeTransport('instruction', () => context.request.post("/api/instructions/execute", {
          data: call, headers: { Origin: origin, 'X-Portal-Request-ID': requestId } }));
        try { body = await response.json(); } catch (_error) { body = {}; }
      } catch (error) {
        failure = instructionFailure(error);
        throw error;
      } finally {
        append({ ...request, kind: 'request_finished', at: new Date().toISOString(),
          duration_ms: performance.now() - started, http_status: response?.status() ?? null,
          instruction_id: /^inst_[a-zA-Z0-9_]{1,100}$/.test(body.instruction_id || '') ? body.instruction_id : null,
          instruction_status: ['committed', 'failed', 'running'].includes(body.status) ? body.status : null,
          replayed: typeof body.replayed === 'boolean' ? body.replayed : null,
          ...(failure ? { error: failure } : {}) });
      }
      if (response.ok() && body.status === "committed") return body.result || {};
      const error = body.error || {};
      if (key && ["instruction.projection_pending", "instruction.in_progress"].includes(error.code) && attempt < attempts) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        continue;
      }
      throw Object.assign(new Error(`${name} failed: ${error.code || response.status()} ${error.message || ""}`.trim()),
        { code: error.code || "", details: error.details || {}, status: response.status() });
    }
  }

  async function inputState(residentId, messageId) {
    return (await instruction("GetInputState", { resident_id: residentId, message_id: messageId })).input_state || {};
  }

  async function triggerPath(lineageId, triggerPathId, { offset, limit, artifact_activation_key } = {}) {
    if (typeof lineageId !== 'string' || !lineageId.trim() ||
        typeof triggerPathId !== 'string' || !triggerPathId.trim()) {
      throw new TypeError('triggerPath requires both lineageId and triggerPathId');
    }
    const args = { lineage_id: lineageId, trigger_path_id: triggerPathId };
    if (offset !== undefined) args.offset = offset;
    if (limit !== undefined) args.limit = limit;
    if (artifact_activation_key !== undefined) args.artifact_activation_key = artifact_activation_key;
    const result = await instruction('GetTriggerPath', args);
    if (result.path?.trigger_path_id !== triggerPathId) {
      throw new Error('GetTriggerPath returned a different or missing path identity');
    }
    return result;
  }

  async function waitForInput(residentId, messageId, { states = ["completed", "failed", "cancelled"], timeoutMs = 600_000, intervalMs = 1000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const current = await inputState(residentId, messageId);
      if (states.includes(String(current.state || ""))) return current;
      if (Date.now() > deadline) {
        throw Object.assign(new Error(`${messageId} stayed ${current.state || "unknown"}; expected ${states.join("/")}`), { inputState: current });
      }
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
  }

  async function waitForRunningActivity({ scope = page, timeoutMs = 120_000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    // Locators resolve again after navigation or a Chat remount. A caller with
    // multiple Chats must identify its own visible panel, including after reload.
    const runtime = scope.locator('[data-message-key="__runtime__"]:visible');
    const targetPage = runtime.page();
    while (Date.now() < deadline) {
      if (targetPage.isClosed()) throw new Error("Chat page closed while waiting for a running tool");
      try {
        if (await runtime.count() > 1) throw new Error("Multiple live Chat cards; pass the target Chat as scope");
        const toolTitle = /^(?:Command (?:started|progress)(?:\s|$)|(?:Tool(?: progress)?|MCP):)/;
        const summary = runtime.locator('summary:visible')
          .filter({ hasText: /(?:^| · )(?:Command (?:started|progress)(?:\s|$)|(?:Tool(?: progress)?|MCP):)/ });
        const item = runtime.locator('.runtimeDetailItem:visible')
          .filter({ has: targetPage.locator('.runtimeDetailKind').filter({ hasText: toolTitle }) })
          .filter({ has: targetPage.locator('.runtimeDetailMeta').filter({ hasText: /^running(?:\s|$)/ }) });
        const visibleTool = await summary.count() ? summary.first() : item.first();
        if (await visibleTool.isVisible()) {
          const seen = await visibleTool.evaluate(node => ({ text: node.innerText.trim(), seen_at_ms: Date.now() }));
          return { ...seen, seen_at: new Date(seen.seen_at_ms).toISOString() };
        }
        const collapsed = runtime.locator('details:not([open]) > summary:visible').first();
        if (await collapsed.isVisible()) await collapsed.click({ timeout: Math.min(250, Math.max(1, deadline - Date.now())) });
      } catch (error) {
        // UI reads/clicks can race a document replacement; business instructions
        // are never retried here. Other failures remain visible to the caller.
        if (error.name !== "TimeoutError" && !/Execution context was destroyed|Cannot find context|not attached to the DOM/.test(error.message)) throw error;
      }
      await sleep(Math.min(100, Math.max(0, deadline - Date.now())));
    }
    throw new Error(`No visible running tool in the target Chat within ${timeoutMs}ms`);
  }

  let closed = false;
  async function close() {
    if (closed) return;
    closed = true;
    let error = "";
    try { await observer.flush(); }
    catch (exc) { error = String(exc.name || "ObservationError"); }
    // Keep network listeners active through context.close: its real cancellation
    // events are evidence. Finally mark any still-live requests as pending.
    try { await context.close(); }
    catch (exc) { error ||= String(exc.name || "ContextCloseError"); }
    finally {
      try { await observer.flush(); }
      catch (exc) { error ||= String(exc.name || "ObservationError"); }
      finally {
        try { observer.stop(); }
        catch (exc) { error ||= String(exc.name || "ObservationError"); }
      }
    }
    try { await browser.close(); }
    catch (exc) { error ||= String(exc.name || "BrowserCloseError"); }
    const stopped = await stopHost(host);
    if (!stopped && !error) error = `browser host ${host.host_pid} did not stop`;
    writeReceipt(evidence, "tau-browser-close.json", { helper_pid: process.pid, host_pid: host.host_pid, ok: !error, error });
  }
  return { browser, context, page, actor: launch.actor, buildId: launch.build_id,
    verifyIdentity: () => checkIdentity('explicit_recovery'),
    action: (name, operation, { page: target = page } = {}) => observer.action(target, name, operation),
    requestId: request => observer.requestId(request), instruction, inputState, triggerPath, waitForInput, waitForRunningActivity,
    sendThenStop: options => sendThenStop({ ...options, evidenceDir: evidence }), close };
}

module.exports = { EXPECTED_ACTOR, openPortal, safeError };
