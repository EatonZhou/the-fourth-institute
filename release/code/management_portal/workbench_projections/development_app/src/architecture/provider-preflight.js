import './provider-preflight.css';
import {PREFLIGHT_ID,createProviderPreflightSimulation,supportsProviderPreflight,getProviderPreflightEntry,getProviderPreflightModes} from './provider-preflight.model.js';
export {supportsProviderPreflight,getProviderPreflightEntry};

const groups=[
  {title:'功能条件 · AND / OR',ids:['requirements_first_alternative','requirements_second_alternative','requirements_missing','requirements_compaction_missing']},
  {title:'操作与可选插件',ids:['supported','unsupported','optional','setting']},
  {title:'账号目录与 schema 样例',ids:['catalog-gap','model','effort','tier']},
  {title:'原生权限与 ISA',ids:['permissions']},
];
const capNames={portal_tools:'Portal 工具',input_image_detail:'图像细节输入',auto_compact_threshold:'自动压缩阈值',native_tool_events:'原生工具事件',context_usage_events:'上下文用量事件',error_continuation:'故障后续接'};
const states={pending:'待检查',passed:'已通过本项校验',rejected:'调用前拒绝',blocked_missing_catalog:'目录未取得，停止本地预演',capability_requirement_evaluated:'功能条件已核对',not_requested:'本例未请求插件',skipped:'跳过可选插件',eligible:'条件满足，可计划',not_evaluated:'未评估'};
const label=v=>states[v]||v||'尚未选择';
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;}
function panel(title,cls=''){const n=el('section','pf-panel '+cls);n.append(el('h3','',title));return n;}
function note(root,text){root.append(el('p','pf-note',text));}
function row(root,key,value){const n=el('div','pf-row');n.append(el('span','',key),el('strong','',value));root.append(n);return n;}
function chip(cap,status){const n=el('span','pf-cap '+(status||''));n.dataset.capability=cap;n.append(el('b','',capNames[cap]||cap),el('code','',cap));return n;}
function values(root,list){const n=el('div','pf-caps');for(const v of list||[])n.append(chip(v));root.append(n);}
function requirements(scene){
  const s=scene.state,root=panel(s.feature_id||'可选功能的条件','pf-requirements');
  note(root,s.feature_id?'固定注册功能声明 × 本地案例能力集合；这里只判断能否计划该功能。':scene.case_id.startsWith('requirements_')?'具体注册功能与能力集合尚待载入。':'本地示例插件；未把它添加为生产注册功能。');
  const provided=panel('本例提供的能力','pf-provided');values(provided,s.provided_capabilities);
  if(!s.provided_capabilities.length)note(provided,scene.cursor>1?'本例没有提供满足条件的能力。':'推进到“选择具体功能与能力集合”，再查看本例输入。');root.append(provided);
  const alternatives=s.requirement_alternatives.length?s.requirement_alternatives:s.required_capabilities.length?[s.required_capabilities]:[];
  const equation=el('div','pf-rule','每组内部：全部满足 AND　｜　不同组之间：任选一组 OR');root.append(equation);
  const flow=el('div','pf-alternatives');
  alternatives.forEach((required,index)=>{
    if(index)flow.append(el('div','pf-or','或 OR'));
    const check=s.alternative_checks.find(c=>c.index===index),inputComparison=!check&&!s.requirement_alternatives.length&&s.feature_status==='skipped';
    const card=panel(`方案 ${index+1} · 组内全部满足`,check?(check.satisfied?'pf-met':'pf-missing'):'');card.dataset.alternativeIndex=index;
    const list=el('ul','pf-requirement-list');
    for(const cap of required){const missing=check?check.missing.includes(cap):inputComparison&&!s.provided_capabilities.includes(cap),item=el('li');item.dataset.requirement=cap;item.append(chip(cap,check||inputComparison?(missing?'missing':'met'):''),el('span','pf-check',check?(missing?'缺少':'已具备'):inputComparison?(missing?'本例未提供':'本例已提供'):'待正式检查'));list.append(item);}
    if(!required.length)list.append(el('li','','本组没有额外能力条件。'));
    card.append(list,el('strong','pf-alt-result',check?(check.satisfied?'整组满足':'整组不满足'):inputComparison?'逐项对照来自本例输入；跳过结果来自正式状态':'尚未检查'));flow.append(card);
  });
  if(!alternatives.length)note(flow,'本情境的条件尚未载入。');root.append(flow);
  const result=el('div','pf-rule-result');result.dataset.featureStatus=s.feature_status;
  result.append(el('strong','',s.feature_status==='eligible'?'至少一组完整满足 · 可计划该功能':s.feature_status==='skipped'?'条件未满足 · 跳过可选插件':'推进检查，观察每组与最终结果'));
  if(s.unmet_requirement)result.append(el('code','',s.unmet_requirement));
  if(s.feature_status==='skipped'&&s.feature_id)note(result,'未满足时，正式规则只报告第一备选按字典序首个缺失；其他缺失仍在各组中可见。');
  root.append(result);note(root,'本批注册项的非空备选均为单能力组；图中保留“组内全部满足”规则，没有编造多能力注册项。');
  return root;
}
function operation(scene){
  const s=scene.state,root=el('div','pf-operation'),request=panel('本例请求'),declared=panel('固定 Adapter 声明');
  row(request,'Provider',s.provider_id||'尚未选择');row(request,'操作',s.operation||'尚未选择');if(s.requested_field)row(request,'显式更新字段',s.requested_field);
  const d=s.provider_declarations.find(d=>d.provider===s.provider_id);
  if(d){note(declared,'声明支持的操作');values(declared,d.operations);note(declared,'声明支持的设置');values(declared,d.runtime_settings);}else note(declared,'先载入本情境并选择声明，再进行调用前校验。');
  root.append(request,el('div','pf-arrow','校验 →'),declared);
  if(s.required_capabilities.length)root.append(requirements(scene));
  return root;
}
function catalog(scene){
  const s=scene.state,root=panel('账号目录不能由 Provider 声明推断','pf-catalog');
  row(root,'账号存在观察',s.account_observation.status==='observed'?'已有独立冻结观察':'未取得');
  row(root,'逐账号模型目录',s.account_observation.model_catalog_status==='not_captured'?'未取得冻结目录':s.account_observation.model_catalog_status);
  note(root,s.account_observation.model_catalog_reason);
  if(s.catalog_origin==='schema_fixture'){
    const example=panel('仅为合成 schema 样例 · 非真实账号目录','pf-fixture');
    row(example,'演示账号',s.schema_fixture.account_id);row(example,'样例模型',s.schema_fixture.models.join('、'));
    const table=el('table','pf-schema-table'),head=el('tr');for(const text of ['字段','本例显式请求','同一样例允许值'])head.append(el('th','',text));const thead=el('thead');thead.append(head);table.append(thead);const tbody=el('tbody');
    for(const [name,value,allowed] of [['model',s.requested_model,s.schema_fixture.models],['reasoning_effort',s.requested_effort,s.schema_fixture.efforts_by_model[s.requested_model]||[]],['service_tier',s.requested_tier,s.schema_fixture.service_tiers_by_model[s.requested_model]||[]]]){
      if(!value)continue;const tr=el('tr');tr.dataset.schemaField=name;tr.append(el('th','',name),el('td','',value),el('td','',allowed.join('、')||'该样例没有此模型的允许值'));tbody.append(tr);
    }table.append(tbody);example.append(table);note(example,'此表只解释显式设置的校验。它不证明任何线上账号支持这些模型、effort或速度。');root.append(example);
  }else note(root,'目录缺口只阻止依赖目录的本地案例继续；固定源码机制研究与实时账号观察分开。');
  return root;
}
function authority(scene){
  const s=scene.state,root=el('div','pf-authority'),native=panel('原生 Provider 权限预设'),isa=panel('ISA 授权 · 独立边界');
  row(native,'Provider',s.provider_id||'尚未选择');row(native,'本例预设',s.native_permission_preset||'尚未读取');
  note(native,'预设只属于原生配置。');row(isa,'授权结果',label(s.isa_authorization));row(isa,'本地 ISA 调用计数',String(s.isa_call_count));note(isa,'真正调用时独立检查授权；full-access 不授予 ISA 权限。');
  root.append(native,el('div','pf-authority-fence','独立检查'),isa);return root;
}
function boundaries(s){
  const root=el('div','pf-boundaries');root.setAttribute('aria-label','三类依据');
  for(const [title,text] of [['能力声明','固定提交中的操作、设置与功能要求；不证明已执行。'],['账号目录','账号存在观察与模型目录不同；本批逐账号模型目录尚未取得。'],['授权','Provider预设与ISA授权不同；此演示不作真实授权。']]){const p=panel(title);note(p,text);root.append(p);}return root;
}

