'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(path.resolve(__dirname,'../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async()=>{
  const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
  try {
    await page.addInitScript(lineageId=>{
      document.addEventListener('DOMContentLoaded',()=>{
        window.bootReady=(async()=>{
          const query=new URLSearchParams(location.search);
          PortalI18n.apply('zh-CN');
          window.PortalIdentity={username:()=>query.get('account')||'sample_admin',peek:()=>({}),userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
          window.PortalAPI={get:url=>fetch(url).then(r=>r.json())};
          window.PortalNav={personalAssistantTarget:async()=>null,loadChatWorkspace:async()=>{}};
          window.execute=async(instruction,args={},options={})=>{
            const data=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();
            if(data.error)throw Object.assign(new Error(data.error.message),data.error);return data.result;
          };
          window.PortalInstructionClient={execute};await PortalHatgLoader.load();
          document.body.innerHTML='<main id="reloadWorkbench" style="height:100vh"></main>';
          PortalWorkbench.registerPanelPackage({type:'reload-source',version:1,presentation:{title:'Source',icon:'network'},
            sizing:{minWidth:120,minHeight:100},instances:'single',capabilities:[],
            behavior:{canHide:true,canCollapse:true,canMove:true,closable:false},context:{channels:[]},persistence:{schemaVersion:1},create:({root})=>root});
          const deps=Object.fromEntries(PortalLineageWorkbenchEditors.dependencies.map(key=>[key,()=>{}]));
          const editors=PortalLineageWorkbenchEditors.createEditors({...deps,state:{chatTarget:{lineage_id:lineageId}},lineageSidebarRoots:new Map()});
          window.cards=PortalWorkbench.create({id:query.get('workspace')||'reload-workshop',root:'#reloadWorkbench',
            initialEditors:[{id:'source',type:'reload-source'},editors.workshopEditorDescriptor()],
            defaultLayout:PortalWorkbench.model.stack(['source']),editorTypes:editors.lineageEditorTypes,
            createEditorDescriptor:editors.lineageEditorDescriptor,
            restorePanel:spec=>editors.lineageEditorDescriptor({instanceId:spec.id,type:spec.type,resource:spec.binding}),
            onPanelEvent:({event,sourcePanelId,detail},controller)=>{
              if(['workshop-saved','workshop-title'].includes(event))controller.setPanelTitle(sourcePanelId,detail.title);
              if(event==='workshop-deleted')void controller.closePanel(sourcePanelId);
            }});
          await Promise.all(cards.getPanelsByType('workshop').filter(panel=>cards.isPanelVisible(panel.id)).map(panel=>panel.ready));
          window.bootDone=true;
        })().catch(error=>{window.bootError=error.stack;throw error;});
      });
    },process.env.WORKSHOP_TEST_LINEAGE);
    const boot=async()=>{await page.waitForFunction(()=>window.bootDone||window.bootError);assert.equal(await page.evaluate(()=>window.bootError),undefined);};
    const state=()=>page.evaluate(()=>({layout:cards.getLayout(),hidden:cards.getState().hidden,
      panels:cards.getPanelsByType('workshop').map(panel=>({id:panel.id,target:panel.target()}))}));
    const reload=async()=>{await page.reload();await boot();assert.deepEqual(errors,[]);};
    await page.goto(process.env.WORKSHOP_TEST_ORIGIN);await boot();
    assert.equal(await page.evaluate(()=>cards.isPanelVisible('workshop')),false,'first entry is hidden');
    await page.locator('[data-workbench-restore-panel="workshop"]').click();await page.evaluate(()=>cards.getPanel('workshop').ready);
    const blank=await state();await reload();assert.deepEqual(await state(),blank,'full reload preserves an explicitly opened blank workshop');
    const current=page.locator('[data-portal-workbench-panel="workshop"]');
    assert.equal(await current.locator('.hatgNode').count(),0);
    await current.locator('[data-meta="title"]').fill('Saved reload target');await current.locator('[data-workshop-save]').click();
    await page.waitForFunction(()=>cards.getPanel('workshop').target().template_id);
    const saved=await state(),savedId=saved.panels[0].target.template_id;
    await reload();assert.deepEqual(await state(),saved);assert.equal(await current.locator('[data-meta="title"]').inputValue(),'Saved reload target');
    await current.locator('[data-meta="title"]').fill('Unsaved reload draft');
    let prompted=false;page.once('dialog',async dialog=>{assert.equal(dialog.type(),'beforeunload');prompted=true;await dialog.dismiss();});
    await page.reload().catch(()=>{});assert.equal(prompted,true);assert.equal(await current.locator('[data-meta="title"]').inputValue(),'Unsaved reload draft');
    await current.locator('[data-meta="title"]').fill('Saved reload target');
    const ids=await page.evaluate(async lineageId=>{
      const templates=(await execute('ListTemplates')).templates;
      const family=templates.find(row=>row.builtin_key==='family').template_id;
      const holder=(await execute('CreateResident',{lineage_id:lineageId,title:'Reload holder',provider:'codex'})).resident_id;
      const duty=(await execute('CreateDuty',{lineage_id:lineageId,holder_id:holder,description:'Reload duty',prompt:'Preserve duty binding',memory_level:'session'})).function_block.id;
      const store=(await execute('PublishTemplate',{template_id:cards.getPanel('workshop').target().template_id})).item.store_id;
      for(const resource of [{template_id:family},{lineage_id:lineageId,function_block_id:duty},{store_id:store}])
        await PortalTemplateWorkshopLoader.openInWorkbench(cards,resource,'workshop');
      await Promise.all(cards.getPanelsByType('workshop').map(panel=>panel.ready));
      return {family,duty,store};
    },process.env.WORKSHOP_TEST_LINEAGE);
    const multi=await state();assert.equal(multi.panels.length,4);await reload();assert.deepEqual(await state(),multi,'all independent resources and tab placements restore');
    assert.equal(await page.locator('.workshopPanelHost').count(),4,'one runtime per stable instance');
    // Switch a dynamic panel in place: the persisted locator follows the live
    // target instead of retaining its original family binding.
    const familyPanel=multi.panels.find(p=>p.target.template_id===ids.family).id;
    await page.evaluate(id=>cards.focusPanel(id),familyPanel);
    await page.locator(`[data-portal-workbench-panel="${familyPanel}"] [data-template-id="${savedId}"]`).click();
    await page.waitForFunction(({id,templateId})=>cards.getPanel(id).target().template_id===templateId,{id:familyPanel,templateId:savedId});
    const switched=await state();await reload();assert.deepEqual(await state(),switched);
    // Change layout, hide one and close another, then perform a real reload.
    await page.evaluate(()=>{
      const {split,stack}=PortalWorkbench.model,panels=cards.getPanelsByType('workshop');
      cards.applyWorkspace({name:'Moved',layout:split('vertical',[stack(['source','workshop']),stack(panels.slice(1).map(p=>p.id))],[2,3]),hidden:[]});
      cards.hidePanel('workshop');
    });
    const closed=switched.panels.find(p=>p.target.store_id===ids.store).id;
    await page.evaluate(id=>cards.closePanel(id),closed);const hidden=await state();await reload();assert.deepEqual(await state(),hidden);
    assert.equal(await page.evaluate(()=>cards.isPanelVisible('workshop')),false,'explicit hide remains hidden');
    assert.equal(await page.evaluate(id=>cards.getPanel(id),closed),null,'closed dynamic panel does not revive');
    const folder=process.env.WORKSHOP_RELOAD_SCREENSHOT_DIR||'/tmp/workshop-reload-evidence';fs.mkdirSync(folder,{recursive:true});
    await page.screenshot({animations:'disabled',path:path.join(folder,'restored-layout.png')});
    for(const query of ['?account=other','?workspace=other']){
      await page.goto(process.env.WORKSHOP_TEST_ORIGIN+query);await boot();
      assert.equal(await page.evaluate(()=>cards.getPanelsByType('workshop').length),1);
      assert.equal(await page.evaluate(()=>cards.isPanelVisible('workshop')),false,'other account/workbench starts separately');
    }
    await page.goto(process.env.WORKSHOP_TEST_ORIGIN);await boot();assert.deepEqual(await state(),hidden,'original scope remains intact');
    await page.evaluate(()=>cards.showPanel('workshop'));await page.evaluate(()=>cards.getPanel('workshop').ready);
    assert.equal(await current.locator('[data-meta="title"]').inputValue(),'Saved reload target');
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
