import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {resolveFeatureFixture} from './packaging-feature-fixture.mjs';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const id='workbench_abcdefabcdef',lineage='lineage_111111111111';
const code=await build({stdin:{contents:"export {featureStateEditor} from './src/packaging/feature-states.js';export {createSelectionEditor} from './src/packaging/selection-editor.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'UI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.exposeFunction('resolveFeatures',snapshot=>{
  const result=resolveFeatureFixture(snapshot);result.features.find(v=>v.id==='test_view').projection_builtin='test';
  const s=result.resolution.states.test_view;s.code_required_by=[];const manual=snapshot.projections?.some(p=>p.id===id);if(manual||snapshot.externalRequired)s.included=true;
  if(s.included){s.code_required_by.push('explicit_resolution','projections.'+id);result.resolution.states.test_framework.included=true;}
  result.resolution.resource_dependencies={schema:'tfi.release-resource-dependencies.v2',projections:s.included?[{id,builtin:'test',selection_feature:'test_view',required_by:[...(snapshot.externalRequired?['resource:test']:[]),...(manual?['manual.projections']:[]),...(s.requested_included?['feature_states.test_view']:[])],bindings:[{alias:'main',lineage_id:lineage}],revision:7}]:[],lineages:[],projects:[],types:[],gaps:[],valid:true};return result;
 });await page.setContent('<section id="features"></section><main></main>');await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(async({id,lineage})=>{
  window.value={test_view:true,selection_version:3,manual_projects:[],manual_lineages:[],projections:[]};window.id=id;window.lineage=lineage;
  window.sdk={release:async(_,a)=>resolveFeatures(a.arguments.snapshot),hatg:async(op,args)=>{
   if(op==='ListResourceTypes')return {types:[]};if(op==='SearchResources')return {items:args.resource_type==='workbench'?[{resource_id:id,title:'测试工作台'}]:[]};if(op==='GetProject')return {project:{resource_id:'project'}};
   if(op==='ResolveResource')return {resource:args.address===id?{resource_id:id,resource_type:'workbench'}:{resource_id:lineage,resource_type:'lineage',title:'团队',attributes:{project_ids:['project']}}};throw Error(op);
  },resource:async()=>({content:JSON.stringify({workbench_id:id,builtin:'test',title:'测试工作台',revision:7,bindings:{main:{lineage_id:lineage}}})})};
  window.mount=async()=>{window.controller?.abort();window.controller=new AbortController();document.querySelector('main').replaceChildren();document.querySelector('#features').replaceChildren();
   window.features=UI.featureStateEditor(document.querySelector('#features'),{sdk,signal:controller.signal,value,getSnapshot:()=>({...editor.read(),...features.read(),externalRequired:window.externalRequired===true}),onChange:()=>{},onResolved:r=>editor.syncProjectionFeatures?.(r)});
   window.editor=UI.createSelectionEditor(document.querySelector('main'),{sdk,signal:controller.signal,value,featureStates:features,onChange:()=>{void features.refresh();}});await editor.ready();if(!window.deferFeatures){await features.ready();await editor.settle();}};await mount();
 },{id,lineage});
 const rows=page.locator(`[data-projection-id="${id}"]`),box=rows.getByLabel('测试工作台',{exact:true});await box.waitFor();assert.equal(await rows.count(),1);assert(await box.isChecked());assert(!(await box.isDisabled()));assert.equal(await page.getByText('Test 测试页面（原生开发工作区）',{exact:true}).count(),0);assert.equal(await page.locator('#features input[value="test_view"]').count(),0);
 await page.evaluate(async()=>{window.externalRequired=true;await features.refresh();await editor.settle();});assert(await box.isDisabled());
 await page.evaluate(async()=>{window.externalRequired=false;await features.refresh();await editor.settle();});assert(!(await box.isDisabled()));
 await box.uncheck();await page.evaluate(async()=>{await editor.settle();await features.settle();});assert.equal(await page.evaluate(()=>features.read().feature_states.test_view.included),false);assert.equal(await page.evaluate(()=>editor.read().projections.length),0);
 await box.check();await page.evaluate(async()=>{await editor.settle();await features.settle();});assert.equal(await page.evaluate(()=>editor.read().projections[0].id),id);assert(await box.isChecked());assert(!(await box.isDisabled()));
 await box.uncheck();await page.evaluate(async()=>{await editor.settle();await features.settle();value={...editor.read(),...features.read(),test_view:true};await mount();});assert.equal(await page.evaluate(()=>features.read().feature_states.test_view.included),false);assert.equal(await page.evaluate(()=>editor.read().projections.length),0);
 await page.evaluate(async()=>{window.deferFeatures=true;value={...value,feature_states:{test_view:{included:false,default_enabled:null}},projections:[]};await mount();});
 assert.equal(await page.evaluate(()=>features.isReady()),false);
 await box.check();await page.evaluate(async()=>{await editor.settle();await features.settle();});
 assert.equal(await page.evaluate(()=>features.read().feature_states.test_view.included),true);
 assert.equal(await page.evaluate(()=>editor.read().projections[0].id),id);
 console.log(JSON.stringify({ok:true,checks:['actual builtin identity','single projection row','legacy true dependency','select/remove manual and canonical','stale legacy true cannot restore','no invented ID']}));
}finally{await browser.close();}
