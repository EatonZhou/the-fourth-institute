import { SPECIAL_TYPES, expandSelection, migrateSelection, projectionReasons, resourceReasons, readInstance, readLineage, readProjection, verifySelection } from './selection-model.js';
import {instancePicker} from './instance-picker.js';
import {workspaceEditor} from './workspace.js';
import {documentRequirements} from './document-dependencies.js';

const element=(tag,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
const title=item=>(typeof item?.title==='string'?item.title:item?.title?.zh||item?.title?.en||Object.values(item?.title||{}).find(v=>typeof v==='string'&&v.trim()))||item?.type||item?.resource_id;
const check=(text,checked,fn)=>{const label=element('label','','packagingChoice'),input=element('input');input.type='checkbox';input.checked=checked;input.addEventListener('change',()=>fn(input.checked));label.append(input,document.createTextNode(text));return label;};

export function createSelectionEditor(root,{sdk,signal,value,onChange,featureStates}) {
  const scope=element('fieldset','','packagingEditScope packagingSelectionScope');root.append(scope);root=scope;
  let state=migrateSelection(value),registry=[],catalogue=new Map(),projectItems=new Map(),projectCursor='',projectionCursor='';
  const lineageLists=new Map(),lineageNames=new Map(),projectNames=new Map(),lineageStatus=new Map();
  let pickerLifecycle=new AbortController();
  signal.addEventListener('abort',()=>pickerLifecycle.abort(),{once:true});
  // Verification already resolves every selected/bound Lineage. Keep its display
  // metadata outside the checklist so pagination cannot erase a known name.
  const sourceSDK=sdk;
  sdk={...sourceSDK,hatg:async(name,args,...rest)=>{
    const lineageRead=name==='ResolveResource'&&args.address?.startsWith('lineage_');
    try{
      const result=await sourceSDK.hatg(name,args,...rest);
      if(name==='GetProject'&&result.project?.resource_id===args.project_id&&result.project.title)projectNames.set(args.project_id,title(result.project));
      if(lineageRead){
        const record=result.resource;
        if(record?.resource_id===args.address&&record.resource_type==='lineage'){
          lineageStatus.set(args.address,{valid:true});
          if(record.title)lineageNames.set(args.address,title(record));
          else lineageNames.delete(args.address); // Search summaries may carry the display title instead.
        }else {lineageNames.set(args.address,'');lineageStatus.set(args.address,{valid:false,error:'资源不存在或类型不符'});}
      }
      return result;
    }catch(error){if(lineageRead){lineageNames.set(args.address,'');lineageStatus.set(args.address,{valid:false,error:error.message});}throw error;}
  }};
  const typesBox=element('fieldset'),societyBox=element('fieldset','','packagingProjects'),projectionBox=element('fieldset');
  const errors=element('p','','packagingSelectionErrors');errors.setAttribute('role','status');
  const typesBody=element('div','','packagingTypes'),projectsBody=element('div'),projectionsBody=element('div');
  const featureProjections=new Map();let projectionFeatureKey='',resourceDependencyKey='',resourceDependencies={lineages:[],projects:[]},resourceDependencyErrors=[];
  const testViewBody=element('div','','packagingTestView');
  let currentAction=Promise.resolve(),pendingActions=0;
  const run=fn=>{
    pendingActions++;root.disabled=true;root.setAttribute('aria-busy','true');
    currentAction=currentAction.then(async()=>{
      try{if(!signal.aborted)await fn();}
      catch(error){if(!signal.aborted){draw();errors.textContent=error.message;}}
      finally{if(--pendingActions===0){root.disabled=false;root.removeAttribute('aria-busy');}}
    });
    return currentAction;
  };
  const button=(text,fn)=>{const b=element('button',text,'portalButton');b.type='button';b.addEventListener('click',()=>{b.disabled=true;void run(fn).finally(()=>{b.disabled=false;});});return b;};
  const commit=(render=true)=>{if(signal.aborted)return;state=expandSelection(state);if(render)draw();onChange();};
  // Stop bubbling: the editor changes its model before notifying the outer form.
  root.addEventListener('input',event=>event.stopPropagation());
  const description=(name,key)=>{const label=element('label',name),area=element('textarea','','portalField');area.rows=3;area.value=state[key];area.addEventListener('input',()=>{state[key]=area.value;commit(false);});label.append(area);return label;};
  const queryInput=name=>{const input=element('input','','portalField');input.setAttribute('aria-label',name);input.placeholder=name;return input;};
  const projectSearch=queryInput('搜索 Project'),projectionSearch=queryInput('搜索投影'),projectionId=queryInput('Workbench ID');
  const loadProjects=async(more=false)=>{
    const result=await sdk.hatg('SearchResources',{resource_type:'project',q:projectSearch.value.trim(),limit:50,...(more&&projectCursor?{cursor:projectCursor}:{})});
    if(!more)projectItems.clear();for(const item of result.items||[])projectItems.set(item.resource_id,item);
    projectCursor=result.next_cursor||'';drawSociety();moreProjects.hidden=!projectCursor;
  };
  const loadLineages=async(project,more=false)=>{
    const data=lineageLists.get(project)||{items:new Map(),query:'',cursor:''};lineageLists.set(project,data);
    const result=await sdk.hatg('SearchResources',{resource_type:'lineage',project_id:project,q:data.query,limit:50,...(more&&data.cursor?{cursor:data.cursor}:{})});
    if(!more)data.items.clear();for(const item of result.items||[]){const owners=Array.isArray(item.project)?item.project:[item.project];if(owners.length===1&&owners[0]===project){data.items.set(item.resource_id,item);lineageStatus.set(item.resource_id,{valid:true});if(item.title)lineageNames.set(item.resource_id,title(item));}}
    data.cursor=result.next_cursor||'';data.loaded=true;drawSociety();
  };
  const loadProjections=async(more=false)=>{
    const result=await sdk.hatg('SearchResources',{resource_type:'workbench',q:projectionSearch.value.trim(),limit:8,...(more&&projectionCursor?{cursor:projectionCursor}:{})});
    if(!more)catalogue.clear();
    // Search is a candidate list, not a request to hydrate every Workbench.
    // Verify bindings when selected; saved selections are verified in ready().
    for(const item of result.items||[])catalogue.set(item.resource_id,
      state.projections.find(p=>p.id===item.resource_id)||featureProjections.get(item.resource_id)||
      {id:item.resource_id,title:title(item),verified:false,error:'选择时核实关联'});
    projectionCursor=result.next_cursor||'';moreProjections.hidden=!projectionCursor;draw();
  };
  const toggleProjection=async(id,selected)=>{
    const item=selected?await readProjection(sdk,id):state.projections.find(p=>p.id===id)||featureProjections.get(id)||catalogue.get(id);
    // Discovery can finish before the canonical feature catalogue on reload.
    // Resolve the identity before applying this one selection to both models.
    await featureStates?.ready();
    const identity=featureStates?.projectionFeature(item||{});
    if(identity){featureStates.setIncluded(identity,selected);if(identity==='test_view'){state.test_view=selected;state.pending_test_view=false;}}
    if(selected){state.projections=state.projections.filter(v=>v.id!==id).concat(item);catalogue.set(id,item);}
    else state.projections=state.projections.filter(v=>v.id!==id);
    commit();
    for(const project of state.projects)if(!lineageLists.get(project.id)?.loaded)await loadLineages(project.id);
  };
  const projectionRow=item=>{
    const row=element('div','','packagingProjection');row.dataset.projectionId=item.id;
    const identity=featureStates?.projectionFeature(item),dependency=featureStates?.projectionDependency(item.id);
    const choice=check(`${item.title||item.id}`,state.projections.some(v=>v.id===item.id),selected=>void run(()=>toggleProjection(item.id,selected)));row.append(choice);
    if(identity){
      const removable=dependency?.required_by?.length>0&&dependency.required_by.every(r=>r==='manual.projections'||r===`feature_states.${identity}`);
      featureStates.bind(identity,choice.querySelector('input'),item.title||item.id,{managed:true,removableReasons:removable?[`projections.${item.id}`,'explicit_resolution']:[]});
    }
    row.append(element('small',item.verified?`关联：${item.dependencies.map(v=>`${v.project_id} / ${v.id}`).join('、')}`:`关联待核实：${item.error||'不可读取'}`,'packagingHint'));
    return row;
  };
  function drawSociety(){
    const effective=expandSelection(state,resourceDependencies);
    const dependencyReasons=(id,project=false)=>(resourceDependencies[project?'projects':'lineages'].find(item=>item.id===id)?.required_by||[]);
    const saveOptions=(id,changes)=>{
      const current=effective.lineages.find(item=>item.id===id);if(!current)return;
      const options={history:current.history,workspace:current.workspace,...changes};
      state.resource_lineage_options={...(state.resource_lineage_options||{}),[id]:options};
      const explicit=state.lineages.find(item=>item.id===id);if(explicit)Object.assign(explicit,options);
      Object.assign(current,options);commit(false);
    };
    projectsBody.replaceChildren();
    for(const message of resourceDependencyErrors)projectsBody.append(element('p',message,'packagingSelectionErrors'));
    const items=new Map([...effective.projects.map(v=>[v.id,{resource_id:v.id,title:projectNames.get(v.id)||v.id}]),...projectItems]);
    const list=element('div','','packagingProjectChoices');projectsBody.append(list);
    for(const [id,item] of items){
      const required=[...dependencyReasons(id,true),...projectionReasons(state,id,true),...resourceReasons(state,id)];
      const row=element('div');row.append(check(title(item),state.manual_projects.includes(id)||required.length>0,selected=>void run(async()=>{
        if(selected){await sdk.hatg('GetProject',{project_id:id});state.manual_projects.push(id);}
        else{state.manual_projects=state.manual_projects.filter(v=>v!==id);state.manual_lineages=state.manual_lineages.filter(v=>state.lineages.find(l=>l.id===v)?.project_id!==id);}
        commit();if(selected)await loadLineages(id);
      })));
      if(required.length){
        row.querySelector('input').disabled=true;row.append(element('small',`依赖必需，已包含：${[...new Set(required)].join('、')}`,'packagingHint'));
      }
      list.append(row);
    }
    for(const project of effective.projects){
      const id=project.id,item=items.get(id),group=element('fieldset');group.dataset.projectId=id;group.append(element('legend',title(item)));
      const data=lineageLists.get(id)||{items:new Map(),query:'',cursor:''};lineageLists.set(id,data);
      const search=queryInput(`${title(item)} · 搜索 Lineage`);search.value=data.query;
      group.append(search,button('搜索 Lineage',()=>{data.query=search.value.trim();return loadLineages(id);}));
      const candidates=new Map([...effective.lineages.filter(v=>v.project_id===id&&lineageStatus.get(v.id)?.valid).map(v=>[v.id,{resource_id:v.id,title:v.id}]),...data.items]);
      for(const [lineage,item] of candidates){
        if(lineageStatus.get(lineage)?.valid===false)continue;
        const row=element('div','','packagingLineageEntry'),chosen=effective.lineages.find(v=>v.id===lineage);
        row.dataset.lineageId=lineage;
        const displayName=lineageNames.get(lineage)||title(item)||lineage;
        const required=[...dependencyReasons(lineage),...projectionReasons(state,lineage)];
        row.append(check(displayName,state.manual_lineages.includes(lineage)||required.length>0,selected=>void run(async()=>{
          if(selected){const verified=await readLineage(sdk,lineage);if(verified.project_id!==id)throw Error('Lineage 归属已改变，请重新搜索');
            if(!state.manual_projects.includes(id))state.manual_projects.push(id);state.manual_lineages.push(lineage);
            if(!state.lineages.some(v=>v.id===lineage))state.lineages.push({...verified,history:chosen?.history===true,workspace:chosen?.workspace});
          }else state.manual_lineages=state.manual_lineages.filter(v=>v!==lineage);
          commit();
        })));
        if(required.length){
          row.querySelector('input').disabled=true;row.append(element('p',`依赖必需，已包含：${[...new Set(required)].join('、')}`,'packagingHint'));
        }
        row.append(element('small',displayName===lineage?'名称待核实':lineage,'packagingHint'));
        const history=check('保留经历',chosen?.history===true,selected=>saveOptions(lineage,{history:selected}));
        history.querySelector('input').disabled=!chosen;row.append(history);
        if(chosen){
          row.append(workspaceEditor(chosen.workspace,options=>saveOptions(lineage,{workspace:options})));
          const related=[...new Map([...catalogue,...state.projections.map(v=>[v.id,v])]).values()].filter(v=>v.verified&&v.dependencies.some(d=>d.id===lineage)&&!featureStates?.projectionFeature(v));
          if(related.length){const links=element('div','','packagingRelated');links.append(element('p','关联投影'));for(const projection of related)links.append(projectionRow(projection));row.append(links);}
        }
        group.append(row);
      }
      if(!candidates.size)group.append(element('p','此项目暂无匹配的可读 Lineage；可搜索加载。','packagingHint'));
      const more=button('加载更多 Lineage',()=>loadLineages(id,true));more.hidden=!data.cursor;group.append(more);projectsBody.append(group);
    }
    for(const item of effective.lineages.filter(item=>!lineageStatus.get(item.id)?.valid)){
      const status=lineageStatus.get(item.id),row=element('div','','packagingInvalidSelection');row.dataset.invalidLineageId=item.id;
      row.append(element('p',`${item.id}：${status?'选择失效或读取失败：'+status.error:'尚未核实，暂不作为有效候选'}`));
      if(state.manual_lineages.includes(item.id))row.append(button('移除失效选择',()=>{
        state.manual_lineages=state.manual_lineages.filter(id=>id!==item.id);commit();
      }));
      projectsBody.append(row);
    }
    for(const item of state.unresolved_lineages||[]){const row=element('p',`旧 Lineage 归属待核实：${item.id}`);row.append(button('移除此旧选择',()=>{state.unresolved_lineages=state.unresolved_lineages.filter(v=>v.id!==item.id);commit();}));projectsBody.append(row);}
    if(state.pending_workspace_selections.length){
      const pending=element('fieldset','','packagingPendingWorkspace');pending.append(element('legend','Workspace 范围待核对'),element('p','以下是此前的 Workspace 选择原文，尚未分配到 Lineage，不代表已确认带出。请在对应 Lineage 的“工作区与文件”中明确范围，或交给团队核对。','packagingHint'));
      state.pending_workspace_selections.forEach((item,index)=>{const row=element('div');row.append(element('pre',JSON.stringify(item,null,2)),button('移除此待核对记录',()=>{state.pending_workspace_selections.splice(index,1);commit();}));pending.append(row);});
      projectsBody.append(pending);
    }
  }
  function drawTypes(){
    pickerLifecycle.abort();pickerLifecycle=new AbortController();
    typesBody.replaceChildren();
    const items=new Map(registry.filter(v=>!SPECIAL_TYPES.has(v.type)).map(v=>[v.type,v]));
    for(const selected of state.resource_types)if(!items.has(selected.type))items.set(selected.type,{type:selected.type,title:`${selected.type}（登记待核实）`});
    const requirements=documentRequirements(state.document_indexes);
    for(const required of requirements)if(!items.has(required.type))items.set(required.type,{type:required.type,title:required.type});
    for(const [type,item] of items){
      const group=element('div','','packagingResourceType');group.dataset.resourceType=type;
      let selection=state.resource_types.find(v=>v.type===type);
      group.dataset.expanded=String(!!selection);
      const required=requirements.filter(v=>v.type===type);
      const typeChoice=check(`${title(item)} (${type})`,!!selection||required.length>0,selected=>{state.resource_types=state.resource_types.filter(v=>v.type!==type);if(selected)state.resource_types.push({type,instances:[],description:'',ownership:{}});commit();});
      typeChoice.querySelector('input').disabled=required.length>0;group.append(typeChoice);
      if(required.length)group.append(element('p',`文档需要，已自动包含 ${type} 类型支持：${required.map(v=>`${v.kind} → ${v.entries.join('、')}`).join('；')}。依赖期间不能取消；撤销依赖后保留原手动选择，不带出该类型全部实例。`,'packagingHint'));
      if(type==='device'){
        group.append(element('p','Device 目前仅支持类型能力，暂不支持带出设备实例或实例文字范围。','packagingHint'));
      }else if(selection||required.length){
        selection||={type,instances:[],description:'',ownership:{}};
        const selectInstances=()=>{if(!state.resource_types.some(v=>v.type===type))state.resource_types.push(selection);};
        const detail=element('div','','packagingInstances');detail.append(element('p','未选实例且未描述范围时，仅包含类型能力，不包含现有数据。','packagingHint'));
        const add=async id=>{
          const owned=await readInstance(sdk,type,id);selectInstances();selection.ownership[id]=owned;
          if(!selection.instances.includes(id))selection.instances.push(id);commit();
          // Adding one instance must not eagerly fetch every Project's Lineages.
          // Required resource Lineages arrive through the dependency resolution.
        };
        detail.append(instancePicker({sdk,type,selected:selection.instances,signal:pickerLifecycle.signal,onSelect:id=>void run(()=>add(id))}));
        for(const id of selection.instances){
          const owned=selection.ownership[id],row=element('div','','packagingSelectedInstance');row.dataset.instanceId=id;
          row.append(element('span',`${owned?.title||id} · ${id}`));
          row.append(element('small',owned?.verified?(owned.project_ids.length?`所属 Project：${owned.project_ids.join('、')}（已自动包含）`:'无所属 Project'):`归属待核实：${owned?.error||'尚未核实'}`,'packagingHint'));
          row.append(button('移除实例',()=>{selection.instances=selection.instances.filter(v=>v!==id);delete selection.ownership[id];commit();}));detail.append(row);
        }
        const label=element('label',`${type} · 自然语言范围`),area=element('textarea','','portalField');area.value=selection.description;area.rows=2;
        area.addEventListener('input',()=>{selectInstances();selection.description=area.value;commit(false);});label.append(area);detail.append(label);group.append(detail);
      }
      typesBody.append(group);
    }
    if(state.excluded_device_selections?.length){const note=element('details');note.append(element('summary','已排除的 Device 实例说明'),element('p','下列原文仅保留作说明，不会作为带出范围。','packagingHint'),element('pre',JSON.stringify(state.excluded_device_selections,null,2)));typesBody.append(note);}
  }
  function draw(){
    if(signal.aborted)return;drawTypes();drawSociety();projectionsBody.replaceChildren();
    for(const item of new Map([...catalogue,...featureProjections,...state.projections.map(v=>[v.id,v])]).values())projectionsBody.append(projectionRow(item));
    testViewBody.replaceChildren();
    if(state.pending_test_view)testViewBody.append(element('p','原 Test Workbench 选择待核对：请在真实 Workbench 列表选择对应投影，或移除此旧记录；原文保留，不推测 ID。','packagingHint'),button('移除旧 Test Workbench 选择',()=>{state.pending_test_view=false;commit();}));
    errors.textContent=(state.issues||[]).join('\n');
  }
  const moreProjects=button('加载更多 Project',()=>loadProjects(true)),moreProjections=button('加载更多投影',()=>loadProjections(true));moreProjects.hidden=true;moreProjections.hidden=true;
  typesBox.append(element('legend','资源类型与实例'),element('p','选择类型以包含其声明、提供者和服务；展开后单独选择实例。Workspace 基础支持固定包含，文件范围在 Lineage 下设置；Society 结构与投影在下方选择。','packagingHint'),typesBody);
  societyBox.append(element('legend','Society：Project 与 Lineage'),element('p','先选 Project，再选 Lineage。Residents、Family 等必要结构随 Lineage；选择项目不会自动带出全部数据。资源关联所需的 Project 与 Lineage 会自动列出，可直接配置经历和文件范围；依赖期间不能取消带出。','packagingHint'),projectSearch,button('搜索 Project',()=>loadProjects()),moreProjects,projectsBody,description('Society 自然语言范围','society_description'));
  projectionBox.append(element('legend','关联投影'),element('p','可直接选择 Workbench，或在 Lineage 下选择同一投影。自动加入全部必要 Project 和 Lineage，不自动保留经历。关联列表来自当前已加载的真实投影；可搜索或加载更多。','packagingHint'),projectionSearch,button('搜索投影',()=>loadProjections()),moreProjections,projectionId,button('添加 Workbench ID',()=>toggleProjection(projectionId.value.trim(),true)),projectionsBody,description('投影自然语言范围','projection_description'));
  const pages=element('fieldset');pages.append(element('legend','页面入口'),check('Projects 页面',state.projects_page,selected=>{state.projects_page=selected;commit(false);}),element('p','仅控制 Projects 页面和导航；Project 后端、服务和所选数据继续保留。','packagingHint'));
  if(featureStates)featureStates.bind('projects',pages.querySelector('input'),'Projects 页面');
  projectionBox.append(testViewBody);
  root.append(typesBox,societyBox,projectionBox,pages,errors);draw();
  return {
    societyRoot:societyBox,
    syncProjectionFeatures(resolution){
      const required=(resolution?.resource_dependencies?.projections||[]).filter(p=>p.selection_feature);
      const dependencies=resolution?.resource_dependencies;
      const dependencyKey=JSON.stringify(dependencies?[dependencies.lineages,dependencies.projects,dependencies.projections,dependencies.gaps]:null);
      const key=JSON.stringify([required,[...catalogue.values(),...state.projections].map(p=>[p.id,featureStates?.projectionFeature(p)])]);
      if(key===projectionFeatureKey&&dependencyKey===resourceDependencyKey)return currentAction;projectionFeatureKey=key;resourceDependencyKey=dependencyKey;
      currentAction=currentAction.then(async()=>{
        if(signal.aborted||resourceDependencyKey!==dependencyKey)return;
        if(dependencies){
          const hydrated=[],problems=(dependencies.gaps||[]).map(gap=>`资源关联 ${gap.id||gap.application||''} 待核实：${gap.reason}`),projectReads=new Map();
          const dependencySDK={...sdk,hatg:(name,args)=>{
            if(name!=='GetProject')return sdk.hatg(name,args);
            if(!projectReads.has(args.project_id))projectReads.set(args.project_id,sdk.hatg(name,args));
            return projectReads.get(args.project_id);
          }};
          const automatic=(dependencies.lineages||[]).map(original=>({...original,required_by:(original.required_by||[]).filter(reason=>reason!=='manual.lineages')})).filter(item=>item.required_by.length);
          // Bound parallel ownership reads; failed bindings remain visible gaps.
          for(let start=0;start<automatic.length;start+=4){
            const batch=automatic.slice(start,start+4);
            const results=await Promise.allSettled(batch.map(item=>readLineage(dependencySDK,item.id)));
            results.forEach((result,index)=>{
              const item=batch[index];
              if(result.status==='fulfilled')hydrated.push({...item,...result.value});
              else problems.push(`资源关联 ${item.id} 读取失败：${result.reason.message}`);
            });
          }
          const structuralSources=new Set([...hydrated.map(item=>item.id),...(dependencies.projections||[]).map(item=>item.id)]);
          const projects=new Map((dependencies.projects||[]).map(p=>({...p,required_by:(p.required_by||[]).filter(reason=>structuralSources.has(reason))})).filter(p=>p.required_by.length).map(p=>[p.id,p]));
          for(const item of hydrated)if(!projects.has(item.project_id))projects.set(item.project_id,{id:item.project_id,required_by:[item.id]});
          for(const item of projects.values())try{
            const result=await dependencySDK.hatg('GetProject',{project_id:item.id});
            if(result.project?.resource_id===item.id)projectItems.set(item.id,result.project);
          }catch(error){problems.push(`资源关联 Project ${item.id} 读取失败：${error.message}`);}
          resourceDependencies={lineages:hydrated,projects:[...projects.values()]};resourceDependencyErrors=problems;if(problems.length)resourceDependencyKey='';
        }
        for(const item of required){
          if(featureProjections.get(item.id)?.revision===item.revision)continue;
          try{featureProjections.set(item.id,await readProjection(sdk,item.id));}
          catch(error){featureProjections.set(item.id,{...item,title:item.id,verified:false,error:error.message});}
        }
        if(!signal.aborted)draw();
      });
      return currentAction;
    },
    setDocuments(value){state=expandSelection({...state,document_indexes:value});draw();},
    setSystem(value){state=expandSelection({...state,system_lineage:value});draw();},
    read:()=>structuredClone(state),
    settle:()=>currentAction,
    async refresh(){
      resourceDependencyKey='';state=await verifySelection(sdk,state);
      const required=resourceDependencies.lineages.filter(item=>!state.lineages.some(v=>v.id===item.id));
      for(let start=0;start<required.length;start+=4){
        const batch=required.slice(start,start+4),results=await Promise.allSettled(batch.map(item=>readLineage(sdk,item.id)));
        results.forEach((result,index)=>{if(result.status==='rejected')resourceDependencyErrors.push(`资源关联 ${batch[index].id} 读取失败：${result.reason.message}`);});
      }
      for(const [id,data] of lineageLists)if(data.loaded)await loadLineages(id);
      commit();
    },
    async ready(){
      registry=(await sdk.hatg('ListResourceTypes',{})).types;draw();
      await Promise.all([loadProjects(),loadProjections()]);
      state=await verifySelection(sdk,state);draw();
      for(const project of state.projects)await loadLineages(project.id);
    },
    async validate(){
      const associations=()=>JSON.stringify([state.projections,state.resource_types.map(v=>v.ownership)]);
      const before=associations();state=await verifySelection(sdk,state);commit();
      if(resourceDependencyErrors.length)throw Error(resourceDependencyErrors.join('\n'));
      if(state.issues.length)throw Error(state.issues.join('\n'));
      if(before!==associations())throw Error('资源归属或投影关联发生变化，已更新清单，请重新核对后提交');
    },
  };
}
