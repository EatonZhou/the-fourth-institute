'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=path.resolve(__dirname,'../../..'),staticDir=path.join(code,'management_portal/static');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {PORTAL_COMPONENTS_STYLES}=require('./portal_components_styles.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const report={formal_acceptance:false,fixture:'Native ScopeMenu/Overlay/UI/DetailSurface/i18n/CSS; records, avatar module, permission response and entry adapter are local fixtures'};
 try{
  const p=await browser.newPage({viewport:{width:1200,height:900}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><main>Local scope entry</main>');
  for(const name of ['portal_design_system','portal_themes',...PORTAL_COMPONENTS_STYLES,'portal_resident_menu'])await p.addStyleTag({path:path.join(staticDir,name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface'])await p.addScriptTag({path:path.join(staticDir,name+'.js')});
  await p.evaluate(()=>{
   PortalI18n.apply('en');window.advancedCalls=0;window.avatarReads=[];
   window.PortalIdentity={permissionSystemOff:async()=>false};
   window.PortalInstructionClient={execute:()=>{throw new Error('Unexpected instruction');}};
   window.PortalAvatarEditor={loadAssets:async()=>{},read:async(kind,id)=>{avatarReads.push({kind,id});return null;},paint:node=>{node.textContent='L';},mount:()=>({close(){},destroy(){},isBusy:()=>false,hasUnsavedChanges:()=>false})};
  });
  await p.addScriptTag({path:path.join(staticDir,'portal_scope_menu.js')});
  await p.evaluate(()=>{
   window.openLocal=id=>PortalScopeMenu.open({kind:'lineage',id,record:{lineage_id:id,title:'Local Lineage',description:'Local fixture',function_blocks:[],nodes:[],allows:{UpdateLineage:true}},onOpenAdvanced:()=>{advancedCalls++;}});
  });
  // A same-title Menu is not the original target: preserve the native ID and kind.
  await p.evaluate(()=>openLocal('lineage_other'));
  const dialog=p.locator('#scopeMenuModal [role="dialog"]');
  assert.equal(await p.getByRole('dialog',{name:'Menu',exact:true}).isVisible(),true);
  report.other_scope={id:await dialog.locator('#scopeMenuId').innerText(),kind:await dialog.locator('#scopeMenuKind').innerText(),title:await dialog.locator('#scopeMenuName').innerText()};
  assert.notEqual(report.other_scope.id,'lineage_fixture');
  await p.evaluate(()=>openLocal('lineage_fixture'));
  let oldError;
  try{await p.getByRole('menu').filter({visible:true}).waitFor({state:'visible',timeout:150});}
  catch(e){oldError={name:e.name,message:e.message,stack:e.stack};}
  assert.equal(oldError?.name,'TimeoutError','Native Lineage Menu is a dialog, not a menu role');
  assert.equal(await dialog.count(),1);assert.equal(await dialog.isVisible(),true);
  const settings=dialog.locator('[data-scope-tab="settings"]'),panel=dialog.locator('#scopeSettingsPanel'),advanced=panel.locator('[data-scope-advanced]');
  report.original_predicate_error=oldError;
  report.target={id:await dialog.locator('#scopeMenuId').innerText(),kind:await dialog.locator('#scopeMenuKind').innerText(),title:await dialog.locator('#scopeMenuName').innerText(),tab_selected:await settings.getAttribute('aria-selected'),tab_controls:await settings.getAttribute('aria-controls'),panel_id:await panel.getAttribute('id'),panel_visible:await panel.isVisible(),advanced_visible:await advanced.isVisible(),advanced_disabled:await advanced.isDisabled(),name_value:await panel.locator('input[name="title"]').inputValue(),snapshot:await dialog.ariaSnapshot()};
  assert.equal(report.target.id,'lineage_fixture');assert.equal(report.target.kind,'Lineage');assert.equal(report.target.title,'Local Lineage');
  assert.equal(report.target.tab_selected,'true');assert.equal(report.target.tab_controls,report.target.panel_id);assert.equal(report.target.panel_visible,true);
  assert.equal(await advanced.count(),1);assert.equal(report.target.advanced_visible,true);assert.equal(report.target.advanced_disabled,false);
  assert.equal(report.target.name_value,'Local Lineage');
  assert.equal(await p.evaluate(()=>advancedCalls),0,'Advanced settings and deletion were not opened');
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
  report.status='passed';if(process.env.TAU_LINEAGE_MENU_REPORT)fs.writeFileSync(process.env.TAU_LINEAGE_MENU_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,formal_acceptance:false,original_menu_role_wait:'TimeoutError',advanced_opened:false,business_replayed:false}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
