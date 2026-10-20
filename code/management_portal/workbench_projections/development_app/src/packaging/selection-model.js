import {workspaceOptions} from './workspace.js';
import {documentRequirements} from './document-dependencies.js';
import {PORTAL_SYSTEM} from './system-flows.js';
export const SOCIETY_TYPES = new Set(['project','lineage','resident','family']);
export const SPECIAL_TYPES = new Set([...SOCIETY_TYPES,'workbench','workspace']);
const unique = values => [...new Set(values)];
export const rawText = value => typeof value === 'string' ? value : '';
// ResolveResource retains provider-specific records; target carries the
// canonical type even when a record omits resource_type (knowledge/workbench).
export const resolvedType = result => result?.target?.resource_type || result?.resource?.resource_type;

export function migrateSelection(value={}) {
  const modern=value.selection_version===3;
  const {legacy_selections:_discarded,...current}=value;
  const categories=Object.fromEntries(Object.entries(value.categories||{}).filter(([key])=>!['business','projections','data'].includes(key)));
  const oldTests=categories.tests||[];
  if(categories.tests)categories.tests=unique(oldTests.filter(v=>v!=='Test Workbench').map(v=>v==='公开版本测试用例'?'测试用例':v));
  return expandSelection({...current,categories,selection_version:3,
    test_view:value.test_view===true,
    pending_test_view:value.pending_test_view===true||oldTests.includes('Test Workbench'),
    manual_projects:unique(modern&&Array.isArray(value.manual_projects)?value.manual_projects:(value.projects||[]).map(v=>v.id)),
    manual_lineages:unique(modern&&Array.isArray(value.manual_lineages)?value.manual_lineages:(value.lineages||[]).map(v=>v.id)),
    excluded_device_selections:[...new Map([...(value.excluded_device_selections||[]),...(value.resource_types||[]).filter(v=>v.type==='device'&&((v.instances||[]).length||v.description))].map(v=>[JSON.stringify(v),structuredClone(v)])).values()],
    pending_workspace_selections:[...new Map([...(value.pending_workspace_selections||[]),...(value.resource_types||[]).filter(v=>v.type==='workspace')].map(v=>[JSON.stringify(v),structuredClone(v)])).values()],
    resource_types:(value.resource_types||[]).filter(v=>v.type!=='workspace'&&v.manual!==false).map(v=>v.type==='device'?{...v,instances:[],description:'',ownership:{}}:v).map(v=>({type:v.type,instances:unique(v.instances||[]),description:rawText(v.description),
      ownership:Object.fromEntries((v.instances||[]).filter(id=>v.ownership?.[id]).map(id=>[id,v.ownership[id]]))})),
    projections:value.projections||[],
    projects_page:modern?value.projects_page===true:(value.categories?.business||[]).includes('Projects'),
    society_description:rawText(value.society_description),projection_description:rawText(value.projection_description),
    issues:value.issues||[],
  });
}

// Resource relationships are a derived view, never new manual selections.
export function expandSelection(value, resourceDependencies) {
  const projects=new Map((value.manual_projects||[]).map(id=>[id,{id}]));
  const old=new Map((value.lineages||[]).map(v=>[v.id,v]));
  const lineages=new Map((value.manual_lineages||[]).filter(id=>projects.has(old.get(id)?.project_id)).map(id=>[id,old.get(id)]));
  if(value.system_lineage?.project_id){
    const project_id=value.system_lineage.project_id;projects.set(project_id,{id:project_id});
    const previous=old.get(PORTAL_SYSTEM);lineages.set(PORTAL_SYSTEM,{id:PORTAL_SYSTEM,project_id,history:previous?.history===true,workspace:workspaceOptions(previous?.workspace)});
  }
  for(const selection of value.resource_types||[])for(const id of selection.instances||[])
    for(const project of selection.ownership?.[id]?.project_ids||[])projects.set(project,{id:project});
  for(const item of documentRequirements(value.document_indexes))for(const id of item.project_ids)projects.set(id,{id});
  // Keep last-known dependencies and history visible if a binding becomes
  // unreadable. Verification blocks submission until resolved or deselected.
  for(const projection of value.projections||[])for(const dependency of projection.dependencies||[]) {
    projects.set(dependency.project_id,{id:dependency.project_id});
    const previous=old.get(dependency.id);
    lineages.set(dependency.id,{id:dependency.id,project_id:dependency.project_id,history:previous?.history===true,workspace:workspaceOptions(previous?.workspace)});
  }
  for(const project of resourceDependencies?.projects||[])projects.set(project.id,{id:project.id});
  for(const dependency of resourceDependencies?.lineages||[]) {
    if(!dependency.project_id)continue;
    projects.set(dependency.project_id,{id:dependency.project_id});
    if(lineages.has(dependency.id))continue;
    const options=value.resource_lineage_options?.[dependency.id]||{history:false,workspace:{own_outputs:false,extra_bindings:'exclude'}};
    lineages.set(dependency.id,{id:dependency.id,project_id:dependency.project_id,history:options.history===true,workspace:workspaceOptions(options.workspace)});
  }
  return {...value,projects:[...projects.values()].sort((a,b)=>a.id.localeCompare(b.id)),
    lineages:[...lineages.values()].map(v=>({...v,workspace:workspaceOptions(v.workspace)})).sort((a,b)=>a.id.localeCompare(b.id))};
}

