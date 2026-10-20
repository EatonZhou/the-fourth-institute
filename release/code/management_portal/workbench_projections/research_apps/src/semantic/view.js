import {decode} from '../session.js';
import {readableIndex} from '../index-access.js';
import {createCatalog} from './catalog.js';
export function mount(root, {session, snapshotRef, snapshotHash}) {
const sdk=session.sdk,byId=id=>root.querySelector("#"+id);
let data=createCatalog({snapshot:[],snapshotEvidence:{}});
const target="workbench_d82dedd31208",lineage="lineage_98e68feb0c21",statisticsId="resident_e1ab831fb7c3";
const state={models:[],reports:new Map(),failures:new Map(),pending:new Map(),selected:null,preferences:{},graph:null,verified:false,busy:false,epoch:0,disposed:false,methodRequest:0,sourceMessage:null};
const stageLabels=["PyTorch","FlashRT 原生","FlashRT 既有优化","团队组合语义优化"];
const labels={baseline:"PyTorch 基线",native:"FlashRT 原生",optimized:"进一步优化",method:"方法测量"};
const node=(tag,className,text)=>{const n=document.createElement(tag);if(className)n.className=className;if(text!==undefined)n.textContent=String(text);return n;};
const json=payload=>JSON.parse(decode(payload));
const latency=value=>Number.isFinite(value)?value.toFixed(3)+" ms":"暂无可用计时";
const sourceFor=(model,hardware)=>model.sources[data.hardware.indexOf(hardware)]||null;
const snapshotFor=(model,hardware)=>data.snapshot.find(row=>row[0]===hardware&&data.modelKey(row[1])===model.key);
const statusLabel=report=>report?"报告状态："+(report.status||"unknown"):"报告尚未读取";
const isCurrent=selection=>!state.disposed&&state.selected===selection&&byId("matrixDetail").open;
function errorText(error){return (error.code?error.code+" · ":"")+(error.message||String(error));}
function activeTab(){return root.querySelector("[data-tab][aria-selected=true]")?.dataset.tab||"results";}
function closeDetail(){session.clearSelection();state.selected=null;state.methodRequest++;if(byId("matrixDetail").open)byId("matrixDetail").close();}
function reportDetails(report,label){
 const d=node("details");d.append(node("summary","",label||"展开原始报告"),node("pre","sourceEvidence",report.text));return d;
}
function snapshotNote(model,hardware){
 const notes=["2026-09-21 统计汇总；前三列为历史配置，第四列为团队工作记录。列间不直接计算加速比。"];
 if(model.key==="dreamer")notes.push("前三列为初始化权重工程数据，缺目标训练权重。");
 if(model.key==="smolvla"&&hardware==="RTX4090")notes.push("实际设备为 4090D，FlashRT 与框架混合实现；混合命中和全变化分别保留。");
 if(model.key==="rdt"&&hardware!=="Thor")notes.push("轨迹口径：五帧总耗时中位数除以五，不能当作所有单请求的 p50。");
 if(model.key==="octo")notes.push(hardware==="RTX4090"?"5.447 ms 为历史 289-token 范围；完整 690-token 范围另列 7.474 / 9.398 ms。":"混合命中与全变化分别列出，不合并为无条件最优值。");
 if(model.key==="cosmos")notes.push("各 GPU 的输出解码范围不同；A100 的候选增益尚未可靠确认。");
 if(model.key==="pi05")notes.push("Thor 的异常 10.094 ms、RTX4090 的 r64 30.230848 ms 已排除；历史 NO_GO 记录保留标识。");
 return notes.join(" ");
}
async function readReport(id){
 if(state.reports.has(id))return state.reports.get(id);
 if(state.pending.has(id))return state.pending.get(id);
 const epoch=state.epoch;
 const promise=sdk.resource("read",{address:id}).then(payload=>{
  const report=data.parseReport(payload,id);
  if(epoch===state.epoch&&!state.disposed){state.reports.set(id,report);state.failures.delete(id);}
  return report;
 }).catch(error=>{if(epoch===state.epoch&&!state.disposed)state.failures.set(id,errorText(error));throw error;}).finally(()=>{if(state.pending.get(id)===promise)state.pending.delete(id);});
 state.pending.set(id,promise);return promise;
}
function performanceChart(row){
 const names=["PyTorch","FlashRT 原生","FlashRT 既有优化","组合"];
 const items=row.slice(2).map((raw,index)=>{
  // Only one explicitly reported latency can be plotted; annotations are retained in the source text.
  const match=raw.match(/^(\d+(?:\.\d+)?)(?:〔[^〕]*〕)*$/);
  const value=match?Number(match[1]):null;
  return {raw,index,value:Number.isFinite(value)?value:null};
 });
 const max=Math.max(0,...items.map(item=>item.value===null?0:item.value));
 const chart=node("span","cellChart");
 chart.setAttribute("role","img");
 chart.setAttribute("aria-label","延迟柱状图，单位毫秒，越低越快。"+items.map(item=>stageLabels[item.index]+"："+item.raw).join("；")+"。各格独立刻度，配置口径见详情。");
 chart.append(node("span","cellChartCaption","延迟 ms · 越低越快"));
 const columns=node("span","cellChartColumns");columns.setAttribute("aria-hidden","true");
 for(const item of items){
  const column=node("span","cellChartColumn");column.dataset.stage=String(item.index);
  column.title=stageLabels[item.index]+"："+item.raw+(item.value===null?"":" ms");
  const missing=item.raw.startsWith("—");
  column.append(node("span","cellChartValue",item.value===null?(missing?"—":"多口径"):item.value.toFixed(3)));
  const track=node("span","cellChartTrack");
  if(item.value!==null){const bar=node("span","cellChartBar");bar.dataset.value=String(item.value);bar.style.height=(max>0?item.value/max*100:0)+"%";track.append(bar);}
  else track.append(node("span","cellChartMissing",missing?"暂无计时":"见完整数值"));
  column.append(track,node("span","cellChartLabel",names[item.index]));columns.append(column);
 }
 chart.append(columns,node("span","cellChartNote","零起点 · 各格独立刻度；配置可能不同，口径见详情"));
 return chart;
}
function cell(model,hardware){
 const row=snapshotFor(model,hardware),source=sourceFor(model,hardware);
 const button=node("button","portalButton matrixCell");
 button.type="button";button.dataset.model=model.key;button.dataset.hardware=hardware;
 button.setAttribute("aria-label",model.name+" × "+hardware+"，查看性能与优化方式");
 button.setAttribute("aria-haspopup","dialog");
 const hasTiming=!row[5].startsWith("—");
 const top=node("span","cellTop");
 const badge=model.key==="dreamer"?"缺目标权重":/候选|NO_GO|未闭合/.test(row[5])?"带条件的工作记录":hasTiming?"已有组合计时":"已有历史性能";
 top.append(node("strong","cellSpeedup",hasTiming?"组合结果":"组合待补齐"),node("span","cellBadge",badge));
 button.setAttribute("aria-label",model.name+" × "+hardware+"，延迟毫秒，"+row.slice(2).map((value,index)=>stageLabels[index]+"："+value).join("；")+"。查看性能与优化方式");
 button.append(top,node("span","cellOutcome",row[5]+(hasTiming?" ms":"")),performanceChart(row),node("small","matrixMuted","查看方案尝试、组合历程与证据 →"));
 button.onclick=()=>openDetail(model,hardware,button);
 return button;
}
function render(){
 if(!state.verified)return;
 const query=byId("matrixSearch").value.trim().toLowerCase(),filter=byId("matrixHardware").value;
 const platforms=filter==="all"?data.hardware:[filter],models=state.models.filter(m=>m.name.toLowerCase().includes(query));
 const table=byId("performanceMatrix"),header=node("tr"),title=node("th","","模型 / 平台");title.scope="col";header.append(title);
 for(const hw of platforms){const heading=node("th","",hw);heading.scope="col";header.append(heading);}
 table.tHead.replaceChildren(header);
 const rows=models.map(model=>{const tr=node("tr"),heading=node("th","",model.name);heading.scope="row";heading.append(node("span","modelOwner",model.residentId?"Steward 已关联":"来自完整模型统计目录"));tr.append(heading);
 for(const hw of platforms){const td=node("td");td.append(cell(model,hw));tr.append(td);}return tr;});
 if(!rows.length){const tr=node("tr"),td=node("td","","没有匹配的模型；清除筛选可恢复全部 block。");td.colSpan=platforms.length+1;tr.append(td);rows.push(tr);}
 table.tBodies[0].replaceChildren(...rows);
 byId("matrixDimensions").textContent=state.models.length+" 模型 × "+data.hardware.length+" 平台 · "+(query||filter!=="all"?"显示 ":"")+models.length*platforms.length+" 格";
 byId("matrixCoverage").textContent="当前 "+models.length*platforms.length+" 格 · 历史统计；候选、负例与限制见详情";
}
function snapshotResults(container,selection){
 const {model,hardware}=selection,row=snapshotFor(model,hardware);
 container.replaceChildren();
 container.append(performanceChart(snapshotFor(selection.model,selection.hardware)));container.classList.add("resultList");
 container.append(node("p","matrixNotice","团队统计快照 · 2026-09-21 · 单位 ms"));
 row.slice(2).forEach((value,index)=>{const card=node("article","resultRow");card.append(node("h3","",stageLabels[index]),node("strong","resultNumbers",value),node("p","matrixMuted",index===3?"保留候选、条件性结果和缺测原因。":"历史配置的汇总值。"));container.append(card);});
 container.append(node("p","matrixMuted",snapshotNote(model,hardware)));
 const source=sourceFor(model,hardware),report=state.reports.get(source);
 if(!source){container.append(node("p","matrixNotice","该模型／硬件的结果已在统计汇总中记录；尚未关联可读取的平台实验资源，详见原始证据。"));return;}
 container.append(node("h3","","关联平台实验 · 按该报告自身口径"));
 if(!report){container.append(node("p","matrixMuted",state.failures.get(source)||"正在读取关联实验……"));return;}
 container.append(node("p","matrixMuted",source+" · "+statusLabel(report)));
 if(!report.rows.length)container.append(node("p","matrixNotice","该实验报告没有可解析的性能分层；可在原始证据中查看进展，保留上方已有统计值。"));
 for(const item of report.rows){
 const card=node("article","resultRow");card.append(node("h3","",labels[item.kind]||item.name),node("p","resultNumbers","p50："+latency(item.p50)+" · p95："+latency(item.p95)),node("p","matrixMuted",item.contract||"输入契约见原报告"));
 if(item.speedup>0)card.append(node("p","","同一报告配对："+item.speedup.toFixed(3)+"× PyTorch"));
 card.append(reportDetails({text:item.evidence},"查看该项测量原文"));container.append(card);
 }
 if(report.progress)container.append(node("p","matrixNotice",report.progress));
}
function evidence(container,selection){
 const {model,hardware}=selection,row=snapshotFor(model,hardware),source=sourceFor(model,hardware),report=state.reports.get(source);
 container.replaceChildren();
 container.append(node("p","matrixMuted","统计来源："+statisticsId+" · "+data.snapshotEvidence.message_id+" · "+data.snapshotEvidence.created_at));
 const excerpt=["平台："+hardware,"| 模型 | PyTorch | FlashRT 原生 | FlashRT 既有优化 | 团队组合语义优化 |","| "+row.slice(1).join(" | ")+" |",snapshotNote(model,hardware)].join("\n");
 container.append(node("pre","sourceEvidence",excerpt));
 container.append(node("p","matrixMuted",state.sourceMessage?"原汇总消息已在本次读取中核对。":"原消息已移出近期活动窗口；这里保留带日期和消息 ID 的已核对快照。"));
 container.append(reportDetails({text:state.sourceMessage?.content||data.snapshotEvidence.content},"完整统计原文与来源路径"));
 if(source){
 container.append(node("h3","","平台实验原文 · "+source));
 if(report)container.append(node("p","matrixMuted",statusLabel(report)),reportDetails(report,"展开平台实验原文"));
 else container.append(node("p","matrixNotice",state.failures.get(source)||"正在读取平台实验……"));
 }else container.append(node("p","matrixNotice","尚未关联可读取的独立平台实验；统计记录仍可查看。原文中的文件路径作为证据线索保留。"));
 appendIndexEvidence(selection);
}
const explorationAddress='information_6d772dbfa65b';
let explorationPromise=null;
async function loadExploration(){
 if(!explorationPromise){
   const pending=sdk.resource('read',{address:explorationAddress}).then(async payload=>{
   const match=String(payload.content||'').match(/```semantic-exploration-index\s*\n([\s\S]*?)\n```/);
   if(!match)throw Error('实验索引缺少可读取的数据段');
   const index=await readableIndex(JSON.parse(match[1]),session);
   if(index.schema!=='semantic.exploration.index.v1'||index.lineage_id!==lineage||index.workbench_id!==target)throw Error('实验索引与当前团队不匹配');
   return index;
  });
  explorationPromise=pending;
  pending.catch(()=>{if(explorationPromise===pending)explorationPromise=null;});
 }
 return explorationPromise;
}
const plain=text=>String(text||'').replace(/[*`#]/g,'').trim();
const dateText=date=>date?String(date).slice(0,16).replace('T',' ')+' UTC':'日期见来源';
const concise=text=>plain(text).split('\n').filter(l=>!/(?:\/share\/|deliverables\/|SHA256|^MID[:：]|^结果[:：]|^交付[:：])/.test(l)).join('\n').trim();
function stateHint(text){
 if(/no_gain_needs_new_candidate|未证明稳定|未通过重复置信|性能目标未达成/i.test(text))return '未确认新增收益';
 if(/blocked_external|blocked_|BUSY_SKIP|等待.*窗口/i.test(text))return '阻塞 / 待补齐';
 if(/rejected|质量失败|不准入|回归候选/i.test(text))return '负例 / 兼容性限制';
 return '记录原文';
}
function ledgerStatus(raw){
 const map={historical_reference_only:'历史参考 · 非本轮实测',queued:'等待本轮基线计时',sm80_ready_waiting_baseline:'SM80 已准备 · 等待基线',ready_after_baseline:'基线完成后可尝试',requires_merge_and_current_measurement:'等待集成与本窗口测量',requires_merge_after_incumbent:'等待集成到当前组合',compatibility_port_required:'需要兼容性移植',blocked_unverified_source_unresolved:'源代码与验证尚未闭合',implemented_formal_run_waiting_nonblocking_safe_window:'已实现 · 等待安全计时窗口',rejected_conflict:'未纳入 · 状态机冲突',rejected_not_formal_eligible:'未纳入 · 正式准入未满足',blocked_no_host_gpu_access:'阻塞 · 尚未取得目标 GPU 访问',offline_integrated_requires_rebase:'离线已集成 · 需重新对齐基线',offline_inventory_missing_groot_artifact:'缺少该模型的可消费制品'};
 return map[raw]||raw||'状态见原始记录';
}
function methodLabel(raw){
 const labels={vla_pruning:'VLA Pruning',vla_cache:'VLA-Cache',action_cache:'ActionCache',tome:'ToMe',spinquant:'SpinQuant',kivi:'KIVI',svd_llm:'SVD-LLM',shortgpt:'ShortGPT',dynamic_sparsity:'动态稀疏',multilevel_dynamic_sparsity:'多层次动态稀疏',native_flashrt_baseline:'原生 FlashRT 基线',dit_cache:'DiT Cache',adaptive_timestep:'Adaptive Timestep',viditq:'ViDiT-Q'};
 return labels[raw]||raw;
}
const fmt=n=>Number.isFinite(n)?n.toFixed(3):'未记录';
function emptyExplore(p,title,description){const d=node('div','exploreEmpty');d.append(node('strong','',title),node('p','matrixMuted',description));p.append(d);}
function badge(text){return node('span','exploreChip',text);}
function btn(text,fn,cls=''){const b=node('button','portalButton '+cls,text);b.type='button';b.onclick=fn;return b;}
function selectField(title,choices){const label=node('label','portalField',title),select=node('select');select.setAttribute('aria-label',title);for(const [value,text] of choices){const o=node('option','',text);o.value=value;select.append(o);}label.append(select);return {label,select};}
function statusName(s){return ({succeeded:'分支已完成',completed:'分支已完成',complete:'记录完成',running:'进行中',queued:'排队',failed:'失败',blocked:'阻塞',unknown:'状态未登记',pass:'原检查通过',success:'原检查通过'})[s]||ledgerStatus(s);}
const fieldNames={name:'方案',candidate:'候选',step:'步骤',parent:'父项',warmup:'预热',warmups:'预热',repeats:'测量次数',iterations:'迭代次数',flow_steps:'Flow 步数',diffusion_steps:'扩散步数',nfe:'NFE',batch_size:'Batch',views:'视角数',output_shape:'输出形状',precision:'精度',timer:'计时方式',gpu_name:'GPU',physical_index:'物理 GPU',host_physical_index:'物理 GPU',status:'原始状态',passed:'原检查通过',full_model:'完整模型',output_finite:'输出有限值',output_nonempty:'输出非空',retain_ratio:'Token 保留比例',baseline:'基线',optimized:'优化后',unit:'单位',kind:'测量对象',reason:'原因',notes:'说明',formal_complete:'正式记录闭合',source_revision:'源码版本',revision:'版本',base_relation:'与基线的关系',single_variable_isolation:'单变量隔离',prior_a100_10x50:'已有 A100 10+50 记录',current_window_result:'本窗口结果',p50_ms:'p50 / ms',historical_p50_ms:'历史 p50 / ms',formal_metrics:'正式指标',acceptance:'验收条件',decision:'原判定'};
function facts(data,limit=24){const dl=node('dl','visualFacts');let count=0;function add(o,prefix='',depth=0){if(count>=limit||depth>3||o==null)return;for(const [k,v] of Object.entries(o)){if(count>=limit)break;if(/sha256|uuid|path|samples|trace|commit|revision|schema|result_id/.test(k))continue;if(v&&typeof v==='object'&&!Array.isArray(v)){add(v,(fieldNames[k]||k)+' / ',depth+1);continue;}if(Array.isArray(v)&&v.some(x=>x&&typeof x==='object'))continue;const value=v==null?'未取得':typeof v==='boolean'?(v?'是':'否'):Array.isArray(v)?v.join(' × '):String(v);if(value.length>220)continue;dl.append(node('dt','',prefix+(fieldNames[k]||k)),node('dd','',value));count++;}}if(typeof data==='string'){dl.append(node('dt','','记录'),node('dd','',data));}else add(data);return dl;}
function readable(text){const wrap=node('div','readableReport');for(const line of String(text||'').split('\n')){if(/^```/.test(line))continue;const t=plain(line);if(!t)continue;wrap.append(node(/^#{1,4} /.test(line)?'h4':'p','',t));}return wrap;}
function sourceBox(index,address,label){const box=node('details','exploreSource');box.append(node('summary','',label||index.sources[address]?.title||'查看原始证据'),node('p','exploreMeta',address));box.addEventListener('toggle',async()=>{if(!box.open||box.dataset.loaded)return;box.dataset.loaded='true';const slot=node('div');box.append(slot);try{const content=index.sources[address]?.text??(await sdk.resource('read',{address})).content;try{slot.append(facts(JSON.parse(content),40));}catch{slot.append(readable(content));}}catch(e){slot.append(node('p','matrixNotice',errorText(e)));box.dataset.loaded='';}},{once:false});return box;}
function receiptBox(m,resident,label){const d=node('details','exploreSource');d.append(node('summary','',label||'成员回执'),node('p','exploreMeta',dateText(m.date)+' · '+resident),readable(m.text||m.quote||''));return d;}
function qualityLabel(r){if(r.excluded)return '无效契约归档';if(r.interference)return '受干扰';if(r.developmental)return '探索记录';if(r.warmup==null||r.repeats==null)return '协议待补齐';return r.warmup+' 预热 / '+r.repeats+' 测量';}
function sourceDetails(r){const d=node('details','exploreSource');d.append(node('summary','','来源与复核位置'));for(const [t,v] of [['资源',r.source.address],['指标字段',r.source.pointer],['内容指纹',r.source.sha256]])d.append(node('p','exploreMeta',t+'：'+v));return d;}
function methodFor(cell,r){return cell.strategies.find(x=>x.id===r.experiment_id+':'+r.model_key)||{name:r.method,category:'平台结果',mechanism:'此记录属于平台上的组合与实现探索，具体语义条件见原始报告。',sources:[r.experiment_id]};}
function detailMethod(item,panel,index,cell,record){
 const source=record?.source?.address||item.sources?.[0];
 if(source)session.select({ref:source,kind:'research-method',title:item.name,scene:{workbench_id:target,experiment_id:record?.experiment_id||'',pointer:record?.source?.pointer||'',source_sha256:record?.source?.sha256||''}}).catch(error=>{byId('matrixError').textContent=errorText(error);byId('matrixError').hidden=false;});
 panel.hidden=false;panel.replaceChildren();panel.append(node('span','exploreEyebrow',item.category||'方案详情'),node('h3','',item.name));
 panel.append(node('p','',item.mechanism||'方法说明以原始报告为准。'));
 if(record){
  const numbers=node('div','metricPair');for(const [title,value] of [['p50',record.p50_ms],['p95',record.p95_ms]]){const x=node('div');x.append(node('span','exploreMeta',title),node('strong','',fmt(value)),node('small','matrixMuted',' ms'));numbers.append(x);}panel.append(numbers);
  const chips=node('div','exploreChips');[record.backend,record.stage,record.variant,qualityLabel(record)].filter(Boolean).forEach(t=>chips.append(badge(t)));panel.append(chips);
  if(record.notes)panel.append(node('p','visualCaution',record.notes));
  panel.append(node('h4','','测量条件'),facts({预热:record.warmup,测量:record.repeats,计时:record.timer,范围:record.scope,精度:record.precision}));
  const condition=node('details','exploreSource');condition.append(node('summary','','输入、语义配置与原验收'),facts(record.input),facts(record.semantic_configuration),facts(record.validation));panel.append(condition,sourceDetails(record));
  const siblings=cell.measurements.filter(r=>r.experiment_id===record.experiment_id&&!r.excluded);if(siblings.length>1){const d=node('details','exploreSource');d.append(node('summary','','此方法的 '+siblings.length+' 条测量记录'));const table=node('table','recordTable'),caption=node('caption','','分别保留后端、阶段和输入条件');table.append(caption);const h=node('tr');['后端 / 阶段','条件','p50 ms','p95 ms'].forEach(t=>h.append(node('th','',t)));table.append(h);for(const r of siblings){const tr=node('tr'),td=node('td');td.append(btn(r.backend+' · '+r.stage,()=>detailMethod(item,panel,index,cell,r),'recordLink'));tr.append(td,node('td','',r.variant||qualityLabel(r)),node('td','',fmt(r.p50_ms)),node('td','',fmt(r.p95_ms)));table.append(tr);}const sc=node('div','tableScroll');sc.append(table);d.append(sc);panel.append(d);}
 }else{panel.append(node('p','visualCaution','尚无可唯一归属到本模型与硬件的机器性能记录。'),node('p','matrixMuted',plain(item.summary)));}
 if(record&&index.normalized_reports?.[record.experiment_id])panel.append(sourceBox(index,index.normalized_reports[record.experiment_id].report,'统一结果报告'));
 for(const a of (item.sources||[]).filter(a=>!a.endsWith('.json')))panel.append(sourceBox(index,a,'阅读方法报告'));
 if(item.mentions)for(const m of item.mentions.slice(0,3))panel.append(receiptBox(m,m.resident_id,'相关探索回执'));
}
function representatives(rows){const map=new Map();function score(r){return (r.repeats>=50?40:0)+(r.stage==='优化后'?25:r.stage==='图捕获'?15:0)+(r.developmental?-100:0)+(r.stage==='ABI 回归复核'?-80:0)+(r.excluded?-1000:0);}for(const r of rows){const k=[r.experiment_id,r.variant,r.backend].join(':');if(!map.has(k)||score(r)>score(map.get(k)))map.set(k,r);}return [...map.values()];}
async function showMethods(selection){
 const req=++state.methodRequest,c=byId('matrixMethods'),valid=()=>isCurrent(selection)&&req===state.methodRequest&&activeTab()==='methods';c.replaceChildren(node('p','matrixMuted','正在载入逐方案测量……'));c.setAttribute('aria-busy','true');
 try{const index=await loadExploration();if(!valid())return;const cell=index.cells[selection.model.key+'__'+selection.hardware.toLowerCase()];if(!cell)throw Error('没有该模型与硬件的索引');
 c.replaceChildren();const toolbar=node('div','portalToolbar visualControls');
 const searchLabel=node('label','portalField','方案'),search=node('input');search.type='search';search.placeholder='搜索优化方案';searchLabel.append(search);
 const backend=selectField('后端',[['FlashRT','FlashRT'],['PyTorch','PyTorch'],['all','全部后端']]),metric=selectField('延迟指标',[['p50_ms','p50 · ms'],['p95_ms','p95 · ms']]),view=selectField('记录范围',[['representative','每方案 / 配置一条'],['all','全部有效记录']]);toolbar.append(searchLabel,backend.label,metric.label,view.label);c.append(toolbar);
 const summary=node('p','visualSummary');c.append(summary);
 const layout=node('div','visualCompare'),chart=node('div','visualChart'),panel=node('article','visualInspector');panel.setAttribute('aria-label','所选语义优化方法');panel.setAttribute('aria-live','polite');layout.append(chart,panel);c.append(layout);
 emptyExplore(panel,'点击柱子查看方案','查看这条记录的优化方法、输入条件、测量协议和原始证据。');
 const missing=node('details','visualMissing');c.append(missing);
 function render(){
  chart.replaceChildren();panel.replaceChildren();emptyExplore(panel,'点击柱子查看方案','不同配置独立保留。柱长从零开始，表示记录中的延迟。');
  const q=search.value.trim().toLowerCase(),all=(cell.measurements||[]).filter(r=>!r.excluded&&(backend.select.value==='all'||r.backend===backend.select.value)&&[r.method,r.variant,r.stage].join(' ').toLowerCase().includes(q));
  const items=(view.select.value==='all'?all:representatives(all)).filter(r=>Number.isFinite(r[metric.select.value])).sort((a,b)=>a.method.localeCompare(b.method)||a.variant.localeCompare(b.variant)||a.stage.localeCompare(b.stage));
  const measured=new Set((cell.measurements||[]).filter(r=>!r.excluded).map(r=>r.experiment_id)),unmeasured=cell.strategies.filter(s=>!measured.has(s.id.split(':')[0])&&s.name.toLowerCase().includes(q));
  summary.textContent=items.length+' 条可见测量 · '+measured.size+' 个有数据的研究 · 延迟越短越快。不同输入 / NFE / 精度不构成统一排名。';
  if(!items.length)emptyExplore(chart,'当前筛选没有延迟数据',all.length?'该指标未登记，请切换 p50 或其他后端。':'切换后端或查看下方尚无可归属测量的方案。');
  else{const max=Math.max(...items.map(r=>r[metric.select.value])),axis=node('div','visualAxis');axis.append(node('span','','0'),node('span','',fmt(max/2)),node('span','',fmt(max)+' ms'));chart.append(axis);
   items.forEach(r=>{const b=btn('',()=>{chart.querySelectorAll('.measureBar').forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');detailMethod(methodFor(cell,r),panel,index,cell,r);if(matchMedia('(max-width: 48rem)').matches)panel.scrollIntoView({block:'start',behavior:'smooth'});},'measureBar');b.dataset.measurement=r.id;b.dataset.method=r.method;b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',r.method+' '+r.variant+' '+r.stage+' '+r.backend+' '+fmt(r[metric.select.value])+' 毫秒，'+qualityLabel(r));
   const top=node('span','measureTop');top.append(node('strong','',r.method+(r.variant?' · '+r.variant:'')),node('strong','measureValue',fmt(r[metric.select.value])+' ms'));
   const track=node('span','measureTrack'),fill=node('span','measureFill');fill.style.width=(r[metric.select.value]/max*100)+'%';if(r.interference||r.developmental)fill.classList.add('is-caution');track.append(fill);
   const bottom=node('span','measureMeta');bottom.append(node('span','',r.stage+(backend.select.value==='all'?' · '+r.backend:'')),node('span','',qualityLabel(r)));b.append(top,track,bottom);chart.append(b);});}
  missing.replaceChildren(node('summary','','尚无可归属测量的方案 / 线索 · '+unmeasured.length));const list=node('div','missingChoices');unmeasured.forEach(s=>list.append(btn(s.name,()=>{detailMethod(s,panel,index,cell,null);panel.scrollIntoView({block:'nearest'});})));missing.append(list);
 }
 search.oninput=render;[backend,metric,view].forEach(x=>x.select.onchange=render);render();
 }catch(e){if(valid()){c.replaceChildren();emptyExplore(c,'方案索引读取失败',errorText(e));c.append(btn('重新读取',()=>showMethods(selection)));}}finally{if(valid())c.setAttribute('aria-busy','false');}
}
function network(nodes,edges,onSelect){
 const shell=node('div','networkShell'),tools=node('div','portalToolbar graphTools'),viewport=node('div','networkViewport'),sizer=node('div','networkSizer'),world=node('div','networkWorld');viewport.tabIndex=0;viewport.setAttribute('aria-label','探索图，可滚动查看，节点可点击');
 const known=new Set(nodes.map(n=>n.id)),levels=new Map();function level(id,seen=new Set()){if(levels.has(id))return levels.get(id);if(seen.has(id))return 0;seen.add(id);const ps=edges.filter(e=>e.target===id&&known.has(e.source));const n=ps.length?1+Math.max(...ps.map(e=>level(e.source,new Set(seen)))):0;levels.set(id,n);return n;}nodes.forEach(n=>level(n.id));
 const counts={},positions=new Map();for(const n of nodes){const l=levels.get(n.id),row=counts[l]||0;counts[l]=row+1;positions.set(n.id,{x:l*260+16,y:row*112+16});}
 const width=Math.max(1,...levels.values())*260+252,height=Math.max(1,...Object.values(counts))*112+24;world.style.width=width+'px';world.style.height=height+'px';
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('aria-hidden','true');svg.classList.add('networkLines');
 for(const e of edges){const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)continue;const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',`M ${a.x+216} ${a.y+40} C ${a.x+240} ${a.y+40}, ${b.x-24} ${b.y+40}, ${b.x} ${b.y+40}`);svg.append(path);}world.append(svg);
 for(const n of nodes){const p=positions.get(n.id),b=btn('',()=>{world.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');onSelect(n);},'graphNode');b.style.left=p.x+'px';b.style.top=p.y+'px';b.setAttribute('aria-pressed','false');b.append(node('strong','',n.title),node('small','',statusName(n.status)));world.append(b);}
 let zoom=1;const zoomText=node('span','exploreMeta','100%');function setZoom(z){zoom=Math.max(.5,Math.min(1.5,z));world.style.transform='scale('+zoom+')';sizer.style.width=width*zoom+'px';sizer.style.height=height*zoom+'px';zoomText.textContent=Math.round(zoom*100)+'%';}tools.append(btn('−',()=>setZoom(zoom-.1)),zoomText,btn('+',()=>setZoom(zoom+.1)),btn('适应宽度',()=>setZoom(Math.min(1,viewport.clientWidth/width))),node('span','exploreMeta','连线表示原档案的父子关系'));tools.querySelector('button').setAttribute('aria-label','缩小探索图');tools.querySelectorAll('button')[1].setAttribute('aria-label','放大探索图');sizer.append(world);viewport.append(sizer);shell.append(tools,viewport);setZoom(1);return shell;
}
async function showJourney(selection){
 const req=++state.methodRequest,c=byId('matrixJourney'),valid=()=>isCurrent(selection)&&req===state.methodRequest&&activeTab()==='journey';c.replaceChildren(node('p','matrixMuted','正在读取探索关系……'));c.setAttribute('aria-busy','true');
 try{const index=await loadExploration();if(!valid())return;const cell=index.cells[selection.model.key+'__'+selection.hardware.toLowerCase()],model=selection.model.key;c.replaceChildren();
 const choices=[];
 for(const trail of cell.trails||[])choices.push({type:'trail',title:trail.title,data:trail});
 for(const s of cell.strategies.filter(s=>s.kind==='archive')){const a=s.id.split(':')[0],report=index.normalized_reports?.[a];if(report?.branches.length)choices.push({type:'tree',title:s.name+' · 研究分支',data:report,method:s});}
 for(const r of cell.rounds||[])choices.push({type:'round',title:dateText(r.messages.at(-1)?.date).slice(0,10)+' · '+r.name,data:r});
 if(!choices.length){emptyExplore(c,'尚无可归属的探索过程','当前索引没有该格的明确父子关系或组合台账。已知性能保留在性能对比。');return;}
 const chooser=selectField('探索记录',choices.map((x,i)=>[String(i),x.title]));chooser.label.classList.add('journeyChooser');c.append(chooser.label);const stageNode=node('div','visualJourney'),plot=node('div','journeyPlot'),detail=node('article','visualInspector journeyInspector');stageNode.append(plot,detail);c.append(stageNode);
 function render(){plot.replaceChildren();detail.replaceChildren();const choice=choices[+chooser.select.value];emptyExplore(detail,'点击节点或候选查看详情','查看组合包含哪些方法、当前限制及已有结果。');
 if(choice.type==='tree'){
  const branches=choice.data.branches,byIdMap=new Map(branches.map(n=>[n.id,n])),keep=new Set(branches.filter(n=>!n.model_key||n.model_key===model).map(n=>n.id));for(const id of [...keep]){let n=byIdMap.get(id),seen=new Set();while(n?.parent_id&&!seen.has(n.id)){seen.add(n.id);keep.add(n.parent_id);n=byIdMap.get(n.parent_id);}}
  const ns=branches.filter(n=>keep.has(n.id)).map(n=>({...n,title:n.id==='root'?'研究起点':n.title})),edges=ns.filter(n=>n.parent_id&&keep.has(n.parent_id)).map(n=>({source:n.parent_id,target:n.id}));
  plot.append(node('p','visualSummary','原始研究分支 · '+ns.length+' 个节点。根节点可能跨模型；完成状态沿用原档案。'));
  plot.append(network(ns,edges,n=>{detail.replaceChildren(node('span','exploreEyebrow','研究分支'),node('h3','',n.title),badge(statusName(n.status)),node('p','',choice.method.mechanism),node('p','exploreMeta','父节点：'+(n.parent_id||'无')));const rows=cell.measurements.filter(r=>r.experiment_id===choice.method.id.split(':')[0]&&r.source.address.includes('/nodes/'+n.id+'/'));if(rows.length)for(const r of rows)detail.append(btn(r.backend+' '+r.stage+' · '+fmt(r.p50_ms)+' ms',()=>detailMethod(choice.method,detail,index,cell,r)));else detail.append(node('p','matrixMuted','节点没有直接附带本格计时。该方法的其他已归属测量可在「方案性能」中查看。'));detail.append(sourceBox(index,n.source,'查看分支档案'));}));
 }else if(choice.type==='trail'){
  const trail=choice.data,entries=trail.entries.flatMap(e=>Array.isArray(e.candidates)?e.candidates:Array.isArray(e.order)?e.order.map(name=>({name,status:'候选顺序，尚无本项计时'})):e);
  const names=entries.map((e,i)=>String(e.name||e.candidate||e.run_id||e.method||i)),nodes=entries.map((e,i)=>({id:names[i],title:Array.isArray(e.combination)?e.combination.map(methodLabel).join(' + '):methodLabel(names[i]),status:e.status||e.state,raw:e}));
  const edges=entries.flatMap((e,i)=>e.parent&&names.includes(e.parent)?[{source:e.parent,target:names[i]}]:[]);
  function show(n){detail.replaceChildren(node('span','exploreEyebrow','组合台账'),node('h3','',n.title),badge(statusName(n.status)));if(n.raw.reason||n.raw.notes)detail.append(node('p','',n.raw.reason||n.raw.notes));if(n.raw.p50_ms!=null||n.raw.historical_p50_ms!=null)detail.append(node('p','metricText',(n.status==='historical_reference_only'||n.raw.historical_p50_ms!=null?'历史参考 ':'记录值 ')+fmt(n.raw.p50_ms??n.raw.historical_p50_ms)+' ms'));else detail.append(node('p','visualCaution','本候选尚无正式计时。'));detail.append(facts(n.raw),sourceBox(index,trail.source,'查看台账来源'));}
  if(edges.length){plot.append(node('p','visualSummary','组合父子关系 · 点击查看本窗口结果与限制'),network(nodes,edges,show));}
  else{plot.append(node('p','visualSummary','候选组合与准入状态 · 原台账未给出实验父子关系，保留独立候选。'));const grid=node('div','candidateGrid');nodes.forEach((n,i)=>{const b=btn('',()=>{grid.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');show(n);},'candidateNode');b.setAttribute('aria-pressed','false');if(/rejected|failed/.test(n.status||''))b.classList.add('is-rejected');b.append(node('small','exploreMeta',String(i+1).padStart(2,'0')),node('strong','',n.title),node('span','',statusName(n.status)));grid.append(b);});plot.append(grid);}
 }else{
  plot.append(node('p','visualSummary','回执时间线 · 连续消息表示发布顺序，不推断实验父子关系。'));const timeline=node('div','interactiveTimeline');choice.data.messages.forEach((m,i)=>{const b=btn('',()=>{timeline.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');detail.replaceChildren(node('h3','','探索回执 '+(i+1)),badge(stateHint(m.text)),readable(m.text),node('p','exploreMeta',m.message_id+' · '+choice.data.resident_id));},'timelineNode');b.setAttribute('aria-pressed','false');b.append(node('small','',dateText(m.date)),node('strong','',stateHint(m.text)),node('span','',concise(m.text).slice(0,100)));timeline.append(b);});plot.append(timeline);
 }
 }
 chooser.select.onchange=render;render();
 }catch(e){if(valid()){c.replaceChildren();emptyExplore(c,'探索记录读取失败',errorText(e));c.append(btn('重新读取',()=>showJourney(selection)));}}finally{if(valid())c.setAttribute('aria-busy','false');}
}
async function appendIndexEvidence(selection){
 const container=byId('matrixEvidence');const slot=node('div','exploreEvidenceList');container.prepend(slot);
 try{
  const index=await loadExploration();if(!isCurrent(selection)||!slot.isConnected)return;
  const cell=index.cells[selection.model.key+'__'+selection.hardware.toLowerCase()];
  slot.append(node('h3','','实验索引与归属范围'),node('p','matrixMuted','整理于 '+index.as_of+' · '+explorationAddress));
  for(const rule of index.contract)slot.append(node('p','exploreMeta',rule));
  const d=node('details');d.append(node('summary','','待归属近期消息 · '+index.unassigned_messages.length+' 条（全团队）'),node('p','exploreMeta','缺少唯一模型 / 硬件映射的消息保留来源，未强行分配。此数量不是实验失败数。'),node('pre','sourceEvidence',index.unassigned_messages.map(m=>m.date+' · '+m.source+' · '+m.message_id).join('\n')));slot.append(d);
  const inventory=node('details');inventory.append(node('summary','','资源目录 · '+index.inventory.length+' 项'),node('pre','sourceEvidence',index.inventory.map(x=>(x.mapped?'已关联':'待细分')+' · '+x.title+' · '+x.address).join('\n')));slot.append(inventory);
  const sources=[...new Set(cell.strategies.flatMap(s=>s.sources).concat(cell.trails.map(t=>t.source)))];
  for(const a of sources)slot.append(sourceBox(index,a));
 }catch(error){if(isCurrent(selection)&&slot.isConnected)slot.append(node('p','matrixNotice','实验索引读取失败：'+errorText(error)));}
}

function selectTab(name){
 state.methodRequest++;
 root.querySelectorAll("[data-tab]").forEach(button=>{const chosen=button.dataset.tab===name;button.setAttribute("aria-selected",String(chosen));button.tabIndex=chosen?0:-1;});
 for(const tab of ["results","methods","journey","evidence"])byId("matrix"+tab[0].toUpperCase()+tab.slice(1)).hidden=tab!==name;
 if(name==="journey"&&state.selected)showJourney(state.selected);
 if(name==="methods"&&state.selected){const selected=state.selected;showMethods(selected).catch(error=>{if(isCurrent(selected)&&activeTab()==="methods")byId("matrixMethods").replaceChildren(node("p","matrixNotice",errorText(error)));});}
}
async function openDetail(model,hardware,opener){
 const selection={model,hardware,opener};state.selected=selection;
 const rowIndex=data.snapshot.findIndex(row=>row[0]===hardware&&data.modelKey(row[1])===model.key);
 // Each migrated row has a stable line in the source JSON, verified against its digest.
 const ref=snapshotRef+'#L'+(rowIndex*8+6);
 session.select({ref,source:statisticsId,kind:'research-measurement:'+model.key+':'+hardware,title:model.name+' × '+hardware,
 scene:{workbench_id:target,model:model.key,hardware,record:'historical-snapshot',snapshot_sha256:snapshotHash,source_message:data.snapshotEvidence.message_id}}).catch(error=>{if(isCurrent(selection)){byId('matrixError').textContent=errorText(error);byId('matrixError').hidden=false;}});
 byId("matrixDetailTitle").textContent=model.name+" × "+hardware;
 byId("matrixDetailSubtitle").textContent="单项方案 · 组合探索 · 真实证据";
 byId("matrixDetailSummary").textContent=snapshotNote(model,hardware);
 byId("matrixDetail").showModal();selectTab("methods");
 snapshotResults(byId("matrixResults"),selection);evidence(byId("matrixEvidence"),selection);
 byId("matrixMethodsTab").focus();
 const source=sourceFor(model,hardware);
 if(source){try{await readReport(source);}catch{}if(isCurrent(selection)){snapshotResults(byId("matrixResults"),selection);evidence(byId("matrixEvidence"),selection);}}
}
async function refresh(){
 explorationPromise=null;
 if(state.busy||state.disposed||!session.active)return;
 state.busy=true;const epoch=++state.epoch;closeDetail();state.verified=false;
 const button=byId("matrixRefresh");button.disabled=true;button.textContent="正在核对……";byId("matrixError").hidden=true;
 const scroll=root.querySelector(".matrixScroll");scroll.hidden=true;scroll.setAttribute("aria-busy","true");
 byId("matrixDimensions").textContent="正在核对团队与统计来源……";byId("matrixCoverage").textContent="";
 state.reports.clear();state.failures.clear();state.pending.clear();
 try{
 session.reset();
 const archived=await sdk.resource('read',{address:snapshotRef});
 if(archived.metadata?.content_sha256!==snapshotHash)throw Error('历史统计快照版本不匹配');
 const archive=json(archived);
 if(archive.workbench_id!==target||archive.revision!==7)throw Error('历史统计快照身份不匹配');
 data=createCatalog(archive);
 const context=await sdk.context(),binding=context.bindings?.main;
 if(context.workbench_id!==target||binding?.lineage_id!==lineage||binding?.residents?.coordinator!=="resident_5549c42d1317")throw Error("当前工作台或团队绑定不匹配");
 const payloads=await Promise.all([sdk.resource("read",{address:lineage}),sdk.resource("read",{address:statisticsId})]);
 if(state.disposed||epoch!==state.epoch)return;
 const graph=json(payloads[0]),statistics=json(payloads[1]);
 if(graph.lineage_id!==lineage||!Array.isArray(graph.members)||!graph.members.some(m=>m.resident_id===statisticsId))throw Error("无法核对当前团队中的统计成员");
 if(statistics.resident_id!==statisticsId||statistics.lineage_id!==lineage||!Array.isArray(statistics.recent_activity))throw Error("统计资源与绑定不匹配");
 state.graph=graph;state.sourceMessage=statistics.recent_activity.find(m=>m.message_id===data.snapshotEvidence.message_id)||null;
 state.models=data.modelsFromGraph({nodes:graph.members});state.verified=true;scroll.hidden=false;render();
 byId("matrixUpdated").textContent="图 revision "+graph.revision+" · 性能快照 2026-09-21";
 byId("matrixNotice").textContent="统计汇总收录 12 个模型（含 Dreamer 初始化权重数据）。点击每个 block 可查看性能、方法和证据。缺测不等于未登记；候选和未达标记录保留原标注。";
 }catch(error){if(epoch!==state.epoch||state.disposed)return;byId("matrixError").textContent=errorText(error);byId("matrixError").hidden=false;byId("matrixDimensions").textContent="来源核对失败，可点击刷新重试";byId("matrixUpdated").textContent="";scroll.hidden=true;}
 finally{if(epoch===state.epoch&&!state.disposed){state.busy=false;button.disabled=false;button.textContent="刷新数据";scroll.setAttribute("aria-busy","false");}}
}
let saveQueue=Promise.resolve();
function saveFilters(){
 const preference={search:byId("matrixSearch").value,hardware:byId("matrixHardware").value};
 saveQueue=saveQueue.catch(()=>{}).then(async()=>{
 if(state.disposed)return;
 const current=await sdk.state();
 await sdk.state({...current,matrix:preference});
 }).catch(error=>{if(!state.disposed){byId("matrixError").textContent="筛选未保存："+errorText(error);byId("matrixError").hidden=false;}});
}
for(const hw of data.hardware){const option=node("option","",hw);option.value=hw;byId("matrixHardware").append(option);}
byId("matrixRefresh").onclick=refresh;
byId("matrixLineage").onclick=()=>sdk.openLineage("main").catch(error=>{byId("matrixError").textContent=errorText(error);byId("matrixError").hidden=false;});
byId("matrixSearch").oninput=render;byId("matrixSearch").onchange=saveFilters;byId("matrixHardware").onchange=()=>{render();saveFilters();};
byId("matrixReset").onclick=()=>{byId("matrixSearch").value="";byId("matrixHardware").value="all";render();saveFilters();};
byId("matrixClose").onclick=()=>byId("matrixDetail").close();
byId("matrixDetail").onclose=()=>{session.clearSelection();const opener=state.selected?.opener;state.selected=null;state.methodRequest++;if(opener?.isConnected)opener.focus();};
root.querySelectorAll("[data-tab]").forEach(button=>{
 button.onclick=()=>selectTab(button.dataset.tab);
 button.onkeydown=event=>{
 const tabs=[...root.querySelectorAll("[data-tab]")],index=tabs.indexOf(button);
 let next;if(event.key==="ArrowRight")next=(index+1)%tabs.length;if(event.key==="ArrowLeft")next=(index+tabs.length-1)%tabs.length;if(event.key==="Home")next=0;if(event.key==="End")next=tabs.length-1;
 if(next!==undefined){event.preventDefault();selectTab(tabs[next].dataset.tab);tabs[next].focus();}
 };
});

(async()=>{try{const prefs=await sdk.state();if(state.disposed)return;const saved=prefs?.matrix||{};byId("matrixSearch").value=typeof saved.search==="string"?saved.search:"";if(["all",...data.hardware].includes(saved.hardware))byId("matrixHardware").value=saved.hardware;}catch{}if(!state.disposed)await refresh();})();
return {refresh,
 invalidate(){state.epoch++;state.methodRequest++;state.busy=false;state.verified=false;state.reports.clear();state.pending.clear();explorationPromise=null;closeDetail();byId('performanceMatrix').tBodies[0].replaceChildren();root.querySelector('.matrixScroll').hidden=true;for(const id of ['matrixResults','matrixMethods','matrixJourney','matrixEvidence'])byId(id).replaceChildren();byId('matrixDimensions').textContent='正在重新核对来源…';byId('matrixCoverage').textContent='';},
 destroy(){this.invalidate();state.disposed=true;root.replaceChildren();}};
}
