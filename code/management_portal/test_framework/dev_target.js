// An explicit instance target; neither the worktree name nor an old registry selects a site.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');

function targetFile() {
  const instance = (process.env.PORTAL_INSTANCE || 'dev').replace(/[^a-zA-Z0-9_.-]+/g, '-') || 'dev';
  const state = process.env.PORTAL_TEST_FRAMEWORK_STATE_ROOT ||
    path.join(os.homedir(), '.local/state/tfi-portal/test-framework', instance);
  return process.env.PORTAL_TEST_TARGET_FILE || path.join(state, 'target.json');
}
function resolveTestTarget() {
  const payload = JSON.parse(fs.readFileSync(targetFile(), 'utf8'));
  if (payload.schema !== 'tfi.portal-test-target.v1') throw new Error('Invalid TAU target schema');
  const item = payload.deployment;
  if (!item || !['dev', 'release', 'exploration'].includes(String(item.kind || '').trim().toLowerCase()) ||
      !String(item.label || '').trim() || String(item.label).trim().length > 80) {
    throw new Error('TAU target needs a supported instance kind and label');
  }
  if (!item || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(String(item.id || '').trim().toLowerCase()) || !String(item.build_id || '').trim()) {
    throw new Error('TAU target needs an instance ID and expected build ID');
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
  return { instance: String(item.id).trim().toLowerCase(), origin: url.origin, browserOrigin: browser.origin,
    expectedBuildId: String(item.build_id).trim().slice(0, 240) };
}
function resolveDevOrigin() { return resolveTestTarget().origin; }
module.exports = { targetFile, resolveTestTarget, resolveDevTarget: resolveTestTarget, resolveDevOrigin };
