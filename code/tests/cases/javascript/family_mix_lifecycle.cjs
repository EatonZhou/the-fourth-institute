'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(process.env.FAMILY_STATUS_SOURCE || path.resolve(__dirname, '../../../management_portal/static/chat_workspace_family_status.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const metrics = [];

async function scenario({survivor = false, remount = false} = {}) {
  const dom = new JSDOM('<main></main>', {runScripts: 'outside-only'});
  const w = dom.window, watchers = new Set(), errors = [];
  const counts = {reads: 0, writes: 0, snapshots: 0, invalidations: 0};
  let policy = {muted_member_ids: [], solo_member_id: ''};
  const saved = deferred();
  w.PortalUI = {toast: message => errors.push(message)};
  w.PortalResidentProjection = {
    subscribe: (_, callback) => { watchers.add(callback); return () => watchers.delete(callback); },
    invalidate: () => { counts.invalidations++; for (const callback of watchers) callback({detail: {}}); },
  };
  w.PortalInstructionClient = {execute: async name => {
    if (name === 'GetFamilyChatPreferences') { counts.reads++; return structuredClone(policy); }
    assert.equal(name, 'UpdateFamilyChatPreferences');
    counts.writes++;
    await saved.promise;
    policy = {...policy, solo_member_id: 'member'};
    return structuredClone(policy);
  }};
  w.PortalResidentProtocol = {loadSnapshot: async () => { counts.snapshots++; }};
  w.eval(source);
  const api = w.PortalChatWorkspaceFamilyStatus.createFamilyStatus({escapeHtml: String, footerTime: String, sessionRestartActive: () => false});
  const mount = () => {
    const root = w.document.createElement('div');
    root.innerHTML = '<span data-family-reply-controls data-family-reply-chain=\'["member"]\'></span>';
    w.document.body.append(root);
    const controls = api.createFamilyReplyControls('facade', root);
    controls.update({members: [{resident_id: 'member', title: 'Member'}], chat_preferences: policy});
    return {root, controls};
  };
  try {
    const first = mount(), second = survivor ? mount() : null;
    await tick();
    assert.equal(counts.reads, 1, 'initial preference reads are shared across mounts');
    first.root.querySelector('[data-family-mix="solo"]').click();
    await tick();
    for (const callback of watchers) callback({detail: {}}); // Refresh queued while save is pending.
    first.controls.destroy();
    first.root.remove();
    const replacement = remount ? mount() : null;
    await tick();
    const before = {...counts};
    saved.resolve();
    await tick(); await tick();
    const delta = Object.fromEntries(Object.keys(counts).map(key => [key, counts[key] - before[key]]));
    metrics.push({scenario: survivor ? 'one-view-survives' : remount ? 'destroy-and-remount' : 'last-view-destroyed', before, after: {...counts}, post_save: delta});
    assert.equal(counts.writes, 1, 'the already submitted save must still commit exactly once');
    if (survivor) {
      assert.equal(delta.snapshots, 1, 'a surviving view retains the authoritative post-save snapshot');
      assert.equal(delta.reads, 1, 'queued refreshes merge into one preference read');
      assert.equal(second.root.querySelector('[data-family-mix="solo"]').getAttribute('aria-pressed'), 'true');
    } else {
      assert.equal(delta.snapshots, 0, 'destroyed owner must not force a post-save snapshot');
      assert.equal(delta.reads, remount ? 1 : 0, 'only a newly mounted owner may refresh preferences');
      assert.equal(delta.invalidations, 1, 'successful save must still invalidate cached data for future views');
    }
    if (replacement) {
      assert.equal(replacement.root.querySelector('[data-family-mix="solo"]').getAttribute('aria-pressed'), 'true', 'replacement observes the committed policy');
      // A late release from the old view cannot remove the new shared owner.
      first.controls.destroy();
      const peer = mount(); await tick();
      assert.equal(watchers.size, 1, 'remount retains one shared subscription');
      peer.controls.destroy(); replacement.controls.destroy();
    }
    second?.controls.destroy();
    assert.equal(watchers.size, 0);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
}

(async () => {
  // Run the live-view control first so the red evidence also verifies normal behavior.
  await scenario({survivor: true});
  await scenario();
  await scenario({remount: true});
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  if (process.env.FAMILY_LIFECYCLE_METRICS) fs.writeFileSync(process.env.FAMILY_LIFECYCLE_METRICS, JSON.stringify(metrics, null, 2));
  console.log(JSON.stringify(metrics));
});
