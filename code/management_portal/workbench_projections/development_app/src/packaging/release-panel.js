import {appendPlatformResolution,appendPlatformResults} from './platforms.js';
import {releaseSession} from './release-session.js';
import {runPresentation,safeResultUrl} from './release-runs.js';

export function releasePanel({configRoot,resultRoot,api,sdk,signal,readState,writeState,collect,requestText,loadChecklist,prepare,beforeSave=async()=>{},act}) {
  const el=(tag,text)=>{const node=document.createElement(tag);if(text)node.textContent=text;return node;};
  const button=(label,fn,readOnly=false)=>{const node=el('button',label);node.type='button';node.className='portalButton';node.addEventListener('click',()=>readOnly?read(fn):act(fn));return node;};
  const read=async fn=>{try{await fn();}catch(error){if(!signal.aborted)status.textContent=`发布记录读取失败：${error.message}`;}};
  const session=releaseSession({api,readState,writeState,collect,requestText});
  let visible=true,timer,readGeneration=0,displayedRunId='';
  const caption=el('h2','发布配置'),nameLabel=el('label','配置名称'),name=el('input');name.className='portalField';name.maxLength=120;name.value=readState()?.config?.name||'';nameLabel.append(name);
  const chooseLabel=el('label','已保存的配置'),choose=el('select');choose.className='portalField';choose.setAttribute('aria-label','已保存的配置');chooseLabel.append(choose);
  const configStatus=el('p'),configActions=el('div');configActions.className='packagingActions';
  const save=async copy=>{await beforeSave();const config=await session.save(name.value,{copy});await listConfigs();configStatus.textContent=`已保存 ${config.name}`;};
  configActions.append(button('保存配置',()=>save(false)),button('另存为新配置',()=>save(true)),button('加载配置',async()=>{
    if(!choose.value)throw Error('请选择已保存的配置');
    const checklist=await session.load(choose.value);await loadChecklist(checklist);
  }),button('刷新配置列表',()=>listConfigs(),true));
  configRoot.append(caption,nameLabel,chooseLabel,configActions,configStatus);
  const listConfigs=async()=>{
    const configs=await api.configs();if(signal.aborted)return;
    const selected=readState()?.config?.config_id||choose.value;
    choose.replaceChildren(new Option('请选择配置',''));
    for(const config of configs)choose.append(new Option(config.name,config.config_id));
    choose.value=selected;
  };
  const tauLabel=el('label'),tau=el('input');tau.type='checkbox';tau.checked=readState()?.validate_tau===true;
  tauLabel.className='packagingChoice';tauLabel.append(tau,document.createTextNode('完整发布测试中使用 TAU 验证'));
  tau.addEventListener('change',()=>act(()=>writeState({...readState(),validate_tau:tau.checked})));
  const hint=el('p','本次 TAU 验证与清单中是否带出 TAU 功能独立。完整测试由实际监督 Resident 验收；安装完成不等于测试通过。');hint.className='packagingHint';
  const actions=el('div');actions.className='packagingActions';
  const start=async mode=>{await prepare();try{await session.start(mode,tau.checked);}finally{renderPending();}await refresh();};
  actions.append(button('发布',()=>start('publish')),button('完整发布测试',()=>start('full_test')));
  configRoot.append(tauLabel,hint,actions);
  const runLabel=el('label','发布运行'),runs=el('select');runs.className='portalField';runs.setAttribute('aria-label','发布运行');runLabel.append(runs);
  const status=el('p'),body=el('div'),pending=el('div');
  resultRoot.append(el('h2','发布进度与产物'),el('p','这里显示已发起的发布或完整测试记录，不影响上方配置编辑。'),runLabel,button('刷新运行结果',()=>refresh(),true),status,pending,body);
  runs.addEventListener('change',()=>read(async()=>{if(runs.value){await session.selectRun(runs.value);await refresh();}}));
  const field=(label,value)=>{if(value!==undefined&&value!==null&&value!=='')body.append(el('p',`${label}：${typeof value==='object'?JSON.stringify(value):value}`));};
  const link=(label,value)=>{
    const url=safeResultUrl(value);
    if(!url){field(label,value);return;}
    // The projection sandbox prohibits popups. Keep external instance addresses
    // directly selectable instead of presenting a link the browser cannot open.
    if(url.startsWith('/'))body.append(button(label,()=>sdk.openPage(url)));
    else {const row=el('label',`${label}（复制地址打开）`),address=el('input');address.className='portalField';address.readOnly=true;address.value=url;address.addEventListener('focus',()=>address.select());row.append(address);body.append(row);}
  };
  const render=run=>{
    displayedRunId=run.run_id;body.replaceChildren();const presentation=runPresentation(run);status.textContent=presentation.label;
    field('运行 ID',run.run_id);field('配置',run.config_id);field('操作',run.mode==='full_test'?'完整发布测试':'发布');
    field('负责 Resident',run.responsible_resident_id);field('监督 Resident',run.supervisor_resident_id||(run.mode==='full_test'?'尚未指派':'不适用（仅完整发布测试）'));
    field('当前阶段',run.phase);field('错误',run.error);field('未覆盖项',run.uncovered||run.uncovered_items);
    if(run.instance_url)link('测试实例',run.instance_url);
    for(const artifact of run.artifacts||[])link(artifact.title||artifact.name||'产物',artifact.url||artifact.address||artifact.path);
    const targetEvidence=[...(run.stages||[])].reverse().map(stage=>stage.evidence).find(e=>e?.support_matrix)||run;
    field('选定目标',targetEvidence.selected_targets||run.manifest?.selected_targets||run.snapshot?.target_platforms);
    field('目标范围来源',targetEvidence.target_resolution||run.manifest?.target_resolution);
    appendPlatformResolution(body,{support_matrix:targetEvidence.support_matrix||run.manifest?.support_matrix,capability_gaps:targetEvidence.capability_gaps||run.manifest?.capability_gaps});
    appendPlatformResults(body,run.platform_results||run.result?.platform_results||targetEvidence.platform_results);
    for(const stage of run.stages||[])if(stage.evidence!==targetEvidence)appendPlatformResults(body,stage.evidence?.platform_results);
    if(run.stages?.length){
      const details=el('details');details.append(el('summary',`阶段记录（${run.stages.length}）`));
      details.addEventListener('toggle',()=>{
        if(details.open&&!details.querySelector('pre'))details.append(el('pre',JSON.stringify(run.stages,null,2)));
      });
      body.append(details);
    }
    if(run.manifest){const details=el('details');details.append(el('summary','工程 Manifest'),el('pre',typeof run.manifest==='string'?run.manifest:JSON.stringify(run.manifest,null,2)));body.append(details);}
    if(run.manifest_url)link('工程 Manifest',run.manifest_url);
    if(run.result){const details=el('details');details.append(el('summary','实际运行记录'),el('pre',JSON.stringify(run.result,null,2)));body.append(details);}
    if(run.snapshot){const details=el('details');details.append(el('summary','本次运行的配置快照'),el('pre',JSON.stringify(run.snapshot,null,2)));body.append(details);}
    return presentation;
  };
  const renderPending=()=>{
    pending.replaceChildren();
    for(const request of readState()?.pending_runs||[])pending.append(button(`核实并重试原运行 ${request.request_key}`,async()=>{await session.retry(request.request_key);await refresh();}));
  };
  const refresh=async()=>{
    clearTimeout(timer);const generation=++readGeneration;
    renderPending();
    const records=await api.runs();if(signal.aborted||generation!==readGeneration)return;
    const selected=readState()?.run_id||records[0]?.run_id;
    runs.replaceChildren(new Option('请选择运行',''));
    for(const record of records)runs.append(new Option(`${record.run_id} · ${runPresentation(record).label}`,record.run_id));
    if(selected&&!records.some(r=>r.run_id===selected))runs.append(new Option(selected,selected));
    runs.value=selected||'';
    if(!selected){status.textContent='尚未启动发布运行';body.replaceChildren();return;}
    const record=await api.run(selected);if(signal.aborted||generation!==readGeneration)return;
    const selection=body.ownerDocument.getSelection();
    const copying=selection&&!selection.isCollapsed&&(resultRoot.contains(selection.anchorNode)||resultRoot.contains(selection.focusNode));
    const active=body.ownerDocument.activeElement;
    const editingAddress=body.contains(active)&&active.matches('input,textarea');
    // Keep the selected nodes (and a selected address input) alive while copying.
    // Poll once more even for a terminal update so deferred facts appear afterward.
    const deferred=displayedRunId===record.run_id&&(copying||editingAddress);
    const presentation=deferred?runPresentation(record):render(record);
    if(visible&&(deferred||!presentation.terminal))schedulePoll(generation);
  };
  // Read-only polling must not acquire the form's interaction lock: inert
  // blurs the active resource picker even though its contents did not change.
  const schedulePoll=generation=>{
    timer=setTimeout(async()=>{
      if(signal.aborted||!visible||generation!==readGeneration)return;
      try{await refresh();}
      catch(error){
        if(signal.aborted||!visible||readGeneration!==generation+1)return;
        status.textContent=`运行状态刷新失败：${error.message}`;
        schedulePoll(readGeneration);
      }
    },5000);
  };
  signal.addEventListener('abort',()=>{clearTimeout(timer);readGeneration++;},{once:true});
  return {ready:()=>Promise.all([read(listConfigs),read(refresh)]),refresh,hide(){visible=false;clearTimeout(timer);},show(){visible=true;return read(refresh);},destroy(){clearTimeout(timer);readGeneration++;}};
}
