'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  const open=async()=>page.evaluate(async target=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
   window.PortalAPI={get:url=>fetch(url).then(response=>response.json())};
   window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};
   await PortalTemplateWorkshopLoader.load();
   window.original=await execute('GetLineage',{lineage_id:target.lineage_id});
   window.panel=PortalTemplateWorkshopLoader.create({root:document.querySelector('main.workshopStandaloneHost'),id:'restored-workshop',target:JSON.parse(localStorage.getItem('saved-family-workshop')||'null')||target,execute});
   await panel.connect();
   window.families=(await execute('ListTemplates')).templates.filter(t=>t.kind==='familize');
  },{kind:'workshop',lineage_id:process.env.WORKSHOP_TEST_LINEAGE,template_id:process.env.WORKSHOP_PREVIOUS_FAMILY_ID});
  await open();
  const current=await page.evaluate(()=>families.find(t=>t.builtin_key==='family').template_id);
  assert.notEqual(current,process.env.WORKSHOP_PREVIOUS_FAMILY_ID);
  assert.equal(await page.evaluate(()=>families.filter(t=>t.builtin_key).length),1);
  assert.equal(await page.locator('[data-toolbox-group="organizations"] .templateTile').count(),1);
  assert.equal(await page.locator('.templateCompatibility').count(),0);
  assert.equal(await page.evaluate(()=>panel.target().template_id),current);
  assert.equal(await page.evaluate(()=>panel.snapshot().template_id),current);
  assert.equal(await page.evaluate(()=>panel.canClose()),true);
  await page.evaluate(old=>localStorage.setItem('saved-family-workshop',JSON.stringify({...panel.snapshot(),template_id:old})),process.env.WORKSHOP_PREVIOUS_FAMILY_ID);
  await page.reload();await open();
  assert.equal(await page.evaluate(()=>panel.target().template_id),current);
  assert.equal(await page.locator('.templateWorkshop').count(),1);
  assert.equal(await page.evaluate(()=>panel.canClose()),true);
  assert.equal(await page.evaluate(()=>execute('GetLineage',{lineage_id:panel.target().lineage_id}).then(result=>result.lineage.revision===original.lineage.revision)),true);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
