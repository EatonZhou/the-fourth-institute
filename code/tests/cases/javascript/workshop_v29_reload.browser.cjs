'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:600,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.addInitScript(lineageId=>document.addEventListener('DOMContentLoaded',()=>{
   window.boot=(async()=>{
    PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',peek:()=>({}),userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
    window.PortalAPI={get:url=>fetch(url).then(r=>r.json())};window.PortalNav={personalAssistantTarget:async()=>null,loadChatWorkspace:async()=>{}};
    window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};
    window.PortalInstructionClient={execute};await PortalHatgLoader.load();await PortalTemplateWorkshopLoader.load();
    document.body.innerHTML='<main id="cards" style="height:100vh"></main>';
    PortalWorkbench.registerPanelPackage({type:'v29-source',version:1,presentation:{title:'Source',icon:'network'},sizing:{minWidth:100,minHeight:100},instances:'single',capabilities:[],behavior:{canHide:true,canMove:true,canCollapse:true},context:{channels:[]},persistence:{schemaVersion:1},create:({root})=>root});
    const deps=Object.fromEntries(PortalLineageWorkbenchEditors.dependencies.map(k=>[k,()=>{}]));
    const editors=PortalLineageWorkbenchEditors.createEditors({...deps,state:{chatTarget:{lineage_id:lineageId}},lineageSidebarRoots:new Map()});
    window.cards=PortalWorkbench.create({id:'v29-reload',root:'#cards',initialEditors:[{id:'source',type:'v29-source'}],defaultLayout:PortalWorkbench.model.stack(['source']),editorTypes:editors.lineageEditorTypes,createEditorDescriptor:editors.lineageEditorDescriptor,restorePanel:s=>editors.lineageEditorDescriptor({instanceId:s.id,type:s.type,resource:s.binding}),onPanelEvent:({event,sourcePanelId,detail},controller)=>{if(['workshop-title','workshop-saved'].includes(event))controller.setPanelTitle(sourcePanelId,detail.title);}});
    await Promise.all(cards.getPanelsByType('workshop').map(p=>p.ready));window.bootDone=true;
   })().catch(e=>{window.bootError=e.stack;});
  }),process.env.WORKSHOP_TEST_LINEAGE);
  const boot=async()=>{await page.waitForFunction(()=>window.bootDone||window.bootError);assert.equal(await page.evaluate(()=>window.bootError),undefined);};
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);await boot();
  const id=await page.evaluate(async()=>{const router=(await execute('ListTemplates')).templates.find(t=>t.builtin_key==='family_role_router');const opened=await PortalTemplateWorkshopLoader.openInWorkbench(cards,{template_id:router.template_id},'source');await cards.getPanel(opened.instanceId).ready;return opened.instanceId;});
  const root=page.locator(`[data-portal-workbench-panel="${id}"]`);await root.locator('[data-recipe-expand]').click();
  const node=root.locator('[data-node-id="router_resident"]');const b=await node.boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+30,b.y+b.height/2+30,{steps:10});await page.mouse.up();
  const before=await page.evaluate(id=>cards.getPanel(id).snapshot(),id);
  assert.ok(Object.values(before.graphViews).some(s=>s.positions.router_resident));assert.equal(before.dirty,false);
  await page.reload();await boot();
  const after=await page.evaluate(id=>cards.getPanel(id).snapshot(),id);assert.equal(after.template_id,before.template_id);assert.deepEqual(after.graphViews[before.template_id].positions,before.graphViews[before.template_id].positions);assert.equal(after.dirty,false);
  await page.evaluate(()=>PortalTemplateWorkshopLoader.openInWorkbench(cards,{},'source'));assert.equal(await page.evaluate(()=>cards.getPanelsByType('workshop').length),1);
  await page.evaluate(()=>PortalTemplateWorkshopLoader.openInWorkbench(cards,{newDraft:true},'source'));assert.equal(await page.evaluate(()=>cards.getPanelsByType('workshop').length),2);
  // Unedited view changes never replace the existing refresh protection.
  const current=page.locator('.workshopPanelHost:visible [data-meta="title"]').last();const initial=await current.inputValue();await current.fill('尚未保存的正文');let prompted=false;
  page.once('dialog',async d=>{assert.equal(d.type(),'beforeunload');prompted=true;await d.dismiss();});await page.reload().catch(()=>{});assert.equal(prompted,true);assert.equal(await current.inputValue(),'尚未保存的正文');await current.fill(initial);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
