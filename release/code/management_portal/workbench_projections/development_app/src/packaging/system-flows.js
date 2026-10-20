export const PORTAL_SYSTEM='lineage_ed619225d417';
// Verified identity of the existing timing workflow, not a title heuristic.
const SETTING_FLOWS={fbi_5f1e3f7f62db:'model_request_timing'};
export const SYSTEM_RULES='Portal System（lineage_ed619225d417）作为普通 Lineage 默认带出其所属 Project 与必要结构；内部以 feature_states 的唯一功能意图解析真实流程，system_lineage.selected 为结构投影及缺字段兼容；按所选功能/流程及必要依赖处理 Resident、组件和连线，不复制全部可选流程。selected=null 表示尚未设置，空数组表示明确不附带可选流程。elements/wire_ids 来自真实定义，boundary_wires 是跨选中范围的关联，执行时核实共享依赖、整理连线，不将未选流程静默带回；不可读或定义变更时重新核对。其他 Lineage 仍默认带全部 Resident。经历与 Workspace 范围继续独立，默认带入不等于保留经历或所有成果。';
export function flowCatalogue(lineage){
  if(lineage?.lineage_id!==PORTAL_SYSTEM||!lineage.project_id)throw Error('Portal System 定义或 Project 归属不可核实');
  const objects=new Map([...(lineage.nodes||[]).map(v=>({...v,kind:'resident'})),...(lineage.program?.components||[])].map(v=>[v.id,v]));
  const grouped=new Set((lineage.function_blocks||[]).flatMap(v=>(v.elements||[]).map(v=>v.id)));
  const candidates=[...(lineage.function_blocks||[]).map(v=>({id:v.id,title:v.title,kind:'function_block',elements:(v.elements||[]).map(v=>v.id)})),...[...objects.values()].filter(v=>!grouped.has(v.id)).map(v=>({id:v.id,title:v.title,kind:v.kind,elements:[v.id]}))];
  return candidates.map(v=>{
    const ids=new Set(v.elements), wires=lineage.program?.wires||[];
    return {...v,setting_key:SETTING_FLOWS[v.id]||null,
      missing:v.elements.filter(id=>!objects.has(id)),
      wire_ids:wires.filter(w=>ids.has(w.source)&&ids.has(w.target)).map(w=>w.id),
      boundary_wires:wires.filter(w=>ids.has(w.source)!==ids.has(w.target)).map(w=>({id:w.id,source:w.source,target:w.target,kind:w.kind}))};
  });
}
export function normalizeSystem(value){
  return {id:PORTAL_SYSTEM,project_id:typeof value?.project_id==='string'?value.project_id:'',revision:value?.revision??null,verified:value?.verified===true,
    selected:Array.isArray(value?.selected)?structuredClone(value.selected):null,description:typeof value?.description==='string'?value.description:''};
}
export function systemFlowEditor(root,{sdk,signal,value,getFeatures,featureStates,onChange,onDependency}){
  let state=normalizeSystem(value),catalogue=[],loaded=false,error='',pending=Promise.resolve();
  const el=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(tag==='button')n.className='portalButton';if(tag==='textarea'){n.className='portalField';n.rows=3;}if(tag==='p')n.className='packagingHint';return n;};
  const box=el('fieldset'),body=el('div'),status=el('p');box.className='packagingSystemFlows';
  box.append(el('legend','Portal System 功能与流程'),el('p','Portal System 及所属项目默认包含。下面按真实 Function Block、独立成员及组件选择；经历和工作区在 Society 区独立设置。'),body,status);
  const scope=el('textarea');scope.setAttribute('aria-label','Portal System 功能范围说明');scope.value=state.description;scope.addEventListener('input',()=>{state.description=scope.value;onChange();});const scopeLabel=el('label','Portal System 功能范围说明');scopeLabel.className='packagingScope';scopeLabel.append(scope);box.append(scopeLabel);
  function selectWithSettings(selected){
    if(!loaded)return selected;
    if(featureStates?.isReady())return catalogue.filter(v=>featureStates.flowIds().includes(v.id));
    const result=(selected||[]).filter(v=>!v.setting_key);
    for(const item of catalogue.filter(v=>v.setting_key))if(getFeatures()?.[item.setting_key]===true)result.push(item);
    return selected===null&&!result.length?null:result;
  }
  function draw(){
    if(signal.aborted)return;body.replaceChildren();
    status.textContent=error||(!loaded?'正在读取 Portal System…':state.selected===null?'可选流程尚未设置；请逐项选择或明确不带可选流程。':'功能选择已记录；跨范围连线交执行团队按依赖核实。');
    const selected=selectWithSettings(state.selected)||[];
    for(const item of [...catalogue,...(state.selected||[]).filter(v=>!catalogue.some(c=>c.id===v.id))]){
      const row=el('label'),input=el('input');input.type='checkbox';input.value=item.id;input.checked=selected.some(v=>v.id===item.id);
      input.disabled=!loaded||!!item.setting_key||item.missing?.length>0;
      input.addEventListener('change',()=>{state.selected=input.checked?[...(state.selected||[]).filter(v=>v.id!==item.id),item]:(state.selected||[]).filter(v=>v.id!==item.id);draw();onChange();});
      row.className='packagingChoice';row.append(input,document.createTextNode(`${item.title||item.id} · ${item.kind}`));body.append(row);if(featureStates)featureStates.bindFlow(item.id,input,item.title||item.id);
      const detail=el('small',`${item.id}${item.setting_key?' · 与上方模型请求时间记录共用选择':''}${!catalogue.some(v=>v.id===item.id)?' · 定义已不可用，待核对':''}${item.missing?.length?' · 存在缺失成员':''}`);detail.className='packagingHint';body.append(detail);
      const details=el('details');details.append(el('summary','查看真实成员与连线'),el('pre',JSON.stringify({elements:item.elements,wire_ids:item.wire_ids,boundary_wires:item.boundary_wires},null,2)));body.append(details);
    }
  }
  async function load(){
    try{
      // The release-only host intentionally exposes no ordinary chat bindings.
      const lineage=(await sdk.hatg('GetLineage',{lineage_id:PORTAL_SYSTEM})).lineage;
      const found=flowCatalogue(lineage);const project=await sdk.hatg('GetProject',{project_id:lineage.project_id});
      if(project.project?.resource_id!==lineage.project_id)throw Error('所属 Project 不可读取');
      catalogue=found;loaded=true;error='';
      state={...state,project_id:lineage.project_id,revision:lineage.revision,verified:true};
      // Keep selected definitions as evidence until validation compares them.
      onDependency(state);draw();
    }catch(e){loaded=false;state.verified=false;error=`Portal System 无法核实：${e.message}`;onDependency(state);draw();}
  }
  const none=el('button','不附带其他可选流程');none.type='button';none.addEventListener('click',()=>{state.selected=[];draw();onChange();});if(featureStates)none.hidden=true;
  const retry=el('button','重新读取流程');retry.type='button';retry.addEventListener('click',()=>{pending=load();});box.append(none,retry);root.append(box);draw();
  return {read:()=>({...structuredClone(state),selected:selectWithSettings(state.selected)}),refresh:draw,settle:()=>pending,
    ready:()=>pending=load(),async validate(){
      const before=state;await load();if(!loaded)throw Error(error);
      if(before.project_id&&before.project_id!==state.project_id)throw Error('Portal System 归属已变化，请重新核对');
      const selected=selectWithSettings(state.selected);
      if(before.revision!==null&&before.revision!==state.revision&&selected?.length){draw();onChange();throw Error('Portal System 定义版本已变化，请重新核对流程范围');}
      for(const item of selected||[]){const actual=catalogue.find(v=>v.id===item.id);if(!actual||actual.missing.length)throw Error(`流程不可核实：${item.title||item.id}`);
        if(JSON.stringify(actual)!==JSON.stringify(item)){state.selected=(selected||[]).map(v=>v.id===item.id?actual:v);draw();onChange();throw Error('流程定义已变化，已更新清单，请重新核对');}}
    }};
}
