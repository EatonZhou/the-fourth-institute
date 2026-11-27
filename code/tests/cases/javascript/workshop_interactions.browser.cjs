'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},hasTouch:true});
 page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const folder='/tmp/workshop-interactions-evidence';fs.mkdirSync(folder,{recursive:true});
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};window.PortalAPI={get:path=>fetch(path).then(r=>r.json())};window.calls=[];
   window.execute=async(instruction,args={},options={})=>{calls.push({instruction,args});const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};
   await PortalTemplateWorkshopLoader.load();window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId,execute});await workshop.load();
   window.catalog=(await execute('ListTemplates')).templates;window.router=catalog.find(t=>t.builtin_key==='family_role_router');window.steward=catalog.find(t=>t.builtin_key==='family_role_steward');window.family=catalog.find(t=>t.builtin_key==='family');
  },process.env.WORKSHOP_TEST_LINEAGE);
  const root=page.locator('#templateWorkshop'),info=root.locator('.workshopInfoOverlay'),menu=root.locator('.templateResidentOverlay'),summary=root.locator('.templateDutySummary:visible');
  const closeInfo=()=>info.locator('[data-close-template-info]').click();
  if(process.env.WORKSHOP_R27_VISUAL_ONLY==='1'){
   await page.screenshot({path:`${folder}/blank-draft-entry.png`});
   await page.evaluate(()=>workshop.openTemplate(router.template_id));const owner=root.locator('.hatgNode[data-node-id="router_resident"]');await owner.hover();await summary.waitFor({state:'visible'});await page.screenshot({path:`${folder}/router-duty-hover.png`});
   await owner.dblclick();await menu.waitFor({state:'visible'});await page.screenshot({path:`${folder}/router-duty-menu.png`});return;
  }
  assert.equal(await root.locator('.workshopInfo').count(),0);await root.locator('[data-workshop-info]').click();await info.waitFor({state:'visible'});assert.equal(await page.evaluate(()=>workshop.template().template_id||''),'');assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await info.locator('[data-meta="description"]').fill('Unsent draft information');await closeInfo();assert.equal(await page.evaluate(()=>workshop.dirty()),true);await root.locator('[data-workshop-history="undo"]').click();assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await root.locator('[data-template-id="workshop_new"]').click();await root.locator('.hatgNode[data-node-id]').hover();await summary.waitFor({state:'visible'});assert.match(await summary.innerText(),/暂无职责/);await root.locator('[data-workshop-history="undo"]').click();assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  // Double click joins the protected single-click selection, including cancellation.
  await page.evaluate(()=>{window.confirmations=0;window.confirmOriginal=PortalUI.confirm;PortalUI.confirm=async()=>{confirmations++;return false;};});
  await root.locator('[data-meta="title"]').fill('Unsaved blank draft');
  const routerId=await page.evaluate(()=>router.template_id),stewardId=await page.evaluate(()=>steward.template_id),familyId=await page.evaluate(()=>family.template_id);
  const tile=id=>root.locator(`[data-workshop-library] [data-template-id="${id}"]`);
  await tile(routerId).dblclick();await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>confirmations),1);assert.equal(await info.isVisible(),false);assert.equal(await page.evaluate(()=>workshop.template().kind),'structure');
  await page.evaluate(()=>PortalUI.confirm=confirmOriginal);await root.locator('[data-workshop-history="undo"]').click();
  await tile(routerId).dblclick();await info.waitFor({state:'visible'});assert.equal(await page.evaluate(()=>workshop.template().template_id),routerId);assert.equal(await page.evaluate(()=>calls.filter(c=>c.instruction==='GetTemplate'&&c.args.template_id===router.template_id).length),1);await closeInfo();
  const node=key=>root.locator(`.hatgNode[data-node-id="${key}_resident"]`);
  await node('router').hover();await summary.waitFor({state:'visible'});assert.deepEqual(await summary.locator('li').allTextContents(),['门面收到消息后，将消息路由到最合适的成员处']);await summary.hover();assert.equal(await summary.isVisible(),true);assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await summary.locator('[data-summary-menu]').click();await menu.waitFor({state:'visible'});await menu.locator('.residentMenuDuty summary').click();await menu.locator('[data-template-duty][data-duty-field="title"]').fill('收到原请求后，为当前任务选择合适的成员');await menu.locator('[data-menu-close]').click();
  await node('router').hover();await summary.waitFor({state:'visible'});assert.deepEqual(await summary.locator('li').allTextContents(),['收到原请求后，为当前任务选择合适的成员']);await root.locator('[data-workshop-history="undo"]').click();await node('router').hover();await summary.waitFor({state:'visible'});assert.match(await summary.innerText(),/门面收到消息后/);await root.locator('[data-workshop-history="redo"]').click();await node('router').hover();await summary.waitFor({state:'visible'});assert.match(await summary.innerText(),/为当前任务/);
  await root.locator('[data-action="copy"]').click();await page.waitForFunction(()=>!workshop.template().builtin_key);const ownId=await page.evaluate(()=>workshop.template().template_id);assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  // Metadata controls are the original draft and retain failed saves/history.
  await root.locator('[data-workshop-info]').click();await info.locator('[data-meta="description"]').fill('Metadata through shared draft');await closeInfo();
  await page.evaluate(()=>{window.realExecute=execute;execute=async(name,...args)=>{if(name==='UpdateTemplate')throw Error('Deliberate metadata save failure');return realExecute(name,...args);};});
  // The mounted request closure calls the original function, so inject failure at transport.
  await page.route('**/rpc',async route=>{const data=route.request().postDataJSON();if(data.instruction==='UpdateTemplate')await route.fulfill({json:{error:{message:'Deliberate metadata save failure'}}});else await route.continue();});
  await root.locator('[data-workshop-save]').click();await page.waitForFunction(()=>document.querySelector('[data-workshop-id="workshopError"],#workshopError').textContent.includes('Deliberate'));assert.equal(await page.evaluate(()=>workshop.dirty()),true);await page.unroute('**/rpc');await page.evaluate(()=>execute=realExecute);
  await root.locator('[data-workshop-save]').click();await page.waitForFunction(()=>!workshop.dirty());assert.equal(await page.evaluate(id=>execute('GetTemplate',{template_id:id}).then(r=>r.template.description),ownId),'Metadata through shared draft');
  // A late selection cannot open information over the newer resource.
  await page.evaluate(()=>{window.pendingTemplate=null;window.fetchOriginal=fetch;window.fetch=async(input,options)=>{if(String(input)==='/rpc'&&options){const data=JSON.parse(options.body);if(data.instruction==='GetTemplate'&&data.arguments.template_id===steward.template_id)await new Promise(resolve=>pendingTemplate=resolve);}return fetchOriginal(input,options);};});
  await tile(stewardId).dblclick();await page.waitForFunction(()=>pendingTemplate);await tile(familyId).click();await page.waitForFunction(()=>workshop.template().template_id===family.template_id);await page.evaluate(()=>{pendingTemplate();window.fetch=fetchOriginal;});await page.waitForTimeout(180);assert.equal(await info.isVisible(),false);assert.equal(await page.evaluate(()=>workshop.template().template_id),familyId);
  await node('steward').hover();await summary.waitFor({state:'visible'});assert.deepEqual(await summary.locator('li').allTextContents(),await page.evaluate(()=>steward.body.duties.map(d=>d.title)));await node('steward').focus();assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await page.evaluate(()=>workshop.openTemplate(steward.template_id));await node('steward').tap();await root.locator('[data-template-resident-menu="steward_resident"]').tap();await menu.waitFor({state:'visible'});assert.deepEqual(await menu.locator('.residentMenuDutyText').allTextContents(),await page.evaluate(()=>steward.body.duties.map(d=>d.title)));await menu.locator('[data-menu-close]').click();
  await node('steward').dblclick();await menu.waitFor({state:'visible'});await menu.locator('[data-menu-close]').click();await page.keyboard.press('Tab');await node('steward').focus();await summary.waitFor({state:'visible'});
  await page.evaluate(async()=>{const host=document.createElement('section');host.id='rSibling';host.style='position:fixed;right:0;top:0;width:500px;height:800px;z-index:220;background:var(--panel)';document.body.append(host);window.rSibling=PortalTemplateWorkshopLoader.create({root:host,id:'r-sibling',target:{template_id:router.template_id},execute});await rSibling.show();});
  const sibling=page.locator('#rSibling');await sibling.locator('.hatgNode[data-node-id="router_resident"]').hover();await sibling.locator('.templateDutySummary:visible').waitFor();assert.deepEqual(await sibling.locator('.templateDutySummary:visible li').allTextContents(),['门面收到消息后，将消息路由到最合适的成员处']);await sibling.locator('[data-workshop-info]').click();assert.equal(await info.isVisible(),false);await sibling.locator('[data-close-template-info]').click();await page.evaluate(()=>{rSibling.destroy();document.getElementById('rSibling').remove();});
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
   await page.setViewportSize({width,height:1000});await page.evaluate(theme=>PortalTheme.setMode(theme),theme);await node('steward').hover();await summary.waitFor({state:'visible'});await page.screenshot({path:`${folder}/duties-${theme}-${width}.png`});await tile(stewardId).dblclick();await info.waitFor({state:'visible'});await page.screenshot({path:`${folder}/information-${theme}-${width}.png`});await closeInfo();
  }
  assert.equal(await page.evaluate(()=>workshop.dirty()),false);assert.equal(await root.locator('.workshopInfo').count(),0);
  assert.equal(await page.evaluate(()=>calls.some(c=>['GetResident','GetResidentSettings','GetPromptInjectionPreview'].includes(c.instruction))),false);assert.deepEqual(errors,[]);
 }catch(error){await page.screenshot({path:`${folder}/failure.png`});console.error(await page.locator('#workshopError').textContent().catch(()=>''));throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
