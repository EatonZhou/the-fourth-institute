import test from 'node:test';
import assert from 'node:assert/strict';
import { migrateSelection, expandSelection, readProjection, verifySelection } from '../src/packaging/selection-model.js';
const a='lineage_111111111111',b='lineage_222222222222',w='workbench_111111111111';
const projection={id:w,title:'跨项目投影',verified:true,dependencies:[{id:a,project_id:'A'},{id:b,project_id:'B'}]};
test('service-created modern snapshots retain explicit selections without frontend provenance',()=>{
 const result=migrateSelection({selection_version:3,projects:[{id:'A'}],lineages:[{id:a,project_id:'A',history:true,workspace:{own_outputs:false,extra_bindings:'exclude'}}]});
 assert.deepEqual(result.manual_projects,['A']);assert.deepEqual(result.manual_lineages,[a]);
 assert.equal(result.lineages[0].history,true);assert.equal(result.lineages[0].workspace.own_outputs,false);
 assert.deepEqual(migrateSelection({...result,manual_projects:[],manual_lineages:[]}).lineages,[]);
});
test('projection closure preserves manual choices, does not infer history, and removes only automatic dependencies',()=>{
 const initial=migrateSelection({selection_version:2,projects:[{id:'A'}],lineages:[{id:a,project_id:'A',history:true}]});
 const selected=expandSelection({...initial,projections:[projection]});
 assert.deepEqual(selected.projects,[{id:'A'},{id:'B'}]);
 assert.equal(selected.lineages.find(v=>v.id===a).history,true);
 assert.equal(selected.lineages.find(v=>v.id===b).history,false);
 const removed=expandSelection({...selected,projections:[]});
 assert.deepEqual(removed.projects,[{id:'A'}]);assert.deepEqual(removed.lineages,initial.lineages);
 const withoutManual=expandSelection({...selected,manual_projects:[],manual_lineages:[]});
 assert.equal(withoutManual.lineages.length,2);
 assert.deepEqual(expandSelection({...withoutManual,projections:[]}).projects,[]);
});
test('obsolete checklist options are discarded without guessing new selections or losing current notes',()=>{
 const value={legacy_selections:[{category:'business',value:'Article'}],categories:{business:['Paper','Projects','Repo'],projections:['开发投影'],data:['  全部讨论\n保留格式  '],docs:['README']},notes:'  原文\n '};
 const result=migrateSelection(value);
 assert.deepEqual(result.resource_types,[]);assert.deepEqual(result.projections,[]);
 assert.equal(result.projects_page,true);assert.equal(result.notes,value.notes);
 assert.equal(Object.hasOwn(result,'legacy_selections'),false);
 assert.deepEqual(result.categories,{docs:['README']});
 assert.deepEqual(migrateSelection(result),result);
});
const sdk=(denied=false,empty=false)=>({
 resource:async()=>({content:JSON.stringify({workbench_id:w,title:'投影',bindings:empty?{}:{main:{lineage_id:a},other:{lineage_id:b}}})}),
 hatg:async(name,args)=>{
  if(name==='ListResourceTypes')return {types:[{type:'paper'},{type:'repo'}]};
  if(name==='GetProject')return {project:{resource_id:args.project_id}};
  if(denied&&args.address===b)throw Error('权限不足');
  const type=args.address===w?'workbench':args.address.startsWith('paper_')?'paper':'lineage';
  return {target:{resource_type:type},resource:{resource_id:args.address,...(type==='lineage'?{resource_type:type}:{}),attributes:{project_ids:[args.address===b?'B':'A']}}};
 }
});
test('instances recover verified Project ownership, retain manual/dependent projects, and never add Lineages',async()=>{
 const instance='paper_111111111111', second='paper_222222222222';
 const api={hatg:async(name,args)=>{
   if(name==='ListResourceTypes')return {types:[{type:'paper'}]};
   if(name==='GetProject')return {project:{resource_id:args.project_id}};
   return {resource:{resource_id:args.address,title:'论文',project_ids:['A','B']},target:{resource_type:'paper'}};
 }};
 const restored=await verifySelection(api,{selection_version:3,manual_projects:['C'],resource_types:[{type:'paper',instances:[instance,second],description:' 原文 '}]});
 assert.deepEqual(restored.projects,[{id:'A'},{id:'B'},{id:'C'}]);assert.deepEqual(restored.lineages,[]);
 assert.deepEqual(restored.resource_types[0].ownership[instance].project_ids,['A','B']);
 assert.deepEqual(migrateSelection(restored),restored);
 const one=expandSelection({...restored,resource_types:[{...restored.resource_types[0],instances:[second]}]});
 assert.deepEqual(one.projects,restored.projects);
 assert.deepEqual(expandSelection({...one,resource_types:[]}).projects,[{id:'C'}]);
 const denied={hatg:async(name,args)=>{if(name==='GetProject')throw Error('无权限');return api.hatg(name,args);}};
 const pending=await verifySelection(denied,restored);
 assert.ok(pending.issues.length);assert.equal(pending.resource_types[0].ownership[instance].verified,false);
 assert.deepEqual(pending.projects,restored.projects);
 const unowned={hatg:async(name,args)=>name==='ResolveResource'?{resource:{resource_id:args.address,project_ids:[]},target:{resource_type:'paper'}}:api.hatg(name,args)};
 assert.deepEqual((await verifySelection(unowned,restored)).projects,[{id:'C'}]);
 const unknown={hatg:async(name,args)=>name==='ResolveResource'?{resource:{resource_id:args.address},target:{resource_type:'paper'}}:api.hatg(name,args)};
 assert.match((await verifySelection(unknown,restored)).issues.join(),/归属/);
});
test('real bindings require every lineage and project; missing or denied bindings fail',async()=>{
 const found=await readProjection(sdk(),w);assert.deepEqual(found.dependencies,projection.dependencies);
 await assert.rejects(readProjection(sdk(true),w),/权限不足/);
 await assert.rejects(readProjection(sdk(false,true),w),/绑定/);
});
test('type-only selection means no instances; IDs verified against registered type; free text preserved',async()=>{
 const description='  论文范围\n由团队理解  ';
 const initial=migrateSelection({selection_version:3,resource_types:[{type:'paper',instances:[],description}],society_description:'  团队  ',projection_description:'\n页面\n'});
 const verified=await verifySelection(sdk(),initial);
 assert.deepEqual(verified.resource_types[0].instances,[]);assert.equal(verified.resource_types[0].description,description);
 assert.equal(verified.society_description,'  团队  ');assert.equal(verified.projection_description,'\n页面\n');
 const wrong=await verifySelection(sdk(),{...initial,resource_types:[{type:'repo',instances:['paper_111111111111'],description:''}]});
 assert.ok(wrong.issues.length);assert.deepEqual(wrong.resource_types[0].instances,['paper_111111111111']);
 const invalid=await verifySelection(sdk(true),{...initial,projections:[projection]});
 assert.equal(invalid.projections[0].verified,false);assert.ok(invalid.issues.length);
 const history=await verifySelection(sdk(true),{...initial,projections:[projection],lineages:[{id:a,project_id:'A',history:true},{id:b,project_id:'B',history:true}]});
 assert.equal(history.lineages.find(v=>v.id===b).history,true);
 assert.deepEqual(history.projects,[{id:'A'},{id:'B'}]);
});
