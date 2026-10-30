'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {RESIDENT_MENU_SCRIPTS}=require(path.join(code,'tests/cases/javascript/resident_menu_scripts.cjs'));
const {HATG_PANEL_STYLES}=require(path.join(code,'tests/cases/javascript/hatg_panel_styles.cjs'));
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const baseline=!!process.env.TAU_FAMILY_MENU_BASELINE;
const read=baseline?null:require(path.join(code,'management_portal/test_framework/tau_function_block_menu_evidence')).readFunctionBlockMenuReady;
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  const p=await browser.newPage({viewport:{width:1100,height:900}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><main><button id="trigger">Local fixture</button></main>');
  for(const name of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES,'lineage_function_blocks','portal_resident_menu'])await p.addStyleTag({path:path.join(code,'management_portal/static',name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','lineage_element_fields',...RESIDENT_MENU_SCRIPTS,'hatg_function_block_menu'])await p.addScriptTag({path:path.join(code,'management_portal/static',name+'.js')});
  await p.evaluate(()=>{
   PortalI18n.apply('en');
   window.instructionCalls=[];
   window.PortalSocietyService={createClient:()=>({})};
   window.PortalInstructionClient={execute:async(name,args)=>{
    instructionCalls.push({name,args});
    if(name!=='ListUsers')throw new Error('Unexpected fixture instruction '+name);
    return {users:[]};
   }};
   const family={facade:'resident_facade',members:[]};
   window.blocks=[{id:'fbi_expected',title:'Expected Family',family,elements:[]},{id:'fbi_other',title:'Other Family',family,elements:[]}];
   const module=PortalHatgFunctionBlockMenu;
   const deps=Object.fromEntries(module.dependencies.map(k=>[k,()=>{throw new Error('Unexpected fixture dependency '+k);} ]));
   Object.assign(deps,{state:{programMutationInFlight:false,lineage:{lineage_id:'lineage_fixture',revision:1,program:{wires:[]}}},esc:PortalUIPrimitives.escapeHtml,
    familyRoles:block=>block.family,functionBlockInstanceById:id=>blocks.find(b=>b.id===id),residentMenuHandlers:()=>({}),
    functionBlockInstanceFailures:()=>[],functionBlockInstanceUnacknowledgedFailures:()=>[],instanceInnerObjects:()=>[],
    displayNodeById:()=>null,familyExpanded:()=>false,functionBlockElements:()=>[],functionBlockInstanceTriggersHtml:()=>''});
   // Keep the real Resident Menu, overlay, tab wiring and block renderer; only
   // the lazy asset loader and unavailable service/data sources are fixtures.
   window.PortalNav={openResidentMenu:options=>PortalResidentMenu.open(options)};
   window.menuModule=module.createFunctionBlockMenu(deps);
   window.openFixture=(id,pane='block')=>menuModule.openFunctionBlockMenu(id,pane);
  });
  const expected={blockId:'fbi_expected',title:'Expected Family',pane:'block'};
  await p.evaluate(()=>openFixture('fbi_other'));
  const originalPredicate=(await Promise.all((await p.getByRole('dialog').all()).map(d=>d.isVisible()))).some(Boolean);
  const snapshot=await p.getByRole('dialog').ariaSnapshot();
  assert.equal(originalPredicate,true);assert.equal(await p.locator('[data-function-block-menu-panel="fbi_expected"]').count(),0);
  assert.match(snapshot,/Other Family/);
  if(!baseline){
   const wrong=await read(p,expected);assert.equal(wrong.ready,false,JSON.stringify(wrong));
   assert.equal(wrong.dialogs[0].panels[0].id,'fbi_other');
   await p.evaluate(()=>openFixture('fbi_expected','objects'));
   assert.equal((await read(p,expected)).ready,false,'Right instance on a different native tab is not the requested pane');
   assert.equal((await read(p,{...expected,pane:'objects'})).ready,true);
   await p.evaluate(()=>openFixture('fbi_expected'));
   assert.equal((await read(p,expected)).ready,true);
   // Use the real group-tab event handler for a tab switch; read still only
   // observes the DOM and cannot open or select a different target itself.
   await p.locator('[data-group-tab="objects"]').click();
   await p.locator('[data-group-tab="objects"]').getAttribute('aria-selected').then(x=>assert.equal(x,'true'));
   assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   const panel=p.locator('[data-function-block-menu-panel="fbi_expected"]');
   await panel.locator('.functionBlockMenuIdentity code').evaluate(n=>n.textContent='fbi_other');assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   assert.equal((await read(p,{...expected,title:'Other title'})).ready,false);
   await panel.evaluate(n=>n.hidden=true);assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   await panel.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   await p.locator('[data-group-tab="objects"]').evaluate(n=>n.setAttribute('aria-selected','true'));assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   await p.locator('[data-group-tab="block"]').evaluate(n=>n.setAttribute('aria-controls','wrong-panel'));assert.equal((await read(p,expected)).ready,false);
   await p.evaluate(()=>openFixture('fbi_expected'));
   // Unrelated native confirmation leaves the expected menu underneath it.
   await p.evaluate(()=>{window.confirmation=PortalUI.confirm({title:'Unrelated',message:'Local fixture',confirmLabel:'Confirm'});});
   const blocked=await read(p,expected);assert.equal(blocked.ready,false);assert.ok(blocked.visible_dialog_count>1);
   await p.getByRole('alertdialog',{name:'Unrelated',exact:true}).getByRole('button',{name:'Cancel',exact:true}).click();
   await p.evaluate(()=>confirmation);
   assert.equal((await read(p,expected)).ready,true);
   const html=await p.locator('#nodeSettingsModal').innerHTML(),before=await p.evaluate(()=>instructionCalls.length);
   await read(p,expected);
   assert.equal(await p.locator('#nodeSettingsModal').innerHTML(),html);assert.equal(await p.evaluate(()=>instructionCalls.length),before);
   await p.locator('#nodeSettingsModal [data-portal-detail-close]').click();
   assert.equal((await read(p,expected)).ready,false);
  }
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
  assert.ok((await p.evaluate(()=>instructionCalls)).every(x=>x.name==='ListUsers'));
  console.log(JSON.stringify({status:baseline?'reproduced':'passed',original_any_dialog_predicate:originalPredicate,expected_instance_absent_at_baseline:true,baseline_snapshot:snapshot,fixture:'Native PortalResidentMenu/Overlay/UI/DetailSurface and HATG Function Block renderer; local data/service fixtures, no live business',formal_acceptance:false,production_requests:0}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
