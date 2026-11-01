'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Invoked by the native lineage.html fixture. No fabricated Chat/Menu markup.
module.exports = async ({page, root, residentId, childId, sessionId, errors, requests, deleteFixtureResident}) => {
  page.setDefaultTimeout(5000);
  const {readResidentChatReady} = require('../../../management_portal/test_framework/tau_chat_ready_evidence');
  const sample = await readResidentChatReady(root, {residentId});
  assert.equal(sample.ready, true);
  assert.equal(sample.session_identity_ready, false,
    'an established provider Session does not add a native Chat header field');
  assert.equal(await root.locator('[data-workspace-identity="session"]').count(), 0);
  // Negative control for the archived 300bee4 readiness condition.
  assert.equal(sample.ready && sample.session_identity_ready, false);
  const prompt = fs.readFileSync(path.join(__dirname,'../../../management_portal/test_framework/config/tau_runner_prompt.md'),'utf8');
  assert.ok(!prompt.includes('首次真实输入建立Session后现场等 session_identity_ready'),
    'the archived public prompt requires a Session header absent from the native page');
  await root.getByRole('button', {name: 'Resident Menu', exact: true}).click();
  const menu = page.getByRole('dialog', {name: 'Menu', exact: true});
  await menu.waitFor({state: 'visible'});
  await menu.getByText(residentId, {exact: true}).waitFor({state: 'visible'});
  await menu.getByRole('status').filter({hasText: sessionId}).waitFor({state: 'visible'});
  const copy = menu.getByRole('button', {name: /^(复制|Copy) Session ID$/});
  await copy.waitFor({state: 'visible'});
  assert.equal(await copy.isEnabled(), true);
  assert.equal(await menu.locator('#nodeMenuSessionId').innerText(), sessionId);
  const {readResidentMenuSession} = require('../../../management_portal/test_framework/tau_session_dom_evidence');
  const menuSession = await readResidentMenuSession(menu, {residentId});
  assert.equal(menuSession.ready, true, JSON.stringify(menuSession));
  assert.equal(menuSession.session_id, sessionId);
  assert.equal((await readResidentMenuSession(menu, {residentId:childId})).ready, false);
  await page.evaluate(() => { window.sampleCopied = []; PortalUI.copyText = async value => { sampleCopied.push(value); }; });
  await copy.click();
  assert.deepEqual(await page.evaluate(() => sampleCopied), [sessionId], 'native event copies the full opaque ID');
  await menu.getByRole('button', {name: /^(关闭|Close)$/}).click();
  await menu.waitFor({state: 'hidden'});
  assert.equal((await readResidentMenuSession(menu, {residentId})).ready, false);
  assert.equal((await readResidentChatReady(root, {residentId})).ready, true);
  await page.getByRole('button', {name:'Show HATG', exact:true}).click();
  const hatg = page.getByRole('region', {name:'HATG', exact:true});
  await hatg.waitFor({state:'visible'});
  const newNode = hatg.getByRole('button', {name:/^New node:/});
  await newNode.waitFor({state:'visible'});
  await page.waitForFunction(() => !document.getElementById('addNodeBtn')?.disabled);
  // Both the Workbench host and its named SECTION are real region roots.
  // The latter has an implicit accessibility role and no literal role attribute.
  const {readSessionLineageIdentity, readSessionGraphSnapshot} = require('../../../management_portal/test_framework/tau_session_prerequisites');
  const {readHatgForkPrerequisites} = require('../../../management_portal/test_framework/tau_hatg_fork_evidence');
  const {readWatchboardNodePrerequisites} = require('../../../management_portal/test_framework/tau_watchboard_node_prerequisites');
  const nativePane = hatg.locator('section#hatgPane[data-lineage-pane="hatg"]');
  assert.equal(await nativePane.getAttribute('role'), null);
  const {waitForHatgView} = require('../../../management_portal/test_framework/tau_hatg_view_evidence');
  const geometrySamples=[];
  const graphReady=await waitForHatgView(hatg,{lineageId:'lineage_222222222222',nodeIds:[residentId],edgeIds:[],
    requireExecutionOpen:true,requireWithinCanvas:true,stableMs:120,intervalMs:30,timeoutMs:3000,
    record:s=>geometrySamples.push(s)});
  assert.ok(graphReady.ready && geometrySamples.length>=3);
  for (const panel of [nativePane, hatg]) {
    assert.equal((await readSessionLineageIdentity(panel, {lineageId:'lineage_222222222222'})).identity_ready, true);
    assert.equal((await readSessionLineageIdentity(panel, {lineageId:'lineage_other'})).identity_ready, false);
    const graph = await readSessionGraphSnapshot(panel, {residentId});
    assert.equal(graph.visible_node_count, 1);
    assert.equal(graph.work_state, 'idle');
    assert.equal((await readHatgForkPrerequisites(panel, {residentId})).checks.source_bound, true);
    assert.equal((await readWatchboardNodePrerequisites(panel, {residentId})).first.bound_to_canvas, true);
  }
  await assert.rejects(readSessionGraphSnapshot(hatg.locator('#hatgCanvas'), {residentId}), /native HATG/);
  await newNode.click();
  const form = page.getByRole('dialog', {name:'New node', exact:true});
  await form.getByRole('textbox', {name:'Agent name', exact:true}).waitFor({state:'visible'});
  await form.getByRole('button', {name:'Cancel', exact:true}).click();
  await form.waitFor({state:'hidden'});
  const catalog = page.getByRole('region', {name:'Lineage navigation', exact:true});
  await catalog.getByRole('button', {name:'Lineage menu',exact:true}).click();
  assert.equal(await page.getByRole('dialog', {name:'New node',exact:true}).count(), 0,
    'opening the native card menu alone does not open the creation form');
  await page.getByRole('button', {name:'Attach',exact:true}).click();
  const choices = page.getByRole('dialog', {name:'Additional Resident',exact:true});
  await choices.getByRole('button', {name:'New Resident',exact:true}).click();
  await page.getByRole('dialog').getByRole('textbox', {name:/Title|标题/}).waitFor({state:'visible'});
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({state:'hidden'});
  const fork = hatg.getByRole('button', {name:'从「Fixture Resident」完整上下文 Fork 新 Resident',exact:true});
  await fork.click();
  const children = page.locator('.portalChatWorkspaceRoot').filter({has:page.locator(`.chatHead [data-workspace-resident-id="${childId}"]`)});
  await children.waitFor({state:'visible'});
  assert.equal(await children.count(), 1, 'native Fork has already opened the child; discovery adds no Editor');
  assert.equal((await readResidentChatReady(children,{residentId:childId})).ready,true);
  assert.equal(requests.filter(r=>r.instruction === 'ForkResident').length,1);
  const originalEditor = await children.getAttribute('id');
  await page.evaluate(childId => {
    const host=document.createElement('section');host.id='sample-second-editor';
    host.style.cssText='position:absolute;left:850px;top:100px;width:550px;height:700px';
    document.body.append(host);
    window.sampleSecondEditor=PortalChatWorkspace.panelPackage.create({root:host,id:host.id,target:{resident_id:childId}});
  },childId);
  await page.locator('#sample-second-editor .chatHead [data-workspace-resident-id]').waitFor({state:'visible'});
  const discovered = await children.evaluateAll(nodes=>nodes.map(n=>n.id));
  assert.deepEqual(new Set(discovered),new Set([originalEditor,'sample-second-editor']));
  assert.equal((await readResidentChatReady(children,{residentId:childId})).ready,false,'ambiguous Editors cannot become ready');
  await assert.rejects(children.waitFor({state:'visible',timeout:150}),/strict mode violation/,
    'the archived strict wait fails before it can enumerate Editors');
  assert.equal((await readResidentChatReady(page.locator('#'+originalEditor),{residentId:childId})).ready,true,
    'the original Fork-opened Editor is selected by its discovered DOM identity');
  const stableRoot = page.locator('#sample-second-editor');
  const oldName = stableRoot.and(page.getByRole('region',{name:'Fixture Child',exact:true}));
  assert.equal(await oldName.count(),1);
  deleteFixtureResident(childId);
  await assert.rejects(page.evaluate(() => sampleSecondEditor.reload()), /Error/);
  await stableRoot.getByText(/该 Resident 已删除或不存在|This Resident has been deleted|Resident has been deleted/).waitFor({state:'visible'});
  assert.equal(await oldName.count(),0,'deleted Chat no longer has its previous accessible name');
  assert.equal(await stableRoot.count(),1,'discovered native root survives its title change');
  const deleted = await readResidentChatReady(stableRoot,{residentId:childId});
  assert.equal(deleted.candidates[0].checks.identity,true);
  assert.equal(deleted.ready,false,'deletion feedback is not a send-ready Chat');
  await page.evaluate(()=>{sampleSecondEditor.destroy();document.getElementById('sample-second-editor').remove();});

  // A test host contains the actual Family status renderer, not a hand-written table.
  await page.evaluate(() => {
    const host = document.createElement('div'); host.id='sample-family-host'; host.setAttribute('role','dialog');
    host.setAttribute('aria-label','Sample family scope'); document.body.append(host);
    window.sampleFamily = PortalChatWorkspace.mountFamilyStatus('resident_444444444444',host);
  });
  const scope = page.getByRole('dialog',{name:'Sample family scope',exact:true});
  await scope.getByRole('table').waitFor({state:'visible'});
  assert.equal(await scope.getByRole('region').filter({has:scope.getByRole('table')}).count(),0,
    'an outer dialog path cannot resolve relative to a candidate region');
  assert.equal(await scope.getByRole('region').filter({has:page.getByRole('table')}).count(),1);
  await page.evaluate(() => {sampleFamily.destroy();document.getElementById('sample-family-host').remove();});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({status:'passed', formal_acceptance:false,
    samples:['native Chat without Session header', 'full Session via native Resident Menu', 'copy event identity',
      'direct form versus card menu and choice dialog', 'original quick Fork opens Chat',
      'discover duplicate native Editors before strict wait', 'relative has with native Family table'],
    local_requests:requests.length}));
};
