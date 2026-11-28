// An explicit deployment target; neither the worktree name nor an old registry selects a site.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const {execFileSync} = require('node:child_process');

function targetFile() {
  if (process.env.PORTAL_TEST_TARGET_FILE) return process.env.PORTAL_TEST_TARGET_FILE;
  const root = process.env.PORTAL_INSTANCE_ROOT;
  if (!root || !path.isAbsolute(root)) throw new Error('Select an instance with PORTAL_INSTANCE_ROOT');
  const deployment = (process.env.PORTAL_DEPLOYMENT || 'dev').replace(/[^a-zA-Z0-9_.-]+/g, '-') || 'dev';
  return path.join(root, 'runtime/test-framework', deployment, 'target.json');
}
function resolveTestTarget() {
  const payload = JSON.parse(fs.readFileSync(targetFile(), 'utf8'));
  if (payload.schema !== 'tfi.portal-test-target.v1') throw new Error('Invalid TAU target schema');
  const item = payload.deployment;
  if (!item || !['dev', 'release', 'exploration'].includes(String(item.kind || '').trim().toLowerCase()) ||
      !String(item.label || '').trim() || String(item.label).trim().length > 80) {
    throw new Error('TAU target needs a supported deployment kind and label');
  }
  if (!item || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(String(item.id || '').trim().toLowerCase()) || !String(item.build_id || '').trim()) {
    throw new Error('TAU target needs a deployment ID and expected build ID');
  }
  const url = new URL(item.origin);
  if (url.protocol !== 'http:' || url.username || url.password || url.search || url.hash ||
      url.pathname !== '/' || !(url.hostname === '[::1]' || (net.isIP(url.hostname) === 4 && /^127\./.test(url.hostname)))) {
    throw new Error('TAU backend must be a loopback HTTP origin');
  }
  const browser = new URL(item.browser_origin || item.origin);
  if (browser.origin !== url.origin && (browser.protocol !== 'http:' || browser.hostname !== 'localhost' || browser.port !== url.port)) {
    throw new Error('TAU browser origin must be localhost on the backend port');
  }
  if (browser.username || browser.password || browser.search || browser.hash || browser.pathname !== '/') {
    throw new Error('Invalid TAU browser origin');
  }
  let expectedBuildId=String(item.build_id).trim().slice(0,240);
  if(item.kind==='dev' && expectedBuildId==='development'){
    const commit=execFileSync('git',['rev-parse','HEAD'],{cwd:path.resolve(__dirname,'../../..'),encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
    if(!/^[0-9a-f]{40,64}$/.test(commit))throw new Error('Cannot verify selected Dev source identity');
    expectedBuildId='dev-'+commit.slice(0,12);
  }
  return { deployment: String(item.id).trim().toLowerCase(), origin: url.origin, browserOrigin: browser.origin, expectedBuildId };
}
function resolveDevOrigin() { return resolveTestTarget().origin; }
module.exports = { targetFile, resolveTestTarget, resolveDevTarget: resolveTestTarget, resolveDevOrigin };
