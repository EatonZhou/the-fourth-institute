import {resourceDependencyView} from './resource-dependencies.js';
// The release backend owns identities, aliases, dependencies and initialization.
export function featureStateFields(value={}){
  if(!Object.hasOwn(value,'feature_states'))return {};
  return {feature_states:structuredClone(value.feature_states),...(value.feature_states_origin?{feature_states_origin:value.feature_states_origin}:{})};
}
export const FEATURE_STATE_RULES='feature_states 为功能是否带出 included 与安装后默认开启 default_enabled 的唯一意图，均允许未设置 null；旧字段仅缺字段兼容。未带出不应用默认值；纯代码支持无独立启停，旧默认意图仅保留不执行；运行功能带出关闭保留代码、结构及启用入口。功能依赖及默认来源、就绪条件由 release_features 真实目录解析；默认开启意图不等于运行已就绪，冲突不得伪装成功。新安装只在发行实例应用初始化；升级仅处理本站未设置的新ID，源开关、旧run和submission不改写。';

export function featureStateEditor(root,{sdk,signal,value,getSnapshot,onChange,onResolved=()=>{}}){
  let fields=featureStateFields(value),features=[],resolution=null,loaded=false,error='',generation=0,pending=Promise.resolve(),lastKey='';
  let queued=null,draining=false;
  const bindings=new Set(),extras=document.createElement('div'),status=document.createElement('p');
  extras.className='packagingFeatureExtras';status.className='packagingHint';root.append(status,extras);
  const dependencies=resourceDependencyView(root);
  const state=id=>fields.feature_states?.[id]||{included:null,default_enabled:null};
  const spec=id=>features.find(v=>v.id===id);
  const read=()=>structuredClone(fields);
  const included=id=>resolution?resolution.states?.[id]?.included===true:state(id).included===true;
  const aliases=()=>new Map(features.flatMap(v=>(v.system_flow_ids||[]).map(id=>[id,v.id])));
  function render(newInput){
    status.textContent=error?`功能状态核实失败：${error}`:!loaded?'正在读取功能默认值与依赖…':fields.feature_states_origin?'旧配置：默认开启未设置；以下来源是初始化规则推导，不是源站点开关。':'带出选择与安装后默认开启分别保存；默认意图不代表实际就绪。';
    for(const b of bindings){
      if(loaded&&!b.input.isConnected&&b.input!==newInput){bindings.delete(b);continue;}
      const s=state(b.id),r=resolution?.states?.[b.id],definition=spec(b.id);
      const reasons=r?.code_required_by||[];
      const defaultApplicable=(r?.default_applicable??definition?.default_applicable)!==false&&(r?.kind??definition?.kind)!=='code_support';
      const reasonDetails=r?.code_required_by_details||[];
      const reasonText=reasons.map(id=>{
        const detail=reasonDetails.find(d=>d.id===id);
        return detail?[detail.title||id,detail.source].filter(Boolean).join(' · '):spec(id)?.title||id;
      });
      b.input.checked=!loaded?b.initialChecked:r?r.included===true:s.included===true;
      b.input.indeterminate=!loaded?b.initialIndeterminate:s.included==null&&!b.input.checked;
      b.input.disabled=!loaded||reasons.some(reason=>!b.options?.removableReasons?.includes(reason))||!!definition?.mandatory;
      b.default.value=s.default_enabled==null?'':String(s.default_enabled);
      b.defaultLabel.hidden=!defaultApplicable;
      b.defaultLabel.style.display=defaultApplicable?'':'none';
      b.default.disabled=!loaded||!b.input.checked||!defaultApplicable;
      const conflicts=(resolution?.conflicts||[]).filter(v=>v.id===b.id||v.dependency===b.id);
      b.detail.textContent=[definition?.mandatory?'基础代码固定包含':s.included==null?'带出未设置':s.included?'手动带出':'未手动带出',reasons.length?`自动包含：${reasonText.join('、')}`:'',
        defaultApplicable?`默认意图：${s.default_enabled==null?'未设置':s.default_enabled?'开启':'关闭'}`:'依赖支持：无独立运行开关',
        !defaultApplicable&&s.default_enabled!=null?`旧默认意图：${s.default_enabled?'开启':'关闭'}（仅保留，不执行）`:'',
        defaultApplicable?(r?`解析：${r.resolved_default_enabled==null?'不应用/未确定':r.resolved_default_enabled?'开启':'关闭'}；来源：${r.default_source}；就绪：${r.readiness}`:'等待核实'):'',
        r?.mapping_readiness?`映射：${r.mapping_readiness}`:'',
        r?.initialization_status?`初始化：${({pending_installation:'待安装应用',not_requested:'未请求'})[r.initialization_status]||r.initialization_status}`:'',
        r?.mapping_diagnostics?.length?`映射说明：${r.mapping_diagnostics.map(v=>typeof v==='string'?v:JSON.stringify(v)).join('；')}`:'',
        r?.missing_requirements?.length?`所需条件：${r.missing_requirements.join('、')}`:'',
        r?.runtime_required_by?.length?`运行依赖：${r.runtime_required_by.join('、')}`:'',...conflicts.map(v=>`冲突：${v.reason}`)].filter(Boolean).join(' · ');
    }
  }
  function edit(id,key,v){
    fields={feature_states:{...(fields.feature_states||{}),[id]:{...state(id),[key]:v}}};
    resolution=null;lastKey='';render();onChange();void refresh();
  }
  function bind(id,input,title=id,options={}){
    if([...bindings].some(b=>b.input===input))return;
    const label=document.createElement('label'),select=document.createElement('select'),detail=document.createElement('small');
    label.className='packagingDefault';label.append(document.createTextNode('安装后默认开启 '));
    select.className='portalField';select.setAttribute('aria-label',`安装后默认开启 · ${title}`);
    select.append(new Option('未设置（按初始化规则解析）',''),new Option('开启','true'),new Option('关闭','false'));
    label.append(select);detail.className='packagingHint packagingFeatureResolution';
    const anchor=input.closest('label')||input;anchor.after(label,detail);
    if(!options.managed)input.addEventListener('input',()=>edit(id,'included',input.checked));
    select.addEventListener('input',()=>edit(id,'default_enabled',select.value===''?null:select.value==='true'));
    bindings.add({id,input,default:select,defaultLabel:label,detail,initialChecked:input.checked,initialIndeterminate:input.indeterminate,options});render(input);
  }
  function drawExtras(){
    extras.replaceChildren();
    const bound=new Set([...bindings].filter(b=>b.input.isConnected).map(b=>b.id));
    for(const f of [...features,...Object.keys(fields.feature_states||{}).filter(id=>!spec(id)).map(id=>({id,title:`待核对：${id}`}))]){
      if(f.mandatory||f.projection_builtin||bound.has(f.id)||f.system_flow_ids?.length&&f.id.startsWith('flow:'))continue;
      const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input');
      input.type='checkbox';input.value=f.id;label.className='packagingChoice';label.append(input,document.createTextNode(f.title));row.append(label);extras.append(row);bind(f.id,input,f.title);
    }
  }
  async function query(snapshot,key){
    const token=++generation;
    try{
      let result=await sdk.release('candidates',{operation:'release_features',arguments:{snapshot}});
      if(signal.aborted||token!==generation)return;
      if(!Array.isArray(result.features)||!result.resolution?.states)throw Error('功能目录或解析结果缺失');
      // These are the existing page controls' contract IDs, not inferred labels.
      // Submit legacy intent back to the owner so it still resolves dependencies.
      if(!Object.hasOwn(snapshot,'feature_states')){
        const legacy=[['projects_page','projects'],['test_view','test_view']].filter(([field,id])=>typeof snapshot[field]==='boolean'&&result.resolution.states[id]?.requested_included!==snapshot[field]);
        if(legacy.length){
          if(legacy.some(([,id])=>!result.features.some(f=>f.id===id)))throw Error('旧页面功能未在真实目录注册；保留原选择，等待核实');
          const migrated=Object.fromEntries(Object.entries(result.resolution.states).map(([id,s])=>[id,{included:s.requested_included??null,default_enabled:s.default_enabled??null}]));
          for(const [field,id] of legacy)migrated[id].included=snapshot[field];
          result=await sdk.release('candidates',{operation:'release_features',arguments:{snapshot:{...snapshot,feature_states:migrated,feature_states_origin:'legacy_selection_fields'}}});
          if(signal.aborted||token!==generation)return;
          for(const [field,id] of legacy)if(result.resolution?.states?.[id]?.requested_included!==snapshot[field])throw Error(`旧配置 ${field} 尚未被服务迁移；保留原选择，等待核实`);
        }
      }
      if(snapshot.society_templates?.selected?.length&&!result.resolution.states.society_templates?.included)throw Error('所选 Society 模板的必要功能依赖尚未被服务解析；保留选择，等待核实');
      features=result.features;resolution=result.resolution;loaded=true;error='';
      // Canonical aliases and legacy defaults come from the owner, never titles.
      fields={feature_states:Object.fromEntries(Object.entries(resolution.states).map(([id,s])=>[id,{included:s.requested_included??null,default_enabled:s.default_enabled??null}])),
        ...((fields.feature_states_origin||!Object.hasOwn(snapshot,'feature_states'))?{feature_states_origin:fields.feature_states_origin||'legacy_selection_fields'}:{})};
      lastKey=JSON.stringify({...getSnapshot(),...read()});drawExtras();onResolved(resolution);render();dependencies.update(resolution.resource_dependencies,snapshot);
    }catch(e){if(!signal.aborted&&token===generation){error=e.message;lastKey='';render();dependencies.update(null,{},`资源依赖核实失败：${e.message}`);}}
  }
  function refresh(){
    const snapshot={...getSnapshot(),...read()},key=JSON.stringify(snapshot);
    if(key===lastKey)return pending;lastKey=key;
    dependencies.update(null,{},'正在核实资源关联与必要结构…');
    queued={snapshot,key};generation++;
    if(draining)return pending;
    draining=true;
    pending=(async()=>{
      while(queued&&!signal.aborted){
        // Collapse rapid edits; only one server resolution may be in flight.
        const next=queued;queued=null;
        await query(next.snapshot,next.key);
      }
    })().finally(()=>{draining=false;});
    return pending;
  }
  const retry=document.createElement('button');retry.type='button';retry.className='portalButton';retry.textContent='重新核实功能状态';retry.addEventListener('click',()=>{lastKey='';void refresh();},{signal});root.append(retry);
  return {read,bind,refresh,
    projectionFeature:item=>features.find(f=>f.projection_builtin&&f.projection_builtin===item.builtin)?.id,
    projectionDependency:id=>resolution?.resource_dependencies?.projections?.find(p=>p.id===id),
    setIncluded(id,included){fields={feature_states:{...(fields.feature_states||{}),[id]:{...state(id),included}}};resolution=null;lastKey='';render();},ready:refresh,settle:()=>pending,included,effectiveFeatures:()=>resolution?.included_features||Object.keys(fields.feature_states||{}).filter(included),isReady:()=>loaded,
    bindFlow(id,input,title){const canonical=aliases().get(id);if(canonical)bind(canonical,input,title);else{input.disabled=true;input.title='该结构未映射为独立可选功能；随所需流程的结构依赖处理';}},
    flowIds:()=>features.filter(v=>included(v.id)).flatMap(v=>v.system_flow_ids||[]),
    async validate(){lastKey='';await refresh();if(error||!loaded)throw Error(error||'功能状态尚未核实');if(resolution.resource_dependencies?.valid===false||resolution.resource_dependencies?.gaps?.length)throw Error('资源依赖尚有缺口，请核实关联后再发布；当前配置仍可保存');if(resolution.conflicts?.length)throw Error(`功能状态存在冲突：${resolution.conflicts.map(v=>`${v.id}: ${v.reason}`).join('；')}`);},
  };
}
