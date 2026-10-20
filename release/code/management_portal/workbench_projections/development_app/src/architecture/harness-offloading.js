import './harness-offloading.css';
import {createHarnessWorkbench} from './harness-mechanisms.js';
import {HARNESS_OFFLOADING_ID, HARNESS_OFFLOADING_ADAPTER, HARNESS_PIPELINE_MODE_IDS, createHarnessOffloadingSimulation} from './harness-offloading.model.js';
export {HARNESS_OFFLOADING_ID, HARNESS_OFFLOADING_ADAPTER, HARNESS_PIPELINE_MODE_IDS} from './harness-offloading.model.js';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const note=text=>el('p','hof-note',text);
const btn=(text,action,value)=>{const n=el('button','',text);n.type='button';n.dataset.offloadAction=action;if(value)n.dataset.value=value;return n;};
const num=value=>value.toLocaleString('en-US');

export function createHarnessOffloadingWorkbench(root,{subject,content,ports={},signal,initialState}={}){
  content=structuredClone(content);
  const objectId=subject?.objectId,object=content?.objects?.find(o=>o.id===objectId);
  if(!['worker.harness','worker.harness.pipeline'].includes(objectId)||subject.mechanismId!==HARNESS_OFFLOADING_ID
    ||!object?.mechanism_ids?.includes(HARNESS_OFFLOADING_ID))throw Error('harness_offloading:subject');
  const sim=createHarnessOffloadingSimulation(content),host=el('section','harness-offloading');host.setAttribute('aria-label','工具输出外置工作台');root.append(host);
  let shown=true,destroyed=false,sourcesOpen=false,scrollTop=0,focusKey=null,recallOffset=0,recallLimit=120,recallSlice=null;
  const listeners=new AbortController(),emit=(name,value)=>{if(shown&&!destroyed&&!signal?.aborted)ports[name]?.(value);};
  function diagram(scene){
    const s=scene.state,prepared=scene.observed_fields.includes('original_bytes'),terminal=scene.complete;
    const diagram=el('div','hof-diagram');diagram.dataset.offloadDiagram='tool-output-offloading';
    const original=el('div','hof-original');original.append(el('h3','','① 工具原始结果'),el('strong','hof-size',prepared?`${num(s.original_bytes)} UTF-8 字节`:'等待载入结果'),
      el('span','hof-muted',prepared?`${num(s.original_chars)} 字符 · ${s.result_kind} · ${s.source}`:'固定合成案例'));
    const paper=el('pre','hof-paper',prepared?`${sim.original().slice(0,65)}\n⋮\n${sim.original().slice(-25)}`:'尚未观察到本案例正文');
    original.append(paper,note('教学正文使用固定重复字符；字节阈值和字符预览分别计量。'));diagram.append(original);
    const gates=el('div','hof-gates');gates.append(el('h3','','② 外置前提'));
    for(const [label,value] of [
      ['阈值',`${num(s.original_bytes)} ${s.original_bytes>=s.trigger_bytes?'≥':'<'} ${num(s.trigger_bytes)} 字节`],
      ['Portal 工具能力',s.portal_tools?'portal_tools 已声明':'缺 portal_tools'],
      ['稳定附件范围',s.stable_scope?'范围可用':'缺稳定范围'],
      ['召回入口 / Schema',`${s.recall_tool?'入口存在':'入口未装配'} / ${s.recall_schema?'Schema 可用':'Schema 缺失'}`],
      ['来源 / 类型',`${s.source} / ${s.result_kind} / ${s.tool_name}`],
      ['本轮预算',`已外置 ${s.already_offloaded} 项`],
    ]){const line=el('div','hof-gate');line.append(el('strong','',label),el('span','',prepared?value:'待观察'));gates.append(line);}
    const meter=el('div','hof-meter');if(prepared){const fill=el('div','');fill.style.width=`${Math.min(100,s.original_bytes/s.trigger_bytes*100)}%`;meter.append(fill);}gates.append(meter,note('仅受控纯文本可变换；媒体、未知结构、原生工具和召回结果保留原内容。'));diagram.append(gates);
    const outputs=el('div','hof-outputs'),artifact=el('div',`hof-artifact ${s.artifact_retained?'retained':''}`),visible=el('div','hof-visible');
    artifact.append(el('h3','','③ 完整原文附件'),el('strong','',scene.observed_fields.includes('artifact_retained')?(s.artifact_retained?'full-v1 · 完整原文已保存':'没有留存附件'):'尚未观察到存储结果'),
      note(scene.observed_fields.includes('put_attempts')?`put 尝试 ${s.put_attempts} 次；内容精确保留与可见文本分开。`:'尚未尝试保存。'));
    visible.append(el('h3','','④ Provider 可见文本'),el('strong','',scene.observed_fields.includes('replaced')?(s.replaced?'短引用 + 头尾预览':'仍为原始结果'):'尚未执行可见文本替换'));
    if(s.replaced){const head=Math.max(1,Math.floor(s.preview_budget*3/4)),tail=Math.max(1,s.preview_budget-head);
      visible.append(el('div','hof-reference','教学附件 full-v1 · 通过 handle 召回精确切片'),
        el('pre','hof-preview',`${sim.original().slice(0,40)}\n[… 已外置中间原文 …]\n${sim.original().slice(-20)}`),note(`原文预览预算 ${s.preview_budget} 字符：头 ${head} + 尾 ${tail}。元数据、召回提示与省略标记另计。`));
    }else visible.append(note(s.artifact_retained&&terminal?'附件已存在；占位文本未缩短，原文不替换。':'保存完成后，还需确认占位文本实际缩短，才替换当前结果。'));
    outputs.append(artifact,el('div','hof-transfer','原文入库\n引用回传'),visible);diagram.append(outputs);
    const error=el('div',`hof-exit ${s.propagated_error?'control':s.reason==='artifact_retention_unavailable'?'storage':''}`);
    if(!terminal)error.append(el('strong','','等待案例出口'),note('尚未完成的阶段保留待观察状态。'));
    else if(s.propagated_error)error.append(el('strong','',`${s.propagated_error} → 上层`),note('外层插件边界透传；本图没有 Provider 已停止的观察。'));
    else if(scene.case_id==='storage_interrupted')error.append(el('strong','','put 内 InterruptedError 被 except Exception 捕获'),note('artifact_retention_unavailable · skipped；原文保留，propagated_error 为空。'));
    else if(s.plugin_status==='failed_open')error.append(el('strong','','普通逸出插件异常 → failed_open'),note('当前原结果保留，普通错误在外层插件边界放行。'));
    else error.append(el('strong','',`${s.plugin_status} · ${s.reason}`),note(s.replaced?'完整附件可召回；当前结果已采用短引用。':'本次未替换原始结果。'));
    diagram.append(error);return diagram;
  }
  function render(){
    if(destroyed)return;
    const scene=sim.getScene(),header=el('header','hof-header');header.append(btn('返回','return'),el('h2','','工具输出外置'),el('span','hof-demo','固定案例 · 本地模拟'));
    const label=el('label','hof-cases','演示情境'),select=el('select');select.dataset.offloadAction='case';
    for(const c of sim.mechanism.cases){const option=el('option','',c.title);option.value=c.id;select.append(option);}select.value=scene.case_id;label.append(select);
    const actions=el('div','hof-actions'),next=btn(scene.next?.title||'本案例完成','next'),back=btn('上一步','back');next.disabled=!scene.next;back.disabled=!scene.cursor;
    actions.append(next,back,btn('重新演示','reset'),el('span','hof-muted',`${scene.cursor} / ${scene.total_steps}`));
    const status=el('p','hof-status',scene.current?.title||sim.mechanism.question);status.setAttribute('role','status');
    const recall=el('section','hof-recall');recall.append(el('h3','','⑤ 从完整原文召回精确切片'),note('实际使用：可先 search_harness_artifact 定位，再用 read_harness_artifact(handle, offset, limit) 读取；下方只切片本案例的教学附件 full-v1。'));
    for(const [name,title,value]of [['offset','字符 offset',recallOffset],['limit','字符 limit（1–2000）',recallLimit]]){const label=el('label','',title),input=el('input');input.type='number';input.min=name==='offset'?'0':'1';if(name==='limit')input.max='2000';input.value=String(value);input.dataset.offloadAction=name;label.append(input);recall.append(label);}
    const read=btn('读取本地教学切片','recall');read.disabled=!scene.observed_fields.includes('original_retrievable')||!scene.state.original_retrievable;recall.append(read);
    if(recallSlice)recall.append(el('pre','hof-slice',`full-v1 [${recallSlice.offset}, ${recallSlice.offset+recallSlice.text.length})\n${recallSlice.text}`));
    else recall.append(note(read.disabled?'完整附件尚不可召回。':'附件已可召回；输入字符范围后读取。'));
    const sources=el('details','hof-sources');sources.open=sourcesOpen;sources.append(el('summary','','固定规则与来源'));
    for(const text of sim.mechanism.invariants)sources.append(note(text));
    if(scene.current)sources.append(el('pre','hof-rule',JSON.stringify({guard:scene.current.guard,when:scene.current.when,next:scene.current.next,state_changes:scene.current.state_changes},null,2)));
    for(const id of scene.evidence_ids){const e=sim.evidence.get(id),b=btn(`${e.symbol||e.title||id} · L${e.start_line}–${e.end_line}`,'source',id);b.title=`${e.frozen.address}\nSHA-256 ${e.sha256}`;sources.append(b);}
    const nav=el('footer','hof-actions');nav.append(btn('查看 Harness 组成','composition'),btn('选中插件管线','select','worker.harness.pipeline'),btn('选中附件存储','select','storage.artifacts.harness-store'));
    host.replaceChildren(header,label,diagram(scene),actions,status,recall,sources,nav);host.scrollTop=scrollTop;
    if(shown&&focusKey)host.querySelector(`[data-offload-action="${focusKey}"]`)?.focus({preventScroll:true});
  }
  function getViewState(){return destroyed?null:{schema:'harness-offloading-view.v1',batchKey:content.batchKey,objectId,simulation:sim.getViewState(),
    scrollTop:shown?host.scrollTop:scrollTop,sourcesOpen:host.querySelector('.hof-sources')?.open??sourcesOpen,focusKey,recallOffset,recallLimit,recallShown:!!recallSlice};}
  function restoreViewState(v){
    if(destroyed||v?.schema!=='harness-offloading-view.v1'||v.batchKey!==content.batchKey||v.objectId!==objectId||!Number.isFinite(v.scrollTop)||v.scrollTop<0
      ||typeof v.sourcesOpen!=='boolean'||!(v.focusKey===null||['case','next','back','reset','recall','source','select','composition','return','offset','limit'].includes(v.focusKey))
      ||!Number.isSafeInteger(v.recallOffset)||v.recallOffset<0||!Number.isSafeInteger(v.recallLimit)||v.recallLimit<1||v.recallLimit>2000||typeof v.recallShown!=='boolean')return false;
    const staged=createHarnessOffloadingSimulation(content);if(!staged.restoreViewState(v.simulation)||v.recallShown&&!staged.recall(v.recallOffset,v.recallLimit))return false;
    sim.restoreViewState(v.simulation);({scrollTop,sourcesOpen,focusKey,recallOffset,recallLimit}=v);recallSlice=v.recallShown?sim.recall(recallOffset,recallLimit):null;render();return true;
  }
  host.addEventListener('click',event=>{
    const b=event.target.closest?.('button[data-offload-action]');if(!b||!host.contains(b)||!shown||destroyed||b.disabled||signal?.aborted)return;
    const {offloadAction:action,value}=b.dataset;focusKey=action;scrollTop=host.scrollTop;
    if(action==='return')return emit('return',getViewState());if(action==='source')return emit('source',value);
    if(action==='select')return emit('select',{objectId:value});if(action==='composition')return emit('navigate',{kind:'composition',objectId:'worker.harness',mechanismId:null});
    if(action==='next')sim.advance();if(action==='back')sim.back();if(action==='reset')sim.reset();
    if(action==='recall'){
      const offset=Number(host.querySelector('[data-offload-action="offset"]').value),limit=Number(host.querySelector('[data-offload-action="limit"]').value);
      const result=sim.recall(offset,limit);if(!result)return;recallOffset=offset;recallLimit=limit;recallSlice=result;
    }else recallSlice=null;
    render();emit('change',{...sim.getScene(),localRecall:recallSlice?{offset:recallOffset,limit:recallLimit}:null});
  },{signal:listeners.signal});
  host.addEventListener('change',event=>{if(!shown||destroyed||signal?.aborted)return;if(event.target.dataset.offloadAction!=='case'||!sim.chooseCase(event.target.value))return;
    scrollTop=host.scrollTop;focusKey='case';recallSlice=null;recallOffset=0;render();emit('change',sim.getScene());},{signal:listeners.signal});
  host.addEventListener('toggle',event=>{if(shown&&!destroyed&&host.contains(event.target)&&event.target.matches?.('.hof-sources'))sourcesOpen=event.target.open;},{capture:true,signal:listeners.signal});
  function destroy(){if(destroyed)return;destroyed=true;shown=false;listeners.abort();host.remove();signal?.removeEventListener('abort',destroy);}
  render();if(initialState)restoreViewState(initialState);signal?.addEventListener('abort',destroy,{once:true});if(signal?.aborted)destroy();
  return {show(){if(!destroyed){shown=true;host.hidden=false;host.scrollTop=scrollTop;}},hide(){if(!destroyed){scrollTop=host.scrollTop;shown=false;host.hidden=true;}},destroy,getViewState,restoreViewState,getScene:()=>destroyed?null:sim.getScene()};
}

