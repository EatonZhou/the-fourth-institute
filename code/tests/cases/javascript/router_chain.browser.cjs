'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},hasTouch:true});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
 const folder='/tmp/router-chain-evidence';fs.mkdirSync(folder,{recursive:true});
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};window.PortalAPI={get:path=>fetch(path).then(r=>r.json())};window.calls=[];
   window.execute=async(instruction,args={},options={})=>{calls.push(instruction);const data=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(data.error)throw Error(data.error.message);return data.result;};
   await PortalTemplateWorkshopLoader.load();window.workshop=LineageTemplateWorkshop.mount({root:document.getElementById('templateWorkshop'),lineageId,execute});await workshop.load();
   const list=(await execute('ListTemplates')).templates;window.family=list.find(t=>t.builtin_key==='family');window.router=list.find(t=>t.builtin_key==='family_role_router');await workshop.openTemplate(family.template_id);
  },process.env.WORKSHOP_TEST_LINEAGE);
  const root=page.locator('#templateWorkshop'),menu=root.locator('.templateResidentOverlay'),toolbar=root.locator('[data-recipe-expand]');
  const open=async key=>{await root.locator(`[data-template-resident-menu="${key}_resident"]`).click();await menu.waitFor({state:'visible'});};
  const clean=async()=>{assert.equal(await page.evaluate(()=>workshop.dirty()),false);assert.equal(await root.locator('[data-workshop-history="undo"]').isDisabled(),true);assert.equal(await root.locator('[data-workshop-history="redo"]').isDisabled(),true);};
  await open('router');assert.equal(await menu.locator('[data-template-menu-expand]').innerText(),'展开内部结构');await menu.locator('[data-template-menu-expand]').click();
  const collapse=key=>root.locator(`[data-collapse-role="${key}_resident"]`);
  await collapse('router').waitFor({state:'visible'});assert.equal(await toolbar.getAttribute('aria-pressed'),'mixed');await clean();
  assert.equal(await root.locator('.hatgNode[data-node-id="route_relay"]').isVisible(),true);
  await page.waitForTimeout(400);await page.screenshot({path:`${folder}/family-router-expanded.png`});await collapse('router').tap();assert.equal(await collapse('router').count(),0);await clean();
  await open('steward');await menu.locator('[data-template-menu-expand]').click();await open('router');await menu.locator('[data-template-menu-expand]').click();await open('router');assert.equal(await menu.locator('[data-template-menu-expand]').innerText(),'收起内部结构');await menu.locator('[data-template-menu-expand]').click();assert.equal(await collapse('steward').isVisible(),true);await clean();
  await toolbar.click();assert.equal(await toolbar.getAttribute('aria-expanded'),'true');await collapse('router').waitFor({state:'visible'});await toolbar.click();assert.equal(await toolbar.getAttribute('aria-expanded'),'false');assert.equal(await root.locator('[data-collapse-role]').count(),0);await clean();
  await page.evaluate(()=>workshop.openTemplate(router.template_id));
  const graph=await page.evaluate(()=>LineageTemplateGraph.project(workshop.template().editor.graph.body,{workshop:true}));
  assert.ok(graph.edges.some(edge=>edge.source==='facade_resident'&&edge.target==='router_resident'));
  assert.ok(graph.edges.some(edge=>edge.source==='router_resident'&&edge.target==='member_resident'&&edge.label==='转交所选成员'));
  assert.equal(await root.locator('.templateBinding[data-binding-key="family"],.templateBinding[data-binding-key="member_done"]').count(),0);
  await page.mouse.move(5,5);await page.waitForTimeout(400);await page.screenshot({path:`${folder}/router-complete-collapsed.png`});
  await open('router');await menu.locator('[data-template-menu-expand]').click();await collapse('router').waitFor({state:'visible'});
  assert.equal(await root.locator('.hatgNode[data-node-id="router"]').isVisible(),true);assert.equal(await root.locator('.hatgNode[data-node-id="route_relay"]').isVisible(),true);
  await page.evaluate(()=>{window.beforeBody=JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template()));});
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
   await page.setViewportSize({width,height:1000});await page.evaluate(theme=>PortalTheme.setMode(theme),theme);await root.locator('[data-graph-fit]').click();await page.mouse.move(5,5);await page.waitForTimeout(400);await page.screenshot({path:`${folder}/router-expanded-${theme}-${width}.png`});
  }
  await collapse('router').focus();await collapse('router').press('Enter');await clean();assert.equal(await page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(workshop.template()))===beforeBody),true);
  await open('router');await menu.locator('[data-template-menu-expand]').click();
  await page.evaluate(async()=>{const host=document.createElement('section');host.id='chainSibling';host.style='position:fixed;right:0;top:0;width:450px;height:800px;z-index:220;background:var(--panel)';document.body.append(host);window.sibling=PortalTemplateWorkshopLoader.create({root:host,id:'chain-sibling',target:{template_id:family.template_id},execute});await sibling.show();});
  assert.equal(await page.locator('#chainSibling [data-collapse-role]').count(),0);assert.equal(await collapse('router').count(),1);await page.evaluate(()=>{sibling.destroy();document.getElementById('chainSibling').remove();});await clean();
  assert.equal(await page.evaluate(()=>calls.some(name=>['CreateTemplate','UpdateTemplate','ApplyTemplate','ApplyLineageChanges'].includes(name))),false);assert.deepEqual(errors,[]);
 }catch(error){await page.screenshot({path:`${folder}/failure.png`});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
