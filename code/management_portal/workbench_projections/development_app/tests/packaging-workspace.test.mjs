import test from 'node:test';
import assert from 'node:assert/strict';
import {migrateSelection,expandSelection,SPECIAL_TYPES,verifySelection} from '../src/packaging/selection-model.js';
import {normalizeChecklist,requestFor} from '../src/packaging/model.js';
const id='lineage_111111111111',other='lineage_222222222222';
const policy={own_outputs:true,extra_bindings:'partial',extra_scope:'  报告\n保留原文  ',exclusions:' workspace_111111111111\n/tmp/cache '};
const initial={selection_version:3,version:'workspace-check',source:'Release',destination:'/out',manual_projects:['A'],manual_lineages:[id],lineages:[{id,project_id:'A',history:false,workspace:policy}]};
test('workspace settings survive normalization, projections and history changes without selecting files implicitly',()=>{
 const draft=normalizeChecklist(initial);
 assert.deepEqual(draft.lineages[0].workspace,policy);
 const projected=expandSelection({...draft,projections:[{id:'workbench_111111111111',verified:true,dependencies:[{id,project_id:'A'},{id:other,project_id:'B'}]}]});
 assert.deepEqual(projected.lineages[0].workspace,policy);
 assert.deepEqual(projected.lineages[1].workspace,{own_outputs:null,extra_bindings:'unset',extra_scope:'',exclusions:''});
 projected.lineages[0].history=true;
 assert.deepEqual(normalizeChecklist(projected).lineages[0].workspace,policy);
 for(const own_outputs of [null,false,true])for(const extra_bindings of ['unset','exclude','all','partial']){
   const next=normalizeChecklist({...initial,lineages:[{...initial.lineages[0],workspace:{...policy,own_outputs,extra_bindings}}]});
   assert.equal(next.lineages[0].workspace.own_outputs,own_outputs);
   assert.equal(next.lineages[0].workspace.extra_bindings,extra_bindings);
   assert.equal(next.lineages[0].history,false);
   assert.equal(next.lineages[0].workspace.extra_scope,policy.extra_scope);
   assert.deepEqual(normalizeChecklist(next),next);
 }
 const missing=normalizeChecklist({...initial,lineages:[{id,project_id:'A',history:true}]});
 assert.equal(missing.lineages[0].workspace.own_outputs,null);
 assert.equal(missing.lineages[0].workspace.extra_bindings,'unset');
});
test('old workspace choices stay visible as pending original content, never independent exports or dependencies',async()=>{
 const legacy={type:'workspace',instances:['workspace_111111111111'],description:' 原有范围\n ',ownership:{workspace_111111111111:{project_ids:['B'],verified:true}}};
 const result=migrateSelection({...initial,resource_types:[legacy]});
 assert.equal(SPECIAL_TYPES.has('workspace'),true);
 assert.deepEqual(result.resource_types,[]);assert.deepEqual(result.pending_workspace_selections,[legacy]);
 assert.deepEqual(result.projects,[{id:'A'}]);
 assert.deepEqual(migrateSelection(result),result);
 const empty=migrateSelection({resource_types:[{type:'workspace',instances:[],description:''}]});
 assert.equal(empty.pending_workspace_selections.length,1);
 const verified=await verifySelection({hatg:async(name,args)=>{
   if(name==='ListResourceTypes')return {types:[]};
   if(name==='GetProject')return {project:{resource_id:args.project_id}};
   if(args.address===id)return {resource:{resource_id:id,resource_type:'lineage',attributes:{project_ids:['A']}}};
   throw Error('Workspace must not be resolved as a selected instance');
 }},result);
 assert.deepEqual(verified.pending_workspace_selections,[legacy]);assert.deepEqual(verified.issues,[]);
 const request=requestFor('workbench_111111111111',verified);
 assert.match(request.text,/workspaces\.md/);assert.match(request.text,/共享 Workspace.*去重/);
 assert.match(request.text,/源.*不变/);assert.match(request.text,/未设置.*不.*全带/);
 assert.match(request.text,/Resident Folder/);assert.match(request.text,/本地缓存/);
 const content=JSON.parse(request.text.split('发布清单：\n')[1]);
 assert.deepEqual(content.pending_workspace_selections,[legacy]);
 assert.deepEqual(content.lineages[0].workspace,policy);
});