// Composition of two domain views, with one parent frame and independent local
// state. The existing four-mechanism implementation remains untouched.
export function createHarnessPipelineWorkbench(root,{subject,content,ports={},signal,initialState}={}){
  content=structuredClone(content);const objectId=subject?.objectId,object=content.objects.find(o=>o.id===objectId);
  if(objectId!=='worker.harness.pipeline'||!HARNESS_PIPELINE_MODE_IDS.includes(subject?.mechanismId)||content.mechanism.id!==subject.mechanismId)throw Error('harness_pipeline:subject');
  const host=el('section','harness-pipeline-modes'),bar=el('label','hof-mode','阅读主题'),select=el('select');bar.append(select);host.append(bar);root.append(host);
  const supplied=[content.mechanism,...(content.mechanisms||[]).filter(m=>m.id!==content.mechanism.id)];
  const candidates=new Map();
  for(const id of HARNESS_PIPELINE_MODE_IDS){const matches=supplied.filter(m=>m.id===id);if(object?.mechanism_ids?.includes(id)&&matches.length===1)candidates.set(id,matches[0]);}
  const children=new Map(),containers=new Map(),visibleChildren=new Set();let activeId=subject.mechanismId,shown=true,destroyed=false;
  const listeners=new AbortController(),emit=(name,value)=>{if(shown&&!destroyed&&!signal?.aborted)ports[name]?.(value);};
  function make(id){const box=el('div','hof-mode-body'),m=candidates.get(id);box.hidden=true;if(!m)throw Error('harness_pipeline:unavailable_mode');
    const factory=id===HARNESS_OFFLOADING_ID?createHarnessOffloadingWorkbench:createHarnessWorkbench;
    const child=factory(box,{subject:{objectId,mechanismId:id},content:{...content,mechanism:m},ports:{...Object.fromEntries(['source','select','navigate','change'].map(name=>[name,value=>emit(name,value)])),return:()=>emit('return',getViewState())}});
    child.hide();return {box,child};}
  for(const id of candidates.keys()){
    try{const {box,child}=make(id);children.set(id,child);containers.set(id,box);host.append(box);const option=el('option','',candidates.get(id).title);option.value=id;select.append(option);}
    catch(error){if(id===activeId){host.remove();for(const child of children.values())child.destroy();throw error;}}
  }
  if(!children.has(activeId)){host.remove();throw Error('harness_pipeline:active_mode');}
  if(children.size<2)bar.hidden=true;
  function showActive(){
    for(const [id,child]of children){
      const box=containers.get(id);
      if(id===activeId&&shown){box.hidden=false;if(!visibleChildren.has(id)){child.show();visibleChildren.add(id);}}
      else{if(visibleChildren.delete(id))child.hide();box.hidden=id!==activeId;}
    }
    select.value=activeId;
  }
  function getViewState(){return destroyed?null:{schema:'harness-pipeline-modes.v1',batchKey:content.batchKey,objectId,activeMechanismId:activeId,
    views:[...children].map(([mechanismId,child])=>({mechanismId,state:child.getViewState()}))};}
  function restoreViewState(v){
    if(destroyed||v?.schema!=='harness-pipeline-modes.v1'||v.batchKey!==content.batchKey||v.objectId!==objectId||!children.has(v.activeMechanismId)||!Array.isArray(v.views)||v.views.length!==children.size)return false;
    const staged=new Map();
    try{for(const item of v.views){if(!children.has(item?.mechanismId)||staged.has(item.mechanismId))throw Error('mode');const next=make(item.mechanismId);staged.set(item.mechanismId,next);if(!next.child.restoreViewState(item.state))throw Error('state');}}
    catch{for(const {child}of staged.values())child.destroy();return false;}
    for(const child of children.values())child.destroy();children.clear();visibleChildren.clear();for(const box of containers.values())box.remove();containers.clear();
    for(const [id,{child,box}]of staged){children.set(id,child);containers.set(id,box);host.append(box);}activeId=v.activeMechanismId;showActive();return true;
  }
  select.addEventListener('change',()=>{if(!shown||destroyed||signal?.aborted||!children.has(select.value))return;activeId=select.value;showActive();emit('change',children.get(activeId).getScene());},{signal:listeners.signal});
  function destroy(){if(destroyed)return;destroyed=true;shown=false;listeners.abort();for(const child of children.values())child.destroy();children.clear();host.remove();signal?.removeEventListener('abort',destroy);}
  showActive();if(initialState)restoreViewState(initialState);signal?.addEventListener('abort',destroy,{once:true});if(signal?.aborted)destroy();
  return {show(){if(!destroyed){shown=true;host.hidden=false;showActive();}},hide(){if(!destroyed){shown=false;showActive();host.hidden=true;}},destroy,getViewState,restoreViewState,getScene:()=>destroyed?null:children.get(activeId).getScene()};
}
export const harnessOffloadingFactories=Object.freeze({[HARNESS_OFFLOADING_ADAPTER]:createHarnessOffloadingWorkbench});
export const harnessPipelineObjectFactories=Object.freeze({'worker.harness.pipeline':createHarnessPipelineWorkbench});
export const harnessPipelineObjectModes=Object.freeze({'worker.harness.pipeline':HARNESS_PIPELINE_MODE_IDS});
