'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

module.exports = async ({ page, repo, output }) => {
  const replies = [], results = [];
  await page.route('**/api/me', route => { replies.push(route); });
  const setup = async () => {
    await page.reload();
    await page.evaluate(() => {
      window.PortalAPI = { get: async url => {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Identity failed');
        return response.json();
      } };
      window.calls = []; window.rid = 'resident_111111111111'; window.initialEvents = 0;
      window.addEventListener('portal:identity-changed', () => initialEvents++);
      window.PortalResidentService = {
        snapshot: async (id, options) => {
          calls.push({ id, ...options });
          return { resident: { resident_id: id }, cursor: 1, state: { work_state: 'idle' },
            controls: { can_send: true }, queue: { items: [] }, activities: [], input_states: [], next_page_token: '',
            messages: [{ message_id: 'initial-message', role: 'user', content_parts: [{ type: 'text', text: '首次身份就绪后正常加载' }] }] };
        },
        inputHistory: async () => ({ entries: [] }), runtimeSettings: async () => ({ settings: {} }), runtimeCatalog: async () => ({})
      };
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'initialIdentity', autoLoad: false,
        target: { resident_id: rid }, historyLimit: 2 });
      panel.residentProjection = { resident: { resident_id: rid }, chat_workspace: { schema: 'portal.chat_workspace.v3',
        target: { kind: 'resident', id: rid }, composer: { can_send: true } } };
      window.acquire = () => PortalResidentProtocol.acquire(rid, update => {
        if (update.snapshot) panel.applySnapshot(update.snapshot, update);
      }, { load: false });
      window.outcome = promise => promise.then(snapshot => ({ status: 'resolved', messages: snapshot.messages.map(m => m.message_id) }),
        error => ({ status: 'rejected', error: error.name, message: error.message }));
    });
    // Real Identity.load -> /api/me -> remember -> identity-changed, not a
    // manually synthesized event or an already-resolved Identity mock.
    await page.addScriptTag({ path: path.join(repo, 'management_portal/static/portal_identity.js') });
    await page.evaluate(() => { panel.protocol = acquire(); window.first = outcome(panel.protocol.load({ historyLimit: 2 })); });
    // Later cases deliberately retain the session-cached username. Identity
    // still performs its first /api/me read and emits the same initial event.
    for (let attempt = 0; !replies.length && attempt < 200; attempt++) await new Promise(resolve => setTimeout(resolve, 5));
    assert(replies.length, 'Identity did not request /api/me');
  };
  const finishIdentity = async () => replies.shift().fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ username: 'initial-reader' }) });
  await setup();
  await finishIdentity();
  const initial = await page.evaluate(async () => ({ ...(await first), calls, initialEvents,
    snapshot_available: Boolean(panel.protocol.snapshot()), current: panel.protocol.snapshot()?.messages.map(m => m.message_id) }));
  results.push({ scenario: 'initial /api/me emits identity change while public load awaits it', ...initial });
  if (process.env.HISTORY_WINDOW_IDENTITY_BASELINE) {
    assert.equal(initial.status, 'rejected'); assert.equal(initial.error, 'AbortError');
    assert.equal(initial.snapshot_available, true); assert.equal(initial.calls.length, 1);
    if (output) fs.writeFileSync(path.join(output, 'initial-identity-red.json'), JSON.stringify(results, null, 2));
    console.log('Reproduced initial identity public load AbortError despite available snapshot');
    return;
  }
  assert.equal(initial.status, 'resolved'); assert.deepEqual(initial.messages, initial.current);
  assert.equal(initial.initialEvents, 1); assert.deepEqual(initial.calls, [{ id: 'resident_111111111111', historyLimit: 2 }]);
  assert.equal(await page.locator('[data-message-key="initial-message"]').count(), 1);
  await setup();
  await page.evaluate(() => { panel.protocol.destroy(); panel.protocol = acquire(); window.second = outcome(panel.protocol.load({ historyLimit: 2 })); });
  await finishIdentity();
  const replaced = await page.evaluate(async () => ({ old: await first, replacement: await second, calls }));
  assert.equal(replaced.old.error, 'AbortError'); assert.equal(replaced.replacement.status, 'resolved');
  assert.deepEqual(replaced.calls, [{ id: 'resident_111111111111', historyLimit: 2 }]);
  results.push({ scenario: 'subscription replaced during initial identity wait', ...replaced });
  await setup();
  await page.evaluate(() => panel.protocol.destroy());
  await finishIdentity();
  const destroyed = await page.evaluate(async () => ({ old: await first, calls }));
  assert.equal(destroyed.old.error, 'AbortError'); assert.deepEqual(destroyed.calls, []);
  results.push({ scenario: 'subscription destroyed during initial identity wait', ...destroyed });
  if (output) fs.writeFileSync(path.join(output, 'initial-identity-green.json'), JSON.stringify({ results,
    source_sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(repo, 'management_portal/static/resident_protocol.js'))).digest('hex'),
    identity_source_sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(repo, 'management_portal/static/portal_identity.js'))).digest('hex') }, null, 2));
  console.log('Initial identity and subscription lifetime checks passed');
};
