// Browser host: start only after the Python owner durably records its Job/session.
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const [evidenceDir, headlessFlag = '1', token, python, ownedProfile] = process.argv.slice(2);
const evidence = fs.realpathSync(evidenceDir);
const hostFile = path.join(evidence, 'tau-browser-host.json');
let server, profileRoot, stopping = false, started = false;

function write(payload) {
  const temporary = `${hostFile}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({ ...payload, token, host_pid: process.pid,
    evidence_dir: evidence, written_at: new Date().toISOString() }, null, 2));
  fs.renameSync(temporary, hostFile);
}

async function stop(reason) {
  if (stopping) return;
  stopping = true;
  try { if (server) await server.close(); }
  finally {
    if (profileRoot) fs.rmSync(profileRoot, { recursive: true, force: true });
    write({ state: 'closed', reason });
    process.exit(0);
  }
}
process.on('SIGTERM', () => { void stop('SIGTERM'); });
process.on('SIGINT', () => { void stop('SIGINT'); });
process.stdin.on('end', () => {
  if (!started) return process.exit(1);
  // On Windows the Job closes with its owner. On POSIX the pipe's EOF also
  // covers SIGKILL of that owner. The independent cleanup client verifies the
  // persisted session identity and delegates all signals to process_runtime.
  const child = spawn(python, [path.join(__dirname, 'tau_browser_owner.py'), 'stop-tree', evidence, token],
    { detached: true, stdio: 'ignore', windowsHide: true });
  child.on('error', error => write({ state: 'failed', error: `owner-exit cleanup: ${error.message}` }));
  child.unref();
  // Keep the leader alive until the ownership-checked client signals it.
});
process.stdin.once('data', async data => {
  if (String(data) !== 'start\n') return process.exit(1);
  started = true;
  try {
    // A Resident helper may have another HOME or browser cache. Use the
    // framework's verified runtime before Playwright resolves its executables.
    const contextFile = path.join(evidence, 'run-context.json');
    const context = fs.existsSync(contextFile) ? JSON.parse(fs.readFileSync(contextFile, 'utf8')) : {};
    const browsers = context.browser_runtime?.browsers_path;
    if (browsers != null) {
      if (typeof browsers !== 'string' || (browsers !== '0' && !path.isAbsolute(browsers))) {
        throw new Error('TAU browser_runtime.browsers_path must be an absolute path or 0');
      }
      process.env.PLAYWRIGHT_BROWSERS_PATH = browsers;
    }
    const { chromium } = require(path.join(__dirname, 'tau_runtime', 'node_modules', 'playwright'));
    profileRoot = fs.realpathSync(ownedProfile);
    // Node uses TEMP/TMP on Windows and TMPDIR on POSIX. Keep Playwright's
    // temporary profile below the directory owned by this browser lease.
    process.env.TMPDIR = process.env.TEMP = process.env.TMP = profileRoot;
    server = await chromium.launchServer({ headless: headlessFlag !== '0', args: [`--tau-evidence-dir=${evidence}`] });
    if (stopping) { await server.close(); return; }
    server.on('close', () => { void stop('browser closed'); });
    write({ state: 'running', ws_endpoint: server.wsEndpoint(), profile_root: profileRoot });
  } catch (error) {
    if (profileRoot) fs.rmSync(profileRoot, { recursive: true, force: true });
    write({ state: 'failed', error: String(error.message || error) });
    process.exit(1);
  }
});
