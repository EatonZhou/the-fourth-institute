'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},hasTouch:true});
 const errors=[],folder='/tmp/workshop-v29-evidence';fs.mkdirSync(folder,{recursive:true});page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&m.text().includes('cannot be parsed'))errors.push(m.text());});
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
   window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};
   await PortalTemplateWorkshopLoader.load();window.graphViews={};window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId,execute,graphViews});await workshop.load();window.catalog=(await execute('ListTemplates')).templates;
  },process.env.WORKSHOP_TEST_LINEAGE);
  const root=page.locator('#templateWorkshop');
  assert.equal(await root.locator('[data-workshop-save]').isDisabled(),true);assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  const open=async key=>page.evaluate(key=>workshop.openTemplate(catalog.find(t=>t.builtin_key===key).template_id),key);
  await open('agent_loop');const baseline=await page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template())));
  for(const mode of ['list','json','graph']){await root.locator(`[data-structure-view="${mode}"]`).click();assert.equal(await page.evaluate(()=>workshop.dirty()),false);}
  assert.equal(await page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template()))),baseline);
  assert.equal(await root.locator('[data-toolbox-group="organizations"] .templateTile').count(),2);
  await root.locator('[data-graph-arrange]').click();assert.equal(await page.evaluate(()=>workshop.dirty()),true);await root.locator('[data-workshop-history="undo"]').click();assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await root.locator('[data-node-id="rounds"]').click();assert.equal(await root.locator('[data-template-token]').first().inputValue(),'{{param.max_rounds}}');
  const inspector=await root.locator('[data-graph-inspector]').boundingBox();assert.ok(inspector.y+inspector.height<=1000);
  const assistant=root.locator('[data-workshop-assistant]');await assistant.hover();await page.waitForTimeout(300);await assistant.click();assert.equal(await assistant.getAttribute('aria-expanded'),'true');await assistant.press('Escape');
  await open('family_role_router');const content=await page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template())));
  await root.locator('[data-recipe-expand]').click();assert.equal(await root.locator('[data-recipe-expand]').getAttribute('aria-pressed'),'true');
  const node=root.locator('[data-node-id="router_resident"]');await root.locator('[data-graph-reset]').click();
  // Exercise the real graph drag callbacks, then a render and a Menu cycle.
  let box=await node.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+65,box.y+box.height/2-50,{steps:12});await page.mouse.up();
  const saved=await page.evaluate(()=>JSON.stringify(graphViews[workshop.template().template_id].positions));
  await node.click();await node.press('Enter');await root.locator('.templateResidentOverlay [data-menu-close]').click();
  assert.equal(await page.evaluate(()=>JSON.stringify(graphViews[workshop.template().template_id].positions)),saved);assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  assert.equal(await page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template()))),content);
  await open('family');await root.locator('[data-organization-menu]').click();
  assert.match(await root.locator('.templateResidentOverlay').innerText(),/摘要.*登记|登记工作摘要/);
  assert.match(await root.locator('.templateResidentOverlay').innerText(),/接单|承接/);await root.locator('.templateResidentOverlay [data-menu-close]').click();
  await open('expand_ideas');assert.equal(await root.locator('.workshopTools').isVisible(),false);assert.equal(await root.locator('[data-body-field="count"]').evaluate(n=>n.tagName),'INPUT');
  await open('family_role_router');if(await root.locator('[data-recipe-expand]').getAttribute('aria-expanded')!=='true')await root.locator('[data-recipe-expand]').click();
  await root.locator('[data-graph-arrange]').click();
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
   await page.setViewportSize({width,height:1000});await page.evaluate(t=>PortalTheme.setMode(t),theme);await root.locator('[data-graph-fit]').click();await page.mouse.move(3,3);await page.waitForTimeout(250);
   assert.ok(Number(await root.locator('.hatgCanvas').getAttribute('data-zoom'))>=.6);await page.screenshot({path:`${folder}/router-${theme}-${width}.png`});
  }
  assert.deepEqual(errors,[]);
 }catch(e){await page.screenshot({path:`${folder}/failure.png`});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
