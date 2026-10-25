'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const codeRoot=process.env.TFI_TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(codeRoot,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWorkbenchTabClose}=require(path.join(codeRoot,'management_portal/test_framework/tau_tab_close_evidence'));
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1100,height:800}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
    await page.setContent('<main id="fixture"><section id="localPanel">Local panel content</section></main>');
    await page.addStyleTag({content:fs.readFileSync(path.join(codeRoot,'management_portal/static/portal_workbench.css'),'utf8')});
    await page.addStyleTag({content:'#fixture{width:700px;height:400px;margin:100px 50px} :root{--portal-workbench-single-tab-opacity:0;--portal-workbench-single-tab-revealed-opacity:1}'});
    await page.evaluate(()=>{window.PortalIdentity={username:()=> 'fixture'};});
    for(const name of ['portal_text.js','portal_ui.js','portal_ui_primitives.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js','portal_workbench.js'])
      await page.addScriptTag({content:fs.readFileSync(path.join(codeRoot,'management_portal/static',name),'utf8')});
    await page.evaluate(()=>{
      window.PortalWorkbench.registerPanelPackage({type:'local-test',version:1,presentation:{title:'Local',icon:'panel'},sizing:{minWidth:180,minHeight:140},instances:'multiple',capabilities:[],behavior:{canHide:true,canCollapse:true,canMove:true,closable:true},context:{channels:['*']},persistence:{schemaVersion:1},create:()=>document.getElementById('localPanel')});
      const {stack}=window.PortalWorkbench.model;
      window.testWorkbench=window.PortalWorkbench.create({id:'fixture',root:document.getElementById('fixture'),initialEditors:[{id:'owned',type:'local-test',title:'Owned'}],defaultLayout:stack(['owned']),store:{read:()=>({}),replace:value=>value}});
    });
    const tab=page.locator('[data-portal-workbench-tab="owned"]');
    const hidden=await readWorkbenchTabClose(tab,{panelId:'owned'});assert.equal(hidden.ready,false);
    await assert.rejects(readWorkbenchTabClose(tab,{panelId:'other'}),/Wrong native/);
    await tab.focus(); // Native focus reveals the strip through its real binding/CSS.
    const moving=await readWorkbenchTabClose(tab,{panelId:'owned'});
    if(moving.samples.some(s=>s.animating))assert.equal(moving.ready,false);
    await page.waitForFunction(()=>document.querySelector('.portalWorkbenchTabs').getAnimations({subtree:true}).every(a=>a.playState!=='running'&&!a.pending));
    const ready=await readWorkbenchTabClose(tab,{panelId:'owned'});assert.equal(ready.ready,true);
    await page.evaluate(()=>{const blocker=document.createElement('div');blocker.id='blocker';blocker.style.cssText='position:fixed;inset:0;z-index:2147483647';document.body.append(blocker);});
    const blocked=await readWorkbenchTabClose(tab,{panelId:'owned'});assert.equal(blocked.ready,false);assert.equal(blocked.samples[1].center_hittable,false);
    await page.locator('#blocker').evaluate(n=>n.remove());
    assert.equal(await tab.count(),1,'Passive samples never close the original panel');
    await page.locator('.portalWorkbenchTabClose').click();
    assert.equal(await tab.count(),0,'One native close click closes the owned local panel');
    await page.evaluate(()=>window.testWorkbench.destroy());
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    console.log(JSON.stringify({status:'passed',hidden,moving,ready,blocked,production_requests:0,formal_acceptance:false}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
