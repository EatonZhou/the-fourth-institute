'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Invoked by the native lineage.html fixture. No fabricated Chat/Menu markup.
module.exports = async ({page, root, residentId, childId, sessionId, errors, requests, deleteFixtureResident}) => {
  page.setDefaultTimeout(5000);
  const {readResidentChatReady,visibleResidentChatRoots} = require('../../../management_portal/test_framework/tau_evidence');
  const discoveredRoot=visibleResidentChatRoots(page,{residentId});
  assert.equal(await discoveredRoot.count(),1);
  assert.equal(await discoveredRoot.getAttribute('id'),await root.getAttribute('id'));
  assert.equal(await root.getAttribute('aria-label'),null, 'the native accessible name does not require a literal aria-label');
  assert.equal(await visibleResidentChatRoots(page,{residentId:'resident_absent'}).count(),0);
  const sample = await readResidentChatReady(root, {residentId});
  assert.equal(sample.ready, true);
  assert.equal(sample.session_identity_ready, false,
    'an established provider Session does not add a native Chat header field');
  assert.equal(await root.locator('[data-workspace-identity="session"]').count(), 0);
  // Negative control for the archived 300bee4 readiness condition.
  assert.equal(sample.ready && sample.session_identity_ready, false);
  const prompt = fs.readFileSync(path.join(__dirname,'../../../management_portal/test_framework/config/tau_runner_prompt.md'),'utf8');
  const controlsExample=prompt.match(/```js\n(\/\/ tau-current-controls\n[\s\S]*?)\n```/);
  assert.ok(controlsExample, 'public dynamic-control contract must be executable');
  const controls=new Function('require','frameworkRoot',controlsExample[1]+'; return {nativeEntryDestination,currentStatusTexts,currentSelectOptions,currentNativeDialog};')(require,path.resolve(__dirname,'../../../management_portal/test_framework'));
  const discoveryExample=prompt.match(/```js\n(\/\/ tau-native-chat-locator\n[\s\S]*?)\n```/);
  assert.ok(discoveryExample, 'the public Chat discovery example must specify its Locator contract');
  const uniqueResidentChat=new Function('visibleResidentChatRoots',discoveryExample[1]+'; return uniqueResidentChat;')(visibleResidentChatRoots);
  assert.equal(await (await uniqueResidentChat(page,residentId)).getAttribute('id'),await root.getAttribute('id'));
  assert.equal(await uniqueResidentChat(page,'resident_absent'),null);
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
  const {readResidentMenuSession} = require('../../../management_portal/test_framework/tau_evidence');
  const menuSession = await readResidentMenuSession(menu, {residentId});
  assert.equal(menuSession.ready, true, JSON.stringify(menuSession));
  assert.equal(menuSession.session_id, sessionId);
  const deletionExplanation=await menu.getByRole('button',{name:'Delete node',exact:true})
    .getAttribute('data-portal-tooltip');
  assert.match(deletionExplanation,/Owned directories and their nested objects are permanently deleted according to resource ownership/);
  assert.match(deletionExplanation,/unrelated files in shared directories are preserved/);
  assert.match(deletionExplanation,/Conversations and execution traces are retained/);
  assert.doesNotMatch(deletionExplanation,/referenced Workspaces are retained/);
  assert.equal((await readResidentMenuSession(menu, {residentId:childId})).ready, false);
  await page.evaluate(() => { window.sampleCopied = []; PortalUI.copyText = async value => { sampleCopied.push(value); }; });
  await copy.click();
  assert.deepEqual(await page.evaluate(() => sampleCopied), [sessionId], 'native event copies the full opaque ID');
  const {readResidentMenuTitle}=require('../../../management_portal/test_framework/tau_evidence');
  const {waitForEvidenceReady}=require('../../../management_portal/test_framework/tau_readiness_wait');
  assert.ok((await readResidentMenuTitle(menu,{residentId,title:'Fixture Resident'})).ready);
  assert.equal((await readResidentMenuTitle(menu,{residentId:childId,title:'Fixture Resident'})).ready,false);
  await menu.getByRole('button',{name:'Rename',exact:true}).click();
  const titleInput=menu.getByRole('textbox',{name:'Resident name',exact:true});
  await titleInput.fill('Renamed native fixture');
  assert.equal((await readResidentMenuTitle(menu,{residentId,title:'Renamed native fixture'})).ready,false,
    'typing an unsaved name is not a committed display');
  await menu.getByRole('button',{name:'Save name',exact:true}).click();
  const titleSamples=[];
  await waitForEvidenceReady(()=>readResidentMenuTitle(menu,{residentId,title:'Renamed native fixture'}),
    {record:row=>titleSamples.push(row),timeoutMs:5000});
  assert.ok(titleSamples.length);
  assert.equal(await menu.locator('#nodeMenuName').evaluate(n=>n.tagName),'STRONG');
  assert.ok(await menu.getByRole('strong').count(),
    'the original failed predicate waited for every strong element to disappear');
  assert.equal(requests.filter(r=>r.instruction==='UpdateResident').length,1);
  await menu.getByRole('button', {name: /^(关闭|Close)$/}).click();
  await menu.waitFor({state: 'hidden'});
  assert.equal((await readResidentMenuSession(menu, {residentId})).ready, false);
  assert.equal((await readResidentMenuTitle(menu,{residentId,title:'Renamed native fixture'})).ready,false);
  assert.equal((await readResidentChatReady(root, {residentId})).ready, true);
  await root.getByRole('button', {name:'Clear Resident context',exact:true}).click();
  const resetConfirmation=page.getByRole('alertdialog',{name:'Clear Resident context?',exact:true});
  await resetConfirmation.waitFor({state:'visible'});
  assert.equal(await page.getByRole('dialog',{name:'Clear Resident context?',exact:true}).count(),0);
  assert.ok(await controls.currentNativeDialog(page,'Clear Resident context?'),
    'the public modal finder must discover the native Reset alertdialog');
  assert.equal(requests.filter(r=>r.instruction==='ClearContext').length,0,
    'opening confirmation has not submitted Reset');
  await resetConfirmation.getByRole('button',{name:'Cancel',exact:true}).click();
  await resetConfirmation.waitFor({state:'hidden'});
  assert.equal(await controls.currentNativeDialog(page,'Clear Resident context?'),null);
  assert.equal(requests.filter(r=>r.instruction==='ClearContext').length,0);
  const openingExample=prompt.match(/```js\n(\/\/ tau-hatg-opening\n[\s\S]*?)\n```/);
  assert.ok(openingExample, 'ordinary navigation must not wait for a hidden HATG');
  const nativeHatgShowControl=new Function('require','frameworkRoot',openingExample[1]+'; return nativeHatgShowControl;')(require,path.resolve(__dirname,'../../../management_portal/test_framework'));
  const {readSessionLineageIdentity:readOpeningIdentity}=require('../../../management_portal/test_framework/tau_evidence');
  const hiddenGraph=page.getByRole('region',{name:'HATG',exact:true});
  assert.equal((await readOpeningIdentity(hiddenGraph,{lineageId:'lineage_222222222222'})).identity_ready,false);
  await page.evaluate(() => PortalI18n.set('zh-CN',{reload:false}));
  await page.reload();
  await page.getByRole('button',{name:'显示 HATG',exact:true}).waitFor({state:'visible'});
  const localizedShow=await nativeHatgShowControl(page);
  assert.equal(await localizedShow.getAttribute('aria-label'),'显示 HATG');
  assert.equal(await hiddenGraph.isVisible(),false,'discovery must not change layout');
  await page.evaluate(() => PortalI18n.set('en',{reload:false}));
  await page.reload();
  await page.getByRole('button',{name:'Show HATG',exact:true}).waitFor({state:'visible'});
  const showGraph=await nativeHatgShowControl(page);
  assert.equal(await showGraph.getAttribute('aria-label'),'Show HATG');
  assert.equal(await showGraph.isVisible(),true);
  await showGraph.click();
  const hatg = page.getByRole('region', {name:'HATG', exact:true});
  await hatg.waitFor({state:'visible'});
  assert.equal(await nativeHatgShowControl(page),null,'shown graph does not require another opening action');
  const section = page.locator('section#hatgPane');
  const oldRole = await section.getAttribute('role');
  await section.evaluate(node => node.removeAttribute('role'));
  assert.equal(await nativeHatgShowControl(page),null,'native section does not require an ARIA region');
  if (oldRole) await section.evaluate((node, role) => node.setAttribute('role', role), oldRole);
  const newNode = hatg.getByRole('button', {name:/^New node:/});
  await newNode.waitFor({state:'visible'});
  await page.waitForFunction(() => !document.getElementById('addNodeBtn')?.disabled);
  const searchAbsentId='resident_999999999999';
  await hatg.locator('#graphNodeSearchToggle').click();
  await hatg.locator('#graphNodeSearchInput').fill(searchAbsentId);
  await page.getByText('No matching nodes',{exact:true}).waitFor({state:'visible'});
  const {readSessionGraphSnapshot:readEmptySearch}=require('../../../management_portal/test_framework/tau_evidence');
  const absentSearch=await readEmptySearch(hatg,{residentId:searchAbsentId});
  assert.equal(absentSearch.unresolved_status_count,0,'a settled native empty result is not unresolved');
  assert.equal(absentSearch.search_empty_ready,true);
  await hatg.locator('#graphNodeSearchInput').fill('');

  // Both the Workbench host and its named SECTION are real region roots.
  // The latter has an implicit accessibility role and no literal role attribute.
  const {readSessionLineageIdentity, readSessionGraphSnapshot} = require('../../../management_portal/test_framework/tau_evidence');
  const {readHatgForkPrerequisites} = require('../../../management_portal/test_framework/tau_evidence');
  const {readWatchboardNodePrerequisites} = require('../../../management_portal/test_framework/tau_watchboard_node_prerequisites');
  const nativePane = hatg.locator('section#hatgPane[data-lineage-pane="hatg"]');
  assert.equal(await nativePane.getAttribute('role'), null);
  const {waitForHatgView} = require('../../../management_portal/test_framework/tau_evidence');
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
  assert.equal(await controls.nativeEntryDestination(newNode),null,
    'an undeclared popup cannot be inferred from the button label');
  await newNode.click();
  const form = page.getByRole('dialog', {name:'New node', exact:true});
  await form.getByRole('textbox', {name:'Agent name', exact:true}).waitFor({state:'visible'});
  assert.equal(await page.getByRole('menu').count(),0,'the New node primary button did not open a menu');
  await form.getByRole('button', {name:'Cancel', exact:true}).click();
  await form.waitFor({state:'hidden'});
  // Use the actual command palette. Its empty status disappears on results.
  await page.route('**/api/resources/reference?*', route=>route.fulfill({json:{
    resource_id:residentId,resource_type:'resident',title:'Fixture Resident',address:residentId,url:'/r/'+residentId}}));
  await page.keyboard.press('Control+k');
  const search=page.getByRole('dialog',{name:'Portal search',exact:true});
  await search.waitFor({state:'visible'});
  assert.ok((await controls.currentStatusTexts(search)).length>0);
  await search.getByRole('searchbox').fill(residentId);
  await search.getByRole('button').filter({hasText:residentId}).waitFor({state:'visible'});
  assert.deepEqual(await controls.currentStatusTexts(search),[]);
  await assert.rejects(search.getByRole('status').innerText({timeout:150}),/Timeout/);
  await page.keyboard.press('Escape');
  // Mount the production runtime dialog without invoking a restart or model.
  await page.evaluate(()=>{
    const api=PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml:PortalUIPrimitives.escapeHtml});
    window.sampleRuntime=api.createSessionRestartDialog('sample-runtime');
    sampleRuntime.open({source:{provider:'codex',account_id:'one'},providers:[{id:'codex'}],
      accounts:{codex:[{id:'one',label:'codex_one'},{id:'two',label:'codex_two',
        context_options:[{id:'preserve',enabled:true,description:'Preserve current context'}]}]}},
      async args=>{window.sampleRestartArguments=args;return {status:'completed',restart_id:'restart_fixture'};});
  });
  const select=page.locator('[data-session-target]');
  await select.waitFor({state:'visible'});
  const options=await controls.currentSelectOptions(select);
  const two=options.find(option=>option.label==='codex_two');
  assert.ok(two && !two.disabled);
  assert.notEqual(two.value,two.label);
  assert.equal(await select.locator('option').filter({hasText:'codex_two'}).isVisible(),false,
    'an available native select option does not require a visible popup');
  await select.selectOption({label:two.label});
  assert.equal(await select.inputValue(),two.value);
  const runtimeDialog=await controls.currentNativeDialog(page,'运行设置');
  assert.ok(runtimeDialog);
  assert.equal(await controls.nativeEntryDestination(runtimeDialog.locator('[data-session-restart-submit]')),null,
    'a submit button does not declare a new dialog');
  await runtimeDialog.locator('[data-session-restart-submit]').click();
  await runtimeDialog.waitFor({state:'hidden'});
  assert.equal(await controls.currentNativeDialog(page,'运行设置'),null,'completed switch closes its native dialog');
  assert.equal((await page.evaluate(()=>sampleRestartArguments)).target_account,'two');
  await assert.rejects(runtimeDialog.screenshot({timeout:150}),/Timeout/,'a closed dialog cannot supply the original screenshot');
  await page.evaluate(()=>sampleRuntime.destroy());
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
  const fork = hatg.getByRole('button', {name:'从「Renamed native fixture」完整上下文 Fork 新 Resident',exact:true});
  await fork.click();
  const children = visibleResidentChatRoots(page,{residentId:childId});
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
  await assert.rejects(uniqueResidentChat(page,childId), /Multiple native Chat Editors/);
  assert.deepEqual(new Set(discovered),new Set([originalEditor,'sample-second-editor']));
  assert.equal((await readResidentChatReady(children,{residentId:childId})).ready,false,'ambiguous Editors cannot become ready');
  await assert.rejects(children.waitFor({state:'visible',timeout:150}),/strict mode violation/,
    'the archived strict wait fails before it can enumerate Editors');
  assert.equal((await readResidentChatReady(page.locator('#'+originalEditor),{residentId:childId})).ready,true,
    'the original Fork-opened Editor is selected by its discovered DOM identity');
  const stableRoot = page.locator('#sample-second-editor');
  assert.equal(await stableRoot.getAttribute('role'),null,'native named SECTION uses an implicit accessibility role');
  const implicitChat=await readResidentChatReady(stableRoot,{residentId:childId});
  assert.equal(implicitChat.ready,true,'native Editor identity and structure do not require a literal role attribute');
  assert.equal(implicitChat.deletion_feedback_ready,false);
  await page.evaluate(()=>sampleSecondEditor.workspace.setMessages([{id:'msg_444444444444',
    message_id:'msg_444444444444',role:'agent',content:'Native implicit-region message'}]));
  await stableRoot.getByText('Native implicit-region message',{exact:true}).waitFor({state:'visible'});
  const {readSessionMessageCards}=require('../../../management_portal/test_framework/tau_evidence');
  const messageExample=prompt.match(/```js\n(\/\/ tau-single-message-card\n[\s\S]*?)\n```/);
  assert.ok(messageExample,'the plural helper name still requires one original messageId');
  const readDocumentedMessage=new Function('readSessionMessageCards','chat','residentId','messageId',
    'return (async()=>{'+messageExample[1]+'; return originalCard;})();');
  assert.equal((await readDocumentedMessage(readSessionMessageCards,stableRoot,childId,'msg_444444444444')).ready,true);
  await assert.rejects(readSessionMessageCards(stableRoot,{residentId:childId,messageIds:['msg_444444444444']}),/exact original/);
  assert.equal((await readSessionMessageCards(stableRoot,{residentId:childId,messageId:'msg_555555555555'})).ready,false);

  // Render real Resident message menus in both supported UI languages. The
  // open popover is portalled outside its article, but keeps its message key.
  const currentMessageQuoteAction=new Function('require','frameworkRoot',controlsExample[1]+'; return currentMessageQuoteAction;')(require,path.resolve(__dirname,'../../../management_portal/test_framework'));
  for (const language of ['zh-CN','en']) {
    await page.evaluate(language=>{
      PortalI18n.apply(language);
      sampleSecondEditor.workspace.setMessages([]);
      sampleSecondEditor.workspace.setMessages([{id:'msg_quote_sample',message_id:'msg_quote_sample',role:'agent',
        content:'Native quote sample 42'}]);
    },language);
    const message=stableRoot.locator('article.messageItem[data-message-key="msg_quote_sample"]');
    await message.getByText('Native quote sample 42',{exact:true}).click({button:'right'});
    const nativeQuote=page.locator('[data-portal-message-menu]:visible [data-portal-message-action="quote"]');
    await nativeQuote.waitFor({state:'visible'});
    assert.equal(await nativeQuote.innerText(),language==='zh-CN'?'引用回复':'Quote reply');
    if(language==='zh-CN') assert.equal(await page.getByRole('button',{name:'Quote reply',exact:true}).count(),0,
      'the old literal English readiness condition cannot find a Chinese menu');
    const quote=await currentMessageQuoteAction(page,{messageId:'msg_quote_sample'});
    assert.ok(quote);
    assert.equal(await quote.getAttribute('data-portal-message-action'),'quote');
    assert.equal(await currentMessageQuoteAction(page,{messageId:'msg_other'}),null);
    const {readResidentComposerQuote}=require('../../../management_portal/test_framework/tau_evidence');
    const quoteTarget={residentId:childId,messageId:'msg_quote_sample'};
    assert.equal((await readResidentComposerQuote(stableRoot,quoteTarget)).ready,false);
    await quote.click();
    const composed=await readResidentComposerQuote(stableRoot,quoteTarget);
    assert.equal(composed.ready,true,JSON.stringify(composed));
    assert.equal(await stableRoot.locator('[data-message-quote="msg_quote_sample"]').count(),0,
      'sent-message jump controls are not composer quote identity');
    assert.equal((await readResidentComposerQuote(stableRoot,{...quoteTarget,messageId:'msg_other'})).ready,false);
    assert.equal((await readResidentComposerQuote(stableRoot,{...quoteTarget,residentId})).ready,false);
    await stableRoot.locator('[data-composer-quote-cancel]').click();
    assert.equal((await readResidentComposerQuote(stableRoot,quoteTarget)).ready,false);
    assert.equal(await currentMessageQuoteAction(page,{messageId:'msg_quote_sample'}),null);
  }
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
  assert.equal(await stableRoot.getByRole('alert').count(),0,'native deletion feedback is a Portal message, not an alert');
  assert.equal(deleted.deletion_feedback_ready,true,JSON.stringify({deleted,dom:await stableRoot.evaluate(root=>({
    messages:[...root.querySelectorAll('.messageItem')].map(n=>({class:n.className,text:n.innerText})),
    send:[...root.querySelectorAll('button[aria-keyshortcuts]')].map(n=>({keys:n.getAttribute('aria-keyshortcuts'),disabled:n.disabled})),
  }))}));
  assert.equal((await readResidentChatReady(stableRoot,{residentId})).deletion_feedback_ready,false,
    'a deleted Chat does not establish feedback for another Resident');
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
  await require('./tau_native_workspace_directory.cjs')({page});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({status:'passed', formal_acceptance:false,
    samples:['native Chat without Session header', 'full Session via native Resident Menu', 'copy event identity',
      'direct form versus card menu and choice dialog', 'original quick Fork opens Chat',
      'discover duplicate native Editors before strict wait', 'relative has with native Family table'],
    local_requests:requests.length}));
};
