import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const code=await build({stdin:{contents:"export {createSelectionEditor} from './src/packaging/selection-editor.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'SelectionUI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.setContent('<main></main>');await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.lineageId='lineage_111111111111';window.projectionId='workbench_111111111111';window.lineageTitle='真实团队';window.denied=false;window.searchName='';
  window.sdk={hatg:async(op,args)=>{
   if(op==='ListResourceTypes')return {types:[]};
   if(op==='SearchResources')return {items:args.resource_type==='lineage'&&searchName?[{resource_id:lineageId,title:searchName,project:'project'}]:[]}; // Selected object outside the paginated/filtered search.
   if(op==='GetProject')return {project:{resource_id:'project'}};
   if(op==='ResolveResource'){
    if(args.address===lineageId){if(denied)throw Error('不可读取');return {resource:{resource_id:lineageId,resource_type:'lineage',title:lineageTitle,attributes:{project_ids:['project']}}};}
    return {resource:{resource_id:projectionId,resource_type:'workbench'}};
   }
   throw Error(op);
  },resource:async()=>({content:JSON.stringify({workbench_id:projectionId,title:'投影',bindings:{main:{lineage_id:lineageId}}})})};
  window.value={selection_version:3,manual_projects:[],manual_lineages:[],projections:[{id:projectionId,verified:true,dependencies:[{id:lineageId,project_id:'project'}]}],lineages:[{id:lineageId,project_id:'project',history:true,workspace:{own_outputs:true,extra_bindings:'partial',extra_scope:'保留正文'}}]};
  window.mount=async()=>{document.querySelector('main').replaceChildren();window.editor=SelectionUI.createSelectionEditor(document.querySelector('main'),{sdk,signal:new AbortController().signal,value,onChange:()=>{}});await editor.ready();};await mount();
 });
 const row=page.locator('[data-lineage-id="lineage_111111111111"]');
 assert.equal(await row.getByLabel('真实团队',{exact:true}).count(),1);
 assert.match(await row.innerText(),/lineage_111111111111/);
 assert(await row.getByLabel('保留经历',{exact:true}).isChecked());
 assert.equal(await row.getByLabel('额外绑定的部分内容范围',{exact:true}).inputValue(),'保留正文');
 await page.getByRole('button',{name:'搜索 Lineage',exact:true}).click();assert.equal(await row.getByLabel('真实团队',{exact:true}).count(),1);
 await page.evaluate(async()=>{value=editor.read();await mount();});assert.equal(await row.getByLabel('真实团队',{exact:true}).count(),1);
 await page.evaluate(async()=>{lineageTitle='团队新名称';await editor.refresh();});assert.equal(await row.getByLabel('团队新名称',{exact:true}).count(),1);
 await page.evaluate(async()=>{denied=true;await editor.refresh();});assert.equal(await row.getByLabel('团队新名称',{exact:true}).count(),0);assert.match(await row.innerText(),/名称待核实/);
 assert.equal(await page.evaluate(()=>editor.read().lineages[0].history),true);
 await page.evaluate(async()=>{denied=false;lineageTitle=null;searchName='搜索提供的名称';await editor.refresh();});
 await page.getByRole('button',{name:'搜索 Lineage',exact:true}).click();assert.equal(await row.getByLabel('搜索提供的名称',{exact:true}).count(),1);
 console.log(JSON.stringify({ok:true,checks:['automatic lineage outside search page','ID retained','filtered search','save and remount','rename','unreadable name fallback','scope unchanged']}));
}finally{await browser.close();}
