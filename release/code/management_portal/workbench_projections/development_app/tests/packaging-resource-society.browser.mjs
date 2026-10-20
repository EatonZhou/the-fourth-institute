import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const code=await build({stdin:{contents:"export {createSelectionEditor} from './src/packaging/selection-editor.js';export {normalizeChecklist} from './src/packaging/model.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'UI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.setContent('<main></main>');await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.ids={paper:'lineage_111111111111',article:'lineage_222222222222',experiment:'lineage_333333333333',concept:'lineage_444444444444',decision:'lineage_444444444444',skill:'lineage_555555555555',repo:'lineage_666666666666',device:'lineage_777777777777'};
  window.deleted=new Set();
  window.value={selection_version:3,manual_projects:[],manual_lineages:[],resource_types:[],projections:[]};
  const sdk={hatg:async(op,args)=>{
   if(op==='ListResourceTypes')return {types:Object.keys(ids).map(type=>({type,title:type}))};
   if(op==='SearchResources')return {items:[]};
   if(op==='GetProject')return {project:{resource_id:'project_123456789abc',title:'研究项目'}};
   if(op==='ResolveResource'&&deleted.has(args.address))throw Error('Resource not found');
   if(op==='ResolveResource')return {resource:{resource_id:args.address,resource_type:'lineage',title:'真实名称 '+args.address,attributes:{project_ids:['project_123456789abc']}}};
   throw Error(op);
  }};
  window.refresh=()=>{
   const s=editor.read(),reasons=new Map();
   for(const type of [...s.resource_types.map(r=>r.type),...(s.document_indexes?.['SPEC.md']?.enabled?['decision']:[])]){
    const id=ids[type];if(!id)continue;reasons.set(id,[...(reasons.get(id)||[]),'resource_types.'+type]);
   }
   for(const item of s.lineages)reasons.set(item.id,[...(reasons.get(item.id)||[]),'manual.lineages']);
   return editor.syncProjectionFeatures({resource_dependencies:{schema:'tfi.release-resource-dependencies.v3',lineages:[...reasons].map(([id,required_by])=>({id,required_by})),projects:[],projections:[]}});
  };
  window.mount=async()=>{window.controller?.abort();window.controller=new AbortController();document.querySelector('main').replaceChildren();window.editor=UI.createSelectionEditor(document.querySelector('main'),{sdk,signal:controller.signal,value,onChange:()=>void refresh()});await editor.ready();await refresh();await editor.settle();};await mount();
 });
 const select=async(type,on)=>{await page.locator(`[data-resource-type="${type}"] > label input`).setChecked(on);await page.evaluate(()=>editor.settle());};
 const row=id=>page.locator(`[data-lineage-id="${id}"]`);
 await select('paper',true);const paper=row('lineage_111111111111');assert.equal(await paper.count(),1);assert(await paper.locator('input').first().isChecked());assert(await paper.locator('input').first().isDisabled());assert(await paper.getByText('真实名称 lineage_111111111111',{exact:true}).count());
 assert.equal(await paper.getByLabel('自有工作成果',{exact:true}).inputValue(),'false');assert.equal(await paper.getByLabel('额外绑定 Workspace',{exact:true}).inputValue(),'exclude');
 await paper.getByLabel('保留经历',{exact:true}).check();await page.evaluate(()=>editor.settle());
 await paper.getByLabel('自有工作成果',{exact:true}).selectOption('true');await page.evaluate(()=>editor.settle());
 await paper.getByLabel('额外绑定 Workspace',{exact:true}).selectOption('partial');await page.evaluate(()=>editor.settle());
 await paper.getByLabel('额外绑定的部分内容范围',{exact:true}).fill('只带论文笔记');await page.evaluate(()=>editor.settle());
 await paper.getByLabel('排除 Workspace（ID、路径或说明）',{exact:true}).fill('排除缓存');await page.evaluate(()=>editor.settle());
 let snapshot=await page.evaluate(()=>UI.normalizeChecklist(editor.read(),{validate:false}));assert.deepEqual(snapshot.manual_lineages,[]);assert.deepEqual(snapshot.lineages,[]);assert.deepEqual(snapshot.manual_projects,[]);
 assert.equal(snapshot.resource_lineage_options.lineage_111111111111.history,true);assert.equal(snapshot.resource_lineage_options.lineage_111111111111.workspace.exclusions,'排除缓存');
 await page.evaluate(async()=>{value=UI.normalizeChecklist(editor.read(),{validate:false});await mount();});assert(await paper.getByLabel('保留经历',{exact:true}).isChecked());assert.equal(await paper.getByLabel('额外绑定的部分内容范围',{exact:true}).inputValue(),'只带论文笔记');
 assert.equal(await page.getByLabel(/同时手动保留/).count(),0);
 await select('paper',false);assert.equal(await paper.count(),0);snapshot=await page.evaluate(()=>editor.read());assert.equal(snapshot.lineages.length,0);assert(snapshot.resource_lineage_options.lineage_111111111111);
 await select('paper',true);assert(await paper.getByLabel('保留经历',{exact:true}).isChecked());
 await select('concept',true);await select('decision',true);const knowledge=row('lineage_444444444444');assert.equal(await knowledge.count(),1);await select('concept',false);assert.equal(await knowledge.count(),1);await select('decision',false);assert.equal(await knowledge.count(),0);
 await page.evaluate(async()=>{editor.setDocuments({'SPEC.md':{enabled:true,entries:['decision_123456789abc']}});await refresh();await editor.settle();});assert.equal(await knowledge.count(),1);
 for(const type of ['article','experiment','skill','repo','device'])await select(type,true);assert.equal(await page.locator('.packagingLineageEntry').count(),7);snapshot=await page.evaluate(()=>editor.read());assert(snapshot.resource_types.every(r=>r.instances.length===0));assert.deepEqual(snapshot.manual_lineages,[]);
 // A separately selected lineage survives removal of its resource dependency.
 await page.evaluate(async()=>{value={...editor.read(),manual_projects:['project_123456789abc'],manual_lineages:['lineage_111111111111'],lineages:[{id:'lineage_111111111111',project_id:'project_123456789abc',history:true,workspace:{own_outputs:true,extra_bindings:'all'}}]};await mount();});
 await select('paper',false);assert.equal(await paper.count(),1);assert(await paper.getByLabel('保留经历',{exact:true}).isChecked());assert.equal(await paper.getByLabel('额外绑定 Workspace',{exact:true}).inputValue(),'all');
 assert.equal(await paper.locator('input').first().isDisabled(),false);
 await page.evaluate(()=>{value=editor.read();});
 await paper.locator('input').first().click();await page.evaluate(()=>editor.settle());
 assert(!(await page.evaluate(()=>editor.read())).manual_lineages.includes('lineage_111111111111'));
 await page.evaluate(()=>mount());
 // Deleted selections remain removable evidence, never effective candidate controls.
 await page.evaluate(async()=>{deleted.add('lineage_111111111111');await editor.refresh();});
 assert.equal(await paper.count(),0);assert.equal(await page.locator('[data-invalid-lineage-id="lineage_111111111111"]').count(),1);
 await page.evaluate(async()=>{value=editor.read();await mount();});assert.equal(await paper.count(),0);
 await page.locator('[data-invalid-lineage-id="lineage_111111111111"]').getByRole('button',{name:'移除失效选择'}).click();await page.evaluate(()=>editor.settle());
 assert(!(await page.evaluate(()=>editor.read())).manual_lineages.includes('lineage_111111111111'));
 await select('paper',true);assert.equal(await paper.count(),0);assert.match(await page.locator('.packagingProjects').textContent(),/lineage_111111111111.*读取失败/);
 console.log(JSON.stringify({ok:true,checks:['type-only derived Society','default safe scope','editable options without manual promotion','normalize/reload retention','last dependency removal','shared knowledge dedupe','indirect document','device without instances','manual source survives']}));
}finally{await browser.close();}
