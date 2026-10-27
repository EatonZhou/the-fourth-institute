'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const portal=path.resolve(__dirname,'../../../management_portal');
const {chromium}=require(path.join(portal,'test_framework/tau_runtime/node_modules/playwright'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 const report={formal_acceptance:false,fixture:'Native HATG template/LineageDialogs/UI/Overlay/DetailSurface/i18n/CSS; graph, user catalog and entry dependencies are local fixtures'};
 try{
  const p=await browser.newPage({viewport:{width:1280,height:1000}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><section id="panel" class="portalHatgPanelComponent">'+fs.readFileSync(path.join(portal,'hatg-panel.html'),'utf8')+'</section>');
  for(const n of ['portal_design_system','portal_themes','portal_components','hatg_panel'])await p.addStyleTag({path:path.join(portal,'static',n+'.css')});
  await p.evaluate(()=>{window.__PORTAL_LANGUAGE__='en';});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','hatg_lineage_dialogs'])await p.addScriptTag({path:path.join(portal,'static',n+'.js')});
  await p.evaluate(()=>{
   PortalI18n.apply('en');
   const modal=document.getElementById('lineageModal');
   const controller=PortalUI.createDialogController({modal,openClass:'portalDialogOpen',bodyClass:'agentLineageModalOpen',backdropClose:true,escapeClose:true,onOpen:()=>{modal.hidden=false;},onClose:()=>{modal.hidden=true;}});
   window.PortalLineageCatalogs={users:async()=>[]};
   const state={projects:[{id:'project_fixture',name:'Local Project'}],users:[],lineage:null};
   const owner=PortalHatgLineageDialogs;
   const deps=Object.fromEntries(owner.dependencies.map(n=>[n,()=>{throw new Error('Unexpected dependency '+n);} ]));
   Object.assign(deps,{$id:id=>document.getElementById(id),state,esc:PortalUIPrimitives.escapeHtml,openModal:id=>{if(id!=='lineageModal')throw new Error('Unexpected modal');controller.open();}});
   const dialogs=owner.createLineageDialogs(deps);
   window.openLocal=id=>{
    state.lineage={lineage_id:id,title:'Same Lineage Title',revision:7,project_id:'project_fixture',nodes:[],description:'Local fixture'};
    dialogs.openEditLineageModal();
   };
   window.openLocalCreate=()=>dialogs.openCreateLineageModal('project_fixture');
  });
  const dialog=p.locator('#panel #lineageModal [role="dialog"]');
  const read=()=>dialog.evaluate(n=>{
   const form=n.querySelector('#lineageForm'),zone=n.querySelector('#lineageDeleteZone'),button=zone.querySelector('#deleteLineageBtn');
   const visible=e=>e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
   return {title:n.querySelector('#lineageModalTitle').textContent,mode:form.getAttribute('data-mode'),lineage_id:form.getAttribute('data-lineage-id'),revision:form.getAttribute('data-revision'),name:n.querySelector('#lineageNameInput').value,zone_visible:visible(zone),button_visible:visible(button),button_disabled:button.disabled,button_text:button.textContent,zone_heading:zone.querySelector('strong').textContent};
  });
  // Same title is not a substitute for the exact original form binding.
  await p.evaluate(()=>openLocal('lineage_other'));
  report.other=await read();assert.notEqual(report.other.lineage_id,'lineage_fixture');
  await p.evaluate(()=>openLocal('lineage_fixture'));
  let oldError;try{await p.getByRole('button',{name:'Delete Lineage',exact:true}).waitFor({state:'visible',timeout:150});}catch(e){oldError={name:e.name,message:e.message,stack:e.stack};}
  assert.equal(oldError?.name,'TimeoutError');report.original_predicate_error=oldError;
  assert.equal(await dialog.count(),1);assert.equal(await dialog.isVisible(),true);
  report.target=await read();report.snapshot=await dialog.ariaSnapshot();
  assert.equal(report.target.title,'Lineage settings');assert.equal(report.target.mode,'edit');
  assert.equal(report.target.lineage_id,'lineage_fixture');assert.equal(report.target.revision,'7');
  assert.equal(report.target.name,'Same Lineage Title');assert.equal(report.target.zone_heading,'Delete Lineage');
  assert.equal(report.target.zone_visible,true);assert.equal(report.target.button_visible,true);assert.equal(report.target.button_disabled,false);
  assert.equal(await dialog.locator('#lineageDeleteZone').getByRole('button',{name:'Delete',exact:true}).count(),1);
  // Native create mode can retain the prior dataset: ID alone is not readiness.
  await p.evaluate(()=>openLocalCreate());report.create=await read();
  assert.equal(report.create.mode,'create');assert.equal(report.create.zone_visible,false);assert.equal(report.create.button_visible,false);
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
  report.status='passed';report.business_replayed=false;
  if(process.env.TAU_LINEAGE_DELETION_REPORT)fs.writeFileSync(process.env.TAU_LINEAGE_DELETION_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,formal_acceptance:false,original_button_name_wait:'TimeoutError',business_replayed:false}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
