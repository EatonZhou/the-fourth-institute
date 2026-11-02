import {KEY,LABELS,RELATIONS,progressOf,isHistory,validateItem,readItems,saveDocument,deliver} from './frontier_model.js';
import template from './frontier_template.js';
export function mountFrontier(host, config = {}) {
const shadow = host.shadowRoot || host.attachShadow({mode:'open'});
shadow.innerHTML = '<link rel="stylesheet" href="/static/frontier.css">';
const root = document.createElement('div');
if (config.project) root.className = 'projectFrontier';
root.innerHTML = template;
shadow.append(root);
root.querySelector('.intro h1').textContent = config.title || '探索前沿';
root.querySelector('.intro p').textContent = config.teams ? '这个方向负责团队的工作线；团队线头不等同于方向归属。' : '沿着已有工作，找到值得继续的下一步。';
root.querySelector('.eyebrow').textContent = 'FRONTIER / 线头';
if (config.project) root.querySelector('#new').hidden = true;
const controller = new AbortController();
const $ = s => root.querySelector(s);
const rowKey = row => row._lineage + ':' + row.id;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const call = (...args) => window.PortalInstructionClient.execute(...args);
const uid = () => crypto.randomUUID().replaceAll('-','');
let lineage='', project='', doc=null, rows=[], members=[], selected=null, editing=null, filter='all', epoch=0, busy=false, actor='', prefs, later=[];
const states=new Map(), sources=new Map();
const labels={all:'全部前沿',decision:'需要决定',active:'正在推进',explore:'留待探索',later:'稍后关注',history:'结果与历史'};
const notice = text => {$('#notice').textContent=text;};
function options(items) { return items.map(m=>`<option value="${esc(m.resident_id || m.id)}">${esc(m.title || m.name || m.resident_id || m.id)}</option>`).join(''); }
function inputOf(row) {return states.get(`${row._lineage}:${row.id}`);}
function permitted(row) {return row.sources.every(s=>sources.get(s)?.ok);}
function sourceStamp(row) {return JSON.stringify(row.sources.map(s=>[s,sources.get(s)?.resource?.version ?? sources.get(s)?.resource?.mtime ?? null]));}
function visible(row, kind=filter) {
  const history=isHistory(row,inputOf(row));
  if(kind==='history') return history;
  if(history) return false;
  if(kind==='later') return later.includes(rowKey(row));
  if(later.includes(rowKey(row))) return false;
  if(kind==='decision') return row.attention==='decision';
  if(kind==='active') return ['queued','running','pending'].includes(inputOf(row)?.state);
  if(kind==='explore') return row.attention==='explore';
  return true;
}
function render() {
  const available=rows.filter(permitted);
  $('#filters').innerHTML=Object.entries(labels).map(([key,label])=>`<button data-filter="${key}" aria-current="${filter===key}">${label}<small>${available.filter(r=>visible(r,key)).length}</small></button>`).join('');
  $('#section-title').textContent=labels[filter];
  const query=$('#search').value.toLowerCase();
  const shown=available.filter(r=>visible(r) && [r.title,r.line,r.reason].join(' ').toLowerCase().includes(query));
  const groups=Map.groupBy ? Map.groupBy(shown,r=>r.line) : shown.reduce((m,r)=>m.set(r.line,[...(m.get(r.line)||[]),r]),new Map());
  $('#cards').innerHTML=shown.length ? [...groups].map(([line,items])=>`<div class="group-title">${esc(line)}</div><div class="card-grid">${items.map(r=>`<article class="card"><div class="card-top"><span class="tag ${esc(r.attention)}">${LABELS[r.attention] || '待核实'}</span><span class="status">${esc(progressOf(r,inputOf(r)))}</span></div><h3><button data-open="${rowKey(r)}">${esc(r.title)}</button></h3><p>${esc(r.progress)}</p><p class="next"><b>下一步</b>${esc(r.next)}</p><div class="card-footer"><span>${r.sources.length} 个依据${r._team?' · '+esc(r._team):''}</span><button data-open="${rowKey(r)}">接上这条线 ↗</button></div></article>`).join('')}</div>`).join('') : `<div class="empty"><div class="orbit">⌁</div><b>${rows.length?'这里暂时没有线头':'给下一次思考，留一个起点'}</b><p>${rows.length?'可以换个筛选，或留下一条新的想法。':'记录一个问题、它的依据，以及值得继续的理由。<br>没有新的入口时，这里可以安静地留白。'}</p></div>`;
  const hidden=rows.length-available.length;
  if(hidden) notice(`${hidden} 条线头的来源暂不可读，内容已隐藏。刷新可重新核对权限或来源状态。`);
  renderGraph(shown,available);
}
function renderGraph(shown,available) {
  const links=shown.filter(r=>r.parent && available.some(p=>p.id===r.parent && p._lineage===r._lineage));
  $('#graph').innerHTML='<h3>沿着来路，找到下一步</h3><p class="muted">局部关系与卡片共用同一份内容。点击问题可展开依据。</p>'+ (links.length ? links.map(r=>{
    const p=available.find(x=>x.id===r.parent && x._lineage===r._lineage);
    return `<div class="graph-line"><button data-open="${rowKey(p)}">${esc(p.title)}</button><span>${RELATIONS[r.relation] || '关联'}</span><button data-open="${rowKey(r)}">${esc(r.title)}</button></div>`;
  }).join(''):'<p class="muted">这些线索暂时独立。编辑线头时可以关联发展、依赖或汇合关系。</p>');
}
async function verify(rowsToCheck) {
  const addresses=[...new Set(rowsToCheck.flatMap(r=>r.sources))];
  // Bounded batches avoid flooding shared resource and execution services.
  for(let i=0;i<addresses.length;i+=4) await Promise.all(addresses.slice(i,i+4).map(async address=>{
    try {const result=await call('ResolveResource',{address}); sources.set(address,{ok:true,resource:result.resource});}
    catch {sources.set(address,{ok:false});}
  }));
  const dispatched=rowsToCheck.filter(r=>r.dispatch && permitted(r));
  for(let i=0;i<dispatched.length;i+=4) await Promise.all(dispatched.slice(i,i+4).map(async r=>{
    try {const result=await call('GetInputState',{resident_id:r.dispatch.owner,message_id:r.dispatch.message_id});states.set(`${r._lineage}:${r.id}`,result.input_state);}
    catch {states.set(`${r._lineage}:${r.id}`,null);}
  }));
}
async function load() {
  if(busy) return;
  busy=true; $('#refresh').disabled=true; $('#new').disabled=true; notice('正在核对来源与工作状态…');
  const generation=++epoch;
  let incomplete=false;
  try {
    $('#detail').close();
    states.clear();sources.clear(); rows=[];render();
    if(lineage) {
      const [l,s]=await Promise.all([call('GetLineage',{lineage_id:lineage}),call('GetSharedState',{lineage_id:lineage,key:KEY})]);
      doc=s.value; members=(l.lineage.nodes||[]).filter(n=>/^resident_[a-f0-9]{12}$/.test(n.id)).map(n=>({...n,resident_id:n.id}));
      rows=readItems(doc).map(r=>({...r,_lineage:lineage}));
    } else if(project) {
      const [map,info]=await Promise.all([call('GetResearchMap',{project_id:project,include_outputs:false}),call('GetProject',{project_id:project,include_resources:false})]);
      const teams=[...new Set([...map.directions.flatMap(d=>d.teams),...(info.team?.lineages||[]).map(t=>t.lineage_id),info.team?.project_lineage_id].filter(Boolean))].filter(id=>!config.teams || config.teams.includes(id));
      const names=new Map((info.team?.lineages||[]).map(t=>[t.lineage_id,t.title]));
      for(const id of teams) {
        try {const s=await call('GetSharedState',{lineage_id:id,key:KEY});rows.push(...readItems(s.value).map(r=>({...r,_lineage:id,_team:names.get(id)||id.slice(-6)})));}
        catch {incomplete=true;}
      }
    } else {notice('选择一个项目，打开它的探索前沿。'); return;}
    await verify(rows);
    if(generation!==epoch)return;
    notice(incomplete?'部分团队当前不可读；仅显示已核对的工作线。':''); render(); $('#new').disabled=!lineage;
    $('#checked').textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})+' 核对 · 手动刷新';
  } catch(error) {rows=[];render();notice(error.message);}
  finally {busy=false;$('#refresh').disabled=false;}
}
function openDetail(id) {
  selected=rows.find(r=>rowKey(r)===id && permitted(r)); if(!selected)return;
  const r=selected,s=inputOf(r),history=isHistory(r,s);
  $('#detail-body').innerHTML=`<div class="dialog-heading"><span class="tag ${esc(r.attention)}">${LABELS[r.attention]}</span><button data-close="detail" aria-label="关闭">×</button></div><h1 style="font-size:27px">${esc(r.title)}</h1><div class="muted">${esc(r.line)} · ${esc(progressOf(r,s))}</div><h3>已经推进到哪里</h3><p>${esc(r.progress)}</p><h3>为什么值得继续</h3><p>${esc(r.reason)}</p><h3>下一步</h3><p>${esc(r.next)}</p><h3>依据与来路</h3><ul>${r.sources.map(address=>`<li><a href="/r/${encodeURI(address)}" target="_blank" rel="noopener">${esc(sources.get(address)?.resource?.title || address)} ↗</a></li>`).join('')}</ul>${r.dispatch?`<h3>这次交付</h3><p class="muted">${esc(progressOf(r,s))}${s?.error?' · '+esc(typeof s.error==='string'?s.error:JSON.stringify(s.error)):''}</p><a href="/lineage?resident=${encodeURIComponent(r.dispatch.owner)}" target="_blank" rel="noopener">打开负责 Agent 的对话与结果 ↗</a><p class="muted">${(s?.reply_message_ids||[]).length?'已收到回复，结论请以原对话和产物为准。':'投递成功不代表任务完成。'}</p>`:''}<footer><button data-action="later">${later.includes(rowKey(r))?'恢复关注':'稍后关注'}</button>${lineage?'<button data-action="edit">更新线头</button>':`<a href="/lineage?view=frontier&lineage=${r._lineage}">进入团队继续 ↗</a>`}${lineage&&!history?`<button class="primary" data-action="dispatch">${r.dispatch?'查看 / 重试投递':'交给 Agent'}</button>`:''}</footer>`;
  if(!$('#detail').open)$('#detail').showModal();
  $('#detail-body').querySelectorAll('li a').forEach((a,index)=>{
    const b=document.createElement('button');b.textContent='在此阅读';b.style.marginLeft='10px';
    b.onclick=async()=>{
      $('#source-text').textContent='正在读取…';$('#source').showModal();
      try{const content=await call('ReadResource',{address:r.sources[index],max_bytes:120000});$('#source-text').textContent=content.text || '此来源需在原页面打开完整文件。';}
      catch(error){$('#source-text').textContent=error.message;}
    };a.after(b);
  });
}
function edit(item=null) {
  editing=item ? structuredClone(doc.items.find(r=>r.id===item.id)) : null;
  const form=$('#edit-form');form.reset();form.querySelector('.form-error').textContent='';
  $('#edit-title').textContent=item?'更新这条线':'留下一条线头';
  form.elements.owner.innerHTML='<option value="">暂不指定</option>'+options(members);
  form.elements.parent.innerHTML='<option value="">独立线索</option>'+rows.filter(r=>r.id!==item?.id && permitted(r)).map(r=>`<option value="${r.id}">${esc(r.title)}</option>`).join('');
  if(item) for(const key of ['title','line','attention','progress','reason','next','owner','parent','relation','closed']) form.elements[key].value=item[key]||'';
  if(item)form.elements.sources.value=item.sources.join('\n');
  $('#editor').showModal();
}
async function commit(item) {
  const previous=doc;
  const value={schema:'tfi.exploration-frontier.v1',items:[...(doc?.items||[])]};
  const index=value.items.findIndex(r=>r.id===item.id);
  if(index<0)value.items.push(item);else value.items[index]=item;
  doc=await saveDocument(call,lineage,previous,value);
}
$('#edit-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button.primary');button.disabled=true;
  try {
    const data=Object.fromEntries(new FormData(form));
    const item=validateItem({...editing,...data,id:editing?.id||uid(),sources:[...new Set(data.sources.split(/\s+/).filter(Boolean))],updated_at:new Date().toISOString()},doc?.items||[]);
    await verify([{...item,_lineage:lineage}]); if(!permitted(item))throw new Error('有来源无法读取，请核对地址或访问权限。');
    await commit(item);$('#editor').close();$('#detail').close();await load();
  } catch(error){form.querySelector('.form-error').textContent=error.message;}
  finally{button.disabled=false;}
};
function dispatchForm() {
  const r=selected,form=$('#dispatch-form');form.reset();form.querySelector('.form-error').textContent='';
  form.elements.owner.innerHTML=options(members);form.elements.owner.value=r.dispatch?.owner || r.owner || members[0]?.resident_id || '';
  form.elements.instruction.value=r.dispatch?.text || r.next;
  form.elements.owner.disabled=!!r.dispatch;form.elements.instruction.readOnly=!!r.dispatch;
  form.querySelector('button.primary').textContent=r.dispatch?'重试同一次投递':'确认交给 Agent';
  $('#dispatch').showModal();
}
$('#dispatch-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button.primary');button.disabled=true;
  try {
    let item=doc.items.find(r=>r.id===selected.id);
    const before=sourceStamp(item);
    await verify([{...item,_lineage:lineage}]);
    if(!permitted(item)) {$('#detail').close();render();throw new Error('来源当前不可读，无法投递。');}
    if(before!==sourceStamp(item)) throw new Error('来源已更新，请关闭此窗口，刷新并核对后再投递。');
    // Claim the immutable request with CAS before sending. Reloads and multiple
    // viewers recover this exact message ID; a lost receipt cannot duplicate work.
    if(!item.dispatch) {
      const owner=form.elements.owner.value;if(!owner)throw new Error('请选择负责 Agent。');
      item={...item,dispatch:{owner,message_id:'msg_'+uid(),text:`${item.title}\n\n${form.elements.instruction.value.trim()}\n\n依据：\n${item.sources.join('\n')}\n\n线头入口：/lineage?view=frontier&lineage=${lineage}`,created_at:new Date().toISOString()}};
      await commit(item);
    }
    await deliver(call,item);$('#dispatch').close();$('#detail').close();await load();notice('已接收。执行与结果可在线头详情中查看。');
  }catch(error){form.querySelector('.form-error').textContent=error.message;}
  finally{button.disabled=false;}
};
root.addEventListener('click', e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.close)$('#'+b.dataset.close).close();
  if(b.dataset.open)openDetail(b.dataset.open);
  if(b.dataset.filter){filter=b.dataset.filter;render();}
  if(b.dataset.action==='edit')edit(selected);
  if(b.dataset.action==='dispatch')dispatchForm();
  if(b.dataset.action==='later') {later=later.includes(rowKey(selected))?later.filter(id=>id!==rowKey(selected)):[...later,rowKey(selected)];prefs.write({later});render();openDetail(rowKey(selected));}
}, {signal:controller.signal});
$('#new').onclick=()=>edit();$('#refresh').onclick=load;$('#search').oninput=render;
$('#map-toggle').onclick=()=>{const next=$('#graph').hidden;$('#graph').hidden=!next;$('#map-toggle').setAttribute('aria-pressed',String(next));};
async function init() {
  try {
    const me=await window.PortalAPI.get('/api/me');actor=me.username;
    prefs=window.PortalPageState.create('exploration-frontier',{user:()=>actor});later=prefs.read({later:[]}).later;
    lineage=config.lineage||'';project=lineage?'':config.project||'';
    await load();
  }catch(error){notice(error.message);}
}
const ready=init();
return {ready, destroy(){epoch++;controller.abort();root.querySelectorAll('dialog').forEach(d=>d.close());root.remove();}};
}
