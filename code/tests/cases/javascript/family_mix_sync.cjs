'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {runScripts: 'outside-only'});
const w = dom.window;
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const empty = () => ({muted_member_ids: [], solo_member_id: ''});
let policy = empty(), save, snapshot, delayedRead;
const watchers = new Set(), errors = [];
let writes = 0;
w.PortalUI = {toast: error => errors.push(error)};
w.PortalResidentProjection = {
  subscribe: (_, fn) => { watchers.add(fn); queueMicrotask(() => fn({detail: {}})); return () => watchers.delete(fn); },
  invalidate: () => watchers.forEach(fn => fn({detail: {}})),
};
w.PortalInstructionClient = {execute: async (name, args) => {
  if (name === 'GetLineage') return {lineage: {function_blocks: [{id: 'family', family: {facade: 'facade', members: ['member']}}]}};
  if (name === 'GetFamilyChatPreferences') return delayedRead ? delayedRead.promise : structuredClone(policy);
  assert.equal(name, 'UpdateFamilyChatPreferences'); writes++;
  await save.promise;
  policy = {muted_member_ids: [], solo_member_id: args.enabled ? args.member_id : ''};
  return structuredClone(policy);
}};
w.PortalResidentProtocol = {loadSnapshot: () => snapshot.promise};
w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/chat_workspace_family_status.js'), 'utf8'));
const api = w.PortalChatWorkspaceFamilyStatus.createFamilyStatus({escapeHtml: String, footerTime: String, sessionRestartActive: () => false});
const family = () => ({members: [{resident_id: 'member', title: 'Member'}], chat_preferences: structuredClone(policy)});
const root = w.document.createElement('div'); root.innerHTML = '<span data-family-reply-controls data-family-reply-chain=\'["member"]\'></span>';
w.document.body.append(root);
const reply = api.createFamilyReplyControls('facade', root);
reply.update(family());
const own = w.document.createElement('div'); w.document.body.append(own);
const member = api.createFamilyMemberChatControls({resident_id: 'member', lineage_id: 'lineage', title: 'Member', family: {family_id: 'family'}}, own);
const button = host => host.querySelector('[data-family-mix="solo"]');
(async () => {
  await tick(); await tick();
  const original = button(root), ownOriginal = button(own), stale = family();
  // A read already in flight before the click must never overwrite the save.
  delayedRead = deferred(); w.PortalResidentProjection.invalidate(); await tick();
  save = deferred(); snapshot = deferred(); original.click(); await tick();
  assert.equal(button(root), original, 'pending save preserves the button DOM');
  assert.equal(button(own), ownOriginal, 'other views preserve their buttons too');
  ownOriginal.click(); assert.equal(writes, 1, 'pending writes are serialized across views');
  save.resolve(); await tick();
  assert.equal(button(root).getAttribute('aria-pressed'), 'true');
  assert.equal(button(own).getAttribute('aria-pressed'), 'true', 'all mounted controls share the saved state');
  reply.update(stale); await tick();
  assert.equal(button(root).getAttribute('aria-pressed'), 'true', 'old conversation projection cannot undo the save');
  delayedRead.resolve(empty()); delayedRead = null; await tick();
  assert.equal(button(own).getAttribute('aria-pressed'), 'true', 'pre-save read cannot undo the save');
  snapshot.resolve(); await tick();
  assert.equal(button(root).disabled, false);
  assert.equal(button(root), original);
  assert.equal(button(own), ownOriginal);
  // A rejected save keeps the last confirmed selection and allows retry.
  save = deferred(); snapshot = deferred(); original.click(); save.reject(new Error('save failed')); await tick();
  assert.equal(button(root).getAttribute('aria-pressed'), 'true');
  assert.equal(button(root).disabled, false);
  assert.deepEqual(errors, ['save failed']);
  save = deferred(); original.click(); save.resolve(); snapshot.resolve(); await tick();
  assert.equal(button(root).getAttribute('aria-pressed'), 'false');
  // Fresh external changes remain observable after our mutation has finished.
  policy = {muted_member_ids: [], solo_member_id: 'member'};
  w.PortalResidentProjection.invalidate(); await tick();
  assert.equal(button(root).getAttribute('aria-pressed'), 'true');
  reply.destroy(); member.destroy(); assert.equal(watchers.size, 0);
  dom.window.close(); console.log('PASS: stable M/S nodes, shared saves, stale reads, rollback, external refresh, cleanup');
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
