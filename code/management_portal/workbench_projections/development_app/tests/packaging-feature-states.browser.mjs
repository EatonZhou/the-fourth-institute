import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {resolveFeatureFixture} from './packaging-feature-fixture.mjs';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const code=await build({stdin:{contents:"export {featureStateEditor} from './src/packaging/feature-states.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'FeatureUI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.exposeFunction('resolveFeatures',resolveFeatureFixture);
 await page.setContent('<main><label><input id="a" type="checkbox">计时 A</label><label><input id="b" type="checkbox">计时 B</label><section></section><div class="packagingSelections"><div class="packagingSelectedInstance"><span>很长的资源名称 resource_111111111111</span><small>所属Project</small><button class="portalButton">移除实例</button></div></div></main>');
 await page.addStyleTag({content:await readFile('src/packaging/style.css','utf8')});await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.controller=new AbortController();window.saved={optional_features:{model_request_timing:true}};
  window.editor=FeatureUI.featureStateEditor(document.querySelector('section'),{sdk:{release:async(_,args)=>resolveFeatures(args.arguments.snapshot)},signal:controller.signal,value:saved,getSnapshot:()=>saved,onChange:()=>{saved={...saved,...editor.read()};}});
  editor.bind('model_request_timing',document.querySelector('#a'),'计时 A');editor.bind('model_request_timing',document.querySelector('#b'),'计时 B');await editor.ready();
 });
 // Required permission code still exposes its native installation default.
 await page.evaluate(()=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='permission';label.append(input,'权限系统');document.querySelector('main').append(label);editor.bind('permission_system',input,'权限系统');});
 const permission=page.locator('#permission'),permissionDefault=page.getByLabel('安装后默认开启 · 权限系统',{exact:true});
 assert(await permission.isChecked());assert(await permission.isDisabled());assert(await permissionDefault.isEnabled());
 await permissionDefault.selectOption('false');await page.waitForFunction(()=>editor.read().feature_states.permission_system.default_enabled===false);
 assert(await permission.isChecked());assert(await permission.isDisabled());assert.equal(await permissionDefault.inputValue(),'false');
 await editorRefresh();
 async function editorRefresh(){await page.evaluate(async()=>{await editor.refresh();});assert.equal(await permissionDefault.inputValue(),'false');}
 assert(await page.getByLabel('计时 A',{exact:true}).isChecked());assert(await page.getByLabel('计时 B',{exact:true}).isChecked());
 const a=page.getByLabel('安装后默认开启 · 计时 A',{exact:true}),b=page.getByLabel('安装后默认开启 · 计时 B',{exact:true});assert.equal(await a.inputValue(),'');
 await a.selectOption('false');await page.waitForFunction(()=>editor.read().feature_states.model_request_timing.default_enabled===false);assert.equal(await b.inputValue(),'false');
 await page.getByLabel('计时 B',{exact:true}).uncheck();await page.waitForFunction(()=>!editor.included('model_request_timing'));assert(await a.isDisabled());assert.equal(await a.inputValue(),'false');
 await page.getByLabel('计时 A',{exact:true}).check();await page.waitForFunction(()=>editor.included('model_request_timing'));assert.equal(await b.inputValue(),'false');
 assert.equal(await page.evaluate(()=>saved.feature_states_origin),undefined);
 await page.getByLabel('tau',{exact:true}).check();await page.waitForFunction(()=>editor.included('test_framework'));assert(await page.getByLabel('test_framework',{exact:true}).isDisabled());
 await page.getByLabel('安装后默认开启 · test_framework',{exact:true}).selectOption('false');await page.getByLabel('安装后默认开启 · tau',{exact:true}).selectOption('true');
 await page.waitForFunction(()=>document.body.textContent.includes('Runtime dependency explicitly disabled'));
 assert.match(await page.evaluate(async()=>{try{await editor.validate();return '';}catch(e){return e.message;}}),/冲突/);
 for(const width of [1100,400]){await page.setViewportSize({width,height:700});const row=await page.locator('.packagingSelectedInstance').boundingBox(),button=await page.getByRole('button',{name:'移除实例',exact:true}).boundingBox(),name=await page.locator('.packagingSelectedInstance>span').boundingBox();assert(button.x>=name.x+name.width);assert(button.x+button.width<=row.x+row.width+1);assert.equal(Math.round(button.y),Math.round(name.y));}
 await page.evaluate(async()=>{
  const root=document.createElement('section');root.id='migration';document.body.append(root);
  const legacy={projects_page:true};
  window.legacyEditor=FeatureUI.featureStateEditor(root,{sdk:{release:async()=>{const result=await resolveFeatures(legacy);result.resolution.states.projects.requested_included=null;return result;}},signal:controller.signal,value:legacy,getSnapshot:()=>legacy,onChange:()=>{}});
  const input=document.createElement('input');input.type='checkbox';input.id='legacy-project';input.checked=true;root.append(input);legacyEditor.bind('projects',input,'旧Projects');
  await legacyEditor.ready();
 });
 await page.evaluate(async()=>{
  const root=document.createElement('section');document.body.append(root);let calls=0;
  const legacy={projects_page:true,test_view:false,optional_features:{model_request_timing:true}};
  window.migratedEditor=FeatureUI.featureStateEditor(root,{sdk:{release:async(_,args)=>{calls++;const result=await resolveFeatures(args.arguments.snapshot);if(!args.arguments.snapshot.feature_states){result.resolution.states.projects.requested_included=null;result.resolution.states.test_view.requested_included=null;}return result;}},signal:controller.signal,value:legacy,getSnapshot:()=>legacy,onChange:()=>{}});
  await migratedEditor.ready();window.migrationCalls=calls;
 });
 assert.equal(await page.evaluate(()=>migrationCalls),2);
 assert.deepEqual(await page.evaluate(()=>migratedEditor.read().feature_states.projects),{included:true,default_enabled:null});
 assert.deepEqual(await page.evaluate(()=>migratedEditor.read().feature_states.test_view),{included:false,default_enabled:null});
 assert.equal(await page.evaluate(()=>migratedEditor.read().feature_states.model_request_timing.included),true);
 assert.match(await page.locator('#migration').innerText(),/保留原选择/);assert(await page.locator('#legacy-project').isChecked());
 assert.deepEqual(await page.evaluate(()=>legacyEditor.read()),{});
 assert.match(await page.evaluate(async()=>{try{await legacyEditor.validate();return '';}catch(e){return e.message;}}),/尚未被服务迁移/);
 // Code support has no runtime switch; preserve historical intent across edits/reload.
 await page.evaluate(async()=>{
  const root=document.createElement('section');root.id='code-support';document.body.append(root);
  window.codeSnapshot={feature_states:{support:{included:true,default_enabled:false}}};
  window.codeSDK={release:async(_,args)=>{const s=args.arguments.snapshot.feature_states.support;return {features:[{id:'support',title:'依赖代码',kind:'code_support',default_applicable:false}],resolution:{states:{support:{...s,requested_included:s.included,kind:'code_support',default_applicable:false,resolved_default_enabled:null,effective_enabled:null,code_required_by:['consumer'],code_required_by_details:[{id:'consumer',title:'实际消费功能',source:'resource_types.paper'}]}},included_features:['support'],conflicts:[]}};}};
  window.codeEditor=FeatureUI.featureStateEditor(root,{sdk:codeSDK,signal:controller.signal,value:codeSnapshot,getSnapshot:()=>codeSnapshot,onChange:()=>{codeSnapshot={...codeSnapshot,...codeEditor.read()};}});await codeEditor.ready();
 });
 const support=page.locator('#code-support');
 assert.equal(await support.getByLabel('安装后默认开启 · 依赖代码').isVisible(),false);
 assert.match(await support.innerText(),/依赖支持.*无独立运行开关/s);
 assert.match(await support.innerText(),/实际消费功能.*resource_types.paper/s);
 assert.match(await support.innerText(),/旧默认意图：关闭.*不执行/s);
 assert.deepEqual(await page.evaluate(()=>codeEditor.read().feature_states.support),{included:true,default_enabled:false});
 await page.evaluate(async()=>{const root=document.querySelector('#code-support');root.replaceChildren();codeSnapshot=JSON.parse(JSON.stringify(codeEditor.read()));codeEditor=FeatureUI.featureStateEditor(root,{sdk:codeSDK,signal:controller.signal,value:codeSnapshot,getSnapshot:()=>codeSnapshot,onChange:()=>{}});await codeEditor.ready();await codeEditor.validate();});
 assert.equal(await support.getByLabel('安装后默认开启 · 依赖代码').isVisible(),false);
 assert.equal(await page.evaluate(()=>codeEditor.read().feature_states.support.default_enabled),false);
 // Dependency relationships come from the service and must never become manual selections.
 await page.evaluate(async()=>{
  const root=document.createElement('section');root.id='dependencies';document.body.append(root);
  window.dependencySnapshot={resource_types:[{type:'concept'}],lineages:[]};
  window.dependencyResponse={schema:'tfi.release-resource-dependencies.v1',types:['concept'],type_reasons:{concept:['resource_types.concept']},pages:[{id:'resource:concept',provider:'knowledge'}],projections:[{id:'workbench_fixture',revision:7,required_by:['resource:concept'],bindings:[{alias:'main',lineage_id:'lineage_fixture'}]}],lineages:[{id:'lineage_fixture',required_by:['resource:concept']}],projects:[{id:'project_fixture',required_by:['lineage_fixture']}],edges:[],gaps:[],valid:true};
  window.dependencyEditor=FeatureUI.featureStateEditor(root,{sdk:{release:async()=>{const result=await resolveFeatures(dependencySnapshot);result.resolution.resource_dependencies=dependencyResponse;return result;}},signal:controller.signal,value:dependencySnapshot,getSnapshot:()=>dependencySnapshot,onChange:()=>{}});
  await dependencyEditor.ready();
 });
 const deps=page.locator('#dependencies .packagingResourceDependencies');
 assert.equal(await deps.evaluate(el=>el.open),false);
 await deps.locator('summary').click();
 assert.match(await deps.innerText(),/workbench_fixture.*修订 7/s);
 assert.match(await deps.innerText(),/lineage_fixture.*经历：不带出.*自有成果：不带出.*额外绑定：不带出/s);
 assert.match(await deps.innerText(),/project_fixture/);
 assert.equal(await page.evaluate(()=>Object.hasOwn(dependencyEditor.read(),'lineages')),false);
 await page.evaluate(async()=>{dependencyResponse.schema='tfi.release-resource-dependencies.v2';dependencySnapshot.lineages=[{id:'lineage_fixture',history:true,workspace:{own_outputs:true,extra_bindings:'partial',extra_scope:'选定报告'}}];await dependencyEditor.refresh();});
 assert.equal(await deps.evaluate(el=>el.open),true);
 assert.match(await deps.innerText(),/显式选择.*经历：带出.*自有成果：带出.*部分.*选定报告/s);
 await page.evaluate(async()=>{dependencySnapshot={resource_types:[],lineages:[]};dependencyResponse={schema:'tfi.release-resource-dependencies.v1',types:[],pages:[],projections:[],lineages:[],projects:[],gaps:[],valid:true};await dependencyEditor.refresh();});
 assert.doesNotMatch(await deps.innerText(),/lineage_fixture|project_fixture/);
 await page.evaluate(async()=>{dependencySnapshot.resource_types=[{type:'missing'}];dependencyResponse.gaps=[{id:'missing',reason:'No published binding'}];dependencyResponse.valid=false;await dependencyEditor.refresh();});
 assert.match(await deps.innerText(),/No published binding/);
 assert.match(await page.evaluate(async()=>{try{await dependencyEditor.validate();return '';}catch(e){return e.message;}}),/资源依赖/);
 await page.evaluate(async()=>{dependencyResponse=undefined;dependencySnapshot.resource_types=[];await dependencyEditor.refresh();});
 assert.match(await deps.innerText(),/尚未提供/);
 assert.doesNotMatch(await deps.innerText(),/No published binding/);
 await page.evaluate(async()=>{
  const root=document.createElement('section');root.id='native-status';document.body.append(root);
  const snapshot={feature_states:{pulse:{included:true,default_enabled:true}}};
  const response={features:[{id:'pulse',title:'Pulse',adapter:'native_component_switch'}],resolution:{states:{pulse:{included:true,requested_included:true,default_enabled:true,resolved_default_enabled:true,default_source:'explicit',readiness:'ready',mapping_readiness:'ready',initialization_status:'pending_installation',mapping_diagnostics:[]}},conflicts:[]}};
  window.nativeResponse=response;
  window.nativeEditor=FeatureUI.featureStateEditor(root,{sdk:{release:async()=>response},signal:controller.signal,value:snapshot,getSnapshot:()=>snapshot,onChange:()=>{}});
  await nativeEditor.ready();
 });
 assert.match(await page.locator('#native-status').innerText(),/映射：ready.*初始化：待安装应用/s);
 await page.evaluate(async()=>{Object.assign(nativeResponse.resolution.states.pulse,{mapping_readiness:'needs_configuration',mapping_diagnostics:['Missing ordinary Lineage binding'],resolved_default_enabled:null,readiness:'needs_configuration'});await nativeEditor.validate();});
 assert.match(await page.locator('#native-status').innerText(),/Missing ordinary Lineage binding/);
 await page.evaluate(async()=>{Object.assign(nativeResponse.resolution.states.pulse,{default_enabled:null,resolved_default_enabled:null,readiness:'code_only',initialization_status:'not_requested'});await nativeEditor.validate();});
 assert.match(await page.locator('#native-status').innerText(),/初始化：未请求/);
 console.log(JSON.stringify({ok:true,checks:['shared canonical state','null source','included/off','disabled default retained','dependencies and conflict','instance action right aligned at desktop/mobile','server resource dependencies and revisions','automatic defaults versus explicit scope','dependency removal without manual expansion','gap and missing-contract states']}));
}finally{await browser.close();}
