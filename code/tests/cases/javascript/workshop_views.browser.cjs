'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
   window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};
   await PortalTemplateWorkshopLoader.load();window.lid=lineageId;
   let old=(await execute('ListTemplates')).templates.find(t=>t.builtin_key==='family');window.legacyId=old.template_id;
   delete old.body.role_templates;old.body.goal='Keep custom goal';
   await execute('UpdateTemplate',{template_id:old.template_id,template:{body:old.body}});
   window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId,execute});await workshop.load();
   window.catalog=(await execute('ListTemplates')).templates;
  },process.env.WORKSHOP_TEST_LINEAGE);
  const main=await page.evaluate(()=>catalog.find(t=>t.catalog_entry?.key==='family').template_id);
  const group=page.locator('[data-toolbox-group="organizations"]');
  assert.equal(await group.locator('.templateTile').count(),1);
  await group.locator('.templateTile').click();await page.waitForFunction(()=>workshop.template().kind==='familize');
  const baseline=await page.evaluate(()=>JSON.stringify(workshop.template().body));
  for(const action of ['zoom-in','zoom-out','reset','fit'])await page.locator(`[data-graph-${action}]`).click();
  await page.locator('[data-graph-reset]').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('[data-graph-zoom]').textContent==='100%');
  assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  assert.equal(await page.evaluate(()=>JSON.stringify(workshop.template().body)),baseline);
  // Independent component camera and empty graph safety.
  await page.evaluate(()=>{const host=document.body.appendChild(document.createElement('div'));host.style='position:fixed;right:0;bottom:0;width:300px;height:220px';window.other=LineageTemplateGraph.mount({root:host,body:()=>({roles:[],components:[],wires:[],match:[]}),workshop:true});});
  const before=await page.locator('#templateWorkshop .hatgCanvas').getAttribute('data-zoom');
  await page.locator('body > div .templateGraphNavigation [data-graph-zoom-in]').last().click();
  assert.equal(await page.locator('#templateWorkshop .hatgCanvas').getAttribute('data-zoom'),before);
  await page.evaluate(()=>other.destroy());
  await group.locator('summary').click();await group.locator('[data-compat-open]').click();
  await page.waitForFunction(()=>workshop.template().template_id===legacyId);
  assert.equal(await page.evaluate(()=>workshop.template().body.goal),'Keep custom goal');
  assert.equal(await page.evaluate(()=>workshop.dirty()),false);
  await group.locator('.templateTile').click();await page.waitForFunction(id=>workshop.template().template_id===id,main);
  const folder='/tmp/workshop-views-evidence';fs.mkdirSync(folder,{recursive:true});
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
   await page.setViewportSize({width,height:1000});await page.evaluate(t=>PortalTheme.setMode(t),theme);await page.locator('[data-graph-fit]').click();await page.waitForTimeout(250);await page.screenshot({path:`${folder}/${theme}-${width}.png`});
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
