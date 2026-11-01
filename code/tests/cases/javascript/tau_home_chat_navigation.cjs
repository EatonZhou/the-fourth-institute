'use strict';
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const code = path.resolve(__dirname, '../../..'), portal = path.join(code, 'management_portal');
const { chromium } = require(path.join(portal, 'test_framework/tau_runtime/node_modules/playwright'));
const python = text => execFileSync(process.env.TFI_TEST_PYTHON || 'python3', ['-c', text], { cwd: code, encoding: 'utf8' });
const html = python('from management_portal import script_groups; print(script_groups.expand_document(open("management_portal/lineage.html").read()))');
const routes = python('from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())');
const residentId = 'resident_111111111111', lineageId = 'lineage_222222222222';
const origin = 'http://home-navigation.fixture.invalid';
const entryHref = `${origin}/lineage?resident=${residentId}&lineage=${lineageId}&open_chat=1`;
const readinessSamples = process.argv.includes('--readiness-samples');
const cameraSamples = process.argv.includes('--camera-samples');
let releaseGraph;
const graphGate = cameraSamples ? new Promise(resolve => { releaseGraph = resolve; }) : Promise.resolve();
const sessionId = 'opaque.full-session-id-for-native-menu';
const childId = 'resident_333333333333';
let forked = false;
const deletedResidents = new Set();

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { window.PortalSite = { apply() {} }; });
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url());
      assert.equal(u.origin, origin);
      const body = req.postDataJSON();
      requests.push({ path: u.pathname, method: req.method(), instruction: body?.instruction });
      if (u.pathname === '/lineage') return route.fulfill({ contentType: 'text/html', body: html });
      if (u.pathname === '/static/portal_resource_routes.js') return route.fulfill({ contentType: 'text/javascript', body: routes });
      const file = path.join(portal, u.pathname);
      if (fs.existsSync(file) && fs.statSync(file).isFile()) return route.fulfill({ path: file });
      if (u.pathname === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: '' });
      const makeResident = id => ({id, resident_id:id, lineage_id:lineageId,
        title:id === childId ? 'Fixture Child' : 'Fixture Resident', owner:'fixture', backend:'codex', kind:'agent', x:id === childId ? 400 : 0, y:0});
      const targetId = body?.arguments?.resident_id || u.searchParams.get('id') || residentId;
      if (deletedResidents.has(targetId) && (u.pathname.endsWith('/projection') || body?.instruction === 'GetConversation'))
        return route.fulfill({status:404,json:{code:'resident.deleted',error:'Resident deleted'}});
      const resident = makeResident(targetId);
      const nodes = [makeResident(residentId), ...(forked ? [makeResident(childId)] : [])];
      let result = {};
      if (u.pathname === '/api/me') result = { username: 'fixture' };
      if (u.pathname === '/api/agent-workbench/residents') result = { ready: true, residents: nodes,
        lineages: [{lineage_id:lineageId, title:'Fixture Lineage', leader_resident_id:residentId, project_id:'sample-project', allows:{CreateResident:true}}],
        principal_hierarchy: { projects: readinessSamples ? [{project_id:'sample-project', title:'Sample Project',
          lineages:[{lineage_id:lineageId,title:'Fixture Lineage',resident_id:residentId}]}] : [], user_residents: [] } };
      if (u.pathname.endsWith('/navigation-target')) result = resident;
      if (u.pathname.endsWith('/projection')) result = { resident_id: targetId, lineage_id: lineageId, resident, provider_session: {id: sessionId},
        family_facade: targetId === 'resident_444444444444' ? {family_id:'fbi_sample',title:'Sample Family',nodes:[makeResident(residentId)]} : {}, chat_workspace: {
        schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: targetId, lineage_id: lineageId },
        title: resident.title, header: { title: resident.title, subtitle_parts: [
          { identity: 'resident', text: targetId, copy_value: targetId, copy_label: 'resident_id' },
        ] }, messages: [], composer: { can_send: true, can_edit: true },
      } };
      if (u.pathname === '/api/agent-conversations') result = { ready: true, entries: [] };
      if (u.pathname === '/api/instructions/execute') {
        if (!/^(Get|List|Search|Read)|^MarkMailboxItemRead$/.test(body.instruction)
            && !(readinessSamples && body.instruction === 'ForkResident')) {
          errors.push(`Unexpected local instruction: ${body.instruction}`);
          return route.fulfill({ status: 400, json: { error: { code: 'fixture.unexpected_instruction' } } });
        }
        if (body.instruction === 'MarkMailboxItemRead') result = { item: { resident_id: residentId, unread: false, read_revision: 1 } };
        if (body.instruction === 'GetConversation') result = { resident, messages: [], queue: { items: [] }, controls: { can_send: true }, state: 'idle' };
        if (body.instruction === 'GetResident') result = {resident};
        if (body.instruction === 'ListWorkbenches') result = {workbenches:[]};
        if (body.instruction === 'ForkResident') { forked = true; result = {resident_id:childId}; }
        if (body.instruction === 'ListResidentStates') result = { states: [{ resident_id: residentId, state: 'idle', cursor: 1, viewer_state: { unread: false } }] };
        if (body.instruction === 'GetLineage') {
          await graphGate;
          result = { lineage: { lineage_id: lineageId, revision:forked ? 2 : 1, title: 'Fixture Lineage', nodes, residents:nodes, edges: [], seeds: [], function_blocks: [], program: {components: [], wires: []}, components: [], wires: [] } };
        }
        result = { status: 'committed', result };
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
    });
    await page.goto(entryHref);
    await page.waitForURL(url => url.searchParams.get('resident') === residentId && !url.searchParams.has('open_chat'), { timeout: 12000 });
    const { readResidentChatReady } = require(path.join(portal, 'test_framework/tau_chat_ready_evidence'));
    const root = page.locator('#lineage-chat-panel');
    await root.locator('.chatHead [data-workspace-resident-id]').waitFor({ timeout: 10000 }).catch(async error => {
      console.error(JSON.stringify({ url: page.url(), errors, requests: requests.filter(r => !r.path.startsWith('/static/')),
        root: (await root.innerText()).slice(0,2000), chat: await readResidentChatReady(root, { residentId }) }));
      throw error;
    });
    const chat = await readResidentChatReady(root, { residentId });
    assert.equal(chat.ready, true, JSON.stringify({ chat, errors, requests }));
    if (cameraSamples) {
      await require('./tau_native_camera_samples.cjs')({page, residentId, releaseGraph, errors});
      return;
    }
    if (readinessSamples) {
      await require('./tau_native_readiness_samples.cjs')({page, root, residentId, childId, sessionId, errors, requests,
        deleteFixtureResident:id=>deletedResidents.add(id)});
      return;
    }
    if (process.argv.includes('--probe')) {
      await assert.rejects(page.waitForURL(entryHref, { timeout: 150 }), /Timeout/);
    } else {
      const { readHomeChatNavigation } = require(path.join(portal, 'test_framework/tau_home_chat_navigation'));
      const options = { sourceUrl: origin + '/', entryHref, residentId, lineageId };
      const read = () => readHomeChatNavigation(page, options);
      const first = await read();
      assert.equal(first.location_matches, true, JSON.stringify(first));
      assert.equal(first.location.one_shot_state, 'consumed');
      assert.equal(Object.hasOwn(first, 'ready'), false, 'location is not business or Chat readiness');
      const stable = `/lineage?lineage=${lineageId}&resident=${residentId}`;
      // Use history only inside this isolated fixture. The helper never alters it.
      const setURL = url => page.evaluate(url => history.replaceState({}, '', url), url);
      for (const url of [stable, stable + '&open_chat=1', stable + '#chat']) {
        await setURL(url);
        assert.equal((await read()).location_matches, true, url);
      }
      for (const url of ['/lineage', `/lineage?resident=${residentId}`, stable.replace(lineageId, 'lineage_wrong'),
        stable.replace(residentId, 'resident_wrong'), stable.replace('/lineage?', '/home?'),
        stable + '&resident=' + residentId, stable + '&lineage=' + lineageId,
        stable + '&open_chat=0', stable + '&open_chat=1&open_chat=1']) {
        await setURL(url);
        const sample = await read();
        assert.equal(sample.location_matches, false, url);
        assert.ok(sample.location, 'rejected values remain available for original-action persistence');
      }
      await setURL(stable);
      for (const bad of [entryHref.replace(origin, 'https://foreign.invalid'),
        entryHref.replace('/lineage?', '/home?'), entryHref.replace('&open_chat=1', ''),
        entryHref + '&resident=' + residentId, entryHref.replace('http://', 'http://secret:password@')]) {
        const sample = await readHomeChatNavigation(page, { ...options, entryHref: bad });
        assert.equal(sample.location_matches, false);
        assert.equal(sample.entry.valid, false);
        assert.ok(!JSON.stringify(sample).includes('password'), 'do not retain URL credentials');
      }
      const broken = await readHomeChatNavigation(page, { ...options, sourceUrl: 'invalid' });
      assert.equal(broken.error.phase, 'original_link');
      await root.evaluate(n => n.hidden = true);
      assert.equal((await read()).location_matches, true);
      assert.equal((await readResidentChatReady(root, { residentId })).ready, false, 'URL cannot substitute for visible Chat');
      await root.evaluate(n => n.hidden = false);
      assert.equal((await readResidentChatReady(root, { residentId: 'resident_other' })).ready, false);
      assert.equal((await readResidentChatReady(root, { residentId })).ready, true);
      const before = { requests: requests.length, url: page.url(), html: await root.innerHTML() };
      const saved = JSON.stringify(first);
      await read();
      assert.equal(requests.length, before.requests, 'location sampling sends no request or readmark');
      assert.equal(page.url(), before.url);
      assert.equal(await root.innerHTML(), before.html);
      assert.equal(JSON.stringify(first), saved);
      await page.close();
      const failed = await read();
      assert.equal(failed.location_matches, false);
      assert.equal(failed.location, null);
      assert.equal(failed.error.phase, 'current_location');
      assert.ok(failed.error.name && failed.error.message && failed.error.stack);
      assert.equal(failed.entry.valid, true, 'original link retained when destination sampling fails');
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'passed', scope: 'native lineage.html/scripts/Workbench/Chat; all HTTP and automatic read acknowledgement are local fixtures',
      entry_href: entryHref, chat, errors, formal_acceptance: false, original_caller_rtt: false }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
