'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {portalWorkbenchScripts,STATIC}=require('./portal_workbench_scripts.cjs');
const {PORTAL_COMPONENTS_STYLES}=require('./portal_components_styles.cjs');
const out=process.env.TAB_POINTER_EVIDENCE;fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true});const observations=[];
  try{
    for(const width of [1440,1000]){
      const page=await browser.newPage({viewport:{width,height:900}}),errors=[],requests=[];
      page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
      await page.setContent('<!doctype html><body class="portalBoardShell"><header class="portalSiteHeader"><div class="portalNavShell"><div class="portalNavTop"><div class="portalNavMain">Portal</div><div class="portalUser">Fixture</div></div></div></header><main id="root" style="height:800px"></main></body>');
      for(const file of ['portal_design_system','portal_themes',...PORTAL_COMPONENTS_STYLES,'portal_workbench'])
        await page.addStyleTag({path:path.join(STATIC,file+'.css')});
      for(const file of ['portal_state.js','portal_text.js','portal_ui.js','portal_ui_primitives.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts()])
        await page.addScriptTag({path:path.join(STATIC,file)});
      await page.evaluate(()=>{
        PortalWorkbench.registerPanelPackage({type:'pointer-fixture',version:1,presentation:{title:'Chat',icon:'chat'},
          sizing:{minWidth:100,minHeight:100},instances:'multiple',capabilities:[],context:{channels:['*']},persistence:{schemaVersion:1},
          behavior:{canHide:true,canCollapse:true,canMove:true,closable:true},
          create:()=>{const el=document.createElement('section');el.textContent='Isolated dynamic Chat body';return el;}});
        window.wb=PortalWorkbench.create({id:'pointer',root:'#root',initialEditors:[{id:'chat',type:'pointer-fixture',title:'Main Chat'},
          {id:'dynamic-chat',type:'pointer-fixture',title:'Dynamic Chat'}],defaultLayout:PortalWorkbench.model.stack(['chat','dynamic-chat']),store:{read:()=>({}),replace:v=>v}});
        wb.setLayoutLocked(false);
        window.pointerLog=[];
        for(const type of ['pointermove','pointerdown','pointerup','click'])document.addEventListener(type,e=>{
          pointerLog.push({type,x:e.clientX,y:e.clientY,target:e.target.className, trusted:e.isTrusted});
        },true);
      });
      const stack=page.locator('.portalWorkbenchStack').first();
      const close=page.locator('.portalWorkbenchTabSlot:has([data-portal-workbench-tab="dynamic-chat"]) > .portalWorkbenchTabClose');
      // Read only: capture the center hit without moving focus or revealing tabs.
      const sample=()=>stack.evaluate(el=>{
        const tabs=el.querySelector(':scope > .portalWorkbenchTabs');
        const close=tabs.querySelector('[data-portal-workbench-tab="dynamic-chat"]').parentElement.querySelector('.portalWorkbenchTabClose');
        const r=close.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return {stack:el.getBoundingClientRect().toJSON(),tabs:tabs.getBoundingClientRect().toJSON(),close:r.toJSON(),
          nav:document.querySelector('.portalNavTop').getBoundingClientRect().toJSON(),revealed:el.classList.contains('is-tab-strip-revealed'),
          transform:getComputedStyle(tabs).transform,opacity:getComputedStyle(tabs).opacity,hitClass:hit?.className,
          closeHit:hit===close||close.contains(hit)};
      });
      await page.mouse.move(width/2,400);
      const before=await sample();
      const bounds=await stack.boundingBox();
      await page.mouse.move(bounds.x+bounds.width/2,bounds.y+2,{steps:20});
      await page.waitForFunction(()=>document.querySelector('.portalWorkbenchStack').classList.contains('is-tab-strip-revealed'));
      await page.evaluate(async()=>{await Promise.all(document.querySelector('.portalWorkbenchTabs').getAnimations().map(a=>a.finished));});
      const revealed=await sample();
      observations.push({width,before,revealed});
      fs.writeFileSync(path.join(out,'geometry.json'),JSON.stringify(observations,null,2));
      await page.screenshot({path:path.join(out,`revealed-${width}.png`)});
      assert.equal(revealed.closeHit,true,JSON.stringify(revealed));
      // Actual mouse coordinates, no focus, force, DOM click or locator action.
      await page.mouse.click(revealed.close.x+revealed.close.width/2,revealed.close.y+revealed.close.height/2);
      await close.waitFor({state:'detached'});
      assert.equal(await page.locator('[data-portal-workbench-tab="chat"]').count(),1);
      observations.at(-1).events=await page.evaluate(()=>pointerLog);
      assert(observations.at(-1).events.some(e=>e.type==='click'&&e.trusted),'real pointer click');
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
      await page.evaluate(()=>wb.destroy());await page.close();
    }
    fs.writeFileSync(path.join(out,'geometry.json'),JSON.stringify(observations,null,2));
    console.log(JSON.stringify({passed:true,observations}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
