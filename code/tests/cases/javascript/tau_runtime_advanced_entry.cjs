'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=path.resolve(__dirname,'../../..'),staticDir=path.join(code,'management_portal/static');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));

// Isolate the original entry contract, not saving settings or restarting a session.
// Native Menu shell, controller and both dialog implementations; service and the
// Menu's entry adapter are local fixtures, with an explicitly bound Resident.
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const report={formal_acceptance:false,fixture:'Native Menu/RuntimeController/runtime and Session dialogs/model API fields/i18n/CSS; local service and Menu entry adapter',branches:[]};
 try{
  for(const restart of [false,true]){
   const p=await browser.newPage({viewport:{width:1200,height:900}}),requests=[],errors=[];
   p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
   await p.setContent('<!doctype html><main>Local runtime entry</main>');
   for(const name of ['portal_design_system','portal_themes','portal_components','portal_resident_menu'])await p.addStyleTag({path:path.join(staticDir,name+'.css')});
   for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','portal_model_api_settings','portal_resident_menu','chat_workspace_runtime_settings','portal_resident_runtime_controller'])await p.addScriptTag({path:path.join(staticDir,name+'.js')});
   await p.evaluate(async restart=>{
    PortalI18n.apply('en');window.calls=[];const residentId='resident_runtime_fixture';
    const settings={model:'model_fixture',prompt_context:{history:{enabled:true,max_tokens:8000}},backend_options:{timeout_seconds:360,max_retries:0,parameters:{}}};
    const catalog={models:['model_fixture'],default_model:'model_fixture',account_id:'account_fixture',backend_route_id:'route_fixture',runtime_capabilities:{settings:['model','prompt_context','backend_options']},api_settings:{history_max_tokens:8000,timeout_max:3600,parameters_by_model:{}}};
    const record=(name,id)=>{calls.push({name,residentId:id});if(id!==residentId)throw new Error('Wrong bound Resident');};
    const forbidden=()=>{throw new Error('Unexpected mutation');};
    const service={
     projection:async({residentId:id})=>{record('projection',id);return {resident:{resident_id:id,backend:'provider_api'},execution_account:{account_id:'account_fixture'},allows:{RestartSession:restart,UpdateResidentSettings:true},chat_workspace:{actions:{runtime_settings:{instruction:'UpdateResidentSettings'}}}};},
     runtimeSettings:async id=>{record('runtimeSettings',id);return {settings};},
     runtimeCatalog:async({residentId:id})=>{record('runtimeCatalog',id);return catalog;},
     runtimeAccounts:async()=>({accounts:[{account_id:'account_fixture',enabled:true}]}),
     observeSessionRestart:(id,fn)=>{record('observeSessionRestart',id);return ()=>{};},
     sessionRestartOptions:async id=>{record('sessionRestartOptions',id);return {resident_id:id,providers:[{id:'provider_api',label:'Model API'}],strategies:[{id:'fresh',label:'Fresh'}],accounts:{provider_api:[{id:'route_fixture',label:'Local API',account_id:'account_fixture'}]},source:{provider:'provider_api',account_id:'route_fixture',has_session:false,binding_status:'current'},runtime_settings:settings,runtime_catalog:catalog};},
     saveRuntimeSettings:forbidden,restartSession:forbidden,cancelSessionRestart:forbidden,
    };
    window.PortalResidentService=service;
    window.PortalSocietyService={createClient:()=>({})};
    window.PortalInstructionClient={execute:forbidden};
    window.PortalChatWorkspace=PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml:PortalUIPrimitives.escapeHtml});
    window.binding=PortalResidentRuntimeController.create({service}).bind(residentId);
    await binding.load();
    const row=PortalChatWorkspace.residentRuntimeSettingRows(settings,catalog).find(([key])=>key==='高级');
    if(!row)throw new Error('Native advanced row absent');
    await PortalResidentMenu.open({lineage:{lineage_id:'lineage_fixture'},group:{active:'fixture',tabs:[{key:'fixture',label:'Local runtime entry'}],render:(_key,panel)=>{
     const button=document.createElement('button');button.setAttribute('data-fixture-runtime-entry','');button.textContent=PortalI18n.t(row[0])+' '+row[1];
     button.onclick=()=>{window.opening=binding.openSettings(()=>button);};panel.append(button);
    }}});
   },restart);
   const menu=p.getByRole('dialog',{name:'Menu',exact:true});
   await menu.locator('[data-fixture-runtime-entry]').click();
   await p.evaluate(()=>opening);
   let oldError;
   try{await menu.getByRole('textbox').filter({visible:true}).waitFor({state:'visible',timeout:150});}
   catch(error){oldError={name:error.name,message:error.message,stack:error.stack};}
   assert.equal(oldError?.name,'TimeoutError','Original Menu-local textbox predicate remains false');
   const surface=p.locator(restart?'.portalSessionRestartOverlay [role="dialog"]':'.portalRuntimeSettingsOverlay:not(.portalSessionRestartOverlay) [role="dialog"]');
   // The Session card keeps the hidden switch-account target editor in the same dialog; only the shown form counts.
   const timeout=surface.locator('input[data-api-timeout]').filter({visible:true}),retries=surface.locator('input[data-api-retries]').filter({visible:true});
   await timeout.waitFor({state:'visible',timeout:1000});
   assert.equal(await surface.count(),1);assert.equal(await surface.isVisible(),true);
   assert.equal(await surface.locator('.portalRuntimeSettingsEditor').filter({visible:true}).count(),1);
   for(const control of [timeout,retries]){
    assert.equal(await control.count(),1);assert.equal(await control.getAttribute('type'),'number');
    assert.equal(await control.isVisible(),true);assert.equal(await control.isEnabled(),true);
   }
   assert.equal(await timeout.inputValue(),'360');assert.equal(await retries.inputValue(),'0');
   assert.equal(await surface.getByRole('textbox').filter({visible:true}).count(),0,'Collapsed optional text fields are not an advanced-entry readiness requirement');
   const state=await p.evaluate(()=>({residentId:binding.state().residentId,calls}));
   assert.equal(state.residentId,'resident_runtime_fixture');
   assert.equal(state.calls.every(c=>c.residentId===state.residentId),true);
   assert.equal(state.calls.some(c=>c.name==='sessionRestartOptions'),restart);
   report.branches.push({restart_permission:restart,original_predicate_error:oldError,surface_snapshot:await surface.ariaSnapshot(),original_binding:state,timeout:await timeout.inputValue(),retries:await retries.inputValue()});
   await p.evaluate(()=>binding.destroy());
   assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
   await p.close();
  }
  report.status='passed';if(process.env.TAU_ADVANCED_ENTRY_REPORT)fs.writeFileSync(process.env.TAU_ADVANCED_ENTRY_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,formal_acceptance:false,original_menu_textbox_predicate:false,settings_saved:false,session_restarted:false}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
