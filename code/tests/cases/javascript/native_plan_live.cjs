'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const {PORTAL_COMPONENTS_STYLES} = require('./portal_components_styles.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(source,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const snapshots = input.snapshots;
const routeScript = input.routeScript;
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:820}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>route.request().url()==='https://plan.fixture.invalid/' ? route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><main id="fixture" style="width:850px;height:700px"></main></body></html>'}) : route.abort());
  await page.goto('https://plan.fixture.invalid/');
  await page.evaluate(()=>{
   window.PortalIdentity={username:()=> 'fixture'};
   window.PortalEvents={isConnected:()=>true,connect(){}};
   window.PortalAPI={get:async url=>{throw new Error('Unexpected API '+url);}};
   window.PortalInstructionClient={execute:async name=>{throw new Error('Unexpected instruction '+name);}};
  });
  await page.addScriptTag({content:routeScript});
  for(const name of ['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_preview_contract.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(),'portal_resident_service.js','resident_protocol.js',...chatWorkspaceScripts()]) {
   await page.addScriptTag({content:fs.readFileSync(path.join(source,'management_portal/static',name),'utf8')});
  }
  await page.addStyleTag({content:PORTAL_COMPONENTS_STYLES.map(name=>fs.readFileSync(path.join(source,'management_portal/static',name+'.css'),'utf8')).join('\n')});
  await page.evaluate(()=>{
   window.fixtureWorkspace=PortalChatWorkspace.createWorkspace({root:document.getElementById('fixture')});
   fixtureWorkspace.setMessages([], {runtimeLive:true});
  });
  const ids=[];
  for(const [index,snapshot] of snapshots.entries()) {
   await page.evaluate(details=>fixtureWorkspace.setRuntime(details,true),snapshot.runtime_details);
   const card=page.locator('.nativePlanLive .nativeTurnCard');
   assert.equal(await card.count(),1);
   assert.equal(await card.isVisible(),true);
   assert.equal(await card.locator('li').getAttribute('data-step-status'),['pending','inProgress','completed'][index]);
   assert.match(await card.locator('li').textContent(),/Check <fixture>/);
   assert.equal(await card.locator('fixture').count(),0,'step text is escaped');
   assert.equal(await card.locator('[data-turn-review]').count(),0,'live plans cannot initiate final review');
   assert.equal(await card.evaluate(el=>!!el.closest('details:not([open])')),false,'plan is outside collapsed activity');
   ids.push(await card.getAttribute('data-native-turn'));
  }
  assert.equal(new Set(ids).size,1,'every update uses one stable identity');
  await page.evaluate(details=>fixtureWorkspace.setMessages([], {runtimeDetails:details,runtimeLive:true}),snapshots[1].runtime_details);
  assert.equal(await page.locator('.nativePlanLive li').getAttribute('data-step-status'),'inProgress','restored runtime snapshot renders before another event');
  await page.evaluate(()=>fixtureWorkspace.setRuntime([],true));
  assert.equal(await page.locator('.nativePlanLive').count(),0,'final artifact ends live card');
  const part=input.finalMessage.content_parts[0];
  part.message_id=input.finalMessage.id;
  await page.evaluate(part=>fixtureWorkspace.setMessages([{id:part.message_id,role:'agent',content_parts:[part]}],{
   runtimeDetails:[],runtimeLive:false,renderMessageBody:(item,context)=>PortalChatWorkspace.residentMessageBody(item,context),
  }),part);
  const finalCard=page.locator('.nativeTurnCard');
  assert.equal(await finalCard.count(),1);
  assert.equal(await finalCard.getAttribute('data-native-turn'),ids[0]);
  assert.equal(await finalCard.locator('[data-turn-review]').count(),1,'final Diff keeps its review affordance');
  assert.equal(await finalCard.locator('[data-turn-diff]').textContent(),'+actual change');
  assert.equal(await finalCard.locator('.messageSystemBadge').textContent(),'本轮已结束');
  await page.evaluate(details=>fixtureWorkspace.setRuntime(details,true),snapshots[0].runtime_details);
  assert.equal(await page.locator('.nativePlanLive').count(),0,'a final message wins over a stale live snapshot');
  await page.evaluate(({message, details})=>fixtureWorkspace.setMessages([message], {runtimeDetails:details,runtimeLive:true,force:true}),{message:input.finalMessage,details:snapshots[0].runtime_details});
  assert.equal(await page.locator('.nativePlanLive').count(),0,'a fresh draw also prefers the final message');
  await page.evaluate(details=>fixtureWorkspace.setRuntime(details,false),snapshots[0].runtime_details);
  assert.equal(await page.locator('.nativePlanLive').count(),0,'inactive runtime cannot resurrect a live plan');
  for (const [status, label] of [['completed','本轮已结束'], ['failed','本轮失败'], ['interrupted','本轮已中断'], ['cancelled','本轮已取消']]) {
   const steps = ['completed','inProgress','pending'].map(status => ({step:'Step '+status, status}));
   const ended = {...part, status, plan:steps, explanation:'Draft ready; registration blocked; import awaits approval.'};
   await page.evaluate(part=>fixtureWorkspace.setMessages([{id:part.message_id,role:'agent',content_parts:[part]}],{
    runtimeLive:true, runtimeDetails:[], force:true,
    renderMessageBody:(item,context)=>PortalChatWorkspace.residentMessageBody(item,context),
   }),ended);
   assert.equal(await finalCard.locator('.messageSystemBadge').textContent(),label);
   assert.deepEqual(await finalCard.locator('li > span').allTextContents(),['已完成','未完成','未执行']);
   assert.deepEqual(await finalCard.locator('li').evaluateAll(nodes=>nodes.map(node=>node.dataset.stepStatus)),['completed','inProgress','pending']);
   assert.match(await finalCard.textContent(),/registration blocked; import awaits approval/);
   assert.equal(await finalCard.getAttribute('data-turn-ended'),'true','other live work cannot reactivate a historical plan');
  }
  await page.evaluate(()=>fixtureWorkspace.destroy());
  assert.deepEqual(errors,[]);
  console.log('Real Chromium: projected plan snapshots remain visible, update in place, restore before new events, end without duplicates and retain final Diff/Review identity. No service actions.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
