import {catalogCaseGroup, catalogFieldObserved, CATALOG_ID} from './provider-catalog.model.js';

export function createCatalogViews(el) {
  const note=(root,value)=>root.append(el('p','pc-muted',value));
  const panel=(title)=>{const n=el('section','pc-panel');n.append(el('h3','',title));return n;};
  const chips=(values)=>{const n=el('div','pc-models');for(const value of values)n.append(el('span','pc-pill',value));return n;};
  const list=(values)=>{const n=el('ol','pc-flow-list');for(const value of values)n.append(el('li','',value));return n;};
  const observed=catalogFieldObserved;
  function models(root,catalog) {
    root.append(chips(catalog.models||[]));
    if(catalog.default_model)note(root,`默认模型：${catalog.default_model}`);
  }
  function registration(scene) {
    const s=scene.state,root=el('div','pc-layout'),registered=panel('① 代码注册与本地目录'),dispatch=panel('② 账号解析与 Adapter 派发');
    root.dataset.catalogView='registration';
    if(!observed(scene,'registration_entries'))note(registered,'载入注册情境后查看冻结的代码声明。');
    for(const entry of s.registration_entries){const row=el('div','pc-registry-row');row.append(el('strong','',entry.provider),el('p','',entry.local_catalog?'local_catalog · 注册列举可展开本地目录':'远端目录 · 注册列举只保留账号路由'));registered.append(row);}
    if(observed(scene,'registration_request'))registered.append(el('p','',`请求：${JSON.stringify(s.registration_request)}`));
    if(!observed(scene,'adapter_calls'))note(dispatch,'尚未解析账号或调用 Adapter。');
    else {
      dispatch.append(list(s.account_lookups.map(lookup=>`${lookup.kind} → ${lookup.value}`)));
      for(const call of s.adapter_calls)dispatch.append(el('div','pc-route',`${call.provider} → ${call.account_id} → ${call.route_id}`));
      if(!s.adapter_calls.length)dispatch.append(el('p','pc-emphasis','没有 Adapter 派发'));
    }
    const result=panel('③ 注册响应 / 选定账号目录');result.classList.add('pc-wide');
    if(s.error)result.append(el('div','pc-error',s.error));
    else if(!observed(scene,'registry_result'))note(result,'尚未返回；静态注册声明与当前账号目录分别观察。');
    else if(s.registry_result.backends){
      for(const backend of s.registry_result.backends){
        const row=el('div','pc-registry-row');row.append(el('strong','',backend.provider));
        for(const route of backend.routes||[]){row.append(el('p','',`${route.account_id} → ${route.backend_route_id}`));if(route.models)models(row,route);else note(row,'此远端路由未展开模型目录。');}
        result.append(row);
      }
      note(result,'本次返回后端注册投影；没有把所有远端账号的目录读入。');
    }else{models(result,s.registry_result);note(result,`目录来源账号：${s.registry_result.account_id||'未提供'} · route：${s.registry_result.backend_route_id||'未提供'}`);}
    root.append(registered,dispatch,result);return root;
  }
  function pages(scene) {
    const s=scene.state,root=el('div','pc-layout'),identity=panel('账号决定目录与缓存键');
    const pageTrace=scene.trace.filter(step=>step.changes.some(c=>c.field==='request_count')&&step.changes.some(c=>c.field==='cursor'));
    const acceptedObserved=observed(scene,'catalog_accepted');
    const pagingTitle=acceptedObserved&&s.catalog_accepted
      ? (observed(scene,'cache_hit')&&s.cache_hit&&s.request_count===0?'缓存目录已接纳 · 未发分页请求':'接纳前的分页记录 · 当前目录已接纳')
      : acceptedObserved&&s.error?'读取记录 · 完整目录未接纳'
      : pageTrace.length?'分页暂存 · 尚未完整接纳':'分页观察 · 尚未收到分页';
    const paging=panel(pagingTitle);
    root.dataset.catalogView='pages';
    const route=el('div','pc-route');route.append(el('span','pc-pill',observed(scene,'account_id')?s.account_id:'尚未选择账号'),el('span','','→'),el('span','pc-pill',observed(scene,'route_id')?s.route_id:'尚未选择 route'));identity.append(route);
    const cache=el('div','pc-cache');cache.append(el('strong','',`缓存键：${observed(scene,'cache_key')?s.cache_key:'待选择账号'}`));
    note(cache,'按 account_id 加锁；route 变化不改变这个缓存键。');
    const scale=el('div','pc-cache-scale');
    for(const [label,ids] of [['年龄 < 60 秒',['cache_hit','route_change_cached']],['年龄 ≥ 60 秒',['cache_expired']]])scale.append(el('span',ids.includes(scene.case_id)&&scene.cursor?'active':'',label));
    cache.append(scale,el('p','pc-emphasis',observed(scene,'cache_hit')?(s.cache_hit?'本案例命中缓存 · 返回副本':'本案例读取目录后记录结果'):'缓存结果尚未记录'));
    note(cache,scene.case_id==='cache_hit'?'冻结案例年龄为59秒。':scene.case_id==='cache_expired'?'冻结案例年龄恰好60秒，重新读取。':scene.case_id==='route_change_cached'?'同一账号换route，仍可能复用旧缓存。':'年龄由 monotonic 计算；本页没有实时缓存计时器。');identity.append(cache);
    const journal=el('ol','pc-page-journal');
    for(const step of pageTrace){const values=Object.fromEntries(step.changes.map(c=>[c.field,c.after]));journal.append(el('li','',`第 ${values.request_count} 页 · cursor ${values.cursor||'起点'} → ${values.next_cursor||'末页'} · 累计隐藏 ${values.hidden_count}`));}
    paging.append(journal);
    if(!pageTrace.length)note(paging,observed(scene,'cache_hit')&&s.cache_hit?'缓存已返回，本案例没有分页请求。':'尚未收到分页。');
    if(observed(scene,'observed_model_ids')){paging.append(chips(s.observed_model_ids));note(paging,`累计过滤 hidden：${s.hidden_count}；这些模型 ID 是分页观察。`);}
    note(paging,'每页 limit=100 · timeout=10 · 最多20页；重复检查只比较相邻cursor。');
    const accepted=panel('完整目录接纳边界');accepted.classList.add('pc-wide');accepted.dataset.catalogResult='';
    if(!observed(scene,'catalog_accepted'))accepted.append(el('p','pc-emphasis',pageTrace.length?'已收到分页，尚未接纳完整目录':'尚未观察完整目录结果'));
    else if(s.error)accepted.append(el('div','pc-error',s.error),el('p','','本案例未接纳完整目录；保留此前分页观察供检查。'));
    else if(s.catalog_accepted){accepted.append(el('p','pc-emphasis','教学目录已完整接纳'));models(accepted,s.catalog);}
    root.append(identity,paging,accepted);return root;
  }
  function tuple(scene) {
    const s=scene.state,root=el('div','pc-panel');root.dataset.catalogView='tuple';
    root.append(el('h3','','同一教学账号的 model × effort × tier'));
    note(root,'选择已冻结的规范化输入情境。粗色边标记显式字段；非显式旧值可以按目录协调。');
    if(!observed(scene,'requested')){root.append(el('p','pc-emphasis','尚未载入设置和账号目录'));return root;}
    const fields=el('div','pc-tuple');
    for(const [name,title] of [['model','model'],['reasoning_effort','effort'],['service_tier','tier']]){
      const box=el('div',`pc-field ${s.explicit_fields.includes(name)?'explicit':''}`);
      box.append(el('div','',title),el('strong','',s.requested[name]||'空值'),el('small','',s.explicit_fields.includes(name)?'显式更新 · 严格校验':'非显式旧值 · reconcile'));
      let options=s.catalog.models||[];
      if(name==='reasoning_effort')options=s.catalog.efforts_by_model?.[s.requested.model]||[];
      if(name==='service_tier')options=s.catalog.service_tiers_by_model?.[s.requested.model]||[];
      const unknownModel=name!=='model'&&!s.catalog.models?.includes(s.requested.model);
      box.append(el('div','pc-choices',options.length?`请求模型的目录可选：${options.join(' / ')}`:unknownModel?'请求模型未在目录列出，尚无对应选项':'该字段未列出可选项'));fields.append(box);
    }
    root.append(fields);
    if(!observed(scene,'accepted'))root.append(el('p','pc-emphasis','待执行本情境的校验／协调'));
    else if(s.phase==='rejected')root.append(el('div','pc-error',s.error),el('p','','没有得到可采用的设置组合。'));
    else if(s.phase==='validated'){
      const result=el('div','pc-result');result.append(el('strong','','可采用的设置组合'));
      const values=el('div','pc-snapshot');
      for(const name of ['model','reasoning_effort','service_tier']){const box=el('div','pc-field');box.append(el('small','',name),el('strong','',s.resolved[name]||'空值'));values.append(box);}
      result.append(values,el('p','',`所选模型输入声明：${s.selected_model_modalities.join(' / ')||'未提供'}`));root.append(result);
    }
    note(root,'此情境未派生 Harness 原生媒体能力；模型的 image 输入声明单独呈现。');return root;
  }
  function media(scene) {
    const s=scene.state,root=el('div','pc-panel');root.dataset.catalogView='media';root.append(el('h3','','Provider 声明 → 原生传输 → route 媒体观察'));
    const loaded=observed(scene,'provider_media'),derived=s.phase==='media_derived',chain=el('div','pc-media-chain');
    const entries=[['Provider 媒体声明',loaded?(s.provider_media?'已声明':'未声明'):'尚未载入'],['图片传输',loaded?s.image_input_transport:'尚未载入'],['route 目录',loaded?(s.route_catalog_module||'未配置目录模块'):'尚未载入']];
    for(const [title,value]of entries){const n=el('div','pc-media-gate');n.append(el('strong','',title),el('p','',value));chain.append(n);}root.append(chain);
    if(!derived)root.append(el('p','pc-emphasis','尚未执行媒体能力派生'));
    else{
      const observations={supports_image:'route目录支持图片',denied:'route目录不提供图片',error:'route目录读取失败',not_read:'本情境未查询route目录'};
      root.append(el('p','',observations[s.route_catalog_observation]||s.route_catalog_observation),el('p','pc-muted',`route目录调用：${s.route_catalog_calls.join(' / ')||'无'}`));
      if(!s.route_catalog_module)note(root,'没有route目录模块时，固定算法按Provider媒体声明派生。');
      const result=el('div','pc-result');result.append(el('strong','',`native_image_input：${s.native_image_input?'提供':'不提供'}`),chips(s.effective_harness_capabilities));root.append(result);
      note(root,`基础能力：${s.base_harness_capabilities.join(' / ')||'无'}；active_turn_input独立保留。`);
    }
    note(root,'本情境仅演示媒体派生，没有执行 model × effort × tier 校验。ISA 授权未评估。');return root;
  }
  return {render(scene){const group=catalogCaseGroup(scene.mechanism_id,scene.case_id);
    return scene.mechanism_id===CATALOG_ID?(group==='registration'?registration(scene):pages(scene)):(group==='media'?media(scene):tuple(scene));}};
}
