'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=path.resolve(__dirname,'../../..'),staticDir=path.join(code,'management_portal/static');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));

// Native Menu shell and mounted Harness editor. Projection, settings, preview,
// and the parent Menu group are local fixtures; never save or contact a server.
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const report={formal_acceptance:false,fixture:'Native Menu shell/Harness mount/i18n/Overlay/UI/DetailSurface/CSS; local parent group and read responses'};
 try{
  const p=await browser.newPage({viewport:{width:1200,height:900}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><main>Local prompt editor</main>');
  for(const name of ['portal_design_system','portal_themes','portal_components','portal_resident_menu'])await p.addStyleTag({path:path.join(staticDir,name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','portal_resident_menu','chat_workspace_harness_panel'])await p.addScriptTag({path:path.join(staticDir,name+'.js')});
  await p.evaluate(async()=>{
   PortalI18n.apply('en');window.calls=[];const residentId='resident_prompt_fixture';
   const defaults={
    inline_media_history:{enabled:true,trigger_inline_media_bytes:2097152,keep_recent_images:4},
    tool_call_granularity:{enabled:false},tool_output_offloading:{enabled:true,offload_trigger_bytes:65536},
    artifact_search_and_slice:{enabled:false},hash_aware_read_dedup:{enabled:false,min_output_tokens:400},
    unified_resource_recall:{enabled:false},early_context_compaction:{enabled:false},
    native_tool_loop_governor:{enabled:false,checkpoint_interval:12,max_checkpoints:3},provider_error_continue:{enabled:false},
   };
   window.PortalResidentProjection={load:async id=>{
    if(id!==residentId)throw new Error('Wrong projection identity');calls.push({instruction:'projection',resident_id:id});
    return {runtime_settings:{prompt_injection:{preset:'standard'}},agent_harness_defaults:defaults,backend_capabilities:{runtime_capabilities:{harness_features:[],harness_capabilities:[]}}};
   }};
   window.PortalInstructionClient={execute:(instruction,args)=>{
    calls.push({instruction,...args});if(args.resident_id!==residentId)throw new Error('Wrong original Resident');
    if(instruction==='GetResidentSettings')return new Promise(resolve=>{window.resolveSettings=resolve;});
    if(instruction==='PreviewPromptInjection')return new Promise((_resolve,reject)=>{window.rejectPreview=reject;});
    throw new Error('Unexpected instruction '+instruction);
   }};
   window.PortalSocietyService={createClient:()=>({})};
   const harness=PortalChatWorkspaceHarnessPanel.createHarnessPanel({escapeHtml:PortalUIPrimitives.escapeHtml});
   await PortalResidentMenu.open({lineage:{lineage_id:'lineage_fixture'},group:{active:'harness_fixture',tabs:[{key:'harness_fixture',label:'Harness'}],render:(_key,container)=>{
    window.panel=harness.mountResidentHarnessPanel({container,residentId});return ()=>panel.destroy();
   }}});
  });
  const menu=p.getByRole('dialog',{name:'Menu',exact:true}),editor=menu.locator('.chatPromptInjection'),preset=editor.locator('select[data-prompt-injection-preset]');
  await menu.locator('[data-prompt-injection-open]').click();
  assert.equal(await editor.count(),0,'Pending original settings response is not editor readiness');
  report.before_response={editor_count:await editor.count(),calls:await p.evaluate(()=>calls)};
  await p.evaluate(()=>resolveSettings({prompt_injection:{preset:'standard',presets:[{id:'standard',label:'标准'},{id:'pure',label:'纯 TUI'},{id:'custom',label:'自定义'}],parts:[
   {id:'rules',label:'Portal 核心规则',description:'Local rules',placement:'System',choice:'first',choices:[{id:'never',label:'关闭'},{id:'first',label:'首轮'}]},
   {id:'identity',label:'运行身份',description:'Local identity',placement:'Message',choice:'every',choices:[{id:'never',label:'关闭'},{id:'every',label:'每轮'}]},
  ]}}));
  await preset.waitFor({state:'visible',timeout:1000});
  const allComboboxes=menu.getByRole('combobox').filter({visible:true});
  assert.ok(await allComboboxes.count()>1,'Preset, timing categories and preview are distinct controls');
  let oldError;
  try{await allComboboxes.waitFor({state:'visible',timeout:150});}
  catch(e){oldError={name:e.name,message:e.message,stack:e.stack};}
  assert.equal(oldError?.name,'Error');assert.match(oldError.message,/strict mode violation/);
  assert.equal(await editor.count(),1);assert.equal(await preset.count(),1);assert.equal(await preset.isEnabled(),true);
  assert.equal(await preset.inputValue(),'standard');assert.equal(await preset.locator('option[value="pure"]').count(),1);
  const candidates=await allComboboxes.evaluateAll(nodes=>nodes.map(n=>({label:n.getAttribute('aria-label'),preset:n.hasAttribute('data-prompt-injection-preset'),part:n.getAttribute('data-prompt-injection-part'),preview:n.hasAttribute('data-prompt-injection-preview-turn'),value:n.value,disabled:n.disabled})));
  assert.equal(candidates.filter(c=>c.preset).length,1);assert.equal(candidates.some(c=>c.preview),true);
  report.baseline={old_error:oldError,candidates,snapshot:await menu.ariaSnapshot(),preview_pending:await editor.locator('[data-prompt-injection-preview]').innerText()};
  // Preview has its own result. Failure must not change what preset controls mean.
  await p.evaluate(()=>rejectPreview(new Error('Local preview fixture failure')));
  await editor.locator('[data-prompt-injection-preview]').filter({hasText:'Local preview fixture failure'}).waitFor({state:'visible',timeout:1000});
  assert.equal(await preset.isEnabled(),true);assert.equal(await preset.inputValue(),'standard');
  report.after_preview_failure={preset:await preset.inputValue(),preview:await editor.locator('[data-prompt-injection-preview]').innerText(),calls:await p.evaluate(()=>calls)};
  assert.deepEqual(report.after_preview_failure.calls.map(c=>c.instruction),['projection','GetResidentSettings','PreviewPromptInjection']);
  assert.equal(report.after_preview_failure.calls.every(c=>c.resident_id==='resident_prompt_fixture'),true);
  await p.evaluate(()=>panel.destroy());assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
  report.status='passed';if(process.env.TAU_PROMPT_ENTRY_REPORT)fs.writeFileSync(process.env.TAU_PROMPT_ENTRY_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,formal_acceptance:false,original_combobox_wait:'strict mode violation',settings_saved:false}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
