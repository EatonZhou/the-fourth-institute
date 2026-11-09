'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_button_tooltips.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css', 'portal_components.css', 'portal_components_markdown.css',
  'portal_components_chat_frame.css', 'portal_components_messages.css', 'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const output = process.env.SCREENSHOT_DIR;
if (output) fs.mkdirSync(output, { recursive: true });
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body;
    if (!name) { res.setHeader('Content-Type', 'text/html'); body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
      + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('') + '<style>html,body{height:100%;margin:0}#chat{height:100%;min-height:0}</style><main id="chat"></main><script src="routes.js"></script>'
      + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else { res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
      if (name === 'routes.js') body = routeScript;
      else if (modules.includes(name) || styles.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name)); }
    res.statusCode = body === undefined ? 404 : 200; res.end(body ?? 'not found');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser; const checks = [], errors = [];
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1100, height: 820 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:' + server.address().port);
    if (process.env.CHAT_RESUME_ONLY) {
      await require('./chat_resume.browser.cjs')({ page, output });
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.HISTORY_WINDOW_IDENTITY_ONLY) {
      await require('./chat_history_identity.cjs')({ page, repo, output });
      assert.deepEqual(errors, []);
      return;
    }
    await page.evaluate(async () => {
      window.rid = 'resident_111111111111'; window.viewer = 'reader'; PortalIdentity = { username: () => viewer, load: async () => {} };
      window.version = 20; window.calls = []; window.gates = []; window.failPage = false; window.personal = false;
      window.row = n => ({ message_id: 'm' + n, role: 'user', origin: { sender: { kind: 'user', id: 'reader' } }, created_at: `2026-11-05T00:${String(n).padStart(2, '0')}:00Z`, content_parts: [{ type: 'text', text: `历史第${n}轮\n` + '这段已加载历史在刷新后应保持可阅读。\n'.repeat(5) }] });
      window.data = Array.from({ length: 10 }, (_, i) => row(i + 1));
      window.facts = Object.fromEntries(data.map(m => [m.message_id, { message_id: m.message_id, state: 'completed', work: { state: 'completed', kind: 'primary', execution_id: 'execution-' + m.message_id, updated_at: '2026-11-05T00:00:00Z' } }]));
      window.pageFor = (before, limit) => {
        const visible = data.filter(m => Number(m.message_id.slice(1)) < before).slice(-limit);
        const min = Number(visible[0]?.message_id.slice(1));
        return { resident_id: rid, messages: structuredClone(visible), input_states: structuredClone(visible.map(m => facts[m.message_id]).filter(Boolean)),
          ...(personal ? { family_history: { resident_id: 'resident_222222222222' } } : {}),
          next_page_token: data.some(m => Number(m.message_id.slice(1)) < min) ? (personal && min <= 11 ? 'personal-history:' : '') + String(min) : '' };
      };
      window.defer = async (kind, value) => { if (window.hold === kind) { window.hold = ''; return new Promise((resolve, reject) => gates.push({ kind, resolve: () => resolve(value), reject })); } return value; };
      PortalResidentService = {
        snapshot: async (id, { historyLimit }) => { calls.push({ type: 'snapshot', historyLimit }); return defer('snapshot', {
          ...pageFor(Infinity, historyLimit), resident: { resident_id: id }, cursor: version, state: { work_state: 'idle' }, controls: { can_send: true }, queue: { items: [] }, activities: [] }); },
        conversationMessages: async (id, { pageToken, limit }) => { calls.push({ type: 'page', pageToken, limit }); if (failPage) throw new Error('page denied');
          // This is the SERVER fixture's token decoder; the protocol treats it as opaque.
          return defer('page', pageFor(Number(pageToken.replace(/^personal-history:/, '')), limit)); },
        events: async () => ({ resident_id: rid, cursor: version, events: [] }),
        inputHistory: async () => ({ entries: [] }), runtimeSettings: async () => ({ settings: {} }), runtimeCatalog: async () => ({})
      };
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'historyWindow', autoLoad: false, target: { resident_id: rid }, historyLimit: 2 });
      panel.residentProjection = { resident: { resident_id: rid }, chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: rid }, composer: { can_send: true } } };
      panel.protocol = PortalResidentProtocol.acquire(rid, update => { if (update.snapshot) panel.applySnapshot(update.snapshot, update); }, { load: false });
      await panel.protocol.load({ historyLimit: 2 });
      window.ids = () => panel.protocol.snapshot().messages.map(m => m.message_id);
      window.refresh = () => panel.protocol.load({ force: true, historyLimit: 2 });
    });
    // The public protocol handle owns pagination too; use the exact Panel entry.
    await page.evaluate(() => { window.older = () => panel.loadOlder(); });
    if (!process.env.HISTORY_WINDOW_EDGES_ONLY && !process.env.HISTORY_WINDOW_RACES_ONLY) {
    await page.locator('[data-workspace-load-older]').click();
    await page.waitForFunction(() => ids().includes('m7'));
    await page.evaluate(() => older());
    const before = await page.evaluate(() => ids());
    await page.locator('.paneBody').first().hover(); await page.mouse.wheel(0, -700); await page.waitForTimeout(100);
    if (output) await page.screenshot({ path: path.join(output, 'before-refresh.png') });
    await page.evaluate(() => refresh());
    const after = await page.evaluate(() => ids());
    if (output) fs.writeFileSync(path.join(output, 'first-refresh.json'), JSON.stringify({ before, after }, null, 2));
    if (process.env.HISTORY_WINDOW_BASELINE) { assert(after.length < before.length); console.log('Reproduced: expanded history lost on forced refresh'); return; }
    assert.deepEqual(after, before); checks.push('expanded history and continuation survive forced refresh');
    await page.evaluate(() => { data = [...data, row(11), row(12)]; data = data.filter(m => m.message_id !== 'm6'); data.find(m => m.message_id === 'm7').content_parts = [{ type: 'text', text: '服务端修改后的第7轮\n' + '新正文\n'.repeat(5) }]; facts.m7 = { ...facts.m7, work: { ...facts.m7.work, state: 'running', updated_at: '2026-11-05T01:00:00Z' } }; version++; });
    await page.evaluate(() => refresh());
    assert.deepEqual(await page.evaluate(() => ids()), ['m5', 'm7', 'm8', 'm9', 'm10', 'm11', 'm12']);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'm7').work.state), 'running');
    assert((await page.locator('[data-message-key="m7"]').textContent()).includes('服务端修改')); checks.push('new authoritative page updates content/work and removes deleted historical rows while retaining oldest boundary');
    await page.locator('[data-message-key="m7"]').scrollIntoViewIfNeeded(); await page.locator('.paneBody').first().hover(); await page.mouse.wheel(0, -15); await page.waitForTimeout(100);
    const y = await page.locator('[data-message-key="m7"]').evaluate(n => n.getBoundingClientRect().top);
    await page.evaluate(() => refresh()); await page.waitForTimeout(100);
    assert(Math.abs(await page.locator('[data-message-key="m7"]').evaluate(n => n.getBoundingClientRect().top) - y) < 2); checks.push('native Panel refresh preserves reading anchor');
    if (output) await page.screenshot({ path: path.join(output, 'after-refresh.png') });
    await page.evaluate(() => { hold = 'page'; window.pagePromise = older(); });
    await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => { window.refreshPromise = refresh(); gates.shift().resolve(); });
    await page.evaluate(() => Promise.all([pagePromise, refreshPromise]));
    assert((await page.evaluate(() => ids())).includes('m3')); checks.push('page-first concurrent refresh retains accepted page');
    await page.evaluate(() => { hold = 'snapshot'; window.refreshPromise = refresh(); }); await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => { window.pagePromise = older(); gates.shift().resolve(); });
    await page.evaluate(() => Promise.all([pagePromise, refreshPromise]));
    assert((await page.evaluate(() => ids())).includes('m1')); checks.push('refresh-first concurrent page uses refreshed continuation');
    const callsBefore = await page.evaluate(() => calls.length); await page.evaluate(() => older()); assert.equal(await page.evaluate(() => calls.length), callsBefore); checks.push('exhausted history remains exhausted');
    await page.evaluate(() => { failPage = true; });
    const kept = await page.evaluate(() => ids());
    const failure = await page.evaluate(() => refresh().then(() => '', e => e.message));
    assert.equal(failure, 'page denied'); assert.deepEqual(await page.evaluate(() => ids()), kept); checks.push('failed revalidation never commits partial truncated window');
    await page.evaluate(() => { failPage = false; });
    await page.evaluate(() => { hold = 'snapshot'; window.late = refresh().catch(e => e.name); }); await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => { panel.protocol.destroy(); panel.protocol = PortalResidentProtocol.acquire(rid, update => { if (update.snapshot) panel.applySnapshot(update.snapshot, update); }, { load: false }); data = [row(40), row(41)]; version++; gates.shift().resolve(); });
    assert.equal(await page.evaluate(() => late), 'AbortError'); await page.evaluate(() => panel.protocol.load({ historyLimit: 2 }));
    assert.deepEqual(await page.evaluate(() => ids()), ['m40', 'm41']); checks.push('new subscription rejects old request and discards expanded boundary');
    await page.evaluate(() => { hold = 'snapshot'; window.late = refresh().catch(e => e.name); }); await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => { data = [row(50), row(51)]; viewer = 'other'; window.dispatchEvent(new Event('portal:identity-changed')); gates.shift().resolve(); });
    assert.equal(await page.evaluate(() => late), 'AbortError'); await page.waitForFunction(() => ids().includes('m50'));
    assert.deepEqual(await page.evaluate(() => ids()), ['m50', 'm51']); checks.push('identity switch cannot revive previous rows');
    assert(await page.evaluate(() => calls.filter(c => c.type === 'snapshot').slice(0, -1).every(c => c.historyLimit === 2))); checks.push('refresh snapshot historyLimit never grows; new actor uses the default initial head');
    }
    if (!process.env.HISTORY_WINDOW_CORE_ONLY) {
    await page.evaluate(() => {
      window.resetFixture = async (count = 14, withPersonal = true) => {
        panel.protocol.destroy();
        personal = withPersonal; data = Array.from({ length: count }, (_, i) => row(i + 1)); version++;
        failPage = false; hold = ''; gates = []; calls = [];
        facts = Object.fromEntries(data.map(m => [m.message_id, { message_id: m.message_id, state: 'completed', work: { state: 'running', kind: 'primary', execution_id: 'execution-' + m.message_id, updated_at: '2026-11-05T00:00:00Z' } }]));
        panel.protocol = PortalResidentProtocol.acquire(rid, update => { if (update.snapshot) panel.applySnapshot(update.snapshot, update); }, { load: false });
        await panel.protocol.load({ historyLimit: 2 });
      };
      window.emitEvent = (event_type, payload) => window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_events: [{ resident_id: rid, cursor: ++version, event_type, payload }] } }));
    });
    if (!process.env.HISTORY_WINDOW_RACES_ONLY) {
    await page.evaluate(async () => { await resetFixture(); await older(); });
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().next_page_token), 'personal-history:11');
    await page.evaluate(async () => { await older(); await refresh(); });
    assert.deepEqual(await page.evaluate(() => ids()), ['m9', 'm10', 'm11', 'm12', 'm13', 'm14']);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().next_page_token), 'personal-history:9');
    checks.push('ordinary pages cross to opaque personal-history continuation and retain its exact boundary');
    await page.evaluate(async () => {
      data = data.filter(m => m.message_id !== 'm9'); data.push(row(15), row(16));
      facts.m10 = { ...facts.m10, work: { state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' } };
      version++; await refresh();
    });
    assert.deepEqual(await page.evaluate(() => ids()), ['m10', 'm11', 'm12', 'm13', 'm14', 'm15', 'm16']);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'm10').work.state), 'unknown');
    checks.push('deleted oldest row and new arrivals do not shift the opaque boundary; fresh unknown replaces cached running');
    await page.evaluate(async () => { data = data.filter(m => m.message_id !== 'm10'); version++; await refresh(); });
    assert.deepEqual(await page.evaluate(() => ids()), ['m11', 'm12', 'm13', 'm14', 'm15', 'm16']);
    checks.push('entire oldest loaded page can disappear without retaining deleted rows or expanding into unseen history');
    await page.evaluate(async () => { while (panel.protocol.snapshot().next_page_token) await older(); await refresh(); });
    assert.deepEqual(await page.evaluate(() => ids()), ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm11', 'm12', 'm13', 'm14', 'm15', 'm16']);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().next_page_token), '');
    const endCalls = await page.evaluate(() => calls.length); await page.evaluate(() => older());
    assert.equal(await page.evaluate(() => calls.length), endCalls);
    checks.push('personal-history last page survives refresh and no further page request is issued');
    const expected = await page.evaluate(() => ids());
    await page.evaluate(() => { window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_changes: { [rid]: ['projection'] } } })); });
    await page.waitForTimeout(500); await page.evaluate(() => refresh());
    assert.deepEqual(await page.evaluate(() => ids()), expected);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('portal:ready', { detail: { snapshot_required: true } })));
    await page.waitForTimeout(100); await page.evaluate(() => refresh());
    assert.deepEqual(await page.evaluate(() => ids()), expected);
    checks.push('Family projection refresh and stream reset preserve the expanded personal window');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
    await page.locator('[data-message-key="m7"]').scrollIntoViewIfNeeded();
    await page.locator('.paneBody').first().hover(); await page.mouse.wheel(0, -10); await page.waitForTimeout(100);
    const narrowY = await page.locator('[data-message-key="m7"]').evaluate(n => n.getBoundingClientRect().top);
    await page.evaluate(() => refresh()); await page.waitForTimeout(100);
    assert(Math.abs(await page.locator('[data-message-key="m7"]').evaluate(n => n.getBoundingClientRect().top) - narrowY) < 2);
    if (output) await page.screenshot({ path: path.join(output, 'personal-history-390.png') });
    checks.push('390 / 20px native Chat keeps the same historical reading anchor');
    await page.evaluate(async () => { await resetFixture(); await older(); hold = 'page'; window.late = older().catch(e => e.name); });
    await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => {
      personal = false; data = data.filter(m => Number(m.message_id.slice(1)) >= 11);
      emitEvent('conversation.context.cleared', { context_cleared: true }); gates.shift().resolve();
    });
    assert.equal(await page.evaluate(() => late), 'AbortError'); await page.evaluate(() => refresh());
    assert.deepEqual(await page.evaluate(() => ids()), ['m13', 'm14']);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().next_page_token), '13');
    checks.push('context clear cancels in-flight personal page and discards the obsolete window');
    await page.evaluate(async () => { await resetFixture(10, false); hold = 'page'; window.late = older(); });
    await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => {
      data = data.filter(m => m.message_id !== 'm7');
      emitEvent('conversation.message.updated', { message_id: 'm7', removed: true }); gates.shift().resolve();
    });
    await page.evaluate(() => late);
    assert.deepEqual(await page.evaluate(() => ids()), ['m8', 'm9', 'm10']);
    assert(!(await page.evaluate(() => panel.protocol.snapshot().input_states.map(x => x.message_id))).includes('m7'));
    checks.push('durable removal during page load prevents a stale page from reviving the deleted row or its work');
    await page.evaluate(async () => { await resetFixture(10, false); await older(); hold = 'page'; window.late = refresh().catch(e => e.name); });
    await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => {
      data = [row(40), row(41)]; emitEvent('conversation.context.cleared', { context_cleared: true }); gates.shift().resolve();
    });
    assert.equal(await page.evaluate(() => late), 'AbortError'); await page.evaluate(() => refresh());
    assert.deepEqual(await page.evaluate(() => ids()), ['m40', 'm41']);
    checks.push('context clear during expanded snapshot revalidation rejects the obsolete candidate');
    }
    await page.evaluate(async () => {
      await resetFixture(10, false);
      window.pageReader = PortalResidentService.conversationMessages;
      PortalResidentService.conversationMessages = async (...args) => {
        const response = await pageReader(...args);
        response.messages.push(structuredClone(response.messages[0]), { ...row(10), content_parts: [{ type: 'text', text: '迟到的旧正文' }] });
        response.input_states.push({ ...facts.m10, work: { ...facts.m10.work, state: 'completed', updated_at: '2026-11-04T23:00:00Z' } });
        return response;
      };
      hold = 'page'; window.firstPage = older(); window.secondPage = older();
    });
    await page.waitForFunction(() => gates.length === 1);
    await page.evaluate(() => {
      const message = { ...row(10), content_parts: [{ type: 'text', text: '当前权威正文' }] };
      emitEvent('conversation.message.updated', { message_id: 'm10', message });
      emitEvent('resident.input.state.changed', { input_state: { ...facts.m7, work: { state: 'unknown', kind: 'unknown', updated_at: '' } } });
      gates.shift().resolve();
    });
    await page.evaluate(() => Promise.all([firstPage, secondPage]));
    assert.deepEqual(await page.evaluate(() => ids()), ['m7', 'm8', 'm9', 'm10']);
    assert((await page.locator('[data-message-key="m10"]').textContent()).includes('当前权威正文'));
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'm7').work.state), 'unknown');
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'm10').work.state), 'running');
    assert.equal(await page.evaluate(() => calls.filter(c => c.type === 'page').length), 1);
    checks.push('duplicate pages coalesce; overlapping IDs dedupe and late page content/work cannot overwrite ordered events');
    await page.evaluate(async () => {
      PortalResidentService.conversationMessages = pageReader;
      await resetFixture(10, false); await older();
      PortalResidentService.conversationMessages = async (...args) => {
        const response = await pageReader(...args);
        if (args[1].limit === 2) response.next_page_token = args[1].pageToken;
        return response;
      };
    });
    assert.equal(await page.evaluate(() => refresh().then(() => '', e => e.message)), 'Conversation page did not advance');
    assert.deepEqual(await page.evaluate(() => ids()), ['m7', 'm8', 'm9', 'm10']);
    checks.push('cyclic opaque continuation fails without looping or committing a partial window');
    await page.evaluate(async () => {
      PortalResidentService.conversationMessages = pageReader;
      await resetFixture(); await older(); await older();
      PortalResidentService.conversationMessages = async (...args) => {
        const response = await pageReader(...args);
        response.messages = response.messages.filter(message => message.message_id !== 'm8');
        response.input_states = response.input_states.filter(input => input.message_id !== 'm8');
        return response;
      };
      await refresh();
    });
    assert.deepEqual(await page.evaluate(() => ids()), ['m9', 'm10', 'm11', 'm12', 'm13', 'm14']);
    assert(await page.evaluate(() => calls.some(call => call.pageToken === 'personal-history:8' && call.limit === 2)));
    checks.push('filtered empty personal page advances its opaque token in batches without reviving hidden rows');
    }
    assert.deepEqual(errors, []);
    if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, errors, calls: await page.evaluate(() => calls),
      source_sha256: require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(repo, 'management_portal/static/resident_protocol.js'))).digest('hex'),
      boundary: 'Real ResidentPanel + ResidentProtocol, deterministic isolated service responses. No business or TAU writes.' }, null, 2));
    console.log('History window checks passed: ' + checks.length);
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(e => { console.error(e); process.exitCode = 1; });
