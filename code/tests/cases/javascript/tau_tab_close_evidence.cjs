'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const codeRoot=process.env.TFI_TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(codeRoot,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWorkbenchTabClose}=require(path.join(codeRoot,'management_portal/test_framework/tau_tab_close_evidence'));
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1100,height:800}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
    await page.setContent('<main id="fixture"><section id="localPanel">Local panel content</section></main>');
    await page.addStyleTag({content:fs.readFileSync(path.join(codeRoot,'management_portal/static/portal_workbench.css'),'utf8')});
    await page.addStyleTag({content:'#fixture{width:700px;height:400px;margin:100px 50px} :root{--portal-workbench-single-tab-opacity:0;--portal-workbench-single-tab-revealed-opacity:1}'});
    await page.evaluate(()=>{window.PortalIdentity={username:()=> 'fixture'};});
    for(const name of ['portal_text.js','portal_ui.js','portal_ui_primitives.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(path.join(codeRoot,'management_portal/static'))])
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
    assert.equal(ready.frames_completed,2);assert.equal(ready.error,null);
    for(const frameTimeoutMs of [0,-1,NaN,Infinity,10001,'150'])
      await assert.rejects(readWorkbenchTabClose(tab,{panelId:'owned',frameTimeoutMs}),/deadline/);
    // Keep the real workbench/close DOM. Stall only the browser frame scheduler,
    // once before each of the two frames, to exercise a finite passive sample.
    const deadlines=[];
    for(const stallAt of [1,2]){
      await page.evaluate(stallAt=>{
        const original={request:window.requestAnimationFrame,cancel:window.cancelAnimationFrame};
        const held=new Map(),cancelled=[],issued=[];let calls=0,nextId=1000000;
        window.frameFixture={original,held,cancelled,issued};
        window.requestAnimationFrame=callback=>{
          calls++;
          if(calls<stallAt)return original.request.call(window,callback);
          const id=nextId++;held.set(id,callback);issued.push(callback);return id;
        };
        window.cancelAnimationFrame=id=>{
          if(held.has(id)){held.delete(id);cancelled.push(id);}
          else original.cancel.call(window,id);
        };
      },stallAt);
      let watchdog;
      const pending=readWorkbenchTabClose(tab,{panelId:'owned',frameTimeoutMs:150});
      const result=await Promise.race([pending,new Promise(resolve=>{watchdog=setTimeout(()=>resolve({watchdog:true}),1500);})]);
      clearTimeout(watchdog);
      const cleanup=await page.evaluate(()=>{
        const f=window.frameFixture;
        const before={pending:f.held.size,cancelled:f.cancelled.length};
        window.requestAnimationFrame=f.original.request;window.cancelAnimationFrame=f.original.cancel;
        // Invoke even cancelled callbacks to prove that late frames cannot
        // append a sample or restart the chain after a settled deadline.
        for(const callback of f.issued)callback(performance.now());
        return before;
      });
      await pending;
      assert(!result.watchdog,'The original two-frame helper never settles while its frame is paused');
      assert.equal(result.ready,false);assert.equal(result.stable,false);
      assert.equal(result.error.code,'frame_timeout');assert.equal(result.frames_completed,stallAt-1);
      assert.equal(result.samples.length,1,'Retain the original sample; do not backfill a second after timeout');
      assert.equal(result.samples[0].connected,true);assert.equal(result.frame_timeout_ms,150);
      assert.deepEqual(cleanup,{pending:0,cancelled:1});deadlines.push(result);
    }
    await page.locator('.portalWorkbenchTabClose').evaluate(button=>{
      const original=button.getBoundingClientRect;let reads=0;
      window.restoreCloseBounds=()=>{button.getBoundingClientRect=original;};
      button.getBoundingClientRect=function(){
        if(++reads===2)throw new Error('isolated final geometry failure');
        return original.call(this);
      };
    });
    const failedSample=await readWorkbenchTabClose(tab,{panelId:'owned'});
    await page.evaluate(()=>window.restoreCloseBounds());
    assert.equal(failedSample.ready,false);assert.equal(failedSample.samples.length,1);
    assert.equal(failedSample.error.code,'sampling_error');assert.equal(failedSample.error.phase,'final_sample');
    assert.match(failedSample.error.stack,/isolated final geometry failure/);
    await page.evaluate(()=>{const blocker=document.createElement('div');blocker.id='blocker';blocker.style.cssText='position:fixed;inset:0;z-index:2147483647';document.body.append(blocker);});
    const blocked=await readWorkbenchTabClose(tab,{panelId:'owned'});assert.equal(blocked.ready,false);assert.equal(blocked.samples[1].center_hittable,false);
    await page.locator('#blocker').evaluate(n=>n.remove());
    assert.equal(await tab.count(),1,'Passive samples never close the original panel');
    await page.locator('.portalWorkbenchTabClose').click();
    assert.equal(await tab.count(),0,'One native close click closes the owned local panel');
    await page.evaluate(()=>window.testWorkbench.destroy());
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    console.log(JSON.stringify({status:'passed',hidden,moving,ready,blocked,deadlines,failedSample,production_requests:0,formal_acceptance:false}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