export function createProviderPreflight(root,{subject,content,ports={},signal,initialState}={}){
  const objectId=subject?.objectId||subject?.id||content?.mechanism?.owner_id;
  const entry=getProviderPreflightEntry(objectId),object=content?.objects?.find(o=>o.id===objectId);
  if(!entry||!object?.mechanism_ids?.includes(PREFLIGHT_ID))throw new Error('preflight_subject_mismatch');
  const simulation=createProviderPreflightSimulation(content),m=simulation.mechanism,delegates=getProviderPreflightModes(content,objectId);
  simulation.chooseCase('requirements_first_alternative');
  let shown=true,destroyed=false,scrollTop=0,focusAction=null;const open={sources:false,requirements:false,declarations:false};
  const host=el('section','provider-preflight');host.setAttribute('aria-label','Provider 调用前预检');root.append(host);const listeners=new AbortController();
  const emit=(name,value)=>{if(shown&&!destroyed&&!signal?.aborted)ports[name]?.(value);};
  const button=(text,action)=>{const b=el('button','',text);b.type='button';b.dataset.preflightAction=action;return b;};
  function render(focus){
    if(destroyed)return;const before=host.scrollTop,scene=simulation.getScene(),s=scene.state;
    const header=el('header','pf-header');header.append(button('返回','return'),el('div'));header.lastChild.append(el('span','pf-eyebrow','本地案例 · 固定规则'),el('h2','','调用前预检：条件、目录与权限'));
    const nav=el('div','pf-controls');if(delegates.length){const mode=el('select');mode.dataset.preflightAction='mode';mode.setAttribute('aria-label','对象内的机制');for(const item of [{mechanismId:PREFLIGHT_ID,title:m.title},...delegates]){const opt=el('option','',item.title);opt.value=item.mechanismId;mode.append(opt);}nav.append(el('span','','阅读主题'),mode);}
    const controls=el('div','pf-controls'),select=el('select');select.dataset.preflightAction='case';select.setAttribute('aria-label','预检情境');
    for(const group of groups){const optgroup=el('optgroup');optgroup.label=group.title;for(const id of group.ids){const c=m.cases.find(c=>c.id===id);if(c){const option=el('option','',c.title);option.value=id;optgroup.append(option);}}select.append(optgroup);}select.value=scene.case_id;controls.append(el('span','','演示情境'),select);
    const pending=el('div','pf-pending','所选情境尚未载入。先载入本例输入，再按固定步骤选择条件并检查。');
    const body=el('main','pf-main');body.dataset.caseId=scene.case_id;
    if(scene.case_id.startsWith('requirements_'))body.append(requirements(scene));else if(['catalog-gap','model','effort','tier'].includes(scene.case_id))body.append(catalog(scene));else if(scene.case_id==='permissions')body.append(authority(scene));else body.append(operation(scene));
    const actions=el('div','pf-actions'),next=button(scene.cursor===0?'载入所选情境':scene.next?.title||'本情境完成','next');next.disabled=!scene.next;
    const back=button('上一步','back');back.disabled=!scene.cursor;actions.append(next,back,button('重新演示','reset'));
    const receipt=panel('本例判断与调用边界','pf-receipt');receipt.setAttribute('role','status');
    row(receipt,'请求校验',label(s.validation));row(receipt,'可选功能',label(s.feature_status));
    const counts=el('div','pf-counts');const n=row(counts,'本地路径抵达 Backend.execute',String(s.native_call_count));n.dataset.localCallCount=s.native_call_count;row(counts,'ISA 调用',String(s.isa_call_count));receipt.append(counts);
    if(s.unmet_requirement||s.error)note(receipt,[s.unmet_requirement,s.error].filter(Boolean).join(' · '));
    const observable=scene.current?.observable;note(receipt,Array.isArray(observable)?observable.join('；'):observable||'所有动作只改变本地演示。');
    if(typeof scene.outcome==='string')note(receipt,scene.outcome);note(receipt,'这些计数不是线上请求记录；通过一个检查也不代表账号目录已验证或ISA已授权。');
    const catalogDetails=el('details','pf-details');catalogDetails.dataset.detail='requirements';catalogDetails.open=open.requirements;catalogDetails.append(el('summary','','固定注册功能的 requirements'));
    for(const requirement of s.requirement_catalog){const line=el('div','pf-static-rule');line.append(el('strong','',requirement.feature_id),el('p','',requirement.alternatives.length?requirement.alternatives.map((group,i)=>`方案${i+1}（全部）：${group.join(' + ')}`).join('　或　'):'声明未附额外能力条件（默认允许）'));catalogDetails.append(line);}
    const declarations=el('details','pf-details');declarations.dataset.detail='declarations';declarations.open=open.declarations;declarations.append(el('summary','','固定 Provider 声明 · 不含账号模型推断'));
    for(const d of s.provider_declarations){const line=el('div','pf-static-rule');line.append(el('strong','',d.label));note(line,'操作：'+d.operations.join('、'));note(line,'设置：'+d.runtime_settings.join('、'));note(line,'功能能力：'+d.harness.join('、'));declarations.append(line);}
    const sources=el('details','pf-details pf-sources');sources.dataset.detail='sources';sources.open=open.sources;sources.append(el('summary','','本步规则与固定来源'));
    for(const invariant of m.invariants)note(sources,invariant);
    for(const id of scene.evidence_ids){const e=simulation.evidence.get(id),b=button(`${e.claim||e.symbol||id} · L${e.start_line}–${e.end_line}`,'source');b.dataset.evidence=id;sources.append(b);}
    const footer=el('footer','pf-footer');for(const id of new Set([m.owner_id,...(scene.current?[scene.current.from,scene.current.to]:[])])){const object=simulation.objects.get(id);if(object){const b=button(object.title||id,'object');b.dataset.objectId=id;footer.append(b);}}
    host.replaceChildren(header,...(delegates.length?[nav]:[]),controls,...(scene.cursor===0?[pending]:[]),body,actions,receipt,boundaries(s),catalogDetails,declarations,sources,footer);host.scrollTop=before;
    if(shown&&focus)[...host.querySelectorAll('[data-preflight-action]')].find(n=>n.dataset.preflightAction===focus&&!n.disabled)?.focus({preventScroll:true});
  }
  host.addEventListener('click',event=>{
    const b=event.target.closest?.('[data-preflight-action]');if(!shown||destroyed||signal?.aborted||!b||!host.contains(b)||b.disabled)return;
    const action=b.dataset.preflightAction;focusAction=action;
    if(action==='return')return emit('return');if(action==='source')return emit('source',b.dataset.evidence);
    if(action==='object')return emit('select',{objectId:b.dataset.objectId});
    if(!['next','back','reset'].includes(action))return;
    if(action==='next')simulation.perform(simulation.getScene().next?.id);if(action==='back')simulation.back();if(action==='reset')simulation.reset();render(action);emit('change',simulation.getScene());
  },{signal:listeners.signal});
  host.addEventListener('change',event=>{
    if(!shown||destroyed||signal?.aborted)return;const action=event.target.dataset.preflightAction;
    if(action==='case'){simulation.chooseCase(event.target.value);focusAction='case';render('case');emit('change',simulation.getScene());}
    if(action==='mode'){const target=delegates.find(m=>m.mechanismId===event.target.value);event.target.value=PREFLIGHT_ID;if(target)emit('navigate',target.route);}
  },{signal:listeners.signal});
  host.addEventListener('toggle',event=>{if(event.target.dataset.detail in open)open[event.target.dataset.detail]=event.target.open;},{capture:true,signal:listeners.signal});
  function getViewState(){return destroyed?null:{schema:'provider-preflight-view.v1',objectId,batchKey:content.batchKey,simulation:simulation.getViewState(),scrollTop:shown?host.scrollTop:scrollTop,focusAction,open:{...open}};}
  function restoreViewState(saved){
    if(destroyed||saved?.schema!=='provider-preflight-view.v1'||saved.objectId!==objectId||saved.batchKey!==content.batchKey||!simulation.restoreViewState(saved.simulation))return false;
    scrollTop=Number.isFinite(saved.scrollTop)?Math.max(0,saved.scrollTop):0;focusAction=typeof saved.focusAction==='string'?saved.focusAction:null;
    for(const key of Object.keys(open))open[key]=saved.open?.[key]===true;render(focusAction);host.scrollTop=scrollTop;return true;
  }
  function destroy(){if(destroyed)return;destroyed=true;shown=false;listeners.abort();host.remove();signal?.removeEventListener('abort',destroy);}
  render();if(initialState)restoreViewState(initialState);signal?.addEventListener('abort',destroy,{once:true});if(signal?.aborted)destroy();
  return {show(){if(!destroyed){shown=true;host.hidden=false;host.scrollTop=scrollTop;}},hide(){if(!destroyed){scrollTop=host.scrollTop;shown=false;host.hidden=true;}},destroy,getViewState,restoreViewState,getScene:()=>destroyed?null:simulation.getScene()};
}
