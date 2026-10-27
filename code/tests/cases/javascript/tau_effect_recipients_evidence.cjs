'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const baseline=!!process.env.TAU_RECIPIENTS_LABEL_BASELINE;
const read=baseline?null:require(path.join(code,'management_portal/test_framework/tau_effect_recipients_evidence')).readClosedEffectRecipients;
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  const p=await browser.newPage({viewport:{width:1100,height:800}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><section id="menu" role="dialog" aria-label="Menu"><div id="body"></div></section>');
  for(const name of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks'])await p.addStyleTag({path:path.join(code,'management_portal/static',name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','lineage_element_fields','hatg_function_block_menu'])await p.addScriptTag({path:path.join(code,'management_portal/static',name+'.js')});
  await p.evaluate(()=>{
   window.instructionCalls=[];
   window.PortalInstructionClient={execute:async(name,args)=>{
    instructionCalls.push({name,args});
    if(name!=='ListUsers')throw new Error('Unexpected fixture instruction '+name);
    return {users:[{id:'alice',name:'Alice',status:'active'}]};
   }};
   window.localEffects=[{id:'effect_one',title:'One',kind:'effect',config:{failure_recipients:[]}},{id:'effect_two',title:'Two',kind:'effect',config:{failure_recipients:[]}}];
   const block={id:'function_block_fixture',title:'Fixture'},module=PortalHatgFunctionBlockMenu;
   const deps=Object.fromEntries(module.dependencies.map(k=>[k,()=>{throw new Error('Unexpected fixture dependency '+k);} ]));
   Object.assign(deps,{state:{programMutationInFlight:false,lineage:{id:'lineage_fixture',revision:1}},esc:PortalUIPrimitives.escapeHtml,
    familyRoles:()=>null,functionBlockInstanceById:id=>id===block.id?block:null,residentMenuHandlers:()=>({}),
    functionBlockInstanceFailures:()=>[],functionBlockInstanceUnacknowledgedFailures:()=>[],instanceInnerObjects:()=>localEffects,
    programEffectRuntime:()=>({}),functionBlockElements:()=>[]});
   window.PortalNav={openResidentMenu:options=>options.group.render('objects',document.querySelector('#body'),{close:()=>{},setLabel:()=>{}})};
   window.menuModule=module.createFunctionBlockMenu(deps);
   window.draw=language=>{PortalI18n.apply(language);menuModule.openFunctionBlockMenu(block.id,'objects');};
  });
  const menu=p.locator('#menu'),options={blockId:'function_block_fixture',effectIds:['effect_one','effect_two']},labels=[];
  for(const language of ['en','zh-CN']){
   await p.evaluate(language=>draw(language),language);
   const original=await menu.locator('[data-block-recipients="effect_one"]').evaluate(button=>({
    whole_text:button.innerText,state_text:button.querySelector('.effectRecipientsOff').innerText,
    aria_label:button.getAttribute('aria-label'),edit_text:button.querySelector('[aria-hidden="true"]').innerText,
    expected_off:PortalI18n.t('已关闭 · 失败时不提醒任何人'),expected_edit:PortalI18n.t('修改')}));
   assert.equal(original.state_text,original.expected_off);
   assert.notEqual(original.whole_text,original.expected_off,'Original whole-button comparison rejects a correct native off state');
   assert.equal(original.edit_text,original.expected_edit);labels.push({language,...original});
   if(!baseline){const evidence=await read(menu,options);assert.equal(evidence.ready,true,JSON.stringify(evidence));}
  }
  if(!baseline){
   await p.evaluate(()=>draw('en'));
   assert.equal((await read(menu,{...options,blockId:'function_block_other'})).ready,false);
   assert.equal((await read(menu,{...options,effectIds:['effect_absent']})).ready,false);
   await assert.rejects(read(menu,{...options,effectIds:['effect_one','effect_one']}),/unique/);
   const button=menu.locator('[data-block-recipients="effect_one"]'),chips=button.locator(':scope > .effectRecipientChips');
   for(const raw of [null,'not-json','null','{}','["alice"]']){
    await chips.evaluate((n,raw)=>raw===null?n.removeAttribute('data-recipient-chips'):n.setAttribute('data-recipient-chips',raw),raw);
    const result=await read(menu,options);assert.equal(result.ready,false);assert.equal(result.rows[0].ready,false);
    assert.equal(result.rows[0].candidates[0].buttons[0].chips[0].raw_recipients,raw);
   }
   await p.evaluate(()=>draw('en'));
   await button.evaluate(n=>{n.setAttribute('aria-label','Wrong target');});assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await button.evaluate(n=>{n.dataset.blockRecipients='effect_other';});assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await button.evaluate(n=>{n.disabled=true;});assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await chips.evaluate(n=>{n.dataset.recipientsState='unset';});assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>{localEffects[0].config={};draw('en');});assert.equal((await read(menu,options)).ready,false,'Unset is not explicitly closed');
   await p.evaluate(()=>{localEffects[0].config={failure_recipients:['alice']};draw('en');});assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>{localEffects[0].config={failure_recipients:[]};draw('en');});
   await chips.evaluate(n=>n.hidden=true);assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await button.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await menu.locator('[data-block-object="effect_one"]').evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read(menu,options)).ready,false);
   await p.evaluate(()=>draw('en'));
   await menu.evaluate(n=>n.hidden=true);assert.equal((await read(menu,options)).ready,false);
   await menu.evaluate(n=>n.hidden=false);
   assert.equal((await read(menu,options)).ready,true);
  }
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
  assert.ok((await p.evaluate(()=>instructionCalls)).every(x=>x.name==='ListUsers'));
  console.log(JSON.stringify({status:baseline?'reproduced':'passed',labels,fixture:'Actual Function Block Menu renderer, native recipient fields/i18n/CSS; local dialog host and dependency fixtures',formal_acceptance:false,production_requests:0}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
