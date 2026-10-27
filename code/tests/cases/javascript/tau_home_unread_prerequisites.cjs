'use strict';
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const code = path.resolve(__dirname, '../../..'), portal = path.join(code, 'management_portal');
const { chromium } = require(path.join(portal, 'test_framework/tau_runtime/node_modules/playwright'));
const { readHomeUnreadPrerequisites: read } = require(path.join(portal, 'test_framework/tau_home_unread_prerequisites.js'));
const python = text => execFileSync(process.env.TFI_TEST_PYTHON || 'python3', ['-c', text], { cwd: code, encoding: 'utf8' });
const html = python('from management_portal import script_groups; print(script_groups.expand_document(open("management_portal/index.html").read()))');
const routes = python('from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())');
const residentId = 'resident_111111111111', lineageId = 'lineage_222222222222', actor = 'fixture';
const inputId = 'msg_current_input', finalId = 'msg_current_final';
const expected = { residentId, lineageId, inputMessageId: inputId, finalMessageId: finalId, requestActor: actor };
const markers = JSON.parse(python('import json; from management_portal.conversation_completion import completion_signature; '
  + 'print(json.dumps([completion_signature(dict(schema_version=1,request_actor="fixture",input_message_id=i,completion_id=f,completed_at="2026-10-27T00:00:00Z")) '
  + 'for i,f in [("msg_previous_input","msg_previous_final"),("msg_current_input","msg_current_final")]]))'));

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
    const requests = [], errors = [];
    const resident = { resident_id: residentId, lineage_id: lineageId, title: 'Fixture target', type: 'resident' };
    let signature = markers[0], activity = 'running', revision = 1;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { window.PortalSite = { apply() {} }; });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), body = req.postDataJSON();
      assert.equal(url.origin, 'http://home-unread.fixture.invalid');
      requests.push({ path: url.pathname, method: req.method(), instruction: body?.instruction });
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname === '/static/portal_resource_routes.js') return route.fulfill({ contentType: 'text/javascript', body: routes });
      const file = path.join(portal, url.pathname);
      if (fs.existsSync(file) && fs.statSync(file).isFile()) return route.fulfill({ path: file });
      if (url.pathname === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: '' });
      let result = {};
      if (url.pathname === '/api/me') result = { username: actor };
      if (url.pathname === '/api/agent-status') result = { ready: true, accounts: [] };
      if (url.pathname === '/api/agent-status/active-agents') result = { ready: true, running: activity === 'running' ? 1 : 0, queued: 0,
        running_runs: activity === 'running' ? [resident] : [], queued_runs: [] };
      if (url.pathname === '/api/agent-conversations') result = { ready: true, entries: [{ ...resident, key: 'resident:' + residentId,
        section: 'conversation', unread: true, read_revision: revision, completion_signature: signature, status: 'idle', cursor: revision }],
        read_revision: revision, content_signature: 'fixture-index-' + revision };
      if (url.pathname === '/api/instructions/execute') {
        if (!/^(Get|List|Search|Read)/.test(body.instruction)) errors.push('Unexpected fixture instruction ' + body.instruction);
        if (body.instruction === 'ListResidentStates') result = { states: [{ resident_id: residentId, cursor: revision,
          state: { status: 'idle', work_state: 'idle', working: false, queued: false, queued_count: 0, active_message_id: '', intake_state: 'open' },
          viewer_state: { unread: true, read_revision: revision } }] };
        result = { status: 'committed', result };
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
    });
    await page.goto('http://home-unread.fixture.invalid/');
    const root = page.locator('[data-home-component="agent-status"]');
    const icon = root.locator(`a.agent-status-creature[data-agent-status-resident="${residentId}"]`);
    await icon.waitFor({ timeout: 10000 });
    await page.waitForFunction(id => PortalResidentProtocol.stateSnapshot(id)?.ready
      && PortalConversationStore.snapshot().ready && PortalConversationStore.entry('resident:' + id)?.unread, residentId);
    const original = await page.evaluate(id => ({ protocol: PortalResidentProtocol.stateSnapshot(id),
      signature: PortalConversationStore.entry('resident:' + id).completion_signature }), residentId);
    assert.equal(original.protocol.state.status, 'idle');
    assert.equal(original.protocol.viewer_state.unread, true);
    assert.equal(original.signature, markers[0]);
    assert.match(await icon.getAttribute('class'), /running/);
    if (process.argv.includes('--probe')) {
      console.log(JSON.stringify({ status: 'reproduced', original, icon: await icon.getAttribute('class'), errors,
        scope: 'full native Home/ConversationStore/protocol with local HTTP fixtures; no original input or read replay', formal_acceptance: false }));
      assert.deepEqual(errors, []);
      return;
    }
    const stale = await read(root, expected);
    assert.equal(stale.ready, false);
    assert.equal(stale.observation.checks.idle, true);
    assert.equal(stale.observation.checks.viewer_unread, true);
    assert.equal(stale.observation.checks.current_final, false);
    assert.equal(stale.observation.checks.unread_icon, false);
    assert.equal(stale.observation.entries[0].completion_signature, markers[0]);
    // The fixture changes its HTTP responses; the production consumers still
    // own index/state loading, notification handling and icon rendering.
    signature = markers[1]; revision++;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('portal:change', { detail: {
      domains: ['projection'], projection_changes: { 'agent-conversation-index': { current: {} } },
    } })));
    await page.waitForFunction(({ id, marker }) => PortalConversationStore.entry('resident:' + id)?.completion_signature === marker,
      { id: residentId, marker: signature }, { timeout: 8000 });
    const stillRunning = await read(root, expected);
    assert.equal(stillRunning.observation.checks.current_final, true);
    assert.equal(stillRunning.observation.checks.unread_icon, false);
    assert.equal(stillRunning.ready, false);
    activity = 'idle'; revision++;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('portal:change', { detail: {
      domains: ['projection'], projection_changes: { 'active-agents': { current: {} }, 'agent-conversation-index': { current: {} } },
    } })));
    await page.waitForFunction(({ id, marker }) => PortalConversationStore.entry('resident:' + id)?.completion_signature === marker,
      { id: residentId, marker: signature }, { timeout: 8000 });
    await icon.and(page.locator('.unread')).waitFor({ timeout: 8000 });
    await page.waitForFunction(() => !PortalConversationStore.snapshot().refreshing);
    const ready = await read(root, expected);
    assert.equal(ready.ready, true, JSON.stringify(ready));
    for (const changed of [{ inputMessageId: 'msg_other' }, { finalMessageId: 'msg_other' }, { requestActor: 'other' }]) {
      const wrong = await read(root, { ...expected, ...changed });
      assert.equal(wrong.ready, false);
      assert.equal(wrong.observation.checks.current_final, false);
    }
    // Deliberately malformed public snapshots below are negative fixtures.
    // They do not stand in for the native consumers exercised above.
    await page.evaluate(() => {
      window.originalStore = window.PortalConversationStore;
      window.originalProtocol = window.PortalResidentProtocol;
      window.storeSnapshot = PortalConversationStore.snapshot();
    });
    for (const mutation of ['missing_queue', 'missing_store_ready', 'missing_entries', 'duplicate_entry', 'missing_marker',
      'malformed_marker', 'unknown_schema', 'entry_read', 'wrong_state_identity', 'wrong_entry_identity', 'wrong_entry_lineage', 'store_error']) {
      await page.evaluate(({ mutation, id }) => {
        const snapshot = JSON.parse(JSON.stringify(storeSnapshot));
        const protocol = JSON.parse(JSON.stringify(originalProtocol.stateSnapshot(id)));
        if (mutation === 'missing_queue') delete protocol.state.queued_count;
        if (mutation === 'missing_store_ready') delete snapshot.ready;
        if (mutation === 'missing_entries') delete snapshot.entries;
        if (mutation === 'duplicate_entry') snapshot.entries.push({ ...snapshot.entries[0] });
        if (mutation === 'missing_marker') delete snapshot.entries[0].completion_signature;
        if (mutation === 'malformed_marker') snapshot.entries[0].completion_signature = 'actor-completion-v7:{';
        if (mutation === 'unknown_schema') snapshot.entries[0].completion_signature = snapshot.entries[0].completion_signature.replace('"schema_version":1', '"schema_version":2');
        if (mutation === 'entry_read') snapshot.entries[0].unread = false;
        if (mutation === 'wrong_state_identity') protocol.resident_id = 'resident_other';
        if (mutation === 'wrong_entry_identity') snapshot.entries[0].resident_id = 'resident_other';
        if (mutation === 'wrong_entry_lineage') snapshot.entries[0].lineage_id = 'lineage_other';
        if (mutation === 'store_error') snapshot.error = 'fixture-index-error';
        window.PortalConversationStore = { snapshot: () => snapshot };
        window.PortalResidentProtocol = { stateSnapshot: () => protocol };
      }, { mutation, id: residentId });
      const bad = await read(root, expected);
      assert.equal(bad.ready, false, mutation + JSON.stringify(bad));
      if (mutation === 'missing_queue') assert.equal(bad.observation.protocol.state.queued_count, null);
      if (mutation === 'duplicate_entry') assert.equal(bad.observation.entries.length, 2);
      if (mutation === 'malformed_marker') assert.match(bad.observation.completion.parse_error.stack, /SyntaxError/);
    }
    await page.evaluate(() => { window.PortalConversationStore = originalStore; window.PortalResidentProtocol = originalProtocol; });
    await icon.evaluate(el => { el.style.display = 'none'; });
    assert.equal((await read(root, expected)).ready, false);
    await icon.evaluate(el => { el.style.display = ''; el.after(el.cloneNode(true)); });
    const duplicateIcon = await read(root, expected);
    assert.equal(duplicateIcon.ready, false);
    assert.equal(duplicateIcon.observation.icons.length, 2);
    await icon.nth(1).evaluate(el => el.remove());
    const duplicateRoot = await read(page.locator('[data-home-component]'), expected);
    assert.equal(duplicateRoot.ready, false);
    assert.ok(duplicateRoot.observation.roots.length > 1);
    await page.evaluate(() => { window.PortalConversationStore = { snapshot() { throw new Error('fixture-store-error'); } }; });
    const error = await read(root, expected);
    assert.equal(error.ready, false);
    assert.equal(error.error.phase, 'conversation_index');
    assert.match(error.error.stack, /fixture-store-error/);
    assert.equal(error.observation.protocol.ready, true);
    assert.equal(error.observation.icons.length, 1);
    await page.evaluate(() => { window.PortalConversationStore = originalStore; });
    // Watch only this single read: timers belonging to native Home remain intact.
    const quiet = await page.evaluate(() => {
      window.readMutationObserver = new MutationObserver(records => { window.readMutations.push(...records.map(r => r.type)); });
      window.readMutations = [];
      readMutationObserver.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      return true;
    });
    const requestCount = requests.length;
    const readonly = await read(root, expected);
    const mutations = await page.evaluate(() => { readMutationObserver.disconnect(); return readMutations; });
    assert.equal(quiet, true);
    assert.equal(readonly.ready, true);
    assert.equal(requests.length, requestCount);
    assert.deepEqual(mutations, []);
    assert.deepEqual(errors, []);
    const evidence = { status: 'passed', stale, stillRunning: stillRunning.observation.checks, ready,
      errors, scope: 'native Home/Store/protocol with local HTTP fixtures; malformed negative snapshots explicitly injected', formal_acceptance: false };
    if (process.env.TAU_HOME_UNREAD_EVIDENCE_PATH) fs.writeFileSync(process.env.TAU_HOME_UNREAD_EVIDENCE_PATH, JSON.stringify(evidence, null, 2) + '\n');
    console.log(JSON.stringify(evidence));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