export function projectionReasons(value, id, project=false) {
  const required=value.system_lineage?.project_id&&(project?value.system_lineage.project_id===id:id===PORTAL_SYSTEM);
  return [...(required?['Portal System 默认带出']:[]),...(value.projections||[]).filter(v=>(v.dependencies||[]).some(d=>(project?d.project_id:d.id)===id)).map(v=>`${v.title||v.id}${v.verified?'':'（关联待核实）'}`)];
}

export function resourceReasons(value,project) {
  return [...documentRequirements(value.document_indexes).filter(v=>v.project_ids.includes(project)).map(v=>`${v.kind} 所选正文：${v.entries.join('、')}`),...(value.resource_types||[]).flatMap(selection=>(selection.instances||[]).filter(id=>selection.ownership?.[id]?.project_ids?.includes(project)).map(id=>{
    const item=selection.ownership[id];return `${item.title||id} · ${id}${item.verified?'':'（归属待核实）'}`;
  }))];
}

// Read authoritative provider ownership. The canonical search summary supplies
// project for providers whose ResolveResource record has no project_ids field.
export async function readInstance(sdk,type,id) {
  if(type==='device'||/^device_/.test(id))throw Error('Device 实例暂不支持带出');
  const result=await sdk.hatg('ResolveResource',{address:id}),record=result.resource;
  if(record?.resource_id!==id||resolvedType(result)!==type)throw Error('资源类型不匹配或不可读取');
  let owners=record.project_ids??record.attributes?.project_ids;
  if(owners===undefined){
    const found=await sdk.hatg('SearchResources',{resource_type:type,q:id,limit:30});
    const item=found.items?.find(v=>v.resource_id===id);
    if(item&&Object.hasOwn(item,'project'))owners=typeof item.project==='string'?(item.project?[item.project]:[]):item.project;
  }
  if(!Array.isArray(owners)||owners.some(v=>typeof v!=='string'||!v.trim()))throw Error('资源 Project 归属无法核实');
  const project_ids=unique(owners).sort(),errors=[];
  for(const project_id of project_ids)try{
    if((await sdk.hatg('GetProject',{project_id})).project?.resource_id!==project_id)throw Error('Project 不可读取');
  }catch(error){errors.push(`${project_id}：${error.message}`);}
  return {title:typeof record.title==='string'?record.title:id,project_ids,verified:!errors.length,...(errors.length?{error:errors.join('；')}:{})};
}

export function lineageProject(resource) {
  const owners=resource?.attributes?.project_ids;
  return resource?.resource_type==='lineage'&&Array.isArray(owners)&&owners.length===1&&typeof owners[0]==='string'?owners[0]:'';
}
export async function readLineage(sdk,id) {
  const result=await sdk.hatg('ResolveResource',{address:id}), project_id=lineageProject(result.resource);
  if(result.resource?.resource_id!==id||!project_id)throw Error(`${id}：Lineage 归属无法核实`);
  const project=await sdk.hatg('GetProject',{project_id});
  if(project.project?.resource_id!==project_id)throw Error(`${id}：Project 不可读取`);
  return {id,project_id};
}
export async function readProjection(sdk,id) {
  const resolved=await sdk.hatg('ResolveResource',{address:id});
  if(resolved.resource?.resource_id!==id||resolvedType(resolved)!=='workbench')throw Error('请选择可读的 Workbench ID');
  const result=await sdk.resource('read',{address:id});
  const content=result.encoding==='base64'?new TextDecoder().decode(Uint8Array.from(atob(result.content),v=>v.charCodeAt(0))):result.content;
  const app=JSON.parse(content);
  if(app.workbench_id!==id||!app.bindings||typeof app.bindings!=='object'||Array.isArray(app.bindings))throw Error('投影绑定无法核实');
  const bindings=Object.values(app.bindings);
  if(!bindings.length||bindings.some(v=>!/^lineage_[0-9a-f]{12}$/.test(v?.lineage_id||'')))throw Error('投影缺少有效的 Lineage 绑定');
  const dependencies=[];
  for(const lineage of unique(bindings.map(v=>v.lineage_id)))dependencies.push(await readLineage(sdk,lineage));
  return {id,title:app.title||id,...(app.builtin?{builtin:app.builtin}:{}),revision:app.revision,dependencies,verified:true};
}

export async function verifySelection(sdk,value) {
  const result=migrateSelection(value),issues=[];
  const registry=await sdk.hatg('ListResourceTypes',{}),types=new Set(registry.types.map(v=>v.type));
  for(const selection of result.resource_types) {
    if(!types.has(selection.type)||SPECIAL_TYPES.has(selection.type)){issues.push(`${selection.type}：类型未登记或须在专用区域选择`);continue;}
    for(const id of selection.instances)try {
      const item=await readInstance(sdk,selection.type,id);selection.ownership[id]=item;
      if(!item.verified)issues.push(`${id}：${item.error}`);
    }catch(error){selection.ownership[id]={...selection.ownership[id],verified:false,error:error.message};issues.push(`${id}：${error.message}`);}
  }
  for(const project of expandSelection(result).projects)try {
    if((await sdk.hatg('GetProject',{project_id:project.id})).project?.resource_id!==project.id)throw Error('不可读取');
  }catch(error){issues.push(`${project.id}：${error.message}`);}
  for(const item of result.lineages)try {
    if((await readLineage(sdk,item.id)).project_id!==item.project_id)throw Error('归属已变化，请重新选择');
  }catch(error){issues.push(`${item.id}：${error.message}`);}
  const projections=[];
  for(const item of result.projections)try {projections.push(await readProjection(sdk,item.id));}
  catch(error){projections.push({...item,verified:false,error:error.message});issues.push(`${item.id}：${error.message}`);}
  return expandSelection({...result,projections,issues});
}
