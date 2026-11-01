'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const portal = path.resolve(__dirname, '../../../management_portal');
const {chromium} = require(path.join(portal, 'test_framework/tau_runtime/node_modules/playwright'));
const prompt = fs.readFileSync(path.join(portal, 'test_framework/config/tau_runner_prompt.md'), 'utf8');
const example = prompt.match(/```js\n(\/\/ tau-created-component\n[\s\S]*?)\n```/);
assert.ok(example, 'public original component and Watch preflight contract');
const {createdHatgComponent, currentResidentWatchFields} = new Function(example[1] +
  ';return {createdHatgComponent,currentResidentWatchFields};')();
const component = {id:'event_fixture',kind:'event_source',title:'Finished',config:{event_type:'resident.work_finished'}};
const receipt = {status:'committed',result:{component_id:component.id,component}};
assert.equal(createdHatgComponent(receipt).id, component.id);
assert.equal(component.component_id, undefined, 'graph components use id, not the receipt field');
for (const bad of [{}, {status:'failed',result:receipt.result},
  {status:'committed',result:{component}},
  {status:'committed',result:{component_id:'other',component}}]) {
  assert.throws(()=>createdHatgComponent(bad), /Original committed component identity/);
}
(async()=>{
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      const name=new URL(route.request().url()).pathname;
      if(name.startsWith('/static/'))return route.fulfill({path:path.join(portal,name)});
      return route.fulfill({contentType:'text/html',body:'<!doctype html><form id="sample"></form>'});
    });
    await page.goto('http://component-preflight.fixture.invalid');
    for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives',
      'lineage_program','lineage_primitive_inspector','hatg_program_inspector_primitive_fields','hatg_program_inspector_saving']) {
      await page.addScriptTag({url:'/static/'+name+'.js'});
    }
    await page.evaluate(async component=>{
      window.PortalAPI={get:async url=>(await fetch(url)).json()};
      await PortalLineageProgram.loadPrimitiveDefinitions();
      const state={lineage:{lineage_id:'lineage_fixture',program:{components:[component],wires:[]}}};
      const dep=(module,provided,method)=>module[method](Object.fromEntries(module.dependencies.map(name=>[
        name,Object.hasOwn(provided,name)?provided[name]:(()=>{throw new Error('Unexpected dependency '+name);})])));
      const fields=dep(PortalHatgProgramInspectorPrimitiveFields,{
        esc:PortalUIPrimitives.escapeHtml,displayedProgram:()=>state.lineage.program,
        canonicalProgramResidentId:v=>String(v||'').trim(),programResidentOptions:()=>'',
        displayNodeById:()=>null,
      },'createPrimitiveFields');
      const form=document.getElementById('sample');
      form.innerHTML=fields.programPrimitiveInspectorHtml(component,'event','resident.work_finished',component.config,false);
      window.saved=[],window.feedback=[];
      const saving=dep(PortalHatgProgramInspectorSaving,{
        state,programComponentById:()=>component,isPacketOperatorComponent:()=>false,
        programPrimitiveDefaultType:()=>PortalLineageProgram.manifest.default_types.event,
        canonicalProgramResidentId:v=>String(v||'').trim(),definitionRevisions:new WeakMap([[form,1]]),
        programResidentReferenceError:id=>id?'':'Missing Resident ID',
        applyProgramMutation:callback=>callback(),toast:text=>feedback.push(text),
        societyService:{saveProgramComponent:async value=>{saved.push(value);return {component};}},
      },'createSaving');
      window.saveSample=()=>saving.saveProgramComponentFromInspector(component.id,form);
    },component);
    const form=page.locator('#sample');
    assert.deepEqual(await currentResidentWatchFields(form),[{value:'',disabled:false}]);
    await page.evaluate(()=>saveSample());
    assert.deepEqual(await page.evaluate(()=>saved),[],'native save rejects missing Watch before calling the service');
    assert.ok((await page.evaluate(()=>feedback)).length>0);
    const resident='resident_111111111111';
    await form.locator('input[name="subject_resident_id"]').fill(resident);
    assert.deepEqual(await currentResidentWatchFields(form),[{value:resident,disabled:false}]);
    await page.evaluate(()=>saveSample());
    const calls=await page.evaluate(()=>saved);
    assert.equal(calls.length,1);
    assert.equal(calls[0].component_id,component.id);
    assert.deepEqual(calls[0].component.watch_subject,{kind:'resident',id:resident});
    await form.locator('input[name="subject_resident_id"]').evaluate(node=>{node.disabled=true;});
    assert.equal((await currentResidentWatchFields(form))[0].disabled,true);
    await form.evaluate(node=>{node.hidden=true;});
    assert.deepEqual(await currentResidentWatchFields(form),[]);
    assert.deepEqual(errors,[]);
    console.log('Original component identity and native Event Watch preflight passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
